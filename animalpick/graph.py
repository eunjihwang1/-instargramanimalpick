"""Meta Graph API 최소 클라이언트 (표준 라이브러리).

- 토큰은 Authorization 헤더로만 보낸다 (URL·로그에 남기지 않음).
- 응답·오류 문자열에 섞인 토큰은 [REDACTED] 로 지운다.
"""
from __future__ import annotations

import json
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import config

DEFAULT_VERSION = "v27.0"
VERSION_RE = re.compile(r"^v\d+\.\d+$")
TOKEN_IN_TEXT_RE = re.compile(r"(access_token=)[^&\s\"']+")
# 잠깐 기다리면 풀리는 오류 (재시도 대상)
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613, 9007}

ERROR_HINTS = {
    190: "토큰이 만료됐거나 잘못됐습니다. Graph API 탐색기에서 토큰을 새로 발급해 .env 에 다시 넣어 주세요.",
    102: "로그인 세션이 끊겼습니다. 토큰을 새로 발급해 주세요.",
    10: "권한이 부족합니다. 토큰 발급 시 필요한 권한을 모두 체크했는지 확인하세요.",
    200: "권한이 부족합니다. 토큰 발급 시 필요한 권한과 '페이지 선택' 화면에서 애니멀픽 페이지를 체크했는지 확인하세요.",
    100: "요청한 필드나 ID 가 맞지 않습니다. 계정 이름 철자, META_IG_USER_ID, 또는 지원되지 않는 필드를 확인하세요.",
    110: "대상 계정을 찾을 수 없습니다. 개인(비프로) 계정이거나, 연령 제한 계정, 또는 핸들 철자 오류일 수 있습니다.",
    4: "앱 호출 한도에 걸렸습니다. 잠시(약 1시간) 뒤 다시 시도하세요.",
    17: "사용자 호출 한도에 걸렸습니다. 잠시 뒤 다시 시도하세요.",
    32: "페이지 호출 한도에 걸렸습니다. 잠시 뒤 다시 시도하세요.",
    613: "호출 빈도 제한에 걸렸습니다. 잠시 뒤 다시 시도하세요.",
    9004: "Instagram 이 이미지 주소를 열지 못했습니다. 이미지 URL 이 공개돼 있는지 확인하세요.",
    36003: "이미지 비율이 Instagram 허용 범위를 벗어났습니다.",
}


class GraphError(RuntimeError):
    def __init__(self, status: int, code, subcode, message: str):
        self.status, self.code, self.subcode = status, code, subcode
        hint = ERROR_HINTS.get(code if isinstance(code, int) else -1, "")
        text = f"Graph API HTTP {status}: code={code}, subcode={subcode}, message={message}"
        super().__init__(text + (f"\n  → {hint}" if hint else ""))

    @property
    def transient(self) -> bool:
        return self.status >= 500 or self.code in TRANSIENT_CODES


def api_version() -> str:
    version = config.env("META_GRAPH_API_VERSION", DEFAULT_VERSION)
    if not VERSION_RE.fullmatch(version):
        raise ValueError("META_GRAPH_API_VERSION 은 v27.0 형태로 입력하세요.")
    return version


def redact(value, token: str = ""):
    if isinstance(value, str):
        if token:
            value = value.replace(token, "[REDACTED]")
        return TOKEN_IN_TEXT_RE.sub(r"\1[REDACTED]", value)
    if isinstance(value, dict):
        return {k: redact(v, token) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, token) for v in value]
    return value


def request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    token: str | None = None,
    token_env: str = "META_ACCESS_TOKEN",
    opener: Callable = urlopen,
    auth: bool = True,
) -> dict:
    """GET 은 params 를 쿼리로, POST 는 form 본문으로 보낸다."""
    token = (token if token is not None else config.env(token_env)) if auth else ""
    if auth and not token:
        raise RuntimeError(
            f"{token_env} 이 없습니다. 프로젝트 폴더의 .env 파일에 토큰을 넣어 주세요. "
            "(토큰을 채팅에 보내지 마세요)"
        )
    url = f"https://graph.facebook.com/{api_version()}/{path}"
    data = None
    if method == "GET":
        if params:
            url += "?" + urlencode(params)
    else:
        data = urlencode(params or {}).encode()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=data, method=method, headers=headers)
    try:
        with opener(req, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise GraphError(exc.code, "?", "-", "응답 본문을 해석할 수 없음") from None
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        message = redact(str(error.get("message", "Meta API 오류")), token)
        raise GraphError(exc.code, error.get("code", "?"), error.get("error_subcode", "-"), message) from None
    except URLError as exc:
        raise RuntimeError(f"네트워크 연결 실패 ({type(exc.reason).__name__})") from None
    if not isinstance(result, dict):
        raise RuntimeError("Graph API 응답이 JSON 객체가 아님")
    return redact(result, token)


def get(path: str, fields: str | None = None, **kw) -> dict:
    params = dict(kw.pop("params", None) or {})
    if fields:
        params["fields"] = fields
    return request("GET", path, params=params, **kw)


def post(path: str, params: dict, **kw) -> dict:
    return request("POST", path, params=params, **kw)
