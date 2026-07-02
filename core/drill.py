"""
core/drill.py
スモールステップ練習（集中ドリル）と聞き分けテストのデータ生成。

言語聴覚士の構音指導の標準ステップ（ことたぶ等）に準拠：
  音単独 → 音節（のばす・くりかえす） → 語頭 → 語中・語尾 → 単語
"""
from __future__ import annotations

import random
from pathlib import Path

from config import STATIC_DIR, RAW_AUDIO_DIR
from core.weakness import kana_vowel
from core.history import load_history

# ── 行ごとの構音ヒント（子音の作り方） ────────────────────────────────
_ROW_HINTS: dict[str, str] = {
    "あ": "のどの奥から声をまっすぐ出します。舌は下げたままリラックス。",
    "か": "舌の奥を上あごの奥につけて、ぱっと離すと同時に息を出します。",
    "さ": "舌先を上の歯ぐきに近づけて、すき間から「スーッ」と細い息を出します。声はその後から。",
    "た": "舌先を上の歯ぐきにつけて、はじくように離した瞬間に声を出します。",
    "な": "舌先を上の歯ぐきにつけたまま、鼻から声を出します。",
    "は": "口は母音の形のまま、先に息だけを「ハッ」とやわらかく出します。",
    "ま": "唇をしっかり閉じて鼻から声を出し、唇を開くと同時に母音へつなげます。",
    "や": "「い」の口の形から、すばやく次の母音の口へ移ります。",
    "ら": "舌先で上の歯ぐきを軽く1回はじきます。強くつけすぎないのがコツ。",
    "わ": "「う」の小さく丸めた口から、すばやく「あ」の口へ開きます。",
    "が": "「か」と同じ舌の位置で、のどを震わせて（声を出しながら）発音します。",
    "ざ": "「さ」と同じ舌の位置で、のどを震わせながら息を出します。",
    "だ": "「た」と同じ舌の位置で、のどを震わせて発音します。",
    "ば": "唇を閉じてから、声と一緒に「バッ」と開きます。",
    "ぱ": "唇を閉じてから、息だけで「パッ」と勢いよく開きます。",
}

_ROWS: dict[str, str] = {
    "あ": "あいうえお",   "か": "かきくけこきゃきゅきょ", "さ": "さしすせそしゃしゅしょ",
    "た": "たちつてとちゃちゅちょ", "な": "なにぬねのにゃにゅにょ", "は": "はひふへほひゃひゅひょ",
    "ま": "まみむめもみゃみゅみょ", "や": "やゆよ", "ら": "らりるれろりゃりゅりょ",
    "わ": "わをん",
    "が": "がぎぐげごぎゃぎゅぎょ", "ざ": "ざじずぜぞじゃじゅじょ", "だ": "だぢづでど",
    "ば": "ばびぶべぼびゃびゅびょ", "ぱ": "ぱぴぷぺぽぴゃぴゅぴょ",
}


def kana_row(kana: str) -> str | None:
    """かなモーラの行（か行→'か'）を返す。"""
    if not kana:
        return None
    head = kana[0]
    for leader, members in _ROWS.items():
        if head in members:
            return leader
    return None


def get_articulation_hint(kana: str) -> str:
    row = kana_row(kana)
    return _ROW_HINTS.get(row or "", "お手本をよく聞いて、口の形を意識しながら発音しましょう。")


def _kana_positions(kana: str, reading: str) -> str | None:
    """単語の読みの中でのかなの位置: head / mid / tail / None"""
    if not reading or kana not in reading:
        return None
    idx = reading.index(kana)
    if idx == 0:
        return "head"
    if idx + len(kana) >= len(reading):
        return "tail"
    return "mid"


def get_kana_recent_avg(kana: str, recent: int = 30) -> float | None:
    """当該かなモーラの直近平均スコアを返す。"""
    vals: list[float] = []
    for rec in load_history()[:recent]:
        for m in rec.get("mora_scores", []):
            if m.get("kana") == kana and m.get("total") is not None:
                vals.append(float(m["total"]))
    return round(sum(vals) / len(vals), 1) if vals else None


def get_drill_data(kana: str, words: list[dict]) -> dict | None:
    """集中ドリルページ用データを返す。"""
    vowel = kana_vowel(kana)
    if not vowel:
        return None

    words_head, words_midtail = [], []
    for w in words:
        pos = _kana_positions(kana, w.get("reading", ""))
        if pos == "head" and len(words_head) < 4:
            words_head.append(w)
        elif pos in ("mid", "tail") and len(words_midtail) < 4:
            words_midtail.append(w)

    return {
        "kana":         kana,
        "vowel":        vowel,
        "hint":         get_articulation_hint(kana),
        "row":          kana_row(kana),
        "words_head":   words_head,
        "words_midtail": words_midtail,
        "recent_avg":   get_kana_recent_avg(kana),
    }


# ── 聞き分けテスト ────────────────────────────────────────────────────

def _has_audio(word_id: str) -> bool:
    if (STATIC_DIR / "sample" / f"{word_id}.wav").exists():
        return True
    if (RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.wav").exists():
        return True
    return False


def _edit_distance(a: str, b: str) -> int:
    la, lb = len(a), len(b)
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, lb + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1,
                        prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return dp[lb]


def build_listening_quiz(words: list[dict], n: int = 8, n_choices: int = 3) -> list[dict]:
    """聞き分けテストの問題リストを返す。

    音声のある単語から出題し、読みが似ている単語を選択肢に混ぜる
    （びょういん / びよういん のようなミニマルペアを優先）。
    """
    pool = [w for w in words if _has_audio(w["word_id"]) and w.get("reading")]
    if len(pool) < n_choices:
        return []

    random.shuffle(pool)
    questions = []
    for w in pool[:n]:
        # 読みの編集距離が近い順に紛らわしい選択肢を選ぶ
        # 同じ読み・同じ表記の単語（重複登録）は問題が成立しないので除外
        others = sorted(
            (o for o in words
             if o["word_id"] != w["word_id"] and o.get("reading")
             and o["reading"] != w["reading"]
             and o.get("display") != w.get("display")),
            key=lambda o: (_edit_distance(w["reading"], o["reading"]),
                           abs(len(w["reading"]) - len(o["reading"]))),
        )
        distractors = others[:n_choices - 1]
        if len(distractors) < n_choices - 1:
            continue
        choices = [{"word_id": c["word_id"], "display": c.get("display", ""),
                    "reading": c.get("reading", "")}
                   for c in [w] + distractors]
        random.shuffle(choices)
        questions.append({
            "audio":     f"/sample_audio/{w['word_id']}",
            "answer_id": w["word_id"],
            "choices":   choices,
        })
    return questions
