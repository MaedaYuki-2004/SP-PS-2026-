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
                "best_grade": None, "word_counts": {}, "word_best": {},
                "streak": 0}

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
    """現在の連続練習日数を返す（ソフトストリーク方式）。

    Duolingo のストリークフリーズと同様の効果を狙い、
    1日だけの空きはストリークを途切れさせない（橋渡しする）。
    カウントされるのは実際に練習した日数のみ。
    """
    from datetime import timedelta
    history = load_history()
    if not history:
        return 0

    dates: set = set()
    for r in history:
        ts = r.get("timestamp", "")[:10]
        if ts:
            dates.add(ts)
    if not dates:
        return 0

    today = datetime.now().date()
    date_objs = set()
    for d in dates:
        try:
            date_objs.add(datetime.fromisoformat(d).date())
        except Exception:
            continue

    # 今日か昨日に練習していなければストリークなし
    # （昨日までの場合も1日ブリッジで今日はまだセーフ）
    if today not in date_objs and (today - timedelta(days=1)) not in date_objs \
            and (today - timedelta(days=2)) not in date_objs:
        return 0

    streak  = 0
    gap     = 0
    cursor  = today
    while True:
        if cursor in date_objs:
            streak += 1
            gap = 0
        else:
            gap += 1
            if gap > 1:   # 2日以上の空白で終了（1日はブリッジ）
                break
        cursor -= timedelta(days=1)
        if streak > 3650:
            break
    return streak


def get_overall_score() -> dict | None:
    """総合発音力スコアを返す（ELSA Score 方式）。

    直近30回の加重平均（新しいほど重い）でスコアを算出し、
    レベルラベルと直近のトレンド（前半 vs 後半の差）を添える。
    """
    history = load_history()
    totals  = [float(r["total"]) for r in history[:30] if r.get("total") is not None]
    if len(totals) < 3:
        return None

    # 加重平均: 最新レコードの重みを大きく
    weights = [1.0 / (1.0 + i * 0.1) for i in range(len(totals))]
    score   = sum(t * w for t, w in zip(totals, weights)) / sum(weights)

    if score >= 90:   level = "達人"
    elif score >= 80: level = "上級"
    elif score >= 65: level = "中級"
    elif score >= 50: level = "初中級"
    else:             level = "基礎"

    # トレンド: 直近10回 vs その前10回
    trend = None
    if len(totals) >= 10:
        recent = sum(totals[:5])  / 5
        prev   = sum(totals[5:10]) / 5
        trend  = round(recent - prev, 1)

    return {"score": round(score, 1), "level": level, "trend": trend}


def get_weekly_report() -> dict | None:
    """今週（直近7日）の練習サマリーを返す。

    Returns: {sessions, days, avg, delta_vs_prev_week, improved_sound}
    """
    from datetime import timedelta
    history = load_history()
    if not history:
        return None

    now        = datetime.now()
    week_ago   = now - timedelta(days=7)
    two_weeks  = now - timedelta(days=14)

    this_week, prev_week = [], []
    for r in history:
        try:
            ts = datetime.fromisoformat(r.get("timestamp", ""))
        except Exception:
            continue
        if ts >= week_ago:
            this_week.append(r)
        elif ts >= two_weeks:
            prev_week.append(r)

    if not this_week:
        return None

    tw_totals = [float(r["total"]) for r in this_week if r.get("total") is not None]
    pw_totals = [float(r["total"]) for r in prev_week if r.get("total") is not None]
    avg   = round(sum(tw_totals) / len(tw_totals), 1) if tw_totals else None
    delta = round(avg - sum(pw_totals) / len(pw_totals), 1) if (avg is not None and pw_totals) else None
    days  = len({r.get("timestamp", "")[:10] for r in this_week})

    # 一番伸びた音: 今週 vs それ以前のモーラ別平均を比較
    improved_sound = None
    try:
        def _mora_avgs(records):
            agg: dict[str, list[float]] = {}
            for r in records:
                for m in r.get("mora_scores", []):
                    k, t = m.get("kana", ""), m.get("total")
                    if k and k not in ("ん", "っ", "ー") and t is not None:
                        agg.setdefault(k, []).append(float(t))
            return {k: sum(v) / len(v) for k, v in agg.items() if len(v) >= 2}

        tw_avg = _mora_avgs(this_week)
        base_avg = _mora_avgs(prev_week + history[len(this_week) + len(prev_week):])
        gains = [(k, tw_avg[k] - base_avg[k]) for k in tw_avg if k in base_avg]
        gains = [(k, g) for k, g in gains if g > 3]
        if gains:
            k, g = max(gains, key=lambda x: x[1])
            improved_sound = {"kana": k, "gain": round(g, 1)}
    except Exception:
        pass

    return {"sessions": len(this_week), "days": days, "avg": avg,
            "delta": delta, "improved_sound": improved_sound}


def get_word_recent_scores(limit: int = 5) -> dict[str, list[float]]:
    """各単語の直近スコアを {word_id: [oldest→newest]} で返す。"""
    history = load_history()
    tmp: dict[str, list[float]] = {}
    for r in reversed(history):
        wid   = r.get("word_id", "")
        total = r.get("total")
        if wid and total is not None:
            tmp.setdefault(wid, []).append(float(total))
    return {wid: scores[-limit:] for wid, scores in tmp.items()}


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
    mora_scores:  list[dict] | None = None,
) -> dict:
    """スコア結果を履歴の先頭に追加して保存する。

    mora_scores はモーラ別スコア（苦手音分析用）。
    [{kana, accent, length, vowel, total}] の圧縮形で保存する。
    """
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
    if mora_scores:
        compact = []
        for m in mora_scores:
            try:
                compact.append({
                    "kana":   m.get("kana") or m.get("label", ""),
                    "accent": round(float(m["accent"]), 1) if m.get("accent") is not None else None,
                    "length": round(float(m["length"]), 1) if m.get("length") is not None else None,
                    "vowel":  round(float(m["vowel"]),  1) if m.get("vowel")  is not None else None,
                    "total":  round(float(m["total"]),  1) if m.get("total")  is not None else None,
                })
            except Exception:
                continue
        if compact:
            record["mora_scores"] = compact

    history = load_history()
    history.insert(0, record)
    history = history[:MAX_RECORDS]

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(HISTORY_PATH)

    return record