from __future__ import annotations

import os
import re
import sys
import time
import subprocess
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import noisereduce as nr
import numpy as np
import parselmouth
from fastdtw import fastdtw
from flask import Flask, render_template, request, session
from pydub import AudioSegment
from scipy.io import wavfile
from scipy.spatial.distance import euclidean

# --- フォルダ構造の定義（最適化後のパス） ---
BASE_DIR = Path(__file__).resolve().parent

WEB_DIR = BASE_DIR / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = DATA_DIR / "config"
RAW_AUDIO_DIR = DATA_DIR / "raw_audio"
AUDIO_WAV_DIR = RAW_AUDIO_DIR / "wav"
AUDIO_MFCC_DIR = DATA_DIR / "mfcc"
DISTANCE_RESULT_DIR = STATIC_DIR / "distance_result"

TEST_TXT_PATH = AUDIO_WAV_DIR / "test.txt"
WORD_ID_MEMO_PATH = CONFIG_DIR / "word_id.txt"
AUDIO_SCP_PATH = CONFIG_DIR / "audio.scp"
WORDS_TXT_PATH = CONFIG_DIR / "words.txt"
TEST_WAV_PATH = AUDIO_WAV_DIR / "test.wav"
TEST_LAB_PATH = AUDIO_WAV_DIR / "test.lab"
TEST_LOG_PATH = AUDIO_WAV_DIR / "test.log"
TEST_SEGMENT_WAV_PATH = RAW_AUDIO_DIR / "test2.wav"

SCRIPTS_DIR = BASE_DIR / "scripts"
PERL_SCRIPT_PATH = SCRIPTS_DIR / "segment_julius.pl"

ALLOWED_EXTENSIONS = {".wav"}

WORDS = {
    "word1": "おんど", "word2": "かいけー", "word3": "がっこー", "word4": "ぎんこー", "word5": "こーえん",
    "word6": "こーつー", "word7": "こーばい", "word8": "しごと", "word9": "しつど", "word10": "じどーしゃ",
    "word11": "しゅーしょく", "word12": "しゅみ", "word13": "しょーめーしょ", "word14": "しょくざい",
    "word15": "すけじゅーる", "word16": "すーぱー", "word17": "せーきゅーしょ", "word18": "ぜーきん",
    "word19": "ちゅーしょく", "word20": "ちょーしょく", "word21": "ちょーみりょー", "word22": "ちょきん",
    "word23": "でんしゃ", "word24": "でんわ", "word25": "どーろ", "word26": "びょーいん",
    "word27": "びよーいん", "word28": "ほどー", "word29": "ゆーしょく", "word30": "やちん",
}

VOWELS = ["a", "i", "u", "e", "o", "a:", "i:", "u:", "e:", "o:"]
CONSONANTS = ["b", "c", "d", "f", "g", "h", "j", "k", "n", "m", "p", "r", "s", "t", "w", "y", "z"]

app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

def ensure_directories() -> None:
    DIRS = [CONFIG_DIR, AUDIO_WAV_DIR, AUDIO_MFCC_DIR, DISTANCE_RESULT_DIR, STATIC_DIR]
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)
    if not TEST_TXT_PATH.exists(): TEST_TXT_PATH.touch()
    if not WORD_ID_MEMO_PATH.exists(): WORD_ID_MEMO_PATH.touch()

def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS

def word_select(word_id: str) -> str:
    if word_id not in WORDS:
        raise ValueError(f"不正な単語IDです: {word_id}")
    word = WORDS[word_id]
    with TEST_TXT_PATH.open(mode="w", encoding="utf-8") as f:
        f.write(word)
    with WORD_ID_MEMO_PATH.open(mode="w", encoding="utf-8") as f:
        f.write(word_id)
    return word

def sleep_second(seconds: float = 1.5) -> None:
    time.sleep(seconds)

def perl_run() -> None:
    if not PERL_SCRIPT_PATH.exists():
        raise FileNotFoundError(f"Perlスクリプトが見つかりません: {PERL_SCRIPT_PATH}")
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
        raise RuntimeError(f"外部プログラムの実行に失敗しました: {exc}")

def read_sample(word_id: str) -> str:
    if not AUDIO_SCP_PATH.exists():
        raise FileNotFoundError(f"audio.scpが見つかりません: {AUDIO_SCP_PATH}")
    match = re.search(r"\d+", word_id)
    if not match: return ""
    file_idx = int(match.group()) - 1
    with AUDIO_SCP_PATH.open(mode="r", encoding="utf-8") as f:
        wav_paths = f.read().splitlines()
    if file_idx < 0 or file_idx >= len(wav_paths):
        raise IndexError(f"audio.scp の範囲外です")
    
    # パスに含まれる古い「audio/」という表記を自動で削除して調整する
    sample_path_str = wav_paths[file_idx].replace("\\", "/")
    if sample_path_str.startswith("audio/"):
        sample_path_str = sample_path_str[6:]
        
    sample_abs = (RAW_AUDIO_DIR / sample_path_str).resolve()
    return str(sample_abs)

def mora_time(phones: list[list[str | int | float]]) -> list[list[str | int | float]]:
    data: list[str | int | float] = []
    mora_list: list[list[str | int | float]] = []
    for i in range(len(phones)):
        phone = str(phones[i][2])
        if i == 0 and phone in VOWELS:
            data.extend([phones[i][0], phones[i][1], phone])
            mora_list.append(data)
            data = []
            continue
        if i < len(phones) - 1:
            next_phone = str(phones[i + 1][2])
            is_consonant = (phone in CONSONANTS) or (len(phone) == 2 and ":" not in phone)
            if is_consonant and next_phone in VOWELS:
                merged_phone = phone + next_phone
                data.extend([phones[i][0], phones[i + 1][1], merged_phone])
                mora_list.append(data)
                data = []
                continue
        if phone not in VOWELS and phone not in CONSONANTS:
            data.extend([phones[i][0], phones[i][1], phone])
            mora_list.append(data)
            data = []
            continue
        if i >= 1 and phone in VOWELS and str(phones[i - 1][2]) in VOWELS:
            data.extend([phones[i][0], phones[i][1], phone])
            mora_list.append(data)
            data = []
    return mora_list

def lab_load(lab_file: str | Path):
    lab_path = Path(lab_file)
    lab_list: list[list[str]] = []
    phoneme_start: list[str] = []
    mora_start: list[str] = []
    phoneme_length: list[float] = []
    mora_length: list[float] = []
    phoneme: list[str] = []
    mora: list[str] = []
    with lab_path.open("r", encoding="utf-8") as f:
        for line in f.readlines():
            a = line.split()
            if not a or ("silB" in a or "silE" in a): continue
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
    return lab_list, mora_list, phoneme, mora, phoneme_start, mora_start, phoneme_length, mora_length

def librosa_pitch(sound_file: str | Path):
    y, sr = librosa.load(str(sound_file), sr=None)
    if str(y.dtype) == "int16": y = (y / 32768).astype(np.float32)
    f0, _, _ = librosa.pyin(y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"), sr=sr)
    times = librosa.times_like(f0)
    return f0.tolist(), times.tolist()

def praat_pitch(sound_file: str | Path):
    snd = parselmouth.Sound(str(sound_file))
    pitch = snd.to_pitch()
    pitch_values = pitch.selected_array["frequency"]
    pitch_values[pitch_values == 0] = np.nan
    return pitch_values.tolist(), pitch.xs().tolist()

def phone_list(frame: list[int | str]) -> list[list[int | str]]:
    result_f = []
    for i in range(0, len(frame), 3):
        result_f.append(frame[i:i + 3])
    return result_f

def phoneme_frame(phoneme: list[list[int | str]]) -> list[list[int | str]]:
    if not phoneme: return phoneme
    start = int(phoneme[0][0])
    for i in range(len(phoneme)):
        phoneme[i][0] = int(phoneme[i][0]) - start
        phoneme[i][1] = int(phoneme[i][1]) - start
    return phoneme

def log_load(log_file: str | Path):
    log_path = Path(log_file)
    num = 0
    frame: list[int | str] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f.readlines():
            if "begin forced alignment" in line: num = 1
            elif "end forced alignment" in line: num = 0
            if num == 1 and "[" in line and "silB" not in line and "silE" not in line:
                m = re.findall(r"\d+", line)
                if len(m) < 2: continue
                start_f, end_f = int(m[0]), int(m[1])
                n = re.search(r"[A-Z]|[a-z]+[:]?", line)
                if not n: continue
                frame.extend([start_f, end_f, n.group()])
    phoneme_list = phone_list(frame)
    phoneme_list2 = phone_list(frame.copy())
    mora_list = mora_time(phoneme_list)
    phoneme_only = phoneme_frame(phoneme_list2)
    return phoneme_list, phoneme_only, mora_list

def graph_compensate(pitch: np.ndarray, idx: int, count: int) -> np.ndarray:
    n_space = pitch[idx - 1: idx + count + 1]
    distance = n_space[-1] - n_space[0]
    difference = round(distance / (count + 1), 2)
    start = n_space[0] + difference
    end = n_space[0] + (difference * count)
    num = np.linspace(start, end, count)
    pitch[idx: idx + count] = num
    return pitch

def count_nan(pitch: np.ndarray) -> np.ndarray:
    if len(pitch) == 0: return pitch
    if np.all(np.isnan(pitch)): return np.zeros_like(pitch, dtype=float)
    idx = 0
    while idx < len(pitch):
        if np.isnan(pitch[idx]):
            start = idx
            while idx < len(pitch) and np.isnan(pitch[idx]): idx += 1
            end = idx
            if start == 0 or end >= len(pitch): continue
            count = end - start
            pitch = graph_compensate(pitch, start, count)
        else:
            idx += 1
    return pitch

def graph_compensate2(pitch: np.ndarray) -> np.ndarray:
    if len(pitch) == 0: return pitch
    if np.all(np.isnan(pitch)): return np.zeros_like(pitch, dtype=float)
    idx = 0
    while idx < len(pitch) and np.isnan(pitch[idx]): idx += 1
    if idx > 0 and idx < len(pitch): pitch[0:idx] = pitch[idx]
    idx2 = len(pitch) - 1
    while idx2 >= 0 and np.isnan(pitch[idx2]): idx2 -= 1
    if idx2 >= 0 and idx2 < len(pitch) - 1: pitch[idx2 + 1:] = pitch[idx2]
    return pitch

def comp(pitch: list[float] | np.ndarray) -> np.ndarray:
    pitch_array = np.array(pitch, dtype=float)
    pitch2 = count_nan(np.copy(pitch_array))
    pitch2 = graph_compensate2(pitch2)
    return pitch2

def smooth(pitch: list[float] | np.ndarray) -> np.ndarray:
    pitch2 = np.array(pitch, dtype=float)
    if len(pitch2) == 0: return pitch2
    ran = 5
    pad = int((ran - 1) / 2)
    pitch2 = np.concatenate([np.full(pad, pitch2[0]), pitch2, np.full(pad, pitch2[-1])])
    pitch3 = np.zeros(np.size(pitch2) - (ran - 1))
    for i in range(np.size(pitch2) - (ran - 1)):
        pitch3[i] = np.sum(pitch2[i:i + ran]) / ran
    return pitch3

def length_arrange(pitch: list[float] | np.ndarray, phoneme1, phoneme2) -> np.ndarray:
    pitch_arr = np.array(pitch, dtype=float)
    pitch3 = np.array([], dtype=float)

    # 変更点:
    # 参照音声と録音音声で音素数が一致しない場合があるため、
    # 共通して存在する件数までに制限して index error を防ぐ
    usable_len = min(len(phoneme1), len(phoneme2))
    if usable_len == 0:
        raise ValueError("音素フレーム情報が不足しています")

    for i in range(usable_len):
        standard = int(phoneme1[i][1]) - int(phoneme1[i][0]) + 1
        frame_in = int(phoneme2[i][1]) - int(phoneme2[i][0]) + 1
        dif = standard - frame_in
        start = int(phoneme2[i][0])
        end = int(phoneme2[i][1]) + 1
        pitch2 = pitch_arr[start:end]

        if dif > 0:
            pitch2 = np.append(pitch2, np.full(dif, np.nan))
        elif dif < 0:
            if i == 0:
                pitch2 = np.delete(pitch2, slice(0, abs(dif)), 0)
            else:
                pitch2 = np.delete(pitch2, slice(len(pitch2) - abs(dif), len(pitch2)), 0)

        pitch3 = pitch2 if i == 0 else np.concatenate([pitch3, pitch2])

    return pitch3

def segment_audio(sound_file: str | Path, start: float, end: float) -> None:
    sound = AudioSegment.from_wav(str(sound_file))
    cut_start = max(0, int(start * 1000) - 100)
    cut_end = max(cut_start, int(end * 1000) + 100)
    seg_sound = sound[cut_start:cut_end]
    seg_sound.export(str(TEST_SEGMENT_WAV_PATH), format="wav")

def pct_length(length: list[float]) -> list[float]:
    total = sum(length)
    if total == 0: return [0.0 for _ in length]
    return [round((i / total) * 100, 2) for i in length]

def audio_mfcc(wav_file: str | Path) -> np.ndarray:
    y, sr = librosa.load(str(wav_file), sr=None)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, n_fft=512, hop_length=160)
    mfcc = mfcc.T
    mfcc = np.delete(mfcc, 0, 1)
    return mfcc

def create_dtw_list(mfcc1: np.ndarray, start: int = 0, finish: int = 30, sample_path: str | Path = AUDIO_MFCC_DIR) -> list[float]:
    dtw_list = []
    sample_path = Path(sample_path)
    num_dims = 12
    for i in range(start, finish):
        sample_file = sample_path / f"word{i + 1}.bin"
        mfcc2 = np.fromfile(str(sample_file), dtype=np.float32)
        mfcc2 = mfcc2.reshape(-1, num_dims)
        distance, _ = fastdtw(mfcc1, mfcc2, dist=euclidean)
        dtw_list.append(float(distance))
    return dtw_list

def create_word_list(path: str | Path = WORDS_TXT_PATH) -> list[str]:
    with Path(path).open("r", encoding="utf-8") as f:
        return [data.rstrip("\n") for data in f]

def descending_order(dtw_list: list[float], word_list: list[str]):
    pairs = sorted(zip(dtw_list, word_list), key=lambda x: x[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]

def dtw_ascending_order(mfcc_file: str | Path, num: int):
    mfcc1 = audio_mfcc(mfcc_file)
    dtw_list = create_dtw_list(mfcc1)
    word_list = create_word_list()
    word_list_copy = word_list.copy()
    dtw_list, word_list = descending_order(dtw_list, word_list)
    red_index = 0
    for i in range(len(dtw_list)):
        if word_list_copy[num - 1] == word_list[i]:
            red_index = i
            break
    colors = ["red" if n == red_index else "blue" for n in range(len(word_list))]
    return dtw_list, word_list, colors, red_index

def scale(values: list[float] | np.ndarray) -> np.ndarray:
    values_arr = np.array(values, dtype=float)
    if len(values_arr) == 0: return values_arr
    min_val, max_val = np.min(values_arr), np.max(values_arr)
    if max_val == min_val: return np.zeros_like(values_arr)
    normalized = np.zeros(np.size(values_arr))
    for i in range(np.size(values_arr)):
        normalized[i] = (values_arr[i] - min_val) / (max_val - min_val)
    return normalized

def convert_to_16kHz(input_path: str | Path, output_path: str | Path) -> bool:
    sound = AudioSegment.from_wav(str(input_path))
    needs_convert = sound.frame_rate != 16000 or sound.channels != 1 or sound.sample_width != 2
    if needs_convert:
        sound = sound.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        sound.export(str(output_path), format="wav")
        return True
    if input_path != output_path:
        sound.export(str(output_path), format="wav")
    return False

# --- Webルーティング ---

@app.route("/", methods=["GET", "POST"])
def select():
    if request.method == "POST":
        word_id = request.form.get("Words")
        if not word_id: return "単語を選択してください"
        word = word_select(word_id)
        return render_template("audio.html", test=word)
    return render_template("select.html")

@app.route("/select")
def select_page():
    return render_template("select.html")

@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    if request.method == "GET": return render_template("upload.html")
    try:
        file = request.files["file"]
        word_id = request.form.get("fileword", "").strip()
        word_select(word_id)
        file.save(str(TEST_WAV_PATH))
        convert_to_16kHz(TEST_WAV_PATH, TEST_WAV_PATH)
        sleep_second()
        perl_run()
        return render_template("upload.html", message="アップロード完了")
    except Exception as exc:
        return f"エラー: {exc}"

@app.route("/audio", methods=["GET", "POST"])
def record_audio():
    if request.method == "GET": return render_template("audio.html")
    try:
        file = request.files["file"]
        file.save(str(TEST_WAV_PATH))
        convert_to_16kHz(TEST_WAV_PATH, TEST_WAV_PATH)
        sleep_second()
        perl_run()
        return "OK!"
    except Exception as exc:
        return f"エラー: {exc}", 500

@app.route("/graph", methods=["GET", "POST"])
def audio_analysis():
    if request.method != "POST": return "送信できませんでした", 400
    try:
        with WORD_ID_MEMO_PATH.open(mode="r", encoding="utf-8") as f:
            word_id = f.readline().strip()
        num_match = re.search(r"\d+", word_id)
        num = int(num_match.group())
        audio_sample = read_sample(word_id)
        audio_learn = str(TEST_WAV_PATH)
        audio_learn_edit = str(TEST_SEGMENT_WAV_PATH)

        lab_sample = str(Path(audio_sample).with_suffix(".lab"))
        lab_learn = str(TEST_LAB_PATH)
        log_sample = str(Path(audio_sample).with_suffix(".log"))
        log_learn = str(TEST_LOG_PATH)

        pitch1, time1 = praat_pitch(audio_sample)
        pitch2, time2 = praat_pitch(audio_learn)

        lab_list1, mora_list1, phoneme1, mora1, _, _, phoneme_length1, mora_length1 = lab_load(lab_sample)
        lab_list2, mora_list2, phoneme2, mora2, _, _, phoneme_length2, mora_length2 = lab_load(lab_learn)

        # 変更点:
        # lab が空だと後続の [0], [-1] アクセスで落ちるため、先に明示チェックする
        if not lab_list1:
            raise ValueError(f"参照labが空です: {lab_sample}")
        if not lab_list2:
            raise ValueError(f"録音labが空です: {lab_learn}")

        pitch_com1, pitch_com2 = comp(pitch1), comp(pitch2)
        pitch_native, pitch_learn = smooth(pitch_com1), smooth(pitch_com2)

        xline_phoneme1 = [float(i[0]) for i in lab_list1]
        xline_phoneme2 = [float(i[0]) for i in lab_list2]
        xline_mora1 = [float(i[0]) for i in mora_list1]
        xline_mora2 = [float(i[0]) for i in mora_list2]

        phoneme_frame1, phoneme_frame2, mora_frame1 = log_load(log_sample)

        # 変更点:
        # 参照側 log の解析結果が空だと phoneme_frame1[0] で落ちるためチェックする
        if not phoneme_frame1 or not phoneme_frame2 or not mora_frame1:
            raise ValueError(f"参照音声のアライメント結果が空です: {log_sample}")

        pitch1_sil = pitch1[int(phoneme_frame1[0][0]): int(phoneme_frame1[-1][1]) + 1]

        phoneme_frame3, phoneme_frame4, mora_frame2 = log_load(log_learn)

        # 変更点:
        # 録音側 log の解析結果が空だと phoneme_frame3[0] で落ちるためチェックする
        if not phoneme_frame3 or not phoneme_frame4 or not mora_frame2:
            raise ValueError(f"録音音声のアライメント結果が空です: {log_learn}")

        pitch2_sil = pitch2[int(phoneme_frame3[0][0]): int(phoneme_frame3[-1][1]) + 1]

        # 変更点:
        # length_arrange() 側でも件数ズレに耐えるように修正済み
        pitch3 = length_arrange(pitch2_sil, phoneme_frame2, phoneme_frame4)
        xline_mora = [int(i[0]) - int(mora_frame1[0][0]) for i in mora_frame1]

        pitch_fin = scale(smooth(comp(pitch1_sil)))
        pitch_fin2 = scale(smooth(comp(pitch3)))

        x_axis = list(range(len(pitch_fin)))

        start1, end1 = float(lab_list1[0][0]), float(lab_list1[-1][1])
        start2, end2 = float(lab_list2[0][0]), float(lab_list2[-1][1])

        segment_audio(audio_learn, start2, end2)
        dtw_list, word_list, colors, _ = dtw_ascending_order(audio_learn_edit, num)

        return render_template(
            "line_graph.html",
            original_filename=session.get("original_filename", "録音データ"),
            Native_pitch=pitch_native.tolist(), Native_time=time1,
            User_pitch=pitch_learn.tolist(), User_time=time2,
            Native_phoneme_values=xline_phoneme1, Native_mora_values=xline_mora1,
            User_mora_values=xline_mora2, User_phoneme_values=xline_phoneme2,
            phoneme_labels=phoneme1, mora_labels=mora1,
            start1=start1, end1=end1, start2=start2, end2=end2,
            mora_values=xline_mora, x_axis=x_axis,
            pitch_fin=pitch_fin.tolist(), pitch_fin2=pitch_fin2.tolist(),
            Native_phoneme_length=pct_length(phoneme_length1), User_phoneme_length=pct_length(phoneme_length2),
            Native_mora_length=pct_length(mora_length1), User_mora_length=pct_length(mora_length2),
            words=word_list, sort_distance=dtw_list, bar_color=colors,
        )
    except Exception as exc:
        # 変更点:
        # ターミナル側に詳細なスタックトレースを出して、
        # どこで落ちたか追いやすくする
        import traceback
        traceback.print_exc()
        return f"解析中にエラーが発生しました: {exc}", 500
if __name__ == "__main__":
    ensure_directories()
    app.run(host="127.0.0.1", port=5000, debug=True)