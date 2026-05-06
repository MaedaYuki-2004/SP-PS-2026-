"""
core/alignment.py
Julius を用いた音素アライメント処理を担当するモジュール。
Perl スクリプトの実行、lab / log ファイルの読み込みと解析をまとめる。

【修正点】
  - perl_run() の cwd を ENGINE_DIR に変更し、./models/ が正しく解決されるようにした。
  - Julius バイナリパスを環境変数 JULIUS_BIN 経由で Perl スクリプトに渡す。
  - mora_time() の欠落ケース（子音が末尾/子音が連続）を明示的に処理する。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from config import (
    AUDIO_WAV_DIR,
    CONSONANTS,
    ENGINE_DIR,
    JULIUS_BIN_PATH,
    PERL_SCRIPT_PATH,
    VOWELS,
)
from core.utils import phone_list, phoneme_frame


def perl_run() -> None:
    """
    segment_julius.pl を実行して音素アライメントを行う。

    実行ディレクトリを ENGINE_DIR（engine/）にすることで
    Perl スクリプト内の ./models/ および ./bin/ が正しく解決される。
    音声データディレクトリ（AUDIO_WAV_DIR）は引数で渡す。
    Julius バイナリパスは環境変数 JULIUS_BIN で渡す。
    """
    if not PERL_SCRIPT_PATH.exists():
        raise FileNotFoundError(
            f"Perl スクリプトが見つかりません: {PERL_SCRIPT_PATH}"
        )
    if not ENGINE_DIR.exists():
        raise FileNotFoundError(
            f"engine/ ディレクトリが見つかりません: {ENGINE_DIR}"
        )

    env = os.environ.copy()
    env["JULIUS_BIN"] = JULIUS_BIN_PATH  # Perl スクリプトに Julius パスを通知

    try:
        subprocess.run(
            ["perl", str(PERL_SCRIPT_PATH), str(AUDIO_WAV_DIR)],
            check=True,
            cwd=str(ENGINE_DIR),   # engine/ を起点にすることで ./models/ が解決される
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
    """
    音素リストをモーラ単位にまとめて返す。

    処理ルール：
      1. 子音 + 次が母音 → 結合してひとつのモーラ（次の母音はスキップ）
      2. 先頭が母音 → 単独モーラ
      3. 母音が連続（長音など） → 単独モーラ
      4. 特殊音素（N, q など）→ 単独モーラ
      5. 結合できなかった子音（末尾・次が子音）→ 単独モーラとして追加
         ※ 旧実装ではこのケースが無言でスキップされていたが、明示的に処理する。
    """
    mora_list: list[list[str | int | float]] = []
    skip_next = False  # 直前で子音+母音を結合したので次の母音をスキップするフラグ

    for i, entry in enumerate(phones):
        if skip_next:
            skip_next = False
            continue

        phone = str(entry[2])
        is_consonant = phone in CONSONANTS or (
            len(phone) == 2 and ":" not in phone
        )

        # ── 子音 + 次が母音 → 結合 ─────────────────────────────────
        if is_consonant and i < len(phones) - 1:
            next_entry = phones[i + 1]
            if str(next_entry[2]) in VOWELS:
                mora_list.append(
                    [entry[0], next_entry[1], phone + str(next_entry[2])]
                )
                skip_next = True  # 次の母音は結合済みなのでスキップ
                continue

        # ── 子音が末尾または次も子音（アライメント失敗時など） ───────
        if is_consonant:
            mora_list.append([entry[0], entry[1], phone])
            continue

        # ── 単独の母音 ────────────────────────────────────────────
        if phone in VOWELS:
            mora_list.append([entry[0], entry[1], phone])
            continue

        # ── 特殊音素（N: 撥音, q: 促音 など） ────────────────────
        mora_list.append([entry[0], entry[1], phone])

    return mora_list


def lab_load(lab_file: str | Path):
    """
    .lab ファイルを読み込んで音素・モーラ情報を返す。

    Returns
    -------
    lab_list, mora_list,
    phoneme, mora,
    phoneme_start, mora_start,
    phoneme_length, mora_length
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
    Julius の .log ファイルを読み込んでフレーム単位の音素情報を返す。

    Returns
    -------
    phoneme_list  : 絶対Juliusフレームの音素リスト
    phoneme_only  : 発話先頭基準の相対Juliusフレーム音素リスト
    mora_list     : 絶対Juliusフレームのモーラリスト
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