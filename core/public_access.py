"""公開用URLと、同じPCで動く中継からの接続元判定。"""
from ipaddress import ip_address
from urllib.parse import urlsplit


def client_key(remote: str | None, headers, proxy: str) -> str:
    remote = remote or "unknown"
    # ループバックでもローカルプロセスは偽装できる。信頼できるPC上の中継のみ想定する。
    if remote not in {"127.0.0.1", "::1"}:
        return remote
    if proxy != "cloudflare":
        return remote
    value = headers.get("CF-Connecting-IP", "").strip()
    try:
        return str(ip_address(value))
    except ValueError:
        return remote


def public_url(value: str) -> str:
    """QRに使うHTTPSの公開先。資格情報・クエリ・フラグメントは含めない。"""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # 不正なポート表記を検出する
    except ValueError as exc:
        raise ValueError("PUBLIC_BASE_URL のURL形式が正しくありません。") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value):
        raise ValueError("PUBLIC_BASE_URL は認証情報・クエリ・#を含まないHTTPS URLにしてください。")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("スマホ用にはlocalhostではなく、公開されたHTTPS URLを設定してください。")
    return value.rstrip("/") + "/"


def url_from_host(host: str) -> str:
    """今開いているホスト名から公開URLを作る。Tunnel 経由（ポート番号なしのドメイン名）のときだけ使える。"""
    host = (host or "").strip()
    try:
        ip_address(host.strip("[]"))
        is_ip = True
    except ValueError:
        is_ip = False
    # ポート付き・IPアドレスは Mac や同じ Wi-Fi から直接開いたときで、スマホ用の公開URLではない
    if not host or ":" in host or is_ip:
        raise ValueError("Tunnel 経由のURLではありません。")
    return public_url(f"https://{host}")
