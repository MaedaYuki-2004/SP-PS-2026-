"""
core/ratelimit.py
続けて間違えたときに、しばらく受け付けないようにする（総当たり対策）。

参加者のログイン（app.py）と先生用パスワード（core/teacher.py）で使う。
キーは呼び出し側で決める。参加者のログインは「接続元 + 利用者 ID」にする。同じ Wi-Fi の生徒は
公開 IP を共有することがあるので、接続元だけをキーにすると、1人の打ち間違いでクラス全員が
待たされてしまうため。

状態の置き場所:
  path を渡すと、そのファイル（DATA_DIR/run/ の JSON）に持つ。サーバーは複数のプロセス（ワーカー）で
  動くので（gunicorn.conf.py）、プロセスの中に持つと、どのワーカーに届くかで回数が分かれ、
  「5回まで」が実際には「5回 × ワーカー数」になってしまう。ファイルの「読んで・直して・書く」は
  core/storage.py のロックで1つずつ行う。サーバーを再起動しても残るが、待つ時間が過ぎれば効かない。
  path を渡さないときはプロセスの中に持つ（テスト用）。
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from core import storage

# キーが増え続けないよう、これを超えたら終わった記録を捨てる
_MAX_KEYS = 10000


class FailureLimiter:
    def __init__(self, max_fails: int, lock_seconds: int, path: Path | str | None = None):
        self.max_fails = max_fails
        self.lock_seconds = lock_seconds
        self.path = Path(path) if path else None
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def seconds_locked(self, key: str) -> int:
        """まだ受け付けない残り秒数（0 なら受け付ける）。"""
        with self._exclusive(reading=True):
            _, locked_until = self._load().get(key, (0, 0.0))
        left = locked_until - time.time()
        return int(left) + 1 if left > 0 else 0

    def record_failure(self, key: str) -> None:
        with self._exclusive():
            fails = self._load()
            if len(fails) >= _MAX_KEYS:
                now = time.time()
                fails = {k: v for k, v in fails.items() if v[1] > now}
            count, _ = fails.get(key, (0, 0.0))
            count += 1
            if count >= self.max_fails:
                fails[key] = (0, time.time() + self.lock_seconds)
            else:
                fails[key] = (count, 0.0)
            self._store(fails)

    def clear(self, key: str) -> None:
        with self._exclusive():
            fails = self._load()
            if fails.pop(key, None) is not None:
                self._store(fails)

    # ── 状態の読み書き ────────────────────────────────────────────

    @contextmanager
    def _exclusive(self, reading: bool = False):
        if self.path is None:
            with self._lock:
                yield
        elif reading:
            yield   # ファイルは置き換えで書くので、読むだけならロックはいらない
        else:
            with storage.lock(self.path.stem, timeout=10, directory=self.path.parent):
                yield

    def _load(self) -> dict[str, tuple[int, float]]:
        if self.path is None:
            return self._fails
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(k): (int(v[0]), float(v[1])) for k, v in data.items()}
        except (OSError, ValueError, TypeError, IndexError, AttributeError):
            return {}

    def _store(self, fails: dict[str, tuple[int, float]]) -> None:
        if self.path is None:
            self._fails = fails
        else:
            storage.write_json_atomic(self.path, {k: list(v) for k, v in fails.items()}, indent=None)
