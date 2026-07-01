"""
core/pitch.py
ピッチ（基本周波数）の抽出・補間・スムージング・正規化を担当するモジュール。

【変更点】
  smooth() のデフォルト window を 5（50ms）→ 3（30ms）に短縮。

  【理由】
  日本語のアクセント変化はモーラ境界で急激に起きる。
  window=5（50ms）では境界が平滑化されすぎてアクセント核の検出が甘くなる。
  window=3（30ms）の方が境界の立ち上がり・立ち下がりが鮮明になり、
  detect_accent_nucleus() の精度が向上する。

  表示用グラフ（pitch_native, pitch_learn）はHzのまま window=5 を使い、
  スコア計算用（pitch_fin, pitch_fin2）は window=3 を使う。
  app.py 側で window 引数を明示的に渡すことで使い分けが可能。
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


def estimate_pitch_range(
    sound_file: str,
    percentile_low:  float = 10.0,
    percentile_high: float = 90.0,
    margin_low:      float = 0.75,
    margin_high:     float = 1.50,
) -> tuple[float, float]:
    """
    音声ファイルから話者のピッチ範囲を自動推定し、
    Praat に渡す (pitch_floor, pitch_ceiling) を返す。
    """
    snd        = parselmouth.Sound(sound_file)
    pitch_wide = snd.to_pitch_ac(pitch_floor=50.0, pitch_ceiling=700.0)
    values     = pitch_wide.selected_array["frequency"].copy()
    voiced     = values[values > 0]

    if len(voiced) < 10:
        return PITCH_FLOOR_DEFAULT, PITCH_CEILING_DEFAULT

    p_low   = np.percentile(voiced, percentile_low)
    p_high  = np.percentile(voiced, percentile_high)
    floor   = max(50.0,  p_low  * margin_low)
    ceiling = min(700.0, p_high * margin_high)

    if floor >= ceiling:
        return PITCH_FLOOR_DEFAULT, PITCH_CEILING_DEFAULT

    return round(floor, 1), round(ceiling, 1)


def praat_pitch(
    sound_file: str,
    pitch_floor: float = PITCH_FLOOR_DEFAULT,
    pitch_ceiling: float = PITCH_CEILING_DEFAULT,
    silence_threshold: float = 0.01,
    voicing_threshold: float = 0.3,
) -> tuple[list[float], list[float]]:
    """
    Praat（parselmouth）で F0 を推定する。無声区間は NaN に置換する。
    pitch_floor / pitch_ceiling には estimate_pitch_range() の返り値を渡すこと。
    """
    snd   = parselmouth.Sound(sound_file)
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
    """Praatのピッチ配列をJuliusと同じ10msグリッドにリサンプリングする。"""
    times_arr = np.array(times, dtype=float)
    pitch_arr = np.array(pitch, dtype=float)
    if len(times_arr) == 0:
        return pitch_arr

    duration  = times_arr[-1]
    n_frames  = int(duration / 0.01) + 1
    grid      = np.arange(n_frames) * 0.01
    nan_mask  = np.isnan(pitch_arr)
    valid_idx = ~nan_mask

    if not np.any(valid_idx):
        return np.full(n_frames, np.nan)

    resampled = np.interp(grid, times_arr[valid_idx], pitch_arr[valid_idx])
    for gi, t in enumerate(grid):
        nearest_idx = int(np.argmin(np.abs(times_arr - t)))
        if nan_mask[nearest_idx]:
            resampled[gi] = np.nan

    return resampled


# ── Hz → 半音変換 ────────────────────────────────────────────────────

def hz_to_semitone(
    pitch: list[float] | np.ndarray,
    ref_hz: float | None = None,
) -> np.ndarray:
    """ピッチ配列を Hz → 半音（semitone）スケールに変換する。"""
    arr = np.array(pitch, dtype=float)
    arr[arr == 0] = np.nan
    valid_mask = ~np.isnan(arr)

    if not np.any(valid_mask):
        return arr
    if ref_hz is None:
        ref_hz = float(np.median(arr[valid_mask]))
    if ref_hz <= 0:
        return arr

    result = np.full_like(arr, np.nan)
    result[valid_mask] = 12.0 * np.log2(arr[valid_mask] / ref_hz)
    return result


# ── NaN 補間 ──────────────────────────────────────────────────────────

def _graph_compensate(pitch: np.ndarray, idx: int, count: int) -> np.ndarray:
    n_space  = pitch[idx - 1: idx + count + 1]
    distance = n_space[-1] - n_space[0]
    diff     = distance / (count + 1)
    pitch[idx: idx + count] = np.linspace(
        n_space[0] + diff, n_space[0] + diff * count, count,
    )
    return pitch


def _fill_internal_nan(pitch: np.ndarray) -> np.ndarray:
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

def smooth(
    pitch: list[float] | np.ndarray,
    window: int = 3,
) -> np.ndarray:
    """
    移動平均でピッチ曲線をスムージングする。

    【window のデフォルト変更：5 → 3】
    日本語アクセントの変化はモーラ境界で急激に起きるため、
    window=5（50ms）では境界が平滑化されすぎる。
    window=3（30ms）の方がアクセント核の検出精度が向上する。

    使い分け：
      スコア計算用（pitch_fin, pitch_fin2）→ window=3（デフォルト）
      表示用グラフ（pitch_native, pitch_learn）→ window=5 を明示的に指定
    """
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


def normalize_zscore(
    values: list[float] | np.ndarray,
    clip_sigma: float = 2.5,
) -> np.ndarray:
    """
    Z-score 正規化（0〜1）。外れ値に対してロバスト。
    半音変換後は scale() で十分なため、通常は使用しない。
    特に外れ値が多い録音環境での代替手段として残しておく。
    """
    arr = np.array(values, dtype=float)
    if not len(arr):
        return arr
    mean = np.mean(arr)
    std  = np.std(arr)
    if std == 0:
        return np.zeros_like(arr)
    z         = (arr - mean) / std
    z_clipped = np.clip(z, -clip_sigma, clip_sigma)
    return (z_clipped + clip_sigma) / (2.0 * clip_sigma)


# ── 長さ整合 ──────────────────────────────────────────────────────────

def _resample_chunk(chunk: np.ndarray, target_len: int) -> np.ndarray:
    """
    音素チャンクを target_len フレームに線形補間でリサンプリングする。
    各出力フレームを元配列の対応位置に線形補間でマップし、
    両隣が NaN のフレームのみ NaN を出力する。
    """
    src_len = len(chunk)
    if src_len == 0:
        return np.full(target_len, np.nan)
    if src_len == target_len:
        return chunk.copy()

    result = np.full(target_len, np.nan)
    for j in range(target_len):
        src_pos = j * (src_len - 1) / (target_len - 1) if target_len > 1 else 0.0
        i0      = int(np.floor(src_pos))
        i1      = min(i0 + 1, src_len - 1)
        v0, v1  = chunk[i0], chunk[i1]

        if np.isnan(v0) and np.isnan(v1):
            result[j] = np.nan                   # 両隣とも無声 → NaN
        elif np.isnan(v0):
            result[j] = v1                       # 片方が有声 → 有声側の値
        elif np.isnan(v1):
            result[j] = v0
        else:
            t         = src_pos - i0
            result[j] = v0 * (1.0 - t) + v1 * t  # 両隣とも有声 → 線形補間

    return result


def length_arrange(
    pitch: list[float] | np.ndarray,
    phoneme1: list,
    phoneme2: list,
) -> np.ndarray:
    """
    phoneme1（基準）の各音素長に合わせて pitch（録音側）を線形補間でリサンプリングする。

    変更前：不足分に NaN を追加、超過分を切り取り（音調の形が崩れる）。
    変更後：各音素を _resample_chunk で等比リサンプリング（音調の形を保持）。
    これにより「ピッチ比較（正規化）」グラフで録音タイミング差が吸収される。
    """
    pitch_arr = np.array(pitch, dtype=float)
    usable    = min(len(phoneme1), len(phoneme2))
    if usable == 0:
        raise ValueError("音素フレーム情報が不足しています")

    chunks = []
    for i in range(usable):
        target_len = int(phoneme1[i][1]) - int(phoneme1[i][0]) + 1
        src_start  = int(phoneme2[i][0])
        src_end    = int(phoneme2[i][1]) + 1
        chunk      = pitch_arr[src_start:src_end].copy()
        chunks.append(_resample_chunk(chunk, target_len))

    return np.concatenate(chunks) if chunks else np.array([], dtype=float)