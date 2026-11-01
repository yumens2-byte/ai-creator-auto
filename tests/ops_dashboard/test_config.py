import logging

from ops_dashboard.config import DEFAULT_GRACE_MIN, load_settings, parse_int, parse_mode


def test_mode_parse(caplog):  # TS-OPS-01
    assert parse_mode("live") == "live"
    assert parse_mode(" SHADOW ") == "shadow"
    assert parse_mode("") == "dry_run"
    assert parse_mode(None) == "dry_run"
    assert parse_mode("-") == "dry_run"
    with caplog.at_level(logging.WARNING):
        assert parse_mode("prod") == "dry_run"
    assert any("강등" in r.message for r in caplog.records)


def test_int_parse():  # TS-OPS-02
    for raw in ("", "—", "none", "abc", None, "5", "99999"):
        assert parse_int(raw, DEFAULT_GRACE_MIN, 10, 1440) == DEFAULT_GRACE_MIN
    assert parse_int("90", DEFAULT_GRACE_MIN, 10, 1440) == 90


def test_load_settings_token_fallback():
    s = load_settings({"RUN_MODE": "shadow", "GITHUB_TOKEN_FALLBACK": "g", "OPS_GH_TOKEN": "-",
                       "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "9",
                       "GITHUB_SHA": "abcdef123456"})
    assert s.mode == "shadow"
    assert s.gh_token == "g"
    assert s.sha == "abcdef1"
    assert s.run_url == "https://github.com/o/r/actions/runs/9"
    s2 = load_settings({"OPS_GH_TOKEN": "p", "GITHUB_TOKEN_FALLBACK": "g"})
    assert s2.gh_token == "p"
    assert s2.mode == "dry_run"
