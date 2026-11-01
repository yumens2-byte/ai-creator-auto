"""환경 설정 파서 — STD-MODE-01(모드 3값), STD-CFG-01(빈 값·자리표시자 기본값 처리)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

VALID_MODES = ("dry_run", "shadow", "live")
PLACEHOLDERS = frozenset({"", "-", "—", "none", "null"})
DEFAULT_GRACE_MIN = 120


def _clean(raw: str | None) -> str:
    return (raw or "").strip()


def parse_mode(raw: str | None) -> str:
    """dry_run·shadow·live 외의 값은 dry_run으로 강등하고 WARNING을 남긴다."""
    value = _clean(raw).lower()
    if value in VALID_MODES:
        return value
    if value not in PLACEHOLDERS:
        logger.warning("[config] 알 수 없는 모드 값 → dry_run 강등")
    return "dry_run"


def parse_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    """빈 값·자리표시자·숫자 아님·범위 밖은 기본값."""
    value = _clean(raw)
    if value.lower() in PLACEHOLDERS:
        return default
    try:
        number = int(value)
    except ValueError:
        logger.warning("[config] 숫자 아님 → 기본값 %s", default)
        return default
    if number < minimum or number > maximum:
        logger.warning("[config] 범위 밖(%s~%s) → 기본값 %s", minimum, maximum, default)
        return default
    return number


def _optional(raw: str | None) -> str:
    value = _clean(raw)
    return "" if value.lower() in PLACEHOLDERS else value


@dataclass(frozen=True)
class Settings:
    mode: str
    gh_token: str
    notion_token: str
    page_id: str
    tg_token: str
    tg_chat: str
    grace_min: int
    run_id: str
    sha: str
    run_url: str


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    server = _optional(env.get("GITHUB_SERVER_URL")) or "https://github.com"
    repo = _optional(env.get("GITHUB_REPOSITORY"))
    run_id = _optional(env.get("GITHUB_RUN_ID"))
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""
    return Settings(
        mode=parse_mode(env.get("RUN_MODE")),
        # OPS_GH_TOKEN 우선, 미설정 시 워크플로 기본 토큰(상세설계 §6, DN2026_0087)
        gh_token=_optional(env.get("OPS_GH_TOKEN")) or _optional(env.get("GITHUB_TOKEN_FALLBACK")),
        notion_token=_optional(env.get("NOTION_TOKEN")),
        page_id=_optional(env.get("OPS_DASHBOARD_PAGE_ID")),
        tg_token=_optional(env.get("TELEGRAM_BOT_TOKEN")),
        tg_chat=_optional(env.get("TELEGRAM_PAID_CHANNEL_ID")),
        grace_min=parse_int(env.get("OPS_DEFAULT_GRACE_MIN"), DEFAULT_GRACE_MIN, 10, 1440),
        run_id=run_id,
        sha=_optional(env.get("GITHUB_SHA"))[:7],
        run_url=run_url,
    )
