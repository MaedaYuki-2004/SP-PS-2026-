"""
core/accent.py
MeCab によるアクセント型の自動取得と
VOICEVOX による基準音声の自動生成を担当するモジュール。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

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


# ── VOICEVOX 音声生成 ─────────────────────────────────────────────────

VOICEVOX_URL = "http://127.0.0.1:50021"
VOICEVOX_SPEAKER = 11  # ずんだもん（変更可）


def is_voicevox_running() -> bool:
    """VOICEVOX が起動しているか確認する。"""
    try:
        urllib.request.urlopen(f"{VOICEVOX_URL}/version", timeout=2)
        return True
    except Exception:
        return False


def generate_sample_wav(display: str, out_path: str | Path) -> None:
    """
    VOICEVOX を使って display のテキストを音声合成し WAV を保存する。

    Parameters
    ----------
    display  : 発音させるテキスト（漢字・カタカナ・ひらがな）
    out_path : 保存先パス（16kHz に変換してから保存される）
    """
    out_path = Path(out_path)

    # audio_query（POST）
    query_url = (
        f"{VOICEVOX_URL}/audio_query"
        f"?text={urllib.parse.quote(display)}"
        f"&speaker={VOICEVOX_SPEAKER}"
    )
    req = urllib.request.Request(query_url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as res:
        query = json.loads(res.read().decode("utf-8"))

    # サンプリングレートを 16kHz に指定
    query["outputSamplingRate"] = 16000
    query["outputStereo"]       = False

    # synthesis（POST）
    data = json.dumps(query).encode("utf-8")
    req  = urllib.request.Request(
        f"{VOICEVOX_URL}/synthesis?speaker={VOICEVOX_SPEAKER}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        wav_data = res.read()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(wav_data)