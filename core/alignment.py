"""
core/alignment.py
Julius を用いた音素アライメント処理を担当するモジュール。
Perl スクリプトの実行、lab / log ファイルの読み込みと解析をまとめる。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from config import (
    CONSONANTS,
    PERL_SCRIPT_PATH,
    RAW_AUDIO_DIR,
    VOWELS,
)
from core.utils import phone_list, phoneme_frame


def perl_run() -> None:
    """segment_julius.pl を実行して音素アライメントを行う。"""
    if not PERL_SCRIPT_PATH.exists():
        raise FileNotFoundError(
            f"Perl スクリプトが見つかりません: {PERL_SCRIPT_PATH}"
        )
    try:
        subprocess.run(
            ["perl", str(PERL_SCRIPT_PATH)],
            check=True,
            cwd=str(RAW_AUDIO_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        raise RuntimeError(f"外部プログラムの実行に失敗しました: {exc}") from exc


def mora_time(
    phones: list[list[str | int | float]],
) -> list[list[str | int | float]]:
    """
    音素リストをモーラ単位にまとめて返す。
    子音＋母音をひとつのモーラとして結合する。
    """
    mora_list: list[list[str | int | float]] = []

    for i, entry in enumerate(phones):
        phone = str(entry[2])

        # 先頭が母音の場合はそのままモーラとして追加
        if i == 0 and phone in VOWELS:
            mora_list.append([entry[0], entry[1], phone])
            continue

        if i < len(phones) - 1:
            next_phone = str(phones[i + 1][2])
            is_consonant = phone in CONSONANTS or (
                len(phone) == 2 and ":" not in phone
            )
            # 子音＋母音の結合
            if is_consonant and next_phone in VOWELS:
                mora_list.append(
                    [entry[0], phones[i + 1][1], phone + next_phone]
                )
                continue

        # 母音でも子音でもない音素（N, q など）
        if phone not in VOWELS and phone not in CONSONANTS:
            mora_list.append([entry[0], entry[1], phone])
            continue

        # 母音が連続する場合（長音など）
        if i >= 1 and phone in VOWELS and str(phones[i - 1][2]) in VOWELS:
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
    lab_list: list[list[str]] = []
    phoneme_start: list[str]  = []
    phoneme: list[str]        = []
    phoneme_length: list[float] = []
    mora_start: list[str]     = []
    mora: list[str]           = []
    mora_length: list[float]  = []

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
    phoneme_list  : フレーム情報付き音素リスト（生）
    phoneme_only  : 開始フレームを先頭基準に正規化した音素リスト
    mora_list     : フレーム情報付きモーラリスト
    """
    log_path = Path(log_file)
    num   = 0
    frame: list[int | str] = []

    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            if "begin forced alignment" in line:
                num = 1
            elif "end forced alignment" in line:
                num = 0

            if num == 1 and "[" in line and "silB" not in line and "silE" not in line:
                m = re.findall(r"\d+", line)
                if len(m) < 2:
                    continue
                n = re.search(r"[A-Z]|[a-z]+[:]?", line)
                if not n:
                    continue
                frame.extend([int(m[0]), int(m[1]), n.group()])

    phoneme_list = phone_list(frame)
    phoneme_list2 = phone_list(frame.copy())
    mora_list    = mora_time(phoneme_list)
    phoneme_only = phoneme_frame(phoneme_list2)

    return phoneme_list, phoneme_only, mora_list