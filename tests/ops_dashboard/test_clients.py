from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from ops_dashboard import notify_failure
from ops_dashboard.github_client import GitHubClient, GitHubError
from ops_dashboard.notion_client import NotionClient, NotionError
from ops_dashboard.registry import load_registry
from ops_dashboard.state_store import load_state, save_state
from ops_dashboard.tg_client import TelegramClient, TelegramError


class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    get = post = request = _next


def test_pagination_retry():  # TS-OPS-14
    page = [{"id": i, "workflow_id": 1} for i in range(100)]
    page2 = [{"id": i, "workflow_id": 1} for i in range(100, 105)]
    s = Session([Resp(200, {"workflow_runs": page}), Resp(502),
                 Resp(200, {"workflow_runs": page2})])
    gh = GitHubClient("t", session=s, retry_sleep=0)
    runs, truncated = gh.list_runs_since("o/r", datetime(2026, 10, 25, tzinfo=UTC))
    assert len(runs) == 105 and truncated is False
    assert gh.calls == 3
    assert s.calls[0][1]["params"]["created"] == ">=2026-10-25T00:00:00Z"
    assert s.calls[0][1]["headers"]["Authorization"] == "Bearer t"

    gh404 = GitHubClient("t", session=Session([Resp(404)]), retry_sleep=0)
    with pytest.raises(GitHubError, match="HTTP 404"):
        gh404.list_workflows("o/r")

    conn = Session([requests.ConnectionError("x"), requests.ConnectionError("x")])
    with pytest.raises(GitHubError, match="연결 실패"):
        GitHubClient("t", session=conn, retry_sleep=0).list_workflows("o/r")


def test_github_helpers():
    shas = Session([Resp(200, [
        {"type": "file", "path": ".github/workflows/a.yml", "sha": "s1"},
        {"type": "file", "path": ".github/workflows/README.md", "sha": "s2"},
        {"type": "dir", "path": ".github/workflows/sub", "sha": "s3"}])])
    assert GitHubClient("", session=shas).workflow_file_shas("o/r") == {
        ".github/workflows/a.yml": "s1"}
    jobs = Session([Resp(200, {"jobs": [
        {"name": "build", "conclusion": "failure",
         "steps": [{"name": "setup", "conclusion": "success"},
                   {"name": "pytest", "conclusion": "failure"}]},
        {"name": "lint", "conclusion": "success", "steps": []}]})])
    assert GitHubClient("", session=jobs).failed_steps("o/r", 1) == ["build › pytest"]
    raw = Session([Resp(200, text="on: push")])
    gh = GitHubClient("", session=raw)
    assert gh.file_text("o/r", ".github/workflows/a.yml") == "on: push"
    assert raw.calls[0][1]["headers"]["Accept"] == "application/vnd.github.raw+json"
    assert "Authorization" not in raw.calls[0][1]["headers"]


def test_notion_replace_order_and_errors():
    s = Session([Resp(200, {"results": [{"id": "old1", "type": "callout"},
                                         {"id": "old2", "type": "table"}], "has_more": False}),
                 Resp(200, {"results": []}), Resp(200, {}), Resp(200, {})])
    nc = NotionClient("tok", session=s, interval=0)
    assert nc.replace_page("p", [{"type": "paragraph", "paragraph": {"rich_text": []}}]) == 2
    methods = [c[0][0] for c in s.calls]
    assert methods == ["GET", "PATCH", "DELETE", "DELETE"]  # 추가 성공 후 삭제

    fail = Session([Resp(200, {"results": [{"id": "old1", "type": "table"}], "has_more": False}),
                    Resp(400, {"code": "validation_error"})])
    nc2 = NotionClient("tok", session=fail, interval=0)
    with pytest.raises(NotionError, match="validation_error"):
        nc2.replace_page("p", [{"type": "paragraph", "paragraph": {"rich_text": []}}])
    assert [c[0][0] for c in fail.calls] == ["GET", "PATCH"]  # 기존 블록 삭제 안 함


def test_telegram_masks_token():
    tg = TelegramClient("123:SECRET", "-1", session=Session([requests.Timeout("bot123:SECRET")]))
    with pytest.raises(TelegramError) as ei:
        tg.send("x")
    assert "SECRET" not in str(ei.value)
    bad = TelegramClient("123:SECRET", "-1", session=Session([Resp(401, {"ok": False})]))
    with pytest.raises(TelegramError, match="HTTP 401"):
        bad.send("x")
    good_s = Session([Resp(200, {"ok": True})])
    good = TelegramClient("123:SECRET", "-1", session=good_s)
    good.send("hello")
    assert good.sent == ["hello"] and good_s.calls[0][1]["data"]["chat_id"] == "-1"


def test_state_roundtrip_and_corrupt(tmp_path: Path):
    p = tmp_path / "s.json"
    assert load_state(p) == (load_state(p)[0], False)
    save_state(p, {"schema": 1, "levels": {"k": "red"}, "collect_failed": [],
                   "digest_date": "2026-11-01", "yml_cache": {}})
    state, had = load_state(p)
    assert had and state["levels"] == {"k": "red"}
    p.write_text("{broken")
    assert load_state(p)[1] is False
    p.write_text('{"schema": 99, "levels": {"k": "red"}}')
    assert load_state(p)[1] is False


def test_registry_file_loads():
    reg = load_registry()
    assert len(reg.repos) == 7
    alert = reg.override_for("yumens2-byte/investment-alert", ".github/workflows/alert.yml")
    assert alert.count_events == ("schedule", "workflow_dispatch")
    assert reg.override_for("o/r", "x.yml").count_events == ("schedule",)


def test_notify_failure_should_notify(tmp_path: Path):
    p = tmp_path / "r.json"
    assert notify_failure.should_notify(p) is True
    p.write_text('{"status": "partial"}')
    assert notify_failure.should_notify(p) is False
    p.write_text('{"status": "fatal"}')
    assert notify_failure.should_notify(p) is True
