import pytest

from ops_dashboard import cron_eval
from tests.ops_dashboard.conftest import ts

# TS-OPS-03: 시각 행렬 (STD-CI-10)
CASES = [
    # (cron 목록, now, 기대 last_fire_at_or_before)
    (["6 21 * * 0-5"], "2026-11-01T21:06:00", "2026-11-01T21:06:00"),   # 정확히 발화 시각 포함
    (["6 21 * * 0-5"], "2026-11-01T21:05:59", "2026-10-30T21:06:00"),   # 토(6) 제외 → 금
    (["47 21 * * 5"], "2026-11-02T03:00:00", "2026-10-30T21:47:00"),    # 주 1회
    (["14 22 * * *", "9 23 * * *"], "2026-11-01T23:00:00", "2026-11-01T22:14:00"),  # 다중 줄
    (["*/10 15-21 * * 0-4"], "2026-11-01T00:00:00", "2026-10-29T21:50:00"),  # 금·토 제외 → 목
    (["0 0 * * *"], "2026-11-01T00:00:00", "2026-11-01T00:00:00"),       # 자정 경계
]


@pytest.mark.parametrize(("crons", "now", "expected"), CASES)
def test_ref_slot_matrix(crons, now, expected):
    assert cron_eval.last_fire_at_or_before(crons, ts(now)) == ts(expected)


def test_last_fire_before_is_strict():
    t = ts("2026-11-01T00:00:00")
    assert cron_eval.last_fire_before(["0 0 * * *"], t) == ts("2026-10-31T00:00:00")


def test_min_interval_and_next():
    now = ts("2026-11-02T03:00:00")
    assert cron_eval.min_interval_minutes(["*/10 15-21 * * 0-4"], now) == 10
    assert cron_eval.min_interval_minutes(["47 21 * * 5"], now) == 7 * 24 * 60
    assert cron_eval.min_interval_minutes(["14 22 * * *", "9 23 * * *"], now) == 55
    assert cron_eval.next_fire_after(["47 21 * * 5"], now) == ts("2026-11-06T21:47:00")


def test_parse_schedule_variants():
    quoted = '"on":\n  schedule:\n    - cron: "7,37 * * * *"\n  workflow_dispatch:\n'
    bare = "on:\n  schedule:\n    - cron: '0 1 * * 6'\n    - cron: 'bad cron'\n"
    assert cron_eval.parse_schedule(quoted) == ["7,37 * * * *"]
    assert cron_eval.parse_schedule(bare) == ["0 1 * * 6"]
    assert cron_eval.parse_schedule("on: [push]") == []
    assert cron_eval.parse_schedule(":::") == []


def test_fmt_kst():
    assert cron_eval.fmt_kst(ts("2026-11-01T23:07:00")) == "11-02 08:07"
    assert cron_eval.fmt_kst(None) == "—"
