FROM python:3.12-slim

# ── システムパッケージ ────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    mecab \
    libmecab-dev \
    mecab-ipadic-utf8 \
    ffmpeg \
    julius \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python 依存 ───────────────────────────────────────────────────────
# requirements.txt を先にコピーしてレイヤーキャッシュを活用
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# unidic 辞書をビルド時にダウンロード（起動のたびに落とさない）
RUN python -m unidic download

# ── アプリコード ──────────────────────────────────────────────────────
COPY . .

# 必要ディレクトリを事前作成（Volume マウント前でも起動できるように）
RUN mkdir -p \
    data/config \
    data/raw_audio/wav \
    data/raw_audio/sound \
    data/mfcc \
    web/static/sample \
    web/static/distance_result

EXPOSE 8080

# Railway は PORT 環境変数を自動でセットする
# shell 形式にして ${PORT} を展開できるようにする
CMD gunicorn app:app --bind "0.0.0.0:${PORT:-8080}" --workers 1 --timeout 120
