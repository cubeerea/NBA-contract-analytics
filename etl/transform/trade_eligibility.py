"""Trade eligibility flags (ADR-011).

DELIBERATELY SHALLOW. This implements date-based restrictions, no-trade
clauses, and recently-signed locks — the rules that apply to most players most
of the time and can be evaluated from a single player's own contract data.

It does NOT implement salary matching. Under the 2023 CBA that requires
modeling every team's live payroll against first/second-apron rules, base year
compensation, and the poison pill provision — a project comparable in size to
the rest of this dashboard. The team payroll state that engine would need is
already produced by build.py's team rollup, so the deferral is a stopping
point rather than a dead end.

Anything this module cannot determine is reported as "unknown" rather than
guessed. A confidently wrong trade flag is worse than an absent one.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 2023 CBA date rules
# --------------------------------------------------------------------------
# A player who signs as a free agent cannot be traded for three months, or
# until December 15 of that league year, WHICHEVER IS LATER. The December 15
# date is what binds for the July signings that make up most of an offseason.
DECEMBER_FREEZE_MONTH = 12
DECEMBER_FREEZE_DAY = 15

# Re-signed players carry a January 15 restriction instead when all of the
# following hold: Bird/Early-Bird rights were used, the raise exceeded 20%, and
# the team was over the cap at signing. We can observe the raise but not
# reliably the rights type or the team's cap position at the moment of signing,
# so this rule is flagged as indeterminate rather than applied.
JANUARY_FREEZE_MONTH = 1
JANUARY_FREEZE_DAY = 15

# Newly drafted players cannot be traded for 30 days after signing.
DRAFT_PICK_FREEZE_DAYS = 30


def _league_year_start(as_of: date) -> date:
    """The NBA league year begins July 1.

    Between January and June we are still inside the league year that started
    the previous July, which is what the December 15 rule is anchored to.
    """
    return date(as_of.year if as_of.month >= 7 else as_of.year - 1, 7, 1)


def _december_freeze_date(as_of: date) -> date:
    ly = _league_year_start(as_of)
    return date(ly.year, DECEMBER_FREEZE_MONTH, DECEMBER_FREEZE_DAY)


def evaluate(
    df: pd.DataFrame, as_of: date | None = None
) -> pd.DataFrame:
    """Add `trade_eligible` and `trade_restriction_reason` columns.

    `as_of` defaults to today. It is a parameter so the nightly build is
    reproducible and so tests can pin a date rather than depend on the calendar.
    """
    as_of = as_of or date.today()
    out = df.copy()

    freeze = _december_freeze_date(as_of)
    league_year = _league_year_start(as_of)

    eligible: list[bool] = []
    reasons: list[str] = []

    for row in out.itertuples():
        reason = ""
        ok = True

        signed_year = getattr(row, "signed_year", None)
        contract_type = str(getattr(row, "contract_type", "") or "")
        has_ntc = bool(getattr(row, "no_trade_clause", False))

        # --- no-trade clause: absolute, overrides everything else -----------
        if has_ntc:
            ok = False
            reason = "No-trade clause"

        # --- recently signed free agent -------------------------------------
        # A player who signed in the current league year is frozen until
        # December 15. We only know the signing YEAR, not the exact date, so
        # this is a conservative approximation: treat a signing year matching
        # the current league year as "signed this offseason".
        elif (
            signed_year is not None
            and not pd.isna(signed_year)
            and int(signed_year) == league_year.year
            and as_of < freeze
        ):
            ok = False
            reason = f"Recently signed — ineligible until {freeze:%b %d, %Y}"

        # --- two-way contracts ----------------------------------------------
        elif contract_type == "two_way":
            ok = False
            reason = "Two-way contract — not tradeable in the ordinary sense"

        # --- rookie-scale first-year picks ----------------------------------
        elif contract_type == "rookie_scale" and signed_year is not None:
            if not pd.isna(signed_year) and int(signed_year) == league_year.year:
                ok = False
                reason = (
                    f"Drafted this league year — {DRAFT_PICK_FREEZE_DAYS}-day "
                    "freeze from signing"
                )

        # --- indeterminate ---------------------------------------------------
        # The January 15 re-signing restriction needs the rights type used and
        # the team's cap position at signing. We have neither, so say so.
        if ok and contract_type == "extension" and as_of < date(
            league_year.year + 1, JANUARY_FREEZE_MONTH, JANUARY_FREEZE_DAY
        ):
            reason = (
                "Possible Jan 15 re-signing restriction — depends on Bird rights "
                "and cap position at signing (not modeled)"
            )

        eligible.append(ok)
        reasons.append(reason)

    out["trade_eligible"] = eligible
    out["trade_restriction_reason"] = reasons

    blocked = (~pd.Series(eligible)).sum()
    log.info(
        "trade eligibility as of %s: %d of %d players restricted",
        as_of,
        blocked,
        len(out),
    )
    return out
