#!/bin/bash
# サーバー（serve_mac.sh quick）をターミナルから切り離して起動・停止する。
# ターミナルを閉じても SSH が切れても止まらない。Mac 本体からでも SSH からでも同じように使える。
#
#   scripts/server.sh start    起動して、公開 URL と QR を表示する
#   scripts/server.sh status   動いているかと、今の公開 URL・QR を表示する
#   scripts/server.sh stop     停止する
#
# 環境変数: PORT（既定 5000）
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.tmp/serve"
PID_FILE="$LOG_DIR/server.pid"
OUT="$LOG_DIR/server.out"
cd "$ROOT" || exit 1
# ssh mac 'scripts/server.sh start' のようにログインシェルを通らないときも cloudflared が見つかるようにする
PATH="$PATH:$HOME/homebrew/bin:/opt/homebrew/bin:/usr/local/bin"

if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
else
    PY="$(command -v python3)"
fi

# start は serve_mac.sh を新しいプロセスグループ（番号 = その PID）で動かす。
# gunicorn の本体が強制終了されたときなどに残ったプロセス（ポートをつかんだままのワーカーなど）を止める。
# PID が別のプログラムに再利用されている場合に備え、このアプリのプロセスだけを対象にする。
kill_leftovers() {
    local pgid="$1" pid
    for pid in $(pgrep -g "$pgid" 2>/dev/null); do
        if ps -p "$pid" -o command= 2>/dev/null | grep -qE "gunicorn app:app|cloudflared tunnel|caffeinate -ims|serve_mac\.sh"; then
            kill -TERM "$pid" 2>/dev/null
        fi
    done
}

# 動いているサーバーの PID を返す。PID ファイルが古い（途中で止まった・再起動後など）ときは片付けて消す。
running_pid() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid="$(cat "$PID_FILE")"
    if [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null | grep -q "serve_mac.sh"; then
        echo "$pid"
        return 0
    fi
    [ -n "$pid" ] && kill_leftovers "$pid"
    rm -f "$PID_FILE"
    return 1
}

current_url() {
    grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$OUT" 2>/dev/null | head -1
}

show_url() {
    local url
    url="$(current_url)"
    if [ -z "$url" ]; then
        echo "公開 URL はまだ分かりません。少し待ってから scripts/server.sh status を実行してください。"
        return
    fi
    PUBLIC_BASE_URL="$url" "$PY" scripts/make_access_qr.py --no-file 2>/dev/null | sed 's/^/  /'
    echo
    echo "公開 URL:       $url"
    echo "印刷用のページ: $url/admin/accounts/access_qr （先生用パスワードが必要）"
    echo "※ URL は起動するたびに変わります。"
}

start() {
    local pid
    if pid="$(running_pid)"; then
        echo "サーバーはすでに動いています（PID ${pid}）。"
        show_url
        return 0
    fi
    mkdir -p "$LOG_DIR"
    : >"$OUT"
    # 新しいセッションで起動し、ターミナル（SSH）から切り離す。macOS には setsid コマンドが無いので Python で行う
    nohup "$PY" -c 'import os, sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
        /bin/bash "$ROOT/scripts/serve_mac.sh" quick </dev/null >>"$OUT" 2>&1 &
    pid=$!
    echo "$pid" >"$PID_FILE"

    echo "起動しています。1〜2分かかることがあります…"
    for _ in $(seq 1 150); do
        if ! kill -0 "$pid" 2>/dev/null; then
            kill_leftovers "$pid"
            rm -f "$PID_FILE"
            echo "起動できませんでした。表示の最後:" >&2
            tail -20 "$OUT" >&2
            return 1
        fi
        [ -n "$(current_url)" ] && break
        sleep 1
    done
    echo "起動しました。ターミナルを閉じても、SSH を切っても動き続けます。"
    echo
    show_url
    echo
    echo "止めるとき: scripts/server.sh stop"
}

status() {
    local pid
    if pid="$(running_pid)"; then
        echo "サーバーは動いています（PID ${pid}）。"
        show_url
    else
        echo "サーバーは止まっています。起動するとき: scripts/server.sh start"
        if [ -s "$OUT" ] && grep -q "停止したため" "$OUT"; then
            echo "前回は途中で止まりました。表示の最後:"
            tail -5 "$OUT"
        fi
    fi
}

stop() {
    local pid
    if ! pid="$(running_pid)"; then
        echo "サーバーは止まっています。"
        return 0
    fi
    echo "停止しています…"
    kill -TERM "$pid" 2>/dev/null
    # 解析中の要求があると、gunicorn は終わるまで最大30秒待つ
    for _ in $(seq 1 45); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "止まりませんでした。もう一度 scripts/server.sh stop を実行してください。" >&2
        return 1
    fi
    kill_leftovers "$pid"
    rm -f "$PID_FILE"
    echo "停止しました。"
}

case "${1:-}" in
    start) start ;;
    status) status ;;
    stop) stop ;;
    *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
