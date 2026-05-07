"""
core/timbre.py
音色評価（MFCC 抽出・DTW 距離計算）を担当するモジュール。
librosa で MFCC を算出し、fastdtw で基準音声との類似度を評価する。

【変更点】
  - words_db.json に登録された全単語を動的に比較対象とする
  - 結果を上位 TOP_N 件に絞って返す
"""
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from fastdtw import fastdtw
from scipy.spatial.distance import euclidean

from config import AUDIO_MFCC_DIR

# 表示する上位件数
TOP_N = 20


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


def _load_all_words() -> list[dict]:
    """
    words_db.json から全単語を読み込む。
    .bin ファイルが存在する単語のみ返す。
    """
    from config import DATA_DIR
    import json

    db_path = DATA_DIR / "config" / "words_db.json"
    if not db_path.exists():
        return []

    with db_path.open("r", encoding="utf-8") as f:
        db = json.load(f)

    words = []
    for word_id, entry in db.items():
        bin_path = AUDIO_MFCC_DIR / f"{word_id}.bin"
        if bin_path.exists() and bin_path.stat().st_size > 0:
            words.append({
                "word_id": word_id,
                "display": entry.get("display", word_id),
                "bin_path": bin_path,
            })
    return words


def create_dtw_list_dynamic(
    mfcc1: np.ndarray,
    words: list[dict],
) -> list[float]:
    """
    mfcc1 と words_db の全単語の MFCC バイナリとの DTW 距離リストを返す。
    """
    num_dims = 12
    dtw_list = []

    for entry in words:
        mfcc2    = np.fromfile(str(entry["bin_path"]), dtype=np.float32).reshape(-1, num_dims)
        distance, _ = fastdtw(mfcc1, mfcc2, dist=euclidean)
        dtw_list.append(float(distance))

    return dtw_list


def dtw_ascending_order(
    mfcc_file: str | Path,
    word_id_selected: str,
) -> tuple[list[float], list[str], list[str], int]:
    """
    録音音声と words_db の全単語の DTW 距離を昇順で返す。
    上位 TOP_N 件に絞り、選択単語が範囲外の場合は末尾に追加する。

    Parameters
    ----------
    mfcc_file        : 録音音声ファイルパス
    word_id_selected : 選択中の単語ID（例：word29）

    Returns
    -------
    dtw_list  : 昇順の DTW 距離リスト（上位 TOP_N + 選択単語）
    word_list : 対応する単語表示テキストリスト
    colors    : 棒グラフの色（選択単語="red", それ以外="blue"）
    red_index : 選択単語のインデックス
    """
    mfcc1 = audio_mfcc(mfcc_file)
    words = _load_all_words()

    if not words:
        return [], [], [], 0

    dtw_list_raw = create_dtw_list_dynamic(mfcc1, words)

    # 昇順ソート
    pairs = sorted(
        zip(dtw_list_raw, words),
        key=lambda x: x[0]
    )
    sorted_distances = [p[0] for p in pairs]
    sorted_words     = [p[1] for p in pairs]

    # 選択単語の位置を特定
    red_index_full = next(
        (i for i, w in enumerate(sorted_words) if w["word_id"] == word_id_selected),
        0
    )

    # 上位 TOP_N 件を取得
    top_distances = sorted_distances[:TOP_N]
    top_words     = sorted_words[:TOP_N]

    # 選択単語が上位 TOP_N に含まれない場合は末尾に追加
    selected_in_top = any(w["word_id"] == word_id_selected for w in top_words)
    if not selected_in_top:
        top_distances.append(sorted_distances[red_index_full])
        top_words.append(sorted_words[red_index_full])

    # 表示用リストに変換
    display_distances = top_distances
    display_labels    = [w["display"] for w in top_words]

    # 赤バーのインデックスを再計算
    red_index = next(
        (i for i, w in enumerate(top_words) if w["word_id"] == word_id_selected),
        len(top_words) - 1
    )
    colors = ["red" if i == red_index else "blue" for i in range(len(top_words))]

    return display_distances, display_labels, colors, red_index