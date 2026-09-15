"""
scripts/repair_alignment.py
指定した単語IDのお手本音声に対してJuliusアライメントを再実行するスクリプト。

使い方：
  python scripts/repair_alignment.py word35
  python scripts/repair_alignment.py word35 word36 word37

data/raw_audio/sound/<word_id>/ の <word_id>.wav と <word_id>.txt（ひらがな読み）から
<word_id>.lab / <word_id>.log を作り直す。失敗したときは既存の .lab / .log を残す。

【2026-09-14 修正】
  以前は core.alignment から存在しない perl_run() を import していたため、起動時に
  ImportError で止まっていた。また、短い音声には前後 0.5 秒の無音を足してから
  アライメントし、その結果を「無音を足す前の wav」の .lab として保存していたため、
  成功しても時刻が 0.5 秒ずれていた。無音の追加はやめ、保存されている wav そのものを
  core.alignment.run_alignment_on_file() でアライメントする（単語登録と同じ処理）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import RAW_AUDIO_DIR
from core.alignment import lab_load, run_alignment_on_file


def repair(word_id: str) -> bool:
    sound_dir = RAW_AUDIO_DIR / "sound" / word_id
    wav_path  = sound_dir / f"{word_id}.wav"
    txt_path  = sound_dir / f"{word_id}.txt"
    lab_path  = sound_dir / f"{word_id}.lab"
    log_path  = sound_dir / f"{word_id}.log"

    print(f"\n[{word_id}] アライメント再実行")

    if not wav_path.exists():
        print(f"  [NG] WAVファイルが見つかりません: {wav_path}")
        return False
    if not txt_path.exists():
        print(f"  [NG] TXTファイルが見つかりません: {txt_path}")
        return False

    reading = txt_path.read_text(encoding="utf-8").strip()
    print(f"  WAV : {wav_path.stat().st_size:,} bytes")
    print(f"  TXT : {reading}")

    # いったん一時ファイルに出力し、成功したときだけ既存の .lab / .log を置き換える
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_lab = Path(tmpdir) / "result.lab"
        tmp_log = Path(tmpdir) / "result.log"
        try:
            run_alignment_on_file(wav_path, reading, tmp_lab, tmp_log)
        except Exception as e:
            print(f"  [NG] アライメントに失敗しました（既存の .lab / .log はそのまま）: {e}")
            return False

        lab_list, mora_list, *_ = lab_load(tmp_lab)
        # 一時フォルダと保存先が別のドライブ・ファイルシステムでも動くよう、移動ではなくコピーする
        shutil.copyfile(tmp_lab, lab_path)
        if tmp_log.exists():
            shutil.copyfile(tmp_log, log_path)

    print(f"  [OK] {len(lab_list)} 音素 / {len(mora_list)} モーラ: {' '.join(str(m[2]) for m in mora_list)}")
    print("  ※ お手本の口形データ（lip_refs.json の mora_data）は作り直していません。"
          "口形も合わせたい場合は、先生モードでお手本を録り直してください。")
    return True


if __name__ == "__main__":
    targets = sys.argv[1:]
    if not targets:
        print("使い方: python scripts/repair_alignment.py word35")
        sys.exit(1)
    results = [repair(word_id) for word_id in targets]
    print(f"\n完了しました（成功 {sum(results)} / {len(results)}）。")
    sys.exit(0 if all(results) else 1)
