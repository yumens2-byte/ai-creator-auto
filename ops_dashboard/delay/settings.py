"""예약 지연 감지 설정 — 모드·숫자는 기존 config 파서를 그대로 쓴다(STD-MODE-01, STD-CFG-01)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from ops_dashboard.config import PLACEHOLDERS, parse_int, parse_mode

logger = logging.getLogger(__name__)

TARGETS_PATH = Path(__file__).with_name("targets.yaml")

DEFAULT_WARN_MIN = 60
DEFAULT_CRIT_MIN = 240
DEFAULT_DROP_WARN_PCT = 5
DEFAULT_DROP_CRIT_PCT = 15
DEFAULT_PENDING_H = 24


def _optional(raw: str | None) -> str:
    value = (raw or "").strip()
    return "" if value.lower() in PLACEHOLDERS else value


@dataclass(frozen=True)
class DelaySettings:
    mode: str
    gh_token: str
    notion_token: str
    page_id: str
    tg_token: str
    tg_chat: str
    warn_min: int
    crit_min: int
    drop_warn_pct: int
    drop_crit_pct: int
    pending_h: int
    run_id: str
    sha: str
    run_url: str


def _pair(env: Mapping[str, str], warn_key: str, crit_key: str, warn_default: int,
          crit_default: int, maximum: int) -> tuple[int, int]:
    """경고 < 위험 이어야 한다. 순서가 뒤집히면 둘 다 기본값(WARNING)."""
    warn = parse_int(env.get(warn_key), warn_default, 1, maximum)
    crit = parse_int(env.get(crit_key), crit_default, 1, maximum)
    if warn >= crit:
        logger.warning("[config] %s ≥ %s → 기본값 %s/%s", warn_key, crit_key,
                       warn_default, crit_default)
        return warn_default, crit_default
    return warn, crit


def load_delay_settings(env: Mapping[str, str] | None = None) -> DelaySettings:
    env = os.environ if env is None else env
    server = _optional(env.get("GITHUB_SERVER_URL")) or "https://github.com"
    repo = _optional(env.get("GITHUB_REPOSITORY"))
    run_id = _optional(env.get("GITHUB_RUN_ID"))
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""
    warn_min, crit_min = _pair(env, "OPS_DELAY_WARN_MIN", "OPS_DELAY_CRIT_MIN",
                               DEFAULT_WARN_MIN, DEFAULT_CRIT_MIN, 1440)
    drop_warn, drop_crit = _pair(env, "OPS_DELAY_DROP_WARN_PCT", "OPS_DELAY_DROP_CRIT_PCT",
                                 DEFAULT_DROP_WARN_PCT, DEFAULT_DROP_CRIT_PCT, 100)
    return DelaySettings(
        mode=parse_mode(env.get("RUN_MODE")),
        # 기존 대시보드와 같은 토큰 우선순위(DN2026_0087 §6)
        gh_token=_optional(env.get("OPS_GH_TOKEN")) or _optional(env.get("GITHUB_TOKEN_FALLBACK")),
        notion_token=_optional(env.get("NOTION_TOKEN")),
        page_id=_optional(env.get("OPS_DELAY_PAGE_ID")),
        tg_token=_optional(env.get("TELEGRAM_BOT_TOKEN")),
        tg_chat=_optional(env.get("TELEGRAM_PAID_CHANNEL_ID")),
        warn_min=warn_min,
        crit_min=crit_min,
        drop_warn_pct=drop_warn,
        drop_crit_pct=drop_crit,
        pending_h=parse_int(env.get("OPS_DELAY_PENDING_H"), DEFAULT_PENDING_H, 1, 168),
        run_id=run_id,
        sha=_optional(env.get("GITHUB_SHA"))[:7],
        run_url=run_url,
    )


def load_targets(path: Path = TARGETS_PATH) -> tuple[str, ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    repos = tuple(str(r) for r in data.get("repos") or [])
    if not repos:
        raise ValueError("targets.yaml: repos 가 비어 있음")
    return repos
