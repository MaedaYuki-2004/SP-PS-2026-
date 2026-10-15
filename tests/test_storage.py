import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core import storage
from core.ratelimit import FailureLimiter


def _count_up(lock_dir: str, counter: str, times: int) -> None:
    """別のプロセスから、ロックを取ってファイルの数を1つずつ増やす。"""
    for _ in range(times):
        with storage.lock("counter", directory=Path(lock_dir)):
            path = Path(counter)
            n = int(path.read_text()) if path.exists() else 0
            storage.write_text_atomic(path, str(n + 1))


class StorageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_atomic_write_leaves_no_temp_files(self):
        path = self.dir / "a" / "data.json"
        storage.write_json_atomic(path, {"かな": [1, 2]})
        storage.write_json_atomic(path, {"かな": [3]})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"かな": [3]})
        self.assertEqual([p.name for p in path.parent.iterdir()], ["data.json"])

    def test_readers_never_see_a_half_written_file(self):
        # 書いている途中のファイルを読まない（以前の words_db.json・lip_refs.json は上書きで書いていた）
        path = self.dir / "words_db.json"
        storage.write_json_atomic(path, {"n": 0, "pad": "x" * 50000})
        errors, stop = [], threading.Event()

        def writer():
            try:
                for i in range(150):
                    storage.write_json_atomic(path, {"n": i, "pad": "x" * (50000 + i)})
            except Exception as exc:
                errors.append(exc)
            finally:
                stop.set()

        def reader():
            while not stop.is_set():
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except PermissionError:
                    pass   # Windows で置き換えの瞬間に開けないことがある（壊れたファイルを読むのとは違う）
                except ValueError as exc:
                    errors.append(exc)
                # Windows は読んでいる途中のファイルを置き換えられないので、休みなく読み続けない
                time.sleep(0.001)

        threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_lock_serializes_threads(self):
        counter = self.dir / "count.txt"

        def work():
            for _ in range(40):
                with storage.lock("counter", directory=self.dir):
                    n = int(counter.read_text()) if counter.exists() else 0
                    time.sleep(0.0005)
                    storage.write_text_atomic(counter, str(n + 1))

        threads = [threading.Thread(target=work) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(counter.read_text(), "240")

    def test_lock_serializes_processes(self):
        # gunicorn のワーカー（別のプロセス）どうしでも、読んで・直して・書くが混ざらない
        counter = self.dir / "count.txt"
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_count_up, args=(str(self.dir), str(counter), 25)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(120)
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(counter.read_text(), "100")

    def test_lock_times_out_while_held(self):
        held, release = threading.Event(), threading.Event()

        def holder():
            with storage.lock("busy", directory=self.dir):
                held.set()
                release.wait(5)

        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        try:
            with self.assertRaises(storage.LockTimeout):
                with storage.lock("busy", timeout=0.2, directory=self.dir):
                    pass
        finally:
            release.set()
            t.join()
        with storage.lock("busy", timeout=1, directory=self.dir):
            pass

    def test_failure_limiter_is_shared_through_the_file(self):
        # ワーカーごとに別の FailureLimiter を作っても、同じファイルなら回数を共有する
        path = self.dir / "run" / "login_failures.json"
        worker_a = FailureLimiter(max_fails=3, lock_seconds=60, path=path)
        worker_b = FailureLimiter(max_fails=3, lock_seconds=60, path=path)
        worker_a.record_failure("ip|A01")
        worker_b.record_failure("ip|A01")
        self.assertEqual(worker_a.seconds_locked("ip|A01"), 0)
        worker_a.record_failure("ip|A01")
        self.assertGreater(worker_b.seconds_locked("ip|A01"), 0)
        self.assertEqual(worker_b.seconds_locked("ip|A02"), 0)
        worker_b.clear("ip|A01")
        self.assertEqual(worker_a.seconds_locked("ip|A01"), 0)


if __name__ == "__main__":
    unittest.main()
