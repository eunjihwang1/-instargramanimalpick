"""게시용 이미지 임시 공개 — 이미지는 내 컴퓨터에만 두고, 게시하는 동안만 주소를 연다.

흐름:
1. 이 컴퓨터(127.0.0.1)에 작은 파일 서버를 띄운다. 넘겨받은 파일만, 추측 불가능한 무작위 경로로 제공.
2. Cloudflare 무료 임시 터널(cloudflared, 회원가입 불필요)로 https://xxxx.trycloudflare.com 주소를 받는다.
3. 주소가 실제로 열리는지 확인한 뒤 Instagram 에 넘긴다.
4. 게시가 끝나면(성공·실패 모두) 터널과 파일 서버를 닫는다.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config

CLOUDFLARED = config.ROOT / "tools" / "cloudflared.exe"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelError(RuntimeError):
    pass


def make_file_server(files: list[Path]) -> tuple[ThreadingHTTPServer, str]:
    """넘겨받은 파일만 /<무작위>/<파일명> 으로 제공하는 로컬 서버. (서버, 경로 접두어) 반환."""
    token = secrets.token_hex(16)
    allowed = {f"/{token}/{f.name}": f for f in files}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self, body: bool):
            f = allowed.get(self.path.split("?")[0])
            if not f or not f.is_file():
                self.send_response(404)
                self.end_headers()
                return
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if body:
                self.wfile.write(data)

        def do_GET(self):
            self._serve(True)

        def do_HEAD(self):
            self._serve(False)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"/{token}"


def find_url(line: str) -> str | None:
    m = URL_RE.search(line)
    if m and "api.trycloudflare.com" not in m.group(0):
        return m.group(0)
    return None


def watch_output(stream, timeout: float = 40.0) -> str:
    """cloudflared 출력을 백그라운드에서 끝까지 읽으며(파이프가 차서 멈추지 않게) 주소를 기다린다."""
    found: dict[str, str] = {}
    ready = threading.Event()
    tail: list[str] = []

    def reader():
        for line in iter(stream.readline, ""):
            tail.append(line)
            del tail[:-5]
            if not ready.is_set():
                url = find_url(line)
                if url:
                    found["url"] = url
                    ready.set()
        ready.set()  # 프로세스가 끝나도 기다림 해제

    threading.Thread(target=reader, daemon=True).start()
    ready.wait(timeout)
    if "url" not in found:
        last = "".join(tail).strip()[-200:]
        raise TunnelError("임시 주소를 받지 못했습니다" + (f": {last}" if last else ""))
    return found["url"]


def wait_reachable(url: str, *, tries: int = 15, delay: float = 2.0, opener=urlopen, sleep=time.sleep) -> None:
    """새 주소는 DNS 반영에 몇 초 걸린다. 이미지가 실제로 열릴 때까지 기다린다."""
    for _ in range(tries):
        try:
            with opener(Request(url, method="HEAD", headers={"User-Agent": "AnimalPick"}), timeout=10) as r:
                if r.status == 200:
                    return
        except (HTTPError, URLError, OSError):
            pass
        sleep(delay)
    raise TunnelError(f"임시 주소가 열리지 않습니다: {url}")


@contextmanager
def public_urls(files: list[Path], *, exe: Path | None = None, popen=subprocess.Popen, reach=wait_reachable):
    """with public_urls(files) as urls: ... → 블록 안에서만 이미지가 공개된다."""
    exe = exe or CLOUDFLARED
    if not Path(exe).exists():
        raise TunnelError("tools/cloudflared.exe 가 없습니다. 게시용 임시 주소 프로그램을 먼저 설치하세요.")
    server, prefix = make_file_server(files)
    port = server.server_address[1]
    proc = None
    try:
        proc = popen([str(exe), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                     errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        base = watch_output(proc.stderr)
        urls = [f"{base}{prefix}/{f.name}" for f in files]
        reach(urls[0])
        yield urls
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        server.shutdown()
        server.server_close()
