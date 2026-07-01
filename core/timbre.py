"""
core/timbre.py
音色評価（MFCC 抽出・DTW 距離計算）を担当するモジュール。
librosa で MFCC を算出し、fastdtw で基準音声との類似度を評価する。

【変更点】
  - audio_mfcc() に Delta MFCC（Δ・ΔΔ）を追加。
    静的MFCC（12次元）＋ Δ（12次元）＋ ΔΔ（12次元）= 計36次元に変更。
    時間的な変化（音の動き）も捉えられるため DTW の比較精度が向上する。

  ⚠️ 重要：この変更により既存の .bin ファイル（12次元）は非互換になる。
  　　　　　python scripts/regenerate_mfcc.py を必ず再実行すること。
"""
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from fastdtw import fastdtw
from scipy.spatial.distance import euclidean

from config import AUDIO_MFCC_DIR

# ── MFCC 次元数 ──────────────────────────────────────────────────────
# 静的MFCC(12) + Δ(12) + ΔΔ(12) = 36 次元
# ※ 0次元（エネルギー）は各成分から除外するため n_mfcc=13 から 1 を引いた 12
MFCC_STATIC_DIMS = 12
MFCC_TOTAL_DIMS  = MFCC_STATIC_DIMS * 3  # 36

# 表示する上位件数
TOP_N = 20


def audio_mfcc(wav_file: str | Path) -> np.ndarray:
    """
    WAV ファイルから MFCC + Δ + ΔΔ（計36次元）を抽出する。

    【次元構成】
      - 静的 MFCC （12次元）: スペクトル包絡の形状
      - Δ  MFCC （12次元）: スペクトルの1階時間微分（変化の速さ）
      - ΔΔ MFCC （12次元）: スペクトルの2階時間微分（変化の加速度）

    従来の静的 MFCC だけでは音の「形」しか捉えられなかったが、
    Δ・ΔΔ を追加することで音の「動き」も比較できる。
    これにより DTW による音色類似度の精度が向上する。

    Returns
    -------
    np.ndarray : shape (frames, 36)
    """
    y, sr = librosa.load(str(wav_file), sr=None)

    # ── 静的 MFCC ─────────────────────────────────────────────────
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, n_fft=512, hop_length=160)

    # ── Δ（1階差分）・ΔΔ（2階差分） ────────────────────────────────
    delta1 = librosa.feature.delta(mfcc, order=1)
    delta2 = librosa.feature.delta(mfcc, order=2)

    # ── (frames, 13) に転置してから 0次元（エネルギー）を除去 ────────
    mfcc_T   = np.delete(mfcc.T,   0, axis=1)  # (frames, 12)
    delta1_T = np.delete(delta1.T, 0, axis=1)  # (frames, 12)
    delta2_T = np.delete(delta2.T, 0, axis=1)  # (frames, 12)

    # ── 結合：(frames, 36) ─────────────────────────────────────────
    return np.concatenate([mfcc_T, delta1_T, delta2_T], axis=1)


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

    .bin ファイルは MFCC_TOTAL_DIMS（36）次元として読み込む。
    regenerate_mfcc.py で再生成した .bin ファイルと次元数が一致すること。
    """
    dtw_list = []

    for entry in words:
        try:
            raw = np.fromfile(str(entry["bin_path"]), dtype=np.float32)
            if raw.size == 0 or raw.size % MFCC_TOTAL_DIMS != 0:
                print(
                    f"[timbre] {entry['bin_path'].name}: "
                    f"次元不一致（{raw.size} 要素, {MFCC_TOTAL_DIMS} 次元期待）"
                    " — 旧12次元ファイルの可能性。regenerate_mfcc.py を再実行してください。"
                )
                dtw_list.append(float("inf"))
                continue
            mfcc2 = raw.reshape(-1, MFCC_TOTAL_DIMS)
            distance, _ = fastdtw(mfcc1, mfcc2, dist=euclidean)
            dtw_list.append(float(distance) / (len(mfcc1) + len(mfcc2)))
        except Exception as e:
            print(f"[timbre] {entry['bin_path'].name}: 読み込みエラー — {e}")
            dtw_list.append(float("inf"))

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
    dtw_list  : 昇順の DTW 距離リスト
    word_list : 対応する単語表示テキストリスト
    colors    : 棒グラフの色（選択単語="red", それ以外="blue"）
    red_index : 選択単語のインデックス
    """
    mfcc1 = audio_mfcc(mfcc_file)
    words = _load_all_words()

    if not words:
        return [], [], [], 0

    dtw_list_raw = create_dtw_list_dynamic(mfcc1, words)

    # 昇順ソート（inf は末尾に）
    pairs = sorted(
        zip(dtw_list_raw, words),
        key=lambda x: x[0]
    )
    sorted_distances = [p[0] for p in pairs]
    sorted_words     = [p[1] for p in pairs]

    red_index_full = next(
        (i for i, w in enumerate(sorted_words) if w["word_id"] == word_id_selected),
        0
    )

    top_distances = sorted_distances[:TOP_N]
    top_words     = sorted_words[:TOP_N]

    selected_in_top = any(w["word_id"] == word_id_selected for w in top_words)
    if not selected_in_top:
        top_distances.append(sorted_distances[red_index_full])
        top_words.append(sorted_words[red_index_full])

    display_labels = [w["display"] for w in top_words]
    red_index = next(
        (i for i, w in enumerate(top_words) if w["word_id"] == word_id_selected),
        len(top_words) - 1
    )
    colors = ["red" if i == red_index else "blue" for i in range(len(top_words))]

    return top_distances, display_labels, colors, red_index