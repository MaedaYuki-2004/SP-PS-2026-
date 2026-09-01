"""
core/accounts.py
研究参加者アカウント（data/config/accounts.json）の読み書き・検証・認証を担当する。

倫理審査資料 SP-PS に準拠した設計:
  - §6.1  利用者 ID は研究者が割り当てる（A01, A02 ...）。本人は自由入力しない。
  - §6.1  パスワードは本人が初回ログイン時に設定し、ハッシュ化して保存する
          （werkzeug、平文は保持しない）。
  - §5.1#6 学年（grade）は学部単位（中学部・高等部）に丸めて保持する。
  - §5.3  聴覚障害の程度（hearing_level）は要配慮個人情報のため、
          dB 値などの詳細は持たず区分に丸めて保持する。

grade / hearing_level は調査票由来のため、研究者のみが管理画面から入力・変更する。
参加者が初回ログインで設定するのはパスワードのみ。

accounts.json の構造:
{
  "A01": {
    "user_id":       "A01",
    "password_hash": "scrypt:...",           # 未設定時は null
    "password_set":  true,
    "profile": {
      "grade":         "junior",
      "hearing_level": "moderate"
    },
    "created_at": "2026-08-31T12:00:00",
    "updated_at": "2026-08-31T12:00:00"
  }
}
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from config import DATA_DIR

ACCOUNTS_PATH = DATA_DIR / "config" / "accounts.json"

# ログイン状態を保持する Flask セッションキー（app.py と共有）
SESSION_KEY = "user_id"

# 研究者が割り当てる ID の形式（英字始まり・半角英数と - _ のみ・1〜32文字）
USER_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")

MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128

# 学年は学部単位（中学部・高等部）に丸めて保持する（再識別リスク低減、資料 §5.3）。
GRADE_LABELS: dict[str, str] = {
    "junior": "中学部",
    "senior": "高等部",
    "unknown": "未回答",
}

# 聴覚障害の程度（要配慮個人情報。dB 値は持たず区分で保持する）。
HEARING_LEVEL_LABELS: dict[str, str] = {
    "mild": "軽度難聴",
    "moderate": "中等度難聴",
    "severe": "高度難聴",
    "profound": "重度難聴",
    "unknown": "未回答",
}

DEFAULT_GRADE = "unknown"
DEFAULT_HEARING_LEVEL = "unknown"


# ── 入出力 ───────────────────────────────────────────────────────────

def load_accounts() -> dict:
    """accounts.json を読み込む。存在しない・壊れている場合は空辞書を返す。"""
    if not ACCOUNTS_PATH.exists():
        return {}
    try:
        with ACCOUNTS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_accounts(accounts: dict) -> None:
    """accounts.json へアトミックに書き込む（tmp へ書いて置換）。"""
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNTS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ACCOUNTS_PATH)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sanitize(account: dict) -> dict:
    """password_hash を除いた表示用のコピーを返す。"""
    safe = {k: v for k, v in account.items() if k != "password_hash"}
    safe["profile"] = dict(account.get("profile", {}))
    return safe


# ── 検証 ─────────────────────────────────────────────────────────────

def validate_user_id(user_id: str) -> str:
    uid = (user_id or "").strip()
    if not USER_ID_PATTERN.match(uid):
        raise ValueError(
            "利用者IDは英字で始まる1〜32文字の半角英数字・ハイフン・アンダースコアにしてください"
        )
    return uid


def validate_grade(grade: str) -> str:
    if grade not in GRADE_LABELS:
        raise ValueError(f"学年の値が不正です: {grade!r}")
    return grade


def validate_hearing_level(level: str) -> str:
    if level not in HEARING_LEVEL_LABELS:
        raise ValueError(f"聴覚障害の程度の値が不正です: {level!r}")
    return level


def validate_password(password: str) -> str:
    pw = password or ""
    if len(pw) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"パスワードは{MIN_PASSWORD_LENGTH}文字以上にしてください")
    if len(pw) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"パスワードは{MAX_PASSWORD_LENGTH}文字以内にしてください")
    return pw


# ── 参照 ─────────────────────────────────────────────────────────────

def get_account(user_id: str) -> dict | None:
    """user_id に対応するアカウント（生データ）を返す。無ければ None。"""
    return load_accounts().get((user_id or "").strip())


def list_accounts() -> list[dict]:
    """全アカウントを user_id 昇順の表示用リストで返す（password_hash 除外）。"""
    accounts = load_accounts()
    return [_sanitize(accounts[uid]) for uid in sorted(accounts)]


def account_exists(user_id: str) -> bool:
    return (user_id or "").strip() in load_accounts()


def needs_password_setup(user_id: str) -> bool:
    """アカウントは存在するがパスワード未設定（初回ログイン待ち）か。"""
    acc = get_account(user_id)
    return bool(acc) and not acc.get("password_set")


# ── 研究者向け操作（管理画面 /admin/accounts） ───────────────────────

def create_account(
    user_id: str,
    grade: str = DEFAULT_GRADE,
    hearing_level: str = DEFAULT_HEARING_LEVEL,
) -> dict:
    """新しい参加者アカウントを発行する。パスワードは未設定で作成する。"""
    uid = validate_user_id(user_id)
    grade = validate_grade(grade)
    hearing_level = validate_hearing_level(hearing_level)

    accounts = load_accounts()
    if uid in accounts:
        raise ValueError(f"利用者ID {uid} は既に存在します")

    now = _now_iso()
    accounts[uid] = {
        "user_id": uid,
        "password_hash": None,
        "password_set": False,
        "profile": {
            "grade": grade,
            "hearing_level": hearing_level,
        },
        "created_at": now,
        "updated_at": now,
    }
    save_accounts(accounts)
    return _sanitize(accounts[uid])


def update_research_profile(
    user_id: str,
    grade: str | None = None,
    hearing_level: str | None = None,
) -> dict:
    """調査票由来の項目（学年・聴覚障害の程度）を研究者が更新する。"""
    accounts = load_accounts()
    acc = accounts.get((user_id or "").strip())
    if not acc:
        raise ValueError(f"利用者ID {user_id} は登録されていません")

    if grade is not None:
        acc["profile"]["grade"] = validate_grade(grade)
    if hearing_level is not None:
        acc["profile"]["hearing_level"] = validate_hearing_level(hearing_level)
    acc["updated_at"] = _now_iso()
    save_accounts(accounts)
    return _sanitize(acc)


def clear_password(user_id: str) -> dict:
    """パスワードを未設定に戻す（次回ログインで参加者が再設定する）。"""
    accounts = load_accounts()
    acc = accounts.get((user_id or "").strip())
    if not acc:
        raise ValueError(f"利用者ID {user_id} は登録されていません")
    acc["password_hash"] = None
    acc["password_set"] = False
    acc["updated_at"] = _now_iso()
    save_accounts(accounts)
    return _sanitize(acc)


def delete_account(user_id: str) -> dict:
    """アカウントを削除する（参加者の撤回時など）。"""
    accounts = load_accounts()
    uid = (user_id or "").strip()
    if uid not in accounts:
        raise ValueError(f"利用者ID {uid} は登録されていません")
    del accounts[uid]
    save_accounts(accounts)
    return {"message": f"{uid} を削除しました"}


# ── 参加者向け操作（初回ログイン・ログイン） ─────────────────────────

def complete_first_login(user_id: str, password: str) -> dict:
    """初回ログイン: 参加者がパスワードを設定する。

    学年・聴覚障害の程度はここでは触らない（研究者が調査票から入力済み）。
    """
    accounts = load_accounts()
    acc = accounts.get((user_id or "").strip())
    if not acc:
        raise ValueError(f"利用者ID {user_id} は登録されていません")
    if acc.get("password_set"):
        raise ValueError("このアカウントは既にパスワードが設定されています")

    validate_password(password)

    acc["password_hash"] = generate_password_hash(password)
    acc["password_set"] = True
    acc["updated_at"] = _now_iso()
    save_accounts(accounts)
    return _sanitize(acc)


def change_password(user_id: str, current_password: str, new_password: str) -> dict:
    """参加者が現在のパスワードを確認のうえ新しいパスワードに変更する。"""
    accounts = load_accounts()
    acc = accounts.get((user_id or "").strip())
    if not acc or not acc.get("password_set") or not acc.get("password_hash"):
        raise ValueError("パスワードが未設定のアカウントです")
    if not check_password_hash(acc["password_hash"], current_password or ""):
        raise ValueError("現在のパスワードが正しくありません")
    validate_password(new_password)
    acc["password_hash"] = generate_password_hash(new_password)
    acc["updated_at"] = _now_iso()
    save_accounts(accounts)
    return _sanitize(acc)


def verify_login(user_id: str, password: str) -> dict | None:
    """ID とパスワードを検証する。成功で表示用アカウント、失敗で None。"""
    acc = get_account(user_id)
    if not acc or not acc.get("password_set") or not acc.get("password_hash"):
        return None
    if not check_password_hash(acc["password_hash"], password or ""):
        return None
    return _sanitize(acc)
