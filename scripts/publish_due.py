#!/usr/bin/env python3
"""예약 시간이 지난 승인 원고를 게시한다. Windows 작업 스케줄러에서 주기적으로 실행할 용도.
기본은 실제 게시. 확인만 하려면 --dry-run."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

from animalpick import config, drafts  # noqa: E402

config.load_env()
results = drafts.run_due(dry_run="--dry-run" in sys.argv)
print("\n".join(results) or "예약된 게시 없음")
