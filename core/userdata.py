"""
core/userdata.py
単語・お手本・録音の作業ファイルを「どの生徒のものか」で切り替えるパスの窓口。

なぜ必要か:
  ろう学校の調査では、生徒が各自の家で使い、保護者が家でお手本を録ったり単語を
  追加したりする。全家庭が同じサーバーにつなぐので、単語やお手本を 1 か所に置くと
  ある家庭の変更が他の家庭の生徒にも見えてしまう。そこで、単語に関わるファイルを
  生徒ごとのフォルダに分けて置く（家庭ごとに完全に別。共通の単語は持たない）。

  録音のたびに上書きする作業ファイル（test.wav・word_id.txt など）も生徒ごとに分ける。
  1 か所だと、2 つの家庭が同時に録音したときに互いの録音を上書きしてしまうため。

誰のデータか:
  core/usercontext.current_user() の ID（app.py の before_request が入れる）。
  None のとき（未ログイン・CLI スクリプト）は、従来の共有パス（data/config/ 等）を使う。

生徒ごとのフォルダ（data/users/<ID>/）:
  words_db.json  words.txt  audio.scp  lip_refs.json   … 単語とお手本の口形データ
  sound/<word_id>/<word_id>.wav|.lab|.log|.webm|.txt   … お手本の音声・動画とアライメント
  mfcc/<word_id>.bin                                     … お手本の MFCC
  work/wav/test.wav|.txt|.lab|.log  work/test2.wav  work/word_id.txt … 録音の作業ファイル
  （history.json・daily_lesson.json も同じフォルダ。core/history.py・core/lesson.py）
"""
from __future__ import annotations

from pathlib import Path

from config import (
    AUDIO_MFCC_DIR,
    AUDIO_SCP_PATH,
    AUDIO_WAV_DIR,
    CONFIG_DIR,
    DATA_DIR,
    RAW_AUDIO_DIR,
    TEST_SEGMENT_WAV_PATH,
    WORD_ID_MEMO_PATH,
    WORDS_TXT_PATH,
)
from core import usercontext

USERS_DIR = DATA_DIR / "users"


def safe_seg(user_id: str) -> str:
    """user_id をディレクトリ名として安全な 1 セグメントに整える。

    accounts 側で ^[A-Za-z][A-Za-z0-9_-]{0,31}$ に検証済みだが、
    パス組み立て前の多層防御としてセパレータ等を除去する。
    """
    seg = str(user_id).strip().replace("\\", "").replace("/", "")
    if seg in ("", ".", "..") or ".." in seg:
        raise ValueError(f"不正な利用者IDです: {user_id!r}")
    return seg


def user_dir(user_id: str) -> Path:
    return USERS_DIR / safe_seg(user_id)


def _owner_dir() -> Path | None:
    """いまのリクエストの生徒のフォルダ。未ログインなら None（共有パスを使う）。"""
    uid = usercontext.current_user()
    return user_dir(uid) if uid else None


def is_shared() -> bool:
    """従来の共有データ（生徒に分ける前のもの）を使っているか。"""
    return _owner_dir() is None


# ── 単語とお手本 ────────────────────────────────────────────────────

def words_db_path() -> Path:
    d = _owner_dir()
    return d / "words_db.json" if d else CONFIG_DIR / "words_db.json"


def words_txt_path() -> Path:
    d = _owner_dir()
    return d / "words.txt" if d else WORDS_TXT_PATH


def audio_scp_path() -> Path:
    d = _owner_dir()
    return d / "audio.scp" if d else AUDIO_SCP_PATH


def lip_refs_path() -> Path:
    d = _owner_dir()
    return d / "lip_refs.json" if d else CONFIG_DIR / "lip_refs.json"


def sound_root() -> Path:
    d = _owner_dir()
    return d / "sound" if d else RAW_AUDIO_DIR / "sound"


def sound_dir(word_id: str) -> Path:
    """お手本の音声・動画・アライメント結果を置くフォルダ。"""
    return sound_root() / safe_seg(word_id)


def mfcc_dir() -> Path:
    d = _owner_dir()
    return d / "mfcc" if d else AUDIO_MFCC_DIR


def mfcc_path(word_id: str) -> Path:
    return mfcc_dir() / f"{safe_seg(word_id)}.bin"


# ── 録音の作業ファイル（録音のたびに上書き） ─────────────────────────
# Julius の segment_julius.pl はフォルダ内の *.wav をすべて処理するので、
# work_wav_dir() には test.wav 以外の wav を置かない（切り出し後の test2.wav は外に置く）。

def work_wav_dir() -> Path:
    d = _owner_dir()
    path = d / "work" / "wav" if d else AUDIO_WAV_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_wav_path() -> Path:
    return work_wav_dir() / "test.wav"


def test_txt_path() -> Path:
    return work_wav_dir() / "test.txt"


def test_lab_path() -> Path:
    return work_wav_dir() / "test.lab"


def test_log_path() -> Path:
    return work_wav_dir() / "test.log"


def segment_wav_path() -> Path:
    d = _owner_dir()
    if not d:
        return TEST_SEGMENT_WAV_PATH
    path = d / "work" / "test2.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def word_id_memo_path() -> Path:
    d = _owner_dir()
    if not d:
        return WORD_ID_MEMO_PATH
    path = d / "work" / "word_id.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
