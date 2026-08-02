"""Tests for the modeling layer.

These are correctness tests, not smoke tests. The claims being defended:

  * VORP converts to wins by the documented constant and nothing else.
  * The other two WAR estimates are genuinely calibrated to VORP's league total
    — this is the assertion the credibility of the dollar model rests on.
  * Missing DARKO degrades correctly at both the player level (blend fewer
    estimates) and the league level (renormalise composite weights).
  * Negative production cannot produce a surplus more negative than the salary.
  * CBA-suppressed contracts do not get a vote in defining market price. The
    test constructs a frame where they would visibly move the line and asserts
    that they do not.

Synthetic frames are built to match STATS_SCHEMA / CONTRACTS_SCHEMA /
DARKO_SCHEMA so the transforms are exercised against the same contract the real
source adapters promise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etl import config, schema
from etl.transform import composite, contract_type, valuation, war

TEAM_MINUTES_PER_SLOT = 48 * config.GAMES_PER_SEASON  # 3936


# --------------------------------------------------------------------------
# Synthetic league
# --------------------------------------------------------------------------


def make_league(
    n: int = 400, *, seed: int = 7, darko_coverage: float = 1.0
) -> pd.DataFrame:
    """A league-shaped frame with internally consistent stats.

    VORP is derived from BPM by Basketball-Reference's actual definition and
    WS/48 from BPM with league-average calibration (BPM 0 -> WS/48 .100), so the
    relationship between the three metrics resembles the real one rather than
    being independent noise. That matters: calibration on independent noise
    would pass trivially.
    """
    rng = np.random.default_rng(seed)

    minutes = rng.uniform(500, 2900, n).round()
    bpm = rng.normal(-0.5, 3.0, n)
    vorp = (bpm - config.BPM_REPLACEMENT_LEVEL) * minutes / TEAM_MINUTES_PER_SLOT
    ws48 = 0.100 + 0.018 * bpm + rng.normal(0, 0.015, n)
    ws = ws48 * minutes / 48.0

    usg = np.clip(rng.normal(19.0, 5.0, n), 8.0, 38.0)
    # Real league-wide slope of TS% on USG% is mildly negative.
    ts = 0.575 - 0.0015 * usg + rng.normal(0, 0.03, n)

    dpm = 0.9 * bpm + rng.normal(0, 1.0, n)
    if darko_coverage < 1.0:
        missing = rng.random(n) > darko_coverage
        dpm = np.where(missing, np.nan, dpm)

    games = np.clip((minutes / rng.uniform(14, 34, n)).round(), 20, 82)

    return pd.DataFrame(
        {
            "bbref_slug": [f"player{i:04d}" for i in range(n)],
            "name": [f"Player {i}" for i in range(n)],
            "age": rng.integers(20, 38, n),
            "team": "BOS",
            "position": "SF",
            "games": games.astype(int),
            "games_started": 0,
            "minutes": minutes.astype(int),
            "ts_pct": ts,
            "usg_pct": usg,
            "ws": ws,
            "ws_per_48": ws48,
            "bpm": bpm,
            "vorp": vorp,
            "darko_dpm": dpm,
        }
    )


# --------------------------------------------------------------------------
# war.py
# --------------------------------------------------------------------------


def test_war_vorp_uses_the_documented_conversion():
    df = pd.DataFrame(
        {
            "vorp": [4.0, 0.0, -1.5],
            "ws": [10.0, 2.0, 0.5],
            "minutes": [2400, 1200, 600],
            "darko_dpm": [3.0, 0.0, -2.0],
        }
    )
    out, _ = war.calibrate_war(df)

    # VORP is already replacement-adjusted; no baseline is subtracted.
    assert out["war_vorp"].tolist() == pytest.approx([10.8, 0.0, -4.05])
    assert out.loc[0, "war_vorp"] == pytest.approx(4.0 * config.VORP_TO_WINS)


def test_calibration_equates_league_totals():
    """THE core assertion: all three estimates agree on the league total."""
    df = make_league()
    out, fitted = war.calibrate_war(df)

    anchor = out["war_vorp"].sum()
    assert anchor > 0

    for column in ("war_ws", "war_darko"):
        total = out[column].sum()
        relative_error = abs(total - anchor) / abs(anchor)
        assert relative_error < config.WAR_CALIBRATION_TOLERANCE, (
            f"{column} league total {total:.1f} vs anchor {anchor:.1f} "
            f"({relative_error:.1%} off)"
        )


def test_calibration_is_a_solve_not_an_approximation():
    """The closed-form fits should equate the totals to floating-point precision."""
    df = make_league(n=250, seed=11)
    out, fitted = war.calibrate_war(df)

    anchor = out["war_vorp"].sum()
    assert out["war_ws"].sum() == pytest.approx(anchor, rel=1e-9)
    assert out["war_darko"].sum() == pytest.approx(anchor, rel=1e-9)

    # A fitted replacement level, not a hardcoded one, but still physically
    # plausible: a positive per-minute baseline well under a starter's rate.
    assert 0.0 < fitted["replacement_ws48"] < 0.10
    assert -6.0 < fitted["replacement_dpm"] < 0.0


def test_uncalibrated_win_shares_would_have_failed():
    """Guard against someone deleting the calibration and calling raw WS 'WAR'."""
    df = make_league()
    out, _ = war.calibrate_war(df)

    anchor = out["war_vorp"].sum()
    raw_ws_total = df["ws"].sum()
    error = abs(raw_ws_total - anchor) / abs(anchor)
    assert error > config.WAR_CALIBRATION_TOLERANCE, (
        "raw WS happens to match the anchor on this fixture; the test no longer "
        "proves calibration is doing anything"
    )


def test_darko_calibrates_on_its_own_support_when_coverage_is_partial():
    df = make_league(n=400, seed=3, darko_coverage=0.6)
    out, fitted = war.calibrate_war(df)

    covered = out["war_darko"].notna()
    assert 0 < covered.sum() < len(out)

    # Calibrating against the full-league total would push the DARKO baseline
    # far too low; the fit is restricted to players DARKO actually covers.
    anchor_on_support = out.loc[covered, "war_vorp"].sum()
    assert out.loc[covered, "war_darko"].sum() == pytest.approx(
        anchor_on_support, rel=1e-9
    )
    assert fitted["anchor_total_on_darko_support"] == pytest.approx(
        anchor_on_support
    )


def test_missing_darko_blends_only_what_exists():
    df = pd.DataFrame(
        {
            "vorp": [3.0, 3.0],
            "ws": [8.0, 8.0],
            "minutes": [2000, 2000],
            "darko_dpm": [2.5, np.nan],
        }
    )
    out, _ = war.calibrate_war(df)

    with_darko = out.iloc[0]
    without_darko = out.iloc[1]

    assert without_darko["war_n_sources"] == 2
    assert without_darko["war_sources"] == "vorp,ws"
    assert np.isnan(without_darko["war_darko"])
    assert without_darko["war_blended"] == pytest.approx(
        np.mean([without_darko["war_vorp"], without_darko["war_ws"]])
    )
    assert without_darko["war_spread"] == pytest.approx(
        abs(without_darko["war_vorp"] - without_darko["war_ws"])
    )

    assert with_darko["war_n_sources"] == 3
    assert with_darko["war_sources"] == "vorp,ws,darko"


def test_war_spread_is_the_disagreement_between_estimates():
    df = make_league(n=120, seed=5)
    out, _ = war.calibrate_war(df)

    estimates = out[war.WAR_ESTIMATE_COLUMNS]
    assert (out["war_spread"] >= -1e-9).all()
    assert out["war_spread"].equals(
        estimates.max(axis=1) - estimates.min(axis=1)
    )
    # Blend must sit inside the band it reports.
    assert (out["war_blended"] >= estimates.min(axis=1) - 1e-9).all()
    assert (out["war_blended"] <= estimates.max(axis=1) + 1e-9).all()


# --------------------------------------------------------------------------
# composite.py
# --------------------------------------------------------------------------


def test_composite_score_is_bounded_0_100():
    df = make_league(n=300, seed=13, darko_coverage=0.8)
    df, _ = war.calibrate_war(df)
    out, meta = composite.compute_composite(df)

    scores = out["composite_score"].dropna()
    assert len(scores) == len(out)
    assert scores.min() >= 0.0
    assert scores.max() <= 100.0
    assert sum(meta["weights_effective"].values()) == pytest.approx(1.0)


def test_ts_residual_recovers_the_usage_efficiency_line():
    n = 200
    rng = np.random.default_rng(1)
    usg = rng.uniform(10, 35, n)
    ts = 0.60 - 0.002 * usg + rng.normal(0, 0.01, n)
    df = pd.DataFrame({"usg_pct": usg, "ts_pct": ts})

    residual, fit = composite.fit_ts_residual(df)

    assert fit["slope"] == pytest.approx(-0.002, abs=3e-4)
    assert fit["intercept"] == pytest.approx(0.60, abs=5e-3)
    assert residual.mean() == pytest.approx(0.0, abs=1e-9)

    # A high-usage player above the line beats a low-usage player above it by
    # less — which is the whole point of using the residual rather than raw TS%.
    high_usage_efficient = pd.DataFrame({"usg_pct": [34.0], "ts_pct": [0.58]})
    combined = pd.concat([df, high_usage_efficient], ignore_index=True)
    resid_combined, _ = composite.fit_ts_residual(combined)
    assert resid_combined.iloc[-1] > 0


def test_league_wide_darko_outage_renormalises_the_weights():
    """A DARKO outage must not systematically depress every score by 25%."""
    # Player 0 is constructed to top every surviving component, including the
    # TS-residual fit (checked in test_ts_residual_recovers_the_usage_efficiency_line
    # that the fit is a real OLS, so these residuals are not free parameters).
    df = pd.DataFrame(
        {
            "vorp": [6.0, 3.0, 1.5, 0.5],
            "ws": [12.0, 6.0, 3.0, 1.0],
            "ws_per_48": [0.220, 0.130, 0.080, 0.040],
            "minutes": [2600, 2200, 1500, 1200],
            "games": [78, 70, 55, 50],
            "usg_pct": [31.0, 24.0, 19.0, 14.0],
            "ts_pct": [0.660, 0.545, 0.530, 0.500],
            "darko_dpm": [np.nan, np.nan, np.nan, np.nan],
        }
    )
    df, _ = war.calibrate_war(df)
    out, meta = composite.compute_composite(df)

    assert meta["dropped_components"] == ["darko_dpm"]
    assert "darko_dpm" not in meta["weights_effective"]
    assert sum(meta["weights_effective"].values()) == pytest.approx(1.0)

    # Player 0 tops every surviving component, so he must score a full 100 —
    # not the 75 an un-renormalised blend would produce.
    assert out.loc[0, "composite_score"] == pytest.approx(100.0)


def test_single_player_missing_darko_is_scored_on_what_he_has():
    """One missing DARKO row must not push a player a quarter of the way down.

    The per-player renormalisation means his score is the weighted mean of the
    four components he does have, not that mean multiplied by 0.75.
    """
    df = pd.DataFrame(
        {
            "vorp": [6.0, 3.0, 1.5, 0.5],
            "ws": [12.0, 6.0, 3.0, 1.0],
            "ws_per_48": [0.220, 0.130, 0.080, 0.040],
            "minutes": [2600, 2200, 1500, 1200],
            "games": [78, 70, 55, 50],
            "usg_pct": [31.0, 24.0, 19.0, 14.0],
            "ts_pct": [0.660, 0.545, 0.530, 0.500],
            "darko_dpm": [4.0, np.nan, 0.0, -3.0],
        }
    )
    df, _ = war.calibrate_war(df)
    out, meta = composite.compute_composite(df)

    assert out["composite_n_components"].tolist() == [5, 4, 5, 5]
    assert meta["dropped_components"] == []  # league-wide the component survives

    weights = config.COMPOSITE_WEIGHTS
    survivors = {k: v for k, v in weights.items() if k != "darko_dpm"}
    own = {
        "war_blended": composite.percentile_rank(out["war_blended"]).iloc[1],
        "ws48": composite.percentile_rank(out["ws_per_48"]).iloc[1],
        "ts_residual": composite.percentile_rank(out["ts_residual"]).iloc[1],
        "availability": composite.percentile_rank(out["availability"]).iloc[1],
    }
    expected = sum(survivors[k] * own[k] for k in survivors) / sum(survivors.values())

    assert out.loc[1, "composite_score"] == pytest.approx(expected)
    # The bug this guards against: scoring him against the full weight vector.
    depressed = sum(survivors[k] * own[k] for k in survivors)
    assert out.loc[1, "composite_score"] > depressed


def test_availability_rewards_playing_time_and_is_capped():
    df = pd.DataFrame(
        {
            "games": [82, 41, 82, 82, np.nan],
            "minutes": [82 * 36, 41 * 36, 82 * 20, 82 * 44, 2000],
        }
    )
    avail = composite.compute_availability(df)

    assert avail.iloc[0] == pytest.approx(1.0)
    assert avail.iloc[1] == pytest.approx(0.5)
    assert avail.iloc[2] == pytest.approx(20 / 36)
    # 44 mpg is a statement about role, not durability.
    assert avail.iloc[3] == pytest.approx(1.0)
    # Missing input propagates rather than being half-computed.
    assert np.isnan(avail.iloc[4])


# --------------------------------------------------------------------------
# valuation.py
# --------------------------------------------------------------------------


def test_both_denominators_are_emitted_and_differ():
    df = pd.DataFrame(
        {
            "war_blended": [5.0],
            "salary": [20_000_000.0],
            "composite_score": [70.0],
            "is_market_priced": [True],
        }
    )
    out, meta = valuation.value_players(df)

    assert out.loc[0, "expected_salary_naive"] == pytest.approx(
        5.0 * config.DOLLARS_PER_WIN["naive"]
    )
    assert out.loc[0, "expected_salary_replacement"] == pytest.approx(
        5.0 * config.DOLLARS_PER_WIN["replacement"]
    )
    assert (
        out.loc[0, "surplus_replacement"] > out.loc[0, "surplus_naive"]
    ), "the replacement denominator must make players look less overpaid"
    assert out.loc[0, "cap_pct"] == pytest.approx(20_000_000 / config.SALARY_CAP)
    assert set(meta["regression"]) == {"naive", "replacement"}
    assert set(meta["model_line"]) == {"naive", "replacement"}


def test_negative_production_is_floored_not_negative():
    salary = 18_000_000.0
    df = pd.DataFrame(
        {
            "war_blended": [-2.4, 0.0, 4.0],
            "salary": [salary, salary, salary],
            "composite_score": [5.0, 20.0, 80.0],
            "is_market_priced": [True, True, True],
        }
    )
    out, _ = valuation.value_players(df)

    for denominator in ("naive", "replacement"):
        expected = out[f"expected_salary_{denominator}"]
        surplus = out[f"surplus_{denominator}"]

        assert (expected >= config.ROOKIE_MINIMUM_SALARY).all()
        assert expected.iloc[0] == pytest.approx(config.ROOKIE_MINIMUM_SALARY)
        assert out.loc[0, f"expected_salary_floored_{denominator}"]
        assert not out.loc[2, f"expected_salary_floored_{denominator}"]

        # The coherence claim: no contract is worth less than nothing.
        assert surplus.iloc[0] > -salary


def test_regression_excludes_cba_suppressed_contracts():
    """Rookie deals are constructed to visibly move the line. They must not."""
    true_slope, true_intercept = 0.0035, 0.005

    market_scores = np.linspace(20, 95, 40)
    market_cap_pct = true_slope * market_scores + true_intercept

    # Stars on rookie-scale money: high production, ~3% of the cap. Including
    # them flattens the slope and drops the intercept.
    rookie_scores = np.linspace(60, 99, 40)
    rookie_cap_pct = np.full(40, 0.030)

    df = pd.DataFrame(
        {
            "composite_score": np.concatenate([market_scores, rookie_scores]),
            "cap_pct": np.concatenate([market_cap_pct, rookie_cap_pct]),
            "war_blended": 3.0,
            "contract_type_override": ["free_agent"] * 40 + ["rookie_scale"] * 40,
        }
    )
    df["salary"] = df["cap_pct"] * config.SALARY_CAP
    df = contract_type.classify_contracts(df)

    assert df["is_market_priced"].sum() == 40

    fit, residuals = valuation.fit_market_line(df)

    assert fit["n"] == 40
    assert fit["slope"] == pytest.approx(true_slope, rel=1e-6)
    assert fit["intercept"] == pytest.approx(true_intercept, rel=1e-6)

    # And confirm the suppressed deals really would have wrecked it.
    contaminated = df.assign(is_market_priced=True)
    bad_fit, _ = valuation.fit_market_line(contaminated)
    assert bad_fit["slope"] < 0.5 * true_slope, (
        "fixture no longer demonstrates the contamination it is guarding against"
    )

    # Rookie-scale players are still plotted and still get a residual — they are
    # the ones the 'best contracts' leaderboard is built from.
    assert residuals.notna().all()
    assert residuals.iloc[40:].min() < 0


def test_model_line_moves_with_the_denominator_and_the_market_line_does_not():
    """The two lines answer different questions; only one responds to the toggle."""
    df = make_league(n=200, seed=31)
    df, _ = war.calibrate_war(df)
    df, _ = composite.compute_composite(df)
    rng = np.random.default_rng(31)
    df["salary"] = np.clip(
        rng.lognormal(15.6, 1.0, len(df)), 1.3e6, 0.35 * config.SALARY_CAP
    )
    df = contract_type.classify_contracts(df)
    out, meta = valuation.value_players(df)

    naive = meta["model_line"]["naive"]
    replacement = meta["model_line"]["replacement"]
    ratio = (
        config.DOLLARS_PER_WIN["replacement"] / config.DOLLARS_PER_WIN["naive"]
    )

    assert ratio == pytest.approx(1.25, abs=0.01)
    assert replacement["slope"] == pytest.approx(naive["slope"] * ratio)
    assert replacement["intercept"] == pytest.approx(naive["intercept"] * ratio)
    # Rescaling y cannot change the fraction of variance explained.
    assert replacement["r2"] == pytest.approx(naive["r2"])
    assert replacement["slope"] > naive["slope"] > 0

    # The market line, by contrast, must be identical across denominators — it
    # has no dollars-per-win term in either axis.
    assert meta["regression"]["naive"] == meta["regression"]["replacement"]
    assert meta["regression_denominator_dependent"] is False

    # Populations differ on purpose: the model line is the model's opinion of
    # every player, the market line is only what negotiated deals reveal.
    assert naive["n"] == int(out["composite_score"].notna().sum())
    assert meta["regression"]["naive"]["n"] < naive["n"]
    assert meta["regression"]["naive"]["n"] == int(out["is_market_priced"].sum())


def test_model_line_is_fit_unfloored():
    """The rookie-minimum clamp must not bend the model's price line."""
    df = pd.DataFrame(
        {
            "composite_score": np.linspace(2, 98, 60),
            "war_blended": np.linspace(-2.5, 14.0, 60),
            "salary": 10_000_000.0,
            "is_market_priced": True,
        }
    )
    out, meta = valuation.value_players(df)

    # Several players are floored, so a floored fit would differ measurably.
    assert out["expected_salary_floored_naive"].sum() > 5

    dpw = config.DOLLARS_PER_WIN["naive"]
    slope, intercept = np.polyfit(df["composite_score"], df["war_blended"], 1)
    assert meta["model_line"]["naive"]["slope"] == pytest.approx(
        slope * dpw / config.SALARY_CAP
    )
    assert meta["model_line"]["naive"]["intercept"] == pytest.approx(
        intercept * dpw / config.SALARY_CAP
    )
    assert meta["model_line_floored"] is False


def test_market_line_residual_ranks_contracts():
    cap_pct = np.array([0.02, 0.06, 0.10, 0.14, 0.18, 0.25])
    df = pd.DataFrame(
        {
            "composite_score": [10, 30, 50, 70, 90, 50],
            "salary": cap_pct * config.SALARY_CAP,
            "is_market_priced": [True] * 6,
            "war_blended": 1.0,
        }
    )
    out, meta = valuation.value_players(df)

    assert out["cap_pct"].to_numpy() == pytest.approx(cap_pct)

    # The last player pays 25% of the cap for median production; he must be the
    # worst residual by a wide margin.
    assert out["cap_pct_residual"].idxmax() == 5
    assert meta["regression"]["replacement"]["n"] == 6


# --------------------------------------------------------------------------
# contract_type.py
# --------------------------------------------------------------------------


def test_salary_tier_matches_real_league_anchors():
    """Real 2026-27 cap hits, scraped. These are the cases the split fixed.

    Exact dollar figures rather than rounded cap shares, so this is a genuine
    regression test against live data and not a restatement of the thresholds.
    """
    df = pd.DataFrame(
        {
            "name": ["Jokic", "Curry", "SGA", "Wembanyama", "LeBron"],
            "salary": [
                59_033_114.0,  # 35.8% of cap
                62_587_158.0,  # 37.9%
                40_806_150.0,  # 24.7%
                16_868_246.0,  # 10.2%
                3_876_529.0,   #  2.35%
            ],
            "age": [30, 37, 27, 22, 41],
            # BBRef's payroll notes for the first four all say "extension",
            # which is exactly how the old single-enum design lost the max.
            "contract_type_override": ["extension"] * 4 + ["free_agent"],
        }
    )
    out = contract_type.classify_contracts(df)

    assert out["salary_tier"].tolist() == [
        "designated_veteran",  # at the 35% tier
        "designated_veteran",
        "max",                 # a 25%-tier max, light because the cap has risen
        "rookie_scale",        # fourth-year option
        "minimum",             # 10+ year veteran minimum
    ]
    # The acquisition axis survives rather than being overwritten by the tier.
    assert out["contract_type"].tolist() == ["extension"] * 4 + ["free_agent"]
    assert out["is_market_priced"].tolist() == [True, True, True, False, False]


def test_minimum_scale_is_derived_from_the_cap_not_a_stale_constant():
    """The 10-year veteran minimum must match the real 2026-27 figure.

    config.ROOKIE_MINIMUM_SALARY is the 2025-26 number, so deriving the scale
    from it directly lands ~6% low and reads veteran-minimum deals as
    negotiated ones. Pegging to the cap, as the CBA does, self-corrects.
    """
    assert contract_type.minimum_salary_for(12) == pytest.approx(
        3_876_529.0, rel=0.01
    )
    assert contract_type.minimum_salary_for(0) == pytest.approx(
        1_357_800.0, rel=0.01
    )
    # Monotonic in service time, as the published scale is.
    scale = [contract_type.minimum_salary_for(y) for y in range(11)]
    assert scale == sorted(scale)


def test_the_two_axes_are_independent():
    """An extension can be a max; a max label must not erase the extension."""
    cap = config.SALARY_CAP
    df = pd.DataFrame(
        {
            "salary": [0.35 * cap, 0.35 * cap, 0.05 * cap, 0.05 * cap],
            "age": [30, 30, 24, 24],
            "experience": [11, 11, 4, 4],
            "contract_type_override": [
                "extension",
                "free_agent",
                "rookie_scale",
                "free_agent",
            ],
        }
    )
    out = contract_type.classify_contracts(df)

    # Same money, different acquisition -> same tier, different type.
    assert out["salary_tier"].tolist() == [
        "designated_veteran",
        "designated_veteran",
        "rookie_scale",
        "rookie_scale",
    ]
    assert out["contract_type"].tolist() == [
        "extension",
        "free_agent",
        "rookie_scale",
        "free_agent",
    ]
    # Suppression fires when EITHER axis says the price was scale-set. Row 3 is
    # a free-agent deal whose money lands in the rookie band — the tier axis
    # alone is enough to keep it out of the fit.
    assert out["is_market_priced"].tolist() == [True, True, False, False]


def test_market_priced_reads_only_from_config():
    cap = config.SALARY_CAP
    df = pd.DataFrame(
        {
            "salary": [0.30 * cap, 0.09 * cap, 0.0912 * cap, 2_500_000.0, 600_000.0],
            "age": [30, 24, 29, 33, 22],
            "experience": [11, 5, 8, 12, 1],
        }
    )
    out = contract_type.classify_contracts(df)

    for row in out.itertuples():
        expected = (
            row.salary_tier in config.MARKET_PRICED_SALARY_TIERS
            and row.contract_type not in config.SUPPRESSED_CONTRACT_TYPES
        )
        assert row.is_market_priced == expected
    assert set(out["salary_tier"]) <= contract_type.SALARY_TIERS | {
        contract_type.UNKNOWN
    }
    assert set(out["contract_type"]) <= contract_type.ACQUISITION_TYPES | {
        contract_type.UNKNOWN
    }


def test_bbref_override_wins_on_the_acquisition_axis_only():
    df = pd.DataFrame(
        {
            "salary": [0.055 * config.SALARY_CAP] * 3,
            "age": [22, 22, 22],
            "experience": [2, 2, 2],
            # "max" is no longer a contract_type; an out-of-vocabulary override
            # must be ignored rather than written through.
            "contract_type_override": ["extension", None, "max"],
        }
    )
    out = contract_type.classify_contracts(df)

    assert out["contract_type"].tolist() == [
        "extension",
        "rookie_scale",  # no override -> the heuristic's answer stands
        "rookie_scale",  # unrecognised override -> ignored
    ]
    # The override never touches the money axis.
    assert out["salary_tier"].tolist() == ["rookie_scale"] * 3
    assert not out["is_market_priced"].any()


def test_unknown_salary_is_not_market_priced():
    df = pd.DataFrame({"salary": [np.nan], "age": [27], "experience": [6]})
    out = contract_type.classify_contracts(df)

    assert out.loc[0, "contract_type"] == contract_type.UNKNOWN
    assert out.loc[0, "salary_tier"] == contract_type.UNKNOWN
    assert not bool(out.loc[0, "is_market_priced"])


def test_experience_falls_back_to_age_when_absent():
    df = pd.DataFrame({"salary": [5_000_000.0], "age": [20]})
    exp = contract_type.infer_experience(df)
    assert exp.iloc[0] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


def test_full_pipeline_produces_the_player_record_columns():
    df = make_league(n=350, seed=21, darko_coverage=0.75)
    rng = np.random.default_rng(21)
    df["salary"] = rng.uniform(1_200_000, 55_000_000, len(df))

    df, war_meta = war.calibrate_war(df)
    df, composite_meta = composite.compute_composite(df)
    df = contract_type.classify_contracts(df)
    df, valuation_meta = valuation.value_players(df)

    # Columns this layer owns. Asserted in both directions: every one must be
    # produced, and every one must still be declared in the shared contract.
    owned = [
        "war_vorp",
        "war_ws",
        "war_darko",
        "war_blended",
        "war_spread",
        "war_sources",
        "war_n_sources",
        "ts_residual",
        "composite_score",
        "composite_n_components",
        "salary",
        "cap_pct",
        "expected_salary_naive",
        "expected_salary_replacement",
        "expected_salary_floored_naive",
        "expected_salary_floored_replacement",
        "surplus_naive",
        "surplus_replacement",
        "cap_pct_residual",
        "contract_type",
        "salary_tier",
        "is_market_priced",
    ]
    assert not [c for c in owned if c not in df.columns], "columns not produced"
    assert not [
        c for c in owned if c not in schema.PLAYER_RECORD_SCHEMA
    ], "columns produced but not declared in PLAYER_RECORD_SCHEMA"

    assert df["composite_score"].between(0, 100).all()
    assert (df["expected_salary_replacement"] >= config.ROOKIE_MINIMUM_SALARY).all()
    assert valuation_meta["regression"]["replacement"]["n"] > 0
    assert war_meta["replacement_ws48"] > 0
    assert composite_meta["ts_fit"]["n"] == len(df)
