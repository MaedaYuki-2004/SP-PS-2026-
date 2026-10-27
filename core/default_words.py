"""
core/default_words.py
はじめから入っている単語（初期単語）のセット。

参加者の単語とお手本は参加者ごとに持つ（core/library.py）ので、新しい参加者は単語が0個から始まる。
初期単語のセットを用意しておくと、アカウントを作ったときにその参加者のライブラリへ写す。

  1. 研究者が見本用のアカウントで、ふつうに単語を登録する（お手本の録画もふつうに録る）
  2. scripts/default_words.py save <見本の利用者ID> --apply で、その単語を初期単語のセットとして保存する
     （data/default_words/。単語 ID は word1, word2, … に振り直す）
  3. 新しいアカウントを作ると、そのセットが写される（app.py の admin_accounts_create）。
     すでにあるアカウントには scripts/default_words.py install --all --apply で写す

写すときの決まり:
  - 単語 ID は写す先の参加者の続きの番号にする（vocab.get_next_word_id()）
  - 表示と読みが同じ単語（削除した単語を含む）が写す先にあれば写さない（同じ単語は新しく作らない。README §9.6）
  - 以前の版で完全に消した同じ単語が練習記録にだけ残っていれば、その ID を使う（register_word() と同じ）
  - お手本のファイルは中身ごと写すので、お手本ID（ref_id）は見本と同じになる（同じお手本と比べた点数と分かる）
  - 写した単語は source: "default"。ふつうの単語と同じように削除・元に戻すができる
"""
from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from config import DATA_DIR
from core import storage, usercontext, vocab
from core.library import Library, library_for

# お手本と一緒に写すファイル（sound/<word_id>/<word_id><拡張子>）。.web.webm は生徒に送る小さい動画
_SOUND_SUFFIXES = (".wav", ".lab", ".log", ".txt", ".webm", ".web.webm")
# お手本の採点に欠かせないファイル
_REQUIRED_SUFFIXES = (".wav", ".lab", ".webm")
# 単語のデータのうち、写す項目
_ENTRY_KEYS = ("display", "reading", "accent", "accent_source", "note", "tags")

_LOCK = "default-words"


def default_dir() -> Path:
    return DATA_DIR / "default_words"


def _library_at(root: Path) -> Library:
    return Library(
        words_db=root / "words_db.json",
        audio_scp=root / "audio.scp",
        words_txt=root / "words.txt",
        sound_root=root / "sound",
        mfcc_dir=root / "mfcc",
        lip_refs=root / "lip_refs.json",
        formant_cache=root / "formant_cache.json",
        shared=False,
    )


def default_library() -> Library:
    return _library_at(default_dir())


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def list_default_words() -> list[dict]:
    """初期単語のセット（並び順のまま）。セットがまだ無いときは空。"""
    db = _read_json(default_library().words_db)
    return [{"word_id": wid, **entry} for wid, entry in db.items()]


@contextmanager
def _as_user(user_id: str):
    """vocab の関数（ログイン中の参加者のライブラリを使う）を、user_id の分として動かす。"""
    before = usercontext.current_user()
    usercontext.set_current_user(user_id)
    try:
        yield
    finally:
        usercontext.set_current_user(before)


def _copy_sound(src: Library, src_id: str, dest: Library, dest_id: str) -> None:
    dest_dir = dest.sound_dir(dest_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for suffix in _SOUND_SUFFIXES:
        f = src.sound_dir(src_id) / f"{src_id}{suffix}"
        if f.exists():
            shutil.copy2(f, dest_dir / f"{dest_id}{suffix}")
    mfcc = src.mfcc_bin(src_id)
    if mfcc.exists():
        dest.mfcc_bin(dest_id).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mfcc, dest.mfcc_bin(dest_id))


# ── 初期単語のセットを作る ───────────────────────────────────────────

def plan_save(user_id: str, word_ids: list[str] | None = None) -> list[dict]:
    """user_id の単語から、初期単語のセットにする単語を選ぶ（何も書きかえない）。

    word_ids を省くと、削除していない単語をすべて（登録の順に）使う。
    お手本の録音・音の区切り・録画のどれかが無い単語があれば ValueError。
    各単語は {word_id（新しい ID）, from（見本の ID）, display, reading, lip（口形データがあるか）}。
    """
    src = library_for(user_id)
    db = _read_json(src.words_db)
    if word_ids is None:
        word_ids = [wid for wid, e in db.items() if not vocab.is_archived(e)]
    if not word_ids:
        raise ValueError(f"{user_id} には単語がありません")
    lip = _read_json(src.lip_refs)
    plan, errors = [], []
    for wid in word_ids:
        entry = db.get(wid)
        if entry is None or vocab.is_archived(entry):
            errors.append(f"{wid} は {user_id} の単語にありません（削除した単語も使えません）")
            continue
        missing = [s for s in _REQUIRED_SUFFIXES if not (src.sound_dir(wid) / f"{wid}{s}").exists()]
        if missing:
            errors.append(f"{wid}（{entry.get('display')}）のお手本のファイルがありません: {'・'.join(missing)}")
            continue
        plan.append({"word_id": f"word{len(plan) + 1}", "from": wid,
                     "display": entry.get("display", ""), "reading": entry.get("reading", ""),
                     "lip": wid in lip})
    if errors:
        raise ValueError("\n".join(errors))
    return plan


def save_from_user(user_id: str, word_ids: list[str] | None = None) -> list[dict]:
    """user_id の単語とお手本を、初期単語のセットとして保存する（前のセットと置きかえる）。"""
    with storage.lock(f"user-{user_id}", timeout=120), storage.lock(_LOCK, timeout=120):
        plan = plan_save(user_id, word_ids)
        src = library_for(user_id)
        src_db = _read_json(src.words_db)
        src_lip = _read_json(src.lip_refs)

        # 別のフォルダに作ってから置きかえる（途中で止まっても、前のセットが半端に壊れない）
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        root = default_dir()
        tmp_root = root.with_name(f"{root.name}.new-{stamp}")
        tmp = _library_at(tmp_root)
        db, lip = {}, {}
        try:
            for item in plan:
                _copy_sound(src, item["from"], tmp, item["word_id"])
                entry = src_db[item["from"]]
                db[item["word_id"]] = {k: entry[k] for k in _ENTRY_KEYS if k in entry}
                if item["from"] in src_lip:
                    lip[item["word_id"]] = src_lip[item["from"]]
            storage.write_json_atomic(tmp.words_db, db)
            storage.write_json_atomic(tmp.lip_refs, lip, indent=None)

            old = root.with_name(f"{root.name}.old-{stamp}")
            if root.exists():
                root.rename(old)
            tmp_root.rename(root)
        except BaseException:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise
        shutil.rmtree(old, ignore_errors=True)
        return plan


# ── 参加者へ写す ─────────────────────────────────────────────────────

def install_for_user(user_id: str, dry_run: bool = False) -> dict:
    """初期単語のセットを user_id のライブラリへ写す。

    戻り値 {"added": [{word_id, from, display, reading, reused}], "skipped": [{from, display, reading, reason}]}。
    dry_run のときは何も書きかえずに、写すとどうなるかだけを返す。
    呼ぶ側は user-<user_id> のロックを持たずに呼ぶ（ここで取る）。
    """
    with storage.lock(f"user-{user_id}", timeout=120), storage.lock(_LOCK, timeout=120), _as_user(user_id):
        return _install(user_id, dry_run)


def _install(user_id: str, dry_run: bool) -> dict:
    src = default_library()
    src_db = _read_json(src.words_db)
    src_lip = _read_json(src.lip_refs)
    dest = library_for(user_id)
    db = vocab.load_db()
    lip = _read_json(dest.lip_refs)
    added, skipped = [], []

    for src_id, entry in src_db.items():
        display, reading = entry.get("display", ""), entry.get("reading", "")
        same = vocab.find_same_words(display, reading)
        # dry_run では db をディスクに書かないので、このセットの中で先に写した単語もここで見る
        planned = vocab.word_key(display, reading) in {vocab.word_key(a["display"], a["reading"]) for a in added}
        if same["exact"] or planned:
            skipped.append({"from": src_id, "display": display, "reading": reading,
                            "reason": "同じ単語がもうあります"})
            continue
        previous = same["previous"]
        # get_next_word_id() は db も見るので、dry_run でも下で db に足せば番号が進む
        word_id = previous["word_id"] if previous else vocab.get_next_word_id(db)
        added.append({"word_id": word_id, "from": src_id, "display": display, "reading": reading,
                      "reused": bool(previous)})

        new_entry = {k: entry[k] for k in _ENTRY_KEYS if k in entry}
        new_entry["source"] = "default"
        db[word_id] = new_entry
        if dry_run:
            continue
        # お手本のファイルを置いてから一覧に足す（一覧にあるのにお手本が無い時間を作らない）
        _copy_sound(src, src_id, dest, word_id)
        if src_id in src_lip:
            lip[word_id] = src_lip[src_id]
            storage.write_json_atomic(dest.lip_refs, lip, indent=None)
        vocab.save_db(db)

    if added and not dry_run:
        vocab._update_audio_scp(db)
        vocab._update_words_txt(db)
    return {"added": added, "skipped": skipped}
