"""소재(주제 + 출처) → 캐러셀 원고 JSON.

사실은 출처 본문에 있는 것만 쓴다. 출처가 없으면 fact_status='unverified' 로 표시해
검수 화면에서 승인 전에 반드시 확인하게 한다.
"""
from __future__ import annotations

import html
import json
import re
import time
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

from . import claude

UA = "AnimalPick/0.1 (local editorial tool)"
MAX_SOURCE_CHARS = 30000  # 9000 이면 위키백과 긴 문서의 행동·번식 부분이 잘렸다

SYSTEM = """너는 한국어 인스타그램 매거진 '애니멀픽(ANIMAL PICK)'의 편집장이다.
동물·자연·과학·세상의 흥미로운 실제 이야기를 '표지 + 표지 사진 + 설명란(캡션)' 형식으로 만든다.
캐러셀은 2장뿐이다: 1장 = 사진 위에 제목, 2장 = 같은 사진(글자 없음). 이야기는 전부 캡션에서 한다.

사실 규칙:
- 사실은 [출처 본문]에 있는 내용만 쓴다. 숫자·날짜·이름을 지어내지 않는다. 출처에 없는 내용은 쓰지 않는다.
- 출처 문장을 그대로 베끼지 말고 새 문장으로 쓴다. 과장·낚시성 허위 금지.

표지(slides 의 첫 장, kind "cover"):
- headline: 강한 호기심을 주되 사실과 맞는 제목. 22자 이내, 줄바꿈은 \\n 으로 최대 2줄.
  끝은 명사형(예: '돌로 집을 짓는 펭귄') 또는 합니다체(예: '돌로 집을 짓습니다'). '-다' 로 끝내지 않는다.
- body: "" (표지에 부제는 넣지 않는다)
- image_query: 표지 사진을 찾을 영어 검색어. 주인공이 크게 잘 보이는 장면으로 구체적으로 (예: "Adelie penguin carrying stone").

캡션(caption) — 이야기의 본문. 가장 중요하다:
- 말투는 처음부터 끝까지 '-입니다', '-합니다', '-습니다', '-습니까' 로 끝나는 합니다체. '-다', '-요', '-까요' 로 끝내지 않는다 (질문도 '-ㅂ니까?').
- 첫 줄: 궁금증을 주는 한 문장.
- 그다음 빈 줄을 두고, 4~7개의 짧은 문단. 문단마다 1~2문장, 문단 사이는 빈 줄 하나.
- 중학생도 한 번에 이해할 쉬운 말로 쓴다. 전문 용어는 쓰지 않거나 바로 뒤에 쉬운 말로 풀어 준다.
- 숫자는 꼭 필요한 것만, 단위를 붙여 쉽게 읽히게 쓴다. 비교를 들 때도 출처에 없는 사실은 만들지 않는다.
- 이야기 순서: 무슨 일인가 → 왜 그런가 → 흥미로운 사실 → 마무리 한 문장(질문이나 여운). 억지 댓글 유도 금지.
- 출처에 없는 부분은 그냥 쓰지 않는다. '자료에는 나와 있지 않습니다' 같은 내부 사정을 캡션에 쓰지 않는다 (그런 내용은 fact_notes 에).
- 이모지는 쓰지 않는다. 길이는 400~900자.
- 마지막 줄: "출처: 매체명"

기타:
- image_subject: 이야기의 주인공 동물의 영어 일반 이름 (예: "Adelie penguin", "red panda"). 특정 동물이 아니면 "".
- hashtags: 한국어 위주 6~10개, # 없이.

JSON 하나만 출력한다:
{"topic": "...", "fact_status": "sourced" 또는 "unverified", "image_subject": "...",
 "slides": [{"kind": "cover", "headline": "...", "body": "", "image_query": "..."}],
 "caption": "...", "hashtags": ["..."], "sources": [{"title": "...", "url": "..."}],
 "fact_notes": "검수자가 확인해야 할 사실·숫자 목록 (짧게)"}"""


WIKI_RE = re.compile(r"^https?://([a-z]{2,3})\.(?:m\.)?wikipedia\.org/wiki/([^?#]+)")


def fetch_wikipedia(url: str, opener=urlopen) -> str | None:
    """위키백과는 HTML 대신 공식 API 의 순수 본문(extract)을 받는다. 위키백과 주소가 아니면 None."""
    m = WIKI_RE.match(url)
    if not m:
        return None
    lang, title = m.group(1), unquote(m.group(2))
    api = (f"https://{lang}.wikipedia.org/w/api.php?" +
           urlencode({"action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1,
                      "titles": title.replace("_", " "), "format": "json", "formatversion": 2}))
    with opener(Request(api, headers={"User-Agent": UA}), timeout=20) as resp:
        data = json.load(resp)
    pages = (data.get("query") or {}).get("pages") or []
    text = (pages[0].get("extract") or "") if pages else ""
    if not text.strip():
        raise ValueError("위키백과 본문이 비어 있습니다")
    return f"{pages[0].get('title', title)} - Wikipedia\n\n{text}"[:MAX_SOURCE_CHARS]


def fetch_source_text(url: str, opener=urlopen) -> str:
    wiki = fetch_wikipedia(url, opener)
    if wiki is not None:
        return wiki
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
    if not slides:
        raise ValueError("표지가 없습니다")
    slides = slides[:1]  # 표지 하나만 쓴다 (2장째는 표지 사진을 글자 없이)
    out = []
    for i, s in enumerate(slides):
        kind = "cover" if i == 0 else "body"  # 마지막 글자 장(end)은 없앴다 — 설명은 캡션으로
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
                fetcher=fetch_source_text, sleep=time.sleep) -> dict:
    if not topic.strip():
        raise ValueError("주제를 입력하세요")
    text = source_text.strip()
    warnings = []
    if not text and source_url.strip():
        for attempt in range(2):  # 일시 실패 대비 한 번 더
            try:
                text = fetcher(source_url.strip())
                break
            except Exception as exc:  # 사이트가 막아도 원고는 만들되 미확인으로 표시
                text, err = "", f"{type(exc).__name__}: {str(exc)[:80]}"
                if attempt == 0:
                    sleep(2)
        if not text:
            warnings.append(f"출처 페이지를 읽지 못했습니다 ({err}). 원고 사실을 반드시 직접 확인하세요.")
            topic = f"{topic}\n(출처 페이지를 읽지 못함)"
    data = claude.ask_json(SYSTEM, build_prompt(topic, source_url, text), asker=asker)
    out = normalize(data, source_url=source_url.strip(), has_source=bool(text))
    bad = style_issues(out["caption"])
    if bad:
        warnings.append("합니다체가 아닌 문장: " + " / ".join(bad[:3]))
    out["warnings"] = warnings
    return out


def style_issues(caption: str) -> list[str]:
    """'-요' '-다'(합니다체 '-니다' 제외)로 끝나는 문장을 찾는다. 출처 줄은 제외."""
    out = []
    for line in caption.splitlines():
        line = line.strip()
        if not line or line.startswith("출처"):
            continue
        for sent in re.split(r"(?<=[.?!])\s+", line):
            core = sent.rstrip(" .?!\"'”’)")
            if core.endswith("요") or (core.endswith("다") and not core.endswith("니다")):
                out.append(sent[-25:])
    return out
