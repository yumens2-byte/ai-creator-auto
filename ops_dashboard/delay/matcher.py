"""실행 ↔ 예정 슬롯 매칭 — 순수 함수 (상세설계 §4.2, DN2026_0128).

규칙: 예약(schedule) 실행을 생성 순으로 보며, 생성 시각 이하이면서 아직 짝이 없는 슬롯 중
가장 최근 슬롯에 1:1로 붙인다. 이 매칭은 '지연 < 최소 간격'일 때만 정확하므로
최소 간격 ≥ 24시간인 워크플로만 신뢰도 HIGH 로 둔다.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ops_dashboard.judge import parse_ts

HIGH_CONFIDENCE_MIN = 24 * 60
STATS_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class Match:
    slot: datetime
    created: datetime
    run_id: int | None

    @property
    def delay_min(self) -> float:
        return (self.created - self.slot).total_seconds() / 60


@dataclass
class WorkflowDelay:
    repo: str
    path: str
    name: str
    crons: list[str]
    interval_min: float
    max_slot_gap_min: float
    confidence: str  # HIGH | LOW
    matches: list[Match] = field(default_factory=list)   # 창 안 슬롯에 매칭된 실행
    dropped: list[datetime] = field(default_factory=list)  # 보류창 지난 무매칭 슬롯
    pending: int = 0           # 보류창 안 무매칭 슬롯(아직 늦게 올 수 있음)
    judged_slots: int = 0      # 판정 가능 슬롯(매칭 + 누락)
    run_times: list[datetime] = field(default_factory=list)  # 창 안 예약 실행 생성 시각
    dispatch_runs: int = 0
    max_gap_excess_min: float | None = None
    durations_min: list[float] = field(default_factory=list)
    waits_min: list[float] = field(default_factory=list)
    yml_error: bool = False

    @property
    def key(self) -> str:
        return f"{self.repo}/{self.path}"

    @property
    def account(self) -> str:
        return self.repo.split("/", 1)[0]

    def runs_since(self, t: datetime) -> int:
        return sum(1 for c in self.run_times if c >= t)


def confidence_of(interval_min: float) -> str:
    return "HIGH" if interval_min >= HIGH_CONFIDENCE_MIN else "LOW"


def match_runs(slots: list[datetime], created: list[tuple[datetime, int | None]]
               ) -> tuple[list[Match], list[datetime]]:
    """(매칭 목록, 짝 없는 슬롯). slots 는 정렬된 UTC 시각, created 는 (생성 시각, run id)."""
    used = [False] * len(slots)
    matches: list[Match] = []
    for c, rid in sorted(created, key=lambda x: x[0]):
        i = bisect.bisect_right(slots, c) - 1
        while i >= 0 and used[i]:
            i -= 1
        if i < 0:
            continue  # 창 이전 슬롯의 실행 — 지연 계산에서 제외
        used[i] = True
        matches.append(Match(slot=slots[i], created=c, run_id=rid))
    unmatched = [s for s, u in zip(slots, used, strict=True) if not u]
    return matches, unmatched


def _minutes(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    value = (b - a).total_seconds() / 60
    return value if value >= 0 else None


def evaluate_workflow(*, repo: str, workflow: dict, crons: list[str], slots: list[datetime],
                      interval_min: float, max_slot_gap_min: float, runs: list[dict],
                      since: datetime, now: datetime, pending: timedelta) -> WorkflowDelay:
    """slots 는 since 이전 하루치(사전 슬롯)를 포함해 넘긴다 — 창 경계 실행의 오매칭 방지."""
    wf_created = parse_ts(workflow.get("created_at"))
    if wf_created is not None:
        slots = [s for s in slots if s >= wf_created]
    sched = [r for r in runs if r.get("event") == "schedule"]
    created = [(parse_ts(r.get("created_at")), r.get("id")) for r in sched]
    created = [(c, rid) for c, rid in created if c is not None]
    matches, unmatched = match_runs(slots, created)

    row = WorkflowDelay(
        repo=repo, path=workflow.get("path", ""),
        name=workflow.get("name", "") or workflow.get("path", ""), crons=list(crons),
        interval_min=interval_min, max_slot_gap_min=max_slot_gap_min,
        confidence=confidence_of(interval_min),
    )
    row.matches = [m for m in matches if m.slot >= since]
    in_window = [s for s in unmatched if s >= since]
    row.dropped = [s for s in in_window if now - s >= pending]
    row.pending = len(in_window) - len(row.dropped)
    judged_matched = [m for m in row.matches if now - m.slot >= pending]
    row.judged_slots = len(judged_matched) + len(row.dropped)
    times = sorted(c for c, _ in created if c >= since)
    row.run_times = times
    row.dispatch_runs = sum(1 for r in runs if r.get("event") == "workflow_dispatch"
                            and (parse_ts(r.get("created_at")) or since) >= since)
    if times:
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:], strict=False)]
        gaps.append((now - times[-1]).total_seconds() / 60)  # 마지막 실행 이후 현재까지
        if max_slot_gap_min != float("inf"):
            row.max_gap_excess_min = max(0.0, max(gaps) - max_slot_gap_min)

    stats_since = max(since, now - STATS_WINDOW)  # 소요·대기는 최근 7일
    for r in runs:
        run_created = parse_ts(r.get("created_at"))
        if run_created is None or run_created < stats_since:
            continue
        started = parse_ts(r.get("run_started_at"))
        if r.get("status") == "completed":
            dur = _minutes(started, parse_ts(r.get("updated_at")))
            if dur is not None:
                row.durations_min.append(dur)
        if r.get("event") == "schedule":
            wait = _minutes(run_created, started)
            if wait is not None:
                row.waits_min.append(wait)
    return row
