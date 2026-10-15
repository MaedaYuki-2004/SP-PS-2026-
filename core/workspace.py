"""
core/workspace.py
直前の練習の作業ファイル（選んだ単語・録音・アライメント結果・切り出し音声・口の動画）の置き場所。

なぜ必要か:
  以前は全員が data/raw_audio/wav/test.wav や data/config/word_id.txt を共有していたため、
  ある参加者が単語を選んでから「結果を見る」までの間に別の参加者が録音すると、
  録音・単語・口の動画が入れ替わって採点されていた（README §5.2）。
  ログイン中の参加者ごとに data/users/<user_id>/work/ を使い、互いに上書きしないようにする。

フォールバック:
  未ログイン（CLI スクリプトなど）のときは従来の共有パスを返す。

構成:
  work/word_id.txt   選んだ単語 ID
  work/align/        Julius 用。test.wav・test.txt → test.lab・test.log
                     segment_julius.pl はこのフォルダの *.wav をすべて処理するので、他の wav は置かない
  work/test2.wav     発話区間の切り出し（音色評価用）
  work/lip/          口の動画の一時ファイル（解析後に削除）
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from config import (
    AUDIO_WAV_DIR,
    DATA_DIR,
    TEST_SEGMENT_WAV_PATH,
    WORD_ID_MEMO_PATH,
)
from core import usercontext
from core.history import _safe_seg
from core.storage import write_text_atomic


@dataclass(frozen=True)
class Workspace:
    word_id_path: Path
    align_dir:    Path
    segment_wav:  Path
    lip_dir:      Path

    @property
    def wav(self) -> Path:
        return self.align_dir / "test.wav"

    @property
    def txt(self) -> Path:
        return self.align_dir / "test.txt"

    @property
    def lab(self) -> Path:
        return self.align_dir / "test.lab"

    @property
    def log(self) -> Path:
        return self.align_dir / "test.log"

    def read_word_id(self) -> str:
        try:
            return self.word_id_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def select_word(self, word_id: str, reading: str) -> None:
        """練習する単語を記録する（録音ページを開くときに呼ぶ）。"""
        write_text_atomic(self.word_id_path, word_id)
        write_text_atomic(self.txt, reading)


def clear_lip_dir(user_id: str) -> int:
    """その参加者の口の動画の一時ファイル（work/lip/）を全部消す。消した数を返す。

    口の動画は解析（POST /graph）に使ったあと消すが、「結果を見る」を押さないままセッションが
    切れると残ってしまう。ログインのときに呼んで、前のセッションの残りを消す。
    """
    lip_dir = DATA_DIR / "users" / _safe_seg(user_id) / "work" / "lip"
    removed = 0
    if lip_dir.is_dir():
        for p in lip_dir.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def current_workspace() -> Workspace:
    """ログイン中の参加者の作業フォルダ。未ログインなら従来の共有パス。"""
    uid = usercontext.current_user()
    if not uid:
        return Workspace(
            word_id_path=WORD_ID_MEMO_PATH,
            align_dir=AUDIO_WAV_DIR,
            segment_wav=TEST_SEGMENT_WAV_PATH,
            lip_dir=Path(tempfile.gettempdir()),
        )
    root = DATA_DIR / "users" / _safe_seg(uid) / "work"
    ws = Workspace(
        word_id_path=root / "word_id.txt",
        align_dir=root / "align",
        segment_wav=root / "test2.wav",
        lip_dir=root / "lip",
    )
    ws.align_dir.mkdir(parents=True, exist_ok=True)
    ws.lip_dir.mkdir(parents=True, exist_ok=True)
    return ws
