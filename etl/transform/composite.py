"""Composite Production Score — the 0-100 X-axis (ADR-005).

Five components, each percentile-ranked league-wide and then weighted per
config.COMPOSITE_WEIGHTS.

Percentiles, not z-scores. These distributions are right-skewed and heavy-tailed:
Jokic's WAR is roughly six standard deviations of the rotation-player
distribution above its mean, so z-scoring hands him a score that pins everyone
from the 20th to the 80th percentile into a narrow, visually indistinguishable
clump. The chart's whole job is to separate the middle of the league, which is
where nearly every contract decision actually lives.

The TS%-vs-usage residual is the one non-obvious component. ADR-012 declined a
separate usage-vs-efficiency view, so that relationship has to live somewhere;
regressing TS% on USG% league-wide and taking each player's residual collapses a
two-variable tradeoff into one scalar that means "efficient *for that volume*".
A raw TS% column would rank low-usage rim-runners above primary creators; a raw
USG% column would reward empty volume. The residual does neither.

Pure functions: no I/O, no network.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .. import config

log = logging.getLogger(__name__)

# Weight key -> source column. Weights are named for the concept; the frame uses
# BBRef's column names.
COMPONENT_COLUMNS = {
    "war_blended": "war_blended",
    "darko_dpm": "darko_dpm",
    "ws48": "ws_per_48",
    "ts_residual": "ts_residual",
    "availability": "availability",
}

# Per-game minutes above which extra minutes stop counting as availability.
# 36 mpg is a full starter's load; a 38-minute-per-game season is a statement
# about role, not durability, and the volume it represents is already priced
# into war_blended.
FULL_TIME_MINUTES_PER_GAME = 36.0


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").astype("float64")


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


def fit_ts_residual(df: pd.DataFrame) -> tuple[pd.Series, dict]:
    """Fit TS% ~ USG% league-wide; return each player's residual and the fit.

    Ordinary least squares on a single predictor. The league-wide slope is
    normally slightly negative — the efficiency cost of volume — so the residual
    credits a high-usage player for beating the *expectation at their usage*
    rather than penalising them for not shooting like a play-finisher.

    Degenerate inputs (fewer than three usable players, or no variance in usage)
    yield all-zero residuals: a flat component contributes an identical
    percentile to everyone, which is the correct behaviour for "we cannot
    measure this today".
    """
    usg = _numeric(df, "usg_pct")
    ts = _numeric(df, "ts_pct")
    usable = usg.notna() & ts.notna()

    if usable.sum() < 3 or usg[usable].nunique() < 2:
        log.warning(
            "TS%%~USG%% fit skipped (n=%d usable); ts_residual set to 0",
            int(usable.sum()),
        )
        return pd.Series(0.0, index=df.index, dtype="float64"), {
            "slope": None,
            "intercept": None,
            "r2": None,
            "n": int(usable.sum()),
        }

    x = usg[usable].to_numpy()
    y = ts[usable].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)

    predicted = slope * usg + intercept
    residual = ts - predicted

    resid_fit = y - (slope * x + intercept)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid_fit**2).sum()) / ss_tot if ss_tot > 0 else 0.0

    fit = {
        "slope": float(slope),
        "intercept": float(intercept),
        "r2": float(r2),
        "n": int(usable.sum()),
    }
    log.info(
        "TS%%~USG%% fit: slope=%.5f intercept=%.4f r2=%.3f n=%d",
        fit["slope"],
        fit["intercept"],
        fit["r2"],
        fit["n"],
    )
    return residual, fit


def compute_availability(df: pd.DataFrame) -> pd.Series:
    """Durability as the fraction of a full starter's season actually delivered.

        availability = min(minutes, games * 36) / (82 * 36)

    Equivalently: share of the 82 games played, scaled by minutes per game but
    capped at 36 mpg. Both halves of the README's "GP x MP" are present, and the
    cap stops a heavy-minutes star from earning durability credit twice — his
    volume is already the largest term in war_blended.

    Clipped to [0, 1]; a player who appears in more than 82 games (traded
    mid-season with a schedule quirk) is at full availability, not above it.
    """
    games = _numeric(df, "games")
    minutes = _numeric(df, "minutes")
    # np.minimum rather than DataFrame.min: a missing games or minutes value
    # must propagate to NaN so the component is renormalised away for that
    # player, not silently replaced by whichever half survived.
    effective = np.minimum(minutes, games * FULL_TIME_MINUTES_PER_GAME)
    full_season = config.GAMES_PER_SEASON * FULL_TIME_MINUTES_PER_GAME
    return (effective / full_season).clip(lower=0.0, upper=1.0)


# --------------------------------------------------------------------------
# Percentile blend
# --------------------------------------------------------------------------


def percentile_rank(series: pd.Series) -> pd.Series:
    """League-wide percentile in [0, 100]; NaN in, NaN out.

    Ties share the average rank, so a block of players on identical zero values
    all land at the same percentile rather than being ordered by row position.
    """
    return series.rank(pct=True, na_option="keep", method="average") * 100.0


def compute_composite(
    df: pd.DataFrame, *, weights: dict[str, float] | None = None
) -> tuple[pd.DataFrame, dict]:
    """Add `ts_residual`, `availability` and `composite_score`; return fit meta.

    Percentiles are taken over every row passed in, so the caller must apply the
    ADR-008 scope filter first — ranking a 40-minute call-up against the league
    would compress everyone else's percentiles toward the middle.

    RENORMALISATION. Weights are renormalised per player over the components
    that actually resolved. A league-wide DARKO outage therefore reweights the
    remaining four to sum to 1.0 instead of capping every score at 75, and an
    individual player missing only DARKO is scored on his other four components
    rather than being pushed a quarter of the way down the axis for a data gap.
    Both cases fall out of the same rule.
    """
    weights = dict(weights or config.COMPOSITE_WEIGHTS)
    out = df.copy()

    residual, ts_fit = fit_ts_residual(out)
    out["ts_residual"] = residual
    out["availability"] = compute_availability(out)

    percentiles = pd.DataFrame(index=out.index)
    dropped: list[str] = []
    for key, column in COMPONENT_COLUMNS.items():
        if key not in weights:
            continue
        values = _numeric(out, column)
        if values.notna().sum() == 0:
            # Whole component missing league-wide (e.g. DARKO outage).
            dropped.append(key)
            log.warning(
                "composite component %r (%s) is empty league-wide; "
                "renormalising remaining weights",
                key,
                column,
            )
            continue
        percentiles[key] = percentile_rank(values)

    active = [k for k in weights if k in percentiles.columns]
    if not active:
        raise ValueError("no composite components available; cannot score players")

    weight_vector = pd.Series({k: weights[k] for k in active}, dtype="float64")
    present = percentiles[active].notna()

    # Per-row renormalisation: weighted mean over the components this player has.
    weight_sum = present.mul(weight_vector, axis=1).sum(axis=1)
    weighted = percentiles[active].fillna(0.0).mul(weight_vector, axis=1).sum(axis=1)
    out["composite_score"] = (weighted / weight_sum).where(weight_sum > 0)
    out["composite_n_components"] = present.sum(axis=1).astype("int64")

    effective = {k: float(weight_vector[k] / weight_vector.sum()) for k in active}
    meta = {
        "ts_fit": ts_fit,
        "weights_requested": {k: float(v) for k, v in weights.items()},
        "weights_effective": effective,
        "dropped_components": dropped,
        "availability_formula": (
            "min(minutes, games * 36) / (82 * 36), clipped to [0, 1]"
        ),
        "scored_players": int(out["composite_score"].notna().sum()),
    }

    if dropped:
        log.info("composite weights renormalised to %s", effective)

    return out, meta
