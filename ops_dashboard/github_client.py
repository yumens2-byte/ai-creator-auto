"""GitHub REST API 읽기 전용 클라이언트 (GET만 사용)."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

API = "https://api.github.com"
TIMEOUT_SEC = 20
MAX_RUN_PAGES = 10
WORKFLOW_DIR = ".github/workflows"


class GitHubError(RuntimeError):
    """GitHub API 호출 실패(메시지에 토큰 미포함)."""


class GitHubClient:
    def __init__(self, token: str, session: Any | None = None, retry_sleep: float = 2.0):
        self._session = session or requests.Session()
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._retry_sleep = retry_sleep
        self.calls = 0

    def _get(self, path: str, params: dict | None = None, raw: bool = False) -> Any:
        headers = dict(self._headers)
        if raw:
            headers["Accept"] = "application/vnd.github.raw+json"
        url = f"{API}{path}"
        for attempt in (1, 2):
            self.calls += 1
            try:
                resp = self._session.get(url, params=params, headers=headers, timeout=TIMEOUT_SEC)
            except requests.RequestException as exc:
                if attempt == 1:
                    time.sleep(self._retry_sleep)
                    continue
                raise GitHubError(f"GET {path} 연결 실패: {type(exc).__name__}") from None
            if resp.status_code >= 500 and attempt == 1:
                time.sleep(self._retry_sleep)
                continue
            if resp.status_code != 200:
                raise GitHubError(f"GET {path} HTTP {resp.status_code}")
            if raw:
                return resp.text
            try:
                return resp.json()
            except ValueError:
                raise GitHubError(f"GET {path} JSON 아님") from None
        raise GitHubError(f"GET {path} 재시도 소진")  # pragma: no cover

    def list_workflows(self, repo: str) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            data = self._get(f"/repos/{repo}/actions/workflows", {"per_page": 100, "page": page})
            items = data.get("workflows") or []
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1

    def workflow_file_shas(self, repo: str) -> dict[str, str]:
        """.github/workflows 디렉터리의 {경로: blob sha}."""
        data = self._get(f"/repos/{repo}/contents/{WORKFLOW_DIR}")
        if not isinstance(data, list):
            raise GitHubError(f"GET /repos/{repo}/contents/{WORKFLOW_DIR} 목록 아님")
        return {
            item["path"]: item.get("sha", "")
            for item in data
            if item.get("type") == "file" and str(item.get("path", "")).endswith((".yml", ".yaml"))
        }

    def file_text(self, repo: str, path: str) -> str:
        return self._get(f"/repos/{repo}/contents/{path}", raw=True)

    def list_runs_since(self, repo: str, since: datetime) -> tuple[list[dict], bool]:
        """since 이후 생성된 실행 목록과 페이지 상한 도달 여부."""
        created = f">={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        out: list[dict] = []
        seen: set = set()
        for page in range(1, MAX_RUN_PAGES + 1):
            data = self._get(
                f"/repos/{repo}/actions/runs",
                {"created": created, "per_page": 100, "page": page},
            )
            items = data.get("workflow_runs") or []
            for item in items:  # 페이지 사이 신규 run 유입으로 밀린 중복 제거(R6-S3)
                if item.get("id") not in seen:
                    seen.add(item.get("id"))
                    out.append(item)
            if len(items) < 100:
                return out, False
        return out, True

    def list_workflow_runs(self, repo: str, workflow_id: int, per_page: int = 5) -> list[dict]:
        data = self._get(
            f"/repos/{repo}/actions/workflows/{workflow_id}/runs", {"per_page": per_page}
        )
        return data.get("workflow_runs") or []

    def failed_steps(self, repo: str, run_id: int) -> list[str]:
        """실패 job·step 이름(최대 3개)."""
        data = self._get(f"/repos/{repo}/actions/runs/{run_id}/jobs", {"filter": "latest"})
        names: list[str] = []
        for job in data.get("jobs") or []:
            if job.get("conclusion") not in ("failure", "timed_out", "startup_failure"):
                continue
            steps = [s.get("name", "") for s in job.get("steps") or []
                     if s.get("conclusion") == "failure"]
            names.append(f"{job.get('name', '')} › {steps[0]}" if steps else job.get("name", ""))
        return names[:3]
