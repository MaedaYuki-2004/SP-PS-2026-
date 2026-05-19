from __future__ import annotations

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    CONFIG_DIR,
    ENGINE_DIR,
    JULIUS_BIN_PATH,
    PERL_SCRIPT_PATH,
    RAW_AUDIO_DIR,
    STATIC_DIR,
)
from core.accent import generate_sample_wav
from core.audio import convert_to_16kHz

WORDS_DB_PATH = CONFIG_DIR / "words_db.json"
STATIC_SAMPLE_DIR = STATIC_DIR / "sample"
SOUND_DIR = RAW_AUDIO_DIR / "sound"


def load_words() -> list[dict]:
    with WORDS_DB_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("words"), list):
            return data["words"]
        return list(data.values())

    raise ValueError("words_db.json の形式が想定外です")


def get_word_id(entry: dict, index: int) -> str:
    for key in ("word_id", "id"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"word{index + 1}"


def get_display(entry: dict) -> str:
    value = entry.get("display")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"display がありません: {entry}")


def get_reading(entry: dict) -> str:
    value = entry.get("reading")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"reading がありません: {entry}")


def ensure_dirs(word_id: str) -> Path:
    word_dir = SOUND_DIR / word_id
    word_dir.mkdir(parents=True, exist_ok=True)
    STATIC_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    return word_dir


def perl_run_for_word_dir(word_dir: Path) -> None:
    perl_bin = shutil.which("perl")
    if perl_bin is None:
        raise RuntimeError("Perl が見つかりません。")

    env = dict(os.environ)
    if JULIUS_BIN_PATH:
        env["JULIUS_BIN"] = JULIUS_BIN_PATH

    result = subprocess.run(
        [perl_bin, str(PERL_SCRIPT_PATH), str(word_dir)],
        cwd=str(ENGINE_DIR),
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "segment_julius.pl の実行に失敗しました。\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def validate_alignment(word_dir: Path, word_id: str) -> None:
    lab_path = word_dir / f"{word_id}.lab"
    log_path = word_dir / f"{word_id}.log"

    if not lab_path.exists():
        raise RuntimeError(f"lab が生成されていません: {lab_path}")

    lab_text = lab_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not lab_text:
        log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
        raise RuntimeError(
            f"lab が空です: {lab_path}\n"
            f"log:\n{log_text}"
        )


def regenerate_one(entry: dict, index: int) -> None:
    word_id = get_word_id(entry, index)
    display = get_display(entry)
    reading = get_reading(entry)

    word_dir = ensure_dirs(word_id)
    raw_wav_path = word_dir / f"{word_id}.wav"
    static_wav_path = STATIC_SAMPLE_DIR / f"{word_id}.wav"
    transcription_path = word_dir / f"{word_id}.txt"

    generate_sample_wav(display, raw_wav_path)
    convert_to_16kHz(raw_wav_path, raw_wav_path)
    transcription_path.write_text(reading, encoding="utf-8")
    perl_run_for_word_dir(word_dir)
    validate_alignment(word_dir, word_id)
    shutil.copy2(raw_wav_path, static_wav_path)


def main() -> None:
    words = load_words()
    for index, entry in enumerate(words):
        word_id = get_word_id(entry, index)
        print(f"[START] {word_id}")
        regenerate_one(entry, index)
        print(f"[DONE]  {word_id}")

    print("すべてのサンプル音声・アライメントを再生成しました。")
    print("続けて python scripts/regenerate_mfcc.py を実行してください。")


if __name__ == "__main__":
    main()