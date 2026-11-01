"""운영 대시보드 수집기 진입점 — python -m ops_dashboard.main (DN2026_0087)."""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ops_dashboard import VERSION, cron_eval
from ops_dashboard.config import Settings, load_settings
from ops_dashboard.github_client import GitHubClient, GitHubError
from ops_dashboard.judge import (
    RED,
    Row,
    collect_failed_row,
    failed_run_id,
    judge_workflow,
)
from ops_dashboard.notion_client import NotionClient, NotionError
from ops_dashboard.registry import Registry, load_registry
from ops_dashboard.render import (
    Meta,
    notion_blocks,
    summary_line,
    tg_collect_text,
    tg_digest_text,
    tg_transition_text,
    to_json,
)
from ops_dashboard.state_store import load_state, save_state
from ops_dashboard.tg_client import TelegramClient, TelegramError

logger = logging.getLogger("ops_dashboard")

RUN_LOOKBACK = timedelta(days=8)
FALLBACK_INTERVAL_MIN = 3 * 24 * 60
DIGEST_HOUR_KST = 8
STATE_PATH = Path("state/ops_state.json")
OUT_DIR = Path("out")
NETWORK_LOGGERS = ("urllib3", "requests", "httpx", "httpcore", "hpack", "h2", "hyperframe")


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    for name in NETWORK_LOGGERS:  # STD-SEC-01
        logging.getLogger(name).setLevel(logging.WARNING)


def _repo_of(key: str) -> str:
    return "/".join(key.split("/")[:2])


def collect_repo(gh: GitHubClient, repo: str, registry: Registry, now: datetime,
                 grace_min: int, yml_cache: dict) -> list[Row]:
    workflows = gh.list_workflows(repo)
    shas = gh.workflow_file_shas(repo)
    crons_by_path: dict[str, list[str]] = {}
    yml_errors: set[str] = set()
    for path, sha in shas.items():
        key = f"{repo}/{path}"
        cached = yml_cache.get(key)
        if isinstance(cached, dict) and sha and cached.get("sha") == sha:
            crons, ok = list(cached.get("crons") or []), cached.get("ok", True) is not False
        else:
            crons, ok = cron_eval.parse_schedule_checked(gh.file_text(repo, path))
            yml_cache[key] = {"sha": sha, "crons": crons, "ok": ok}
        crons_by_path[path] = crons
        if not ok:
            yml_errors.add(path)

    runs, truncated = gh.list_runs_since(repo, now - RUN_LOOKBACK)
    by_wf: dict[int, list[dict]] = defaultdict(list)
    for run in runs:
        by_wf[run.get("workflow_id")].append(run)

    rows: list[Row] = []
    for wf in workflows:
        path = wf.get("path", "")
        override = registry.override_for(repo, path)
        crons = crons_by_path.get(path, [])
        wf_runs = by_wf.get(wf.get("id"), [])
        if wf.get("state") == "active" and not override.ignore and (crons or truncated):
            counted = [r for r in wf_runs if r.get("event") in override.count_events]
            sparse = bool(crons) and (
                cron_eval.min_interval_minutes(crons, now) >= FALLBACK_INTERVAL_MIN)
            if truncated or not counted or sparse:
                seen = {r.get("id") for r in wf_runs}
                extra = gh.list_workflow_runs(repo, wf["id"])
                wf_runs = wf_runs + [r for r in extra if r.get("id") not in seen]
        row = judge_workflow(repo=repo, workflow=wf, crons=crons, runs=wf_runs, now=now,
                             grace_min=grace_min, override=override,
                             yml_error=path in yml_errors)
        run_id = failed_run_id(row, wf_runs, override)
        if run_id is not None:
            try:
                row.failed_steps = gh.failed_steps(repo, run_id)
            except GitHubError as exc:
                logger.warning("[collect] %s 실패 step 조회 실패: %s", repo, exc)
        rows.append(row)
    return rows


def run(settings: Settings, *, gh: GitHubClient, notion: NotionClient | None,
        tg: TelegramClient | None, registry: Registry, now: datetime,
        state_path: Path = STATE_PATH, out_dir: Path = OUT_DIR) -> int:
    logger.info("[ops_dashboard] v%s 시작 | mode=%s | run_id=%s | sha=%s",
                VERSION, settings.mode, settings.run_id or "-", settings.sha or "-")
    state, had_state = load_state(state_path)
    errors: list[str] = []

    rows: list[Row] = []
    failed_repos: list[str] = []
    for repo in registry.repos:
        try:
            rows += collect_repo(gh, repo, registry, now, settings.grace_min, state["yml_cache"])
        except Exception as exc:  # noqa: BLE001 — 레포 1개 이상이 전체를 막지 않게 격리(R6-W4)
            detail = str(exc) if isinstance(exc, GitHubError) else type(exc).__name__
            logger.error("[collect] %s 수집 실패: %s", repo, detail)
            failed_repos.append(repo)
            rows.append(collect_failed_row(repo))
            errors.append(f"collect:{repo}")
    logger.info("[collect] 레포 %d개, 워크플로 %d행, GitHub 호출 %d회",
                len(registry.repos), len(rows), gh.calls)

    page_url = ""
    if settings.page_id:
        page_url = f"https://www.notion.so/{settings.page_id.replace('-', '')}"
    meta = Meta(version=VERSION, mode=settings.mode, run_id=settings.run_id, sha=settings.sha,
                run_url=settings.run_url, now=now, page_url=page_url, failed_repos=failed_repos)
    logger.info("[judge] %s", summary_line(rows))
    for r in rows:
        if r.level == RED:
            logger.info("[judge] 🔴 %s — %s", r.key, r.reason_text())

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dashboard.json").write_text(
        json.dumps(to_json(rows, meta), ensure_ascii=False, indent=1), encoding="utf-8")

    # 전환 계산 — 수집 실패 레포는 이전 상태 유지(거짓 진입·해소 방지)
    prev_levels: dict = state["levels"] if had_state else {}
    judged = [r for r in rows if r.repo not in failed_repos]
    entered = [r for r in judged if r.level == RED and prev_levels.get(r.key) != RED]
    # 해소 = 🔴에서 벗어남(🟡·⚪ 포함, R6-W3). 문구에 현재 레벨 표기
    recovered = [r for r in judged if r.level != RED and prev_levels.get(r.key) == RED]
    prev_failed = set(state.get("collect_failed") or [])
    cur_failed = set(failed_repos)
    new_levels = {k: v for k, v in prev_levels.items() if _repo_of(k) in cur_failed}
    new_levels.update({r.key: r.level for r in judged})

    if settings.mode in ("shadow", "live"):
        if not (notion and settings.page_id):
            logger.error("[notion] 설정 누락(NOTION_TOKEN 또는 OPS_DASHBOARD_PAGE_ID)")
            errors.append("notion:config")
        else:
            try:
                removed = notion.replace_page(settings.page_id, notion_blocks(rows, meta))
                logger.info("[notion] 페이지 갱신 완료(이전 블록 %d개 교체, 호출 %d회)",
                            removed, notion.calls)
            except NotionError as exc:
                logger.error("[notion] 갱신 실패: %s", exc)
                errors.append("notion:write")

    today_kst = now.astimezone(cron_eval.KST).date().isoformat()
    digest_due = now.astimezone(cron_eval.KST).hour >= DIGEST_HOUR_KST
    messages: list[tuple[str, str]] = []
    if not had_state:
        messages.append(("digest", tg_digest_text(rows, meta, title="첫 실행 요약")))
    else:
        if entered or recovered:
            messages.append(("transition", tg_transition_text(entered, recovered, meta)))
        if cur_failed != prev_failed:
            messages.append(("collect", tg_collect_text(sorted(cur_failed - prev_failed),
                                                        sorted(prev_failed - cur_failed), meta)))
        if digest_due and state.get("digest_date") != today_kst:
            messages.append(("digest", tg_digest_text(rows, meta)))

    digest_sent = False
    failed_kinds: set[str] = set()
    if settings.mode == "live":
        if not tg:
            logger.error("[tg] 설정 누락(TELEGRAM_BOT_TOKEN 또는 TELEGRAM_PAID_CHANNEL_ID)")
            errors.append("tg:config")
            failed_kinds.update(k for k, _ in messages)
        else:
            for kind, text in messages:
                try:
                    tg.send(text)
                    digest_sent = digest_sent or kind == "digest"
                except TelegramError as exc:
                    logger.error("[tg] %s 발송 실패: %s", kind, exc)
                    errors.append(f"tg:{kind}")
                    failed_kinds.add(kind)
            logger.info("[tg] 발송 %d/%d건", len(tg.sent), len(messages))
    else:
        logger.info("[tg] (%s) 발송 안 함 — 대상 %d건: %s", settings.mode, len(messages),
                    ",".join(k for k, _ in messages) or "-")

    if settings.mode in ("shadow", "live"):
        # 전환 알림 발송 실패 시 해당 키는 이전 레벨을 유지 → 다음 실행에서 다시 전환으로 잡혀 발송
        # (자동 재시도 아님, STD-PUB-03 · R6-W2)
        if "transition" in failed_kinds:
            for r in entered + recovered:
                if r.key in prev_levels:
                    new_levels[r.key] = prev_levels[r.key]
                else:
                    new_levels.pop(r.key, None)
        state["levels"] = new_levels
        state["collect_failed"] = sorted(prev_failed if "collect" in failed_kinds else cur_failed)
        if digest_sent and digest_due:
            state["digest_date"] = today_kst
        save_state(state_path, state)
        logger.info("[state] 저장 완료(%d행)", len(new_levels))

    status = "partial" if errors else "ok"
    (out_dir / "run_result.json").write_text(
        json.dumps({"status": status, "errors": errors, "version": VERSION}, ensure_ascii=False),
        encoding="utf-8")
    logger.info("[ops_dashboard] 종료 | status=%s | errors=%s", status, ",".join(errors) or "-")
    return 0 if status == "ok" else 1


def main() -> int:
    setup_logging()
    try:
        settings = load_settings()
        gh = GitHubClient(settings.gh_token)
        notion = NotionClient(settings.notion_token) if settings.notion_token else None
        tg = (TelegramClient(settings.tg_token, settings.tg_chat)
              if settings.tg_token and settings.tg_chat else None)
        return run(settings, gh=gh, notion=notion, tg=tg, registry=load_registry(),
                   now=datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 — 치명 실패는 결과 파일로 남기고 yml 실패 알림 step에 넘김
        logger.error("[ops_dashboard] 치명 실패: %s", type(exc).__name__)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "run_result.json").write_text(
            json.dumps({"status": "fatal", "error": type(exc).__name__, "version": VERSION}),
            encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main())
