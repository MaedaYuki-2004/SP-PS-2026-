"""PUBLIC_BASE_URLからスマホ用QR画像をローカルで生成する。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.public_access import public_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent.parent / "data/access_qr.png")
    args = parser.parse_args()
    try:
        url = public_url(os.environ.get("PUBLIC_BASE_URL", ""))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        import qrcode
    except ImportError:
        print('先に python -m pip install "qrcode[pil]" を実行してください。', file=sys.stderr)
        return 1
    if args.output.exists():
        print("出力ファイルが存在します。--outputで別のファイル名を指定してください。", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    qrcode.make(url).save(args.output)
    print(f"アクセス先: {url}")
    print(f"QR画像: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
