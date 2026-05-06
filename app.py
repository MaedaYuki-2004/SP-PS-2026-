"""
app.py
Flask ルーティングのみを担当するエントリーポイント。
ビジネスロジックはすべて core/ 配下のモジュールに委譲する。
"""
from __future__ import annotations

import re
import traceback
from pathlib import Path

import json
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
from core.audio import (
    convert_to_16kHz,
    read_sample,
    reduce_noise_wav,
    segment_audio,
)
from core.vocab import list_words, register_word, get_reading_for_julius, get_word, delete_word, update_word
from core.alignment import lab_load, log_load, perl_run
from core.pitch import (
    comp,
    length_arrange,
    praat_pitch,
    resample_to_10ms,
    scale,
    smooth,
)
from core.evaluate import calc_total_score
from core.timbre import dtw_ascending_order
from core.vocab import list_words, register_word
from core.utils import pct_length, sleep_second

# ── アプリケーション初期化 ────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)
app.secret_key = FLASK_SECRET_KEY


def ensure_directories() -> None:
    """起動時に必要なディレクトリとファイルを作成する。"""
    for d in [CONFIG_DIR, AUDIO_WAV_DIR, AUDIO_MFCC_DIR, DISTANCE_RESULT_DIR, STATIC_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not (AUDIO_WAV_DIR / "test.txt").exists():
        (AUDIO_WAV_DIR / "test.txt").touch()
    if not WORD_ID_MEMO_PATH.exists():
        WORD_ID_MEMO_PATH.touch()


# ── ルーティング ──────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def select():
    if request.method == "POST":
        word_id = request.form.get("Words")
        if not word_id:
            return "単語を選択してください"
        reading = get_reading_for_julius(word_id)
        # word_id.txt と test.txt を更新
        WORD_ID_MEMO_PATH.write_text(word_id, encoding="utf-8")
        (AUDIO_WAV_DIR / "test.txt").write_text(reading, encoding="utf-8")
        return render_template("audio.html", test=reading)
    words = list_words()
    return render_template("select.html", words=words)


@app.route("/select")
def select_page():
    words = list_words()
    return render_template("select.html", words=words)


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
        # reduce_noise_wav(TEST_WAV_PATH)   # 一時無効化（ピッチへの影響を確認中）
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
        # reduce_noise_wav(TEST_WAV_PATH)   # 一時無効化（ピッチへの影響を確認中）
        perl_run()                        # Julius は同期実行なので完了後に OK を返す
        return "OK!"
    except Exception as exc:
        traceback.print_exc()
        return f"エラー: {exc}", 500


@app.route("/graph", methods=["GET", "POST"])
def audio_analysis():
    if request.method != "POST":
        return "送信できませんでした", 400
    try:
        # ── 単語 ID の読み込み ─────────────────────────────────────
        word_id   = WORD_ID_MEMO_PATH.read_text(encoding="utf-8").strip()
        num_match = re.search(r"\d+", word_id)
        num       = int(num_match.group())

        # ── パスの解決 ────────────────────────────────────────────
        audio_sample     = read_sample(word_id)
        audio_learn      = str(TEST_WAV_PATH)
        audio_learn_edit = str(TEST_SEGMENT_WAV_PATH)

        lab_sample = str(Path(audio_sample).with_suffix(".lab"))
        lab_learn  = str(TEST_LAB_PATH)
        log_sample = str(Path(audio_sample).with_suffix(".log"))
        log_learn  = str(TEST_LOG_PATH)

        # ── ピッチ抽出 ────────────────────────────────────────────
        pitch1, time1 = praat_pitch(audio_sample)
        pitch2, time2 = praat_pitch(audio_learn)

        # ── 【修正①】Praatピッチを10msグリッドにリサンプリング ──
        # これによりJuliusのフレーム番号を直接インデックスとして使える。
        # （従来はPraatフレームとJuliusフレームを混同していた）
        pitch1_10ms = resample_to_10ms(pitch1, time1)
        pitch2_10ms = resample_to_10ms(pitch2, time2)

        # ── アライメント読み込み（lab） ───────────────────────────
        (lab_list1, mora_list1, phoneme1, mora1,
         _, _, phoneme_length1, mora_length1) = lab_load(lab_sample)
        (lab_list2, mora_list2, phoneme2, mora2,
         _, _, phoneme_length2, mora_length2) = lab_load(lab_learn)

        if not lab_list1:
            raise ValueError(f"参照 lab が空です: {lab_sample}")
        if not lab_list2:
            raise ValueError(f"録音 lab が空です: {lab_learn}")

        # ── ピッチ補完・スムージング（表示用・元のPraat時刻軸のまま）
        pitch_native = smooth(comp(pitch1))
        pitch_learn  = smooth(comp(pitch2))

        # ── 境界時刻の抽出 ────────────────────────────────────────
        xline_phoneme1 = [float(i[0]) for i in lab_list1]
        xline_phoneme2 = [float(i[0]) for i in lab_list2]
        xline_mora1    = [float(i[0]) for i in mora_list1]
        xline_mora2    = [float(i[0]) for i in mora_list2]

        # ── アライメント読み込み（log） ───────────────────────────
        phoneme_frame1, phoneme_frame2, mora_frame1 = log_load(log_sample)
        if not phoneme_frame1 or not phoneme_frame2 or not mora_frame1:
            raise ValueError(f"参照音声のアライメント結果が空です: {log_sample}")

        # ── 【修正①】Juliusフレーム番号で10msグリッドを正しく切り出す
        # 旧コード: pitch1[julius_frame] → Praatフレームの混用でずれていた
        # 新コード: pitch1_10ms[julius_frame] → 10msグリッドなので整合する
        sil_start1 = int(phoneme_frame1[0][0])
        sil_end1   = int(phoneme_frame1[-1][1]) + 1
        pitch1_sil = pitch1_10ms[sil_start1:sil_end1]

        phoneme_frame3, phoneme_frame4, mora_frame2 = log_load(log_learn)
        if not phoneme_frame3 or not phoneme_frame4 or not mora_frame2:
            raise ValueError(f"録音音声のアライメント結果が空です: {log_learn}")

        sil_start2 = int(phoneme_frame3[0][0])
        sil_end2   = int(phoneme_frame3[-1][1]) + 1
        pitch2_sil = pitch2_10ms[sil_start2:sil_end2]

        # ── 長さ整合・正規化 ──────────────────────────────────────
        # phoneme_frame2/4 は相対Juliusフレーム、pitch_sil は10msグリッド
        # → インデックスの単位が一致しているので正しく整合できる
        pitch3     = length_arrange(pitch2_sil, phoneme_frame2, phoneme_frame4)
        xline_mora = [
            int(i[0]) - int(mora_frame1[0][0]) for i in mora_frame1
        ]

        pitch_fin  = scale(smooth(comp(pitch1_sil)))
        pitch_fin2 = scale(smooth(comp(pitch3)))
        x_axis     = list(range(len(pitch_fin)))

        # ── 発話区間の切り出し ────────────────────────────────────
        start1, end1 = float(lab_list1[0][0]), float(lab_list1[-1][1])
        start2, end2 = float(lab_list2[0][0]), float(lab_list2[-1][1])
        segment_audio(audio_learn, start2, end2)

        # ── DTW 音色評価 ──────────────────────────────────────────
        dtw_list, word_list, colors, _ = dtw_ascending_order(audio_learn_edit, num)

        # ── 発音スコア算出 ────────────────────────────────────────
        word_entry = get_word(word_id)
        accent     = word_entry.get("accent") if word_entry else None
        n_mora     = len(mora1)
        score_result = calc_total_score(
            pitch_fin2=pitch_fin2.tolist(),
            mora_values=xline_mora,
            accent=accent,
            n_mora=n_mora,
            native_mora_length=pct_length(mora_length1),
            user_mora_length=pct_length(mora_length2),
        )

        return render_template(
            "line_graph.html",
            original_filename=session.get("original_filename", "録音データ"),
            Native_pitch=pitch_native.tolist(), Native_time=time1,
            User_pitch=pitch_learn.tolist(),    User_time=time2,
            Native_phoneme_values=xline_phoneme1, Native_mora_values=xline_mora1,
            User_mora_values=xline_mora2,         User_phoneme_values=xline_phoneme2,
            phoneme_labels=phoneme1, mora_labels=mora1,
            start1=start1, end1=end1, start2=start2, end2=end2,
            mora_values=xline_mora, x_axis=x_axis,
            pitch_fin=pitch_fin.tolist(), pitch_fin2=pitch_fin2.tolist(),
            Native_phoneme_length=pct_length(phoneme_length1),
            User_phoneme_length=pct_length(phoneme_length2),
            Native_mora_length=pct_length(mora_length1),
            User_mora_length=pct_length(mora_length2),
            words=word_list, sort_distance=dtw_list, bar_color=colors,
            score=score_result,
        )

    except Exception as exc:
        traceback.print_exc()
        return f"解析中にエラーが発生しました: {exc}", 500


# ── サンプル音声配信 ─────────────────────────────────────────────────────

@app.route("/sample_audio/<word_id>")
def sample_audio(word_id: str):
    """
    単語選択画面のサンプル音声を配信する。
    探索順：
      1. web/static/sample/wordX.wav （既存の人間録音）
      2. data/raw_audio/sound/wordX/wordX.wav （VOICEVOX自動生成）
    """
    # 1. まず static/sample/ を探す（既存の録音済み音声）
    static_path = STATIC_DIR / "sample" / f"{word_id}.wav"
    if static_path.exists():
        return send_file(str(static_path), mimetype="audio/wav")

    # 2. なければ data/raw_audio/sound/ を探す（TTS生成音声）
    tts_path = RAW_AUDIO_DIR / "sound" / word_id / f"{word_id}.wav"
    if tts_path.exists():
        return send_file(str(tts_path), mimetype="audio/wav")

    return "not found", 404


# ── 単語削除・編集 API ───────────────────────────────────────────────────

@app.route("/admin/delete_word", methods=["POST"])
def api_delete_word():
    try:
        data    = request.get_json()
        word_id = data.get("word_id", "").strip()
        if not word_id:
            return jsonify({"error": "word_id が指定されていません"}), 400
        result = delete_word(word_id)
        return jsonify(result)
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
        result = update_word(word_id, display, reading, accent)
        return jsonify(result)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ── 管理画面 ──────────────────────────────────────────────────────────

@app.route("/admin")
def admin():
    words = list_words()
    return render_template("admin.html", words=words)


@app.route("/admin/add_word", methods=["POST"])
def add_word():
    try:
        data    = request.get_json()
        display = data.get("display", "").strip()
        reading = data.get("reading", "").strip()
        if not display or not reading:
            return jsonify({"error": "表示テキストとひらがな読みを入力してください"}), 400
        result = register_word(display, reading)
        return jsonify(result)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ── 起動 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ensure_directories()
    app.run(host="127.0.0.1", port=5000, debug=True)