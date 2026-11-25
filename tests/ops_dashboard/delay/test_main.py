import json
import logging
from dataclasses import replace

import pytest

from ops_dashboard.delay import VERSION
from ops_dashboard.delay.main import run
from ops_dashboard.delay.settings import load_delay_settings
from tests.ops_dashboard.conftest import wf
from tests.ops_dashboard.delay.helpers import NOW, daily_runs
from tests.ops_dashboard.fakes import FakeGitHub, FakeNotion, FakeTelegram

DAILY_YML = '"on":\n  schedule:\n    - cron: "0 1 * * *"\n'
CI_YML = '"on":\n  push:\n'
REPOS = ("acct/a", "acct/b")


def repo_data(delay_min=420):
    return {
        "workflows": [wf(".github/workflows/daily.yml", wid=1, name="Daily"),
                      wf(".github/workflows/ci.yml", wid=2, name="CI")],
        "files": {".github/workflows/daily.yml": DAILY_YML, ".github/workflows/ci.yml": CI_YML},
        "runs": daily_runs(1, delay_min, 15, wid=1),
    }


def settings(mode, **kw):
    base = load_delay_settings({"RUN_MODE": mode, "OPS_DELAY_PAGE_ID": "page-1",
                                "NOTION_TOKEN": "secret-notion-xyz",
                                "TELEGRAM_BOT_TOKEN": "123:tgsecret",
                                "TELEGRAM_PAID_CHANNEL_ID": "-100",
                                "GITHUB_TOKEN_FALLBACK": "ghs_secret", "GITHUB_RUN_ID": "77",
                                "GITHUB_REPOSITORY": "o/ops", "GITHUB_SHA": "abc1234"})
    return replace(base, **kw)


def do_run(tmp_path, mode, gh=None, notion=None, tg=None, st=None, repos=REPOS):
    gh = gh or FakeGitHub({r: repo_data() for r in repos})
    notion = notion if notion is not None else FakeNotion()
    tg = tg if tg is not None else FakeTelegram()
    code = run(st or settings(mode), gh=gh, notion=notion, tg=tg, repos=repos, now=NOW,
               state_path=tmp_path / "state" / "d.json", out_dir=tmp_path / "out")
    return code, gh, notion, tg


@pytest.mark.parametrize(("mode", "notion_calls", "state_saved"), [
    ("dry_run", 0, False), ("shadow", 1, True), ("live", 1, True)])
def test_mode_side_effects(tmp_path, mode, notion_calls, state_saved):  # TS-DLY-13
    code, _, notion, tg = do_run(tmp_path, mode)
    assert code == 0
    assert notion.calls == notion_calls
    assert tg.sent == []  # 첫 실행은 알림 없음
    assert (tmp_path / "state" / "d.json").exists() is state_saved
    data = json.loads((tmp_path / "out" / "delay.json").read_text())
    assert data["version"] == VERSION
    assert data["accounts"][0]["level"] == "red"
    assert {w["path"] for w in data["workflows"]} == {".github/workflows/daily.yml"}


def test_tg_transition_once(tmp_path):  # TS-DLY-14
    st_path = tmp_path / "state" / "d.json"
    do_run(tmp_path, "live")  # 상태 없음 → 0건, 🔴 저장
    _, _, _, tg = do_run(tmp_path, "live")  # 🔴→🔴 → 0건
    assert tg.sent == []
    state = json.loads(st_path.read_text())
    state["levels"]["acct"] = "yellow"
    st_path.write_text(json.dumps(state))
    _, _, _, tg = do_run(tmp_path, "live")  # 🟡→🔴 → 1건
    assert len(tg.sent) == 1 and "🟡→🔴" in tg.sent[0]


def test_tg_failure_keeps_previous_level(tmp_path):
    st_path = tmp_path / "state" / "d.json"
    do_run(tmp_path, "live")
    state = json.loads(st_path.read_text())
    state["levels"]["acct"] = "yellow"
    st_path.write_text(json.dumps(state))
    code, _, _, _ = do_run(tmp_path, "live", tg=FakeTelegram(fail=True))
    assert code == 1
    assert json.loads(st_path.read_text())["levels"]["acct"] == "yellow"  # 다음 실행에 재전환


def test_partial_repo_failure(tmp_path):  # TS-DLY-15
    gh = FakeGitHub({r: repo_data() for r in REPOS}, fail_repos=("acct/b",))
    code, _, notion, _ = do_run(tmp_path, "shadow", gh=gh)
    assert code == 1
    data = json.loads((tmp_path / "out" / "delay.json").read_text())
    assert data["failed_repos"] == ["acct/b"]
    assert {w["repo"] for w in data["workflows"]} == {"acct/a"}
    assert notion.calls == 1
    result = json.loads((tmp_path / "out" / "run_result.json").read_text())
    assert result["status"] == "partial"


class TruncatedGitHub(FakeGitHub):
    def list_runs_since(self, repo, since):
        runs, _ = super().list_runs_since(repo, since)
        return runs, True


def test_truncated_runs(tmp_path):  # TS-DLY-16
    gh = TruncatedGitHub({r: repo_data() for r in REPOS})
    code, _, notion, _ = do_run(tmp_path, "shadow", gh=gh)
    assert code == 0
    data = json.loads((tmp_path / "out" / "delay.json").read_text())
    assert data["partial_repos"] == list(REPOS)
    callout = notion.pages[0][1][0]["callout"]["rich_text"][0]["text"]["content"]
    assert "부분 데이터" in callout


def test_yml_cache_reused(tmp_path):
    _, gh1, _, _ = do_run(tmp_path, "shadow")
    _, gh2, _, _ = do_run(tmp_path, "shadow")
    assert gh1.file_fetches == 4 and gh2.file_fetches == 0


def test_notion_failure_partial(tmp_path):
    code, _, _, _ = do_run(tmp_path, "shadow", notion=FakeNotion(fail=True))
    assert code == 1


def test_start_log_no_secret(tmp_path, caplog):  # TS-DLY-17
    caplog.set_level(logging.INFO)
    do_run(tmp_path, "live")
    text = caplog.text
    assert f"[ops_delay] v{VERSION} 시작 | mode=live | run_id=77 | sha=abc1234" in text
    for secret in ("secret-notion-xyz", "tgsecret", "ghs_secret"):
        assert secret not in text
    out = (tmp_path / "out" / "delay.json").read_text()
    assert "secret" not in out
