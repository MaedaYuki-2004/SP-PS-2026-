# SP-PS — 日本語単語 音声解析・可視化プラットフォーム

> 「声（口）のかたちを綺麗にする一歩を踏める嬉しさを届ける」

録音した日本語単語の発音をネイティブ音声と比較し、
アクセント・モーラ長・母音品質の 3 軸でスコアを算出しフィードバックを提供する Flask アプリケーション。
加えて MediaPipe FaceMesh によるリアルタイム口形分析で、**Julius アライメントのタイムスタンプに基づくモーラ単位の口の開き方**もフィードバックします。

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
| FFmpeg | pydub の音声変換・WebM→WAV 変換（口形分析に必要） |
| MediaPipe | `pip install mediapipe`（口形分析に使用・任意） |
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
| pydub | 音声フォーマット変換（WebM → WAV） |
| soundfile | 音声ファイルの読み書き |
| noisereduce | ノイズ除去 |
| mecab-python3 | アクセント型の自動取得 |
| unidic | MeCab 用 UniDic 辞書 |
| mediapipe | 口形分析（FaceMesh）・任意 |
| opencv-python | 動画フレーム読み込み（mediapipe と組み合わせ） |

### 3. Julius の配置

Julius は音素アライメントに使用します。

**Windows の場合**

```
engine/bin/julius-4.3.1.exe
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
sudo apt-get install -y julius julius-dev
```

### 4. Perl のインストール

**Windows**（Strawberry Perl 推奨）
https://strawberryperl.com/ からインストール。

**Ubuntu / Debian**

```bash
sudo apt-get install -y perl
```

### 5. FFmpeg のインストール

pydub の音声変換・口形分析での WebM→WAV 変換に必要です。

**Windows**

```powershell
winget install Gyan.FFmpeg
# または https://ffmpeg.org/download.html から手動ダウンロード後、bin/ を PATH に追加
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
> 詳細は「既知の限界」を参照してください。

```bash
conda create -n mfa -c conda-forge montreal-forced-aligner python=3.10
conda activate mfa
python scripts/setup_mfa.py
```

完了後に `config.py` の `USE_MFA = True` に変更します（現在は非推奨）。

### 9. 動作確認

```bash
python diagnose.py
```

`[OK]` が全項目で出れば起動準備完了です。

---

## 設定

`config.py` を環境に合わせて確認・編集してください。

```python
# Praat ピッチ検出のデフォルト範囲（自動推定が失敗した場合のフォールバック）
PITCH_FLOOR_DEFAULT   = 70.0    # Hz
PITCH_CEILING_DEFAULT = 400.0   # Hz

# Flask シークレットキー（本番環境では必ず変更）
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

# MFA（Montreal Forced Aligner）
# True  → MFA を優先。失敗時は Julius にフォールバック
# False → Julius のみ（デフォルト・推奨）
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
    "source": "tts"
  }
}
```

| フィールド | 説明 |
|-----------|------|
| `display` | 画面表示用テキスト |
| `reading` | Julius に渡すひらがな読み（長音は「ー」） |
| `accent` | アクセント型（0=平板型 / 1=頭高型 / N=N型） |
| `accent_source` | `"mecab"` / `"manual"` |
| `source` | `"tts"` / `"recorded"` |

### ネイティブ音声の配置

```
data/raw_audio/sound/{word_id}/
  {word_id}.wav   ← ネイティブ音声（16kHz/モノラル/16bit PCM WAV）
  {word_id}.lab   ← Julius アライメント結果
  {word_id}.log   ← Julius ログ
  {word_id}.txt   ← ひらがな読み
```

### 口形参照データ（lip_refs.json）

`data/config/lip_refs.json` に単語ごとの参照口形データが保存されます。

```json
{
  "word1": {
    "schema_version": 2,
    "vectors": [[0.31, 0.28, ...], ...],
    "ratios":  [0.31, 0.28, ...],
    "mora_data": [
      {"label": "o",  "v_h_ratio": 0.42},
      {"label": "N",  "v_h_ratio": 0.18},
      {"label": "do", "v_h_ratio": 0.31}
    ]
  }
}
```

| フィールド | 説明 |
|-----------|------|
| `schema_version` | `2` = Julius アライメント付き新形式。なし / 1 = 旧形式（後方互換） |
| `vectors` | 全フレームの FaceMesh ランドマークベクトル（DTW 用） |
| `ratios` | 全フレームの `v_h_ratio` 列（グラフ表示用） |
| `mora_data` | モーラごとの代表 `v_h_ratio`（アライメントで取得した 30/50/70% の中央値）|

`mora_data` がない場合は音韻テーブルにフォールバックします（後方互換）。

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
2. 録音ページで「お手本を録画」ボタンを押して参照口形を登録する（3秒）
3. 録音ページで発音する（`Space` で開始/停止・`Enter` で解析）
4. 解析結果ページでスコア・フィードバック・モーラ別口形分析を確認する
5. 「交互に再生」でネイティブ音声と自分の録音を聞き比べる
6. クエストをこなしながら苦手パターンを練習する
7. `/history` で過去のスコア推移・間隔反復の候補を確認する

---

## 機能一覧

### 発音スコア（3軸 100点満点）

| 軸 | 配点 | 評価内容 |
|----|------|---------|
| アクセント | 50点 | 核位置・ピッチ相関・H/L 一致率・安定度 |
| 長さ | 30点 | 各モーラの時間割合比較（長音・促音を2倍重視） |
| 母音品質 | 20点 | F1/F2 フォルマントの Bark スケール距離（30/50/70%の3点平均・**性別補正・サンプル数重み付け**あり） |

グレード：**S** (≥90) / **A** (≥75) / **B** (≥60) / **C** (≥40) / **D** (<40)

---

### 口形分析（スコア非影響・フィードバックのみ）

MediaPipe FaceMesh を使い、Julius のアライメントで得たモーラのタイムスタンプを基に、各音節の口の開き方を定量評価します。

**使用するランドマーク**

| 番号 | 部位 |
|------|------|
| 13 | 上唇（内側） |
| 14 | 下唇（内側） |
| 61 | 口の左端 |
| 291 | 口の右端 |

**指標：v_h_ratio（縦横比）**

```
v_dist = 上唇(13) と 下唇(14) の距離
h_dist = 左端(61) と 右端(291) の距離
v_h_ratio = v_dist / h_dist
```

値が大きいほど口が縦に大きく開いている。値が小さいほど口が横に細く開いている。

**比較方式**

- **お手本登録あり（schema_version: 2 + mora_data）**：同じ位置のモーラ同士を直接比較
  - 正規化：95 パーセンタイルを最大値として各値を 0〜1.5 にスケール（外れ値に頑健）
  - 差の判定：|actual - expected| < 0.15 → 「良好」／差 < 0 → 「開きが足りない」／差 > 0 → 「開きすぎ」
- **お手本登録なし**：母音の音韻テーブル（`/a/` は大きく・`/i/` は小さく 等）を期待値として使用

**フォールバック構造**

```
ref_entry に schema_version==2 かつ mora_data あり
  → _build_lip_mora_comparison()  ← 直接比較（位置基準）
  
ref_entry なし または mora_data なし
  → _build_lip_mora_analysis()    ← 音韻テーブル期待値で代替
```

**MediaPipe が使えない場合**

`mediapipe` がインストールされていない場合は `MEDIA_PIPE_AVAILABLE = False` となり、口形分析タブが非表示になります。他の機能（スコア評価）は通常通り動作します。

---

### UI / UX

| 機能 | 説明 |
|------|------|
| **キーボードショートカット** | `Space` で録音開始/停止・`Enter` で解析開始 |
| **音声比較再生** | ネイティブ → 自分の録音を3回交互再生 |
| **音量バー** | 録音中のリアルタイム入力レベル表示（RMS） |
| **無音自動停止** | 1.5秒無音で録音を自動停止（0.5秒発話後に有効化） |
| **録音やり直し確認** | 録音済みの状態で録音開始を押すと確認ダイアログを表示 |
| **解析ボタンの動的表示** | 音声・唇動画のアップロードが両方完了してから表示（`Promise.all`） |
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
| **口形参照データ管理** | `/api/lip_refs/*` で参照データの一覧・削除・上書き |

---

## ディレクトリ構成

```
sp-ps/
├── app.py                              # Flask ルーティング・スコア集計・口形比較ロジック
├── config.py                           # パス定数・Julius 自動検出・音素定数
├── diagnose.py                         # 環境診断スクリプト
├── requirements.txt
├── core/
│   ├── __init__.py
│   ├── accent.py                       # MeCab アクセント取得・VOICEVOX 音声生成
│   ├── alignment.py                    # Julius 実行・lab/log 読み込み・任意ファイルアライメント
│   ├── analysis.py                     # 学習曲線の自動分析（回帰・プラトー・Sランク分布）
│   ├── audio.py                        # 音声変換・ノイズ除去・セグメント切り出し
│   ├── confidence.py                   # ブートストラップ法による 95% 信頼区間の推定
│   ├── evaluate.py                     # スコア算出（アクセント・長さ・総合・モーラ別）
│   ├── formant.py                      # F1/F2 フォルマント抽出（30/50/70%平均）・性別補正・キャッシュ
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
│   │   ├── lip_refs.json               # 口形参照データ（schema_version: 2 形式）
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
│               ├── {word_id}.lab
│               ├── {word_id}.log
│               └── {word_id}.txt
├── engine/
│   ├── License.md
│   ├── bin/julius-4.3.1.exe
│   └── models/hmmdefs_monof_mix16_gid.binhmm
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
    │   └── js/
    │       ├── audio_recorder.js      # AudioWorklet（録音・WAV エンコード）
    │       └── main.js                # 録音 UI・口形録画・無音自動停止・音量バー
    └── templates/
        ├── admin.html
        ├── analysis.html              # 学習曲線の自動分析ページ
        ├── audio.html                 # 録音ページ（口形録画ボタン含む）
        ├── error.html
        ├── history.html               # 練習履歴・スコア推移・アクセント型別分析
        ├── line_graph.html            # 解析結果（モーラ別スコア・口形分析・信頼区間）
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
  ↓ test.lab / test.log を生成
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
  核位置スコア     × 0.40   ← 日本語アクセントで最重要
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
内部スコア   = max(0, (1.0 - weighted_diff / 20.0) × 40)
長さスコア   = 内部スコア × 30 / 40
```

長音（ー）と促音（っ）については通常モーラの平均長さとの比率も確認します（絶対時間チェック）。

```
長音の適正範囲：通常モーラの 1.5〜3.0 倍
促音の適正範囲：通常モーラの 0.6〜1.8 倍
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

`estimate_pitch_range()` が返す `pitch_ceiling` を使い、200Hz 以下の場合は男性と判定します。

| ネイティブ | ユーザー | 補正係数 |
|-----------|---------|---------|
| 女性 | 女性 | 1.0（補正なし） |
| 男性 | 男性 | 1.0（補正なし） |
| 女性 | 男性 | ネイティブ × 0.85 |
| 男性 | 女性 | ネイティブ × 1.18（÷0.85） |

補正係数 0.85 の根拠：Peterson & Barney (1952) ほか音響音声学研究で
男性フォルマントは女性の約 82〜87%。保守的な値として 0.85 を採用。

**フォルマント抽出の3点サンプリング**

各モーラのフォルマントはモーラ区間の **30% / 50% / 70%** の3時刻で測定し、
有効値（NaN でない値）の平均を最終値とします。

```python
_SAMPLE_RATIOS = [0.30, 0.50, 0.70]

for ratio in _SAMPLE_RATIOS:
    t = start + duration * ratio
    f1, f2 = get_formant_at_time(formant, t)
```

中心時刻1点だけを使う場合、子音の解放直後（/k/→/a/ の遷移区間など）を
誤って拾うことがあります。3点平均で遷移区間の影響を緩和し、母音の安定核を評価します。

**有効サンプル数による信頼度重み付け**

```
3点すべて有効 → weight = 1.0
2点有効       → weight = 0.67
1点のみ有効   → weight = 0.33

mean_dist = Σ(dist × weight) / Σ(weight)  ← 加重平均
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

| 指標 | 内容 | しきい値 |
|------|------|---------|
| 発話速度 | モーラ/秒でネイティブと比較 | ネイティブ比 0.70〜1.15 が適切 |
| ジッター | F0 の変動率（声のピッチの安定さ） | > 3% で警告 |
| シマー | 振幅の変動率（声の音量の安定さ） | > 8% で警告 |
| 有声フレーム比率 | モーラ内で声が出ているフレームの割合 | < 45% で警告 |
| **口形（v_h_ratio）** | 唇の縦横比とお手本の差（モーラ別） | ±0.15 未満で良好 |

---

## システム内部の処理フロー

スコアロジックが「何を計算するか」を説明するのに対し、このセクションは「どのように処理されるか」を説明します。

---

### 1. 録音から結果画面までの全体フロー

```
【ブラウザ側】
  navigator.mediaDevices.getUserMedia({ video:true, audio:true })
    ↓
  stream（音声+映像）と videoStream（映像のみ）を分離
  AudioWorkletNode（audio_recorder.js）で Float32 サンプルをキャプチャ
    ↓
  encodeAudio()  →  WAV ヘッダを付けて Blob に変換
    ↓
  POST /audio    →  test.wav として保存 → Julius アライメント

  （録音開始と同時に lipRecorder で映像をテスト動画として録画開始）
    ↓
  POST /upload_lip_video (mode=test)  →  一時保存

【解析ボタン押下（POST /graph）】
  音声・唇動画のアップロードが両方完了してから表示（Promise.all）
    ↓
  estimate_pitch_range()    ネイティブ・録音の話者ピッチ範囲を自動推定
  praat_pitch()             Praat で F0 を抽出（NaN 保持の Hz 配列）
  resample_to_10ms()        Julius フレーム（10ms 単位）にリサンプリング
  lab_load() / log_load()   アライメント結果を読み込み
  extract_julius_score()    品質チェック → < -3000 なら以降の計算をスキップ
  hz_to_semitone()          Hz → 半音変換（NaN 保持）
  length_arrange()          録音ピッチをネイティブのモーラ長に時間正規化
  extract_mora_formants()   F1/F2 を 3点平均で抽出
  calc_total_score()        アクセント・長さ・母音スコアを合算
  _extract_mora_lip_openness()  唇動画からモーラ別 v_h_ratio を抽出
  _build_lip_mora_comparison()  お手本データと直接比較
  save_record()             history.json に保存
  check_and_update_quests() クエスト更新（間隔反復含む）
  render_template("line_graph.html")
  → 処理完了後、一時唇動画ファイルを削除し session をクリア
```

---

### 2. 口形分析の内部フロー

**お手本登録（POST /upload_lip_video mode=ref）**

```
ブラウザの「お手本を録画」ボタンを押す
  → stream（音声+映像）を MediaRecorder で 3 秒録画
  → 音声付き WebM を POST /upload_lip_video

サーバー側：
  _save_temp_video()         一時ファイルに保存
  _extract_lip_data()        全フレームの FaceMesh ベクトル・v_h_ratio を抽出

  ── アライメントによるモーラ別参照データ ──
  WORD_ID_MEMO_PATH から word_id を取得
  get_word(word_id)          words_db.json からひらがな読みを取得
  _webm_to_wav()             pydub + FFmpeg で音声を 16kHz WAV に変換
  _align_lip_ref()
    └ run_alignment_on_file()
        一時ディレクトリを作成
        test.wav + test.txt を配置
        Julius Perl スクリプトを実行
        test.lab を lab_out にコピー
        一時ディレクトリを削除
    └ lab_load()             モーラのタイムスタンプ（秒）を取得

  _extract_mora_lip_openness(webm_path, mora_list_sec)
    VideoCapture で全フレームをメモリに読み込み（WebM シーク問題を回避）
    各モーラの 30/50/70% 時刻のフレームインデックスを FPS で計算
    FaceMesh でランドマークを検出
    v_h_ratio = v_dist / h_dist を計算

  mora_data = [{"label": "...", "v_h_ratio": ...}, ...]

  lip_refs.json に保存：
  {
    "schema_version": 2,
    "vectors": [...],
    "ratios":  [...],
    "mora_data": [{"label": "ka", "v_h_ratio": 0.35}, ...]
  }

  レスポンス：{"message": "ok", "alignment_ok": true/false, "mora_count": N}
  alignment_ok=true  → 「お手本登録完了（Nモーラ分析済み）」（3秒後に消える）
  alignment_ok=false → 「⚠ 音声認識に失敗しました。はっきり発音して録画し直してください。」
```

**テスト比較（/graph）**

```
セッションの唇テスト動画パスを取得
  _extract_mora_lip_openness(test_video, mora_list2)
    ユーザーの各モーラの v_h_ratio を取得

  lip_refs.json から word_id のエントリを確認
  schema_version==2 かつ mora_data あり
    → _build_lip_mora_comparison(mora_list2, raw_user, ref_mora_data)
         for i in range(n_ref):
           actual   = min(user[i].openness / user_p95, 1.5)
           expected = min(ref[i].v_h_ratio / ref_p95,  1.5)
           diff     = actual - expected
           status   = |diff|<0.15→"good" / diff<0→"low" / diff>0→"high"
  mora_data なし
    → _build_lip_mora_analysis(mora_list2, raw_user)
         音韻テーブルの期待値と比較
```

---

### 3. Julius 強制アライメント

Julius の「強制アライメント（Forced Alignment）」は、
**既知のテキスト（ひらがな読み）が音声のどのタイミングで発音されているか**を特定する処理です。

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
0.0000000 0.0750000 silB
0.0750000 0.1500000 by
0.1500000 0.2500000 o:
0.2500000 0.3000000 i
0.3000000 0.3750000 N
0.3750000 0.4500000 silE
```

**Julius スコアの目安**

| スコア | 状態 |
|--------|------|
| -1000 以上 | アライメント良好 |
| -1000 〜 -3000 | やや不安定（音質や発音の問題の可能性） |
| -3000 以下 | 不安定 → スコア計算をスキップ |

**任意ファイルへのアライメント（`run_alignment_on_file()`）**

通常の `run_alignment()` は固定パス（`data/raw_audio/wav/test.wav`）専用ですが、
`run_alignment_on_file()` は任意の WAV ファイルに対してアライメントを実行できます。
口形お手本の音声アライメントに使用しています。

```python
def run_alignment_on_file(wav_path, reading, lab_out, log_out):
    """Julius のみ対応。固定パスに依存しない任意ファイル向けアライメント。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        shutil.copy(wav_path, tmp / "test.wav")
        (tmp / "test.txt").write_text(reading, encoding="utf-8")
        subprocess.run(["perl", PERL_SCRIPT_PATH, str(tmp)], ...)
        shutil.copy(tmp / "test.lab", lab_out)
```

---

### 4. モーラと音素の違い

| 概念 | 定義 | 例（「びょういん」） |
|------|------|------|
| 音素 | 音声学的な最小単位 | `by` / `o:` / `i` / `N` |
| モーラ | 日本語リズムの最小単位（1拍） | `びょ` / `う` / `い` / `ん` |

SP-PS では Julius の音素アライメント結果を `mora_time()` でモーラ単位に変換してから評価します。

```
① 子音 + 次が母音 → 結合して1モーラ       by + o: → "byo:"
② 単独の母音      → そのまま1モーラ         i      → "i"
③ N（撥音・ん）   → 単独1モーラ             N      → "N"
④ q（促音・っ）   → 単独1モーラ             q      → "q"
⑤ 結合できない子音 → 単独1モーラ（異常時のフォールバック）
```

アクセントスコアとモーラ長スコアはこのモーラ単位で計算されます。

---

### 5. ピッチ処理パイプライン

ピッチ処理は**表示用**と**スコア計算用**で異なる経路をたどります。

```
praat_pitch()          Hz 配列（NaN 保持：無声区間は NaN）
  ↓
resample_to_10ms()     Julius の 10ms フレームに揃える
  ↓
発話区間だけを切り出す（Julius の silB〜silE の外を除去）
  ↓
hz_to_semitone()       Hz → 半音変換（NaN 保持）
                       ← pitch_native_raw / pitch_user_raw
                          （有声フレームのみで Pearson 計算に使用）

  ┌──────────────────────────┐     ┌──────────────────────────┐
  │     スコア計算用          │     │     表示用（グラフ）       │
  ├──────────────────────────┤     ├──────────────────────────┤
  │ length_arrange()          │     │ comp()    NaN を線形補間  │
  │ 録音をネイティブの音素長  │     │ smooth()  window=5 で平滑 │
  │ に時間正規化              │     │ scale()   0〜1 に正規化   │
  │ comp()  NaN を補間        │     └──────────────────────────┘
  │ smooth() window=3         │
  └──────────────────────────┘
```

**なぜ Hz → 半音（semitone）に変換するのか**

ピッチを Hz のまま比較すると、話者の声の絶対的な高さの違いがそのまま「差」になります。
半音は比率（対数）スケールなので、絶対値ではなく「上がり下がりのパターン」だけを比較できます。

```
半音 = 12 × log2(F / F_ref)
F_ref = 有声フレームの中央値（話者ごとに個別に設定 → 話者差を吸収）
```

---

### 6. 時間正規化（length_arrange）

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

---

### 7. 音色評価（MFCC + DTW）

**MFCC 構成（36次元）**

```
静的 MFCC （12次元）: スペクトル包絡の形状
Δ  MFCC （12次元）: 1階時間微分（変化の速さ）
ΔΔ MFCC （12次元）: 2階時間微分（変化の加速度）
```

静的 MFCC だけでは音の「瞬間的な断面」しか捉えられません。
Δ・ΔΔ を追加することで「音の動き」も比較できます。

**DTW（動的時間伸縮法）**

2つの時系列データの「最も近い対応付け」を動的計画法で求めるアルゴリズムです。
長さが異なる音声同士でも比較でき、発話速度の違いを吸収します。

**音色グラフの見方**

```
赤いバー  ：今回練習した単語
青いバー  ：その他の単語
バーが左（距離が小）→ 音色が近い
理想：赤いバーが最左端
```

---

### 8. クエストシステムの内部ロジック

```
check_and_update_quests(score_result, word_id)
  ↓
  1. アクティブなクエストのうち目標を超えたものをクリア
  2. 空きスロット（最大3）分を補充
     ① get_spaced_repetition_candidates() で復習候補を確認
        3日以上未練習 かつ スコア85点未満 → review クエストを最大1つ追加
     ② 残りを弱点軸（スコアが低い順）で埋める
  3. quest_progress.json に保存
```

**難易度と目標増分**

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

**クエストカテゴリと表示色**

| category | 意味 | 表示色 |
|----------|------|--------|
| `accent` | アクセント改善 | 赤（`--accent`） |
| `length` | 長さ改善 | 緑（`--green`） |
| `vowel` | 母音改善 | 青（`--blue`） |
| `total` | 総合スコア | 紺（`--navy`） |
| `review` | 間隔反復（復習） | 橙（`--quest`） |

---

### 9. 単語登録フロー（vocab.py）

```
register_word(display, reading)
  ↓
  1. get_accent(display)
     MeCab + UniDic でアクセント型を自動取得
     → UniDic フィーチャーの25番目フィールドを読む
     → 複数型がある場合は最初の値を採用

  2. generate_sample_wav(display, wav_path)
     VOICEVOX（話者 ID: 11 / ずんだもん）で音声合成
     → POST /audio_query → POST /synthesis → 16kHz WAV として保存

  3. convert_to_16kHz()
     16kHz / モノラル / 16bit PCM に変換（Julius 要求形式）

  4. perl_run()
     Julius で音素アライメントを実行
     → .lab・.log を生成

  5. audio_mfcc()
     MFCC + Δ + ΔΔ（36次元）を計算 → {word_id}.bin として保存

  6. words_db.json・audio.scp・words.txt を更新
```

---

### 10. 録音の音声処理パイプライン

**ブラウザ側の流れ**

```
getUserMedia()            マイク（16kHz）+ カメラ入力
  ↓
videoStream               映像トラックのみ（口形プレビュー・テスト録画用）
  ↓
AudioWorkletNode          Float32 サンプルをフレーム単位でキャプチャ
  ↓
encodeAudio()
  ├ sampleSize が未定義のブラウザ（Firefox 等）→ 16bit にフォールバック
  └ WAV ヘッダ（44バイト）を付加して Blob に変換
  ↓
POST /audio               Flask に送信

音量バー：AnalyserNode で RMS を計算して #volumeFill の width に反映
無音自動停止：RMS < 0.03 が 1500ms 継続かつ 500ms 以上発話後 → buttonStop.click()
```

**サーバー側の流れ**

```
POST /audio
  ↓ file.save(TEST_WAV_PATH)
  ↓ convert_to_16kHz()
  ↓ run_alignment()              Julius でアライメント実行（test.lab・test.log 生成）
  ↓ 「OK!」を返す

POST /graph（解析ボタン押下）
  ↓ Promise.all([audioUpload, lipUpload]) 完了後にボタン表示
  ↓ audio_analysis()
  ↓ render_template("line_graph.html")
  ↓ 一時唇動画ファイルを削除・session をクリア
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
- ピッチグラフは引き続き表示

---

### Delta MFCC の導入（`core/timbre.py`）

静的 MFCC(12次元) + Δ(12次元) + ΔΔ(12次元) = **36次元** に変更。

> ⚠️ 既存の `.bin` ファイル（12次元）は非互換です。
> ```bash
> python scripts/regenerate_mfcc.py
> ```

---

### 話者ピッチ範囲の自動推定（`core/pitch.py`）

```python
def estimate_pitch_range(sound_file, percentile_low=10.0, percentile_high=90.0,
                         margin_low=0.75, margin_high=1.50) -> tuple[float, float]:
    # 1. 広い範囲（50〜700Hz）でピッチを大まかに検出
    # 2. 有声フレームの 10〜90 パーセンタイルを取得
    # 3. マージンを掛けて floor / ceiling を決定
```

---

### フォルマント抽出精度の改善

| | 変更前 | 変更後 |
|--|--------|--------|
| 測定点 | モーラ中心（50%）の1点 | 30% / 50% / 70% の3点の平均 |
| NaN 処理 | 中心が NaN の場合スキップ | 有効点のみで平均（最低1点あれば算出） |

---

### 間隔反復クエスト（`core/history.py` + `core/quest.py`）

```python
get_spaced_repetition_candidates(
    min_days  = 3.0,    # 最終練習から何日以上経過したか
    max_score = 85.0,   # スコアがこの点未満の単語のみ対象
    limit     = 5,
) -> list[dict]
```

---

### キーボードショートカット（`web/templates/audio.html`）

`input` / `textarea` / `audio` 要素にフォーカスしているときは無効。

| キー | 動作 |
|------|------|
| `Space` | 録音開始（startBtn が有効時）/ 停止（stopBtn が有効時） |
| `Enter` | 解析開始ボタンが表示されている場合にフォーム送信 |

---

### 音声比較再生（`/recorded_audio`）

```
GET /recorded_audio
  → data/raw_audio/wav/test.wav を audio/wav で返す
```

結果ページの「交互に再生」ボタン：ネイティブ → 800ms → 録音 → 800ms（3回繰り返し）。

---

### 全単語 VOICEVOX 統一（`scripts/regenerate_all_tts.py`）

`source="recorded"`（人間録音）を含む全単語を、同一の VOICEVOX 話者で再生成。
`words_db.json` の全単語の `source` を `"tts"` に統一。

---

### 話者性別によるフォルマント補正（`core/formant.py`）

| | 変更前 | 変更後 |
|--|--------|--------|
| 性別判定 | なし | `pitch_ceiling ≤ 200Hz` → 男性と判定 |
| 補正方向 | ユーザーのみ | ネイティブ・ユーザー両方の性別で組み合わせ判定 |

---

### MFA（Montreal Forced Aligner）対応（`core/alignment.py`）

Julius の代わりに MFA を使えるようになりました。`config.py` の `USE_MFA` フラグで切り替えます。

> ⚠️ **現在 `USE_MFA = False` を推奨します。**
> MFA を有効にするとスコアが大幅に低下します。原因は以下の3点：
>
> 1. **音素ラベルの体系が違う** — MFA は Julius と異なる音素ラベルを使用し、`mora_time()` の変換が失敗する
> 2. **フレーム番号の変換ズレ** — Julius の `offset_align = 0.0125` 込みのフレーム番号を合成ログは再現できない
> 3. **パイプライン全体が Julius 前提** — `lab_load()` / `log_load()` / `mora_time()` / `length_arrange()` がすべて Julius の出力体系を前提としている

追加した関数：

| 関数 | 説明 |
|------|------|
| `mfa_run()` | MFA でアライメントを実行し TextGrid を生成 |
| `textgrid_to_lab()` | TextGrid → Julius 互換 .lab に変換 |
| `_synthetic_log_from_lab()` | .lab から `log_load()` が読める合成 .log を生成 |
| `run_alignment()` | MFA を試し、失敗したら Julius にフォールバック |
| **`run_alignment_on_file()`** | **固定パスに依存しない任意ファイル向けアライメント（口形分析で使用）** |

---

### モーラ別スコアの表示・ピッチグラフハイライト

| 機能 | 説明 |
|------|------|
| `calc_mora_scores()` | 音節ごとにアクセント・長さ・母音を 0〜100 点で評価 |
| モーラ別スコアテーブル | 最低点の音節に ⚠️ マークと赤ハイライト |
| ピッチグラフハイライト | `worst_mora` の区間を赤背景帯で強調（Chart.js Annotation） |

---

### 長音・促音の絶対時間チェック（`core/evaluate.py`）

```
通常モーラの平均長さを基準に判定：
  長音（ー）: 1.5〜3.0 倍が適正範囲
  促音（っ）: 0.6〜1.8 倍が適正範囲
```

---

### スコアの信頼区間（`core/confidence.py`）

ブートストラップ法で 95% 信頼区間を推定する機能を追加。

```
1. N 個からランダムに N 個を復元抽出（1試行）
2. その試行の平均を記録
3. 1〜2 を 2000 回繰り返す
4. 2000 個の平均の 2.5〜97.5 パーセンタイルが 95% 信頼区間
```

- 3回未満：「あと○回録音すると計算できます」を表示
- 3回以上：`68〜76点（±4点）` のように幅で表示
- 区間の幅が「発音の安定性」の指標になる（幅が狭いほど安定）

---

### 学習曲線の自動分析（`core/analysis.py` / `/analysis`）

| 分析 | 内容 | 研究的意義 |
|------|------|-----------|
| 上達速度 | 回帰直線の傾き（点/回） | 個人差・単語難易度の定量化 |
| プラトー検出 | 直近10回の傾きが±0.3未満 | 停滞期の客観的な定義 |
| アクセント型難易度 | 型ごとの平均傾き・Sランク到達率 | どの型が学習者に難しいかの検証 |
| Sランク到達回数 | 平均・中央値・最小/最大 | 「何回練習すれば上達するか」の定量化 |

---

### 口形分析機能の追加（`feature/mouth-open` → `feature/lip-ref-alignment`）

#### 口の開き具合の検出（`feature/mouth-open`）

MediaPipe FaceMesh でリアルタイムに口の開き度合いを検出し、結果ページに表示する機能を追加。

- ランドマーク 13（上唇）・14（下唇）・61（左端）・291（右端）を使用
- `v_h_ratio = v_dist / h_dist` で口の縦横比を算出
- `_extract_mora_lip_openness()` でモーラ別の代表フレームを抽出
  - 全フレームをメモリに読み込んでから FPS でインデックス計算（WebM シーク問題の回避）
  - 各モーラの 30/50/70% 時刻の3フレームの中央値を代表値とする
- 音韻テーブル（`_VOWEL_OPENNESS`）による期待値との比較フィードバック

#### Julius アライメントによるお手本との直接比較（`feature/lip-ref-alignment`）

| | 変更前 | 変更後 |
|--|--------|--------|
| 参照データの取得 | 映像のみ録画（`videoStream`） | 音声付き WebM（`stream`）を録画し、サーバーで音声を抽出 |
| アライメント | なし（全フレーム平均） | Julius で音素タイムスタンプを取得し、モーラ単位で対応付け |
| 比較方式 | 音韻テーブルの期待値のみ | お手本の `mora_data` と位置基準で直接比較（テーブルにフォールバック） |
| 正規化 | `max()`（外れ値に脆弱） | `np.percentile(vals, 95)`（外れ値ロバスト） |
| `n` の計算 | `min(mora_list, raw_user)` | `min(mora_list, raw_user, ref_mora_data)`（3者の最小） |
| アライメント失敗通知 | なし | `alignment_ok` フラグを返し、ユーザーに再録画を促す |
| `api_lip_refs_overwrite` | 旧スキーマ（`vectors/ratios` のみ） | アライメントパイプラインを実行し `mora_data` も保存 |
| 解析ボタンの表示 | 3秒固定タイマー | `Promise.all([audioUpload, lipUpload])` 完了後に表示 |
| 一時ファイルの扱い | セッション終了時に残留 | `/graph` 処理後に削除・session をクリア |

**`lip_refs.json` スキーマの進化**

| バージョン | 構造 | 取得方法 |
|-----------|------|---------|
| 旧（schema_version なし） | `{vectors, ratios}` | 映像のみ・全フレーム平均 |
| 新（schema_version: 2） | `{schema_version, vectors, ratios, mora_data}` | 音声付き録画 + Julius アライメント |

---

### バグ修正まとめ

| # | ファイル | 問題 | 修正 |
|---|---------|------|------|
| H-1 | `app.py` | `calc_mora_scores` に Min-Max 正規化済みの表示用ピッチ（0〜1）を渡していた → モーラアクセントスコアが常に過大評価 | `pitch_fin_score` / `pitch_fin2_score`（半音スケール）を渡すよう修正 |
| M-1 | `app.py` | `api_lip_refs_overwrite` が `{vectors, ratios}` のみ保存し、`mora_data` が消える | アライメントパイプラインを追加し `schema_version: 2` で保存 |
| M-2 | `main.js` | `settings.sampleSize` が Firefox/Safari で `undefined` → WAV エンコードが壊れる | `(settings.sampleSize \|\| 16) / 8` にフォールバック |
| M-3 | `app.py` | セッション終了時に一時唇動画ファイルが `/tmp` に蓄積し続ける | `/graph` 処理後に `session.pop("lip_video_paths")` し一時ファイルを削除 |
| M-4 | `main.js` | 解析ボタン表示が 3秒固定タイマー → 唇動画アップロードが間に合わないと口形分析が空になる | `Promise.all([audioPromise, lipUploadPromise]).finally(() => ...)` で完了後に表示 |
| L-1 | `app.py` | `re.search(r"\d+", word_id)` が `None` のとき `num_match.group()` でクラッシュ（`num` は使われない死コード） | 該当2行を削除 |
| L-2 | `core/timbre.py` | 旧12次元 MFCC `.bin` ファイルが `reshape` エラーで無音の `inf` になり原因がわからない | `raw.size % MFCC_TOTAL_DIMS != 0` を先にチェックしてログ出力 |
| L-3 | `core/confidence.py` | `np.random.default_rng(seed=42)` の固定シードでブートストラップ CI が統計的に不正確 | `np.random.default_rng()` でシードなしに変更 |
| L-4 | `app.py` | スコア差が 0 のとき `_score_delta()` が `"+0.0"` を返す | `diff == 0` のとき `None` を返すよう修正 |

---

## 主要 API ルート一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/` | 単語選択ページ |
| POST | `/` | 単語を選択して録音ページへ遷移 |
| GET | `/select` | 単語選択ページ（直接アクセス用） |
| GET | `/audio` | 録音ページ |
| POST | `/audio` | 録音データを保存・Julius アライメントを実行 |
| GET | `/recorded_audio` | 直前の録音（test.wav）を返す |
| POST | `/graph` | Julius 解析を実行して結果ページを返す |
| GET | `/sample_audio/<word_id>` | ネイティブ音声を返す |
| GET | `/upload` | 音声アップロードページ |
| POST | `/upload` | 音声ファイルをアップロードして解析 |
| POST | **`/upload_lip_video`** | **唇動画（ref/test）をアップロード。ref 時は Julius アライメント + mora_data 保存** |
| GET | **`/api/lip_refs`** | **口形参照データのキー一覧を返す** |
| GET | **`/api/lip_refs/<word_id>/ratios`** | **指定単語の v_h_ratio 列を返す** |
| POST | **`/api/lip_refs/delete`** | **指定単語の参照データを削除** |
| POST | **`/api/lip_refs/overwrite`** | **動画ファイルで参照データを上書き（アライメントパイプライン付き）** |
| GET | `/history` | 練習履歴ページ（アクセント型別分析を含む） |
| GET | `/history/export.csv` | 履歴を CSV でダウンロード |
| GET | `/analysis` | 学習曲線の自動分析ページ |
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
| 口形分析は MediaPipe 依存 | `pip install mediapipe` がない環境では口形分析タブが非表示 | オプション機能として維持 |
| 口形比較の 95 パーセンタイル正規化 | 録音数が 1〜2 モーラしかない場合は外れ値の影響が残りうる | `len(vals) >= 2` のときのみ `percentile` を使い、単独値はそのまま使用 |

**解決済みの限界**

| 項目 | 対応内容 |
|------|---------|
| ~~話者性別補正なし~~ | ネイティブ・ユーザー両方の性別を自動判定し、組み合わせに応じた補正係数を適用済み |
| ~~性別補正がネイティブ女性前提だった~~ | `pitch_ceiling_native` を追加し、サンプル音声が男性の場合も正しく補正 |
| ~~参照音声の話者混在~~ | 全単語を同一 VOICEVOX 話者で統一済み |
| ~~口形比較がラベル基準の平均だった~~ | 位置基準（`mora[i] ↔ ref_mora_data[i]`）の直接比較に変更 |
| ~~正規化に max() を使い外れ値に脆弱だった~~ | 95 パーセンタイル正規化に変更 |
| ~~アライメント失敗が無音だった~~ | `alignment_ok` フラグをフロントエンドに返しユーザーに通知 |

---

## ブランチ運用ルール

### ブランチの種類と命名規則

| プレフィックス | 用途 | 例 |
|-------------|------|-----|
| `feature/` | 新機能の追加 | `feature/lip-ref-alignment` |
| `fix/` | バグ修正 | `fix/formant-gender-correction` |
| `refactor/` | 動作を変えないコードの整理 | `refactor/alignment-cleanup` |
| `docs/` | ドキュメントのみの変更 | `docs/readme-update` |

- すべて小文字・単語はハイフン（`-`）でつなぐ
- 何をするブランチか一目でわかる名前にする（3〜4単語程度）

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

| プレフィックス | 用途 |
|-------------|------|
| `feat:` | 新機能 |
| `fix:` | バグ修正 |
| `refactor:` | リファクタリング |
| `docs:` | ドキュメント変更 |
| `chore:` | ビルドや設定ファイルの変更 |

```bash
# 良い例
feat: お手本口形をアライメントで正解データと直接比較する
fix: 唇比較の精度問題を4点修正
docs: READMEを現在の実装内容に合わせて更新

# 悪い例
update          ← 何を更新したか不明
fix bug         ← プレフィックスがない
色々修正した    ← 内容が不明
```

---

### やってはいけないこと

| NG | 理由 |
|----|------|
| main に直接 commit & push | 変更履歴が追いにくくなる |
| 1つのブランチに複数の無関係な変更を混ぜる | レビューや差し戻しが難しくなる |
| `.bin`・`.wav` 以外の動作確認用一時ファイルをコミット | リポジトリが肥大化する |
| コミットせずに長期間作業を続ける | 差分が大きくなりすぎてコンフリクトが起きやすくなる |

---

## ライセンス

MIT License

Julius 音響モデル・セグメンテーションキット：MIT License（`engine/License.md` 参照）
