"""
app.py
Flask ルーティングのみを担当するエントリーポイント。

【変更点】
  pitch_native_raw（NaN保持のネイティブ半音ピッチ）を生成して
  calc_total_score() に渡すよう変更。

  【背景】
  _pitch_correlation_score() が有声フレームのみで Pearson 相関を
  計算するように変更されたため、NaN-preserved の配列を渡す必要がある。

  変更前：pitch_fin_score（comp/smooth済み、NaN なし）をネイティブとして渡す
          → comp() による補間値も相関計算に混入
  変更後：pitch_native_raw_semi（hz_to_semitone 直後、NaN 保持）を渡す
          → 有声フレームのみで相関計算

  pitch_fin_score（comp/smooth済み）は H/L 分類・核検出用に引き続き使用。
"""
from __future__ import annotations

import re
import traceback
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, session

from config import (
    AUDIO_MFCC_DIR,
    AUDIO_WAV_DIR,
    CONFIG_DIR,
    DISTANCE_RESULT_DIR,
    FLASK_SECRET_KEY,
    RAW_AUDIO_DIR,
    STATIC_DIR,
    TEMPLATES_DIR,
    TEST_LAB_PATH,
    TEST_LOG_PATH,
    TEST_SEGMENT_WAV_PATH,
    TEST_WAV_PATH,
    WORD_ID_MEMO_PATH,
)
from core.audio import convert_to_16kHz, read_sample, segment_audio
from core.vocab import (
    list_words, register_word, get_reading_for_julius,
    get_word, delete_word, update_word,
)
from core.alignment import lab_load, log_load, perl_run, extract_julius_score
from core.pitch import (
    comp, estimate_pitch_range, hz_to_semitone,
    length_arrange, praat_pitch, resample_to_10ms, scale, smooth,
)
from core.evaluate import calc_total_score, calc_speaking_rate
from core.formant import extract_mora_formants, calc_vowel_score, calc_voice_quality
from core.timbre import dtw_ascending_order
from core.utils import pct_length, sleep_second

JULIUS_GATE_THRESHOLD = -3000

app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)
app.secret_key = FLASK_SECRET_KEY


def ensure_directories() -> None:
    for d in [CONFIG_DIR, AUDIO_WAV_DIR, AUDIO_MFCC_DIR, DISTANCE_RESULT_DIR, STATIC_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not (AUDIO_WAV_DIR / "test.txt").exists():
        (AUDIO_WAV_DIR / "test.txt").touch()
    if not WORD_ID_MEMO_PATH.exists():
        WORD_ID_MEMO_PATH.touch()


@app.route("/", methods=["GET", "POST"])
def select():
    if request.method == "POST":
        word_id = request.form.get("Words")
        if not word_id:
            return "単語を選択してください"
        reading = get_reading_for_julius(word_id)
        WORD_ID_MEMO_PATH.write_text(word_id, encoding="utf-8")
        (AUDIO_WAV_DIR / "test.txt").write_text(reading, encoding="utf-8")
        return render_template("audio.html", test=reading)
    return render_template("select.html", words=list_words())


@app.route("/select")
def select_page():
    return render_template("select.html", words=list_words())


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
        perl_run()
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
        perl_run()
        return "OK!"
    except Exception as exc:
        traceback.print_exc()
        return f"エラー: {exc}", 500


@app.route("/graph", methods=["GET", "POST"])
def audio_analysis():
    if request.method != "POST":
        return "送信できませんでした", 400
    try:
        word_id   = WORD_ID_MEMO_PATH.read_text(encoding="utf-8").strip()
        num_match = re.search(r"\d+", word_id)
        num       = int(num_match.group())

        audio_sample     = read_sample(word_id)
        audio_learn      = str(TEST_WAV_PATH)
        audio_learn_edit = str(TEST_SEGMENT_WAV_PATH)
        lab_sample       = str(Path(audio_sample).with_suffix(".lab"))
        lab_learn        = str(TEST_LAB_PATH)
        log_sample       = str(Path(audio_sample).with_suffix(".log"))
        log_learn        = str(TEST_LOG_PATH)

        # ── 話者ピッチ範囲自動推定 ──────────────────────────────────
        floor_sample, ceiling_sample = estimate_pitch_range(audio_sample)
        floor_learn,  ceiling_learn  = estimate_pitch_range(audio_learn)

        # ── ピッチ抽出 ──────────────────────────────────────────────
        pitch1, time1 = praat_pitch(audio_sample, pitch_floor=floor_sample, pitch_ceiling=ceiling_sample)
        pitch2, time2 = praat_pitch(audio_learn,  pitch_floor=floor_learn,  pitch_ceiling=ceiling_learn)

        pitch1_10ms = resample_to_10ms(pitch1, time1)
        pitch2_10ms = resample_to_10ms(pitch2, time2)

        # ── アライメント読み込み（lab） ─────────────────────────────
        (lab_list1, mora_list1, phoneme1, mora1,
         _, _, phoneme_length1, mora_length1) = lab_load(lab_sample)
        (lab_list2, mora_list2, phoneme2, mora2,
         _, _, phoneme_length2, mora_length2) = lab_load(lab_learn)

        if not lab_list1:
            raise ValueError(f"参照 lab が空です: {lab_sample}")
        if not lab_list2:
            raise ValueError(f"録音 lab が空です: {lab_learn}")

        # ── 表示用ピッチ（Hz・window=5 で滑らか） ───────────────────
        pitch_native = smooth(comp(pitch1), window=5)
        pitch_learn  = smooth(comp(pitch2), window=5)

        xline_phoneme1 = [float(i[0]) for i in lab_list1]
        xline_phoneme2 = [float(i[0]) for i in lab_list2]
        xline_mora1    = [float(i[0]) for i in mora_list1]
        xline_mora2    = [float(i[0]) for i in mora_list2]

        # ── アライメント読み込み（log） ─────────────────────────────
        phoneme_frame1, phoneme_frame2, mora_frame1 = log_load(log_sample)
        if not phoneme_frame1 or not phoneme_frame2 or not mora_frame1:
            raise ValueError(f"参照音声のアライメント結果が空です: {log_sample}")

        sil_start1 = int(phoneme_frame1[0][0])
        sil_end1   = int(phoneme_frame1[-1][1]) + 1
        pitch1_sil = pitch1_10ms[sil_start1:sil_end1]

        phoneme_frame3, phoneme_frame4, mora_frame2 = log_load(log_learn)
        if not phoneme_frame3 or not phoneme_frame4 or not mora_frame2:
            raise ValueError(f"録音音声のアライメント結果が空です: {log_learn}")

        sil_start2 = int(phoneme_frame3[0][0])
        sil_end2   = int(phoneme_frame3[-1][1]) + 1
        pitch2_sil = pitch2_10ms[sil_start2:sil_end2]

        pitch3     = length_arrange(pitch2_sil, phoneme_frame2, phoneme_frame4)
        xline_mora = [int(i[0]) - int(mora_frame1[0][0]) for i in mora_frame1]

        start1, end1 = float(lab_list1[0][0]), float(lab_list1[-1][1])
        start2, end2 = float(lab_list2[0][0]), float(lab_list2[-1][1])
        segment_audio(audio_learn, start2, end2)

        dtw_list, word_list, colors, _ = dtw_ascending_order(audio_learn_edit, word_id)

        # ── Julius 品質ゲート ─────────────────────────────────────────
        julius_score = extract_julius_score(log_learn)

        # ── 半音変換 ─────────────────────────────────────────────────
        pitch1_sil_semi = hz_to_semitone(pitch1_sil, ref_hz=None)
        pitch3_semi     = hz_to_semitone(pitch3,     ref_hz=None)

        # ── 表示用ピッチ（comp/smooth/scale 済み） ───────────────────
        pitch_fin_disp  = scale(smooth(comp(pitch1_sil_semi), window=5))
        pitch_fin2_disp = scale(smooth(comp(pitch3_semi),     window=5))
        x_axis          = list(range(len(pitch_fin_disp)))

        common_kwargs = dict(
            original_filename=session.get("original_filename", "録音データ"),
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
        )

        if julius_score is not None and julius_score < JULIUS_GATE_THRESHOLD:
            return render_template(
                "line_graph.html",
                **common_kwargs,
                score=None,
                alignment_failed=True,
                julius_score=julius_score,
                alignment_feedback=(
                    "音声のアライメントに失敗しました。"
                    "以下の点を確認して再録音してください：\n"
                    "① マイクにしっかり近づく\n"
                    "② はっきりと、ゆっくり発音する\n"
                    "③ 静かな環境で録音する"
                ),
                voice_quality={"jitter": None, "shimmer": None, "feedback": None},
                speaking_rate=0.0,
                rate_feedback=None,
            )

        # ── スコア計算用ピッチ（H/L・核検出用：comp/smooth済み） ────
        pitch_fin_score  = smooth(comp(pitch1_sil_semi), window=3)
        pitch_fin2_score = smooth(comp(pitch3_semi),     window=3)

        # ── 有声フレーム保持配列（Pearson相関・安定度・有声率用） ───
        # comp() を通す前の NaN 保持配列を使う。
        # これにより _pitch_correlation_score() で「声が出ている
        # フレームのみ」の相関計算が可能になる。
        pitch_native_raw = pitch1_sil_semi.copy()  # NaN保持（ネイティブ）
        pitch_user_raw   = pitch3_semi.copy()      # NaN保持（録音）

        # ── フォルマント分析 ──────────────────────────────────────────
        max_formant_sample = 5500.0 if ceiling_sample > 400 else 5000.0
        max_formant_learn  = 5500.0 if ceiling_learn  > 400 else 5000.0
        try:
            native_formants = extract_mora_formants(audio_sample, mora_list1, max_formant=max_formant_sample)
            user_formants   = extract_mora_formants(audio_learn,  mora_list2, max_formant=max_formant_learn)
            vowel_score, vowel_feedback = calc_vowel_score(native_formants, user_formants)
        except Exception:
            vowel_score, vowel_feedback = 10.0, "母音の評価中にエラーが発生しました。"

        # ── 声質評価 ──────────────────────────────────────────────────
        try:
            voice_quality = calc_voice_quality(
                audio_learn, start2, end2,
                pitch_floor=floor_learn, pitch_ceiling=ceiling_learn,
            )
        except Exception:
            voice_quality = {"jitter": None, "shimmer": None, "feedback": None}

        # ── 発話速度評価 ─────────────────────────────────────────────
        try:
            native_rate, _           = calc_speaking_rate(lab_list1, mora_list1)
            user_rate, rate_feedback = calc_speaking_rate(lab_list2, mora_list2, native_rate=native_rate)
        except Exception:
            user_rate, rate_feedback = 0.0, None

        # ── 発音スコア算出 ────────────────────────────────────────────
        word_entry = get_word(word_id)
        accent     = word_entry.get("accent") if word_entry else None
        n_mora     = len(mora1)

        score_result = calc_total_score(
            pitch_fin2=pitch_fin2_score.tolist(),    # comp/smooth済み（H/L・核検出用）
            mora_values=xline_mora,
            accent=accent,
            n_mora=n_mora,
            native_mora_length=pct_length(mora_length1),
            user_mora_length=pct_length(mora_length2),
            mora_labels=mora1,
            pitch_fin=pitch_fin_score.tolist(),      # comp/smooth済み（後方互換）
            pitch_user_raw=pitch_user_raw,           # NaN保持（安定度・有声率・Pearson用）
            pitch_native_raw=pitch_native_raw,       # NaN保持（Pearson用・新規）
            vowel_score=vowel_score,
            vowel_feedback=vowel_feedback,
        )

        score_result["alignment_failed"]   = False
        score_result["julius_score"]       = julius_score
        score_result["alignment_feedback"] = None

        return render_template(
            "line_graph.html",
            **common_kwargs,
            score=score_result,
            alignment_failed=False,
            julius_score=julius_score,
            alignment_feedback=None,
            voice_quality=voice_quality,
            speaking_rate=user_rate,
            rate_feedback=rate_feedback,
        )

    except Exception as exc:
        traceback.print_exc()
        return f"解析中にエラーが発生しました: {exc}", 500


@app.route("/sample_audio/<word_id>")
def sample_audio(word_id: str):
    static_path = STATIC_DIR / "sample" / f"{word_id}.wav"
    if static_path.exists():
        return send_file(str(static_path), mimetype="audio/wav")
    tts_path = RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.wav"
    if tts_path.exists():
        return send_file(str(tts_path), mimetype="audio/wav")
    return "not found", 404


@app.route("/admin/delete_word", methods=["POST"])
def api_delete_word():
    try:
        data    = request.get_json()
        word_id = data.get("word_id", "").strip()
        if not word_id:
            return jsonify({"error": "word_id が指定されていません"}), 400
        return jsonify(delete_word(word_id))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/admin/update_word", methods=["POST"])
def api_update_word():
    try:
        data    = request.get_json()
        word_id = data.get("word_id", "").strip()
        display = data.get("display", "").strip()
        reading = data.get("reading", "").strip()
        accent  = data.get("accent")
        if not word_id or not display or not reading:
            return jsonify({"error": "必須項目が不足しています"}), 400
        return jsonify(update_word(word_id, display, reading, accent))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/admin")
def admin():
    return render_template("admin.html", words=list_words())


@app.route("/admin/add_word", methods=["POST"])
def add_word():
    try:
        data    = request.get_json()
        display = data.get("display", "").strip()
        reading = data.get("reading", "").strip()
        if not display or not reading:
            return jsonify({"error": "表示テキストとひらがな読みを入力してください"}), 400
        return jsonify(register_word(display, reading))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    ensure_directories()
    app.run(host="127.0.0.1", port=5000, debug=True)