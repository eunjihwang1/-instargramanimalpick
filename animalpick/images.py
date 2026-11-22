"""사용 권리가 확인되는 이미지만 가져온다.

1순위 위키미디어 공용(Wikimedia Commons) — 동물 종 정확도가 높다.
  허용: 퍼블릭 도메인, CC0, CC BY (변경·상업 이용 가능, 저작자 표시 필요).
  제외: CC BY-SA(2차 저작물에 같은 라이선스 강제), NC(비영리), ND(변경 금지), 그 외 불명확한 것.
2순위 Pexels — 사진 품질이 좋고 요청 제한이 덜하다. .env 에 PEXELS_API_KEY 가 있을 때만.
  Pexels 라이선스(2026-11-22 확인): 무료, 수정·상업 이용 가능, 인스타그램 게시 명시적으로 허용.
  금지: 식별 가능한 인물을 부정적으로 묘사, 보증 암시, 무수정 판매, 상표 사용.
  API 가이드라인에 따라 사진가와 Pexels 를 항상 표기한다. (2026-11-22 현재 신규 키 발급 중단)
3순위 Pixabay — .env 에 PIXABAY_API_KEY 가 있을 때만.
  라이선스 요약(2026-11-22 확인): 무료, 저작자 표기 불필요, 수정·2차 창작 허용.
  금지: 무수정 단독 판매, 상표가 보이는 사진의 상품 관련 상업 이용, 인물 부정·오해 유발 사용, 상표 사용.
  API 규칙: 검색 결과 24시간 캐시, 대량 자동 요청 금지, 이미지는 내려받아 써야 함(핫링크 금지).
  기본 키는 최대 1280px 이미지만 제공 → 세로·큰 사진 우선.
저작자·라이선스·원본 주소를 함께 기록해 캡션/슬라이드에 표기한다.
"""
from __future__ import annotations

import html
import io
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from . import config

API = "https://commons.wikimedia.org/w/api.php"
PEXELS_API = "https://api.pexels.com/v1/search"
PIXABAY_API = "https://pixabay.com/api/"
PIXABAY_CACHE = config.DATA / "cache" / "pixabay"
SOURCE_LABEL = {"wikimedia_commons": "Wikimedia Commons", "pexels": "Pexels", "pixabay": "Pixabay"}
UA = "AnimalPick/0.1 (local editorial tool; contact via project owner)"
MIN_WIDTH = 900
MAX_BYTES = 30_000_000


def license_ok(short_name: str) -> bool:
    name = (short_name or "").strip().lower()
    if not name:
        return False
    if any(bad in name for bad in ("-sa", " sa", "nc", "nd", "fair use", "non-free")):
        return False
    if name in ("public domain", "pd", "cc0", "cc0 1.0", "cc-zero") or name.startswith(("pd-", "public domain")):
        return True
    return bool(re.fullmatch(r"cc[ -]by[ -]\d(\.\d)?", name)) or name in ("cc by", "cc-by")


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def search_commons(query: str, *, limit: int = 15, opener=urlopen) -> list[dict]:
    """위키미디어 공용: 조건(라이선스·크기·형식)에 맞는 후보를 품질 순으로 반환."""
    if not query.strip():
        return []
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920,  # 위키미디어 표준 썸네일 폭 (비표준 폭은 429 차단)
    }
    req = Request(f"{API}?{urlencode(params)}", headers={"User-Agent": UA})
    with opener(req, timeout=20) as resp:
        data = json.load(resp)
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    results = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if info.get("mime") not in ("image/jpeg", "image/png") or info.get("width", 0) < MIN_WIDTH:
            continue
        if not license_ok(lic):
            continue
        results.append({
            "title": page.get("title", ""),
            "download_url": info.get("thumburl") or info.get("url"),
            "original_url": info.get("url"),
            "original_bytes": info.get("size"),
            "page_url": info.get("descriptionurl", ""),
            "author": strip_html((meta.get("Artist") or {}).get("value", ""))[:80] or "작자 미상",
            "license": lic,
            "license_url": (meta.get("LicenseUrl") or {}).get("value", ""),
            "width": info.get("width"), "height": info.get("height"),
            "source": "wikimedia_commons", "ai_generated": False,
        })
    return results


def pexels_sized(original: str, width: int, height: int) -> str:
    """원본 주소에 크기 파라미터를 붙여 긴 변 2000px 로 받는다 (Pexels CDN 기본 기능)."""
    side = "w" if width >= height else "h"
    return f"{original.split('?')[0]}?auto=compress&cs=tinysrgb&{side}=2000"


def search_pexels(query: str, *, limit: int = 15, opener=urlopen) -> list[dict]:
    key = config.env("PEXELS_API_KEY")
    if not key or not query.strip():
        return []
    url = f"{PEXELS_API}?{urlencode({'query': query, 'per_page': limit, 'size': 'medium'})}"
    req = Request(url, headers={"Authorization": key, "User-Agent": "AnimalPick"})  # Pexels 는 Bearer 없이 키만
    try:
        with opener(req, timeout=20) as resp:
            data = json.load(resp)
    except HTTPError as exc:
        msg = {401: "Pexels 키가 잘못됐습니다 (.env 의 PEXELS_API_KEY 확인)",
               429: "Pexels 요청 한도(시간당 200회)에 걸렸습니다"}.get(exc.code, f"Pexels HTTP {exc.code}")
        raise RuntimeError(msg) from None
    results = []
    for ph in data.get("photos", []):
        src = ph.get("src") or {}
        if not src.get("original") or (ph.get("width") or 0) < MIN_WIDTH:
            continue
        results.append({
            "title": ph.get("alt") or f"Pexels {ph.get('id')}",
            "download_url": pexels_sized(src["original"], ph.get("width", 1), ph.get("height", 1)),
            "original_url": src.get("large2x"),  # 실패 시 대체
            "original_bytes": None,
            "page_url": ph.get("url", ""),
            "author": (ph.get("photographer") or "Pexels")[:80],
            "author_url": ph.get("photographer_url", ""),
            "license": "Pexels License",
            "license_url": "https://www.pexels.com/license/",
            "width": ph.get("width"), "height": ph.get("height"),
            "source": "pexels", "ai_generated": False,
        })
    return results


def search_pixabay(query: str, *, limit: int = 20, opener=urlopen, cache_dir: Path | None = None,
                   now=time.time) -> list[dict]:
    key = config.env("PIXABAY_API_KEY")
    if not key or not query.strip():
        return []
    cache_dir = cache_dir or PIXABAY_CACHE
    cache_file = cache_dir / (re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")[:80] + ".json")
    data = None
    if cache_file.exists() and now() - cache_file.stat().st_mtime < 24 * 3600:  # 규칙: 24시간 캐시
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except ValueError:
            data = None
    if data is None:
        params = {"key": key, "q": query[:100], "image_type": "photo", "per_page": max(3, min(limit, 200)),
                  "min_width": 1000, "safesearch": "true"}
        req = Request(f"{PIXABAY_API}?{urlencode(params)}", headers={"User-Agent": "AnimalPick"})
        try:
            with opener(req, timeout=20) as resp:
                data = json.load(resp)
        except HTTPError as exc:
            msg = {400: "Pixabay 키가 잘못됐습니다 (.env 의 PIXABAY_API_KEY 확인)",
                   429: "Pixabay 요청 한도(1분 100회)에 걸렸습니다"}.get(exc.code, f"Pixabay HTTP {exc.code}")
            raise RuntimeError(msg) from None  # 오류 메시지에 키가 든 URL 을 남기지 않는다
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    results = []
    for hit in data.get("hits", []):
        if not hit.get("largeImageURL"):
            continue
        results.append({
            "title": hit.get("tags", "") or f"Pixabay {hit.get('id')}",
            "download_url": hit["largeImageURL"],
            "original_url": None,
            "original_bytes": None,
            "page_url": hit.get("pageURL", ""),
            "author": (hit.get("user") or "Pixabay")[:80],
            "license": "Pixabay Content License",
            "license_url": "https://pixabay.com/service/license-summary/",
            "width": hit.get("imageWidth"), "height": hit.get("imageHeight"),
            "source": "pixabay", "ai_generated": False,
        })
    # 기본 키는 1280px 까지라 세로 사진이 표지·본문에 덜 흐리다 → 세로 우선
    results.sort(key=lambda r: (r["height"] or 0) < (r["width"] or 0))
    return results


STOCK_SOURCES = ("pexels", "pixabay")


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)  # penguins → penguin
    return out


SENSITIVE_WORDS = {"corpse", "dead", "carcass", "carcasse", "blood", "bloody", "skull", "skeleton", "killed",
                   "roadkill", "slaughter", "taxidermy", "stuffed", "necropsy", "autopsy", "injured", "wound"}


def is_sensitive(item: dict) -> bool:
    """사체·피·박제 등 매거진 표지·본문에 자동으로 쓰기엔 불편한 사진 (제목·태그 기준)."""
    return bool(_words(item.get("title", "")) & SENSITIVE_WORDS)


def matches_subject(item: dict, subject: str) -> bool:
    """일반 사진 사이트(Pexels/Pixabay) 사진은 태그·설명에 주인공 동물 이름이 모두 있어야 통과.
    위키미디어는 검색 자체가 파일 제목 기준이라 그대로 둔다. 불편한 사진은 출처와 관계없이 제외."""
    if is_sensitive(item):
        return False
    if item.get("source") not in STOCK_SOURCES or not subject.strip():
        return True
    need = _words(subject)
    return bool(need) and need <= _words(item.get("title", ""))


def search(query: str, *, commons=None, pexels=None, pixabay=None) -> list[dict]:
    """위키미디어 → Pexels → Pixabay 순. 한쪽이 실패해도 다른 쪽 결과는 쓴다.
    모두 실패하면 마지막 오류를 올려 경고에 남긴다."""
    results, errors = [], []
    for fn in (commons or search_commons, pexels or search_pexels, pixabay or search_pixabay):
        try:
            results.extend(fn(query))
        except Exception as exc:
            errors.append(exc)
    if not results and errors:
        raise errors[-1]
    return results


def _fetch(url: str, opener) -> bytes:
    with opener(Request(url, headers={"User-Agent": UA}), timeout=40) as resp:
        return resp.read(MAX_BYTES)


def download(url: str, dest: Path, opener=urlopen, *, fallback_url: str | None = None, sleep=time.sleep) -> Path:
    """썸네일을 받되, 429(요청 과다)면 잠깐 쉬고 한 번 더, 그래도 안 되면 원본 파일로 대체.
    받은 사진은 긴 변 2000px 이하 JPEG 로 줄여 저장한다."""
    data = None
    # 썸네일 → (잠깐 쉬고) 썸네일 → 원본 → (더 쉬고) 원본. 429 가 아닌 오류는 바로 실패.
    plan = [(url, 0), (url, 3)] + ([(fallback_url, 0), (fallback_url, 8)] if fallback_url else [])
    for target, wait in plan:
        if wait:
            sleep(wait)
        try:
            data = _fetch(target, opener)
            break
        except HTTPError as exc:
            if exc.code != 429:
                raise
    if data is None:
        raise RuntimeError("사진 서버가 잠시 요청을 제한했습니다 (429)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((2000, 2000))
        img.save(dest, "JPEG", quality=92)
    return dest


def credit_line(img: dict) -> str:
    if img.get("source") in ("pexels", "pixabay"):
        return f"{img.get('author', '')} / {SOURCE_LABEL[img['source']]}"
    return f"{img.get('author', '작자 미상')} / {img.get('license', '')}"


def credits_by_source(slides: list[dict]) -> str:
    """'사진: A / CC0, B / CC BY 2.0 (Wikimedia Commons) · C / Pexels (Pexels)' 형태."""
    groups: dict[str, list[str]] = {}
    for s in slides:
        img = s.get("image")
        if img and img.get("credit"):
            label = SOURCE_LABEL.get(img.get("source", "wikimedia_commons"), img.get("source", ""))
            name = img.get("author", "") if img.get("source") in ("pexels", "pixabay") else img["credit"]
            if name not in groups.setdefault(label, []):
                groups[label].append(name)
    if not groups:
        return ""
    return "사진: " + " · ".join(f"{', '.join(v)} ({k})" for k, v in groups.items())
