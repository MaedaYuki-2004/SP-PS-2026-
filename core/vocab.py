"""
core/vocab.py
words_db.json の読み書きと単語登録フローを担当するモジュール。

単語とお手本の置き場所は core/library.py の current_library()（ログイン中の参加者ごと）。

単語登録の流れ：
  1. display（表示テキスト）と reading（ひらがな読み）、wav_path（参照音声）を受け取る
  2. MeCab でアクセント型を自動取得
  3. wav_path を sound/{word_id}/{word_id}.wav にコピー
  4. Julius でアライメントを自動実行
  5. MFCC を自動計算・保存
  6. words_db.json に追記（source: "manual"）
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from config import STATIC_DIR
from core.accent import get_accent
from core.alignment import run_alignment_on_file
from core.history import load_history
from core.library import current_library
from core.timbre import audio_mfcc


# ── DB 読み書き ───────────────────────────────────────────────────────

def load_db() -> dict:
    """words_db.json を読み込む。存在しなければ空辞書を返す。"""
    path = current_library().words_db
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db: dict) -> None:
    """words_db.json に書き込む。"""
    path = current_library().words_db
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_next_word_id(db: dict) -> str:
    """これまでに一度でも使われた最大IDの次のIDを返す。例：word30 → word31

    words_db に現存するIDだけでなく、単語削除後も残る痕跡
    （静的サンプル音声・録音済みディレクトリ）も見て最大値を取る。
    words_db だけを見ると、単語を全削除した直後は再び "word1" から
    採番されてしまい、初期データセット由来の
    web/static/sample/word1.wav（VOICEVOXの「おんど」等）が
    新しく登録した別の単語のサンプル音声として誤って再生される
    （sample_audio() は static/sample を優先するため）。

    練習履歴にある ID も見る。単語を消すと sound/ のフォルダも消えるので、
    履歴を見ないと同じ ID が別の単語に振られ、消した単語の練習記録が
    新しい単語の記録として苦手音の分析などに混ざってしまう。
    """
    lib  = current_library()
    nums = [0]
    for key in db:
        m = re.search(r"\d+", key)
        if m:
            nums.append(int(m.group()))

    sample_dir = STATIC_DIR / "sample"
    if lib.shared and sample_dir.exists():
        for p in sample_dir.glob("word*.wav"):
            m = re.search(r"\d+", p.stem)
            if m:
                nums.append(int(m.group()))

    sound_dir = lib.sound_root
    if sound_dir.exists():
        for p in sound_dir.iterdir():
            if p.is_dir():
                m = re.search(r"\d+", p.name)
                if m:
                    nums.append(int(m.group()))

    for record in load_history():
        m = re.search(r"\d+", str(record.get("word_id") or ""))
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

def register_word(display: str, reading: str, wav_path: Path | None = None) -> dict:
    """
    新しい単語を登録する。

    Parameters
    ----------
    display  : 表示テキスト（漢字・カタカナ・ひらがなすべてOK）
    reading  : ひらがな読み（Julius用・長音は「ー」で表記）
    wav_path : 登録用音声（16kHz/モノラル/16bit PCM WAV）。
               None の場合は DB 登録のみ。音声は後から upload_lip_video で追加する。

    Returns
    -------
    dict : 登録結果 {"word_id": ..., "accent": ..., "alignment_ok": ..., "message": ...}
    """
    lib     = current_library()
    db      = load_db()
    word_id = get_next_word_id(db)

    # ── 1. アクセント型の取得 ─────────────────────────────────────
    accent, accent_source = get_accent(display)

    sound_dir = lib.sound_dir(word_id)
    sound_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. ひらがな読みを txt ファイルに書き込む（Julius用） ─────
    txt_path = sound_dir / f"{word_id}.txt"
    txt_path.write_text(reading, encoding="utf-8")

    alignment_ok = False
    if wav_path is not None:
        # ── 3. 音声を sound/{word_id}/ に配置 ────────────────────
        native_wav = sound_dir / f"{word_id}.wav"
        shutil.copy2(wav_path, native_wav)

        # ── 4. Julius でアライメント実行 ──────────────────────────
        native_lab = sound_dir / f"{word_id}.lab"
        native_log = sound_dir / f"{word_id}.log"
        try:
            run_alignment_on_file(native_wav, reading, native_lab, native_log)
            alignment_ok = native_lab.exists()
        except Exception as exc:
            # ここで握りつぶすとサーバーコンソールに何も残らず、
            # 「発音が認識できませんでした」以上の手がかりが完全に消える。
            # 環境差異（ffmpeg/Julius/perlの設定不備など）の切り分けに
            # 必須なので、失敗理由は必ずログに残す。
            print(f"[register_word] アライメント失敗 word_id={word_id} reading={reading!r}: {exc}")

        # ── 5. MFCC 計算・保存 ────────────────────────────────────
        try:
            mfcc = audio_mfcc(native_wav)
            bin_path = lib.mfcc_bin(word_id)
            bin_path.parent.mkdir(parents=True, exist_ok=True)
            mfcc.tofile(str(bin_path))
        except Exception:
            pass

    # ── 6. words_db.json に追記 ───────────────────────────────────
    db[word_id] = {
        "display":       display,
        "reading":       reading,
        "accent":        accent,
        "accent_source": accent_source,
        "source":        "manual",
    }
    save_db(db)

    # ── 7. 既存の設定ファイルを更新 ──────────────────────────────
    _update_audio_scp(db)
    _update_words_txt(db)

    return {
        "word_id":       word_id,
        "accent":        accent,
        "accent_source": accent_source,
        "alignment_ok":  alignment_ok,
        "message":       f"{display} を {word_id} として登録しました",
    }


def _update_audio_scp(db: dict) -> None:
    """audio.scp を words_db の内容で再生成する。"""
    lines = [
        f"sound/{wid}/{wid}.wav"
        for wid in db
    ]
    path = current_library().audio_scp
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_words_txt(db: dict) -> None:
    """words.txt（DTW表示用）を words_db の内容で再生成する。"""
    lines = [entry["display"] for entry in db.values()]
    path = current_library().words_txt
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        raise ValueError("初期収録単語は削除できません")

    # DBから削除（ファイル削除より先に行い、ファイルが消せなくても単語は消える）
    del db[word_id]
    save_db(db)
    _update_audio_scp(db)
    _update_words_txt(db)

    # ファイル削除（Windows でブラウザがファイルを掴んでいる場合も無視して続行）
    import shutil as _shutil
    lib       = current_library()
    sound_dir = lib.sound_dir(word_id)
    try:
        if sound_dir.exists():
            _shutil.rmtree(str(sound_dir))
    except OSError:
        pass

    try:
        lib.mfcc_bin(word_id).unlink(missing_ok=True)
    except OSError:
        pass

    if lib.shared:
        try:
            (STATIC_DIR / "tts" / f"{word_id}.wav").unlink(missing_ok=True)
        except OSError:
            pass

    return {"message": f"{entry['display']} を削除しました"}


def update_word_tags(word_id: str, tags: list[str]) -> dict:
    """単語のタグリストを更新する。"""
    db    = load_db()
    entry = db.get(word_id)
    if not entry:
        raise ValueError(f"{word_id} は登録されていません")
    db[word_id]["tags"] = [t.strip() for t in tags if t.strip()]
    save_db(db)
    return {"message": f"{entry['display']} のタグを更新しました", "tags": db[word_id]["tags"]}


def get_all_tags() -> list[str]:
    """全単語に付いているタグの重複なしリストを返す。"""
    db   = load_db()
    tags = set()
    for entry in db.values():
        for t in entry.get("tags", []):
            tags.add(t)
    return sorted(tags)


def update_word_note(word_id: str, note: str) -> dict:
    """単語の発音メモを更新する。"""
    db    = load_db()
    entry = db.get(word_id)
    if not entry:
        raise ValueError(f"{word_id} は登録されていません")
    db[word_id]["note"] = note.strip()
    save_db(db)
    return {"message": f"{entry['display']} のメモを更新しました"}


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