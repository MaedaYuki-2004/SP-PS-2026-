"""
core/formant.py
フォルマント（F1/F2）分析と声質評価を担当するモジュール。

【改善①：話者性別によるフォルマント補正】
  変更前：男性・女性問わず同じ距離計算をしていた。
          VOICEVOXの声（女性寄り）を基準にしているため、
          男性ユーザーが録音するとF1/F2が体系的に低くなり
          母音スコアが常に低く出る問題があった。

  変更後：estimate_pitch_range()が返すpitch_ceiling_userを受け取り、
          200Hz以下の場合は男性と判定してネイティブのF1/F2に
          補正係数（MALE_F1_SCALE / MALE_F2_SCALE）を適用する。

  【補正係数の根拠】
  Peterson & Barney (1952) ほか音響音声学の研究によると、
  男性の平均フォルマントは女性の約82〜87%。
  ここでは保守的に0.85を採用。
  単語ごとに変わらない定数なので _MALE_FORMANT_SCALE で一元管理。

  【判定しきい値：200Hz】
  estimate_pitch_range() が返す ceiling の目安：
    女性話者 → 350〜550Hz 程度
    男性話者 → 150〜250Hz 程度
  200Hz を境界にすることで誤判定を最小化できる。

【改善②：有効サンプル数による信頼度重み付け】
  変更前：30/50/70%の3点のうち有効値が1点だけでも、
          3点すべて有効なモーラと同じ重みで距離の平均を計算していた。

  変更後：モーラごとの有効サンプル数を信頼度スコアに変換し、
          加重平均で最終距離を算出する。

          信頼度 = min(native有効数, user有効数) / 3.0
            → 3点すべて有効 : weight = 1.0
            → 2点有効       : weight = 0.67
            → 1点のみ有効   : weight = 0.33

  子音が長いモーラや短いモーラで1点しか取れないケースで
  ノイズを拾った値が過大に影響するのを防ぐ。
"""
from __future__ import annotations

import numpy as np
import parselmouth
import parselmouth.praat

_VOWEL_CHARS = {'a', 'i', 'u', 'e', 'o'}

# モーラ内でフォルマントを測定するサンプル点の割合
_SAMPLE_RATIOS = [0.30, 0.50, 0.70]

# ── ① 性別補正係数 ─────────────────────────────────────────────────
# 男性のフォルマントは女性の約85%（Peterson & Barney 1952 ほかより）
_MALE_FORMANT_SCALE = 0.85
# pitch_ceiling_user がこの値以下なら男性と判定する（Hz）
_MALE_CEILING_THRESHOLD = 200.0


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


def extract_mora_formants(
    sound_file: str,
    mora_list: list,
    max_formant: float = 5500.0,
) -> list[dict]:
    """
    各モーラの安定した母音区間で F1・F2 フォルマントを抽出する。
    モーラ区間の30%・50%・70%の3点を測定して有効値の平均を返す。
    """
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

    return results


def calc_vowel_score(
    native_formants:    list[dict],
    user_formants:      list[dict],
    max_score:          float = 20.0,
    bark_scale_f1:      float = 3.0,
    bark_scale_f2:      float = 4.0,
    pitch_ceiling_user: float | None = None,   # ← ① 性別補正に使用
) -> tuple[float, str]:
    """
    ネイティブと録音の F1/F2 を Bark スケールで比較して母音品質スコアを算出する。

    【改善①】pitch_ceiling_user が 200Hz 以下なら男性と判定し、
              ネイティブの F1/F2 に補正係数 (_MALE_FORMANT_SCALE) を適用する。
              これにより男性ユーザーでも公平なスコアが得られる。

    【改善②】モーラごとの有効サンプル数を信頼度重みに変換し、
              加重平均で最終距離を算出する。
              有効サンプルが少ないモーラほど距離計算への影響が小さくなる。

    dist  = √( (ΔBark_F1 / 3.0)² + (ΔBark_F2 / 4.0)² )
    score = 20 × exp(−1.2 × mean_dist)
    """
    # ── ① 性別判定と補正係数の決定 ─────────────────────────────────
    if (pitch_ceiling_user is not None
            and pitch_ceiling_user <= _MALE_CEILING_THRESHOLD):
        f1_correction = _MALE_FORMANT_SCALE
        f2_correction = _MALE_FORMANT_SCALE
        is_male       = True
    else:
        f1_correction = 1.0
        f2_correction = 1.0
        is_male       = False

    n           = min(len(native_formants), len(user_formants))
    distances:  list[float] = []
    weights:    list[float] = []   # ② サンプル数重み
    mora_dists: list[tuple[float, str]] = []

    for i in range(n):
        native = native_formants[i]
        user   = user_formants[i]
        label  = native["label"]

        if not _has_vowel(label):
            continue

        # ① 補正済みのネイティブ F1/F2
        native_f1_raw = native["f1"]
        native_f2_raw = native["f2"]
        if native_f1_raw is None or native_f2_raw is None:
            continue
        native_f1 = native_f1_raw * f1_correction
        native_f2 = native_f2_raw * f2_correction

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

        # ── ② 信頼度重みの計算 ──────────────────────────────────────
        n_native = native.get("f1_n_samples", 3)
        n_user   = user.get("f1_n_samples",   3)
        # 少ない方を基準に信頼度を決める（0.33 / 0.67 / 1.0）
        confidence = min(n_native, n_user) / len(_SAMPLE_RATIOS)

        distances.append(dist)
        weights.append(confidence)
        mora_dists.append((dist, label))

    if not distances:
        return round(max_score * 0.5, 1), "母音の評価データが不十分でした。"

    # ── ② 加重平均で最終距離を算出 ─────────────────────────────────
    total_weight = float(sum(weights))
    if total_weight > 0:
        mean_dist = float(
            sum(d * w for d, w in zip(distances, weights)) / total_weight
        )
    else:
        mean_dist = float(np.mean(distances))

    score = round(max_score * float(np.exp(-mean_dist * 1.2)), 1)
    score = max(0.5, min(max_score, score))

    worst_dist, worst_label = max(mora_dists, key=lambda x: x[0])

    # フィードバック生成
    gender_note = "（男性補正済み）" if is_male else ""
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