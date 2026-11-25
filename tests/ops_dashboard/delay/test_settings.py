import pytest

from ops_dashboard.delay.settings import load_delay_settings, load_targets


@pytest.mark.parametrize("raw", ["", "—", "-", "none", "abc", "0", "99999"])
def test_vars_defaults(raw):  # TS-DLY-11
    s = load_delay_settings({"OPS_DELAY_WARN_MIN": raw, "OPS_DELAY_CRIT_MIN": raw,
                             "OPS_DELAY_DROP_WARN_PCT": raw, "OPS_DELAY_PENDING_H": raw})
    assert (s.warn_min, s.crit_min, s.drop_warn_pct, s.drop_crit_pct, s.pending_h) == (
        60, 240, 5, 15, 24)


def test_vars_custom_and_inverted():
    s = load_delay_settings({"OPS_DELAY_WARN_MIN": "30", "OPS_DELAY_CRIT_MIN": "120",
                             "OPS_DELAY_PENDING_H": "12"})
    assert (s.warn_min, s.crit_min, s.pending_h) == (30, 120, 12)
    inv = load_delay_settings({"OPS_DELAY_WARN_MIN": "300", "OPS_DELAY_CRIT_MIN": "100"})
    assert (inv.warn_min, inv.crit_min) == (60, 240)


def test_mode_and_tokens():
    s = load_delay_settings({"RUN_MODE": "LIVE", "OPS_GH_TOKEN": "", "GITHUB_TOKEN_FALLBACK": "g",
                             "OPS_DELAY_PAGE_ID": "p", "GITHUB_REPOSITORY": "o/r",
                             "GITHUB_RUN_ID": "5", "GITHUB_SHA": "abcdef123"})
    assert (s.mode, s.gh_token, s.page_id, s.sha) == ("live", "g", "p", "abcdef1")
    assert s.run_url == "https://github.com/o/r/actions/runs/5"
    assert load_delay_settings({"RUN_MODE": "prod"}).mode == "dry_run"


def test_targets_file():
    repos = load_targets()
    assert "yumens2-byte/ai-creator-auto" in repos
    assert "yumens2-byte/page76-ops" not in repos  # Q-6 확인 전 제외(DN2026_0128)
    assert len(repos) == len(set(repos))
