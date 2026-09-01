"""
core/usercontext.py
現在ログイン中の研究参加者 ID を「リクエスト単位」で保持する小さな器。

なぜ必要か:
  core/history.py・core/lesson.py は Flask のリクエストコンテキストを持たない
  純粋なライブラリ層で、`session` を直接読めない。一方で練習記録は
  参加者ごとに分けて保存する必要がある（倫理審査資料 §6.1）。
  そこで app.py の before_request がログイン中の user_id をここへ入れ、
  history / lesson 側はこの値でデータファイルのパスを切り替える。

フォールバック:
  CLI スクリプト（diagnose.py / regenerate_mfcc.py 等）や未ログイン時は
  current_user() が None を返す。呼び出し側は従来の共有パス
  （data/config/history.json 等）を使う。

スレッドモデル:
  Flask 開発サーバ・gunicorn の sync/gthread いずれもリクエストを 1 スレッドで
  処理するため threading.local() で十分。値はリクエスト終了時に clear() する。
"""
from __future__ import annotations

import threading

_state = threading.local()


def set_current_user(user_id: str | None) -> None:
    """このスレッド（＝現在のリクエスト）の参加者 ID を設定する。"""
    _state.user_id = (user_id or None)


def current_user() -> str | None:
    """現在のリクエストの参加者 ID。未設定なら None。"""
    return getattr(_state, "user_id", None)


def clear() -> None:
    """リクエスト終了時に呼ぶ。スレッド再利用による ID 漏れを防ぐ。"""
    _state.user_id = None
