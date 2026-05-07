"""
scripts/repair_alignment.py
指定した単語IDのサンプル音声に対してJuliusアライメントを再実行するスクリプト。
音声が短すぎる場合は前後に無音を追加してから処理する。

使い方：
  python scripts/repair_alignment.py word35
  python scripts/repair_alignment.py word35 word36 word37
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydub import AudioSegment
from config import AUDIO_WAV_DIR, RAW_AUDIO_DIR
from core.alignment import perl_run

MIN_DURATION_SEC = 1.5
SILENCE_PAD_MS   = 500


def add_silence_padding(wav_path: Path) -> None:
    sound   = AudioSegment.from_wav(str(wav_path))
    silence = AudioSegment.silent(duration=SILENCE_PAD_MS, frame_rate=sound.frame_rate)
    padded  = silence + sound + silence
    padded.export(str(wav_path), format="wav")
    print(f"  無音追加: {len(sound)/1000:.2f}s → {len(padded)/1000:.2f}s")


def repair(word_id: str) -> None:
    sound_dir = RAW_AUDIO_DIR / "sound" / word_id
    wav_path  = sound_dir / f"{word_id}.wav"
    txt_path  = sound_dir / f"{word_id}.txt"
    lab_path  = sound_dir / f"{word_id}.lab"
    log_path  = sound_dir / f"{word_id}.log"

    print(f"\n[{word_id}] アライメント再実行")

    if not wav_path.exists():
        print(f"  [NG] WAVファイルが見つかりません: {wav_path}")
        return
    if not txt_path.exists():
        print(f"  [NG] TXTファイルが見つかりません: {txt_path}")
        return

    reading = txt_path.read_text(encoding="utf-8").strip()
    print(f"  WAV : {wav_path.stat().st_size:,} bytes")
    print(f"  TXT : {reading}")

    tmp_wav = AUDIO_WAV_DIR / f"{word_id}.wav"
    tmp_txt = AUDIO_WAV_DIR / f"{word_id}.txt"
    tmp_lab = AUDIO_WAV_DIR / f"{word_id}.lab"
    tmp_log = AUDIO_WAV_DIR / f"{word_id}.log"

    shutil.copy(wav_path, tmp_wav)
    shutil.copy(txt_path, tmp_txt)

    sound    = AudioSegment.from_wav(str(tmp_wav))
    duration = len(sound) / 1000.0
    if duration < MIN_DURATION_SEC:
        print(f"  音声が短いため無音パディングを追加します（{duration:.2f}s）")
        add_silence_padding(tmp_wav)

    try:
        perl_run()

        if tmp_lab.exists() and tmp_lab.stat().st_size > 0:
            shutil.copy(str(tmp_lab), str(lab_path))
            shutil.copy(str(tmp_log), str(log_path))
            print(f"  [OK] lab: {lab_path.stat().st_size:,} bytes")
            print(f"  → アライメント成功！")
        else:
            print(f"  [NG] labファイルが生成されませんでした")
            if tmp_log.exists() and tmp_log.stat().st_size > 0:
                log_text = tmp_log.read_text(encoding="utf-8", errors="replace")
                print("  --- Julius ログ（末尾200文字）---")
                print("  " + log_text[-200:])

    except Exception as e:
        print(f"  [NG] エラー: {e}")
    finally:
        for p in [tmp_wav, tmp_txt, tmp_lab, tmp_log]:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else []
    if not targets:
        print("使い方: python scripts/repair_alignment.py word35")
        sys.exit(1)
    for word_id in targets:
        repair(word_id)
    print("\n完了しました。")