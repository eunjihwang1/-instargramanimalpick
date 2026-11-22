#!/usr/bin/env python3
"""오늘의 후보 수집 (매일 새벽 작업 스케줄러용).

  python scripts/collect_topics.py                 후보만 모음 (대시보드에서 골라 원고 만들기)
  python scripts/collect_topics.py --auto-draft    후보를 모은 뒤 하루 구성(해외5·국내2·상시3)대로 원고까지 생성
원고는 '검수 대기' 상태로 쌓일 뿐, 승인 없이는 게시되지 않는다.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

from animalpick import config, db, sources  # noqa: E402

config.load_env(override=True)
conn = db.connect(config.DB_PATH)
try:
    result = sources.collect(conn=conn)
    if "--auto-draft" in sys.argv:
        picks = sources.pick_daily(sources.list_candidates(conn, days=2))
        print(f"\n원고 자동 생성: {len(picks)}개")
        sources.make_drafts(picks, conn=conn)
finally:
    conn.close()
