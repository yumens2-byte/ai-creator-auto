"""감시 대상·override 로더."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ops_dashboard.config import parse_int

DEFAULT_PATH = Path(__file__).with_name("registry.yaml")


@dataclass(frozen=True)
class Override:
    count_events: tuple[str, ...] = ("schedule",)
    grace_minutes: int | None = None
    ignore: bool = False
    known_issue: str = ""


@dataclass(frozen=True)
class Registry:
    repos: tuple[str, ...]
    overrides: dict[str, Override] = field(default_factory=dict)

    def override_for(self, repo: str, path: str) -> Override:
        return self.overrides.get(f"{repo}/{path}", Override())


def _to_override(raw: dict) -> Override:
    events = raw.get("count_events") or ["schedule"]
    grace = raw.get("grace_minutes")
    grace_val = None
    if grace is not None:  # 범위 밖은 무시하고 기본값 사용(R6-N2)
        grace_val = parse_int(str(grace), -1, 10, 1440)
        grace_val = None if grace_val == -1 else grace_val
    return Override(
        count_events=tuple(str(e) for e in events),
        grace_minutes=grace_val,
        ignore=bool(raw.get("ignore", False)),
        known_issue=str(raw.get("known_issue") or ""),
    )


def load_registry(path: Path = DEFAULT_PATH) -> Registry:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    repos = tuple(str(r) for r in data.get("repos") or [])
    if not repos:
        raise ValueError("registry.yaml: repos 가 비어 있음")
    overrides = {str(k): _to_override(v or {}) for k, v in (data.get("overrides") or {}).items()}
    return Registry(repos=repos, overrides=overrides)
