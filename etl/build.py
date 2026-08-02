"""ETL orchestrator: sources -> crosswalk -> transforms -> data/processed/*.json.

Run nightly by GitHub Actions (ADR-004/009). Also runnable locally:

    python3 -m etl.build              # normal run, uses cached scrapes
    python3 -m etl.build --refresh    # bypass cache, refetch everything
    python3 -m etl.build --verify     # run build-time assertions, exit nonzero on failure

Sources are ranked ESSENTIAL vs ENRICHMENT (README "Fail-soft ingestion").
An essential source failing must fail the build loudly — publishing a dashboard
with half the league silently missing is worse than publishing nothing. An
enrichment source failing must only degrade: the nightly job must not go dark
because Spotrac changed a CSS class.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from . import config, schema
from .ratelimit import SourceUnavailable

log = logging.getLogger("etl.build")


class BuildFailure(RuntimeError):
    """A verification assertion failed. The artifact must not be published."""


# --------------------------------------------------------------------------
# Stage 1 — ingest
# --------------------------------------------------------------------------


def _load_sources(refresh: bool) -> dict[str, pd.DataFrame]:
    """Fetch all sources. Essential failures raise; enrichment failures warn."""
    from .sources import bbref_contracts, bbref_stats, darko

    out: dict[str, pd.DataFrame] = {}

    # --- essential ---------------------------------------------------------
    log.info("fetching BBRef advanced stats (%s)", config.STATS_SEASON_LABEL)
    out["stats"] = bbref_stats.fetch_advanced_stats(force_refresh=refresh)
    log.info("  %d players", len(out["stats"]))

    log.info("fetching BBRef contracts (%s)", config.SALARY_SEASON_LABEL)
    out["contracts"] = bbref_contracts.fetch_contracts(force_refresh=refresh)
    log.info("  %d contracts", len(out["contracts"]))

    # --- enrichment --------------------------------------------------------
    try:
        log.info("fetching DARKO projections")
        out["darko"] = darko.fetch_darko(force_refresh=refresh)
        log.info("  %d players", len(out["darko"]))
    except (SourceUnavailable, Exception) as exc:  # noqa: BLE001 - degrade on anything
        log.warning("DARKO unavailable, continuing without it: %s", exc)
        out["darko"] = pd.DataFrame(columns=list(schema.DARKO_SCHEMA))

    try:
        from .sources import spotrac

        log.info("fetching Spotrac contract structure")
        out["spotrac"] = spotrac.fetch_contract_details(force_refresh=refresh)
        log.info("  %d players", len(out["spotrac"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("Spotrac unavailable, contract types will be inferred: %s", exc)
        out["spotrac"] = pd.DataFrame(columns=list(schema.SPOTRAC_SCHEMA))

    return out


# --------------------------------------------------------------------------
# Stage 2 — join
# --------------------------------------------------------------------------


def _join(frames: dict[str, pd.DataFrame], crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Join every source onto the canonical bbref_slug spine."""
    stats = frames["stats"]
    contracts = frames["contracts"]

    # WHICH TEAM WINS THE JOIN.
    # Both sources carry a `team`, and they disagree for anyone who moved this
    # summer — 89 of 428 joined, 61 of the 334 published. The stats team is where a
    # player produced in 2025-26; the contracts team is who is paying him in
    # 2026-27. This dashboard is about 2026-27 contracts, and `_team_rollup`
    # groups by `team` to build payrolls, so the CONTRACT team is the correct
    # label: filing Anthony Davis's Washington salary against Dallas would put a
    # $58M cap hit on a team that is not paying it. The stats team is kept
    # alongside as `stats_team` so "where these minutes were played" is still
    # answerable.
    stats = stats.rename(columns={"team": "stats_team"})

    # Inner join: a player needs BOTH production and a contract to be scored.
    # Players with a contract but no 2025-26 production (2026 draftees,
    # international signings) are counted and reported, not silently dropped.
    df = stats.merge(
        contracts.drop(columns=["name"], errors="ignore"),
        on="bbref_slug",
        how="inner",
        validate="one_to_one",
    )
    moved = int((df["team"] != df["stats_team"]).sum())
    if moved:
        log.info(
            "%d/%d players changed teams since %s — labelled with their %s "
            "contract team (stats team retained as `stats_team`)",
            moved,
            len(df),
            config.STATS_SEASON_LABEL,
            config.SALARY_SEASON_LABEL,
        )

    no_production = set(contracts["bbref_slug"]) - set(stats["bbref_slug"])
    if no_production:
        log.info(
            "%d contracted players have no %s production (draftees/intl) — excluded",
            len(no_production),
            config.STATS_SEASON_LABEL,
        )

    df = df.merge(
        crosswalk[["bbref_slug", "nba_player_id", "headshot_url", "name_normalized"]],
        on="bbref_slug",
        how="left",
    )

    # DARKO join. The sheet publishes an "NBA ID" column populated for every
    # row, so we join on that rather than on name — an exact integer key beats
    # fuzzy name matching, and it sidesteps the accent problem entirely
    # ("Nikola Jokic" vs "Nikola Jokić" is 35 of the 530 rows). Name matching
    # remains as a fallback for any row missing an ID.
    darko = frames["darko"]
    if not darko.empty:
        df["darko_dpm"] = np.nan

        if "nba_player_id" in darko.columns and darko["nba_player_id"].notna().any():
            by_id = (
                darko.dropna(subset=["nba_player_id"])
                .assign(nba_player_id=lambda d: d["nba_player_id"].astype("Int64"))
                .set_index("nba_player_id")["dpm"]
            )
            df["darko_dpm"] = df["nba_player_id"].astype("Int64").map(by_id)
            log.info(
                "DARKO matched %d/%d players by NBA ID",
                df["darko_dpm"].notna().sum(),
                len(df),
            )

        # Fallback for anyone the ID join missed.
        unmatched = df["darko_dpm"].isna()
        if unmatched.any():
            from .crosswalk import normalize_name

            by_name = (
                darko.assign(_n=darko["name"].map(normalize_name))
                .drop_duplicates("_n")
                .set_index("_n")["dpm"]
            )
            filled = df.loc[unmatched, "name_normalized"].map(by_name)
            df.loc[unmatched, "darko_dpm"] = filled
            recovered = filled.notna().sum()
            if recovered:
                log.info("DARKO recovered %d more players by name", recovered)

        still_missing = df["darko_dpm"].isna().sum()
        if still_missing:
            log.info(
                "%d players have no DARKO value; composite weights will "
                "renormalize for them",
                still_missing,
            )
    else:
        df["darko_dpm"] = np.nan

    # Spotrac carries no BBRef identifier, so it resolves through the same
    # normalizer as every other name-keyed source rather than growing its own
    # matching logic.
    spotrac = frames["spotrac"]
    if not spotrac.empty and "name" in spotrac.columns:
        from .crosswalk import normalize_name

        # Only the STRUCTURAL fields are taken. Spotrac's own contract_type is
        # deliberately discarded: its values (max, designated_veteran,
        # free_agent) straddle both classification axes, and letting them write
        # to `contract_type` would recreate exactly the conflation that left the
        # Max/Designated-Veteran category empty. `salary_tier` is computed from
        # cap share against the published CBA tables, which is strictly better
        # than Spotrac's coarse inference.
        wanted = [
            c
            for c in (
                "signed_year",
                "contract_end_year",
                "contract_years",
                "total_value",
                "aav",
                "years_remaining",
                "no_trade_clause",
            )
            if c in spotrac.columns
        ]
        sp = (
            spotrac.assign(_n=spotrac["name"].map(normalize_name))
            .drop_duplicates("_n")
            .set_index("_n")[wanted]
        )
        before = len(df)
        df = df.join(sp, on="name_normalized")
        assert len(df) == before, "Spotrac join changed row count"
        matched = df[wanted[0]].notna().sum() if wanted else 0
        log.info("Spotrac enriched %d/%d players", matched, len(df))

    # CONTRACTS_SCHEMA names the cap hit for its season explicitly
    # ("salary_2026_27") so the source frame is self-describing, but everything
    # downstream works on whichever season is configured and calls it "salary".
    # Renaming here keeps the season-specific name from leaking into the model.
    season_col = f"salary_{config.SALARY_SEASON_LABEL.replace('-', '_')}"
    if season_col in df.columns:
        df = df.rename(columns={season_col: "salary"})
    elif "salary" not in df.columns:
        raise BuildFailure(
            f"contracts frame has neither '{season_col}' nor 'salary'; "
            f"columns were {sorted(df.columns)}"
        )

    # A player under contract but with no cap hit for this season (waived,
    # dead money attributed elsewhere) cannot be valued.
    missing_salary = df["salary"].isna().sum()
    if missing_salary:
        log.info("%d joined players have no %s cap hit — excluded",
                 missing_salary, config.SALARY_SEASON_LABEL)
        df = df[df["salary"].notna()].copy()

    # Dead money is a charge against a team that waived, bought out or traded a
    # player; it is not what anyone is paying him to play in 2026-27, and it must
    # not be presented as his cap hit.
    #
    # fetch_contracts() already resolves the ordinary case — waived by one team,
    # signed by another — by keeping the ACTIVE row and dropping the dead one, so
    # those players arrive here correctly priced and unaffected by this filter.
    # What survives to this point flagged `is_dead_money` is the other case: a
    # player whose ONLY 2026-27 row in the league is a dead charge, because his
    # new deal is not on a BBRef payroll page yet (or does not exist). Left in,
    # the buyout figure becomes his salary, and since his production is real he
    # lands at the top of the "most overpaid" ranking on money nobody is paying
    # him — Kentavious Caldwell-Pope carried $21,621,500 of Memphis dead money
    # while actually signing with Philadelphia for ~$3.9M.
    #
    # The rows still count against the paying team's cap, so they are dropped
    # only from the per-player frame. `_team_rollup` reads payroll off the
    # unfiltered contract set, which retains them.
    if "is_dead_money" in df.columns:
        dead_only = df["is_dead_money"].fillna(False).astype(bool)
        n_dead = int(dead_only.sum())
        if n_dead:
            log.info(
                "%d joined players have only a dead-money %s charge (no active "
                "contract anywhere) — excluded: %s",
                n_dead,
                config.SALARY_SEASON_LABEL,
                ", ".join(
                    f"{n} ({t} ${s:,.0f})"
                    for n, t, s in zip(
                        df.loc[dead_only, "name"],
                        df.loc[dead_only, "team"],
                        df.loc[dead_only, "salary"],
                    )
                ),
            )
            df = df[~dead_only].copy()

    return df


# --------------------------------------------------------------------------
# Stage 3 — scope filter (ADR-008)
# --------------------------------------------------------------------------


def _apply_scope(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop players below the playing-time floor.

    Per-minute metrics are unstable at low sample; an unfiltered chart lets a
    40-minute player post a wild surplus that dominates the outlier view and
    discredits everything else on it.
    """
    before = len(df)
    keep = (df["minutes"] >= config.MIN_MINUTES_PLAYED) & (
        df["games"] >= config.MIN_GAMES_PLAYED
    )
    out = df[keep].copy()
    excluded = before - len(out)
    log.info(
        "scope filter: %d -> %d players (%d below %dMP/%dG floor)",
        before,
        len(out),
        excluded,
        config.MIN_MINUTES_PLAYED,
        config.MIN_GAMES_PLAYED,
    )
    return out, excluded


# --------------------------------------------------------------------------
# Stage 4 — transforms
# --------------------------------------------------------------------------


def _transform(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    from .transform import composite, contract_type, valuation, war

    from .transform import trade_eligibility

    df = contract_type.classify_contracts(df)
    df, war_constants = war.calibrate_war(df)
    df, composite_fit = composite.compute_composite(df)
    df, valuation_meta = valuation.value_players(df)
    # Needs signed_year from the Spotrac enrichment above, so it runs last.
    df = trade_eligibility.evaluate(df)

    # value_players returns both the empirical market line and the
    # denominator-dependent model line (ADR-013).
    return df, {
        "war_constants": war_constants,
        "composite_fit": composite_fit,
        "regression": valuation_meta.get("regression", {}),
        "model_line": valuation_meta.get("model_line", {}),
        "regression_denominator_dependent": valuation_meta.get(
            "regression_denominator_dependent", False
        ),
        "model_line_floored": valuation_meta.get("model_line_floored", False),
    }


# --------------------------------------------------------------------------
# Stage 5 — verification
# --------------------------------------------------------------------------


def _verify(
    df: pd.DataFrame,
    fits: dict[str, Any],
    strict: bool,
    raw_contracts: pd.DataFrame | None = None,
) -> list[str]:
    """Build-time assertions. See README verification section.

    These exist because every one of them corresponds to a failure mode that
    would otherwise ship silently and look plausible.
    """
    problems: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)
            log.error("VERIFY FAIL: %s", message)

    # Identity: an unresolved player means a broken headshot and a hole in the
    # chart. Better to fail than to publish a face-less point.
    missing_id = df["nba_player_id"].isna().sum()
    check(missing_id == 0, f"{missing_id} players have no NBA player ID")

    # WAR calibration: the three estimates must agree at the league level after
    # calibration. Divergence means a conversion constant drifted.
    totals = {
        col: df[col].sum()
        for col in ("war_vorp", "war_ws", "war_darko")
        if col in df and df[col].notna().any()
    }
    if len(totals) > 1:
        anchor = totals.get("war_vorp")
        for name, total in totals.items():
            if name == "war_vorp" or not anchor:
                continue
            drift = abs(total - anchor) / abs(anchor)
            check(
                drift <= config.WAR_CALIBRATION_TOLERANCE,
                f"{name} league total drifts {drift:.1%} from war_vorp "
                f"(tolerance {config.WAR_CALIBRATION_TOLERANCE:.0%})",
            )

    # Cap arithmetic: catches salary parse errors instantly. A team summing to
    # $12M or $900M means the parser ate a comma or a dollar sign.
    #
    # This MUST run on the full contracts frame, not on `df`. `df` has already
    # been through the ADR-008 minutes filter, so a team's visible total is only
    # its rotation players — Washington reads $50.6M scoped against $186.5M
    # actual. Asserting a full-payroll band against a filtered frame is a check
    # that fails on correct data, which is worse than no check at all.
    full = raw_contracts
    if full is not None and "salary" in full.columns:
        team_totals = full.groupby("team")["salary"].sum()
        for team, total in team_totals.items():
            check(
                100e6 <= total <= 280e6,
                f"team {team} full payroll ${total/1e6:.1f}M is implausible",
            )
    else:
        log.info("skipping cap-arithmetic check (no unfiltered contracts frame)")
        team_totals = df.groupby("team")["salary"].sum()

    check(df["composite_score"].between(0, 100).all(), "composite_score out of 0-100")
    check(not df["bbref_slug"].duplicated().any(), "duplicate bbref_slug in output")
    # Post-scope count. ~450 players carry a cap hit; the 500MP/20G floor
    # removes deep bench and injury-shortened seasons, landing near 340.
    check(
        250 <= len(df) <= 600,
        f"player count {len(df)} outside plausible post-scope range",
    )

    # Cross-source check: compare BBRef-derived team payrolls against Spotrac's
    # independently-reported totals. A single source's total can be confidently
    # wrong; two sources agreeing across 30 teams is real evidence. This is the
    # check most likely to catch a silent parser regression when BBRef changes
    # its markup, because it does not depend on our own parsing being right.
    try:
        from .sources.spotrac import fetch_team_cap_totals

        spotrac_totals = fetch_team_cap_totals().set_index("team_spotrac")["total_cap"]
        # Our scope filter drops sub-threshold players, so our totals are
        # legitimately LOWER than full payroll. We are checking for gross
        # divergence (a parse error), not exact agreement.
        diverged = []
        for team, ours in team_totals.items():
            theirs = spotrac_totals.get(team)
            if theirs is None or not np.isfinite(theirs):
                continue
            if ours > theirs * 1.05 or ours < theirs * 0.55:
                diverged.append(f"{team}: ours ${ours/1e6:.0f}M vs Spotrac ${theirs/1e6:.0f}M")
        check(
            len(diverged) <= 3,
            f"{len(diverged)} teams diverge from Spotrac payrolls: {diverged[:5]}",
        )
    except Exception as exc:  # noqa: BLE001 - cross-check is advisory only
        log.info("skipping Spotrac cross-check (%s)", exc)

    # The regression must be fit on a meaningful sample of market-priced deals.
    for mode, fit in fits["regression"].items():
        check(
            fit.get("n", 0) >= 100,
            f"regression '{mode}' fit on only {fit.get('n')} market-priced contracts",
        )

    if problems and strict:
        raise BuildFailure(f"{len(problems)} verification failure(s)")
    return problems


# --------------------------------------------------------------------------
# Stage 6 — emit
# --------------------------------------------------------------------------


def _mirror_headshots(df: pd.DataFrame) -> pd.DataFrame:
    """Mirror portraits locally and repoint `headshot_url` at the copies.

    cdn.nba.com sends no Access-Control-Allow-Origin, so WebGL cannot texture
    its images and deck.gl's IconLayer — which is how players are drawn at all
    (ADR-007) — cannot build its atlas from the CDN. See sources/headshots.py.

    Enrichment-grade: a failure here costs portraits, not the build.
    """
    try:
        from .sources.headshots import mirror_headshots

        ids = [int(p) for p in df["nba_player_id"].dropna().unique()]
        resolved = mirror_headshots(ids)
        if not resolved:
            return df

        df = df.copy()
        # Keep the CDN URL for the detail card, which uses a plain <img> and is
        # unaffected by CORS, and point the chart at the same-origin mirror.
        df["headshot_cdn_url"] = df["headshot_url"]
        df["headshot_url"] = (
            df["nba_player_id"]
            .map(lambda p: resolved.get(int(p)) if pd.notna(p) else None)
            .fillna(df["headshot_url"])
        )
        return df
    except Exception as exc:  # noqa: BLE001 - portraits are cosmetic
        log.warning("headshot mirroring failed, using CDN URLs: %s", exc)
        return df


def _emit(
    df: pd.DataFrame,
    fits: dict[str, Any],
    excluded: int,
    contracts: pd.DataFrame | None = None,
) -> None:
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    # No-trade clauses are not exposed by any source we ingest: BBRef's payroll
    # tables omit them and Spotrac only shows them on per-player pages, which
    # would cost ~500 extra requests for an enrichment field. Emit null rather
    # than False so the UI can say "unknown" — reporting "no NTC" when we simply
    # did not look would be a confidently wrong trade flag, which ADR-011 says
    # is worse than an absent one.
    if "no_trade_clause" not in df.columns:
        df = df.copy()
        df["no_trade_clause"] = None

    cols = [c for c in schema.PLAYER_RECORD_SCHEMA if c in df.columns]
    missing = set(schema.PLAYER_RECORD_SCHEMA) - set(cols)
    if missing:
        log.warning("player records missing schema fields: %s", sorted(missing))

    records = json.loads(
        df[cols].replace({np.nan: None}).to_json(orient="records")
    )
    _write("players.json", records)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats_season": config.STATS_SEASON_LABEL,
        "salary_season": config.SALARY_SEASON_LABEL,
        "salary_cap": config.SALARY_CAP,
        "dollars_per_win": config.DOLLARS_PER_WIN,
        "default_dollars_per_win": config.DEFAULT_DOLLARS_PER_WIN,
        "regression": fits["regression"],
        "model_line": fits.get("model_line", {}),
        "regression_denominator_dependent": fits.get(
            "regression_denominator_dependent", False
        ),
        "model_line_floored": fits.get("model_line_floored", False),
        "composite_weights": config.COMPOSITE_WEIGHTS,
        "composite_fit": fits["composite_fit"],
        "war_constants": fits["war_constants"],
        "min_minutes": config.MIN_MINUTES_PLAYED,
        "player_count": len(df),
        "excluded_count": excluded,
    }
    _write("meta.json", meta)

    _write("teams.json", _team_rollup(df, contracts))


def _team_rollup(
    scoped: pd.DataFrame, contracts: pd.DataFrame | None = None
) -> list[dict[str, Any]]:
    """Aggregate by team (ADR-012). TWO POPULATIONS, DELIBERATELY.

    These fields do not all come from the same frame, and conflating them is the
    bug this signature exists to prevent.

    PAYROLL fields — `total_salary`, `roster_count`, `cap_space`,
    `apron_status` — are computed from `contracts`, the UNFILTERED contract set
    (~506 rows). Every dollar committed for the season counts against the cap:
    deep-bench players below the ADR-008 minutes floor, and dead money owed to
    players who are no longer on the roster. Cap space is a fact about a
    franchise's books, not about its rotation, so a filtered frame cannot
    produce it. Computing these off the scoped frame is what made Washington
    read $50.6M with $114M of room; the real figure is $186.5M and no room at
    all. (The same mistake was already fixed once in `_verify`, which is why the
    cap-arithmetic assertion there also takes the unfiltered frame.)

    VALUE fields — `total_war`, `total_surplus_naive`,
    `total_surplus_replacement`, `player_count`, `rotation_salary` — are
    computed from `scoped`, the post-scope-filter frame. These are legitimately
    "rotation players only": a 40-minute player has no stable per-minute metric,
    so his WAR and surplus are noise and must not be summed into a team's total.
    `rotation_salary` is published next to `total_salary` so the two populations
    are visible side by side rather than having to be inferred.

    `contracts` must carry `team` and `salary`. Passing None falls back to the
    scoped frame for everything and logs a warning — correct only for tests that
    do not care about payroll.
    """
    if contracts is None or "salary" not in getattr(contracts, "columns", []):
        log.warning(
            "team rollup has no unfiltered contract frame; payroll and cap "
            "space will reflect ROTATION players only and will read low"
        )
        contracts = scoped

    payroll = contracts.groupby("team")["salary"].sum()
    roster_counts = contracts.groupby("team")["salary"].size()

    rows = []
    # The contract set is the spine: all 30 teams have a payroll, but a team
    # could in principle have no player clear the minutes floor.
    for team in sorted(set(payroll.index) | set(scoped["team"].unique())):
        g = scoped[scoped["team"] == team]
        total_salary = float(payroll.get(team, 0.0))
        rows.append(
            {
                "team": team,
                # --- payroll: full contract set -----------------------------
                "total_salary": total_salary,
                "roster_count": int(roster_counts.get(team, 0)),
                "cap_space": float(config.SALARY_CAP - total_salary),
                "apron_status": _apron_status(total_salary),
                # --- value: scoped rotation players only ---------------------
                "rotation_salary": float(g["salary"].sum()),
                "total_war": float(g["war_blended"].sum()),
                "total_surplus_naive": float(g["surplus_naive"].sum()),
                "total_surplus_replacement": float(g["surplus_replacement"].sum()),
                "player_count": int(len(g)),
            }
        )
    return sorted(rows, key=lambda r: -r["total_surplus_replacement"])


def _apron_status(total: float) -> str:
    if total >= config.SECOND_APRON:
        return "second_apron"
    if total >= config.FIRST_APRON:
        return "first_apron"
    if total >= config.LUXURY_TAX:
        return "over_tax"
    if total >= config.SALARY_CAP:
        return "over_cap"
    return "under_cap"


def _write(name: str, payload: Any) -> None:
    path = config.DATA_PROCESSED / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("wrote %s (%.1f KB)", path.name, path.stat().st_size / 1024)


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="bypass scrape cache")
    parser.add_argument(
        "--verify", action="store_true", help="fail the build on assertion failure"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        from .crosswalk import build_crosswalk

        log.info("=== building identity crosswalk ===")
        crosswalk = build_crosswalk(force_refresh=args.refresh)

        log.info("=== ingesting sources ===")
        frames = _load_sources(args.refresh)

        log.info("=== joining ===")
        raw_contracts = frames["contracts"].rename(
            columns={
                f"salary_{config.SALARY_SEASON_LABEL.replace(chr(45), chr(95))}": "salary"
            }
        )

        df = _join(frames, crosswalk)

        df, excluded = _apply_scope(df)

        log.info("=== transforming ===")
        df, fits = _transform(df)

        log.info("=== verifying ===")
        problems = _verify(
            df, fits, strict=args.verify, raw_contracts=raw_contracts
        )

        log.info("=== mirroring headshots ===")
        df = _mirror_headshots(df)

        log.info("=== emitting ===")
        _emit(df, fits, excluded, contracts=raw_contracts)

        if problems:
            log.warning("build completed with %d verification warning(s)", len(problems))
        else:
            log.info("build clean: %d players", len(df))
        return 0

    except BuildFailure as exc:
        log.error("BUILD FAILED: %s", exc)
        return 1
    except SourceUnavailable as exc:
        log.error("ESSENTIAL SOURCE UNAVAILABLE: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
