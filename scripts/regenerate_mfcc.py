"""
scripts/regenerate_mfcc.py
基準音声（data/raw_audio/sound/wordX/wordX.wav）から
MFCCを計算し直して data/mfcc/wordX.bin を上書きするスクリプト。

【実行方法】
  プロジェクトのルート階層（app.py と同じ場所）で実行してください。
  python scripts/regenerate_mfcc.py

【何をするか】
  audio_mfcc()と完全に同じパラメータ（n_mfcc=13, n_fft=512, hop_length=160）で
  全30単語の基準音声のMFCCを計算し、.binファイルとして保存します。
  これにより録音音声とのDTW比較が正しく機能するようになります。
"""
from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートをパスに追加（core/ や config.py を import できるように）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import librosa
import numpy as np

from config import AUDIO_MFCC_DIR, RAW_AUDIO_DIR

# ── パラメータ（audio_mfcc() と完全に一致させること） ───────────────
N_MFCC     = 13
N_FFT      = 512
HOP_LENGTH = 160
NUM_WORDS  = 30


def compute_mfcc(wav_path: Path) -> np.ndarray:
    """
    WAV ファイルから MFCC を計算する。
    core/timbre.py の audio_mfcc() と完全に同じ処理。
    """
    y, sr = librosa.load(str(wav_path), sr=None)
    mfcc  = librosa.feature.mfcc(
        y=y, sr=sr,
        n_mfcc=N_MFCC,
        n_fft=N_FFT,
        hop_length=HOP_LENGTH,
    )
    mfcc = mfcc.T
    mfcc = np.delete(mfcc, 0, axis=1)  # 0次元（エネルギー）を除去 → shape: (frames, 12)
    return mfcc.astype(np.float32)


def main() -> None:
    print("=" * 55)
    print("  MFCC バイナリ再生成スクリプト")
    print("=" * 55)
    print(f"  パラメータ: n_mfcc={N_MFCC}, n_fft={N_FFT}, hop_length={HOP_LENGTH}")
    print(f"  出力先: {AUDIO_MFCC_DIR}")
    print()

    AUDIO_MFCC_DIR.mkdir(parents=True, exist_ok=True)

    success = 0
    errors  = []

    for i in range(1, NUM_WORDS + 1):
        word_id  = f"word{i}"
        wav_path = RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.wav"
        bin_path = AUDIO_MFCC_DIR / f"{word_id}.bin"

        if not wav_path.exists():
            msg = f"[NG] {word_id}: WAVファイルが見つかりません → {wav_path}"
            print(msg)
            errors.append(msg)
            continue

        try:
            mfcc = compute_mfcc(wav_path)
            mfcc.tofile(str(bin_path))

            frames = mfcc.shape[0]
            size   = bin_path.stat().st_size
            print(f"  [OK] {word_id}  frames={frames:4d}  {size:,} bytes → {bin_path.name}")
            success += 1

        except Exception as exc:
            msg = f"[NG] {word_id}: エラー → {exc}"
            print(msg)
            errors.append(msg)

    print()
    print("=" * 55)
    print(f"  完了: {success} / {NUM_WORDS} 単語")
    if errors:
        print(f"  エラー: {len(errors)} 件")
        for e in errors:
            print(f"    {e}")
    else:
        print("  全単語の .bin ファイルを正常に生成しました。")
        print("  DTW音色評価の精度が向上するはずです。")
    print("=" * 55)


if __name__ == "__main__":
    main()