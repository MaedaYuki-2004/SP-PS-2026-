"""
app.py — Flask ルーティング

【変更点】
  calc_vowel_score() に pitch_ceiling_native=ceiling_sample を追加。
  ネイティブとユーザー両方の性別を考慮した補正を有効化する。
"""
from __future__ import annotations

import csv
import io
import re
import traceback
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file, session

from config import (
    AUDIO_MFCC_DIR, AUDIO_WAV_DIR, CONFIG_DIR, DISTANCE_RESULT_DIR,
    FLASK_SECRET_KEY, RAW_AUDIO_DIR, STATIC_DIR, TEMPLATES_DIR,
    TEST_LAB_PATH, TEST_LOG_PATH, TEST_SEGMENT_WAV_PATH,
    TEST_WAV_PATH, WORD_ID_MEMO_PATH,
)
from core.audio     import convert_to_16kHz, read_sample, segment_audio
from core.vocab     import list_words, register_word, get_reading_for_julius, get_word, delete_word, update_word
from core.alignment import lab_load, log_load, run_alignment, extract_julius_score
from core.pitch     import comp, estimate_pitch_range, hz_to_semitone, length_arrange, praat_pitch, resample_to_10ms, scale, smooth
from core.evaluate  import calc_total_score, calc_speaking_rate
from core.formant   import extract_mora_formants, calc_vowel_score, calc_voice_quality
from core.timbre    import dtw_ascending_order
from core.quest     import check_and_update_quests, load_active_quests
from core.history   import save_record, load_history, get_last_score, get_stats
from core.utils     import pct_length, sleep_second

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


def _enrich_quests(quests, word_map: dict) -> list[dict]:
    result = []
    for q in quests:
        d = q.to_dict() if hasattr(q, "to_dict") else dict(q)
        d["word_display"] = word_map.get(d.get("word_id", ""), {}).get("display", d.get("word_id", ""))
        result.append(d)
    return result


def _score_delta(current, prev) -> str | None:
    if current is None or prev is None:
        return None
    diff = round(float(current) - float(prev), 1)
    return f"+{diff}" if diff >= 0 else str(diff)


_SMALL_KANA = set('ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ')

def _mora_count(reading: str) -> int:
    return max(1, sum(1 for c in reading if c not in _SMALL_KANA))

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
            return "単語を選択してください"
        reading    = get_reading_for_julius(word_id)
        word_entry = get_word(word_id)
        display    = word_entry.get("display", reading) if word_entry else reading
        WORD_ID_MEMO_PATH.write_text(word_id, encoding="utf-8")
        (AUDIO_WAV_DIR / "test.txt").write_text(reading, encoding="utf-8")
        return render_template("audio.html", test=reading, display=display, word_id=word_id)

    words    = list_words()
    word_map = {w["word_id"]: w for w in words}
    stats    = get_stats()
    quests   = _enrich_quests(load_active_quests(), word_map)
    accent_patterns = {
        w["word_id"]: _accent_pattern_for_word(w.get("accent"), w.get("reading", ""))
        for w in words
    }
    return render_template("select.html", words=words, active_quests=quests,
                           stats=stats, accent_patterns=accent_patterns)


@app.route("/select")
def select_page():
    words    = list_words()
    word_map = {w["word_id"]: w for w in words}
    stats    = get_stats()
    quests   = _enrich_quests(load_active_quests(), word_map)
    accent_patterns = {
        w["word_id"]: _accent_pattern_for_word(w.get("accent"), w.get("reading", ""))
        for w in words
    }
    return render_template("select.html", words=words, active_quests=quests,
                           stats=stats, accent_patterns=accent_patterns)


@app.route("/history")
def history_page():
    history  = load_history()
    stats    = get_stats()
    words    = list_words()

    word_history: dict[str, list] = {}
    for record in reversed(history):
        wid = record.get("word_id")
        if wid:
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

    return render_template("history.html",
                           history=history[:100], stats=stats,
                           word_latest=word_latest, word_history=word_history,
                           words=words, accent_stats=accent_stats)


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


@app.route("/recorded_audio")
def recorded_audio():
    """直前の録音（test.wav）を返す。結果ページの比較再生ボタンで使用。"""
    if TEST_WAV_PATH.exists():
        return send_file(str(TEST_WAV_PATH), mimetype="audio/wav")
    return "録音データが見つかりません", 404


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
        )

        words_list = list_words()
        word_map   = {w["word_id"]: w for w in words_list}

        if julius_score is not None and julius_score < JULIUS_GATE_THRESHOLD:
            return render_template("line_graph.html", **common_kwargs,
                                   score=None, alignment_failed=True,
                                   julius_score=julius_score,
                                   voice_quality={"jitter":None,"shimmer":None,"feedback":None},
                                   speaking_rate=0.0, rate_feedback=None,
                                   newly_completed=[], active_quests=_enrich_quests(load_active_quests(), word_map),
                                   score_delta=None, suggestions=[])

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

        score_delta = _score_delta(score_result.get("total"), prev_score.get("total") if prev_score else None)

        try:
            save_record(word_id, display, reading, score_result)
        except Exception:
            pass

        try:
            newly_completed_raw, _, active_raw = check_and_update_quests(score_result, word_id)
            newly_completed = _enrich_quests(newly_completed_raw, word_map)
            active_quests   = _enrich_quests(active_raw, word_map)
        except Exception:
            newly_completed = []
            active_quests   = _enrich_quests(load_active_quests(), word_map)

        suggestions = _get_suggestions(word_id, score_result, words_list)

        return render_template("line_graph.html", **common_kwargs,
                               score=score_result,
                               alignment_failed=False, julius_score=julius_score,
                               voice_quality=voice_quality,
                               speaking_rate=user_rate, rate_feedback=rate_feedback,
                               newly_completed=newly_completed, active_quests=active_quests,
                               score_delta=score_delta, suggestions=suggestions)

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


@app.route("/admin/delete_word", methods=["POST"])
def api_delete_word():
    try:
        data    = request.get_json()
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


@app.route("/admin")
def admin():
    return render_template("admin.html", words=list_words(), stats=get_stats())


@app.route("/admin/add_word", methods=["POST"])
def add_word():
    try:
        data    = request.get_json()
        display = data.get("display", "").strip()
        reading = data.get("reading", "").strip()
        if not display or not reading: return jsonify({"error": "表示テキストとひらがな読みを入力してください"}), 400
        return jsonify(register_word(display, reading))
    except Exception as exc:
        traceback.print_exc(); return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    ensure_directories()
    app.run(host="127.0.0.1", port=5000, debug=True)