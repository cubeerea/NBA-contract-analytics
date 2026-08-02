"""Basketball-Reference contract scraper: 2026-27 salary for every player.

WHY PER-TEAM PAGES, NOT THE INDEX
---------------------------------
https://www.basketball-reference.com/contracts/ looks like the obvious source,
but its `players` table is a *leaderboard* — only the 30 highest-paid players in
the league. The full roster-level data only exists on the 30 per-team pages
(/contracts/GSW.html etc.), so this module walks the team list off the index and
then fetches each team. That is 31 requests, comfortably inside the
Sports-Reference budget enforced by etl.ratelimit.

WHY EVERY TABLE LOOKUP GOES THROUGH find_table()
------------------------------------------------
Sports-Reference ships most secondary tables inside HTML comments and unwraps
them client-side with JavaScript. Which tables are commented is not stable — it
varies by page type and changes between site revisions. As of the last verified
crawl the team page's `contracts` table is in the live DOM while `payroll-notes`
is commented, and on the index page it is the reverse. Rather than encode that
(fragile) knowledge, find_table() checks the live DOM and then every comment
block, so either arrangement parses.

WHY data-stat AND NOT COLUMN POSITION
-------------------------------------
BBRef column order shifts between seasons (a year column falls off the front
each summer). The `data-stat` attributes are stable: player, age_today, y1..y6,
remain_gtd. `y1` is *relative* — it means "the first contract year shown" — so
this module reads the season labels out of the header's aria-label attributes
and asserts y1 == config.SALARY_SEASON_LABEL. When BBRef rolls the page over to
2027-28, that assertion fails loudly instead of silently mislabelling a season.

REAL-WORLD MESSINESS — see _parse_team_page() for the per-case handling.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Iterator

import pandas as pd
from bs4 import BeautifulSoup, Tag

from .. import config, ratelimit
from ..schema import CONTRACTS_SCHEMA

log = logging.getLogger(__name__)

INDEX_URL = "https://www.basketball-reference.com/contracts/"
TEAM_URL = "https://www.basketball-reference.com/contracts/{team}.html"
NAMESPACE = "bbref_contracts"

# Sanity bands. A team's 2026-27 commitments sit between roughly the cap floor
# and the second apron plus dead money; anything outside this is a parse bug,
# not a front office decision. config carries the real cap figures:
# cap 164.96M / tax 200.43M / second apron 221.69M.
TEAM_TOTAL_MIN = 100_000_000.0
TEAM_TOTAL_MAX = 250_000_000.0

# ~450 players on standard deals + ~46 two-ways + a dozen dead-money rows.
LEAGUE_COUNT_WARN = (400, 600)
LEAGUE_COUNT_FATAL = (300, 700)   # outside this, the parse is broken, not odd

_TEAM_HREF_RE = re.compile(r"^/contracts/([A-Z]{3})\.html$")
_SLUG_RE = re.compile(r"/players/[a-z]/([a-z0-9]+)\.html")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.S)


# ---------------------------------------------------------------------------
# Parsing primitives (pure, unit-tested)
# ---------------------------------------------------------------------------


def parse_salary(text: str | None) -> float:
    """Parse a BBRef money cell like "$62,587,158" into dollars.

    Returns NaN for an empty cell. Empty is meaningful on these pages and must
    not collapse to 0.0: it means "no salary commitment in this year", which is
    a different statement from "$0 guaranteed". Callers decide which they want.
    """
    if text is None:
        return float("nan")
    # BBRef occasionally emits non-breaking spaces inside money cells.
    cleaned = text.replace("\xa0", " ").strip()
    cleaned = cleaned.replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned or cleaned in {"-", "--", "—"}:
        return float("nan")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = float(cleaned)
    except ValueError:
        log.warning("unparseable salary cell %r", text)
        return float("nan")
    return -value if negative else value


def extract_comment_blocks(html: str) -> list[str]:
    """Return the inner text of every HTML comment in the document.

    Sports-Reference hides tables inside comments (see module docstring); this
    is the first half of getting at them.
    """
    return _COMMENT_RE.findall(html)


def find_table(html: str, table_id: str) -> Tag | None:
    """Locate a table by id in the live DOM *or* inside any comment block.

    Checks the live DOM first because that is the cheap path, then falls back to
    comment blocks, only parsing blocks that mention the id so a page with 70
    comments does not cost 70 BeautifulSoup constructions.
    """
    live = BeautifulSoup(html, "html.parser").find("table", id=table_id)
    if live is not None:
        return live

    needle = f'id="{table_id}"'
    for block in extract_comment_blocks(html):
        if needle not in block and table_id not in block:
            continue
        found = BeautifulSoup(block, "html.parser").find("table", id=table_id)
        if found is not None:
            log.debug("table #%s recovered from an HTML comment", table_id)
            return found
    return None


def repair_mojibake(text: str) -> str:
    """Undo a latin-1/UTF-8 double decode on an accented player name.

    BBRef serves UTF-8 but omits the charset from its Content-Type header, so a
    naive client falls back to ISO-8859-1 per RFC 2616 and "Nikola Jokić"
    arrives as "Nikola JokiÄ‡". ratelimit.fetch() now sniffs the encoding and
    prevents this at the source, so in the normal path this function is a
    no-op — it stays as a second line of defence for cache entries written
    before that fix and for any page where the sniff comes back wrong. Accented
    names are exactly the ones the crosswalk struggles to match, so a silent
    regression here is expensive.

    Guarded so a correctly-decoded name passes through untouched: this must
    never corrupt a name it cannot prove is broken.
    """
    if text.isascii():
        return text
    try:
        candidate = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    # Only accept the repair if it produces well-formed characters. Mojibake is
    # littered with C1 control codes (U+0080-U+009F); a clean result is not.
    if any(unicodedata.category(ch) == "Cc" for ch in candidate):
        return text
    return candidate


def _cell_text(row: Tag, data_stat: str) -> str:
    cell = row.find(attrs={"data-stat": data_stat})
    return cell.get_text(strip=True) if cell is not None else ""


def _cell_classes(row: Tag, data_stat: str) -> set[str]:
    cell = row.find(attrs={"data-stat": data_stat})
    return set(cell.get("class") or []) if cell is not None else set()


# ---------------------------------------------------------------------------
# Contract-type inference from the payroll notes table
# ---------------------------------------------------------------------------
# BBRef prints a free-text note list per player ("Signed 4-yr/$16M rookie scale
# contract July 3, 2023"). The vocabulary is small and templated, so the three
# CBA-suppressed types in config.SUPPRESSED_TYPES can be read off it directly
# rather than guessed at from the salary level — which matters, because
# transform/contract_type.py documents its own rookie_scale inference as
# "depends on the experience estimate" and its extension inference as never
# attempted at all. Reading the prose beats reverse-engineering the dollars.
#
# What is NOT inferable: "max" and "designated_veteran". No note ever says
# "max", and reconstructing it from the cap percentage would be a guess. Those
# players come out as "free_agent" or "extension", which is harmless: the only
# decision this feeds is membership in config.SUPPRESSED_CONTRACT_TYPES, and
# max/designated_veteran/free_agent/extension all sit on the same side of that
# line. Where the prose is ambiguous this returns None rather than guessing.
#
# The result is emitted as `contract_type_override`, not `contract_type`,
# because that is the extension point transform/contract_type.py actually
# reads (`classify_contracts(override_column=...)`); it overwrites any
# incoming `contract_type` column unconditionally, so a column by that name
# would be silently discarded.


def _infer_contract_type(notes: list[str], is_two_way: bool) -> str | None:
    joined = " ".join(notes).lower()
    if is_two_way or "two-way" in joined:
        return "two_way"

    # Order matters. A "rookie scale extension" is a negotiated (often max)
    # deal, not a rookie-scale contract, so extension wins the match.
    if "extension" in joined:
        return "extension"
    if "rookie scale" in joined:
        return "rookie_scale"
    if "minimum" in joined:
        return "minimum"
    # Exhibit 10 is by rule a one-year minimum contract.
    if "exhibit 10" in joined:
        return "minimum"
    # "Signed 3-yr/$50M contract July 6, 2026" — a negotiated signing, whether
    # an outside offer or a Bird-rights re-signing. Both are market priced. The
    # dollar figure is what distinguishes it from the bare "Signed 2-yr
    # contract" template used for camp/rest-of-season deals, which is ambiguous
    # and deliberately left unclassified.
    if re.search(r"signed\s+\d+-yr/\$[\d.]+m\s+contract", joined):
        return "free_agent"
    return None


def _parse_notes(html: str) -> tuple[dict[str, list[str]], set[str]]:
    """Return {slug: [note, ...]} and the set of slugs on two-way contracts.

    The notes table has no <tbody> and uses a `thead`-classed header row inside
    the table body, so it is walked row by row rather than via find("tbody").
    """
    table = find_table(html, "payroll-notes")
    if table is None:
        return {}, set()

    notes: dict[str, list[str]] = {}
    two_way: set[str] = set()
    for row in table.find_all("tr"):
        if "thead" in (row.get("class") or []):
            continue
        anchor = row.find("a", href=_SLUG_RE)
        if anchor is None:
            continue
        slug = _SLUG_RE.search(anchor["href"]).group(1)
        bullets = [li.get_text(" ", strip=True) for li in row.find_all("li")]
        if not bullets:
            cell = row.find(attrs={"data-stat": "notes"})
            bullets = [cell.get_text(" ", strip=True)] if cell else []
        notes[slug] = bullets
        if any("two-way" in b.lower() for b in bullets):
            two_way.add(slug)
    return notes, two_way


# ---------------------------------------------------------------------------
# Team page parsing
# ---------------------------------------------------------------------------


def _season_labels(table: Tag) -> dict[str, str]:
    """Map y1..y6 -> season label, read from the header's aria-label.

    The label lives on the *last* header row; the first row is the "Salary"
    over-header spanning the year columns.
    """
    header_rows = table.find("thead").find_all("tr")
    labels: dict[str, str] = {}
    for th in header_rows[-1].find_all("th"):
        stat = th.get("data-stat") or ""
        if re.fullmatch(r"y\d+", stat):
            labels[stat] = (th.get("aria-label") or th.get_text(strip=True)).strip()
    return labels


def _team_total(table: Tag) -> float:
    """The tfoot "Team Totals" 2026-27 figure, used to verify the row parse."""
    foot = table.find("tfoot")
    if foot is None:
        return float("nan")
    return parse_salary(_cell_text(foot, "y1"))


def _parse_team_page(html: str, team: str) -> tuple[list[dict[str, Any]], float]:
    """Parse one team page into player records plus BBRef's own team total.

    Messiness handled here, case by case:

    * Player / team options — the salary cell carries class `salary-pl` or
      `salary-tm`. The dollar figure is real and counts against the cap as
      shown, so it is kept as-is; the option status is surfaced separately in
      `salary_2026_27_option` because a team-option year may never be paid.
    * Early termination / mutual options — BBRef expresses these only in the
      notes prose, never as a cell class, so they are not distinguishable from
      the markup. They flow through as ordinary salary; Spotrac owns
      option_type for the cases that need precision.
    * Two-way contracts — appear as rows with a completely empty salary line
      (two-way money is not a cap charge). They are kept, with NaN salary and
      `is_two_way` True, so a downstream NaN reads as "not a cap hit" rather
      than "scrape dropped a player". 46 league-wide at last crawl.
    * Non-guaranteed money — never marked on the salary cell. The guarantee is
      expressed entirely by `remain_gtd`, which is why that column is carried
      through verbatim. An empty guarantee cell means genuinely $0 guaranteed
      (e.g. an unexercised player option), so it maps to 0.0, not NaN.
    * Dead money / waived / retired players — BBRef puts a blank
      `<tr class="thead">` spacer after the active roster and renders the
      remainder as `<tr class="partial_table">` with the name in <em>. These
      are cap charges, not players on the roster, so they are flagged
      `is_dead_money` and de-duplicated away in fetch_contracts() when the same
      player also appears active on another team.
    * Empty cells for years not under contract — `class="iz"`, parsed to NaN
      and simply omitted from the future_years dict.
    """
    table = find_table(html, "contracts")
    if table is None:
        raise ratelimit.SourceUnavailable(f"no #contracts table on {team} page")

    labels = _season_labels(table)
    y1_label = labels.get("y1", "")
    if y1_label != config.SALARY_SEASON_LABEL:
        # Season rollover guard: y1 is positional, so if BBRef advances the page
        # we would otherwise file 2027-28 money as 2026-27 without a murmur.
        raise ValueError(
            f"{team}: first salary column is {y1_label!r}, expected "
            f"{config.SALARY_SEASON_LABEL!r} — BBRef has rolled the season over "
            "and config.SALARY_SEASON_LABEL needs updating"
        )

    notes, two_way_slugs = _parse_notes(html)
    future_stats = sorted(
        (s for s in labels if s != "y1"), key=lambda s: int(s[1:])
    )

    records: list[dict[str, Any]] = []
    for row in table.find("tbody").find_all("tr"):
        classes = set(row.get("class") or [])
        if "thead" in classes:
            continue  # blank spacer separating the roster from dead money

        player_cell = row.find(attrs={"data-stat": "player"})
        anchor = player_cell.find("a", href=_SLUG_RE) if player_cell else None
        if anchor is None:
            # Every genuine row links the player page. No link means a layout
            # artefact; skipping is safer than inventing a slug.
            log.debug("%s: skipping row with no player link", team)
            continue
        slug = _SLUG_RE.search(anchor["href"]).group(1)
        name = repair_mojibake(anchor.get_text(strip=True))

        is_dead_money = "partial_table" in classes or player_cell.find("em") is not None

        y1_classes = _cell_classes(row, "y1")
        if "salary-pl" in y1_classes:
            option = "player"
        elif "salary-tm" in y1_classes:
            option = "team"
        else:
            option = ""

        future_years = {}
        for stat in future_stats:
            amount = parse_salary(_cell_text(row, stat))
            if amount == amount:  # not NaN
                future_years[labels[stat]] = amount

        guaranteed = parse_salary(_cell_text(row, "remain_gtd"))

        records.append(
            {
                "bbref_slug": slug,
                "name": name,
                "team": team,
                "salary_2026_27": parse_salary(_cell_text(row, "y1")),
                "guaranteed_remaining": 0.0 if guaranteed != guaranteed else guaranteed,
                "future_years": future_years,
                "contract_type_override": _infer_contract_type(
                    notes.get(slug, []), slug in two_way_slugs
                ),
                "is_two_way": slug in two_way_slugs,
                "is_dead_money": is_dead_money,
                "salary_2026_27_option": option,
            }
        )

    return records, _team_total(table)


def _team_abbreviations(index_html: str) -> list[str]:
    """Pull the 30 team abbreviations off the index page's team_summary table."""
    table = find_table(index_html, "team_summary")
    if table is None:
        raise ratelimit.SourceUnavailable("no #team_summary table on contracts index")

    seen: list[str] = []
    for anchor in table.find_all("a", href=True):
        match = _TEAM_HREF_RE.match(anchor["href"])
        if match and match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(records: list[dict[str, Any]], reported_totals: dict[str, float]) -> None:
    """Log every figure a human would want to eyeball, and shout about outliers.

    The strongest check available is not the plausibility band — it is BBRef's
    own tfoot "Team Totals" row. If the sum of the rows we parsed differs from
    the total BBRef printed, we dropped or double-counted a row, and we know it
    to the dollar. The $100M-$250M band is the backstop for the case where the
    tfoot itself is missing.
    """
    frame = pd.DataFrame(records)
    league_total = 0.0

    for team, group in frame.groupby("team", sort=True):
        parsed = float(group["salary_2026_27"].sum(skipna=True))
        league_total += parsed
        reported = reported_totals.get(team, float("nan"))
        n_players = len(group)
        n_dead = int(group["is_dead_money"].sum())
        n_two_way = int(group["is_two_way"].sum())

        log.info(
            "%s  2026-27 $%.1fM  rows=%d (two-way %d, dead money %d)",
            team,
            parsed / 1e6,
            n_players,
            n_two_way,
            n_dead,
        )

        if reported == reported and abs(parsed - reported) > 1.0:
            log.error(
                "%s: parsed total $%.0f != BBRef tfoot total $%.0f "
                "(difference $%.0f) — rows were dropped or double counted",
                team,
                parsed,
                reported,
                parsed - reported,
            )
        if not (TEAM_TOTAL_MIN <= parsed <= TEAM_TOTAL_MAX):
            log.warning(
                "%s: 2026-27 total $%.0f is outside the plausible "
                "$%.0fM-$%.0fM band (cap $%.0fM, second apron $%.0fM) — "
                "check the parse",
                team,
                parsed,
                TEAM_TOTAL_MIN / 1e6,
                TEAM_TOTAL_MAX / 1e6,
                config.SALARY_CAP / 1e6,
                config.SECOND_APRON / 1e6,
            )

    n = len(frame)
    log.info(
        "league totals: %d contract rows across %d teams, "
        "2026-27 commitments $%.1fM (%.1f x the $%.1fM cap)",
        n,
        frame["team"].nunique(),
        league_total / 1e6,
        league_total / config.SALARY_CAP,
        config.SALARY_CAP / 1e6,
    )

    if not (LEAGUE_COUNT_WARN[0] <= n <= LEAGUE_COUNT_WARN[1]):
        log.warning(
            "league player count %d is outside the expected range %s",
            n,
            LEAGUE_COUNT_WARN,
        )
    if not (LEAGUE_COUNT_FATAL[0] <= n <= LEAGUE_COUNT_FATAL[1]):
        raise ValueError(
            f"league player count {n} is outside {LEAGUE_COUNT_FATAL}; "
            "the parse has failed, refusing to emit a broken frame"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _iter_team_pages(
    teams: list[str], force_refresh: bool
) -> Iterator[tuple[str, str]]:
    for team in teams:
        html = ratelimit.fetch(
            TEAM_URL.format(team=team),
            namespace=NAMESPACE,
            use_cache=not force_refresh,
        )
        yield team, html


def fetch_contracts(force_refresh: bool = False) -> pd.DataFrame:
    """Scrape 2026-27 contract data for every player in the league.

    Returns a DataFrame conforming to schema.CONTRACTS_SCHEMA, one row per
    player, plus four documented extras that the schema columns cannot express:

        contract_type_override   read off BBRef's payroll notes; None where the
                                 prose is ambiguous. Named to feed
                                 transform.contract_type.classify_contracts(),
                                 whose default override_column this is.
        is_two_way               two-way deals carry no cap hit, so their NaN
                                 salary is a fact and not a scrape failure.
        is_dead_money            waived/retired players whose cap charge remains
                                 on a team's books.
        salary_2026_27_option    ""|"player"|"team" for the 2026-27 year.

    Raises ratelimit.SourceUnavailable if BBRef cannot be reached, or ValueError
    if what comes back does not look like a league's worth of contracts.
    """
    index_html = ratelimit.fetch(
        INDEX_URL, namespace=NAMESPACE, use_cache=not force_refresh
    )
    teams = _team_abbreviations(index_html)
    log.info("found %d team contract pages on the index", len(teams))
    if len(teams) != 30:
        raise ValueError(f"expected 30 team contract pages, found {len(teams)}: {teams}")

    records: list[dict[str, Any]] = []
    reported_totals: dict[str, float] = {}
    for team, html in _iter_team_pages(teams, force_refresh):
        team_records, reported = _parse_team_page(html, team)
        if not team_records:
            raise ValueError(f"{team}: parsed zero contract rows")
        records.extend(team_records)
        reported_totals[team] = reported

    _validate(records, reported_totals)

    frame = pd.DataFrame.from_records(records)

    # A waived player can appear twice: as dead money on the team that cut him
    # and as an active contract on the team that signed him. bbref_slug is the
    # pipeline's join key, so duplicates would fan out every downstream merge.
    # Keep the active row — it is the one that describes what the player is
    # actually being paid to play for in 2026-27.
    duplicated = frame["bbref_slug"].duplicated(keep=False)
    if duplicated.any():
        dupes = frame.loc[duplicated].sort_values("bbref_slug")
        for slug, group in dupes.groupby("bbref_slug"):
            log.info(
                "duplicate slug %s (%s): %s — keeping the active row",
                slug,
                group["name"].iloc[0],
                ", ".join(
                    f"{r.team}{' dead' if r.is_dead_money else ' active'} "
                    f"${0 if r.salary_2026_27 != r.salary_2026_27 else r.salary_2026_27:,.0f}"
                    for r in group.itertuples()
                ),
            )
        frame = (
            frame.sort_values(["bbref_slug", "is_dead_money"], kind="stable")
            .drop_duplicates("bbref_slug", keep="first")
            .reset_index(drop=True)
        )

    frame = frame.astype(
        {
            "bbref_slug": "str",
            "name": "str",
            "team": "str",
            "salary_2026_27": "float",
            "guaranteed_remaining": "float",
        }
    )
    # Schema columns first and in declaration order, extras after.
    ordered = list(CONTRACTS_SCHEMA) + [
        c for c in frame.columns if c not in CONTRACTS_SCHEMA
    ]
    frame = frame[ordered]

    missing = set(CONTRACTS_SCHEMA) - set(frame.columns)
    if missing:
        raise ValueError(f"output is missing schema columns: {sorted(missing)}")

    log.info(
        "fetch_contracts: %d unique players, %d with a 2026-27 cap hit, "
        "%d two-way, %d dead money",
        len(frame),
        int(frame["salary_2026_27"].notna().sum()),
        int(frame["is_two_way"].sum()),
        int(frame["is_dead_money"].sum()),
    )
    return frame


if __name__ == "__main__":  # pragma: no cover - manual smoke run
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s"
    )
    df = fetch_contracts()
    pd.set_option("display.width", 160)
    print(df.head(15))
    print(df.dtypes)
