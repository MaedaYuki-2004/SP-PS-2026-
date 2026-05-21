"""
regenerate_all_tts.py
全単語のサンプル音声を指定した VOICEVOX 話者で再生成するスクリプト。
source="recorded"（人間録音）の単語も含めてすべて上書きする。

【事前確認】
    python scripts/check_voicevox_speakers.py を実行して
    使いたい話者の ID を確認してから SPEAKER_ID を変更すること。

【実行手順】
    1. VOICEVOX を起動する
    2. このファイルの SPEAKER_ID を変更する
    3. python scripts/regenerate_all_tts.py を実行する
    4. 完了後に python scripts/regenerate_mfcc.py を実行する

【実行時間の目安】
    53単語 × 約2〜3秒 ≒ 約2〜3分
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# ─── ここを変更する ────────────────────────────────────────────
SPEAKER_ID = 11   # ← check_voicevox_speakers.py で確認した ID に変更
# ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import AUDIO_MFCC_DIR, DATA_DIR, ENGINE_DIR, JULIUS_BIN_PATH, PERL_SCRIPT_PATH, RAW_AUDIO_DIR
from core.audio import convert_to_16kHz

WORDS_DB_PATH = DATA_DIR / "config" / "words_db.json"
VOICEVOX_URL  = "http://127.0.0.1:50021"


# ── VOICEVOX ──────────────────────────────────────────────────────

def check_voicevox() -> str | None:
    """VOICEVOXの接続確認とバージョン取得。"""
    try:
        with urllib.request.urlopen(f"{VOICEVOX_URL}/version", timeout=5) as res:
            return res.read().decode().strip()
    except Exception:
        return None


def synthesize(text: str, speaker_id: int, out_path: Path) -> None:
    """VOICEVOXで音声合成して16kHz WAVを保存する。"""
    # audio_query（POST）
    query_url = (
        f"{VOICEVOX_URL}/audio_query"
        f"?text={urllib.parse.quote(text)}"
        f"&speaker={speaker_id}"
    )
    req = urllib.request.Request(query_url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=15) as res:
        query = json.loads(res.read().decode())

    # 出力を16kHz/モノラルに指定
    query["outputSamplingRate"] = 16000
    query["outputStereo"]       = False

    # synthesis（POST）
    data = json.dumps(query).encode("utf-8")
    req  = urllib.request.Request(
        f"{VOICEVOX_URL}/synthesis?speaker={speaker_id}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        wav_data = res.read()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(wav_data)

    # 念のため16kHzに変換（VOICEVOXが16kHzで返すが念のため）
    convert_to_16kHz(out_path, out_path)


# ── Julius アライメント ───────────────────────────────────────────

def run_julius(word_id: str, word_dir: Path) -> bool:
    """Julius アライメントを実行する。成功したら True を返す。"""
    import os

    env = dict(os.environ)
    if JULIUS_BIN_PATH:
        env["JULIUS_BIN"] = str(JULIUS_BIN_PATH)

    try:
        subprocess.run(
            ["perl", str(PERL_SCRIPT_PATH), str(word_dir)],
            cwd=str(ENGINE_DIR),
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"    [Julius エラー] {e.stderr.strip()[:120]}")
        return False

    lab_path = word_dir / f"{word_id}.lab"
    if not lab_path.exists() or lab_path.stat().st_size == 0:
        print("    [Julius エラー] .lab ファイルが生成されませんでした")
        return False
    return True


# ── メイン ────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  全単語 VOICEVOX 一括再生成スクリプト")
    print("=" * 60)

    # VOICEVOX 接続確認
    version = check_voicevox()
    if version is None:
        print("[エラー] VOICEVOX に接続できません。起動してから再実行してください。")
        sys.exit(1)
    print(f"  VOICEVOX バージョン : {version}")
    print(f"  使用する話者 ID    : {SPEAKER_ID}")
    print()

    # 話者名を取得して表示
    try:
        with urllib.request.urlopen(f"{VOICEVOX_URL}/speakers", timeout=10) as res:
            speakers = json.loads(res.read().decode())
        speaker_name = "不明"
        for sp in speakers:
            for st in sp["styles"]:
                if st["id"] == SPEAKER_ID:
                    speaker_name = f"{sp['name']}（{st['name']}）"
        print(f"  話者名 : {speaker_name}")
    except Exception:
        pass

    # 続行確認
    print()
    ans = input("  ⚠️  全53単語の音声を上書きします。続行しますか？ [y/N]: ").strip().lower()
    if ans != "y":
        print("  キャンセルしました。")
        sys.exit(0)
    print()

    # words_db.json の読み込み
    if not WORDS_DB_PATH.exists():
        print("[エラー] words_db.json が見つかりません")
        sys.exit(1)
    with WORDS_DB_PATH.open("r", encoding="utf-8") as f:
        db: dict = json.load(f)

    success_count = 0
    fail_words: list[str] = []

    for word_id, entry in db.items():
        display = entry.get("display", "")
        reading = entry.get("reading", "")
        print(f"[{word_id}] {display}（{reading}）")

        word_dir = RAW_AUDIO_DIR / "sound" / word_id
        wav_path = word_dir / f"{word_id}.wav"
        txt_path = word_dir / f"{word_id}.txt"
        word_dir.mkdir(parents=True, exist_ok=True)

        # ── 1. 音声合成 ──────────────────────────────────────────
        try:
            synthesize(display, SPEAKER_ID, wav_path)
            print(f"    音声生成 : OK ({wav_path.stat().st_size:,} bytes)")
        except Exception as e:
            print(f"    音声生成 : [エラー] {e}")
            fail_words.append(word_id)
            continue

        # ── 2. 読みファイルを更新 ─────────────────────────────────
        txt_path.write_text(reading, encoding="utf-8")

        # ── 3. Julius アライメント ────────────────────────────────
        # Julius は wav/ にファイルが必要なため一時コピー
        tmp_wav = RAW_AUDIO_DIR / "wav" / f"{word_id}.wav"
        tmp_txt = RAW_AUDIO_DIR / "wav" / f"{word_id}.txt"
        tmp_lab = RAW_AUDIO_DIR / "wav" / f"{word_id}.lab"
        tmp_log = RAW_AUDIO_DIR / "wav" / f"{word_id}.log"

        shutil.copy(wav_path, tmp_wav)
        shutil.copy(txt_path, tmp_txt)

        julius_ok = run_julius(word_id, RAW_AUDIO_DIR / "wav")

        if julius_ok:
            # アライメント結果を sound/ へ移動
            if tmp_lab.exists():
                shutil.move(str(tmp_lab), str(word_dir / f"{word_id}.lab"))
            if tmp_log.exists():
                shutil.move(str(tmp_log), str(word_dir / f"{word_id}.log"))
            print("    アライメント : OK")
            success_count += 1
        else:
            fail_words.append(word_id)

        # 一時ファイルを削除
        for p in [tmp_wav, tmp_txt, tmp_lab, tmp_log]:
            p.unlink(missing_ok=True)

    # ── 4. web/static/sample/ にサンプル音声を同期 ───────────────
    # app.py の sample_audio() は static/sample/ を優先するため、
    # 古いファイルが残っていると再生成後も旧音声が再生されてしまう。
    from config import STATIC_DIR
    sample_dir = STATIC_DIR / "sample"
    sample_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("  web/static/sample/ にサンプル音声を同期中...")
    synced = 0
    for word_id, entry in db.items():
        src = RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.wav"
        dst = sample_dir / f"{word_id}.wav"
        if word_id in fail_words:
            dst.unlink(missing_ok=True)
            continue
        if src.exists():
            shutil.copy2(str(src), str(dst))
            synced += 1
    print(f"  同期完了 : {synced} ファイル -> {sample_dir}")

    # ── 5. words_db.json の source を tts に統一 ─────────────────
    print()
    print("  words_db.json の source を全単語 'tts' に更新中...")
    for word_id in db:
        db[word_id]["source"] = "tts"
    with WORDS_DB_PATH.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print("  更新完了")

    # ── 6. accent.py の VOICEVOX_SPEAKER を更新 ──────────────────
    accent_path = ROOT / "core" / "accent.py"
    if accent_path.exists():
        text = accent_path.read_text(encoding="utf-8")
        import re
        new_text = re.sub(
            r"VOICEVOX_SPEAKER\s*=\s*\d+",
            f"VOICEVOX_SPEAKER = {SPEAKER_ID}",
            text,
        )
        if new_text != text:
            accent_path.write_text(new_text, encoding="utf-8")
            print(f"  core/accent.py の VOICEVOX_SPEAKER を {SPEAKER_ID} に更新しました")

    # ── 結果サマリー ──────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  完了 : {success_count} / {len(db)} 単語")
    if fail_words:
        print(f"  失敗 : {len(fail_words)} 件 → {', '.join(fail_words)}")
    print()
    print("  次のステップ：")
    print("  python scripts/regenerate_mfcc.py")
    print("=" * 60)


if __name__ == "__main__":
    main()