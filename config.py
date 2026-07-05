"""
config.py
パス定数・単語辞書・音素定数を一元管理するモジュール。
app.py や core/ 各モジュールはここからインポートする。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# ── ベースディレクトリ ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

# ── Web 関連 ─────────────────────────────────────────────────────────
WEB_DIR        = BASE_DIR / "web"
TEMPLATES_DIR  = WEB_DIR / "templates"
STATIC_DIR     = WEB_DIR / "static"

# ── データ関連 ───────────────────────────────────────────────────────
# Railway では環境変数 DATA_DIR=/app_data を設定し Volume をマウントする。
# ローカル開発時は BASE_DIR/data をそのまま使用。
_DATA_DIR_OVERRIDE = os.environ.get("DATA_DIR")
DATA_DIR            = Path(_DATA_DIR_OVERRIDE) if _DATA_DIR_OVERRIDE else BASE_DIR / "data"
CONFIG_DIR          = DATA_DIR / "config"
RAW_AUDIO_DIR       = DATA_DIR / "raw_audio"
AUDIO_WAV_DIR       = RAW_AUDIO_DIR / "wav"
AUDIO_MFCC_DIR      = DATA_DIR / "mfcc"
DISTANCE_RESULT_DIR = STATIC_DIR / "distance_result"

# ── エンジン関連（Julius本体・音響モデル） ────────────────────────────
# segment_julius.pl は ENGINE_DIR をカレントとして実行する。
# これにより ./models/ や（Windows時）./bin/ が正しく解決される。
ENGINE_DIR = BASE_DIR / "engine"

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

# ── Julius バイナリの自動検出 ─────────────────────────────────────────
# 優先順位：
#   1. 環境変数 JULIUS_BIN（手動設定）
#   2. Windows の場合は engine/bin/ 以下の exe を自動検出
#   3. Apple Silicon Mac の Homebrew パス
#   4. Intel Mac / Linux の Homebrew パス
#   5. PATH 検索（which julius）
#   6. 見つからない場合は None（Perl スクリプトのデフォルトに任せる）
def _detect_julius() -> str | None:
    if env := os.environ.get("JULIUS_BIN"):
        return env
    # Windows: engine/bin/ 以下の julius*.exe を自動検出
    if os.name == "nt":
        bin_dir = BASE_DIR / "engine" / "bin"
        if bin_dir.exists():
            for exe in bin_dir.glob("julius*.exe"):
                return str(exe)
    for candidate in [
        "/opt/homebrew/bin/julius",  # Apple Silicon Mac
        "/usr/local/bin/julius",     # Intel Mac / Linux
    ]:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("julius")
    return found  # 見つからない場合は None（Perl のデフォルトに任せる）

JULIUS_BIN_PATH: str | None = _detect_julius()

# ── アップロード許可拡張子 ────────────────────────────────────────────
ALLOWED_EXTENSIONS = {".wav"}

# ── 単語辞書（word_id → Julius読み） ─────────────────────────────────
# 【注意】Julius に渡すひらがな読みを格納する。
# 長音は「ー」で表記（Julius のひらがな→音素変換ルールに準拠）。
# 表示用の標準表記は data/config/words.txt で管理する（2つは別物）。
WORDS: dict[str, str] = {
    "word1":  "おんど",        "word2":  "かいけー",      "word3":  "がっこー",
    "word4":  "ぎんこー",      "word5":  "こーえん",      "word6":  "こーつー",
    "word7":  "こーばい",      "word8":  "しごと",        "word9":  "しつど",
    "word10": "じどーしゃ",    "word11": "しゅーしょく",  "word12": "しゅみ",
    "word13": "しょーめーしょ","word14": "しょくざい",    "word15": "すけじゅーる",
    "word16": "すーぱー",      "word17": "せーきゅーしょ","word18": "ぜーきん",
    "word19": "ちゅーしょく",  "word20": "ちょーしょく",  "word21": "ちょーみりょー",
    "word22": "ちょきん",      "word23": "でんしゃ",      "word24": "でんわ",
    "word25": "どーろ",        "word26": "びょーいん",    "word27": "びよーいん",
    "word28": "ほどー",        "word29": "ゆーしょく",    "word30": "やちん",
}

# ── 音素分類 ─────────────────────────────────────────────────────────
VOWELS: list[str] = ["a", "i", "u", "e", "o", "a:", "i:", "u:", "e:", "o:"]
CONSONANTS: list[str] = [
    "b", "c", "d", "f", "g", "h", "j", "k",
    "n", "m", "p", "r", "s", "t", "w", "y", "z",
]

# ── ピッチ解析範囲（Hz） ──────────────────────────────────────────────
# 話者の性別・属性に合わせて praat_pitch() に渡す。
# デフォルトは男女両方をカバーする保守的な範囲を使用。
PITCH_FLOOR_DEFAULT:   float = 70.0
PITCH_CEILING_DEFAULT: float = 400.0
PITCH_FLOOR_MALE:      float = 70.0
PITCH_CEILING_MALE:    float = 200.0
PITCH_FLOOR_FEMALE:    float = 150.0
PITCH_CEILING_FEMALE:  float = 400.0

# ── Flask シークレットキー ────────────────────────────────────────────
FLASK_SECRET_KEY: str = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

# ── 管理機能のパスワード ──────────────────────────────────────────────
# 設定すると単語の追加・削除・編集、お手本の差し替え、管理ページに
# ログインが必要になる。空（未設定）ならすべて従来どおり認証なし
# （ローカル開発向け）。公開デプロイでは必ず設定すること。
ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "")

