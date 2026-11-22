"""소재(주제 + 출처) → 캐러셀 원고 JSON.

사실은 출처 본문에 있는 것만 쓴다. 출처가 없으면 fact_status='unverified' 로 표시해
검수 화면에서 승인 전에 반드시 확인하게 한다.
"""
from __future__ import annotations

import html
import re
from urllib.request import Request, urlopen

from . import claude

UA = "AnimalPick/0.1 (local editorial tool)"
MAX_SOURCE_CHARS = 30000  # 9000 이면 위키백과 긴 문서의 행동·번식 부분이 잘렸다

SYSTEM = """너는 한국어 인스타그램 매거진 '애니멀픽(ANIMAL PICK)'의 편집장이다.
동물·자연·과학·세상의 흥미로운 실제 이야기를 짧은 카드뉴스 캐러셀로 만든다.

반드시 지킬 것:
- 사실은 [출처 본문]에 있는 내용만 쓴다. 숫자·날짜·이름을 지어내지 않는다. 출처에 없는 내용은 쓰지 않는다.
- 출처 문장을 그대로 베끼지 말고 새 문장으로 쓴다.
- 문장은 짧게. 한 장에 핵심 하나. 'AI 같은' 장황한 표현, 과장, 낚시성 허위 금지. 이모지는 쓰지 않는다.
- 표지(cover) 제목은 강한 호기심을 주되 사실과 맞아야 한다. 22자 이내, 줄바꿈은 \\n 으로 최대 2줄.
- 본문(body) 장: headline 16자 이내, body 70자 이내 (2~3문장).
- 마지막(end) 장: 결말 또는 생각해 볼 질문 한 줄. 억지 댓글 유도 금지.
- 전체 5~8장 (cover 1, body 3~6, end 1).
- image_query: 그 장에 어울리는 사진을 위키미디어 공용에서 찾을 영어 검색어 (예: "red panda", "humpback whale breaching"). 동물 종·장소 위주로 구체적으로.
- image_subject: 이야기의 주인공 동물의 영어 일반 이름 (예: "Adelie penguin", "red panda"). 특정 동물이 아니면 "".
  사진 사이트에서 다른 종 사진을 걸러내는 데 쓴다.
- caption: 인스타 캡션 3~5줄. 첫 줄이 후킹. 마지막 줄에 '출처: 매체명'.
- hashtags: 한국어 위주 6~10개, # 없이.

JSON 하나만 출력한다:
{"topic": "...", "fact_status": "sourced" 또는 "unverified", "image_subject": "...",
 "slides": [{"kind": "cover"|"body"|"end", "headline": "...", "body": "...", "image_query": "..."}],
 "caption": "...", "hashtags": ["..."], "sources": [{"title": "...", "url": "..."}],
 "fact_notes": "검수자가 확인해야 할 사실·숫자 목록 (짧게)"}"""


def fetch_source_text(url: str, opener=urlopen) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with opener(req, timeout=20) as resp:
        raw = resp.read(2_000_000)
        charset = resp.headers.get_content_charset() if hasattr(resp, "headers") and resp.headers else None
    text = raw.decode(charset or "utf-8", errors="replace")
    return html_to_text(text)[:MAX_SOURCE_CHARS]


def html_to_text(doc: str) -> str:
    doc = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", doc)
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", doc)
    body = re.sub(r"(?s)<[^>]+>", " ", doc)
    body = html.unescape(re.sub(r"\s+", " ", body)).strip()
    return (html.unescape(title.group(1)).strip() + "\n\n" if title else "") + body


def build_prompt(topic: str, source_url: str, source_text: str) -> str:
    src = source_text.strip() or "(출처 본문 없음 — 확인되지 않은 소재. fact_status 는 unverified)"
    return f"[주제]\n{topic.strip()}\n\n[출처 URL]\n{source_url.strip() or '없음'}\n\n[출처 본문]\n{src}"


def normalize(data: dict, *, source_url: str = "", has_source: bool = False) -> dict:
    """AI 결과를 검사·정리. 필수 구조가 없으면 ValueError."""
    slides = [s for s in data.get("slides", []) if isinstance(s, dict) and str(s.get("headline", "")).strip()]
    if len(slides) < 3:
        raise ValueError("슬라이드가 3장 미만입니다")
    slides = slides[:10]  # 피드 캐러셀은 10장까지만 사용
    out = []
    for i, s in enumerate(slides):
        kind = "cover" if i == 0 else ("end" if i == len(slides) - 1 else "body")
        out.append({
            "kind": kind,
            "headline": str(s.get("headline", "")).replace("\\n", "\n").strip(),
            "body": str(s.get("body", "")).strip(),
            "image_query": str(s.get("image_query", "")).strip(),
            "image": None,
        })
    sources = [x for x in data.get("sources", []) if isinstance(x, dict) and x.get("url")]
    if source_url and not any(x["url"] == source_url for x in sources):
        sources.insert(0, {"title": data.get("topic", ""), "url": source_url})
    tags = [re.sub(r"[#\s]", "", str(t)) for t in data.get("hashtags", [])]
    return {
        "topic": str(data.get("topic", "")).strip(),
        "image_subject": str(data.get("image_subject", "")).strip(),
        "fact_status": "sourced" if has_source and data.get("fact_status") != "unverified" else "unverified",
        "slides": out,
        "caption": str(data.get("caption", "")).strip(),
        "hashtags": [t for t in tags if t][:10],
        "sources": sources,
        "fact_notes": str(data.get("fact_notes", "")).strip(),
    }


def write_draft(topic: str, source_url: str = "", source_text: str = "", *, asker=claude.ask,
                fetcher=fetch_source_text) -> dict:
    if not topic.strip():
        raise ValueError("주제를 입력하세요")
    text = source_text.strip()
    if not text and source_url.strip():
        try:
            text = fetcher(source_url.strip())
        except Exception as exc:  # 사이트가 막아도 원고는 만들되 미확인으로 표시
            text = ""
            note = f"(출처 페이지를 읽지 못함: {type(exc).__name__})"
            topic = f"{topic}\n{note}"
    data = claude.ask_json(SYSTEM, build_prompt(topic, source_url, text), asker=asker)
    return normalize(data, source_url=source_url.strip(), has_source=bool(text))
