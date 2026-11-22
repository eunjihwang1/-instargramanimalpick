#!/usr/bin/env python3
"""검수 대시보드 실행 (어느 폴더에서 실행해도 동작)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animalpick.server import main  # noqa: E402

main()
