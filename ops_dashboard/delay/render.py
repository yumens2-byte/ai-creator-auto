"""출력 생성 — delay.json · Notion 블록 · 텔레그램 문안. 키 값은 다루지 않는다.

Notion 블록 헬퍼는 기존 대시보드 render 의 것을 재사용한다(DN2026_0128, 기존 파일 무수정).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ops_dashboard.cron_eval import fmt_kst
from ops_dashboard.delay.matcher import WorkflowDelay
from ops_dashboard.delay.metrics import WEEK, AccountSummary, Report, Stat, workflow_week_stat
from ops_dashboard.judge import GRAY, LEVEL_ICON, LEVEL_ORDER, RED, YELLOW
from ops_dashboard.render import TABLE_ROWS_MAX, _clip, _heading, _paragraph, _rt, _table


@dataclass
class Meta:
    version: str
    mode: str
    run_id: str
    sha: str
    run_url: str
    now: datetime
    page_url: str = ""
    window_days: int = 14
    drop_crit_pct: int = 15


def drop_pct(w: WorkflowDelay) -> float | None:
    return 100 * len(w.dropped) / w.judged_slots if w.judged_slots else None


def heavy_drop_workflows(workflows: list[WorkflowDelay], threshold_pct: int
                         ) -> list[tuple[WorkflowDelay, float]]:
    """신뢰도와 관계없이 누락률이 기준 이상인 워크플로(누락률 내림차순).

    레벨 판정에는 쓰지 않는다(표시 전용).
    """
    rows = [(w, p) for w in workflows if not w.yml_error
            and (p := drop_pct(w)) is not None and p >= threshold_pct]
    return sorted(rows, key=lambda x: (-x[1], x[0].key))


def fmt_min(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 60:
        return f"{value:.0f}분"
    return f"{value / 60:.1f}h"


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _short(repo: str) -> str:
    return repo.split("/", 1)[-1]


def _stat_json(s: Stat) -> dict:
    return {"n": s.n, "median_min": s.median, "p90_min": s.p90, "max_min": s.max}


def overall_level(report: Report) -> str:
    present = [a.level for a in report.accounts if a.level != GRAY]
    return max(present, key=LEVEL_ORDER.__getitem__) if present else GRAY


def account_line(a: AccountSummary, icon: bool = True) -> str:
    prefix = f"{LEVEL_ICON[a.level]} " if icon else ""
    stat, label = (a.delay_24h, "24h") if a.basis == "24h" else (a.delay_7d, "7일")
    return (f"{prefix}{a.account} — {label} 지연 중앙 {fmt_min(stat.median)}"
            f" · p90 {fmt_min(stat.p90)} (n={stat.n})"
            f" · 7일 누락 {a.dropped_7d}/{a.judged_7d} ({fmt_pct(a.drop_pct)})")


# ---------------- JSON ----------------

def _wf_json(w: WorkflowDelay, now: datetime) -> dict:
    return {
        "key": w.key, "repo": w.repo, "path": w.path, "name": w.name, "crons": w.crons,
        "confidence": w.confidence, "yml_error": w.yml_error,
        "interval_min": None if w.interval_min == float("inf") else w.interval_min,
        "delay_7d": _stat_json(workflow_week_stat(w, now)),
        "runs_7d": w.runs_since(now - WEEK), "dropped": len(w.dropped), "pending": w.pending,
        "judged_slots": w.judged_slots, "drop_pct": drop_pct(w),
        "dispatch_runs": w.dispatch_runs, "max_gap_excess_min": w.max_gap_excess_min,
        "duration_7d": _stat_json(Stat.of(w.durations_min)),
        "matches": [{"slot": m.slot.isoformat(), "created": m.created.isoformat(),
                     "delay_min": round(m.delay_min, 1), "run_id": m.run_id} for m in w.matches],
        "dropped_slots": [s.isoformat() for s in w.dropped],
    }


def to_json(report: Report, meta: Meta) -> dict:
    return {
        "version": meta.version, "mode": meta.mode, "run_id": meta.run_id, "sha": meta.sha,
        "generated_at": meta.now.isoformat(), "window_days": meta.window_days,
        "overall": overall_level(report),
        "failed_repos": report.failed_repos, "partial_repos": report.partial_repos,
        "accounts": [{"account": a.account, "level": a.level, "reasons": a.reasons,
                      "basis": a.basis, "delay_24h": _stat_json(a.delay_24h),
                      "delay_7d": _stat_json(a.delay_7d), "dropped_7d": a.dropped_7d,
                      "judged_7d": a.judged_7d} for a in report.accounts],
        "daily": [{"date": d.date, "account": d.account, "delay": _stat_json(d.delay),
                   "dropped": d.dropped} for d in report.daily],
        "repos": [{"repo": r.repo, "schedule_runs_7d": r.schedule_runs, "judged_7d": r.judged,
                   "dropped_7d": r.dropped, "delay_7d": _stat_json(r.delay),
                   "wait_7d": _stat_json(r.wait), "workflows": r.workflows,
                   "partial": r.partial, "failed": r.failed} for r in report.repos],
        "workflows": [_wf_json(w, meta.now) for w in sorted(report.workflows,
                                                           key=lambda x: x.key)],
    }


# ---------------- Notion ----------------

def _chunks(header: list[str], body: list[list[dict]]) -> list[dict]:
    if not body:
        return [_paragraph("없음")]
    return [_table(header, body[i:i + TABLE_ROWS_MAX]) for i in range(0, len(body), TABLE_ROWS_MAX)]


def notion_blocks(report: Report, meta: Meta) -> list[dict]:
    head = [account_line(a) for a in report.accounts]
    head.append(f"갱신 {fmt_kst(meta.now)} KST · 수집기 v{meta.version} · mode={meta.mode}"
                f" · sha={meta.sha or '—'} · 데이터 창 {meta.window_days}일")
    if report.failed_repos:
        head.append("수집 실패: " + ", ".join(_short(r) for r in report.failed_repos))
    if report.partial_repos:
        head.append("부분 데이터(실행 목록 상한): "
                    + ", ".join(_short(r) for r in report.partial_repos))
    rich = [_rt("\n".join(head))]
    if meta.run_url:
        rich.append(_rt(" · 실행 로그", url=meta.run_url))
    color = {RED: "red_background", YELLOW: "yellow_background"}.get(
        overall_level(report), "green_background")
    blocks: list[dict] = [{"type": "callout", "callout": {
        "rich_text": rich, "icon": {"type": "emoji", "emoji": "⏱️"}, "color": color}}]

    blocks.append(_heading(f"일별 추이 (최근 {meta.window_days}일, KST · 일 1회 이하 워크플로)"))
    body = [[_rt(d.date), _rt(d.account), _rt(str(d.delay.n)), _rt(fmt_min(d.delay.median)),
             _rt(fmt_min(d.delay.p90)), _rt(fmt_min(d.delay.max)), _rt(str(d.dropped))]
            for d in report.daily]
    blocks += _chunks(["날짜", "계정", "실행", "지연 중앙", "p90", "최대", "누락"], body)

    blocks.append(_heading("레포 요약 (7일)"))
    body = []
    for r in report.repos:
        note = " (수집 실패)" if r.failed else " (부분 데이터)" if r.partial else ""
        pct = 100 * r.dropped / r.judged if r.judged else None
        body.append([_rt(_short(r.repo) + note), _rt(r.account), _rt(str(r.schedule_runs)),
                     _rt(fmt_min(r.delay.median)), _rt(fmt_min(r.delay.p90)),
                     _rt(fmt_min(r.delay.max)), _rt(f"{r.dropped}/{r.judged} ({fmt_pct(pct)})"),
                     _rt(fmt_min(r.wait.median))])
    blocks += _chunks(["레포", "계정", "예약 실행", "지연 중앙", "p90", "최대", "누락",
                       "러너 대기"], body)

    blocks.append(_heading(f"누락 많은 워크플로 (14일, 누락률 ≥ {meta.drop_crit_pct}%)"))
    body = []
    for w, pct in heavy_drop_workflows(report.workflows, meta.drop_crit_pct):
        body.append([_rt(_short(w.repo)), _rt(f"{w.name} ({w.path.rsplit('/', 1)[-1]})"),
                     _rt(w.confidence), _rt(f"{len(w.dropped)}/{w.judged_slots}"),
                     _rt(fmt_pct(pct)), _rt(fmt_min(w.max_gap_excess_min))])
    blocks += _chunks(["레포", "워크플로", "신뢰도", "누락/판정 슬롯", "누락률", "공백 초과"], body)

    blocks.append(_heading("워크플로 상세 (지연·소요 7일, 공백 14일)"))
    for repo in [r.repo for r in report.repos]:
        mine = sorted((w for w in report.workflows if w.repo == repo), key=lambda x: x.path)
        if not mine:
            continue
        blocks.append(_heading(_short(repo), level=3))
        body = []
        for w in mine:
            s = workflow_week_stat(w, meta.now)
            ref = "" if w.confidence == "HIGH" else " (참고)"
            conf = "yml 오류" if w.yml_error else w.confidence
            body.append([
                _rt(f"{w.name} ({w.path.rsplit('/', 1)[-1]})"), _rt(", ".join(w.crons) or "—"),
                _rt(conf), _rt(f"{w.runs_since(meta.now - WEEK)}"),
                _rt(f"{fmt_min(s.median)} / {fmt_min(s.p90)} / {fmt_min(s.max)}{ref}"),
                _rt(str(len(w.dropped))), _rt(fmt_min(w.max_gap_excess_min)),
                _rt(f"{fmt_min(Stat.of(w.durations_min).median)} / "
                    f"{fmt_min(Stat.of(w.durations_min).max)}"),
                _rt(str(w.dispatch_runs)),
            ])
        blocks += _chunks(["워크플로", "cron(UTC)", "신뢰도", "예약 실행", "지연 중앙/p90/최대",
                           "누락(14일)", "공백 초과", "소요 중앙/최대", "dispatch(14일)"], body)

    blocks.append(_paragraph(" ".join([
        "계산 기준: 지연 = GitHub가 run을 만든 시각 − 예정 시각.",
        "실행은 생성 시각 이하의 가장 최근 미매칭 슬롯에 1:1로 붙인다.",
        "이 매칭은 지연이 간격보다 짧을 때만 정확하므로",
        "최소 간격 24시간 이상(HIGH)만 대표값에 쓰고,",
        "그 밖(LOW)은 참고값과 공백 초과(최대 실행 간격 − 최대 예정 간격)로 본다.",
        "누락 = 예정 후 보류창(기본 24시간)이 지나도 실행이 없는 슬롯.",
        "소요 = updated_at − run_started_at 근사값. 지연 원인은 GitHub가 공개하지 않는다.",
        "근거: 상세설계 — 예약 지연 감지 (DN2026_0128)",
    ])))
    return blocks


# ---------------- Telegram ----------------

def tg_transition_text(changes: list[tuple[AccountSummary, str]], meta: Meta) -> str:
    lines = ["[예약 지연 감지] 계정 레벨 변경"]
    for a, prev in changes:
        arrow = f"{LEVEL_ICON.get(prev, '⚪')}→{LEVEL_ICON[a.level]}"
        lines.append(f"{arrow} {account_line(a, icon=False)}")
        if a.reasons:
            lines.append("  사유: " + ", ".join(a.reasons))
    if meta.page_url:
        lines.append(f"페이지: {meta.page_url}")
    return _clip(lines)
