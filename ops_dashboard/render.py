"""출력 생성 — dashboard.json · Notion 블록 · 텔레그램 문안. 키 값은 다루지 않는다."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from ops_dashboard.cron_eval import fmt_kst
from ops_dashboard.judge import GRAY, GREEN, LEVEL_ICON, LEVEL_ORDER, RED, YELLOW, Row, parse_ts

NOTION_TEXT_MAX = 2000
TG_TEXT_MAX = 3500  # 텔레그램 4096은 UTF-16 단위 — 이모지 여유
TABLE_ROWS_MAX = 90  # 표 children(행) 요청당 100개 제한 — 머리행 포함 여유


@dataclass
class Meta:
    version: str
    mode: str
    run_id: str
    sha: str
    run_url: str
    now: datetime
    page_url: str = ""
    failed_repos: list[str] = field(default_factory=list)


def counts(rows: list[Row]) -> Counter:
    return Counter(r.level for r in rows)


def overall_level(rows: list[Row]) -> str:
    present = [r.level for r in rows if r.level != GRAY]
    return max(present, key=LEVEL_ORDER.__getitem__) if present else GRAY


def summary_line(rows: list[Row]) -> str:
    c = counts(rows)
    return (f"{LEVEL_ICON[overall_level(rows)]} 전체 | 🟢{c[GREEN]} 🟡{c[YELLOW]} "
            f"🔴{c[RED]} ⚪{c[GRAY]}")


def _sort_key(row: Row) -> tuple:
    return (-LEVEL_ORDER[row.level], row.repo, row.path)


def attention_rows(rows: list[Row]) -> list[Row]:
    return sorted((r for r in rows if r.level in (RED, YELLOW)), key=_sort_key)


def _short_repo(repo: str) -> str:
    return repo.split("/", 1)[-1]


def _file(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _last_run_text(row: Row) -> str:
    run = row.last_run
    if not run:
        return "—"
    result = run.get("conclusion") or run.get("status")
    return f"{fmt_kst(parse_ts(run.get('created_at')))} {result}"


# ---------------- JSON ----------------

def to_json(rows: list[Row], meta: Meta) -> dict:
    return {
        "version": meta.version,
        "mode": meta.mode,
        "run_id": meta.run_id,
        "sha": meta.sha,
        "generated_at": meta.now.isoformat(),
        "summary": dict(counts(rows)),
        "overall": overall_level(rows),
        "failed_repos": meta.failed_repos,
        "rows": [
            {
                "key": r.key,
                "repo": r.repo,
                "path": r.path,
                "name": r.name,
                "state": r.state,
                "level": r.level,
                "codes": r.codes,
                "crons": r.crons,
                "next_fire": r.next_fire.isoformat() if r.next_fire else None,
                "last_run": r.last_run,
                "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
                "failed_steps": r.failed_steps,
                "known_issue": r.known_issue,
            }
            for r in sorted(rows, key=_sort_key)
        ],
    }


# ---------------- Notion ----------------

def _rt(text: str, url: str | None = None, bold: bool = False) -> dict:
    item: dict = {"type": "text", "text": {"content": (text or " ")[:NOTION_TEXT_MAX]}}
    if url:
        item["text"]["link"] = {"url": url}
    if bold:
        item["annotations"] = {"bold": True}
    return item


def _table(header: list[str], body: list[list[dict]]) -> dict:
    rows = [{"type": "table_row", "table_row": {"cells": [[_rt(h, bold=True)] for h in header]}}]
    rows += [{"type": "table_row", "table_row": {"cells": [[c] for c in cells]}} for cells in body]
    return {"type": "table", "table": {"table_width": len(header), "has_column_header": True,
                                       "has_row_header": False, "children": rows}}


def _heading(text: str, level: int = 2, children: list | None = None) -> dict:
    kind = f"heading_{level}"
    block: dict = {"type": kind, kind: {"rich_text": [_rt(text)]}}
    if children is not None:
        block[kind]["is_toggleable"] = True
        block[kind]["children"] = children
    return block


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": [_rt(text)]}}


def notion_blocks(rows: list[Row], meta: Meta) -> list[dict]:
    head = (f"{summary_line(rows)}\n갱신 {fmt_kst(meta.now)} KST · 수집기 v{meta.version}"
            f" · mode={meta.mode} · sha={meta.sha or '—'}")
    if meta.failed_repos:
        head += "\n수집 실패: " + ", ".join(_short_repo(r) for r in meta.failed_repos)
    callout_text = [_rt(head)]
    if meta.run_url:
        callout_text.append(_rt(" · 실행 로그", url=meta.run_url))
    color = {RED: "red_background", YELLOW: "yellow_background"}.get(
        overall_level(rows), "green_background")
    blocks: list[dict] = [{"type": "callout", "callout": {
        "rich_text": callout_text, "icon": {"type": "emoji", "emoji": "📊"}, "color": color}}]

    blocks.append(_heading("조치 필요"))
    attention = attention_rows(rows)
    if attention:
        body = []
        for r in attention:
            url = (r.last_run or {}).get("url")
            body.append([
                _rt(LEVEL_ICON[r.level]), _rt(_short_repo(r.repo)), _rt(r.name),
                _rt(r.reason_text()), _rt(_last_run_text(r), url=url),
                _rt(fmt_kst(r.last_success_at)),
            ])
        for start in range(0, len(body), TABLE_ROWS_MAX):
            blocks.append(_table(["상태", "레포", "워크플로", "사유", "최근 실행", "최근 성공"],
                                 body[start:start + TABLE_ROWS_MAX]))
    else:
        blocks.append(_paragraph("없음"))

    blocks.append(_heading("레포 요약"))
    repos = sorted({r.repo for r in rows})
    body = []
    for repo in repos:
        c = counts([r for r in rows if r.repo == repo])
        body.append([_rt(_short_repo(repo)), _rt(str(c[GREEN])), _rt(str(c[YELLOW])),
                     _rt(str(c[RED])), _rt(str(c[GRAY]))])
    blocks.append(_table(["레포", "🟢", "🟡", "🔴", "⚪"], body))

    blocks.append(_heading("레포별 상세"))
    for repo in repos:
        repo_rows = sorted((r for r in rows if r.repo == repo), key=_sort_key)
        detail = []
        for r in repo_rows:
            detail.append([
                _rt(LEVEL_ICON[r.level]), _rt(f"{r.name} ({_file(r.path)})"),
                _rt(r.reason_text()), _rt(", ".join(r.crons) or "—"),
                _rt(fmt_kst(r.next_fire)),
                _rt(_last_run_text(r), url=(r.last_run or {}).get("url")),
            ])
        c = counts(repo_rows)
        title = f"{_short_repo(repo)} — 🟢{c[GREEN]} 🟡{c[YELLOW]} 🔴{c[RED]} ⚪{c[GRAY]}"
        # 중첩은 table → row 1단계만(토글 heading 안 표는 요청당 중첩 한도 초과 위험)
        blocks.append(_heading(title, level=3))
        for start in range(0, len(detail), TABLE_ROWS_MAX):
            header = ["상태", "워크플로", "사유", "cron(UTC)", "다음 예정", "최근 실행"]
            blocks.append(_table(header, detail[start:start + TABLE_ROWS_MAX]))
    blocks.append(_paragraph("판정 기준: 상세설계 — 운영 대시보드 1차 §4 (DN2026_0087)"))
    return blocks


# ---------------- Telegram ----------------

def _clip(lines: list[str], limit: int = TG_TEXT_MAX) -> str:
    out: list[str] = []
    size = 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > limit - 20:
            out.append(f"… 외 {len(lines) - i}건")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _row_line(r: Row) -> str:
    url = (r.last_run or {}).get("url") or ""
    line = f"{LEVEL_ICON[r.level]} {_short_repo(r.repo)} · {r.name} — {r.reason_text()} {url}"
    return line.rstrip()


def tg_transition_text(entered: list[Row], recovered: list[Row], meta: Meta) -> str:
    lines = [f"[운영 대시보드] 🔴 장애 {len(entered)}건 · ✅ 해소 {len(recovered)}건"]
    lines += [_row_line(r) for r in entered]
    lines += [f"✅ {_short_repo(r.repo)} · {r.name} — 🔴→{LEVEL_ICON[r.level]} {r.reason_text()}"
              for r in recovered]
    if meta.page_url:
        lines.append(f"대시보드: {meta.page_url}")
    return _clip(lines)


def tg_collect_text(started: list[str], cleared: list[str], meta: Meta) -> str:
    lines = ["[운영 대시보드] 레포 수집 상태 변화"]
    lines += [f"⚠️ 수집 실패: {_short_repo(r)}" for r in started]
    lines += [f"✅ 수집 복구: {_short_repo(r)}" for r in cleared]
    if meta.run_url:
        lines.append(f"실행: {meta.run_url}")
    return _clip(lines)


def tg_digest_text(rows: list[Row], meta: Meta, title: str = "일일 요약") -> str:
    lines = [f"[운영 대시보드] {title} {fmt_kst(meta.now)} KST", summary_line(rows)]
    if meta.failed_repos:
        lines.append("수집 실패: " + ", ".join(_short_repo(r) for r in meta.failed_repos))
    lines += [_row_line(r) for r in attention_rows(rows)]
    if meta.page_url:
        lines.append(f"대시보드: {meta.page_url}")
    return _clip(lines)
