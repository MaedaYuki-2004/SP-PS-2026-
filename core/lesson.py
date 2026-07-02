"""
core/lesson.py
「今日のレッスン」自動編成モジュール。

Duolingo / ELSA Speak の「デイリーレッスン」方式：
アプリ側が練習履歴からその日のメニューを1本道で組み立て、
ユーザーは「始める」を押すだけにする。

ステップ構成（最大4つ・データに応じて可変）:
  1. review    — 間隔反復（3日以上空いた単語の復習）
  2. sound     — 苦手音を含む単語の録音練習
  3. mouth     — 母音トレーナーで口の形練習
  4. challenge — 練習回数が少ない単語への挑戦

進捗は data/config/daily_lesson.json に日付キーで保存。
録音ステップは履歴から自動判定、mouth ステップは
/api/vowel_trainer/complete が mark_mouth_done() を呼んで更新する。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from config import DATA_DIR
from core.history import load_history, get_spaced_repetition_candidates, get_stats
from core.weakness import get_weak_sounds, find_words_with_kana

LESSON_PATH = DATA_DIR / "config" / "daily_lesson.json"
MAX_STEPS   = 4


def _load() -> dict:
    try:
        if LESSON_PATH.exists():
            return json.loads(LESSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save(state: dict) -> None:
    LESSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LESSON_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LESSON_PATH)


def _practiced_today(word_id: str) -> bool:
    today = date.today().isoformat()
    for r in load_history():
        ts = r.get("timestamp", "")
        if not ts.startswith(today):
            break  # 履歴は新しい順なので今日以外に達したら終了
        if r.get("word_id") == word_id:
            return True
    return False


def _build_steps(words: list[dict]) -> list[dict]:
    """履歴から今日のステップを編成する。"""
    steps: list[dict] = []
    used_ids: set[str] = set()
    word_map = {w["word_id"]: w for w in words}

    # ── 1. 復習（間隔反復） ─────────────────────────────
    try:
        sr = get_spaced_repetition_candidates(min_days=3.0, max_score=85.0, limit=1)
        if sr:
            wid = sr[0]["word_id"]
            if wid in word_map:
                days = float(sr[0].get("days_ago", 3))
                steps.append({
                    "type": "review", "kind": "record",
                    "word_id": wid,
                    "display": word_map[wid].get("display", wid),
                    "title": "復習",
                    "reason": f"{days:.0f}日ぶり。忘れないうちにもう一度",
                })
                used_ids.add(wid)
    except Exception:
        pass

    # ── 2. 苦手音の録音練習 ─────────────────────────────
    weak_sounds = []
    try:
        weak_sounds = get_weak_sounds(limit=3)
        for w in weak_sounds:
            candidates = [c for c in find_words_with_kana(w["kana"], words, limit=6)
                          if c["word_id"] not in used_ids]
            if candidates:
                c = candidates[0]
                steps.append({
                    "type": "sound", "kind": "record",
                    "word_id": c["word_id"],
                    "display": c.get("display", c["word_id"]),
                    "title": f"苦手音「{w['kana']}」",
                    "reason": f"「{w['kana']}」の平均が{w['avg']:.0f}点。集中練習しよう",
                })
                used_ids.add(c["word_id"])
                break
    except Exception:
        pass

    # ── 3. 口の形（母音トレーナー） ──────────────────────
    try:
        vowel_weak = next((w for w in weak_sounds
                           if w.get("worst_axis") == "vowel" and w.get("vowel")), None)
        if vowel_weak:
            candidates = [c for c in find_words_with_kana(vowel_weak["kana"], words, limit=6)]
            if candidates:
                c = candidates[0]
                steps.append({
                    "type": "mouth", "kind": "mouth",
                    "word_id": c["word_id"],
                    "display": c.get("display", c["word_id"]),
                    "title": f"口の形「{vowel_weak['vowel']}」",
                    "reason": "カメラで口の形を確認しながら1回完走しよう",
                })
                used_ids.add(c["word_id"])
    except Exception:
        pass

    # ── 4. チャレンジ（練習回数が少ない単語）で埋める ────
    try:
        counts = (get_stats() or {}).get("word_counts", {})
        fresh = sorted(
            (w for w in words if w["word_id"] not in used_ids),
            key=lambda w: counts.get(w["word_id"], 0),
        )
        for w in fresh:
            if len(steps) >= MAX_STEPS:
                break
            n = counts.get(w["word_id"], 0)
            steps.append({
                "type": "challenge", "kind": "record",
                "word_id": w["word_id"],
                "display": w.get("display", w["word_id"]),
                "title": "チャレンジ" if n == 0 else "仕上げ",
                "reason": "初めての単語に挑戦" if n == 0 else f"まだ{n}回。定着させよう",
            })
            used_ids.add(w["word_id"])
            # チャレンジは最大2枠まで
            if sum(1 for s in steps if s["type"] == "challenge") >= 2:
                break
    except Exception:
        pass

    return steps[:MAX_STEPS]


def get_daily_lesson(words: list[dict]) -> dict:
    """今日のレッスンを返す（なければ編成して保存する）。

    Returns: {date, steps: [{..., done}], done_count, total, all_done}
    """
    today = date.today().isoformat()
    state = _load()

    if state.get("date") != today:
        state = {"date": today, "steps": _build_steps(words), "mouth_done": []}
        _save(state)

    # 録音ステップの完了を履歴から判定 / mouth はフラグから
    mouth_done = set(state.get("mouth_done", []))
    for s in state.get("steps", []):
        if s["kind"] == "mouth":
            s["done"] = s["word_id"] in mouth_done or "any" in mouth_done
        else:
            s["done"] = _practiced_today(s["word_id"])

    steps = state.get("steps", [])
    done_count = sum(1 for s in steps if s.get("done"))
    return {
        "date":       today,
        "steps":      steps,
        "done_count": done_count,
        "total":      len(steps),
        "all_done":   bool(steps) and done_count == len(steps),
    }


def mark_mouth_done(word_id: str) -> None:
    """母音トレーナー完走を今日のレッスンに反映する。"""
    today = date.today().isoformat()
    state = _load()
    if state.get("date") != today:
        return
    done = set(state.get("mouth_done", []))
    done.add(word_id)
    # どの単語でも mouth ステップを1つ完了扱いにする（母音が同じなら効果は同等）
    if any(s["kind"] == "mouth" for s in state.get("steps", [])):
        done.add("any")
    state["mouth_done"] = sorted(done)
    _save(state)
