"""테스트용 가짜 클라이언트 — 실제 네트워크 호출 없음."""

from __future__ import annotations

from ops_dashboard.github_client import GitHubError
from ops_dashboard.notion_client import NotionError
from ops_dashboard.tg_client import TelegramError


class FakeGitHub:
    """repos: {repo: {"workflows": [...], "files": {path: yml}, "runs": [...]}}"""

    def __init__(self, repos: dict, fail_repos: tuple[str, ...] = ()):
        self.repos = repos
        self.fail_repos = fail_repos
        self.calls = 0
        self.file_fetches = 0

    def _repo(self, repo):
        self.calls += 1
        if repo in self.fail_repos:
            raise GitHubError(f"GET /repos/{repo} HTTP 500")
        return self.repos[repo]

    def list_workflows(self, repo):
        return self._repo(repo)["workflows"]

    def workflow_file_shas(self, repo):
        return {p: f"sha-{hash(t)}" for p, t in self._repo(repo)["files"].items()}

    def file_text(self, repo, path):
        self.file_fetches += 1
        return self._repo(repo)["files"][path]

    def list_runs_since(self, repo, since):
        return list(self._repo(repo)["runs"]), False

    def list_workflow_runs(self, repo, workflow_id, per_page=5):
        return [r for r in self._repo(repo)["runs"] if r["workflow_id"] == workflow_id][:per_page]

    def failed_steps(self, repo, run_id):
        self._repo(repo)
        return ["build › pytest"]


class FakeNotion:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0
        self.pages: list[tuple[str, list]] = []

    def replace_page(self, page_id, blocks):
        self.calls += 1
        if self.fail:
            raise NotionError("PATCH /blocks HTTP 400 validation_error")
        self.pages.append((page_id, blocks))
        return 3


class FakeTelegram:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[str] = []
        self.attempts = 0

    def send(self, text):
        self.attempts += 1
        if self.fail:
            raise TelegramError("sendMessage HTTP 400")
        self.sent.append(text)
