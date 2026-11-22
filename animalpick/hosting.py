"""게시용 이미지 공개 호스팅 — GitHub 공개 저장소(raw.githubusercontent.com).

Instagram API 는 이미지 파일이 아니라 '공개 URL' 을 받는다. 게시 직전에만 올린다.
저장소 이름은 .env 의 MEDIA_GITHUB_REPO (예: 계정명/animalpick-media).
인증은 사용자가 이미 로그인해 둔 `gh` CLI 자격을 쓴다 (값은 출력하지 않음).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config


class HostingError(RuntimeError):
    pass


def repo() -> str:
    name = config.env("MEDIA_GITHUB_REPO")
    if not name or "/" not in name:
        raise HostingError("이미지 저장소(MEDIA_GITHUB_REPO)가 아직 설정되지 않았습니다.")
    return name


def gh_token() -> str:
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise HostingError("GitHub CLI(gh)를 실행하지 못했습니다.") from None
    token = out.stdout.strip()
    if out.returncode != 0 or not token:
        raise HostingError("GitHub CLI 로그인이 필요합니다. 터미널에서 `gh auth login` 을 실행하세요.")
    return token


def raw_url(repo_name: str, path: str, branch: str = "main") -> str:
    return f"https://raw.githubusercontent.com/{repo_name}/{branch}/{path}"


def upload(files: list[Path], prefix: str, *, opener=urlopen, token_fn=gh_token) -> list[str]:
    name, token = repo(), token_fn()
    urls = []
    for f in files:
        path = f"{prefix}/{f.name}"
        body = json.dumps({"message": f"add {path}", "content": base64.b64encode(f.read_bytes()).decode()}).encode()
        req = Request(f"https://api.github.com/repos/{name}/contents/{path}", data=body, method="PUT",
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "User-Agent": "AnimalPick"})
        try:
            with opener(req, timeout=60) as resp:
                resp.read()
        except HTTPError as exc:
            msg = {404: "저장소를 찾을 수 없습니다", 422: "같은 경로에 파일이 이미 있습니다",
                   401: "GitHub 인증이 만료됐습니다 (`gh auth login`)"}.get(exc.code, f"HTTP {exc.code}")
            raise HostingError(f"이미지 업로드 실패: {msg}") from None
        except URLError as exc:
            raise HostingError(f"이미지 업로드 네트워크 오류 ({type(exc.reason).__name__})") from None
        urls.append(raw_url(name, path))
    return urls


def wait_public(urls: list[str], *, opener=urlopen, tries: int = 6, delay: float = 2.0, sleep=time.sleep) -> None:
    """올린 이미지가 실제로 공개 URL 에서 열리는지 확인 (Instagram 이 가져가기 전)."""
    for url in urls:
        for attempt in range(tries):
            try:
                with opener(Request(url, method="HEAD", headers={"User-Agent": "AnimalPick"}), timeout=15) as r:
                    ctype = r.headers.get("Content-Type", "") if r.headers else ""
                    if "image" in ctype or not ctype:
                        break
            except (HTTPError, URLError):
                pass
            if attempt == tries - 1:
                raise HostingError(f"이미지가 공개 주소에서 열리지 않습니다: {url}")
            sleep(delay)
