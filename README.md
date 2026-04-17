```text
【試作システム：日本語単語 音声解析・可視化プラットフォーム】

【システム概要】
・日本語単語の音声入力（録音およびアップロード）を行い、音素アライメント、ピッチ（基本周波数）、長さ、音色の比較結果を可視化するFlaskアプリケーションです。
・システムの中枢となるプログラム、画面表示（Web）、解析データ、音声認識エンジンを役割ごとに完全に分離し、研究・開発における保守性と拡張性を高めたフォルダ構造を採用しています。

【主要機能】
・単語選択：画面から解析対象の単語（30単語）を選択します。
・音声入力：ブラウザ上でのリアルタイム録音、またはWAVファイルのアップロードに対応しています。
・自動音素アライメント：Juliusを用いて、音声波形と音素ラベルを自動で対応付けます。
・解析結果の可視化：ネイティブ音声との比較（ピッチ曲線、音素長、DTWによる音色類似度グラフ）を画面に表示します。

【フォルダ・ファイル構造】
sp-ps/
├── app.py              システム起動およびWeb・解析処理のメインプログラム
├── requirements.txt    必要なPythonライブラリ一覧
├── README.md           本ファイル（システム概要と実行手順）
├── docs/               仕様書や手順書の保管場所
│   ├── 実行方法.pdf
│   └── explanation.txt
├── web/                フロントエンド（画面表示）関連
│   ├── templates/      画面のHTMLファイル群（select.html, upload.html, audio.html, line_graph.html）
│   └── static/         CSS, JS, 画像, および単語選択画面のサンプル音声の格納先
├── data/               システムが読み書きするデータ・音声ファイル
│   ├── config/         システム設定・リスト（audio.scp, words.txt, word_id.txtなど）
│   ├── raw_audio/      基準音声・テストデータの保管先（sound/, wav/）
│   └── mfcc/           音色評価に使う基準音声のバイナリファイル
├── engine/             音声認識のコアシステム
│   ├── bin/            Julius実行ファイル（julius-4.3.1.exeなど）
│   └── models/         音響モデル・トライフォン定義ファイル
└── scripts/            外部処理用スクリプト
    └── segment_julius.pl 音素アライメントを実行するPerlスクリプト

【動作環境と依存ライブラリ】
・Python 3.9以上（推奨）
・外部プログラム：FFmpeg, Julius, Perl（Windowsの場合はStrawberry Perl等のインストールが必要です）
・必要なPythonライブラリは requirements.txt に記載の通りです（flask, librosa, praat-parselmouth, pydub, noisereduce, fastdtw, click, gunicorn）。

【環境構築・実行手順】
外部ツールの準備
　macOSの場合はHomebrewを利用してffmpegとjuliusをインストールし（brew install ffmpeg、brew install julius）、パスを通してください。Windowsの場合は公式サイト等からインストールします。Perlも実行可能な状態にしてください。

Pythonライブラリのインストール
　ターミナル（またはコマンドプロンプト）でプロジェクトのルート階層に移動し、以下のコマンドを実行して必要なモジュールを一括インストールします。
　pip install -r requirements.txt

システムの起動
　同じくプロジェクトのルート階層で以下のコマンドを実行します。
　python app.py

ブラウザでのアクセス
　起動後、画面にURLが表示されたら、ブラウザで http://127.0.0.1:5000/ にアクセスしてシステムを利用します。終了する場合はターミナル上で「CTRL+C」を押します。

【注意事項】
・データパスの管理：data/config/audio.scp 内のパスは、必ず data/raw_audio/ を基準としたフォルダ構成（例：sound/word1/word1.wav）で記述してください。
・アライメント実行：録音後の解析には scripts/segment_julius.pl が正しく動作する環境が必須です。
