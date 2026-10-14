import unittest
from unittest import mock

from core.ratelimit import FailureLimiter


class FailureLimiterTests(unittest.TestCase):
    def test_locks_after_max_fails_and_unlocks_later(self):
        lim = FailureLimiter(max_fails=3, lock_seconds=60)
        with mock.patch("core.ratelimit.time.time", return_value=1000.0):
            for _ in range(2):
                lim.record_failure("k")
            self.assertEqual(lim.seconds_locked("k"), 0)
            lim.record_failure("k")
            self.assertEqual(lim.seconds_locked("k"), 61)
        with mock.patch("core.ratelimit.time.time", return_value=1061.0):
            self.assertEqual(lim.seconds_locked("k"), 0)

    def test_keys_are_separate_and_clear_resets(self):
        # 同じ Wi-Fi の生徒どうしで巻き込まないよう、キー（接続元 + 利用者 ID）ごとに数える
        lim = FailureLimiter(max_fails=2, lock_seconds=60)
        lim.record_failure("ip|a01")
        lim.record_failure("ip|a01")
        self.assertGreater(lim.seconds_locked("ip|a01"), 0)
        self.assertEqual(lim.seconds_locked("ip|a02"), 0)
        lim.record_failure("ip|a02")
        lim.clear("ip|a02")
        lim.record_failure("ip|a02")
        self.assertEqual(lim.seconds_locked("ip|a02"), 0)


if __name__ == "__main__":
    unittest.main()
