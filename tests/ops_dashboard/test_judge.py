from datetime import timedelta

from ops_dashboard.judge import GRAY, GREEN, RED, YELLOW, failed_run_id, judge_workflow
from ops_dashboard.registry import Override
from tests.ops_dashboard.conftest import run, ts, wf

DAILY = ["0 0 * * *"]  # 매일 00:00 UTC
GRACE = 120


def judge(now, runs, crons=DAILY, workflow=None, override=None):
    return judge_workflow(repo="o/r", workflow=workflow or wf(), crons=crons, runs=runs,
                          now=now, grace_min=GRACE, override=override or Override())


def test_ok(now):  # TS-OPS-04 — now 11-02 03:00, ref = 11-02 00:00
    row = judge(now, [run(ts("2026-11-02T00:05:00"))])
    assert (row.level, row.codes) == (GREEN, ["OK"])
    assert row.last_success_at == ts("2026-11-02T00:05:00")
    assert row.next_fire == ts("2026-11-03T00:00:00")


def test_missed_once(now):  # TS-OPS-05
    row = judge(now, [run(ts("2026-11-01T00:03:00"))])
    assert (row.level, row.codes) == (YELLOW, ["MISSED"])


def test_within_grace_is_not_missed():
    now = ts("2026-11-02T01:30:00")  # 슬롯 후 90분 < 유예 120분 → ref = 11-01
    row = judge(now, [run(ts("2026-11-01T00:03:00"))])
    assert row.codes == ["OK"]


def test_missed_repeat(now):  # TS-OPS-06
    row = judge(now, [run(ts("2026-10-31T00:03:00"))])
    assert (row.level, row.codes) == (RED, ["MISSED_REPEAT"])


def test_high_freq(now):  # TS-OPS-07 — 매 10분, 유예 120분
    crons = ["*/10 * * * *"]
    two_h = judge(now, [run(now - timedelta(hours=2, minutes=30))], crons=crons)
    assert (two_h.level, two_h.codes) == (YELLOW, ["MISSED"])  # 누락 구간 30분
    seven_h = judge(now, [run(now - timedelta(hours=7))], crons=crons)
    assert (seven_h.level, seven_h.codes) == (YELLOW, ["MISSED"])  # 누락 구간 4시간 50분
    nine_h = judge(now, [run(now - timedelta(hours=9))], crons=crons)
    assert (nine_h.level, nine_h.codes) == (RED, ["MISSED_REPEAT"])  # 누락 구간 6시간 50분


def test_high_freq_window_tail_miss_is_not_red():
    crons = ["*/10 15-21 * * 0-4"]  # 창 끝(21:10~21:50) 누락 뒤 다음 창까지 공백
    now = ts("2026-11-03T08:00:00")
    row = judge(now, [run(ts("2026-11-02T21:00:00"))], crons=crons)
    assert (row.level, row.codes) == (YELLOW, ["MISSED"])


def test_high_freq_irregular_delay_is_yellow_and_full_day_stop_is_red():
    # threads-promo chat.yml 실측 일정(2026-11-01 shadow): 마지막 실행 10-31 17:57 UTC
    crons = ["4 0 * * *", "26 0 * * *", "47 0 * * *", "11 1 * * *", "33 1 * * *",
             "56 1 * * *", "18 2 * * *", "39 2 * * *", "57 2 * * *", "58 6 * * *",
             "17 10 * * *", "44 11 * * *", "19 12 * * *", "36 13 * * *", "54 13 * * *"]
    last = [run(ts("2026-10-31T17:57:00"))]
    delayed = judge(ts("2026-11-01T02:57:00"), last, crons=crons)
    assert (delayed.level, delayed.codes) == (YELLOW, ["MISSED"])
    stopped = judge(ts("2026-11-01T16:00:00"), last, crons=crons)
    assert (stopped.level, stopped.codes) == (RED, ["MISSED_REPEAT"])


def test_failed_and_cancelled(now):  # TS-OPS-08
    created = ts("2026-11-02T00:05:00")
    expect = {"failure": RED, "timed_out": RED, "startup_failure": RED,
              "cancelled": YELLOW, "action_required": YELLOW, "skipped": GREEN, "neutral": GREEN}
    for conclusion, level in expect.items():
        assert judge(now, [run(created, conclusion)]).level == level, conclusion


def test_dispatch_counted_for_alert(now):  # TS-OPS-09
    runs = [run(ts("2026-11-02T00:05:00"), event="workflow_dispatch")]
    with_ov = judge(now, runs, override=Override(count_events=("schedule", "workflow_dispatch")))
    assert with_ov.codes == ["OK"]
    without = judge(now, runs)
    assert without.level == RED  # 예약 실행 0건 → 연속 누락


def test_disabled_ignored_idle_manual(now):  # TS-OPS-10
    assert judge(now, [], workflow=wf(state="disabled_manually")).codes == ["DISABLED"]
    assert judge(now, [], override=Override(ignore=True)).codes == ["IGNORED"]
    idle = judge(now, [run(now - timedelta(days=2), event="push")], crons=[])
    assert (idle.level, idle.codes) == (GRAY, ["IDLE"])
    manual = judge(now, [run(now - timedelta(days=2), "failure", event="push")], crons=[])
    assert (manual.level, manual.codes) == (YELLOW, ["MANUAL_FAILED"])
    old = judge(now, [run(now - timedelta(days=9), "failure", event="push")], crons=[])
    assert old.codes == ["IDLE"]


def test_known_issue_and_stuck(now):  # TS-OPS-11
    ki = judge(now, [run(ts("2026-11-02T00:05:00"), "failure")],
               override=Override(known_issue="KI-TP-01"))
    assert (ki.level, ki.codes) == (YELLOW, ["KNOWN_ISSUE"])
    assert "KI-TP-01" in ki.reason_text()
    stuck = judge(now, [run(now - timedelta(hours=7), None, status="in_progress")],
                  crons=["0 20 * * *"])
    assert "STUCK" in stuck.codes and stuck.level == YELLOW
    running = judge(now, [run(ts("2026-11-02T00:05:00"), None, status="in_progress"),
                          run(ts("2026-11-01T00:05:00"), "success", rid=99)])
    assert (running.level, running.codes) == (GREEN, ["IN_PROGRESS"])


def test_new_workflow_not_missed(now):
    row = judge(now, [], workflow=wf(created="2026-11-02T01:00:00"))
    assert row.codes == ["OK"]


def test_combined_codes_take_highest(now):
    row = judge(now, [run(ts("2026-11-01T00:03:00"), "failure")])
    assert row.codes == ["MISSED", "RUN_FAILED"] and row.level == RED


def test_failed_run_id(now):
    runs = [run(ts("2026-11-02T00:05:00"), "failure", rid=7)]
    row = judge(now, runs)
    assert failed_run_id(row, runs, Override()) == 7
    ok_runs = [run(ts("2026-11-02T00:05:00"))]
    assert failed_run_id(judge(now, ok_runs), ok_runs, Override()) is None
