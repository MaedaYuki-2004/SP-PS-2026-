"""
core/weakness.py
練習履歴のモーラ別スコアから「苦手な音」を分析するモジュール。

ELSA Speak などの発音アプリが行う「弱点フォニームの特定 → 集中ドリル」
と同じ発想で、履歴に蓄積された mora_scores を音（かな）単位で集計し、
繰り返しスコアが低い音をランキングで返す。
"""
from __future__ import annotations

from core.history import load_history

# ん・っ・ー は単独で練習対象にしない
_SKIP_KANA = {"ん", "っ", "ー", ""}

# かな → 属する母音（口の形練習用）
_KANA_VOWEL: dict[str, str] = {}
for _row, _vowels in [
    ("あかさたなはまやらわがざだばぱ", "あ"),
    ("いきしちにひみりぎじぢびぴ",     "い"),
    ("うくすつぬふむゆるぐずづぶぷ",   "う"),
    ("えけせてねへめれげぜでべぺ",     "え"),
    ("おこそとのほもよろをごぞどぼぽ", "お"),
]:
    for _k in _row:
        _KANA_VOWEL[_k] = _vowels


def kana_vowel(kana: str) -> str | None:
    """かなモーラの母音を返す（拗音は末尾文字基準: きゃ→あ）。"""
    if not kana:
        return None
    ch = kana[-1]
    if ch in "ゃャ": return "あ"
    if ch in "ゅュ": return "う"
    if ch in "ょョ": return "お"
    return _KANA_VOWEL.get(ch)


def get_weak_sounds(
    min_attempts: int = 2,
    threshold:    float = 70.0,
    limit:        int = 5,
    recent:       int = 60,
) -> list[dict]:
    """繰り返しスコアが低い音（かなモーラ）をランキングで返す。

    Returns
    -------
    [{kana, avg, count, worst_axis, vowel}] — avg 昇順（弱い順）
      worst_axis: accent / length / vowel のうち平均が最も低い軸
    """
    history = load_history()[:recent]
    agg: dict[str, dict] = {}

    for rec in history:
        for m in rec.get("mora_scores", []):
            kana  = m.get("kana", "")
            total = m.get("total")
            if kana in _SKIP_KANA or total is None:
                continue
            a = agg.setdefault(kana, {"totals": [], "accent": [], "length": [], "vowel": []})
            a["totals"].append(float(total))
            for axis in ("accent", "length", "vowel"):
                if m.get(axis) is not None:
                    a[axis].append(float(m[axis]))

    result = []
    for kana, a in agg.items():
        if len(a["totals"]) < min_attempts:
            continue
        avg = sum(a["totals"]) / len(a["totals"])
        if avg >= threshold:
            continue
        axis_avgs = {
            axis: (sum(v) / len(v)) for axis, v in
            (("accent", a["accent"]), ("length", a["length"]), ("vowel", a["vowel"]))
            if v
        }
        worst_axis = min(axis_avgs, key=axis_avgs.get) if axis_avgs else "vowel"
        result.append({
            "kana":       kana,
            "avg":        round(avg, 1),
            "count":      len(a["totals"]),
            "worst_axis": worst_axis,
            "vowel":      kana_vowel(kana),
        })

    result.sort(key=lambda x: x["avg"])
    return result[:limit]


def find_words_with_kana(kana: str, words: list[dict], limit: int = 4) -> list[dict]:
    """読みに指定のかなモーラを含む単語を返す。"""
    hits = []
    for w in words:
        if kana in (w.get("reading") or ""):
            hits.append(w)
        if len(hits) >= limit:
            break
    return hits
