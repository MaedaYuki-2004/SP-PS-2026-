"""
core/ratelimit.py
続けて間違えたときに、しばらく受け付けないようにする（総当たり対策）。

参加者のログインで使う。先生用パスワードは core/teacher.py に同じ考え方の仕組みがある。
キーは呼び出し側で「接続元 + 利用者 ID」にする。同じ Wi-Fi の生徒は公開 IP を共有することがあるので、
接続元だけをキーにすると、1人の打ち間違いでクラス全員が待たされてしまうため。
状態はプロセスの中に持つ（gunicorn はワーカー1つ。README §14）。サーバーを再起動すると消える。
"""
from __future__ import annotations

import threading
import time

# キーが増え続けてメモリを使い切らないよう、これを超えたら終わった記録を捨てる
_MAX_KEYS = 10000


class FailureLimiter:
    def __init__(self, max_fails: int, lock_seconds: int):
        self.max_fails = max_fails
        self.lock_seconds = lock_seconds
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def seconds_locked(self, key: str) -> int:
        """まだ受け付けない残り秒数（0 なら受け付ける）。"""
        with self._lock:
            _, locked_until = self._fails.get(key, (0, 0.0))
        left = locked_until - time.time()
        return int(left) + 1 if left > 0 else 0

    def record_failure(self, key: str) -> None:
        with self._lock:
            if len(self._fails) >= _MAX_KEYS:
                now = time.time()
                self._fails = {k: v for k, v in self._fails.items() if v[1] > now}
            count, _ = self._fails.get(key, (0, 0.0))
            count += 1
            if count >= self.max_fails:
                self._fails[key] = (0, time.time() + self.lock_seconds)
            else:
                self._fails[key] = (count, 0.0)

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
