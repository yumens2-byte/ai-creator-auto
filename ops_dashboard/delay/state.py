"""실행 간 상태(JSON) — Actions 캐시(키 ops-delay-state-)로 이어받는다.

대시보드 상태(state/ops_state.json)와 파일을 분리한다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA = 1


def empty_state() -> dict:
    return {"schema": SCHEMA, "levels": {}, "yml_cache": {}}


def load_state(path: Path) -> tuple[dict, bool]:
    """(상태, 이전 상태 존재 여부). 손상·스키마 불일치는 빈 상태."""
    if not path.exists():
        return empty_state(), False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_state(), False
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return empty_state(), False
    state = empty_state()
    for key in ("levels", "yml_cache"):
        if not isinstance(data.get(key), dict):
            return empty_state(), False
        state[key] = data[key]
    return state, True


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
