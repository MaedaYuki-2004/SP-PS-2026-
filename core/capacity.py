"""
core/capacity.py
重い処理（録音・録画の解析）を、1つのプロセスで同時にいくつまで動かすかを決める。

1回の採点（POST /graph）は、口の動画の解析（MediaPipe）・声の高さと母音（Praat）・音色（MFCC と DTW）で
CPU を使い、録音の受け取り（POST /audio）は Julius を動かす。サーバーは複数のスレッドで要求を受けるが
（gunicorn.conf.py）、届いた数だけ同時に動かすと CPU を取り合ってどれも遅くなり、ホームなどの軽い画面
まで待たされる。そこで同時に動かす数を HEAVY_SLOTS までにし、超えた分は順番を待たせる。
HEAVY_WAIT_SECONDS 待っても順番が来ないときは ServerBusy を投げ、app.py が 503「混み合っています」を返す
（録音の送信は main.js が少し待って自動で送り直す）。Cloudflare は 100 秒で応答を打ち切るので、
待つ時間はそれより十分短くしている。

環境変数:
  SP_PS_HEAVY_SLOTS  1プロセスで同時に動かす重い処理の数（既定: CPU の数 ÷ ワーカー数。2〜4 の範囲）
  SP_PS_HEAVY_WAIT   順番を待つ最大の秒数（既定 40）
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager


def _env_number(name: str, default: float, cast=int):
    try:
        value = cast(os.environ.get(name, ""))
        return value if value > 0 else default
    except ValueError:
        return default


def _default_slots() -> int:
    # gunicorn.conf.py がワーカー数を SP_PS_WORKERS に入れる。ワーカーで CPU を分け合う
    workers = _env_number("SP_PS_WORKERS", 1)
    return max(2, min(4, (os.cpu_count() or 2) // workers))


HEAVY_SLOTS = _env_number("SP_PS_HEAVY_SLOTS", _default_slots())
HEAVY_WAIT_SECONDS = _env_number("SP_PS_HEAVY_WAIT", 40.0, float)

_slots = threading.BoundedSemaphore(HEAVY_SLOTS)
_count_lock = threading.Lock()
_running = 0
_waiting = 0


class ServerBusy(Exception):
    """重い処理の順番が決めた時間のうちに来なかった（混み合っている）。"""

    retry_after = 5   # 何秒後に送り直してもらうか（Retry-After）


@contextmanager
def heavy_slot(timeout: float | None = None):
    """重い処理の順番を取る。取れるまで待ち、timeout 秒（既定 HEAVY_WAIT_SECONDS）を超えたら ServerBusy。"""
    global _running, _waiting
    started = time.monotonic()
    with _count_lock:
        _waiting += 1
    try:
        acquired = _slots.acquire(timeout=HEAVY_WAIT_SECONDS if timeout is None else timeout)
    finally:
        with _count_lock:
            _waiting -= 1
    waited = time.monotonic() - started
    if not acquired:
        print(f"[capacity] {waited:.0f}秒待っても順番が来なかったため 503 を返しました（{stats()}）", flush=True)
        raise ServerBusy()
    if waited >= 1.0:
        # 負荷試験のときに、順番待ちが起きているかをサーバーのログで確かめられるようにする
        print(f"[capacity] 解析の順番を {waited:.1f} 秒待ちました（{stats()}）", flush=True)
    with _count_lock:
        _running += 1
    try:
        yield
    finally:
        with _count_lock:
            _running -= 1
        _slots.release()


def stats() -> dict:
    """このプロセスの重い処理の状態（同時に動かせる数・動いている数・待っている数）。"""
    with _count_lock:
        return {"pid": os.getpid(), "slots": HEAVY_SLOTS, "running": _running, "waiting": _waiting}
