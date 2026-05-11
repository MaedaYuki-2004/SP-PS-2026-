"""
core/formant.py
フォルマント（F1/F2）分析と声質評価（ジッター・シマー）を担当するモジュール。

【変更点】
  calc_vowel_score() のフォルマント比較を Hz スケール → Bark スケールに変更。

  【なぜ Bark スケールか】
  Hz はフォルマント比較に不適切なスケールである。

  具体的な問題：
  - F1 が 300Hz から 350Hz に変化（+50Hz）
  - F2 が 2000Hz から 2050Hz に変化（+50Hz）
  Hz 上では同じ 50Hz の差だが、人間の知覚では F1 の変化の方が
  はるかに大きく聞こえる（F1 は低周波域で知覚が敏感）。

  Bark スケールは人間の聴覚特性に基づいた知覚的に均等なスケールで、
  上記の例では F1 の変化が Bark 上で約 0.9 Bark、
  F2 の変化が約 0.1 Bark と正しく評価される。

  【Bark 変換式】
  Bark = 26.81 × F / (1960 + F) − 0.53

  【スケール値の根拠】
  話者間で同じ母音を発音したとき、性別・年齢の違いにより：
    F1 は 0.5〜2.0 Bark 程度ずれることがある
    F2 は 1.0〜3.0 Bark 程度ずれることがある
  bark_scale_f1=3.0、bark_scale_f2=4.0 はこの話者差を「許容範囲」と見なす設定。
  発音の誤りはこれを超えるずれとして検出される。
"""
from __future__ import annotations

import numpy as np
import parselmouth
import parselmouth.praat

_VOWEL_CHARS = {'a', 'i', 'u', 'e', 'o'}


def _has_vowel(mora_label: str) -> bool:
    """モーララベルが母音成分を含むか判定する（N・q などは False）。"""
    return any(v in mora_label for v in _VOWEL_CHARS)


def _hz_to_bark(f: float) -> float:
    """
    Hz → Bark スケールに変換する。

    Bark スケールは人間の聴覚特性（蝸牛の非線形応答）に基づく知覚的均等スケール。
    低周波域（F1 帯域）の変化を高周波域（F2 帯域）と比較可能な単位で表せる。

    変換式: Bark = 26.81 × F / (1960 + F) − 0.53

    目安：
      100 Hz  ≈  0.98 Bark
      500 Hz  ≈  5.04 Bark
      1000 Hz ≈  8.51 Bark
      2000 Hz ≈ 13.30 Bark
      5000 Hz ≈ 19.68 Bark
    """
    if f <= 0:
        return 0.0
    return 26.81 * f / (1960.0 + f) - 0.53


# ── フォルマント抽出 ──────────────────────────────────────────────────

def extract_mora_formants(
    sound_file: str,
    mora_list: list,
    max_formant: float = 5500.0,
) -> list[dict]:
    """
    各モーラの中心時刻で F1・F2 フォルマントを抽出する。

    Parameters
    ----------
    sound_file  : 音声ファイルパス
    mora_list   : lab_load() が返す mora_list（各要素 [start, end, label]）
    max_formant : フォルマント検出の上限周波数（Hz）
                  男性話者 → 5000Hz、女性話者 → 5500Hz（デフォルト）
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
        start  = float(mora_info[0])
        end    = float(mora_info[1])
        label  = str(mora_info[2])
        center = (start + end) / 2.0

        try:
            f1 = formant.get_value_at_time(1, center)
            f2 = formant.get_value_at_time(2, center)
            f1 = float(f1) if (f1 is not None and not np.isnan(float(f1))) else None
            f2 = float(f2) if (f2 is not None and not np.isnan(float(f2))) else None
        except Exception:
            f1, f2 = None, None

        results.append({
            "label":  label,
            "start":  start,
            "end":    end,
            "center": center,
            "f1":     f1,
            "f2":     f2,
        })

    return results


# ── 母音品質スコア ────────────────────────────────────────────────────

def calc_vowel_score(
    native_formants: list[dict],
    user_formants:   list[dict],
    max_score: float = 20.0,
    bark_scale_f1: float = 3.0,
    bark_scale_f2: float = 4.0,
) -> tuple[float, str]:
    """
    ネイティブと録音の F1/F2 を Bark スケールで比較して母音品質スコアを算出する。

    【Bark スケール比較の流れ】
    1. native と user の F1/F2 を Hz → Bark に変換
    2. Bark 差を bark_scale で正規化して距離を計算
       db1 = (bark(native_F1) - bark(user_F1)) / bark_scale_f1
       db2 = (bark(native_F2) - bark(user_F2)) / bark_scale_f2
       dist = sqrt(db1² + db2²)
    3. 指数減衰でスコア化
       score = max_score × exp(-dist × 1.2)

    【bark_scale の設定根拠】
    話者が異なる場合（男性 vs 女性など）、
    同じ母音でも F1 が 0.5〜2.0 Bark、F2 が 1.0〜3.0 Bark 程度異なる。
    bark_scale_f1=3.0 / bark_scale_f2=4.0 は、この話者差を超えるずれを
    「発音の問題」と見なすための閾値。

    【スコアの目安】
    dist ≈ 0.2（F1差0.6Bark・F2差0.8Bark）→ 約 16 点
    dist ≈ 0.5（F1差1.5Bark・F2差2.0Bark）→ 約 11 点
    dist ≈ 1.0（F1差3.0Bark・F2差4.0Bark）→ 約  6 点
    dist ≈ 1.5（F1差4.5Bark・F2差6.0Bark）→ 約  3 点

    Parameters
    ----------
    native_formants : extract_mora_formants() のネイティブ結果
    user_formants   : extract_mora_formants() の録音結果
    max_score       : 満点（デフォルト 20）
    bark_scale_f1   : F1 の許容ずれ（Bark）。大きいほど寛容。
    bark_scale_f2   : F2 の許容ずれ（Bark）。大きいほど寛容。
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

        # Hz → Bark 変換
        native_b1 = _hz_to_bark(native["f1"])
        native_b2 = _hz_to_bark(native["f2"])
        user_b1   = _hz_to_bark(user["f1"])
        user_b2   = _hz_to_bark(user["f2"])

        # Bark 正規化距離
        db1  = (native_b1 - user_b1) / bark_scale_f1
        db2  = (native_b2 - user_b2) / bark_scale_f2
        dist = float(np.sqrt(db1 ** 2 + db2 ** 2))

        distances.append(dist)
        mora_dists.append((dist, label))

    if not distances:
        return round(max_score * 0.5, 1), "母音の評価データが不十分でした。"

    mean_dist = float(np.mean(distances))

    # 指数減衰スコア（どんな距離でも 0 点にならない）
    score = round(max_score * float(np.exp(-mean_dist * 1.2)), 1)
    score = max(0.5, min(max_score, score))

    worst_dist, worst_label = max(mora_dists, key=lambda x: x[0])

    if mean_dist < 0.3:
        feedback = "母音の発音が正確です！口の形が正しく作れています。"
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


# ── 声質評価（ジッター・シマー） ──────────────────────────────────────

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