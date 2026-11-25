"""예약 지연 감지 테스트 헬퍼 — 외부 호출 없음(STD-TEST-14)."""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.ops_dashboard.conftest import iso, ts

NOW = ts("2026-11-25T03:00:00")  # 12:00 KST (고정 now, STD-CI-10)


def srun(created: datetime, *, rid: int, wid: int = 1, event: str = "schedule",
         status: str = "completed", conclusion: str | None = "success",
         wait_min: float = 0.0, dur_min: float = 1.0) -> dict:
    started = created + timedelta(minutes=wait_min)
    return {"id": rid, "workflow_id": wid, "event": event, "status": status,
            "conclusion": conclusion, "created_at": iso(created),
            "run_started_at": iso(started),
            "updated_at": iso(started + timedelta(minutes=dur_min)),
            "html_url": f"https://github.com/o/r/actions/runs/{rid}"}


def daily_runs(cron_hour: int, delay_min: int, days: int, *, wid: int = 1, start_rid: int = 1,
               skip: tuple[int, ...] = (), now: datetime = NOW) -> list[dict]:
    """최근 days 일의 매일 cron_hour:00 UTC 슬롯에 delay_min 만큼 늦은 실행.

    skip = 건너뛸 일 오프셋(0 = 오늘).
    """
    out = []
    base = now.replace(hour=cron_hour, minute=0, second=0, microsecond=0)
    if base > now:
        base -= timedelta(days=1)
    for d in range(days):
        if d in skip:
            continue
        created = base - timedelta(days=d) + timedelta(minutes=delay_min)
        if created <= now:
            out.append(srun(created, rid=start_rid + d, wid=wid))
    return out
