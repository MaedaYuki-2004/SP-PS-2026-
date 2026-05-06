```text
【試作システム：日本語単語 音声解析・可視化プラットフォーム】

【システム概要】
・日本語単語の音声入力（録音およびアップロード）を行い、音素アライメント、ピッチ（基本周波数）、長さ、音色の比較結果を可視化するFlaskアプリケーションです。
・録音した音声をネイティブ音声と比較し、アクセントスコア（60点）と長さスコア（40点）からなる100点満点の発音スコアとフィードバックを表示します。
・単語管理画面からVOICEVOXを使って任意の単語を追加でき、30単語の固定リストに縛られずに運用できます。
・システムの中枢となるプログラム、画面表示（Web）、解析データ、音声認識エンジンを役割ごとに完全に分離し、研究・開発における保守性と拡張性を高めたフォルダ構造を採用しています。

【主要機能】
・単語選択：画面から練習対象の単語を選択します（words_db.jsonで動的管理）。
・単語登録：管理画面（/admin）から任意の単語を追加・編集・削除できます。
・音声入力：ブラウザ上でのリアルタイム録音、またはWAVファイルのアップロードに対応しています。
・自動音素アライメント：Juliusを用いて、音声波形と音素ラベルを自動で対応付けます。
・解析結果の可視化：ネイティブ音声との比較（ピッチ曲線、音素長、DTWによる音色類似度グラフ）を画面に表示します。
・発音スコア：アクセント型との一致率とモーラ長の差異から100点満点のスコアとフィードバックを算出します。
・サンプル音声の自動生成：VOICEVOXを使って新規登録単語のサンプル音声を自動生成します。

【フォルダ・ファイル構造】
sp-ps/
├── app.py              Flaskルーティングのみ（ビジネスロジックは core/ に委譲）
├── config.py           パス定数・WORDS辞書・音素定数・Julius自動検出を一元管理
├── requirements.txt    必要なPythonライブラリ一覧
├── README.md           本ファイル（システム概要と実行手順）
├── core/               解析ロジック層（音声・アライメント・ピッチ・音色・評価・単語管理）
│   ├── __init__.py
│   ├── accent.py       MeCabによるアクセント型取得・VOICEVOX音声生成
│   ├── audio.py        音声変換・フォーマット正規化・セグメント切り出し
│   ├── alignment.py    Julius実行・lab/logファイルの読み込み・モーラ分割
│   ├── evaluate.py     発音スコア算出（アクセントスコア・長さスコア）
│   ├── pitch.py        F0抽出・NaN補間・スムージング・正規化・長さ整合・10msリサンプリング
│   ├── timbre.py       MFCC抽出・DTW距離計算・音色評価
│   ├── utils.py        汎用ヘルパー（割合計算・フレーム変換など）
│   └── vocab.py        単語データベース管理（登録・削除・編集）
├── docs/               仕様書や手順書の保管場所
│   ├── 実行方法.pdf
│   └── explanation.txt
├── web/                フロントエンド（画面表示）関連
│   ├── templates/      HTMLファイル群
│   │   ├── select.html      単語選択画面（words_db.jsonから動的生成）
│   │   ├── upload.html      ファイルアップロード画面（words_db.jsonから動的生成）
│   │   ├── audio.html       録音画面
│   │   ├── line_graph.html  解析結果・発音スコア表示画面
│   │   └── admin.html       単語管理画面（登録・編集・削除）
│   └── static/         CSS, JS, 画像, サンプル音声の格納先
│       ├── css/         各画面のスタイルシート
│       ├── js/          JavaScriptファイル（録音・波形描画）
│       ├── image/       単語イラスト画像
│       ├── sample/      既存の録音済みサンプル音声（word1.wav〜word30.wav）
│       └── tts/         VOICEVOX自動生成サンプル音声（新規登録単語）
├── data/               システムが読み書きするデータ・音声ファイル
│   ├── config/         システム設定・リスト
│   │   ├── words_db.json    単語データベース（display・reading・accent等を管理）
│   │   ├── audio.scp        基準音声ファイルのパス一覧（自動更新）
│   │   ├── words.txt        DTW評価用の単語表記リスト（自動更新）
│   │   └── word_id.txt      直前に選択された単語IDの記録
│   ├── raw_audio/      基準音声・テストデータの保管先
│   │   ├── sound/      基準音声（wav・lab・logファイル）
│   │   └── wav/        録音・テスト用の一時ファイル（test.wav・test.lab等）
│   └── mfcc/           音色評価に使う基準音声のバイナリファイル（word1.bin〜）
├── engine/             音声認識のコアシステム（Julius本体・音響モデル）
│   ├── bin/            Julius実行ファイル（julius-4.3.1.exe など）
│   └── models/         音響モデル・トライフォン定義ファイル
└── scripts/            外部処理用スクリプト
    ├── segment_julius.pl    音素アライメントを実行するPerlスクリプト
    └── regenerate_mfcc.py   MFCCバイナリを再生成するスクリプト

【動作環境と依存ライブラリ】
・Python 3.9以上（推奨）
・外部プログラム：FFmpeg、Julius、Perl、VOICEVOX
・必要なPythonライブラリは requirements.txt に記載の通りです。

requirements.txt に記載の主要ライブラリ：
  flask, librosa, praat-parselmouth, pydub, noisereduce, fastdtw,
  click, gunicorn, mecab-python3, unidic, scipy, numpy, matplotlib

【インストールが必要な外部ツール一覧】

■ Strawberry Perl（Windows）
  URL：https://strawberryperl.com/
  ・segment_julius.pl の実行に必要です。
  ・インストール後にPCを再起動してください。
  ・macOS の場合は標準でインストール済みのため不要です。

■ FFmpeg
  Windows：https://ffmpeg.org/download.html
  macOS  ：brew install ffmpeg

■ Julius
  Windows：engine/bin/julius-4.3.1.exe を配置済みのため追加インストール不要。
  macOS  ：brew install julius
  ・config.py の _detect_julius() が自動でパスを検出します。
  ・自動検出に失敗する場合は環境変数 JULIUS_BIN にパスを設定してください。
    例：set JULIUS_BIN=C:\path\to\julius.exe（Windows）
    例：export JULIUS_BIN=/opt/homebrew/bin/julius（macOS Apple Silicon）

■ VOICEVOX（任意・単語の自動追加に必要）
  URL：https://voicevox.hiroshiba.jp/
  ・新しい単語を管理画面から追加する際に必要です。
  ・インストール後に起動してから app.py を実行してください。
  ・VOICEVOX が起動していない場合、既存の30単語は通常通り使えます。

【環境構築・実行手順】

1. 外部ツールの準備
   上記「インストールが必要な外部ツール一覧」を参照してインストールしてください。

2. Pythonライブラリのインストール
   ターミナル（またはコマンドプロンプト）でプロジェクトのルート階層に移動し、
   以下のコマンドを実行して必要なモジュールを一括インストールします。
   pip install -r requirements.txt

3. UniDic辞書のダウンロード（MeCab用・初回のみ）
   pip install unidic
   python -m unidic download
   ・約500MBのダウンロードが発生します。

4. MFCCバイナリの再生成（初回のみ・または音声を差し替えた場合）
   python scripts/regenerate_mfcc.py
   ・基準音声（data/raw_audio/sound/）のMFCCを計算してdata/mfcc/に保存します。
   ・DTW音色評価の精度に直結するため、必ず実行してください。

5. システムの起動
   VOICEVOX を起動してから、以下のコマンドを実行します。
   python app.py

6. ブラウザでのアクセス
   起動後、ブラウザで http://127.0.0.1:5000/ にアクセスします。
   単語管理画面は http://127.0.0.1:5000/admin です。
   終了する場合はターミナル上で「CTRL+C」を押します。

【単語を追加する手順】
1. VOICEVOXを起動する。
2. ブラウザで http://127.0.0.1:5000/admin を開く。
3. 「表示テキスト」と「ひらがな読み」を入力して「登録する」ボタンを押す。
4. 20〜30秒でサンプル音声の生成・アライメント・MFCCの計算が完了する。
5. 単語選択画面に自動で追加される。

【注意事項】
・プロジェクトフォルダのパスに日本語・スペース・ハイフンが含まれると
  Julius が正常に動作しない場合があります。
  例：C:\sp-ps\ のようなシンプルなパスに配置してください。
・データパスの管理：data/config/audio.scp は単語登録・削除時に自動更新されます。
  手動で編集する場合は data/raw_audio/ を基準としたパスで記述してください。
・アライメント実行：録音後の解析には scripts/segment_julius.pl が
  正しく動作する環境が必須です。
・VOICEVOX のスピーカーIDは core/accent.py の VOICEVOX_SPEAKER で変更できます
  （デフォルト：1 = ずんだもん）。