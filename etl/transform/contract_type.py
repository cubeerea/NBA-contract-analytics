"""Classify contracts on two orthogonal axes: how it was acquired, what it pays.

The regression that defines "market price" is only meaningful if the contracts
feeding it were actually negotiated. Rookie-scale, minimum, and two-way deals
are set by a schedule in the CBA — the player had no ability to bid his price up
and the team had no ability to bid it down. Mixing them into the fit drags the
line down and makes every veteran on a real deal look overpaid.

TWO AXES, BECAUSE ONE ENUM COULD NOT HOLD BOTH FACTS:

  contract_type   HOW the deal was acquired. rookie_scale | free_agent |
                  extension | minimum | two_way | unknown. BBRef's payroll notes
                  say this outright ("Signed 4-yr rookie scale contract"), so
                  `contract_type_override` is authoritative and wins whenever it
                  is present. The heuristic here is only a fallback.

  salary_tier     WHAT it pays. designated_veteran | max | mle | minimum |
                  rookie_scale | standard | unknown. A pure function of cap share
                  and service time against the published CBA tables — never
                  overridden, because no source states it and it is fully
                  determined by figures we already have.

Jokic is on an extension AND a max. Collapsing that into one field meant
whichever source wrote last destroyed the other fact, and on live data BBRef's
"Extension" note swallowed every max deal in the league, leaving ADR-005's
"Max / Designated Veteran" colour category empty. salary_tier is the axis the
chart colours by.

RELIABILITY, worst to best. This is inference from a cap hit and an age, and it
will misfire:

  mle          UNRELIABLE. An MLE deal and a cap-space free-agent deal of the
               same size are indistinguishable from one number. Only close
               matches against the three MLE start values (and their standard 5%
               raises) are claimed, so this under-detects on purpose. Costs
               nothing but a colour: mle and standard are both market-priced.
  rookie_scale (tier axis) Depends on the experience estimate. A 23-year-old on
               a modest free-agent deal is swept in, and that one DOES cost
               something — it suppresses him out of the regression. The
               acquisition axis is the reliable read here, and BBRef supplies it
               directly.
  max          Good at the top, weaker in the middle. Max salaries are pegged to
               the cap AT SIGNING and the cap has risen ~6.7% year over year, so
               a max signed a few years ago reads well below its nominal tier —
               which is why SGA's 25%-tier max shows up at 24.7% of today's cap.
               Handled by testing against every tier at or below the player's
               ceiling rather than only his current one, with a tolerance sized
               for a few years of cap growth. A max signed five-plus years ago
               will still fall through to `standard`.
  minimum      Good. The minimum scale is a published table and cap hits land on
               it almost exactly.
  two_way      Good. Two-way money is roughly half the minimum and nothing else
               lives down there.

  extension    NEVER INFERRED, only taken from the override. An extension and a
               free-agent signing are identical in a cap-hit table. Harmless:
               both are market-priced.

Pure functions: no I/O, no network.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .. import config

log = logging.getLogger(__name__)

# Axis 1 — how the deal was acquired.
ACQUISITION_TYPES = {
    "rookie_scale",
    "free_agent",
    "extension",
    "minimum",
    "two_way",
}

# Axis 2 — what it pays.
SALARY_TIERS = {
    "designated_veteran",
    "max",
    "mle",
    "minimum",
    "rookie_scale",
    "standard",
}

UNKNOWN = "unknown"

# The minimum scale is pegged to the cap by CBA formula, so it is expressed here
# as a share of the cap rather than in dollars. This is not premature generality:
# config.ROOKIE_MINIMUM_SALARY is $1,272,870, which is the 2025-26 figure, and
# using it directly put the 10-year veteran minimum at $3,634,044 when the real
# 2026-27 number is $3,876,529 — enough to misclassify LeBron's veteran-minimum
# deal as a negotiated one. Deriving from the cap self-corrects when the cap
# moves and cannot go stale independently of it.
#
#   $1,272,870 / $154,647,000 (2025-26 cap) = 0.8231% of the cap
#   0.8231% x $164,961,000 (2026-27 cap)    = $1,357,800 base
#   x 2.855 (10-year multiplier)            = $3,876,400  <- matches live data
MIN_SALARY_BASE_SHARE = 0.008231
MIN_SALARY_BASE = MIN_SALARY_BASE_SHARE * config.SALARY_CAP

# Minimum salary scale as a multiple of the 0-year minimum. Taken from the
# published NBA minimum scale; the level moves with the cap each year but the
# shape of the curve is stable, so ratios travel better than dollar figures.
MIN_SCALE_MULTIPLIERS = {
    0: 1.000,
    1: 1.635,
    2: 1.833,
    3: 1.899,
    4: 1.964,
    5: 2.127,
    6: 2.290,
    7: 2.453,
    8: 2.467,
    9: 2.481,
    10: 2.855,  # 10+ years
}
# Tight, because the base is now correct. Cap hits land on the published scale
# almost exactly; the remaining slack is for partial guarantees and for the
# league reimbursing the portion of a veteran minimum above the two-year figure.
MIN_SALARY_TOLERANCE = 1.02

# Two-way contracts pay roughly half the 0-year minimum. Nothing else in the
# league lives in that band, so the test is a simple floor.
TWO_WAY_CEILING = 0.75 * MIN_SALARY_BASE

# Maximum salary as a share of the cap, by years of experience. A player can be
# paid at any tier at or below the one his service time entitles him to — his
# deal was signed under whatever tier applied THEN — so all three are candidates.
MAX_TIERS = ((7, 0.25), (10, 0.30), (99, 0.35))
MAX_TIER_SHARES = tuple(share for _, share in MAX_TIERS)

# Tolerance below the nominal tier. Wider than it looks like it should be, on
# purpose: max salaries are fixed at signing while the cap keeps rising (+6.7%
# this year), so a two- or three-year-old max reads several points light. 0.92
# covers roughly four years of that drift.
MAX_TOLERANCE = 0.92

# A deal at the 35% tier, or above the standard tier for that service time, is a
# designated (super)max — the 30% rookie-extension exception or the 35% veteran
# exception.
MAX_TIER_SHARES_TOP = 0.35
DESIGNATED_MARGIN = 0.04

# Mid-level exception start values as a share of the cap, plus the standard
# annual raise. Bands are tight because this is purely a labelling decision —
# mle and standard are both market-priced, so neither error moves the
# regression, and guessing costs player-card accuracy for nothing in return.
MLE_START_PCT = {"taxpayer": 0.0368, "room": 0.0568, "non_taxpayer": 0.0912}
MLE_RAISES = (1.00, 1.05, 1.10, 1.15)
MLE_TOLERANCE = 0.015

# Rookie-scale band: the #1 pick is ~9% of the cap in year one and ~10% on his
# fourth-year option; the #30 pick is ~2%.
ROOKIE_SCALE_MAX_PCT = 0.12
ROOKIE_SCALE_MAX_EXPERIENCE = 4
ROOKIE_SCALE_MAX_AGE = 25

# Typical age at NBA debut, used only when a real experience column is absent.
ASSUMED_DEBUT_AGE = 19


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").astype("float64")


def infer_experience(df: pd.DataFrame) -> pd.Series:
    """Years of NBA experience, from a real column if present else from age.

    The age fallback assumes a debut at 19, which is right for lottery picks and
    wrong for four-year college players and international signings by two to
    four years. That error propagates into both the rookie-scale test and the
    max ceiling, which is why those are listed as unreliable.
    """
    for column in ("experience", "years_experience", "seasons_played"):
        if column in df.columns:
            exp = _numeric(df, column)
            if exp.notna().any():
                return exp.clip(lower=0)
    return (_numeric(df, "age") - ASSUMED_DEBUT_AGE).clip(lower=0)


def minimum_salary_for(experience: float) -> float:
    """Scale minimum for a given years of service, in 2026-27 dollars."""
    if not np.isfinite(experience):
        experience = 0.0
    years = int(min(max(experience, 0), 10))
    return MIN_SALARY_BASE * MIN_SCALE_MULTIPLIERS[years]


def max_share_for(experience: float) -> float:
    """Highest maximum-salary tier this service time entitles a player to."""
    if not np.isfinite(experience):
        experience = 0.0
    for threshold, share in MAX_TIERS:
        if experience < threshold:
            return share
    return MAX_TIERS[-1][1]


def _minimum_threshold(experience: float) -> float:
    """Ceiling under which a salary counts as a minimum deal.

    Padded by a year of service because the experience estimate skews low for
    anyone who did not debut at 19. A false "minimum" only removes a cheap
    contract from the fit; a missed one adds a scale-set price to it.
    """
    padded = min(experience + 1, 10) if np.isfinite(experience) else 0.0
    return MIN_SALARY_TOLERANCE * minimum_salary_for(padded)


def _looks_like_mle(cap_share: float) -> bool:
    return any(
        abs(cap_share - start * raise_) <= MLE_TOLERANCE * start * raise_
        for start in MLE_START_PCT.values()
        for raise_ in MLE_RAISES
    )


def _looks_like_rookie_scale(cap_share: float, experience: float) -> bool:
    return (
        np.isfinite(experience)
        and experience <= ROOKIE_SCALE_MAX_EXPERIENCE
        and cap_share <= ROOKIE_SCALE_MAX_PCT
    )


# --------------------------------------------------------------------------
# Axis 2 — salary tier
# --------------------------------------------------------------------------


def salary_tier_for(salary: float, experience: float) -> str:
    """What this contract pays, against the published 2026-27 CBA figures.

    Deliberately takes no override and no acquisition hint: this is the axis the
    chart colours by, and it must mean exactly one thing — where the money sits
    relative to the CBA's tiers.
    """
    if not np.isfinite(salary) or salary <= 0:
        return UNKNOWN

    # A two-way salary is below the rookie minimum, so on the money axis it is a
    # minimum. `two_way` lives on the acquisition axis, where it belongs.
    if salary <= _minimum_threshold(experience):
        return "minimum"

    cap_share = salary / config.SALARY_CAP
    ceiling = max_share_for(experience)

    # Any tier at or below his ceiling is a candidate, so the test is against
    # the LOWEST tier — a max signed years ago under the 25% tier is still a max
    # today even though his service time now entitles him to 30% or 35%.
    if cap_share >= MAX_TOLERANCE * MAX_TIER_SHARES[0]:
        at_top_tier = cap_share >= MAX_TOLERANCE * MAX_TIER_SHARES_TOP
        above_own_tier = cap_share >= MAX_TOLERANCE * (ceiling + DESIGNATED_MARGIN)
        return "designated_veteran" if at_top_tier or above_own_tier else "max"

    if _looks_like_rookie_scale(cap_share, experience):
        return "rookie_scale"

    if _looks_like_mle(cap_share):
        return "mle"

    return "standard"


# --------------------------------------------------------------------------
# Axis 1 — acquisition type (fallback only; the override is authoritative)
# --------------------------------------------------------------------------


def _acquisition_type_for(salary: float, age: float, experience: float) -> str:
    """Best guess at how a deal was acquired, used only without an override.

    Never returns `extension` — see the module docstring. Everything not
    recognisably scale-set falls to free_agent, which is the right side of the
    market-priced line whether or not it was technically an extension.
    """
    if not np.isfinite(salary) or salary <= 0:
        return UNKNOWN
    if salary < TWO_WAY_CEILING:
        return "two_way"
    if salary <= _minimum_threshold(experience):
        return "minimum"

    cap_share = salary / config.SALARY_CAP
    age_ok = not np.isfinite(age) or age <= ROOKIE_SCALE_MAX_AGE
    if age_ok and _looks_like_rookie_scale(cap_share, experience):
        return "rookie_scale"

    return "free_agent"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def classify_contracts(
    df: pd.DataFrame, *, override_column: str = "contract_type_override"
) -> pd.DataFrame:
    """Add `contract_type`, `salary_tier`, and `is_market_priced`.

    `override_column` (BBRef payroll notes, allowed to be absent or partially
    populated) wins on the acquisition axis wherever it holds a recognised
    value. It does NOT touch salary_tier — that axis is defined as a pure
    function of the money, so an authoritative "Extension" note can no longer
    erase the fact that the extension is also a max.

    A contract is market-priced when its money sits in a negotiated tier AND its
    acquisition path was not scale-set. Both conditions read straight out of
    config, which stays the single place that decision lives. An unknown tier —
    a player with no usable salary — is never market-priced, because the
    positive-list test on the tier axis excludes it by construction.
    """
    out = df.copy()

    if "salary" in out.columns:
        salary = _numeric(out, "salary")
    else:
        salary = _numeric(out, "salary_2026_27")
    age = _numeric(out, "age")
    experience = infer_experience(out)

    out["salary_tier"] = pd.Series(
        [salary_tier_for(s, e) for s, e in zip(salary, experience, strict=True)],
        index=out.index,
        dtype="object",
    )

    inferred = pd.Series(
        [
            _acquisition_type_for(s, a, e)
            for s, a, e in zip(salary, age, experience, strict=True)
        ],
        index=out.index,
        dtype="object",
    )

    if override_column in out.columns:
        override = out[override_column].where(
            out[override_column].isin(ACQUISITION_TYPES)
        )
        n_overridden = int(override.notna().sum())
        contract_type = override.fillna(inferred)
        log.info(
            "contract types: %d of %d taken from %s, %d inferred",
            n_overridden,
            len(out),
            override_column,
            len(out) - n_overridden,
        )
    else:
        contract_type = inferred
        log.info("contract types: all %d inferred (no override column)", len(out))

    out["contract_type"] = contract_type
    out["is_market_priced"] = out["salary_tier"].isin(
        config.MARKET_PRICED_SALARY_TIERS
    ) & ~contract_type.isin(config.SUPPRESSED_CONTRACT_TYPES)

    log.info("contract_type counts: %s", contract_type.value_counts().to_dict())
    log.info(
        "salary_tier counts: %s (%d of %d market-priced)",
        out["salary_tier"].value_counts().to_dict(),
        int(out["is_market_priced"].sum()),
        len(out),
    )
    return out
