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
・app.py：システムが起動するメインプログラムです（旧：sarver.pyから移行されました）。
・requirements.txt：必要なPythonライブラリの一覧です。
・README.md：本ファイル（システム概要と実行手順）です。
・webフォルダ：フロントエンド（画面表示）関連のファイルを格納します。
　・templatesフォルダ：各画面のHTMLファイル群（select.html, upload.html, audio.html, line_graph.html）を格納します。
　・staticフォルダ：CSS、JavaScript（js）、画像のほか、単語選択画面のサンプル音声（sample）などを格納します。
・dataフォルダ：システムが読み書きするデータや音声ファイルを格納します。
　・configフォルダ：システム設定やリスト（audio.scp, words.txt, word_id.txtなど）を格納します。
　・raw_audioフォルダ：基準音声やテストデータの保管先です（sound/, wav/）。
　・mfccフォルダ：音色評価に使う基準音声のバイナリファイルを格納します。
・engineフォルダ：音声認識のコアシステムです。
　・binフォルダ：Julius実行ファイル（julius-4.3.1.exeなど）を格納します。
　・modelsフォルダ：音響モデル・トライフォン定義ファイルを格納します。
・scriptsフォルダ：外部処理用スクリプト（segment_julius.plなど）を格納します。
・docsフォルダ：仕様書や手順書の保管場所です。

【動作環境と依存ライブラリ】
・Python 3.9以上（推奨）
・外部プログラム：FFmpeg, Julius, Perl（Windowsの場合はStrawberry Perl等のインストールが必要です）
・必要なPythonライブラリは requirements.txt に記載の通りです（flask, librosa, praat-parselmouth, pydub, noisereduce, fastdtw, click, gunicorn）。

【環境構築・実行手順】

・手順1：外部ツールの準備
　macOSの場合はHomebrewを利用してffmpegとjuliusをインストールし（brew install ffmpeg、brew install julius）、パスを通してください。Windowsの場合は公式サイト等からインストールします。Perlも実行可能な状態にしてください。

・手順2：Pythonライブラリのインストール
　ターミナル（またはコマンドプロンプト）でプロジェクトのルート階層に移動し、以下のコマンドを実行して必要なモジュールを一括インストールします。
　pip install -r requirements.txt

・手順3：システムの起動
　同じくプロジェクトのルート階層で以下のコマンドを実行します。
　python app.py

・手順4：ブラウザでのアクセス
　起動後、画面にURLが表示されたら、ブラウザで http://127.0.0.1:5000/ にアクセスしてシステムを利用します。終了する場合はターミナル上で「CTRL+C」を押します。

【注意事項】
・データパスの管理：data/config/audio.scp 内のパスは、必ず data/raw_audio/ を基準としたフォルダ構成（例：sound/word1/word1.wav）で記述してください。
・アライメント実行：録音後の解析には scripts/segment_julius.pl が正しく動作する環境が必須です。
