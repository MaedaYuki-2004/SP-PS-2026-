"""
scripts/default_words.py
はじめから入っている単語（初期単語）のセットを作る・見る・参加者へ写すスクリプト（core/default_words.py）。

使い方（リポジトリの一番上のフォルダで。サーバーを動かしたままでよい）:
    python scripts/default_words.py show
        いまの初期単語のセットを表示する

    python scripts/default_words.py save <見本の利用者ID>                     # 何を保存するか表示するだけ
    python scripts/default_words.py save <見本の利用者ID> --apply             # 保存する（前のセットと置きかえる）
    python scripts/default_words.py save <見本の利用者ID> --words word1 word3 --apply
        見本のアカウントの単語（--words を省くと、削除していない単語すべて）を初期単語のセットにする

    python scripts/default_words.py install --all                 # 写すとどうなるか表示するだけ
    python scripts/default_words.py install --all --apply         # すべての参加者へ写す
    python scripts/default_words.py install --user A01 --apply    # 1人だけ
        すでにあるアカウントへ初期単語を写す。同じ単語がもうある参加者には、その単語を写さない
        （何度実行しても同じ単語が増えることはない）。新しく作るアカウントには自動で写る。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import accounts as acc  # noqa: E402
from core import default_words as dw  # noqa: E402

EXPECTED_COUNT = 5


def _show() -> int:
    words = dw.list_default_words()
    if not words:
        print(f"初期単語のセットはまだありません（{dw.default_dir()}）")
        return 0
    print(f"初期単語のセット（{len(words)} 個。{dw.default_dir()}）")
    for w in words:
        print(f"  {w['word_id']:>7}  {w.get('display', '')}（{w.get('reading', '')}）")
    return 0


def _save(user_id: str, word_ids: list[str] | None, apply: bool) -> int:
    if not acc.account_exists(user_id):
        print(f"利用者ID {user_id} は登録されていません", file=sys.stderr)
        return 1
    try:
        plan = dw.save_from_user(user_id, word_ids) if apply else dw.plan_save(user_id, word_ids)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"{user_id} の単語 {len(plan)} 個を初期単語のセットに{'しました' if apply else 'します'}")
    for item in plan:
        lip = "" if item["lip"] else "  ※口形データなし（口の形の比べは出ません）"
        print(f"  {item['from']:>7} → {item['word_id']:<7} {item['display']}（{item['reading']}）{lip}")
    if len(plan) != EXPECTED_COUNT:
        print(f"※ {EXPECTED_COUNT} 個ではありません（{len(plan)} 個）。--words で選べます")
    if not apply:
        print("保存するときは --apply を付けてください")
    return 0


def _install(user_ids: list[str], apply: bool) -> int:
    if not dw.list_default_words():
        print("初期単語のセットがまだありません。先に save で作ってください", file=sys.stderr)
        return 1
    known = {a["user_id"] for a in acc.list_accounts()}
    unknown = [u for u in user_ids if u not in known]
    if unknown:
        print(f"登録されていない利用者ID: {', '.join(unknown)}", file=sys.stderr)
        return 1
    for uid in user_ids:
        result = dw.install_for_user(uid, dry_run=not apply)
        print(f"{uid}: {len(result['added'])} 個を{'写しました' if apply else '写します'}"
              f"（とばす {len(result['skipped'])} 個）")
        for a in result["added"]:
            note = "  ※前の練習記録の ID を使う" if a["reused"] else ""
            print(f"    + {a['word_id']:<7} {a['display']}（{a['reading']}）{note}")
        for s in result["skipped"]:
            print(f"    - {s['display']}（{s['reading']}）: {s['reason']}")
    if not apply:
        print("写すときは --apply を付けてください")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初期単語のセットを作る・見る・参加者へ写す")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="いまの初期単語のセットを表示する")
    p_save = sub.add_parser("save", help="見本のアカウントの単語を初期単語のセットにする")
    p_save.add_argument("user_id")
    p_save.add_argument("--words", nargs="+", metavar="WORD_ID", help="使う単語（省くと、削除していない単語すべて）")
    p_save.add_argument("--apply", action="store_true", help="実際に保存する")
    p_inst = sub.add_parser("install", help="すでにあるアカウントへ初期単語を写す")
    who = p_inst.add_mutually_exclusive_group(required=True)
    who.add_argument("--all", action="store_true", help="すべての参加者")
    who.add_argument("--user", nargs="+", metavar="USER_ID", help="写す参加者")
    p_inst.add_argument("--apply", action="store_true", help="実際に写す")
    args = parser.parse_args(argv)

    if args.command == "show":
        return _show()
    if args.command == "save":
        return _save(args.user_id, args.words, args.apply)
    users = [a["user_id"] for a in acc.list_accounts()] if args.all else args.user
    return _install(users, args.apply)


if __name__ == "__main__":
    sys.exit(main())
