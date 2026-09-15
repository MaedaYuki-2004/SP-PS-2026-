"""
core/teacher.py
先生用パスワードと「先生モード」の判定を担当する。

単語の追加・編集・削除、お手本の録画・録り直し・削除、参加者アカウントの管理は、
生徒が使う端末から誤って（または意図的に）操作されると練習の土台が壊れる。
画面のボタンを隠すだけでは URL を直接開けば使えてしまうため、サーバー側で
「先生モードのセッションか」を確かめてから処理する。

  - 先生モードは、先生用パスワードを入力したときだけ ON になる（Flask セッションに期限を保存）。
  - 期限は最後に先生の操作をしてから SESSION_MINUTES 分。生徒の端末で先生が解除したまま
    置いていっても、しばらくすれば自動で OFF に戻る。ログアウト・ログインでも OFF になる。
  - パスワードの置き場所は次の順に探す。
      1. 環境変数 SP_PS_TEACHER_PASSWORD（クラウドなど、ファイルを置きにくい環境向け）
      2. data/config/teacher.json（scripts/set_teacher_password.py で設定。ハッシュのみ保存）
    どちらも無いときは誰も先生モードにできない（安全側に倒す）。
  - 総当たりを防ぐため、MAX_FAILS 回続けて間違えると LOCK_SECONDS 秒は受け付けない。

teacher.json の構造:
{
  "password_hash": "scrypt:...",
  "updated_at":    "2026-09-14T08:00:00"
}
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from config import DATA_DIR

TEACHER_CONFIG_PATH = DATA_DIR / "config" / "teacher.json"
ENV_PASSWORD = "SP_PS_TEACHER_PASSWORD"

# Flask セッションに「先生モードの期限（UNIX 秒）」を入れるキー
SESSION_KEY = "teacher_until"
SESSION_MINUTES = 60

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

MAX_FAILS = 5
LOCK_SECONDS = 60


# ── パスワード ───────────────────────────────────────────────────────

def _env_password() -> str | None:
    return os.environ.get(ENV_PASSWORD) or None


def _load_hash() -> str | None:
    if not TEACHER_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(TEACHER_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("password_hash") or None


def is_configured() -> bool:
    """先生用パスワードが設定されているか。"""
    return bool(_env_password() or _load_hash())


def validate_password(password: str) -> str:
    if not isinstance(password, str) or not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise ValueError(f"先生用パスワードは {MIN_PASSWORD_LENGTH}〜{MAX_PASSWORD_LENGTH} 文字にしてください。")
    return password


def set_password(password: str) -> None:
    """先生用パスワードを設定する（ハッシュだけを保存し、平文は残さない）。"""
    validate_password(password)
    TEACHER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "password_hash": generate_password_hash(password),
        "updated_at":    datetime.now().isoformat(timespec="seconds"),
    }
    tmp = TEACHER_CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TEACHER_CONFIG_PATH)


def verify_password(password: str) -> bool:
    if not isinstance(password, str) or not password:
        return False
    env = _env_password()
    if env:
        return hmac.compare_digest(password.encode("utf-8"), env.encode("utf-8"))
    stored = _load_hash()
    return bool(stored) and check_password_hash(stored, password)


# ── 先生モード（セッション） ─────────────────────────────────────────

def is_active(sess) -> bool:
    """このセッションが先生モードの期限内か。期限切れなら印を消す。"""
    until = sess.get(SESSION_KEY)
    if not until:
        return False
    try:
        if float(until) > time.time():
            return True
    except (TypeError, ValueError):
        pass
    sess.pop(SESSION_KEY, None)
    return False


def activate(sess) -> None:
    """先生モードを ON にする（すでに ON なら期限を延ばす）。"""
    sess[SESSION_KEY] = time.time() + SESSION_MINUTES * 60


def deactivate(sess) -> None:
    sess.pop(SESSION_KEY, None)


# ── 総当たり対策 ─────────────────────────────────────────────────────
# 端末（接続元）ごとに、続けて間違えた回数を覚えておく。サーバーを再起動すると消える。
_fails: dict[str, tuple[int, float]] = {}
_fails_lock = threading.Lock()


def seconds_locked(key: str) -> int:
    """まだ受け付けない残り秒数（0 なら受け付ける）。"""
    with _fails_lock:
        count, locked_until = _fails.get(key, (0, 0.0))
    left = locked_until - time.time()
    return int(left) + 1 if left > 0 else 0


def record_failure(key: str) -> None:
    with _fails_lock:
        count, _ = _fails.get(key, (0, 0.0))
        count += 1
        if count >= MAX_FAILS:
            _fails[key] = (0, time.time() + LOCK_SECONDS)
        else:
            _fails[key] = (count, 0.0)


def clear_failures(key: str) -> None:
    with _fails_lock:
        _fails.pop(key, None)
