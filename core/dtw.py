"""
core/dtw.py
FastDTW（Salvador & Chan, 2007）をユークリッド距離で計算する。

fastdtw パッケージの fastdtw(x, y, radius=1, dist=scipy の euclidean) と同じ手順
（系列を半分に縮めて解き、その経路の周り radius マスだけを元の長さで解き直す。
同点のときは上・左・斜めの順に選ぶ）で、同じ距離と経路を返す。

違うのは速さだけ。パッケージ（純 Python 版）は1マスごとに scipy の euclidean を呼ぶため、
1回の採点で音色の比較（単語の数だけ）に 0.1〜0.5 秒かかり、その間ほかの要求の Python 処理も
止まっていた（GIL を握ったまま）。ここでは各段の距離を numpy でまとめて計算し、表の計算は
numba（librosa と一緒に入る）でコンパイルして GIL を手放したまま行う。numba が使えないときは
同じ計算を Python で行う。距離の計算のしかたが違うため、値は最後の桁（1e-15 程度の相対差）で
異なることがある（tests/test_dtw.py でパッケージと比べている）。

使っているところ: core/timbre.py（音色）、app.py の _compute_lip_score（唇スコア）、core/sync.py（口と声の同期）。
"""
from __future__ import annotations

import numpy as np

try:
    from numba import njit
except ImportError:  # numba が無いときは Python で計算する
    njit = None

_INF = float("inf")


def fastdtw(x, y, radius: int = 1) -> tuple[float, list[tuple[int, int]]]:
    """x と y（時間 × 特徴量の配列）の FastDTW 距離と経路を返す（ユークリッド距離）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    if y.ndim == 1:
        y = y[:, None]
    if x.shape[1] != y.shape[1]:
        raise ValueError("second dimension of x and y must be the same")
    if len(x) == 0 or len(y) == 0:
        raise ValueError("x と y は1コマ以上必要です")
    return _fastdtw(x, y, radius)


def _fastdtw(x: np.ndarray, y: np.ndarray, radius: int) -> tuple[float, list[tuple[int, int]]]:
    min_time_size = radius + 2
    if len(x) < min_time_size or len(y) < min_time_size:
        return _dtw(x, y, np.zeros(len(x), dtype=np.int64), np.full(len(x), len(y), dtype=np.int64))
    _, path = _fastdtw(_reduce_by_half(x), _reduce_by_half(y), radius)
    return _dtw(x, y, *_expand_window(path, len(x), len(y), radius))


def _reduce_by_half(x: np.ndarray) -> np.ndarray:
    # 隣り合う2コマの平均（奇数のときは最後の1コマを捨てる）。パッケージの __reduce_by_half と同じ式
    n = len(x) - len(x) % 2
    return (x[0:n:2] + x[1:n:2]) / 2


def _expand_window(path, len_x: int, len_y: int, radius: int) -> tuple[np.ndarray, np.ndarray]:
    """縮めた系列の経路を、元の長さで計算するマス（行ごとの列の範囲 [lo, hi)）に広げる。"""
    p = np.asarray(path, dtype=np.int64)
    offsets = np.arange(-radius, radius + 1)
    di, dj = np.meshgrid(offsets, offsets, indexing="ij")
    # 経路の各点の周り radius マス
    near_i = (p[:, 0, None] + di.ravel()[None, :]).ravel()
    near_j = (p[:, 1, None] + dj.ravel()[None, :]).ravel()
    # 1マスを元の長さの 2×2 マスに広げる
    cells_i = np.concatenate([near_i * 2, near_i * 2, near_i * 2 + 1, near_i * 2 + 1])
    cells_j = np.concatenate([near_j * 2, near_j * 2 + 1, near_j * 2, near_j * 2 + 1])
    ok = (cells_i >= 0) & (cells_i < len_x) & (cells_j >= 0) & (cells_j < len_y)
    mask = np.zeros((len_x, len_y), dtype=bool)
    mask[cells_i[ok], cells_j[ok]] = True

    # パッケージと同じく、各行で「前の行の始まり」以降にある最初のマスから、途切れるまでを使う。
    # 行ごとに numpy を呼ぶと遅いので、表を 0/1 のバイト列にして bytes.find で探す
    buf = mask.tobytes()
    los = [0] * len_x
    his = [0] * len_x
    start_j = 0
    for i in range(len_x):
        base = i * len_y
        first = buf.find(b"\x01", base + start_j, base + len_y)
        if first < 0:
            los[i] = his[i] = start_j
            continue
        end = buf.find(b"\x00", first, base + len_y)
        los[i] = first - base
        his[i] = (end - base) if end >= 0 else len_y
        start_j = los[i]
    return np.array(los, dtype=np.int64), np.array(his, dtype=np.int64)


def _dtw(x: np.ndarray, y: np.ndarray, los: np.ndarray, his: np.ndarray) -> tuple[float, list[tuple[int, int]]]:
    """行ごとの列の範囲 [los[i], his[i]) のマスだけで DTW を解く。範囲の外は通れない（無限大）。"""
    len_x, len_y = len(x), len(y)
    counts = his - los
    starts = np.cumsum(counts) - counts       # 各行のマスが平らな配列のどこから始まるか
    rows = np.repeat(np.arange(len_x), counts)
    cols = np.arange(int(counts.sum())) - np.repeat(starts, counts) + np.repeat(los, counts)
    diff = x[rows] - y[cols]
    dists = np.sqrt(np.einsum("ij,ij->i", diff, diff))

    total, moves = _fill(dists, los, his, len_y)
    if not np.isfinite(total):
        raise ValueError("DTW の経路が見つかりませんでした")

    moves = moves.tolist()
    starts = starts.tolist()
    los_l = los.tolist()
    path: list[tuple[int, int]] = []
    i, j = len_x, len_y
    while not (i == j == 0):
        path.append((i - 1, j - 1))
        move = moves[starts[i - 1] + j - 1 - los_l[i - 1]]
        if move != 1:   # 0: 上から、2: 斜めから
            i -= 1
        if move != 0:   # 1: 左から、2: 斜めから
            j -= 1
    path.reverse()
    return float(total), path


def _fill_py(dists, los, his, len_y):
    """累積距離の表を埋め、各マスにどこから来たか（0=上・1=左・2=斜め）を記録する。"""
    dists = dists.tolist()
    prev = [_INF] * (len_y + 1)   # 1つ前の行までの累積距離（列 0 は番兵）
    prev[0] = 0.0
    moves = bytearray(len(dists))
    k = 0
    for lo, hi in zip(los.tolist(), his.tolist()):
        cur = [_INF] * (len_y + 1)
        left = _INF
        for j in range(lo, hi):
            dt = dists[k]
            best = prev[j + 1] + dt   # 上から
            move = 0
            s = left + dt             # 左から
            if s < best:
                best, move = s, 1
            s = prev[j] + dt          # 斜めから
            if s < best:
                best, move = s, 2
            cur[j + 1] = best
            moves[k] = move
            left = best
            k += 1
        prev = cur
    return prev[len_y], np.frombuffer(bytes(moves), dtype=np.uint8)


if njit is not None:
    @njit(cache=True, nogil=True)
    def _fill_nb(dists, los, his, len_y):  # pragma: no cover（_fill_py と同じ計算）
        prev = np.full(len_y + 1, np.inf)
        prev[0] = 0.0
        cur = np.empty(len_y + 1)
        moves = np.empty(dists.shape[0], dtype=np.uint8)
        k = 0
        for i in range(los.shape[0]):
            cur[:] = np.inf
            left = np.inf
            for j in range(los[i], his[i]):
                dt = dists[k]
                best = prev[j + 1] + dt
                move = 0
                s = left + dt
                if s < best:
                    best = s
                    move = 1
                s = prev[j] + dt
                if s < best:
                    best = s
                    move = 2
                cur[j + 1] = best
                moves[k] = move
                left = best
                k += 1
            prev, cur = cur, prev
        return prev[len_y], moves

    _fill = _fill_nb
else:
    _fill = _fill_py
