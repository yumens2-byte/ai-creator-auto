import json
import logging
from dataclasses import replace

import pytest

from ops_dashboard import VERSION
from ops_dashboard.config import load_settings
from ops_dashboard.main import run
from ops_dashboard.registry import Override, Registry
from tests.ops_dashboard.conftest import run as mkrun
from tests.ops_dashboard.conftest import ts, wf
from tests.ops_dashboard.fakes import FakeGitHub, FakeNotion, FakeTelegram

DAILY_YML = '"on":\n  schedule:\n    - cron: "0 0 * * *"\n'
CI_YML = '"on":\n  push:\n'
REPOS = ("o/a", "o/b")
NOW = ts("2026-11-02T03:00:00")  # 12:00 KST


def repo_data(conclusion="success"):
    return {
        "workflows": [wf(".github/workflows/daily.yml", wid=1, name="Daily"),
                      wf(".github/workflows/ci.yml", wid=2, name="CI")],
        "files": {".github/workflows/daily.yml": DAILY_YML, ".github/workflows/ci.yml": CI_YML},
        "runs": [mkrun(ts("2026-11-02T00:05:00"), conclusion, rid=11, wid=1),
                 mkrun(ts("2026-11-01T10:00:00"), "success", event="push", rid=12, wid=2)],
    }


def settings(mode, **kw):
    base = load_settings({"RUN_MODE": mode, "OPS_DASHBOARD_PAGE_ID": "page-1",
                          "NOTION_TOKEN": "secret-notion-xyz", "TELEGRAM_BOT_TOKEN": "123:tgsecret",
                          "TELEGRAM_PAID_CHANNEL_ID": "-100", "GITHUB_TOKEN_FALLBACK": "ghs_secret",
                          "GITHUB_RUN_ID": "77", "GITHUB_REPOSITORY": "o/ops",
                          "GITHUB_SHA": "abc1234"})
    return replace(base, **kw)


REG = Registry(repos=REPOS, overrides={})


def do_run(tmp_path, mode, gh=None, notion=None, tg=None, now=NOW, registry=REG, st=None):
    gh = gh or FakeGitHub({r: repo_data() for r in REPOS})
    notion = notion if notion is not None else FakeNotion()
    tg = tg if tg is not None else FakeTelegram()
    code = run(st or settings(mode), gh=gh, notion=notion, tg=tg, registry=registry, now=now,
               state_path=tmp_path / "state" / "s.json", out_dir=tmp_path / "out")
    return code, gh, notion, tg


@pytest.mark.parametrize(("mode", "notion_calls", "tg_sent", "state_saved"), [
    ("dry_run", 0, 0, False), ("shadow", 1, 0, True), ("live", 1, 1, True)])
def test_mode_side_effects(tmp_path, mode, notion_calls, tg_sent, state_saved):  # TS-OPS-17
    code, _, notion, tg = do_run(tmp_path, mode)
    assert code == 0
    assert notion.calls == notion_calls
    assert len(tg.sent) == tg_sent  # live 첫 실행 = 요약 1건
    assert (tmp_path / "state" / "s.json").exists() is state_saved
    data = json.loads((tmp_path / "out" / "dashboard.json").read_text())
    assert data["mode"] == mode and len(data["rows"]) == 4
    assert json.loads((tmp_path / "out" / "run_result.json").read_text())["status"] == "ok"


def test_partial_collect_failure(tmp_path):  # TS-OPS-18
    gh = FakeGitHub({r: repo_data() for r in REPOS}, fail_repos=("o/b",))
    code, _, notion, _ = do_run(tmp_path, "shadow", gh=gh)
    assert code == 1
    data = json.loads((tmp_path / "out" / "dashboard.json").read_text())
    assert data["failed_repos"] == ["o/b"]
    codes = {r["key"]: r["codes"] for r in data["rows"]}
    assert codes["o/b/__collect__"] == ["COLLECT_FAILED"]
    assert codes["o/a/.github/workflows/daily.yml"] == ["OK"]
    assert notion.calls == 1  # 나머지는 게시
    result = json.loads((tmp_path / "out" / "run_result.json").read_text())
    assert result["status"] == "partial" and "collect:o/b" in result["errors"]


def test_start_log_and_no_secret(tmp_path, caplog):  # TS-OPS-19
    with caplog.at_level(logging.INFO):
        do_run(tmp_path, "live")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert f"[ops_dashboard] v{VERSION} 시작 | mode=live | run_id=77 | sha=abc1234" in text
    outputs = text + (tmp_path / "out" / "dashboard.json").read_text()
    for secret in ("secret-notion-xyz", "tgsecret", "ghs_secret"):
        assert secret not in outputs


def test_transitions_enter_and_recover(tmp_path):  # TS-OPS-12 (통합)
    ok = {r: repo_data() for r in REPOS}
    do_run(tmp_path, "live", gh=FakeGitHub(ok))  # 1회차: 상태 없음 → 요약 1건
    bad = {"o/a": repo_data("failure"), "o/b": repo_data()}
    tg2 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(bad), tg=tg2)
    assert len(tg2.sent) == 1 and "🔴 장애 1건" in tg2.sent[0] and "build › pytest" in tg2.sent[0]
    tg3 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(bad), tg=tg3)  # 같은 🔴 유지 → 알림 없음
    assert tg3.sent == []
    tg4 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok), tg=tg4)
    assert len(tg4.sent) == 1 and "해소 1건" in tg4.sent[0]


def test_collect_failure_alert_once_and_keeps_levels(tmp_path):
    ok = {r: repo_data() for r in REPOS}
    do_run(tmp_path, "live", gh=FakeGitHub(ok))
    tg2 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok, fail_repos=("o/a",)), tg=tg2)
    assert len(tg2.sent) == 1 and "수집 실패: a" in tg2.sent[0]
    state = json.loads((tmp_path / "state" / "s.json").read_text())
    assert state["levels"]["o/a/.github/workflows/daily.yml"] == "green"  # 이전 상태 유지
    tg3 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok, fail_repos=("o/a",)), tg=tg3)
    assert tg3.sent == []
    tg4 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok), tg=tg4)
    assert len(tg4.sent) == 1 and "수집 복구: a" in tg4.sent[0]


def test_daily_digest_once(tmp_path):  # TS-OPS-13
    ok = {r: repo_data() for r in REPOS}
    first = ts("2026-11-01T22:30:00")  # 07:30 KST 첫 실행 → 첫 요약, digest_date 미기록
    do_run(tmp_path, "live", gh=FakeGitHub(ok), now=first)
    assert json.loads((tmp_path / "state" / "s.json").read_text())["digest_date"] is None
    tg_759 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok), tg=tg_759, now=ts("2026-11-01T22:59:00"))
    assert tg_759.sent == []
    tg_807 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok), tg=tg_807, now=ts("2026-11-01T23:07:00"))
    assert len(tg_807.sent) == 1 and "일일 요약" in tg_807.sent[0]
    tg_837 = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok), tg=tg_837, now=ts("2026-11-01T23:37:00"))
    assert tg_837.sent == []


def test_digest_retried_when_send_fails(tmp_path):
    ok = {r: repo_data() for r in REPOS}
    do_run(tmp_path, "live", gh=FakeGitHub(ok), now=ts("2026-11-01T22:30:00"))
    code, *_ = do_run(tmp_path, "live", gh=FakeGitHub(ok), tg=FakeTelegram(fail=True),
                      now=ts("2026-11-01T23:07:00"))
    assert code == 1
    tg = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok), tg=tg, now=ts("2026-11-01T23:37:00"))
    assert len(tg.sent) == 1 and "일일 요약" in tg.sent[0]


def test_notion_failure_and_missing_config(tmp_path):
    code, *_ = do_run(tmp_path, "shadow", notion=FakeNotion(fail=True))
    assert code == 1
    code2, *_ = do_run(tmp_path, "shadow", st=settings("shadow", page_id=""))
    assert code2 == 1
    res = json.loads((tmp_path / "out" / "run_result.json").read_text())
    assert "notion:config" in res["errors"]


def test_yml_cache_reused(tmp_path):
    gh1 = FakeGitHub({r: repo_data() for r in REPOS})
    do_run(tmp_path, "shadow", gh=gh1)
    assert gh1.file_fetches == 4
    gh2 = FakeGitHub({r: repo_data() for r in REPOS})
    do_run(tmp_path, "shadow", gh=gh2)
    assert gh2.file_fetches == 0


def test_override_applied(tmp_path):
    reg = Registry(repos=("o/a",), overrides={
        "o/a/.github/workflows/daily.yml": Override(ignore=True)})
    gh = FakeGitHub({"o/a": repo_data("failure")})
    do_run(tmp_path, "dry_run", gh=gh, registry=reg)
    data = json.loads((tmp_path / "out" / "dashboard.json").read_text())
    row = next(r for r in data["rows"] if r["path"].endswith("daily.yml"))
    assert row["codes"] == ["IGNORED"]
