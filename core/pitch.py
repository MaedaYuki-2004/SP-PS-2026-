"""
core/pitch.py
ピッチ（基本周波数）の抽出・補間・スムージング・正規化を担当するモジュール。
librosa / praat-parselmouth を用いた F0 推定と前処理関数をまとめる。
"""
from __future__ import annotations

import librosa
import numpy as np
import parselmouth


# ── F0 抽出 ──────────────────────────────────────────────────────────

def librosa_pitch(sound_file: str) -> tuple[list[float], list[float]]:
    """librosa の pyin アルゴリズムで F0 を推定する。"""
    y, sr = librosa.load(sound_file, sr=None)
    if str(y.dtype) == "int16":
        y = (y / 32768).astype(np.float32)
    f0, _, _ = librosa.pyin(
        y,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=sr,
    )
    return f0.tolist(), librosa.times_like(f0).tolist()


def praat_pitch(sound_file: str) -> tuple[list[float], list[float]]:
    """Praat（parselmouth）で F0 を推定する。無声区間は NaN に置換する。"""
    snd = parselmouth.Sound(sound_file)
    pitch = snd.to_pitch()
    values = pitch.selected_array["frequency"].copy()
    values[values == 0] = np.nan
    return values.tolist(), pitch.xs().tolist()


# ── NaN 補間 ──────────────────────────────────────────────────────────

def _graph_compensate(pitch: np.ndarray, idx: int, count: int) -> np.ndarray:
    """NaN 区間を前後の値で線形補間する（内部ヘルパー）。"""
    n_space  = pitch[idx - 1: idx + count + 1]
    distance = n_space[-1] - n_space[0]
    diff     = distance / (count + 1)
    pitch[idx: idx + count] = np.linspace(
        n_space[0] + diff,
        n_space[0] + diff * count,
        count,
    )
    return pitch


def _fill_internal_nan(pitch: np.ndarray) -> np.ndarray:
    """配列内部の NaN 区間を線形補間する。"""
    idx = 0
    while idx < len(pitch):
        if np.isnan(pitch[idx]):
            start = idx
            while idx < len(pitch) and np.isnan(pitch[idx]):
                idx += 1
            end = idx
            if start == 0 or end >= len(pitch):
                continue
            pitch = _graph_compensate(pitch, start, end - start)
        else:
            idx += 1
    return pitch


def _fill_edge_nan(pitch: np.ndarray) -> np.ndarray:
    """配列の先頭・末尾の NaN を隣接値で埋める。"""
    if not len(pitch):
        return pitch

    # 先頭
    idx = 0
    while idx < len(pitch) and np.isnan(pitch[idx]):
        idx += 1
    if 0 < idx < len(pitch):
        pitch[:idx] = pitch[idx]

    # 末尾
    idx2 = len(pitch) - 1
    while idx2 >= 0 and np.isnan(pitch[idx2]):
        idx2 -= 1
    if 0 <= idx2 < len(pitch) - 1:
        pitch[idx2 + 1:] = pitch[idx2]

    return pitch


def comp(pitch: list[float] | np.ndarray) -> np.ndarray:
    """NaN 補間を適用してピッチ配列を補完する。"""
    arr = np.array(pitch, dtype=float)
    if np.all(np.isnan(arr)):
        return np.zeros_like(arr)
    arr = _fill_internal_nan(np.copy(arr))
    arr = _fill_edge_nan(arr)
    return arr


# ── スムージング・正規化 ───────────────────────────────────────────────

def smooth(pitch: list[float] | np.ndarray, window: int = 5) -> np.ndarray:
    """移動平均でピッチ曲線をスムージングする。"""
    arr = np.array(pitch, dtype=float)
    if not len(arr):
        return arr
    pad  = window // 2
    padded = np.concatenate([np.full(pad, arr[0]), arr, np.full(pad, arr[-1])])
    return np.array(
        [np.mean(padded[i:i + window]) for i in range(len(padded) - window + 1)]
    )


def scale(values: list[float] | np.ndarray) -> np.ndarray:
    """Min-Max 正規化（0〜1）を適用する。"""
    arr = np.array(values, dtype=float)
    if not len(arr):
        return arr
    mn, mx = np.min(arr), np.max(arr)
    if mx == mn:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


# ── 長さ整合 ──────────────────────────────────────────────────────────

def length_arrange(
    pitch: list[float] | np.ndarray,
    phoneme1: list,
    phoneme2: list,
) -> np.ndarray:
    """
    phoneme1（基準）の各音素長に合わせて pitch（録音側）を伸縮する。
    音素数が一致しない場合は共通する件数で打ち切る。
    """
    pitch_arr = np.array(pitch, dtype=float)
    usable    = min(len(phoneme1), len(phoneme2))
    if usable == 0:
        raise ValueError("音素フレーム情報が不足しています")

    result = np.array([], dtype=float)
    for i in range(usable):
        standard = int(phoneme1[i][1]) - int(phoneme1[i][0]) + 1
        frame_in = int(phoneme2[i][1]) - int(phoneme2[i][0]) + 1
        dif      = standard - frame_in
        chunk    = pitch_arr[int(phoneme2[i][0]): int(phoneme2[i][1]) + 1].copy()

        if dif > 0:
            chunk = np.append(chunk, np.full(dif, np.nan))
        elif dif < 0:
            cut = abs(dif)
            if i == 0:
                chunk = chunk[cut:]
            else:
                chunk = chunk[: len(chunk) - cut]

        result = chunk if i == 0 else np.concatenate([result, chunk])

    return result