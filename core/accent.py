"""
core/accent.py
MeCab によるアクセント型の自動取得を担当するモジュール。
"""
from __future__ import annotations

import csv
import threading

# MeCab はオプション依存。入っていない環境では
# accent=None / source="unknown" を返してアプリを継続動作させる。
try:
    import MeCab
    import unidic
    _MECAB_AVAILABLE = True
except Exception:
    _MECAB_AVAILABLE = False

# ── MeCab 初期化 ─────────────────────────────────────────────────────
_tagger = None
# サーバーは1つのプロセスで複数のスレッドが要求を受ける。MeCab の解析器は同時に使うと
# 安全とは限らないので、解析は1つずつ行う
_tagger_lock = threading.Lock()

def _get_tagger():
    """MeCab tagger のシングルトン。初回のみ初期化する。"""
    global _tagger
    if not _MECAB_AVAILABLE:
        return None
    if _tagger is None:
        try:
            dicdir = unidic.DICDIR
            _tagger = MeCab.Tagger(f'-d "{dicdir}"')
        except Exception:
            return None
    return _tagger


def _features(line: str) -> list[str]:
    """MeCab の1行（表層形 TAB 素性）から素性の並びを取り出す。

    UniDic の素性は CSV で、"B,B4WB7G9G"（「台」）や "0,2" のように、引用符の中にカンマを含む項目がある。
    カンマで単純に区切ると、それより後ろの項目の位置がずれて、読みやアクセント型をまちがえる。
    """
    try:
        return next(csv.reader([line.split("\t", 1)[1]]))
    except (IndexError, StopIteration, csv.Error):
        return []


# ── アクセント型取得 ──────────────────────────────────────────────────

def get_accent(display: str) -> tuple[int | None, str]:
    """
    表示テキスト（漢字・カタカナ・ひらがな）からアクセント型を取得する。

    Returns
    -------
    accent : int | None   – 0=平板型, 1=頭高型, N=N型。取得不可なら None。
    source : str          – "mecab" または "unknown"
    """
    tagger = _get_tagger()
    if tagger is None:
        return None, "unknown"

    try:
        with _tagger_lock:
            result = tagger.parse(display)
    except Exception:
        return None, "unknown"

    lines = [l for l in result.splitlines() if l and l != "EOS" and "\t" in l]

    if len(lines) != 1:
        return None, "unknown"

    features = _features(lines[0])
    if len(features) <= 24:
        return None, "unknown"

    # アクセント型（aType）。「0,2」のように複数あるときは最初の値を使う
    first = features[24].split(",")[0].strip()
    if first.isdigit():
        return int(first), "mecab"

    return None, "unknown"


# ── 読みがなの候補 ────────────────────────────────────────────────────

def _to_hiragana(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def _is_kana_reading(text: str) -> bool:
    return bool(text) and all("ぁ" <= c <= "ゖ" or c == "ー" for c in text)


def guess_reading(display: str) -> str | None:
    """
    表示テキストから、読みがな（ひらがな）の候補を返す。単語を登録する画面で、読みの欄に先に入れておくため。

    かな だけの単語は、カタカナをひらがなに直すだけ（「スーパー」→「すーぱー」）。
    漢字を含む単語は MeCab + UniDic の読みをつなげる。MeCab は「お母さん」を「おははさん」と
    読むようなまちがいもするので、あくまで候補として出し、画面で確かめて直してもらう。
    分からないとき（MeCab が無い・ひらがなにならない文字がある）は None。
    """
    text = (display or "").strip()
    if not text or len(text) > 40:
        return None
    kana = _to_hiragana(text)
    if _is_kana_reading(kana):
        return kana
    tagger = _get_tagger()
    if tagger is None:
        return None
    try:
        with _tagger_lock:
            result = tagger.parse(text)
    except Exception:
        return None
    parts = []
    for line in result.splitlines():
        if not line or line == "EOS" or "\t" not in line:
            continue
        surface = line.split("\t", 1)[0]
        f = _features(line)
        # UniDic：20 番目が書いてあるとおりの読み（仮名形）、6 番目が語彙素の読み
        kana_form = f[20] if len(f) > 20 and f[20] not in ("", "*") else (f[6] if len(f) > 6 else "")
        if not kana_form or kana_form == "*":
            kana_form = surface
        parts.append(_to_hiragana(kana_form))
    reading = "".join(parts)
    return reading if _is_kana_reading(reading) else None
