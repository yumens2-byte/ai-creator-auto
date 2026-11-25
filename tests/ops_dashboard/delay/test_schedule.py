from datetime import timedelta

from ops_dashboard.delay import schedule
from tests.ops_dashboard.conftest import ts


def test_slots_utc_and_tz():  # TS-DLY-01
    yml = ('"on":\n  schedule:\n    - cron: "0 0 * * *"\n'
           '    - cron: "30 9 * * *"\n      timezone: "Asia/Seoul"\n'
           '    - cron: "0 12 * * 6"\n')
    lines, ok = schedule.parse_cron_lines(yml)
    assert ok
    assert [ln.label() for ln in lines] == ["0 0 * * *", "30 9 * * * [Asia/Seoul]", "0 12 * * 6"]
    start, end = ts("2026-11-20T23:00:00"), ts("2026-11-22T23:00:00")  # 금 23:00 → 일 23:00 UTC
    slots = schedule.slots_between(lines, start, end)
    assert slots == [
        ts("2026-11-21T00:00:00"),
        ts("2026-11-21T00:30:00"),  # 09:30 KST = 00:30 UTC
        ts("2026-11-21T12:00:00"),  # 토요일 주 1회
        ts("2026-11-22T00:00:00"),
        ts("2026-11-22T00:30:00"),
    ]
    assert all(s.tzinfo is not None and s.utcoffset() == timedelta(0) for s in slots)


def test_parse_invalid_and_no_schedule():
    assert schedule.parse_cron_lines("on:\n  push:\n") == ([], True)  # 따옴표 없는 on
    assert schedule.parse_cron_lines(": : :")[1] is False
    lines, ok = schedule.parse_cron_lines(
        '"on":\n  schedule:\n    - cron: "61 * * * *"\n    - cron: "0 1 * * *"\n'
        '      timezone: "Mars/Base"\n    - cron: "5 2 * * *"\n')
    assert ok is False
    assert [ln.expr for ln in lines] == ["5 2 * * *"]


def test_interval_stats():
    daily, _ = schedule.parse_cron_lines('"on":\n  schedule:\n    - cron: "47 1 * * *"\n')
    assert schedule.interval_stats(daily, ts("2026-11-25T03:00:00")) == (1440.0, 1440.0)
    multi, _ = schedule.parse_cron_lines(
        '"on":\n  schedule:\n    - cron: "10 0 * * *"\n    - cron: "10 9 * * *"\n')
    assert schedule.interval_stats(multi, ts("2026-11-25T03:00:00")) == (540.0, 900.0)
