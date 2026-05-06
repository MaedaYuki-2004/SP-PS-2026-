"""
core/pitch.py
ピッチ（基本周波数）の抽出・補間・スムージング・正規化を担当するモジュール。

【重要】Praatのフレームレートとjuliusのフレームレートについて
  Praatは独自の分析窓でF0を推定するため、フレーム番号がJuliusの
  10msフレームと一致しない。resample_to_10ms() でPraatピッチを
  10msグリッドに揃えてからJuliusフレーム番号をインデックスとして使うこと。
"""
from __future__ import annotations

import librosa
import numpy as np
import parselmouth

from config import PITCH_FLOOR_DEFAULT, PITCH_CEILING_DEFAULT

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


def praat_pitch(
    sound_file: str,
    pitch_floor: float = PITCH_FLOOR_DEFAULT,
    pitch_ceiling: float = PITCH_CEILING_DEFAULT,
    silence_threshold: float = 0.01,
    voicing_threshold: float = 0.3,
) -> tuple[list[float], list[float]]:
    """
    Praat（parselmouth）で F0 を推定する。無声区間は NaN に置換する。

    Parameters
    ----------
    sound_file         : 音声ファイルパス
    pitch_floor        : ピッチ検出下限（Hz）
                         男性：70Hz、女性：150Hz、デフォルト：70Hz
    pitch_ceiling      : ピッチ検出上限（Hz）
                         男性：200Hz、女性：400Hz、デフォルト：400Hz
    silence_threshold  : 無音と判定する振幅閾値（デフォルト 0.01）
                         Praatのデフォルトは 0.03。小さくすると小声でも検出可能。
    voicing_threshold  : 有声と判定する閾値（デフォルト 0.3）
                         Praatのデフォルトは 0.45。小さくするとNaNが減る。
                         録音音量が小さい・マイクが遠い場合に下げると改善する。
    """
    snd = parselmouth.Sound(sound_file)
    pitch = snd.to_pitch_ac(
        pitch_floor=pitch_floor,
        pitch_ceiling=pitch_ceiling,
        silence_threshold=silence_threshold,
        voicing_threshold=voicing_threshold,
    )
    values = pitch.selected_array["frequency"].copy()
    values[values == 0] = np.nan
    return values.tolist(), pitch.xs().tolist()


# ── Juliusフレームとの整合 ────────────────────────────────────────────

def resample_to_10ms(
    pitch: list[float] | np.ndarray,
    times: list[float] | np.ndarray,
) -> np.ndarray:
    """
    Praatのピッチ配列をJuliusと同じ10msグリッドにリサンプリングする。

    Praatは独自の分析窓でF0を推定するため、インデックスNがN×10msに
    対応するとは限らない。このリサンプリング後は：
        resampled[julius_frame_N] ≈ F0 at N×10ms
    となるため、Juliusのフレーム番号を直接インデックスとして使える。

    NaN（無声区間）は線形補間の対象外とし、最近傍フレームのNaN状態を
    そのまま引き継ぐ。
    """
    times_arr = np.array(times, dtype=float)
    pitch_arr = np.array(pitch, dtype=float)

    if len(times_arr) == 0:
        return pitch_arr

    duration  = times_arr[-1]
    n_frames  = int(duration / 0.01) + 1
    grid      = np.arange(n_frames) * 0.01  # 10ms グリッド

    nan_mask = np.isnan(pitch_arr)

    # NaN以外の値のみ使って線形補間
    valid_idx = ~nan_mask
    if not np.any(valid_idx):
        return np.full(n_frames, np.nan)

    resampled = np.interp(grid, times_arr[valid_idx], pitch_arr[valid_idx])

    # 元のNaN区間に対応するグリッド点にNaNを復元する
    # （最近傍のPraatフレームがNaNならそのグリッド点もNaN）
    for gi, t in enumerate(grid):
        nearest_idx = int(np.argmin(np.abs(times_arr - t)))
        if nan_mask[nearest_idx]:
            resampled[gi] = np.nan

    return resampled


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
    idx = 0
    while idx < len(pitch) and np.isnan(pitch[idx]):
        idx += 1
    if 0 < idx < len(pitch):
        pitch[:idx] = pitch[idx]
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
    pad    = window // 2
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

    【前提】pitch は resample_to_10ms() 済みの10msグリッド配列であること。
    phoneme1・phoneme2 はともにJuliusの相対フレーム番号（0始まり）であること。
    これにより「フレーム番号 = 配列インデックス」が保証される。
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
            chunk = chunk[cut:] if i == 0 else chunk[:len(chunk) - cut]

        result = chunk if i == 0 else np.concatenate([result, chunk])

    return result