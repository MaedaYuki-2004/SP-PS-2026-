"""
core/formant.py
フォルマント（F1/F2）分析と声質評価を担当するモジュール。

【変更点：フォルマント抽出位置の改善】
  変更前：モーラの中心時刻（50%）の1点のみを取得。
  変更後：モーラの30%・50%・70%の3点を取得し、有効値の平均を使用。

  【なぜ改善が必要か】
  モーラの中心時刻だけを使うと、子音の影響が残った区間を拾う場合がある。
  例：「か」の中心が子音 /k/ の解放直後に当たると、
      /a/ の安定した母音区間ではなくフォルマント遷移部分を測定してしまう。

  【改善の効果】
  モーラの30〜70%の複数点を平均することで：
  - 子音からの遷移区間を避けて母音の安定核を測定できる
  - 単一時刻の測定ノイズに対して頑健になる
  - 長音モーラなど幅が広い場合でも適切な区間を測定できる

  【サンプル点の設定根拠】
  日本語の音節構造では、母音の安定した部分（母音核）は
  モーラ全体の30〜70%の範囲に収まることが多い（Ladefoged 2003）。
  3点を均等に配置することで偏りを防ぐ。
"""
from __future__ import annotations

import numpy as np
import parselmouth
import parselmouth.praat

_VOWEL_CHARS = {'a', 'i', 'u', 'e', 'o'}

# モーラ内でフォルマントを測定するサンプル点の割合
# 30%・50%・70%の3点を平均する
_SAMPLE_RATIOS = [0.30, 0.50, 0.70]


def _has_vowel(mora_label: str) -> bool:
    """モーララベルが母音成分を含むか判定する（N・q などは False）。"""
    return any(v in mora_label for v in _VOWEL_CHARS)


def _hz_to_bark(f: float) -> float:
    """
    Hz → Bark スケールに変換する。
    変換式: Bark = 26.81 × F / (1960 + F) − 0.53
    """
    if f <= 0:
        return 0.0
    return 26.81 * f / (1960.0 + f) - 0.53


def _get_formant_at_time(formant, t: float) -> tuple[float | None, float | None]:
    """指定時刻の F1・F2 を安全に取得する。"""
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

    【改善点】
    モーラの30%・50%・70%の3時刻でフォルマントを測定し、
    有効な値（NaN でない値）の平均を最終値とする。

    有効な測定点が0個の場合は None を返す（後続のスコア計算でスキップされる）。

    Parameters
    ----------
    sound_file  : 音声ファイルパス
    mora_list   : lab_load() が返す mora_list（各要素 [start, end, label]）
    max_formant : フォルマント検出の上限周波数（Hz）
                  男性話者 → 5000Hz、女性話者 → 5500Hz
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
        start = float(mora_info[0])
        end   = float(mora_info[1])
        label = str(mora_info[2])
        duration = end - start

        # 複数点でフォルマントを取得して平均する
        f1_vals: list[float] = []
        f2_vals: list[float] = []

        for ratio in _SAMPLE_RATIOS:
            t = start + duration * ratio
            # 音声の有効範囲内に収める
            t = max(snd.start_time, min(snd.end_time, t))
            f1, f2 = _get_formant_at_time(formant, t)
            if f1 is not None:
                f1_vals.append(f1)
            if f2 is not None:
                f2_vals.append(f2)

        # 有効値の平均を最終フォルマント値とする
        f1_mean = float(np.mean(f1_vals)) if f1_vals else None
        f2_mean = float(np.mean(f2_vals)) if f2_vals else None

        results.append({
            "label":  label,
            "start":  start,
            "end":    end,
            "center": start + duration * 0.5,  # 参照用（従来の中心時刻）
            "f1":     f1_mean,
            "f2":     f2_mean,
            "f1_n_samples": len(f1_vals),  # デバッグ用：有効サンプル数
            "f2_n_samples": len(f2_vals),
        })

    return results


def calc_vowel_score(
    native_formants: list[dict],
    user_formants:   list[dict],
    max_score: float = 20.0,
    bark_scale_f1: float = 3.0,
    bark_scale_f2: float = 4.0,
) -> tuple[float, str]:
    """
    ネイティブと録音の F1/F2 を Bark スケールで比較して母音品質スコアを算出する。

    dist  = √( (ΔBark_F1 / 3.0)² + (ΔBark_F2 / 4.0)² )
    score = 20 × exp(−1.2 × dist)
    """
    n          = min(len(native_formants), len(user_formants))
    distances  = []
    mora_dists = []

    for i in range(n):
        native = native_formants[i]
        user   = user_formants[i]
        label  = native["label"]

        if not _has_vowel(label):
            continue
        if any(x is None for x in [native["f1"], native["f2"], user["f1"], user["f2"]]):
            continue

        native_b1 = _hz_to_bark(native["f1"])
        native_b2 = _hz_to_bark(native["f2"])
        user_b1   = _hz_to_bark(user["f1"])
        user_b2   = _hz_to_bark(user["f2"])

        db1  = (native_b1 - user_b1) / bark_scale_f1
        db2  = (native_b2 - user_b2) / bark_scale_f2
        dist = float(np.sqrt(db1 ** 2 + db2 ** 2))

        distances.append(dist)
        mora_dists.append((dist, label))

    if not distances:
        return round(max_score * 0.5, 1), "母音の評価データが不十分でした。"

    mean_dist = float(np.mean(distances))
    score     = round(max_score * float(np.exp(-mean_dist * 1.2)), 1)
    score     = max(0.5, min(max_score, score))

    worst_dist, worst_label = max(mora_dists, key=lambda x: x[0])

    if mean_dist < 0.3:
        feedback = "母音の発音が正確です。口の形が正しく作れています。"
    elif mean_dist < 0.7:
        feedback = (
            f"「{worst_label}」の母音が少しずれています。"
            f"サンプル音声を参考に口の形を確認してください。"
        )
    elif mean_dist < 1.2:
        feedback = (
            f"「{worst_label}」の母音がずれています。"
            f"口の開き方と舌の位置を意識して発音してください。"
        )
    else:
        feedback = (
            f"「{worst_label}」の母音が大きくずれています。"
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
    """
    ジッター（F0変動率）とシマー（振幅変動率）で声質を評価する。
    スコアには影響せず、フィードバック生成のみに使用する。
    """
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