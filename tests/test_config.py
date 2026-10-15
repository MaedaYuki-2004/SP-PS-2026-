import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _read_secret_key(queue) -> None:
    import config
    queue.put(config.FLASK_SECRET_KEY)


class SecretKeyTests(unittest.TestCase):
    def test_workers_starting_together_share_one_key(self):
        # gunicorn のワーカーが同時に起動しても、全員が同じ鍵を使う（別々だとログインが切れる）
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k != "FLASK_SECRET_KEY"}
            env["DATA_DIR"] = tmp
            ctx = multiprocessing.get_context("spawn")
            queue = ctx.Queue()
            with mock.patch.dict(os.environ, env, clear=True):
                procs = [ctx.Process(target=_read_secret_key, args=(queue,)) for _ in range(4)]
                for p in procs:
                    p.start()
                keys = [queue.get(timeout=120) for _ in procs]
                for p in procs:
                    p.join(60)
            self.assertEqual(len(set(keys)), 1)
            saved = (Path(tmp) / "config" / "flask_secret_key.txt").read_text(encoding="utf-8").strip()
            self.assertEqual(saved, keys[0])


if __name__ == "__main__":
    unittest.main()
