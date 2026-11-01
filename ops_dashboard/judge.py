"""워크플로 상태 판정 — 단일 진입점 judge_workflow (STD-PROC-13). 순수 함수."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ops_dashboard import cron_eval
from ops_dashboard.registry import Override

RED, YELLOW, GREEN, GRAY = "red", "yellow", "green", "gray"
LEVEL_ORDER = {RED: 3, YELLOW: 2, GREEN: 1, GRAY: 0}
LEVEL_ICON = {RED: "🔴", YELLOW: "🟡", GREEN: "🟢", GRAY: "⚪"}

FAILED = ("failure", "timed_out", "startup_failure")
CANCELLED = ("cancelled", "action_required")
STUCK_AFTER = timedelta(hours=6)
HIGH_FREQ_WINDOW = timedelta(hours=6)
HIGH_FREQ_MIN = 60
MANUAL_WINDOW = timedelta(days=7)

# 사유코드 → (레벨, 표시 문구). 사유마다 별도 코드(STD-PROC-15)
CODES = {
    "OK": (GREEN, "정상"),
    "IN_PROGRESS": (GREEN, "실행 중"),
    "STUCK": (YELLOW, "실행 6시간 초과"),
    "MISSED": (YELLOW, "예정 실행 지연·누락"),
    "MISSED_REPEAT": (RED, "예정 실행 연속 누락"),
    "RUN_FAILED": (RED, "최근 실행 실패"),
    "RUN_CANCELLED": (YELLOW, "최근 실행 취소·승인 대기"),
    "MANUAL_FAILED": (YELLOW, "수동·CI 실행 실패(7일 이내)"),
    "KNOWN_ISSUE": (YELLOW, "알려진 이슈"),
    "AUTO_DISABLED": (YELLOW, "GitHub 자동 비활성(60일 무활동 등)"),
    "YML_PARSE_FAILED": (YELLOW, "워크플로 yml 파싱 실패"),
    "DISABLED": (GRAY, "비활성"),
    "IDLE": (GRAY, "예약 없음"),
    "IGNORED": (GRAY, "판정 제외"),
    "COLLECT_FAILED": (GRAY, "수집 실패"),
}


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)  # 오프셋 없으면 UTC로 간주


@dataclass
class Row:
    repo: str
    path: str
    name: str
    state: str
    crons: list[str]
    codes: list[str] = field(default_factory=list)
    level: str = GRAY
    next_fire: datetime | None = None
    last_run: dict | None = None
    last_success_at: datetime | None = None
    failed_steps: list[str] = field(default_factory=list)
    known_issue: str = ""

    @property
    def key(self) -> str:
        return f"{self.repo}/{self.path}"

    def reason_text(self) -> str:
        parts = [CODES[c][1] for c in self.codes]
        if self.known_issue:
            parts.append(self.known_issue)
        if self.failed_steps:
            parts.append(" / ".join(self.failed_steps))
        return " · ".join(parts)


def _finish(row: Row, codes: list[str]) -> Row:
    row.codes = codes
    row.level = max((CODES[c][0] for c in codes), key=LEVEL_ORDER.__getitem__)
    return row


def _brief(run: dict) -> dict:
    return {
        "id": run.get("id"),
        "event": run.get("event"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"),
        "url": run.get("html_url"),
    }


def judge_workflow(
    *,
    repo: str,
    workflow: dict,
    crons: list[str],
    runs: list[dict],
    now: datetime,
    grace_min: int,
    override: Override,
    yml_error: bool = False,
) -> Row:
    """runs: 이 워크플로의 실행 목록(순서 무관)."""
    row = Row(
        repo=repo,
        path=workflow.get("path", ""),
        name=workflow.get("name", "") or workflow.get("path", ""),
        state=workflow.get("state", ""),
        crons=list(crons),
        known_issue=override.known_issue,
    )
    if override.ignore:
        return _finish(row, ["IGNORED"])
    if row.state == "disabled_inactivity":  # 의도하지 않은 정지 — 조용히 넘기지 않음(R6-W5)
        return _finish(row, ["AUTO_DISABLED"])
    if row.state != "active":
        return _finish(row, ["DISABLED"])
    if yml_error:  # 실행 결과 판정은 계속해 최고 레벨을 쓴다(R6-N3)
        done = sorted((r for r in runs if r.get("status") == "completed"),
                      key=lambda r: r.get("created_at") or "", reverse=True)
        codes = ["YML_PARSE_FAILED"]
        if done and done[0].get("conclusion") in FAILED:
            codes.append("RUN_FAILED")
        return _finish(row, codes)

    ordered = sorted(runs, key=lambda r: r.get("created_at") or "", reverse=True)
    if ordered:
        row.last_run = _brief(ordered[0])
    for run in ordered:
        if run.get("conclusion") == "success":
            row.last_success_at = parse_ts(run.get("created_at"))
            break

    if not crons:
        recent_done = [r for r in ordered if r.get("status") == "completed"]
        if recent_done:
            latest = recent_done[0]
            created = parse_ts(latest.get("created_at"))
            if latest.get("conclusion") in FAILED and created and now - created <= MANUAL_WINDOW:
                return _finish(row, ["MANUAL_FAILED"])
        return _finish(row, ["IDLE"])

    row.next_fire = cron_eval.next_fire_after(crons, now)
    counted = [r for r in ordered if r.get("event") in override.count_events]
    codes: list[str] = []

    # 1) 기대 슬롯 대비 실행 여부
    grace_val = override.grace_minutes if override.grace_minutes is not None else grace_min
    grace = timedelta(minutes=grace_val)
    ref1 = cron_eval.last_fire_at_or_before(crons, now - grace)
    wf_created = parse_ts(workflow.get("created_at"))
    if ref1 is not None and not (wf_created and wf_created > ref1):
        created_list = [parse_ts(r.get("created_at")) for r in counted]
        created_list = [c for c in created_list if c]
        if not any(c >= ref1 for c in created_list):
            interval = cron_eval.min_interval_minutes(crons, now)
            if interval >= HIGH_FREQ_MIN:
                ref2 = cron_eval.last_fire_before(crons, ref1)
                repeat = (
                    ref2 is not None
                    # ref2 이후 생성이면 놓친 슬롯은 1개뿐(R6-W1)
                    and not (wf_created and wf_created > ref2)
                    and not any(c >= ref2 for c in created_list)
                )
            else:
                window_start = now - HIGH_FREQ_WINDOW
                repeat = (
                    not any(c >= window_start for c in created_list)
                    and cron_eval.has_fire_between(crons, window_start, now - grace)
                )
            codes.append("MISSED_REPEAT" if repeat else "MISSED")

    # 2) 최근 실행 결과
    if counted:
        latest = counted[0]
        status = latest.get("status")
        if status != "completed":
            started = parse_ts(latest.get("created_at"))
            codes.append("STUCK" if started and now - started > STUCK_AFTER else "IN_PROGRESS")
            done = [r for r in counted if r.get("status") == "completed"]
            latest = done[0] if done else None
        if latest is not None:
            conclusion = latest.get("conclusion")
            # 실패 뒤 같은 브랜치의 더 새로운 성공(계수 event·수동 재실행)이면 해소(R6-S2).
            # 다른 브랜치 push·PR 성공으로 예약 실패를 가리지 않는다(R6-N1)
            failed_at = parse_ts(latest.get("created_at"))
            fix_events = set(override.count_events) | {"workflow_dispatch"}
            fixed = any(
                r.get("conclusion") == "success"
                and r.get("event") in fix_events
                and r.get("head_branch") == latest.get("head_branch")
                and (parse_ts(r.get("created_at")) or failed_at) > failed_at
                for r in ordered
            ) if failed_at else False
            if conclusion in FAILED and not fixed:
                codes.append("KNOWN_ISSUE" if override.known_issue else "RUN_FAILED")
            elif conclusion in CANCELLED and not fixed:
                codes.append("RUN_CANCELLED")

    if not codes:
        codes.append("OK")
    return _finish(row, codes)


def collect_failed_row(repo: str) -> Row:
    return _finish(Row(repo=repo, path="__collect__", name="수집", state="", crons=[]),
                   ["COLLECT_FAILED"])


def failed_run_id(row: Row, runs: list[dict], override: Override) -> int | None:
    """RUN_FAILED 판정의 근거가 된 실행 id (jobs 조회용)."""
    if "RUN_FAILED" not in row.codes and "KNOWN_ISSUE" not in row.codes:
        return None
    done = sorted(
        (r for r in runs if r.get("event") in override.count_events
         and r.get("status") == "completed"),
        key=lambda r: r.get("created_at") or "",
        reverse=True,
    )
    return done[0].get("id") if done else None
