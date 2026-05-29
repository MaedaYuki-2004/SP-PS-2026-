"""
core/analysis.py
学習曲線の統計分析モジュール。

【分析内容】
  1. 単語ごとの上達速度
     scipy.stats.linregress でスコア履歴に回帰直線を当てはめ、
     傾き（slope）で「1回練習するとスコアが何点上がるか」を推定する。

  2. プラトー検出
     直近 PLATEAU_WINDOW 回の傾きが PLATEAU_THRESHOLD 未満なら
     「停滞中」と判定する。

  3. アクセント型ごとの上達しやすさ
     アクセント型ごとに slope の平均・中央値・サンプル数を集計する。
     slope が大きいほど「上達しやすいアクセント型」を意味する。

  4. Sランク到達回数の分布
     実際に S グレード（90点以上）に到達した単語について、
     「何回目の練習で初めてSになったか」を集計する。

  5. 全体の上達トレンド
     すべての練習記録を時系列に並べ、スコアの移動平均を計算する。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import linregress

PLATEAU_WINDOW    = 10    # プラトー判定に使う直近の練習回数
PLATEAU_THRESHOLD = 0.3   # 傾きがこれ未満なら停滞と判定（点/回）
MIN_SAMPLES_REG   = 3     # 回帰直線を引くのに必要な最低サンプル数


# ── アクセント型ラベル ────────────────────────────────────────────
_ACCENT_LABEL = {
    0: "平板型",
    1: "頭高型",
    2: "中高型（2型）",
    3: "中高型（3型）",
    4: "中高型（4型）",
}


def _accent_label(accent: int | None) -> str:
    if accent is None:
        return "不明"
    return _ACCENT_LABEL.get(int(accent), f"{accent}型")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 内部ユーティリティ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _regression(scores: list[float]) -> dict | None:
    """
    スコアリストに回帰直線を当てはめて結果を返す。
    サンプル数が MIN_SAMPLES_REG 未満の場合は None を返す。

    Returns
    -------
    dict:
        slope     : 傾き（点/回）
        intercept : 切片
        r_value   : 相関係数
        r_squared : 決定係数
        first     : 初回スコア
        last      : 最終スコア
        improvement : 最終スコア − 初回スコア
    """
    if len(scores) < MIN_SAMPLES_REG:
        return None
    x = list(range(len(scores)))
    slope, intercept, r, p, se = linregress(x, scores)
    return {
        "slope":       round(float(slope), 3),
        "intercept":   round(float(intercept), 2),
        "r_value":     round(float(r), 3),
        "r_squared":   round(float(r ** 2), 3),
        "first":       round(float(scores[0]), 1),
        "last":        round(float(scores[-1]), 1),
        "improvement": round(float(scores[-1]) - float(scores[0]), 1),
    }


def _is_plateau(scores: list[float]) -> bool:
    """直近 PLATEAU_WINDOW 回の傾きが PLATEAU_THRESHOLD 未満なら停滞と判定する。"""
    recent = scores[-PLATEAU_WINDOW:]
    if len(recent) < 5:
        return False
    result = _regression(recent)
    if result is None:
        return False
    return abs(result["slope"]) < PLATEAU_THRESHOLD


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# メイン分析関数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_learning_stats(
    history: list[dict],
    words_db: dict,
) -> dict:
    """
    練習履歴と単語DBから学習曲線の統計情報を計算して返す。

    Parameters
    ----------
    history  : load_history() の返値（新しい順）
    words_db : words_db.json の内容

    Returns
    -------
    dict:
        word_stats        : 単語ごとの統計（回帰・プラトー）
        accent_stats      : アクセント型ごとの統計
        s_rank_dist       : Sランク到達回数の分布
        overall_trend     : 全体スコアの時系列
        summary           : サマリー統計
    """
    # 履歴を時系列順（古い順）に並び替えてから単語ごとにグループ化
    chron = sorted(
        [r for r in history if r.get("total") is not None],
        key=lambda r: r.get("timestamp", ""),
    )

    word_records: dict[str, list[dict]] = defaultdict(list)
    for rec in chron:
        wid = rec.get("word_id")
        if wid:
            word_records[wid].append(rec)

    # ── 1. 単語ごとの統計 ──────────────────────────────────────────
    word_stats: list[dict] = []
    all_slopes: list[float] = []

    for wid, recs in word_records.items():
        scores = [float(r["total"]) for r in recs]
        info   = words_db.get(wid, {})
        reg    = _regression(scores)
        plateau = _is_plateau(scores)

        # Sランク到達：初めて90点以上になったのは何回目か
        s_rank_session = None
        for i, s in enumerate(scores):
            if s >= 90.0:
                s_rank_session = i + 1
                break

        entry = {
            "word_id":       wid,
            "display":       info.get("display", wid),
            "reading":       info.get("reading", ""),
            "accent":        info.get("accent"),
            "accent_label":  _accent_label(info.get("accent")),
            "n_sessions":    len(scores),
            "scores":        scores,
            "best":          round(max(scores), 1),
            "latest":        round(scores[-1], 1),
            "regression":    reg,
            "is_plateau":    plateau,
            "s_rank_session": s_rank_session,
        }
        word_stats.append(entry)
        if reg:
            all_slopes.append(reg["slope"])

    word_stats.sort(key=lambda w: w["display"])

    # ── 2. アクセント型ごとの統計 ──────────────────────────────────
    accent_buckets: dict[str, list[float]] = defaultdict(list)
    accent_n: dict[str, int] = defaultdict(int)
    accent_s_pct: dict[str, list[float]] = defaultdict(list)

    for w in word_stats:
        label = w["accent_label"]
        if w["regression"]:
            accent_buckets[label].append(w["regression"]["slope"])
        accent_n[label] += w["n_sessions"]
        if w["best"] >= 90.0:
            accent_s_pct[label].append(100.0)
        elif w["n_sessions"] > 0:
            accent_s_pct[label].append(0.0)

    accent_stats: list[dict] = []
    for label in sorted(accent_buckets.keys()):
        slopes = accent_buckets[label]
        accent_stats.append({
            "label":      label,
            "n_words":    len(slopes),
            "n_sessions": accent_n[label],
            "avg_slope":  round(float(np.mean(slopes)), 3) if slopes else None,
            "med_slope":  round(float(np.median(slopes)), 3) if slopes else None,
            "s_rate":     round(float(np.mean(accent_s_pct[label])), 1) if accent_s_pct[label] else 0.0,
        })
    accent_stats.sort(key=lambda a: a["avg_slope"] or 0, reverse=True)

    # ── 3. Sランク到達回数の分布 ────────────────────────────────────
    s_sessions = [w["s_rank_session"] for w in word_stats if w["s_rank_session"] is not None]
    s_rank_dist = {}
    if s_sessions:
        from collections import Counter
        counts = Counter(s_sessions)
        s_rank_dist = {
            "distribution": dict(sorted(counts.items())),
            "mean":   round(float(np.mean(s_sessions)), 1),
            "median": round(float(np.median(s_sessions)), 1),
            "min":    int(min(s_sessions)),
            "max":    int(max(s_sessions)),
            "n":      len(s_sessions),
        }

    # ── 4. 全体スコアの時系列（5回移動平均） ────────────────────────
    all_scores = [float(r["total"]) for r in chron]
    window = 5
    if len(all_scores) >= window:
        ma = [
            round(float(np.mean(all_scores[max(0, i - window + 1):i + 1])), 1)
            for i in range(len(all_scores))
        ]
    else:
        ma = all_scores[:]

    overall_trend = {
        "scores":         all_scores,
        "moving_average": ma,
        "timestamps":     [r.get("timestamp", "")[:10] for r in chron],
    }

    # ── 5. サマリー ──────────────────────────────────────────────
    plateau_words = [w for w in word_stats if w["is_plateau"]]
    improving     = [w for w in word_stats if w["regression"] and w["regression"]["slope"] > PLATEAU_THRESHOLD]
    declining     = [w for w in word_stats if w["regression"] and w["regression"]["slope"] < -PLATEAU_THRESHOLD]

    summary = {
        "total_sessions":  len(chron),
        "total_words":     len(word_stats),
        "avg_slope":       round(float(np.mean(all_slopes)), 3) if all_slopes else None,
        "n_plateau":       len(plateau_words),
        "n_improving":     len(improving),
        "n_declining":     len(declining),
        "n_s_rank":        len(s_sessions),
        "fastest_improver": max(word_stats, key=lambda w: (w["regression"]["slope"] if w["regression"] else -999))
            if word_stats else None,
        "most_plateau":    sorted(plateau_words, key=lambda w: w["n_sessions"], reverse=True)[:3],
    }

    return {
        "word_stats":    word_stats,
        "accent_stats":  accent_stats,
        "s_rank_dist":   s_rank_dist,
        "overall_trend": overall_trend,
        "summary":       summary,
    }