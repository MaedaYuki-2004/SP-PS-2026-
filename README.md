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
10. [主要 API ルート一覧](#主要-api-ルート一覧)
11. [既知の限界](#既知の限界)
12. [ブランチ運用ルール](#ブランチ運用ルール)

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
| MFA | Montreal Forced Aligner 3.3.x（**オプション**・conda 環境が必要） |

---

## インストール

### 1. リポジトリのクローン

```bash
git clone https://github.com/MaedaYuki-2004/SP-PS-2026-.git
cd SP-PS-2026-
```

### 2. Python 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

MeCab の辞書データを別途ダウンロードします（初回のみ）。

```bash
python -m unidic download
```

| パッケージ | 用途 |
|-----------|------|
| flask | Web サーバー |
| praat-parselmouth | ピッチ・フォルマント抽出 |
| librosa | 音声読み込み・MFCC 抽出 |
| fastdtw | DTW 距離計算（音色評価） |
| scipy | 統計計算（Pearson 相関など） |
| numpy | 数値計算全般 |
| pydub | 音声フォーマット変換 |
| soundfile | 音声ファイルの読み書き |
| noisereduce | ノイズ除去 |
| mecab-python3 | アクセント型の自動取得 |
| unidic | MeCab 用 UniDic 辞書 |

### 3. Julius の配置

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

### 4. Perl のインストール

**Windows**（Strawberry Perl 推奨）
https://strawberryperl.com/ からインストール。

**Ubuntu / Debian**

```bash
sudo apt-get install -y perl
```

### 5. ffmpeg のインストール

pydub の音声変換処理に必要です。

**Windows**

```powershell
# winget を使う場合（推奨）
winget install Gyan.FFmpeg

# または https://ffmpeg.org/download.html から手動でダウンロードして
# 解凍後、bin/ フォルダを PATH に追加する
```

**Ubuntu / Debian**

```bash
sudo apt-get install -y ffmpeg
```

### 6. VOICEVOX のインストール

サンプル音声の自動生成に使用します。

1. https://voicevox.hiroshiba.jp/ からインストーラーをダウンロードする
2. インストール後に起動する（常時起動している必要があります）
3. デフォルトポート `50021` で起動していることを確認する

> **Note:** VOICEVOX が起動していない状態でも既存の音声ファイルは使えます。
> 新しい単語を管理画面から追加する場合にのみ VOICEVOX が必要です。

### 7. MFCC バイナリの生成

Delta MFCC（Δ・ΔΔ）を含む 36 次元で生成します。
新規セットアップ時・単語追加後は必ず実行してください。

```bash
python scripts/regenerate_mfcc.py
```

### 8. MFA のセットアップ（オプション・現在は非推奨）

> ⚠️ **現在 `USE_MFA = False`（Julius 使用）を推奨します。**
> MFA を有効にするとスコアが大幅に低下することが確認されています。
> 原因は SP-PS のパイプライン全体が Julius の音素体系・フレーム番号を
> 前提として設計されているためです。詳細は「既知の限界」を参照してください。

Julius より高精度なアライメントが必要な場合に導入します。
導入しない場合は Julius がそのまま使われます（`USE_MFA = False`）。

```bash
# conda 環境を作成
conda create -n mfa -c conda-forge montreal-forced-aligner python=3.10
conda activate mfa

# モデルのダウンロード確認
python scripts/setup_mfa.py
```

完了後に `config.py` の `USE_MFA = True` に変更します（現在は非推奨）。

> **Note:** MFA を使う場合はアプリを **`conda activate mfa` した環境**で起動してください。

### 9. 動作確認

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

# MFA（Montreal Forced Aligner）
# True  → MFA を優先。失敗時は Julius にフォールバック
# False → Julius のみ（デフォルト）
USE_MFA: bool = False
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
| 母音品質 | 20点 | F1/F2 フォルマントの Bark スケール距離（30/50/70%の3点平均・**性別補正・サンプル数重み付け**あり） |

グレード：**S** (≥90) / **A** (≥75) / **B** (≥60) / **C** (≥40) / **D** (<40)

### UI / UX

| 機能 | 説明 |
|------|------|
| **キーボードショートカット** | `Space` で録音開始/停止・`Enter` で解析開始 |
| **音声比較再生** | ネイティブ→自分の録音を3回交互再生（`/recorded_audio` 経由） |
| **音量バー** | 録音中のリアルタイム入力レベル表示（RMS） |
| **無音自動停止** | 1.5秒無音で録音を自動停止（0.5秒発話後に有効化） |
| **録音やり直し確認** | 録音済みの状態で録音開始を押すと確認ダイアログを表示 |
| **ローディングオーバーレイ** | Julius 解析中の画面ブロック |
| **レーダーチャート** | アクセント・長さ・母音の3軸レーダー |
| **モーラ別スコア表示** | 音節ごとのアクセント・長さ・母音スコアをテーブルで表示。最低点の音節に ⚠️ マーク |
| **ピッチグラフのハイライト** | ピッチ比較グラフ上で最もズレている音節の区間を赤帯でハイライト |
| **前回比較バッジ** | 前回スコアとの差分を4項目で表示（+/-バッジ） |
| **アクセント図解** | 単語カードに H/L パターンをバッジで可視化 |
| **ダークモード** | 右上の 🌙 ボタンで切り替え。設定は localStorage に永続化 |
| **デザインシステム** | CSS 変数ベース（`base.css`）・`[data-theme="dark"]` で切替 |

### 練習サポート

| 機能 | 説明 |
|------|------|
| **クエスト自動生成** | 弱点軸（アクセント・長さ・母音）から最大3つを自動発行 |
| **クエスト自動クリア** | 次回録音でスコアが目標を超えたら自動クリア＋補充 |
| **間隔反復クエスト** | 3日以上練習していない単語（スコア85点未満）を `review` クエストとして最優先提案 |
| **練習履歴** | `/history` で単語別スコア推移グラフ（Chart.js） |
| **苦手アクセント型の分析** | 履歴ページにアクセント型別の平均スコアカードを表示（苦手順に並べる） |
| **学習曲線の自動分析** | `/analysis` で上達速度・停滞期・Sランク到達回数を統計的に可視化 |
| **スコアの信頼区間** | ブートストラップ法で 95% 信頼区間を推定。「68〜76点」のように幅で実力を表示 |
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
│   ├── analysis.py                     # 学習曲線の自動分析（回帰・プラトー・Sランク分布）
│   ├── audio.py                        # 音声変換・ノイズ除去・セグメント切り出し
│   ├── confidence.py                   # ブートストラップ法による 95% 信頼区間の推定
│   ├── evaluate.py                     # スコア算出（アクセント・長さ・総合・モーラ別）
│   ├── formant.py                      # F1/F2 フォルマント抽出（30/50/70%平均）・キャッシュ対応
│   ├── history.py                      # 練習履歴の保存・読み込み・間隔反復候補取得
│   ├── pitch.py                        # F0 抽出・補間・正規化・hz_to_semitone 等
│   ├── quest.py                        # クエスト生成・自動クリア・間隔反復クエスト
│   ├── timbre.py                       # MFCC+Delta（36次元）・DTW 音色評価
│   ├── utils.py                        # 汎用ユーティリティ
│   └── vocab.py                        # words_db.json 管理・単語登録フロー
├── data/
│   ├── config/
│   │   ├── audio.scp                   # 基準音声パス一覧（Julius 用）
│   │   ├── formant_cache.json          # ネイティブ音声フォルマントのキャッシュ
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
│   ├── regenerate_all_tts.py          # 全単語 VOICEVOX 一括再生成（話者変更時）
│   ├── check_voicevox_speakers.py     # 使用可能な VOICEVOX 話者一覧を表示
│   ├── setup_mfa.py                   # MFA インストール確認・モデルダウンロード
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
        ├── analysis.html              # 学習曲線の自動分析ページ（NEW）
        ├── audio.html                 # 録音ページ（キーボードショートカット）
        ├── error.html                 # 404/500 エラーページ
        ├── history.html               # 練習履歴・スコア推移・アクセント型別分析
        ├── line_graph.html            # 解析結果（モーラ別スコア・信頼区間・ハイライト）
        ├── select.html                # 単語選択（アクセント図解・クエストサイドバー）
        └── upload.html                # 音声アップロードページ
```

---

## スコアロジック

### 全体フロー

```
録音音声（test.wav）
  ↓
run_alignment()（USE_MFA=True → MFA、False → Julius にフォールバック）
  ↓ test.lab / test.log を生成（MFA の場合は TextGrid を Julius 互換形式に変換）
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

**話者性別によるフォルマント補正**

`estimate_pitch_range()` が返す `pitch_ceiling_user` を `calc_vowel_score()` に渡し、
200Hz 以下の場合は男性と判定してネイティブ F1/F2 に補正係数（`_MALE_FORMANT_SCALE = 0.85`）を適用します。

```
pitch_ceiling_user ≤ 200Hz → 男性と判定 → native_F1 × 0.85 / native_F2 × 0.85 で比較
pitch_ceiling_user > 200Hz → 女性と判定 → 補正なし（VOICEVOX の声域に合わせた比較）
```

補正係数 0.85 の根拠：Peterson & Barney (1952) ほか音響音声学の研究によると、
男性のフォルマントは女性の約 82〜87%。保守的な値として 0.85 を採用。

**有効サンプル数による信頼度重み付け**

30/50/70% の 3 点のうち有効な測定点が少ないモーラほど距離計算への影響を下げます。

```
信頼度 = min(native有効サンプル数, user有効サンプル数) / 3

3点すべて有効 → weight = 1.0
2点有効       → weight = 0.67
1点のみ有効   → weight = 0.33

mean_dist = Σ(dist × weight) / Σ(weight)  ← 加重平均
```

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

### 全単語 VOICEVOX 統一（`scripts/regenerate_all_tts.py`）

`source="recorded"`（人間録音）の単語（word1〜word30）を含む全 53 単語を、
同一の VOICEVOX 話者で再生成しました。

**変更内容**
- `data/raw_audio/sound/` の全音声・アライメント（.lab・.log）を上書き再生成
- `web/static/sample/` のサンプル音声を新音声に同期
- `words_db.json` の全単語の `source` を `"tts"` に統一
- `core/accent.py` の `VOICEVOX_SPEAKER` を指定 ID に自動更新

**関連スクリプト**

```bash
# 使用可能な話者一覧を確認する
python scripts/check_voicevox_speakers.py

# 全単語を指定話者で再生成する（SPEAKER_ID を変更してから実行）
python scripts/regenerate_all_tts.py
python scripts/regenerate_mfcc.py   # 必ずセットで実行
```

---

### 話者性別によるフォルマント補正（`core/formant.py`）

| | 変更前 | 変更後 |
|--|--------|--------|
| 性別判定 | なし | `pitch_ceiling_user ≤ 200Hz` → 男性と判定 |
| 補正係数 | なし | `native_F1/F2 × 0.85` を適用 |
| 根拠 | — | Peterson & Barney (1952)：男性フォルマント ≈ 女性の 82〜87% |

VOICEVOX（女性寄りの声）を基準にしているため、男性ユーザーが録音すると
F1/F2 が体系的にずれて母音スコアが不当に低く出る問題を解消しました。

`app.py` の `calc_vowel_score()` 呼び出しに `pitch_ceiling_user=ceiling_learn` を追加することで有効化しています。

---

### 有効サンプル数による信頼度重み付け（`core/formant.py`）

| 有効サンプル数 | 変更前 | 変更後 |
|-------------|--------|--------|
| 3点 | weight = 1.0 | weight = 1.0 |
| 2点 | weight = 1.0 | weight = 0.67 |
| 1点のみ | weight = 1.0 | weight = 0.33 |

子音が長いモーラや短いモーラで有効測定点が 1 点だけになった場合に
ノイズを拾った値が過大に影響するのを防ぎます。

---

### MFA（Montreal Forced Aligner）対応（`core/alignment.py`）

Julius の代わりに MFA を使えるようになりました。`config.py` の `USE_MFA` フラグで切り替えます。

> ⚠️ **現在 `USE_MFA = False` を推奨します。**
> MFA を有効にするとスコアが大幅に低下することを確認しました。
> 原因は以下の3点です：
>
> 1. **音素ラベルの体系が違う** — MFA は Julius と異なる音素ラベルを使用する。`mora_time()` が Julius の出力形式を前提としているため、MFA の出力ではモーラ境界が正しく構築できない
> 2. **フレーム番号の変換ズレ** — Julius の `.log` は `offset_align = 0.0125`（12.5ms）込みのフレーム番号を出力する。合成ログは単純に `秒 ÷ 0.01` で変換しているためズレが生じ、ピッチ抽出窓がずれる
> 3. **パイプライン全体が Julius 前提** — `lab_load()` / `log_load()` / `mora_time()` / `length_arrange()` がすべて Julius の出力体系を前提に設計されている
>
> 正しく動かすには音素変換・フレーム変換・モーラ構築のパイプライン全体の書き直しが必要。

**バグ修正履歴**

| バージョン | 修正内容 |
|-----------|---------|
| 初版 | `mfa_run()` で `TEST_WAV_PATH` が未インポート → `NameError` |
| 修正1 | `TEST_WAV_PATH` をインポートに追加 |
| 修正2 | `mfa align_one` の第2引数にテキスト文字列を直接渡していた → 一時ファイルに書き出してパスを渡すよう修正 |

**追加した関数**

| 関数 | 説明 |
|------|------|
| `mfa_run()` | MFA でアライメントを実行し、TextGrid を生成する |
| `textgrid_to_lab()` | TextGrid → Julius 互換 .lab に変換する |
| `_synthetic_log_from_lab()` | .lab から `log_load()` が読める合成 .log を生成する |
| `run_alignment()` | MFA を試し、失敗したら Julius にフォールバックする |

**フォールバック設計**

```
run_alignment()
  ├─ USE_MFA=True  → mfa_run() 成功 → TextGrid → .lab + 合成.log
  │                → mfa_run() 失敗 → perl_run() → .lab + .log
  └─ USE_MFA=False → perl_run() → .lab + .log（従来通り・推奨）
```

**app.py の変更**

`perl_run()` の呼び出しを `run_alignment()` に変更した 2 箇所のみ。
`lab_load()` / `log_load()` は変更なし。

---

### 性別補正ロジックの修正（`core/formant.py`）

**バグの内容**

初期実装ではネイティブ音声が女性前提の補正になっていた。
サンプル音声が男性の場合、補正が逆効果（スコアが下がる方向）になっていた。

**修正内容**

ネイティブ・ユーザーの `pitch_ceiling` を比較し、**両方の性別の組み合わせ**で補正係数を決定するよう変更。

```python
_calc_correction(pitch_ceiling_native, pitch_ceiling_user)

同性同士    → 補正なし（1.0）
女性→男性   → ネイティブ × 0.85
男性→女性   → ネイティブ × 1.18（÷0.85）
```

`app.py` の `calc_vowel_score()` 呼び出しに `pitch_ceiling_native=ceiling_sample` を追加。

**限界（変更なし）**

補正係数 0.85 は集団平均値のため個人差を完全には吸収できない。
「性別による評価の偏りを大幅に軽減した」であり「完全に公平」ではない。

---

### モーラ別スコアの表示・ピッチグラフハイライト（`core/evaluate.py` / `line_graph.html`）

**追加した機能**

| 機能 | 説明 |
|------|------|
| `calc_mora_scores()` | 音節ごとにアクセント・長さ・母音を 0〜100 点で評価。最もスコアが低い `worst_mora` を返す |
| モーラ別スコアテーブル | スコアタブに音節単位の内訳テーブルを表示。最低点の音節に ⚠️ マークと赤ハイライト |
| ピッチグラフハイライト | ピッチ比較グラフ上で `worst_mora` の区間を赤背景帯で強調（Chart.js Annotation） |

---

### 長音・促音の絶対時間チェック（`core/evaluate.py`）

`calc_length_score()` に通常モーラとの比率チェックを追加した。

```
通常モーラの平均長さを基準に判定：
  長音（ー）: 1.5〜3.0 倍が適正範囲
  促音（っ）: 0.6〜1.8 倍が適正範囲
```

範囲外の場合は相対比較エラーより優先して具体的なフィードバックを出す。

---

### スコアの信頼区間（`core/confidence.py`）

**実装背景**

録音環境・ノイズ・Julius のブレにより、同じ発音でも毎回数点スコアが変わる。
1回の結果だけでは「本当の実力」がわからないという問題を解消するため、
ブートストラップ法で95%信頼区間を推定する機能を追加した。

**仕組み**

```
同じ単語の N 回分のスコアに対して：
  1. N 個からランダムに N 個を復元抽出（1試行）
  2. その試行の平均を記録
  3. 1〜2 を 2000 回繰り返す
  4. 2000 個の平均の 2.5〜97.5 パーセンタイルが 95% 信頼区間
```

**表示**

- 3回未満：「あと○回録音すると計算できます」を表示
- 3回以上：`68〜76点（±4点）` のように幅で表示
- 精度ランク（高/中/低）を録音回数から自動判定

**研究的な意義**

区間の幅が「発音の安定性」の指標になる。
幅が狭いほど毎回安定した発音ができているという解釈が可能。

---

### 学習曲線の自動分析（`core/analysis.py` / `/analysis`）

練習ログから上達の状況を統計的に分析する新ページ（`/analysis`）を追加した。

**分析内容**

| 分析 | 内容 | 研究的意義 |
|------|------|-----------|
| 上達速度 | 回帰直線の傾き（点/回） | 個人差・単語難易度の定量化 |
| プラトー検出 | 直近10回の傾きが±0.3未満 | 停滞期の客観的な定義 |
| アクセント型難易度 | 型ごとの平均傾き・Sランク到達率 | どの型が学習者に難しいかの検証 |
| Sランク到達回数 | 平均・中央値・最小/最大 | 「何回練習すれば上達するか」の定量化 |
| 全体トレンド | 5回移動平均 | アプリの教育効果の可視化 |

---

### フォルマントキャッシュ（`core/formant.py`）

`extract_mora_formants()` に `use_cache` オプションを追加した。

```python
# ネイティブ音声はキャッシュを使用（2回目以降は Praat を起動しない）
native_formants = extract_mora_formants(audio_sample, mora_list1, use_cache=True)
# 録音音声はキャッシュなし（毎回新しく計算）
user_formants   = extract_mora_formants(audio_learn,  mora_list2, use_cache=False)
```

キャッシュ先：`data/config/formant_cache.json`
ファイルの更新時刻（mtime）が変わると自動で無効化される。

---

### ダークモード（`web/static/css/base.css`）

`[data-theme="dark"]` の CSS 変数セットを追加した。
全テンプレートの右上ナビに 🌙 トグルボタンを追加し、
選択した設定は `localStorage` に保存してページをまたいで維持する。

---

### その他 UX 改善

| 機能 | ファイル | 説明 |
|------|---------|------|
| 録音やり直し確認ダイアログ | `main.js` | 録音済みの状態で録音開始を押すと「前の録音を破棄しますか？」を表示 |
| 苦手アクセント型の分析 | `history.html` | 履歴ページにアクセント型別の平均スコアカードを追加（苦手順） |

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
| GET | `/history` | 練習履歴ページ（アクセント型別分析を含む） |
| GET | `/history/export.csv` | 履歴を CSV でダウンロード |
| GET | **`/analysis`** | **学習曲線の自動分析ページ** |
| GET | `/admin` | 管理ページ |
| POST | `/admin/add_word` | 単語追加 API（JSON） |
| POST | `/admin/update_word` | 単語更新 API（JSON） |
| POST | `/admin/delete_word` | 単語削除 API（JSON） |

---

## 既知の限界

| 限界 | 内容 | 状態 |
|------|------|------|
| 参照音声が1本 | 同一話者の VOICEVOX 音声が「唯一の正解」になる | 複数話者の平均化で改善予定 |
| パラメータが推測値 | 重み・閾値を正解データで検証できていない | 評価付き録音 20〜30 例収集でチューニング予定 |
| 性別補正は集団平均 | 補正係数 0.85 は集団平均のため個人差を完全には吸収できない | 個人単位の話者正規化（VTLN）で改善予定 |
| シングルユーザー | `test.wav` が上書きされると比較再生が前の録音になる | セッション ID でファイルを分ける方針で対応予定 |
| `source="recorded"` 単語の削除不可 | `vocab.py` の `delete_word()` で弾かれる | 管理者権限フラグの実装で対応予定 |
| MFA は現状 Julius より精度が低い | SP-PS のパイプライン全体が Julius の音素体系・フレーム番号を前提としており、MFA に切り替えるとモーラ境界がズレてスコアが大幅に低下する | パイプライン全体の書き直しが必要。`USE_MFA = False` を推奨 |

**解決済みの限界**

| 項目 | 対応内容 |
|------|---------|
| ~~話者性別補正なし~~ | ネイティブ・ユーザー両方の性別を自動判定し、組み合わせに応じた補正係数を適用済み |
| ~~性別補正がネイティブ女性前提だった~~ | `pitch_ceiling_native` を追加し、サンプル音声が男性の場合も正しく補正するよう修正済み |
| ~~参照音声の話者混在~~ | 全 53 単語を同一 VOICEVOX 話者で統一済み |

---

## ブランチ運用ルール

### ブランチの種類と命名規則

| プレフィックス | 用途 | 例 |
|-------------|------|-----|
| `feature/` | 新機能の追加 | `feature/quest-system` |
| `fix/` | バグ修正 | `fix/formant-gender-correction` |
| `refactor/` | 動作を変えないコードの整理 | `refactor/alignment-cleanup` |
| `docs/` | ドキュメントのみの変更 | `docs/readme-update` |

**命名のルール**

- すべて小文字・単語はハイフン（`-`）でつなぐ
- 何をするブランチか一目でわかる名前にする
- 長すぎない（3〜4単語程度）

```bash
# 良い例
feature/mfa-alignment
fix/mfa-and-formant-correction
feature/unify-tts-voice

# 悪い例
fix/bug          ← 何のバグか不明
feature/update   ← 何を更新するか不明
Feature/MFA      ← 大文字・単語がつながっていない
```

---

### ブランチの作成からマージまでの流れ

```bash
# 1. main を最新にする
git checkout main
git pull origin main

# 2. ブランチを切る
git checkout -b feature/xxxx

# 3. 作業・コミット
git add <files>
git commit -m "feat: ○○を追加"

# 4. プッシュ
git push origin feature/xxxx

# 5. main にマージ
git checkout main
git merge feature/xxxx
git push origin main
```

---

### コミットメッセージの書き方

1行目にプレフィックスをつけて、何をしたか端的に書く。

| プレフィックス | 用途 |
|-------------|------|
| `feat:` | 新機能 |
| `fix:` | バグ修正 |
| `refactor:` | リファクタリング |
| `docs:` | ドキュメント変更 |
| `chore:` | ビルドや設定ファイルの変更 |

```bash
# 良い例
feat: MFA（Montreal Forced Aligner）対応を追加
fix: 性別補正でネイティブ音声の性別も考慮するよう修正
docs: README にブランチルールを追記

# 悪い例
update          ← 何を更新したか不明
fix bug         ← プレフィックスがない
色々修正した    ← 内容が不明
```

複数の変更がある場合は本文に箇条書きで補足する。

```bash
git commit -m "fix: MFAバグ修正・性別補正ロジック修正

- core/alignment.py: TEST_WAV_PATH をインポートに追加
- core/alignment.py: mfa align_one の引数をファイルパスに修正
- core/formant.py: ネイティブ・ユーザー両方の性別を判定するよう変更"
```

---

### やってはいけないこと

| NG | 理由 |
|----|------|
| main に直接 commit & push | 変更履歴が追いにくくなる |
| 1つのブランチに複数の無関係な変更を混ぜる | レビューや差し戻しが難しくなる |
| バイナリファイル（`.bin`・`.wav`）以外の動作確認用一時ファイルをコミット | リポジトリが肥大化する |
| コミットせずに長期間作業を続ける | 差分が大きくなりすぎてコンフリクトが起きやすくなる |

---

## ライセンス

MIT License

Julius 音響モデル・セグメンテーションキット：MIT License（`engine/License.md` 参照）