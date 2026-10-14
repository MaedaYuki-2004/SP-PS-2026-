"""PUBLIC_BASE_URLからスマホ用QR画像をローカルで生成する。"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.public_access import public_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent.parent / "data/access_qr.png")
    parser.add_argument("--no-file", action="store_true",
                        help="画像を作らず、ターミナルにQRを表示するだけにする（SSH接続時など）")
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
    if not args.no_file and args.output.exists():
        print("出力ファイルが存在します。--outputで別のファイル名を指定してください。", file=sys.stderr)
        return 1
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    # SSH 接続でも読めるよう、ターミナルにも文字でQRを出す。
    # 背景が白か黒かはターミナルの設定で変わるので、色を指定して白地に黒で描く
    buf = io.StringIO()
    qr.print_ascii(out=buf)
    for line in buf.getvalue().splitlines():
        print(f"\033[30;107m{line}\033[0m")
    print(f"アクセス先: {url}")
    if not args.no_file:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        qr.make_image().save(args.output)
        print(f"QR画像: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
