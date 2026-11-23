"""주제 소싱 — '오늘의 후보' 자동 수집·선별.

흐름 (collect):
1. 수집: 해외 과학·동물 RSS + 연합뉴스 + (키가 있으면) 네이버 뉴스 검색 + AI 상시 소재 기획(위키백과 확인)
2. 1차 거르기(코드): 최근 7일, 동물 키워드, 이미 모은 후보·만든 원고와 중복 제거
3. AI 점수: 호기심·감정·공유·한국 관련성·시각·사실 확인 + 제외 판정 + '그 개체 사진이 꼭 필요한가'
4. 사진 사전 확인: 상위 후보만 실제 검색 → 쓸 수 있는 사진이 없으면 제외, 적으면 감점
5. 저장(SQLite candidates) → 대시보드에서 원고 만들기, 또는 하루 구성 비율대로 자동 원고 생성

기사는 주제 선정과 사실 확인에만 쓴다. 기사 문장·사진은 쓰지 않는다 (2026-11-22 실측 기반 설계).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from . import claude, config, db, images

UA = "Mozilla/5.0 (compatible; AnimalPick/0.1; local editorial tool)"

# 2026-11-22 실측으로 작동·동물 기사 확인된 피드만 (EurekAlert·정책브리핑·한겨레 애니멀피플·KBS RSS 는 404,
# Google 뉴스는 이용 조건 불명확, 네이버 검색 API 는 신규 앱에서 선택 불가로 확인됨 — 키가 이미 있을 때만 사용)
FEEDS = [
    # 동물 (동물 키워드로 거름)
    ("news_intl", "Phys.org 동식물", "https://phys.org/rss-feed/biology-news/plants-animals/", "animal"),
    ("news_intl", "Phys.org 생태", "https://phys.org/rss-feed/biology-news/ecology/", "animal"),
    ("news_intl", "ScienceDaily 동물", "https://www.sciencedaily.com/rss/plants_animals/animals.xml", "animal"),
    ("news_intl", "Guardian 동물", "https://www.theguardian.com/world/animals/rss", "animal"),
    ("news_intl", "Guardian 야생동물", "https://www.theguardian.com/environment/wildlife/rss", "animal"),
    ("news_intl", "Mongabay", "https://news.mongabay.com/feed/", "animal"),
    ("news_kr", "뉴스펭귄", "https://www.newspenguin.com/rss/allArticle.xml", "animal"),  # 환경·멸종위기종 전문
    # 신기한 세상 이야기 (2026-11-23 사용자 요청으로 확장: 과학·우주·기술·별난 소식·문화. 금지어만 거름)
    ("news_intl", "ScienceDaily 별난 과학", "https://www.sciencedaily.com/rss/strange_offbeat.xml", "broad"),
    ("news_intl", "ScienceDaily 최신 과학", "https://www.sciencedaily.com/rss/top/science.xml", "broad"),
    ("news_intl", "Phys.org 우주", "https://phys.org/rss-feed/space-news/", "broad"),
    ("news_intl", "NASA 소식", "https://www.nasa.gov/feed/", "broad"),
    ("news_intl", "Space.com", "https://www.space.com/feeds/all", "broad"),
    ("news_intl", "Live Science", "https://www.livescience.com/feeds/all", "broad"),
    ("news_intl", "Guardian 과학", "https://www.theguardian.com/science/rss", "broad"),
    ("news_intl", "BBC 과학·환경", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "broad"),
    ("news_intl", "NYT 과학", "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml", "broad"),
    ("news_intl", "Smithsonian 과학·자연", "https://www.smithsonianmag.com/rss/science-nature/", "broad"),
    ("news_intl", "New Atlas", "https://newatlas.com/index.rss", "broad"),
    ("news_intl", "IEEE Spectrum 로봇", "https://spectrum.ieee.org/feeds/topic/robotics.rss", "broad"),
    ("news_intl", "Atlas Obscura", "https://www.atlasobscura.com/feeds/latest", "broad"),
    ("news_intl", "UPI 별난 뉴스", "https://rss.upi.com/news/odd_news.rss", "broad"),
    ("news_intl", "Good News Network", "https://www.goodnewsnetwork.org/feed/", "broad"),
    ("news_kr", "연합뉴스 국제", "https://www.yna.co.kr/rss/international.xml", "broad"),
    ("news_kr", "연합뉴스 문화", "https://www.yna.co.kr/rss/culture.xml", "broad"),
    ("news_kr", "연합뉴스 사회", "https://www.yna.co.kr/rss/society.xml", "animal"),
]
BROAD_CAP = 20  # 넓은 피드는 최신 20개만 AI 평가로 (비용·시간 관리)
# 제목만 봐도 애니멀픽에서 다루지 않을 소재 (AI 평가 전에 미리 거름)
BLOCK_EN = re.compile(r"\b(sex|sexual|porn|nude|rape|murder|murdered|killed|killing|shooting|stabbing|terror|war|"
                      r"election|president|senate|congress|trump|tariff|stocks?|shares|earnings|ipo|lawsuit|"
                      r"cancer|diabetes|vaccine|drug|overdose|suicide|abuse)\b", re.I)
BLOCK_KO = re.compile(r"성관계|성폭|성범죄|살해|살인|숨진|사망|흉기|총격|테러|전쟁|대통령|국회|선거|여당|야당|"
                      r"증시|주가|환율|금리|관세|소송|마약|자살|학대|암 치료|치매")
NAVER_QUERIES = ["멸종위기 동물", "야생동물 발견", "동물원 새끼", "동물 구조", "신종 발견", "철새", "고래 돌고래", "희귀 동물"]

ANIMAL_EN = re.compile(r"\b(animal|bird|fish|shark|whale|dolphin|penguin|bear|wolf|fox|cat|dog|lion|tiger|elephant|monkey|ape|chimp|chimpanzee|gorilla|frog|toad|snake|lizard|turtle|tortoise|insect|bee|ant|spider|octopus|squid|crab|owl|parrot|bat|mouse|rat|deer|horse|pig|seal|otter|panda|koala|kangaroo|species|wildlife|mammal|reptile|amphibian|predator|crocodile|alligator|eagle|hawk|crow|raven|jellyfish|coral|butterfly|moth|beetle|salamander|axolotl|hippo|giraffe|zebra|rhino|leopard|cheetah|jaguar|hyena|seabird|songbird|baboon|lemur|sloth|squirrel|rabbit|hedgehog|orca|manatee|walrus|puffin|flamingo|pelican|swan|goose|duck|hummingbird|gecko|chameleon|iguana|cicada|firefly|scorpion|snail|slug|starfish|seahorse|eel|salmon|tuna|cod|herring|ray|sea\s+lion|sea\s+turtle|coyote|moose|bison|camel|llama|goat|sheep|cow|chicken|primate|rodent|dinosaur)s?\b", re.I)
ANIMAL_KO = re.compile(r"동물|새끼|고양이|강아지|반려|멸종|야생|조류|어류|곤충|고래|돌고래|상어|(?<![가-힣])곰(?![곰])|여우|늑대|호랑이|표범|(?<![가-힣])판다|펭귄|거북|(?<![가-힣])뱀|개구리|수달|물범|사슴|원숭이|코끼리|(?<![봉검판])사자|생물|생태|철새|두루미|황새|저어새|농게|산양|삵|하늘다람쥐|독수리|부엉이")

WEIGHTS = {"curiosity": 1.3, "emotion": 1.2, "share": 1.2, "korea": 1.0, "visual": 1.0, "factual": 1.3}
DAILY_MIX = {"news_intl": 5, "news_kr": 2, "evergreen": 3}  # 하루 10개 목표 구성
CATEGORY_CAP = 4  # 하루 선정에서 한 분야(예: 동물)는 최대 4개 — 여러 분야가 섞이게
NEWS_DAYS = 7


# ---------- 공통 ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def title_key(title: str) -> str:
    t = re.sub(r"\s+-\s+[^-]+$", "", title)  # '제목 - 매체명' 꼬리 제거
    return re.sub(r"[\W_]+", "", t.lower())[:80]


def cand_id(link: str) -> str:
    return hashlib.sha1(link.encode()).hexdigest()[:12]


def _get(url: str, headers: dict | None = None, opener=urlopen, timeout: int = 20) -> bytes:
    with opener(Request(url, headers={"User-Agent": UA, **(headers or {})}), timeout=timeout) as r:
        return r.read(5_000_000)


# ---------- 1. 수집 ----------

def _txt(el, tag) -> str:
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def _parse_date(s: str):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0 / Atom 공용. [{title, link, published}]"""
    root = ET.fromstring(data)
    out = []
    items = root.findall(".//item")
    if items:
        for it in items:
            d = _txt(it, "pubDate") or _txt(it, "{http://purl.org/dc/elements/1.1/}date")
            out.append({"title": html.unescape(_txt(it, "title")), "link": _txt(it, "link"), "published": _parse_date(d)})
    else:
        ns = "{http://www.w3.org/2005/Atom}"
        for it in root.findall(f".//{ns}entry"):
            link = it.find(f"{ns}link")
            d = _txt(it, f"{ns}published") or _txt(it, f"{ns}updated")
            out.append({"title": html.unescape(_txt(it, f"{ns}title")),
                        "link": link.get("href", "") if link is not None else "", "published": _parse_date(d)})
    return [x for x in out if x["title"] and x["link"]]


def is_recent(published, now: datetime, days: int = NEWS_DAYS) -> bool:
    return published is None or now - published <= timedelta(days=days)


def looks_animal(title: str) -> bool:
    return bool(ANIMAL_KO.search(title) if re.search(r"[가-힣]", title) else ANIMAL_EN.search(title))


def blocked(title: str) -> bool:
    return bool(BLOCK_KO.search(title) if re.search(r"[가-힣]", title) else BLOCK_EN.search(title))


def collect_feeds(*, fetch=_get, now: datetime | None = None, log=print) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out = []
    for feed in FEEDS:
        kind, name, url = feed[:3]
        mode = feed[3] if len(feed) > 3 else "animal"
        try:
            items = parse_feed(fetch(url))
        except Exception as exc:
            log(f"  [건너뜀] {name}: {type(exc).__name__}")
            continue
        recent = [i for i in items if is_recent(i["published"], now) and not blocked(i["title"])]
        if mode == "animal":
            keep = [i for i in recent if looks_animal(i["title"])]
            log(f"  {name}: {len(items)}개 중 동물 기사 {len(keep)}개")
        else:
            keep = recent[:BROAD_CAP]
            log(f"  {name}: {len(items)}개 중 {len(keep)}개")
        for i in keep:
            out.append({"kind": kind, "source": name, "title": i["title"], "link": i["link"],
                        "published_at": i["published"].isoformat() if i["published"] else None})
    return out


def collect_naver(*, fetch=_get, now: datetime | None = None, log=print) -> list[dict]:
    cid, secret = config.env("NAVER_CLIENT_ID"), config.env("NAVER_CLIENT_SECRET")
    if not (cid and secret):
        log("  [건너뜀] 네이버 뉴스: 키 없음 (.env 의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)")
        return []
    now = now or datetime.now(timezone.utc)
    out = []
    for q in NAVER_QUERIES:
        url = "https://openapi.naver.com/v1/search/news.json?" + urlencode({"query": q, "display": 30, "sort": "date"})
        try:
            data = json.loads(fetch(url, {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}))
        except HTTPError as exc:
            log(f"  [건너뜀] 네이버 '{q}': HTTP {exc.code}" + (" (키 확인)" if exc.code in (401, 403) else ""))
            continue
        except Exception as exc:
            log(f"  [건너뜀] 네이버 '{q}': {type(exc).__name__}")
            continue
        n = 0
        for it in data.get("items", []):
            title = html.unescape(re.sub(r"<[^>]+>", "", it.get("title", "")))
            pub = _parse_date(it.get("pubDate", ""))
            if not title or not is_recent(pub, now) or not looks_animal(title):
                continue
            out.append({"kind": "news_kr", "source": f"네이버 뉴스 '{q}'", "title": title,
                        "link": it.get("originallink") or it.get("link", ""),
                        "published_at": pub.isoformat() if pub else None})
            n += 1
        log(f"  네이버 '{q}': 동물 기사 {n}개")
    return out


EVERGREEN_SYSTEM = """너는 한국어 인스타그램 매거진 '애니멀픽'의 기획자다. 애니멀픽은 '신기한 세상 이야기'를 다룬다.
뉴스가 아니어도 언제 올려도 통하는 소재를 제안한다. 분야를 고르게 섞는다:
동물, 자연현상(오로라·싱크홀 등), 우주, 신기한 장소·건축, 발명·기술·로봇, 세계의 별난 문화·축제·음식, 역사 속 미스터리.
조건: 실제로 영어 위키백과에 문서가 있는 주제, 한 가지 놀라운 사실로 이야기가 되는 것, 사진(자료 사진)이 흔한 주제,
잔혹·질병·정치·성적인 소재와 실존 개인이 주인공인 이야기 제외, [이미 다룬 주제] 목록과 겹치지 않을 것.
JSON 하나만: {"items":[{"subject":"영어 이름(동물·사물·장소·현상)","wiki_title":"영어 위키백과 문서 제목","angle":"한국어 한 줄 관점"}]}"""


def wiki_exists(title: str, *, fetch=_get) -> str | None:
    """영어 위키백과 문서가 있으면 정식 주소, 없으면 None."""
    try:
        data = json.loads(fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_"))))
    except Exception:
        return None
    if data.get("type") == "disambiguation":
        return None
    return (data.get("content_urls") or {}).get("desktop", {}).get("page")


def collect_evergreen(n: int, used_subjects: list[str], *, asker=claude.ask, fetch=_get, log=print) -> list[dict]:
    if n <= 0:
        return []
    prompt = f"[제안 개수]\n{n + 3}\n\n[이미 다룬 주제]\n" + (", ".join(used_subjects[-200:]) or "없음")
    try:
        data = claude.ask_json(EVERGREEN_SYSTEM, prompt, asker=asker)
    except Exception as exc:
        log(f"  [건너뜀] 상시 소재 기획: {exc}")
        return []
    used = {s.lower() for s in used_subjects}
    out = []
    for it in data.get("items", []):
        subj, wt = str(it.get("subject", "")).strip(), str(it.get("wiki_title", "")).strip()
        if not subj or not wt or subj.lower() in used:
            continue
        url = wiki_exists(wt, fetch=fetch)
        if not url:
            continue
        out.append({"kind": "evergreen", "source": "상시 소재 (위키백과)",
                    "title": f"{subj}: {it.get('angle', '')}".strip(), "link": url, "published_at": None,
                    "subject_hint": subj})
        if len(out) >= n:
            break
    log(f"  상시 소재: {len(out)}개 (위키백과 문서 확인)")
    return out


# ---------- 2. 중복 제거 ----------

def dedupe(items: list[dict], conn) -> list[dict]:
    """이미 모은 후보(링크·제목), 이번 수집 안의 중복을 뺀다."""
    known_ids = {r[0] for r in conn.execute("SELECT id FROM candidates")}
    known_keys = {r[0] for r in conn.execute("SELECT title_key FROM candidates")}
    out, seen = [], set()
    for it in items:
        cid, key = cand_id(it["link"]), title_key(it["title"])
        if cid in known_ids or key in known_keys or key in seen or cid in seen:
            continue
        seen.update((cid, key))
        out.append({**it, "id": cid, "title_key": key})
    return out


# ---------- 3. AI 점수 ----------

SCORE_SYSTEM = """너는 한국어 인스타그램 매거진 '애니멀픽'의 기획자다. 애니멀픽은 '신기한 세상 이야기'를 다룬다:
동물, 과학, 우주, 자연현상, 기술·로봇·발명, 세계의 별난 소식·문화·축제·음식, 역사. 후보 목록을 평가한다.
애니멀픽 형식: 표지(사진+제목) 1장 + 관련 사진 모음, 이야기는 캡션. 사진은 허락된 자료 사진만 쓴다
(위키미디어·NASA·무료 사진 사이트. 뉴스 사진·특정 인물 사진은 쓸 수 없다).
점수 1~5 정수: curiosity(호기심), emotion(귀여움·놀라움·경이·뭉클함), share(친구에게 보내고 싶음),
korea(한국 독자 관심·국내에 덜 알려짐), visual(자료 사진으로 눈길을 끌 수 있음), factual(믿을 만한 출처·과장 위험 낮음).
needs_event_photo: 이야기가 '그 특정 개체·사람·사건 현장'의 사진 없이는 성립하지 않으면 true
  (예: 뿔이 하나인 특정 사슴, 특정 인물의 외모·변화, 특정 사고 현장). 주제 일반 자료 사진으로 충분하면 false.
exclude=true: 성적인 소재, 범죄·사고·재난의 피해자나 실존 일반인이 주인공인 이야기, 정치·선거·전쟁,
  잔혹·사체·학대, 의료·건강·다이어트 주장, 기업 실적·주식·제품 광고·출시, 연예인 가십,
  칼럼·퀴즈·행사·행정 소식, 목록 안 중복.
category: animal / science / space / nature / tech / world / culture / food / history 중 하나.
subject: 주인공(동물·사물·장소·현상)의 영어 이름. photo_query: 그 주인공 사진 검색어(영어 2~4단어).
ko_title: 표지 제목 초안(22자 이내, 명사형 또는 합니다체, '-다'로 끝내지 않음).
JSON 하나만: {"items":[{"i":번호,"exclude":false,"needs_event_photo":false,"reason":"한 줄 평 또는 제외 이유",
"category":"","subject":"","photo_query":"","ko_title":"","curiosity":0,"emotion":0,"share":0,"korea":0,"visual":0,"factual":0}]}"""


def weighted(item: dict) -> float:
    total = 0.0
    for k, w in WEIGHTS.items():
        try:
            v = max(0, min(5, int(item.get(k, 0) or 0)))
        except (TypeError, ValueError):
            v = 0
        total += w * v
    return round(total, 1)


def score_items(items: list[dict], *, asker=claude.ask, chunk: int = 40, log=print) -> list[dict]:
    out = []
    for start in range(0, len(items), chunk):
        part = items[start:start + chunk]
        lines = "\n".join(f"{i}. [{p['source']}] {p['title']}" for i, p in enumerate(part))
        try:
            res = claude.ask_json(SCORE_SYSTEM, lines, asker=asker)
        except Exception as exc:
            log(f"  [AI 평가 실패] {len(part)}개: {exc}")
            continue
        for r in res.get("items", []):
            i = r.get("i")
            if not isinstance(i, int) or not 0 <= i < len(part):
                continue
            p = part[i]
            excluded = bool(r.get("exclude")) or bool(r.get("needs_event_photo"))
            reason = str(r.get("reason", "")).strip()
            if r.get("needs_event_photo") and not r.get("exclude"):
                reason = "그 개체 사진이 꼭 필요한 이야기 (자료 사진으로는 오해 소지) · " + reason
            out.append({**p,
                        "ko_title": str(r.get("ko_title", "")).strip(),
                        "subject": str(r.get("subject", "") or p.get("subject_hint", "")).strip(),
                        "photo_query": str(r.get("photo_query", "")).strip(),
                        "category": str(r.get("category", "")).strip().lower(),
                        "scores": {k: r.get(k) for k in WEIGHTS},
                        "score": weighted(r),
                        "status": "excluded" if excluded else "new",
                        "reason": reason})
    return out


# ---------- 4. 사진 사전 확인 ----------

def photo_adjust(n: int) -> float:
    return -4.0 if n <= 2 else (-1.0 if n <= 5 else 0.0)


def check_photos(items: list[dict], *, top: int = 30, finder=images.search, pause=time.sleep, log=print) -> None:
    """점수 상위 후보만 실제로 사진을 검색해 본다. 0장이면 제외, 적으면 감점."""
    ranked = sorted([i for i in items if i["status"] == "new"], key=lambda i: -i["score"])[:top]
    for n, it in enumerate(ranked):
        if n:
            pause(1.0)
        found = {}
        for q in dict.fromkeys(x for x in (it["photo_query"], it["subject"]) if x):
            try:
                for c in finder(q):
                    if images.matches_subject(c, it["subject"]):
                        found[c["page_url"]] = c
            except Exception:
                continue
        it["photos"] = len(found)
        if not found:
            it["status"], it["reason"] = "excluded", "쓸 수 있는 사진 없음 · " + it["reason"]
        else:
            it["score"] = round(it["score"] + photo_adjust(len(found)), 1)
    log(f"  사진 사전 확인 {len(ranked)}개 완료")


# ---------- 5. 저장 · 선택 ----------

def save(conn, items: list[dict]) -> int:
    ts = now_iso()
    for it in items:
        conn.execute(
            """INSERT OR IGNORE INTO candidates
               (id, kind, source, title, title_key, link, published_at, collected_at, ko_title, subject, photo_query,
                scores, score, photos, status, reason, category)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (it["id"], it["kind"], it["source"], it["title"], it["title_key"], it["link"], it.get("published_at"), ts,
             it.get("ko_title"), it.get("subject"), it.get("photo_query"), json.dumps(it.get("scores") or {}),
             it.get("score"), it.get("photos"), it.get("status", "new"), it.get("reason"), it.get("category")))
    conn.commit()
    return len(items)


def rows(conn, where: str = "1=1", args: tuple = ()) -> list[dict]:
    cur = conn.execute(f"SELECT * FROM candidates WHERE {where}", args)
    cols = [c[0] for c in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d["scores"] = json.loads(d["scores"] or "{}")
        out.append(d)
    return out


def list_candidates(conn, *, days: int = 3, include_excluded: bool = False) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cond = "collected_at >= ?" + ("" if include_excluded else " AND status = 'new'")
    return sorted(rows(conn, cond, (since,)), key=lambda r: -(r["score"] or 0))


def pick_daily(cands: list[dict], mix: dict | None = None) -> list[dict]:
    """하루 구성 비율(해외 5 · 국내 2 · 상시 3)대로 고르고, 모자란 자리는 다른 종류 상위로 채운다.
    사진 확인을 통과한 후보(photos > 0)만. 같은 주인공 동물은 하루에 한 번만."""
    mix = mix or DAILY_MIX
    pool = sorted([c for c in cands if c["status"] == "new" and (c.get("photos") or 0) > 0], key=lambda c: -c["score"])
    chosen: list[dict] = []
    taken: list[set] = []

    def words(s: str) -> set:
        return {w.rstrip("s") for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2}

    def take(c):
        # 'red crab' 과 'Christmas Island red crab' 처럼 한쪽 이름이 다른 쪽에 포함되면 같은 동물로 본다
        w = words(c.get("subject") or "")
        if c in chosen or (w and any(w <= t or t <= w for t in taken)):
            return False
        cat = c.get("category") or ""
        if cat and sum(1 for x in chosen if x.get("category") == cat) >= CATEGORY_CAP:
            return False
        chosen.append(c)
        if w:
            taken.append(w)
        return True

    for kind, n in mix.items():
        got = 0
        for c in pool:
            if got >= n:
                break
            if c["kind"] == kind and take(c):
                got += 1
    for c in pool:
        if len(chosen) >= sum(mix.values()):
            break
        take(c)
    return chosen


def set_status(conn, cid: str, status: str, draft_id: str | None = None) -> None:
    conn.execute("UPDATE candidates SET status = ?, draft_id = COALESCE(?, draft_id) WHERE id = ?", (status, draft_id, cid))
    conn.commit()


def used_subjects(conn) -> list[str]:
    subs = [r[0] for r in conn.execute("SELECT subject FROM candidates WHERE status = 'drafted' AND subject != ''")]
    from . import drafts as _d
    subs += [d.get("image_subject", "") for d in _d.list_all() if d.get("image_subject")]
    return list(dict.fromkeys(subs))


# ---------- 전체 실행 ----------

def collect(*, conn=None, asker=claude.ask, fetch=_get, finder=images.search, evergreen: int | None = None,
            log=print, pause=time.sleep) -> dict:
    config.load_env(override=True)
    own = conn is None
    conn = conn or db.connect(config.DB_PATH)
    try:
        log("1) 수집")
        items = collect_feeds(fetch=fetch, log=log) + collect_naver(fetch=fetch, log=log)
        n_ever = DAILY_MIX["evergreen"] + 2 if evergreen is None else evergreen
        items += collect_evergreen(n_ever, used_subjects(conn), asker=asker, fetch=fetch, log=log)
        log("2) 중복 제거")
        fresh = dedupe(items, conn)
        log(f"  {len(items)}개 → 새 후보 {len(fresh)}개")
        log("3) AI 점수")
        scored = score_items(fresh, asker=asker, log=log)
        log("4) 사진 사전 확인")
        check_photos(scored, finder=finder, pause=pause, log=log)
        save(conn, scored)
        ok = [s for s in scored if s["status"] == "new"]
        log(f"완료: 후보 {len(ok)}개, 제외 {len(scored) - len(ok)}개")
        return {"collected": len(items), "new": len(fresh), "candidates": len(ok), "excluded": len(scored) - len(ok)}
    finally:
        if own:
            conn.close()


def make_drafts(cands: list[dict], *, conn=None, create=None, log=print) -> list[str]:
    """선택한 후보로 원고 생성. 성공하면 candidates.status='drafted'."""
    from . import drafts as _d
    create = create or _d.create
    own = conn is None
    conn = conn or db.connect(config.DB_PATH)
    made = []
    try:
        for c in cands:
            topic = c.get("ko_title") or c["title"]
            if c.get("subject"):
                topic = f"{topic} (주인공: {c['subject']} / 원제: {c['title']})"
            try:
                d = create(topic, c["link"], "")
            except Exception as exc:
                log(f"  [원고 실패] {c.get('ko_title') or c['title']}: {exc}")
                continue
            set_status(conn, c["id"], "drafted", d["id"])
            made.append(d["id"])
            log(f"  원고 생성: {c.get('ko_title') or c['title']}")
    finally:
        if own:
            conn.close()
    return made
