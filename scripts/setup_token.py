#!/usr/bin/env python3
"""처음 한 번만 실행: .env 의 짧은 토큰을 만료 없는 게시용 토큰으로 바꿔 저장한다."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

from animalpick import token_setup  # noqa: E402

try:
    print("\n".join(token_setup.run()))
except (RuntimeError, ValueError) as exc:
    print(f"실패: {exc}", file=sys.stderr)
    sys.exit(1)
