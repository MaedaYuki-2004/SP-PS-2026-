"""
gunicorn.conf.py
本番のサーバー（gunicorn）の設定。scripts/serve_mac.sh が -c で読み込む。

同時に多くの人が使えるように、ワーカー（プロセス）とスレッドを複数にしている
（だれかの「結果を見る」の解析中でも、ほかの人のホームの表示などが待たされないように）。

  - ワーカー（WEB_CONCURRENCY、既定 2）: 声の高さの計算（Praat）などの Python の処理は、1つの
    プロセスの中では同時に1つしか進まない（GIL）。プロセスを分けて CPU のコアを使う。
    1つのワーカーは 0.3〜0.5GB ほどのメモリを使う。
  - スレッド（SP_PS_THREADS、既定 16）: 画面の表示・録音の受け取り・解析の順番待ちを、ワーカーの
    中で並べて処理する。重い解析を同時に動かす数は core/capacity.py が別に絞るので、スレッドを
    増やしても CPU を取り合いすぎることはない。

ログインの試行回数や、同じ人の処理を1つずつにする順番は、ファイルとロックで全ワーカーが共有する
（core/ratelimit.py・core/storage.py）。
"""
import os
import threading


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
        return value if value > 0 else default
    except ValueError:
        return default


workers = _int_env("WEB_CONCURRENCY", 2)
threads = _int_env("SP_PS_THREADS", 16)
worker_class = "gthread"
# gthread のワーカーは、長い要求の途中でも生きていることを知らせるので、この秒数で解析が打ち切られる
# ことはない（応答しなくなったワーカーを入れ替えるまでの時間）
timeout = 300
graceful_timeout = 30
keepalive = 5


def on_starting(server):
    # 実際のワーカー数（コマンドの --workers を含む）を core/capacity.py に伝える。
    # 1つのワーカーで同時に動かす解析の数を、CPU の数をワーカーで分けて決めるのに使う
    os.environ["SP_PS_WORKERS"] = str(server.cfg.workers)


def post_worker_init(worker):
    # 起動直後の最初の解析が遅くならないよう、ライブラリの読み込みなどを裏で先に済ませる
    import app
    threading.Thread(target=app.warm_up, name="warm-up", daemon=True).start()
