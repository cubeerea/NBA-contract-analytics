"""Tests for trade eligibility flags.

Dates are pinned rather than derived from the calendar so these tests do not
start failing in December.
"""

from datetime import date

import pandas as pd
import pytest

from etl.transform import trade_eligibility as te


def _frame(**overrides):
    base = {
        "name": "Test Player",
        "signed_year": 2025,
        "contract_type": "free_agent",
        "no_trade_clause": False,
    }
    base.update(overrides)
    return pd.DataFrame([base])


class TestLeagueYear:
    def test_august_is_current_year(self):
        # August 2026 is inside the league year that began July 1 2026.
        assert te._league_year_start(date(2026, 8, 1)) == date(2026, 7, 1)

    def test_march_belongs_to_prior_league_year(self):
        # March 2026 is still inside the league year that began July 1 2025 —
        # this is the off-by-one that makes the Dec 15 rule easy to get wrong.
        assert te._league_year_start(date(2026, 3, 1)) == date(2025, 7, 1)

    def test_july_1_is_the_boundary(self):
        assert te._league_year_start(date(2026, 7, 1)) == date(2026, 7, 1)
        assert te._league_year_start(date(2026, 6, 30)) == date(2025, 7, 1)


class TestFreezeDate:
    def test_december_freeze_anchors_to_league_year(self):
        assert te._december_freeze_date(date(2026, 8, 1)) == date(2026, 12, 15)
        # From March 2026 the binding freeze was Dec 15 2025, already passed.
        assert te._december_freeze_date(date(2026, 3, 1)) == date(2025, 12, 15)


class TestRecentSigning:
    def test_offseason_signing_is_frozen(self):
        df = te.evaluate(_frame(signed_year=2026), as_of=date(2026, 8, 1))
        assert not df.loc[0, "trade_eligible"]
        assert "Dec 15, 2026" in df.loc[0, "trade_restriction_reason"]

    def test_eligible_after_december_15(self):
        df = te.evaluate(_frame(signed_year=2026), as_of=date(2026, 12, 16))
        assert df.loc[0, "trade_eligible"]

    def test_prior_year_signing_is_eligible(self):
        df = te.evaluate(_frame(signed_year=2024), as_of=date(2026, 8, 1))
        assert df.loc[0, "trade_eligible"]


class TestNoTradeClause:
    def test_ntc_blocks(self):
        df = te.evaluate(_frame(no_trade_clause=True), as_of=date(2026, 8, 1))
        assert not df.loc[0, "trade_eligible"]
        assert df.loc[0, "trade_restriction_reason"] == "No-trade clause"

    def test_ntc_takes_precedence_over_recent_signing(self):
        # Both rules apply; the NTC is the one reported because it is absolute.
        df = te.evaluate(
            _frame(signed_year=2026, no_trade_clause=True), as_of=date(2026, 8, 1)
        )
        assert df.loc[0, "trade_restriction_reason"] == "No-trade clause"


class TestContractTypes:
    def test_two_way_blocked(self):
        df = te.evaluate(
            _frame(contract_type="two_way", signed_year=2023), as_of=date(2026, 8, 1)
        )
        assert not df.loc[0, "trade_eligible"]

    def test_current_year_draftee_frozen(self):
        df = te.evaluate(
            _frame(contract_type="rookie_scale", signed_year=2026),
            as_of=date(2026, 8, 1),
        )
        assert not df.loc[0, "trade_eligible"]

    def test_second_year_rookie_scale_is_eligible(self):
        df = te.evaluate(
            _frame(contract_type="rookie_scale", signed_year=2024),
            as_of=date(2026, 8, 1),
        )
        assert df.loc[0, "trade_eligible"]


class TestIndeterminate:
    def test_extension_is_flagged_but_not_blocked(self):
        # We cannot evaluate the Jan 15 rule without Bird rights and cap
        # position. The player stays eligible, but the uncertainty is surfaced
        # rather than silently resolved in either direction.
        df = te.evaluate(
            _frame(contract_type="extension", signed_year=2024), as_of=date(2026, 8, 1)
        )
        assert df.loc[0, "trade_eligible"]
        assert "not modeled" in df.loc[0, "trade_restriction_reason"]


class TestRobustness:
    def test_missing_signed_year_does_not_crash(self):
        df = te.evaluate(_frame(signed_year=None), as_of=date(2026, 8, 1))
        assert len(df) == 1

    def test_nan_signed_year_does_not_crash(self):
        df = te.evaluate(_frame(signed_year=float("nan")), as_of=date(2026, 8, 1))
        assert len(df) == 1

    def test_empty_frame(self):
        empty = pd.DataFrame(
            columns=["name", "signed_year", "contract_type", "no_trade_clause"]
        )
        out = te.evaluate(empty, as_of=date(2026, 8, 1))
        assert len(out) == 0
        assert "trade_eligible" in out.columns
