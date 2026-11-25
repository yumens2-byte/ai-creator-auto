from datetime import timedelta

import pytest

from ops_dashboard.delay.matcher import Match, WorkflowDelay
from ops_dashboard.delay.metrics import Stat, Thresholds, build_report, level_for, quantile
from ops_dashboard.judge import GRAY, GREEN, RED, YELLOW
from tests.ops_dashboard.conftest import ts
from tests.ops_dashboard.delay.helpers import NOW

TH = Thresholds(60, 240, 5, 15)


def wd(repo="a/r", path=".github/workflows/x.yml", confidence="HIGH", matches=(), dropped=()):
    return WorkflowDelay(repo=repo, path=path, name="X", crons=["0 1 * * *"],
                         interval_min=1440, max_slot_gap_min=1440, confidence=confidence,
                         matches=list(matches), dropped=list(dropped))


def m(slot, delay_min):
    return Match(slot=slot, created=slot + timedelta(minutes=delay_min), run_id=None)


def test_quantile_nearest_rank():
    assert quantile([], 0.5) is None
    assert quantile([5], 0.9) == 5
    assert quantile([1, 2, 3, 4], 0.5) == 2
    assert quantile(list(range(1, 11)), 0.9) == 9


def test_quantiles_and_daily_kst():  # TS-DLY-08
    # 11-24 14:59 UTC = 23:59 KST(11-24), 11-24 15:00 UTC = 00:00 KST(11-25)
    a = wd(matches=[m(ts("2026-11-24T14:59:00"), 100), m(ts("2026-11-24T15:00:00"), 200),
                    m(ts("2026-11-24T16:00:00"), 300)])
    rep = build_report([a], now=NOW, th=TH, repos=["a/r"])
    by_date = {d.date: d for d in rep.daily}
    assert by_date["2026-11-24"].delay.n == 1
    assert by_date["2026-11-25"].delay.n == 2
    assert by_date["2026-11-25"].delay.median == 200
    acct = rep.accounts[0]
    assert acct.delay_24h.n == 3 and acct.delay_24h.median == 200 and acct.delay_24h.p90 == 300


@pytest.mark.parametrize(("median", "dropped", "judged", "n", "expected"), [
    (59, 0, 100, 3, GREEN), (60, 0, 100, 3, YELLOW), (239, 0, 100, 3, YELLOW),
    (240, 0, 100, 3, RED), (30, 4, 100, 3, GREEN), (30, 5, 100, 3, YELLOW),
    (30, 14, 100, 3, YELLOW), (30, 15, 100, 3, RED), (300, 5, 100, 3, RED),
    (300, 0, 2, 2, GRAY), (None, 3, 3, 0, RED),
])
def test_level_matrix(median, dropped, judged, n, expected):  # TS-DLY-09
    stat = Stat(n, median, median, median)
    assert level_for(stat, dropped, judged, TH)[0] == expected


def test_low_excluded_from_account():
    high = wd(matches=[m(NOW - timedelta(hours=h), 30) for h in (2, 3, 4)])
    low = wd(path=".github/workflows/y.yml", confidence="LOW",
             matches=[m(NOW - timedelta(hours=h), 900) for h in (2, 3, 4)])
    rep = build_report([high, low], now=NOW, th=TH, repos=["a/r"])
    assert rep.accounts[0].delay_24h.median == 30
    assert rep.accounts[0].level == GREEN


def test_accounts_split_and_repo_summary():
    a = wd(repo="acct1/r1", matches=[m(NOW - timedelta(hours=h), 500) for h in (2, 3, 4)])
    b = wd(repo="acct2/r2", matches=[m(NOW - timedelta(hours=h), 10) for h in (2, 3, 4)],
           dropped=[NOW - timedelta(days=2)])
    rep = build_report([a, b], now=NOW, th=TH, repos=["acct1/r1", "acct2/r2"],
                       failed_repos=[], partial_repos=["acct2/r2"])
    levels = {x.account: x.level for x in rep.accounts}
    assert levels == {"acct1": RED, "acct2": RED}  # acct2: 누락 1/4 = 25%
    r2 = [r for r in rep.repos if r.repo == "acct2/r2"][0]
    assert (r2.dropped, r2.judged, r2.partial) == (1, 4, True)


def test_small_24h_sample_falls_back_to_week():
    # 24h 안 실행 1건뿐이지만 7일 지연이 7시간 — 누락 0이어도 🔴여야 한다
    a = wd(matches=[m(NOW - timedelta(days=d, hours=10), 420) for d in range(5)])
    rep = build_report([a], now=NOW, th=TH, repos=["a/r"])
    acct = rep.accounts[0]
    assert acct.delay_24h.n == 1
    assert (acct.basis, acct.level) == ("7d", RED)
    assert "24h 표본 부족 → 7일 지연 기준" in acct.reasons
