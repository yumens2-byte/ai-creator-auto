"""공용 픽스처 — 외부 호출 없음(STD-TEST-14)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def wf(path: str = ".github/workflows/a.yml", state: str = "active", wid: int = 1,
       created: str = "2026-01-01T00:00:00Z", name: str = "A") -> dict:
    return {"id": wid, "path": path, "state": state, "name": name, "created_at": created}


def run(created: datetime, conclusion: str | None = "success", event: str = "schedule",
        status: str = "completed", rid: int = 100, wid: int = 1) -> dict:
    return {"id": rid, "workflow_id": wid, "event": event, "status": status,
            "conclusion": conclusion, "created_at": iso(created),
            "html_url": f"https://github.com/o/r/actions/runs/{rid}"}


@pytest.fixture
def now() -> datetime:
    # 2026-11-02(월) 03:00 UTC = 12:00 KST
    return ts("2026-11-02T03:00:00")
