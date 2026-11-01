"""Notion API — 대시보드 페이지 본문 교체 전용."""

from __future__ import annotations

import time
from typing import Any

import requests

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TIMEOUT_SEC = 30
MIN_INTERVAL_SEC = 0.35  # 초당 3회 제한
MAX_TOP_BLOCKS = 20
MAX_TOTAL_BLOCKS = 900
MANAGED_TYPES = frozenset({"callout", "heading_1", "heading_2", "heading_3", "table",
                           "paragraph", "divider"})


class NotionError(RuntimeError):
    """Notion 호출 실패(메시지에 토큰 미포함)."""


def count_blocks(block: dict) -> int:
    body = block.get(block.get("type", ""), {})
    children = body.get("children") or [] if isinstance(body, dict) else []
    return 1 + sum(count_blocks(c) for c in children)


def chunk_blocks(blocks: list[dict]) -> list[list[dict]]:
    """요청당 최상위 20개·전체 900개 이하로 분할."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    total = 0
    for b in blocks:
        n = count_blocks(b)
        if cur and (len(cur) >= MAX_TOP_BLOCKS or total + n > MAX_TOTAL_BLOCKS):
            chunks.append(cur)
            cur, total = [], 0
        cur.append(b)
        total += n
    if cur:
        chunks.append(cur)
    return chunks


class NotionClient:
    def __init__(self, token: str, session: Any | None = None, interval: float = MIN_INTERVAL_SEC):
        self._session = session or requests.Session()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self._interval = interval
        self.calls = 0

    def _call(self, method: str, path: str, **kwargs: Any) -> dict:
        self.calls += 1
        if self._interval:
            time.sleep(self._interval)
        try:
            resp = self._session.request(method, f"{API}{path}", headers=self._headers,
                                         timeout=TIMEOUT_SEC, **kwargs)
        except requests.RequestException as exc:
            raise NotionError(f"{method} {path} 연결 실패: {type(exc).__name__}") from None
        if resp.status_code != 200:
            code = ""
            try:
                code = resp.json().get("code", "")
            except ValueError:
                pass
            raise NotionError(f"{method} {path} HTTP {resp.status_code} {code}".rstrip())
        try:
            return resp.json()
        except ValueError:
            raise NotionError(f"{method} {path} JSON 아님") from None

    def list_children_ids(self, block_id: str) -> list[str]:
        """수집기가 만드는 타입의 자식 블록 id만 반환(하위 페이지·DB 등은 보존 — R6-W6)."""
        ids: list[str] = []
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._call("GET", f"/blocks/{block_id}/children", params=params)
            ids += [b["id"] for b in data.get("results") or [] if b.get("type") in MANAGED_TYPES]
            if not data.get("has_more"):
                return ids
            cursor = data.get("next_cursor")

    def replace_page(self, page_id: str, blocks: list[dict]) -> int:
        """새 블록 추가가 모두 성공한 뒤 기존 블록을 지운다(실패 시 기존 내용 유지)."""
        old_ids = self.list_children_ids(page_id)
        for chunk in chunk_blocks(blocks):
            self._call("PATCH", f"/blocks/{page_id}/children", json={"children": chunk})
        for block_id in old_ids:
            self._call("DELETE", f"/blocks/{block_id}")
        return len(old_ids)
