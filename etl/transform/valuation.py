"""Expected salary, surplus, and the two lines drawn over the scatter (ADR-006).

TWO LINES, NOT ONE. They are easy to conflate and they answer different questions:

  MARKET line  cap_pct ~ composite_score, fit on market-priced deals only.
               "What do teams actually pay for this much production?"
               Denominator-independent, correctly so: neither axis involves
               dollars-per-win. Player residuals are measured against THIS line
               (`cap_pct_residual`) — that residual is what ranks best and worst
               contracts.

  MODEL line   expected_cap_pct ~ composite_score, where expected_cap_pct is
               war_blended x $/win / cap. "What does the model say a player at
               this production level should earn?" This is the line the UI's
               $/win toggle moves, and the two denominators genuinely differ:
               the replacement line is exactly $5.03M/$4.02M = 1.25x steeper,
               because it is the same production priced higher.

The gap between the two lines at a given composite score is the model's claim
about whether the league as a whole is over- or under-paying at that tier.


Two dollars-per-win denominators, both precomputed:

    naive       cap / 41    = $4.02M   assumes a replacement roster wins 0 games
    replacement cap / 32.8  = $5.03M   assumes replacement level is a .100 win pct

The naive figure inflates how overpaid everyone looks by about 20%. Rather than
picking one silently, both are emitted and the UI toggles between them — the
toggle is then a lookup rather than a rebuild (README "Precompute heavy,
recompute light").

Two things here are load-bearing and easy to get wrong:

1. Expected salary is FLOORED at the rookie minimum. Unfloored, a player with
   negative blended WAR gets a negative expected salary, and his surplus comes
   out more negative than his entire salary. "This contract is worth minus four
   million dollars" is not a coherent statement about a contract — no team can
   pay less than the minimum for a roster spot, so the minimum is the floor of
   what any signed player is worth.

2. The market line is fit on MARKET-PRICED CONTRACTS ONLY. Rookie-scale and
   minimum deals are CBA-suppressed: the player's price is set by a scale, not
   by a negotiation. Including them pulls the intercept down and the slope flat,
   which makes every veteran on a market deal look overpaid relative to a line
   that half its population was legally forbidden from bidding on. Rookie-scale
   players are still plotted; they just do not get a vote.

Pure functions: no I/O, no network.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .. import config

log = logging.getLogger(__name__)

DENOMINATORS = tuple(config.DOLLARS_PER_WIN)  # ("naive", "replacement")

MIN_REGRESSION_PLAYERS = 5

# Below this r2 a straight model line is a poor summary of the model's own
# pricing and the frontend should draw sampled points instead. Logged, not
# enforced — see fit_model_line.
MODEL_LINE_MIN_R2 = 0.80


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").astype("float64")


# --------------------------------------------------------------------------
# Expected salary / surplus
# --------------------------------------------------------------------------


def expected_salary(
    war: pd.Series,
    dollars_per_win: float,
    *,
    floor: float = config.ROOKIE_MINIMUM_SALARY,
) -> tuple[pd.Series, pd.Series]:
    """Return (floored expected salary, was-floored flag).

    The flag matters downstream: a floored player's surplus is a statement about
    the floor, not about the model's read of his production, and the player card
    should say so instead of implying precision the number does not have.
    """
    raw = war * dollars_per_win
    floored = raw.clip(lower=floor)
    was_floored = (raw < floor) & raw.notna()
    return floored, was_floored


# --------------------------------------------------------------------------
# Lines
# --------------------------------------------------------------------------

_NO_FIT = {"slope": None, "intercept": None, "r2": None, "n": 0}


def _ols(x: np.ndarray, y: np.ndarray) -> dict:
    """Simple least-squares fit reported in META_SCHEMA's shape."""
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "r2": 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else 0.0,
        "n": int(len(x)),
    }


def fit_market_line(
    df: pd.DataFrame,
    *,
    x_column: str = "composite_score",
    y_column: str = "cap_pct",
) -> tuple[dict, pd.Series]:
    """Fit y ~ x on market-priced contracts; return the fit and every player's residual.

    Residuals are computed for ALL players, including the suppressed contracts
    excluded from the fit — the rookie-scale outliers below the line are exactly
    what the "best contracts" leaderboard is looking for. Only the fit
    population is restricted.

    The residual, not the surplus, is what actually ranks contracts on the
    scatter: it is the vertical distance from what the market charges for that
    much production.
    """
    x_all = _numeric(df, x_column)
    y_all = _numeric(df, y_column)

    if "is_market_priced" in df.columns:
        market = df["is_market_priced"].fillna(False).astype(bool)
    else:
        log.warning("no is_market_priced column; fitting on ALL contracts")
        market = pd.Series(True, index=df.index)

    fit_rows = market & x_all.notna() & y_all.notna()
    n = int(fit_rows.sum())

    if n < MIN_REGRESSION_PLAYERS or x_all[fit_rows].nunique() < 2:
        log.error(
            "market line not fit: only %d usable market-priced contracts", n
        )
        return (
            {**_NO_FIT, "n": n},
            pd.Series(np.nan, index=df.index, dtype="float64"),
        )

    fit = _ols(x_all[fit_rows].to_numpy(), y_all[fit_rows].to_numpy())
    slope, intercept = fit["slope"], fit["intercept"]
    log.info(
        "market line %s~%s: slope=%.6f intercept=%.5f r2=%.3f n=%d "
        "(%d contracts excluded as CBA-suppressed)",
        y_column,
        x_column,
        fit["slope"],
        fit["intercept"],
        fit["r2"],
        n,
        int((~market).sum()),
    )

    residuals = y_all - (slope * x_all + intercept)
    return fit, residuals


def fit_model_line(
    df: pd.DataFrame,
    dollars_per_win: float,
    *,
    x_column: str = "composite_score",
) -> dict:
    """Fit the model's own price line: expected_cap_pct ~ composite_score.

        expected_cap_pct = war_blended * dollars_per_win / SALARY_CAP

    Fit on EVERY scored player, not just market-priced ones. This line is a
    statement about the model, not about the market, so excluding rookie-scale
    players would be excluding the model's own opinion of them.

    UNFLOORED ON PURPOSE. The rookie-minimum floor is a per-player clamp applied
    to the emitted expected_salary columns; folding it into the line would put a
    flat segment at the bottom and make the fitted slope a function of how many
    negative-WAR players happened to qualify. The consequence to be aware of
    downstream: below roughly composite 15 the drawn line sits under what the
    model actually pays, because those players are floored.

    STRAIGHT LINE, WITH A CAVEAT. war_blended and composite_score are
    monotonically related but not linearly so — composite is a percentile blend,
    which compresses the tails, so the true relationship curves upward at the
    top. A straight fit is emitted because it is what the frontend can draw in
    one call and because it stays a fair summary in the dense middle of the
    distribution; r2 is reported so the caller can see when it is not. Below
    MODEL_LINE_MIN_R2 this logs a warning recommending sampled points instead.
    """
    x_all = _numeric(df, x_column)
    y_all = _numeric(df, "war_blended") * dollars_per_win / config.SALARY_CAP

    fit_rows = x_all.notna() & y_all.notna()
    n = int(fit_rows.sum())

    if n < MIN_REGRESSION_PLAYERS or x_all[fit_rows].nunique() < 2:
        log.error("model line not fit: only %d usable players", n)
        return {**_NO_FIT, "n": n}

    fit = _ols(x_all[fit_rows].to_numpy(), y_all[fit_rows].to_numpy())
    if fit["r2"] < MODEL_LINE_MIN_R2:
        log.warning(
            "model line r2=%.3f is below %.2f; a straight line is a poor "
            "summary of the model's pricing — consider sampled points",
            fit["r2"],
            MODEL_LINE_MIN_R2,
        )
    return fit


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def value_players(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Add salary/cap_pct/expected/surplus columns and fit both lines.

    Adds per PLAYER_RECORD_SCHEMA: salary, cap_pct, expected_salary_{naive,
    replacement}, surplus_{naive,replacement}, expected_salary_floored_{naive,
    replacement}, and cap_pct_residual — the residual against the MARKET line,
    which is the column the best/worst-contract leaderboards sort on.

    Returns (frame, meta) with both lines keyed by denominator per META_SCHEMA:

      meta["regression"]  MARKET line. The two entries are identical by
                          construction: cap_pct ~ composite_score involves no
                          dollars-per-win term, so the denominator cannot move
                          it. Both keys are emitted because META_SCHEMA
                          specifies them and the frontend reads whichever the
                          toggle is on; `regression_denominator_dependent:
                          False` records that this is intentional rather than a
                          copy-paste bug.

      meta["model_line"]  MODEL line. The two entries DO differ, by exactly the
                          ratio of the denominators — this is the line the
                          toggle moves.
    """
    out = df.copy()

    if "salary" not in out.columns and "salary_2026_27" in out.columns:
        out["salary"] = _numeric(out, "salary_2026_27")
    salary = _numeric(out, "salary")
    out["salary"] = salary

    out["cap_pct"] = salary / config.SALARY_CAP

    war = _numeric(out, "war_blended")
    model_lines: dict[str, dict] = {}
    for name in DENOMINATORS:
        dpw = config.DOLLARS_PER_WIN[name]
        expected, floored = expected_salary(war, dpw)
        out[f"expected_salary_{name}"] = expected
        out[f"expected_salary_floored_{name}"] = floored
        out[f"surplus_{name}"] = expected - salary
        log.info(
            "%s ($%.2fM/win): %d of %d players floored at the minimum",
            name,
            dpw / 1e6,
            int(floored.sum()),
            int(war.notna().sum()),
        )

    fit, residuals = fit_market_line(out)
    out["cap_pct_residual"] = residuals

    for name in DENOMINATORS:
        model_lines[name] = fit_model_line(out, config.DOLLARS_PER_WIN[name])
        log.info(
            "model line (%s): slope=%s intercept=%s r2=%s n=%s",
            name,
            model_lines[name]["slope"],
            model_lines[name]["intercept"],
            model_lines[name]["r2"],
            model_lines[name]["n"],
        )

    meta = {
        "dollars_per_win": dict(config.DOLLARS_PER_WIN),
        "default_dollars_per_win": config.DEFAULT_DOLLARS_PER_WIN,
        "salary_cap": float(config.SALARY_CAP),
        "expected_salary_floor": float(config.ROOKIE_MINIMUM_SALARY),
        "regression": {name: dict(fit) for name in DENOMINATORS},
        "regression_denominator_dependent": False,
        "model_line": model_lines,
        "model_line_floored": False,
    }
    return out, meta
