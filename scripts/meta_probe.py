#!/usr/bin/env python3
"""Meta Graph API 연결 확인 CLI (읽기 전용).

- 게시·삭제 등 쓰기 요청은 하지 않는다. 비공식 스크래핑도 하지 않는다.
- 토큰은 프로젝트 루트의 .env (Git 제외)에서만 읽고, 화면·로그·파일 어디에도 출력하지 않는다.

사용 예:
    python scripts/meta_probe.py whoami
    python scripts/meta_probe.py pages
    python scripts/meta_probe.py discovery --handle heodukgoo --limit 10 --save
    python scripts/meta_probe.py discovery --handle heodukgoo --probe-fields
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Callable
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from animalpick import config, db, graph  # noqa: E402
from animalpick.graph import GraphError, api_version, redact  # noqa: E402,F401

HANDLE_RE = re.compile(r"^[A-Za-z0-9._]+$")
DB_PATH = config.DB_PATH
RAW_DIR = ROOT / "data" / "raw"

ACCOUNT_FIELDS = "username,name,followers_count,media_count"
# 2026-11 공식 문서 기준 business_discovery 에서 지원되는 게시물 필드
MEDIA_FIELDS = ["id", "caption", "media_type", "media_product_type", "timestamp",
                "permalink", "like_count", "comments_count", "view_count"]
# 문서상 없는 값이 올 수 있거나 제약이 있는 필드 → --probe-fields 로 하나씩 실측
OPTIONAL_MEDIA_FIELDS = ["media_url", "thumbnail_url", "children{media_type,media_url}"]
REQUIRED_PERMISSIONS = ["instagram_basic", "pages_show_list", "pages_read_engagement",
                        "instagram_content_publish"]


def load_local_env() -> None:
    config.load_env()


def graph_get(path: str, fields: str | None = None, *, opener: Callable = urlopen) -> dict:
    return graph.get(path, fields, opener=opener)


# ---------- 필드 조립 ----------

def page_fields() -> str:
    return "id,name,instagram_business_account{id,username}"


def discovery_fields(handle: str, limit: int, media_fields: list[str] | None = None) -> str:
    if not HANDLE_RE.fullmatch(handle):
        raise ValueError("Instagram 사용자 이름은 영문/숫자/점/밑줄만 허용합니다")
    if not (1 <= limit <= 50):
        raise ValueError("limit 은 1~50 사이로 지정하세요")
    media = ",".join(media_fields or MEDIA_FIELDS)
    return (
        f"business_discovery.username({handle})"
        f"{{{ACCOUNT_FIELDS},"
        f"media.limit({limit}){{{media}}}}}"
    )


# ---------- 결과 해석 ----------

def summarize_permissions(payload: dict) -> list[str]:
    granted = {p.get("permission") for p in payload.get("data", []) if p.get("status") == "granted"}
    lines = []
    for perm in REQUIRED_PERMISSIONS:
        lines.append(f"  {'[O]' if perm in granted else '[X]'} {perm}")
    missing = [p for p in REQUIRED_PERMISSIONS if p not in granted]
    if missing:
        lines.append("  → 빠진 권한이 있습니다. 토큰을 다시 발급할 때 위 [X] 권한을 체크하세요.")
    return lines


def summarize_pages(payload: dict) -> tuple[list[str], str | None]:
    """(안내 문구, 연결된 IG 숫자 ID 또는 None)."""
    pages = payload.get("data", [])
    if not pages:
        return ([
            "페이지가 0개로 나옵니다. 가능한 원인:",
            "  1) 토큰 발급 때 '페이지 선택' 화면에서 애니멀픽 페이지를 체크하지 않음",
            "  2) pages_show_list 권한 미승인",
            "  3) 이 Facebook 계정이 페이지 관리자가 아님",
        ], None)
    lines, ig_id = [], None
    for page in pages:
        iga = page.get("instagram_business_account")
        if iga:
            lines.append(f"[연결됨] 페이지 '{page.get('name')}' ↔ Instagram @{iga.get('username')} (IG ID {iga.get('id')})")
            ig_id = ig_id or iga.get("id")
        else:
            lines.append(f"[미연결] 페이지 '{page.get('name')}' 에 연결된 Instagram 프로 계정이 없습니다.")
    if ig_id:
        lines.append(f"→ .env 의 META_IG_USER_ID={ig_id} 로 설정하면 discovery 를 쓸 수 있습니다.")
    else:
        lines.append("→ Instagram 앱 > 설정 > 계정 센터 또는 Meta Business Suite 에서 페이지와 Instagram 을 연결하세요.")
    return lines, ig_id


def field_presence(payload: dict, field: str) -> int:
    """게시물 중 해당 필드(중괄호 앞 이름)가 실제로 들어 있는 개수."""
    name = field.split("{", 1)[0]
    media = ((payload.get("business_discovery") or {}).get("media") or {}).get("data") or []
    return sum(1 for m in media if m.get(name) is not None)


def probe_fields(handle: str, *, get=graph_get, ig_id: str) -> list[str]:
    """선택 필드를 하나씩 붙여 3개 게시물로 실측 → 지원 매트릭스 문자열 목록."""
    rows = []
    for field in ["(기본)"] + OPTIONAL_MEDIA_FIELDS:
        fields = MEDIA_FIELDS if field == "(기본)" else MEDIA_FIELDS + [field]
        try:
            payload = get(ig_id, discovery_fields(handle, 3, fields))
            n = len(((payload.get("business_discovery") or {}).get("media") or {}).get("data") or [])
            if field == "(기본)":
                detail = ", ".join(f"{f}={field_presence(payload, f)}/{n}" for f in MEDIA_FIELDS)
                rows.append(f"  {field:38s} OK  {detail}")
            else:
                rows.append(f"  {field:38s} OK  값 있음 {field_presence(payload, field)}/{n}")
        except GraphError as exc:
            rows.append(f"  {field:38s} 실패 code={exc.code}")
    return rows


def save_raw(handle: str, payload: dict) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RAW_DIR / f"{stamp}_{handle}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ---------- CLI ----------

def require_ig_id() -> str:
    ig_id = os.environ.get("META_IG_USER_ID", "").strip()
    if not ig_id.isdigit():
        raise ValueError("META_IG_USER_ID 에 'pages' 결과의 Instagram 숫자 ID 를 넣어 주세요")
    return ig_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Meta Graph API 연결 확인 (읽기 전용)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("whoami", help="토큰 주인과 승인된 권한 확인")
    sub.add_parser("pages", help="내 Facebook 페이지와 연결된 Instagram ID 확인")
    disc = sub.add_parser("discovery", help="다른 프로 계정의 공개 게시물 조회")
    disc.add_argument("--handle", required=True)
    disc.add_argument("--limit", type=int, default=10)
    disc.add_argument("--save", action="store_true", help="원본 JSON 과 SQLite DB 에 저장")
    disc.add_argument("--probe-fields", action="store_true", help="필드별 실제 지원 여부 매트릭스 출력")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔(cp949)에서 한글·기호 깨짐 방지
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    load_local_env()
    try:
        if args.command == "whoami":
            me = graph_get("me", "id,name")
            print(f"토큰 주인: {me.get('name')} (Facebook 사용자 ID {me.get('id')})")
            print("권한:")
            print("\n".join(summarize_permissions(graph_get("me/permissions"))))
        elif args.command == "pages":
            lines, _ = summarize_pages(graph_get("me/accounts", page_fields()))
            print("\n".join(lines))
        else:
            handle = args.handle.lstrip("@")
            ig_id = require_ig_id()
            if args.probe_fields:
                print(f"@{handle} 필드 지원 매트릭스 (게시물 3개 기준):")
                print("\n".join(probe_fields(handle, ig_id=ig_id)))
                return 0
            payload = graph_get(ig_id, discovery_fields(handle, args.limit))
            if args.save:
                raw = save_raw(handle, payload)
                conn = db.connect(DB_PATH)
                try:
                    result = db.save_discovery(conn, payload, api_version=api_version(),
                                               raw_path=str(raw.relative_to(ROOT)))
                finally:
                    conn.close()
                print(f"원본 저장: {raw.relative_to(ROOT)}")
                print(f"DB 저장: @{result['account']} 새 게시물 {result['posts_new']}개, "
                      f"갱신 {result['posts_updated']}개 → {DB_PATH.relative_to(ROOT)}")
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
