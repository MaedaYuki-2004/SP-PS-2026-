"""
scripts/move_shared_library.py
共有の単語とお手本を、ある参加者のライブラリ（data/users/<利用者ID>/library/）へ移すスクリプト。

単語とお手本は参加者ごとに持つ（core/library.py）。以前の全員共有の単語・お手本は、
ログイン中の参加者からは見えなくなるので、このスクリプトで誰か1人に移す。
単語 ID（word1, …）はそのまま移すので、その参加者の練習履歴とも対応したままになる。

移したあとの共有の元ファイルは消さずに data/shared_library_backup_<日時>/ へ退避する。

使い方（リポジトリの一番上のフォルダで）:
    python scripts/move_shared_library.py <利用者ID>           # 何を移すか表示するだけ
    python scripts/move_shared_library.py <利用者ID> --apply   # 実際に移す
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR  # noqa: E402
from core import accounts as acc  # noqa: E402
from core.library import SHARED_LIBRARY, library_for  # noqa: E402


def _items(dest) -> list[tuple[Path, Path]]:
    """（共有の元, 参加者のライブラリの先）の組。存在するものだけ。"""
    src = SHARED_LIBRARY
    pairs = [
        (src.words_db,  dest.words_db),
        (src.audio_scp, dest.audio_scp),
        (src.words_txt, dest.words_txt),
        (src.lip_refs,  dest.lip_refs),
    ]
    if src.sound_root.exists():
        pairs += [(p, dest.sound_root / p.name) for p in sorted(src.sound_root.iterdir()) if p.is_dir()]
    if src.mfcc_dir.exists():
        pairs += [(p, dest.mfcc_dir / p.name) for p in sorted(src.mfcc_dir.glob("*.bin"))]
    # フォルマントのキャッシュはファイルの場所をキーに含むので移さない（初回の解析で作り直す）
    return [(s, d) for s, d in pairs if s.exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("user_id", help="移す先の利用者ID")
    parser.add_argument("--apply", action="store_true", help="実際に移す（付けなければ表示だけ）")
    args = parser.parse_args()

    if not acc.account_exists(args.user_id):
        print(f"利用者ID「{args.user_id}」のアカウントがありません。", file=sys.stderr)
        return 1
    dest = library_for(args.user_id)
    if dest.words_db.exists():
        print(f"{args.user_id} にはすでに単語があります（{dest.words_db}）。"
              "単語 ID がぶつかるので、空の参加者にだけ移せます。", file=sys.stderr)
        return 1

    items = _items(dest)
    if not items:
        print("移す共有の単語・お手本がありません。")
        return 0

    for s, d in items:
        print(f"  {s.relative_to(DATA_DIR)}  →  {d.relative_to(DATA_DIR)}")
    if not args.apply:
        print("\n表示だけです。移すときは --apply を付けて実行してください。")
        return 0

    for s, d in items:
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    backup = DATA_DIR / f"shared_library_backup_{datetime.now():%Y%m%d_%H%M%S}"
    for s, _ in items:
        target = backup / s.relative_to(DATA_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(target))

    print(f"\n{len(items)} 件を {args.user_id} に移しました。")
    print(f"共有の元ファイルは {backup.relative_to(DATA_DIR.parent)} に退避しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
