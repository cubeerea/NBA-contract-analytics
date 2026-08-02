"""Three wins-above-replacement estimates, calibrated to a common league total.

Win Shares, VORP, and DARKO DPM are expressed in incompatible units. WS sums to
*team wins* (a replacement-level roster still wins ~8 games per 82, so raw WS is
not "above replacement" at all). VORP is points above a -2.0 BPM replacement per
100 team possessions. DPM is points per 100 possessions with no volume term and
no fixed replacement baseline. Averaging them as-is is the single most common
error in public contract-value analysis: the blend silently inherits whichever
metric happens to have the largest league total, and the resulting dollars-per-win
is off by tens of percent.

So each estimate is converted to wins above replacement and then *calibrated* —
its free constant is solved for so that its league total equals VORP's. VORP is
the anchor because its conversion (x2.7) and its replacement baseline (-2.0 BPM)
are both documented by Basketball-Reference and neither is ours to invent.

Calibration is a genuine solve, not a fudge factor: both estimates are linear in
their unknown constant, so the constant that equates league totals is closed-form.
See `fit_replacement_ws48` and `fit_replacement_dpm` for the derivations.

Consumes STATS_SCHEMA columns (vorp, ws, minutes) plus a `darko_dpm` column
joined in from DARKO_SCHEMA. Pure functions: no I/O, no network.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .. import config

log = logging.getLogger(__name__)

# Possessions per 48 minutes of team play. The NBA has sat between roughly 97
# and 101 for the last several seasons; 99.0 is the midpoint and is stable
# enough that a per-season pace table would be false precision.
#
# Note this constant is only weakly load-bearing: a biased pace assumption is
# absorbed almost entirely by the fitted replacement DPM (both scale the same
# sum), so it shifts the DARKO baseline rather than the league total. It does
# affect the *spread* between players, which is why it is stated rather than
# buried. Belongs in config.py once someone wants to make it a UI toggle.
LEAGUE_PACE = 99.0

WAR_ESTIMATE_COLUMNS = ["war_vorp", "war_ws", "war_darko"]

# Column names each estimate reports itself as in `war_sources`.
_SOURCE_LABELS = {"war_vorp": "vorp", "war_ws": "ws", "war_darko": "darko"}


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    """Column as float with non-numeric coerced to NaN; all-NaN if absent."""
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").astype("float64")


# --------------------------------------------------------------------------
# 1. VORP — the anchor
# --------------------------------------------------------------------------


def war_from_vorp(df: pd.DataFrame) -> pd.Series:
    """VORP -> wins. Already replacement-adjusted, so no baseline subtraction.

    BBRef documents VORP as points above replacement per 100 team possessions
    over a full season, converted to wins by multiplying by 2.7.
    """
    return _numeric(df, "vorp") * config.VORP_TO_WINS


# --------------------------------------------------------------------------
# 2. Win Shares — solve for the replacement WS/48
# --------------------------------------------------------------------------


def fit_replacement_ws48(
    ws: pd.Series, minutes: pd.Series, anchor_total: float
) -> float:
    """Solve for the WS/48 baseline that makes the WS league total match VORP's.

    war_ws_i = ws_i - r * minutes_i / 48

    Summing over the league and setting equal to the anchor:

        sum(ws) - r * sum(minutes) / 48 = anchor_total
        r = 48 * (sum(ws) - anchor_total) / sum(minutes)

    Linear in r, hence closed-form. Expect r around 0.02: the league plays
    ~590k minutes and carries ~246 wins of replacement-level baseline.
    """
    total_minutes = float(minutes.sum())
    if total_minutes <= 0:
        raise ValueError("cannot fit replacement WS/48 with zero league minutes")
    return 48.0 * (float(ws.sum()) - anchor_total) / total_minutes


# --------------------------------------------------------------------------
# 3. DARKO DPM — solve for the replacement DPM
# --------------------------------------------------------------------------


def estimate_possessions(minutes: pd.Series, league_pace: float) -> pd.Series:
    """Possessions a player was on the floor for, from minutes and pace.

    A team uses `league_pace` possessions per 48 minutes, so a player on the
    floor for m minutes is present for m * pace / 48 of them. DPM is a net
    rating (offense and defense combined) so this counts team possessions once,
    not offensive and defensive possessions separately.
    """
    return minutes * league_pace / 48.0


def fit_replacement_dpm(
    dpm: pd.Series,
    possessions: pd.Series,
    anchor_total: float,
    points_per_win: float,
) -> float:
    """Solve for the DPM baseline that makes the DARKO league total match VORP's.

    war_darko_i = (dpm_i - r) * poss_i / 100 / points_per_win

    Summing and setting equal to the anchor:

        sum(dpm_i * poss_i) - r * sum(poss_i) = 100 * points_per_win * anchor
        r = (sum(dpm_i * poss_i) - 100 * points_per_win * anchor) / sum(poss_i)

    UNDERDETERMINED, RESOLVED DELIBERATELY: one equation, two free constants
    (r and points_per_win). We hold points_per_win fixed and solve for r,
    because ~32 points per win is an empirical property of NBA scoring margin
    that is not ours to retune, whereas "replacement level" is a modeling
    convention with no ground truth. Solving for points_per_win instead would
    make the *slope* of DARKO-derived wins absorb the error, distorting the gap
    between stars and rotation players; putting the error in r shifts everyone
    by a constant per-minute amount instead, which is the more honest failure.
    """
    total_poss = float(possessions.sum())
    if total_poss <= 0:
        raise ValueError("cannot fit replacement DPM with zero league possessions")
    weighted = float((dpm * possessions).sum())
    return (weighted - 100.0 * points_per_win * anchor_total) / total_poss


# --------------------------------------------------------------------------
# Blend
# --------------------------------------------------------------------------


def _blend(df: pd.DataFrame) -> pd.DataFrame:
    """Mean / spread / provenance across whichever estimates exist per player.

    Many players have no DARKO row (the sheet lags on two-way and late signings)
    and a handful will be missing a BBRef advanced line. The blend is a mean of
    what is present rather than an imputation, and `war_sources` records what
    that was so the player card can say "2 of 3 estimates".
    """
    estimates = df[WAR_ESTIMATE_COLUMNS]
    present = estimates.notna()

    df["war_blended"] = estimates.mean(axis=1, skipna=True)

    # Disagreement is signal, not noise — this is the uncertainty band on the
    # player card. Zero when only one estimate exists, which is not the same
    # claim as "the estimates agree"; war_n_sources disambiguates.
    df["war_spread"] = estimates.max(axis=1) - estimates.min(axis=1)

    df["war_n_sources"] = present.sum(axis=1).astype("int64")
    df["war_sources"] = [
        ",".join(_SOURCE_LABELS[c] for c in WAR_ESTIMATE_COLUMNS if row[c])
        for _, row in present.iterrows()
    ]
    return df


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def calibrate_war(
    df: pd.DataFrame,
    *,
    league_pace: float = LEAGUE_PACE,
    points_per_win: float = config.POINTS_PER_WIN,
) -> tuple[pd.DataFrame, dict]:
    """Add the three WAR estimates plus blend/spread, and return fitted constants.

    Adds: war_vorp, war_ws, war_darko, war_blended, war_spread (PLAYER_RECORD_SCHEMA)
    and war_sources / war_n_sources (provenance for the player card).

    The returned dict carries the fitted constants and the realised league
    totals; build.py logs them and the verification step asserts the totals
    agree within config.WAR_CALIBRATION_TOLERANCE.

    Each estimate is calibrated on the subset of players where it *and* the
    anchor both exist — calibrating DARKO against a league total that includes
    500 players it does not cover would push its baseline far too low.
    """
    out = df.copy()

    out["war_vorp"] = war_from_vorp(out)

    minutes = _numeric(out, "minutes")
    ws = _numeric(out, "ws")
    dpm = _numeric(out, "darko_dpm")
    anchor = out["war_vorp"]

    fitted: dict[str, float | int | None] = {
        "vorp_to_wins": config.VORP_TO_WINS,
        "league_pace": league_pace,
        "points_per_win": points_per_win,
    }

    # --- Win Shares ---------------------------------------------------------
    ws_defined = ws.notna() & minutes.notna() & (minutes > 0)
    ws_fit_rows = ws_defined & anchor.notna()
    if ws_fit_rows.any():
        replacement_ws48 = fit_replacement_ws48(
            ws[ws_fit_rows], minutes[ws_fit_rows], float(anchor[ws_fit_rows].sum())
        )
    else:
        # No overlap to calibrate against. BBRef's published replacement level
        # is the least-wrong constant available; say so loudly rather than
        # emitting a silently uncalibrated column.
        replacement_ws48 = 0.0
        log.error("no players with both WS and VORP; war_ws is NOT calibrated")

    out["war_ws"] = (ws - replacement_ws48 * minutes / 48.0).where(ws_defined)

    # --- DARKO --------------------------------------------------------------
    possessions = estimate_possessions(minutes, league_pace)
    darko_defined = dpm.notna() & minutes.notna() & (minutes > 0)
    darko_fit_rows = darko_defined & anchor.notna()
    if darko_fit_rows.any():
        replacement_dpm = fit_replacement_dpm(
            dpm[darko_fit_rows],
            possessions[darko_fit_rows],
            float(anchor[darko_fit_rows].sum()),
            points_per_win,
        )
    else:
        replacement_dpm = config.DARKO_REPLACEMENT_DPM
        log.warning(
            "no players with both DARKO and VORP; falling back to config "
            "DARKO_REPLACEMENT_DPM=%s (uncalibrated)",
            replacement_dpm,
        )

    out["war_darko"] = (
        (dpm - replacement_dpm) * possessions / 100.0 / points_per_win
    ).where(darko_defined)

    out = _blend(out)

    fitted.update(
        {
            "replacement_ws48": replacement_ws48,
            "replacement_dpm": replacement_dpm,
            "n_players": int(len(out)),
            "n_with_vorp": int(anchor.notna().sum()),
            "n_with_ws": int(ws_defined.sum()),
            "n_with_darko": int(darko_defined.sum()),
            "league_total_war_vorp": float(out["war_vorp"].sum()),
            "league_total_war_ws": float(out["war_ws"].sum()),
            "league_total_war_darko": float(out["war_darko"].sum()),
            # Anchor totals restricted to each estimate's own support — these
            # are the quantities the calibration actually equated, and the
            # right comparison for the tolerance check.
            "anchor_total_on_ws_support": float(anchor[ws_fit_rows].sum()),
            "anchor_total_on_darko_support": float(anchor[darko_fit_rows].sum()),
        }
    )

    log.info(
        "WAR calibration: replacement_ws48=%.5f replacement_dpm=%.3f "
        "(pace=%.1f, points_per_win=%.1f)",
        replacement_ws48,
        replacement_dpm,
        league_pace,
        points_per_win,
    )
    log.info(
        "WAR league totals: vorp=%.1f ws=%.1f darko=%.1f (darko covers %d/%d)",
        fitted["league_total_war_vorp"],
        fitted["league_total_war_ws"],
        fitted["league_total_war_darko"],
        fitted["n_with_darko"],
        fitted["n_players"],
    )

    return out, fitted
