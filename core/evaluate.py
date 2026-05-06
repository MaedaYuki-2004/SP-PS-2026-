"""
core/evaluate.py
アクセント型とピッチ曲線・音素長を照合して発音スコアを算出するモジュール。

スコア構成（100点満点）
  ・アクセントスコア（60点）：ピッチ曲線がアクセント型のパターンに合っているか
  ・長さスコア    （40点）：各モーラの長さがサンプルと近いか
"""
from __future__ import annotations

import numpy as np


# ── アクセント型の説明 ─────────────────────────────────────────────────
ACCENT_LABELS = {
    0: "平板型",
    1: "頭高型",
    2: "中高型（2型）",
    3: "中高型（3型）",
    4: "中高型（4型）",
}


def _accent_label(accent: int | None) -> str:
    if accent is None:
        return "不明"
    return ACCENT_LABELS.get(accent, f"{accent}型")


# ── アクセントパターン生成 ────────────────────────────────────────────

def expected_accent_pattern(accent: int, n_mora: int) -> list[str]:
    """
    アクセント型とモーラ数から、各モーラの高低パターンを返す。

    Returns
    -------
    list[str] : 各モーラの期待高低（"H"=高, "L"=低）
    """
    if n_mora == 0:
        return []

    pattern = []

    if accent == 0:
        # 平板型：1拍目低、2拍目以降高
        for i in range(n_mora):
            pattern.append("L" if i == 0 else "H")

    elif accent == 1:
        # 頭高型：1拍目高、2拍目以降低
        for i in range(n_mora):
            pattern.append("H" if i == 0 else "L")

    else:
        # N型：1拍目低、2〜N拍目高、N+1拍目以降低
        for i in range(n_mora):
            if i == 0:
                pattern.append("L")
            elif i < accent:
                pattern.append("H")
            else:
                pattern.append("L")

    return pattern


# ── アクセントスコア算出 ──────────────────────────────────────────────

def calc_accent_score(
    pitch_fin2: list[float] | np.ndarray,
    mora_values: list[int],
    accent: int | None,
    n_mora: int,
) -> tuple[float, str, list[str]]:
    """
    ピッチ曲線がアクセント型のパターンに合っているかをスコア化する。

    Parameters
    ----------
    pitch_fin2  : 正規化済みの録音ピッチ（resample_to_10ms後・length_arrange後）
    mora_values : 各モーラの開始フレーム番号（Juliusフレーム、相対）
    accent      : アクセント型（0=平板, 1=頭高, N=N型, None=不明）
    n_mora      : モーラ数

    Returns
    -------
    score    : 0〜60 のスコア
    feedback : フィードバックメッセージ
    pattern  : 期待パターン（"H"/"L" のリスト）
    """
    if accent is None or n_mora == 0:
        return 30.0, "アクセント型が不明のため評価できません。", []

    pitch = np.array(pitch_fin2, dtype=float)
    if len(pitch) == 0 or np.all(np.isnan(pitch)):
        return 0.0, "ピッチが検出できませんでした。マイクに近づいて発音してください。", []

    # NaN を補間
    nan_mask = np.isnan(pitch)
    if np.any(~nan_mask):
        xs = np.arange(len(pitch))
        pitch = np.interp(xs, xs[~nan_mask], pitch[~nan_mask])

    pattern = expected_accent_pattern(accent, n_mora)
    if not pattern:
        return 30.0, "パターンを生成できませんでした。", pattern

    # 各モーラの平均ピッチを計算
    mora_pitches = []
    boundaries   = list(mora_values) + [len(pitch)]
    for i in range(min(n_mora, len(mora_values))):
        start = max(0, boundaries[i])
        end   = min(len(pitch), boundaries[i + 1])
        if start >= end:
            mora_pitches.append(np.nan)
        else:
            mora_pitches.append(float(np.nanmean(pitch[start:end])))

    if not mora_pitches or all(np.isnan(p) for p in mora_pitches):
        return 0.0, "各モーラのピッチを計算できませんでした。", pattern

    # 高低の判定：中央値より上なら"H"、以下なら"L"
    valid_pitches = [p for p in mora_pitches if not np.isnan(p)]
    if not valid_pitches:
        return 0.0, "有効なピッチ値がありませんでした。", pattern
    median_pitch  = np.median(valid_pitches)

    actual_pattern = []
    for p in mora_pitches:
        if np.isnan(p):
            actual_pattern.append("?")
        elif p > median_pitch:
            actual_pattern.append("H")
        else:
            actual_pattern.append("L")

    # 一致率を計算
    match_count = sum(
        1 for a, e in zip(actual_pattern, pattern)
        if a == e
    )
    total = min(len(actual_pattern), len(pattern))
    match_rate = match_count / total if total > 0 else 0.0
    score = round(match_rate * 60, 1)

    # フィードバック生成
    if match_rate >= 0.9:
        feedback = f"アクセント（{_accent_label(accent)}）が正確に発音できています！"
    elif match_rate >= 0.6:
        # どのモーラがずれているか特定
        wrong = [
            i + 1 for i, (a, e) in enumerate(zip(actual_pattern, pattern))
            if a != e and a != "?"
        ]
        if wrong:
            feedback = f"{wrong[0]}拍目のアクセントが異なります。{_accent_label(accent)}を意識してください。"
        else:
            feedback = f"アクセント（{_accent_label(accent)}）をもう少し意識して発音してください。"
    else:
        feedback = (
            f"アクセントが大きくずれています。"
            f"{_accent_label(accent)}：{' → '.join(pattern)} を意識してください。"
        )

    return score, feedback, pattern


# ── 長さスコア算出 ────────────────────────────────────────────────────

def calc_length_score(
    native_mora_length: list[float],
    user_mora_length: list[float],
) -> tuple[float, str]:
    """
    サンプルと録音のモーラ長の割合を比較してスコア化する。

    Parameters
    ----------
    native_mora_length : サンプルの各モーラの割合（%）
    user_mora_length   : 録音の各モーラの割合（%）

    Returns
    -------
    score    : 0〜40 のスコア
    feedback : フィードバックメッセージ
    """
    if not native_mora_length or not user_mora_length:
        return 20.0, "長さの評価ができませんでした。"

    n = min(len(native_mora_length), len(user_mora_length))
    if n == 0:
        return 20.0, "長さの評価ができませんでした。"

    native = np.array(native_mora_length[:n], dtype=float)
    user   = np.array(user_mora_length[:n],   dtype=float)

    # 各モーラの差（%ポイント）
    diffs = np.abs(native - user)
    mean_diff = float(np.mean(diffs))

    # 差が小さいほど高スコア（差0%→40点、差20%以上→0点）
    score = max(0.0, round((1.0 - mean_diff / 20.0) * 40, 1))

    # 最も差が大きいモーラを特定
    worst_idx  = int(np.argmax(diffs))
    worst_diff = float(diffs[worst_idx])

    if mean_diff < 5:
        feedback = "各モーラの長さのバランスがとても良いです！"
    elif mean_diff < 10:
        mora_num = worst_idx + 1
        if user[worst_idx] > native[worst_idx]:
            feedback = f"{mora_num}拍目が少し長すぎます。テンポよく発音してみてください。"
        else:
            feedback = f"{mora_num}拍目が少し短すぎます。もう少し伸ばして発音してみてください。"
    else:
        mora_num = worst_idx + 1
        if user[worst_idx] > native[worst_idx]:
            feedback = f"{mora_num}拍目が長すぎます（サンプルより {worst_diff:.0f}%多い）。"
        else:
            feedback = f"{mora_num}拍目が短すぎます（サンプルより {worst_diff:.0f}%少ない）。"

    return score, feedback


# ── 総合スコア ────────────────────────────────────────────────────────

def calc_total_score(
    pitch_fin2: list[float] | np.ndarray,
    mora_values: list[int],
    accent: int | None,
    n_mora: int,
    native_mora_length: list[float],
    user_mora_length: list[float],
) -> dict:
    """
    アクセントスコアと長さスコアを合算して総合スコアを返す。

    Returns
    -------
    dict : {
        "total":           総合スコア（0〜100）,
        "accent_score":    アクセントスコア（0〜60）,
        "length_score":    長さスコア（0〜40）,
        "accent_feedback": アクセントのフィードバック,
        "length_feedback": 長さのフィードバック,
        "accent_pattern":  期待アクセントパターン（["H","L",...]）,
        "accent_label":    アクセント型のラベル,
        "grade":           評価グレード（S/A/B/C/D）,
    }
    """
    accent_score, accent_feedback, pattern = calc_accent_score(
        pitch_fin2, mora_values, accent, n_mora
    )
    length_score, length_feedback = calc_length_score(
        native_mora_length, user_mora_length
    )

    total = round(accent_score + length_score, 1)

    if total >= 90:
        grade = "S"
    elif total >= 75:
        grade = "A"
    elif total >= 60:
        grade = "B"
    elif total >= 40:
        grade = "C"
    else:
        grade = "D"

    return {
        "total":           total,
        "accent_score":    accent_score,
        "length_score":    length_score,
        "accent_feedback": accent_feedback,
        "length_feedback": length_feedback,
        "accent_pattern":  pattern,
        "accent_label":    _accent_label(accent),
        "grade":           grade,
    }