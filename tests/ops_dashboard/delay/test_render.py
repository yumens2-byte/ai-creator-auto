from datetime import timedelta

from ops_dashboard.delay.matcher import Match, WorkflowDelay
from ops_dashboard.delay.metrics import Thresholds, build_report
from ops_dashboard.delay.render import (
    Meta,
    fmt_min,
    heavy_drop_workflows,
    notion_blocks,
    tg_transition_text,
    to_json,
)
from ops_dashboard.render import NOTION_TEXT_MAX, TABLE_ROWS_MAX
from tests.ops_dashboard.delay.helpers import NOW

TH = Thresholds(60, 240, 5, 15)


def wd(i, judged=10, dropped=0, confidence="HIGH"):
    slot = NOW - timedelta(hours=5)
    return WorkflowDelay(repo="o/r", path=f".github/workflows/w{i}.yml", name=f"W{i}",
                         crons=["0 1 * * *"], interval_min=1440, max_slot_gap_min=1440,
                         confidence=confidence,
                         matches=[Match(slot, slot + timedelta(minutes=300), i)],
                         dropped=[NOW - timedelta(days=2)] * dropped, judged_slots=judged)


def meta():
    return Meta(version="9.9.9", mode="shadow", run_id="1", sha="abc1234",
                run_url="https://github.com/o/ops/actions/runs/1", now=NOW, page_url="")


def _texts(blocks):
    for b in blocks:
        body = b[b["type"]]
        for rt in body.get("rich_text", []):
            yield rt["text"]["content"]
        for row in body.get("children", []) or []:
            for cell in row["table_row"]["cells"]:
                for rt in cell:
                    yield rt["text"]["content"]


def test_notion_blocks_limits():  # TS-DLY-12
    rows = [wd(i) for i in range(120)]
    rep = build_report(rows, now=NOW, th=TH, repos=["o/r"])
    blocks = notion_blocks(rep, meta())
    tables = [b for b in blocks if b["type"] == "table"]
    assert all(len(t["table"]["children"]) <= TABLE_ROWS_MAX + 1 for t in tables)
    texts = list(_texts(blocks))
    assert all(len(t) <= NOTION_TEXT_MAX for t in texts)
    joined = "\n".join(texts)
    assert "ghs_" not in joined and "secret" not in joined


def test_fmt_min():
    assert (fmt_min(None), fmt_min(45), fmt_min(420)) == ("—", "45분", "7.0h")


def test_heavy_drop_list_includes_low():
    rows = [wd(1, judged=10, dropped=1), wd(2, judged=100, dropped=90, confidence="LOW"),
            wd(3, judged=0)]
    picked = heavy_drop_workflows(rows, 15)
    assert [(w.name, round(p)) for w, p in picked] == [("W2", 90)]


def test_json_and_tg():
    rep = build_report([wd(1), wd(2), wd(3)], now=NOW, th=TH, repos=["o/r"])
    data = to_json(rep, meta())
    assert data["accounts"][0]["level"] == "red"
    assert data["workflows"][0]["drop_pct"] == 0.0
    text = tg_transition_text([(rep.accounts[0], "yellow")], meta())
    assert text.startswith("[예약 지연 감지]") and "🟡→🔴" in text
