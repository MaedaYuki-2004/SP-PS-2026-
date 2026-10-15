"""
scripts/merge_split_words.py
同じ単語の練習記録が、2つ以上の単語 ID に分かれていないかを調べ、必要なら1つにまとめる。

以前の版では、単語を削除するとお手本ごと消え、同じ単語を登録し直すと新しい ID になった
（例：「温度」の記録が word3 と word7 に分かれる）。今の版は同じ単語を新しく作らない（core/vocab.py）が、
それより前に集めたデータには、分かれた記録が残っていることがある。

  python scripts/merge_split_words.py                 # 調べて報告するだけ（何も書きかえない）
  python scripts/merge_split_words.py --apply         # 1つの ID にまとめる（先にバックアップを作る）
  python scripts/merge_split_words.py --user A01      # 1人だけ

同じ単語とみなすのは、表示と読みが同じもの（全角・半角、空白、カタカナの読みの違いはそろえる）。
まとめ先は、使っている（削除していない）単語の ID。その中では記録の多い ID、無ければ記録の多い ID。
--apply では次を行う。
  - history.json の記録の word_id をまとめ先に書きかえ、元の ID を "merged_from" に残す
    （比べたお手本は元の単語のものなので、分析でお手本ごとに分けたいときに使う）
  - まとめ先でない単語は削除した扱いにし（"archived"）、"merged_into" にまとめ先を書く。
    お手本の録音・録画は消さない
  - 書きかえる前に history.json・words_db.json を <名前>.bak-<日時> にコピーする
同じ参加者の処理とぶつからないよう、参加者ごとにサーバーと同じロックを取るので、サーバーを動かしたままでも使える。
データの置き場所は app.py と同じ（環境変数 DATA_DIR があればそこ）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR  # noqa: E402
from core import storage  # noqa: E402
from core.vocab import word_key  # noqa: E402


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def find_splits(words_db: dict, history: list[dict]) -> list[dict]:
    """同じ単語が2つ以上の ID にあるものを返す。[{display, reading, target, ids: [{word_id, status, records}]}]"""
    groups: dict[tuple[str, str], dict[str, dict]] = {}

    def add(key, wid, display, reading):
        group = groups.setdefault(key, {})
        info = group.setdefault(wid, {"word_id": wid, "display": display, "reading": reading,
                                      "status": "記録だけ", "records": 0})
        return info

    for wid, entry in words_db.items():
        info = add(word_key(entry.get("display", ""), entry.get("reading", "")), wid,
                   entry.get("display", ""), entry.get("reading", ""))
        info["status"] = "削除済み" if entry.get("archived") else "使用中"
    for record in history:
        wid = record.get("word_id")
        if not wid:
            continue
        add(word_key(record.get("display", ""), record.get("reading", "")), wid,
            record.get("display", ""), record.get("reading", ""))["records"] += 1

    splits = []
    for group in groups.values():
        if len(group) < 2:
            continue
        ids = sorted(group.values(), key=lambda i: (i["status"] != "使用中", -i["records"], _num(i["word_id"])))
        splits.append({"display": ids[0]["display"], "reading": ids[0]["reading"],
                       "target": ids[0]["word_id"], "ids": ids})
    return splits


def _num(word_id: str) -> int:
    digits = "".join(c for c in word_id if c.isdigit())
    return int(digits) if digits else 0


def merge(user_dir: Path, splits: list[dict], words_db: dict, history: list[dict]) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_path = user_dir / "library" / "words_db.json"
    history_path = user_dir / "history.json"
    for path in (history_path, db_path):
        if path.exists():
            shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
    moved = 0
    now = datetime.now().isoformat(timespec="seconds")
    for split in splits:
        target = split["target"]
        others = {i["word_id"] for i in split["ids"]} - {target}
        for record in history:
            if record.get("word_id") in others:
                record.setdefault("merged_from", record["word_id"])
                record["word_id"] = target
                moved += 1
        for wid in others:
            entry = words_db.get(wid)
            if entry is not None:
                entry.setdefault("archived", True)
                entry.setdefault("archived_at", now)
                entry["merged_into"] = target
    storage.write_json_atomic(history_path, history)
    if db_path.exists() or words_db:
        storage.write_json_atomic(db_path, words_db)
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description="同じ単語の練習記録が複数の ID に分かれていないか調べ、まとめる")
    parser.add_argument("--apply", action="store_true", help="1つの ID にまとめる（指定しなければ報告だけ）")
    parser.add_argument("--user", help="この利用者 ID だけを調べる")
    args = parser.parse_args()

    users_root = DATA_DIR / "users"
    user_dirs = sorted(p for p in users_root.iterdir() if p.is_dir()) if users_root.exists() else []
    if args.user:
        user_dirs = [p for p in user_dirs if p.name == args.user]
    found = 0
    for user_dir in user_dirs:
        uid = user_dir.name
        with storage.lock(f"user-{uid}", timeout=120):
            words_db = _load(user_dir / "library" / "words_db.json", {})
            history = _load(user_dir / "history.json", [])
            splits = find_splits(words_db, history)
            if not splits:
                continue
            found += len(splits)
            print(f"\n{uid}:")
            for s in splits:
                parts = "、".join(f"{i['word_id']}（{i['status']}・記録 {i['records']} 回）" for i in s["ids"])
                print(f"  「{s['display']}」（{s['reading']}）: {parts} → {s['target']} にまとめる")
            if args.apply:
                moved = merge(user_dir, splits, words_db, history)
                print(f"  → {moved} 件の記録の ID を書きかえました（バックアップ: *.bak-<日時>）")
    if not found:
        print("分かれている単語はありませんでした。")
    elif not args.apply:
        print(f"\n分かれている単語が {found} 個あります。まとめるときは --apply を付けて実行してください。")


if __name__ == "__main__":
    main()
