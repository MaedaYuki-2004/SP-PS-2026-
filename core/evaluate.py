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

  ピッチ相関は、お手本と録音の「両方が有声（NaN でない）」フレームだけで計算する。
  無声区間（子音・無音）を補間した値は実際のピッチではないため。

【2026-09-14 評価ロジックの見直し】
  実際の録音（練習履歴26回・過去のお手本52語）で途中の値を確かめ、次の問題を直した。
  1. アクセント型が不明（MeCab で取れない）の単語は、発音に関係なくアクセント点が
     28.0 点に固定されていた。お手本の録音から高低の形を作って評価する。
  2. アクセント核の下降しきい値 max(0.05, min(0.25, 範囲×0.15)) は 0〜1 に正規化した
     ピッチ用の値だったが、半音の配列に使われていた（0.25 半音で頭打ち）。
     → NUCLEUS_DROP_ST（1 半音）
  3. モーラの高さを、無声区間を補間した値の平均で出していたため、「っ」のように
     声の出ていないモーラも補間値で H/L を判定していた。
     → 有声フレームの中央値。測れないモーラは比較から外す。
  4. comp() は全区間が無声の配列を 0 で埋めるので、声がほとんど出ていない録音でも
     「ピッチが検出できません」にならず、平板型なら核位置が満点になっていた。
  5. 平坦ゲート（抑揚 1.5 半音未満で上限 25）がお手本の抑揚の大きさを見ていなかった。
  6. 安定度をモーラ内の分散そのもので見ていたため、お手本どおりにモーラの中で
     上げ下げしても「不安定」になった。→ お手本より揺れた分だけを見る。
  7. 長さの点を割合の差（パーセントポイント）で出していたため、同じ比率のずれでも
     モーラ数の少ない単語ほど点が低かった。→ 1モーラあたりの平均の割合で割る
     （4モーラの単語では以前と同じ点になる）。
  8. 長音・促音のチェックが固定の比率だったため、お手本を自分自身と比べても
     「っが短すぎます」と出た。→ お手本の比率を基準にする。
  9. 「こう」「せい」の「う」「い」を独立したモーラとして長さを比べていた。
     → core/utils.long_vowel_groups でまとめる。
  10. モーラ別の「高さ」を 0〜1 の min-max 正規化で比べていたため、抑揚のない声が
      拡大されて高く出たり、1フレームの外れ値で全体が潰れたりしていた。
  11. モーラ別の「口の形」に話者差補正がかかっていなかった（core/formant.py）。
  12. 「っ」のようにお手本でも声の出ないモーラに「声が弱すぎます」と出ていた。
"""
from __future__ import annotations

import numpy as np
from scipy.stats import pearsonr

from core.formant import vowel_distance
from core.utils import long_vowel_groups, mora_group_kana, romaji_mora_to_kana


# ── 定数 ──────────────────────────────────────────────────────────────

# アクセント核とみなす、隣り合うモーラ間の下がり幅（半音）。
# 設計上の値（知覚実験や正解データで最適化したものではない）。平板型のお手本「がっこう」で
# 測った語中の自然な下がり（約 0.3 半音）を核と数えず、はっきりした下降だけを拾うために置いている。
NUCLEUS_DROP_ST = 1.0

# モーラの高さを「測れた」とみなす条件（有声フレーム数と、モーラ内に占める割合）
MIN_VOICED_FRAMES = 3
MIN_VOICED_RATIO  = 0.3

# 録音全体でこれより有声フレームが少なければ「声の高さを測れなかった」とする（10ms × 10）
MIN_VOICED_TOTAL = 10

# モーラ別の「高さ」で抑揚の幅をそろえるときの下限（半音）。
# これより抑揚の小さい声は拡大しない（平坦な声のわずかな揺れを大きな動きに見せないため）。
PITCH_SPREAD_FLOOR_ST = 3.0

_NO_PITCH_FEEDBACK = (
    "声の高さ（ピッチ）を測れませんでした。"
    "マイクに近づいて、はっきり声を出して発音してください。"
)


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


# ── 共通ヘルパー ──────────────────────────────────────────────────────

def _voiced(values) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return arr[~np.isnan(arr)]


def _pitch_spread(values) -> float | None:
    """有声フレームの P90 − P10（半音）。有声フレームが少なければ None。"""
    v = _voiced(values)
    if len(v) < MIN_VOICED_TOTAL:
        return None
    return float(np.percentile(v, 90) - np.percentile(v, 10))


def _mora_bounds(mora_values: list[int], n_mora: int, length: int) -> list[tuple[int, int]]:
    """各モーラの [開始, 終了) フレーム。配列の長さで切り詰める。"""
    boundaries = list(mora_values) + [length]
    return [
        (max(0, int(boundaries[i])), min(length, int(boundaries[i + 1])))
        for i in range(min(n_mora, len(mora_values)))
    ]


def _mora_pitch_medians(pitch_raw, mora_values: list[int], n_mora: int) -> list[float]:
    """各モーラの高さ（有声フレームの中央値）。声の出ていないモーラは NaN。"""
    arr = np.asarray(pitch_raw, dtype=float)
    result = []
    for s, e in _mora_bounds(mora_values, n_mora, len(arr)):
        seg = arr[s:e]
        v   = seg[~np.isnan(seg)]
        if len(seg) and len(v) >= MIN_VOICED_FRAMES and len(v) >= MIN_VOICED_RATIO * len(seg):
            result.append(float(np.median(v)))
        else:
            result.append(np.nan)
    return result


def _mora_pitch_means(pitch, mora_values: list[int], n_mora: int) -> list[float]:
    """NaN 保持の配列を渡さない呼び出し元向け：補間済み配列のモーラ平均。"""
    arr  = np.asarray(pitch, dtype=float)
    mask = np.isnan(arr)
    if np.any(mask) and np.any(~mask):
        xs  = np.arange(len(arr))
        arr = np.interp(xs, xs[~mask], arr[~mask])
    return [
        float(np.mean(arr[s:e])) if s < e else np.nan
        for s, e in _mora_bounds(mora_values, n_mora, len(arr))
    ]


def _unit_names(kana: list[str]) -> list[str]:
    """フィードバック用の呼び名（「こ」など）。同じかなが2回以上出るときは何番目かを添える。"""
    names = []
    for i, k in enumerate(kana):
        if not k or k.isascii():
            names.append(f"{i + 1}拍目")
        elif kana.count(k) > 1:
            names.append(f"{i + 1}番目の「{k}」")
        else:
            names.append(f"「{k}」")
    return names


def _mora_names(mora_labels: list[str] | None, n: int) -> list[str]:
    if mora_labels is None or len(mora_labels) < n:
        return [f"{i + 1}拍目" for i in range(n)]
    return _unit_names([romaji_mora_to_kana(str(label)) for label in mora_labels[:n]])


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


def _classify_hl(mora_pitches: list[float], l_ratio: float) -> list[str]:
    """高さの順位で H/L に分ける（低い方から l_ratio の割合を L）。測れないモーラは "?"。"""
    valid = [p for p in mora_pitches if not np.isnan(p)]
    if not valid:
        return ["?"] * len(mora_pitches)
    threshold = float(np.percentile(valid, l_ratio * 100))
    return ["?" if np.isnan(p) else ("H" if p > threshold else "L") for p in mora_pitches]


# ── アクセント核（downstep）位置の検出 ───────────────────────────────

def detect_accent_nucleus(
    mora_pitches: list[float],
    drop_threshold: float = NUCLEUS_DROP_ST,
) -> int:
    """
    モーラごとの高さ（半音）から、隣り合うモーラ間でいちばん大きく下がる位置を探し、
    アクセント核型（何モーラ目のあとで下がるか。0 = 下がらない）を返す。
    高さを測れなかったモーラ（NaN）は飛ばし、その前後を隣り合うものとして比べる。
    """
    valid = [(i, p) for i, p in enumerate(mora_pitches) if not np.isnan(p)]
    if len(valid) < 2:
        return 0

    drops = [
        (valid[k][0], valid[k][1] - valid[k + 1][1])
        for k in range(len(valid) - 1)
    ]
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

    pitch_native_raw / pitch_user_raw は comp() 適用前の NaN 保持配列を渡すこと。
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

def _mora_excess_variances(
    pitch_user_raw,
    mora_values: list[int],
    n_mora: int,
    pitch_native_raw=None,
) -> list[tuple[int, float]]:
    """
    モーラごとの (モーラ番号, お手本より揺れた分の分散) を返す。
    有声フレームが3未満のモーラは除く。お手本の配列が無ければ分散そのもの。

    モーラの中で高さを上げ下げするのが正しい発音のこともある（長音の中で
    下がる、平板型の1モーラ目から上がる、など）。お手本の分散を引くことで、
    お手本と同じ動きは「揺れ」として数えない。
    """
    user   = np.asarray(pitch_user_raw, dtype=float)
    native = np.asarray(pitch_native_raw, dtype=float) if pitch_native_raw is not None else None
    result = []
    for i, (s, e) in enumerate(_mora_bounds(mora_values, n_mora, len(user))):
        uv = user[s:e]
        uv = uv[~np.isnan(uv)]
        if len(uv) < 3:
            continue
        var = float(np.var(uv))
        if native is not None:
            nv = native[s:min(e, len(native))]
            nv = nv[~np.isnan(nv)]
            if len(nv) >= 3:
                var = max(0.0, var - float(np.var(nv)))
        result.append((i, var))
    return result


def _mora_stability_score(
    pitch_user_raw: np.ndarray,
    mora_values: list[int],
    n_mora: int,
    pitch_native_raw: np.ndarray | None = None,
    max_score: float = 60.0,
    variance_threshold: float = 1.5,
) -> float:
    """モーラ内でお手本より揺れた分（有声フレームのみ）から安定度をスコア化する。"""
    variances = [v for _, v in _mora_excess_variances(pitch_user_raw, mora_values, n_mora, pitch_native_raw)]
    if not variances:
        return max_score / 2.0
    mean_var = float(np.mean(variances))
    return round(max(0.0, max_score * (1.0 - mean_var / variance_threshold)), 1)


def _worst_unstable_mora(
    pitch_user_raw: np.ndarray,
    mora_values: list[int],
    n_mora: int,
    pitch_native_raw: np.ndarray | None = None,
    variance_threshold: float = 1.5,
) -> tuple[int, float] | None:
    """いちばん揺れたモーラの (モーラ番号（0始まり）, 分散)。しきい値未満なら None。"""
    mora_vars = _mora_excess_variances(pitch_user_raw, mora_values, n_mora, pitch_native_raw)
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
    pitch_native_raw: np.ndarray | None = None,
    mora_labels: list[str] | None = None,
) -> str | None:
    """
    声が出ていないモーラを指摘する。
    「っ」や無声化した母音（「し」「す」など）はお手本でも声が出ないので、
    お手本でも有声フレームが少ないモーラは指摘しない。
    """
    pitch      = np.array(pitch_user_raw, dtype=float)
    native     = np.array(pitch_native_raw, dtype=float) if pitch_native_raw is not None else None
    low_voiced = []

    for i, (s, e) in enumerate(_mora_bounds(mora_values, n_mora, len(pitch))):
        if e <= s:
            continue
        chunk = pitch[s:e]
        ratio = float(np.sum(~np.isnan(chunk))) / len(chunk)
        if ratio >= threshold:
            continue
        if native is not None:
            n_chunk = native[s:min(e, len(native))]
            if len(n_chunk) == 0 or float(np.sum(~np.isnan(n_chunk))) / len(n_chunk) < threshold:
                continue
        low_voiced.append((i, ratio))

    if not low_voiced:
        return None
    worst_mora, worst_ratio = min(low_voiced, key=lambda x: x[1])
    name = _mora_names(mora_labels, n_mora)[worst_mora] if worst_mora < n_mora else f"{worst_mora + 1}拍目"
    return (
        f"{name}の声が弱すぎます"
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
    mora_labels: list[str] | None = None,
) -> tuple[float, str, list[str], int | None]:
    """
    ピッチ曲線がお手本の高低の形に合っているかをスコア化する（0〜60）。

    【引数の使い分け】
    pitch_fin2      : comp/smooth済みの録音ピッチ（半音）。NaN 保持の配列が無いときの代わり。
    pitch_user_raw  : NaN保持の録音半音ピッチ。モーラの高さ・安定度・有声率・Pearson相関に使用。
    pitch_native_raw: NaN保持のお手本半音ピッチ。同上。
    pitch_fin       : comp/smooth済みのお手本ピッチ（NaN 保持の配列が無いときの代わり）。
    mora_labels     : Julius のモーララベル。フィードバックで「こ」のように音を名指しするのに使う。

    【基準の決め方】
    お手本の録音の高さの形を正解にする（MeCab のアクセント型より優先）。
    アクセント型が分かっていれば H/L の数（L の割合）にだけ使い、
    分からなければお手本から検出した核の位置で決める。
    お手本の高さが測れないときだけ、MeCab のアクセント型そのものと比べる。
    """
    if n_mora == 0:
        return 30.0, "モーラの区切りが分からないため、アクセントを評価できません。", [], None

    pitch = np.array(pitch_fin2, dtype=float)
    if len(pitch) == 0 or np.all(np.isnan(pitch)):
        return 0.0, _NO_PITCH_FEEDBACK, [], None
    # comp() は全区間が無声の配列を 0 で埋めて返すので、pitch_fin2 だけでは
    # 「声が出ていない」を見分けられない。NaN 保持の配列で有声フレームを数える。
    if pitch_user_raw is not None and len(_voiced(pitch_user_raw)) < MIN_VOICED_TOTAL:
        return 0.0, _NO_PITCH_FEEDBACK, [], None

    # ── 各モーラの高さ ─────────────────────────────────────────────
    if pitch_user_raw is not None:
        mora_pitches = _mora_pitch_medians(pitch_user_raw, mora_values, n_mora)
    else:
        mora_pitches = _mora_pitch_means(pitch, mora_values, n_mora)
    if not mora_pitches or all(np.isnan(p) for p in mora_pitches):
        return 0.0, _NO_PITCH_FEEDBACK, [], None
    n = len(mora_pitches)

    ref_mp = None
    if pitch_native_raw is not None:
        ref_mp = _mora_pitch_medians(pitch_native_raw, mora_values, n_mora)
    elif pitch_fin is not None and len(pitch_fin) > 0:
        ref_mp = _mora_pitch_means(pitch_fin, mora_values, n_mora)
    use_ref_pattern = ref_mp is not None and sum(1 for p in ref_mp if not np.isnan(p)) >= 2

    # ── 基準（お手本）の核位置と H/L ──────────────────────────────
    if use_ref_pattern:
        ref_nucleus = detect_accent_nucleus(ref_mp)
        base        = expected_accent_pattern(accent if accent is not None else ref_nucleus, n)
        l_ratio     = base.count("L") / len(base)
        cmp_pattern = _classify_hl(ref_mp, l_ratio)
    elif accent is not None:
        ref_nucleus = accent
        cmp_pattern = expected_accent_pattern(accent, n)
        l_ratio     = cmp_pattern.count("L") / len(cmp_pattern)
    else:
        return (30.0,
                "お手本の声の高さを測れず、アクセント型も分からないため、アクセントを評価できません。",
                [], None)

    display_pattern = (
        expected_accent_pattern(accent, n) if accent is not None
        else [c if c != "?" else "-" for c in cmp_pattern]
    )

    # ── 【指標1】H/L パターン一致率（両方で高さを測れたモーラだけ） ───
    actual_pattern = _classify_hl(mora_pitches, l_ratio)
    pairs = [(a, e) for a, e in zip(actual_pattern, cmp_pattern) if a != "?" and e != "?"]
    match_rate = sum(1 for a, e in pairs if a == e) / len(pairs) if pairs else 0.5
    hl_score   = match_rate * 60.0

    # ── 【指標2】アクセント核位置スコア（ユーザー vs 基準核） ────────
    detected   = detect_accent_nucleus(mora_pitches)
    nucleus_sc = _nucleus_score(detected, ref_nucleus, n, max_score=60.0)

    # ── 【指標3】ピッチ相関スコア（有声フレームのみ） ────────────────
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

    # ── 【指標4】モーラ内ピッチ安定度（お手本より揺れた分） ─────────
    stability_sc = None
    if pitch_user_raw is not None and len(pitch_user_raw) > 0:
        stability_sc = _mora_stability_score(
            pitch_user_raw, mora_values, n_mora, pitch_native_raw,
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

    # ── 平坦ピッチゲート ──────────────────────────────────────────
    # ユーザーのピッチがほぼ動いていない場合、パーセンタイル分割の
    # H/L 分類は偶然一致で得点してしまう。有声フレームのレンジ（P90 − P10）が
    # 1.5 半音未満、かつお手本の半分未満なら「平坦」と判定し、上限を設ける。
    # お手本自体の抑揚が小さい単語で、お手本どおりに言った生徒を平坦扱いしないため。
    flat_range    = _pitch_spread(pitch_user_raw) if pitch_user_raw is not None else None
    native_spread = _pitch_spread(pitch_native_raw) if pitch_native_raw is not None else None
    flat_limit    = 1.5 if native_spread is None else min(1.5, 0.5 * native_spread)
    is_flat = flat_range is not None and flat_range < flat_limit
    if is_flat:
        raw = min(raw, 25.0)

    final_score = round(min(60.0, max(0.0, raw)), 1)

    # ── フィードバック生成 ────────────────────────────────────────
    names = _mora_names(mora_labels, n)

    def _after(k: int) -> str:
        """「k モーラ目のあと」の k モーラ目の呼び名。"""
        return names[k - 1] if 1 <= k <= n else f"{k}拍目"

    nucleus_diff = abs(detected - ref_nucleus)
    where        = "お手本では" if use_ref_pattern else f"{_accent_label(accent)}では"
    unstable = None
    if pitch_user_raw is not None:
        unstable = _worst_unstable_mora(
            pitch_user_raw, mora_values, n_mora, pitch_native_raw, variance_threshold=2.0
        )

    if is_flat:
        feedback = (
            f"声の高さがほぼ一定です（変化 {flat_range:.1f}半音）。"
            f"ピッチ比較グラフを見ながら、高い拍と低い拍の差を"
            f"大げさにつける練習から始めましょう。"
        )
    elif final_score >= 48:
        feedback = (
            "アクセントがお手本どおりに発音できています！" if accent is None
            else f"アクセント（{_accent_label(accent)}）が正確に発音できています！"
        )
    elif unstable is not None and stability_sc is not None and stability_sc < 30:
        mora_idx, _ = unstable
        feedback = (
            f"{names[mora_idx]}のピッチが不安定です。"
            f"各モーラを一定のピッチで安定して発音してください。"
        )
    elif nucleus_diff == 0 and match_rate >= 0.6:
        feedback = "アクセント核の位置は正しいです。ピッチの上げ下げをより明確にするとさらに良くなります。"
    elif nucleus_diff > 0:
        if ref_nucleus == 0:
            feedback = (
                f"{_after(detected)}のあとで高さが下がっています。"
                f"{where}途中で下げずに、最後まで高さを保ちます。"
            )
        elif detected == 0:
            feedback = (
                f"ピッチの下降が弱すぎます。"
                f"{where}{_after(ref_nucleus)}のあとでしっかり下げています。"
            )
        elif detected < ref_nucleus:
            feedback = (
                f"ピッチの下降が早すぎます（{_after(detected)}のあとで下がっています）。"
                f"{where}{_after(ref_nucleus)}まで高く保ちます。"
            )
        else:
            feedback = (
                f"ピッチの下降が遅すぎます（{_after(detected)}のあとで下がっています）。"
                f"{where}{_after(ref_nucleus)}のあとで下げます。"
            )
    else:
        wrong = [
            i for i, (a, e) in enumerate(zip(actual_pattern, cmp_pattern))
            if a != e and a != "?" and e != "?"
        ]
        if wrong:
            ref_label = "お手本" if use_ref_pattern else _accent_label(accent)
            shown     = " ".join(c if c != "?" else "・" for c in cmp_pattern)
            feedback  = (
                f"{names[wrong[0]]}の高低が逆転しています。"
                f"{ref_label}のパターン（{shown}）を意識してください。"
            )
        elif accent is None:
            feedback = "お手本の高低の形をもう少し意識して発音してください。"
        else:
            feedback = f"アクセント（{_accent_label(accent)}）をもう少し意識して発音してください。"

    return final_score, feedback, display_pattern, detected


# ── 長さスコア算出 ────────────────────────────────────────────────────

# 長音・促音は聞き取りへの影響が大きいので重く見る
_KIND_WEIGHT = {"long": 2.0, "soku": 2.0, "hatsu": 1.5, "normal": 1.0}

# 長音・促音の「通常モーラに対する長さの比」を、お手本の比の何倍まで許すか。
# 以前の固定の目安（長音 1.5〜3.2 倍 ≒ 2倍の 0.75〜1.6 倍、促音 0.6〜1.8 倍 ≒ 1倍の
# 0.6〜1.8 倍）の幅を、お手本の比を中心にして使う。
_SPECIAL_TOLERANCE = {"long": (0.75, 1.6), "soku": (0.6, 1.8)}


def _group_kind(labels: list[str] | None, group: list[int]) -> str:
    if labels is None:
        return "normal"
    head = labels[group[0]]
    if len(group) > 1 or ":" in head:
        return "long"
    if head == "q":
        return "soku"
    if head == "N":
        return "hatsu"
    return "normal"


def calc_length_score(
    native_mora_length: list[float],
    user_mora_length: list[float],
    mora_labels: list[str] | None = None,
) -> tuple[float, str]:
    """
    各モーラの長さの割合（発話全体に占める %）をお手本と比べて、長さスコア（0〜40）を返す。

    「こう」「せい」の後半の「う」「い」は前のモーラとまとめて1つの長音として比べる
    （core/utils.long_vowel_groups）。

    点数は、重み付きの割合の差を「1まとまりあたりの平均の割合」で割った値
    （＝平均的な長さに対して何割ずれたか）から出す。
    割合の差をそのまま使うと、同じ比率のずれでもまとまりの数が少ない単語ほど差が大きくなる。
    4まとまりの単語では以前の式（差 ÷ 25）と同じ点になる。
    """
    if not native_mora_length or not user_mora_length:
        return 20.0, "長さの評価ができませんでした。"

    n_all = min(len(native_mora_length), len(user_mora_length))
    if n_all == 0:
        return 20.0, "長さの評価ができませんでした。"

    labels = (
        [str(label) for label in mora_labels[:n_all]]
        if mora_labels is not None and len(mora_labels) >= n_all
        else None
    )
    groups = long_vowel_groups(labels) if labels else [[i] for i in range(n_all)]
    n      = len(groups)

    native  = np.array([sum(float(native_mora_length[i]) for i in g) for g in groups])
    user    = np.array([sum(float(user_mora_length[i])   for i in g) for g in groups])
    kinds   = [_group_kind(labels, g) for g in groups]
    weights = np.array([_KIND_WEIGHT[k] for k in kinds])
    names   = (
        _unit_names([mora_group_kana(labels, g) for g in groups]) if labels
        else [f"{g[0] + 1}拍目" for g in groups]
    )

    diffs         = np.abs(native - user)
    total_weight  = float(np.sum(weights))
    weighted_diff = float(np.sum(diffs * weights) / total_weight) if total_weight > 0 else 0.0
    avg_share     = float(np.sum(native)) / n if np.sum(native) > 0 else 100.0 / n
    rel_diff      = weighted_diff / avg_share
    score         = max(0.0, round((1.0 - rel_diff) * 40, 1))

    worst_idx = int(np.argmax(diffs * weights))

    # ── 長音・促音：通常モーラに対する長さの比を、お手本の比と比べる ────
    # 以前は「長音は通常モーラの 1.5〜3.2 倍」のような固定の比で判定していたが、
    # Julius は促音（っ）の閉鎖を「q」と次の子音に分けるので q の長さは短く出やすく、
    # お手本を自分自身と比べても「短すぎます」になっていた。
    normal = [k for k in range(n) if kinds[k] == "normal"]
    abs_long_errors: list[tuple[int, str]] = []   # (まとまり番号, "短い"|"長い")
    abs_soku_errors: list[tuple[int, str]] = []

    if normal:
        native_norm = float(np.mean(native[normal]))
        user_norm   = float(np.mean(user[normal]))
        if native_norm > 0 and user_norm > 0:
            for k in range(n):
                if kinds[k] not in _SPECIAL_TOLERANCE or native[k] <= 0:
                    continue
                rel    = (user[k] / user_norm) / (native[k] / native_norm)
                lo, hi = _SPECIAL_TOLERANCE[kinds[k]]
                errors = abs_long_errors if kinds[k] == "long" else abs_soku_errors
                if rel < lo:
                    errors.append((k, "短い"))
                elif rel > hi:
                    errors.append((k, "長い"))

    # 割合の差が大きい長音・促音（平均的な長さの 16% 以上＝4モーラ語で以前の 4 ポイント）
    long_errors = [k for k in range(n) if kinds[k] == "long" and diffs[k] / avg_share > 0.16]
    soku_errors = [k for k in range(n) if kinds[k] == "soku" and diffs[k] / avg_share > 0.16]

    # お手本の比との比較を優先してフィードバック
    if abs_long_errors:
        idx, kind = abs_long_errors[0]
        if kind == "短い":
            feedback = (
                f"{names[idx]}の長音が短すぎます。"
                f"お手本と同じくらい、しっかり伸ばしてください（ほかの音の約2倍が目安）。"
            )
        else:
            feedback = (
                f"{names[idx]}の長音が長すぎます。"
                f"お手本くらいの長さ（ほかの音の約2倍）を目安にしてください。"
            )
    elif abs_soku_errors:
        idx, kind = abs_soku_errors[0]
        if kind == "短い":
            feedback = (
                f"{names[idx]}（促音）が短すぎます。"
                f"お手本と同じくらいの間（ま）を意識して詰めてください。"
            )
        else:
            feedback = (
                f"{names[idx]}（促音）が長すぎます。"
                f"詰まりを短くしてください。"
            )
    elif long_errors:
        idx = long_errors[0]
        d   = "短すぎます。しっかり伸ばして" if user[idx] < native[idx] else "長すぎます。少し短く"
        feedback = f"{names[idx]}の長音が{d}発音してください。"
    elif soku_errors:
        idx = soku_errors[0]
        d   = "短すぎます。一拍分しっかり詰めて" if user[idx] < native[idx] else "長すぎます。詰まりを短く"
        feedback = f"{names[idx]}（促音）が{d}発音してください。"
    elif rel_diff < 0.2:
        feedback = "各モーラの長さのバランスがとても良いです！"
    elif rel_diff < 0.4:
        d = "少し長すぎます。テンポよく" if user[worst_idx] > native[worst_idx] else "少し短すぎます。もう少し伸ばして"
        feedback = f"{names[worst_idx]}が{d}発音してみてください。"
    else:
        pct = abs(user[worst_idx] - native[worst_idx]) / native[worst_idx] * 100 if native[worst_idx] > 0 else 0.0
        feedback = (
            f"{names[worst_idx]}が長すぎます（お手本より約{pct:.0f}%長い）。"
            if user[worst_idx] > native[worst_idx] else
            f"{names[worst_idx]}が短すぎます（お手本より約{pct:.0f}%短い）。"
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
    pitch_fin2        : comp/smooth済みの録音ピッチ
    pitch_fin         : comp/smooth済みのネイティブピッチ（後方互換）
    pitch_user_raw    : NaN保持の録音半音ピッチ（モーラの高さ・安定度・有声率・Pearson用）
    pitch_native_raw  : NaN保持のネイティブ半音ピッチ（同上）
    vowel_score       : core/formant.py の calc_vowel_score() 結果
    vowel_feedback    : 母音品質フィードバック
    """
    accent_raw, accent_feedback, pattern, detected_nucleus = calc_accent_score(
        pitch_fin2=pitch_fin2, mora_values=mora_values,
        accent=accent, n_mora=n_mora,
        pitch_fin=pitch_fin,
        pitch_user_raw=pitch_user_raw,
        pitch_native_raw=pitch_native_raw,
        mora_labels=mora_labels,
    )
    length_raw, length_feedback = calc_length_score(
        native_mora_length=native_mora_length,
        user_mora_length=user_mora_length,
        mora_labels=mora_labels,
    )

    voiced_feedback = None
    if pitch_user_raw is not None:
        voiced_feedback = _voiced_ratio_feedback(
            pitch_user_raw, mora_values, n_mora,
            pitch_native_raw=pitch_native_raw, mora_labels=mora_labels,
        )

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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# モーラ別スコア（④ モーラ別スコアの表示）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _robust_normalize(pitch: np.ndarray, pitch_raw: np.ndarray) -> np.ndarray:
    """
    話者ごとに、有声フレームの中央値を 0、P10〜P90 の幅を 1 にそろえる。
    幅は PITCH_SPREAD_FLOOR_ST（3 半音）を下限にする。

    以前の 0〜1 の min-max 正規化は、抑揚のない声のわずかな揺れを 0〜1 いっぱいに
    広げてしまい（平坦な声でも「高さ」が高く出る）、逆に1フレームの外れ値
    （オクターブ誤検出など）があると残りが潰れてしまっていた。
    """
    v = pitch_raw[~np.isnan(pitch_raw)]
    if len(v) >= MIN_VOICED_TOTAL:
        center = float(np.median(v))
        spread = float(np.percentile(v, 90) - np.percentile(v, 10))
    else:
        finite = pitch[~np.isnan(pitch)]
        center = float(np.median(finite)) if len(finite) else 0.0
        spread = 0.0
    return (pitch - center) / max(spread, PITCH_SPREAD_FLOOR_ST)


def calc_mora_scores(
    pitch_fin:            list[float],
    pitch_fin2:           list[float],
    mora_values:          list[int],
    native_mora_length:   list[float],
    user_mora_length:     list[float],
    mora_labels:          list[str],
    native_formants:      list[dict] | None = None,
    user_formants:        list[dict] | None = None,
    pitch_native_raw:     list[float] | np.ndarray | None = None,
    pitch_user_raw:       list[float] | np.ndarray | None = None,
    vowel_scale:          float = 1.0,
) -> list[dict]:
    """
    モーラ単位のスコアを計算して返す。

    各モーラについて以下の3軸を 0〜100 点で評価する：

      accent : お手本と録音のピッチを話者ごとに正規化（_robust_normalize）し、
               当該モーラの「両方が有声」のフレームでの差の中央値 d から
               score = 100 × exp(−4 × d)
               両方で声が出ているフレームが少ないモーラ（「っ」など）は None。

      length : お手本/録音のモーラ長の比率からのズレ
               score = 100 × exp(−3 × |1 − ratio| × weight)
               長音・促音は weight=2、撥音は weight=1.5
               「こう」の「う」のような長音の後半は、前のモーラとまとめた長さで比べ、
               まとまりの両方に同じ点を付ける。

      vowel  : フォルマントデータがあるモーラのみ計算
               core/formant.vowel_distance()（話者差補正 vowel_scale 付き）の距離 d から
               score = 100 × exp(−d)（合計の母音点と同じ換算）

      total  : 3軸の単純平均（None の軸は除外）

    また、各モーラのフレーム範囲を返す（⑤ ピッチグラフハイライト用）。

    Returns
    -------
    list[dict]  各モーラの { label, mora_index, frame_start, frame_end, accent, length, vowel, total }
    """
    n_mora = min(len(mora_labels), len(mora_values))
    p1 = np.array(pitch_fin,  dtype=float)
    p2 = np.array(pitch_fin2, dtype=float)
    r1 = np.array(pitch_native_raw, dtype=float) if pitch_native_raw is not None else p1
    r2 = np.array(pitch_user_raw,   dtype=float) if pitch_user_raw   is not None else p2
    n_frames = min(len(p1), len(p2), len(r1), len(r2))
    p1_n = _robust_normalize(p1[:n_frames], r1[:n_frames])
    p2_n = _robust_normalize(p2[:n_frames], r2[:n_frames])
    voiced_both = ~np.isnan(r1[:n_frames]) & ~np.isnan(r2[:n_frames])

    labels   = [str(label) for label in mora_labels[:n_mora]]
    group_of = {i: g for g in long_vowel_groups(labels) for i in g}

    VOWELS_SET = {'a', 'i', 'u', 'e', 'o'}

    def _has_vowel(label: str) -> bool:
        return any(v in label for v in VOWELS_SET)

    results: list[dict] = []

    for i in range(n_mora):
        label = labels[i]
        start = mora_values[i]
        end   = mora_values[i + 1] if i + 1 < len(mora_values) else len(p1)

        # ── アクセントスコア ─────────────────────────────────────────
        acc_sc = None
        s, e = max(0, int(start)), min(n_frames, int(end))
        if s < e:
            mask = voiced_both[s:e]
            n_voiced = int(np.sum(mask))
            if n_voiced >= MIN_VOICED_FRAMES and n_voiced >= MIN_VOICED_RATIO * (e - s):
                diff   = float(np.median(np.abs(p1_n[s:e][mask] - p2_n[s:e][mask])))
                acc_sc = round(100.0 * float(np.exp(-4.0 * diff)), 1)

        # ── 長さスコア（長音はまとまりで比べる） ─────────────────────
        group   = group_of[i]
        nat_len = sum(float(native_mora_length[j]) for j in group if j < len(native_mora_length))
        usr_len = sum(float(user_mora_length[j])   for j in group if j < len(user_mora_length))
        if nat_len > 0:
            ratio  = usr_len / nat_len
            weight = _KIND_WEIGHT[_group_kind(labels, group)]
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
            dist = vowel_distance(native_formants[i], user_formants[i], vowel_scale)
            if dist is not None:
                vow_sc = round(100.0 * float(np.exp(-dist)), 1)

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
