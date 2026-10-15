"""
scripts/regenerate_mfcc.py
お手本の音声（ライブラリの sound/<word_id>/<word_id>.wav）から MFCC を計算し直して、
ライブラリの mfcc/<word_id>.bin を上書きするスクリプト。

【実行方法】
  プロジェクトのルート階層（app.py と同じ場所）で実行してください。
  python scripts/regenerate_mfcc.py --user A01
  単語とお手本は参加者ごとなので --user で参加者を指定する（省略すると古い版の共有の単語）。

core/timbre.py の audio_mfcc() と同じパラメータ・処理で計算する。

【処理内容】
  - n_mfcc=13 で MFCC を計算し 0 次元を除去（12次元）
  - librosa.feature.delta() で Δ・ΔΔ を計算し 0 次元を除去（各12次元）
  - 結合して 36 次元の float32 バイナリとして保存
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import librosa
import numpy as np

from core import usercontext
from core.library import current_library

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
    db_path = current_library().words_db
    if not db_path.exists():
        return []
    with db_path.open("r", encoding="utf-8") as f:
        db = json.load(f)
    return list(db.keys())


def main() -> None:
    parser = argparse.ArgumentParser(description="お手本の MFCC を作り直す")
    parser.add_argument("--user", help="単語の持ち主の利用者ID（省略すると共有の単語）")
    usercontext.set_current_user(parser.parse_args().user)
    lib = current_library()

    print("=" * 60)
    print("  MFCC バイナリ再生成スクリプト（Delta MFCC 対応版）")
    print("=" * 60)
    print(f"  パラメータ: n_mfcc={N_MFCC}, n_fft={N_FFT}, hop_length={HOP_LENGTH}")
    print(f"  次元数: {MFCC_STATIC_DIMS}(静的) + {MFCC_STATIC_DIMS}(Δ) + {MFCC_STATIC_DIMS}(ΔΔ) = {MFCC_TOTAL_DIMS}次元")
    print(f"  出力先: {lib.mfcc_dir}")
    print()

    lib.mfcc_dir.mkdir(parents=True, exist_ok=True)

    word_ids = get_all_word_ids()
    if not word_ids:
        print("  [!!] words_db.json が見つからないか空です。")
        print(f"       {lib.words_db} を確認してください。")
        return

    print(f"  対象単語数: {len(word_ids)} 語")
    print()

    success = 0
    errors  = []

    for word_id in word_ids:
        wav_path = lib.sample_wav(word_id)
        bin_path = lib.mfcc_bin(word_id)

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