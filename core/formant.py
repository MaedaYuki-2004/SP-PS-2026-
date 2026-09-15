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

【2026-09-14 見直し】
  ・話者差補正を speaker_formant_scale() に切り出し、モーラ別の「口の形」
    （evaluate.calc_mora_scores）にも同じ補正をかけるようにした。
    以前は合計の母音点だけが補正され、モーラ別は補正なしで比べていたため、
    お手本と声の高さ（声道の長さ）が違う生徒ほど「口の形」が低く出て、
    苦手な音の分析でも「口の形」が弱点と判定されやすかった。
  ・「こう」「せい」の後半（う・い）は前の母音と1つのまとまりとして測る
    （core/utils.long_vowel_groups）。合計点ではまとまりを1回だけ数える。
  ・性別の判定しきい値を estimate_pitch_range() の返す上限に合わせて直した
    （下の _MALE_CEILING_THRESHOLD の説明を参照）。
  ・お手本のフォルマントのキャッシュキーにモーラ区間を含めた。以前は wav の
    更新時刻だけを見ていたので、アライメントをやり直すと古い区間の値が返っていた。
"""
from __future__ import annotations

import hashlib
import json as _json
import numpy as np
import parselmouth
import parselmouth.praat
from pathlib import Path

from core.utils import long_vowel_groups, mora_group_kana

_VOWEL_CHARS = {'a', 'i', 'u', 'e', 'o'}
_SAMPLE_RATIOS = [0.30, 0.50, 0.70]
_MALE_FORMANT_SCALE = 0.85
# estimate_pitch_range() が返す上限は「有声フレームの90パーセンタイル × 1.5」。
# 以前は Praat の男性用上限（200Hz）と同じ値で判定していたが、それだと
# 90パーセンタイルが 133Hz 以下の人しか男性にならず、多くの男性が女性と判定されていた。
# 300Hz は 90パーセンタイル 200Hz に当たる（男性の多くはこれより低く、女性の多くは高い）。
_MALE_CEILING_THRESHOLD = 300.0

# ── フォルマントキャッシュ ────────────────────────────────────────
# ネイティブ音声のフォルマントは毎回同じ計算になるため、
# 初回だけ計算して data/config/formant_cache.json に保存する。
# ファイルの更新時刻（mtime）が変わると自動的にキャッシュを無効化する。
_CACHE_PATH = Path(__file__).parent.parent / "data" / "config" / "formant_cache.json"


def _cache_key(sound_file: str, max_formant: float, mora_list: list) -> str:
    """ファイルパス・更新時刻・max_formant・モーラ区間からキャッシュキーを生成する。

    末尾の "v3" は抽出ロジックのバージョン。妥当性フィルタ導入（v2）、
    モーラ区間をキーに含める変更（v3）で旧キャッシュを無効化するために付与している。
    """
    p = Path(sound_file)
    mtime = str(p.stat().st_mtime) if p.exists() else "0"
    spans = ";".join(f"{float(m[0]):.4f}-{float(m[1]):.4f}-{m[2]}" for m in mora_list)
    raw = f"{sound_file}:{max_formant}:{mtime}:{spans}:v3"
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
        key   = _cache_key(sound_file, max_formant, mora_list)
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

        # F1/F2 をペアで収集し、母音としてあり得ない測定値を除外する。
        # 子音の渡り部分・オクターブ誤検出などのノイズ対策。
        pairs: list[tuple[float, float]] = []
        for ratio in _SAMPLE_RATIOS:
            t = start + duration * ratio
            t = max(snd.start_time, min(snd.end_time, t))
            f1, f2 = _get_formant_at_time(formant, t)
            if f1 is None or f2 is None:
                continue
            if not (150.0 <= f1 <= 1200.0 and 400.0 <= f2 <= 3500.0 and f2 > f1 + 150.0):
                continue
            pairs.append((f1, f2))

        # 平均ではなく中央値（外れ値1点の影響を抑える）
        f1_med = float(np.median([p[0] for p in pairs])) if pairs else None
        f2_med = float(np.median([p[1] for p in pairs])) if pairs else None

        results.append({
            "label":        label,
            "start":        start,
            "end":          end,
            "center":       start + duration * 0.5,
            "f1":           f1_med,
            "f2":           f2_med,
            "f1_n_samples": len(pairs),
            "f2_n_samples": len(pairs),
        })

    if use_cache:
        cache[key] = results
        _save_formant_cache(cache)

    return results


def speaker_formant_scale(
    native_formants:      list[dict],
    user_formants:        list[dict],
    pitch_ceiling_native: float | None = None,
    pitch_ceiling_user:   float | None = None,
) -> tuple[float, str]:
    """
    お手本と録音の声道の長さの違いを吸収する係数（お手本の F1/F2 にかける）を返す。

    性別の2値判定ではなく、この録音ペア自体からスケール係数を推定する。
    同じ位置のモーラ同士の F1/F2 比（録音/お手本）の中央値 ≒ 声道長の違い。
    これにより境界上の話者で補正が ON/OFF に切り替わる問題を避ける。
    比が2つ未満しか取れないときだけ、声の高さから推定した性別で補正する。

    Returns
    -------
    (correction_factor, note)
    """
    n = min(len(native_formants), len(user_formants))
    ratios: list[float] = []
    for i in range(n):
        na, us = native_formants[i], user_formants[i]
        if not _has_vowel(na.get("label", "")):
            continue
        for k in ("f1", "f2"):
            nf, uf = na.get(k), us.get(k)
            if nf and uf and nf > 0:
                ratios.append(uf / nf)

    if len(ratios) >= 2:
        correction = float(np.clip(np.median(ratios), 0.80, 1.25))
        note       = "（話者差補正済み）" if abs(correction - 1.0) > 0.03 else ""
        return correction, note
    return _calc_correction(pitch_ceiling_native, pitch_ceiling_user)


def vowel_distance(
    native:        dict,
    user:          dict,
    correction:    float = 1.0,
    bark_scale_f1: float = 3.0,
    bark_scale_f2: float = 4.0,
) -> float | None:
    """お手本（補正後）と録音の母音の距離（Bark、F1 は 3、F2 は 4 で割って正規化）。
    どちらかの F1/F2 が測れていなければ None。"""
    nf1, nf2 = native.get("f1"), native.get("f2")
    uf1, uf2 = user.get("f1"), user.get("f2")
    if not (nf1 and nf2 and uf1 and uf2):
        return None
    db1 = (_hz_to_bark(nf1 * correction) - _hz_to_bark(uf1)) / bark_scale_f1
    db2 = (_hz_to_bark(nf2 * correction) - _hz_to_bark(uf2)) / bark_scale_f2
    return float(np.sqrt(db1 ** 2 + db2 ** 2))


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

    【話者差補正】
    speaker_formant_scale() の係数をお手本の F1/F2 にかけてから比べる。

    【長音】
    「こう」の「う」のような長音の後半は、前のモーラと同じ区間で測った値が
    入っているので（core/utils.long_vowel_spans）、まとまりごとに1回だけ数える。

    【サンプル数重み付け】
    有効サンプル数が少ないモーラほど距離計算への影響を下げる。
    """
    n = min(len(native_formants), len(user_formants))
    correction, gender_note = speaker_formant_scale(
        native_formants, user_formants, pitch_ceiling_native, pitch_ceiling_user,
    )

    labels = [str(f.get("label", "")) for f in native_formants[:n]]
    distances:  list[float] = []
    weights:    list[float] = []
    mora_dists: list[tuple[float, str]] = []

    for group in long_vowel_groups(labels):
        i      = group[0]
        native = native_formants[i]
        user   = user_formants[i]

        if not _has_vowel(labels[i]):
            continue

        dist = vowel_distance(native, user, correction, bark_scale_f1, bark_scale_f2)
        if dist is None:
            continue

        # サンプル数重み付け
        n_native   = native.get("f1_n_samples", 3)
        n_user     = user.get("f1_n_samples",   3)
        confidence = min(n_native, n_user) / len(_SAMPLE_RATIOS)

        distances.append(dist)
        weights.append(confidence)
        mora_dists.append((dist, mora_group_kana(labels, group)))

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