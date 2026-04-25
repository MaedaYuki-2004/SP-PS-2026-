"""
core/timbre.py
音色評価（MFCC 抽出・DTW 距離計算）を担当するモジュール。
librosa で MFCC を算出し、fastdtw で基準音声との類似度を評価する。
"""
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from fastdtw import fastdtw
from scipy.spatial.distance import euclidean

from config import AUDIO_MFCC_DIR, WORDS_TXT_PATH


def audio_mfcc(wav_file: str | Path) -> np.ndarray:
    """
    WAV ファイルから MFCC（12 次元、1 次元目を除く）を抽出する。

    Returns
    -------
    np.ndarray : shape (frames, 12)
    """
    y, sr = librosa.load(str(wav_file), sr=None)
    mfcc  = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, n_fft=512, hop_length=160)
    mfcc  = mfcc.T
    return np.delete(mfcc, 0, axis=1)  # 0 次元（エネルギー）を除去


def create_dtw_list(
    mfcc1: np.ndarray,
    start: int = 0,
    finish: int = 30,
    sample_path: str | Path = AUDIO_MFCC_DIR,
) -> list[float]:
    """
    mfcc1 と各基準単語の MFCC バイナリ（.bin）との DTW 距離リストを返す。
    """
    dtw_list    = []
    sample_path = Path(sample_path)
    num_dims    = 12

    for i in range(start, finish):
        sample_file = sample_path / f"word{i + 1}.bin"
        mfcc2 = np.fromfile(str(sample_file), dtype=np.float32).reshape(-1, num_dims)
        distance, _ = fastdtw(mfcc1, mfcc2, dist=euclidean)
        dtw_list.append(float(distance))

    return dtw_list


def create_word_list(path: str | Path = WORDS_TXT_PATH) -> list[str]:
    """words.txt から単語リストを読み込む。"""
    with Path(path).open("r", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f]


def _descending_order(
    dtw_list: list[float],
    word_list: list[str],
) -> tuple[list[float], list[str]]:
    """DTW 距離の昇順でソートした (距離リスト, 単語リスト) を返す。"""
    pairs = sorted(zip(dtw_list, word_list), key=lambda x: x[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


def dtw_ascending_order(
    mfcc_file: str | Path,
    num: int,
) -> tuple[list[float], list[str], list[str], int]:
    """
    録音音声と全基準単語の DTW 距離を昇順で返す。
    選択単語に対応するバーを赤色にするカラーリストも生成する。

    Returns
    -------
    dtw_list  : 昇順の DTW 距離リスト
    word_list : 昇順に並び替えた単語リスト
    colors    : 各棒グラフの色（選択単語="red", それ以外="blue"）
    red_index : 選択単語のインデックス
    """
    mfcc1          = audio_mfcc(mfcc_file)
    dtw_list_raw   = create_dtw_list(mfcc1)
    word_list_raw  = create_word_list()
    target_word    = word_list_raw[num - 1]

    dtw_list, word_list = _descending_order(dtw_list_raw, word_list_raw)

    red_index = next(
        (i for i, w in enumerate(word_list) if w == target_word), 0
    )
    colors = ["red" if i == red_index else "blue" for i in range(len(word_list))]

    return dtw_list, word_list, colors, red_index