import json

from ops_dashboard.judge import RED, Row
from ops_dashboard.notion_client import MAX_TOP_BLOCKS, MAX_TOTAL_BLOCKS, chunk_blocks, count_blocks
from ops_dashboard.render import (
    TG_TEXT_MAX,
    Meta,
    notion_blocks,
    tg_digest_text,
    tg_transition_text,
)
from tests.ops_dashboard.conftest import ts

META = Meta(version="0.0.0", mode="live", run_id="1", sha="abc", run_url="https://x/run",
            now=ts("2026-11-02T03:00:00"), page_url="https://www.notion.so/p")


def rows(n: int, level: str = RED, codes=("RUN_FAILED",)) -> list[Row]:
    out = []
    for i in range(n):
        r = Row(repo=f"o/repo{i % 7}", path=f".github/workflows/w{i}.yml", name="이름" * 30,
                state="active", crons=["0 0 * * *"], codes=list(codes), level=level,
                last_run={"url": f"https://github.com/o/r/actions/runs/{i}",
                          "created_at": "2026-11-02T00:05:00Z", "conclusion": "failure"})
        out.append(r)
    return out


def _all_texts(block):
    body = block[block["type"]]
    for rt in body.get("rich_text", []):
        yield rt["text"]["content"]
    for cells in body.get("cells", []):
        for rt in cells:
            yield rt["text"]["content"]
    for child in body.get("children", []) or []:
        yield from _all_texts(child)


def test_notion_blocks(monkeypatch):  # TS-OPS-15
    blocks = notion_blocks(rows(120), META)
    chunks = chunk_blocks(blocks)
    for chunk in chunks:
        assert len(chunk) <= MAX_TOP_BLOCKS
        assert sum(count_blocks(b) for b in chunk) <= MAX_TOTAL_BLOCKS
    texts = [t for b in blocks for t in _all_texts(b)]
    assert all(len(t) <= 2000 for t in texts)
    dumped = json.dumps(blocks, ensure_ascii=False)
    assert "token" not in dumped.lower() and "secret" not in dumped.lower()
    tables = [b for b in blocks if b["type"] == "table"]
    for t in tables:
        width = t["table"]["table_width"]
        assert len(t["table"]["children"]) <= 100
        assert all(len(r["table_row"]["cells"]) == width for r in t["table"]["children"])
    assert len(tables) >= 3  # 조치 필요 120행 → 90 + 30 으로 분할
    for b in blocks:  # 최상위 아래 중첩은 table → table_row 1단계만
        body = b[b["type"]]
        for child in body.get("children", []) or []:
            assert child["type"] == "table_row" and "children" not in child["table_row"]


def test_notion_blocks_empty_attention():
    blocks = notion_blocks(rows(3, level="green", codes=("OK",)), META)
    assert any(b["type"] == "paragraph" and "없음" in list(_all_texts(b))[0] for b in blocks)


def test_tg_text_limit():  # TS-OPS-16
    text = tg_transition_text(rows(300), [], META)
    assert len(text) <= TG_TEXT_MAX
    assert "외" in text.splitlines()[-1]
    digest = tg_digest_text(rows(300), META)
    assert len(digest) <= TG_TEXT_MAX
