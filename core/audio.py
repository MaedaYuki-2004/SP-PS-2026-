"""
core/audio.py
音声ファイルの変換・前処理を担当するモジュール。
フォーマット変換、セグメント切り出しなど入力音声の整形処理をまとめる。
"""
from __future__ import annotations

import re
from pathlib import Path

from pydub import AudioSegment

from config import (
    AUDIO_SCP_PATH,
    RAW_AUDIO_DIR,
    TEST_SEGMENT_WAV_PATH,
    WORDS,
    TEST_TXT_PATH,
    WORD_ID_MEMO_PATH,
)


def word_select(word_id: str) -> str:
    """
    単語IDを受け取り、対応するひらがな読みを返す。
    同時に test.txt と word_id.txt を更新する。
    """
    if word_id not in WORDS:
        raise ValueError(f"不正な単語IDです: {word_id}")
    word = WORDS[word_id]
    TEST_TXT_PATH.write_text(word, encoding="utf-8")
    WORD_ID_MEMO_PATH.write_text(word_id, encoding="utf-8")
    return word


def read_sample(word_id: str) -> str:
    """
    audio.scp から word_id に対応する基準音声の絶対パスを返す。
    """
    if not AUDIO_SCP_PATH.exists():
        raise FileNotFoundError(f"audio.scp が見つかりません: {AUDIO_SCP_PATH}")

    match = re.search(r"\d+", word_id)
    if not match:
        return ""

    file_idx = int(match.group()) - 1
    wav_paths = AUDIO_SCP_PATH.read_text(encoding="utf-8").splitlines()

    if file_idx < 0 or file_idx >= len(wav_paths):
        raise IndexError("audio.scp の範囲外です")

    # 古い "audio/" プレフィックスを自動除去
    sample_path_str = wav_paths[file_idx].replace("\\", "/")
    if sample_path_str.startswith("audio/"):
        sample_path_str = sample_path_str[6:]

    return str((RAW_AUDIO_DIR / sample_path_str).resolve())


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


def segment_audio(sound_file: str | Path, start: float, end: float) -> None:
    """
    音声ファイルを発話区間（start〜end 秒）で切り出し、
    TEST_SEGMENT_WAV_PATH に保存する（前後 100ms のマージン付き）。
    """
    sound = AudioSegment.from_wav(str(sound_file))
    cut_start = max(0, int(start * 1000) - 100)
    cut_end   = max(cut_start, int(end * 1000) + 100)
    sound[cut_start:cut_end].export(str(TEST_SEGMENT_WAV_PATH), format="wav")