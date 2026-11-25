from datetime import timedelta

import pytest

from ops_dashboard.delay import schedule
from ops_dashboard.delay.matcher import evaluate_workflow, match_runs
from tests.ops_dashboard.conftest import ts, wf
from tests.ops_dashboard.delay.helpers import NOW, daily_runs, srun

DAILY = '"on":\n  schedule:\n    - cron: "0 1 * * *"\n'
SINCE = NOW - timedelta(days=14)
PENDING = timedelta(hours=24)


def evaluate(yml, runs, workflow=None, now=NOW):
    lines, _ = schedule.parse_cron_lines(yml)
    interval, max_gap = schedule.interval_stats(lines, now)
    slots = schedule.slots_between(lines, SINCE - timedelta(days=1), now)
    return evaluate_workflow(repo="o/r", workflow=workflow or wf(),
                             crons=[ln.label() for ln in lines], slots=slots,
                             interval_min=interval, max_slot_gap_min=max_gap, runs=runs,
                             since=SINCE, now=now, pending=PENDING)


def test_daily_exact():  # TS-DLY-03
    row = evaluate(DAILY, daily_runs(1, 420, 15))
    assert row.confidence == "HIGH"
    # 14일 창 안 슬롯 14개: 오늘 01:00 슬롯은 실행 예정 08:00(미래) → 보류 1, 나머지 13개 매칭
    assert len(row.matches) == 13
    assert {round(m.delay_min) for m in row.matches} == {420}
    assert row.pending == 1
    assert row.dropped == []
    assert row.judged_slots == 13


def test_drop_detected():  # TS-DLY-04
    runs = daily_runs(1, 60, 14, skip=(3,))
    row = evaluate(DAILY, runs)
    assert [s for s in row.dropped] == [ts("2026-11-22T01:00:00")]
    assert len(row.matches) == 13
    assert all(round(m.delay_min) == 60 for m in row.matches)


def test_pending_not_dropped():  # TS-DLY-05
    runs = daily_runs(1, 60, 14, skip=(0,))  # 오늘 01:00 슬롯(2시간 전) 실행 없음
    row = evaluate(DAILY, runs)
    assert row.dropped == []
    assert row.pending == 1


def test_low_confidence():  # TS-DLY-06
    yml = '"on":\n  schedule:\n' + "".join(
        f'    - cron: "{m} {h} * * *"\n' for h, m in [(0, 4), (0, 26), (1, 11), (13, 54)])
    row = evaluate(yml, [srun(NOW - timedelta(hours=2), rid=1)])
    assert row.confidence == "LOW"


def test_dispatch_excluded():  # TS-DLY-07
    runs = daily_runs(1, 30, 3) + [srun(NOW - timedelta(hours=1), rid=900,
                                        event="workflow_dispatch")]
    row = evaluate(DAILY, runs)
    assert row.dispatch_runs == 1
    assert all(m.run_id != 900 for m in row.matches)
    assert row.runs_since(SINCE) == 3


def test_created_after_and_window_boundary():  # TS-DLY-02
    workflow = wf(created="2026-11-20T00:00:00Z")
    row = evaluate(DAILY, daily_runs(1, 30, 5), workflow=workflow)
    assert all(m.slot >= ts("2026-11-20T00:00:00") for m in row.matches)
    assert all(s >= ts("2026-11-20T00:00:00") for s in row.dropped)
    # 창 시작 직전 슬롯의 늦은 실행은 창 안 슬롯에 붙지 않는다
    late = srun(SINCE + timedelta(hours=2), rid=7)  # 창 시작 직전(01:00) 슬롯의 늦은 실행
    row2 = evaluate(DAILY, [late])
    assert row2.matches == []  # 사전 슬롯에 매칭되어 창 안 지표에서 제외


def test_match_runs_latest_unused():
    slots = [ts("2026-11-01T00:00:00"), ts("2026-11-01T01:00:00"), ts("2026-11-01T02:00:00")]
    created = [(ts("2026-11-01T02:30:00"), 1), (ts("2026-11-01T02:40:00"), 2),
               (ts("2026-10-31T23:00:00"), 3)]
    matches, unmatched = match_runs(slots, created)
    assert [(m.run_id, m.slot.hour) for m in matches] == [(1, 2), (2, 1)]
    assert unmatched == [slots[0]]


@pytest.mark.parametrize(("wait", "dur"), [(0.0, 5.0), (3.0, 12.5)])
def test_wait_and_duration(wait, dur):
    run = srun(NOW - timedelta(hours=3), rid=1, wait_min=wait, dur_min=dur)
    row = evaluate(DAILY, [run])
    assert row.waits_min == [wait]
    assert row.durations_min == [dur]
