"""
diagnose.py
SP-PS の動作環境を診断するスクリプト。

起動できない・エラーが出る場合はまずこれを実行して原因を特定する。
結果をそのままコピーして共有できる形式で出力する。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def ok(msg):  print(f"  [OK]  {msg}")
def ng(msg):  print(f"  [NG]  {msg}")
def warn(msg): print(f"  [!!]  {msg}")
def info(msg): print(f"  [--]  {msg}")
def sep(title): print(f"\n{'=' * 55}\n  {title}\n{'=' * 55}")


# ── 1. フォルダ・ファイルの存在確認 ─────────────────────────────
sep("1. フォルダ・ファイルの存在確認")

required_paths = [
    "engine/",
    "engine/bin/",
    "engine/models/",
    "engine/models/hmmdefs_monof_mix16_gid.binhmm",
    "scripts/segment_julius.pl",
    "data/raw_audio/wav/",
]
# 単語の audio.scp・words.txt・mfcc/ は参加者ごとのライブラリにあるので 6・7 で調べる

for rel in required_paths:
    p = BASE_DIR / rel
    if p.exists():
        ok(rel)
    else:
        ng(f"{rel} が見つかりません")


# ── 2. Julius 実行ファイルの確認 ─────────────────────────────────
sep("2. Julius 実行ファイルの確認")

sys.path.insert(0, str(BASE_DIR))
from config import JULIUS_BIN_PATH  # noqa: E402

if JULIUS_BIN_PATH and Path(JULIUS_BIN_PATH).is_file():
    ok(f"Julius が見つかりました: {JULIUS_BIN_PATH}")
elif JULIUS_BIN_PATH:
    ng(f"自動検出されたパスにファイルがありません: {JULIUS_BIN_PATH}")
else:
    ng("Julius 実行ファイルが見つかりません")
    info("Windows: engine/bin/ に julius*.exe を配置してください")
    info("macOS: brew install julius （Homebrew。Apple Silicon なら /opt/homebrew/bin/julius）")
    info("Ubuntu/Debian: sudo apt-get install julius")
    info("別の場所にある場合は環境変数 JULIUS_BIN でパスを直接指定できます")


# ── 3. Perl の確認 ───────────────────────────────────────────────
sep("3. Perl の確認")

try:
    result = subprocess.run(
        ["perl", "-v"],
        capture_output=True, text=True, timeout=10
    )
    first_line = [l for l in result.stdout.splitlines() if l.strip()]
    if first_line:
        ok(f"Perl が見つかりました: {first_line[0].strip()}")
    else:
        ok("Perl が見つかりました")
except FileNotFoundError:
    ng("Perl が見つかりません")
    info("Windows: https://strawberryperl.com/ からインストールしてください")
    info("macOS: 標準で入っています。見つからない場合は brew install perl")
    info("Ubuntu/Debian: sudo apt-get install perl")
except Exception as e:
    ng(f"Perl の確認中にエラー: {e}")


# ── 4〜7 で調べる単語とお手本 ─────────────────────────────────────
# 単語とお手本は参加者ごと（data/users/<利用者ID>/library/、core/library.py）。
# 共有の単語が無ければ、単語のある最初の参加者のものを調べる。
data_dir = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else BASE_DIR / "data"
lib_label, sound_dir, mfcc_dir = "共有", data_dir / "raw_audio/sound", data_dir / "mfcc"
words_db_path, scp_path, scp_base = data_dir / "config/words_db.json", data_dir / "config/audio.scp", data_dir / "raw_audio"
if not words_db_path.exists():
    for root in sorted((data_dir / "users").glob("*/library")):
        if (root / "words_db.json").exists():
            lib_label, sound_dir, mfcc_dir = f"参加者 {root.parent.name}", root / "sound", root / "mfcc"
            words_db_path, scp_path, scp_base = root / "words_db.json", root / "audio.scp", root
            break
try:
    WORD = next(iter(json.loads(words_db_path.read_text(encoding="utf-8"))), "word1")
except Exception:
    WORD = "word1"

# ── 4. 基準音声ファイルの確認 ────────────────────────────────────
sep(f"4. 基準音声ファイルの確認（{lib_label}の {WORD}）")

if sound_dir.exists():
    ok(f"sound/ ディレクトリ: {sound_dir}")
else:
    ng(f"sound/ ディレクトリが見つかりません: {sound_dir}")

for ext in ["wav", "lab", "log"]:
    p = sound_dir / WORD / f"{WORD}.{ext}"
    if p.exists():
        ok(f"{WORD}.{ext}  ({p.stat().st_size:,} bytes)")
    else:
        ng(f"{WORD}.{ext} が見つかりません")


# ── 5. .lab ファイルの中身を確認 ─────────────────────────────────
sep(f"5. .lab ファイルの中身を確認（{WORD}）")

lab_path = sound_dir / WORD / f"{WORD}.lab"
if lab_path.exists():
    lines = lab_path.read_text(encoding="utf-8").strip().splitlines()
    ok(f"{WORD}.lab の行数: {len(lines)} 行")
    info("--- 内容 ---")
    for line in lines:
        info(f"  {line}")
    info("------------")

    has_silB = any("silB" in l for l in lines)
    has_silE = any("silE" in l for l in lines)
    if has_silB and has_silE:
        ok("silB / silE（無音区間ラベル）が含まれています → 正常なアライメント結果")
    else:
        warn("silB または silE が見つかりません → アライメントが失敗している可能性があります")
else:
    ng(f"{WORD}.lab が見つかりません")


# ── 6. MFCCバイナリファイルの確認 ────────────────────────────────
sep(f"6. MFCCバイナリファイルの確認（{lib_label}）")

# words_db.json から単語数を動的に取得（ハードコード禁止）
expected_count = None
if words_db_path.exists():
    try:
        with words_db_path.open("r", encoding="utf-8") as f:
            db = json.load(f)
        expected_count = len(db)
        info(f"words_db.json に登録された単語数: {expected_count} 件")
    except Exception as e:
        warn(f"words_db.json の読み込みに失敗: {e}")
else:
    ng("words_db.json が見つかりません")

if mfcc_dir.exists():
    bin_files = list(mfcc_dir.glob("*.bin"))
    actual_count = len(bin_files)
    info(f"mfcc/ 内の .bin ファイル数: {actual_count} 個")

    if expected_count is not None:
        if actual_count == expected_count:
            ok(f"{actual_count} 個 / {expected_count} 個必要 → 正常")
        elif actual_count < expected_count:
            ng(f"{actual_count} 個しかありません（{expected_count} 個必要）")
            info("python scripts/regenerate_mfcc.py --user <利用者ID> を実行してください")
        else:
            warn(f"{actual_count} 個あります（{expected_count} 個必要）→ 不要な .bin があります")
    else:
        info(f"{actual_count} 個の .bin ファイルが見つかりました（期待値不明）")
else:
    ng(f"{mfcc_dir} が見つかりません")


# ── 7. audio.scp の内容確認 ──────────────────────────────────────
sep(f"7. audio.scp の内容確認（{lib_label}）")

if scp_path.exists():
    lines = scp_path.read_text(encoding="utf-8").strip().splitlines()
    info(f"audio.scp の行数: {len(lines)} 行")
    info(f"先頭3行: {[l.strip() for l in lines[:3]]}")

    missing = []
    for line in lines:
        wav = scp_base / line.strip()
        if not wav.exists():
            missing.append(line.strip())

    if not missing:
        ok("audio.scp に記載された全WAVファイルが存在します")
    else:
        ng(f"{len(missing)} 件のWAVファイルが見つかりません:")
        for m in missing[:5]:
            info(f"  {m}")
        if len(missing) > 5:
            info(f"  ... 他 {len(missing) - 5} 件")
else:
    ng("audio.scp が見つかりません")


# ── 8. VOICEVOX の確認 ───────────────────────────────────────────
sep("8. VOICEVOX の確認")

try:
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:50021/version", timeout=3) as res:
        version = res.read().decode().strip()
    ok(f"VOICEVOX が起動しています（バージョン: {version}）")
except Exception:
    warn("VOICEVOX に接続できません（http://127.0.0.1:50021）")
    info("単語追加機能を使う場合は VOICEVOX を起動してください")
    info("既存の音声ファイルは使えるため、通常の練習には影響しません")


# ── まとめ ────────────────────────────────────────────────────────
sep("診断まとめ")
print("  [NG] が出た項目が起動失敗の原因の可能性が高いです。")
print("  [!!] は警告です。動作する場合もありますが確認を推奨します。")
print("  結果をそのままコピーして共有してください。")
print()