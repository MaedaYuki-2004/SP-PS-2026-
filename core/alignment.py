"""
core/alignment.py
Julius を用いた音素アライメント処理を担当するモジュール。
MFA サポートは廃止。Julius のみ使用。
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
    VOWELS,
)
from core.utils import phone_list, phoneme_frame

# アライメント外部プロセスの制限時間（秒）。
# 通常は数秒で完了する。Julius は特定の音声入力で無限にハングすることがあり、
# 生き残ったプロセスが test.wav / test.lab を掴んだままになると
# 以後のすべての解析リクエストが詰まるため、必ず時間で打ち切る。
ALIGNMENT_TIMEOUT_SEC = 30.0


def _run_alignment_process(target_dir: str) -> None:
    """segment_julius.pl をタイムアウト付きで実行する。

    タイムアウト時は子プロセスツリーごと強制終了する（perl の下で
    julius が生き残るのを防ぐ）。

    パスは必ずスラッシュ区切りに変換して渡す。perl スクリプト内の
    system() がシェル経由でコマンド文字列を組み立てるため、Windows の
    バックスラッシュパスは msys perl（Git 付属）の sh に食われて壊れ、
    「julius が実行されないのに古い test.log から .lab が再生成される」
    という静かな失敗を引き起こす。

    stdout/stderr は encoding="utf-8" を明示する。text=True 任せだと
    Windows では locale（日本語環境で cp932）でデコードされ、perl/julius
    が出力する UTF-8 バイト列を読む際に読み取りスレッドが
    UnicodeDecodeError で死ぬ。スレッドは黙って落ちるだけで
    communicate() 自体は空文字列を返して先に進んでしまうため、
    本来表示されるべき julius のエラー詳細が握りつぶされる。
    """
    env = os.environ.copy()
    env["JULIUS_BIN"] = str(JULIUS_BIN_PATH).replace("\\", "/")

    kwargs: dict = dict(
        cwd=str(ENGINE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    if os.name != "nt":
        kwargs["start_new_session"] = True  # POSIX: プロセスグループごと殺せるように

    proc = subprocess.Popen(
        ["perl", str(PERL_SCRIPT_PATH).replace("\\", "/"), target_dir.replace("\\", "/")],
        **kwargs,
    )
    try:
        _out, err = proc.communicate(timeout=ALIGNMENT_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
        proc.wait()
        raise RuntimeError(
            f"アライメントが {ALIGNMENT_TIMEOUT_SEC:.0f} 秒以内に完了しませんでした。"
            "録音をやり直してください。"
        )
    if proc.returncode != 0:
        stderr_msg = err.strip() if err else "（詳細なし）"
        raise RuntimeError(
            f"Julius アライメントに失敗しました。\n"
            f"Julius パス: {JULIUS_BIN_PATH}\n"
            f"stderr: {stderr_msg}"
        )


def _lab_has_content(lab_path: Path) -> bool:
    """.lab に実際のアライメント結果が書かれているか確認する。

    segment_julius.pl は `open(RESULT, "> $f.lab")` で結果ファイルを
    先に作成してから中身を書き込むため、Julius が forced alignment に
    失敗して1行も出力しなくても、0バイトの .lab が「存在」してしまう。
    存在チェックだけでは検出できないため、中身の有無を別途確認する。
    """
    try:
        return bool(lab_path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def _alignment_failure_detail(log_path: Path) -> str:
    """julius/perl のログから失敗理由の手がかりを抜き出す。

    呼び出し元（register_word など）は失敗時にこの単語のディレクトリ
    ごと削除することがあり、ログファイル自体は跡形もなく消える。
    「発話が認識できませんでした」というユーザー向け文言だけでは
    環境差異（ffmpeg変換不良・engineの設定不備など）の切り分けが
    サーバーコンソールからも一切できなくなるため、例外メッセージに
    診断用の抜粋を必ず含めておく。
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "（ログファイルなし）"
    if not text.strip():
        return "（ログが空＝julius が起動していない可能性）"
    error_lines = [ln for ln in text.splitlines() if "rror" in ln]
    if error_lines:
        return " / ".join(error_lines[:5])
    return text[-800:].strip() or "（ログに手がかりなし）"


def run_alignment() -> None:
    """
    Julius で強制アライメントを実行する。
    data/raw_audio/wav/test.wav → test.lab / test.log を生成する。
    """
    if not PERL_SCRIPT_PATH.exists():
        raise FileNotFoundError(f"Perl スクリプトが見つかりません: {PERL_SCRIPT_PATH}")
    if not ENGINE_DIR.exists():
        raise FileNotFoundError(f"engine/ ディレクトリが見つかりません: {ENGINE_DIR}")

    # 前回の結果を必ず消す。残っていると julius の実行に失敗しても
    # perl スクリプトが古い test.log から .lab を再生成してしまい、
    # 「前回の録音の結果」が今回の結果として返る静かな失敗になる。
    for stale in (AUDIO_WAV_DIR / "test.log", AUDIO_WAV_DIR / "test.lab"):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass

    _run_alignment_process(str(AUDIO_WAV_DIR))

    lab_path = AUDIO_WAV_DIR / "test.lab"
    if not _lab_has_content(lab_path):
        detail = _alignment_failure_detail(AUDIO_WAV_DIR / "test.log")
        raise RuntimeError(
            "アライメントに失敗しました（発話が認識できませんでした）。"
            f"録音をやり直してください。\n[診断情報] {detail}"
        )


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
    """
    if not PERL_SCRIPT_PATH.exists():
        raise FileNotFoundError(f"Perl スクリプトが見つかりません: {PERL_SCRIPT_PATH}")
    if not ENGINE_DIR.exists():
        raise FileNotFoundError(f"engine/ ディレクトリが見つかりません: {ENGINE_DIR}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        shutil.copy(str(wav_path), str(tmp / "test.wav"))
        (tmp / "test.txt").write_text(reading, encoding="utf-8")

        _run_alignment_process(str(tmp))

        tmp_lab = tmp / "test.lab"
        tmp_log = tmp / "test.log"
        # 失敗時に呼び出し元が単語ごと削除しても診断できるよう、
        # 成否を確認する前にログだけは先に退避しておく。
        if tmp_log.exists():
            shutil.copy(str(tmp_log), str(log_out))
        if not _lab_has_content(tmp_lab):
            detail = _alignment_failure_detail(tmp_log)
            raise RuntimeError(
                "アライメントに失敗しました（発話が認識できませんでした）。"
                f"録音をやり直してください。\n[診断情報] {detail}"
            )
        shutil.copy(str(tmp_lab), str(lab_out))


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
    """Julius の .log ファイルを読み込む。"""
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
    Julius の .log ファイルからアライメントの平均対数尤度を取得する。
    値が -3000 以下の場合はアライメント品質が低い（再録音推奨）。
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
