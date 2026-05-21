# SP-PS — 日本語単語 音声解析・可視化プラットフォーム

> 「声（口）のかたちを綺麗にする一歩を踏める嬉しさを届ける」

録音した日本語単語の発音をネイティブ音声と比較し、
アクセント・モーラ長・母音品質の3軸でスコアを算出してフィードバックを提供する Flask アプリケーション。

---

## 目次

1. [動作環境](#動作環境)
2. [インストール](#インストール)
3. [設定](#設定)
4. [起動方法](#起動方法)
5. [機能一覧](#機能一覧)
6. [ディレクトリ構成](#ディレクトリ構成)
7. [スコアロジック](#スコアロジック)
8. [システム内部の処理フロー](#システム内部の処理フロー)
9. [主要な変更点（実装ログ）](#主要な変更点実装ログ)
10. [既知の限界](#既知の限界)

---

## 動作環境

| 項目 | 要件 |
|------|------|
| OS | Windows 10/11・Ubuntu 22.04 LTS 以降（Julius の動作確認済み） |
| Python | 3.10 以上 |
| Julius | 4.3.1 以上（Windows は `engine/bin/` に exe を配置） |
| Perl | Strawberry Perl（Windows）/ システム標準（Linux） |
| VOICEVOX | 0.14 以上（サンプル音声の自動生成に必要） |
| MeCab | UniDic 辞書と組み合わせて使用 |
| Praat | parselmouth 経由で自動インストール |

---

## インストール

### 1. リポジトリのクローン

```bash
git clone https://github.com/MaedaYuki-2004/SP-PS-2026-.git
cd SP-PS-2026-
```

### 2. Julius の配置

Julius は音素アライメント（音声と音素の対応付け）に使用します。

**Windows の場合**
`engine/bin/` フォルダに Julius の実行ファイルを配置します。

```
engine/bin/julius-4.3.1.exe   ← ここに配置
engine/models/hmmdefs_monof_mix16_gid.binhmm
```

Julius は以下の優先順位で自動検出されます（`config.py` の `_detect_julius()`）。

1. 環境変数 `JULIUS_BIN`（手動設定）
2. `engine/bin/julius*.exe`（Windows 自動検出）
3. `/opt/homebrew/bin/julius`（Apple Silicon Mac）
4. `/usr/local/bin/julius`（Intel Mac / Linux）
5. `PATH` 検索

**Ubuntu / Debian の場合**

```bash
sudo apt-get update
sudo apt-get install -y julius julius-dev
```

### 3. Perl のインストール

**Windows**（Strawberry Perl 推奨）
https://strawberryperl.com/ からインストール。

**Ubuntu / Debian**

```bash
sudo apt-get install -y perl
```

### 4. MeCab + UniDic のインストール

アクセント型の自動取得（`core/accent.py`）に使用します。

```bash
# MeCab 本体
pip install mecab-python3

# UniDic 辞書
pip install unidic
python -m unidic download
```

### 5. Python 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

`requirements.txt` に含まれないが必要なパッケージ：

```bash
pip install mecab-python3 unidic soundfile
```

| パッケージ | 用途 |
|-----------|------|
| flask | Web サーバー |
| praat-parselmouth | Praat 連携（ピッチ・フォルマント抽出） |
| librosa | 音声読み込み・MFCC 抽出 |
| fastdtw | DTW 距離計算（音色評価） |
| scipy | 統計計算（Pearson 相関など） |
| numpy | 数値計算全般 |
| noisereduce | ノイズ除去 |
| pydub | 音声フォーマット変換 |
| mecab-python3 | アクセント型の自動取得 |
| unidic | MeCab 用 UniDic 辞書 |

### 6. ffmpeg のインストール（pydub の依存）

```bash
# Ubuntu / Debian
sudo apt-get install -y ffmpeg

# Windows: https://ffmpeg.org/download.html
```

### 7. MFCC バイナリの生成

Delta MFCC（Δ・ΔΔ）を含む 36 次元で生成します。
新規セットアップ時・単語追加後は必ず実行してください。

```bash
python scripts/regenerate_mfcc.py
```

### 8. 動作確認

インストール後は診断スクリプトで環境を確認できます。

```bash
python diagnose.py
```

`[OK]` が全項目で出れば起動準備完了です。`[NG]` が出た項目が起動失敗の原因になります。

---

## 設定

`config.py` を環境に合わせて確認・編集してください。
パス定数はすべて `BASE_DIR`（`app.py` と同じ場所）からの相対パスで解決されます。

```python
# config.py の主要な設定項目

# Julius バイナリ（自動検出。手動指定する場合は環境変数で上書き）
# export JULIUS_BIN="/path/to/julius"

# Praat ピッチ検出のデフォルト範囲（自動推定が失敗した場合のフォールバック）
PITCH_FLOOR_DEFAULT   = 70.0    # Hz
PITCH_CEILING_DEFAULT = 400.0   # Hz

# Flask シークレットキー（本番環境では必ず変更）
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")
```

### 音響モデルのパス

Julius の音響モデルは `engine/models/` に配置します。
パスは `scripts/segment_julius.pl` の冒頭で指定されています。

```perl
$hmmdefs = "./models/hmmdefs_monof_mix16_gid.binhmm";
```

### 単語データベース

`data/config/words_db.json` に評価対象の単語が登録されています。

```json
{
  "word1": {
    "display": "おんど",
    "reading": "おんど",
    "accent": 1,
    "accent_source": "manual",
    "source": "recorded"
  }
}
```

| フィールド | 説明 |
|-----------|------|
| `display` | 画面表示用テキスト |
| `reading` | Julius に渡すひらがな読み（長音は「ー」） |
| `accent` | アクセント型（0=平板型 / 1=頭高型 / N=N型） |
| `accent_source` | `"mecab"` / `"manual"` |
| `source` | `"recorded"` / `"tts"` |

### ネイティブ音声の配置

各単語のネイティブ音声（16kHz・モノラル・16bit PCM WAV）を配置します。

```
data/raw_audio/sound/{word_id}/{word_id}.wav
data/raw_audio/sound/{word_id}/{word_id}.lab   ← Julius アライメント結果
data/raw_audio/sound/{word_id}/{word_id}.log   ← Julius ログ
data/raw_audio/sound/{word_id}/{word_id}.txt   ← ひらがな読み
```

### 新しい単語の追加方法

管理画面から追加する方法（VOICEVOX が起動している必要があります）：

1. `http://127.0.0.1:5000/admin` にアクセスする
2. 「単語を追加」から表示テキスト・読み・アクセント型を入力する
3. VOICEVOX でサンプル音声を自動生成し、Julius でアライメントを自動実行する
4. MFCC を再生成する

```bash
python scripts/regenerate_mfcc.py
```

全単語のサンプル音声とアライメントを一括再生成する場合：

```bash
python scripts/regenerate_all_samples.py
python scripts/regenerate_mfcc.py
```

特定単語のアライメントが失敗した場合の修復：

```bash
python scripts/repair_alignment.py word35
python scripts/repair_alignment.py word35 word36 word37   # 複数指定可
```

---

## 起動方法

```bash
python app.py
```

デフォルトで `http://127.0.0.1:5000` にアクセスできます。

### 使い方の流れ

1. トップページで評価する単語を選択する（アクセント図解・クエスト確認）
2. 録音ページで発音する（`Space` で開始/停止・`Enter` で解析）
3. 解析結果ページでスコア・フィードバックを確認する
4. 「交互に再生」でネイティブ音声と自分の録音を聞き比べる
5. クエストをこなしながら苦手パターンを練習する
6. `/history` で過去のスコア推移・間隔反復の候補を確認する

---

## 機能一覧

### 発音スコア（3軸100点満点）

| 軸 | 配点 | 評価内容 |
|----|------|---------|
| アクセント | 50点 | 核位置・ピッチ相関・H/L 一致率・安定度 |
| 長さ | 30点 | 各モーラの時間割合比較（長音・促音を2倍重視） |
| 母音品質 | 20点 | F1/F2 フォルマントの Bark スケール距離（30/50/70%の3点平均） |

グレード：**S** (≥90) / **A** (≥75) / **B** (≥60) / **C** (≥40) / **D** (<40)

### UI / UX

| 機能 | 説明 |
|------|------|
| **キーボードショートカット** | `Space` で録音開始/停止・`Enter` で解析開始 |
| **音声比較再生** | ネイティブ→自分の録音を3回交互再生（`/recorded_audio` 経由） |
| **音量バー** | 録音中のリアルタイム入力レベル表示（RMS） |
| **無音自動停止** | 1.5秒無音で録音を自動停止（0.5秒発話後に有効化） |
| **ローディングオーバーレイ** | Julius 解析中の画面ブロック |
| **レーダーチャート** | アクセント・長さ・母音の3軸レーダー |
| **前回比較バッジ** | 前回スコアとの差分を4項目で表示（+/-バッジ） |
| **アクセント図解** | 単語カードに H/L パターンをバッジで可視化 |
| **デザインシステム** | CSS 変数ベース（`base.css`）・Noto Sans JP 統一 |

### 練習サポート

| 機能 | 説明 |
|------|------|
| **クエスト自動生成** | 弱点軸（アクセント・長さ・母音）から最大3つを自動発行 |
| **クエスト自動クリア** | 次回録音でスコアが目標を超えたら自動クリア＋補充 |
| **間隔反復クエスト** | 3日以上練習していない単語（スコア85点未満）を `review` クエストとして最優先提案 |
| **練習履歴** | `/history` で単語別スコア推移グラフ（Chart.js） |
| **CSV エクスポート** | `/history/export.csv` で全履歴ダウンロード（BOM 付き UTF-8） |
| **次の練習候補** | 同アクセント型・練習回数少ない順で3単語を提案 |

### 管理機能

| 機能 | 説明 |
|------|------|
| **管理ページ** | `/admin` で単語の追加・編集・削除 |
| **統計カード** | 総練習回数・ユニーク単語数・平均スコア・最高グレード |
| **単語検索** | 管理テーブルでリアルタイム絞り込み |
| **VOICEVOX 連携** | 単語登録時にサンプル音声を自動生成 |
| **MeCab 連携** | 単語登録時にアクセント型を自動取得 |

---

## ディレクトリ構成

```
sp-ps/
├── app.py                              # Flask ルーティング（/recorded_audio 含む）
├── config.py                           # パス定数・Julius 自動検出・音素定数
├── diagnose.py                         # 環境診断スクリプト
├── requirements.txt
├── test_mecab.py                       # MeCab 動作確認用
├── test_voicevox.py                    # VOICEVOX 動作確認用
├── core/
│   ├── __init__.py
│   ├── accent.py                       # MeCab アクセント取得・VOICEVOX 音声生成
│   ├── alignment.py                    # Julius 実行・lab/log 読み込み・品質スコア抽出
│   ├── audio.py                        # 音声変換・ノイズ除去・セグメント切り出し
│   ├── evaluate.py                     # スコア算出（アクセント・長さ・総合）
│   ├── formant.py                      # F1/F2 フォルマント抽出（30/50/70%平均）・母音品質評価
│   ├── history.py                      # 練習履歴の保存・読み込み・間隔反復候補取得
│   ├── pitch.py                        # F0 抽出・補間・正規化・hz_to_semitone 等
│   ├── quest.py                        # クエスト生成・自動クリア・間隔反復クエスト
│   ├── timbre.py                       # MFCC+Delta（36次元）・DTW 音色評価
│   ├── utils.py                        # 汎用ユーティリティ
│   └── vocab.py                        # words_db.json 管理・単語登録フロー
├── data/
│   ├── config/
│   │   ├── audio.scp                   # 基準音声パス一覧（Julius 用）
│   │   ├── history.json                # 練習履歴（最大500件）
│   │   ├── quest_progress.json         # クエスト進捗
│   │   ├── word_id.txt                 # 直前に選択した単語 ID
│   │   ├── words.txt                   # 表示テキスト一覧（DTW 用）
│   │   └── words_db.json              # 単語データベース
│   ├── mfcc/
│   │   └── {word_id}.bin              # MFCC バイナリ（36次元 float32）
│   └── raw_audio/
│       ├── test2.wav                   # セグメント切り出し用一時ファイル
│       ├── wav/
│       │   ├── test.wav               # 録音音声（上書き保存）
│       │   ├── test.lab               # Julius アライメント結果
│       │   ├── test.log               # Julius ログ
│       │   └── test.txt               # 録音単語のひらがな読み
│       └── sound/
│           └── {word_id}/
│               ├── {word_id}.wav      # ネイティブ音声（16kHz/モノラル/16bit）
│               ├── {word_id}.lab      # Julius アライメント結果
│               ├── {word_id}.log      # Julius ログ
│               └── {word_id}.txt      # ひらがな読み
├── docs/
│   └── explanation.txt
├── engine/
│   ├── License.md
│   ├── README.md
│   ├── bin/
│   │   └── julius-4.3.1.exe           # Windows 用 Julius バイナリ
│   └── models/
│       └── hmmdefs_monof_mix16_gid.binhmm
├── scripts/
│   ├── segment_julius.pl              # Julius 音素アライメント Perl スクリプト
│   ├── regenerate_mfcc.py             # MFCC バイナリ一括再生成
│   ├── regenerate_all_samples.py      # サンプル音声・アライメント一括再生成
│   ├── repair_alignment.py            # 特定単語のアライメント修復
│   ├── mkdir_test.py                  # フォルダ一括作成（初期セットアップ用）
│   ├── move_folder.py                 # 音声ファイル移動（初期セットアップ用）
│   └── path_write.py                  # audio.scp 生成（初期セットアップ用）
└── web/
    ├── static/
    │   ├── css/
    │   │   ├── base.css               # デザインシステム（CSS 変数・Noto Sans JP）
    │   │   ├── audio.css              # 録音ページ用スタイル
    │   │   ├── select.css             # 単語選択ページ用スタイル
    │   │   └── upload.css             # アップロードページ用スタイル
    │   ├── js/
    │   │   ├── audio_recorder.js      # AudioWorklet（録音・WAV エンコード）
    │   │   └── main.js                # 音量バー・無音自動停止
    │   ├── distance_result/           # DTW 結果キャッシュ
    │   └── sample/                    # 静的サンプル音声（オプション）
    └── templates/
        ├── admin.html                 # 管理ページ
        ├── audio.html                 # 録音ページ（キーボードショートカット）
        ├── error.html                 # 404/500 エラーページ
        ├── history.html               # 練習履歴・スコア推移グラフ
        ├── line_graph.html            # 解析結果（比較再生・レーダー・クエスト）
        ├── select.html                # 単語選択（アクセント図解・クエストサイドバー）
        └── upload.html                # 音声アップロードページ
```

---

## スコアロジック

### 全体フロー

```
録音音声（test.wav）
  ↓
Julius アライメント（perl_run() → test.lab / test.log 生成）
  ↓
品質チェック：extract_julius_score() で対数尤度を取得
  Julius スコア < -3000 → スコア計算スキップ・再録音ガイドを表示
  ↓（品質 OK）
  ├─ ピッチ抽出（Hz → 半音変換） → アクセントスコア（50点）
  ├─ モーラ長の割合を比較        → 長さスコア（30点）
  └─ F1/F2 フォルマント抽出      → 母音品質スコア（20点）
  ↓
合計（100点満点）→ グレード判定（S / A / B / C / D）
  ↓
save_record() で history.json に保存
check_and_update_quests() でクエスト更新（間隔反復含む）
```

### グレード基準

| グレード | 点数 |
|---------|------|
| S | 90点以上 |
| A | 75点以上 |
| B | 60点以上 |
| C | 40点以上 |
| D | 39点以下 |

---

### アクセントスコア（0〜50点）

4指標の加重平均で内部スコア（最大60）を算出し、50点に正規化します。

```
内部スコア（最大60） =
  核位置スコア    × 0.40   ← 日本語アクセントで最重要
+ ピッチ相関スコア × 0.35   ← Pearson 相関（有声フレームのみ）
+ H/L 一致率スコア × 0.15   ← 各モーラの高低の正確さ
+ 安定度スコア    × 0.10   ← モーラ内ピッチの安定さ

アクセントスコア = 内部スコア × 50 / 60
```

各サブスコアの最大値はすべて60です。

**ピッチ相関（Pearson 相関係数）**

ネイティブと録音の「両方が有声（NaN でない）」フレームのみを使って計算します。
無声区間の補間値がノイズとして混入しないようにするためです。

```
r = Σ(xi − x̄)(yi − ȳ) / (n × σx × σy)

r = +1.0：ネイティブと同じタイミングで上下（完全一致）
r =  0.0：無相関
r = -1.0：完全に逆パターン

score = max(0, (r + 1) / 2 × 60)
```

**H/L 分類（パーセンタイル閾値）**

期待パターンの L 比率に合わせたパーセンタイルで閾値を設定します。

```python
threshold = percentile(mora_pitches, L比率 × 100)
```

例：平板型（L-H-H-H）→ L 比率 25% → 25 パーセンタイルを閾値

**アクセント核の検出（動的閾値）**

```python
DROP_THRESHOLD = max(0.05, min(0.25, ピッチ範囲 × 0.15))
```

---

### 長さスコア（0〜30点）

モーラ長の割合（全体に占める%）をネイティブと比較し、重み付き誤差でスコア化します。

```
モーラ重み：長音（:）・促音（q）→ 2.0倍 ／ 撥音（N）→ 1.5倍 ／ 通常 → 1.0倍

weighted_diff = Σ(|native[i] - user[i]| × weight[i]) / Σ(weight[i])
内部スコア = max(0, (1.0 - weighted_diff / 20.0) × 40)
長さスコア = 内部スコア × 30 / 40
```

---

### 母音品質スコア（0〜20点）

F1・F2 フォルマントを Bark スケールで比較してスコア化します。

```
# Hz → Bark 変換（知覚的均等スケール）
Bark = 26.81 × F / (1960 + F) − 0.53

# 正規化距離
dist = √( (ΔBark_F1 / 3.0)² + (ΔBark_F2 / 4.0)² )

# 指数減衰スコア（0点にならない設計）
score = 20 × exp(−1.2 × dist)
```

Bark スケールを使う理由：Hz は F1（低周波）と F2（高周波）で知覚的な重みが異なるため、
Hz のままでは「聞こえ方の差」を正しく評価できません。

**フォルマント抽出の3点サンプリング**

各モーラのフォルマントはモーラ区間の **30% / 50% / 70%** の3時刻で測定し、
有効値（NaN でない値）の平均を最終値とします。

```python
_SAMPLE_RATIOS = [0.30, 0.50, 0.70]   # core/formant.py で変更可能

for ratio in _SAMPLE_RATIOS:
    t = start + duration * ratio
    f1, f2 = get_formant_at_time(formant, t)
    # 有効値のみ収集

f1_mean = mean(f1_vals)   # 最終フォルマント値
f2_mean = mean(f2_vals)
```

中心時刻1点だけを使う場合、子音の解放直後（/k/→/a/ の遷移区間など）を
誤って拾うことがあります。複数点を平均することで遷移区間の影響を緩和し、
母音の安定核を正確に評価できます。

**話者性別と max_formant の自動判定**

`app.py` では `estimate_pitch_range()` で推定したピッチ上限を使って
`max_formant` を自動設定します。

```python
max_formant = 5500.0 if ceiling > 400 else 5000.0
# ceiling > 400Hz → 女性話者と判定 → 5500Hz
# ceiling ≤ 400Hz → 男性話者と判定 → 5000Hz
```

---

### Julius 品質ゲート

```
Julius スコア（対数尤度平均）< -3000
  → スコア計算を完全にスキップ
  → 再録音ガイドを表示（マイクに近づく・はっきり発音・静かな環境）
```

ピッチグラフは引き続き表示します（アライメントと独立して計算されるため）。

---

### 追加フィードバック（スコア非影響）

スコアには含まれず、フィードバックとして表示する補助指標です。

| 指標 | 内容 | しきい値 |
|------|------|---------|
| 発話速度 | モーラ/秒でネイティブと比較 | ネイティブ比 0.70〜1.15 が適切 |
| ジッター | F0 の変動率（声のピッチの安定さ） | > 3% で警告 |
| シマー | 振幅の変動率（声の音量の安定さ） | > 8% で警告 |
| 有声フレーム比率 | モーラ内で声が出ているフレームの割合 | < 45% で警告 |

---

## システム内部の処理フロー

スコアロジックが「何を計算するか」を説明するのに対し、このセクションは「どのように処理されるか」を説明します。

---

### 1. 録音から結果画面までの全体フロー

```
【ブラウザ側】
  navigator.mediaDevices.getUserMedia()   マイク入力（16kHz）
    ↓
  AudioWorkletNode（audio_recorder.js）   Float32 サンプルをキャプチャ
    ↓
  encodeAudio()                           WAV ヘッダを付けて Blob に変換
    ↓
  POST /audio                             test.wav として保存 → Julius アライメント

【解析ボタン押下（POST /graph）】
  ↓
  estimate_pitch_range()    ネイティブ・録音それぞれの話者ピッチ範囲を自動推定
  ↓
  praat_pitch()             Praat で F0 を抽出（NaN 保持の Hz 配列）
  ↓
  resample_to_10ms()        Julius フレーム（10ms 単位）にリサンプリング
  ↓
  lab_load()                .lab を読み込み → 音素リスト・モーラリスト・長さ情報
  log_load()                .log を読み込み → フレーム単位の音素・モーラ情報
  ↓
  extract_julius_score()    Julius 品質チェック → < -3000 なら以降の計算をスキップ
  ↓（品質 OK）
  hz_to_semitone()          Hz → 半音変換（NaN 保持）※ここが pitch_native_raw / pitch_user_raw
  ↓
  length_arrange()          録音ピッチをネイティブのモーラ長に時間正規化
  ↓
  extract_mora_formants()   F1/F2 を 30/50/70%の3点平均で抽出
  ↓
  calc_total_score()        アクセント・長さ・母音スコアを合算
  ↓
  save_record()             history.json に保存
  check_and_update_quests() クエスト更新（間隔反復含む）
  ↓
  render_template("line_graph.html")
```

---

### 2. Julius 強制アライメントとは

Julius の「強制アライメント（Forced Alignment）」は、
**既知のテキスト（ひらがな読み）が音声のどのタイミングで発音されているか**を特定する処理です。
通常の音声認識と異なり「何を言ったか」ではなく「どのタイミングで言ったか」を求めます。

**入力**
- `test.wav`：録音音声（16kHz / 16bit PCM）
- `test.txt`：ひらがな読み（例：`びょーいん`）

**Perl スクリプトが行うこと**

`segment_julius.pl` がひらがなを Julius 用の音素列に変換します。

```
びょーいん → by o: i N
```

その後 Julius が Viterbi アルゴリズムで最尤アライメントを計算し、`.lab` と `.log` を出力します。

**出力（.lab ファイルの構造）**

```
0.0000000 0.0750000 silB    ← 無音（文頭）
0.0750000 0.1500000 by      ← 子音 /by/
0.1500000 0.2500000 o:      ← 長母音 /o:/（ー）
0.2500000 0.3000000 i       ← 母音 /i/
0.3000000 0.3750000 N       ← 撥音 /N/（ん）
0.3750000 0.4500000 silE    ← 無音（文末）
```

各行は `開始時刻（秒）　終了時刻（秒）　音素ラベル` の形式です。
アライメント精度は 10ms 単位に丸められます。

**Julius スコアの目安**

`.log` ファイルに含まれる対数尤度の平均値が Julius スコアです（`extract_julius_score()` で取得）。
値が 0 に近いほど「音響モデルとの一致度が高い = 発音がはっきりしている」ことを意味します。

```
-1000 以上     : アライメント良好
-1000 〜 -3000 : やや不安定（音質や発音の問題の可能性）
-3000 以下     : 不安定 → スコア計算をスキップ
```

---

### 3. モーラと音素の違い

日本語の音韻単位には **モーラ（拍）** と **音素** の2種類があります。

| 概念 | 定義 | 例（「びょういん」） |
|------|------|------|
| 音素 | 音声学的な最小単位 | `by` / `o:` / `i` / `N` |
| モーラ | 日本語リズムの最小単位（1拍） | `びょ` / `う` / `い` / `ん` |

SP-PS では Julius の音素アライメント結果を `mora_time()` でモーラ単位に変換してから評価します。

**mora_time() の変換ルール（core/alignment.py）**

```
① 子音 + 次が母音 → 結合して1モーラ       by + o: → "byo:"
② 単独の母音      → そのまま1モーラ         i      → "i"
③ N（撥音・ん）   → 単独1モーラ             N      → "N"
④ q（促音・っ）   → 単独1モーラ             q      → "q"
⑤ 結合できない子音 → 単独1モーラ（異常時のフォールバック）
```

アクセントスコアとモーラ長スコアはこのモーラ単位で計算されます。
グラフのモーラ境界線もこの変換結果を使います。

---

### 4. ピッチ処理パイプライン

ピッチ処理は**表示用**と**スコア計算用**で異なる経路をたどります。
2経路を意図的に分けているのは、グラフの見やすさとスコアの正確さの要求が異なるためです。

```
praat_pitch()          Hz 配列（NaN 保持：無声区間は NaN）
  ↓
resample_to_10ms()     Julius の 10ms フレームに揃える
  ↓
発話区間だけを切り出す（Julius の silB〜silE の外を除去）
  ↓
hz_to_semitone()       Hz → 半音変換（NaN 保持）
                       ← ここが pitch_native_raw / pitch_user_raw（有声フレームのみで Pearson 計算に使用）

  ┌──────────────────────────┐     ┌──────────────────────────┐
  │     スコア計算用          │     │     表示用（グラフ）       │
  ├──────────────────────────┤     ├──────────────────────────┤
  │ length_arrange()          │     │ comp()    NaN を線形補間  │
  │ 録音をネイティブの音素長  │     │ smooth()  window=5 で平滑 │
  │ に時間正規化              │     │ scale()   0〜1 に正規化   │
  │ comp()  NaN を補間        │     └──────────────────────────┘
  │ smooth() window=3 ← 小さく
  │ して境界を鮮明に          │
  └──────────────────────────┘
```

**なぜ Hz → 半音（semitone）に変換するのか**

ピッチを Hz のまま比較すると、話者の声の絶対的な高さの違い（男性 vs 女性など）がそのまま「差」になります。
半音は比率（対数）スケールなので、絶対値ではなく「上がり下がりのパターン」だけを比較できます。

```
半音 = 12 × log2(F / F_ref)

F_ref = 有声フレームの中央値（話者ごとに個別に設定 → 話者差を吸収）
```

---

### 5. 時間正規化（length_arrange）

ネイティブと録音では発話速度が異なるため、ピッチを重ね合わせる前に時間軸を揃える必要があります。

`length_arrange()` はネイティブの各音素のフレーム数を基準として、録音ピッチを伸縮します。

```
例：「か」音素
  ネイティブ：8フレーム
  録音      ：5フレーム → 末尾に NaN を3つ追加して8フレームに伸張

例：「い」音素
  ネイティブ：6フレーム
  録音      ：9フレーム → 先頭または末尾を3フレーム削って6フレームに短縮
```

この処理により「同じモーラ位置のピッチ同士」を比較できます。
結果ページの「ピッチ比較」タブで2本のラインが重なって表示されるのは、この正規化の結果です。

---

### 6. 音色評価（MFCC + DTW）

**MFCC とは**

MFCC（メル周波数ケプストラム係数）は人間の聴覚特性（メルスケール）に基づいて
音の「スペクトル包絡（音色）」を数値化したものです。
ピッチが異なっても同じ母音なら近い値になるため、音色の類似度評価に適しています。

**Delta MFCC を追加する理由**

静的 MFCC だけでは音の「瞬間的な形（断面）」しか捉えられません。
Δ・ΔΔ を追加することで「音の動き」も比較できます。

```
静的 MFCC（12次元） : スペクトル包絡の形状
Δ  MFCC（12次元） : 単位時間あたりの変化量（変化の速さ）
ΔΔ MFCC（12次元） : 変化量の変化量（変化の加速度）
─────────────────
合計 36次元
```

**DTW（動的時間伸縮法）とは**

2つの時系列データの「最も近い対応付け」を動的計画法で求めるアルゴリズムです。
長さが異なる音声同士でも比較でき、発話速度の違いを吸収します。
距離が小さいほど音色が近いことを意味します。

**音色グラフの見方（「音色評価」タブ）**

録音音声と登録済み全単語のネイティブ音声との DTW 距離を昇順で表示します。

```
赤いバー  ：今回練習した単語（練習対象）
青いバー  ：その他の単語

バーが左（距離が小）→ 音色が近い
バーが右（距離が大）→ 音色が遠い

理想：赤いバーが最左端 = 自分の発音が最も練習対象に近い音色になっている
```

---

### 7. クエストシステムの内部ロジック

**クエストのライフサイクル**

```
【録音・解析のたびに実行される】
check_and_update_quests(score_result, word_id)
  ↓
  1. quest_progress.json からアクティブなクエストを読み込む
  2. 各クエストの target_metric と今回のスコアを比較
       current >= target_value → is_completed = True → newly_completed へ
       current <  target_value → still_active に残る
  3. 空きスロット = MAX_ACTIVE_QUESTS(3) - len(still_active)
  4. generate_new_quests() で空きスロット分を補充
       ① get_spaced_repetition_candidates() を確認
          3日以上未練習 かつ スコア85点未満 → review クエストを最大1つ追加
       ② 残りを弱点軸（スコアが低い順）で埋める
  5. quest_progress.json に保存
```

**難易度と目標値の決まり方**

現在のスコアに応じて難易度と目標増分が変わります。

| 軸 | 現在スコア | 難易度 | 目標増分 |
|----|-----------|--------|---------|
| アクセント | < 20点 | 上級 | +12点 |
| アクセント | < 35点 | 中級 | +8点 |
| アクセント | ≥ 35点 | 初級 | +5点 |
| 長さ | < 12点 | 上級 | +7点 |
| 長さ | < 22点 | 中級 | +5点 |
| 長さ | ≥ 22点 | 初級 | +3点 |
| 母音 | < 8点 | 上級 | +5点 |
| 母音 | < 14点 | 中級 | +4点 |
| 母音 | ≥ 14点 | 初級 | +2点 |

目標値は上限（アクセント50点・長さ30点・母音20点）を超えないように制限されます。

**progress_pct（進捗率）の計算**

```python
pct = (current_value - start_value) / (target_value - start_value) × 100
```

クエスト発行時点のスコアを `start_value`、目標を `target_value` とした相対進捗率です。
スコアが悪化した場合は 0% に固定されます（マイナスにはなりません）。

---

### 8. 単語登録フロー（vocab.py）

管理画面から単語を追加すると以下が自動実行されます。

```
register_word(display, reading)
  ↓
  1. get_accent(display)
     MeCab + UniDic でアクセント型を自動取得
     → UniDic フィーチャーの25番目フィールドを読む
     → 複数型がある場合は最初の値を採用
     → 1形態素として認識されない場合は accent=None

  2. generate_sample_wav(display, wav_path)
     VOICEVOX（話者 ID: 11 / ずんだもん）で音声合成
     → POST /audio_query → POST /synthesis → 16kHz WAV として保存

  3. convert_to_16kHz()
     16kHz / モノラル / 16bit PCM に変換（Julius が要求する形式）

  4. perl_run()
     Julius で音素アライメントを実行
     → .lab（音素境界）・.log（詳細ログ）を生成

  5. audio_mfcc()
     MFCC + Δ + ΔΔ（36次元）を計算 → {word_id}.bin として保存

  6. words_db.json・audio.scp・words.txt を更新
```

VOICEVOX が起動していない場合は手順2でエラーになります。
起動確認は `python test_voicevox.py` で行えます。

---

### 9. 録音の音声処理パイプライン

**ブラウザ側の流れ**

```
getUserMedia()            マイクから 16kHz で入力
  ↓
AudioWorkletNode          Float32 サンプルをフレーム単位でキャプチャ
  ↓
encodeAudio()             WAV ヘッダ（44バイト）を付加して Blob に変換
  ↓
POST /audio               Flask に送信

  音量バー：AnalyserNode で RMS を計算して #volumeFill の width に反映
  無音自動停止：RMS < 0.03 が 1500ms 継続かつ 500ms 以上発話後 → buttonStop.click()
```

**サーバー側の流れ**

```
POST /audio
  ↓ file.save(TEST_WAV_PATH)     data/raw_audio/wav/test.wav に保存
  ↓ convert_to_16kHz()           16kHz / モノラル / 16bit に変換
  ↓ perl_run()                   Julius でアライメント実行（test.lab・test.log 生成）
  ↓ 「OK!」を返す（3秒後に解析ボタンが表示される）

POST /graph（解析ボタン押下）
  ↓ audio_analysis()             全解析を実行
  ↓ render_template("line_graph.html")
```

**ノイズ除去（オプション・現状は無効）**

`core/audio.py` の `reduce_noise_wav()` が実装済みですが、現状は呼ばれていません。
有効化する場合は `app.py` の `record_audio()` に以下を追加します。

```python
from core.audio import reduce_noise_wav
reduce_noise_wav(TEST_WAV_PATH)   # 先頭 0.5 秒をノイズプロファイルとして除去
```

---

## 主要な変更点（実装ログ）

### スコア構成の変更

| | 変更前 | 変更後 |
|--|--------|--------|
| アクセント | 60点 | **50点** |
| 長さ | 40点 | **30点** |
| 母音品質 | なし | **20点（新設）** |
| 合計 | 100点 | 100点 |

---

### ピッチ評価の改善

| 変更項目 | 変更前 | 変更後 |
|---------|--------|--------|
| 単位変換 | Hz（絶対値） | 半音（話者差を吸収） |
| 比較手法 | DTW（全フレーム） | Pearson 相関（有声フレームのみ） |
| スムージング（スコア用） | window=5 | window=3（境界を鮮明に） |
| スムージング（表示用） | window=5 | window=5（変更なし） |
| H/L 閾値 | 中央値（固定50パーセンタイル） | L 比率ベースのパーセンタイル |
| 核検出閾値 | 固定値 0.08 | 動的（ピッチ範囲の15%） |
| 安定度閾値 | 0.05（バグ） | 1.5（半音スケール適切値） |
| 正規化 | normalize_zscore() | scale()（二重正規化を解消） |

DTW からピアソン相関に変えた理由：日本語アクセントの本質は「いつ上がって・いつ下がるか（タイミング）」であり、
Pearson 相関は上下タイミングの一致度を直接測れます。また Pearson 相関は線形変換不変のため `scale()` 不要です。

---

### 母音品質評価の新設（`core/formant.py`）

- 各モーラの **30%・50%・70%** の3時刻で F1/F2 を取得し、有効値の平均を使用
- Hz → Bark スケール変換（知覚的均等スケール）
- 指数減衰スコア（距離がいくら大きくても0点にならない）
- `max_formant` を話者のピッチ上限から自動判定

---

### Julius 品質ゲートの追加

- `extract_julius_score()` で対数尤度を取得（`core/alignment.py`）
- `-3000` 以下の場合はスコア計算をスキップし、再録音ガイドを表示
- ピッチグラフは引き続き表示（アライメントと独立）

---

### Delta MFCC の導入（`core/timbre.py`）

静的 MFCC(12次元) + Δ(12次元) + ΔΔ(12次元) = **36次元** に変更。

> ⚠️ 既存の `.bin` ファイル（12次元）は非互換です。以下のコマンドで再生成してください。
>
> ```bash
> python scripts/regenerate_mfcc.py
> ```

---

### 話者ピッチ範囲の自動推定（`core/pitch.py`）

```python
def estimate_pitch_range(sound_file, percentile_low=10.0, percentile_high=90.0,
                         margin_low=0.75, margin_high=1.50) -> tuple[float, float]:
    """
    1. 広い範囲（50〜700Hz）でピッチを大まかに検出
    2. 有声フレームの 10〜90 パーセンタイルを取得
    3. マージンを掛けて floor / ceiling を決定
    """
```

---

### フォルマント抽出精度の改善

| | 変更前 | 変更後 |
|--|--------|--------|
| 測定点 | モーラ中心（50%）の1点 | 30% / 50% / 70% の3点の平均 |
| NaN 処理 | 中心が NaN の場合スキップ | 有効点のみで平均（最低1点あれば算出） |

測定点の数は `core/formant.py` の `_SAMPLE_RATIOS` で変更可能です。

---

### 間隔反復クエスト（`core/history.py` + `core/quest.py`）

`get_spaced_repetition_candidates()` を追加：

```python
get_spaced_repetition_candidates(
    min_days  = 3.0,    # 最終練習から何日以上経過したか
    max_score = 85.0,   # スコアがこの点未満の単語のみ対象
    limit     = 5,      # 最大何件返すか
) -> list[dict]
```

`generate_new_quests()` は新クエスト生成時に間隔反復候補を確認し、
該当する単語があれば `category="review"` の復習クエストを最優先で1つ追加します。
残りのスロットは従来通り弱点軸から生成します。

クエストの `category` 値と対応する表示色：

| category | 意味 | 表示色 |
|----------|------|--------|
| `accent` | アクセント改善 | 赤（`--accent`） |
| `length` | 長さ改善 | 緑（`--green`） |
| `vowel` | 母音改善 | 青（`--blue`） |
| `total` | 総合スコア | 紺（`--navy`） |
| `review` | 間隔反復（復習） | 橙（`--quest`） |

---

### キーボードショートカット（`web/templates/audio.html`）

`input` / `textarea` / `audio` 要素にフォーカスしているときは無効。

| キー | 動作 |
|------|------|
| `Space` | 録音開始（startBtn が有効時）/ 停止（stopBtn が有効時） |
| `Enter` | 解析開始ボタンが表示されている場合にフォーム送信 |

---

### 音声比較再生（`app.py` + `web/templates/line_graph.html`）

`app.py` に `/recorded_audio` ルートを追加：

```
GET /recorded_audio
  → data/raw_audio/wav/test.wav を audio/wav で返す
  → ファイルが存在しない場合は 404
```

結果ページの「発音スコア」タブに「音声を聞き比べる」セクションを追加。
「交互に再生」ボタンで以下の順序を3回繰り返します：

```
ネイティブ音声 → 800ms 待機 → あなたの録音 → 800ms 待機 → （繰り返し）
```

---

## 主要 API ルート一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/` | 単語選択ページ |
| POST | `/` | 単語を選択して録音ページへ遷移 |
| GET | `/select` | 単語選択ページ（直接アクセス用） |
| GET | `/audio` | 録音ページ |
| POST | `/audio` | 録音データを保存（MediaRecorder API） |
| GET | **`/recorded_audio`** | **直前の録音（test.wav）を返す** |
| POST | `/graph` | Julius 解析を実行して結果ページを返す |
| GET | `/sample_audio/<word_id>` | ネイティブ音声を返す |
| GET | `/upload` | 音声アップロードページ |
| POST | `/upload` | 音声ファイルをアップロードして解析 |
| GET | `/history` | 練習履歴ページ |
| GET | `/history/export.csv` | 履歴を CSV でダウンロード |
| GET | `/admin` | 管理ページ |
| POST | `/admin/add_word` | 単語追加 API（JSON） |
| POST | `/admin/update_word` | 単語更新 API（JSON） |
| POST | `/admin/delete_word` | 単語削除 API（JSON） |

---

## 既知の限界

| 限界 | 内容 | 今後の対策 |
|------|------|-----------|
| 参照音声が1本 | 話者の個人差がそのまま「正解」になる | 複数話者（VOICEVOX の別キャラクター等）で録音して平均化 |
| 話者性別補正なし | 男女で声道長が違い F1/F2 が100〜200Hz ずれる | 推定ピッチ範囲から補正係数を自動適用 |
| パラメータが推測値 | 重み・閾値を正解データで検証できていない | 評価付き録音を20〜30例収集してチューニング |
| Julius の精度 | 学習者音声に対するアライメント精度が低い | Montreal Forced Aligner（MFA）への移行 |
| シングルユーザー | test.wav が上書きされると比較再生が前の録音になる | セッションごとにファイル名を分ける |
| `source="recorded"` 単語の削除不可 | `vocab.py` の `delete_word()` で弾かれる | 管理者権限フラグの実装 |

---

## ライセンス

MIT License

Julius 音響モデル・セグメンテーションキット：MIT License（`engine/License.md` 参照）