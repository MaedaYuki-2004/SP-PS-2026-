"""
core/history.py
練習履歴の保存・読み込みを担当するモジュール。

【追加】get_spaced_repetition_candidates()
  指定日数以上練習していない単語のうち、スコアが一定以下のものを返す。
  間隔反復クエスト（12）の基盤となる関数。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from config import DATA_DIR

HISTORY_PATH  = DATA_DIR / "config" / "history.json"
MAX_RECORDS   = 500


def load_history() -> list[dict]:
    """全履歴を返す（新しい順）。"""
    if not HISTORY_PATH.exists():
        return []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_word_history(word_id: str) -> list[dict]:
    """特定の単語の履歴を返す（新しい順）。"""
    return [r for r in load_history() if r.get("word_id") == word_id]


def get_last_score(word_id: str) -> dict | None:
    """特定の単語の直前のスコアを返す。"""
    history = load_word_history(word_id)
    return history[0] if history else None


def get_stats() -> dict:
    """全履歴から集計統計を返す。"""
    history = load_history()
    if not history:
        return {"total_sessions": 0, "unique_words": 0, "avg_score": 0.0,
                "best_grade": None, "word_counts": {}, "word_best": {}}

    totals      = [r["total"] for r in history if r.get("total") is not None]
    word_counts: dict[str, int] = {}
    word_best:   dict[str, float] = {}
    for r in history:
        wid   = r.get("word_id", "")
        total = r.get("total")
        word_counts[wid] = word_counts.get(wid, 0) + 1
        if wid and total is not None:
            t = float(total)
            if wid not in word_best or t > word_best[wid]:
                word_best[wid] = t

    grade_order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4}
    grades      = [r.get("grade") for r in history if r.get("grade")]
    best_grade  = min(grades, key=lambda g: grade_order.get(g, 9)) if grades else None

    return {
        "total_sessions": len(history),
        "unique_words":   len(word_counts),
        "avg_score":      round(sum(totals) / len(totals), 1) if totals else 0.0,
        "best_grade":     best_grade,
        "word_counts":    word_counts,
        "word_best":      word_best,
        "streak":         get_streak(),
    }


def get_spaced_repetition_candidates(
    min_days:  float = 3.0,
    max_score: float = 85.0,
    limit:     int   = 5,
) -> list[dict]:
    """
    間隔反復の対象となる単語を返す。

    条件：
    1. 最後の練習から min_days 日以上経過している
    2. 最後のスコアが max_score 未満（完璧にマスターした単語は除外）

    返り値：
    最後の練習記録に "days_ago"（経過日数）を追加した dict のリスト。
    日数が多い順（より長く放置されている順）でソートして返す。
    """
    history = load_history()

    # 単語ごとに最新の記録だけを保持
    seen: dict[str, dict] = {}
    for r in history:
        wid = r.get("word_id")
        if wid and wid not in seen:
            seen[wid] = r

    candidates = []
    now = datetime.now()

    for wid, rec in seen.items():
        ts = rec.get("timestamp")
        if not ts:
            continue
        try:
            last_dt  = datetime.fromisoformat(ts)
            days_ago = (now - last_dt).total_seconds() / 86400
        except Exception:
            continue

        score = float(rec.get("total") or 0)
        if days_ago >= min_days and score < max_score:
            candidates.append({**rec, "days_ago": round(days_ago, 1)})

    # 日数が多い順（最も長く放置されている順）
    candidates.sort(key=lambda x: x["days_ago"], reverse=True)
    return candidates[:limit]


def get_streak() -> int:
    """現在の連続練習日数を返す（今日or昨日から連続している日数）。"""
    history = load_history()
    if not history:
        return 0
    from collections import OrderedDict
    dates = OrderedDict()
    for r in history:
        ts = r.get("timestamp", "")[:10]
        if ts:
            dates[ts] = True
    sorted_dates = sorted(dates.keys(), reverse=True)
    if not sorted_dates:
        return 0
    today = datetime.now().date()
    streak = 0
    for i, d in enumerate(sorted_dates):
        try:
            dt = datetime.fromisoformat(d).date()
        except Exception:
            continue
        expected = today - __import__("datetime").timedelta(days=i)
        if dt == expected:
            streak += 1
        else:
            break
    return streak


def get_daily_counts(days: int = 84) -> dict[str, int]:
    """過去 days 日分の日別録音回数を {YYYY-MM-DD: count} で返す。"""
    history = load_history()
    counts: dict[str, int] = {}
    for r in history:
        ts = r.get("timestamp", "")[:10]
        if ts:
            counts[ts] = counts.get(ts, 0) + 1
    from datetime import date, timedelta
    today = date.today()
    result = {}
    for i in range(days):
        d = str(today - timedelta(days=days - 1 - i))
        result[d] = counts.get(d, 0)
    return result


def save_record(
    word_id:      str,
    display:      str,
    reading:      str,
    score_result: dict,
) -> dict:
    """スコア結果を履歴の先頭に追加して保存する。"""
    record = {
        "id":           f"rec_{time.time_ns()}",
        "word_id":      word_id,
        "display":      display,
        "reading":      reading,
        "timestamp":    datetime.now().isoformat(),
        "total":        score_result.get("total"),
        "accent_score": score_result.get("accent_score"),
        "length_score": score_result.get("length_score"),
        "vowel_score":  score_result.get("vowel_score"),
        "grade":        score_result.get("grade"),
        "accent_label": score_result.get("accent_label"),
    }

    history = load_history()
    history.insert(0, record)
    history = history[:MAX_RECORDS]

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(HISTORY_PATH)

    return record