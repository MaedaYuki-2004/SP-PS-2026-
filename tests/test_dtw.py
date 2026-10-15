import math
import random
import unittest

from unittest import mock

import numpy as np
from fastdtw import fastdtw as package_fastdtw
from scipy.spatial.distance import euclidean

from core import dtw
from core.dtw import fastdtw

# 表の計算は numba 版（入っていれば）と Python 版の両方を確かめる
FILLS = [("python", dtw._fill_py)] + ([("numba", dtw._fill_nb)] if dtw.njit is not None else [])


class FastDtwTests(unittest.TestCase):
    """core/dtw.py が fastdtw パッケージ（dist=euclidean）と同じ距離・経路を返すこと。"""

    def assert_same(self, x, y):
        expected_distance, expected_path = package_fastdtw(x, y, dist=euclidean)
        for name, fill in FILLS:
            with mock.patch.object(dtw, "_fill", fill):
                distance, path = fastdtw(x, y)
            self.assertTrue(math.isclose(distance, expected_distance, rel_tol=1e-9, abs_tol=1e-12),
                            (name, distance, expected_distance))
            self.assertEqual(path, expected_path, name)

    def test_matches_package_on_random_sequences(self):
        rng = np.random.default_rng(0)
        # 短い系列（縮めずに全部のマスを解く）・奇数長・長さの差が大きいものを含める
        sizes = [(1, 1), (1, 5), (2, 3), (3, 3), (4, 7), (7, 4), (17, 30), (50, 49), (90, 130), (240, 60)]
        for n, m in sizes:
            for dims in (1, 20, 36):
                with self.subTest(n=n, m=m, dims=dims):
                    self.assert_same(rng.normal(size=(n, dims)), rng.normal(size=(m, dims)))

    def test_matches_package_on_smooth_sequences(self):
        # 実際の MFCC や唇の形のように、なめらかに変わる系列（同点が出やすい整数値も含める）
        random.seed(1)
        for _ in range(20):
            n, m = random.randint(5, 120), random.randint(5, 120)
            t1, t2 = np.linspace(0, 3, n)[:, None], np.linspace(0, 3.4, m)[:, None]
            x = np.sin(t1 * np.arange(1, 13)) + 0.05 * np.cos(7 * t1)
            y = np.sin(t2 * np.arange(1, 13) * 0.95)
            self.assert_same(x, y)
            self.assert_same(np.round(x * 2), np.round(y * 2))

    def test_one_dimensional_input(self):
        x = np.array([1, 2, 3, 4, 5], dtype=float)
        y = np.array([2, 3, 4], dtype=float)
        for name, fill in FILLS:
            with mock.patch.object(dtw, "_fill", fill):
                distance, path = fastdtw(x, y)
            self.assertEqual(distance, 2.0, name)
            self.assertEqual(path, [(0, 0), (1, 0), (2, 1), (3, 2), (4, 2)], name)

    def test_rejects_empty_or_mismatched_input(self):
        with self.assertRaises(ValueError):
            fastdtw(np.zeros((0, 3)), np.zeros((4, 3)))
        with self.assertRaises(ValueError):
            fastdtw(np.zeros((4, 3)), np.zeros((4, 2)))


if __name__ == "__main__":
    unittest.main()
