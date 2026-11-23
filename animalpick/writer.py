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

# 레퍼런스 10개 계정 캐러셀 반응 분석(2026-11-23, 계정별 좋아요 중앙값 대비)에서 나온 규칙.
# 터진 것: 장면이 그려지는 구체적 상황·결과를 약속하는 제목·모음형. 망한 것: 사전식 사실 소개·모호한 제목.
HEADLINE_RULES = """표지 제목(headline) — 넘겨 보게 만드는 가장 중요한 한 줄:
- 무엇이(주인공 이름)·어디서·무슨 일이 있었는지 장면이 바로 그려지게 쓴다. 주인공을 숨기지 않는다.
- 가장 놀라운 한 부분을 작은따옴표로 강조한다. 예) 194살 거북 DNA에서 찾은 '늙지 않는 비밀' / 섬 전체가 '빨간 게'로 뒤덮였습니다
- 넘기면 무엇이 나오는지 약속한다: 구체적 숫자('1억 마리', '6가지'), '포착', '공개', '실제 모습'.
- 금지: 비유·시적 표현('빛 커튼'), '~의 정체', '~하는 이유', '~한 동물'처럼 주인공을 감춘 모호한 제목, 교과서식 소개.
- 22자 이내, 줄바꿈은 \\n 으로 최대 2줄. 끝은 명사형 또는 합니다체. '-다' 로 끝내지 않는다.
- headline_options 에 서로 다른 각도의 후보 5개를 쓰고, 그중 가장 센 것을 headline 에 쓴다."""

CAPTION_HOOK_RULES = """- 첫 줄: 가장 놀라운 사실이나 장면을 바로 말한다. '~아십니까?' 같은 퀴즈형 질문으로 시작하지 않는다.
- 백과사전식 나열(서식지·먹이·크기·분류)을 하지 않는다. 제목의 그 한 가지 포인트에 집중하고,
  나머지 문단은 그 포인트를 더 놀랍거나 재미있게 만드는 사실만 쓴다."""

SYSTEM = """너는 한국어 인스타그램 매거진 '애니멀픽(ANIMAL PICK)'의 편집장이다.
동물·과학·우주·자연현상·기술·세계의 별난 소식·문화 등 '신기한 세상 이야기'를 인스타그램 캐러셀로 만든다.
캐러셀: 1장 = 사진 위에 제목(표지). 2장부터 = 관련 사진 3장을 한 장에 모은 사진 모음(글자 없음).
이야기는 전부 캡션에서 한다. 사진은 허락된 자료 사진(위키미디어·NASA·무료 사진 사이트)만 쓴다.

사실 규칙:
- 사실은 [출처 본문]에 있는 내용만 쓴다. 숫자·날짜·이름을 지어내지 않는다. 출처에 없는 내용은 쓰지 않는다.
- 출처 문장을 그대로 베끼지 말고 새 문장으로 쓴다. 과장·낚시성 허위 금지.

""" + HEADLINE_RULES + """

표지(slides 의 첫 장, kind "cover"):
- headline: 위 규칙대로. headline_options: 후보 5개.
- body: "" (표지에 부제는 넣지 않는다)
- image_query: 표지 사진을 찾을 영어 검색어. 주인공이 크게 잘 보이는 장면으로 구체적으로 (예: "Adelie penguin carrying stone").

핵심 사진(key_images) — 0~3개, 없어도 된다:
- 이야기의 주인공이 '동물의 모습'이 아니라 '특정 장면·물건'일 때만 넣는다.
  예) 네모난 똥을 싸는 웜뱃 → 웜뱃의 네모난 똥 사진. 돌로 둥지를 짓는 펭귄 → 돌 둥지 사진.
  표지 사진과 비슷한 동물 사진은 넣지 않는다.
- scene: 그 사진이 보여 줄 장면(한국어 한 줄)
- image_query: 그 장면을 찾을 영어 검색어 (예: "wombat cube scat")
- must_match: 사진 제목·태그에 하나라도 있어야 하는 영어 단어들 (예: ["scat","poo","feces","dung","droppings"]).
  이 단어가 없는 사진은 쓰지 않으므로, 그 장면을 가리키는 흔한 영어 단어를 3~6개 넣는다.

사진 모음 검색어(gallery_queries) — 2~4개:
- 표지 다음 장들에 들어갈 '같은 주인공의 다른 모습·장면' 사진을 찾을 영어 검색어 (예: "red panda eating bamboo", "red panda cub").
- 반드시 주인공 이름을 넣는다. 서로 다른 장면이 나오게 쓴다.

캡션(caption) — 이야기의 본문. 가장 중요하다:
- 말투는 처음부터 끝까지 '-입니다', '-합니다', '-습니다', '-습니까' 로 끝나는 합니다체. '-다', '-요', '-까요' 로 끝내지 않는다 (질문도 '-ㅂ니까?').
""" + CAPTION_HOOK_RULES + """
- 그다음 빈 줄을 두고, 4~7개의 짧은 문단. 문단마다 1~2문장, 문단 사이는 빈 줄 하나.
- 중학생도 한 번에 이해할 쉬운 말로 쓴다. 전문 용어는 쓰지 않거나 바로 뒤에 쉬운 말로 풀어 준다.
- 숫자는 꼭 필요한 것만, 단위를 붙여 쉽게 읽히게 쓴다. 비교를 들 때도 출처에 없는 사실은 만들지 않는다.
- 이야기 순서: 무슨 일인가 → 왜 그런가 → 흥미로운 사실 → 마무리 한 문장(질문이나 여운). 억지 댓글 유도 금지.
- 출처에 없는 부분은 그냥 쓰지 않는다. '자료에는 나와 있지 않습니다' 같은 내부 사정을 캡션에 쓰지 않는다 (그런 내용은 fact_notes 에).
- 이모지는 쓰지 않는다. 길이는 400~900자.
- 마지막 줄: "출처: 매체명"

기타:
- image_subject: 이야기 주인공(동물·사물·장소·현상)의 영어 이름 (예: "Adelie penguin", "aurora", "Saturn", "humanoid robot").
  사진이 그 주인공이 맞는지 걸러내는 데 쓴다. 특정 종·장소 이야기면 그 종·장소의 정확한 이름을 쓴다
  (예: 조너선 거북 → "Aldabra giant tortoise", 'giant tortoise' 처럼 넓게 쓰면 다른 종 사진이 들어온다).
- hashtags: 한국어 위주 6~10개, # 없이.

JSON 하나만 출력한다:
{"topic": "...", "fact_status": "sourced" 또는 "unverified", "image_subject": "...",
 "slides": [{"kind": "cover", "headline": "...", "body": "", "image_query": "..."}],
 "headline_options": ["...", "...", "...", "...", "..."],
 "key_images": [{"scene": "...", "image_query": "...", "must_match": ["..."]}],
 "gallery_queries": ["...", "..."],
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
    cover = slides[0]  # 2장째는 표지 사진을 글자 없이 (render.plan_pages)
    out = [{
        "kind": "cover",
        "headline": str(cover.get("headline", "")).replace("\\n", "\n").strip(),
        "body": "",
        "image_query": str(cover.get("image_query", "")).strip(),
        "image": None,
    }]
    for k in (data.get("key_images") or [])[:3]:  # 이야기의 핵심 장면 사진 (있을 때만 붙는다)
        if not isinstance(k, dict) or not str(k.get("image_query", "")).strip():
            continue
        terms = [str(t).strip().lower() for t in (k.get("must_match") or []) if str(t).strip()]
        if not terms:
            continue  # 확인할 단어가 없으면 엉뚱한 사진을 막을 수 없으므로 쓰지 않는다
        out.append({"kind": "key", "headline": str(k.get("scene", "")).strip(), "body": "",
                    "image_query": str(k["image_query"]).strip(), "must_match": terms[:8], "image": None})
    sources = [x for x in data.get("sources", []) if isinstance(x, dict) and x.get("url")]
    if source_url and not any(x["url"] == source_url for x in sources):
        sources.insert(0, {"title": data.get("topic", ""), "url": source_url})
    tags = [re.sub(r"[#\s]", "", str(t)) for t in data.get("hashtags", [])]
    return {
        "topic": str(data.get("topic", "")).strip(),
        "image_subject": str(data.get("image_subject", "")).strip(),
        "gallery_queries": [str(q).strip() for q in (data.get("gallery_queries") or []) if str(q).strip()][:4],
        "headline_options": [str(h).replace("\\n", "\n").strip() for h in (data.get("headline_options") or [])
                             if str(h).strip()][:5],
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


# ---------- 모음형 (2026-11-23 추가) ----------
# 레퍼런스에서 가장 크게 터진 형식: 주제 하나 + 서로 다른 놀라운 대상 사진 6~9장 (예: '기술 발전으로 사라진 개들').
# 사진마다 작은 번호만 붙이고, 이름·설명은 캡션에 '① 이름: 설명' 으로.

LIST_PLAN_SYSTEM = """너는 한국어 인스타그램 매거진 '애니멀픽'의 기획자다. '모음형' 게시물을 기획한다.
모음형 = 주제 하나에 서로 다른 놀라운 대상 6~9개를 사진으로 보여 주는 게시물.
조건:
- 각 항목은 실제로 영어 위키백과에 문서가 있는 구체적인 대상(동물 종·장소·물건·현상·건축물 등)이어야 한다.
- 각 항목은 사진 한 장으로 '와' 소리가 나올 만큼 시각적으로 놀라워야 한다. 비슷한 대상끼리 겹치지 않게.
- 자료 사진(위키미디어 등)이 흔한 대상. 실존 개인·잔혹·성적·정치 소재 제외.
- 9개를 제안한다 (사진을 못 찾는 항목이 생길 수 있어서).
JSON 하나만: {"theme": "주제 한국어 한 줄",
 "items": [{"name_ko": "한국어 이름", "wiki_title": "영어 위키백과 문서 제목", "image_subject": "사진 확인용 짧은 영어 이름"}]}"""

LIST_WRITE_SYSTEM = """너는 한국어 인스타그램 매거진 '애니멀픽(ANIMAL PICK)'의 편집장이다. '모음형' 게시물을 쓴다.
캐러셀: 1장 표지(사진+제목) → 2장부터 항목 사진 3장씩(사진에는 ①②③ 번호만). 이름·설명은 캡션에.
사실 규칙: 항목마다 주어진 [출처 본문]에 있는 내용만 쓴다. 숫자·이름을 지어내지 않는다. 출처 문장을 베끼지 않는다.

""" + HEADLINE_RULES + """
- 모음형 제목은 항목 수와 주제를 약속한다. 예) 실제로 존재하는 '외계 행성 같은' 장소 6곳

항목(items): 받은 순서·번호 그대로. 항목마다
- fact: 그 대상에서 가장 놀라운 점 한 가지를 합니다체 1~2문장(70자 안팎)으로. 출처 본문에 있는 내용만.
- image_query: 그 대상 사진 검색어(영어, 대상 이름 포함 2~4단어).

캡션(caption_intro / caption_outro):
- 말투는 합니다체('-입니다/-합니다/-습니다/-습니까'). '-다', '-요' 로 끝내지 않는다. 이모지 쓰지 않는다.
- caption_intro: 첫 줄은 주제의 가장 놀라운 점을 바로 말하는 한 문장. 이어서 1~2문장. 퀴즈형 질문으로 시작하지 않는다.
- caption_outro: 마무리 한 문장 (질문이나 여운, 억지 댓글 유도 금지).
- 항목 목록은 프로그램이 '① 이름: 설명' 으로 자동으로 넣으므로 caption 에 쓰지 않는다.

기타: hashtags 한국어 위주 6~10개(# 없이). fact_notes: 검수자가 확인할 사실 목록(짧게).
JSON 하나만:
{"topic": "...", "headline": "...", "headline_options": ["...","...","...","...","..."], "cover_image_query": "...",
 "items": [{"n": 1, "name_ko": "...", "fact": "...", "image_query": "..."}],
 "caption_intro": "...", "caption_outro": "...", "hashtags": ["..."], "fact_notes": "..."}"""

CIRCLED = "①②③④⑤⑥⑦⑧⑨"


def plan_list(theme: str, *, asker=claude.ask) -> dict:
    return claude.ask_json(LIST_PLAN_SYSTEM, f"[주제]\n{theme.strip()}", asker=asker)


def write_list_draft(theme: str, items: list[dict] | None = None, *, asker=claude.ask,
                     fetcher=fetch_source_text, sleep=time.sleep, min_items: int = 3) -> dict:
    """모음형 원고. items 를 안 주면 AI 가 기획한다. 위키백과 문서가 확인된 항목만 쓴다."""
    if not theme.strip():
        raise ValueError("주제를 입력하세요")
    warnings = []
    plan = {"theme": theme, "items": items} if items else plan_list(theme, asker=asker)
    checked = []
    for it in (plan.get("items") or [])[:9]:
        title = str(it.get("wiki_title", "")).strip()
        if not title:
            continue
        url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        try:
            text = fetcher(url)
        except Exception:
            sleep(1)
            try:
                text = fetcher(url)
            except Exception as exc:
                warnings.append(f"'{it.get('name_ko', title)}' 위키백과 문서를 읽지 못해 뺐습니다 ({type(exc).__name__})")
                continue
        checked.append({"name_ko": str(it.get("name_ko", title)).strip(), "wiki_title": title, "wiki_url": url,
                        "image_subject": str(it.get("image_subject") or title).strip(), "text": text[:3500]})
    if len(checked) < min_items:
        raise RuntimeError(f"출처를 확인한 항목이 {len(checked)}개뿐이라 모음형을 만들 수 없습니다")
    prompt = f"[주제]\n{plan.get('theme') or theme}\n\n" + "\n\n".join(
        f"[항목 {i}] {c['name_ko']} ({c['wiki_title']})\n[출처 본문]\n{c['text']}" for i, c in enumerate(checked, 1))
    data = claude.ask_json(LIST_WRITE_SYSTEM, prompt, asker=asker)
    by_n = {int(x.get("n", 0)): x for x in data.get("items", []) if isinstance(x, dict) and str(x.get("n", "")).isdigit()}
    out_items = []
    for i, c in enumerate(checked, 1):
        w = by_n.get(i, {})
        fact = str(w.get("fact", "")).strip()
        if not fact:
            warnings.append(f"'{c['name_ko']}' 설명이 비어 있어 뺐습니다")
            continue
        out_items.append({"name_ko": c["name_ko"], "fact": fact, "wiki_url": c["wiki_url"],
                          "image_subject": c["image_subject"],
                          "image_query": str(w.get("image_query") or c["image_subject"]).strip(),
                          "image": None, "hidden": False})
    headline = str(data.get("headline", "")).replace("\\n", "\n").strip()
    if not headline:
        raise ValueError("표지 제목이 없습니다")
    tags = [re.sub(r"[#\s]", "", str(t)) for t in data.get("hashtags", [])]
    out = {
        "format": "list",
        "topic": str(data.get("topic") or plan.get("theme") or theme).strip(),
        "image_subject": out_items[0]["image_subject"] if out_items else "",
        "fact_status": "sourced",
        "slides": [{"kind": "cover", "headline": headline, "body": "",
                    "image_query": str(data.get("cover_image_query") or (out_items[0]["image_query"] if out_items else "")).strip(),
                    "image": None}],
        "headline_options": [str(h).replace("\\n", "\n").strip() for h in data.get("headline_options", []) if str(h).strip()][:5],
        "items": out_items,
        "caption_intro": str(data.get("caption_intro", "")).strip(),
        "caption_outro": str(data.get("caption_outro", "")).strip(),
        "caption": "",
        "hashtags": [t for t in tags if t][:10],
        "sources": [{"title": f"Wikipedia - {i['name_ko']}", "url": i["wiki_url"]} for i in out_items],
        "fact_notes": str(data.get("fact_notes", "")).strip(),
        "gallery_queries": [],
        "warnings": warnings,
    }
    bad = style_issues(out["caption_intro"] + "\n" + out["caption_outro"] + "\n" +
                       "\n".join(i["fact"] for i in out_items))
    if bad:
        warnings.append("합니다체가 아닌 문장: " + " / ".join(bad[:3]))
    return out


def compose_list_caption(draft: dict, shown: list[dict]) -> str:
    """캐러셀에 실제로 들어간 항목 순서대로 '① 이름: 설명' 목록을 만든다 (번호가 사진 번호와 같다)."""
    lines = [l for l in (draft.get("caption_intro", ""),) if l]
    if shown:
        lines.append("\n".join(f"{CIRCLED[i]} {it['name_ko']}: {it['fact']}" for i, it in enumerate(shown)))
    if draft.get("caption_outro"):
        lines.append(draft["caption_outro"])
    lines.append("출처: Wikipedia")
    return "\n\n".join(lines)
