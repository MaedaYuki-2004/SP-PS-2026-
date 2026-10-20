"""
scripts/make_delivery_videos.py
全員のお手本の動画（ライブラリの sound/<word_id>/<word_id>.webm）から、生徒に送るための小さくした
コピー（<word_id>.web.webm、600 kbps）を前もって作るスクリプト。

【実行方法】
  プロジェクトのルート階層（app.py と同じ場所）で実行してください。サーバーは動かしたままでよい。
  python scripts/make_delivery_videos.py

コピーが無いお手本は、サーバーが最初に開かれたときに裏で作るが、その回は元の大きな動画を送る。
授業の前にこのスクリプトで作っておくと、最初から小さい動画を送れる（app.py の _delivery_video）。
小さいお手本（600KB 以下。1 Mbps で録った新しいお手本など）と、コピーが新しいものは飛ばす。
採点・口の形の基準データには元の動画を使うので、採点は変わらない。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DATA_DIR  # noqa: E402


def main() -> None:
    import app   # 読み込みに数秒かかる（MediaPipe など）

    made = skipped = failed = 0
    before = after = 0
    for original in sorted(DATA_DIR.glob("users/*/library/sound/*/*.webm")):
        if original.name.endswith(".web.webm") or ".tmp." in original.name:
            continue
        try:
            copy = app.make_delivery_video(original)
        except Exception as exc:
            print(f"失敗: {original.relative_to(DATA_DIR)}: {exc}")
            failed += 1
            continue
        size = original.stat().st_size
        before += size
        if copy is None:
            skipped += 1
            after += size
        else:
            made += 1
            after += copy.stat().st_size
    print(f"お手本 {made + skipped + failed} 本: コピーあり {made}・そのまま送る {skipped}・失敗 {failed}")
    print(f"送る大きさの合計: {before // 1024} KB → {after // 1024} KB")


if __name__ == "__main__":
    main()
