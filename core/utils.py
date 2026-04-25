"""
core/utils.py
汎用ユーティリティ関数群。
特定の解析ドメインに依存しない小さな処理をまとめる。
"""
from __future__ import annotations

import time
from pathlib import Path

from config import ALLOWED_EXTENSIONS


def allowed_file(filename: str) -> bool:
    """アップロードされたファイルの拡張子が許可リストに含まれるか確認する。"""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def sleep_second(seconds: float = 1.5) -> None:
    """指定秒数だけスリープする（Julius 処理待ち用）。"""
    time.sleep(seconds)


def pct_length(length: list[float]) -> list[float]:
    """各要素が合計に占める割合（%）を返す。"""
    total = sum(length)
    if total == 0:
        return [0.0 for _ in length]
    return [round((i / total) * 100, 2) for i in length]


def phone_list(frame: list[int | str]) -> list[list[int | str]]:
    """フラットなフレームリストを [start, end, phoneme] の3要素単位に分割する。"""
    return [frame[i:i + 3] for i in range(0, len(frame), 3)]


def phoneme_frame(phoneme: list[list[int | str]]) -> list[list[int | str]]:
    """音素フレームの開始・終了を先頭音素基準の相対フレーム番号に正規化する。"""
    if not phoneme:
        return phoneme
    start = int(phoneme[0][0])
    for item in phoneme:
        item[0] = int(item[0]) - start
        item[1] = int(item[1]) - start
    return phoneme