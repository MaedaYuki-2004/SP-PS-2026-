"""
app.py — Flask ルーティング

【変更点】
  calc_vowel_score() に pitch_ceiling_native=ceiling_sample を追加。
  ネイティブとユーザー両方の性別を考慮した補正を有効化する。
"""
from __future__ import annotations

import csv
import functools
import io
import math
import os
import queue
import shutil
import tempfile
import threading
import time
import traceback
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

# librosa/NumbaがPythonのインストール先へキャッシュを書こうとして
# 権限制限下で止まることを防ぐ。明示された設定は優先する。
os.environ.setdefault("NUMBA_CACHE_DIR", str(Path(__file__).resolve().parent / ".tmp" / "numba-cache"))

try:
    import cv2
    import mediapipe as mp
    import numpy as np
    MEDIA_PIPE_AVAILABLE = True
except ImportError:
    cv2 = mp = np = None
    MEDIA_PIPE_AVAILABLE = False

from flask import Flask, Response, g, jsonify, redirect, render_template, request, send_file, session

from config import (
    AUDIO_MFCC_DIR, AUDIO_WAV_DIR, CONFIG_DIR,
    FLASK_SECRET_KEY, STATIC_DIR, TEMPLATES_DIR,
    WORD_ID_MEMO_PATH,
)
from core.audio     import convert_to_16kHz, read_sample, segment_audio
from core.vocab     import (
    WordExists, active_words_db, archive_word, discard_new_word, find_same_words, get_all_tags,
    get_reading_for_julius, get_word, list_archived_words, list_words, purge_word, register_word,
    restore_word, update_word, update_word_note, update_word_tags,
)
from core.alignment import lab_load, log_load, run_alignment, extract_julius_score, run_alignment_on_file
from core.pitch     import comp, estimate_pitch_range, hz_to_semitone, length_arrange, praat_pitch, resample_to_10ms, scale, smooth
from core.evaluate  import calc_total_score, calc_speaking_rate, calc_mora_scores
from core.formant   import extract_mora_formants, calc_vowel_score, calc_voice_quality, speaker_formant_scale
from core.timbre    import audio_mfcc, dtw_ascending_order
from core.history   import save_record, load_history, get_last_score, get_stats, load_word_history, get_daily_counts, get_overall_score, get_weekly_report, get_week_activity
from core.utils     import pct_length, romaji_mora_to_kana, long_vowel_groups, long_vowel_spans
from core.analysis  import compute_learning_stats
from core.dtw       import fastdtw
from core.confidence import bootstrap_ci, needs_more_data
from core import accounts as acc
from core import teacher
from core import usercontext
from core.workspace import current_workspace
from core.library import current_library, reference_id
from core.public_access import client_key, public_url, url_from_host
from core import capacity, storage
from core.capacity import ServerBusy, heavy_slot
from core.ratelimit import FailureLimiter
from core.storage import RUN_DIR, write_json_atomic
from core.workspace import clear_lip_dir

# Julius の音素ごとのスコア（1フレームあたりの平均対数尤度）の平均がこれより低ければ採点しない。
# 以前は -3000 だったが、extract_julius_score() が返すのは 1フレームあたりの値（正常で -25〜-30 程度）
# なので一度も発動していなかった（-3000 は発話全体の合計スコアの桁）。
# 2026-09-14 の計測: 過去のお手本 86 本は -25.8〜-30.3、生徒の録音は -27.9〜-29.9、
# わざと別の単語の読みでアライメントさせた 12 本も -27.9〜-30.7 で、この値では
# 「別の単語を言った」を見分けられない（雑音・無音はアライメント自体が失敗する）。
# そのため、観測した値より十分低い、明らかに異常な録音だけを止める値にしている。
JULIUS_GATE_THRESHOLD = -35.0

app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)
app.secret_key = FLASK_SECRET_KEY
# ログインと先生モードの Cookie を、別のサイトから送られた POST には付けない（CSRF 対策）。
# 単語の削除などは JSON で受けるが request.get_json(force=True) なので、Cookie が付くと別サイトのフォームからも届きうる
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
# 受け取るファイルの上限。お手本・口の録画は3秒で数 MB、録音の WAV は 1 MB 未満。大きなファイルでディスクや
# メモリを使い切られないようにする（超えると 413）
MAX_UPLOAD_MB = 50
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# ── ユーティリティ ────────────────────────────────────────────────────

def ensure_directories() -> None:
    for d in [CONFIG_DIR, AUDIO_WAV_DIR, AUDIO_MFCC_DIR, STATIC_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not (AUDIO_WAV_DIR / "test.txt").exists():
        (AUDIO_WAV_DIR / "test.txt").touch()
    if not WORD_ID_MEMO_PATH.exists():
        WORD_ID_MEMO_PATH.touch()


# ── MediaPipe FaceMesh の貸し出し ────────────────────────────────────
# FaceMesh は前のフレームの続きとして顔を追うので、1つを複数のスレッドで同時に使うと、別の人の
# 動画のフレームが混ざって結果が崩れる（以前は1つを全員で共有していた。サーバーが1件ずつしか
# 処理しなかったので問題にならなかった）。同時に動く重い処理の数（core/capacity.py）まで作って
# 使い回し、使っている間は1つのスレッドだけが持つ。
_face_mesh_pool: queue.LifoQueue = queue.LifoQueue()
_face_mesh_made = 0
_face_mesh_lock = threading.Lock()


def _new_face_mesh():
    return mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )


@contextmanager
def _face_mesh():
    """FaceMesh を1つ借りる（with の間はこのスレッドだけが使う）。"""
    global _face_mesh_made
    if not MEDIA_PIPE_AVAILABLE:
        raise RuntimeError("MediaPipe と OpenCV がインストールされていません。")
    try:
        mesh = _face_mesh_pool.get_nowait()
    except queue.Empty:
        with _face_mesh_lock:
            make = _face_mesh_made < capacity.HEAVY_SLOTS
            if make:
                _face_mesh_made += 1
        if make:
            try:
                mesh = _new_face_mesh()
            except Exception:
                with _face_mesh_lock:
                    _face_mesh_made -= 1
                raise
        else:
            try:
                mesh = _face_mesh_pool.get(timeout=capacity.HEAVY_WAIT_SECONDS)
            except queue.Empty:
                raise ServerBusy() from None
    try:
        yield mesh
    finally:
        _face_mesh_pool.put(mesh)


def calc_distance(p1, p2, w, h):
    x1, y1 = int(p1.x * w), int(p1.y * h)
    x2, y2 = int(p2.x * w), int(p2.y * h)
    return math.hypot(x2 - x1, y2 - y1)


# MediaPipe で唇の形状を特徴量ベクトル化

def get_lip_shape_vector(landmarks, w, h):
    face_scale = calc_distance(landmarks.landmark[33], landmarks.landmark[263], w, h)
    if face_scale == 0:
        face_scale = 1.0

    top_inner = landmarks.landmark[13]
    bottom_inner = landmarks.landmark[14]
    center_x = (top_inner.x + bottom_inner.x) / 2 * w
    center_y = (top_inner.y + bottom_inner.y) / 2 * h

    shape_vector = []
    LIP_POINTS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 185, 40, 39, 37, 0, 267, 269, 270, 409]
    for idx in LIP_POINTS:
        pt = landmarks.landmark[idx]
        px, py = pt.x * w, pt.y * h
        dist_to_center = math.hypot(px - center_x, py - center_y)
        shape_vector.append(dist_to_center / face_scale)

    return np.array(shape_vector)


def _extract_lip_data_timed(
    video_path: str, max_frames: int = 50,
) -> tuple[list[list[float]], list[float], list[float]]:
    """唇形状ベクトル・開き比率に加えて各フレームの時刻（秒）を返す。

    顔検出に失敗したフレームはスキップされるため、ベクトルの並び順から
    時刻を復元することはできない。音映像同期分析（core/sync.py）が
    正確な時刻対応を必要とするので、フレーム時刻を明示的に記録する。
    """
    with _face_mesh() as face_mesh:
        return _extract_lip_frames(face_mesh, video_path, max_frames)


def _extract_lip_frames(
    face_mesh, video_path: str, max_frames: int,
) -> tuple[list[list[float]], list[float], list[float]]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"動画を開けませんでした: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 120:
        fps = 30.0

    vectors: list[list[float]] = []
    ratios:  list[float] = []
    times:   list[float] = []
    frame_index = 0

    while len(vectors) < max_frames:
        success, frame = cap.read()
        if not success:
            break
        # POS_MSEC は WebM で信頼できないことがあるため fps ベースで算出
        t_sec = frame_index / fps
        frame_index += 1
        image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(image_rgb)
        if not results.multi_face_landmarks:
            continue

        face_landmarks = results.multi_face_landmarks[0]
        h, w, _ = frame.shape
        vectors.append(get_lip_shape_vector(face_landmarks, w, h).tolist())
        times.append(round(t_sec, 4))

        top_lip = face_landmarks.landmark[13]
        bottom_lip = face_landmarks.landmark[14]
        left_lip = face_landmarks.landmark[61]
        right_lip = face_landmarks.landmark[291]
        v_dist = calc_distance(top_lip, bottom_lip, w, h)
        h_dist = calc_distance(left_lip, right_lip, w, h)
        ratios.append(v_dist / (h_dist + 1e-6))

        if frame_index > max_frames * 10:
            break

    cap.release()
    if not vectors:
        raise ValueError("顔ランドマークが検出できませんでした。録画をやり直してください。")
    return vectors, ratios, times


def _compute_lip_score(reference_vectors, test_vectors):
    """唇形状ベクトル列の DTW 距離からスコアを算出する。

    DTW 距離はフレーム数に比例して増えるため、経路長で正規化して
    録画の長さに依存しないスコアにする（1ステップあたりの平均距離）。
    """
    if not reference_vectors or not test_vectors:
        raise ValueError("お手本とテストの両方が必要です。")
    distance, path = fastdtw(reference_vectors, test_vectors)
    per_step = distance / max(1, len(path))
    # per_step の目安: 0.05=ほぼ一致 / 0.15=近い / 0.3以上=大きく異なる
    score = int(round(100 - per_step * 200))
    return max(0, min(100, score)), round(per_step, 4)


# ── モーラ別 口の開き具合の分析 ──────────────────────────────────────────

# 日本語音声学における母音別の口の開き目安（あ=最大開口, い/う=最小）
_VOWEL_OPENNESS = {'a': 1.0, 'e': 0.55, 'o': 0.45, 'i': 0.15, 'u': 0.10}
_LIP_SAMPLE_RATIOS = [0.30, 0.50, 0.70]  # モーラ内で測定する時刻の割合


def _mora_expected_openness(label):
    """モーララベルから期待される口の開き具合(0–1)を返す。母音なしは None。"""
    for ch in label.lower():
        if ch in _VOWEL_OPENNESS:
            return _VOWEL_OPENNESS[ch]
    return None


def _extract_mora_lip_openness(video_path, mora_list_sec, times=None, ratios=None):
    """
    Julius アライメント結果のモーラ時刻を使って映像から口の開き量を取得する。

    times / ratios に _extract_lip_data_timed の抽出結果を渡すと、
    その時刻範囲内のサンプルは動画を再デコードせずに流用する
    （MediaPipe の二重処理と全フレームのメモリ保持を回避）。
    抽出範囲より後ろのサンプルだけ動画から補完する。
    """
    if not MEDIA_PIPE_AVAILABLE:
        return []
    leases = ExitStack()   # 動画から補うときに借りる FaceMesh を、最後に必ず返す
    try:
        # 各モーラのサンプル時刻を列挙: (モーラ番号, 時刻)
        sample_specs: list[tuple[int, float]] = []
        for mi, mora_info in enumerate(mora_list_sec):
            start_sec = float(mora_info[0])
            duration  = float(mora_info[1]) - start_sec
            for ratio in _LIP_SAMPLE_RATIOS:
                sample_specs.append((mi, start_sec + duration * ratio))

        samples_by_mora: dict[int, list[float]] = {}
        pending = sample_specs

        # ── 抽出済みデータの流用 ──────────────────────────────────
        if times and ratios and len(times) == len(ratios):
            t_arr = np.asarray(times, dtype=float)
            if len(t_arr) > 1:
                diffs = np.diff(t_arr)
                pos   = diffs[diffs > 0]
                dt    = float(np.median(pos)) if len(pos) else 1 / 30.0
            else:
                dt = 1 / 30.0
            coverage_end = float(t_arr[-1]) + dt
            pending = []
            for mi, t in sample_specs:
                idx = int(np.argmin(np.abs(t_arr - t)))
                if abs(float(t_arr[idx]) - t) <= dt:
                    samples_by_mora.setdefault(mi, []).append(float(ratios[idx]))
                elif t > coverage_end:
                    pending.append((mi, t))
                # 範囲内なのに近傍フレームが無い＝顔検出失敗区間 → スキップ（従来同様）

        # ── 流用でカバーできなかったサンプルだけ動画から補完 ──────────
        if pending:
            face_mesh = leases.enter_context(_face_mesh())
            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS)
                if fps <= 0 or fps > 120:
                    fps = 30.0

                # フレーム番号 → そのフレームを使うモーラ番号のリスト
                needed: dict[int, list[int]] = {}
                for mi, t in pending:
                    needed.setdefault(max(0, int(t * fps)), []).append(mi)

                def _measure(frame) -> float | None:
                    h, w, _ = frame.shape
                    res = face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    if not res.multi_face_landmarks:
                        return None
                    lm = res.multi_face_landmarks[0].landmark
                    v  = calc_distance(lm[13], lm[14], w, h)
                    hd = calc_distance(lm[61], lm[291], w, h)
                    return v / (hd + 1e-6)

                max_fidx = max(needed)
                fidx = 0
                last_frame = None
                while fidx <= max_fidx:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    last_frame = frame
                    if fidx in needed:
                        val = _measure(frame)
                        if val is not None:
                            for mi in needed.pop(fidx):
                                samples_by_mora.setdefault(mi, []).append(val)
                        else:
                            needed.pop(fidx)
                    fidx += 1
                cap.release()

                # 動画末尾を超えたサンプルは最終フレームで代用（従来の clamp と同じ）
                if needed and last_frame is not None:
                    val = _measure(last_frame)
                    if val is not None:
                        for fi in list(needed):
                            for mi in needed.pop(fi):
                                samples_by_mora.setdefault(mi, []).append(val)

        results = []
        for mi, mora_info in enumerate(mora_list_sec):
            vals = samples_by_mora.get(mi, [])
            results.append({
                "label":    str(mora_info[2]),
                "openness": float(np.mean(vals)) if vals else None,
            })
        return results
    except Exception:
        traceback.print_exc()
        return []
    finally:
        leases.close()


def _normalize_p95(vals: list) -> float:
    """外れ値に頑健な正規化係数を返す（95 パーセンタイル）。"""
    if not vals:
        return 1.0
    p95 = float(np.percentile(vals, 95)) if len(vals) >= 2 else float(vals[0])
    return p95 if p95 > 1e-6 else 1.0


def _lip_diff_status(diff: float) -> tuple[str, str]:
    if abs(diff) < 0.15:
        return "good", "良好"
    if diff < 0:
        return "low", "開きが足りない"
    return "high", "開きすぎ"


def _build_lip_mora_analysis(mora_list_sec, raw_data):
    """
    モーラごとの期待値（音声学テーブル）と実測値を比較してフィードバックを生成する。
    実測値は 95 パーセンタイルで正規化（最大値の外れ値に頑健）。
    """
    valid = [m["openness"] for m in raw_data if m["openness"] is not None]
    max_v = _normalize_p95(valid)

    result = []
    n = min(len(mora_list_sec), len(raw_data))
    for i in range(n):
        label    = str(mora_list_sec[i][2])
        raw_open = raw_data[i]["openness"]
        expected = _mora_expected_openness(label)
        actual   = round(min(raw_open / max_v, 1.5), 3) if raw_open is not None else None

        if expected is not None and actual is not None:
            diff = round(actual - expected, 3)
            status, feedback = _lip_diff_status(diff)
        else:
            diff = status = feedback = None

        result.append({
            "label":    label,
            "expected": expected,
            "actual":   actual,
            "diff":     diff,
            "status":   status,
            "feedback": feedback,
        })
    return result


def _build_lip_mora_comparison(mora_list_sec, raw_user, ref_mora_data):
    """
    ユーザーのモーラ別口の開き量を、アライメント済みのお手本データと位置基準で比較する。

    【修正点】
    - 比較はインデックス基準（mora[i] ↔ ref[i]）で行う。
      ラベル基準の平均化を廃止し、位置が対応するモーラ同士を直接比較する。
    - 正規化は 95 パーセンタイルを使用（最大値の外れ値に頑健）。
    - n = min(mora_list_sec, raw_user, ref_mora_data) で3者の長さを揃える。
      ref_mora_data のモーラ数を超える分は期待値テーブルにフォールバック。
    """
    # 95パーセンタイル正規化（外れ値に頑健）
    user_vals = [d["openness"]   for d in raw_user     if d.get("openness")   is not None]
    ref_vals  = [d["v_h_ratio"]  for d in ref_mora_data if d.get("v_h_ratio") is not None]
    user_max  = _normalize_p95(user_vals)
    ref_max   = _normalize_p95(ref_vals)

    # 3者の最小モーラ数で位置基準比較する範囲を決定
    n_ref    = min(len(mora_list_sec), len(raw_user), len(ref_mora_data))
    n_total  = min(len(mora_list_sec), len(raw_user))

    result = []

    # ── 位置 i が ref_mora_data 内 → お手本と直接比較 ────────────────
    for i in range(n_ref):
        label    = str(mora_list_sec[i][2])
        raw_open = raw_user[i].get("openness")
        actual   = round(min(raw_open / user_max, 1.5), 3) if raw_open is not None else None

        ref_val  = ref_mora_data[i].get("v_h_ratio")
        expected = round(min(ref_val / ref_max, 1.5), 3) if ref_val is not None else None

        # 参照値が None のモーラは音声学期待値にフォールバック
        if expected is None:
            expected = _mora_expected_openness(label)

        if expected is not None and actual is not None:
            diff = round(actual - expected, 3)
            status, feedback = _lip_diff_status(diff)
        else:
            diff = status = feedback = None

        result.append({
            "label":    label,
            "expected": expected,
            "actual":   actual,
            "diff":     diff,
            "status":   status,
            "feedback": feedback,
        })

    # ── 位置 i が ref_mora_data 範囲外 → 期待値テーブルにフォールバック ──
    for i in range(n_ref, n_total):
        label    = str(mora_list_sec[i][2])
        raw_open = raw_user[i].get("openness")
        actual   = round(min(raw_open / user_max, 1.5), 3) if raw_open is not None else None
        expected = _mora_expected_openness(label)

        if expected is not None and actual is not None:
            diff = round(actual - expected, 3)
            status, feedback = _lip_diff_status(diff)
        else:
            diff = status = feedback = None

        result.append({
            "label":    label,
            "expected": expected,
            "actual":   actual,
            "diff":     diff,
            "status":   status,
            "feedback": feedback,
        })

    return result


def _webm_to_wav(webm_path: str) -> str:
    """
    WebM 動画ファイルから音声を抽出し、16kHz/モノラル/16bit WAV として返す。
    pydub（FFmpeg バックエンド）を使用する。
    戻り値は一時 WAV ファイルのパス。呼び出し元が削除すること。
    """
    from pydub import AudioSegment
    audio = AudioSegment.from_file(webm_path, format="webm")
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp.close()
    audio.export(tmp.name, format="wav")
    return tmp.name


def _align_lip_ref(
    wav_path: str,
    reading: str,
    lab_out: Path | None = None,
    log_out: Path | None = None,
) -> list:
    """
    お手本口形録画から抽出した音声を Julius でアライメントし、
    mora_list（[[start_sec, end_sec, label], ...]）を返す。
    lab_out / log_out を指定すると、その場所に直接書き込む（削除しない）。
    アライメント失敗時は空リストを返す。
    """
    use_tmp_lab = lab_out is None
    use_tmp_log = log_out is None
    lab_path = Path(tempfile.mktemp(suffix=".lab")) if use_tmp_lab else lab_out
    log_path = Path(tempfile.mktemp(suffix=".log")) if use_tmp_log else log_out
    try:
        run_alignment_on_file(Path(wav_path), reading, lab_path, log_path)
        if not lab_path.exists():
            return []
        (_, mora_list, *_) = lab_load(lab_path)
        return mora_list
    except Exception:
        traceback.print_exc()
        return []
    finally:
        if use_tmp_lab:
            try:
                if lab_path.exists():
                    lab_path.unlink()
            except Exception:
                pass
        if use_tmp_log:
            try:
                if log_path.exists():
                    log_path.unlink()
            except Exception:
                pass


def _save_temp_video(uploaded_file, directory=None):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.webm', dir=directory)
    uploaded_file.save(tmp.name)
    tmp.close()
    return tmp.name


def _lip_video_error(exc: Exception) -> str:
    """口の動画を使えなかったときの、利用者向けの説明（サーバーのファイルの場所などは出さない）。"""
    if "顔" in str(exc):
        return "顔（口元）が映っていませんでした。明るい場所で、顔を点線の枠に合わせて録り直してください。"
    return "録画を読み込めませんでした。もう一度録画してください。"


def _persist_ref_video(word_id: str, video_path: str) -> None:
    """お手本録画（webm）を sound_dir に永続保存する。

    先生の実際の口の動きを母音トレーナー・録音画面で再生できるように、
    抽出処理のためだけに使い捨てていた動画ファイルを保存しておく。
    失敗しても登録処理自体は継続してよいので例外は握りつぶす。
    """
    try:
        dest = current_library().sample_video(word_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_path, dest)
    except Exception:
        traceback.print_exc()


def _lip_refs_path() -> Path:
    return current_library().lip_refs


def load_lip_refs() -> dict:
    p = _lip_refs_path()
    try:
        if p.exists():
            import json
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_lip_refs(d: dict) -> None:
    try:
        # 書きかけを結果画面などに読まれないよう、一時ファイルに書いてから置き換える
        write_json_atomic(_lip_refs_path(), d, indent=None)
    except Exception:
        traceback.print_exc()


def _score_delta(current, prev) -> str | None:
    if current is None or prev is None:
        return None
    diff = round(float(current) - float(prev), 1)
    if diff == 0:
        return None
    return f"+{diff}" if diff > 0 else str(diff)


_SILENCE_LABELS = {'silb', 'sile', 'sp', 'sil', 'sp2'}

# お手本ピッチデータのインメモリキャッシュ: {word_id: (mtime, data)}
_REF_PITCH_CACHE: dict[str, tuple[float, dict]] = {}


def _get_reference_pitch_data(word_id: str) -> dict:
    """お手本音声のピッチ曲線とモーラタイミングをJSON用辞書で返す。

    WAV ファイルの mtime が変わっていなければキャッシュを返す。
    変わっていれば再計算してキャッシュを更新する。
    """
    lib       = current_library()
    sound_dir = lib.sound_dir(word_id)
    wav_path  = sound_dir / f"{word_id}.wav"
    lab_path  = sound_dir / f"{word_id}.lab"

    if not wav_path.exists() or not lab_path.exists():
        return {"has_data": False}

    # 単語 ID は参加者ごとに振るので、キャッシュはファイルの場所で引く
    cache_key = str(wav_path)
    mtime = wav_path.stat().st_mtime
    cached = _REF_PITCH_CACHE.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        floor, ceiling  = estimate_pitch_range(str(wav_path))
        pitch_hz, times = praat_pitch(str(wav_path), pitch_floor=floor, pitch_ceiling=ceiling)
        pitch_10ms      = resample_to_10ms(pitch_hz, times)
        pitch_filled    = comp(pitch_10ms)
        pitch_smoothed  = smooth(pitch_filled, window=5)
        pitch_scaled    = scale(pitch_smoothed)

        (_, mora_list, *_) = lab_load(str(lab_path))
        duration_sec = len(pitch_10ms) * 0.01

        # lab_load() は silB/silE をスキップするため mora_list に語頭無音が含まれない。
        # ピッチグラフのカーソル（0→duration_sec）と視覚的に揃えるため、
        # 語頭・語末の無音区間を明示的なスペーサーとして追加する。
        speech_start = round(float(mora_list[0][0]), 4) if mora_list else 0.0
        speech_end   = round(float(mora_list[-1][1]), 4) if mora_list else duration_sec

        moras: list[dict] = []
        if speech_start > 0.005:
            moras.append({"label": "silB", "start": 0.0, "end": speech_start, "is_sil": True})
        for m in mora_list:
            moras.append({
                # Julius のローマ字ラベルをかなに変換（聴覚障碍ユーザーに読みやすく）
                "label":  romaji_mora_to_kana(str(m[2])),
                "start":  round(float(m[0]), 4),
                "end":    round(float(m[1]), 4),
                "is_sil": False,
            })
        if speech_end < duration_sec - 0.005:
            moras.append({"label": "silE", "start": speech_end,
                          "end": round(duration_sec, 4), "is_sil": True})
        pitch_values = [
            None if math.isnan(float(v)) else round(float(v), 4)
            for v in pitch_scaled
        ]

        valid_hz = [float(v) for v in pitch_smoothed if not math.isnan(float(v))]
        pitch_min_hz = round(min(valid_hz), 1) if valid_hz else 80.0
        pitch_max_hz = round(max(valid_hz), 1) if valid_hz else 400.0

        result = {
            "has_data":     True,
            "pitch":        pitch_values,
            "duration_sec": round(duration_sec, 3),
            "n_frames":     len(pitch_values),
            "moras":        moras,
            "pitch_min_hz": pitch_min_hz,
            "pitch_max_hz": pitch_max_hz,
        }
        _REF_PITCH_CACHE[cache_key] = (mtime, result)
        return result
    except Exception:
        traceback.print_exc()
        return {"has_data": False}


_SMALL_KANA = set('ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ')

# モーラ → 母音 対応表
_MORA_VOWEL_MAP: dict[str, str | None] = {
    'あ':'あ','い':'い','う':'う','え':'え','お':'お',
    'か':'あ','き':'い','く':'う','け':'え','こ':'お',
    'さ':'あ','し':'い','す':'う','せ':'え','そ':'お',
    'た':'あ','ち':'い','つ':'う','て':'え','と':'お',
    'な':'あ','に':'い','ぬ':'う','ね':'え','の':'お',
    'は':'あ','ひ':'い','ふ':'う','へ':'え','ほ':'お',
    'ま':'あ','み':'い','む':'う','め':'え','も':'お',
    'や':'あ','ゆ':'う','よ':'お',
    'ら':'あ','り':'い','る':'う','れ':'え','ろ':'お',
    'わ':'あ','を':'お',
    'が':'あ','ぎ':'い','ぐ':'う','げ':'え','ご':'お',
    'ざ':'あ','じ':'い','ず':'う','ぜ':'え','ぞ':'お',
    'だ':'あ','ぢ':'い','づ':'う','で':'え','ど':'お',
    'ば':'あ','び':'い','ぶ':'う','べ':'え','ぼ':'お',
    'ぱ':'あ','ぴ':'い','ぷ':'う','ぺ':'え','ぽ':'お',
    'ゃ':'あ','ゅ':'う','ょ':'お',
    'ん': None, 'っ': None,
}

def _get_mora_vowels(reading: str) -> list[dict]:
    """各モーラとその母音を [{mora, vowel, skip}] で返す。"""
    morae = _split_moras(reading)
    result = []
    prev_vowel = 'あ'
    for mora in morae:
        ch = mora[-1] if mora[-1] in 'ゃゅょ' else mora[0]
        vowel = _MORA_VOWEL_MAP.get(ch)
        if mora[0] == 'ー':
            vowel = prev_vowel
        if vowel:
            prev_vowel = vowel
        result.append({'mora': mora, 'vowel': vowel, 'skip': vowel is None})
    return result

def _mora_count(reading: str) -> int:
    return max(1, sum(1 for c in reading if c not in _SMALL_KANA))

def _split_moras(reading: str) -> list[str]:
    moras: list[str] = []
    for ch in reading:
        if ch in _SMALL_KANA:
            if moras:
                moras[-1] += ch
        else:
            moras.append(ch)
    return moras

def _accent_pattern_for_word(accent, reading: str) -> list[str]:
    n = _mora_count(reading)
    if accent is None or n == 0:
        return []
    if accent == 0:
        return ['L'] + ['H'] * (n - 1)
    elif accent == 1:
        return ['H'] + ['L'] * (n - 1)
    else:
        result = ['L']
        for i in range(1, n):
            result.append('H' if i < accent else 'L')
        return result

def _get_suggestions(word_id: str, score_result: dict, words_list: list) -> list[dict]:
    current = get_word(word_id)
    if not current:
        return []
    current_accent = current.get("accent")
    stats       = get_stats()
    word_counts = stats.get("word_counts", {})
    candidates  = [w for w in words_list if w["word_id"] != word_id and w.get("accent") == current_accent]
    candidates.sort(key=lambda w: word_counts.get(w["word_id"], 0))
    if len(candidates) < 3:
        others = [w for w in words_list if w["word_id"] != word_id and w not in candidates]
        others.sort(key=lambda w: word_counts.get(w["word_id"], 0))
        candidates = (candidates + others)[:3]
    return candidates[:3]


# ── エラーハンドラー ─────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, title="ページが見つかりません",
                           message="お探しのページは存在しないか、移動した可能性があります。"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, title="サーバーエラー",
                           message="解析中に問題が発生しました。もう一度試してください。"), 500

@app.errorhandler(ServerBusy)
def server_busy(e):
    """重い処理の順番が来なかった（core/capacity.py）、または同じ人の前の処理が終わらなかった。"""
    msg = "いま、ほかの人の録音を順番に調べていて混み合っています。少し待ってから、もう一度ためしてください。"
    headers = {"Retry-After": str(ServerBusy.retry_after)}
    if _wants_html():
        # 「結果を見る」（POST /graph）は、録音がサーバーに残っているので同じ画面から送り直せる
        retry_post = request.path if request.method == "POST" and request.path == "/graph" else None
        return render_template("error.html", code=503, title="混み合っています", message=msg,
                               retry_post=retry_post), 503, headers
    return jsonify({"error": msg, "busy": True}), 503, headers


def heavy_view(fn):
    """重い処理（解析）をする画面・API。順番（core/capacity.py）を取ってから実行する。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with heavy_slot():
            return fn(*args, **kwargs)
    return wrapper


@app.errorhandler(413)
def too_large(e):
    msg = f"送られたファイルが大きすぎます（{MAX_UPLOAD_MB}MB まで）。録画・録音をし直してください。"
    if _wants_html():
        return render_template("error.html", code=413, title="ファイルが大きすぎます", message=msg), 413
    return jsonify({"error": msg}), 413


# ── 認証（研究参加者ログイン） ──────────────────────────────────────
# 倫理審査資料 SP-PS §6.1：利用者 ID は研究者が割り当て、パスワードは本人が
# 初回ログイン時に設定する。練習記録を個人ごとに分離するための土台。
#
# 先生だけの操作（参加者アカウントの管理と配布用QR。/admin/accounts 配下）は、生徒の端末で使われても
# 書き換えられないよう、先生用パスワードで ON にした「先生モード」のセッションでだけ受け付ける
# （core/teacher.py）。/admin/accounts 配下は参加者ログインではなく先生モードで守る。
# 最初の参加者アカウントを作る前にも開けるようにするため。
# 単語とお手本（/admin・/admin/add_word など）は参加者ごとに本人が管理するので、ログインだけで使える（core/library.py）。

_AUTH_EXEMPT_ENDPOINTS = {"login", "first_login", "logout", "static",
                          "teacher_page", "teacher_unlock_api", "teacher_lock"}


def _wants_html() -> bool:
    return "text/html" in (request.headers.get("Accept") or "")


def _teacher_denied():
    """先生モードでないときの返し方。画面の移動なら先生用パスワードの画面へ、通信なら 403。"""
    if _wants_html():
        # 画面を開こうとしたときはそのページへ、フォーム送信のときは送信元のページへ戻す
        target = request.full_path if request.method == "GET" else (urlparse(request.referrer or "").path or "/admin")
        return redirect(f"/teacher?next={quote(target.rstrip('?'), safe='')}")
    return jsonify({
        "error": "先生モードでないため、この操作はできません。設定から先生モードをONにしてください"
                 "（先生モードは、先生の操作をしないまま"
                 f"{teacher.SESSION_MINUTES}分たつと自動でOFFになります）。",
        "teacher_required": True,
    }), 403


@app.before_request
def _require_login():
    # 既定は「参加者コンテキストなし」。history/lesson は共有パスにフォールバックする。
    usercontext.set_current_user(None)

    endpoint = request.endpoint or ""
    if endpoint in _AUTH_EXEMPT_ENDPOINTS:
        return
    # 参加者アカウントの管理だけは先生モードで行う。単語とお手本の管理（/admin ほか）は
    # ログイン中の参加者が自分の分を行う（core/library.py）ので、下のログイン確認に進む
    if request.path.startswith("/admin/accounts"):
        if not teacher.is_active(session):
            return _teacher_denied()
        teacher.activate(session)   # 先生の操作をしている間は期限を延ばす
        return
    user_id = session.get(acc.SESSION_KEY)
    if user_id and acc.account_exists(user_id):
        # 以降このリクエスト内の load_history()/save_record()/今日のレッスンは
        # data/users/<user_id>/ 配下を読み書きする。
        usercontext.set_current_user(user_id)
        if request.method == "POST":
            _hold_user_lock(user_id)
        return
    if user_id:
        # アカウントが削除済み（参加撤回など）→ セッションを破棄
        session.clear()
    if request.path.startswith("/api/") or request.method != "GET":
        return jsonify({"error": "ログインが必要です"}), 401
    return redirect("/login")


# 同じ参加者の書き込み（POST）は、プロセスをまたいで1つずつ処理する。録音・アライメント結果の
# 作業フォルダと練習記録は参加者ごとに1つなので、2つのタブや2台の端末から同時に送られると、
# 書きかけの録音で採点したり、練習記録が1件消えたりする（以前はサーバーが1件ずつしか処理しなかった）。
# 待つのは同じ人の前の処理だけで、ほかの人の処理は待たない。
USER_LOCK_SECONDS = 50


def _hold_user_lock(user_id: str) -> None:
    stack = ExitStack()
    try:
        stack.enter_context(storage.lock(f"user-{user_id}", timeout=USER_LOCK_SECONDS))
    except storage.LockTimeout:
        raise ServerBusy() from None
    g.user_lock = stack


@app.teardown_request
def _clear_user_context(exc=None):
    # スレッド再利用で次のリクエストへ ID が漏れないよう必ず消す
    usercontext.clear()
    user_lock = g.pop("user_lock", None)
    if user_lock is not None:
        user_lock.close()


@app.context_processor
def _inject_current_account():
    # is_teacher は画面の出し分け（<html class="teacher-mode">）にだけ使う。
    # 操作を許すかどうかは、各ルートで teacher.is_active() を見て決める。
    return {"current_user_id": session.get(acc.SESSION_KEY),
            "is_teacher": teacher.is_active(session),
            "teacher_configured": teacher.is_configured()}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get(acc.SESSION_KEY):
            return redirect("/select")
        return render_template("login.html")

    user_id  = (request.form.get("user_id") or "").strip()
    password = request.form.get("password") or ""

    # 総当たり対策：同じ接続元から同じ利用者 ID で続けて間違えたら、しばらく受け付けない
    limit_key = f"{_client_key()}|{user_id.lower()[:64]}"
    wait = _login_limiter.seconds_locked(limit_key)
    if wait:
        return render_template(
            "login.html",
            error=f"続けて間違えたため、{wait}秒待ってからもう一度入力してください。",
            user_id=user_id,
        ), 429

    # ID は存在するがパスワード未設定 → 初回ログイン（パスワードの設定）へ
    if acc.needs_password_setup(user_id):
        return redirect(f"/first_login?user_id={quote(user_id)}")

    account = acc.verify_login(user_id, password)
    if not account:
        _login_limiter.record_failure(limit_key)
        return render_template(
            "login.html",
            error="利用者IDまたはパスワードが正しくありません。大文字・小文字もご確認ください。",
            user_id=user_id,
        ), 401

    _login_limiter.clear(limit_key)
    _start_session(account["user_id"])
    return redirect("/select")


@app.route("/first_login", methods=["GET", "POST"])
def first_login():
    if request.method == "GET":
        user_id = (request.args.get("user_id") or "").strip()
        if not acc.needs_password_setup(user_id):
            return redirect("/login")
        return render_template("first_login.html", user_id=user_id)

    user_id   = (request.form.get("user_id") or "").strip()
    password  = request.form.get("password") or ""
    password2 = request.form.get("password2") or ""

    if not acc.needs_password_setup(user_id):
        return redirect("/login")

    if password != password2:
        return render_template(
            "first_login.html", user_id=user_id,
            error="確認用のパスワードが一致しません。",
        ), 400

    try:
        account = acc.complete_first_login(user_id, password)
    except ValueError as exc:
        return render_template(
            "first_login.html", user_id=user_id,
            error=str(exc),
        ), 400

    _start_session(account["user_id"])
    return redirect("/select")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    # 「結果を見る」を押さないままログアウトしたときも、受け取った口の動画を残さない
    _remove_session_lip_videos()
    session.clear()
    return redirect("/login")


# 参加者のログインの総当たり対策。同じ接続元から同じ利用者 ID で5回続けて間違えたら60秒待たせる
# （先生用パスワードと同じ回数・秒数。core/teacher.py）。回数は全ワーカーで共有する（core/ratelimit.py）
_login_limiter = FailureLimiter(max_fails=5, lock_seconds=60, path=RUN_DIR / "login_failures.json")


def _start_session(user_id: str) -> None:
    """ログインした参加者のセッションを始める。前のセッションの口の動画の一時ファイルは消す。"""
    _remove_session_lip_videos()
    session.clear()
    session[acc.SESSION_KEY] = user_id
    # 前にログインしたときに、結果を見ないまま残った口の動画（work/lip/）を消す
    clear_lip_dir(user_id)


def _remove_session_lip_videos() -> None:
    """このセッションで受け取った口の動画の一時ファイルを消す（解析に使わなかったものが残らないように）。"""
    for path in (session.get("lip_video_paths") or {}).values():
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# ── 先生モード ───────────────────────────────────────────────────────

def _safe_next(target: str | None, default: str = "/admin") -> str:
    """ログイン後などに戻る先。同じサイトの中のパスだけを許す。"""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def _client_key() -> str:
    """信頼する中継を明示した場合だけ、ループバック経由の接続元IPを読む。"""
    return client_key(request.remote_addr, request.headers,
                      os.environ.get("TRUSTED_PROXY", "none").strip().lower())


def _teacher_try_unlock(password: str) -> tuple[bool, str | None, int]:
    """先生用パスワードを確かめる。(成功したか, エラー文, HTTP ステータス)"""
    if not teacher.is_configured():
        return False, ("先生用パスワードがまだ設定されていません。サーバーのPCで "
                       "python scripts/set_teacher_password.py を実行してください。"), 503
    key = _client_key()
    wait = teacher.seconds_locked(key)
    if wait:
        return False, f"続けて間違えたため、{wait}秒待ってからもう一度入力してください。", 429
    if not teacher.verify_password(password):
        teacher.record_failure(key)
        return False, "先生用パスワードが正しくありません。", 401
    teacher.clear_failures(key)
    teacher.activate(session)
    return True, None, 200


@app.route("/teacher", methods=["GET", "POST"])
def teacher_page():
    """先生用パスワードの入力画面（管理画面を直接開いたときなど）。"""
    next_url = _safe_next(request.values.get("next"))
    if request.method == "GET":
        if teacher.is_active(session):
            return redirect(next_url)
        return render_template("teacher_login.html", next_url=next_url)
    ok, error, status = _teacher_try_unlock(request.form.get("password") or "")
    if ok:
        return redirect(next_url)
    return render_template("teacher_login.html", next_url=next_url, error=error), status


@app.route("/teacher/unlock", methods=["POST"])
def teacher_unlock_api():
    """設定シートの「先生モード」から呼ぶ（JSON）。"""
    data = request.get_json(force=True, silent=True) or {}
    ok, error, status = _teacher_try_unlock(data.get("password") or "")
    if ok:
        return jsonify({"ok": True, "minutes": teacher.SESSION_MINUTES})
    return jsonify({"error": error}), status


@app.route("/teacher/lock", methods=["POST"])
def teacher_lock():
    """先生モードを OFF にする。"""
    teacher.deactivate(session)
    if _wants_html():
        return redirect(_safe_next(request.form.get("next"), default="/select"))
    return jsonify({"ok": True})


# ── ルーティング ─────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def select():
    if request.method == "POST":
        word_id    = request.form.get("Words")
        if not word_id:
            return redirect("/select")
        try:
            reading = get_reading_for_julius(word_id)
        except Exception:
            # 削除済み・不正な単語IDはエラー画面ではなくホームに戻す
            return redirect("/select")
        word_entry = get_word(word_id)
        display    = word_entry.get("display", reading) if word_entry else reading
        current_workspace().select_word(word_id, reading)
        accent_val = word_entry.get("accent") if word_entry else None
        hl_list = _accent_pattern_for_word(accent_val, reading)
        mora_labels_list = _split_moras(reading)
        accent_hl = [{"label": l, "hl": h} for l, h in zip(mora_labels_list, hl_list)]
        ref_preview = _get_reference_pitch_data(word_id)
        return render_template("audio.html", test=reading, display=display, word_id=word_id,
                               ref_preview=ref_preview, accent_hl=accent_hl,
                               has_ref_video=has_sample_video(word_id))

    words    = list_words()
    stats    = get_stats()
    accent_patterns = {
        w["word_id"]: _accent_pattern_for_word(w.get("accent"), w.get("reading", ""))
        for w in words
    }
    lip_ref_keys = set(load_lip_refs().keys())
    all_tags     = get_all_tags()
    weak_sounds  = _get_weak_sound_cards(words)
    lesson       = _get_daily_lesson_safe(words)
    return render_template("select.html", words=words,
                           stats=stats, accent_patterns=accent_patterns,
                           lip_ref_keys=lip_ref_keys, all_tags=all_tags,
                           weak_sounds=weak_sounds,
                           lesson=lesson,
                           overall=get_overall_score(), weekly=get_weekly_report(),
                           week=get_week_activity())


def _get_daily_lesson_safe(words: list[dict]) -> dict | None:
    try:
        from core.lesson import get_daily_lesson
        lesson = get_daily_lesson(words)
        return lesson if lesson.get("steps") else None
    except Exception:
        traceback.print_exc()
        return None


def _get_weak_sound_cards(words: list[dict], limit: int = 3) -> list[dict]:
    """苦手音カード用データ（音 + 対象単語つき）を返す。"""
    try:
        from core.weakness import get_weak_sounds, find_words_with_kana
        cards = []
        for w in get_weak_sounds(limit=limit):
            ex = find_words_with_kana(w["kana"], words, limit=3)
            if ex:
                cards.append({**w, "words": ex})
        return cards
    except Exception:
        traceback.print_exc()
        return []


@app.route("/select")
def select_page():
    words    = list_words()
    stats    = get_stats()
    accent_patterns = {
        w["word_id"]: _accent_pattern_for_word(w.get("accent"), w.get("reading", ""))
        for w in words
    }
    lip_ref_keys = set(load_lip_refs().keys())
    all_tags     = get_all_tags()
    weak_sounds  = _get_weak_sound_cards(words)
    lesson       = _get_daily_lesson_safe(words)
    return render_template("select.html", words=words,
                           stats=stats, accent_patterns=accent_patterns,
                           lip_ref_keys=lip_ref_keys, all_tags=all_tags,
                           weak_sounds=weak_sounds,
                           lesson=lesson,
                           overall=get_overall_score(), weekly=get_weekly_report(),
                           week=get_week_activity())


@app.route("/history")
def history_page():
    history  = load_history()
    stats    = get_stats()
    words    = list_words()

    existing_word_ids = {w["word_id"] for w in words}
    word_history: dict[str, list] = {}
    for record in reversed(history):
        wid = record.get("word_id")
        # 削除済み単語は「単語別スコア推移」から除外する（録音ログ自体には残す）
        if wid and wid in existing_word_ids:
            if wid not in word_history:
                word_history[wid] = []
            word_history[wid].append(record)

    word_latest = {wid: recs[-1] for wid, recs in word_history.items()}

    # ── アクセント型別の平均スコアを集計 ────────────────────────────
    _ACCENT_LABEL = {
        0: "平板型",
        1: "頭高型",
        2: "中高型（2型）",
        3: "中高型（3型）",
        4: "中高型（4型）",
    }
    word_accent = {w["word_id"]: w.get("accent") for w in words}
    accent_buckets: dict[int, dict] = {}
    for record in history:
        wid    = record.get("word_id")
        accent = word_accent.get(wid)
        total  = record.get("total")
        if accent is None or total is None:
            continue
        accent = int(accent)
        if accent not in accent_buckets:
            accent_buckets[accent] = {
                "label":       _ACCENT_LABEL.get(accent, f"{accent}型"),
                "count":       0,
                "total_sum":   0.0,
                "accent_sum":  0.0,
                "length_sum":  0.0,
                "vowel_sum":   0.0,
            }
        b = accent_buckets[accent]
        b["count"]      += 1
        b["total_sum"]  += float(total)
        b["accent_sum"] += float(record.get("accent_score") or 0)
        b["length_sum"] += float(record.get("length_score") or 0)
        b["vowel_sum"]  += float(record.get("vowel_score")  or 0)

    accent_stats = []
    for accent, b in sorted(accent_buckets.items()):
        n = b["count"]
        if n == 0:
            continue
        accent_stats.append({
            "accent":     accent,
            "label":      b["label"],
            "count":      n,
            "avg_total":  round(b["total_sum"]  / n, 1),
            "avg_accent": round(b["accent_sum"] / n, 1),
            "avg_length": round(b["length_sum"] / n, 1),
            "avg_vowel":  round(b["vowel_sum"]  / n, 1),
        })
    # 平均スコアが低い順（= 苦手順）に並べる
    accent_stats.sort(key=lambda x: x["avg_total"])

    # ── 分析タブ用データ（削除した単語の記録は「単語別スコア推移」と同じく除く） ──
    analysis_stats = compute_learning_stats(history, active_words_db())

    return render_template("history.html",
                           history=history[:100], stats=stats,
                           word_latest=word_latest, word_history=word_history,
                           words=words, accent_stats=accent_stats,
                           analysis_stats=analysis_stats)


@app.route("/history/export.csv")
def export_history_csv():
    history = load_history()
    output  = io.StringIO()
    writer  = csv.writer(output)
    # 口と声の列は 2026-09-29 に追加。既存の列の位置を変えないよう末尾に置く（空欄＝点数に入れていない回）
    # お手本ID は 2026-10-15 に追加（比べたお手本の識別子。録り直すと変わる。それより前の記録は空欄）
    writer.writerow(["日時","単語ID","単語","読み","合計点","アクセント","長さ","母音","グレード",
                     "口と声の減点","口と声のばらつき(ms)","お手本ID"])
    for r in history:
        writer.writerow([
            (r.get("timestamp") or "")[:19].replace("T"," "),
            r.get("word_id",""), r.get("display",""), r.get("reading",""),
            r.get("total",""), r.get("accent_score",""), r.get("length_score",""),
            r.get("vowel_score",""), r.get("grade",""),
            r.get("sync_penalty",""), r.get("sync_spread_ms",""),
            r.get("ref_id") or "",
        ])
    return Response("\ufeff" + output.getvalue(),
                    mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=sp-ps-history.csv"})


@app.route("/audio", methods=["GET", "POST"])
def record_audio():
    if request.method == "GET":
        # 録音ページは単語の情報（読み・お手本など）が無いと表示できない。以前は単語を選ばずに
        # 開くとテンプレートが word_id を JSON にできず 500 になっていたので、ホームへ戻す
        return redirect("/select")
    file = request.files.get("file")
    if not file:
        return "エラー: 録音が送られていません", 400
    # Julius のアライメントは重い処理なので、同時に動かす数を絞る（混み合うと 503。main.js が送り直す）
    with heavy_slot():
        try:
            ws = current_workspace()
            file.save(str(ws.wav))
            convert_to_16kHz(ws.wav, ws.wav)
            run_alignment(ws.align_dir)
            return "OK!"
        except Exception as exc:
            traceback.print_exc()
            return f"エラー: {exc}", 500


@app.route("/upload_lip_video", methods=["POST"])
def upload_lip_video():
    # お手本（ref）は口の形の抽出とアライメントを行う重い処理なので順番を取る。練習（test）は保存するだけ
    if request.form.get("mode", "").strip() == "ref":
        with heavy_slot():
            return _upload_lip_video()
    return _upload_lip_video()


def _upload_lip_video():
    try:
        mode = request.form.get("mode", "").strip()
        if mode not in ("ref", "test"):
            return jsonify({"error": "mode は ref または test を指定してください。"}), 400
        # ref はログイン中の参加者自身のお手本を上書きする（core/library.py）。test は練習の録画
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "動画ファイルが送信されていません。"}), 400

        # お手本を保存する単語。画面が表示中の単語 ID を送る（word_id）。
        # 以前は共有ファイル word_id.txt だけを見ていたため、別の端末が単語を選ぶと別の単語に保存され、
        # 削除済みの単語を指していると、その単語のフォルダと口形データを作り直してしまっていた。
        current_word_id = None
        if mode == "ref":
            current_word_id = (request.form.get("word_id") or "").strip()
            if not current_word_id:
                current_word_id = current_workspace().read_word_id()
            if not current_word_id or get_word(current_word_id) is None:
                return jsonify({"error": "お手本を登録する単語が見つかりません。ホームから単語を選び直してください。"}), 404

        lip_video_paths = session.get("lip_video_paths", {})
        old_path = lip_video_paths.get(mode)
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

        new_path = _save_temp_video(file, current_workspace().lip_dir)
        lip_video_paths[mode] = new_path
        session["lip_video_paths"] = lip_video_paths
        session.modified = True
        # 参照（ref）をアップロードした場合は抽出データを永続化しておく
        if mode == 'ref' and MEDIA_PIPE_AVAILABLE:
            mora_data: list[dict] = []
            # 読めない動画・顔が映っていない動画は、お手本を書きかえずに録り直してもらう
            try:
                reference_vectors, reference_ratios, reference_times = _extract_lip_data_timed(new_path)
            except ValueError as exc:
                return jsonify({"error": _lip_video_error(exc)}), 400
            try:
                if current_word_id:
                    _persist_ref_video(current_word_id, new_path)

                # ── アライメントによるモーラ別参照データを生成 ────────────
                if current_word_id:
                    try:
                        word_entry = get_word(current_word_id)
                        reading    = word_entry.get("reading", "") if word_entry else ""
                        if not reading:
                            reading = get_reading_for_julius(current_word_id)
                        if reading:
                            lib        = current_library()
                            sound_dir  = lib.sound_dir(current_word_id)
                            sound_dir.mkdir(parents=True, exist_ok=True)
                            native_wav = sound_dir / f"{current_word_id}.wav"
                            native_lab = sound_dir / f"{current_word_id}.lab"
                            native_log = sound_dir / f"{current_word_id}.log"
                            ref_wav = _webm_to_wav(new_path)
                            try:
                                # 音声は常にサンプルとして保存（アライメント成否に関わらず）
                                shutil.copy2(ref_wav, native_wav)
                                print(f"[lip_ref] サンプル音声を更新: {native_wav}")
                                try:
                                    mfcc = audio_mfcc(native_wav)
                                    lib.mfcc_bin(current_word_id).parent.mkdir(parents=True, exist_ok=True)
                                    mfcc.tofile(str(lib.mfcc_bin(current_word_id)))
                                except Exception:
                                    pass
                                ref_mora_list = _align_lip_ref(
                                    ref_wav, reading,
                                    lab_out=native_lab,
                                    log_out=native_log,
                                )
                                if ref_mora_list:
                                    raw_ref = _extract_mora_lip_openness(
                                        new_path, ref_mora_list,
                                        times=reference_times, ratios=reference_ratios,
                                    )
                                    mora_data = [
                                        {"label": item["label"], "v_h_ratio": item["openness"]}
                                        for item in raw_ref
                                    ]
                                    print(f"[lip_ref] アライメント成功: {len(mora_data)} モーラ")
                            finally:
                                try:
                                    os.remove(ref_wav)
                                except Exception:
                                    pass
                    except Exception:
                        # アライメント失敗は致命的でない
                        traceback.print_exc()

                if current_word_id:
                    refs = load_lip_refs()
                    entry: dict = {
                        "schema_version": 2,
                        "vectors":        reference_vectors,
                        "ratios":         reference_ratios,
                        "times":          reference_times,
                    }
                    if mora_data:
                        entry["mora_data"] = mora_data
                    refs[current_word_id] = entry
                    save_lip_refs(refs)
            except Exception:
                traceback.print_exc()
            # アライメント成否をフロントエンドに返す
            return jsonify({
                "message":       "ok",
                "alignment_ok":  bool(mora_data),
                "mora_count":    len(mora_data),
            })
        return jsonify({"message": "ok"})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/recorded_audio")
def recorded_audio():
    """直前の録音（test.wav）を返す。結果ページの比較再生ボタンで使用。"""
    wav = current_workspace().wav
    if wav.exists():
        return send_file(str(wav), mimetype="audio/wav")
    return "録音データが見つかりません", 404


@app.route('/api/lip_refs/<word_id>/ratios')
def api_lip_refs_ratios(word_id: str):
    try:
        refs = load_lip_refs()
        entry = refs.get(word_id)
        if not entry:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ratios": entry.get('ratios', [])})
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


# ローマ字モーラ末尾の母音 → ひらがな母音
_ROMAJI_VOWEL = {'a': 'あ', 'i': 'い', 'u': 'う', 'e': 'え', 'o': 'お'}


@app.route('/api/lip_guide/<word_id>')
def api_lip_guide(word_id: str):
    """お手本（先生）のモーラ別口開き比率を、読みのモーラ列に揃えて返す。

    lip_refs.json の mora_data（Julius アライメント由来、ローマ字ラベル）を
    _get_mora_vowels(reading) のモーラ列に対応付ける。
    長音「ー」で mora_data 側が不足する場合は直前の値を引き継ぐ。
    """
    try:
        refs  = load_lip_refs()
        entry = refs.get(word_id)
        mora_data = (entry or {}).get('mora_data')
        if not mora_data:
            return jsonify({"has_data": False})

        word_entry = get_word(word_id)
        reading    = (word_entry or {}).get("reading", "")
        if not reading:
            return jsonify({"has_data": False})

        def _vowel_of(label: str) -> str | None:
            base = label.rstrip(':')
            return _ROMAJI_VOWEL.get(base[-1]) if base else None

        mora_vowels = _get_mora_vowels(reading)
        guide: list[dict | None] = []
        last_vowel_item: dict | None = None
        gi = 0
        for mv in mora_vowels:
            if mv.get("skip"):
                # ん・っ は練習対象外。mora_data 側の N/q エントリを読み捨てる
                if gi < len(mora_data) and _vowel_of(mora_data[gi].get("label", "")) is None:
                    gi += 1
                guide.append(None)
                continue

            # mora_data 側に紛れた N/q（母音なし）エントリは読み飛ばす
            while gi < len(mora_data) and _vowel_of(mora_data[gi].get("label", "")) is None:
                gi += 1

            item = None
            if gi < len(mora_data):
                g = mora_data[gi]; gi += 1
                r = g.get("v_h_ratio")
                if r is not None and r > 0:
                    item = {"label": g.get("label", ""),
                            "vowel": _vowel_of(g.get("label", "")),
                            "ratio": round(float(r), 4)}
            elif mv["mora"] == 'ー' and last_vowel_item:
                # 長音: mora_data 側にエントリが無ければ直前モーラの開きを維持
                item = dict(last_vowel_item)

            if item:
                last_vowel_item = item
            guide.append(item)

        return jsonify({"has_data": True, "guide": guide})
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route('/sound_drill/<kana>')
def sound_drill(kana: str):
    """苦手音のスモールステップ練習ページ（音単独→音節→語頭→語中・語尾）。"""
    from core.drill import get_drill_data
    data = get_drill_data(kana, list_words())
    if not data:
        return redirect("/select")
    return render_template("sound_drill.html", **data)


@app.route('/listening_quiz')
def listening_quiz():
    """聞き分けテスト（音の弁別トレーニング）。"""
    from core.drill import build_listening_quiz
    questions = build_listening_quiz(list_words())
    return render_template("listening_quiz.html", questions=questions)


@app.route('/api/vowel_trainer/complete', methods=['POST'])
def api_vowel_trainer_complete():
    """母音トレーナー完走を記録し、今日のレッスンの進捗を更新する。"""
    try:
        data    = request.get_json(force=True, silent=True) or {}
        word_id = (data.get("word_id") or "").strip()
        if not word_id:
            return jsonify({"error": "word_id がありません"}), 400

        # 今日のレッスンの mouth ステップに反映
        try:
            from core.lesson import mark_mouth_done
            mark_mouth_done(word_id)
        except Exception:
            pass

        return jsonify({"ok": True, "completed_quests": []})
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route('/api/lip_refs/overwrite', methods=['POST'])
@heavy_view
def api_lip_refs_overwrite():
    try:
        word_id = (request.form.get('word_id') or '').strip()
        file = request.files.get('file')
        if not word_id:
            return jsonify({"error": "word_id required"}), 400
        if not file:
            return jsonify({"error": "file required"}), 400
        # ログイン中の参加者の単語だけ。無い単語 ID でお手本のファイルを作らない
        if get_word(word_id) is None:
            return jsonify({"error": "お手本を登録する単語が見つかりません。"}), 404
        # 一時保存して抽出
        tmp = _save_temp_video(file)
        try:
            # 先に中身を確かめる。読めない動画・顔が映っていない動画でお手本を上書きしない
            try:
                vectors, ratios, lip_times = _extract_lip_data_timed(tmp)
            except ValueError as exc:
                return jsonify({"error": _lip_video_error(exc)}), 400
            _persist_ref_video(word_id, tmp)

            # モーラ別参照データをアライメントで生成（upload_lip_video と同じパイプライン）
            mora_data: list[dict] = []
            try:
                word_entry = get_word(word_id)
                reading    = word_entry.get("reading", "") if word_entry else ""
                if not reading:
                    reading = get_reading_for_julius(word_id)
                if reading:
                    lib        = current_library()
                    sound_dir  = lib.sound_dir(word_id)
                    sound_dir.mkdir(parents=True, exist_ok=True)
                    native_wav = sound_dir / f"{word_id}.wav"
                    native_lab = sound_dir / f"{word_id}.lab"
                    native_log = sound_dir / f"{word_id}.log"
                    ref_wav = _webm_to_wav(tmp)
                    try:
                        # 音声は常にサンプルとして保存（アライメント成否に関わらず）
                        shutil.copy2(ref_wav, native_wav)
                        try:
                            mfcc = audio_mfcc(native_wav)
                            lib.mfcc_bin(word_id).parent.mkdir(parents=True, exist_ok=True)
                            mfcc.tofile(str(lib.mfcc_bin(word_id)))
                        except Exception:
                            pass
                        ref_mora_list = _align_lip_ref(
                            ref_wav, reading,
                            lab_out=native_lab,
                            log_out=native_log,
                        )
                        if ref_mora_list:
                            raw_ref  = _extract_mora_lip_openness(tmp, ref_mora_list)
                            mora_data = [
                                {"label": item["label"], "v_h_ratio": item["openness"]}
                                for item in raw_ref
                            ]
                    finally:
                        try:
                            os.remove(ref_wav)
                        except Exception:
                            pass
            except Exception:
                traceback.print_exc()

            refs = load_lip_refs()
            entry: dict = {"schema_version": 2, "vectors": vectors, "ratios": ratios, "times": lip_times}
            if mora_data:
                entry["mora_data"] = mora_data
            refs[word_id] = entry
            save_lip_refs(refs)
            return jsonify({"message": "saved", "alignment_ok": bool(mora_data), "mora_count": len(mora_data)})
        finally:
            try:
                if os.path.exists(tmp): os.remove(tmp)
            except Exception:
                pass
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route("/graph", methods=["GET", "POST"])
def audio_analysis():
    if request.method != "POST":
        # 結果画面でリロードした場合など。エラーではなくホームに戻す
        return redirect("/select")
    # 単語が消えた・録音が無いまま送られたとき（別のタブで単語を削除した、戻るボタンで送り直した など）は、
    # 解析エラーの画面ではなく、録音し直せる画面へ戻す
    ws_check = current_workspace()
    word_check = ws_check.read_word_id()
    if not word_check or get_word(word_check) is None:
        return redirect("/select")
    if not ws_check.wav.exists() or not ws_check.lab.exists():
        return redirect(f"/practice/word/{quote(word_check)}")
    # 単語を選んだ（録音ページを開いた）あとに録音していなければ、前の単語の録音なので採点しない
    try:
        if ws_check.wav.stat().st_mtime < ws_check.word_id_path.stat().st_mtime:
            return redirect(f"/practice/word/{quote(word_check)}")
    except OSError:
        pass

    # 採点は重い処理なので順番を取る（core/capacity.py）。混み合っていれば 503 の画面から送り直せる
    with heavy_slot():
        return _analyze_recording()


def _analyze_recording():
    try:
        ws        = current_workspace()
        word_id   = ws.read_word_id()

        lip_compare = None
        lip_ref_ratios = []
        lip_test_ratios = []
        lip_paths = session.get("lip_video_paths", {})
        ref_video = lip_paths.get("ref")
        test_video = lip_paths.get("test")
        # 今回の録音で口の動画が届いたか。結果画面は、これが無いときに端末に残った
        # 前の動画を「あなた」として出さないよう、この値を見る（下の一時ファイル削除より前に調べる）
        user_lip_recorded = bool(test_video and os.path.exists(test_video))
        reference_vectors = None
        ref_lip_times     = None   # 音映像同期分析用（Noneなら30fps仮定）
        test_lip_times    = None
        # 優先順序: セッションにある ref 動画 -> 保存済み参照データ
        if MEDIA_PIPE_AVAILABLE:
            try:
                if ref_video and os.path.exists(ref_video):
                    reference_vectors, lip_ref_ratios, ref_lip_times = _extract_lip_data_timed(ref_video)
                else:
                    # 保存済み参照をロード
                    refs = load_lip_refs()
                    saved = refs.get(word_id)
                    if saved:
                        reference_vectors = saved.get("vectors")
                        lip_ref_ratios = saved.get("ratios", [])
                        ref_lip_times  = saved.get("times")  # 旧データはNone

                if test_video and os.path.exists(test_video):
                    test_vectors, lip_test_ratios, test_lip_times = _extract_lip_data_timed(test_video)

                if reference_vectors and 'test_vectors' in locals():
                    lip_score, lip_distance = _compute_lip_score(reference_vectors, test_vectors)
                    lip_compare = {"score": lip_score, "distance": lip_distance}
            except Exception:
                lip_compare = None

        audio_sample     = read_sample(word_id)
        audio_learn      = str(ws.wav)
        audio_learn_edit = str(ws.segment_wav)
        lab_sample = str(Path(audio_sample).with_suffix(".lab"))
        lab_learn  = str(ws.lab)
        log_sample = str(Path(audio_sample).with_suffix(".log"))
        log_learn  = str(ws.log)

        prev_score = get_last_score(word_id)

        floor_sample, ceiling_sample = estimate_pitch_range(audio_sample)
        floor_learn,  ceiling_learn  = estimate_pitch_range(audio_learn)

        pitch1, time1 = praat_pitch(audio_sample, pitch_floor=floor_sample, pitch_ceiling=ceiling_sample)
        pitch2, time2 = praat_pitch(audio_learn,  pitch_floor=floor_learn,  pitch_ceiling=ceiling_learn)
        pitch1_10ms   = resample_to_10ms(pitch1, time1)
        pitch2_10ms   = resample_to_10ms(pitch2, time2)

        (lab_list1, mora_list1, phoneme1, mora1, _, _, phoneme_length1, mora_length1) = lab_load(lab_sample)
        (lab_list2, mora_list2, phoneme2, mora2, _, _, phoneme_length2, mora_length2) = lab_load(lab_learn)
        if not lab_list1: raise ValueError(f"参照 lab が空: {lab_sample}")
        if not lab_list2: raise ValueError(f"録音 lab が空: {lab_learn}")

        pitch_native = smooth(comp(pitch1), window=5)
        pitch_learn  = smooth(comp(pitch2), window=5)

        xline_phoneme1 = [float(i[0]) for i in lab_list1]
        xline_phoneme2 = [float(i[0]) for i in lab_list2]
        xline_mora1    = [float(i[0]) for i in mora_list1]
        xline_mora2    = [float(i[0]) for i in mora_list2]

        phoneme_frame1, phoneme_frame2, mora_frame1 = log_load(log_sample)
        if not phoneme_frame1 or not mora_frame1: raise ValueError(f"参照アライメント結果が空: {log_sample}")
        sil_start1 = int(phoneme_frame1[0][0]); sil_end1 = int(phoneme_frame1[-1][1]) + 1
        pitch1_sil = pitch1_10ms[sil_start1:sil_end1]

        phoneme_frame3, phoneme_frame4, mora_frame2 = log_load(log_learn)
        if not phoneme_frame3 or not mora_frame2: raise ValueError(f"録音アライメント結果が空: {log_learn}")
        sil_start2 = int(phoneme_frame3[0][0]); sil_end2 = int(phoneme_frame3[-1][1]) + 1
        pitch2_sil = pitch2_10ms[sil_start2:sil_end2]

        pitch3     = length_arrange(pitch2_sil, phoneme_frame2, phoneme_frame4)
        xline_mora = [int(i[0]) - int(mora_frame1[0][0]) for i in mora_frame1]

        start1, end1 = float(lab_list1[0][0]), float(lab_list1[-1][1])
        start2, end2 = float(lab_list2[0][0]), float(lab_list2[-1][1])
        segment_audio(audio_learn, start2, end2, ws.segment_wav)

        dtw_list, word_list, colors, _ = dtw_ascending_order(audio_learn_edit, word_id)
        julius_score = extract_julius_score(log_learn)

        pitch1_sil_semi = hz_to_semitone(pitch1_sil, ref_hz=None)
        pitch3_semi     = hz_to_semitone(pitch3,     ref_hz=None)
        pitch_fin_disp  = scale(smooth(comp(pitch1_sil_semi), window=5))
        pitch_fin2_disp = scale(smooth(comp(pitch3_semi),     window=5))
        x_axis          = list(range(len(pitch_fin_disp)))

        word_entry = get_word(word_id)
        display    = word_entry.get("display", word_id) if word_entry else word_id
        reading    = word_entry.get("reading", word_id) if word_entry else word_id

        # ── 音映像クロスチェック（口と声の同期分析。人が気づく範囲を超えてずれた音があれば合計点から少し引く） ──
        av_sync = None
        if (reference_vectors and 'test_vectors' in locals()
                and mora_list1 and mora_list2):
            try:
                from core.sync import compute_av_sync
                av_sync = compute_av_sync(
                    reference_vectors, ref_lip_times, mora_list1,
                    test_vectors, test_lip_times, mora_list2,
                )
            except Exception:
                traceback.print_exc()

        # ── モーラ別唇開き分析（Julius タイムスタンプ + 映像フレーム） ──
        lip_mora_analysis = []
        if MEDIA_PIPE_AVAILABLE and test_video and os.path.exists(test_video):
            try:
                _raw_lip = _extract_mora_lip_openness(
                    test_video, mora_list2,
                    times=test_lip_times, ratios=lip_test_ratios,
                )
                # schema_version==2 のお手本データがあれば直接比較、なければ期待値テーブル
                _refs       = load_lip_refs()
                _ref_entry  = _refs.get(word_id, {})
                _ref_mora   = (
                    _ref_entry.get("mora_data")
                    if _ref_entry.get("schema_version") == 2 and _ref_entry.get("mora_data")
                    else None
                )
                if _ref_mora:
                    lip_mora_analysis = _build_lip_mora_comparison(mora_list2, _raw_lip, _ref_mora)
                else:
                    lip_mora_analysis = _build_lip_mora_analysis(mora_list2, _raw_lip)
            except Exception:
                traceback.print_exc()

        # 一時唇動画ファイルを使用後に削除してセッションをクリア
        try:
            session.pop("lip_video_paths", None)
            session.modified = True
            for _p in lip_paths.values():
                try:
                    if _p and os.path.exists(_p):
                        os.remove(_p)
                except Exception:
                    pass
        except Exception:
            pass

        common_kwargs = dict(
            original_filename=display, word_id=word_id,
            Native_pitch=pitch_native.tolist(), Native_time=time1,
            User_pitch=pitch_learn.tolist(),    User_time=time2,
            Native_phoneme_values=xline_phoneme1, Native_mora_values=xline_mora1,
            User_mora_values=xline_mora2,         User_phoneme_values=xline_phoneme2,
            phoneme_labels=phoneme1, mora_labels=mora1,
            start1=start1, end1=end1, start2=start2, end2=end2,
            mora_values=xline_mora, x_axis=x_axis,
            pitch_fin=pitch_fin_disp.tolist(), pitch_fin2=pitch_fin2_disp.tolist(),
            Native_phoneme_length=pct_length(phoneme_length1),
            User_phoneme_length=pct_length(phoneme_length2),
            Native_mora_length=pct_length(mora_length1),
            User_mora_length=pct_length(mora_length2),
            words=word_list, sort_distance=dtw_list, bar_color=colors,
            prev_score=prev_score,
            lip_mora_analysis=lip_mora_analysis,
            has_ref_video=has_sample_video(word_id),
            user_lip_recorded=user_lip_recorded,
        )

        words_list = list_words()
        word_map   = {w["word_id"]: w for w in words_list}

        if julius_score is not None and julius_score < JULIUS_GATE_THRESHOLD:
            return render_template("line_graph.html", **common_kwargs,
                                   score=None, alignment_failed=True,
                                   julius_score=julius_score,
                                   voice_quality={"jitter":None,"shimmer":None,"feedback":None},
                                   speaking_rate=0.0, rate_feedback=None,
                                   score_delta=None, suggestions=[],
                                   mora_scores=[], worst_mora=None, ci_info=None,
                                   lip_compare=lip_compare, lip_ref_ratios=lip_ref_ratios, lip_test_ratios=lip_test_ratios,
                               av_sync=av_sync)

        pitch_fin_score  = smooth(comp(pitch1_sil_semi), window=3)
        pitch_fin2_score = smooth(comp(pitch3_semi),     window=3)
        pitch_native_raw = pitch1_sil_semi.copy()
        pitch_user_raw   = pitch3_semi.copy()

        max_formant_sample = 5500.0 if ceiling_sample > 400 else 5000.0
        max_formant_learn  = 5500.0 if ceiling_learn  > 400 else 5000.0
        native_formants = user_formants = None
        vowel_scale     = 1.0
        try:
            # 「こう」の「う」のような長音の後半は、前のモーラとまとめた区間で測る
            # （Julius が置く2つの境界には音響的な根拠がないため。core/utils.long_vowel_groups）
            native_formants = extract_mora_formants(audio_sample, long_vowel_spans(mora_list1), max_formant=max_formant_sample, use_cache=True)
            user_formants   = extract_mora_formants(audio_learn,  long_vowel_spans(mora_list2), max_formant=max_formant_learn,  use_cache=False)
            # 母音チャートで同じ点を2回描かないよう、長音の後半に印を付ける
            for _formants in (native_formants, user_formants):
                for _group in long_vowel_groups([f["label"] for f in _formants]):
                    for _i in _group[1:]:
                        _formants[_i] = {**_formants[_i], "long_vowel_tail": True}
            vowel_scale, _ = speaker_formant_scale(
                native_formants, user_formants,
                pitch_ceiling_native=ceiling_sample, pitch_ceiling_user=ceiling_learn,
            )
            vowel_score, vowel_feedback = calc_vowel_score(
                native_formants,
                user_formants,
                pitch_ceiling_native=ceiling_sample,  # ← ネイティブの性別判定
                pitch_ceiling_user=ceiling_learn,      # ← ユーザーの性別判定
            )
        except Exception:
            vowel_score, vowel_feedback = 10.0, "母音の評価中にエラーが発生しました。"

        try:
            voice_quality = calc_voice_quality(audio_learn, start2, end2,
                                               pitch_floor=floor_learn, pitch_ceiling=ceiling_learn)
        except Exception:
            voice_quality = {"jitter": None, "shimmer": None, "feedback": None}

        try:
            native_rate, _           = calc_speaking_rate(lab_list1, mora_list1)
            user_rate, rate_feedback = calc_speaking_rate(lab_list2, mora_list2, native_rate=native_rate)
        except Exception:
            user_rate, rate_feedback = 0.0, None

        accent = word_entry.get("accent") if word_entry else None
        n_mora = len(mora1)

        score_result = calc_total_score(
            pitch_fin2=pitch_fin2_score.tolist(), mora_values=xline_mora,
            accent=accent, n_mora=n_mora,
            native_mora_length=pct_length(mora_length1),
            user_mora_length=pct_length(mora_length2),
            mora_labels=mora1, pitch_fin=pitch_fin_score.tolist(),
            pitch_user_raw=pitch_user_raw, pitch_native_raw=pitch_native_raw,
            vowel_score=vowel_score, vowel_feedback=vowel_feedback,
            av_sync=av_sync,
        )
        score_result["alignment_failed"] = False
        score_result["julius_score"]     = julius_score

        # ── ④ モーラ別スコア + ⑤ ハイライト用データ ────────────────
        try:
            mora_scores = calc_mora_scores(
                pitch_fin=pitch_fin_score.tolist(),
                pitch_fin2=pitch_fin2_score.tolist(),
                mora_values=xline_mora,
                native_mora_length=pct_length(mora_length1),
                user_mora_length=pct_length(mora_length2),
                mora_labels=mora1,
                native_formants=native_formants,
                user_formants=user_formants,
                pitch_native_raw=pitch_native_raw,
                pitch_user_raw=pitch_user_raw,
                vowel_scale=vowel_scale,
            )
            # ⑤ 最もスコアが低いモーラのフレーム範囲を取得
            valid = [m for m in mora_scores if m["total"] is not None]
            worst_mora = min(valid, key=lambda m: m["total"]) if valid else None
            # JSON serialization のため全値をネイティブ型に変換
            if worst_mora:
                worst_mora = {
                    "label":       str(worst_mora["label"]),
                    "mora_index":  int(worst_mora["mora_index"]),
                    "frame_start": int(worst_mora["frame_start"]),
                    "frame_end":   int(worst_mora["frame_end"]),
                    "accent":  float(worst_mora["accent"])  if worst_mora["accent"]  is not None else None,
                    "length":  float(worst_mora["length"])  if worst_mora["length"]  is not None else None,
                    "vowel":   float(worst_mora["vowel"])   if worst_mora["vowel"]   is not None else None,
                    "total":   float(worst_mora["total"])   if worst_mora["total"]   is not None else None,
                }
        except Exception:
            mora_scores  = []
            worst_mora   = None

        # モーラ別スコアにかなラベルを付与（苦手音分析・クエスト判定用）
        mora_kana_scores: dict[str, float] = {}
        for m in mora_scores:
            try:
                kana = romaji_mora_to_kana(str(m.get("label", "")))
                m["kana"] = kana
                if kana and m.get("total") is not None:
                    v = float(m["total"])
                    # 同じかなが複数回出る場合は低い方（=弱い方）を採用
                    if kana not in mora_kana_scores or v < mora_kana_scores[kana]:
                        mora_kana_scores[kana] = v
            except Exception:
                continue
        score_result["mora_kana_scores"] = mora_kana_scores

        score_delta = _score_delta(score_result.get("total"), prev_score.get("total") if prev_score else None)

        # ── 信頼区間の計算 ────────────────────────────────────────────
        try:
            word_hist   = load_word_history(word_id)
            past_scores = [float(r["total"]) for r in word_hist if r.get("total") is not None]
            all_scores  = past_scores + [float(score_result["total"])]
            ci_result   = bootstrap_ci(all_scores)
            ci_info     = ci_result if ci_result else needs_more_data(len(all_scores))
        except Exception:
            ci_result = None
            ci_info   = None

        # ── この単語のこれまで（結果画面の「この単語の記録」用。保存の前に読む） ──
        word_progress = None
        try:
            _past = [float(r["total"]) for r in load_word_history(word_id) if r.get("total") is not None]
            _past.reverse()                                   # 古い順にする
            _now  = float(score_result["total"])
            _all  = _past + [_now]
            # 表示は点数の輪（score.total|int）と同じく小数を切り捨てる。以前は四捨五入していたため、
            # 自己ベストが 89.5〜89.9 点で「90点以上をとりました／マスター」と出ていた
            word_progress = {
                "scores":  [int(v) for v in _all[-10:]],          # 最近10回（今回を含む）
                "count":   len(_all),                             # 今回が何回目か
                "best":    int(max(_all)),
                "is_best": bool(_past) and _now > max(_past),     # 自己ベスト更新
            }
        except Exception:
            word_progress = None

        try:
            # どのお手本（録音・音の区切り・録画）と比べた点数かを残す。録り直すと変わる
            ref_id = reference_id(audio_sample, lab_sample, current_library().sample_video(word_id))
            save_record(word_id, display, reading, score_result, mora_scores=mora_scores, ref_id=ref_id)
        except Exception:
            traceback.print_exc()

        suggestions = _get_suggestions(word_id, score_result, words_list)

        return render_template("line_graph.html", **common_kwargs,
                               score=score_result,
                               alignment_failed=False, julius_score=julius_score,
                               voice_quality=voice_quality,
                               speaking_rate=user_rate, rate_feedback=rate_feedback,
                               score_delta=score_delta, suggestions=suggestions,
                               mora_scores=mora_scores, worst_mora=worst_mora,
                               ci_info=ci_info, word_progress=word_progress,
                               native_formants=native_formants,
                               user_formants=user_formants,
                               lip_compare=lip_compare, lip_ref_ratios=lip_ref_ratios, lip_test_ratios=lip_test_ratios,
                               av_sync=av_sync)

    except Exception as exc:
        traceback.print_exc()
        return render_template("error.html", code=500, title="解析エラー", message=str(exc)), 500


@app.route("/sample_audio/<word_id>")
def sample_audio(word_id: str):
    """お手本の音声（単語を登録したときの録画から取り出した wav）を配信する。"""
    wav = current_library().sample_wav(word_id)
    if wav.exists(): return send_file(str(wav), mimetype="audio/wav")
    return "not found", 404


@app.route("/sample_video/<word_id>")
def sample_video(word_id: str):
    """お手本録画（webm）を配信する。母音トレーナー等での実映像再生用。"""
    video_path = current_library().sample_video(word_id)
    if video_path.exists(): return send_file(str(video_path), mimetype="video/webm")
    return "not found", 404


def has_sample_video(word_id: str) -> bool:
    return current_library().sample_video(word_id).exists()


# 単語の「削除」は一覧から外すだけで、ID・お手本・練習記録は残す（core/vocab.py）。
# 同じ単語を削除して登録し直すと、記録が2つの ID に分かれ、比べたお手本も変わってしまうため。

@app.route("/admin/delete_word", methods=["POST"])
def api_delete_word():
    try:
        data    = request.get_json(force=True, silent=True) or {}
        word_id = data.get("word_id", "").strip()
        if not word_id: return jsonify({"error": "word_id が指定されていません"}), 400
        return jsonify(archive_word(word_id))
    except ValueError as exc:
        # 自分の単語に無い ID（ほかの参加者の単語 ID を含む）など、入力の誤り
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route("/admin/restore_word", methods=["POST"])
def api_restore_word():
    """削除した単語を元に戻す（同じ ID・同じお手本で、練習記録がつながる）。"""
    try:
        data    = request.get_json(force=True, silent=True) or {}
        word_id = (data.get("word_id") or "").strip()
        if not word_id: return jsonify({"error": "word_id が指定されていません"}), 400
        return jsonify(restore_word(word_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route("/admin/purge_word", methods=["POST"])
def api_purge_word():
    """削除した単語を、お手本の録音・録画ごと完全に消す（先生モードだけ。練習記録がある単語は消さない）。"""
    if not teacher.is_active(session):
        return _teacher_denied()
    teacher.activate(session)
    try:
        data    = request.get_json(force=True, silent=True) or {}
        word_id = (data.get("word_id") or "").strip()
        if not word_id: return jsonify({"error": "word_id が指定されていません"}), 400
        result = purge_word(word_id)
        refs = load_lip_refs()
        if refs.pop(word_id, None) is not None:
            save_lip_refs(refs)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route("/admin/check_word", methods=["POST"])
def api_check_word():
    """単語を追加する前に、同じ単語・似ている単語（削除した単語を含む）があるかを返す。"""
    data    = request.get_json(force=True, silent=True) or {}
    display = (data.get("display") or "").strip()
    reading = (data.get("reading") or "").strip()
    if not display or not reading:
        return jsonify({"error": "表示テキストとひらがな読みを入力してください"}), 400
    return jsonify(find_same_words(display, reading))


@app.route("/admin/update_word", methods=["POST"])
def api_update_word():
    try:
        data    = request.get_json()
        word_id = data.get("word_id", "").strip()
        display = data.get("display", "").strip()
        reading = data.get("reading", "").strip()
        accent  = data.get("accent")
        if not word_id or not display or not reading: return jsonify({"error": "必須項目が不足しています"}), 400
        return jsonify(update_word(word_id, display, reading, accent))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route("/vowel_trainer/<word_id>")
def vowel_trainer(word_id: str):
    """母音形練習ページ。"""
    word_entry = get_word(word_id)
    if not word_entry:
        return redirect("/select")
    reading    = word_entry.get("reading", "")
    display    = word_entry.get("display", reading)
    mora_vowels = _get_mora_vowels(reading)
    return render_template("vowel_trainer.html",
                           word_id=word_id, display=display,
                           reading=reading, mora_vowels=mora_vowels,
                           has_ref_video=has_sample_video(word_id))


@app.route("/practice/word/<word_id>")
def practice_word(word_id: str):
    """連続練習モード用：GET で単語練習画面に直接遷移する。"""
    word_entry = get_word(word_id)
    if not word_entry:
        return redirect("/select")
    reading    = word_entry.get("reading", "")
    display    = word_entry.get("display", reading)
    current_workspace().select_word(word_id, reading)
    accent_val = word_entry.get("accent")
    hl_list          = _accent_pattern_for_word(accent_val, reading)
    mora_labels_list = _split_moras(reading)
    accent_hl        = [{"label": l, "hl": h} for l, h in zip(mora_labels_list, hl_list)]
    ref_preview      = _get_reference_pitch_data(word_id)
    return render_template("audio.html", test=reading, display=display, word_id=word_id,
                           ref_preview=ref_preview, accent_hl=accent_hl,
                           has_ref_video=has_sample_video(word_id))


@app.route("/admin/update_word_note", methods=["POST"])
def api_update_word_note():
    try:
        data    = request.get_json(force=True, silent=True) or {}
        word_id = data.get("word_id", "").strip()
        note    = data.get("note", "")
        if not word_id:
            return jsonify({"error": "word_id が指定されていません"}), 400
        return jsonify(update_word_note(word_id, note))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/admin/update_word_tags", methods=["POST"])
def api_update_word_tags():
    try:
        data    = request.get_json(force=True, silent=True) or {}
        word_id = data.get("word_id", "").strip()
        tags    = data.get("tags", [])
        if not word_id: return jsonify({"error": "word_id が指定されていません"}), 400
        if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
            return jsonify({"error": "tags は文字列のリストで指定してください"}), 400
        return jsonify(update_word_tags(word_id, tags))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route("/api/history/calendar")
def api_history_calendar():
    try:
        days = int(request.args.get("days", 84))
    except ValueError:
        days = 84
    days = max(1, min(days, 365))
    return jsonify(get_daily_counts(days))


@app.route("/admin")
def admin():
    return render_template("admin.html", words=list_words(), archived=list_archived_words(),
                           stats=get_stats(), all_tags=get_all_tags())


# ── 研究参加者アカウント管理（研究者用・ローカル運用前提） ───────────
# 倫理審査資料 §5.1#6 / §5.3：学年と聴覚障害の程度は調査票由来のため
# 研究者がここで入力する。参加者が設定するのはパスワードと表示名のみ。

def _render_admin_accounts(error: str | None = None, status: int = 200):
    html = render_template(
        "admin_accounts.html",
        accounts=acc.list_accounts(),
        grade_labels=acc.GRADE_LABELS,
        hearing_labels=acc.HEARING_LEVEL_LABELS,
        error=error,
    )
    return (html, status) if status != 200 else html


@app.route("/admin/accounts")
def admin_accounts():
    return _render_admin_accounts()


@app.route("/admin/accounts/access_qr")
def admin_access_qr():
    """配布用に、公開URLとQRコードを1枚に印刷するページ（先生モードのみ）。"""
    # 固定URL（named Tunnel）は PUBLIC_BASE_URL、無ければ今開いているホスト名を使う
    url, error = None, None
    base = os.environ.get("PUBLIC_BASE_URL", "").strip()
    try:
        url = public_url(base) if base else url_from_host(request.host)
    except ValueError as exc:
        error = (f"PUBLIC_BASE_URL の設定が正しくありません（{exc}）" if base else
                 "公開URLが分かりません。スマホ用の公開URL（https://…）からこのページを開いてください。")
    qr_svg = None
    if url:
        import qrcode
        import qrcode.image.svg
        # 外部のQRサービスにURLを送らず、ここでSVGを作る
        qr_svg = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage,
                             box_size=10, border=2).to_string(encoding="unicode")
    return render_template("access_qr.html", url=url, qr_svg=qr_svg, error=error,
                           changing=".trycloudflare.com" in (url or ""))


@app.route("/admin/accounts/create", methods=["POST"])
def admin_accounts_create():
    try:
        acc.create_account(
            request.form.get("user_id", ""),
            request.form.get("grade", acc.DEFAULT_GRADE),
            request.form.get("hearing_level", acc.DEFAULT_HEARING_LEVEL),
        )
    except ValueError as exc:
        return _render_admin_accounts(error=str(exc), status=400)
    return redirect("/admin/accounts")


@app.route("/admin/accounts/update_profile", methods=["POST"])
def admin_accounts_update_profile():
    try:
        acc.update_research_profile(
            request.form.get("user_id", ""),
            grade=request.form.get("grade"),
            hearing_level=request.form.get("hearing_level"),
        )
    except ValueError as exc:
        return _render_admin_accounts(error=str(exc), status=400)
    return redirect("/admin/accounts")


@app.route("/admin/accounts/clear_password", methods=["POST"])
def admin_accounts_clear_password():
    try:
        acc.clear_password(request.form.get("user_id", ""))
    except ValueError as exc:
        return _render_admin_accounts(error=str(exc), status=400)
    return redirect("/admin/accounts")


@app.route("/admin/accounts/delete", methods=["POST"])
def admin_accounts_delete():
    try:
        acc.delete_account(request.form.get("user_id", ""))
    except ValueError as exc:
        return _render_admin_accounts(error=str(exc), status=400)
    return redirect("/admin/accounts")


@app.route("/admin/add_word", methods=["POST"])
@heavy_view
def add_word():
    """
    単語を登録する（multipart/form-data + 動画ファイル必須）。

    単語登録とお手本録画は必ずセットで行う仕様のため、動画なし
    （テキストのみ）での登録はサポートしない。動画から音声を抽出して
    アライメント・MFCC・口形データを一括で保存する。
    呼び出し元: select.html の単語追加モーダル / admin.html のカメラ録画。
    """
    try:
        display = (request.form.get("display") or "").strip()
        reading = (request.form.get("reading") or "").strip()
        file    = request.files.get("file")

        if not display or not reading:
            return jsonify({"error": "表示テキストとひらがな読みを入力してください"}), 400
        if not file:
            return jsonify({"error": "お手本の録画（動画）が必要です。単語登録とセットで録画してください。"}), 400
        # 表示と読みが同じ単語（削除した単語を含む）は新しく作らない。削除した単語なら元に戻してもらう
        same = find_same_words(display, reading)["exact"]
        if same:
            return jsonify({"error": str(WordExists(same[0])), "word": same[0]}), 409

        # 音声抽出 → 登録 → 口形データ保存（テキスト+動画をまとめて atomic に登録）
        video_tmp = _save_temp_video(file)
        try:
            try:
                ref_wav = _webm_to_wav(video_tmp)
            except Exception:
                traceback.print_exc()
                return jsonify({"error": "お手本の録画を読み込めませんでした。もう一度録画してください。"}), 400
            try:
                result = register_word(display, reading, Path(ref_wav))
            except WordExists as exc:
                return jsonify({"error": str(exc), "word": exc.word}), 409
            finally:
                try:
                    os.remove(ref_wav)
                except Exception:
                    pass

            word_id = result["word_id"]

            if not result.get("alignment_ok"):
                # お手本の音声認識に失敗＝発音比較の基準データが無いまま
                # 単語だけが残ってしまう。単語+動画は必ずセットという方針
                # （register_word 呼び出し元コメント参照）に反するため、
                # 登録自体を取り消してユーザーに録り直しを促す。
                try:
                    discard_new_word(word_id)
                except Exception:
                    traceback.print_exc()
                return jsonify({
                    "error": "発音が認識できませんでした。マイクが正しく音を拾えているか"
                             "（接続・選択デバイス・ミュート設定）を確認のうえ、"
                             "もう一度、はっきり発音して録画し直してください。",
                    "alignment_ok": False,
                }), 422

            _persist_ref_video(word_id, video_tmp)

            if MEDIA_PIPE_AVAILABLE:
                try:
                    native_lab = current_library().sound_dir(word_id) / f"{word_id}.lab"
                    mora_data: list[dict] = []
                    if native_lab.exists():
                        (_, ref_mora_list, *_) = lab_load(native_lab)
                        if ref_mora_list:
                            raw_ref = _extract_mora_lip_openness(video_tmp, ref_mora_list)
                            mora_data = [
                                {"label": item["label"], "v_h_ratio": item["openness"]}
                                for item in raw_ref
                            ]
                    reference_vectors, reference_ratios, reference_times = _extract_lip_data_timed(video_tmp)
                    refs = load_lip_refs()
                    entry: dict = {
                        "schema_version": 2,
                        "vectors":        reference_vectors,
                        "ratios":         reference_ratios,
                        "times":          reference_times,
                    }
                    if mora_data:
                        entry["mora_data"] = mora_data
                    refs[word_id] = entry
                    save_lip_refs(refs)
                    result["mora_count"] = len(mora_data)
                except Exception:
                    traceback.print_exc()

            return jsonify(result)
        finally:
            try:
                if os.path.exists(video_tmp):
                    os.remove(video_tmp)
            except Exception:
                pass

    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


# gunicorn / 直接起動の両方でディレクトリを確保する
ensure_directories()

def warm_up() -> None:
    """起動直後の最初の解析が遅くならないよう、重いライブラリの準備を先に済ませる。

    librosa は最初の MFCC で読み込みと numba のコンパイルに数秒かかり、MediaPipe は FaceMesh を作るのに
    0.2 秒ほど、結果画面のテンプレートは最初の表示で組み立てに時間がかかる。以前は、サーバーを起動して
    最初に「結果を見る」を押した人がその分を待っていた。gunicorn.conf.py（ワーカーの起動後）と
    python app.py が、裏のスレッドで呼ぶ。
    """
    started = time.monotonic()
    try:
        import librosa
        import numpy as _np
        y = _np.random.default_rng(0).normal(0.0, 0.01, 16000).astype(_np.float32)
        mfcc = librosa.feature.mfcc(y=y, sr=16000, n_mfcc=13, n_fft=512, hop_length=160)
        librosa.feature.delta(mfcc, order=2)
        fastdtw(mfcc.T, mfcc.T[::-1])
        if MEDIA_PIPE_AVAILABLE:
            with _face_mesh() as mesh:
                mesh.process(_np.zeros((240, 320, 3), dtype=_np.uint8))
        for name in ("line_graph.html", "audio.html", "select.html", "history.html"):
            app.jinja_env.get_template(name)
        print(f"[warm_up] 解析の準備ができました（{time.monotonic() - started:.1f} 秒）", flush=True)
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    _port  = int(os.environ.get("PORT", 5000))
    _debug = os.environ.get("FLASK_ENV", "production") == "development"
    # デバッグ時の自動再読み込みでは、実際に要求を受ける子プロセスでだけ準備する
    if not _debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=warm_up, name="warm-up", daemon=True).start()
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=_port, debug=_debug)
