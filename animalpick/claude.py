"""Claude Agent SDK 래퍼 — 구독(OAuth) 인증 고정.

어치의 다른 프로젝트 server/claude.js 와 같은 규칙:
- ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 을 지운다 (남아 있으면 종량 과금으로 빠짐).
- 다른 Claude 세션에서 상속된 호스트 전용 변수도 지운다.
- 도구 없는 단발 텍스트 생성만 한다 (allowed_tools=[], max_turns=1).
"""
from __future__ import annotations

import asyncio
import json
import os
import re

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
_HOST_ONLY = re.compile(r"^(ANTHROPIC_BASE_URL|CLAUDECODE|CLAUDE_PID|CLAUDE_AGENT_SDK_VERSION|CLAUDE_CODE_(?!OAUTH_TOKEN$).+)$")
for _k in [k for k in os.environ if _HOST_ONLY.match(k)]:
    os.environ.pop(_k, None)


class NotLoggedInError(RuntimeError):
    def __init__(self):
        super().__init__("Claude 구독 계정에 로그인되어 있지 않습니다. 터미널에서 `claude` 를 실행한 뒤 `/login` 으로 로그인하세요.")


async def _ask(system: str, prompt: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    options = ClaudeAgentOptions(system_prompt=system, allowed_tools=[], tools=[], max_turns=1,
                                 setting_sources=[])
    result = None
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                if msg.is_error:
                    raise RuntimeError(msg.result or "알 수 없는 오류")
                result = msg.result
    except Exception as exc:  # SDK 는 여러 예외 타입을 던진다
        if re.search(r"not logged in|/login", str(exc), re.I):
            raise NotLoggedInError() from None
        raise
    if not result:
        raise RuntimeError("AI 응답이 비어 있습니다")
    if re.search(r"not logged in|/login", result, re.I) and len(result) < 200:
        raise NotLoggedInError()
    return result


def ask(system: str, prompt: str) -> str:
    return asyncio.run(_ask(system, prompt))


def extract_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    body = fenced.group(1) if fenced else text
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 응답에서 JSON 을 찾지 못했습니다")
    return json.loads(body[start:end + 1])


def ask_json(system: str, prompt: str, *, asker=ask, retries: int = 1) -> dict:
    last = None
    for _ in range(retries + 1):
        try:
            return extract_json(asker(system, prompt))
        except (ValueError, json.JSONDecodeError) as exc:
            last = exc
    raise RuntimeError(f"AI 가 올바른 JSON 을 주지 않았습니다: {last}")
