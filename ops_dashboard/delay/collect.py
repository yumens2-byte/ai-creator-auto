"""레포 수집 — 기존 GitHubClient 메서드만 호출한다(GET 전용, DN2026_0128)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from ops_dashboard.delay import schedule
from ops_dashboard.delay.matcher import WorkflowDelay, evaluate_workflow
from ops_dashboard.github_client import GitHubClient

WINDOW = timedelta(days=14)
PRE_WINDOW = timedelta(days=1)  # 창 경계 실행이 창 안 슬롯에 잘못 붙지 않도록 사전 슬롯 생성


def collect_repo(gh: GitHubClient, repo: str, *, now: datetime, pending: timedelta,
                 yml_cache: dict) -> tuple[list[WorkflowDelay], bool]:
    """(워크플로 지연 목록, runs 페이지 상한 도달 여부). 활성 + 예약 있는 워크플로만."""
    since = now - WINDOW
    workflows = gh.list_workflows(repo)
    shas = gh.workflow_file_shas(repo)
    lines_by_path: dict[str, tuple[list[schedule.CronLine], bool]] = {}
    for path, sha in shas.items():
        key = f"{repo}/{path}"
        cached = yml_cache.get(key)
        if isinstance(cached, dict) and sha and cached.get("sha") == sha:
            lines = [schedule.CronLine(str(e), str(z)) for e, z in cached.get("lines") or []]
            ok = cached.get("ok", True) is not False
        else:
            lines, ok = schedule.parse_cron_lines(gh.file_text(repo, path))
            yml_cache[key] = {"sha": sha, "lines": [[ln.expr, ln.tz] for ln in lines], "ok": ok}
        lines_by_path[path] = (lines, ok)

    runs, truncated = gh.list_runs_since(repo, since - PRE_WINDOW)
    by_wf: dict[int, list[dict]] = defaultdict(list)
    for run in runs:
        by_wf[run.get("workflow_id")].append(run)

    out: list[WorkflowDelay] = []
    for wf in workflows:
        if wf.get("state") != "active":
            continue
        path = wf.get("path", "")
        lines, ok = lines_by_path.get(path, ([], True))
        if not lines and ok:
            continue  # 예약 없음 — 지연 대상 아님
        interval, max_gap = schedule.interval_stats(lines, now) if lines else (0.0, 0.0)
        slots = schedule.slots_between(lines, since - PRE_WINDOW, now) if lines else []
        row = evaluate_workflow(
            repo=repo, workflow=wf, crons=[ln.label() for ln in lines], slots=slots,
            interval_min=interval, max_slot_gap_min=max_gap, runs=by_wf.get(wf.get("id"), []),
            since=since, now=now, pending=pending)
        row.yml_error = not ok
        out.append(row)
    return out, truncated
