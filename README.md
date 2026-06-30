# SP-PS — 日本語単語 音声解析・可視化プラットフォーム

> 「声（口）のかたちを綺麗にする一歩を踏める嬉しさを届ける」

録音した日本語単語の発音をネイティブ音声と比較し、
アクセント・モーラ長・母音品質の 3 軸でスコアを算出しフィードバックを提供する Flask アプリケーション。
加えて MediaPipe によるリアルタイム口形分析で、**モーラ単位の口の開き方**もフィードバックします。

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
9. [主要 API ルート一覧](#主要-api-ルート一覧)
10. [既知の限界](#既知の限界)
11. [ブランチ運用ルール](#ブランチ運用ルール)

---

## 動作環境

| 項目 | 要件 |
|------|------|
| OS | Windows 10/11・Ubuntu 22.04 LTS 以降 |
| Python | 3.10 以上 |
| Julius | 4.3.1 以上（Windows は `engine/bin/` に exe を配置） |
| Perl | Strawberry Perl（Windows）/ システム標準（Linux） |
| VOICEVOX | 0.14 以上（サンプル音声の自動生成に必要） |
| MeCab | UniDic 辞書と組み合わせて使用 |
| Praat | parselmouth 経由で自動インストール |
| FFmpeg | pydub の音声変換に必要（唇分析機能で使用） |
| MediaPipe | `pip install mediapipe`（口形分析に使用。任意） |
| MFA | Montreal Forced Aligner 3.3.x（**オプション**・現在非推奨） |

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

MeCab 辞書を別途ダウンロードします（初回のみ）。

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

**Windows**：`engine/bin/` フォルダに Julius の実行ファイルを配置します。

```
engine/bin/julius-4.3.1.exe
engine/models/hmmdefs_monof_mix16_gid.binhmm
```

Julius は以下の優先順位で自動検出されます（`config.py` の `_detect_julius()`）。

1. 環境変数 `JULIUS_BIN`
2. `engine/bin/julius*.exe`（Windows 自動検出）
3. `/opt/homebrew/bin/julius`（Apple Silicon Mac）
4. `/usr/local/bin/julius`（Intel Mac / Linux）
5. `PATH` 検索

**Ubuntu / Debian**：

```bash
sudo apt-get install -y julius julius-dev
```

### 4. Perl のインストール

**Windows**（Strawberry Perl 推奨）：https://strawberryperl.com/

**Ubuntu / Debian**：

```bash
sudo apt-get install -y perl
```

### 5. FFmpeg のインストール

pydub の音声変換と口形分析の WebM → WAV 変換に必要です。

**Windows**：

```powershell
winget install Gyan.FFmpeg
# または https://ffmpeg.org/download.html から手動でダウンロードして PATH に追加
```

**Ubuntu / Debian**：

```bash
sudo apt-get install -y ffmpeg
```

### 6. VOICEVOX のインストール

サンプル音声の自動生成に使用します（新単語追加時のみ必要）。

1. https://voicevox.hiroshiba.jp/ からインストーラーをダウンロードする
2. インストール後に起動する（デフォルトポート `50021` で起動していること）

### 7. MFCC バイナリの生成

Delta MFCC（Δ・ΔΔ）を含む 36 次元で生成します。
新規セットアップ時・単語追加後は必ず実行してください。

```bash
python scripts/regenerate_mfcc.py
```

### 8. 動作確認

```bash
python diagnose.py
```

`[OK]` が全項目で出れば起動準備完了です。

---

## 設定

`config.py` を環境に合わせて確認・編集してください。

```python
# Praat ピッチ検出のデフォルト範囲（自動推定失敗時のフォールバック）
PITCH_FLOOR_DEFAULT   = 70.0    # Hz
PITCH_CEILING_DEFAULT = 400.0   # Hz

# Flask シークレットキー（本番環境では必ず変更）
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

# MFA（Montreal Forced Aligner）
# True → MFA を優先。失敗時は Julius にフォールバック
# False → Julius のみ（デフォルト・推奨）
USE_MFA: bool = False
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
  {word_id}.wav   ← ネイティブ音声（16kHz / モノラル / 16bit PCM）
  {word_id}.lab   ← Julius アライメント結果
  {word_id}.log   ← Julius ログ
  {word_id}.txt   ← ひらがな読み
```

### 新しい単語の追加方法

1. `http://127.0.0.1:5000/admin` にアクセスする
2. 「単語を追加」から表示テキスト・読み・アクセント型を入力する
3. VOICEVOX でサンプル音声を自動生成し、Julius でアライメントを自動実行する
4. MFCC を再生成する

```bash
python scripts/regenerate_mfcc.py
```

---

## 起動方法

```bash
python app.py
```

デフォルトで `http://127.0.0.1:5000` にアクセスできます。

### 使い方の流れ

1. トップページで評価する単語を選択する
2. 録音ページで口形分析のお手本を録画する（「お手本を録画」ボタン）
3. `Space` で録音開始 → 発音 → 停止（または無音自動停止）
4. `Enter` または「解析」ボタンで結果ページへ
5. スコア・フィードバック・モーラ別口形分析を確認する
6. クエストをこなしながら苦手パターンを練習する
7. `/history` で過去のスコア推移・間隔反復の候補を確認する

---

## 機能一覧

### 発音スコア（3軸 100点満点）

| 軸 | 配点 | 評価内容 |
|----|------|---------|
| アクセント | 50点 | 核位置・ピッチ相関・H/L 一致率・安定度 |
| 長さ | 30点 | 各モーラの時間割合比較（長音・促音を2倍重視） |
| 母音品質 | 20点 | F1/F2 フォルマントの Bark スケール距離（性別補正あり） |

グレード：**S** (≥90) / **A** (≥75) / **B** (≥60) / **C** (≥40) / **D** (<40)

---

### 口形分析（スコア非影響・フィードバックのみ）

MediaPipe FaceMesh を使い、モーラ（音節）ごとの口の開き方を評価します。

**仕組み**

1. 「お手本を録画」ボタンで参照動画（音声付き WebM）を録画する
2. サーバー側で Julius アライメントを実行し、モーラのタイムスタンプを取得する
3. タイムスタンプに基づいて各モーラの 30/50/70% 時刻のフレームを抽出する
4. FaceMesh のランドマーク（上唇・下唇・左端・右端）から `v_h_ratio`（縦横比）を計算する
5. 参照データを `data/config/lip_refs.json` に保存する
6. 発音録音時に同じ処理でユーザーの口形データを取得し、参照データと比較する

**比較方式**

- **お手本登録あり（schema_version: 2 + mora_data）**：同じ位置のモーラ同士を直接比較
  - 正規化：95 パーセンタイルを最大値として各値を 0〜1.5 にスケール（外れ値に頑健）
  - 判定：差が ±0.15 未満 → 良好 / 負 → 開きが足りない / 正 → 開きすぎ
- **お手本登録なし**：母音の音韻テーブル（`/a/` は大きく・`/i/` は小さく 等）を期待値として使用

**MediaPipe が使えない場合**

`pip install mediapipe` でインストール済みでない場合は口形分析タブが表示されません。
他の機能（スコア評価）は通常通り動作します。

---

### UI / UX

| 機能 | 説明 |
|------|------|
| **キーボードショートカット** | `Space` で録音開始/停止・`Enter` で解析開始 |
| **音声比較再生** | ネイティブ → 自分の録音を交互再生 |
| **音量バー** | 録音中のリアルタイム入力レベル表示（RMS） |
| **無音自動停止** | 1.5秒無音で録音を自動停止（0.5秒発話後に有効化） |
| **録音やり直し確認** | 録音済み状態で録音開始を押すと確認ダイアログを表示 |
| **ローディングオーバーレイ** | Julius 解析中の画面ブロック |
| **レーダーチャート** | アクセント・長さ・母音の3軸レーダー |
| **モーラ別スコア表示** | 音節ごとのアクセント・長さ・母音スコアをテーブルで表示。最低点の音節に ⚠️ マーク |
| **ピッチグラフのハイライト** | 最もズレている音節の区間を赤帯でハイライト |
| **前回比較バッジ** | 前回スコアとの差分を4項目で表示 |
| **アクセント図解** | 単語カードに H/L パターンをバッジで可視化 |
| **ダークモード** | 右上の 🌙 ボタンで切り替え（localStorage に永続化） |

### 練習サポート

| 機能 | 説明 |
|------|------|
| **クエスト自動生成** | 弱点軸から最大3つを自動発行 |
| **クエスト自動クリア** | 次回録音でスコアが目標を超えたら自動クリア＋補充 |
| **間隔反復クエスト** | 3日以上練習していない単語（スコア85点未満）を最優先提案 |
| **練習履歴** | `/history` で単語別スコア推移グラフ |
| **苦手アクセント型の分析** | 履歴ページにアクセント型別の平均スコアカードを表示 |
| **学習曲線の自動分析** | `/analysis` で上達速度・停滞期・Sランク到達回数を統計的に可視化 |
| **スコアの信頼区間** | ブートストラップ法で 95% 信頼区間を推定 |
| **CSV エクスポート** | `/history/export.csv` で全履歴ダウンロード |
| **次の練習候補** | 同アクセント型・練習回数少ない順で3単語を提案 |

### 管理機能

| 機能 | 説明 |
|------|------|
| **管理ページ** | `/admin` で単語の追加・編集・削除 |
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
│   ├── accent.py                       # MeCab アクセント取得・VOICEVOX 音声生成
│   ├── alignment.py                    # Julius 実行・lab/log 読み込み・任意ファイルアライメント
│   ├── analysis.py                     # 学習曲線の自動分析（回帰・プラトー・Sランク分布）
│   ├── audio.py                        # 音声変換・ノイズ除去・セグメント切り出し
│   ├── confidence.py                   # ブートストラップ法による 95% 信頼区間の推定
│   ├── evaluate.py                     # スコア算出（アクセント・長さ・総合・モーラ別）
│   ├── formant.py                      # F1/F2 フォルマント抽出・性別補正・キャッシュ
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
│   │   ├── lip_refs.json               # 口形参照データ（モーラ別 v_h_ratio）
│   │   ├── quest_progress.json         # クエスト進捗
│   │   ├── word_id.txt                 # 直前に選択した単語 ID
│   │   └── words_db.json              # 単語データベース
│   ├── mfcc/
│   │   └── {word_id}.bin              # MFCC バイナリ（36次元 float32）
│   └── raw_audio/
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
│   ├── bin/julius-4.3.1.exe
│   └── models/hmmdefs_monof_mix16_gid.binhmm
├── scripts/
│   ├── segment_julius.pl              # Julius 音素アライメント Perl スクリプト
│   ├── regenerate_mfcc.py             # MFCC バイナリ一括再生成
│   ├── regenerate_all_tts.py          # 全単語 VOICEVOX 一括再生成
│   ├── regenerate_all_samples.py      # サンプル音声・アライメント一括再生成
│   ├── repair_alignment.py            # 特定単語のアライメント修復
│   └── setup_mfa.py                   # MFA インストール確認
└── web/
    ├── static/
    │   ├── css/
    │   │   ├── base.css               # デザインシステム（CSS 変数・Noto Sans JP）
    │   │   ├── audio.css              # 録音ページ用スタイル
    │   │   └── select.css             # 単語選択ページ用スタイル
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
        └── select.html                # 単語選択（アクセント図解・クエストサイドバー）
```

---

## スコアロジック

### 全体フロー

```
録音音声（test.wav）
  ↓
run_alignment()  →  test.lab / test.log を生成
  ↓
品質チェック：Julius スコア < -3000 → 再録音ガイド
  ↓（品質 OK）
  ├─ ピッチ抽出（Hz → 半音変換）  →  アクセントスコア（50点）
  ├─ モーラ長の割合を比較          →  長さスコア（30点）
  └─ F1/F2 フォルマント抽出        →  母音品質スコア（20点）
  ↓
合計（100点満点）→ グレード判定（S / A / B / C / D）
  ↓
history.json に保存  →  クエスト更新
```

---

### アクセントスコア（0〜50点）

4指標の加重平均で内部スコア（最大60）を算出し、50点に正規化します。

```
内部スコア（最大60）=
  核位置スコア     × 0.40   ← 日本語アクセントで最重要
+ ピッチ相関スコア × 0.35   ← Pearson 相関（有声フレームのみ）
+ H/L 一致率スコア × 0.15   ← 各モーラの高低の正確さ
+ 安定度スコア    × 0.10   ← モーラ内ピッチの安定さ

アクセントスコア = 内部スコア × 50 / 60
```

**ピッチ比較の方針**

- Hz → 半音（semitone）スケールに変換することで話者差（男性 vs 女性）を吸収
- 参照値 = 有声フレームの中央値（話者ごとに個別に設定）
- 比較にはピアソン相関（全フレームではなく両方が有声のフレームのみ）を使用

**H/L 分類**

期待パターンの L 比率に合わせたパーセンタイルで閾値を設定します。
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

長音（ー）と促音（っ）については通常モーラの平均長さとの比率も確認し、
外れ値があれば具体的なフィードバックを出します（1.5〜3.0倍が長音の適正範囲など）。

---

### 母音品質スコア（0〜20点）

F1・F2 フォルマントを Bark スケールで比較してスコア化します。

```
# Hz → Bark 変換（知覚的均等スケール）
Bark = 26.81 × F / (1960 + F) − 0.53

# 正規化距離
dist = √( (ΔBark_F1 / 3.0)² + (ΔBark_F2 / 4.0)² )

# 指数減衰スコア
score = 20 × exp(−1.2 × dist)
```

**3点サンプリング**：各モーラの 30% / 50% / 70% 時刻で測定し、有効値の平均を使用します。
中心1点だけだと子音の解放直後を誤って拾うことがあるため、3点平均で安定させます。

**性別補正**

| ネイティブ | ユーザー | 補正 |
|-----------|---------|------|
| 女性 | 女性 | なし |
| 男性 | 男性 | なし |
| 女性 | 男性 | ネイティブ × 0.85 |
| 男性 | 女性 | ネイティブ × 1.18 |

判定：`estimate_pitch_range()` が返す `pitch_ceiling` ≤ 200Hz → 男性 / > 200Hz → 女性

補正係数 0.85 の根拠：Peterson & Barney (1952) ほか音響音声学研究で
男性フォルマントは女性の約 82〜87%。保守的な値として 0.85 を採用。

**有効サンプル数による信頼度重み付け**

```
3点すべて有効 → weight = 1.0
2点有効       → weight = 0.67
1点のみ有効   → weight = 0.33
```

---

### Julius 品質ゲート

```
Julius スコア（対数尤度平均）< -3000
  → スコア計算を完全にスキップ
  → 再録音ガイドを表示
```

ピッチグラフは引き続き表示します（アライメントと独立して計算されるため）。

---

### 追加フィードバック（スコア非影響）

| 指標 | 内容 | しきい値 |
|------|------|---------|
| 発話速度 | モーラ/秒でネイティブと比較 | ネイティブ比 0.70〜1.15 が適切 |
| ジッター | F0 の変動率 | > 3% で警告 |
| シマー | 振幅の変動率 | > 8% で警告 |
| 有声フレーム比率 | モーラ内で声が出ているフレームの割合 | < 45% で警告 |
| **口形（v_h_ratio）** | 唇の縦横比とお手本の差 | ±0.15 未満で良好 |

---

## システム内部の処理フロー

### 1. 録音から結果画面まで

```
【ブラウザ側】
  getUserMedia()                マイク（16kHz）+ カメラ入力
    ↓
  AudioWorkletNode              Float32 サンプルをキャプチャ
    ↓
  encodeAudio()                 WAV ヘッダを付けて Blob に変換
    ↓
  POST /audio                   test.wav として保存 → Julius アライメント

  （録音と同時に唇テスト動画も MediaRecorder で自動録画）
    ↓
  POST /upload_lip_video        唇テスト動画を一時保存

【解析ボタン押下（POST /graph）】
  ↓  音声・唇アップロードが両方完了してから表示（Promise.all）
  estimate_pitch_range()        話者ピッチ範囲を自動推定
  praat_pitch()                 Praat で F0 抽出
  resample_to_10ms()            Julius フレームにリサンプリング
  lab_load() / log_load()       アライメント結果を読み込み
  extract_julius_score()        品質チェック → < -3000 でスキップ
  hz_to_semitone()              Hz → 半音変換（NaN 保持）
  length_arrange()              録音ピッチをネイティブの音素長に時間正規化
  extract_mora_formants()       F1/F2 を 3点平均で抽出
  calc_total_score()            アクセント・長さ・母音スコアを合算
  _extract_mora_lip_openness()  唇動画からモーラ別口形データを抽出
  _build_lip_mora_comparison()  お手本データと直接比較（なければ音韻テーブルで代替）
  save_record()                 history.json に保存
  check_and_update_quests()     クエスト更新
  render_template("line_graph.html")
```

---

### 2. 口形分析の内部フロー

```
【お手本登録（POST /upload_lip_video mode=ref）】
  音声付き WebM をセッション一時ファイルに保存
    ↓
  _webm_to_wav()                FFmpeg / pydub で音声を WAV に変換
    ↓
  _align_lip_ref()
    └ run_alignment_on_file()   Julius を一時ディレクトリで実行（固定パスに依存しない）
    └ lab_load()                モーラのタイムスタンプを取得
    ↓
  _extract_mora_lip_openness()  VideoCapture で全フレームをメモリに読み込み
    各モーラの 30/50/70% 時刻のフレームを取得
    FaceMesh ランドマーク（13=上唇 / 14=下唇 / 61=左端 / 291=右端）から
    v_h_ratio = v_dist / h_dist を計算
    ↓
  lip_refs.json に保存（schema_version: 2）
    {
      "schema_version": 2,
      "vectors": [...],             ← DTW 用ベクトル列（全フレーム）
      "ratios":  [...],             ← v_h_ratio 列（全フレーム）
      "mora_data": [
        {"label": "ka", "v_h_ratio": 0.35},
        {"label": "i",  "v_h_ratio": 0.28},
        ...
      ]
    }

【テスト比較（/graph）】
  _extract_mora_lip_openness()  ユーザー動画から同様に v_h_ratio を取得
    ↓
  _build_lip_mora_comparison()
    - mora_data あり  → 位置基準（mora[i] ↔ ref_mora_data[i]）で直接比較
    - mora_data なし  → _mora_expected_openness() 音韻テーブルで代替
    正規化：user_max = np.percentile(vals, 95)（外れ値ロバスト）
```

---

### 3. Julius 強制アライメント

Julius はひらがな読みと音声から音素のタイムスタンプを生成します。

**入力**：`test.wav`（録音音声）と `test.txt`（ひらがな読み）

**Perl スクリプトが行うこと**：ひらがなを Julius 用の音素列に変換します。

```
びょーいん → by o: i N
```

**出力（.lab ファイルの構造）**：

```
0.0000  0.0750  silB
0.0750  0.1500  by
0.1500  0.2500  o:
0.2500  0.3000  i
0.3000  0.3750  N
0.3750  0.4500  silE
```

**Julius スコアの目安**

| スコア | 状態 |
|--------|------|
| -1000 以上 | アライメント良好 |
| -1000 〜 -3000 | やや不安定 |
| -3000 以下 | 不安定 → スコア計算をスキップ |

**任意ファイルへのアライメント（`run_alignment_on_file()`）**

口形お手本の録音など、固定パス（`test.wav`）以外の任意ファイルに対してアライメントを実行できます。
内部では一時ディレクトリを作成して実行し、終了後に削除します。

---

### 4. ピッチ処理パイプライン

スコア計算用と表示用で意図的に経路を分けています。

```
praat_pitch()  →  Hz 配列（NaN 保持）
  ↓
resample_to_10ms()  →  Julius の 10ms フレームに揃える
  ↓
発話区間だけを切り出し（silB〜silE の外を除去）
  ↓
hz_to_semitone()  →  半音変換（NaN 保持）← pitch_native_raw / pitch_user_raw

  ┌──────────────────────┐   ┌──────────────────────┐
  │   スコア計算用        │   │   表示用（グラフ）    │
  ├──────────────────────┤   ├──────────────────────┤
  │ length_arrange()      │   │ comp()   NaN を補間  │
  │ comp()   NaN を補間  │   │ smooth() window=5    │
  │ smooth() window=3     │   │ scale()  0〜1 に正規化│
  └──────────────────────┘   └──────────────────────┘
```

---

### 5. モーラと音素の違い

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
```

---

### 6. 音色評価（MFCC + DTW）

**MFCC 構成（36次元）**

```
静的 MFCC （12次元）: スペクトル包絡の形状
Δ  MFCC （12次元）: 1階時間微分（変化の速さ）
ΔΔ MFCC （12次元）: 2階時間微分（変化の加速度）
```

DTW で全登録単語との距離を昇順にグラフ表示します。
赤いバーが一番左（距離が最小）なら自分の発音が練習対象に最も近い音色です。

> ⚠️ `.bin` ファイルが旧12次元形式の場合、`DTW 距離 = inf` になります。
> `python scripts/regenerate_mfcc.py` で再生成してください。

---

### 7. クエストシステム

```
check_and_update_quests(score_result, word_id)
  ↓
  1. アクティブなクエストのうち目標スコアを超えたものをクリア
  2. 空きスロット（最大3）を補充
     ① 間隔反復候補（3日以上未練習 かつ スコア85点未満）を最大1つ追加
     ② 残りを弱点軸（スコアが低い順）で埋める
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

---

### 8. 単語登録フロー（vocab.py）

```
register_word(display, reading)
  1. get_accent(display)              MeCab + UniDic でアクセント型を自動取得
  2. generate_sample_wav()            VOICEVOX（話者 ID: 11 / ずんだもん）で音声合成
  3. convert_to_16kHz()               16kHz / モノラル / 16bit PCM に変換
  4. perl_run()                       Julius でアライメント実行
  5. audio_mfcc()                     MFCC + Δ + ΔΔ（36次元）→ {word_id}.bin に保存
  6. words_db.json・audio.scp を更新
```

---

## 主要 API ルート一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/` | 単語選択ページ |
| POST | `/` | 単語を選択して録音ページへ遷移 |
| GET | `/audio` | 録音ページ |
| POST | `/audio` | 録音データを保存・Julius アライメントを実行 |
| GET | `/recorded_audio` | 直前の録音（test.wav）を返す |
| POST | `/graph` | Julius 解析を実行して結果ページを返す |
| GET | `/sample_audio/<word_id>` | ネイティブ音声を返す |
| POST | **`/upload_lip_video`** | **唇動画（ref/test）をアップロード・口形データを抽出** |
| GET | **`/api/lip_refs`** | **口形参照データのキー一覧を返す** |
| GET | **`/api/lip_refs/<word_id>/ratios`** | **指定単語の v_h_ratio 列を返す** |
| POST | **`/api/lip_refs/delete`** | **指定単語の参照データを削除** |
| POST | **`/api/lip_refs/overwrite`** | **指定単語の参照データを上書き（アライメント付き）** |
| GET | `/history` | 練習履歴ページ |
| GET | `/history/export.csv` | 履歴を CSV でダウンロード |
| GET | `/analysis` | 学習曲線の自動分析ページ |
| GET | `/admin` | 管理ページ |
| POST | `/admin/add_word` | 単語追加 API |
| POST | `/admin/update_word` | 単語更新 API |
| POST | `/admin/delete_word` | 単語削除 API |

---

## 既知の限界

| 限界 | 内容 | 状態 |
|------|------|------|
| 参照音声が1本 | 同一話者の VOICEVOX 音声が「唯一の正解」 | 複数話者の平均化で改善予定 |
| 性別補正は集団平均 | 補正係数 0.85 は個人差を完全には吸収できない | 個人単位の VTLN で改善予定 |
| シングルユーザー | `test.wav` が上書きされると比較再生が前の録音になる | セッション ID でファイルを分ける方針 |
| MFA は現状 Julius より精度が低い | SP-PS のパイプライン全体が Julius の音素体系・フレーム番号を前提としているため、MFA に切り替えるとモーラ境界がズレてスコアが低下する。`USE_MFA = False` を推奨 | パイプライン全体の書き直しが必要 |
| 口形分析は MediaPipe 依存 | `pip install mediapipe` がない環境では口形タブが非表示 | オプション機能として維持 |
| 口形お手本は 1 セッション 1 単語 | 複数単語を連続練習すると前の単語の参照データが残る | `lip_refs.json` で単語別に管理済みなので実害は少ない |

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
- 3〜4単語程度で何をするブランチか一目でわかる名前にする

### ブランチの作成からマージまでの流れ

```bash
# 1. main を最新にする
git checkout main && git pull origin main

# 2. ブランチを切る
git checkout -b feature/xxxx

# 3. 作業・コミット
git add <files>
git commit -m "feat: ○○を追加"

# 4. プッシュしてマージ
git push origin feature/xxxx
git checkout main && git merge feature/xxxx && git push origin main
```

### コミットメッセージの書き方

| プレフィックス | 用途 |
|-------------|------|
| `feat:` | 新機能 |
| `fix:` | バグ修正 |
| `refactor:` | リファクタリング |
| `docs:` | ドキュメント変更 |
| `chore:` | ビルドや設定ファイルの変更 |

---

## ライセンス

MIT License

Julius 音響モデル・セグメンテーションキット：MIT License（`engine/License.md` 参照）
