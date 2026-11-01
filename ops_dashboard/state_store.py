"""실행 간 상태(JSON) — Actions 캐시로 이어받는다(STD-SCHED-06)."""

from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA = 1


def empty_state() -> dict:
    return {"schema": SCHEMA, "levels": {}, "collect_failed": [], "digest_date": None,
            "yml_cache": {}}


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
    for key, kind in (("levels", dict), ("collect_failed", list), ("yml_cache", dict)):
        if not isinstance(data.get(key), kind):
            return empty_state(), False  # 타입 손상 — 손상 상태가 재저장돼 고착되지 않게 초기화
        state[key] = data[key]
    digest = data.get("digest_date")
    state["digest_date"] = digest if isinstance(digest, str) else None
    # 이전 상태 존재 = 유효한 파일이 있음(levels가 비어도 존재로 본다 — R6-C1)
    return state, True


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
