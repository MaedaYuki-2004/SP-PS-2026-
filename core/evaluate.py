"""
core/evaluate.py
アクセント型とピッチ曲線・音素長・母音品質を照合して発音スコアを算出するモジュール。

【スコア構成（100点満点）】
  ・アクセントスコア（50点）：4指標の加重平均
      - アクセント核位置    (40%)
      - ピッチ相関         (28%)
      - H/L パターン一致率 (22%)
      - モーラ内安定度     (10%)
  ・長さスコア（30点）
  ・母音品質スコア（20点）

【変更点】
  _pitch_correlation_score() を有声フレームのみで計算するよう変更。

  【変更前の問題】
  comp() で NaN（無声区間）を線形補間してから Pearson 相関を計算していた。
  補間された値（子音・無音部分）は実際のピッチではないため、
  相関計算に「ノイズ」が混入していた。

  【変更後】
  ネイティブと録音の「両方が有声（NaNでない）」なフレームのみを使って相関を計算する。
  これにより「実際に声が出ている区間同士のピッチの一致度」を正確に評価できる。

  必要な引数変更：
  - _pitch_correlation_score(): 引数を NaN保持配列に変更
  - calc_accent_score(): pitch_native_raw パラメータを追加
  - calc_total_score(): pitch_native_raw パラメータを追加
"""
from __future__ import annotations

import numpy as np
from scipy.stats import pearsonr


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
    """アクセント型とモーラ数から H/L パターンを返す。"""
    if n_mora == 0:
        return []
    pattern = []
    if accent == 0:
        for i in range(n_mora):
            pattern.append("L" if i == 0 else "H")
    elif accent == 1:
        for i in range(n_mora):
            pattern.append("H" if i == 0 else "L")
    else:
        for i in range(n_mora):
            if i == 0:
                pattern.append("L")
            elif i < accent:
                pattern.append("H")
            else:
                pattern.append("L")
    return pattern


# ── アクセント核（downstep）位置の検出 ───────────────────────────────

def detect_accent_nucleus(mora_pitches: list[float]) -> int:
    """
    モーラごとの平均ピッチから最大下降位置を検出してアクセント核型を返す。
    動的 DROP_THRESHOLD（ピッチ範囲の15%）を使用。
    """
    valid = [(i, p) for i, p in enumerate(mora_pitches) if not np.isnan(p)]
    if len(valid) < 2:
        return 0

    valid_values   = [p for _, p in valid]
    pitch_range    = max(valid_values) - min(valid_values)
    drop_threshold = max(0.05, min(0.25, pitch_range * 0.15))

    drops = []
    for k in range(len(valid) - 1):
        idx_curr, pitch_curr = valid[k]
        _,        pitch_next = valid[k + 1]
        drops.append((idx_curr, pitch_curr - pitch_next))

    max_idx, max_drop = max(drops, key=lambda x: x[1])
    return 0 if max_drop < drop_threshold else max_idx + 1


def _nucleus_score(
    detected: int,
    expected: int | None,
    n_mora: int,
    max_score: float = 60.0,
) -> float:
    if expected is None:
        return max_score / 2.0
    distance     = abs(detected - expected)
    # 分母を (n_mora + 1) にすることで1モーラずれても満点の1/3以上を確保
    penalty_unit = max_score / max(n_mora + 1, 3)
    return round(max(0.0, max_score - distance * penalty_unit), 1)


# ── ピッチ相関スコア（有声フレームのみ） ─────────────────────────────

def _pitch_correlation_score(
    pitch_native_raw: np.ndarray,
    pitch_user_raw:   np.ndarray,
    max_score: float = 60.0,
) -> float:
    """
    有声フレームのみを使ったピアソン相関係数でスコア化する。

    【変更点：有声フレームのみに限定】
    変更前：comp() で NaN を補間した配列を使って相関計算
            → 無声区間（子音部）の補間値がノイズとして混入
    変更後：ネイティブと録音の「両方が有声（NaN でない）」なフレームのみで計算
            → 実際に声が出ている区間同士の比較になる

    【入力配列について】
    pitch_native_raw / pitch_user_raw は comp() 適用前の NaN 保持配列を渡すこと。
    （hz_to_semitone() 直後の値、または length_arrange() 直後の値）
    comp() を通した配列を渡すと変更前と同じ動作になる。

    【有声フレーム数が少ない場合の扱い】
    有声フレーム数 < 4 の場合は相関を計算できないため中間点を返す。
    これはアライメント失敗または録音が極端に短い場合に起こりうる。
    """
    p1 = np.array(pitch_native_raw, dtype=float)
    p2 = np.array(pitch_user_raw,   dtype=float)

    min_len = min(len(p1), len(p2))
    if min_len < 4:
        return max_score / 2.0

    p1, p2 = p1[:min_len], p2[:min_len]

    # 両方が有声（NaN でない）のフレームのみを抽出
    voiced_mask = ~np.isnan(p1) & ~np.isnan(p2)
    n_voiced    = int(np.sum(voiced_mask))

    if n_voiced < 4:
        # 有声フレームが少なすぎる → 中間点
        return max_score / 2.0

    p1_voiced = p1[voiced_mask]
    p2_voiced = p2[voiced_mask]

    std1, std2 = float(np.std(p1_voiced)), float(np.std(p2_voiced))
    if std1 < 1e-6 or std2 < 1e-6:
        # 一方のピッチが完全に平坦 → 低スコア
        return round(max_score * 0.15, 1)

    try:
        r, _ = pearsonr(p1_voiced, p2_voiced)
    except Exception:
        return max_score / 2.0

    if np.isnan(r):
        return max_score / 2.0

    # r: -1〜1 → 0〜max_score
    score = max(0.0, (float(r) + 1.0) / 2.0 * max_score)
    return round(score, 1)


# ── モーラ内ピッチ安定度スコア ────────────────────────────────────────

def _mora_stability_score(
    pitch_user_raw: np.ndarray,
    mora_values: list[int],
    n_mora: int,
    max_score: float = 60.0,
    variance_threshold: float = 1.5,
) -> float:
    """モーラ内のピッチ分散（有声フレームのみ）から安定度をスコア化する。"""
    pitch      = np.array(pitch_user_raw, dtype=float)
    boundaries = list(mora_values) + [len(pitch)]
    variances  = []

    for i in range(min(n_mora, len(mora_values))):
        start  = max(0, boundaries[i])
        end    = min(len(pitch), boundaries[i + 1])
        if end <= start:
            continue
        voiced = pitch[start:end]
        voiced = voiced[~np.isnan(voiced)]
        if len(voiced) < 3:
            continue
        variances.append(float(np.var(voiced)))

    if not variances:
        return max_score / 2.0

    mean_var = float(np.mean(variances))
    return round(max(0.0, max_score * (1.0 - mean_var / variance_threshold)), 1)


def _worst_unstable_mora(
    pitch_user_raw: np.ndarray,
    mora_values: list[int],
    n_mora: int,
    variance_threshold: float = 1.5,
) -> tuple[int, float] | None:
    pitch      = np.array(pitch_user_raw, dtype=float)
    boundaries = list(mora_values) + [len(pitch)]
    mora_vars  = []

    for i in range(min(n_mora, len(mora_values))):
        start  = max(0, boundaries[i])
        end    = min(len(pitch), boundaries[i + 1])
        if end <= start:
            mora_vars.append((i + 1, 0.0))
            continue
        voiced = pitch[start:end]
        voiced = voiced[~np.isnan(voiced)]
        var    = float(np.var(voiced)) if len(voiced) >= 3 else 0.0
        mora_vars.append((i + 1, var))

    if not mora_vars:
        return None
    worst_idx, worst_var = max(mora_vars, key=lambda x: x[1])
    return (worst_idx, worst_var) if worst_var >= variance_threshold else None


# ── 有声フレーム比率フィードバック ───────────────────────────────────

def _voiced_ratio_feedback(
    pitch_user_raw: np.ndarray,
    mora_values: list[int],
    n_mora: int,
    threshold: float = 0.45,
) -> str | None:
    pitch      = np.array(pitch_user_raw, dtype=float)
    boundaries = list(mora_values) + [len(pitch)]
    low_voiced = []

    for i in range(min(n_mora, len(mora_values))):
        start = max(0, boundaries[i])
        end   = min(len(pitch), boundaries[i + 1])
        if end <= start:
            continue
        chunk = pitch[start:end]
        ratio = float(np.sum(~np.isnan(chunk))) / len(chunk)
        if ratio < threshold:
            low_voiced.append((i + 1, ratio))

    if not low_voiced:
        return None
    worst_mora, worst_ratio = min(low_voiced, key=lambda x: x[1])
    return (
        f"{worst_mora}拍目の声が弱すぎます"
        f"（発声フレーム {worst_ratio * 100:.0f}%）。"
        f"はっきりと声を出して発音してください。"
    )


# ── 発話速度評価 ──────────────────────────────────────────────────────

def calc_speaking_rate(
    lab_list: list,
    mora_list: list,
    native_rate: float | None = None,
) -> tuple[float, str | None]:
    """発話速度（モーラ/秒）を計算し、フィードバックを生成する。"""
    if not lab_list or not mora_list:
        return 0.0, None
    duration = float(lab_list[-1][1]) - float(lab_list[0][0])
    if duration <= 0:
        return 0.0, None

    rate = len(mora_list) / duration

    if native_rate is not None and native_rate > 0:
        ratio = rate / native_rate
        if ratio < 0.70:
            feedback = f"発話速度がサンプルより遅すぎます（{rate:.1f}モーラ/秒）。もう少し速く発音してください。"
        elif ratio < 0.85:
            feedback = f"発話速度がサンプルより少し遅めです（{rate:.1f}モーラ/秒）。"
        elif ratio <= 1.15:
            feedback = None
        elif ratio <= 1.30:
            feedback = f"発話速度がサンプルより少し速めです（{rate:.1f}モーラ/秒）。"
        else:
            feedback = f"発話速度がサンプルより速すぎます（{rate:.1f}モーラ/秒）。もう少しゆっくり発音してください。"
    else:
        if rate < 4.0:
            feedback = f"発話速度が遅すぎます（{rate:.1f}モーラ/秒）。"
        elif rate < 5.5:
            feedback = f"発話速度が少し遅めです（{rate:.1f}モーラ/秒）。"
        elif rate <= 9.0:
            feedback = None
        elif rate <= 11.0:
            feedback = f"発話速度が少し速めです（{rate:.1f}モーラ/秒）。"
        else:
            feedback = f"発話速度が速すぎます（{rate:.1f}モーラ/秒）。もう少しゆっくり発音してください。"

    return round(rate, 2), feedback


# ── アクセントスコア算出 ──────────────────────────────────────────────

def calc_accent_score(
    pitch_fin2: list[float] | np.ndarray,
    mora_values: list[int],
    accent: int | None,
    n_mora: int,
    pitch_fin: list[float] | np.ndarray | None = None,
    pitch_user_raw: np.ndarray | None = None,
    pitch_native_raw: np.ndarray | None = None,
) -> tuple[float, str, list[str], int | None]:
    """
    ピッチ曲線がアクセント型のパターンに合っているかをスコア化する。

    【引数の使い分け】
    pitch_fin2      : comp/smooth済みの録音ピッチ。H/L分類・核検出に使用。
    pitch_user_raw  : NaN保持の録音半音ピッチ。安定度・有声率・Pearson相関に使用。
    pitch_native_raw: NaN保持のネイティブ半音ピッチ。Pearson相関に使用（新規）。
    pitch_fin       : comp/smooth済みのネイティブピッチ（後方互換、Pearsonには使わない）。

    【Pearson相関の優先順位】
    1. pitch_native_raw + pitch_user_raw（推奨）: 有声フレームのみで計算
    2. pitch_fin + pitch_fin2（後方互換）: comp済み配列で計算（変更前の動作）
    """
    if accent is None or n_mora == 0:
        return 30.0, "アクセント型が不明のため評価できません。", [], None

    pitch = np.array(pitch_fin2, dtype=float)
    if len(pitch) == 0 or np.all(np.isnan(pitch)):
        return 0.0, "ピッチが検出できませんでした。マイクに近づいて発音してください。", [], None

    # H/L 分類用：NaN を補間
    nan_mask = np.isnan(pitch)
    if np.any(~nan_mask):
        xs    = np.arange(len(pitch))
        pitch = np.interp(xs, xs[~nan_mask], pitch[~nan_mask])

    pattern = expected_accent_pattern(accent, n_mora)
    if not pattern:
        return 30.0, "パターンを生成できませんでした。", pattern, None

    mora_pitches = []
    boundaries   = list(mora_values) + [len(pitch)]
    for i in range(min(n_mora, len(mora_values))):
        s = max(0, boundaries[i])
        e = min(len(pitch), boundaries[i + 1])
        mora_pitches.append(float(np.nanmean(pitch[s:e])) if s < e else np.nan)

    if not mora_pitches or all(np.isnan(p) for p in mora_pitches):
        return 0.0, "各モーラのピッチを計算できませんでした。", pattern, None

    valid_pitches = [p for p in mora_pitches if not np.isnan(p)]
    if not valid_pitches:
        return 0.0, "有効なピッチ値がありませんでした。", pattern, None

    # ── 基準ピッチからモーラ平均・H/Lパターン・核位置を算出 ────────
    # MeCabアクセントより参照録音のピッチを優先する。
    # お手本と異なるアクセント型がDBに入っていても正しく評価できるようにするため。
    use_ref_pattern = False
    ref_nucleus     = accent          # フォールバック：MeCab
    cmp_pattern     = list(pattern)   # フォールバック：MeCab由来H/L

    if pitch_fin is not None and len(pitch_fin) > 0:
        p_ref  = np.array(pitch_fin, dtype=float)
        nm_ref = np.isnan(p_ref)
        if np.any(~nm_ref):
            xs_r  = np.arange(len(p_ref))
            p_ref = np.interp(xs_r, xs_r[~nm_ref], p_ref[~nm_ref])
        b_ref  = list(mora_values) + [len(p_ref)]
        ref_mp = []
        for i in range(min(n_mora, len(mora_values))):
            s = max(0, b_ref[i])
            e = min(len(p_ref), b_ref[i + 1])
            ref_mp.append(float(np.nanmean(p_ref[s:e])) if s < e else np.nan)

        valid_ref = [p for p in ref_mp if not np.isnan(p)]
        if len(valid_ref) >= 2:
            use_ref_pattern = True
            ref_nucleus     = detect_accent_nucleus(ref_mp)
            # H/L分類しきい値：MeCabのL比率を流用して両者を同じ基準で分類
            l_ratio_ref = pattern.count("L") / len(pattern)
            thr_ref     = float(np.percentile(valid_ref, l_ratio_ref * 100))
            cmp_pattern = [
                ("H" if not np.isnan(p) and p > thr_ref else "L")
                for p in ref_mp
            ]

    # ── 【指標1】H/L パターン一致率（ユーザー vs 基準） ────────────
    l_ratio   = cmp_pattern.count("L") / len(cmp_pattern)
    threshold = float(np.percentile(valid_pitches, l_ratio * 100))

    actual_pattern = []
    for p in mora_pitches:
        if np.isnan(p):
            actual_pattern.append("?")
        elif p > threshold:
            actual_pattern.append("H")
        else:
            actual_pattern.append("L")

    match_count = sum(1 for a, e in zip(actual_pattern, cmp_pattern) if a == e)
    total_      = min(len(actual_pattern), len(cmp_pattern))
    match_rate  = match_count / total_ if total_ > 0 else 0.0
    hl_score    = match_rate * 60.0

    # ── 【指標2】アクセント核位置スコア（ユーザー vs 基準核） ────────
    detected   = detect_accent_nucleus(mora_pitches)
    nucleus_sc = _nucleus_score(detected, ref_nucleus, n_mora, max_score=60.0)

    # ── 【指標3】ピッチ相関スコア（有声フレームのみ） ────────────────
    # pitch_native_raw + pitch_user_raw が利用可能な場合は有声フレームのみで計算。
    # 利用不可の場合は pitch_fin + pitch_fin2 で後方互換動作。
    corr_sc = None
    if pitch_native_raw is not None and pitch_user_raw is not None:
        corr_sc = _pitch_correlation_score(
            np.array(pitch_native_raw, dtype=float),
            np.array(pitch_user_raw,   dtype=float),
            max_score=60.0,
        )
    elif pitch_fin is not None and len(pitch_fin) > 0:
        # 後方互換（comp済み配列を使用）
        corr_sc = _pitch_correlation_score(
            np.array(pitch_fin,  dtype=float),
            np.array(pitch_fin2, dtype=float),
            max_score=60.0,
        )

    # ── 【指標4】モーラ内ピッチ安定度 ───────────────────────────────
    stability_sc = None
    if pitch_user_raw is not None and len(pitch_user_raw) > 0:
        stability_sc = _mora_stability_score(
            pitch_user_raw, mora_values, n_mora,
            max_score=60.0, variance_threshold=2.0,
        )

    # 加重平均（相関より H/L パターン重視に調整）
    if corr_sc is not None and stability_sc is not None:
        raw = (nucleus_sc   * 0.40
               + corr_sc   * 0.28
               + hl_score  * 0.22
               + stability_sc * 0.10)
    elif corr_sc is not None:
        raw = nucleus_sc * 0.45 + corr_sc * 0.40 + hl_score * 0.15
    elif stability_sc is not None:
        raw = nucleus_sc * 0.50 + hl_score * 0.35 + stability_sc * 0.15
    else:
        raw = nucleus_sc * 0.60 + hl_score * 0.40

    final_score = round(min(60.0, max(0.0, raw)), 1)

    # フィードバック生成
    nucleus_diff = abs(detected - ref_nucleus)
    unstable = None
    if pitch_user_raw is not None:
        unstable = _worst_unstable_mora(
            pitch_user_raw, mora_values, n_mora, variance_threshold=2.0
        )

    if final_score >= 48:
        feedback = f"アクセント（{_accent_label(accent)}）が正確に発音できています！"
    elif unstable is not None and stability_sc is not None and stability_sc < 30:
        mora_idx, _ = unstable
        feedback = (
            f"{mora_idx}拍目のピッチが不安定です。"
            f"各モーラを一定のピッチで安定して発音してください。"
        )
    elif nucleus_diff == 0 and match_rate >= 0.6:
        feedback = "アクセント核の位置は正しいです。ピッチの上げ下げをより明確にするとさらに良くなります。"
    elif nucleus_diff > 0:
        if use_ref_pattern:
            # 基準録音と比較したフィードバック
            if detected == 0 and ref_nucleus > 0:
                feedback = (
                    f"ピッチの下降が弱すぎます。"
                    f"お手本では{ref_nucleus}拍目の後に下げています。"
                )
            elif detected < ref_nucleus:
                feedback = (
                    f"ピッチの下降が早すぎます（{detected}拍目で下降）。"
                    f"お手本に合わせて{ref_nucleus}拍目まで高く保ってください。"
                )
            else:
                feedback = (
                    f"ピッチの下降が遅すぎます（{detected}拍目で下降）。"
                    f"お手本に合わせて{ref_nucleus}拍目の後で下げてください。"
                )
        else:
            # MeCabアクセントと比較したフィードバック（フォールバック）
            if detected == 0:
                feedback = (
                    f"ピッチの下降が弱すぎます。"
                    f"{_accent_label(accent)}では{accent}拍目の後にしっかり下げてください。"
                )
            elif detected < accent:
                feedback = (
                    f"ピッチの下降が早すぎます（{detected}拍目で下降）。"
                    f"{_accent_label(accent)}：{accent}拍目まで高く保ってください。"
                )
            else:
                feedback = (
                    f"ピッチの下降が遅すぎます（{detected}拍目で下降）。"
                    f"{_accent_label(accent)}：{accent}拍目の後で下げてください。"
                )
    else:
        wrong = [
            i + 1 for i, (a, e) in enumerate(zip(actual_pattern, cmp_pattern))
            if a != e and a != "?"
        ]
        if wrong:
            ref_label = "お手本" if use_ref_pattern else _accent_label(accent)
            feedback  = (
                f"{wrong[0]}拍目の高低が逆転しています。"
                f"{ref_label}のパターン（{' '.join(cmp_pattern)}）を意識してください。"
            )
        else:
            feedback = f"アクセント（{_accent_label(accent)}）をもう少し意識して発音してください。"

    return final_score, feedback, pattern, detected


# ── 長さスコア算出 ────────────────────────────────────────────────────

def _mora_weight(label: str) -> float:
    if ":" in label: return 2.0
    if label == "q": return 2.0
    if label == "N": return 1.5
    return 1.0


def calc_length_score(
    native_mora_length: list[float],
    user_mora_length: list[float],
    mora_labels: list[str] | None = None,
) -> tuple[float, str]:
    if not native_mora_length or not user_mora_length:
        return 20.0, "長さの評価ができませんでした。"

    n = min(len(native_mora_length), len(user_mora_length))
    if n == 0:
        return 20.0, "長さの評価ができませんでした。"

    native  = np.array(native_mora_length[:n], dtype=float)
    user    = np.array(user_mora_length[:n],   dtype=float)
    diffs   = np.abs(native - user)
    weights = (
        np.array([_mora_weight(mora_labels[i]) for i in range(n)])
        if mora_labels is not None and len(mora_labels) >= n
        else np.ones(n)
    )

    total_weight  = float(np.sum(weights))
    weighted_diff = float(np.sum(diffs * weights) / total_weight) if total_weight > 0 else 0.0
    score         = max(0.0, round((1.0 - weighted_diff / 25.0) * 40, 1))

    worst_idx  = int(np.argmax(diffs * weights))
    worst_diff = float(diffs[worst_idx])

    # ── 絶対時間チェック：長音・促音の比率を通常モーラと比較 ──────────
    # 通常モーラ（長音・促音・撥音以外）の平均長さを基準にする
    special_labels = {"q", "N"}
    normal_indices = [
        i for i in range(n)
        if mora_labels is not None
        and ":" not in mora_labels[i]
        and mora_labels[i] not in special_labels
    ]
    normal_user_avg = float(np.mean(user[[i for i in normal_indices]])) if normal_indices else 0.0

    abs_long_errors: list[tuple[int, str]] = []   # (mora_idx, "短い"|"長い")
    abs_soku_errors: list[tuple[int, str]] = []

    if normal_user_avg > 0 and mora_labels is not None:
        for i in range(n):
            label = mora_labels[i]
            ratio = user[i] / normal_user_avg

            if ":" in label:
                # 長音は通常モーラの 1.5〜3.0 倍が目安
                if ratio < 1.5:
                    abs_long_errors.append((i, "短い"))
                elif ratio > 3.2:
                    abs_long_errors.append((i, "長い"))

            elif label == "q":
                # 促音（っ）は通常モーラの 0.6〜1.8 倍が目安
                if ratio < 0.6:
                    abs_soku_errors.append((i, "短い"))
                elif ratio > 1.8:
                    abs_soku_errors.append((i, "長い"))

    long_errors = [i for i in range(n) if mora_labels is not None and ":" in mora_labels[i] and diffs[i] > 4.0]
    soku_errors = [i for i in range(n) if mora_labels is not None and mora_labels[i] == "q" and diffs[i] > 4.0]

    # 絶対時間エラーを優先してフィードバック
    if abs_long_errors:
        idx, kind = abs_long_errors[0]
        if kind == "短い":
            feedback = (
                f"{idx + 1}拍目の長音が短すぎます。"
                f"前後の音の約2倍の長さを意識してしっかり伸ばしてください。"
            )
        else:
            feedback = (
                f"{idx + 1}拍目の長音が長すぎます。"
                f"前後の音の約2倍程度を目安にしてください。"
            )
    elif abs_soku_errors:
        idx, kind = abs_soku_errors[0]
        if kind == "短い":
            feedback = (
                f"{idx + 1}拍目の促音（っ）が短すぎます。"
                f"前後の音と同じくらいの間（ま）を意識して詰めてください。"
            )
        else:
            feedback = (
                f"{idx + 1}拍目の促音（っ）が長すぎます。"
                f"詰まりを短くしてください。"
            )
    elif long_errors:
        idx = long_errors[0]
        d   = "短すぎます。しっかり伸ばして" if user[idx] < native[idx] else "長すぎます。少し短く"
        feedback = f"{idx + 1}拍目の長音が{d}発音してください。"
    elif soku_errors:
        idx = soku_errors[0]
        d   = "短すぎます。一拍分しっかり詰めて" if user[idx] < native[idx] else "長すぎます。詰まりを短く"
        feedback = f"{idx + 1}拍目の促音（っ）が{d}発音してください。"
    elif weighted_diff < 5:
        feedback = "各モーラの長さのバランスがとても良いです！"
    elif weighted_diff < 10:
        mora_num = worst_idx + 1
        d = "少し長すぎます。テンポよく" if user[worst_idx] > native[worst_idx] else "少し短すぎます。もう少し伸ばして"
        feedback = f"{mora_num}拍目が{d}発音してみてください。"
    else:
        mora_num = worst_idx + 1
        feedback = (
            f"{mora_num}拍目が長すぎます（サンプルより {worst_diff:.0f}%多い）。"
            if user[worst_idx] > native[worst_idx] else
            f"{mora_num}拍目が短すぎます（サンプルより {worst_diff:.0f}%少ない）。"
        )

    return score, feedback


# ── 総合スコア ────────────────────────────────────────────────────────

def calc_total_score(
    pitch_fin2: list[float] | np.ndarray,
    mora_values: list[int],
    accent: int | None,
    n_mora: int,
    native_mora_length: list[float],
    user_mora_length: list[float],
    mora_labels: list[str] | None = None,
    pitch_fin: list[float] | np.ndarray | None = None,
    pitch_user_raw: np.ndarray | None = None,
    pitch_native_raw: np.ndarray | None = None,
    vowel_score: float | None = None,
    vowel_feedback: str | None = None,
) -> dict:
    """
    アクセント・長さ・母音品質スコアを合算して総合スコアを返す。

    Parameters
    ----------
    pitch_fin2        : comp/smooth済みの録音ピッチ（H/L分類・核検出用）
    pitch_fin         : comp/smooth済みのネイティブピッチ（後方互換）
    pitch_user_raw    : NaN保持の録音半音ピッチ（安定度・有声率・Pearson用）
    pitch_native_raw  : NaN保持のネイティブ半音ピッチ（Pearson用・新規）
    vowel_score       : core/formant.py の calc_vowel_score() 結果
    vowel_feedback    : 母音品質フィードバック
    """
    accent_raw, accent_feedback, pattern, detected_nucleus = calc_accent_score(
        pitch_fin2=pitch_fin2, mora_values=mora_values,
        accent=accent, n_mora=n_mora,
        pitch_fin=pitch_fin,
        pitch_user_raw=pitch_user_raw,
        pitch_native_raw=pitch_native_raw,
    )
    length_raw, length_feedback = calc_length_score(
        native_mora_length=native_mora_length,
        user_mora_length=user_mora_length,
        mora_labels=mora_labels,
    )

    voiced_feedback = None
    if pitch_user_raw is not None:
        voiced_feedback = _voiced_ratio_feedback(pitch_user_raw, mora_values, n_mora)

    accent_score = round(min(50.0, accent_raw * 50 / 60 + 3.0), 1)
    length_score = round(length_raw * 30 / 40, 1)
    v_score      = round(vowel_score, 1) if vowel_score is not None else 10.0

    total = round(min(100.0, max(0.0, accent_score + length_score + v_score)), 1)

    if total >= 90:   grade = "S"
    elif total >= 75: grade = "A"
    elif total >= 60: grade = "B"
    elif total >= 40: grade = "C"
    else:             grade = "D"

    return {
        "total":              total,
        "accent_score":       accent_score,
        "length_score":       length_score,
        "vowel_score":        v_score,
        "accent_feedback":    accent_feedback,
        "length_feedback":    length_feedback,
        "vowel_feedback":     vowel_feedback or "母音の評価データがありませんでした。",
        "voiced_feedback":    voiced_feedback,
        "accent_pattern":     pattern,
        "accent_label":       _accent_label(accent),
        "grade":              grade,
        "detected_nucleus":   detected_nucleus,
        "alignment_failed":   False,
        "alignment_feedback": None,
    }
# モーラ別スコア（④ モーラ別スコアの表示）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_mora_scores(
    pitch_fin:            list[float],
    pitch_fin2:           list[float],
    mora_values:          list[int],
    native_mora_length:   list[float],
    user_mora_length:     list[float],
    mora_labels:          list[str],
    native_formants:      list[dict] | None = None,
    user_formants:        list[dict] | None = None,
) -> list[dict]:
    """
    モーラ単位のスコアを計算して返す。

    各モーラについて以下の3軸を 0〜100 点で評価する：

      accent : pitch_fin / pitch_fin2 の当該モーラ区間での平均誤差
               score = 100 × exp(−4 × mean_abs_diff)

      length : ネイティブ/ユーザーのモーラ長比率からのズレ
               score = 100 × exp(−3 × |1 − ratio| × weight)
               長音・促音は weight=2、撥音は weight=1.5

      vowel  : フォルマントデータがあるモーラのみ計算
               Bark スケール距離で 0〜100 に変換

      total  : 3軸の単純平均（None の軸は除外）

    また、worst_mora（最もスコアが低いモーラ）の
    フレーム範囲を返す（⑤ ピッチグラフハイライト用）。

    Returns
    -------
    list[dict]  各モーラの { label, accent, length, vowel, total }
    """
    n_mora = min(len(mora_labels), len(mora_values))
    p1 = np.array(pitch_fin,  dtype=float)
    p2 = np.array(pitch_fin2, dtype=float)

    # 0-1 正規化（ネイティブとユーザーの絶対ピッチ差を除去してから形状を比較）
    def _scale01(arr: np.ndarray) -> np.ndarray:
        mn, mx = float(np.nanmin(arr)), float(np.nanmax(arr))
        if mx == mn:
            return np.zeros_like(arr)
        return (arr - mn) / (mx - mn)

    p1_sc = _scale01(p1)
    p2_sc = _scale01(p2)

    VOWELS_SET = {'a', 'i', 'u', 'e', 'o'}

    def _has_vowel(label: str) -> bool:
        return any(v in label for v in VOWELS_SET)

    def _hz_to_bark(f: float) -> float:
        if f <= 0:
            return 0.0
        return 26.81 * f / (1960.0 + f) - 0.53

    results: list[dict] = []

    for i in range(n_mora):
        label = mora_labels[i]
        start = mora_values[i]
        end   = mora_values[i + 1] if i + 1 < len(mora_values) else len(p1)

        # ── アクセントスコア ─────────────────────────────────────────
        # 0-1 正規化済み配列で比較することでネイティブとユーザーの声域差を除去する
        s, e = max(0, start), min(len(p1_sc), end)
        if s < e:
            diff   = np.nanmean(np.abs(p1_sc[s:e] - p2_sc[s:e]))
            acc_sc = round(100.0 * float(np.exp(-4.0 * float(diff))), 1)
        else:
            acc_sc = None

        # ── 長さスコア ───────────────────────────────────────────────
        nat_len = float(native_mora_length[i]) if i < len(native_mora_length) else 0.0
        usr_len = float(user_mora_length[i])   if i < len(user_mora_length)   else 0.0
        if nat_len > 0:
            ratio  = usr_len / nat_len
            # 特殊モーラは重みを大きくする
            if ":" in label or label == "q":
                weight = 2.0
            elif label == "N":
                weight = 1.5
            else:
                weight = 1.0
            err    = abs(1.0 - ratio) * weight
            len_sc = round(100.0 * float(np.exp(-3.0 * err)), 1)
        else:
            len_sc = None

        # ── 母音スコア ───────────────────────────────────────────────
        vow_sc = None
        if (
            _has_vowel(label)
            and native_formants is not None
            and user_formants   is not None
            and i < len(native_formants)
            and i < len(user_formants)
        ):
            nf = native_formants[i]
            uf = user_formants[i]
            nf1, nf2 = nf.get("f1"), nf.get("f2")
            uf1, uf2 = uf.get("f1"), uf.get("f2")
            if nf1 and nf2 and uf1 and uf2:
                nb1 = _hz_to_bark(nf1); nb2 = _hz_to_bark(nf2)
                ub1 = _hz_to_bark(uf1); ub2 = _hz_to_bark(uf2)
                dist   = float(np.sqrt(((nb1 - ub1) / 3.0) ** 2 + ((nb2 - ub2) / 4.0) ** 2))
                vow_sc = round(100.0 * float(np.exp(-1.2 * dist)), 1)

        # ── 総合 ─────────────────────────────────────────────────────
        components = [x for x in [acc_sc, len_sc, vow_sc] if x is not None]
        total_sc   = round(float(np.mean(components)), 1) if components else None

        results.append({
            "label":       label,
            "mora_index":  i,
            "frame_start": int(start),
            "frame_end":   int(end),
            "accent":      acc_sc,
            "length":      len_sc,
            "vowel":       vow_sc,
            "total":       total_sc,
        })

    return results