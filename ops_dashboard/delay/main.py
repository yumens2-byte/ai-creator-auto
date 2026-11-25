"""예약 지연 감지 진입점 — python -m ops_dashboard.delay.main (DN2026_0128)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ops_dashboard.delay import VERSION
from ops_dashboard.delay.collect import WINDOW, collect_repo
from ops_dashboard.delay.matcher import WorkflowDelay
from ops_dashboard.delay.metrics import Thresholds, build_report
from ops_dashboard.delay.render import (
    Meta,
    account_line,
    notion_blocks,
    tg_transition_text,
    to_json,
)
from ops_dashboard.delay.settings import DelaySettings, load_delay_settings, load_targets
from ops_dashboard.delay.state import load_state, save_state
from ops_dashboard.github_client import GitHubClient, GitHubError
from ops_dashboard.judge import GRAY
from ops_dashboard.main import setup_logging
from ops_dashboard.notion_client import NotionClient, NotionError
from ops_dashboard.tg_client import TelegramClient, TelegramError

logger = logging.getLogger("ops_delay")

STATE_PATH = Path("state/delay_state.json")
OUT_DIR = Path("out")


def run(settings: DelaySettings, *, gh: GitHubClient, notion: NotionClient | None,
        tg: TelegramClient | None, repos: tuple[str, ...], now: datetime,
        state_path: Path = STATE_PATH, out_dir: Path = OUT_DIR) -> int:
    logger.info("[ops_delay] v%s 시작 | mode=%s | run_id=%s | sha=%s",
                VERSION, settings.mode, settings.run_id or "-", settings.sha or "-")
    state, had_state = load_state(state_path)
    errors: list[str] = []
    pending = timedelta(hours=settings.pending_h)

    workflows: list[WorkflowDelay] = []
    failed: list[str] = []
    partial: list[str] = []
    for repo in repos:
        try:
            rows, truncated = collect_repo(gh, repo, now=now, pending=pending,
                                           yml_cache=state["yml_cache"])
            workflows += rows
            if truncated:
                partial.append(repo)
        except Exception as exc:  # noqa: BLE001 — 레포 1개 실패가 전체를 막지 않게 격리
            detail = str(exc) if isinstance(exc, GitHubError) else type(exc).__name__
            logger.error("[collect] %s 수집 실패: %s", repo, detail)
            failed.append(repo)
            errors.append(f"collect:{repo}")
    logger.info("[collect] 레포 %d개, 예약 워크플로 %d개, GitHub 호출 %d회",
                len(repos), len(workflows), gh.calls)

    th = Thresholds(settings.warn_min, settings.crit_min, settings.drop_warn_pct,
                    settings.drop_crit_pct)
    report = build_report(workflows, now=now, th=th, repos=list(repos), failed_repos=failed,
                          partial_repos=partial)
    for a in report.accounts:
        logger.info("[metrics] %s", account_line(a))

    page_url = f"https://www.notion.so/{settings.page_id.replace('-', '')}" if settings.page_id \
        else ""
    meta = Meta(version=VERSION, mode=settings.mode, run_id=settings.run_id, sha=settings.sha,
                run_url=settings.run_url, now=now, page_url=page_url,
                window_days=WINDOW.days, drop_crit_pct=settings.drop_crit_pct)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "delay.json").write_text(
        json.dumps(to_json(report, meta), ensure_ascii=False, indent=1), encoding="utf-8")

    if settings.mode in ("shadow", "live"):
        if not (notion and settings.page_id):
            logger.error("[notion] 설정 누락(NOTION_TOKEN 또는 OPS_DELAY_PAGE_ID)")
            errors.append("notion:config")
        else:
            try:
                removed = notion.replace_page(settings.page_id, notion_blocks(report, meta))
                logger.info("[notion] 페이지 갱신 완료(이전 블록 %d개 교체)", removed)
            except NotionError as exc:
                logger.error("[notion] 갱신 실패: %s", exc)
                errors.append("notion:write")

    # 계정 레벨 전환 — 첫 실행·⚪·수집 실패 계정은 알리지 않는다
    prev_levels: dict = state["levels"] if had_state else {}
    failed_accounts = {r.split("/", 1)[0] for r in failed}
    changes = [(a, prev_levels[a.account]) for a in report.accounts
               if a.account in prev_levels and a.level != GRAY
               and prev_levels[a.account] != GRAY and a.account not in failed_accounts
               and prev_levels[a.account] != a.level]
    sent_ok = True
    if changes:
        text = tg_transition_text(changes, meta)
        if settings.mode == "live":
            if not tg:
                logger.error("[tg] 설정 누락(TELEGRAM_BOT_TOKEN 또는 TELEGRAM_PAID_CHANNEL_ID)")
                errors.append("tg:config")
                sent_ok = False
            else:
                try:
                    tg.send(text)
                    logger.info("[tg] 레벨 전환 %d건 발송", len(changes))
                except TelegramError as exc:
                    logger.error("[tg] 발송 실패: %s", exc)
                    errors.append("tg:transition")
                    sent_ok = False
        else:
            logger.info("[tg] (%s) 발송 안 함 — 보낼 예정: 레벨 전환 %d건", settings.mode,
                        len(changes))
    else:
        logger.info("[tg] 대상 없음")

    if settings.mode in ("shadow", "live"):
        new_levels = dict(prev_levels)
        for a in report.accounts:
            if a.account in failed_accounts or a.level == GRAY:
                continue  # 이전 레벨 유지 — 거짓 전환 방지
            if not sent_ok and any(c[0].account == a.account for c in changes):
                continue  # 발송 실패 시 이전 레벨 유지 → 다음 실행에서 다시 전환으로 잡힘
            new_levels[a.account] = a.level
        state["levels"] = new_levels
        save_state(state_path, state)
        logger.info("[state] 저장 완료(계정 %d개)", len(new_levels))

    status = "partial" if errors else "ok"
    (out_dir / "run_result.json").write_text(
        json.dumps({"status": status, "errors": errors, "version": VERSION}, ensure_ascii=False),
        encoding="utf-8")
    logger.info("[ops_delay] 종료 | status=%s | errors=%s", status, ",".join(errors) or "-")
    return 0 if status == "ok" else 1


def main() -> int:
    setup_logging()
    try:
        settings = load_delay_settings()
        gh = GitHubClient(settings.gh_token)
        notion = NotionClient(settings.notion_token) if settings.notion_token else None
        tg = (TelegramClient(settings.tg_token, settings.tg_chat)
              if settings.tg_token and settings.tg_chat else None)
        return run(settings, gh=gh, notion=notion, tg=tg, repos=load_targets(),
                   now=datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 — 치명 실패는 결과 파일로 남기고 yml 실패 알림 step에 넘김
        logger.error("[ops_delay] 치명 실패: %s", type(exc).__name__)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "run_result.json").write_text(
            json.dumps({"status": "fatal", "error": type(exc).__name__, "version": VERSION}),
            encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main())
