"""R6-04 독립 리뷰(2026-11-01) 지적 재현 테스트 — 수정 전 FAIL·수정 후 PASS (STD-TEST-02)."""

import json
from datetime import timedelta

import requests

from ops_dashboard.cron_eval import parse_schedule_checked
from ops_dashboard.github_client import GitHubClient
from ops_dashboard.judge import RED, YELLOW, judge_workflow
from ops_dashboard.notion_client import NotionClient
from ops_dashboard.registry import Override, Registry
from tests.ops_dashboard.conftest import run as mkrun
from tests.ops_dashboard.conftest import ts, wf
from tests.ops_dashboard.fakes import FakeGitHub, FakeTelegram
from tests.ops_dashboard.test_clients import Resp, Session
from tests.ops_dashboard.test_main import REPOS, do_run, repo_data

NOW = ts("2026-11-02T03:00:00")


def judge(runs, workflow=None, override=None, crons=("0 0 * * *",), **kw):
    return judge_workflow(repo="o/r", workflow=workflow or wf(), crons=list(crons), runs=runs,
                          now=NOW, grace_min=120, override=override or Override(), **kw)


def test_c1_all_collect_fail_no_repeat_first_summary(tmp_path):
    ok = {r: repo_data() for r in REPOS}
    sent = []
    for _ in range(3):
        tg = FakeTelegram()
        do_run(tmp_path, "live", gh=FakeGitHub(ok, fail_repos=REPOS), tg=tg)
        sent.append(tg.sent)
    assert len(sent[0]) == 1 and "첫 실행 요약" in sent[0][0]
    assert sent[1] == [] and sent[2] == []
    tg = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(ok), tg=tg)
    assert len(tg.sent) == 1 and "수집 복구" in tg.sent[0] and "첫 실행" not in tg.sent[0]


def test_w1_created_between_slots_is_yellow():
    row = judge([], workflow=wf(created="2026-11-01T12:00:00Z"))
    assert (row.level, row.codes) == (YELLOW, ["MISSED"])


def test_w2_failed_transition_resent_next_run(tmp_path):
    ok = {r: repo_data() for r in REPOS}
    bad = {"o/a": repo_data("failure"), "o/b": repo_data()}
    do_run(tmp_path, "live", gh=FakeGitHub(ok))
    do_run(tmp_path, "live", gh=FakeGitHub(bad), tg=FakeTelegram(fail=True))
    tg = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(bad), tg=tg)
    assert len(tg.sent) == 1 and "🔴 장애 1건" in tg.sent[0]


def test_w3_red_to_yellow_is_recovery(tmp_path):
    ok = {r: repo_data() for r in REPOS}
    bad = {"o/a": repo_data("failure"), "o/b": repo_data()}
    cancelled = {"o/a": repo_data("cancelled"), "o/b": repo_data()}
    do_run(tmp_path, "live", gh=FakeGitHub(ok))
    do_run(tmp_path, "live", gh=FakeGitHub(bad))
    tg = FakeTelegram()
    do_run(tmp_path, "live", gh=FakeGitHub(cancelled), tg=tg)
    assert len(tg.sent) == 1 and "해소 1건" in tg.sent[0] and "🔴→🟡" in tg.sent[0]


def test_w4_non_github_error_isolated(tmp_path):
    class Boom(FakeGitHub):
        def list_runs_since(self, repo, since):
            if repo == "o/a":
                raise KeyError("id")
            return super().list_runs_since(repo, since)
    code, _, notion, _ = do_run(tmp_path, "shadow", gh=Boom({r: repo_data() for r in REPOS}))
    assert code == 1 and notion.calls == 1
    data = json.loads((tmp_path / "out" / "dashboard.json").read_text())
    assert data["failed_repos"] == ["o/a"]
    gh = GitHubClient("t", session=Session([requests.exceptions.ChunkedEncodingError("x"),
                                            Resp(200, None)]), retry_sleep=0)
    try:
        gh.list_workflows("o/r")
        raise AssertionError("예외 기대")
    except Exception as exc:  # noqa: BLE001
        assert type(exc).__name__ == "GitHubError"


def test_w5_inactivity_disabled_is_yellow():
    row = judge([], workflow=wf(state="disabled_inactivity"))
    assert (row.level, row.codes) == (YELLOW, ["AUTO_DISABLED"])


def test_w6_child_pages_not_deleted():
    s = Session([Resp(200, {"results": [{"id": "t1", "type": "table"},
                                        {"id": "cp", "type": "child_page"},
                                        {"id": "db", "type": "child_database"}],
                            "has_more": False}),
                 Resp(200, {}), Resp(200, {})])
    removed = NotionClient("tok", session=s, interval=0).replace_page(
        "p", [{"type": "paragraph", "paragraph": {"rich_text": []}}])
    assert removed == 1
    deleted = [c[0][1] for c in s.calls if c[0][0] == "DELETE"]
    assert deleted == ["https://api.notion.com/v1/blocks/t1"]


def test_s1_yml_parse_failure_visible(tmp_path):
    assert parse_schedule_checked("a: [unclosed")[1] is False
    data = repo_data()
    data["files"][".github/workflows/daily.yml"] = "a: [unclosed"
    do_run(tmp_path, "dry_run", gh=FakeGitHub({"o/a": data}),
           registry=Registry(repos=("o/a",), overrides={}))
    rows = json.loads((tmp_path / "out" / "dashboard.json").read_text())["rows"]
    daily = next(r for r in rows if r["path"].endswith("daily.yml"))
    assert daily["codes"] == ["YML_PARSE_FAILED"] and daily["level"] == YELLOW


def test_s2_newer_manual_success_resolves_failure():
    runs = [mkrun(ts("2026-11-02T00:05:00"), "failure", rid=1),
            mkrun(ts("2026-11-02T01:00:00"), "success", event="workflow_dispatch", rid=2)]
    assert judge(runs).codes == ["OK"]
    assert judge(runs[:1]).level == RED


def test_s6_zero_grace_override():
    runs = [mkrun(NOW - timedelta(hours=26))]  # 11-01 01:00 실행
    zero = judge(runs, override=Override(grace_minutes=0), crons=("0 3 * * *",))
    default = judge(runs, crons=("0 3 * * *",))
    assert zero.codes == ["MISSED_REPEAT"]  # 유예 0 → 오늘 03:00·어제 03:00 모두 누락
    assert default.codes == ["MISSED"]  # 유예 120 → 어제 03:00만 누락


def test_n1_other_branch_push_success_does_not_hide_failure():
    fail = mkrun(ts("2026-11-02T00:05:00"), "failure", rid=1)
    fail["head_branch"] = "main"
    push_ok = mkrun(ts("2026-11-02T01:00:00"), "success", event="push", rid=2)
    push_ok["head_branch"] = "feature/x"
    assert judge([fail, push_ok]).codes == ["RUN_FAILED"]
    manual_other = mkrun(ts("2026-11-02T01:00:00"), "success", event="workflow_dispatch", rid=3)
    manual_other["head_branch"] = "feature/x"
    assert judge([fail, manual_other]).codes == ["RUN_FAILED"]
    manual_main = dict(manual_other, head_branch="main")
    assert judge([fail, manual_main]).codes == ["OK"]


def test_n2_registry_grace_range(tmp_path):
    from ops_dashboard.registry import load_registry
    p = tmp_path / "r.yaml"
    p.write_text("repos: [o/a]\noverrides:\n  o/a/x.yml: {grace_minutes: -1440}\n"
                 "  o/a/y.yml: {grace_minutes: 30}\n")
    reg = load_registry(p)
    assert reg.override_for("o/a", "x.yml").grace_minutes is None
    assert reg.override_for("o/a", "y.yml").grace_minutes == 30


def test_n3_yml_error_keeps_run_failure():
    row = judge([mkrun(ts("2026-11-02T00:05:00"), "failure")], crons=(), yml_error=True)
    assert row.codes == ["YML_PARSE_FAILED", "RUN_FAILED"] and row.level == RED
