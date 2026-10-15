"""
core/storage.py
データファイルを壊さずに書き込む仕組みと、プロセスをまたぐ排他（ロック）。

サーバー（gunicorn）は複数のプロセス（ワーカー）とスレッドで要求を同時に処理する
（gunicorn.conf.py）。同じファイルを同時に読み書きしても壊れないように、次の2つを使う。

  - write_text_atomic() / write_json_atomic()
      同じフォルダの一時ファイルに書いてから置き換える（os.replace）。読む側は古いか新しいかの
      どちらかの完全なファイルを読み、書きかけを読まない。一時ファイルの名前は毎回変える
      （以前は名前が固定の .tmp だったので、同時に書くと互いの一時ファイルを壊しえた）。
  - lock(name)
      「読んで・直して・書く」処理を1つずつ行うための排他。DATA_DIR/run/locks/<name>.lock に
      OS のファイルロック（POSIX は fcntl.flock、Windows は msvcrt.locking）をかけるので、
      別のプロセスの間でも効く。同じプロセスのスレッドの間は threading.Lock で守る。
      同じ名前のロックを入れ子にしないこと（自分自身を待ってしまう）。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from config import DATA_DIR

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

# 動いている間だけ使う状態（ロックのファイル・ログインの試行回数）。消してもよい
RUN_DIR = DATA_DIR / "run"
LOCK_DIR = RUN_DIR / "locks"

_LOCK_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class LockTimeout(Exception):
    """決めた時間のうちにロックを取れなかった（ほかの処理が長く続いている）。"""


# ── 書き込み ─────────────────────────────────────────────────────────

def write_text_atomic(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """path に text を書く。途中で止まっても、読む側が書きかけのファイルを見ることはない。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path | str, data, indent: int | None = 2) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=indent))


def _replace(src: str, dst: Path) -> None:
    for attempt in range(20):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            # Windows では、ほかのスレッドが読んでいる途中のファイルは置き換えられないことがある
            if os.name != "nt" or attempt == 19:
                raise
            time.sleep(0.01 * (attempt + 1))


# ── 排他 ─────────────────────────────────────────────────────────────

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _thread_lock(name: str) -> threading.Lock:
    with _thread_locks_guard:
        lk = _thread_locks.get(name)
        if lk is None:
            lk = _thread_locks[name] = threading.Lock()
        return lk


@contextmanager
def lock(name: str, timeout: float = 30.0, directory: Path | None = None):
    """name ごとの排他。別のプロセス・スレッドが持っている間は timeout 秒まで待つ（超えたら LockTimeout）。

    ロックのファイルは directory（既定は DATA_DIR/run/locks）の <name>.lock。
    """
    if not _LOCK_NAME.match(name):
        raise ValueError(f"ロックの名前が不正です: {name!r}")
    directory = Path(directory) if directory is not None else LOCK_DIR
    deadline = time.monotonic() + timeout
    tl = _thread_lock(str(directory / name))
    if not tl.acquire(timeout=max(0.0, timeout)):
        raise LockTimeout(name)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / f"{name}.lock", "a+b") as f:
            _lock_file(f, deadline, name)
            try:
                yield
            finally:
                _unlock_file(f)
    finally:
        tl.release()


def _lock_file(f, deadline: float, name: str) -> None:
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:  # ほかのプロセスが持っている（POSIX: BlockingIOError、Windows: PermissionError）
            if time.monotonic() >= deadline:
                raise LockTimeout(name)
            time.sleep(0.02)


def _unlock_file(f) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        else:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass  # ファイルを閉じればどちらの OS でもロックは外れる
