"""
core/formant.py
フォルマント（F1/F2）分析と声質評価を担当するモジュール。

【性別補正ロジックの修正】
  変更前：ユーザーの性別のみを見て補正していた。
          「ネイティブ音声は女性寄り」という前提が誤りだった。
          サンプル音声が男性の場合、補正が逆効果になっていた。

  変更後：ネイティブとユーザー両方の性別を検出し、
          組み合わせに応じて補正係数を決定する。

  【補正ロジック】
  ネイティブ・ユーザーの性別を pitch_ceiling で判定
  （ceiling ≤ 200Hz → 男性、> 200Hz → 女性）

  ┌─────────┬─────────┬─────────────────────────────┐
  │ネイティブ│ユーザー  │ 補正                        │
  ├─────────┼─────────┼─────────────────────────────┤
  │ 男性    │ 男性    │ なし（同性・そのまま比較）   │
  │ 女性    │ 女性    │ なし（同性・そのまま比較）   │
  │ 女性    │ 男性    │ ネイティブ × 0.85           │
  │ 男性    │ 女性    │ ネイティブ × 1.18（÷0.85）  │
  └─────────┴─────────┴─────────────────────────────┘

  【補正の限界（重要）】
  0.85 は集団平均値のため個人差を完全には吸収できない。
  「性別による評価の偏りを軽減した」であり、
  「完全に公平にした」ではない点に注意すること。
"""
from __future__ import annotations

import hashlib
import json as _json
import numpy as np
import parselmouth
import parselmouth.praat
from pathlib import Path

_VOWEL_CHARS = {'a', 'i', 'u', 'e', 'o'}
_SAMPLE_RATIOS = [0.30, 0.50, 0.70]
_MALE_FORMANT_SCALE = 0.85
_MALE_CEILING_THRESHOLD = 200.0

# ── フォルマントキャッシュ ────────────────────────────────────────
# ネイティブ音声のフォルマントは毎回同じ計算になるため、
# 初回だけ計算して data/config/formant_cache.json に保存する。
# ファイルの更新時刻（mtime）が変わると自動的にキャッシュを無効化する。
_CACHE_PATH = Path(__file__).parent.parent / "data" / "config" / "formant_cache.json"


def _cache_key(sound_file: str, max_formant: float) -> str:
    """ファイルパス・更新時刻・max_formant からキャッシュキーを生成する。"""
    p = Path(sound_file)
    mtime = str(p.stat().st_mtime) if p.exists() else "0"
    raw = f"{sound_file}:{max_formant}:{mtime}"
    return hashlib.md5(raw.encode()).hexdigest()


def _load_formant_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return _json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_formant_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(_json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_CACHE_PATH)


def _has_vowel(mora_label: str) -> bool:
    return any(v in mora_label for v in _VOWEL_CHARS)


def _hz_to_bark(f: float) -> float:
    if f <= 0:
        return 0.0
    return 26.81 * f / (1960.0 + f) - 0.53


def _get_formant_at_time(formant, t: float) -> tuple[float | None, float | None]:
    try:
        f1 = formant.get_value_at_time(1, t)
        f2 = formant.get_value_at_time(2, t)
        f1 = float(f1) if (f1 is not None and not np.isnan(float(f1))) else None
        f2 = float(f2) if (f2 is not None and not np.isnan(float(f2))) else None
        return f1, f2
    except Exception:
        return None, None


def _detect_gender(pitch_ceiling: float | None) -> str:
    """
    pitch_ceiling から話者の性別を推定する。

    Returns
    -------
    "male" / "female" / "unknown"
    """
    if pitch_ceiling is None:
        return "unknown"
    return "male" if pitch_ceiling <= _MALE_CEILING_THRESHOLD else "female"


def _calc_correction(
    native_ceiling: float | None,
    user_ceiling:   float | None,
) -> tuple[float, str]:
    """
    ネイティブとユーザーの性別の組み合わせから補正係数を返す。

    Returns
    -------
    (correction_factor, gender_note)
      correction_factor : ネイティブのF1/F2にかける係数
      gender_note       : フィードバックに添える補足文字列
    """
    native_gender = _detect_gender(native_ceiling)
    user_gender   = _detect_gender(user_ceiling)

    if native_gender == "unknown" or user_gender == "unknown":
        # 性別不明の場合は補正しない
        return 1.0, ""

    if native_gender == user_gender:
        # 同性 → 補正不要
        return 1.0, ""

    if native_gender == "female" and user_gender == "male":
        # ネイティブ女性・ユーザー男性
        # → ネイティブのF1/F2を × 0.85 して男性スケールに合わせる
        return _MALE_FORMANT_SCALE, "（男性補正済み）"

    # ネイティブ男性・ユーザー女性
    # → ネイティブのF1/F2を ÷ 0.85（× 1.18）して女性スケールに合わせる
    return 1.0 / _MALE_FORMANT_SCALE, "（女性補正済み）"


def extract_mora_formants(
    sound_file: str,
    mora_list: list,
    max_formant: float = 5500.0,
    use_cache: bool = False,
) -> list[dict]:
    """
    各モーラの安定した母音区間で F1・F2 フォルマントを抽出する。
    モーラ区間の 30%・50%・70%の3点を測定して有効値の平均を返す。

    use_cache=True のとき（ネイティブ音声用）、
    初回計算結果を data/config/formant_cache.json に保存し、
    2回目以降はキャッシュから返す。
    ファイルの更新時刻が変わるとキャッシュは自動で無効化される。
    """
    if use_cache:
        cache = _load_formant_cache()
        key   = _cache_key(sound_file, max_formant)
        if key in cache:
            return cache[key]

    snd     = parselmouth.Sound(sound_file)
    formant = snd.to_formant_burg(
        time_step=0.005,
        max_number_of_formants=5.0,
        maximum_formant=max_formant,
        window_length=0.025,
        pre_emphasis_from=50.0,
    )

    results = []
    for mora_info in mora_list:
        start    = float(mora_info[0])
        end      = float(mora_info[1])
        label    = str(mora_info[2])
        duration = end - start

        f1_vals: list[float] = []
        f2_vals: list[float] = []

        for ratio in _SAMPLE_RATIOS:
            t = start + duration * ratio
            t = max(snd.start_time, min(snd.end_time, t))
            f1, f2 = _get_formant_at_time(formant, t)
            if f1 is not None:
                f1_vals.append(f1)
            if f2 is not None:
                f2_vals.append(f2)

        f1_mean = float(np.mean(f1_vals)) if f1_vals else None
        f2_mean = float(np.mean(f2_vals)) if f2_vals else None

        results.append({
            "label":        label,
            "start":        start,
            "end":          end,
            "center":       start + duration * 0.5,
            "f1":           f1_mean,
            "f2":           f2_mean,
            "f1_n_samples": len(f1_vals),
            "f2_n_samples": len(f2_vals),
        })

    if use_cache:
        cache[key] = results
        _save_formant_cache(cache)

    return results


def calc_vowel_score(
    native_formants:     list[dict],
    user_formants:       list[dict],
    max_score:           float = 20.0,
    bark_scale_f1:       float = 3.0,
    bark_scale_f2:       float = 4.0,
    pitch_ceiling_native: float | None = None,  # ← ネイティブの性別判定に使用
    pitch_ceiling_user:   float | None = None,  # ← ユーザーの性別判定に使用
) -> tuple[float, str]:
    """
    ネイティブと録音の F1/F2 を Bark スケールで比較して母音品質スコアを算出する。

    【性別補正】
    ネイティブとユーザーの pitch_ceiling を比較し、
    性別の組み合わせに応じた補正係数をネイティブのF1/F2に適用する。

    同性同士 → 補正なし
    ネイティブ女性・ユーザー男性 → ネイティブ × 0.85
    ネイティブ男性・ユーザー女性 → ネイティブ × 1.18

    【サンプル数重み付け】
    有効サンプル数が少ないモーラほど距離計算への影響を下げる。
    """
    # ── 性別補正係数を決定 ────────────────────────────────────────
    correction, gender_note = _calc_correction(
        pitch_ceiling_native, pitch_ceiling_user
    )

    n           = min(len(native_formants), len(user_formants))
    distances:  list[float] = []
    weights:    list[float] = []
    mora_dists: list[tuple[float, str]] = []

    for i in range(n):
        native = native_formants[i]
        user   = user_formants[i]
        label  = native["label"]

        if not _has_vowel(label):
            continue

        native_f1_raw = native["f1"]
        native_f2_raw = native["f2"]
        if native_f1_raw is None or native_f2_raw is None:
            continue

        # 性別補正を適用
        native_f1 = native_f1_raw * correction
        native_f2 = native_f2_raw * correction

        user_f1 = user["f1"]
        user_f2 = user["f2"]
        if user_f1 is None or user_f2 is None:
            continue

        native_b1 = _hz_to_bark(native_f1)
        native_b2 = _hz_to_bark(native_f2)
        user_b1   = _hz_to_bark(user_f1)
        user_b2   = _hz_to_bark(user_f2)

        db1  = (native_b1 - user_b1) / bark_scale_f1
        db2  = (native_b2 - user_b2) / bark_scale_f2
        dist = float(np.sqrt(db1 ** 2 + db2 ** 2))

        # サンプル数重み付け
        n_native   = native.get("f1_n_samples", 3)
        n_user     = user.get("f1_n_samples",   3)
        confidence = min(n_native, n_user) / len(_SAMPLE_RATIOS)

        distances.append(dist)
        weights.append(confidence)
        mora_dists.append((dist, label))

    if not distances:
        return round(max_score * 0.5, 1), "母音の評価データが不十分でした。"

    # 加重平均で最終距離を算出
    total_weight = float(sum(weights))
    mean_dist = (
        float(sum(d * w for d, w in zip(distances, weights)) / total_weight)
        if total_weight > 0
        else float(np.mean(distances))
    )

    score = round(max_score * float(np.exp(-mean_dist * 1.0)), 1)
    score = max(0.5, min(max_score, score))

    worst_dist, worst_label = max(mora_dists, key=lambda x: x[0])

    if mean_dist < 0.3:
        feedback = f"母音の発音が正確です{gender_note}。口の形が正しく作れています。"
    elif mean_dist < 0.7:
        feedback = (
            f"「{worst_label}」の母音が少しずれています{gender_note}。"
            f"サンプル音声を参考に口の形を確認してください。"
        )
    elif mean_dist < 1.2:
        feedback = (
            f"「{worst_label}」の母音がずれています{gender_note}。"
            f"口の開き方と舌の位置を意識して発音してください。"
        )
    else:
        feedback = (
            f"「{worst_label}」の母音が大きくずれています{gender_note}。"
            f"口の開き方・舌の位置・唇の形を確認してください。"
        )

    return score, feedback


def calc_voice_quality(
    sound_file: str,
    start: float,
    end: float,
    pitch_floor: float = 75.0,
    pitch_ceiling: float = 500.0,
) -> dict:
    """ジッターとシマーで声質を評価する（スコアには非影響）。"""
    try:
        snd  = parselmouth.Sound(sound_file)
        part = snd.extract_part(from_time=start, to_time=end, preserve_times=False)
        pp   = parselmouth.praat.call(
            part, "To PointProcess (periodic, cc)",
            pitch_floor, pitch_ceiling,
        )
        jitter  = parselmouth.praat.call(
            pp, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3
        )
        shimmer = parselmouth.praat.call(
            [part, pp], "Get shimmer (local)",
            0, 0, 0.0001, 0.02, 1.3, 1.6,
        )
        jitter_pct  = float(jitter)  * 100
        shimmer_pct = float(shimmer) * 100
    except Exception:
        return {"jitter": None, "shimmer": None, "feedback": None}

    feedback = None
    if jitter_pct > 3.0:
        feedback = "声のピッチが不安定です。落ち着いた息遣いで安定した声を出してください。"
    elif shimmer_pct > 8.0:
        feedback = "声の音量が不安定です。一定の音量を保って発音してください。"

    return {
        "jitter":   round(jitter_pct,  3),
        "shimmer":  round(shimmer_pct, 3),
        "feedback": feedback,
    }