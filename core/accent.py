"""
core/accent.py
MeCab によるアクセント型の自動取得を担当するモジュール。
（VOICEVOX 音声生成は廃止）
"""
from __future__ import annotations

import MeCab
import unidic

# ── MeCab 初期化 ─────────────────────────────────────────────────────
_tagger: MeCab.Tagger | None = None

def _get_tagger() -> MeCab.Tagger:
    """MeCab tagger のシングルトン。初回のみ初期化する。"""
    global _tagger
    if _tagger is None:
        dicdir = unidic.DICDIR
        _tagger = MeCab.Tagger(f'-d "{dicdir}"')
    return _tagger


# ── アクセント型取得 ──────────────────────────────────────────────────

def get_accent(display: str) -> tuple[int | None, str]:
    """
    表示テキスト（漢字・カタカナ・ひらがな）から
    アクセント型を取得する。

    Returns
    -------
    accent : int | None
        アクセント型（0=平板型, 1=頭高型, N=N型）。取得できなければ None。
    source : str
        "mecab" または "unknown"
    """
    tagger = _get_tagger()
    result = tagger.parse(display)
    lines  = [
        l for l in result.splitlines()
        if l and l != "EOS" and "\t" in l
    ]

    # 1形態素として認識された場合のみアクセントを採用
    if len(lines) != 1:
        return None, "unknown"

    features = lines[0].split("\t")[1].split(",")
    # UniDic の25番目フィールドがアクセント型
    if len(features) <= 24:
        return None, "unknown"

    accent_str = features[24].strip().strip('"')

    # 複数アクセント型（例："1,0"）の場合は最初の値を使う
    first = accent_str.split(",")[0].strip()
    if first.isdigit():
        return int(first), "mecab"

    return None, "unknown"
