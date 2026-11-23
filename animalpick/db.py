"""SQLite 저장소 — 레퍼런스 계정/게시물.

원칙: API 가 돌려주지 않은 값은 NULL 로 둔다. 추정치를 채워 넣지 않는다.
게시물 지표는 수집할 때마다 snapshot 으로 따로 쌓아 시간에 따른 변화를 볼 수 있게 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id            TEXT PRIMARY KEY,          -- 링크 기반 해시
    kind          TEXT NOT NULL,             -- news_intl / news_kr / evergreen
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,             -- 원제
    title_key     TEXT NOT NULL,             -- 중복 판별용 정규화 제목
    link          TEXT NOT NULL,
    published_at  TEXT,
    collected_at  TEXT NOT NULL,
    ko_title      TEXT,
    subject       TEXT,
    photo_query   TEXT,
    scores        TEXT,                      -- JSON (항목별 1~5)
    score         REAL,
    photos        INTEGER,                   -- 사전 확인한 사용 가능 사진 수 (NULL=미확인)
    status        TEXT NOT NULL DEFAULT 'new',  -- new / excluded / drafted / dismissed
    reason        TEXT,
    draft_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_title_key ON candidates(title_key);

CREATE TABLE IF NOT EXISTS reference_accounts (
    username         TEXT PRIMARY KEY,
    ig_id            TEXT,
    name             TEXT,
    followers_count  INTEGER,
    media_count      INTEGER,
    access_status    TEXT,
    last_checked_at  TEXT,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS reference_posts (
    post_id             TEXT PRIMARY KEY,
    reference_username  TEXT NOT NULL REFERENCES reference_accounts(username),
    permalink           TEXT,
    published_at        TEXT,
    media_type          TEXT,
    media_product_type  TEXT,
    caption             TEXT,
    like_count          INTEGER,
    comments_count      INTEGER,
    view_count          INTEGER,
    first_collected_at  TEXT NOT NULL,
    collected_at        TEXT NOT NULL,
    raw_response_path   TEXT,
    api_version         TEXT
);

CREATE TABLE IF NOT EXISTS post_metric_snapshots (
    post_id         TEXT NOT NULL REFERENCES reference_posts(post_id),
    collected_at    TEXT NOT NULL,
    like_count      INTEGER,
    comments_count  INTEGER,
    view_count      INTEGER,
    PRIMARY KEY (post_id, collected_at)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    # 나중에 추가된 칸 (기존 DB 에도 붙인다)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}
    if "category" not in cols:
        conn.execute("ALTER TABLE candidates ADD COLUMN category TEXT")
        conn.commit()
    return conn


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def save_discovery(
    conn: sqlite3.Connection,
    payload: dict,
    *,
    api_version: str,
    raw_path: str | None = None,
    collected_at: str | None = None,
) -> dict:
    """Business Discovery 응답(최상위 dict)을 저장하고 {account, posts_new, posts_updated} 반환."""
    bd = payload.get("business_discovery")
    if not isinstance(bd, dict) or not bd.get("username"):
        raise ValueError("응답에 business_discovery.username 이 없습니다")
    ts = collected_at or now_iso()
    username = bd["username"]

    conn.execute(
        """
        INSERT INTO reference_accounts
            (username, ig_id, name, followers_count, media_count, access_status, last_checked_at)
        VALUES (?, ?, ?, ?, ?, 'ok', ?)
        ON CONFLICT(username) DO UPDATE SET
            ig_id = excluded.ig_id,
            name = excluded.name,
            followers_count = excluded.followers_count,
            media_count = excluded.media_count,
            access_status = 'ok',
            last_checked_at = excluded.last_checked_at
        """,
        (username, bd.get("id"), bd.get("name"), _int_or_none(bd.get("followers_count")),
         _int_or_none(bd.get("media_count")), ts),
    )

    media = (bd.get("media") or {}).get("data") or []
    new = updated = 0
    for m in media:
        post_id = m.get("id")
        if not post_id:
            continue
        likes = _int_or_none(m.get("like_count"))
        comments = _int_or_none(m.get("comments_count"))
        views = _int_or_none(m.get("view_count"))
        exists = conn.execute("SELECT 1 FROM reference_posts WHERE post_id = ?", (post_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO reference_posts
                (post_id, reference_username, permalink, published_at, media_type, media_product_type,
                 caption, like_count, comments_count, view_count, first_collected_at, collected_at,
                 raw_response_path, api_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                permalink = excluded.permalink,
                caption = excluded.caption,
                media_type = excluded.media_type,
                media_product_type = excluded.media_product_type,
                like_count = excluded.like_count,
                comments_count = excluded.comments_count,
                view_count = excluded.view_count,
                collected_at = excluded.collected_at,
                raw_response_path = excluded.raw_response_path,
                api_version = excluded.api_version
            """,
            (post_id, username, m.get("permalink"), m.get("timestamp"), m.get("media_type"),
             m.get("media_product_type"), m.get("caption"), likes, comments, views, ts, ts,
             raw_path, api_version),
        )
        conn.execute(
            "INSERT OR REPLACE INTO post_metric_snapshots VALUES (?, ?, ?, ?, ?)",
            (post_id, ts, likes, comments, views),
        )
        if exists:
            updated += 1
        else:
            new += 1
    conn.commit()
    return {"account": username, "posts_new": new, "posts_updated": updated}


def mark_account_error(conn: sqlite3.Connection, username: str, status: str) -> None:
    conn.execute(
        """
        INSERT INTO reference_accounts (username, access_status, last_checked_at) VALUES (?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            access_status = excluded.access_status, last_checked_at = excluded.last_checked_at
        """,
        (username, status, now_iso()),
    )
    conn.commit()
