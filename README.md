# 試作システム

日本語単語の音声入力（録音/アップロード）を行い、音素/モーラやピッチ、音色の比較結果を可視化するFlaskアプリです。  
単語選択 → 録音/アップロード → 解析結果表示の流れで利用します。

## 動作環境

- Python 3.9+（推奨）
- macOS想定（`brew`で依存を導入）
- ffmpeg / julius / perl

## 依存ライブラリ

`ライブラリリスト.txt` の内容に沿ってインストールします。

```bash
pip install flask librosa praat-parselmouth pydub noisereduce fastdtw click gunicorn
brew install ffmpeg julius
```

## ローカルの起動方法

```bash
python server.py
```

起動後にブラウザで `http://127.0.0.1:5000/` にアクセスします。

## 画面と機能

- 単語選択: `select.html` を表示
- 録音: `audio.html` から録音 → 解析
- アップロード: `upload.html` から音声ファイルを送信 → 解析
- 結果表示: `line_graph.html` でピッチ/長さ/音色の可視化

## ディレクトリ構成

- `server.py` Flaskアプリ本体
- `templates/` 画面テンプレート（select/upload/audio/line_graph）
- `static/` CSS/JS/画像/単語リストなど
- `audio/` 音素アライメント関連（Juliusセグメンテーション）
  - `audio/segment_julius.pl` を実行して `test.wav` をアライメント
  - 詳細は `audio/README.md` を参照

## 入力ファイルについて

- アップロード/録音は `audio/wav/test.wav` に保存されます。
- サンプル音声や比較用データは `audio/sound/` と `audio/mfcc/` を参照します。
- 単語リストは `static/words.txt` を利用します。

## メモ

- `server.py` 内の `app.secret_key` は必要に応じて変更してください。
- `julius` の動作やモデル配置は `audio/README.md` に従ってください。
