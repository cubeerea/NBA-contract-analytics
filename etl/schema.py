"""Shared data contract between ETL stages.

This module is the coordination point for the pipeline. Every source adapter
returns a DataFrame conforming to one of the schemas below; every transform
consumes and extends them. Stages are developed independently against these
definitions, so changing a column name here is a breaking change and must be
made deliberately.

Canonical join key throughout the pipeline is `bbref_slug` (e.g. "curryst01").
It is stable, unique, and present on every Basketball-Reference page, which
makes it a better spine than player name. See README "Canonical identity spine".
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Source outputs
# --------------------------------------------------------------------------

# etl/sources/bbref_stats.py -> DataFrame
# One row per player for the 2025-26 season. Players who changed teams
# mid-season MUST be collapsed to a single row using their combined ("TOT"/"2TM")
# line, with `team` set to their final team.
STATS_SCHEMA = {
    "bbref_slug": "str",      # "curryst01" — canonical key
    "name": "str",
    "age": "int",
    "team": "str",            # 3-letter abbr; final team if traded
    "position": "str",        # PG/SG/SF/PF/C
    "games": "int",
    "games_started": "int",
    "minutes": "int",         # total MP, not per-game
    "per": "float",
    "ts_pct": "float",
    "usg_pct": "float",
    "ows": "float",
    "dws": "float",
    "ws": "float",
    "ws_per_48": "float",
    "obpm": "float",
    "dbpm": "float",
    "bpm": "float",
    "vorp": "float",
}

# etl/sources/bbref_contracts.py -> DataFrame
# One row per player under contract for 2026-27.
CONTRACTS_SCHEMA = {
    "bbref_slug": "str",
    "name": "str",
    "team": "str",
    "salary_2026_27": "float",     # cap hit in dollars; NaN if not under contract
    "guaranteed_remaining": "float",
    "future_years": "object",      # dict[str, float] e.g. {"2027-28": 5.5e7}
}

# etl/sources/darko.py -> DataFrame
# Current DARKO projections. Joined on name (DARKO has no BBRef slug), so it
# MUST go through the crosswalk's name normalizer.
DARKO_SCHEMA = {
    "nba_player_id": "int",        # if DARKO exposes it; else resolved via crosswalk
    "name": "str",
    "dpm": "float",                # Daily Plus-Minus, points per 100 possessions
    "o_dpm": "float",
    "d_dpm": "float",
    "box_dpm": "float",            # box-only variant, fallback if dpm missing
}

# etl/sources/spotrac.py -> DataFrame   [ENRICHMENT — allowed to fail]
SPOTRAC_SCHEMA = {
    "bbref_slug": "str",           # resolved via crosswalk
    "contract_type": "str",        # rookie_scale|minimum|mle|max|free_agent|extension|two_way
    "signed_year": "int",
    "total_value": "float",
    "years_remaining": "int",
    "option_type": "str",          # none|player|team|ETO
    "no_trade_clause": "bool",
    "trade_kicker_pct": "float",
}

# etl/crosswalk.py -> DataFrame
CROSSWALK_SCHEMA = {
    "bbref_slug": "str",
    "nba_player_id": "int",        # from nba_api.stats.static.players (OFFLINE)
    "name": "str",
    "name_normalized": "str",      # lowercase, unaccented, suffix-stripped
    "headshot_url": "str",
}

# --------------------------------------------------------------------------
# Final artifact: data/processed/players.json
# --------------------------------------------------------------------------
# One record per player that cleared the ADR-008 scope filter. The frontend
# reads this directly and recomputes only what a UI toggle affects.

PLAYER_RECORD_SCHEMA = {
    # identity
    "bbref_slug": "str",
    "nba_player_id": "int",
    "name": "str",
    "headshot_url": "str",
    # WHO PAYS HIM IN 2026-27, from the contracts source — not where he played
    # in 2025-26. The two disagree for everyone who moved this summer (61 of
    # 334 at last build: LeBron James is LAL in the stats and PHI on the
    # payroll). The dashboard is about 2026-27 contracts and team payrolls roll
    # up on this field, so the contract team is authoritative.
    "team": "str",
    "stats_team": "str",          # where the 2025-26 production was produced
    "position": "str",
    "age": "int",

    # raw production
    "games": "int",
    "minutes": "int",
    "ts_pct": "float",
    "usg_pct": "float",
    "ws": "float",
    "ws_per_48": "float",
    "vorp": "float",
    "bpm": "float",
    "darko_dpm": "float",

    # derived — transform/war.py
    "war_vorp": "float",
    "war_ws": "float",
    "war_darko": "float",
    "war_blended": "float",
    "war_spread": "float",        # max-min across the three; uncertainty band
    "war_sources": "str",         # "vorp,ws,darko" — which estimates blended
    "war_n_sources": "int",       # spread is only meaningful when this is >= 2

    # derived — transform/composite.py
    "ts_residual": "float",       # TS% above expectation at that usage
    "composite_score": "float",   # 0-100 percentile blend, X-axis default
    "composite_n_components": "int",  # weights renormalised over this many

    # derived — transform/valuation.py
    # Both denominators precomputed so the UI toggle is a lookup, not a rebuild.
    "salary": "float",
    "cap_pct": "float",           # Y-axis
    "expected_salary_naive": "float",
    "expected_salary_replacement": "float",
    "surplus_naive": "float",
    "surplus_replacement": "float",
    # True when negative production hit the rookie-minimum floor, i.e. the
    # surplus is a statement about the floor rather than about the model's read.
    "expected_salary_floored_naive": "bool",
    "expected_salary_floored_replacement": "bool",
    # Vertical distance from the fitted MARKET line (meta.regression), not the
    # model line. This is what ranks best/worst contracts. Denominator-free.
    "cap_pct_residual": "float",

    # contract context
    # Two orthogonal axes — see transform/contract_type.py and config. A player
    # can be on an extension AND a max; one enum could not hold both.
    "contract_type": "str",       # HOW acquired: rookie_scale|free_agent|
                                  # extension|minimum|two_way|unknown
    "salary_tier": "str",         # WHAT it pays: designated_veteran|max|mle|
                                  # minimum|rookie_scale|standard|unknown.
                                  # This is the field the chart colours by.
    "is_market_priced": "bool",   # True -> included in the regression fit
    "guaranteed_remaining": "float",
    "years_remaining": "int",
    "no_trade_clause": "bool",
    "trade_eligible": "bool",
    "trade_restriction_reason": "str",
}

# data/processed/meta.json — constants and fit results the frontend needs.
META_SCHEMA = {
    "generated_at": "str",             # ISO-8601 UTC
    "stats_season": "str",
    "salary_season": "str",
    "salary_cap": "float",
    "dollars_per_win": "object",       # {"naive": float, "replacement": float}
    "regression": "object",            # MARKET line: what teams actually pay.
                                       # {"naive": {slope, intercept, r2, n},
                                       #  "replacement": {...}} — identical
                                       # entries; cap_pct ~ composite_score does
                                       # not depend on the $/win denominator.
    "model_line": "object",            # MODEL line: what the model says a player
                                       # should earn, same axes. Same shape, but
                                       # the two entries DO differ — that is the
                                       # line the $/win toggle moves.
    "composite_weights": "object",
    "min_minutes": "int",
    "player_count": "int",
    "excluded_count": "int",
}

# data/processed/teams.json
#
# TWO POPULATIONS IN ONE RECORD — see build._team_rollup for the reasoning.
# The payroll block is computed from the FULL contract set (every cap charge,
# including sub-threshold players and dead money); the value block is computed
# from the SCOPED frame (rotation players only, ADR-008). Summing surplus over
# deep-bench noise would be wrong, and computing cap space off a filtered
# payroll is equally wrong — hence the split, and hence `roster_count` next to
# `player_count` and `rotation_salary` next to `total_salary`, so a reader can
# see which population each number came from without guessing.
TEAM_RECORD_SCHEMA = {
    "team": "str",
    # --- payroll: full contract set --------------------------------------
    "total_salary": "float",           # full 2026-27 cap commitment
    "roster_count": "int",             # contract rows behind total_salary
    "cap_space": "float",              # SALARY_CAP - total_salary; negative = over
    "apron_status": "str",             # under_cap|over_cap|over_tax|first_apron|second_apron
    # --- value: scoped rotation players only -------------------------------
    "rotation_salary": "float",        # salary of the scoped players only
    "total_war": "float",
    "total_surplus_naive": "float",
    "total_surplus_replacement": "float",
    "player_count": "int",             # scoped players, NOT roster size
}
