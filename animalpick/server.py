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

from . import claude, config, db, drafts, sources, token_setup, tunnel
import threading

PORT = 3800
PUBLIC = config.ROOT / "public"


def status() -> dict:
    config.load_env(override=True)  # .env 를 고치면 새로고침만으로 반영
    return {
        "page_token": bool(config.env("META_PAGE_TOKEN")),
        "ig_username": config.env("META_IG_USERNAME"),
        "app_keys": bool(config.env("META_APP_ID") and config.env("META_APP_SECRET")),
        "short_token": bool(config.env("META_ACCESS_TOKEN")),
        "media_repo": tunnel.CLOUDFLARED.exists() if config.env("MEDIA_HOSTING") != "github" else bool(config.env("MEDIA_GITHUB_REPO")),
        "pexels": bool(config.env("PEXELS_API_KEY")),
        "pixabay": bool(config.env("PIXABAY_API_KEY")),
        "naver": bool(config.env("NAVER_CLIENT_ID") and config.env("NAVER_CLIENT_SECRET")),
        "checklist": drafts.CHECKLIST,
    }


class Job:
    """오래 걸리는 작업(후보 수집·원고 일괄 생성)을 백그라운드에서 한 번에 하나만."""
    lock = threading.Lock()
    state = {"running": False, "name": "", "log": [], "result": None, "error": None}

    @classmethod
    def start(cls, name: str, fn) -> bool:
        if not cls.lock.acquire(blocking=False):
            return False
        cls.state = {"running": True, "name": name, "log": [], "result": None, "error": None}

        def run():
            try:
                cls.state["result"] = fn(cls.state["log"].append)
            except Exception as exc:
                cls.state["error"] = str(exc)
            finally:
                cls.state["running"] = False
                cls.lock.release()
        threading.Thread(target=run, daemon=True).start()
        return True


def _collect_job(log):
    conn = db.connect(config.DB_PATH)
    try:
        return sources.collect(conn=conn, log=log)
    finally:
        conn.close()


def _auto_draft_job(log):
    conn = db.connect(config.DB_PATH)
    try:
        picks = sources.pick_daily(sources.list_candidates(conn, days=2))
        log(f"하루 구성대로 {len(picks)}개 원고 생성 시작")
        return {"drafts": sources.make_drafts(picks, conn=conn, log=log)}
    finally:
        conn.close()


def candidates_payload() -> dict:
    conn = db.connect(config.DB_PATH)
    try:
        cands = sources.list_candidates(conn, days=3)
        picks = {c["id"] for c in sources.pick_daily(cands)}
        for c in cands:
            c["daily_pick"] = c["id"] in picks
        excluded = [c for c in sources.list_candidates(conn, days=3, include_excluded=True) if c["status"] == "excluded"]
        return {"candidates": cands, "excluded": excluded[:40], "mix": sources.DAILY_MIX}
    finally:
        conn.close()


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
        if path == "/api/candidates":
            return self.send_json(candidates_payload())
        if path == "/api/job":
            return self.send_json(Job.state)
        m = re.fullmatch(r"/media/([0-9a-f-]+)/((?:slide|img)_[gk]?\d{1,2}\.jpg)", path)
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
            if method == "POST" and path in ("/api/candidates/collect", "/api/candidates/auto-draft"):
                config.load_env(override=True)
                name, fn = (("후보 수집", _collect_job) if path.endswith("collect") else ("원고 일괄 생성", _auto_draft_job))
                if not Job.start(name, fn):
                    return self.send_json({"error": f"'{Job.state['name']}' 작업이 아직 진행 중입니다"}, 409)
                return self.send_json({"started": name})
            mc = re.fullmatch(r"/api/candidates/([0-9a-f]{12})/(draft|dismiss)", path)
            if method == "POST" and mc:
                conn = db.connect(config.DB_PATH)
                try:
                    found = sources.rows(conn, "id = ?", (mc.group(1),))
                    if not found:
                        return self.send_json({"error": "후보를 찾을 수 없습니다"}, 404)
                    if mc.group(2) == "dismiss":
                        sources.set_status(conn, mc.group(1), "dismissed")
                        return self.send_json({"ok": True})
                    made = sources.make_drafts(found, conn=conn)
                    if not made:
                        return self.send_json({"error": "원고를 만들지 못했습니다"}, 400)
                    return self.send_json(drafts.load(made[0]))
                finally:
                    conn.close()
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
