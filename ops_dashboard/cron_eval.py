"""cron 기대 슬롯 계산 (UTC aware datetime 전용)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml
from croniter import croniter

KST = timezone(timedelta(hours=9))
_ONE_SEC = timedelta(seconds=1)


def parse_schedule(yml_text: str) -> list[str]:
    """워크플로 yml의 on.schedule[].cron 목록. 파싱 실패·없음은 빈 목록."""
    return parse_schedule_checked(yml_text)[0]


def parse_schedule_checked(yml_text: str) -> tuple[list[str], bool]:
    """(cron 목록, yml 파싱 성공 여부). 파싱 실패는 판정에서 별도 코드로 드러낸다(R6-S1)."""
    try:
        data = yaml.safe_load(yml_text) or {}
    except yaml.YAMLError:
        return [], False
    if not isinstance(data, dict):
        return [], False
    return _schedule_from(data), True


def _schedule_from(data: dict) -> list[str]:
    on = data.get("on", data.get(True))  # PyYAML은 따옴표 없는 on 을 True 로 읽음
    if not isinstance(on, dict):
        return []
    out = []
    for item in on.get("schedule") or []:
        if isinstance(item, dict) and isinstance(item.get("cron"), str):
            expr = item["cron"].strip()
            if croniter.is_valid(expr):
                out.append(expr)
    return out


def last_fire_at_or_before(crons: list[str], t: datetime) -> datetime | None:
    """t 이하의 가장 최근 발화 시각."""
    best = None
    for expr in crons:
        prev = croniter(expr, t + _ONE_SEC).get_prev(datetime)
        if best is None or prev > best:
            best = prev
    return best


def last_fire_before(crons: list[str], t: datetime) -> datetime | None:
    """t 미만의 가장 최근 발화 시각."""
    best = None
    for expr in crons:
        prev = croniter(expr, t).get_prev(datetime)
        if best is None or prev > best:
            best = prev
    return best


def next_fire_after(crons: list[str], t: datetime) -> datetime | None:
    best = None
    for expr in crons:
        nxt = croniter(expr, t).get_next(datetime)
        if best is None or nxt < best:
            best = nxt
    return best


def min_interval_minutes(crons: list[str], around: datetime, samples: int = 50) -> float:
    """around 직전 1주일 구간에서 합친 발화열의 최소 간격(분)."""
    start = around - timedelta(days=7)
    fires: set[datetime] = set()
    for expr in crons:
        it = croniter(expr, start)
        for _ in range(samples):
            nxt = it.get_next(datetime)
            if nxt > around:
                break
            fires.add(nxt)
    ordered = sorted(fires)
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(ordered, ordered[1:], strict=False)]
    return min(gaps) if gaps else 7 * 24 * 60.0


def fires_between(crons: list[str], start: datetime, end: datetime,
                  limit: int = 5000) -> list[datetime]:
    """start 초과 end 이하 발화 시각(합집합, 정렬). limit 은 줄당 상한."""
    fires: set[datetime] = set()
    for expr in crons:
        it = croniter(expr, start)
        for _ in range(limit):
            nxt = it.get_next(datetime)
            if nxt > end:
                break
            fires.add(nxt)
    return sorted(fires)


def has_fire_between(crons: list[str], start: datetime, end: datetime) -> bool:
    nxt = next_fire_after(crons, start)
    return nxt is not None and nxt <= end


def fmt_kst(t: datetime | None) -> str:
    return t.astimezone(KST).strftime("%m-%d %H:%M") if t else "—"
