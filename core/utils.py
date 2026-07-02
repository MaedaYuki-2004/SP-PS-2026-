"""
core/utils.py
汎用ユーティリティ関数群。
特定の解析ドメインに依存しない小さな処理をまとめる。
"""
from __future__ import annotations

import time
from pathlib import Path

from config import ALLOWED_EXTENSIONS


def allowed_file(filename: str) -> bool:
    """アップロードされたファイルの拡張子が許可リストに含まれるか確認する。"""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def sleep_second(seconds: float = 1.5) -> None:
    """指定秒数だけスリープする（Julius 処理待ち用）。"""
    time.sleep(seconds)


def pct_length(length: list[float]) -> list[float]:
    """各要素が合計に占める割合（%）を返す。"""
    total = sum(length)
    if total == 0:
        return [0.0 for _ in length]
    return [round((i / total) * 100, 2) for i in length]


def phone_list(frame: list[int | str]) -> list[list[int | str]]:
    """フラットなフレームリストを [start, end, phoneme] の3要素単位に分割する。"""
    return [frame[i:i + 3] for i in range(0, len(frame), 3)]


def phoneme_frame(phoneme: list[list[int | str]]) -> list[list[int | str]]:
    """音素フレームの開始・終了を先頭音素基準の相対フレーム番号に正規化する。"""
    if not phoneme:
        return phoneme
    start = int(phoneme[0][0])
    for item in phoneme:
        item[0] = int(item[0]) - start
        item[1] = int(item[1]) - start
    return phoneme

# ── ローマ字モーラ → かな変換 ─────────────────────────────────────────
# Julius の .lab 由来モーララベル（"sa", "shi", "kya", "N", "q", "ka:" 等）を
# ひらがな表記に変換する。苦手音分析・クエスト表示で使用。

_ROMAJI_ROWS: dict[str, str] = {
    "":   "あいうえお",  "k":  "かきくけこ",  "g":  "がぎぐげご",
    "s":  "さすすせそ",  "z":  "ざずずぜぞ",  "t":  "たちつてと",
    "d":  "だぢづでど",  "n":  "なにぬねの",  "h":  "はひふへほ",
    "b":  "ばびぶべぼ",  "p":  "ぱぴぷぺぽ",  "m":  "まみむめも",
    "r":  "らりるれろ",  "w":  "わゐうゑを",  "y":  "や ゆ よ",
    "f":  "ふぁふぃふふぇふぉ",
}

_ROMAJI_SPECIAL: dict[str, str] = {
    # 拗音・特殊モーラ
    "shi": "し", "chi": "ち", "tsu": "つ", "ji": "じ", "fu": "ふ",
    "sha": "しゃ", "shu": "しゅ", "sho": "しょ", "she": "しぇ",
    "cha": "ちゃ", "chu": "ちゅ", "cho": "ちょ", "che": "ちぇ",
    "ja":  "じゃ", "ju":  "じゅ", "jo":  "じょ", "je":  "じぇ",
    "kya": "きゃ", "kyu": "きゅ", "kyo": "きょ",
    "gya": "ぎゃ", "gyu": "ぎゅ", "gyo": "ぎょ",
    "nya": "にゃ", "nyu": "にゅ", "nyo": "にょ",
    "hya": "ひゃ", "hyu": "ひゅ", "hyo": "ひょ",
    "bya": "びゃ", "byu": "びゅ", "byo": "びょ",
    "pya": "ぴゃ", "pyu": "ぴゅ", "pyo": "ぴょ",
    "mya": "みゃ", "myu": "みゅ", "myo": "みょ",
    "rya": "りゃ", "ryu": "りゅ", "ryo": "りょ",
    "N": "ん", "q": "っ", "sp": "", "silB": "", "silE": "",
}

_VOWEL_INDEX = {"a": 0, "i": 1, "u": 2, "e": 3, "o": 4}


def romaji_mora_to_kana(label: str) -> str:
    """ローマ字モーララベルをひらがなに変換する。変換不能ならそのまま返す。"""
    if not label:
        return label
    long_mark = ""
    base = label
    if base.endswith(":"):
        base = base[:-1]
        long_mark = "ー"

    if base in _ROMAJI_SPECIAL:
        return _ROMAJI_SPECIAL[base] + long_mark

    if len(base) >= 1 and base[-1] in _VOWEL_INDEX:
        cons, vowel = base[:-1], base[-1]
        row = _ROMAJI_ROWS.get(cons)
        if row:
            kana = row[_VOWEL_INDEX[vowel]]
            if kana != " ":
                return kana + long_mark
    return label
