"""
scripts/copy_shared_words.py
生徒に分ける前の共有の単語とお手本（data/config/words_db.json など）を、
生徒のフォルダ（data/users/<ID>/）へ複製するスクリプト。

単語とお手本は生徒ごとに分けて保存する（core/userdata.py）。共有の単語は
ログインした生徒には見えないので、最初の単語セットとして配りたいときに使う。
複製なので、その後に保護者が変えても他の生徒や共有の単語には影響しない。

使い方（リポジトリの一番上のフォルダで）:
  python scripts/copy_shared_words.py A01
  python scripts/copy_shared_words.py A01 A02 A03

すでに単語がある生徒には複製しない（同じ word_id が別の単語を指してしまうため）。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import accounts, usercontext, userdata  # noqa: E402
from core.vocab import _update_audio_scp, _update_words_txt, load_db, save_db  # noqa: E402


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def copy_to(user_id: str) -> bool:
    print(f"\n[{user_id}]")
    if not accounts.account_exists(user_id):
        print("  [NG] この利用者IDは登録されていません")
        return False

    usercontext.set_current_user(None)
    shared_db    = load_db()
    shared_refs  = _load_json(userdata.lip_refs_path())
    shared_sound = userdata.sound_root()
    shared_mfcc  = userdata.mfcc_dir()
    if not shared_db:
        print("  [NG] 共有の単語がありません（data/config/words_db.json）")
        return False

    usercontext.set_current_user(user_id)
    try:
        if load_db():
            print("  [NG] すでに単語があるため複製しません")
            return False
        for word_id in shared_db:
            src = shared_sound / word_id
            if src.exists():
                shutil.copytree(src, userdata.sound_dir(word_id), dirs_exist_ok=True)
            bin_src = shared_mfcc / f"{word_id}.bin"
            if bin_src.exists():
                userdata.mfcc_path(word_id).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bin_src, userdata.mfcc_path(word_id))
        refs = {k: v for k, v in shared_refs.items() if k in shared_db}
        if refs:
            path = userdata.lip_refs_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
        save_db(shared_db)
        _update_audio_scp(shared_db)
        _update_words_txt(shared_db)
    finally:
        usercontext.clear()
    print(f"  [OK] {len(shared_db)} 語を複製しました（お手本の口形 {len(refs)} 語）")
    return True


if __name__ == "__main__":
    targets = sys.argv[1:]
    if not targets:
        print("使い方: python scripts/copy_shared_words.py A01 [A02 ...]")
        sys.exit(1)
    results = [copy_to(uid) for uid in targets]
    print(f"\n完了しました（成功 {sum(results)} / {len(results)}）。")
    sys.exit(0 if all(results) else 1)
