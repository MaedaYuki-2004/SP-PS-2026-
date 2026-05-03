"""
diagnose.py
システムの動作状況を診断するスクリプト。
python diagnose.py をプロジェクトのルートで実行してください。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ── 結果の表示ヘルパー ───────────────────────────────────────────────
def ok(msg):  print(f"  [OK]  {msg}")
def ng(msg):  print(f"  [NG]  {msg}")
def warn(msg):print(f"  [!!]  {msg}")
def info(msg):print(f"  [--]  {msg}")

def section(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")

# ════════════════════════════════════════════════════════
section("1. フォルダ・ファイルの存在確認")
# ════════════════════════════════════════════════════════

checks = [
    ("engine/",                        BASE_DIR / "engine"),
    ("engine/bin/",                    BASE_DIR / "engine" / "bin"),
    ("engine/models/",                 BASE_DIR / "engine" / "models"),
    ("engine/models/hmmdefs_monof_mix16_gid.binhmm",
     BASE_DIR / "engine" / "models" / "hmmdefs_monof_mix16_gid.binhmm"),
    ("scripts/segment_julius.pl",      BASE_DIR / "scripts" / "segment_julius.pl"),
    ("data/raw_audio/wav/",            BASE_DIR / "data" / "raw_audio" / "wav"),
    ("data/config/audio.scp",          BASE_DIR / "data" / "config" / "audio.scp"),
    ("data/config/words.txt",          BASE_DIR / "data" / "config" / "words.txt"),
    ("data/mfcc/",                     BASE_DIR / "data" / "mfcc"),
]

for label, path in checks:
    if path.exists():
        ok(label)
    else:
        ng(f"{label}  ← 存在しない！")

# ════════════════════════════════════════════════════════
section("2. Julius 実行ファイルの確認（Windows）")
# ════════════════════════════════════════════════════════

julius_exe = BASE_DIR / "engine" / "bin" / "julius-4.3.1.exe"
if julius_exe.exists():
    ok(f"julius-4.3.1.exe が見つかりました: {julius_exe}")
else:
    ng(f"julius-4.3.1.exe が見つかりません: {julius_exe}")
    info("engine/bin/ フォルダ内にある .exe ファイルを確認してください")

    bin_dir = BASE_DIR / "engine" / "bin"
    if bin_dir.exists():
        exes = list(bin_dir.glob("*.exe"))
        if exes:
            warn(f"代わりに以下の .exe が見つかりました:")
            for e in exes:
                info(f"  {e.name}")
        else:
            ng("engine/bin/ 内に .exe ファイルが存在しません")

# ════════════════════════════════════════════════════════
section("3. Perl の確認")
# ════════════════════════════════════════════════════════

try:
    result = subprocess.run(
        ["perl", "--version"],
        capture_output=True, text=True, timeout=5
    )
    first_line = result.stdout.strip().splitlines()[0] if result.stdout else "（出力なし）"
    ok(f"Perl が見つかりました: {first_line}")
except FileNotFoundError:
    ng("Perl が見つかりません。Strawberry Perl をインストールしてください。")
    info("https://strawberryperl.com/")
except Exception as e:
    ng(f"Perl の確認に失敗しました: {e}")

# ════════════════════════════════════════════════════════
section("4. 基準音声ファイルの確認（word1）")
# ════════════════════════════════════════════════════════

sound_dir = BASE_DIR / "data" / "raw_audio" / "sound"
if not sound_dir.exists():
    ng(f"sound/ ディレクトリが存在しません: {sound_dir}")
else:
    ok(f"sound/ ディレクトリ: {sound_dir}")
    word1_dir = sound_dir / "word1"
    if not word1_dir.exists():
        ng("sound/word1/ フォルダが存在しません")
    else:
        for ext in [".wav", ".lab", ".log"]:
            f = word1_dir / f"word1{ext}"
            if f.exists():
                size = f.stat().st_size
                if size == 0:
                    ng(f"word1{ext} は空ファイルです！（サイズ 0）")
                else:
                    ok(f"word1{ext}  ({size:,} bytes)")
            else:
                ng(f"word1{ext} が存在しません")

# ════════════════════════════════════════════════════════
section("5. .lab ファイルの中身を確認（word1）")
# ════════════════════════════════════════════════════════

lab_path = BASE_DIR / "data" / "raw_audio" / "sound" / "word1" / "word1.lab"
if lab_path.exists() and lab_path.stat().st_size > 0:
    content = lab_path.read_text(encoding="utf-8", errors="replace").strip()
    lines = content.splitlines()
    ok(f"word1.lab の行数: {len(lines)} 行")
    info("--- 内容 ---")
    for line in lines:
        info(f"  {line}")
    info("------------")

    # silB と silE が含まれているか確認
    has_silB = any("silB" in l for l in lines)
    has_silE = any("silE" in l for l in lines)
    if has_silB and has_silE:
        ok("silB / silE（無音区間ラベル）が含まれています → 正常なアライメント結果")
    else:
        warn("silB / silE が見当たりません → アライメントが不完全な可能性があります")
else:
    ng("word1.lab が存在しないか空です → Julius が正常に動いていない可能性が高い")

# ════════════════════════════════════════════════════════
section("6. MFCCバイナリファイルの確認")
# ════════════════════════════════════════════════════════

mfcc_dir = BASE_DIR / "data" / "mfcc"
if mfcc_dir.exists():
    bins = list(mfcc_dir.glob("*.bin"))
    info(f"mfcc/ 内の .bin ファイル数: {len(bins)} / 30")
    if len(bins) == 30:
        ok("30単語分の .bin ファイルが揃っています")
        sizes = [f.stat().st_size for f in sorted(bins)]
        info(f"ファイルサイズ範囲: {min(sizes):,} 〜 {max(sizes):,} bytes")
        if min(sizes) == 0:
            ng("サイズ 0 の .bin ファイルがあります！")
    elif len(bins) == 0:
        ng("mfcc/ 内に .bin ファイルがありません → DTW評価ができません")
    else:
        warn(f"{len(bins)} 個しかありません（30個必要）")
        missing = [f"word{i}.bin" for i in range(1,31)
                   if not (mfcc_dir / f"word{i}.bin").exists()]
        for m in missing:
            info(f"  欠けているファイル: {m}")
else:
    ng("mfcc/ ディレクトリが存在しません")

# ════════════════════════════════════════════════════════
section("7. audio.scp の内容確認")
# ════════════════════════════════════════════════════════

scp_path = BASE_DIR / "data" / "config" / "audio.scp"
if scp_path.exists():
    lines = scp_path.read_text(encoding="utf-8").splitlines()
    info(f"audio.scp の行数: {len(lines)} 行")
    info(f"先頭3行: {lines[:3]}")

    # パスが実際に存在するか確認
    raw_audio = BASE_DIR / "data" / "raw_audio"
    missing_wav = []
    for line in lines:
        p = line.strip().replace("\\", "/")
        if p.startswith("audio/"):
            p = p[6:]
        full = raw_audio / p
        if not full.exists():
            missing_wav.append(str(full))

    if not missing_wav:
        ok("audio.scp に記載された全WAVファイルが存在します")
    else:
        ng(f"{len(missing_wav)} 個のWAVファイルが見つかりません")
        for m in missing_wav[:5]:
            info(f"  {m}")
        if len(missing_wav) > 5:
            info(f"  ...他 {len(missing_wav)-5} 件")

# ════════════════════════════════════════════════════════
section("診断まとめ")
# ════════════════════════════════════════════════════════
print("""
  [NG] が出た項目が原因の可能性が高いです。
  結果をそのままコピーして共有してください。
""")