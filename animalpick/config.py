"""경로와 .env 로딩/저장. 토큰 값은 절대 출력하지 않는다."""
from __future__ import annotations

import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DATA = ROOT / "data"
DRAFTS = DATA / "drafts"
DB_PATH = DATA / "animalpick.db"

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def load_env(path: Path | None = None, *, override: bool = False) -> None:
    """KEY=VALUE 를 읽어 환경변수로. 이미 설정된 값은 덮어쓰지 않는다."""
    path = path or ENV_PATH
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if _KEY_RE.fullmatch(key) and value:
            if override:
                os.environ[key] = value
            else:
                os.environ.setdefault(key, value)


def update_env(values: dict[str, str], path: Path | None = None) -> None:
    """.env 의 해당 키만 바꾸고 나머지 줄·주석은 그대로 둔다. 없는 키는 끝에 추가."""
    path = path or ENV_PATH
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    for k, v in values.items():
        os.environ[k] = v


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, "").strip() or default
