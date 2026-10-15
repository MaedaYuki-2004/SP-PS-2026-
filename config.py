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

# ── エンジン関連（Julius本体・音響モデル） ────────────────────────────
# segment_julius.pl は ENGINE_DIR をカレントとして実行する。
# これにより ./models/ や（Windows時）./bin/ が正しく解決される。
ENGINE_DIR = BASE_DIR / "engine"

# ── ファイルパス ─────────────────────────────────────────────────────
WORD_ID_MEMO_PATH     = CONFIG_DIR / "word_id.txt"
AUDIO_SCP_PATH        = CONFIG_DIR / "audio.scp"
WORDS_TXT_PATH        = CONFIG_DIR / "words.txt"
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

# ── 音素分類 ─────────────────────────────────────────────────────────
VOWELS: list[str] = ["a", "i", "u", "e", "o", "a:", "i:", "u:", "e:", "o:"]
CONSONANTS: list[str] = [
    "b", "c", "d", "f", "g", "h", "j", "k",
    "n", "m", "p", "r", "s", "t", "w", "y", "z",
]

# ── ピッチ解析範囲（Hz） ──────────────────────────────────────────────
# 話者ごとの範囲は estimate_pitch_range() が録音から推定する。
# 推定できないとき（有声の部分が短いとき）は、男女両方をカバーするこの範囲を使う。
PITCH_FLOOR_DEFAULT:   float = 70.0
PITCH_CEILING_DEFAULT: float = 400.0

# ── Flask シークレットキー ────────────────────────────────────────────
# ログイン状態と先生モードは、この鍵で署名したセッション Cookie に入っている。
# 以前はリポジトリに書かれた固定の文字列を既定値にしていたため、鍵を知っていれば
# Cookie を偽造して他人になりすましたり、先生モードにしたりできた。
# 環境変数が無いときは、ランダムな鍵を作って data/config/ に保存する（data/ は Git 管理外）。
def _load_or_create_secret_key() -> str:
    env = os.environ.get("FLASK_SECRET_KEY")
    if env:
        return env
    path = DATA_DIR / "config" / "flask_secret_key.txt"
    try:
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass
    import secrets
    import time
    key = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # gunicorn は複数のワーカー（プロセス）を同時に起動する。それぞれが別の鍵を作ると、
        # ログインがワーカーごとに通じたり通じなかったりするので、最初に作れた1つの鍵だけを使う
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
    except FileExistsError:
        # ほかのワーカーが先に作った。書き終わるのを待って、その鍵を使う
        for _ in range(50):
            try:
                existing = path.read_text(encoding="utf-8").strip()
                if existing:
                    return existing
            except OSError:
                pass
            time.sleep(0.1)
    except OSError:
        pass  # 保存できない環境でも起動はする（再起動するとログインし直しになる）
    return key


FLASK_SECRET_KEY: str = _load_or_create_secret_key()

