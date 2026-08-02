"""Spotrac adapter — contract structure enrichment (ADR-003).

ENRICHMENT SOURCE. Every function here is allowed to fail: the orchestrator
catches and degrades. Nothing in the core value model may depend on Spotrac
being reachable. See README "Fail-soft ingestion".

Two capabilities:

1. `fetch_contract_details()` — signing year, term, total value, and derived
   contract type for the largest ~100 contracts in the league.

   Spotrac's contracts table is JS-paginated and serves 100 rows to a plain
   HTTP client regardless of the `limit-N` path segment (verified: limit-2000
   returns exactly 100). Rather than fight that, we lean into it — the top 100
   contracts are precisely where structural detail matters, because that is
   where max deals, no-trade clauses, and trade kickers live. Minimum and
   rookie-scale deals have no interesting structure and are classified
   heuristically from salary alone in transform/contract_type.py.

2. `fetch_team_cap_totals()` — team-level cap allocations, used as an
   INDEPENDENT CROSS-CHECK on the Basketball-Reference contract parser. Two
   sources agreeing on 30 team payrolls is strong evidence neither parser ate a
   comma; a single-source total could be confidently wrong.
"""

from __future__ import annotations

import logging
import re

import pandas as pd
from bs4 import BeautifulSoup

from .. import config
from ..ratelimit import SourceUnavailable, fetch

log = logging.getLogger(__name__)

CONTRACTS_URL = "https://www.spotrac.com/nba/contracts/"
TEAM_CAP_URL = "https://www.spotrac.com/nba/cap/_/year/{year}/"

_MONEY_RE = re.compile(r"[^0-9.]")


def _money(text: str) -> float:
    """Parse '$313,933,410' -> 313933410.0. Returns NaN on anything unparseable."""
    cleaned = _MONEY_RE.sub("", text or "")
    try:
        return float(cleaned) if cleaned else float("nan")
    except ValueError:
        return float("nan")


def _dedupe_abbr(text: str) -> str:
    """Spotrac renders team cells as 'BOSBOS' — the responsive layout emits the
    abbreviation twice (desktop + mobile spans) and get_text concatenates them.
    Collapse an exactly-doubled string back to one copy.
    """
    text = (text or "").strip()
    half = len(text) // 2
    if len(text) % 2 == 0 and text[:half] == text[half:]:
        return text[:half]
    return text


def _first_table(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise SourceUnavailable("no table found in Spotrac response")
    return table


def _rows(table) -> list[list[str]]:
    body = table.find("tbody")
    if body is None:
        return []
    out = []
    for tr in body.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if cells:
            out.append(cells)
    return out


def fetch_contract_details(force_refresh: bool = False) -> pd.DataFrame:
    """Top ~100 contracts with structure. Conforms to SPOTRAC_SCHEMA (partial).

    Emits player NAME rather than bbref_slug — Spotrac has no BBRef identifier,
    so name resolution goes through etl.crosswalk like every other name-keyed
    source. Emitting a half-resolved key here would put identity logic in two
    places.
    """
    html = fetch(CONTRACTS_URL, namespace="spotrac", use_cache=not force_refresh)
    table = _first_table(html)
    rows = _rows(table)

    if not rows:
        raise SourceUnavailable("Spotrac contracts table parsed to zero rows")

    records = []
    for cells in rows:
        # Columns: Player, Pos, Team, AgeAtSigning, Start, End, Yrs, Value, AAV
        if len(cells) < 9:
            continue
        name, pos, team, age_signed, start, end, yrs, value, aav = cells[:9]

        try:
            start_year = int(start)
            end_year = int(end)
            years = int(yrs)
        except ValueError:
            log.debug("skipping unparseable Spotrac row: %s", cells[:7])
            continue

        total_value = _money(value)
        records.append(
            {
                "name": name.strip(),
                "position_spotrac": pos.strip(),
                "team_spotrac": _dedupe_abbr(team),
                "age_at_signing": int(age_signed) if age_signed.isdigit() else None,
                "signed_year": start_year,
                "contract_end_year": end_year,
                "contract_years": years,
                "total_value": total_value,
                "aav": _money(aav),
                "years_remaining": max(0, end_year - config.STATS_SEASON_END_YEAR),
                "contract_type": _infer_type(total_value, years, aav),
            }
        )

    df = pd.DataFrame(records)
    log.info("Spotrac: %d contracts parsed", len(df))
    return df


def _infer_type(total_value: float, years: int, aav_text: str | float) -> str:
    """Classify from contract magnitude.

    HEURISTIC and deliberately coarse. Spotrac's table does not expose option
    structure, no-trade clauses, or trade kickers without per-player page
    fetches (~500 additional requests), which is not worth the rate-limit
    budget for an enrichment source. transform/contract_type.py owns the
    authoritative classification; this only supplies a hint where the contract
    is large enough for the signal to be unambiguous.
    """
    aav = _money(aav_text) if isinstance(aav_text, str) else aav_text
    if pd.isna(aav) or pd.isna(total_value):
        return "unknown"

    cap_share = aav / config.SALARY_CAP

    # The 2023 CBA caps individual salary at 25/30/35% of the cap by service
    # time. An AAV at or above ~30% can only be a max or designated-veteran
    # deal; nothing else reaches that number.
    if cap_share >= 0.30:
        return "designated_veteran" if years >= 5 else "max"
    if cap_share >= 0.23:
        return "max"
    if cap_share >= 0.09:
        return "free_agent"
    return "unknown"


def fetch_team_cap_totals(
    year: int | None = None, force_refresh: bool = False
) -> pd.DataFrame:
    """Team-level cap allocations — independent cross-check on the BBRef parser.

    Returns one row per team with Spotrac's total cap allocation and dead cap.
    build.py compares these against BBRef-derived team totals; a systematic
    divergence means one of the two parsers is wrong, which is far more useful
    than either number alone.
    """
    year = year or config.STATS_SEASON_END_YEAR
    html = fetch(
        TEAM_CAP_URL.format(year=year), namespace="spotrac", use_cache=not force_refresh
    )
    table = _first_table(html)

    records = []
    for cells in _rows(table):
        # Rank, Team, PlayersActive, AvgAge, TotalCap, CapSpace, CapSpaceProj,
        # Active, ActiveTop3, DeadCap
        if len(cells) < 10 or not cells[0].isdigit():
            continue
        records.append(
            {
                "team_spotrac": _dedupe_abbr(cells[1]),
                "active_players": int(cells[2]) if cells[2].isdigit() else None,
                "avg_age": float(cells[3]) if cells[3] else None,
                "total_cap": _money(cells[4]),
                "cap_space": _money(cells[5]),
                "dead_cap": _money(cells[9]),
            }
        )

    df = pd.DataFrame(records)
    log.info("Spotrac: %d team cap rows", len(df))
    return df
