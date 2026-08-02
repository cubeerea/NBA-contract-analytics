"""Basketball-Reference season Advanced stats -> STATS_SCHEMA.

WHY THIS MODULE IS MORE THAN `pd.read_html`
-------------------------------------------
The Advanced table is not one row per player. A player traded mid-season gets a
combined full-season line *plus* one line per team he appeared for. On the
2025-26 page that is 734 rows for 582 players. Feeding those rows straight into
the value model would double- and triple-count traded players in every league
aggregate and would make the regression population wrong.

Collapsing is not simply "keep the combined row" either. The combined row's team
cell holds a synthetic marker ("2TM"), and the dashboard filters by team, so a
player whose row says "2TM" would vanish from every team view. We therefore take
statistics from the combined row and the team from the player's *final* stint.

Two other BBRef-specific hazards are handled here:

1. Many BBRef tables are served inside HTML comments to defeat naive scrapers.
   The "advanced" table is currently live in the DOM, but the extraction helper
   checks comment blocks too so a change in BBRef's rendering degrades into a
   slower parse rather than a silent empty DataFrame.

2. Columns are addressed by their `data-stat` attribute, never by position.
   BBRef reorders and inserts columns between seasons; positional indexing
   produces plausible-looking garbage instead of an error.

All HTTP goes through etl.ratelimit — Sports-Reference bans clients that exceed
20 requests/minute for up to 24 hours.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup, Tag

from .. import config, ratelimit
from ..schema import STATS_SCHEMA

log = logging.getLogger(__name__)

ADVANCED_URL = "https://www.basketball-reference.com/leagues/NBA_{year}_advanced.html"
TABLE_ID = "advanced"
CACHE_NAMESPACE = "bbref_stats"

# BBRef marks a traded player's full-season combined line with a synthetic team
# code that counts the teams involved: "2TM", "3TM", "4TM". Much documentation
# (and older season pages) says "TOT" instead. Verified on the 2025-26 page,
# 2026-08-01: 66x "2TM", 5x "3TM", 1x "4TM", zero "TOT". Both spellings are
# accepted so that whichever convention BBRef ships next season, traded players
# are not silently dropped.
COMBINED_TEAM_RE = re.compile(r"^(?:TOT|\d+TM)$")

# data-stat attribute -> STATS_SCHEMA column. Anything BBRef emits that is not
# listed here (rebounding rates, shot-diet ratios, awards) is deliberately
# dropped: the value model does not consume it and carrying it invites
# downstream code to depend on columns the schema does not promise.
_COLUMN_MAP: dict[str, str] = {
    "name_display": "name",
    "age": "age",
    "team_name_abbr": "team",
    "pos": "position",
    "games": "games",
    "games_started": "games_started",
    "mp": "minutes",
    "per": "per",
    "ts_pct": "ts_pct",
    "usg_pct": "usg_pct",
    "ows": "ows",
    "dws": "dws",
    "ws": "ws",
    "ws_per_48": "ws_per_48",
    "obpm": "obpm",
    "dbpm": "dbpm",
    "bpm": "bpm",
    "vorp": "vorp",
}

_INT_COLUMNS = ("age", "games", "games_started", "minutes")
_FLOAT_COLUMNS = tuple(
    col for col, kind in STATS_SCHEMA.items() if kind == "float"
)

# The stint index lives in the name cell's `csk` (custom sort key) attribute:
#   combined row -> "Harden James--98"   (negative, so it sorts first)
#   first stint  -> "Harden James-1"
#   second stint -> "Harden James-2"
# Verified against James Harden's 2025-26 game log: the partial rows are emitted
# in chronological order, so the highest stint index is the final team.
_CSK_STINT_RE = re.compile(r"-(-?\d+)$")

_SLUG_HREF_RE = re.compile(r"/players/[a-z]/([a-z0-9]+)\.html")


# --------------------------------------------------------------------------
# Text and number parsing
# --------------------------------------------------------------------------


def _fix_mojibake(text: str) -> str:
    """Repair UTF-8 bytes that were decoded as Latin-1.

    BBRef serves UTF-8 without a charset in the Content-Type header, so
    `requests` falls back to ISO-8859-1 and "Şengün" arrives as "ĹengĂźn".
    The damage is losslessly reversible by re-encoding and decoding correctly.
    Fixing it here rather than in etl.ratelimit keeps the shared HTTP client
    free of source-specific assumptions; darko.py carries the same helper.
    """
    if text.isascii():
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text  # already correct, or damaged beyond a clean round-trip


def _to_float(raw: str | None) -> float:
    """Parse a BBRef numeric cell, returning NaN for blanks.

    BBRef renders leading-zero-less decimals (".594" for true shooting) and
    leaves cells empty for undefined rates — a player with zero field goal
    attempts has no TS%. Neither may raise.
    """
    if raw is None:
        return float("nan")
    text = raw.strip()
    if not text:
        return float("nan")
    try:
        return float(text)
    except ValueError:
        log.debug("unparseable numeric cell %r", raw)
        return float("nan")


def _to_int(raw: str | None) -> float:
    """Parse an integer cell, returning NaN for blanks.

    Returns a float so that a missing value can be represented at all; the
    column is cast to int64 once non-player rows have been filtered out.
    """
    value = _to_float(raw)
    return value if np.isnan(value) else float(int(value))


# --------------------------------------------------------------------------
# HTML extraction
# --------------------------------------------------------------------------


def _candidate_documents(html: str) -> Iterator[str]:
    """Yield the live document, then each HTML comment block.

    BBRef hides most secondary tables inside comments. Yielding lazily means the
    common case (table is live) costs exactly one BeautifulSoup parse.
    """
    yield html
    yield from re.findall(r"<!--(.*?)-->", html, re.S)


def _extract_table(html: str, table_id: str = TABLE_ID) -> Tag:
    """Find a BBRef table by id in the live DOM or inside a comment block."""
    for index, document in enumerate(_candidate_documents(html)):
        if f'id="{table_id}"' not in document:
            continue
        table = BeautifulSoup(document, "lxml").find("table", id=table_id)
        if table is not None:
            if index:
                log.info("table %r recovered from HTML comment block", table_id)
            return table
    raise ratelimit.SourceUnavailable(
        f"table id={table_id!r} not found in live DOM or comment blocks "
        f"({len(html)} bytes) — BBRef markup likely changed"
    )


def _row_cells(row: Tag) -> dict[str, Tag]:
    return {
        cell.get("data-stat"): cell
        for cell in row.find_all(["th", "td"])
        if cell.get("data-stat")
    }


def _slug_of(name_cell: Tag) -> str | None:
    """Extract the BBRef slug, preferring the explicit attribute over the href."""
    slug = name_cell.get("data-append-csv")
    if slug:
        return slug
    link = name_cell.find("a", href=True)
    if link:
        match = _SLUG_HREF_RE.search(link["href"])
        if match:
            return match.group(1)
    return None


def _stint_index(name_cell: Tag) -> int:
    """Chronological stint number; negative for the combined row."""
    match = _CSK_STINT_RE.search(name_cell.get("csk") or "")
    return int(match.group(1)) if match else 0


def _parse_advanced_html(html: str) -> list[dict[str, Any]]:
    """Parse the advanced table into raw records — one per *table row*.

    Rows without a player link (the trailing "League Average" row, repeated
    header rows) are dropped: they carry no slug and would poison every
    league-wide aggregate downstream.
    """
    table = _extract_table(html)
    body = table.tbody or table

    records: list[dict[str, Any]] = []
    skipped = 0
    for order, row in enumerate(body.find_all("tr")):
        classes = row.get("class") or []
        if "thead" in classes:
            continue

        cells = _row_cells(row)
        name_cell = cells.get("name_display")
        if name_cell is None:
            continue

        slug = _slug_of(name_cell)
        if slug is None:
            skipped += 1
            log.debug("skipping non-player row %r", name_cell.get_text(strip=True))
            continue

        record: dict[str, Any] = {"bbref_slug": slug}
        for stat, column in _COLUMN_MAP.items():
            cell = cells.get(stat)
            raw = cell.get_text(strip=True) if cell is not None else ""
            if column in _INT_COLUMNS:
                record[column] = _to_int(raw)
            elif column in _FLOAT_COLUMNS:
                record[column] = _to_float(raw)
            else:
                record[column] = _fix_mojibake(raw)

        record["_order"] = order
        record["_stint"] = _stint_index(name_cell)
        record["_is_combined"] = bool(COMBINED_TEAM_RE.match(record["team"]))
        records.append(record)

    if skipped:
        log.debug("dropped %d row(s) without a player link", skipped)
    log.info("parsed %d table rows", len(records))
    return records


# --------------------------------------------------------------------------
# Traded-player collapsing
# --------------------------------------------------------------------------


def _collapse_traded(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce multi-row traded players to one row per bbref_slug.

    Statistics come from the combined ("2TM"/"3TM"/"TOT") row because the value
    model needs full-season totals. `team` is overwritten with the final stint's
    team, because a dashboard that filters by team must not have players sitting
    under a bucket named "2TM".

    Final team is the partial row with the highest stint index from the `csk`
    sort key, falling back to document order. Both agree on the current page;
    keeping the fallback means a dropped `csk` attribute degrades to the
    ordering assumption rather than to a crash.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["bbref_slug"], []).append(record)

    collapsed: list[dict[str, Any]] = []
    traded = 0
    markers: dict[str, int] = {}

    for slug, rows in grouped.items():
        if len(rows) == 1 and not rows[0]["_is_combined"]:
            collapsed.append(rows[0])
            continue

        traded += 1
        combined = [row for row in rows if row["_is_combined"]]
        partials = [row for row in rows if not row["_is_combined"]]

        if combined:
            base = combined[0]
            markers[base["team"]] = markers.get(base["team"], 0) + 1
        else:
            # No combined line, yet multiple rows. Not observed on the 2025-26
            # page; if BBRef ever stops emitting it, the longest stint is a
            # defensible stand-in and is far better than emitting duplicates.
            base = max(partials, key=lambda row: (_nan_to_zero(row["minutes"]), row["_order"]))
            log.warning(
                "no combined row for %s across %d team rows; "
                "falling back to the largest stint (%s)",
                slug, len(rows), base["team"],
            )

        if partials:
            final = max(partials, key=lambda row: (row["_stint"], row["_order"]))
            # Only `team` is taken from the stint. `position` stays on the
            # combined line: it is BBRef's full-season primary position and so
            # matches the statistics we are keeping.
            base = {**base, "team": final["team"]}
        else:
            log.warning(
                "%s has a combined row (%s) but no per-team rows; "
                "team will remain a synthetic marker", slug, base["team"],
            )

        collapsed.append(base)

    if markers:
        log.info(
            "combined-row markers seen: %s",
            ", ".join(f"{k}={v}" for k, v in sorted(markers.items())),
        )
    log.info(
        "collapsed %d row(s) -> %d player(s); %d traded player(s) merged",
        len(records), len(collapsed), traded,
    )
    return collapsed


def _nan_to_zero(value: float) -> float:
    return 0.0 if value is None or (isinstance(value, float) and np.isnan(value)) else value


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Build the STATS_SCHEMA DataFrame, casting and validating."""
    frame = pd.DataFrame.from_records(records)

    # Integer columns are only meaningful for real player rows; any row still
    # missing games or minutes here is not a player we can score.
    incomplete = frame[list(_INT_COLUMNS)].isna().any(axis=1)
    if incomplete.any():
        log.warning("dropping %d row(s) with missing counting stats", int(incomplete.sum()))
        frame = frame[~incomplete]

    for column in _INT_COLUMNS:
        frame[column] = frame[column].astype("int64")
    for column in _FLOAT_COLUMNS:
        frame[column] = frame[column].astype("float64")

    frame = frame[list(STATS_SCHEMA)].reset_index(drop=True)

    duplicates = frame["bbref_slug"].duplicated()
    assert not duplicates.any(), (
        "duplicate bbref_slug after collapsing: "
        f"{sorted(frame.loc[duplicates, 'bbref_slug'])}"
    )
    assert list(frame.columns) == list(STATS_SCHEMA), "STATS_SCHEMA drift"
    return frame


def fetch_advanced_stats(
    season_end_year: int | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch one row per player of season Advanced stats.

    Args:
        season_end_year: BBRef season label (its *end* year — 2025-26 is 2026).
            Defaults to config.STATS_SEASON_END_YEAR.
        force_refresh: bypass the on-disk raw cache.

    Returns:
        DataFrame conforming to schema.STATS_SCHEMA, one row per bbref_slug,
        traded players collapsed onto their combined line with `team` set to
        their final team of the season.

    Raises:
        ratelimit.SourceUnavailable: the page could not be fetched, or the
            expected table is absent. BBRef stats are an essential source, so
            callers are expected to let this propagate.
    """
    year = season_end_year or config.STATS_SEASON_END_YEAR
    url = ADVANCED_URL.format(year=year)

    log.info("fetching BBRef advanced stats for %s season", year)
    html = ratelimit.fetch(url, namespace=CACHE_NAMESPACE, use_cache=not force_refresh)

    frame = _to_frame(_collapse_traded(_parse_advanced_html(html)))
    log.info("BBRef advanced stats: %d players, %d teams", len(frame), frame["team"].nunique())
    return frame
