"""
core/alignment.py
Julius を用いた音素アライメント処理を担当するモジュール。

【MFA（Montreal Forced Aligner）対応】
  USE_MFA = True（config.py）のとき、Julius の代わりに MFA を使用する。

  MFA の利点：
    - 機械学習ベースのアライメントで学習者音声に強い
    - Julius スコアが -3000 以下になる頻度が下がる
    - モーラ境界の精度が向上し、スコア信頼性が上がる

  フォールバック設計：
    run_alignment() → mfa_run() 成功 → TextGrid → .lab + 合成.log
                   → mfa_run() 失敗 → perl_run() → .lab + .log（従来通り）

  app.py の変更：
    perl_run() → run_alignment() の1箇所のみ。
    lab_load() / log_load() は変更不要。

  TextGrid → .lab 変換の詳細：
    MFA は Praat の TextGrid 形式で結果を出力する。
    これを Julius 互換の .lab 形式に変換したうえで、
    log_load() が読める合成 .log ファイルを生成する。

    MFA の無音ラベルマッピング：
      先頭の "sil" → "silB"
      末尾の "sil" → "silE"
      中間の "sil" / "sp" → そのまま保持（lab_load 内でスキップされない）

    MFA の音素ラベルマッピング（julius 互換化）：
      "cl" → "q"（促音）
      "aː" → "a:"（長母音）など
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from config import (
    AUDIO_WAV_DIR,
    CONSONANTS,
    DATA_DIR,
    ENGINE_DIR,
    JULIUS_BIN_PATH,
    PERL_SCRIPT_PATH,
    TEST_LAB_PATH,
    TEST_LOG_PATH,
    TEST_WAV_PATH,
    USE_MFA,
    VOWELS,
)
from core.utils import phone_list, phoneme_frame


# ── MFA ラベル → Julius 互換への変換テーブル ─────────────────────────
_MFA_TO_JULIUS: dict[str, str] = {
    # 促音
    "cl":  "q",
    # 長母音（IPA 表記）
    "aː":  "a:",
    "iː":  "i:",
    "uː":  "u:",
    "eː":  "e:",
    "oː":  "o:",
    # 一部モデルで使われる別表記
    "a:":  "a:",
    "i:":  "i:",
    "u:":  "u:",
    "e:":  "e:",
    "o:":  "o:",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MFA 関連
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _mfa_available() -> bool:
    """mfa コマンドが使えるか確認する。"""
    return shutil.which("mfa") is not None


def _parse_textgrid_phones(text: str) -> list[tuple[float, float, str]]:
    """
    TextGrid テキストから phones ティアを解析して
    (start_sec, end_sec, label) のリストを返す。

    TextGrid のフォーマット（Praat 標準）：
        item [1]:
            class = "IntervalTier"
            name = "phones"
            ...
            intervals [N]:
                xmin = 0.0
                xmax = 0.075
                text = "sil"
    """
    intervals: list[tuple[float, float, str]] = []
    in_phones = False
    xmin: float | None = None
    xmax: float | None = None

    for line in text.splitlines():
        line = line.strip()

        # phones ティアの開始を検知
        if re.search(r'name\s*=\s*"phones"', line):
            in_phones = True
            continue

        # 次のティアに入ったら終了
        if in_phones and re.search(r'name\s*=\s*"', line):
            break

        if not in_phones:
            continue

        m = re.match(r"xmin\s*=\s*([\d.e+-]+)", line)
        if m:
            xmin = float(m.group(1))
            continue

        m = re.match(r"xmax\s*=\s*([\d.e+-]+)", line)
        if m:
            xmax = float(m.group(1))
            continue

        m = re.match(r'text\s*=\s*"(.*)"', line)
        if m and xmin is not None and xmax is not None:
            label = m.group(1).strip()
            intervals.append((xmin, xmax, label))
            xmin = xmax = None

    return intervals


def textgrid_to_lab(
    textgrid_path: Path,
    lab_path: Path,
) -> None:
    """
    MFA が出力した TextGrid を Julius 互換の .lab ファイルに変換する。

    変換ルール：
      - 先頭の無音（sil/sp） → silB
      - 末尾の無音（sil/sp） → silE
      - MFA固有ラベル → _MFA_TO_JULIUS テーブルで変換
      - 空ラベル（""）はスキップ
    """
    text      = textgrid_path.read_text(encoding="utf-8")
    intervals = _parse_textgrid_phones(text)

    if not intervals:
        raise ValueError(f"TextGrid に phones ティアが見つかりません: {textgrid_path}")

    # 有効な区間だけを残す（空ラベルを除く）
    valid = [(s, e, l) for s, e, l in intervals if l.strip()]

    lines = []
    for idx, (start, end, label) in enumerate(valid):
        # 無音ラベルの変換
        if label in ("sil", "sp"):
            if idx == 0:
                julius_label = "silB"
            elif idx == len(valid) - 1:
                julius_label = "silE"
            else:
                julius_label = label  # 中間無音はそのまま
        else:
            julius_label = _MFA_TO_JULIUS.get(label, label)

        lines.append(f"{start:.7f} {end:.7f} {julius_label}")

    lab_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _synthetic_log_from_lab(lab_path: Path, log_path: Path) -> None:
    """
    .lab ファイルから log_load() が読める合成 .log ファイルを生成する。

    Julius の .log は 10ms フレーム単位のため、
    .lab の秒単位の時刻をフレーム番号に変換する。

      frame = int(time_sec / 0.01)

    合成 .log の形式：
      begin forced alignment
      [  0  7] -1000.00 silB
      [  8 14]  -800.00 by
      ...
      end forced alignment
    """
    lines_out = ["begin forced alignment"]

    with lab_path.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            start_sec = float(parts[0])
            end_sec   = float(parts[1])
            label     = parts[2]

            # 秒 → 10ms フレームに変換
            start_frame = int(start_sec / 0.01)
            end_frame   = max(start_frame, int(end_sec / 0.01) - 1)

            lines_out.append(f"[ {start_frame} {end_frame}] -1000.00 {label}")

    lines_out.append("end forced alignment")
    log_path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")


def mfa_run() -> None:
    """
    MFA を使って音声アライメントを実行する。

    入力：
      - data/raw_audio/wav/test.wav  （録音音声）
      - words_db.json から display テキストを取得

    出力：
      - data/raw_audio/wav/test.lab  （Julius 互換形式に変換済み）
      - data/raw_audio/wav/test.log  （合成 log ファイル）

    MFA コマンド（v3.x）：
      mfa align_one test.wav display_text japanese_mfa japanese_mfa test.TextGrid

    前提条件：
      - conda activate mfa
      - python scripts/setup_mfa.py を実行済み
    """
    import json

    if not _mfa_available():
        raise RuntimeError("mfa コマンドが見つかりません。scripts/setup_mfa.py を実行してください。")

    # words_db から display テキストを取得
    words_db_path = DATA_DIR / "config" / "words_db.json"
    word_id_path  = DATA_DIR / "config" / "word_id.txt"

    word_id   = word_id_path.read_text(encoding="utf-8").strip()
    with words_db_path.open("r", encoding="utf-8") as f:
        db = json.load(f)
    display = db.get(word_id, {}).get("display", "")

    if not display:
        raise ValueError(f"words_db.json に {word_id} の display が見つかりません")

    wav_path  = TEST_WAV_PATH
    lab_path  = TEST_LAB_PATH
    log_path  = TEST_LOG_PATH

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir        = Path(tmpdir)
        text_path      = tmp_dir / "test.txt"    # ← 追加：テキストファイル
        textgrid_path  = tmp_dir / "test.TextGrid"

        # display テキストをファイルに書き出す
        # MFA align_one の第2引数はファイルパスのため、文字列をそのまま渡せない
        text_path.write_text(display, encoding="utf-8")

        # MFA align_one で単一ファイルをアライメント
        cmd = [
            "mfa", "align_one",
            str(wav_path),
            str(text_path),    # ← 文字列 → ファイルパスに変更
            "japanese_mfa",    # 辞書
            "japanese_mfa",    # 音響モデル
            str(textgrid_path),
            "--clean",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"MFA アライメントに失敗しました。\n"
                f"stderr: {result.stderr.strip()[-500:]}"
            )

        if not textgrid_path.exists():
            raise RuntimeError(f"TextGrid が生成されませんでした: {textgrid_path}")

        # TextGrid → Julius 互換 .lab に変換
        textgrid_to_lab(textgrid_path, lab_path)

    # .lab から合成 .log を生成
    _synthetic_log_from_lab(lab_path, log_path)


def run_alignment() -> None:
    """
    MFA が使える場合は MFA、使えない場合や失敗した場合は Julius でアライメントを実行する。

    config.py の USE_MFA フラグで動作を切り替える：
      USE_MFA = True  → MFA を試し、失敗したら Julius にフォールバック
      USE_MFA = False → Julius のみ（従来通り）

    app.py からは perl_run() の代わりにこの関数を呼び出す。
    """
    if USE_MFA:
        try:
            mfa_run()
            print("[alignment] MFA でアライメント完了")
            return
        except Exception as exc:
            print(f"[alignment] MFA 失敗 → Julius にフォールバック: {exc}")

    perl_run()


def run_alignment_on_file(
    wav_path: Path,
    reading: str,
    lab_out: Path,
    log_out: Path,
) -> None:
    """
    任意の WAV ファイルに対して Julius 強制アライメントを実行し、
    結果を lab_out / log_out に保存する。

    TEST_WAV_PATH などの固定パスに依存せず動作するため、
    お手本口形録画のアライメントなど、通常の録音フロー以外で
    アライメントが必要な場合に使用する。

    Julius のみ対応（MFA は words_db / word_id.txt を前提とするため使用しない）。
    """
    if not PERL_SCRIPT_PATH.exists():
        raise FileNotFoundError(f"Perl スクリプトが見つかりません: {PERL_SCRIPT_PATH}")
    if not ENGINE_DIR.exists():
        raise FileNotFoundError(f"engine/ ディレクトリが見つかりません: {ENGINE_DIR}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        shutil.copy(str(wav_path), str(tmp / "test.wav"))
        (tmp / "test.txt").write_text(reading, encoding="utf-8")

        env = os.environ.copy()
        env["JULIUS_BIN"] = str(JULIUS_BIN_PATH)

        try:
            subprocess.run(
                ["perl", str(PERL_SCRIPT_PATH), str(tmp)],
                check=True,
                cwd=str(ENGINE_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr_msg = exc.stderr.strip() if exc.stderr else "（詳細なし）"
            raise RuntimeError(
                f"Julius アライメントに失敗しました。\nstderr: {stderr_msg}"
            ) from exc

        tmp_lab = tmp / "test.lab"
        tmp_log = tmp / "test.log"
        if not tmp_lab.exists():
            raise RuntimeError("アライメント後 .lab が生成されませんでした")
        shutil.copy(str(tmp_lab), str(lab_out))
        if tmp_log.exists():
            shutil.copy(str(tmp_log), str(log_out))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Julius 関連（変更なし）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def perl_run() -> None:
    """
    segment_julius.pl を実行して音素アライメントを行う（Julius）。
    """
    if not PERL_SCRIPT_PATH.exists():
        raise FileNotFoundError(f"Perl スクリプトが見つかりません: {PERL_SCRIPT_PATH}")
    if not ENGINE_DIR.exists():
        raise FileNotFoundError(f"engine/ ディレクトリが見つかりません: {ENGINE_DIR}")

    env = os.environ.copy()
    env["JULIUS_BIN"] = str(JULIUS_BIN_PATH)

    try:
        subprocess.run(
            ["perl", str(PERL_SCRIPT_PATH), str(AUDIO_WAV_DIR)],
            check=True,
            cwd=str(ENGINE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr_msg = exc.stderr.strip() if exc.stderr else "（詳細なし）"
        raise RuntimeError(
            f"Julius アライメントに失敗しました。\n"
            f"Julius パス: {JULIUS_BIN_PATH}\n"
            f"stderr: {stderr_msg}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"外部プログラムの実行に失敗しました: {exc}") from exc


def mora_time(
    phones: list[list[str | int | float]],
) -> list[list[str | int | float]]:
    """音素リストをモーラ（拍）単位にまとめて返す。"""
    mora_list: list[list[str | int | float]] = []
    skip_next = False

    for i, entry in enumerate(phones):
        if skip_next:
            skip_next = False
            continue

        phone = str(entry[2])
        is_consonant = phone in CONSONANTS or (
            len(phone) == 2 and ":" not in phone
        )

        if is_consonant and i < len(phones) - 1:
            next_entry = phones[i + 1]
            if str(next_entry[2]) in VOWELS:
                mora_list.append(
                    [entry[0], next_entry[1], phone + str(next_entry[2])]
                )
                skip_next = True
                continue

        if is_consonant:
            mora_list.append([entry[0], entry[1], phone])
            continue

        if phone in VOWELS:
            mora_list.append([entry[0], entry[1], phone])
            continue

        mora_list.append([entry[0], entry[1], phone])

    return mora_list


def lab_load(lab_file: str | Path):
    """
    .lab ファイルを読み込んで音素・モーラ情報を返す。
    Julius / MFA 両方の出力に対応（MFA は textgrid_to_lab() で変換済み）。
    """
    lab_path = Path(lab_file)
    lab_list: list[list[str]]   = []
    phoneme_start: list[str]    = []
    phoneme: list[str]          = []
    phoneme_length: list[float] = []
    mora_start: list[str]       = []
    mora: list[str]             = []
    mora_length: list[float]    = []

    with lab_path.open("r", encoding="utf-8") as f:
        for line in f:
            a = line.split()
            if not a or "silB" in a or "silE" in a:
                continue
            lab_list.append(a)
            phoneme_start.append(a[0])
            phoneme.append(a[2])

    mora_list = mora_time(lab_list)
    for item in mora_list:
        mora_start.append(str(item[0]))
        mora.append(str(item[2]))
        mora_length.append(round(float(item[1]) - float(item[0]), 2))

    for item in lab_list:
        phoneme_length.append(round(float(item[1]) - float(item[0]), 2))

    return (
        lab_list, mora_list,
        phoneme, mora,
        phoneme_start, mora_start,
        phoneme_length, mora_length,
    )


def log_load(log_file: str | Path):
    """
    Julius の .log ファイルまたは MFA から生成した合成 .log を読み込む。
    どちらの場合も同じ形式のため、この関数は変更不要。
    """
    log_path = Path(log_file)
    in_alignment = False
    frame: list[int | str] = []

    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            if "begin forced alignment" in line:
                in_alignment = True
            elif "end forced alignment" in line:
                in_alignment = False

            if (
                in_alignment
                and "[" in line
                and "silB" not in line
                and "silE" not in line
            ):
                m = re.findall(r"\d+", line)
                if len(m) < 2:
                    continue
                n = re.search(r"[A-Z]|[a-z]+[:]?", line)
                if not n:
                    continue
                frame.extend([int(m[0]), int(m[1]), n.group()])

    phoneme_list  = phone_list(frame)
    phoneme_list2 = phone_list(frame.copy())
    mora_list     = mora_time(phoneme_list)
    phoneme_only  = phoneme_frame(phoneme_list2)

    return phoneme_list, phoneme_only, mora_list


def extract_julius_score(log_file: str | Path) -> float | None:
    """
    Julius / 合成 .log ファイルからアライメントの平均対数尤度を取得する。

    MFA の合成 .log は固定値 -1000.00 を使用しているため、
    MFA 使用時は常に -1000.00 が返る（品質ゲートの -3000 を超えるため通過する）。
    Julius 使用時は実際の対数尤度が返る。
    """
    log_path = Path(log_file)
    if not log_path.exists():
        return None

    in_alignment = False
    scores: list[float] = []

    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            if "begin forced alignment" in line:
                in_alignment = True
            elif "end forced alignment" in line:
                in_alignment = False

            if not in_alignment or "[" not in line:
                continue

            m = re.search(r"\]\s*([-\d.]+)", line)
            if m:
                try:
                    score_val = float(m.group(1))
                    if "silB" not in line and "silE" not in line:
                        scores.append(score_val)
                except ValueError:
                    pass

    if not scores:
        return None

    return round(float(np.mean(scores)), 2)