"""
core/audio.py
音声ファイルの変換・前処理を担当するモジュール。
フォーマット変換、ノイズ除去、セグメント切り出しなど入力音声の整形処理をまとめる。
"""
from __future__ import annotations

import re
from pathlib import Path

import noisereduce as nr
import numpy as np
from pydub import AudioSegment
from scipy.io import wavfile

from config import (
    TEST_TXT_PATH,
    WORD_ID_MEMO_PATH,
    WORDS,
)
from core import userdata


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
    word_id に対応するお手本音声（sound/<word_id>/<word_id>.wav）の絶対パスを返す。
    生徒ごとに分けたフォルダ（core/userdata.py）から探す。見つからなければ FileNotFoundError。

    以前は audio.scp の各行に word_id が「含まれるか」で探していたため、
    word1 を探すと sound/word10/word10.wav が先に当たることがあった。
    """
    wav = userdata.sound_dir(word_id) / f"{word_id}.wav"
    if not wav.exists():
        raise FileNotFoundError(f"{word_id} のお手本音声がありません。お手本を録画し直してください。")
    return str(wav.resolve())


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


def reduce_noise_wav(wav_path: str | Path, noise_duration: float = 0.5) -> None:
    """
    WAV ファイルのノイズを除去して上書き保存する。

    先頭 noise_duration 秒をノイズプロファイルとして推定し、
    スペクトル減算によってノイズ成分を除去する。
    Julius の音素認識精度および MFCC の品質向上に効果がある。

    Parameters
    ----------
    wav_path       : 処理対象の WAV ファイルパス（16kHz/16bit/モノラル前提）
    noise_duration : ノイズプロファイルとして使う先頭区間の長さ（秒）
    """
    sr, data = wavfile.read(str(wav_path))

    # ノイズサンプル：先頭 noise_duration 秒（無音・環境音区間）
    n_noise = int(sr * noise_duration)
    if n_noise <= 0 or n_noise >= len(data):
        return  # データが短すぎる場合はスキップ

    data_f32   = data.astype(np.float32)
    noise_clip = data_f32[:n_noise]

    reduced = nr.reduce_noise(y=data_f32, sr=sr, y_noise=noise_clip)

    # 16bit PCM にクリップして保存
    reduced_int16 = np.clip(reduced, -32768, 32767).astype(np.int16)
    wavfile.write(str(wav_path), sr, reduced_int16)


def segment_audio(sound_file: str | Path, start: float, end: float) -> None:
    """
    音声ファイルを発話区間（start〜end 秒）で切り出し、
    userdata.segment_wav_path()（生徒ごとの test2.wav）に保存する（前後 100ms のマージン付き）。
    """
    sound     = AudioSegment.from_wav(str(sound_file))
    cut_start = max(0, int(start * 1000) - 100)
    cut_end   = max(cut_start, int(end * 1000) + 100)
    sound[cut_start:cut_end].export(str(userdata.segment_wav_path()), format="wav")