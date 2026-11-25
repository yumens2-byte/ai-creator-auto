"""지연 지표 집계·레벨 판정 — 순수 함수, 단일 진입점 build_report (STD-PROC-13, 상세설계 §4.3·4.4).

대표값(계정·일자)은 신뢰도 HIGH 워크플로만으로 계산한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ops_dashboard.cron_eval import KST
from ops_dashboard.delay.matcher import WorkflowDelay
from ops_dashboard.judge import GRAY, GREEN, RED, YELLOW

DAY = timedelta(days=1)
WEEK = timedelta(days=7)
MIN_SAMPLES = 3


def quantile(values: list[float], p: float) -> float | None:
    """nearest-rank 분위수. 빈 목록은 None."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p * len(ordered)))
    return ordered[rank - 1]


@dataclass(frozen=True)
class Stat:
    n: int
    median: float | None
    p90: float | None
    max: float | None

    @staticmethod
    def of(values: list[float]) -> Stat:
        return Stat(len(values), quantile(values, 0.5), quantile(values, 0.9),
                    max(values) if values else None)


@dataclass(frozen=True)
class Thresholds:
    warn_min: int
    crit_min: int
    drop_warn_pct: int
    drop_crit_pct: int


@dataclass
class AccountSummary:
    account: str
    level: str
    delay_24h: Stat
    dropped_7d: int
    judged_7d: int
    reasons: list[str] = field(default_factory=list)
    basis: str = "24h"  # 판정에 쓴 지연 창: 24h 표본 < 3건이면 7d
    delay_7d: Stat = field(default_factory=lambda: Stat(0, None, None, None))

    @property
    def drop_pct(self) -> float | None:
        return 100 * self.dropped_7d / self.judged_7d if self.judged_7d else None


@dataclass
class DailyRow:
    date: str  # KST YYYY-MM-DD
    account: str
    delay: Stat
    dropped: int


@dataclass
class RepoSummary:
    repo: str
    account: str
    schedule_runs: int
    judged: int
    dropped: int
    delay: Stat          # HIGH 워크플로 7일
    wait: Stat           # 러너 대기(전 워크플로 7일)
    workflows: int
    partial: bool = False
    failed: bool = False


@dataclass
class Report:
    accounts: list[AccountSummary]
    daily: list[DailyRow]
    repos: list[RepoSummary]
    workflows: list[WorkflowDelay]
    failed_repos: list[str]
    partial_repos: list[str]


def level_for(delay: Stat, dropped: int, judged: int, th: Thresholds) -> tuple[str, list[str]]:
    """§4.4: 지연 중앙값과 누락률 중 높은 쪽. 표본이 둘 다 부족하면 ⚪."""
    if delay.n < MIN_SAMPLES and judged < MIN_SAMPLES:
        return GRAY, ["표본 부족"]
    level, reasons = GREEN, []
    if delay.n >= MIN_SAMPLES and delay.median is not None:
        if delay.median >= th.crit_min:
            level, reasons = RED, [f"지연 중앙 ≥ {th.crit_min}분"]
        elif delay.median >= th.warn_min:
            level, reasons = YELLOW, [f"지연 중앙 ≥ {th.warn_min}분"]
    if judged >= MIN_SAMPLES:
        pct = 100 * dropped / judged
        if pct >= th.drop_crit_pct:
            level = RED
            reasons.append(f"누락률 ≥ {th.drop_crit_pct}%")
        elif pct >= th.drop_warn_pct:
            level = RED if level == RED else YELLOW
            reasons.append(f"누락률 ≥ {th.drop_warn_pct}%")
    return level, reasons


def _kst_date(t: datetime) -> str:
    return t.astimezone(KST).date().isoformat()


def build_report(workflows: list[WorkflowDelay], *, now: datetime, th: Thresholds,
                 repos: list[str], failed_repos: list[str] | None = None,
                 partial_repos: list[str] | None = None) -> Report:
    failed_repos = list(failed_repos or [])
    partial_repos = list(partial_repos or [])
    high = [w for w in workflows if w.confidence == "HIGH" and not w.yml_error]

    accounts: list[AccountSummary] = []
    for acct in sorted({r.split("/", 1)[0] for r in repos}):
        mine = [w for w in high if w.account == acct]
        recent = [m.delay_min for w in mine for m in w.matches if now - m.created <= DAY]
        week = [m.delay_min for w in mine for m in w.matches if now - m.created <= WEEK]
        dropped = sum(1 for w in mine for s in w.dropped if now - s <= WEEK)
        judged_matched = sum(1 for w in mine for m in w.matches if now - m.slot <= WEEK)
        judged = dropped + judged_matched
        stat, stat_week = Stat.of(recent), Stat.of(week)
        # 24h 표본이 부족하면 7일 지연으로 판정 — 지연이 커도 누락률만으로 🟢가 되는 것 방지
        basis = "24h" if stat.n >= MIN_SAMPLES else "7d"
        level, reasons = level_for(stat if basis == "24h" else stat_week, dropped, judged, th)
        if basis == "7d" and level != GRAY:
            reasons.append("24h 표본 부족 → 7일 지연 기준")
        accounts.append(AccountSummary(acct, level, stat, dropped, judged, reasons, basis,
                                       stat_week))

    buckets: dict[tuple[str, str], list[float]] = {}
    drops: dict[tuple[str, str], int] = {}
    for w in high:
        for m in w.matches:
            buckets.setdefault((_kst_date(m.slot), w.account), []).append(m.delay_min)
        for s in w.dropped:
            key = (_kst_date(s), w.account)
            drops[key] = drops.get(key, 0) + 1
            buckets.setdefault(key, [])
    daily = [DailyRow(d, a, Stat.of(v), drops.get((d, a), 0))
             for (d, a), v in sorted(buckets.items(), reverse=True)]

    summaries: list[RepoSummary] = []
    for repo in repos:
        mine = [w for w in workflows if w.repo == repo]
        mine_high = [w for w in mine if w.confidence == "HIGH" and not w.yml_error]
        summaries.append(RepoSummary(
            repo=repo, account=repo.split("/", 1)[0],
            schedule_runs=sum(w.runs_since(now - WEEK) for w in mine),
            judged=sum(1 for w in mine_high for m in w.matches if now - m.slot <= WEEK)
            + sum(1 for w in mine_high for s in w.dropped if now - s <= WEEK),
            dropped=sum(1 for w in mine_high for s in w.dropped if now - s <= WEEK),
            delay=Stat.of([m.delay_min for w in mine_high for m in w.matches
                           if now - m.slot <= WEEK]),
            wait=Stat.of([x for w in mine for x in w.waits_min]),
            workflows=len(mine),
            partial=repo in partial_repos,
            failed=repo in failed_repos,
        ))
    return Report(accounts, daily, summaries, workflows, failed_repos, partial_repos)


def workflow_week_stat(w: WorkflowDelay, now: datetime) -> Stat:
    return Stat.of([m.delay_min for m in w.matches if now - m.slot <= WEEK])
