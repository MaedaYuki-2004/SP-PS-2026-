"""
core/quest.py
クエスト生成・保存・自動クリア判定を担当するモジュール。

【追加：間隔反復クエスト（12）】
  generate_new_quests() 内で間隔反復の候補を確認し、
  3日以上練習していない単語があればその復習クエストを優先的に生成する。

  【間隔反復の仕組み】
  1. check_and_update_quests() でクリア済みクエストのスロットが空く
  2. fill で新しいクエストを生成する際、まず get_spaced_repetition_candidates() を確認
  3. 3日以上練習していない単語（スコア85点未満）があれば「復習クエスト」を最優先で生成
  4. なければ通常の弱点ベースのクエストを生成する

  復習クエストは category="total"・特別な title・hint で区別できる。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from config import DATA_DIR

QUEST_PROGRESS_PATH = DATA_DIR / "config" / "quest_progress.json"
MAX_ACTIVE_QUESTS   = 3


@dataclass
class Quest:
    quest_id:      str
    title:         str
    description:   str
    hint:          str
    category:      str   # accent / length / vowel / total / review
    difficulty:    str   # easy / normal / hard
    target_metric: str
    target_value:  float
    start_value:   float
    word_id:       str
    created_at:    str
    is_completed:  bool = False
    completed_at:  str  = ""

    def progress_pct(self, current_value: float) -> float:
        span = self.target_value - self.start_value
        if span <= 0:
            return 100.0
        pct = (current_value - self.start_value) / span * 100
        return max(0.0, min(100.0, round(pct, 1)))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Quest":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── 保存・読み込み ────────────────────────────────────────────────────

def _load_raw() -> dict:
    if not QUEST_PROGRESS_PATH.exists():
        return {"active": [], "completed": []}
    try:
        with QUEST_PROGRESS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active": [], "completed": []}


def _save_raw(data: dict) -> None:
    QUEST_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEST_PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(QUEST_PROGRESS_PATH)


def load_active_quests() -> list[Quest]:
    data = _load_raw()
    result = []
    for d in data.get("active", []):
        try:
            result.append(Quest.from_dict(d))
        except Exception:
            pass
    return result


def _save_quests(active: list[Quest], completed: list[Quest]) -> None:
    data = _load_raw()
    existing_completed = data.get("completed", [])
    new_completed = [q.to_dict() for q in completed] + existing_completed
    _save_raw({
        "active":    [q.to_dict() for q in active],
        "completed": new_completed[:50],
    })


# ── クエスト生成ロジック ──────────────────────────────────────────────

def _make_quest_id() -> str:
    return f"q_{time.time_ns()}"


def _accent_quest(score: float, accent_label: str, accent_fb: str, word_id: str) -> Quest:
    now = datetime.now().isoformat()
    if score < 20:
        target = min(50.0, score + 12.0)
        return Quest(quest_id=_make_quest_id(),
                     title=f"アクセント（{accent_label}）を習得しよう",
                     description=f"アクセントスコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint=f"サンプル音声を5回聞いてから、ピッチを大げさに動かしてゆっくり発音してみよう。アクセントの型（{accent_label}）を意識して！",
                     category="accent", difficulty="hard",
                     target_metric="accent_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)
    elif score < 35:
        target = min(50.0, score + 8.0)
        return Quest(quest_id=_make_quest_id(),
                     title="ピッチの上げ下げをはっきりさせよう",
                     description=f"アクセントスコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint=f"{accent_fb[:40] if accent_fb else 'ピッチの上げ下げを意識して'}。サンプル音声と自分の声を聞き比べよう。",
                     category="accent", difficulty="normal",
                     target_metric="accent_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)
    else:
        target = min(50.0, score + 5.0)
        return Quest(quest_id=_make_quest_id(),
                     title="アクセントを完璧に仕上げよう",
                     description=f"アクセントスコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint="ピッチが下がるタイミングをネイティブに合わせよう。あと少し！",
                     category="accent", difficulty="easy",
                     target_metric="accent_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)


def _length_quest(score: float, length_fb: str, word_id: str) -> Quest:
    now = datetime.now().isoformat()
    if score < 12:
        target = min(30.0, score + 7.0)
        return Quest(quest_id=_make_quest_id(),
                     title="リズムをネイティブに合わせよう",
                     description=f"長さスコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint="手拍子に合わせて1モーラずつ発音する練習をしよう。長音（ー）は2拍分、促音（っ）は1拍分の間を意識して！",
                     category="length", difficulty="hard",
                     target_metric="length_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)
    elif score < 22:
        target = min(30.0, score + 5.0)
        return Quest(quest_id=_make_quest_id(),
                     title="モーラの長さを整えよう",
                     description=f"長さスコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint=f"{length_fb[:40] if length_fb else '長さのバランスを意識して'}。サンプル音声を聞いてリズムを把握してから録音しよう。",
                     category="length", difficulty="normal",
                     target_metric="length_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)
    else:
        target = min(30.0, score + 3.0)
        return Quest(quest_id=_make_quest_id(),
                     title="リズムを完璧に仕上げよう",
                     description=f"長さスコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint="微妙なリズムのズレを確認してもう一息！",
                     category="length", difficulty="easy",
                     target_metric="length_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)


def _vowel_quest(score: float, vowel_fb: str, word_id: str) -> Quest:
    now = datetime.now().isoformat()
    if score < 8:
        target = min(20.0, score + 5.0)
        return Quest(quest_id=_make_quest_id(),
                     title="口の形を意識して発音しよう",
                     description=f"母音スコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint="鏡や手鏡で口の形を確認しながら発音しよう。「あ」は口を大きく、「い」は横に広げて！",
                     category="vowel", difficulty="hard",
                     target_metric="vowel_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)
    elif score < 14:
        target = min(20.0, score + 4.0)
        return Quest(quest_id=_make_quest_id(),
                     title="母音の口の形を整えよう",
                     description=f"母音スコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint=f"{vowel_fb[:40] if vowel_fb else '口の形を意識して'}。各母音の形を大げさに作ってからゆっくり発音しよう。",
                     category="vowel", difficulty="normal",
                     target_metric="vowel_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)
    else:
        target = min(20.0, score + 2.0)
        return Quest(quest_id=_make_quest_id(),
                     title="母音の精度を上げよう",
                     description=f"母音スコアを現在の {score:.0f}点 から {target:.0f}点 以上に上げよう。",
                     hint="あと少し！口の形をもう少しはっきり作ってみよう。",
                     category="vowel", difficulty="easy",
                     target_metric="vowel_score", target_value=target, start_value=score,
                     word_id=word_id, created_at=now)


def _spaced_repetition_quest(rec: dict) -> Quest:
    """
    間隔反復クエストを生成する。

    【仕組み】
    3日以上練習していない単語を「復習」として提案する。
    前回スコアを start_value とし、+8点を目標とする。
    category = "review" で通常クエストと区別できる。
    """
    word_id   = rec.get("word_id", "")
    display   = rec.get("display", word_id)
    days_ago  = float(rec.get("days_ago", 3.0))
    prev_total = float(rec.get("total") or 0)
    target    = min(100.0, prev_total + 8.0)

    if days_ago >= 7:
        days_str = f"{days_ago:.0f}日"
        hint_prefix = "かなり久しぶりの練習です。"
    elif days_ago >= 3:
        days_str = f"{days_ago:.0f}日"
        hint_prefix = "少し間が空きました。"
    else:
        days_str = f"{days_ago:.1f}日"
        hint_prefix = ""

    return Quest(
        quest_id=_make_quest_id(),
        title=f"「{display}」を復習しよう",
        description=(
            f"{days_str}前の練習で {prev_total:.0f}点 でした。"
            f"もう一度練習して {target:.0f}点 以上を目指しましょう。"
        ),
        hint=(
            f"{hint_prefix}まずサンプル音声を聞いてから録音してみましょう。"
            f"前回の結果を参考に、弱点を意識して練習してください。"
        ),
        category="review",
        difficulty="normal" if prev_total >= 60 else "hard",
        target_metric="total",
        target_value=target,
        start_value=prev_total,
        word_id=word_id,
        created_at=datetime.now().isoformat(),
    )


def generate_new_quests(
    score_result: dict,
    word_id: str,
    n: int = MAX_ACTIVE_QUESTS,
) -> list[Quest]:
    """
    スコア結果から新しいクエストを n 個生成して返す。

    【間隔反復の優先順位】
    1. 間隔反復候補（3日以上練習していない単語）があれば最大1つを復習クエストとして追加
    2. 残りのスロットを現在の弱点ベースのクエストで埋める

    これにより、常に「今の弱点改善」と「過去の定着確認」がバランスよく提案される。
    """
    from core.history import get_spaced_repetition_candidates

    accent_score  = float(score_result.get("accent_score", 0) or 0)
    length_score  = float(score_result.get("length_score", 0) or 0)
    vowel_score   = float(score_result.get("vowel_score",  0) or 0)
    accent_label  = score_result.get("accent_label",    "") or ""
    accent_fb     = score_result.get("accent_feedback", "") or ""
    length_fb     = score_result.get("length_feedback", "") or ""
    vowel_fb      = score_result.get("vowel_feedback",  "") or ""

    quests: list[Quest] = []

    # ── 間隔反復クエストを最大1つ追加（12）────────────────────────────
    try:
        sr_candidates = get_spaced_repetition_candidates(min_days=3.0, max_score=85.0)
        # 現在練習中の単語は除外
        sr_candidates = [c for c in sr_candidates if c.get("word_id") != word_id]
        if sr_candidates:
            quests.append(_spaced_repetition_quest(sr_candidates[0]))
    except Exception:
        pass

    # ── 弱点ベースのクエストで残りスロットを埋める ───────────────────
    remaining = n - len(quests)
    axis_scores = {
        "accent": accent_score / 50.0 * 100,
        "length": length_score / 30.0 * 100,
        "vowel":  vowel_score  / 20.0 * 100,
    }
    sorted_axes = sorted(axis_scores, key=axis_scores.get)

    for axis in sorted_axes[:remaining]:
        if axis == "accent":
            quests.append(_accent_quest(accent_score, accent_label, accent_fb, word_id))
        elif axis == "length":
            quests.append(_length_quest(length_score, length_fb, word_id))
        elif axis == "vowel":
            quests.append(_vowel_quest(vowel_score, vowel_fb, word_id))

    return quests[:n]


# ── クリア判定・更新 ──────────────────────────────────────────────────

def check_and_update_quests(
    score_result: dict,
    word_id: str,
) -> tuple[list[Quest], list[Quest], list[Quest]]:
    """
    アクティブなクエストをチェックし、クリア判定を行う。
    空きスロットに新しいクエスト（間隔反復含む）を補充する。
    """
    now              = datetime.now().isoformat()
    active           = load_active_quests()
    newly_completed: list[Quest] = []
    still_active:    list[Quest] = []

    for quest in active:
        current = float(score_result.get(quest.target_metric, 0) or 0)
        if current >= quest.target_value:
            quest.is_completed = True
            quest.completed_at = now
            newly_completed.append(quest)
        else:
            still_active.append(quest)

    # 空きスロットを補充
    n_fill = MAX_ACTIVE_QUESTS - len(still_active)
    if n_fill > 0:
        new_quests = generate_new_quests(score_result, word_id, n=n_fill)
        still_active.extend(new_quests)

    _save_quests(still_active, newly_completed)
    return newly_completed, still_active, still_active


def initialize_quests(score_result: dict, word_id: str) -> list[Quest]:
    """初回（クエストがない場合）に新規生成して保存する。"""
    active = load_active_quests()
    if active:
        return active
    new_quests = generate_new_quests(score_result, word_id, n=MAX_ACTIVE_QUESTS)
    _save_quests(new_quests, [])
    return new_quests