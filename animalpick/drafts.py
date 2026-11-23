"""원고(draft) 저장·제작·검수·게시 파이프라인.

data/drafts/<id>/draft.json + slide_XX.jpg + img_XX.jpg(원본 사진)
상태: draft(검수 대기) → approved(승인) → published / rejected / failed
실제 게시는 approved 상태이고 체크리스트를 모두 통과한 원고만 가능하다.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import threading
import time

from . import config, hosting, images, publisher, render, tunnel, writer

CHECKLIST = {
    "facts": "사실·숫자를 출처와 대조해 확인했다",
    "images": "사진 사용 권리·저작자 표기를 확인했고, 사진이 실제 소재와 맞으며 사람이 나오면 부정적으로 쓰지 않았다",
    "original": "다른 계정 게시물의 문장·디자인을 베끼지 않았다",
    "no_mislead": "제목이 과장·허위 없이 내용과 맞다",
}
_publish_lock = threading.Lock()
GALLERY_MAX = 9  # 사진 모음은 최대 3장(장당 3장씩) — 표지 포함 캐러셀 최대 4장


def draft_dir(draft_id: str) -> Path:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", draft_id):
        raise ValueError("잘못된 원고 ID")
    return config.DRAFTS / draft_id


def load(draft_id: str) -> dict:
    path = draft_dir(draft_id) / "draft.json"
    if not path.exists():
        raise FileNotFoundError("원고를 찾을 수 없습니다")
    return json.loads(path.read_text(encoding="utf-8"))


def save(draft: dict) -> dict:
    draft["updated_at"] = now()
    d = draft_dir(draft["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "draft.json.tmp"
    tmp.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "draft.json")
    return draft


def list_all() -> list[dict]:
    if not config.DRAFTS.exists():
        return []
    items = []
    for p in config.DRAFTS.glob("*/draft.json"):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)


def now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def normalize_topic(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower())


def find_duplicate(topic: str, source_url: str) -> str | None:
    key = normalize_topic(topic)
    for d in list_all():
        if d.get("status") == "rejected":
            continue
        if (source_url and source_url in [s.get("url") for s in d.get("sources", [])]) or \
                (key and normalize_topic(d.get("input", {}).get("topic", "")) == key):
            return d["id"]
    return None


def attach_images(draft: dict, out_dir: Path, *, finder=images.search, downloader=images.download,
                  pause=time.sleep) -> None:
    """각 장의 image_query 로 권리 확인된 사진을 붙인다. 같은 사진은 한 번만 쓴다. 실패해도 계속."""
    used: set[str] = set()   # 이미 쓴 사진 + 받기 실패한 사진
    calls = 0
    cache: dict[str, list] = {}
    warn = draft.setdefault("warnings", []).append

    subject = draft.get("image_subject", "")
    dropped = 0

    def candidates(query: str) -> list:
        nonlocal dropped
        if query not in cache:
            try:
                found = finder(query) if query else []
            except Exception as exc:
                warn(f"이미지 검색 실패 ({query}): {exc}")
                found = []
            kept = [c for c in found if images.matches_subject(c, subject)]
            dropped += len(found) - len(kept)
            cache[query] = kept
        return cache[query]

    cover_q = draft["slides"][0].get("image_query", "") if draft["slides"] else ""
    for i, slide in enumerate(draft["slides"], 1):
        q = slide.get("image_query", "")
        if not q or slide["kind"] == "end":
            continue
        if slide["kind"] == "key":
            # 핵심 장면 사진: 그 검색어 결과 중 '핵심 단어 + 동물 이름'이 제목·태그에 모두 있는 것만. 대체 검색 없음.
            # 검색어 하나로 안 나오면 '동물 이름 + 핵심 단어' 로 몇 번 더 (예: wombat faeces)
            last = subject.split()[-1] if subject.split() else ""
            queries = [q] + ([f"{last} {t}" for t in slide.get("must_match", [])[:3]] if last else [])
            pool, seen_urls = [], set()
            for qq in dict.fromkeys(queries):
                for c in candidates(qq):
                    if (c["page_url"] not in used and c["page_url"] not in seen_urls
                            and images.matches_key(c, slide.get("must_match", []), subject)):
                        pool.append(c)
                        seen_urls.add(c["page_url"])
                if pool:
                    break
        else:
            # 표지: 그 장의 검색어 → 주인공 동물 이름만으로 넓게. 아직 안 쓴 사진만.
            # 위키미디어(출처·촬영 정보가 분명한 실제 사진)를 먼저, 사진 사이트(AI 이미지가 섞여 있음)는 나중에.
            allc = [c for c in candidates(q) + candidates(cover_q) + (candidates(subject) if subject else [])
                    if c["page_url"] not in used]
            pool = ([c for c in allc if c.get("source") not in images.STOCK_SOURCES] +
                    [c for c in allc if c.get("source") in images.STOCK_SOURCES])
        attached, last_err = False, None
        for pick in pool[:2]:  # 받기 실패하면 다음 후보 하나 더
            if pick.get("original_bytes") and pick["original_bytes"] > images.MAX_BYTES:
                pick = {**pick, "original_url": None}
            fname = f"img_{i:02d}.jpg"
            try:
                if calls:
                    pause(1.5)  # 위키미디어 요청 간격 (429 예방)
                calls += 1
                downloader(pick["download_url"], out_dir / fname, fallback_url=pick.get("original_url"))
            except Exception as exc:
                used.add(pick["page_url"])
                last_err = exc
                continue
            used.add(pick["page_url"])
            slide["image"] = {**pick, "file": fname, "credit": images.credit_line(pick)}
            attached = True
            break
        if not attached:
            reason = f"사진 받기 실패 ({last_err})" if last_err else "쓸 수 있는 사진 없음"
            if slide["kind"] == "key":
                warn(f"핵심 사진 '{slide.get('headline', '')}': {reason} → 이 사진 없이 만듦")
            else:
                warn(f"표지: {reason} → 2장째는 글자 장으로 만듦 (나중에 직접 사진 교체 가능)")
    # 사진 모음 (2장부터 한 장에 3장씩): 핵심 사진 다음을 '같은 주인공의 다른 사진' 으로 채운다
    have = sum(1 for s in draft["slides"][1:] if s.get("image"))
    need = GALLERY_MAX - have
    gallery: list[dict] = []
    dup_photos = 0
    if need > 0 and subject:
        queries = list(draft.get("gallery_queries") or []) + [subject, cover_q]
        pool, seen_urls = [], set()
        for qq in dict.fromkeys(x for x in queries if x):
            for c in candidates(qq):
                if c["page_url"] in used or c["page_url"] in seen_urls or not images.matches_photo(c, subject):
                    continue
                pool.append(c)
                seen_urls.add(c["page_url"])
        pool = ([c for c in pool if c.get("source") not in images.STOCK_SOURCES] +
                [c for c in pool if c.get("source") in images.STOCK_SOURCES])
        fails = 0
        hashes = []  # 이미 쓴 사진의 그림 지문 (같은 사진이 다른 주소로 올라온 경우를 거른다)
        for s in draft["slides"]:
            f = (s.get("image") or {}).get("file")
            if f and (out_dir / f).exists():
                try:
                    hashes.append(images.dhash(out_dir / f))
                except Exception:
                    pass
        per_author: dict[str, int] = {}
        for s in draft["slides"]:
            a = ((s.get("image") or {}).get("author") or "").lower()
            if a:
                per_author[a] = per_author.get(a, 0) + 1
        for pick in pool:
            if len(gallery) >= need or fails >= 4:
                break
            # 같은 사진가의 연속 촬영(거의 같은 사진)이 몰리지 않게: 사진 사이트 1장, 위키미디어·NASA 2장까지
            author = (pick.get("author") or "").lower()
            cap = 1 if pick.get("source") in images.STOCK_SOURCES else 2
            if author and author not in ("작자 미상", "nasa") and per_author.get(author, 0) >= cap:
                continue
            fname = f"img_g{len(gallery) + 1:02d}.jpg"
            try:
                if calls:
                    pause(1.5)
                calls += 1
                downloader(pick["download_url"], out_dir / fname, fallback_url=pick.get("original_url"))
            except Exception:
                used.add(pick["page_url"])
                fails += 1
                continue
            used.add(pick["page_url"])
            try:
                h = images.dhash(out_dir / fname)
            except Exception:
                h = None
            if h is not None and images.near_duplicate(h, hashes):
                dup_photos += 1
                continue  # 거의 같은 사진 → 버리고 같은 파일 이름으로 다음 후보를 받는다
            if h is not None:
                hashes.append(h)
            if author:
                per_author[author] = per_author.get(author, 0) + 1
            gallery.append({**pick, "file": fname, "credit": images.credit_line(pick), "hidden": False})
        if have + len(gallery) < 3:
            warn("관련 사진이 3장이 안 돼 사진 모음 없이 2장(표지 + 같은 사진)으로 만듦")
        if dup_photos:
            warn(f"같은 사진이 다른 주소로 올라온 {dup_photos}장을 빼고 다른 사진으로 채웠습니다")
    draft["gallery"] = gallery
    if dropped:
        warn(f"'{subject}' 와 태그가 맞지 않는 사진 사이트 사진 {dropped}장을 걸러냈습니다 (다른 대상일 수 있음)")
    stock = [i for i in render.used_images(draft) if i.get("source") in images.STOCK_SOURCES]
    if stock:
        warn(f"사진 사이트 사진 {len(stock)}장 사용: AI 로 만든 이미지가 섞여 있을 수 있습니다. "
             "'원본 보기'로 AI 생성 표시가 없는지, 실제 그 동물·장소가 맞는지 꼭 확인하세요")


def create(topic: str, source_url: str = "", source_text: str = "", *, write=writer.write_draft,
           finder=images.search, downloader=images.download, pause=time.sleep) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    draft_id = f"{stamp}-{hashlib.sha1((topic + source_url + stamp).encode()).hexdigest()[:6]}"
    dup = find_duplicate(topic, source_url)
    content = write(topic, source_url, source_text)
    draft = {
        "id": draft_id, "status": "draft", "created_at": now(),
        "input": {"topic": topic, "source_url": source_url, "has_source_text": bool(source_text.strip())},
        **content,
        "checklist": {k: False for k in CHECKLIST},
        "duplicate_of": dup, "warnings": list(content.get("warnings", [])),
        "history": [{"at": now(), "event": "created"}],
        "publish": None, "scheduled_at": None,
    }
    if dup:
        draft["warnings"].append(f"비슷한 원고가 이미 있습니다: {dup}")
    out = draft_dir(draft_id)
    out.mkdir(parents=True, exist_ok=True)
    attach_images(draft, out, finder=finder, downloader=downloader, pause=pause)
    draft["slide_files"] = [p.name for p in render.render_draft(draft, out)]
    return save(draft)


def update_content(draft_id: str, patch: dict) -> dict:
    draft = load(draft_id)
    if draft["status"] == "published":
        raise ValueError("이미 게시된 원고는 수정할 수 없습니다")
    for i, s in enumerate(patch.get("slides", [])):
        if i < len(draft["slides"]) and isinstance(s, dict):
            for key in ("headline", "body"):
                if key in s:
                    draft["slides"][i][key] = str(s[key])
            slide = draft["slides"][i]
            if s.get("remove_image") and slide.get("image"):
                # 뺀 사진 정보는 버리지 않고 보관 → '사진 되돌리기'로 복구 (파일도 지우지 않는다)
                slide["removed_image"] = slide["image"]
                slide["image"] = None
            elif s.get("restore_image") and slide.get("removed_image") and not slide.get("image"):
                if (draft_dir(draft_id) / slide["removed_image"]["file"]).exists():
                    slide["image"] = slide.pop("removed_image")
    for i, g in enumerate(patch.get("gallery", [])):
        if i < len(draft.get("gallery", [])) and isinstance(g, dict) and "hidden" in g:
            draft["gallery"][i]["hidden"] = bool(g["hidden"])
    for key in ("caption",):
        if key in patch:
            draft[key] = str(patch[key])
    if "hashtags" in patch:
        draft["hashtags"] = [re.sub(r"[#\s]", "", t) for t in patch["hashtags"] if str(t).strip()][:10]
    draft["status"] = "draft"  # 수정하면 다시 검수
    draft["history"].append({"at": now(), "event": "edited"})
    draft["slide_files"] = [p.name for p in render.render_draft(draft, draft_dir(draft_id))]
    return save(draft)


def set_status(draft_id: str, status: str, checklist: dict | None = None) -> dict:
    if status not in ("draft", "approved", "rejected"):
        raise ValueError("허용되지 않는 상태")
    draft = load(draft_id)
    if draft["status"] == "published":
        raise ValueError("이미 게시된 원고입니다")
    if checklist:
        draft["checklist"] = {k: bool(checklist.get(k)) for k in CHECKLIST}
    if status == "approved" and not all(draft["checklist"].values()):
        raise ValueError("검수 체크리스트를 모두 확인해야 승인할 수 있습니다")
    draft["status"] = status
    draft["history"].append({"at": now(), "event": status})
    return save(draft)


def set_schedule(draft_id: str, when: str | None) -> dict:
    draft = load(draft_id)
    if when:
        datetime.fromisoformat(when)  # 형식 검사
    draft["scheduled_at"] = when or None
    draft["history"].append({"at": now(), "event": f"schedule {when or '취소'}"})
    return save(draft)


def full_caption(draft: dict) -> str:
    parts = [draft.get("caption", "").strip()]
    photo_credit = images.credits_by_source([{"image": i} for i in render.used_images(draft)])
    if photo_credit:
        parts.append(photo_credit)
    if draft.get("hashtags"):
        parts.append(" ".join(f"#{t}" for t in draft["hashtags"]))
    return "\n\n".join(p for p in parts if p)


@contextmanager
def _github_hosting(files: list[Path], draft_id: str):
    """보조 방식: MEDIA_HOSTING=github 일 때만. GitHub 공개 저장소에 영구 업로드."""
    urls = hosting.upload(files, f"{draft_id}/{datetime.now().strftime('%H%M%S')}")
    hosting.wait_public(urls)
    yield urls


def default_hoster(files: list[Path], draft_id: str):
    """기본: 이미지는 이 컴퓨터에만 두고 게시하는 동안만 Cloudflare 임시 주소로 공개."""
    if config.env("MEDIA_HOSTING") == "github":
        return _github_hosting(files, draft_id)
    return tunnel.public_urls(files)


def publish(draft_id: str, *, dry_run: bool = True, hoster=default_hoster,
            publish_fn=publisher.publish_carousel) -> dict:
    """approved 원고를 게시. 중복 게시 방지: published 이거나 media_id 가 있으면 거부."""
    with _publish_lock:
        draft = load(draft_id)
        if draft["status"] == "published" or (draft.get("publish") or {}).get("media_id"):
            raise ValueError("이미 게시된 원고입니다 (중복 게시 방지)")
        if draft["status"] != "approved":
            raise ValueError("승인된 원고만 게시할 수 있습니다")
        log: list[str] = []
        files = [draft_dir(draft_id) / f for f in draft["slide_files"]]
        caption = full_caption(draft)
        try:
            if dry_run:
                result = publish_fn([f"(테스트) {f.name}" for f in files], caption, dry_run=True, log=log.append)
            else:
                with hoster(files, draft_id) as urls:  # 이 블록 안에서만 이미지가 공개된다
                    log.append(f"이미지 {len(urls)}장 임시 공개 주소 준비")
                    result = publish_fn(urls, caption, dry_run=False, log=log.append)
                log.append("임시 공개 주소 닫음")
        except Exception as exc:
            if not dry_run:
                draft["status"] = "failed"
                draft["publish"] = {"error": str(exc), "log": log, "at": now()}
                draft["history"].append({"at": now(), "event": "publish_failed"})
                save(draft)
            raise
        if dry_run:
            draft["history"].append({"at": now(), "event": "dry_run_ok"})
            draft["last_dry_run"] = {"at": now(), "log": log, "caption": caption}
        else:
            draft["status"] = "published"
            draft["publish"] = {**result, "log": log, "at": now()}
            draft["history"].append({"at": now(), "event": "published"})
        return save(draft)


def run_due(*, dry_run: bool = False, now_fn=datetime.now, **kw) -> list[str]:
    """예약 시간이 지난 approved 원고를 게시. 작업 스케줄러에서 주기적으로 호출."""
    done = []
    current = now_fn().astimezone()
    for d in list_all():
        when = d.get("scheduled_at")
        if d.get("status") != "approved" or not when:
            continue
        if datetime.fromisoformat(when).astimezone() <= current:
            try:
                publish(d["id"], dry_run=dry_run, **kw)
                done.append(f"{d['id']} 게시")
            except Exception as exc:
                done.append(f"{d['id']} 실패: {exc}")
    return done
