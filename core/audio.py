"""
core/audio.py
音声ファイルの変換・前処理を担当するモジュール。
フォーマット変換、お手本音声の場所の取得、セグメント切り出しをまとめる。
"""
from __future__ import annotations

from pathlib import Path

from pydub import AudioSegment

from config import (
    AUDIO_SCP_PATH,
    RAW_AUDIO_DIR,
    TEST_SEGMENT_WAV_PATH,
)
from core.library import current_library


def read_sample(word_id: str) -> str:
    """
    word_id に対応するお手本音声の絶対パスを返す。
    ログイン中は参加者のライブラリの sound/{word_id}/{word_id}.wav、見つからなければ IndexError。
    未ログイン（CLI から共有の単語を扱うとき）は audio.scp から探す。
    行の形式は "<tab区切り行番号><tab>path" または "path" の両方に対応。
    """
    lib = current_library()
    if not lib.shared:
        wav = lib.sample_wav(word_id)
        if not wav.exists():
            raise IndexError(f"{word_id} のお手本音声が見つかりません")
        return str(wav.resolve())

    if not AUDIO_SCP_PATH.exists():
        raise FileNotFoundError(f"audio.scp が見つかりません: {AUDIO_SCP_PATH}")

    lines = AUDIO_SCP_PATH.read_text(encoding="utf-8").splitlines()

    for line in lines:
        # "1\tsound/word51/word51.wav" または "sound/word51/word51.wav" 両対応
        parts = line.split("\t")
        raw_path = parts[-1].strip()
        if not raw_path:
            continue
        # パス中に word_id が含まれているか確認
        if word_id in raw_path.replace("\\", "/"):
            sample_path_str = raw_path.replace("\\", "/")
            if sample_path_str.startswith("audio/"):
                sample_path_str = sample_path_str[6:]
            return str((RAW_AUDIO_DIR / sample_path_str).resolve())

    raise IndexError(f"audio.scp に {word_id} が見つかりません")


def convert_to_16kHz(input_path: str | Path, output_path: str | Path) -> bool:
    """
    音声を 16kHz / モノラル / 16bit PCM に変換する。
    変換が不要な場合も output_path へコピーする。
    変換を実施した場合は True を返す。
    """
    sound = AudioSegment.from_wav(str(input_path))
    needs_convert = (
        sound.frame_rate != 16000
        or sound.channels != 1
        or sound.sample_width != 2
    )
    if needs_convert:
        sound = sound.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        sound.export(str(output_path), format="wav")
        return True
    if str(input_path) != str(output_path):
        sound.export(str(output_path), format="wav")
    return False


def segment_audio(
    sound_file: str | Path, start: float, end: float,
    out_path: str | Path = TEST_SEGMENT_WAV_PATH,
) -> None:
    """
    音声ファイルを発話区間（start〜end 秒）で切り出し、
    out_path に保存する（前後 100ms のマージン付き）。
    """
    sound     = AudioSegment.from_wav(str(sound_file))
    cut_start = max(0, int(start * 1000) - 100)
    cut_end   = max(cut_start, int(end * 1000) + 100)
    sound[cut_start:cut_end].export(str(out_path), format="wav")