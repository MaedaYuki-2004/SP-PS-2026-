"""
core/vocab.py
words_db.json の読み書きと単語登録フローを担当するモジュール。

単語登録の流れ：
  1. display（表示テキスト）と reading（ひらがな読み）を受け取る
  2. MeCab でアクセント型を自動取得
  3. VOICEVOX でサンプル音声を自動生成
  4. Julius でアライメントを自動実行
  5. MFCC を自動計算・保存
  6. words_db.json に追記
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from config import (
    AUDIO_MFCC_DIR,
    AUDIO_SCP_PATH,
    RAW_AUDIO_DIR,
    WORDS_TXT_PATH,
)
from core.accent import generate_sample_wav, get_accent
from core.alignment import lab_load, perl_run
from core.audio import convert_to_16kHz
from core.timbre import audio_mfcc

# ── パス定数 ─────────────────────────────────────────────────────────
from config import DATA_DIR
WORDS_DB_PATH = DATA_DIR / "config" / "words_db.json"


# ── DB 読み書き ───────────────────────────────────────────────────────

def load_db() -> dict:
    """words_db.json を読み込む。存在しなければ空辞書を返す。"""
    if not WORDS_DB_PATH.exists():
        return {}
    with WORDS_DB_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db: dict) -> None:
    """words_db.json に書き込む。"""
    WORDS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with WORDS_DB_PATH.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_next_word_id(db: dict) -> str:
    """現在の最大IDの次のIDを返す。例：word30 → word31"""
    if not db:
        return "word1"
    nums = []
    for key in db:
        m = re.search(r"\d+", key)
        if m:
            nums.append(int(m.group()))
    return f"word{max(nums) + 1}"


def get_word(word_id: str) -> dict | None:
    """word_id に対応する単語データを返す。"""
    db = load_db()
    return db.get(word_id)


def list_words() -> list[dict]:
    """全単語をリスト形式で返す（word_id付き）。"""
    db = load_db()
    return [{"word_id": k, **v} for k, v in db.items()]


# ── 単語登録フロー ────────────────────────────────────────────────────

def register_word(display: str, reading: str) -> dict:
    """
    新しい単語を登録する。

    Parameters
    ----------
    display : 表示テキスト（漢字・カタカナ・ひらがなすべてOK）
    reading : ひらがな読み（Julius用・長音は「ー」で表記）

    Returns
    -------
    dict : 登録結果 {"word_id": ..., "accent": ..., "message": ...}
    """
    db      = load_db()
    word_id = get_next_word_id(db)

    # ── 1. アクセント型の取得 ─────────────────────────────────────
    accent, accent_source = get_accent(display)

    # ── 2. サンプル音声の生成（VOICEVOX） ────────────────────────
    sound_dir = RAW_AUDIO_DIR / "sound" / word_id
    sound_dir.mkdir(parents=True, exist_ok=True)
    wav_path  = sound_dir / f"{word_id}.wav"

    generate_sample_wav(display, wav_path)
    convert_to_16kHz(wav_path, wav_path)

    # ── 3. ひらがな読みを txt ファイルに書き込む（Julius用） ─────
    txt_path = sound_dir / f"{word_id}.txt"
    txt_path.write_text(reading, encoding="utf-8")

    # ── 4. Julius でアライメント実行 ──────────────────────────────
    # segment_julius.pl は wav/ ディレクトリを引数として受け取るが、
    # 基準音声は sound/ 以下にあるため、一時的に wav/ にコピーして実行
    tmp_wav  = RAW_AUDIO_DIR / "wav" / f"{word_id}.wav"
    tmp_txt  = RAW_AUDIO_DIR / "wav" / f"{word_id}.txt"
    shutil.copy(wav_path, tmp_wav)
    shutil.copy(txt_path, tmp_txt)

    try:
        perl_run()
        # アライメント結果を sound/ に移動
        tmp_lab = RAW_AUDIO_DIR / "wav" / f"{word_id}.lab"
        tmp_log = RAW_AUDIO_DIR / "wav" / f"{word_id}.log"
        if tmp_lab.exists():
            shutil.move(str(tmp_lab), str(sound_dir / f"{word_id}.lab"))
        if tmp_log.exists():
            shutil.move(str(tmp_log), str(sound_dir / f"{word_id}.log"))
    finally:
        # 一時ファイルを削除
        tmp_wav.unlink(missing_ok=True)
        tmp_txt.unlink(missing_ok=True)

    # ── 5. MFCC 計算・保存 ────────────────────────────────────────
    mfcc = audio_mfcc(wav_path)
    bin_path = AUDIO_MFCC_DIR / f"{word_id}.bin"
    mfcc.tofile(str(bin_path))

    # ── 6. words_db.json に追記 ───────────────────────────────────
    db[word_id] = {
        "display":       display,
        "reading":       reading,
        "accent":        accent,
        "accent_source": accent_source,
        "source":        "tts",
    }
    save_db(db)

    # ── 8. 既存の設定ファイルを更新 ──────────────────────────────
    _update_audio_scp(db)
    _update_words_txt(db)

    return {
        "word_id":  word_id,
        "accent":   accent,
        "accent_source": accent_source,
        "message":  f"{display} を {word_id} として登録しました",
    }


def _update_audio_scp(db: dict) -> None:
    """audio.scp を words_db の内容で再生成する。"""
    lines = [
        f"sound/{wid}/{wid}.wav"
        for wid in db
    ]
    AUDIO_SCP_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_words_txt(db: dict) -> None:
    """words.txt（DTW表示用）を words_db の内容で再生成する。"""
    lines = [entry["display"] for entry in db.values()]
    WORDS_TXT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_reading_for_julius(word_id: str) -> str:
    """Julius に渡すひらがな読みを返す。app.py の word_select() 代替。"""
    entry = get_word(word_id)
    if not entry:
        raise ValueError(f"不正な単語IDです: {word_id}")
    return entry["reading"]


def delete_word(word_id: str) -> dict:
    """
    単語を削除する。
    source=recorded（既存の人間録音）は削除不可。
    """
    db    = load_db()
    entry = db.get(word_id)
    if not entry:
        raise ValueError(f"{word_id} は登録されていません")
    if entry.get("source") == "recorded":
        raise ValueError("既存の録音済み単語は削除できません")

    # ファイル削除
    sound_dir = RAW_AUDIO_DIR / "sound" / word_id
    import shutil as _shutil
    if sound_dir.exists():
        _shutil.rmtree(str(sound_dir))

    bin_path = AUDIO_MFCC_DIR / f"{word_id}.bin"
    bin_path.unlink(missing_ok=True)

    from config import STATIC_DIR
    tts_path = STATIC_DIR / "tts" / f"{word_id}.wav"
    tts_path.unlink(missing_ok=True)

    del db[word_id]
    save_db(db)
    _update_audio_scp(db)
    _update_words_txt(db)

    return {"message": f"{entry['display']} を削除しました"}


def update_word(word_id: str, display: str, reading: str, accent) -> dict:
    """
    単語の表示テキスト・読み・アクセント型を更新する。
    """
    db    = load_db()
    entry = db.get(word_id)
    if not entry:
        raise ValueError(f"{word_id} は登録されていません")

    # アクセント型の更新（手動指定がなければMeCabで取得）
    if accent is not None and str(accent).isdigit():
        new_accent        = int(accent)
        new_accent_source = "manual"
    else:
        new_accent, new_accent_source = get_accent(display)

    db[word_id]["display"]       = display
    db[word_id]["reading"]       = reading
    db[word_id]["accent"]        = new_accent
    db[word_id]["accent_source"] = new_accent_source
    save_db(db)
    _update_audio_scp(db)
    _update_words_txt(db)

    return {
        "message": f"{display} を更新しました",
        "accent":  new_accent,
    }