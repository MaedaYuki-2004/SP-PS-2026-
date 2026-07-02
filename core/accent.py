"""
core/accent.py
MeCab によるアクセント型の自動取得を担当するモジュール。
（VOICEVOX 音声生成は廃止）
"""
from __future__ import annotations

# MeCab はオプション依存。Railway など環境によって利用不可の場合は
# accent=None / source="unknown" を返してアプリを継続動作させる。
try:
    import MeCab
    import unidic
    _MECAB_AVAILABLE = True
except Exception:
    _MECAB_AVAILABLE = False

# ── MeCab 初期化 ─────────────────────────────────────────────────────
_tagger = None

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
        result = tagger.parse(display)
    except Exception:
        return None, "unknown"

    lines = [l for l in result.splitlines() if l and l != "EOS" and "\t" in l]

    if len(lines) != 1:
        return None, "unknown"

    features = lines[0].split("\t")[1].split(",")
    if len(features) <= 24:
        return None, "unknown"

    accent_str = features[24].strip().strip('"')
    first = accent_str.split(",")[0].strip()
    if first.isdigit():
        return int(first), "mecab"

    return None, "unknown"
