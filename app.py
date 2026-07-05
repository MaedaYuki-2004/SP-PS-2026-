"""
app.py — Flask ルーティング

【変更点】
  calc_vowel_score() に pitch_ceiling_native=ceiling_sample を追加。
  ネイティブとユーザー両方の性別を考慮した補正を有効化する。
"""
from __future__ import annotations

import csv
import io
import math
import os
import re
import shutil
import tempfile
import traceback
from pathlib import Path

try:
    import cv2
    import mediapipe as mp
    import numpy as np
    from fastdtw import fastdtw
    from scipy.spatial.distance import euclidean
    MEDIA_PIPE_AVAILABLE = True
except ImportError:
    cv2 = mp = np = fastdtw = euclidean = None
    MEDIA_PIPE_AVAILABLE = False

from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, session

from config import (
    AUDIO_MFCC_DIR, AUDIO_WAV_DIR, CONFIG_DIR, DATA_DIR, DISTANCE_RESULT_DIR,
    FLASK_SECRET_KEY, RAW_AUDIO_DIR, STATIC_DIR, TEMPLATES_DIR,
    TEST_LAB_PATH, TEST_LOG_PATH, TEST_SEGMENT_WAV_PATH,
    TEST_WAV_PATH, WORD_ID_MEMO_PATH,
)
from core.audio     import convert_to_16kHz, read_sample, segment_audio
from core.vocab     import list_words, register_word, get_reading_for_julius, get_word, delete_word, update_word, update_word_tags, get_all_tags, update_word_note
from core.alignment import lab_load, log_load, run_alignment, extract_julius_score, run_alignment_on_file
from core.pitch     import comp, estimate_pitch_range, hz_to_semitone, length_arrange, praat_pitch, resample_to_10ms, scale, smooth
from core.evaluate  import calc_total_score, calc_speaking_rate, calc_mora_scores
from core.formant   import extract_mora_formants, calc_vowel_score, calc_voice_quality
from core.timbre    import audio_mfcc, dtw_ascending_order
from core.history   import save_record, load_history, get_last_score, get_stats, load_word_history, get_daily_counts, get_overall_score, get_weekly_report
from core.utils     import pct_length, sleep_second, romaji_mora_to_kana
from core.analysis  import compute_learning_stats
from core.confidence import bootstrap_ci, needs_more_data

JULIUS_GATE_THRESHOLD = -3000

app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)
app.secret_key = FLASK_SECRET_KEY


# ── ユーティリティ ────────────────────────────────────────────────────

def ensure_directories() -> None:
    for d in [CONFIG_DIR, AUDIO_WAV_DIR, AUDIO_MFCC_DIR, DISTANCE_RESULT_DIR, STATIC_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not (AUDIO_WAV_DIR / "test.txt").exists():
        (AUDIO_WAV_DIR / "test.txt").touch()
    if not WORD_ID_MEMO_PATH.exists():
        WORD_ID_MEMO_PATH.touch()


_face_mesh = None


def _get_face_mesh():
    global _face_mesh
    if not MEDIA_PIPE_AVAILABLE:
        raise RuntimeError("MediaPipe と OpenCV がインストールされていません。")
    if _face_mesh is None:
        _face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
    return _face_mesh


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


def _extract_lip_data(video_path: str, max_frames: int = 50) -> tuple[list[list[float]], list[float]]:
    """後方互換ラッパー（vectors, ratios のみ返す）。"""
    vectors, ratios, _times = _extract_lip_data_timed(video_path, max_frames)
    return vectors, ratios


def _extract_lip_data_timed(
    video_path: str, max_frames: int = 50,
) -> tuple[list[list[float]], list[float], list[float]]:
    """唇形状ベクトル・開き比率に加えて各フレームの時刻（秒）を返す。

    顔検出に失敗したフレームはスキップされるため、ベクトルの並び順から
    時刻を復元することはできない。音映像同期分析（core/sync.py）が
    正確な時刻対応を必要とするので、フレーム時刻を明示的に記録する。
    """
    face_mesh = _get_face_mesh()
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
    distance, path = fastdtw(reference_vectors, test_vectors, dist=euclidean)
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
            face_mesh = _get_face_mesh()
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


def _save_temp_video(uploaded_file):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.webm')
    uploaded_file.save(tmp.name)
    tmp.close()
    return tmp.name


def _persist_ref_video(word_id: str, video_path: str) -> None:
    """お手本録画（webm）を sound_dir に永続保存する。

    先生の実際の口の動きを母音トレーナー・録音画面で再生できるように、
    抽出処理のためだけに使い捨てていた動画ファイルを保存しておく。
    失敗しても登録処理自体は継続してよいので例外は握りつぶす。
    """
    try:
        sound_dir = RAW_AUDIO_DIR / "sound" / word_id
        sound_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_path, sound_dir / f"{word_id}.webm")
    except Exception:
        traceback.print_exc()


def _lip_refs_path() -> Path:
    return DATA_DIR / "config" / "lip_refs.json"


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
    p = _lip_refs_path()
    try:
        import json
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
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
    sound_dir = RAW_AUDIO_DIR / "sound" / word_id
    wav_path  = sound_dir / f"{word_id}.wav"
    lab_path  = sound_dir / f"{word_id}.lab"

    if not wav_path.exists():
        static_path = STATIC_DIR / "sample" / f"{word_id}.wav"
        if static_path.exists():
            wav_path = static_path
        else:
            return {"has_data": False}

    if not lab_path.exists():
        return {"has_data": False}

    mtime = wav_path.stat().st_mtime
    cached = _REF_PITCH_CACHE.get(word_id)
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
                "label":  str(m[2]),
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
        _REF_PITCH_CACHE[word_id] = (mtime, result)
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
        WORD_ID_MEMO_PATH.write_text(word_id, encoding="utf-8")
        (AUDIO_WAV_DIR / "test.txt").write_text(reading, encoding="utf-8")
        accent_val = word_entry.get("accent") if word_entry else None
        hl_list = _accent_pattern_for_word(accent_val, reading)
        mora_labels_list = _split_moras(reading)
        accent_hl = [{"label": l, "hl": h} for l, h in zip(mora_labels_list, hl_list)]
        ref_preview = _get_reference_pitch_data(word_id)
        return render_template("audio.html", test=reading, display=display, word_id=word_id,
                               ref_preview=ref_preview, accent_hl=accent_hl,
                               has_ref_video=has_sample_video(word_id))

    words    = list_words()
    word_map = {w["word_id"]: w for w in words}
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
                           overall=get_overall_score(), weekly=get_weekly_report())


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
    word_map = {w["word_id"]: w for w in words}
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
                           overall=get_overall_score(), weekly=get_weekly_report())


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

    # ── 分析タブ用データ ─────────────────────────────────────────────
    import json as _json
    _words_db_path = DATA_DIR / "config" / "words_db.json"
    _words_db = _json.loads(_words_db_path.read_text(encoding="utf-8")) if _words_db_path.exists() else {}
    analysis_stats = compute_learning_stats(history, _words_db)

    return render_template("history.html",
                           history=history[:100], stats=stats,
                           word_latest=word_latest, word_history=word_history,
                           words=words, accent_stats=accent_stats,
                           analysis_stats=analysis_stats)


@app.route("/analysis")
def analysis_page():
    import json
    history    = load_history()
    words_db_path = DATA_DIR / "config" / "words_db.json"
    words_db   = json.loads(words_db_path.read_text(encoding="utf-8")) if words_db_path.exists() else {}
    stats      = compute_learning_stats(history, words_db)
    return render_template("analysis.html", stats=stats)

@app.route("/lip_compare")
def lip_compare_page():
    return render_template("lip_compare.html")

@app.route("/lip_compare", methods=["POST"])
def lip_compare_process():
    try:
        ref_video = request.files.get('ref_video')
        test_video = request.files.get('test_video')
        if not ref_video or not test_video:
            return jsonify({"error": "お手本動画とテスト動画の両方をアップロードしてください。"}), 400

        ref_path = _save_temp_video(ref_video)
        test_path = _save_temp_video(test_video)
        try:
            reference_vectors, reference_ratios = _extract_lip_data(ref_path)
            test_vectors, test_ratios = _extract_lip_data(test_path)
            score, distance = _compute_lip_score(reference_vectors, test_vectors)
            return jsonify({
                "score": score,
                "distance": distance,
                "ref_ratios": reference_ratios,
                "test_ratios": test_ratios,
            })
        finally:
            for path in (ref_path, test_path):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500

@app.route("/history/export.csv")
def export_history_csv():
    history = load_history()
    output  = io.StringIO()
    writer  = csv.writer(output)
    writer.writerow(["日時","単語ID","単語","読み","合計点","アクセント","長さ","母音","グレード"])
    for r in history:
        writer.writerow([
            (r.get("timestamp") or "")[:19].replace("T"," "),
            r.get("word_id",""), r.get("display",""), r.get("reading",""),
            r.get("total",""), r.get("accent_score",""), r.get("length_score",""),
            r.get("vowel_score",""), r.get("grade",""),
        ])
    return Response("\ufeff" + output.getvalue(),
                    mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=sp-ps-history.csv"})


@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    words = list_words()
    if request.method == "GET":
        return render_template("upload.html", words=words)
    try:
        file    = request.files["file"]
        word_id = request.form.get("fileword", "").strip()
        reading = get_reading_for_julius(word_id)
        WORD_ID_MEMO_PATH.write_text(word_id, encoding="utf-8")
        (AUDIO_WAV_DIR / "test.txt").write_text(reading, encoding="utf-8")
        file.save(str(TEST_WAV_PATH))
        convert_to_16kHz(TEST_WAV_PATH, TEST_WAV_PATH)
        sleep_second()
        run_alignment()
        return render_template("upload.html", words=words, message="アップロード完了")
    except Exception as exc:
        traceback.print_exc()
        return render_template("upload.html", words=words, error=str(exc))


@app.route("/audio", methods=["GET", "POST"])
def record_audio():
    if request.method == "GET":
        return render_template("audio.html")
    try:
        file = request.files["file"]
        file.save(str(TEST_WAV_PATH))
        convert_to_16kHz(TEST_WAV_PATH, TEST_WAV_PATH)
        run_alignment()
        return "OK!"
    except Exception as exc:
        traceback.print_exc()
        return f"エラー: {exc}", 500


@app.route("/upload_lip_video", methods=["POST"])
def upload_lip_video():
    try:
        mode = request.form.get("mode", "").strip()
        if mode not in ("ref", "test"):
            return jsonify({"error": "mode は ref または test を指定してください。"}), 400
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "動画ファイルが送信されていません。"}), 400

        lip_video_paths = session.get("lip_video_paths", {})
        old_path = lip_video_paths.get(mode)
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

        new_path = _save_temp_video(file)
        lip_video_paths[mode] = new_path
        session["lip_video_paths"] = lip_video_paths
        session.modified = True
        # 参照（ref）をアップロードした場合は抽出データを永続化しておく
        if mode == 'ref' and MEDIA_PIPE_AVAILABLE:
            try:
                reference_vectors, reference_ratios, reference_times = _extract_lip_data_timed(new_path)

                try:
                    current_word_id = WORD_ID_MEMO_PATH.read_text(encoding="utf-8").strip()
                except Exception:
                    current_word_id = None

                if current_word_id:
                    _persist_ref_video(current_word_id, new_path)

                # ── アライメントによるモーラ別参照データを生成 ────────────
                mora_data: list[dict] = []
                if current_word_id:
                    try:
                        word_entry = get_word(current_word_id)
                        reading    = word_entry.get("reading", "") if word_entry else ""
                        if not reading:
                            reading = get_reading_for_julius(current_word_id)
                        if reading:
                            sound_dir  = RAW_AUDIO_DIR / "sound" / current_word_id
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
                                    (AUDIO_MFCC_DIR / f"{current_word_id}.bin").parent.mkdir(parents=True, exist_ok=True)
                                    mfcc.tofile(str(AUDIO_MFCC_DIR / f"{current_word_id}.bin"))
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
    if TEST_WAV_PATH.exists():
        return send_file(str(TEST_WAV_PATH), mimetype="audio/wav")
    return "録音データが見つかりません", 404


# --- Lip refs admin APIs -------------------------------------------------
@app.route('/api/lip_refs')
def api_lip_refs_list():
    try:
        refs = load_lip_refs()
        # 戻り値はキー一覧のみ
        return jsonify({"keys": list(refs.keys())})
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


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


@app.route('/api/lip_refs/delete', methods=['POST'])
def api_lip_refs_delete():
    try:
        data = request.get_json() or {}
        word_id = (data.get('word_id') or '').strip()
        if not word_id:
            return jsonify({"error": "word_id required"}), 400
        refs = load_lip_refs()
        if word_id in refs:
            refs.pop(word_id, None)
            save_lip_refs(refs)
            return jsonify({"message": "deleted"})
        return jsonify({"error": "not found"}), 404
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route('/api/lip_refs/overwrite', methods=['POST'])
def api_lip_refs_overwrite():
    try:
        word_id = (request.form.get('word_id') or '').strip()
        file = request.files.get('file')
        if not word_id:
            return jsonify({"error": "word_id required"}), 400
        if not file:
            return jsonify({"error": "file required"}), 400
        # 一時保存して抽出
        tmp = _save_temp_video(file)
        try:
            _persist_ref_video(word_id, tmp)
            vectors, ratios, lip_times = _extract_lip_data_timed(tmp)

            # モーラ別参照データをアライメントで生成（upload_lip_video と同じパイプライン）
            mora_data: list[dict] = []
            try:
                word_entry = get_word(word_id)
                reading    = word_entry.get("reading", "") if word_entry else ""
                if not reading:
                    reading = get_reading_for_julius(word_id)
                if reading:
                    sound_dir  = RAW_AUDIO_DIR / "sound" / word_id
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
                            (AUDIO_MFCC_DIR / f"{word_id}.bin").parent.mkdir(parents=True, exist_ok=True)
                            mfcc.tofile(str(AUDIO_MFCC_DIR / f"{word_id}.bin"))
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
    try:
        word_id   = WORD_ID_MEMO_PATH.read_text(encoding="utf-8").strip()

        lip_compare = None
        lip_ref_ratios = []
        lip_test_ratios = []
        lip_paths = session.get("lip_video_paths", {})
        ref_video = lip_paths.get("ref")
        test_video = lip_paths.get("test")
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
        audio_learn      = str(TEST_WAV_PATH)
        audio_learn_edit = str(TEST_SEGMENT_WAV_PATH)
        lab_sample = str(Path(audio_sample).with_suffix(".lab"))
        lab_learn  = str(TEST_LAB_PATH)
        log_sample = str(Path(audio_sample).with_suffix(".log"))
        log_learn  = str(TEST_LOG_PATH)

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
        segment_audio(audio_learn, start2, end2)

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

        # ── 音映像クロスチェック（口と声の同期分析・表示のみ採点に非影響） ──
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
        try:
            native_formants = extract_mora_formants(audio_sample, mora_list1, max_formant=max_formant_sample, use_cache=True)
            user_formants   = extract_mora_formants(audio_learn,  mora_list2, max_formant=max_formant_learn,  use_cache=False)
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
                native_formants=native_formants if 'native_formants' in dir() else None,
                user_formants=user_formants   if 'user_formants'   in dir() else None,
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

        try:
            save_record(word_id, display, reading, score_result, mora_scores=mora_scores)
        except Exception:
            pass

        suggestions = _get_suggestions(word_id, score_result, words_list)

        return render_template("line_graph.html", **common_kwargs,
                               score=score_result,
                               alignment_failed=False, julius_score=julius_score,
                               voice_quality=voice_quality,
                               speaking_rate=user_rate, rate_feedback=rate_feedback,
                               score_delta=score_delta, suggestions=suggestions,
                               mora_scores=mora_scores, worst_mora=worst_mora,
                               ci_info=ci_info,
                               native_formants=native_formants if 'native_formants' in dir() else None,
                               user_formants=user_formants if 'user_formants' in dir() else None,
                               lip_compare=lip_compare, lip_ref_ratios=lip_ref_ratios, lip_test_ratios=lip_test_ratios,
                               av_sync=av_sync)

    except Exception as exc:
        traceback.print_exc()
        return render_template("error.html", code=500, title="解析エラー", message=str(exc)), 500


@app.route("/sample_audio/<word_id>")
def sample_audio(word_id: str):
    static_path = STATIC_DIR / "sample" / f"{word_id}.wav"
    if static_path.exists(): return send_file(str(static_path), mimetype="audio/wav")
    tts_path = RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.wav"
    if tts_path.exists(): return send_file(str(tts_path), mimetype="audio/wav")
    return "not found", 404


@app.route("/sample_video/<word_id>")
def sample_video(word_id: str):
    """先生のお手本録画（webm）を配信する。母音トレーナー等での実映像再生用。"""
    video_path = RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.webm"
    if video_path.exists(): return send_file(str(video_path), mimetype="video/webm")
    return "not found", 404


def has_sample_video(word_id: str) -> bool:
    return (RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.webm").exists()


@app.route("/admin/delete_word", methods=["POST"])
def api_delete_word():
    try:
        data    = request.get_json(force=True, silent=True) or {}
        word_id = data.get("word_id", "").strip()
        if not word_id: return jsonify({"error": "word_id が指定されていません"}), 400
        return jsonify(delete_word(word_id))
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


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
    WORD_ID_MEMO_PATH.write_text(word_id, encoding="utf-8")
    (AUDIO_WAV_DIR / "test.txt").write_text(reading, encoding="utf-8")
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
        return jsonify(update_word_tags(word_id, tags))
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


@app.route("/api/tags")
def api_get_all_tags():
    return jsonify({"tags": get_all_tags()})


@app.route("/api/history/calendar")
def api_history_calendar():
    days = min(int(request.args.get("days", 84)), 365)
    return jsonify(get_daily_counts(days))


@app.route("/admin")
def admin():
    return render_template("admin.html", words=list_words(), stats=get_stats(), all_tags=get_all_tags())


@app.route("/admin/add_word", methods=["POST"])
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

        # 音声抽出 → 登録 → 口形データ保存（テキスト+動画をまとめて atomic に登録）
        video_tmp = _save_temp_video(file)
        try:
            ref_wav = _webm_to_wav(video_tmp)
            try:
                result = register_word(display, reading, Path(ref_wav))
            finally:
                try:
                    os.remove(ref_wav)
                except Exception:
                    pass

            word_id = result["word_id"]
            _persist_ref_video(word_id, video_tmp)

            if MEDIA_PIPE_AVAILABLE:
                try:
                    sound_dir  = RAW_AUDIO_DIR / "sound" / word_id
                    native_lab = sound_dir / f"{word_id}.lab"
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

if __name__ == "__main__":
    _port  = int(os.environ.get("PORT", 5000))
    _debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(host="0.0.0.0", port=_port, debug=_debug)