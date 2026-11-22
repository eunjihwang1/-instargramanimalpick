"""검수 대시보드 — 표준 라이브러리 HTTP 서버. 127.0.0.1 에서만 열린다."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import socket
import sys
import traceback
from urllib.parse import urlparse

from . import claude, config, drafts, token_setup

PORT = 3800
PUBLIC = config.ROOT / "public"


def status() -> dict:
    config.load_env(override=True)  # .env 를 고치면 새로고침만으로 반영
    return {
        "page_token": bool(config.env("META_PAGE_TOKEN")),
        "ig_username": config.env("META_IG_USERNAME"),
        "app_keys": bool(config.env("META_APP_ID") and config.env("META_APP_SECRET")),
        "short_token": bool(config.env("META_ACCESS_TOKEN")),
        "media_repo": config.env("MEDIA_GITHUB_REPO"),
        "pexels": bool(config.env("PEXELS_API_KEY")),
        "pixabay": bool(config.env("PIXABAY_API_KEY")),
        "checklist": drafts.CHECKLIST,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 요청 로그는 조용히 (토큰은 원래 URL 에 없음)
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path):
        if not path.is_file():
            return self.send_json({"error": "파일 없음"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.send_file(PUBLIC / "index.html")
        if path == "/api/status":
            return self.send_json(status())
        if path == "/api/drafts":
            return self.send_json(drafts.list_all())
        m = re.fullmatch(r"/media/([0-9a-f-]+)/((?:slide|img)_\d{2}\.jpg)", path)
        if m:
            return self.send_file(drafts.draft_dir(m.group(1)) / m.group(2))
        self.send_json({"error": "없는 주소"}, 404)

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def route(self, method):
        # 다른 사이트에서 몰래 요청을 보내는 것(CSRF) 방지
        origin = self.headers.get("Origin")
        if origin and not re.fullmatch(rf"http://(localhost|127\.0\.0\.1):{PORT}", origin):
            return self.send_json({"error": "허용되지 않은 출처"}, 403)
        path = urlparse(self.path).path
        try:
            data = self.body()
            if method == "POST" and path == "/api/drafts":
                return self.send_json(drafts.create(data.get("topic", ""), data.get("source_url", ""),
                                                    data.get("source_text", "")))
            if method == "POST" and path == "/api/setup-token":
                return self.send_json({"lines": token_setup.run()})
            m = re.fullmatch(r"/api/drafts/([0-9a-f-]+)(?:/(\w+))?", path)
            if not m:
                return self.send_json({"error": "없는 주소"}, 404)
            did, action = m.group(1), m.group(2)
            if method == "PUT" and not action:
                return self.send_json(drafts.update_content(did, data))
            if action == "status":
                return self.send_json(drafts.set_status(did, data.get("status", ""), data.get("checklist")))
            if action == "schedule":
                return self.send_json(drafts.set_schedule(did, data.get("scheduled_at")))
            if action == "publish":
                config.load_env(override=True)
                return self.send_json(drafts.publish(did, dry_run=data.get("dry_run", True) is not False))
            return self.send_json({"error": "없는 동작"}, 404)
        except claude.NotLoggedInError as exc:
            self.send_json({"error": str(exc)}, 401)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": f"예상치 못한 오류: {type(exc).__name__}: {exc}"}, 500)


class ExclusiveServer(ThreadingHTTPServer):
    """같은 포트에 서버가 두 개 붙지 않게 한다.
    Windows 는 SO_REUSEADDR 가 켜져 있으면 두 번째 서버도 오류 없이 같은 포트를 잡아,
    요청이 옛 코드의 서버로 갈 수 있다 (2026-11-22 실제로 겪음)."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(port: int = PORT) -> ExclusiveServer:
    return ExclusiveServer(("127.0.0.1", port), Handler)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    config.load_env()
    try:
        server = make_server()
    except OSError:
        print(f"대시보드가 이미 켜져 있습니다. 브라우저에서 http://localhost:{PORT} 를 여세요.")
        print("새 버전으로 바꾸려면 다른 검은 창(대시보드)을 모두 닫고 다시 실행하세요.")
        return
    print(f"애니멀픽 대시보드: http://localhost:{PORT}  (끄려면 이 창을 닫으세요)")
    server.serve_forever()


if __name__ == "__main__":
    main()
