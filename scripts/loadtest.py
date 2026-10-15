"""
scripts/loadtest.py
負荷試験: 何人もの生徒が同時に練習したときの、画面・録音の送信・採点の応答時間とエラーを測る。
Python の標準ライブラリだけで動くので、サーバーの PC でも別の PC でも実行できる。

仮想の生徒1人（スレッド1つ）が、ブラウザと同じ順番で要求を送る:
  ログイン → ホーム →〔単語を押して録音ページ → 口の動画を送る → 録音を送る → 結果を見る（採点）
                        → ときどき記録・ホーム〕をくり返す
録音と口の動画は、その利用者の単語のお手本（/sample_audio・/sample_video）をサーバーから取り出して
送るので、ファイルを用意しなくてよい。採点にかかる重さは本物の録音と同じ。
ブラウザと同じく、混み合っているとき（503）は Retry-After の秒数だけ待って送り直す（503 の回数も数える）。

準備（サーバーの PC で。1人1つの利用者 ID を使うこと。scripts/loadtest_accounts.py の説明を参照）:
  python scripts/loadtest_accounts.py create --count 30 --from A01

実行:
  python scripts/loadtest.py --url http://127.0.0.1:5000 --users 30 --minutes 3
  python scripts/loadtest.py --url https://xxxx.trycloudflare.com --users 10 --practices 3

主なオプション:
  --users      同時に練習する人数（既定 10。LT01〜 の利用者 ID を使う）
  --minutes    続ける時間（既定 3）。--practices を付けると、1人あたりの練習回数で止める
  --think      操作と操作の間の待ち時間（秒、既定 2。0 にするとすき間なく送り続ける）
  --ramp       全員がそろうまでの秒数（既定 10。0 にすると全員が同時に始める）
  --no-video   口の動画を送らない（口の解析を除いて測る）
  --json       結果（各要求の時間）を JSON ファイルに保存する

Cloudflare の Quick Tunnel（trycloudflare.com）は同時に処理できる要求が 200 件までなので、
大人数の試験は Mac の中から http://127.0.0.1:5000 に対して行うか、名前付き Tunnel で行う。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import random
import re
import statistics
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict

STEP_LABELS = {
    "login":   "ログイン",
    "home":    "ホーム（/select）",
    "word":    "録音ページ",
    "lip":     "口の動画を送る",
    "audio":   "録音を送る（Julius）",
    "graph":   "結果を見る（採点）",
    "history": "記録（/history）",
    "static":  "CSS・JS",
    "media":   "お手本の取り出し",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # 302 などはそのまま受け取る（ログインの成否を見るため）


class Client:
    """1人分のブラウザ（Cookie を持つ）。"""

    def __init__(self, base: str, timeout: float):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _NoRedirect())
        self.etags: dict[str, str] = {}

    def request(self, method: str, path: str, data: bytes | None = None,
                headers: dict | None = None) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with self.opener.open(req, timeout=self.timeout) as res:
                return res.status, res.read(), dict(res.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers or {})


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content, ctype) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Results:
    def __init__(self):
        self.lock = threading.Lock()
        self.times: dict[str, list[float]] = defaultdict(list)
        self.failures: dict[str, list[str]] = defaultdict(list)
        self.busy: dict[str, int] = defaultdict(int)
        self.practices = 0
        self.raw: list[dict] = []

    def ok(self, step: str, seconds: float) -> None:
        with self.lock:
            self.times[step].append(seconds)
            self.raw.append({"step": step, "seconds": round(seconds, 4), "t": time.time()})

    def fail(self, step: str, reason: str) -> None:
        with self.lock:
            self.failures[step].append(reason)
            self.raw.append({"step": step, "error": reason, "t": time.time()})

    def was_busy(self, step: str) -> None:
        with self.lock:
            self.busy[step] += 1


class VirtualStudent:
    def __init__(self, user_id: str, args, results: Results):
        self.user_id = user_id
        self.args = args
        self.results = results
        self.client = Client(args.url, args.timeout)
        self.media: dict[str, tuple[bytes, bytes | None]] = {}

    def timed(self, step: str, method: str, path: str, data=None, headers=None, ok=(200,), retry_busy=False):
        """要求を1つ送って時間を測る。503 は Retry-After だけ待って送り直す（ブラウザと同じ）。"""
        started = time.perf_counter()
        for attempt in range(6):
            try:
                status, body, res_headers = self.client.request(method, path, data, headers)
            except Exception as exc:   # タイムアウト・接続できない など
                self.results.fail(step, type(exc).__name__)
                return None
            if status == 503 and retry_busy and attempt < 5:
                self.results.was_busy(step)
                time.sleep(max(1.0, float(res_headers.get("Retry-After") or 5)))
                continue
            break
        elapsed = time.perf_counter() - started
        if status not in ok:
            self.results.fail(step, f"HTTP {status}")
            return None
        self.results.ok(step, elapsed)
        return body

    def think(self, scale: float = 1.0) -> None:
        if self.args.think > 0:
            time.sleep(self.args.think * scale * random.uniform(0.5, 1.5))

    def load_static(self, html: bytes) -> None:
        # ページが読み込む CSS・JS（Service Worker が毎回取り直すので、ブラウザでもサーバーに届く）
        for path in sorted(set(re.findall(rb'(?:href|src)="(/static/[^"?]+\.(?:css|js))"', html))):
            p = path.decode()
            headers = {"If-None-Match": self.client.etags[p]} if p in self.client.etags else {}
            started = time.perf_counter()
            try:
                status, _, res_headers = self.client.request("GET", p, headers=headers)
            except Exception as exc:
                self.results.fail("static", type(exc).__name__)
                continue
            if status in (200, 304):
                if res_headers.get("ETag"):
                    self.client.etags[p] = res_headers["ETag"]
                self.results.ok("static", time.perf_counter() - started)
            else:
                self.results.fail("static", f"HTTP {status}")

    def run(self, deadline: float) -> None:
        a = self.args
        body = urllib.parse.urlencode({"user_id": self.user_id, "password": a.password}).encode()
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.timed("login", "POST", "/login", body, form, ok=(302,)) is None:
            return
        home = self.timed("home", "GET", "/select", headers={"Accept": "text/html"})
        if home is None:
            return
        self.load_static(home)
        words = sorted(set(re.findall(rb'data-word-id="([A-Za-z0-9_-]+)"', home)))
        if not words:
            self.results.fail("home", "単語がない")
            return
        done = 0
        while (a.practices and done < a.practices) or (not a.practices and time.time() < deadline):
            word = random.choice(words).decode()
            # ホームの単語カードを押したときと同じ（POST / で録音ページを開く）
            page = self.timed("word", "POST", "/", urllib.parse.urlencode({"Words": word}).encode(),
                              {"Accept": "text/html", **form})
            if page is None:
                self.think()
                continue
            self.load_static(page)
            audio, video = self.sample(word)
            if audio is None:
                continue
            self.think(1.5)   # 録音している時間
            if video is not None and not a.no_video:
                data, ctype = multipart({"mode": "test", "word_id": word}, {"file": ("test.webm", video, "video/webm")})
                self.timed("lip", "POST", "/upload_lip_video", data, {"Content-Type": ctype}, retry_busy=True)
            data, ctype = multipart({}, {"file": ("test.wav", audio, "audio/wav")})
            if self.timed("audio", "POST", "/audio", data, {"Content-Type": ctype}, retry_busy=True) is None:
                self.think()
                continue
            self.think(0.5)   # 「結果を見る」を押すまで
            result = self.timed("graph", "POST", "/graph", headers={"Accept": "text/html"}, retry_busy=True)
            if result is not None:
                if "<title>解析結果".encode() not in result:
                    self.results.fail("graph", "結果の画面ではない")
                else:
                    with self.results.lock:
                        self.results.practices += 1
                    self.load_static(result)
            done += 1
            self.think(2)     # 結果を見ている時間
            if done % 3 == 0:
                if self.timed("history", "GET", "/history", headers={"Accept": "text/html"}) is not None:
                    self.think()
                self.timed("home", "GET", "/select", headers={"Accept": "text/html"})

    def sample(self, word: str) -> tuple[bytes | None, bytes | None]:
        if word not in self.media:
            audio = self.timed("media", "GET", f"/sample_audio/{word}")
            video = None if self.args.no_video else self.timed("media", "GET", f"/sample_video/{word}", ok=(200, 404))
            if video is not None and not video.startswith(b"\x1a\x45\xdf\xa3"):   # webm でない（404 の本文など）
                video = None
            self.media[word] = (audio, video)
        return self.media[word]


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _left(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _right(text: str, width: int) -> str:
    return " " * max(0, width - _width(text)) + text


def report(results: Results, args, seconds: float) -> None:
    print()
    print(f"人数 {args.users}・{seconds:.0f} 秒・採点が終わった練習 {results.practices} 回"
          f"（1分あたり {results.practices / max(seconds, 1) * 60:.1f} 回）")
    widths = (7, 6, 6, 9, 9, 9, 9)
    head = ("成功", "失敗", "503", "中央値", "90%", "99%", "最大")
    print(_left("", 24) + "".join(_right(h, w) for h, w in zip(head, widths)))
    for step, label in STEP_LABELS.items():
        times, fails = results.times.get(step, []), results.failures.get(step, [])
        if not times and not fails:
            continue
        cells = [str(len(times)), str(len(fails)), str(results.busy.get(step, 0))]
        cells += [f"{pct(times, q):.2f}s" if times else "-" for q in (0.5, 0.9, 0.99, 1.0)]
        print(_left(label, 24) + "".join(_right(c, w) for c, w in zip(cells, widths)))
    reasons = defaultdict(int)
    for step, fails in results.failures.items():
        for r in fails:
            reasons[f"{STEP_LABELS.get(step, step)}: {r}"] += 1
    if reasons:
        print("\n失敗の内訳:")
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {k}  × {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SP-PS の負荷試験")
    parser.add_argument("--url", default="http://127.0.0.1:5000", help="サーバーの URL")
    parser.add_argument("--users", type=int, default=10, help="同時に練習する人数（既定 10）")
    parser.add_argument("--minutes", type=float, default=3.0, help="続ける時間（分、既定 3）")
    parser.add_argument("--practices", type=int, default=0, help="1人あたりの練習回数（指定すると時間ではなく回数で止める）")
    parser.add_argument("--think", type=float, default=2.0, help="操作の間の待ち時間（秒、既定 2）")
    parser.add_argument("--ramp", type=float, default=10.0, help="全員がそろうまでの秒数（既定 10）")
    parser.add_argument("--prefix", default="LT", help="利用者 ID の頭（既定 LT → LT01, LT02, …）")
    parser.add_argument("--first", type=int, default=1, help="最初の番号（既定 1）")
    parser.add_argument("--password", default="loadtest123", help="パスワード（既定 loadtest123）")
    parser.add_argument("--timeout", type=float, default=120.0, help="1つの要求を待つ最大の秒数（既定 120）")
    parser.add_argument("--no-video", action="store_true", help="口の動画を送らない")
    parser.add_argument("--json", help="各要求の結果を保存する JSON ファイル")
    args = parser.parse_args()

    results = Results()
    started = time.time()
    deadline = started + args.minutes * 60
    students = [VirtualStudent(f"{args.prefix}{i:02d}", args, results)
                for i in range(args.first, args.first + args.users)]
    threads = []
    for n, student in enumerate(students):
        t = threading.Thread(target=student.run, args=(deadline,), daemon=True)
        threads.append(t)
        t.start()
        if args.ramp > 0 and n < len(students) - 1:
            time.sleep(args.ramp / len(students))
    print(f"{args.users} 人で開始しました（{args.url}）。"
          + (f"1人 {args.practices} 回練習します。" if args.practices else f"{args.minutes:g} 分続けます。"))
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(10)
            with results.lock:
                fails = sum(len(v) for v in results.failures.values())
                busy = sum(results.busy.values())
                graph = results.times.get("graph", [])
                recent = statistics.median(graph[-20:]) if graph else float("nan")
            print(f"  {time.time() - started:5.0f}秒  採点 {results.practices} 回・直近の採点の中央値 {recent:.2f}秒"
                  f"・失敗 {fails}・503 {busy}", flush=True)
    except KeyboardInterrupt:
        print("中断しました。ここまでの結果:")
    report(results, args, time.time() - started)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"args": vars(args), "results": results.raw}, f, ensure_ascii=False)
        print(f"\n結果を保存しました: {args.json}")
    sys.exit(1 if any(results.failures.values()) else 0)


if __name__ == "__main__":
    main()
