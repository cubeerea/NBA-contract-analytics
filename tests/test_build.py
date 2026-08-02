"""Tests for the orchestrator's own logic: the join, and the team rollup.

Both stages here shipped a defect that looked entirely plausible in the output,
which is why these tests are phrased as claims about specific wrong numbers
rather than as smoke tests:

  * A dead-money charge is not a salary. A player waived by Memphis and signed
    elsewhere is priced from his active row (the dedupe in fetch_contracts
    already handles that, and must keep handling it); a player whose ONLY row
    for the season is a dead charge must be dropped entirely, not published
    with the buyout as his cap hit. Kentavious Caldwell-Pope shipped at
    $21,621,500 of Memphis dead money against real production and ranked near
    the top of "most overpaid" on money nobody was paying him.

  * Payroll is a property of the franchise, value is a property of the
    rotation. Rolling payroll up from the post-scope-filter frame gave
    Washington $50.6M and $114M of cap space against a real $186.5M and none.
    The rollup therefore reads two different frames, and these tests pin which
    field comes from which.

  * The 2026-27 contract team wins the join over the 2025-26 stats team, since
    the whole dashboard is about 2026-27 money.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from etl import build, config, schema


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def make_stats(rows: list[dict]) -> pd.DataFrame:
    """A STATS_SCHEMA-shaped frame; unspecified columns get harmless defaults."""
    defaults = {
        "name": "Player",
        "age": 27,
        "team": "AAA",
        "position": "SF",
        "games": 70,
        "games_started": 70,
        "minutes": 2000,
        "per": 15.0,
        "ts_pct": 0.57,
        "usg_pct": 22.0,
        "ows": 3.0,
        "dws": 2.0,
        "ws": 5.0,
        "ws_per_48": 0.120,
        "obpm": 1.0,
        "dbpm": 0.5,
        "bpm": 1.5,
        "vorp": 2.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def make_contracts(rows: list[dict]) -> pd.DataFrame:
    """A fetch_contracts()-shaped frame, including the documented extras."""
    defaults = {
        "name": "Player",
        "team": "AAA",
        "salary_2026_27": 10_000_000.0,
        "guaranteed_remaining": 10_000_000.0,
        "future_years": {},
        "contract_type_override": None,
        "is_two_way": False,
        "is_dead_money": False,
        "salary_2026_27_option": "",
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def join(stats: pd.DataFrame, contracts: pd.DataFrame) -> pd.DataFrame:
    crosswalk = pd.DataFrame(
        {
            "bbref_slug": list(stats["bbref_slug"]),
            "nba_player_id": range(1, len(stats) + 1),
            "headshot_url": ["http://x/" + s for s in stats["bbref_slug"]],
            "name_normalized": [s for s in stats["bbref_slug"]],
        }
    )
    frames = {
        "stats": stats,
        "contracts": contracts,
        "darko": pd.DataFrame(columns=list(schema.DARKO_SCHEMA)),
        "spotrac": pd.DataFrame(columns=list(schema.SPOTRAC_SCHEMA)),
    }
    return build._join(frames, crosswalk)


# --------------------------------------------------------------------------
# Defect 1 — dead-money-only rows must not become cap hits
# --------------------------------------------------------------------------


def test_dead_money_only_player_is_dropped():
    """KCP: waived by Memphis, no active row anywhere. He must not ship."""
    stats = make_stats(
        [
            {"bbref_slug": "caldwke01", "name": "Kentavious Caldwell-Pope"},
            {"bbref_slug": "curryst01", "name": "Stephen Curry"},
        ]
    )
    contracts = make_contracts(
        [
            {
                "bbref_slug": "caldwke01",
                "name": "Kentavious Caldwell-Pope",
                "team": "MEM",
                "salary_2026_27": 21_621_500.0,
                "is_dead_money": True,
            },
            {
                "bbref_slug": "curryst01",
                "name": "Stephen Curry",
                "team": "GSW",
                "salary_2026_27": 62_587_158.0,
            },
        ]
    )

    out = join(stats, contracts)

    assert list(out["bbref_slug"]) == ["curryst01"]
    assert 21_621_500.0 not in set(out["salary"])


def test_player_with_both_a_dead_and_an_active_row_keeps_the_active_salary():
    """The existing dedupe behaviour must survive the new filter.

    fetch_contracts() collapses this case before the join, keeping the active
    row. The dead-money filter must not then throw the survivor away — the
    player is genuinely employed and genuinely paid.
    """
    stats = make_stats([{"bbref_slug": "hardeja01", "name": "James Harden"}])
    contracts = make_contracts(
        [
            {
                "bbref_slug": "hardeja01",
                "name": "James Harden",
                "team": "LAC",
                "salary_2026_27": 5_000_000.0,
                "is_dead_money": False,
            }
        ]
    )

    out = join(stats, contracts)

    assert len(out) == 1
    assert out["salary"].iloc[0] == 5_000_000.0
    assert out["team"].iloc[0] == "LAC"


def test_dedupe_prefers_the_active_row_over_the_dead_one():
    """Guard the sort that fetch_contracts relies on, at frame level.

    is_dead_money False must sort before True so drop_duplicates(keep="first")
    keeps the money the player is actually being paid.
    """
    frame = pd.DataFrame(
        {
            "bbref_slug": ["x01", "x01"],
            "team": ["MEM", "PHI"],
            "is_dead_money": [True, False],
            "salary_2026_27": [21_621_500.0, 3_900_000.0],
        }
    )
    deduped = (
        frame.sort_values(["bbref_slug", "is_dead_money"], kind="stable")
        .drop_duplicates("bbref_slug", keep="first")
        .reset_index(drop=True)
    )
    assert len(deduped) == 1
    assert deduped["team"].iloc[0] == "PHI"
    assert deduped["salary_2026_27"].iloc[0] == 3_900_000.0
    assert not bool(deduped["is_dead_money"].iloc[0])


def test_two_way_nan_salary_still_dropped_alongside_dead_money():
    """The pre-existing NaN filter must keep working next to the new one."""
    stats = make_stats(
        [
            {"bbref_slug": "twoway01"},
            {"bbref_slug": "dead0001"},
            {"bbref_slug": "live0001"},
        ]
    )
    contracts = make_contracts(
        [
            {"bbref_slug": "twoway01", "salary_2026_27": np.nan, "is_two_way": True},
            {"bbref_slug": "dead0001", "is_dead_money": True},
            {"bbref_slug": "live0001"},
        ]
    )

    out = join(stats, contracts)

    assert list(out["bbref_slug"]) == ["live0001"]


# --------------------------------------------------------------------------
# Defect 3 — the contract team wins the join
# --------------------------------------------------------------------------


def test_contract_team_wins_over_stats_team():
    """LeBron produced for LAL in 2025-26 and is paid by PHI in 2026-27."""
    stats = make_stats([{"bbref_slug": "jamesle01", "team": "LAL"}])
    contracts = make_contracts([{"bbref_slug": "jamesle01", "team": "PHI"}])

    out = join(stats, contracts)

    assert out["team"].iloc[0] == "PHI"
    assert out["stats_team"].iloc[0] == "LAL"
    assert "team_x" not in out.columns and "team_y" not in out.columns


def test_stats_team_retained_for_players_who_did_not_move():
    stats = make_stats([{"bbref_slug": "jokicni01", "team": "DEN"}])
    contracts = make_contracts([{"bbref_slug": "jokicni01", "team": "DEN"}])

    out = join(stats, contracts)

    assert out["team"].iloc[0] == "DEN"
    assert out["stats_team"].iloc[0] == "DEN"


# --------------------------------------------------------------------------
# Defect 2 — payroll from the full set, value from the scoped set
# --------------------------------------------------------------------------


def make_scoped(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "salary": 10_000_000.0,
        "war_blended": 3.0,
        "surplus_naive": 1_000_000.0,
        "surplus_replacement": 2_000_000.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_payroll_comes_from_the_unfiltered_contract_set():
    """Washington: 2 rotation players visible, a real payroll behind them."""
    scoped = make_scoped(
        [{"team": "WAS", "salary": 25_000_000.0}, {"team": "WAS", "salary": 25_625_116.0}]
    )
    contracts = pd.DataFrame(
        {
            "team": ["WAS"] * 4,
            "salary": [25_000_000.0, 25_625_116.0, 80_000_000.0, 55_874_884.0],
        }
    )

    (row,) = build._team_rollup(scoped, contracts)

    assert row["total_salary"] == 186_500_000.0
    assert row["roster_count"] == 4
    # The scoped sum is published, but it is NOT the payroll.
    assert row["rotation_salary"] == 50_625_116.0
    assert row["player_count"] == 2


def test_cap_space_and_apron_follow_the_full_payroll_not_the_rotation():
    """The shipped bug: $50.6M scoped read as $114M of room and under_cap."""
    scoped = make_scoped([{"team": "WAS", "salary": 50_625_116.0}])
    contracts = pd.DataFrame({"team": ["WAS", "WAS"], "salary": [50_625_116.0, 135_874_884.0]})

    (row,) = build._team_rollup(scoped, contracts)

    assert row["cap_space"] == config.SALARY_CAP - 186_500_000.0
    assert row["cap_space"] < 0
    assert row["apron_status"] == "over_cap"


def test_value_aggregates_ignore_players_below_the_scope_floor():
    """Deep-bench WAR is noise; it must not reach a team's totals."""
    scoped = make_scoped(
        [
            {"team": "GSW", "war_blended": 5.0, "surplus_replacement": 4_000_000.0},
            {"team": "GSW", "war_blended": 2.0, "surplus_replacement": 1_000_000.0},
        ]
    )
    contracts = pd.DataFrame(
        {"team": ["GSW"] * 5, "salary": [40e6, 30e6, 20e6, 10e6, 5e6]}
    )

    (row,) = build._team_rollup(scoped, contracts)

    assert row["total_war"] == 7.0
    assert row["total_surplus_replacement"] == 5_000_000.0
    assert row["player_count"] == 2
    assert row["roster_count"] == 5
    assert row["total_salary"] == 105_000_000.0


def test_apron_thresholds_match_config():
    """Each band is exercised at its own boundary, read off config."""
    cases = [
        (config.SALARY_CAP - 1, "under_cap"),
        (config.SALARY_CAP, "over_cap"),
        (config.LUXURY_TAX, "over_tax"),
        (config.FIRST_APRON, "first_apron"),
        (config.SECOND_APRON, "second_apron"),
        (config.SECOND_APRON + 10e6, "second_apron"),
    ]
    for total, expected in cases:
        assert build._apron_status(total) == expected, total


def test_rollup_emits_every_team_that_has_a_payroll():
    """A team with no player clearing the minutes floor still has books."""
    scoped = make_scoped([{"team": "GSW"}])
    contracts = pd.DataFrame({"team": ["GSW", "WAS"], "salary": [150e6, 190e6]})

    rows = {r["team"]: r for r in build._team_rollup(scoped, contracts)}

    assert set(rows) == {"GSW", "WAS"}
    assert rows["WAS"]["total_salary"] == 190e6
    assert rows["WAS"]["player_count"] == 0
    assert rows["WAS"]["total_war"] == 0.0


def test_rollup_records_match_the_declared_schema():
    scoped = make_scoped([{"team": "GSW"}])
    contracts = pd.DataFrame({"team": ["GSW"], "salary": [150e6]})

    (row,) = build._team_rollup(scoped, contracts)

    assert set(row) == set(schema.TEAM_RECORD_SCHEMA)


def test_rollup_without_contracts_falls_back_and_warns(caplog):
    """The fallback is for tests only; it must announce that it is lying."""
    scoped = make_scoped([{"team": "GSW", "salary": 50e6}])

    with caplog.at_level("WARNING", logger="etl.build"):
        (row,) = build._team_rollup(scoped, None)

    assert row["total_salary"] == 50e6
    assert any("ROTATION" in r.message or "rotation" in r.message.lower()
               for r in caplog.records)
