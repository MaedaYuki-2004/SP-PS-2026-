"""
scripts/regenerate_mfcc.py
基準音声（data/raw_audio/sound/wordX/wordX.wav）から
MFCCを計算し直して data/mfcc/wordX.bin を上書きするスクリプト。

【実行方法】
  プロジェクトのルート階層（app.py と同じ場所）で実行してください。
  python scripts/regenerate_mfcc.py

【変更点】
  静的 MFCC（12次元）から Delta MFCC（Δ・ΔΔ追加 / 計36次元）に変更。
  core/timbre.py の audio_mfcc() と完全に同じパラメータ・処理で計算する。

  ⚠️ 既存の .bin ファイル（12次元）は非互換になるため、
  　　このスクリプトを必ず再実行してください。
  　　再実行しないと DTW 音色評価でエラーが発生します。

【処理内容】
  - n_mfcc=13 で MFCC を計算し 0 次元を除去（12次元）
  - librosa.feature.delta() で Δ・ΔΔ を計算し 0 次元を除去（各12次元）
  - 結合して 36 次元の float32 バイナリとして保存
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import librosa
import numpy as np

from config import AUDIO_MFCC_DIR, RAW_AUDIO_DIR, DATA_DIR

# ── パラメータ（core/timbre.py の audio_mfcc() と完全に一致させること） ─
N_MFCC     = 13
N_FFT      = 512
HOP_LENGTH = 160

# 静的(12) + Δ(12) + ΔΔ(12) = 36 次元
MFCC_STATIC_DIMS = N_MFCC - 1   # 12（0次元除去後）
MFCC_TOTAL_DIMS  = MFCC_STATIC_DIMS * 3  # 36


def compute_mfcc(wav_path: Path) -> np.ndarray:
    """
    WAV ファイルから MFCC + Δ + ΔΔ（計36次元）を計算する。
    core/timbre.py の audio_mfcc() と完全に同じ処理。
    """
    y, sr = librosa.load(str(wav_path), sr=None)

    mfcc   = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC, n_fft=N_FFT, hop_length=HOP_LENGTH)
    delta1 = librosa.feature.delta(mfcc, order=1)
    delta2 = librosa.feature.delta(mfcc, order=2)

    mfcc_T   = np.delete(mfcc.T,   0, axis=1)  # (frames, 12)
    delta1_T = np.delete(delta1.T, 0, axis=1)  # (frames, 12)
    delta2_T = np.delete(delta2.T, 0, axis=1)  # (frames, 12)

    result = np.concatenate([mfcc_T, delta1_T, delta2_T], axis=1)  # (frames, 36)
    return result.astype(np.float32)


def get_all_word_ids() -> list[str]:
    """words_db.json から全単語IDを取得する。"""
    import json
    db_path = DATA_DIR / "config" / "words_db.json"
    if not db_path.exists():
        return []
    with db_path.open("r", encoding="utf-8") as f:
        db = json.load(f)
    return list(db.keys())


def main() -> None:
    print("=" * 60)
    print("  MFCC バイナリ再生成スクリプト（Delta MFCC 対応版）")
    print("=" * 60)
    print(f"  パラメータ: n_mfcc={N_MFCC}, n_fft={N_FFT}, hop_length={HOP_LENGTH}")
    print(f"  次元数: {MFCC_STATIC_DIMS}(静的) + {MFCC_STATIC_DIMS}(Δ) + {MFCC_STATIC_DIMS}(ΔΔ) = {MFCC_TOTAL_DIMS}次元")
    print(f"  出力先: {AUDIO_MFCC_DIR}")
    print()

    AUDIO_MFCC_DIR.mkdir(parents=True, exist_ok=True)

    word_ids = get_all_word_ids()
    if not word_ids:
        print("  [!!] words_db.json が見つからないか空です。")
        print("       data/config/words_db.json を確認してください。")
        return

    print(f"  対象単語数: {len(word_ids)} 語")
    print()

    success = 0
    errors  = []

    for word_id in word_ids:
        wav_path = RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.wav"
        bin_path = AUDIO_MFCC_DIR / f"{word_id}.bin"

        if not wav_path.exists():
            msg = f"[NG] {word_id}: WAVファイルが見つかりません → {wav_path}"
            print(f"  {msg}")
            errors.append(msg)
            continue

        try:
            mfcc   = compute_mfcc(wav_path)
            mfcc.tofile(str(bin_path))

            frames = mfcc.shape[0]
            dims   = mfcc.shape[1]
            size   = bin_path.stat().st_size
            print(f"  [OK] {word_id:<10} frames={frames:4d}  dims={dims}  {size:,} bytes")
            success += 1

        except Exception as exc:
            msg = f"[NG] {word_id}: エラー → {exc}"
            print(f"  {msg}")
            errors.append(msg)

    print()
    print("=" * 60)
    print(f"  完了: {success} / {len(word_ids)} 単語")
    if errors:
        print(f"  エラー: {len(errors)} 件")
        for e in errors:
            print(f"    {e}")
    else:
        print(f"  全単語の .bin ファイルを {MFCC_TOTAL_DIMS}次元で正常に生成しました。")
        print("  DTW音色評価の精度が向上するはずです。")
    print("=" * 60)


if __name__ == "__main__":
    main()