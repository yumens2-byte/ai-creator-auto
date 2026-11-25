"""예정 슬롯 — yml on.schedule[]의 (cron, timezone) 을 읽어 UTC 발화 시각을 만든다.

기존 cron_eval.parse_schedule_checked 는 timezone 키를 읽지 않으므로(cron_eval.py 30~40행)
이 모듈에서 따로 읽는다. 기존 모듈은 수정하지 않는다(DN2026_0128).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from croniter import croniter

INTERVAL_SPAN = timedelta(days=35)  # 최소 간격 계산 구간(월 1회 미만은 '매우 김'으로 처리)
SLOT_LIMIT = 20000


@dataclass(frozen=True)
class CronLine:
    expr: str
    tz: str = "UTC"

    def label(self) -> str:
        return self.expr if self.tz == "UTC" else f"{self.expr} [{self.tz}]"


def parse_cron_lines(yml_text: str) -> tuple[list[CronLine], bool]:
    """(cron 줄 목록, 파싱 성공 여부). 잘못된 cron·timezone 줄이 있으면 실패로 본다."""
    try:
        data = yaml.safe_load(yml_text) or {}
    except yaml.YAMLError:
        return [], False
    if not isinstance(data, dict):
        return [], False
    on = data.get("on", data.get(True))  # PyYAML은 따옴표 없는 on 을 True 로 읽음
    if not isinstance(on, dict):
        return [], True
    lines: list[CronLine] = []
    ok = True
    for item in on.get("schedule") or []:
        if not (isinstance(item, dict) and isinstance(item.get("cron"), str)):
            ok = False
            continue
        expr = item["cron"].strip()
        tz = str(item.get("timezone") or "UTC").strip() or "UTC"
        if not croniter.is_valid(expr) or not _valid_tz(tz):
            ok = False
            continue
        lines.append(CronLine(expr, tz))
    return lines, ok


def _valid_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def slots_between(lines: list[CronLine], start: datetime, end: datetime) -> list[datetime]:
    """start 초과 end 이하 발화 시각(UTC aware, 합집합·정렬)."""
    fires: set[datetime] = set()
    for line in lines:
        it = croniter(line.expr, start.astimezone(ZoneInfo(line.tz)))
        for _ in range(SLOT_LIMIT):
            nxt = it.get_next(datetime).astimezone(UTC)
            if nxt > end:
                break
            fires.add(nxt)
    return sorted(fires)


def _gaps(slots: list[datetime]) -> list[float]:
    return [(b - a).total_seconds() / 60 for a, b in zip(slots, slots[1:], strict=False)]


def interval_stats(lines: list[CronLine], around: datetime) -> tuple[float, float]:
    """(최소 간격, 최대 간격) 분. around 직전 35일 기준. 발화 2회 미만이면 (inf, inf)."""
    gaps = _gaps(slots_between(lines, around - INTERVAL_SPAN, around))
    if not gaps:
        return float("inf"), float("inf")
    return min(gaps), max(gaps)
