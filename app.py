"""
app.py
Flask ルーティングのみを担当するエントリーポイント。
ビジネスロジックはすべて core/ 配下のモジュールに委譲する。
"""
from __future__ import annotations

import re
import traceback
from pathlib import Path

from flask import Flask, render_template, request, session

from config import (
    AUDIO_MFCC_DIR,
    AUDIO_WAV_DIR,
    CONFIG_DIR,
    DISTANCE_RESULT_DIR,
    FLASK_SECRET_KEY,
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
    word_select,
)
from core.alignment import lab_load, log_load, perl_run
from core.pitch import (
    comp,
    length_arrange,
    praat_pitch,
    resample_to_10ms,
    scale,
    smooth,
)
from core.timbre import dtw_ascending_order
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
        word = word_select(word_id)
        return render_template("audio.html", test=word)
    return render_template("select.html")


@app.route("/select")
def select_page():
    return render_template("select.html")


@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    if request.method == "GET":
        return render_template("upload.html")
    try:
        file    = request.files["file"]
        word_id = request.form.get("fileword", "").strip()
        word_select(word_id)
        file.save(str(TEST_WAV_PATH))
        convert_to_16kHz(TEST_WAV_PATH, TEST_WAV_PATH)
        # reduce_noise_wav(TEST_WAV_PATH)   # 一時無効化（ピッチへの影響を確認中）
        sleep_second()
        perl_run()
        return render_template("upload.html", message="アップロード完了")
    except Exception as exc:
        traceback.print_exc()
        return f"エラー: {exc}"


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
        )

    except Exception as exc:
        traceback.print_exc()
        return f"解析中にエラーが発生しました: {exc}", 500


# ── 起動 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ensure_directories()
    app.run(host="127.0.0.1", port=5000, debug=True)