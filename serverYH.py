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

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
AUDIO_DIR = BASE_DIR / "audio"
AUDIO_WAV_DIR = AUDIO_DIR / "wav"
AUDIO_MFCC_DIR = AUDIO_DIR / "mfcc"
DISTANCE_RESULT_DIR = STATIC_DIR / "distance_result"

TEST_TXT_PATH = AUDIO_WAV_DIR / "test.txt"
WORD_ID_MEMO_PATH = STATIC_DIR / "word_id.txt"
AUDIO_SCP_PATH = STATIC_DIR / "audio.scp"
WORDS_TXT_PATH = STATIC_DIR / "words.txt"
TEST_WAV_PATH = AUDIO_WAV_DIR / "test.wav"
TEST_LAB_PATH = AUDIO_WAV_DIR / "test.lab"
TEST_LOG_PATH = AUDIO_WAV_DIR / "test.log"
TEST_SEGMENT_WAV_PATH = AUDIO_DIR / "test2.wav"
PERL_SCRIPT_PATH = AUDIO_DIR / "segment_julius.pl"

ALLOWED_EXTENSIONS = {".wav"}

WORDS = {
    "word1": "おんど",
    "word2": "かいけー",
    "word3": "がっこー",
    "word4": "ぎんこー",
    "word5": "こーえん",
    "word6": "こーつー",
    "word7": "こーばい",
    "word8": "しごと",
    "word9": "しつど",
    "word10": "じどーしゃ",
    "word11": "しゅーしょく",
    "word12": "しゅみ",
    "word13": "しょーめーしょ",
    "word14": "しょくざい",
    "word15": "すけじゅーる",
    "word16": "すーぱー",
    "word17": "せーきゅーしょ",
    "word18": "ぜーきん",
    "word19": "ちゅーしょく",
    "word20": "ちょーしょく",
    "word21": "ちょーみりょー",
    "word22": "ちょきん",
    "word23": "でんしゃ",
    "word24": "でんわ",
    "word25": "どーろ",
    "word26": "びょーいん",
    "word27": "びよーいん",
    "word28": "ほどー",
    "word29": "ゆーしょく",
    "word30": "やちん",
}

VOWELS = ["a", "i", "u", "e", "o", "a:", "i:", "u:", "e:", "o:"]
CONSONANTS = ["b", "c", "d", "f", "g", "h", "j", "k", "n", "m", "p", "r", "s", "t", "w", "y", "z"]

app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")


def ensure_directories() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_WAV_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_MFCC_DIR.mkdir(parents=True, exist_ok=True)
    DISTANCE_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    TEST_TXT_PATH.touch(exist_ok=True)
    WORD_ID_MEMO_PATH.touch(exist_ok=True)


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
        raise FileNotFoundError(f"Perl スクリプトが見つかりません: {PERL_SCRIPT_PATH}")

    try:
        subprocess.run(
            ["perl", PERL_SCRIPT_PATH.name],
            check=True,
            cwd=str(AUDIO_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Perl が見つかりません。Perl をインストールして PATH を設定してください。") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"外部プログラムの実行に失敗しました: {exc.stderr}") from exc


def noise_reduce(audio_file: Path = TEST_WAV_PATH) -> None:
    if not audio_file.exists():
        raise FileNotFoundError(f"音声ファイルが見つかりません: {audio_file}")

    sr, y = wavfile.read(str(audio_file))
    data_n = nr.reduce_noise(y=y, sr=sr, prop_decrease=0.7)

    sound = AudioSegment(
        data=data_n.tobytes(),
        sample_width=2,
        frame_rate=sr,
        channels=1,
    )
    sound.export(str(audio_file), format="wav")


def read_sample(word_id: str) -> str:
    if not AUDIO_SCP_PATH.exists():
        raise FileNotFoundError(f"audio.scp が見つかりません: {AUDIO_SCP_PATH}")

    match = re.search(r"\d+", word_id)
    if not match:
        raise ValueError(f"word_id から番号を取得できません: {word_id}")

    file_idx = int(match.group()) - 1

    with AUDIO_SCP_PATH.open(mode="r", encoding="utf-8") as f:
        wav_paths = f.read().splitlines()

    if file_idx < 0 or file_idx >= len(wav_paths):
        raise IndexError(f"audio.scp の範囲外です: index={file_idx}")

    sample_path = wav_paths[file_idx]
    sample_abs = (BASE_DIR / sample_path).resolve() if not Path(sample_path).is_absolute() else Path(sample_path)
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
    if not lab_path.exists():
        raise FileNotFoundError(f"lab ファイルが見つかりません: {lab_path}")

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
            if not a or ("silB" in a or "silE" in a):
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

    return lab_list, mora_list, phoneme, mora, phoneme_start, mora_start, phoneme_length, mora_length


def librosa_pitch(sound_file: str | Path):
    y, sr = librosa.load(str(sound_file), sr=None)

    if str(y.dtype) == "int16":
        y = (y / 32768).astype(np.float32)

    f0, _, _ = librosa.pyin(
        y,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=sr,
    )
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
    if not phoneme:
        return phoneme

    start = int(phoneme[0][0])
    for i in range(len(phoneme)):
        phoneme[i][0] = int(phoneme[i][0]) - start
        phoneme[i][1] = int(phoneme[i][1]) - start
    return phoneme


def log_load(log_file: str | Path):
    log_path = Path(log_file)
    if not log_path.exists():
        raise FileNotFoundError(f"log ファイルが見つかりません: {log_path}")

    num = 0
    frame: list[int | str] = []

    with log_path.open("r", encoding="utf-8") as f:
        for line in f.readlines():
            if "begin forced alignment" in line:
                num = 1
            elif "end forced alignment" in line:
                num = 0

            if num == 1 and "[" in line and "silB" not in line and "silE" not in line:
                m = re.findall(r"\d+", line)
                if len(m) < 2:
                    continue

                start_f, end_f = int(m[0]), int(m[1])
                n = re.search(r"[A-Z]|[a-z]+[:]?", line)
                if not n:
                    continue

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
    if len(pitch) == 0:
        return pitch

    if np.all(np.isnan(pitch)):
        return np.zeros_like(pitch, dtype=float)

    idx = 0
    while idx < len(pitch):
        if np.isnan(pitch[idx]):
            start = idx
            while idx < len(pitch) and np.isnan(pitch[idx]):
                idx += 1
            end = idx

            if start == 0 or end >= len(pitch):
                continue

            count = end - start
            pitch = graph_compensate(pitch, start, count)
        else:
            idx += 1

    return pitch


def graph_compensate2(pitch: np.ndarray) -> np.ndarray:
    if len(pitch) == 0:
        return pitch

    if np.all(np.isnan(pitch)):
        return np.zeros_like(pitch, dtype=float)

    idx = 0
    while idx < len(pitch) and np.isnan(pitch[idx]):
        idx += 1
    if idx > 0 and idx < len(pitch):
        pitch[0:idx] = pitch[idx]

    idx2 = len(pitch) - 1
    while idx2 >= 0 and np.isnan(pitch[idx2]):
        idx2 -= 1
    if idx2 >= 0 and idx2 < len(pitch) - 1:
        pitch[idx2 + 1:] = pitch[idx2]

    return pitch


def comp(pitch: list[float] | np.ndarray) -> np.ndarray:
    pitch_array = np.array(pitch, dtype=float)
    pitch2 = count_nan(np.copy(pitch_array))
    pitch2 = graph_compensate2(pitch2)
    return pitch2


def smooth(pitch: list[float] | np.ndarray) -> np.ndarray:
    pitch2 = np.array(pitch, dtype=float)
    if len(pitch2) == 0:
        return pitch2

    ran = 5
    pad = int((ran - 1) / 2)
    pitch2 = np.concatenate([np.full(pad, pitch2[0]), pitch2, np.full(pad, pitch2[-1])])
    pitch3 = np.zeros(np.size(pitch2) - (ran - 1))

    for i in range(np.size(pitch2) - (ran - 1)):
        pitch3[i] = np.sum(pitch2[i:i + ran]) / ran

    return pitch3


def change_calc(pitch: list[float] | np.ndarray) -> list[float]:
    pitch_arr = np.array(pitch, dtype=float)
    if len(pitch_arr) == 0 or pitch_arr[0] == 0:
        return []

    change = []
    for i in range(1, len(pitch_arr)):
        num = (pitch_arr[i] - pitch_arr[0]) / pitch_arr[0]
        change.append(round(float(num), 2))
    return change


def length_arrange(pitch: list[float] | np.ndarray, phoneme1, phoneme2) -> np.ndarray:
    pitch_arr = np.array(pitch, dtype=float)
    pitch3 = np.array([], dtype=float)

    for i in range(len(phoneme1)):
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
    if total == 0:
        return [0.0 for _ in length]
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
        if not sample_file.exists():
            raise FileNotFoundError(f"MFCC 正解データが見つかりません: {sample_file}")

        mfcc2 = np.fromfile(str(sample_file), dtype=np.float32)
        mfcc2 = mfcc2.reshape(-1, num_dims)
        distance, _ = fastdtw(mfcc1, mfcc2, dist=euclidean)
        dtw_list.append(float(distance))

    return dtw_list


def create_word_list(path: str | Path = WORDS_TXT_PATH) -> list[str]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"words.txt が見つかりません: {path}")

    with path.open("r", encoding="utf-8") as f:
        return [data.rstrip("\n") for data in f]


def descending_order(dtw_list: list[float], word_list: list[str]):
    pairs = sorted(zip(dtw_list, word_list), key=lambda x: x[0])
    sorted_dtw = [p[0] for p in pairs]
    sorted_words = [p[1] for p in pairs]
    return sorted_dtw, sorted_words


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


def create_graph(dtw_list: list[float], word_list: list[str], colors: list[str], red_index: int, img_path: str | Path) -> None:
    plt.rcParams["font.family"] = "MS Gothic"
    plt.rcParams["lines.linewidth"] = 3

    pattern = r"[あ-ん]+"
    y = [_ for _ in range(len(dtw_list), 0, -1)]

    fig = plt.figure(figsize=(9, 6))
    ax = fig.add_subplot(1, 1, 1)
    ax.tick_params(left=False)
    ax.barh(y, dtw_list, height=0.8, align="center", label="Native", color=colors)
    ax.set_yticks(y, word_list)
    ax.set_yticklabels(word_list, ha="left")
    ax.get_yaxis().set_tick_params(pad=75)
    ax.get_yticklabels()[red_index].set_color("red")

    matched = re.search(pattern, word_list[red_index])
    title_word = matched.group() if matched else word_list[red_index]
    ax.set_title(f"入力単語：{title_word}", loc="left")
    ax.set_xlim(0, max(dtw_list) + 1000)

    img_path = Path(img_path)
    img_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(img_path), bbox_inches="tight")
    plt.close(fig)


def scale(values: list[float] | np.ndarray) -> np.ndarray:
    values_arr = np.array(values, dtype=float)
    if len(values_arr) == 0:
        return values_arr

    min_val = np.min(values_arr)
    max_val = np.max(values_arr)

    if max_val == min_val:
        return np.zeros_like(values_arr)

    normalized = np.zeros(np.size(values_arr))
    for i in range(np.size(values_arr)):
        normalized[i] = (values_arr[i] - min_val) / (max_val - min_val)
    return normalized


def convert_to_16kHz(input_path: str | Path, output_path: str | Path) -> bool:
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {input_path}")

    sound = AudioSegment.from_wav(str(input_path))
    needs_convert = sound.frame_rate != 16000 or sound.channels != 1 or sound.sample_width != 2

    if needs_convert:
        sound = sound.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        sound.export(str(output_path), format="wav")
        return True

    if input_path != output_path:
        sound.export(str(output_path), format="wav")

    return False


@app.route("/", methods=["GET", "POST"])
def select():
    if request.method == "POST":
        try:
            word_id = request.form.get("Words")
            if not word_id:
                return "単語を選択した状態で「録音」ボタンを押してください"

            word = word_select(word_id)
            return render_template("audio.html", test=word)
        except Exception as exc:
            return f"エラー: {exc}", 400

    return render_template("select.html")


@app.route("/select")
def select_page():
    return render_template("select.html")


@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    if request.method == "GET":
        return render_template("upload.html")

    try:
        if "file" not in request.files:
            return render_template("upload.html", error="ファイルが見つかりません")

        file = request.files["file"]
        if not file or file.filename == "":
            return render_template("upload.html", error="ファイルが未選択です")

        if not allowed_file(file.filename):
            return render_template("upload.html", error="wav ファイルのみアップロードできます")

        word_id = request.form.get("fileword", "").strip()
        if not word_id:
            return render_template("upload.html", error="単語が選択されていません")

        original_filename = file.filename
        session["original_filename"] = original_filename

        word_select(word_id)

        save_path = TEST_WAV_PATH
        file.save(str(save_path))

        convert_to_16kHz(save_path, save_path)

        sleep_second()
        # noise_reduce(save_path)
        perl_run()

        return render_template(
            "upload.html",
            message="アップロード完了",
            original_filename=original_filename,
        )
    except Exception as exc:
        return render_template("upload.html", error=f"処理中にエラー: {exc}")


@app.route("/audio", methods=["GET", "POST"])
def record_audio():
    if request.method == "GET":
        return render_template("audio.html")

    try:
        if "file" not in request.files:
            return "ファイルがありません", 400

        file = request.files["file"]
        if not file or file.filename == "":
            return "ファイルが未選択です", 400

        file.save(str(TEST_WAV_PATH))
        convert_to_16kHz(TEST_WAV_PATH, TEST_WAV_PATH)

        sleep_second()
        # noise_reduce(TEST_WAV_PATH)
        perl_run()

        return "OK!"
    except Exception as exc:
        return f"エラー: {exc}", 500


@app.route("/graph", methods=["GET", "POST"])
def audio_analysis():
    if request.method != "POST":
        return "送信できませんでした", 400

    try:
        if not WORD_ID_MEMO_PATH.exists():
            return "単語IDファイルが見つかりません", 400

        with WORD_ID_MEMO_PATH.open(mode="r", encoding="utf-8") as f:
            word_id = f.readline().strip()

        if not word_id:
            return "単語IDが未設定です", 400

        num_match = re.search(r"\d+", word_id)
        if not num_match:
            return "単語IDが不正です", 400

        num = int(num_match.group())
        audio_sample = read_sample(word_id)
        audio_learn = str(TEST_WAV_PATH)
        audio_learn_edit = str(TEST_SEGMENT_WAV_PATH)

        lab_sample = str(Path(audio_sample).with_suffix(".lab"))
        lab_learn = str(TEST_LAB_PATH)

        log_sample = str(Path(audio_sample).with_suffix(".log"))
        log_learn = str(TEST_LOG_PATH)

        img_path = DISTANCE_RESULT_DIR / f"word{num}.png"

        pitch1, time1 = praat_pitch(audio_sample)
        pitch2, time2 = praat_pitch(audio_learn)

        lab_list1, mora_list1, phoneme1, mora1, phoneme_start1, mora_start1, phoneme_length1, mora_length1 = lab_load(lab_sample)
        lab_list2, mora_list2, phoneme2, mora2, phoneme_start2, mora_start2, phoneme_length2, mora_length2 = lab_load(lab_learn)

        pitch_com1 = comp(pitch1)
        pitch_com2 = comp(pitch2)
        pitch_native = smooth(pitch_com1)
        pitch_learn = smooth(pitch_com2)

        xline_phoneme1 = [float(i[0]) for i in lab_list1]
        xline_phoneme2 = [float(i[0]) for i in lab_list2]
        xline_mora1 = [float(i[0]) for i in mora_list1]
        xline_mora2 = [float(i[0]) for i in mora_list2]

        phoneme_frame1, phoneme_frame2, mora_frame1 = log_load(log_sample)
        if not phoneme_frame1 or not phoneme_frame2 or not mora_frame1:
            raise ValueError("サンプル側の forced alignment 結果が不正です")

        pitch1_sil = pitch1[int(phoneme_frame1[0][0]): int(phoneme_frame1[-1][1]) + 1]

        phoneme_frame3, phoneme_frame4, mora_frame2 = log_load(log_learn)
        if not phoneme_frame3 or not phoneme_frame4 or not mora_frame2:
            raise ValueError("入力側の forced alignment 結果が不正です")

        pitch2_sil = pitch2[int(phoneme_frame3[0][0]): int(phoneme_frame3[-1][1]) + 1]
        pitch3 = length_arrange(pitch2_sil, phoneme_frame2, phoneme_frame4)

        xline_phoneme = [int(i[0]) for i in phoneme_frame2]
        xline_mora = [int(i[0]) - int(mora_frame1[0][0]) for i in mora_frame1]

        pitch4 = comp(pitch1_sil)
        pitch5 = comp(pitch3)

        pitch_fin = smooth(pitch4)
        pitch_fin2 = smooth(pitch5)

        pitch_fin = scale(pitch_fin)
        pitch_fin2 = scale(pitch_fin2)

        x_axis = list(range(len(pitch_fin)))

        start1 = float(lab_list1[0][0])
        end1 = float(lab_list1[-1][1])
        start2 = float(lab_list2[0][0])
        end2 = float(lab_list2[-1][1])

        phoneme_pct_length1 = pct_length(phoneme_length1)
        mora_pct_length1 = pct_length(mora_length1)
        phoneme_pct_length2 = pct_length(phoneme_length2)
        mora_pct_length2 = pct_length(mora_length2)

        segment_audio(audio_learn, start2, end2)
        dtw_list, word_list, colors, red_index = dtw_ascending_order(audio_learn_edit, num)
        # create_graph(dtw_list, word_list, colors, red_index, img_path)

        original_filename = session.get("original_filename", "（ファイル名なし）")

        return render_template(
            "line_graph.html",
            original_filename=original_filename,
            Native_pitch=pitch_native.tolist(),
            Native_time=time1,
            User_pitch=pitch_learn.tolist(),
            User_time=time2,
            Native_phoneme_values=xline_phoneme1,
            Native_mora_values=xline_mora1,
            User_mora_values=xline_mora2,
            User_phoneme_values=xline_phoneme2,
            phoneme_labels=phoneme1,
            mora_labels=mora1,
            start1=start1,
            end1=end1,
            start2=start2,
            end2=end2,
            mora_values=xline_mora,
            x_axis=x_axis,
            pitch_fin=pitch_fin.tolist(),
            pitch_fin2=pitch_fin2.tolist(),
            Native_phoneme_length=phoneme_pct_length1,
            User_phoneme_length=phoneme_pct_length2,
            Native_mora_length=mora_pct_length1,
            User_mora_length=mora_pct_length2,
            words=word_list,
            sort_distance=dtw_list,
            bar_color=colors,
        )
    except Exception as exc:
        return f"解析中にエラーが発生しました: {exc}", 500


if __name__ == "__main__":
    ensure_directories()
    app.run(host="127.0.0.1", port=5000, debug=True)