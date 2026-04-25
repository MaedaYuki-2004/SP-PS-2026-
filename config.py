"""
config.py
パス定数・単語辞書・音素定数を一元管理するモジュール。
app.py や core/ 各モジュールはここからインポートする。
"""
from __future__ import annotations

import os
from pathlib import Path

# ── ベースディレクトリ ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

# ── Web 関連 ─────────────────────────────────────────────────────────
WEB_DIR        = BASE_DIR / "web"
TEMPLATES_DIR  = WEB_DIR / "templates"
STATIC_DIR     = WEB_DIR / "static"

# ── データ関連 ───────────────────────────────────────────────────────
DATA_DIR            = BASE_DIR / "data"
CONFIG_DIR          = DATA_DIR / "config"
RAW_AUDIO_DIR       = DATA_DIR / "raw_audio"
AUDIO_WAV_DIR       = RAW_AUDIO_DIR / "wav"
AUDIO_MFCC_DIR      = DATA_DIR / "mfcc"
DISTANCE_RESULT_DIR = STATIC_DIR / "distance_result"

# ── ファイルパス ─────────────────────────────────────────────────────
TEST_TXT_PATH         = AUDIO_WAV_DIR / "test.txt"
WORD_ID_MEMO_PATH     = CONFIG_DIR / "word_id.txt"
AUDIO_SCP_PATH        = CONFIG_DIR / "audio.scp"
WORDS_TXT_PATH        = CONFIG_DIR / "words.txt"
TEST_WAV_PATH         = AUDIO_WAV_DIR / "test.wav"
TEST_LAB_PATH         = AUDIO_WAV_DIR / "test.lab"
TEST_LOG_PATH         = AUDIO_WAV_DIR / "test.log"
TEST_SEGMENT_WAV_PATH = RAW_AUDIO_DIR / "test2.wav"

# ── エンジン / スクリプト ────────────────────────────────────────────
SCRIPTS_DIR      = BASE_DIR / "scripts"
PERL_SCRIPT_PATH = SCRIPTS_DIR / "segment_julius.pl"

# ── アップロード許可拡張子 ────────────────────────────────────────────
ALLOWED_EXTENSIONS = {".wav"}

# ── 単語辞書（word_id → ひらがな読み） ──────────────────────────────
WORDS: dict[str, str] = {
    "word1":  "おんど",       "word2":  "かいけー",     "word3":  "がっこー",
    "word4":  "ぎんこー",     "word5":  "こーえん",     "word6":  "こーつー",
    "word7":  "こーばい",     "word8":  "しごと",       "word9":  "しつど",
    "word10": "じどーしゃ",   "word11": "しゅーしょく", "word12": "しゅみ",
    "word13": "しょーめーしょ","word14": "しょくざい",  "word15": "すけじゅーる",
    "word16": "すーぱー",     "word17": "せーきゅーしょ","word18": "ぜーきん",
    "word19": "ちゅーしょく", "word20": "ちょーしょく", "word21": "ちょーみりょー",
    "word22": "ちょきん",     "word23": "でんしゃ",     "word24": "でんわ",
    "word25": "どーろ",       "word26": "びょーいん",   "word27": "びよーいん",
    "word28": "ほどー",       "word29": "ゆーしょく",   "word30": "やちん",
}

# ── 音素分類 ─────────────────────────────────────────────────────────
VOWELS: list[str] = ["a", "i", "u", "e", "o", "a:", "i:", "u:", "e:", "o:"]
CONSONANTS: list[str] = [
    "b", "c", "d", "f", "g", "h", "j", "k",
    "n", "m", "p", "r", "s", "t", "w", "y", "z",
]

# ── Flask シークレットキー ────────────────────────────────────────────
FLASK_SECRET_KEY: str = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")