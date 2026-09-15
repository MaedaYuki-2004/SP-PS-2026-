"""
scripts/set_teacher_password.py
先生用パスワードを設定（変更）するスクリプト。

単語の追加・削除やお手本の録り直し、参加者アカウントの管理は「先生モード」のときだけ
行える。先生モードを ON にするときに入力するパスワードを、サーバーを動かす PC で設定する。
パスワードはハッシュにして data/config/teacher.json に保存し、平文は残さない。

使い方（リポジトリの一番上のフォルダで）:
    python scripts/set_teacher_password.py

環境変数 SP_PS_TEACHER_PASSWORD が設定されている場合は、そちらが優先される。
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import teacher  # noqa: E402


def main() -> int:
    print("先生用パスワードを設定します。")
    print(f"（{teacher.MIN_PASSWORD_LENGTH}文字以上。生徒に知られないものにしてください。入力中の文字は表示されません）")
    first = getpass.getpass("パスワード: ")
    second = getpass.getpass("もう一度: ")
    if first != second:
        print("2回の入力が一致しませんでした。やり直してください。")
        return 1
    try:
        teacher.set_password(first)
    except ValueError as exc:
        print(exc)
        return 1
    print(f"設定しました（{teacher.TEACHER_CONFIG_PATH}）。")
    if teacher._env_password():
        print(f"注意：環境変数 {teacher.ENV_PASSWORD} が設定されているため、いまはそちらが使われます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
