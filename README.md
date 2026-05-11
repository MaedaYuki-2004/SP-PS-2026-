```text
# SP-PS — 日本語単語 音声解析・可視化プラットフォーム

> 「声（口）のかたちを綺麗にする一歩を踏める嬉しさを届ける」

録音した日本語単語の発音をネイティブ音声と比較し、
アクセント・モーラ長・母音の口の形の3軸でスコアを算出してフィードバックを提供するFlaskアプリケーション。

---

## 目次

1. [動作環境](#動作環境)
2. [インストール](#インストール)
3. [設定](#設定)
4. [起動方法](#起動方法)
5. [ディレクトリ構成](#ディレクトリ構成)
6. [スコアロジック](#スコアロジック)
7. [主要な変更点（実装ログ）](#主要な変更点実装ログ)
8. [既知の限界](#既知の限界)

---

## 動作環境

| 項目 | 要件 |
|------|------|
| OS | Ubuntu 22.04 LTS 以降（Julius の動作確認済み） |
| Python | 3.10 以上 |
| Node.js | 18 以上（スライド生成スクリプト使用時のみ） |
| Julius | 4.6 以上 |
| Praat | parselmouth 経由で自動インストール |

---

## インストール

### 1. リポジトリのクローン

```bash
git clone <repository_url>
cd sp-ps
```

### 2. Julius のインストール

Julius は音素アライメント（音声と音素の対応付け）に使用します。

```bash
# Ubuntu / Debian
sudo apt-get update
sudo apt-get install -y julius julius-dev

# バージョン確認
julius --version
```

> **Note:** パッケージ版で動作しない場合はソースからビルドしてください。  
> https://github.com/julius-speech/julius

Julius 用の音響モデルを `config.py` で指定したパスに配置します（後述）。

### 3. Python 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

`requirements.txt` が存在しない場合は以下を手動でインストールしてください。

```bash
pip install flask \
            parselmouth \
            librosa \
            fastdtw \
            scipy \
            numpy \
            pydub \
            soundfile
```

| パッケージ | 用途 |
|-----------|------|
| flask | Webサーバー |
| parselmouth | Praat連携（ピッチ・フォルマント抽出） |
| librosa | 音声読み込み・MFCC抽出 |
| fastdtw | DTW距離計算（音色評価） |
| scipy | 統計計算（Pearson相関など） |
| numpy | 数値計算全般 |
| pydub | 音声フォーマット変換 |
| soundfile | WAVファイル読み書き |

### 4. ffmpeg のインストール（pydub の依存）

```bash
sudo apt-get install -y ffmpeg
```

### 5. MFCCバイナリの生成

Delta MFCC（Δ・ΔΔ）を含む36次元に変更したため、
既存の `.bin` ファイルがある場合は再生成が必要です。

```bash
python scripts/regenerate_mfcc.py
```

### 6. requirements.txt の生成（初回のみ）

```bash
pip freeze > requirements.txt
```

最低限必要なパッケージのバージョン例：

```
Flask>=2.3.0
numpy>=1.24.0
scipy>=1.10.0
librosa>=0.10.0
parselmouth>=0.4.3
fastdtw>=0.3.4
pydub>=0.25.1
soundfile>=0.12.1
```

---

## 設定

`config.py` を環境に合わせて編集してください。

```python
# config.py の主要な設定項目

# Julius 音響モデルのパス
JULIUS_MODEL_PATH = "/path/to/hmmdefs_monof_mix16_gid.binhmm"
JULIUS_DICT_PATH  = "/path/to/dict"

# 音声ファイルの保存先
AUDIO_WAV_DIR     = Path("data/wav")
AUDIO_MFCC_DIR    = Path("data/mfcc")
RAW_AUDIO_DIR     = Path("data/raw")

# Flask
FLASK_SECRET_KEY  = "your-secret-key-here"

# Praat ピッチ検出のデフォルト範囲（自動推定が失敗した場合のフォールバック）
PITCH_FLOOR_DEFAULT   = 75.0   # Hz
PITCH_CEILING_DEFAULT = 600.0  # Hz
```

### 単語データベースの準備

`words_db.json` に評価対象の単語を登録します。

```json
{
  "word_001": {
    "display": "東京",
    "reading": "とうきょう",
    "accent": 0
  },
  "word_002": {
    "display": "日本語",
    "reading": "にほんご",
    "accent": 0
  }
}
```

`accent` の値はアクセント型（0=平板型、1=頭高型、N=N型）を指定します。

### ネイティブ音声の配置

各単語のネイティブ音声（16kHz・モノラル WAV）を以下の場所に配置します。

```
data/raw/sound/{word_id}/{word_id}.wav
```

Julius のアライメント結果（`.lab`・`.log`）も同じディレクトリに配置してください。

### 新しい単語の追加方法

1. 管理画面 `http://127.0.0.1:5000/admin` にアクセスする
2. 「単語を追加」から表示テキスト・よみがな・アクセント型を入力する
3. ネイティブ音声（16kHz WAV）を `data/raw/sound/{word_id}/` に配置する
4. Julius でアライメントを実行して `.lab` `.log` を生成する
5. MFCC を生成する（初回のみ）：
   ```bash
   python scripts/regenerate_mfcc.py --word_id {word_id}
   ```

---

## 起動方法

```bash
python app.py
```

デフォルトで `http://127.0.0.1:5000` にアクセスできます。

### 使い方の流れ

1. トップページで評価する単語を選択する
2. マイクに近づいて単語を発音・録音する
3. 解析結果ページでスコアとフィードバックを確認する
4. ピッチ比較グラフ・長さ比較グラフも参照できる

---

## ディレクトリ構成

```
sp-ps/
├── app.py                        # Flaskルーティング
├── config.py                     # パス定数・グローバル設定
├── requirements.txt
├── core/
│   ├── alignment.py              # Julius実行・lab/log読み込み・品質スコア抽出
│   ├── audio.py                  # 音声変換・セグメント切り出し
│   ├── evaluate.py               # スコア算出（アクセント・長さ・総合）
│   ├── formant.py                # F1/F2フォルマント抽出・母音品質評価・声質評価
│   ├── pitch.py                  # F0抽出・補間・正規化・hz_to_semitone 等
│   ├── timbre.py                 # MFCC+Delta・DTW音色評価
│   ├── utils.py                  # 汎用ユーティリティ
│   └── vocab.py                  # words_db.json 管理
├── data/
│   ├── wav/                      # 録音音声の一時保存
│   ├── mfcc/                     # MFCCバイナリキャッシュ
│   └── raw/sound/{word_id}/      # ネイティブ音声・lab/logファイル
├── scripts/
│   └── regenerate_mfcc.py        # MFCCバイナリ再生成スクリプト
└── web/
    ├── static/
    │   └── css/base.css
    └── templates/
        ├── audio.html
        ├── line_graph.html       # 解析結果表示
        ├── select.html
        ├── upload.html
        └── admin.html
```

---

## スコアロジック

### 全体フロー

```
録音音声
  ↓
Julius アライメント（音素と波形の対応付け）
  ↓
品質チェック：Julius スコア < -3000 → スキップ・再録音を促す
  ↓（品質 OK）
  ├─ ピッチ抽出（Hz → 半音変換）→ アクセントスコア（50点）
  ├─ モーラ長の割合を比較         → 長さスコア（30点）
  └─ F1/F2フォルマント抽出       → 母音品質スコア（20点）
  ↓
合計（100点満点）→ グレード判定（S / A / B / C / D）
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

4指標の加重平均で内部スコア（0〜60）を算出し、50点に正規化します。

```
内部スコア（0〜60） =
  核位置スコア  × 0.40   ← 日本語アクセントで最重要
+ ピッチ相関    × 0.35   ← Pearson相関（有声フレームのみ）
+ H/L一致率    × 0.15   ← 各モーラの高低の正確さ
+ 安定度スコア  × 0.10   ← モーラ内ピッチの安定さ

アクセントスコア = 内部スコア × 50 / 60
```

**ピッチ相関（Pearson相関係数）**

```
r = Σ(xi − x̄)(yi − ȳ) / (n × σx × σy)

r = +1.0：ネイティブと同じタイミングで上下（完全一致）
r =  0.0：無相関
r = -1.0：完全に逆パターン
```

ネイティブと録音の「両方が有声（NaNでない）」フレームのみを使用して計算します。
無声区間の補間値がノイズとして混入しないようにするためです。

**H/L分類**

期待パターンの L 比率に合わせたパーセンタイルで閾値を設定します。

```
threshold = percentile(mora_pitches, L比率 × 100)
```

例：平板型（L-H-H-H）→ L比率25% → 25パーセンタイルを閾値に使用

**アクセント核の検出（動的閾値）**

```
DROP_THRESHOLD = max(0.05, min(0.25, ピッチ範囲 × 0.15))
```

---

### 長さスコア（0〜30点）

モーラ長の割合（全体に占める%）をネイティブと比較し、重み付き誤差でスコア化します。

```
重み：長音・促音 → 2.0倍 ／ 撥音 → 1.5倍 ／ 通常 → 1.0倍
長さスコア = 内部スコア（0〜40） × 30 / 40
```

---

### 母音品質スコア（0〜20点）

F1・F2フォルマントをBarkスケールで比較してスコア化します。

```
# Hz → Bark 変換（知覚的均等スケール）
Bark = 26.81 × F / (1960 + F) − 0.53

# 正規化距離
dist = √( (ΔBark_F1 / 3.0)² + (ΔBark_F2 / 4.0)² )

# 指数減衰スコア（0点にならない設計）
score = 20 × exp(−1.2 × dist)
```

Barkスケールを使う理由：HzはF1（低周波）とF2（高周波）で知覚的な重みが異なるため、
Hzのままでは「聞こえ方の差」を正しく評価できません。

---

### Julius 品質ゲート

```
Julius スコア（対数尤度） < -3000
  → スコア計算を完全にスキップ
  → 再録音ガイドを表示（マイクに近づく・はっきり発音・静かな環境）
```

アライメントが失敗した状態でスコアを出しても意味がないため、
信頼できる改善点だけを表示する仕組みになっています。

---

### 追加フィードバック（スコア非影響）

以下はスコアには含まれず、フィードバックとして表示します。

| 指標 | 内容 | しきい値 |
|------|------|---------|
| 発話速度 | モーラ/秒でネイティブと比較 | ネイティブ比 0.70〜1.30倍の範囲が適切 |
| ジッター | F0の変動率（声のピッチの安定さ） | > 3% で警告 |
| シマー | 振幅の変動率（声の音量の安定さ） | > 8% で警告 |
| 有声フレーム比率 | モーラ内で声が出ているフレームの割合 | < 45% で警告 |

**発話速度の計算式**

```
発話速度（モーラ/秒） = モーラ数 ÷ 発話区間の長さ（秒）

ネイティブ比率 = 録音の速度 ÷ ネイティブの速度
  0.70 未満 → 「遅すぎます」
  0.70〜0.85 → 「少し遅めです」
  0.85〜1.15 → 適切（フィードバックなし）
  1.15〜1.30 → 「少し速めです」
  1.30 超   → 「速すぎます」
```

---

## 主要な変更点（実装ログ）

### スコア構成の変更

| | 変更前 | 変更後 |
|--|--------|--------|
| アクセント | 60点 | 50点 |
| 長さ | 40点 | 30点 |
| 母音品質 | なし | **20点（新規）** |
| 合計 | 100点 | 100点 |

---

### ピッチ評価の改善

| 変更項目 | 変更前 | 変更後 |
|---------|--------|--------|
| 単位変換 | Hz（絶対値） | 半音（semitone）（話者差を吸収） |
| 比較手法 | DTW（全フレーム） | Pearson相関（有声フレームのみ） |
| スムージング（スコア用） | window=5 | window=3（境界を鮮明に） |
| スムージング（表示用） | window=5 | window=5（視認性優先・変更なし） |
| H/L閾値 | 中央値（固定50パーセンタイル） | L比率ベースのパーセンタイル |
| 核検出閾値 | 固定値 0.08 | 動的（ピッチ範囲の15%） |
| 安定度閾値 | variance_threshold=0.05（バグ） | variance_threshold=1.5（半音スケール適切値） |
| 正規化 | normalize_zscore() | scale()（二重正規化を解消） |

---

### 母音品質評価の新設（`core/formant.py`）

- parselmouth（Praat）でF1・F2フォルマントを各モーラの中心時刻で抽出
- Hz→Barkスケール変換（知覚的均等スケール）
- 指数減衰スコア（距離がいくら大きくても0点にならない）
- `max_formant` は話者のピッチ上限から自動判定（女性→5500Hz、男性→5000Hz）

---

### Julius 品質ゲートの追加

- `extract_julius_score()` で対数尤度を取得（`core/alignment.py`）
- `-3000` 以下の場合はスコア計算をスキップし、再録音ガイドを表示
- ピッチグラフは引き続き表示（アライメントと独立して計算されるため）

---

### Delta MFCC の導入（`core/timbre.py`）

静的MFCC(12次元) + Δ(12次元) + ΔΔ(12次元) = **36次元** に変更。

> ⚠️ 既存の `.bin` ファイルは非互換です。以下のコマンドで再生成してください。
> ```bash
> python scripts/regenerate_mfcc.py
> ```

---

### ピッチパイプラインの分離

スコア計算用と表示用でパイプラインを明確に分けています。

```python
# スコア計算用（scale不要・Pearson相関のため）
pitch_native_raw = pitch1_sil_semi.copy()  # NaN保持（ネイティブ）
pitch_user_raw   = pitch3_semi.copy()      # NaN保持（録音）
pitch_fin_score  = smooth(comp(pitch1_sil_semi), window=3)  # H/L・核検出用
pitch_fin2_score = smooth(comp(pitch3_semi),     window=3)

# 表示用（グラフ描画）
pitch_fin_disp  = scale(smooth(comp(pitch1_sil_semi), window=5))
pitch_fin2_disp = scale(smooth(comp(pitch3_semi),     window=5))
```

`pitch_native_raw` / `pitch_user_raw` を別途保持するのは、
Pearson相関を有声フレームのみで計算するために NaN を残す必要があるためです。
`comp()` を通すと無声区間が補間されてしまい、
実際には声が出ていないフレームの人工値が相関計算に混入します。

---

### 話者ピッチ範囲の自動推定（`core/pitch.py`）

変更前は Praat のデフォルト値（75〜500Hz）を使っていたため、
男性話者や子どもの音声でピッチ推定が不安定になることがありました。

```python
def estimate_pitch_range(sound_file, percentile_low=10.0, percentile_high=90.0,
                         margin_low=0.75, margin_high=1.50) -> tuple[float, float]:
    """
    音声ファイルから話者のピッチ範囲を自動推定する。

    1. まず広い範囲（50〜700Hz）でピッチを大まかに検出
    2. 有声フレームの 10〜90 パーセンタイルを取得
    3. マージンを掛けて floor / ceiling を決定

    例：中央値 150Hz（女性）→ floor=84Hz, ceiling=270Hz 程度
        中央値 110Hz（男性）→ floor=62Hz, ceiling=198Hz 程度
    """
```

`app.py` では `estimate_pitch_range()` をネイティブ・録音それぞれに実行し、
その結果を `praat_pitch()` の `pitch_floor` / `pitch_ceiling` に渡しています。

---

### normalize_zscore → scale への変更

| | 変更前 | 変更後 |
|--|--------|--------|
| 正規化関数 | `normalize_zscore()` | `scale()`（Min-Max正規化） |
| 変更理由 | `hz_to_semitone()` で半音変換済みのデータは対数変換されており外れ値の影響が元々小さい。Z-score をさらに重ねると「抑揚の大きさ」という情報まで失われる二重正規化になっていた | Min-Max 正規化で十分 |

`normalize_zscore()` は `core/pitch.py` に残してあるため、必要に応じて切り替え可能です。

---

### 母音品質スコアの修正（スケール・スコア式）

初期実装では以下の問題がありました。

**問題①：f1_scale / f2_scale が厳しすぎた**

```
変更前: f1_scale=200Hz, f2_scale=400Hz
変更後: f1_scale=400Hz, f2_scale=700Hz（※Barkスケール移行前の中間段階）
```

ネイティブ（女性TTS）とユーザー（男性）の組み合わせでは
F1 が 150Hz ずれることは普通にあり、変更前のスケールでは常に 0 点になっていた。

**問題②：線形スコア式が 0 点に崩壊する**

```
変更前: score = max(0, 20 × (1 - dist))  → dist > 1.0 で必ず 0 点
変更後: score = 20 × exp(-1.2 × dist)   → 指数減衰、0 点にならない
```

**最終版：Hz スケール → Bark スケールへ移行**

Hz はF1（低周波）とF2（高周波）で知覚的重みが異なるため、
人間の聴覚特性に基づく Bark スケールに変換して比較します。

```
Bark = 26.81 × F / (1960 + F) − 0.53
dist = √( (ΔBark_F1 / 3.0)² + (ΔBark_F2 / 4.0)² )
score = 20 × exp(-1.2 × dist)
```

---

### アクセントスコアの重み再設計

| 指標 | 変更前の重み | 変更後の重み | 変更理由 |
|------|------------|------------|---------|
| H/L パターン一致率 | 35% → 0.35 | **15%** | Pearson相関で代替できる情報 |
| アクセント核位置 | 25% → 0.25 | **40%** | 日本語アクセントで最重要 |
| ピッチ比較 | DTW 25% | Pearson相関 **35%** | タイミングの一致を直接評価 |
| モーラ内安定度 | 15% → 0.15 | **10%** | 補助的な指標として位置付け |

DTW からピアソン相関に変えた理由：
- DTW は「ピッチ値の近さ（距離）」を測る
- 日本語アクセントの本質は「いつ上がって・いつ下がるか（タイミング）」
- Pearson相関は上下タイミングの一致度を直接測れる
- Pearson相関は線形変換不変のため `scale()` 不要（変換の連鎖が減る）

---

## 既知の限界

| 限界 | 内容 | 今後の対策 |
|------|------|-----------|
| 参照音声が1本 | 話者の個人差がそのまま「正解」になる | 同じ単語を複数話者で録音して平均化 |
| パラメータが推測値 | 重み・閾値を正解データで検証できていない | 評価付き録音を20〜30例収集してチューニング |
| Julius の精度 | 学習者音声に対するアライメント精度が低い | Montreal Forced Aligner（MFA）への移行 |
| クエスト提案 | 現時点で未実装 | 精度向上したスコアを根拠に将来実装予定 |

---

## ライセンス

MIT License