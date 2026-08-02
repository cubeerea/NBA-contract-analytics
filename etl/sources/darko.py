"""DARKO player projections -> DARKO_SCHEMA.

DARKO (Daily Adjusted and Regressed Kalman Optimized projections) is Kostya
Medvedovsky and Andrew Patton's public plus-minus model. It contributes the
per-possession, on-off-informed view of impact that the box-score metrics
(BPM/VORP, Win Shares) cannot see, and it carries 25% of the composite score.

WHY THIS SOURCE IS ALLOWED TO FAIL
----------------------------------
DARKO is published as a Google Sheet on the authors' goodwill. It has no SLA,
no versioned API, and the tab layout is theirs to change. The nightly build must
not be hostage to that, so every failure path here raises
ratelimit.SourceUnavailable and the orchestrator degrades to the box-score
metrics rather than shipping nothing. See README "Fail-soft ingestion".

WHY A GOOGLE SHEET AND NOT darko.app
------------------------------------
Both were checked on 2026-08-01. The sheet publishes the full 530-player table
as CSV with no authentication. darko.app renders the same data as server-side
Svelte HTML paginated at 50 players per page, exposes no JSON or CSV endpoint
(only /api/img/* for headshots), and its displayed values are a differently
rounded snapshot. Scraping it would mean eleven requests against brittle markup
for worse data, so the two Google CSV endpoints back each other up instead.

COLUMN MAPPING (verified 2026-08-01)
------------------------------------
The sheet's headers do not match DARKO_SCHEMA, so they are mapped:

    "NBA ID"          -> nba_player_id
    "Player Name"     -> name
    "DPM"             -> dpm
    "Offensive DPM"   -> o_dpm
    "Defensive DPM"   -> d_dpm
    "Box Only O-DPM"  +
    "Box Only D-DPM"  -> box_dpm

There is no single box-only column. Summing the two halves is the consistent
construction: the sheet's own total obeys DPM == Offensive DPM + Defensive DPM
across all 530 rows (max residual 0.01, i.e. rounding), so the box-only halves
compose the same way.

Names are emitted as published. Resolution to bbref_slug is etl/crosswalk.py's
job — deliberately not duplicated here, so there is exactly one place in the
pipeline where name-matching rules live.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from .. import ratelimit
from ..schema import DARKO_SCHEMA

log = logging.getLogger(__name__)

SHEET_ID = "1mhwOLqPu2F9026EQiVxFPIN1t9RGafGpl-dokaIsm9c"
CACHE_NAMESPACE = "darko"

# Two independent Google export backends. `export` is the primary — it emits
# unquoted, correctly typed CSV. `gviz` is served by a different subsystem and
# has historically stayed up through outages of the other, which is the only
# reason to keep a second URL rather than retrying the first harder.
ENDPOINTS: tuple[str, ...] = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv",
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv",
)

# Sheet header -> DARKO_SCHEMA column.
_COLUMN_MAP: dict[str, str] = {
    "NBA ID": "nba_player_id",
    "Player Name": "name",
    "DPM": "dpm",
    "Offensive DPM": "o_dpm",
    "Defensive DPM": "d_dpm",
}
_BOX_COMPONENTS = ("Box Only O-DPM", "Box Only D-DPM")

# A parse that yields a handful of rows means we fetched a login page, an error
# page, or an empty tab. Better to fail the source than to silently drop 90% of
# the league's DPM and let the composite score quietly change meaning.
MIN_EXPECTED_ROWS = 200


def _fix_mojibake(text: str) -> str:
    """Repair UTF-8 bytes that were decoded as Latin-1.

    Google serves UTF-8 but `requests` guesses ISO-8859-1 when the charset is
    absent, turning "Jakučionis" into "JakuÄ\x8dionis". Crosswalk matching is on
    normalized names, so a mangled name is a silently unjoined player. Fixed
    here rather than in etl.ratelimit to keep the shared HTTP client free of
    source-specific assumptions; bbref_stats.py carries the same helper.
    """
    if text.isascii():
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _parse_csv(text: str) -> pd.DataFrame:
    """Map a raw DARKO CSV export onto DARKO_SCHEMA.

    Raises:
        ValueError: the CSV is missing expected columns or is implausibly short.
    """
    raw = pd.read_csv(io.StringIO(text))

    missing = [
        header
        for header in (*_COLUMN_MAP, *_BOX_COMPONENTS)
        if header not in raw.columns
    ]
    if missing:
        raise ValueError(
            f"DARKO sheet is missing expected column(s) {missing}; "
            f"got {list(raw.columns)}"
        )
    if len(raw) < MIN_EXPECTED_ROWS:
        raise ValueError(
            f"DARKO sheet returned only {len(raw)} rows "
            f"(expected >= {MIN_EXPECTED_ROWS}); likely an error page"
        )

    frame = raw[list(_COLUMN_MAP)].rename(columns=_COLUMN_MAP)
    frame["box_dpm"] = (
        pd.to_numeric(raw[_BOX_COMPONENTS[0]], errors="coerce")
        + pd.to_numeric(raw[_BOX_COMPONENTS[1]], errors="coerce")
    )

    frame["name"] = frame["name"].astype("string").fillna("").map(_fix_mojibake).str.strip()
    frame["nba_player_id"] = pd.to_numeric(
        frame["nba_player_id"], errors="coerce"
    ).astype("Int64")
    for column in ("dpm", "o_dpm", "d_dpm", "box_dpm"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")

    frame = frame[frame["name"] != ""]

    duplicates = frame["name"].duplicated(keep=False)
    if duplicates.any():
        # Not fatal — the crosswalk resolves on name, so it needs to know.
        log.warning(
            "DARKO has duplicate player names: %s",
            sorted(set(frame.loc[duplicates, "name"])),
        )

    frame = frame[list(DARKO_SCHEMA)].reset_index(drop=True)
    assert list(frame.columns) == list(DARKO_SCHEMA), "DARKO_SCHEMA drift"
    return frame


def fetch_darko(force_refresh: bool = False) -> pd.DataFrame:
    """Fetch current DARKO per-player projections.

    Args:
        force_refresh: bypass the on-disk raw cache.

    Returns:
        DataFrame conforming to schema.DARKO_SCHEMA, one row per player, keyed
        by name (resolve to bbref_slug via etl/crosswalk.py).

    Raises:
        ratelimit.SourceUnavailable: every endpoint failed or returned
            unusable data. DARKO is an ENRICHMENT source — callers should catch
            this and continue without DPM rather than fail the build.
    """
    errors: list[str] = []

    for url in ENDPOINTS:
        try:
            text = ratelimit.fetch(
                url, namespace=CACHE_NAMESPACE, use_cache=not force_refresh
            )
            frame = _parse_csv(text)
        except ratelimit.SourceUnavailable as exc:
            log.warning("DARKO endpoint unreachable (%s): %s", exc, url)
            errors.append(f"{url}: {exc}")
            continue
        except (ValueError, pd.errors.ParserError) as exc:
            log.warning("DARKO endpoint returned unusable data (%s): %s", exc, url)
            errors.append(f"{url}: {exc}")
            continue

        log.info("DARKO: %d players from %s", len(frame), url)
        return frame

    raise ratelimit.SourceUnavailable(
        "DARKO unavailable from all endpoints — " + "; ".join(errors)
    )
