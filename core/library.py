"""
core/library.py
単語とお手本（音声・録画・アライメント結果・MFCC・口形データ）の置き場所。

ログイン中の参加者ごとに data/users/<user_id>/library/ を使う。
単語の一覧もお手本も参加者ごとに別々で、ほかの参加者からは見えない。
単語の ID（word1, word2, …）も参加者ごとに振る。

フォールバック:
  未ログイン（CLI スクリプトなど）のときは従来の共有の場所を返す。
  共有の単語を参加者へ移すには scripts/move_shared_library.py を使う。

構成（参加者ごと）:
  library/words_db.json       単語データベース
  library/audio.scp           お手本音声の一覧（互換のため。読み込みには使わない）
  library/words.txt           表示テキストの一覧
  library/sound/<word_id>/    お手本の wav・webm・lab・log・txt
  library/mfcc/<word_id>.bin  お手本の MFCC
  library/lip_refs.json       お手本の口形データ
  library/formant_cache.json  お手本のフォルマントのキャッシュ
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import (
    AUDIO_MFCC_DIR,
    AUDIO_SCP_PATH,
    BASE_DIR,
    CONFIG_DIR,
    DATA_DIR,
    RAW_AUDIO_DIR,
    WORDS_TXT_PATH,
)
from core import usercontext
from core.history import _safe_seg


@dataclass(frozen=True)
class Library:
    words_db:      Path
    audio_scp:     Path
    words_txt:     Path
    sound_root:    Path
    mfcc_dir:      Path
    lip_refs:      Path
    formant_cache: Path
    # 未ログイン時の共有ライブラリか。共有のときだけ web/static/sample の初期データも使う
    shared:        bool

    def sound_dir(self, word_id: str) -> Path:
        return self.sound_root / word_id

    def sample_wav(self, word_id: str) -> Path:
        return self.sound_dir(word_id) / f"{word_id}.wav"

    def sample_video(self, word_id: str) -> Path:
        return self.sound_dir(word_id) / f"{word_id}.webm"

    def mfcc_bin(self, word_id: str) -> Path:
        return self.mfcc_dir / f"{word_id}.bin"


SHARED_LIBRARY = Library(
    words_db=CONFIG_DIR / "words_db.json",
    audio_scp=AUDIO_SCP_PATH,
    words_txt=WORDS_TXT_PATH,
    sound_root=RAW_AUDIO_DIR / "sound",
    mfcc_dir=AUDIO_MFCC_DIR,
    lip_refs=CONFIG_DIR / "lip_refs.json",
    # 以前から DATA_DIR ではなくリポジトリ内の data/ に置いている（README §17）
    formant_cache=BASE_DIR / "data" / "config" / "formant_cache.json",
    shared=True,
)


def library_for(user_id: str) -> Library:
    root = DATA_DIR / "users" / _safe_seg(user_id) / "library"
    return Library(
        words_db=root / "words_db.json",
        audio_scp=root / "audio.scp",
        words_txt=root / "words.txt",
        sound_root=root / "sound",
        mfcc_dir=root / "mfcc",
        lip_refs=root / "lip_refs.json",
        formant_cache=root / "formant_cache.json",
        shared=False,
    )


def current_library() -> Library:
    """ログイン中の参加者の単語とお手本。未ログインなら共有の場所。"""
    uid = usercontext.current_user()
    return library_for(uid) if uid else SHARED_LIBRARY
