"""Instagram 캐러셀 게시 (공식 Content Publishing API).

흐름: 한도 확인 → 이미지마다 캐러셀 항목 컨테이너 → 캐러셀 컨테이너 → 처리 완료 대기 → 게시 → permalink 조회.
dry_run=True 면 Meta 에 아무것도 보내지 않고 할 일만 기록한다.
"""
from __future__ import annotations

import time
from typing import Callable

from . import config, graph

# 문서상 24시간 100건 / 캐러셀 섹션 50건이 상충 → 보수적으로 50, 그리고 우리 운영 목표는 하루 10건
DAILY_SAFE_LIMIT = 50


class PublishError(RuntimeError):
    pass


def _retry(fn: Callable, *, attempts: int = 3, sleep=time.sleep, log=None):
    for i in range(attempts):
        try:
            return fn()
        except graph.GraphError as exc:
            if not exc.transient or i == attempts - 1:
                raise
            if log:
                log(f"일시 오류(code={exc.code}) → {5 * (i + 1)}초 뒤 재시도")
            sleep(5 * (i + 1))


def publish_carousel(image_urls: list[str], caption: str, *, dry_run: bool = True,
                     opener=None, sleep=time.sleep, log=print, poll_tries: int = 20) -> dict:
    if not 2 <= len(image_urls) <= 10:
        raise PublishError("캐러셀은 이미지 2~10장이어야 합니다")
    if len(caption) > 2200:
        raise PublishError("캡션은 2,200자 이하여야 합니다")
    ig = config.env("META_IG_USER_ID")
    if not ig.isdigit():
        raise PublishError("META_IG_USER_ID 가 없습니다. 토큰 설정(setup_token)을 먼저 하세요.")
    kw = {"token_env": "META_PAGE_TOKEN"}
    if opener:
        kw["opener"] = opener

    if dry_run:
        log(f"[테스트] {len(image_urls)}장 캐러셀, 캡션 {len(caption)}자 — Meta 에 보내지 않음")
        for u in image_urls:
            log(f"[테스트] 항목 컨테이너 예정: {u}")
        return {"dry_run": True, "items": len(image_urls)}

    limit = graph.get(f"{ig}/content_publishing_limit", "quota_usage,config", **kw)
    usage = ((limit.get("data") or [{}])[0]).get("quota_usage", 0) or 0
    log(f"최근 24시간 게시 사용량: {usage}")
    if usage >= DAILY_SAFE_LIMIT:
        raise PublishError(f"24시간 게시 한도({DAILY_SAFE_LIMIT})에 도달했습니다")

    children = []
    for i, url in enumerate(image_urls, 1):
        res = _retry(lambda: graph.post(f"{ig}/media", {"image_url": url, "is_carousel_item": "true"}, **kw),
                     sleep=sleep, log=log)
        children.append(res["id"])
        log(f"항목 {i}/{len(image_urls)} 컨테이너 생성")

    container = _retry(lambda: graph.post(f"{ig}/media", {
        "media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}, **kw),
        sleep=sleep, log=log)["id"]
    log("캐러셀 컨테이너 생성")

    for _ in range(poll_tries):
        status = graph.get(container, "status_code,status", **kw)
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise PublishError(f"Instagram 처리 실패: {status.get('status', code)}")
        sleep(3)
    else:
        raise PublishError("Instagram 처리 대기 시간 초과")

    media_id = _retry(lambda: graph.post(f"{ig}/media_publish", {"creation_id": container}, **kw),
                      sleep=sleep, log=log)["id"]
    try:  # 게시는 이미 끝났다. 여기서 실패해도 media_id 는 반드시 돌려줘 중복 게시를 막는다.
        info = graph.get(media_id, "permalink,timestamp", **kw)
    except Exception:
        info = {}
    log(f"게시 완료: {info.get('permalink')}")
    return {"dry_run": False, "media_id": media_id, "permalink": info.get("permalink"),
            "container_id": container, "children": children}
