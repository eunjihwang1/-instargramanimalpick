"""1회성 토큰 설정: 짧은 사용자 토큰 → 60일 사용자 토큰 → 만료 없는 페이지 토큰.

.env 에 META_ACCESS_TOKEN(짧은 토큰), META_APP_ID, META_APP_SECRET 만 넣어 두면
이 모듈이 META_PAGE_TOKEN, META_IG_USER_ID, META_IG_USERNAME, META_PAGE_ID 를 채운다.
토큰 값은 화면에 출력하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from urllib.request import urlopen

from . import config, graph


def exchange_long_lived(short_token: str, app_id: str, app_secret: str, opener: Callable = urlopen) -> str:
    result = graph.get(
        "oauth/access_token",
        params={"grant_type": "fb_exchange_token", "client_id": app_id,
                "client_secret": app_secret, "fb_exchange_token": short_token},
        auth=False, opener=opener,
    )
    token = result.get("access_token")
    if not token:
        raise RuntimeError("장기 토큰 교환 응답에 access_token 이 없습니다")
    return token


def find_linked_page(user_token: str, opener: Callable = urlopen) -> dict:
    result = graph.get("me/accounts", "id,name,access_token,instagram_business_account{id,username}",
                       token=user_token, opener=opener)
    pages = result.get("data", [])
    linked = [p for p in pages if p.get("instagram_business_account")]
    if not pages:
        raise RuntimeError("Facebook 페이지가 0개로 나옵니다. 토큰 발급 때 '페이지 선택'에서 애니멀픽 페이지를 체크하세요.")
    if not linked:
        names = ", ".join(p.get("name", "?") for p in pages)
        raise RuntimeError(f"페이지({names})에 연결된 Instagram 프로 계정이 없습니다. 페이지-Instagram 연결을 확인하세요.")
    return linked[0]


def token_expiry(token: str, app_id: str, app_secret: str, opener: Callable = urlopen) -> str:
    result = graph.get("debug_token", params={"input_token": token},
                       token=f"{app_id}|{app_secret}", opener=opener)
    data = result.get("data", {})
    exp = data.get("expires_at")
    if exp == 0:
        return "만료 없음"
    if isinstance(exp, int):
        return datetime.fromtimestamp(exp, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M 만료")
    return "확인 불가"


def run(opener: Callable = urlopen) -> list[str]:
    config.load_env(override=True)  # 대시보드가 켜진 뒤 .env 를 고쳐도 최신 값 사용
    short = config.env("META_ACCESS_TOKEN")
    app_id = config.env("META_APP_ID")
    secret = config.env("META_APP_SECRET")
    missing = [k for k, v in (("META_ACCESS_TOKEN", short), ("META_APP_ID", app_id),
                              ("META_APP_SECRET", secret)) if not v]
    if missing:
        raise RuntimeError(".env 에 아직 비어 있는 값: " + ", ".join(missing))
    long_user = exchange_long_lived(short, app_id, secret, opener)
    page = find_linked_page(long_user, opener)
    page_token = page.get("access_token")
    if not page_token:
        raise RuntimeError("페이지 토큰을 받지 못했습니다. 이 Facebook 계정이 페이지 관리자인지 확인하세요.")
    iga = page["instagram_business_account"]
    config.update_env({
        "META_ACCESS_TOKEN": long_user,
        "META_PAGE_TOKEN": page_token,
        "META_PAGE_ID": page["id"],
        "META_IG_USER_ID": iga["id"],
        "META_IG_USERNAME": iga.get("username", ""),
    })
    expiry = token_expiry(page_token, app_id, secret, opener)
    return [
        f"[완료] 페이지 '{page.get('name')}' ↔ Instagram @{iga.get('username')} (IG ID {iga['id']})",
        f"게시용 페이지 토큰 저장 ({expiry}). 사용자 토큰은 60일짜리로 교체 저장.",
        ".env 에 저장했습니다. 토큰 값은 화면에 표시하지 않습니다.",
    ]
