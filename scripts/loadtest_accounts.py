"""
scripts/loadtest_accounts.py
負荷試験用の参加者をまとめて作る・消す（サーバーの PC で実行する）。

負荷試験は「1人1つの利用者 ID」で行う。同じ ID を何人分にも使うと、その人の処理は1つずつ順番に
行われる（作業フォルダが1つのため。README §5.2）ので、サーバーの力ではなく1人分の速さを測ってしまう。

  # 既存の参加者（例: A01）の単語とお手本をコピーして、LT01〜LT30 を作る（パスワードも設定する）
  python scripts/loadtest_accounts.py create --count 30 --from A01
  # 作った参加者を、練習の記録ごと消す
  python scripts/loadtest_accounts.py delete

オプション: --prefix（利用者 ID の頭。既定 LT）、--password（既定 loadtest123）
作った ID は DATA_DIR/run/loadtest_accounts.json に記録し、delete はその ID だけを消す。
データの置き場所は app.py と同じ（環境変数 DATA_DIR があればそこ）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import accounts as acc  # noqa: E402
from core.storage import RUN_DIR, write_json_atomic  # noqa: E402

RECORD = RUN_DIR / "loadtest_accounts.json"


def _created() -> list[str]:
    try:
        return list(json.loads(RECORD.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def create(count: int, source: str, prefix: str, password: str) -> None:
    library = acc.user_data_dir(source) / "library"
    if not (library / "words_db.json").exists():
        sys.exit(f"{source} の単語が見つかりません（{library}）。お手本を登録した参加者を --from に指定してください。")
    words = json.loads((library / "words_db.json").read_text(encoding="utf-8"))
    created = _created()
    for i in range(1, count + 1):
        uid = f"{prefix}{i:02d}"
        if acc.account_exists(uid):
            print(f"{uid}: すでにあるので使います")
            continue
        code = acc.create_account(uid)["setup_code"]
        shutil.copytree(library, acc.user_data_dir(uid) / "library")
        acc.complete_first_login(uid, code, password)
        created.append(uid)
        write_json_atomic(RECORD, sorted(set(created)))
        print(f"{uid}: 作りました（単語 {len(words)} 個）")
    print(f"\nパスワード: {password}")
    print(f"負荷試験: python scripts/loadtest.py --url <サーバーのURL> --users {count} --prefix {prefix} --password {password}")


def delete() -> None:
    created = _created()
    if not created:
        print("このスクリプトで作った参加者はいません。")
        return
    for uid in created:
        if acc.account_exists(uid):
            acc.delete_account(uid)
            print(f"{uid}: 消しました")
    RECORD.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="負荷試験用の参加者を作る・消す")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="参加者を作る")
    c.add_argument("--count", type=int, default=30, help="作る人数（既定 30）")
    c.add_argument("--from", dest="source", required=True, help="単語とお手本をコピーする参加者の利用者 ID")
    c.add_argument("--prefix", default="LT", help="利用者 ID の頭（既定 LT → LT01, LT02, …）")
    c.add_argument("--password", default="loadtest123", help="パスワード（既定 loadtest123）")
    sub.add_parser("delete", help="このスクリプトで作った参加者を、記録ごと消す")
    args = parser.parse_args()
    if args.command == "create":
        create(args.count, args.source, args.prefix, args.password)
    else:
        delete()


if __name__ == "__main__":
    main()
