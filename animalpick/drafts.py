"""원고(draft) 저장·제작·검수·게시 파이프라인.

data/drafts/<id>/draft.json + slide_XX.jpg + img_XX.jpg(원본 사진)
상태: draft(검수 대기) → approved(승인) → published / rejected / failed
실제 게시는 approved 상태이고 체크리스트를 모두 통과한 원고만 가능하다.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import threading
import time

from . import config, hosting, images, publisher, render, writer

CHECKLIST = {
    "facts": "사실·숫자를 출처와 대조해 확인했다",
    "images": "사진 사용 권리·저작자 표기를 확인했고, 사진이 실제 소재와 맞으며 사람이 나오면 부정적으로 쓰지 않았다",
    "original": "다른 계정 게시물의 문장·디자인을 베끼지 않았다",
    "no_mislead": "제목이 과장·허위 없이 내용과 맞다",
}
_publish_lock = threading.Lock()


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
        # 그 장의 검색어 → 표지 검색어 → 주인공 동물 이름만으로 넓게. 아직 안 쓴 사진만.
        pool = [c for c in candidates(q) + candidates(cover_q) + (candidates(subject) if subject else [])
                if c["page_url"] not in used]
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
            warn(f"{i}장: {reason} → 글자 중심으로 만듦 (나중에 직접 사진 교체 가능)")
    if dropped:
        warn(f"'{subject}' 와 태그가 맞지 않는 사진 사이트 사진 {dropped}장을 걸러냈습니다 (다른 동물일 수 있음)")


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
        "duplicate_of": dup, "warnings": [], "history": [{"at": now(), "event": "created"}],
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
            if s.get("remove_image"):
                draft["slides"][i]["image"] = None
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
    photo_credit = images.credits_by_source(draft["slides"])
    if photo_credit:
        parts.append(photo_credit)
    if draft.get("hashtags"):
        parts.append(" ".join(f"#{t}" for t in draft["hashtags"]))
    return "\n\n".join(p for p in parts if p)


def publish(draft_id: str, *, dry_run: bool = True, uploader=hosting.upload, waiter=hosting.wait_public,
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
                urls = [f"(테스트) {f.name}" for f in files]
            else:
                prefix = f"{draft_id}/{datetime.now().strftime('%H%M%S')}"
                urls = uploader(files, prefix)
                log.append(f"이미지 {len(urls)}장 공개 호스팅 업로드")
                waiter(urls)
            result = publish_fn(urls, caption, dry_run=dry_run, log=log.append)
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
            draft["publish"] = {**result, "log": log, "at": now(), "image_urls": urls}
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
