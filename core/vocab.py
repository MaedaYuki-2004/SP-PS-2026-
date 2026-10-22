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

同じ単語の練習記録を1つの ID にまとめる（研究データを単語ごとに集計するため）：
  - 単語の「削除」は一覧から外すだけにする（"archived": true）。ID・お手本・練習記録は残し、元に戻せる。
    以前はお手本ごと消していたので、同じ単語を登録し直すと新しい ID になり、
    同じ単語の練習記録が2つの ID に分かれ、比べたお手本も前後で違っていた。
  - 表示と読みが同じ単語は新しく作らない（WordExists）。削除した単語なら元に戻してもらう（restore_word()）。
  - 以前の版で完全に消した単語と同じ単語を登録するときは、練習記録に残る前の ID を使う。
  - 完全に消す（purge_word()）のは、削除済みで練習記録が1つも無い単語だけ（打ち間違いの片付け用。先生モード）。
"""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from core.accent import get_accent
from core.alignment import run_alignment_on_file
from core.history import load_history
from core.library import current_library
from core.storage import write_json_atomic, write_text_atomic
from core.timbre import audio_mfcc


# ── DB 読み書き ───────────────────────────────────────────────────────

def load_db() -> dict:
    """words_db.json を読み込む（削除した単語も含む）。存在しなければ空辞書を返す。"""
    path = current_library().words_db
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db: dict) -> None:
    """words_db.json に書き込む（書きかけを読まれないよう、一時ファイルに書いてから置き換える）。"""
    write_json_atomic(current_library().words_db, db)


def is_archived(entry: dict) -> bool:
    """削除した（一覧から外した）単語か。"""
    return bool(entry.get("archived"))


def active_words_db() -> dict:
    """削除していない単語だけの words_db（画面・練習・分析に使う）。"""
    return {k: v for k, v in load_db().items() if not is_archived(v)}


def get_next_word_id(db: dict) -> str:
    """これまでに一度でも使われた最大IDの次のIDを返す。例：word30 → word31

    words_db にあるID（削除した単語も残っている）に加えて、お手本のフォルダと
    練習履歴にある ID も見る。古い版で完全に消した単語の ID を別の単語に振ると、
    消した単語の練習記録が新しい単語の記録として苦手音の分析などに混ざってしまうため。
    """
    lib  = current_library()
    nums = [0]
    for key in db:
        m = re.search(r"\d+", key)
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
    """word_id に対応する単語データを返す。削除した単語は None（練習には使わない）。"""
    entry = load_db().get(word_id)
    return None if entry is None or is_archived(entry) else entry


def list_words() -> list[dict]:
    """削除していない単語をリスト形式で返す（word_id付き）。"""
    return [{"word_id": k, **v} for k, v in active_words_db().items()]


def list_archived_words() -> list[dict]:
    """削除した単語を、新しく削除した順に返す。練習記録の数（records）を付ける。"""
    counts = _record_counts(load_history())
    words = [{"word_id": k, **v, "records": counts.get(k, 0)}
             for k, v in load_db().items() if is_archived(v)]
    words.sort(key=lambda w: w.get("archived_at") or "", reverse=True)
    return words


def _record_counts(history: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in history:
        wid = record.get("word_id")
        if wid:
            counts[wid] = counts.get(wid, 0) + 1
    return counts


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 同じ単語かどうか ─────────────────────────────────────────────────

def _norm_display(text: str) -> str:
    """表示テキストを比べるための形（全角・半角や空白の違いをそろえる）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def _norm_reading(text: str) -> str:
    """読みを比べるための形（カタカナで書いても、ひらがなと同じに扱う）。"""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in _norm_display(text))


def julius_reading(text: str) -> str:
    """読みを Julius に渡せる形（ひらがなと「ー」だけ）にする。カタカナはひらがなに直す。

    Julius はひらがなしか音に直せないので、カタカナや漢字のままだと、録画をいくらやり直しても
    「発音が認識できませんでした」になる（2026-10-20 の実機テストで「メダカ」「田んぼ」が失敗した）。
    ひらがなにできない文字があるときは ValueError（録画の前に、どの文字が使えないかを知らせる）。
    """
    reading = _norm_reading(text)
    # ゔ・ゕ・ゖ は Julius の変換表（scripts/segment_julius.pl の yomi2voca）に無い
    bad = sorted({c for c in reading if not ("ぁ" <= c <= "ゔ" or c == "ー") or c == "ゔ"}, key=reading.index)
    if not reading:
        raise ValueError("ひらがな読みを入力してください")
    if bad:
        raise ValueError(f"読みは、ひらがなで入力してください（使えない文字: {'・'.join(bad)}）")
    return reading


def word_key(display: str, reading: str) -> tuple[str, str]:
    """同じ単語かどうかを決める組（表示と読みを比べやすい形にしたもの）。"""
    return _norm_display(display), _norm_reading(reading)


def find_same_words(display: str, reading: str, exclude_id: str | None = None) -> dict:
    """登録・編集しようとしている単語と同じ単語・似ている単語を探す（削除した単語も含む）。

    exact    : 表示と読みが同じ単語。同じ単語とみなし、新しくは作らない
    similar  : 読みだけ、または表示だけが同じ単語。同音の別の語や打ち間違いのことがあるので、確かめてもらう
    previous : 以前の版で完全に消した同じ単語（練習記録にだけ残っている）の ID と記録の数
    各単語は {word_id, display, reading, archived, archived_at, records}。
    """
    d, r = _norm_display(display), _norm_reading(reading)
    db = load_db()
    history = load_history()
    counts = _record_counts(history)
    exact: list[dict] = []
    similar: list[dict] = []
    for wid, entry in db.items():
        if wid == exclude_id:
            continue
        same_display = _norm_display(entry.get("display", "")) == d
        same_reading = _norm_reading(entry.get("reading", "")) == r
        if not (same_display or same_reading):
            continue
        item = {"word_id": wid, "display": entry.get("display", ""), "reading": entry.get("reading", ""),
                "archived": is_archived(entry), "archived_at": entry.get("archived_at"),
                "records": counts.get(wid, 0)}
        (exact if same_display and same_reading else similar).append(item)

    previous = None
    if not exact:
        for record in history:   # 新しい順
            wid = record.get("word_id")
            if (wid and wid not in db and wid != exclude_id
                    and _norm_display(record.get("display", "")) == d
                    and _norm_reading(record.get("reading", "")) == r):
                previous = {"word_id": wid, "records": counts.get(wid, 0)}
                break
    return {"exact": exact, "similar": similar, "previous": previous}


class WordExists(ValueError):
    """表示と読みが同じ単語がすでにある（削除した単語を含む）。"""

    def __init__(self, word: dict):
        self.word = word
        name = f"「{word['display']}」（{word['reading']}）"
        if word["archived"]:
            message = f"前に削除した{name}があります。新しく作らずに、元に戻してください（練習の記録がつながります）。"
        else:
            message = f"{name}はもう登録されています。"
        super().__init__(message)


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

    表示と読みが同じ単語があるときは WordExists（削除した単語なら、元に戻してもらう）。
    以前の版で完全に消した同じ単語は、練習記録に残る前の ID で登録する（reused: True）。

    Returns
    -------
    dict : 登録結果 {"word_id": ..., "accent": ..., "alignment_ok": ..., "reused": ..., "message": ...}
    """
    reading = julius_reading(reading)
    lib     = current_library()
    db      = load_db()
    same    = find_same_words(display, reading)
    if same["exact"]:
        raise WordExists(same["exact"][0])
    previous = same["previous"]
    word_id  = previous["word_id"] if previous else get_next_word_id(db)

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

    message = f"{display} を {word_id} として登録しました"
    if previous:
        message += f"（前に練習した {previous['records']} 回の記録とつなげました）"
    return {
        "word_id":       word_id,
        "accent":        accent,
        "accent_source": accent_source,
        "alignment_ok":  alignment_ok,
        "reused":        bool(previous),
        "message":       message,
    }


def _update_audio_scp(db: dict) -> None:
    """audio.scp を words_db の内容（削除した単語を除く）で再生成する。"""
    lines = [
        f"sound/{wid}/{wid}.wav"
        for wid, entry in db.items() if not is_archived(entry)
    ]
    write_text_atomic(current_library().audio_scp, "\n".join(lines) + "\n")


def _update_words_txt(db: dict) -> None:
    """words.txt（DTW表示用）を words_db の内容（削除した単語を除く）で再生成する。"""
    lines = [entry["display"] for entry in db.values() if not is_archived(entry)]
    write_text_atomic(current_library().words_txt, "\n".join(lines) + "\n")


def get_reading_for_julius(word_id: str) -> str:
    """Julius に渡すひらがな読みを返す。"""
    entry = get_word(word_id)
    if not entry:
        raise ValueError(f"不正な単語IDです: {word_id}")
    return entry["reading"]


# ── 削除・元に戻す・完全に消す ───────────────────────────────────────

def archive_word(word_id: str) -> dict:
    """
    単語を削除する（一覧から外すだけ。ID・お手本・練習記録は残し、元に戻せる）。
    source=recorded（既存の人間録音）は削除不可。
    """
    db    = load_db()
    entry = db.get(word_id)
    if not entry or is_archived(entry):
        raise ValueError(f"{word_id} は登録されていません")
    if entry.get("source") == "recorded":
        raise ValueError("初期収録単語は削除できません")
    entry["archived"]    = True
    entry["archived_at"] = _now()
    save_db(db)
    _update_audio_scp(db)
    _update_words_txt(db)
    return {"message": f"{entry['display']} を削除しました（練習の記録は残ります。単語のページの「削除した単語」から元に戻せます）"}


def restore_word(word_id: str) -> dict:
    """削除した単語を元に戻す（同じ ID・同じお手本なので、練習記録がつながる）。"""
    db    = load_db()
    entry = db.get(word_id)
    if not entry:
        raise ValueError(f"{word_id} は登録されていません")
    if not is_archived(entry):
        return {"message": f"{entry['display']} は削除されていません", "word_id": word_id}
    same = find_same_words(entry.get("display", ""), entry.get("reading", ""), exclude_id=word_id)
    active = [w for w in same["exact"] if not w["archived"]]
    if active:
        raise ValueError(f"同じ単語「{active[0]['display']}」（{active[0]['word_id']}）がもう登録されているので、"
                         "元に戻せません。")
    entry.pop("archived", None)
    entry.pop("archived_at", None)
    save_db(db)
    _update_audio_scp(db)
    _update_words_txt(db)
    return {"message": f"{entry['display']} を元に戻しました", "word_id": word_id}


def purge_word(word_id: str) -> dict:
    """削除した単語を、お手本の録音・録画ごと完全に消す。

    打ち間違えて登録した単語などを片付けるためのもの（app.py は先生モードのときだけ受け付ける）。
    練習記録がある単語は、記録を分析に使うので消さない（ID と単語の対応が分からなくなるため）。
    """
    db    = load_db()
    entry = db.get(word_id)
    if not entry:
        raise ValueError(f"{word_id} は登録されていません")
    if not is_archived(entry):
        raise ValueError("完全に消せるのは、削除した単語だけです。先に削除してください。")
    # scripts/merge_split_words.py でほかの ID にまとめた記録も、このお手本と比べた点数なので数える
    if any(word_id in (r.get("word_id"), r.get("merged_from")) for r in load_history()):
        raise ValueError("練習の記録がある単語は、記録を分析に使うため完全には消せません。")
    _remove_word(db, word_id)
    return {"message": f"{entry['display']} を完全に消しました"}


def discard_new_word(word_id: str) -> None:
    """登録したばかりの単語を取り消す（お手本の音声認識に失敗したとき。app.py の add_word）。"""
    db = load_db()
    if word_id in db:
        _remove_word(db, word_id)


def _remove_word(db: dict, word_id: str) -> None:
    # DBから削除（ファイル削除より先に行い、ファイルが消せなくても単語は消える）
    del db[word_id]
    save_db(db)
    _update_audio_scp(db)
    _update_words_txt(db)

    # ファイル削除（Windows でブラウザがファイルを掴んでいる場合も無視して続行）
    lib       = current_library()
    sound_dir = lib.sound_dir(word_id)
    try:
        if sound_dir.exists():
            shutil.rmtree(str(sound_dir))
    except OSError:
        pass

    try:
        lib.mfcc_bin(word_id).unlink(missing_ok=True)
    except OSError:
        pass


# ── 単語の情報の更新 ─────────────────────────────────────────────────

def _active_entry(db: dict, word_id: str) -> dict:
    entry = db.get(word_id)
    if not entry or is_archived(entry):
        raise ValueError(f"{word_id} は登録されていません")
    return entry


def update_word_tags(word_id: str, tags: list[str]) -> dict:
    """単語のタグリストを更新する。"""
    db    = load_db()
    entry = _active_entry(db, word_id)
    entry["tags"] = [t.strip() for t in tags if t.strip()]
    save_db(db)
    return {"message": f"{entry['display']} のタグを更新しました", "tags": entry["tags"]}


def get_all_tags() -> list[str]:
    """削除していない単語に付いているタグの重複なしリストを返す。"""
    tags = set()
    for entry in active_words_db().values():
        for t in entry.get("tags", []):
            tags.add(t)
    return sorted(tags)


def update_word_note(word_id: str, note: str) -> dict:
    """単語の発音メモを更新する。"""
    db    = load_db()
    entry = _active_entry(db, word_id)
    entry["note"] = note.strip()
    save_db(db)
    return {"message": f"{entry['display']} のメモを更新しました"}


def update_word(word_id: str, display: str, reading: str, accent) -> dict:
    """
    単語の表示テキスト・読み・アクセント型を更新する。
    ほかの単語（削除した単語を含む）と表示・読みが同じになる変更は受け付けない（WordExists）。
    """
    reading = julius_reading(reading)
    db    = load_db()
    entry = _active_entry(db, word_id)
    same  = find_same_words(display, reading, exclude_id=word_id)
    if same["exact"]:
        raise WordExists(same["exact"][0])

    # アクセント型の更新（手動指定がなければMeCabで取得）
    if accent is not None and str(accent).isdigit():
        new_accent        = int(accent)
        new_accent_source = "manual"
    else:
        new_accent, new_accent_source = get_accent(display)

    entry["display"]       = display
    entry["reading"]       = reading
    entry["accent"]        = new_accent
    entry["accent_source"] = new_accent_source
    save_db(db)
    _update_audio_scp(db)
    _update_words_txt(db)

    return {
        "message": f"{display} を更新しました",
        "accent":  new_accent,
    }
