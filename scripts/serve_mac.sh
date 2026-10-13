#!/bin/bash
# Mac でアプリ（gunicorn）と Cloudflare Tunnel をまとめて起動する。
#
#   scripts/serve_mac.sh local          公開せずに Mac の中だけで起動する（動作確認用）
#   scripts/serve_mac.sh quick          Quick Tunnel（https://….trycloudflare.com）で公開する
#   scripts/serve_mac.sh named <名前>   独自ドメインの名前付き Tunnel で公開する
#
# Ctrl+C で gunicorn・cloudflared・スリープ防止をまとめて止める。
# 環境変数: PORT（既定 5000）、PUBLIC_BASE_URL（named で QR を作るときの公開 URL）
set -u

MODE="${1:-}"
TUNNEL_NAME="${2:-${TUNNEL_NAME:-}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-5000}"
LOG_DIR="$ROOT/.tmp/serve"

case "$MODE" in
    local|quick) ;;
    named)
        if [ -z "$TUNNEL_NAME" ]; then
            echo "named には Tunnel の名前が必要です: scripts/serve_mac.sh named sp-ps" >&2
            exit 1
        fi ;;
    *)
        sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
        exit 1 ;;
esac

cd "$ROOT" || exit 1
if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
else
    PY="$(command -v python3)"
fi
if ! "$PY" -c "import gunicorn" 2>/dev/null; then
    echo "gunicorn が見つかりません。仮想環境で pip install -r requirements.txt を実行してください。" >&2
    exit 1
fi
if [ "$MODE" != local ] && ! command -v cloudflared >/dev/null; then
    echo "cloudflared が見つかりません。brew install cloudflared を実行してください。" >&2
    exit 1
fi
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ポート $PORT は使用中です。前回のサーバーが残っていないか確認するか、PORT=5001 のように変えてください。" >&2
    exit 1
fi
mkdir -p "$LOG_DIR"

PIDS=""
cleanup() {
    trap - EXIT INT TERM
    echo
    echo "停止しています…"
    for pid in $PIDS; do kill "$pid" 2>/dev/null; done
    wait 2>/dev/null
    echo "停止しました。"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# 先生用パスワードの試行回数などをプロセス内に持つため、ワーカーとスレッドは1つにする（README §14）。
# 127.0.0.1 で待ち受け、外からは Tunnel 経由でしか届かないようにする。
TRUSTED_PROXY=cloudflare FLASK_ENV=production \
    "$PY" -m gunicorn app:app --bind "127.0.0.1:$PORT" \
    --workers 1 --threads 1 --timeout 300 \
    >>"$LOG_DIR/gunicorn.log" 2>&1 &
APP_PID=$!
PIDS="$APP_PID"

echo "アプリを起動しています（ログ: .tmp/serve/gunicorn.log）…"
READY=""
for _ in $(seq 1 60); do
    kill -0 "$APP_PID" 2>/dev/null || break
    if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/login")" = 200 ]; then
        READY=1
        break
    fi
    sleep 1
done
if [ -z "$READY" ]; then
    echo "アプリが起動できませんでした。ログの最後:" >&2
    tail -20 "$LOG_DIR/gunicorn.log" >&2
    exit 1
fi
echo "アプリ: http://127.0.0.1:$PORT"

# アプリが動いている間は Mac をスリープさせない（電源接続時。ふたを閉じると止まる機種がある）
caffeinate -ims -w "$APP_PID" &
PIDS="$PIDS $!"

make_qr() {
    PUBLIC_BASE_URL="$1" "$PY" scripts/make_access_qr.py --output "$2" | sed 's/^/  /'
}

case "$MODE" in
    quick)
        TUNNEL_LOG="$LOG_DIR/cloudflared-quick.log"
        : >"$TUNNEL_LOG"
        cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >>"$TUNNEL_LOG" 2>&1 &
        PIDS="$PIDS $!"
        echo "Quick Tunnel を準備しています…"
        URL=""
        for _ in $(seq 1 30); do
            URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" | head -1)"
            [ -n "$URL" ] && break
            sleep 1
        done
        if [ -z "$URL" ]; then
            echo "公開 URL を取得できませんでした。ログの最後:" >&2
            tail -20 "$TUNNEL_LOG" >&2
            exit 1
        fi
        echo "公開 URL: $URL （起動するたびに変わります）"
        make_qr "$URL" "data/access_qr_quick_$(date +%Y%m%d_%H%M%S).png"
        ;;
    named)
        cloudflared tunnel --no-autoupdate run "$TUNNEL_NAME" >>"$LOG_DIR/cloudflared-$TUNNEL_NAME.log" 2>&1 &
        PIDS="$PIDS $!"
        echo "Tunnel「$TUNNEL_NAME」を起動しました（ログ: .tmp/serve/cloudflared-$TUNNEL_NAME.log）"
        if [ -n "${PUBLIC_BASE_URL:-}" ]; then
            echo "公開 URL: $PUBLIC_BASE_URL"
            [ -e data/access_qr.png ] || make_qr "$PUBLIC_BASE_URL" data/access_qr.png
        fi
        ;;
esac

echo "Ctrl+C で停止します。"
# どれか1つが止まったら全体を止める（bash 3.2 には wait -n が無いので見回る）
while true; do
    for pid in $PIDS; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "プロセス $pid が停止したため、全体を止めます。ログは .tmp/serve/ にあります。" >&2
            exit 1
        fi
    done
    sleep 5
done
