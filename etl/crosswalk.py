"""Canonical identity spine: Basketball-Reference slug -> NBA.com player id.

Every source in this pipeline names players differently. BBRef writes
"Nikola Jokić" and keys him as "jokicni01"; NBA.com writes "Nikola Jokic" and
keys him as 203999; DARKO ships names only. Joining any two of those on a name
string fails on accents, suffixes, apostrophes, nicknames, and — worst, because
it fails *silently* — on the ~50 normalized names in NBA history that belong to
more than one human being.

This module resolves identity exactly once, so that failure mode lives in one
auditable place instead of being rediscovered in every join downstream. The
output is the crosswalk table (CROSSWALK_SCHEMA): BBRef slug is the spine, the
NBA player id rides along solely so a headshot URL can be constructed.

Two hard constraints shape the implementation:

* ADR-010 — NBA ids come from ``nba_api.stats.static.players``, which reads a
  bundled local file and issues no HTTP request. Nothing from
  ``nba_api.stats.endpoints`` may ever be imported here; stats.nba.com blocks
  datacenter IPs and would break the GitHub Actions build.
* The bundled static table is a snapshot. Players who debuted after it was cut
  (two-way and 10-day signings, mostly) have no id to map to at all. That is a
  real, permanent miss, not a bug in the matcher — so unmatched players are
  logged by name at WARNING. A silent 90% match rate is the failure mode this
  module exists to make visible.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from collections import defaultdict
from typing import Iterable, Sequence

import pandas as pd
from bs4 import BeautifulSoup
from nba_api.stats.static import players as nba_static  # OFFLINE ONLY — ADR-010

from . import config, ratelimit
from .schema import CROSSWALK_SCHEMA

log = logging.getLogger(__name__)

# The season advanced table is the roster spine: one row per player who logged
# an NBA minute this season, each cell carrying the player's slug in its href.
# It is the same page etl/sources/bbref_stats.py pulls, so in a full build this
# costs zero extra requests — ratelimit's on-disk cache serves it.
BBREF_ROSTER_URL = (
    f"https://www.basketball-reference.com/leagues/"
    f"NBA_{config.STATS_SEASON_END_YEAR}_advanced.html"
)
BBREF_ROSTER_TABLE_ID = "advanced"

CACHE_PATH = config.DATA_PROCESSED / "crosswalk.json"

# --------------------------------------------------------------------------
# Manual overrides
# --------------------------------------------------------------------------
# bbref_slug -> nba_player_id, for players the matcher cannot resolve or can
# only resolve by coin flip. Every entry needs a reason; an unexplained
# override is indistinguishable from a typo. Entries are applied before any
# automatic pass and are asserted against the static table at build time, so a
# stale one fails loudly rather than mapping to a nonexistent id.

OVERRIDES: dict[str, int] = {
    # Jeenathan Williams goes by "Nate" on both sites, which collides him with
    # Nate Williams (1971-77, id 78561). Both records are flagged inactive in
    # the bundled snapshot, so the is_active tie-break cannot separate them and
    # the matcher would fall through to its "prefer the modern id" heuristic.
    # BBRef's slug (willi + je, from Jeenathan) confirms which one is meant.
    "willije02": 1631466,
    # BBRef shortened Trevon Scott to the nickname he actually uses, "Tre".
    # The first-name-prefix pass gets this right, but "Scott" is a common
    # surname and a wrong prefix match there would be invisible, so pin it.
    # Age on the BBRef row (29) agrees with Trevon Scott's 1997 birth year.
    "scotttr01": 1630286,
}

# --------------------------------------------------------------------------
# Name normalization
# --------------------------------------------------------------------------

# Characters NFKD will not decompose because they are atomic letters rather
# than letter-plus-diacritic. Without these, "Dønčić"-style names survive
# normalization with a non-ASCII character still embedded and never match.
# The Cyrillic pair is real: BBRef spells Egor Dëmin with U+0451, which NFKD
# folds to Cyrillic е (U+0435), not Latin e.
_TRANSLITERATIONS = str.maketrans(
    {
        "ø": "o", "Ø": "O",
        "đ": "d", "Đ": "D",
        "ð": "d", "Ð": "D",
        "ł": "l", "Ł": "L",
        "þ": "th", "Þ": "Th",
        "ß": "ss",
        "æ": "ae", "Æ": "Ae",
        "œ": "oe", "Œ": "Oe",
        "ё": "e", "Ё": "E",
        "е": "e", "Е": "E",
    }
)

# Generational suffixes. Sources disagree constantly: BBRef says "Jaren Jackson
# Jr.", NBA.com sometimes says "Jaren Jackson Jr." and sometimes just the bare
# name. Stripping them makes the join work; the stripped token is kept as a
# separate signal for disambiguation (see _suffix_of).
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

_SLUG_RE = re.compile(r"^(?P<stem>[a-z]+)(?P<ordinal>\d{2})$")


def normalize_name(name: str) -> str:
    """Fold a player name to its match key: lowercase, unaccented, suffix-free.

    "Kristaps Porziņģis" -> "kristaps porzingis"
    "De'Aaron Fox"       -> "deaaron fox"
    "Jaren Jackson Jr."  -> "jaren jackson"

    Hyphens become spaces rather than being deleted, so "Karl-Anthony Towns"
    and "Karl Anthony Towns" land on the same key; apostrophes and periods are
    deleted, so "De'Aaron"/"DeAaron" and "R.J."/"RJ" do too.
    """
    if not name:
        return ""

    folded = name.translate(_TRANSLITERATIONS)
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower()

    folded = re.sub(r"[.'`‘’]", "", folded)
    folded = re.sub(r"[^a-z0-9]+", " ", folded)

    parts = folded.split()
    # Guard against eating the whole name: a lone "V" is a surname somewhere.
    while len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def _suffix_of(name: str) -> str:
    """Return the generational suffix of a name ("jr", "iii", ...) or ""."""
    folded = re.sub(r"[^a-z ]+", " ", name.lower().replace(".", ""))
    parts = folded.split()
    if len(parts) > 1 and parts[-1] in _SUFFIXES:
        return parts[-1]
    return ""


def _slug_stem(first_name: str, last_name: str) -> str:
    """Reproduce BBRef's slug convention: last name[:5] + first name[:2].

    "Nicolas Claxton" -> "claxtni", matching slug "claxtni01".

    This is the single most useful signal in the module, because the slug is
    built from the player's *legal* name while the displayed name may be a
    nickname. It is what connects BBRef's "Ron Holland" to NBA.com's "Ronald
    Holland II" without any hand-written nickname table.
    """
    last = re.sub(r"[^a-z]", "", normalize_name(last_name))
    first = re.sub(r"[^a-z]", "", normalize_name(first_name))
    return last[:5] + first[:2]


def _repair_mojibake(html: str) -> str:
    """Undo UTF-8 text that was decoded as Latin-1 somewhere upstream.

    BBRef serves UTF-8 without declaring a charset in the Content-Type header,
    so requests falls back to ISO-8859-1 and "Şengün" arrives (and is cached)
    as "Ã\x9eengÃ¼n". Re-encoding round-trips it back. Correctly decoded text
    either raises on the encode (any character above U+00FF) or fails to decode
    as UTF-8, so a well-formed page is returned untouched.
    """
    try:
        return html.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return html


def _find_table(html: str, table_id: str) -> BeautifulSoup | None:
    """Locate a BBRef table, including ones buried in HTML comments.

    BBRef ships most secondary tables commented out and un-comments them in
    JavaScript, so a plain parse finds nothing. Every BBRef parser in this
    project has to handle both cases.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id=table_id)
    if table is not None:
        return table

    for block in re.findall(r"<!--(.*?)-->", html, re.S):
        if f'id="{table_id}"' not in block:
            continue
        table = BeautifulSoup(block, "lxml").find("table", id=table_id)
        if table is not None:
            log.debug("table %s recovered from an HTML comment", table_id)
            return table
    return None


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


def _fetch_bbref_roster(use_cache: bool = True) -> list[tuple[str, str]]:
    """Return [(bbref_slug, display_name)] for the season, deduplicated.

    Traded players appear once per team plus a combined row; all rows carry the
    same slug, so first-wins deduplication is enough here.
    """
    html = _repair_mojibake(
        ratelimit.fetch(BBREF_ROSTER_URL, namespace="crosswalk", use_cache=use_cache)
    )
    table = _find_table(html, BBREF_ROSTER_TABLE_ID)
    if table is None:
        raise ratelimit.SourceUnavailable(
            f"table #{BBREF_ROSTER_TABLE_ID} not found at {BBREF_ROSTER_URL}"
        )

    roster: dict[str, str] = {}
    for row in table.find("tbody").find_all("tr"):
        cell = row.find(attrs={"data-stat": "name_display"})
        link = cell.find("a") if cell else None
        if link is None:
            continue
        match = re.search(r"/players/./([a-z0-9]+)\.html", link.get("href", ""))
        if match is None:
            continue
        roster.setdefault(match.group(1), link.get_text(strip=True))

    log.info("BBRef roster: %d players", len(roster))
    return sorted(roster.items())


def _load_nba_players() -> list[dict]:
    """Offline NBA player table. ADR-010: no network call happens here."""
    people = nba_static.get_players()
    log.info(
        "nba_api static table: %d players (%d active)",
        len(people),
        sum(1 for p in people if p["is_active"]),
    )
    return people


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def _disambiguate(
    slug: str, bbref_name: str, candidates: Sequence[dict]
) -> dict | None:
    """Pick one NBA record from several sharing a normalized name.

    Never returns "the first one". Signals are applied in decreasing order of
    trustworthiness, and the weakest one warns, because that is the case a
    human should eventually look at.
    """
    pool = list(candidates)

    # 1. Suffix. "Jabari Smith Jr." vs his father "Jabari Smith" — decisive
    #    when BBRef supplies a suffix and exactly one candidate carries it.
    suffix = _suffix_of(bbref_name)
    if suffix:
        with_suffix = [c for c in pool if _suffix_of(c["full_name"]) == suffix]
        if len(with_suffix) == 1:
            return with_suffix[0]
        if with_suffix:
            pool = with_suffix

    # 2. Activity. The roster comes from the current season, so an inactive
    #    namesake is by definition the wrong person.
    active = [c for c in pool if c["is_active"]]
    if len(active) == 1:
        return active[0]
    if active:
        pool = active

    # 3. Slug stem, which encodes the legal first name BBRef filed him under.
    stem_match = _SLUG_RE.match(slug)
    if stem_match:
        stem = stem_match.group("stem")
        by_stem = [
            c for c in pool if _slug_stem(c["first_name"], c["last_name"]) == stem
        ]
        if len(by_stem) == 1:
            return by_stem[0]
        if by_stem:
            pool = by_stem

    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]

    # 4. Last resort. NBA ids are issued in career order, and every player on
    #    this roster is playing right now, so the largest id is the modern one.
    #    Weak enough to deserve an override entry — say so.
    choice = max(pool, key=lambda c: c["id"])
    log.warning(
        "ambiguous name %r (%s): %s — taking the highest id (%d) as the "
        "contemporary player; add an OVERRIDES entry if that is wrong",
        bbref_name,
        slug,
        [(c["id"], c["full_name"], c["is_active"]) for c in pool],
        choice["id"],
    )
    return choice


def _first_names_compatible(bbref_first: str, nba_first: str) -> bool:
    """True if two first names plausibly belong to the same person.

    Equal, or one is a prefix of the other with at least three characters:
    Ron/Ronald and Tre/Trevon pass; Chaney/Chris and Darius/Damone do not.
    Three characters is the floor that keeps "Ja"/"Jalen" from colliding with
    "Ja"/"Jarrett".
    """
    if bbref_first == nba_first:
        return True
    shorter, longer = sorted((bbref_first, nba_first), key=len)
    return len(shorter) >= 3 and longer.startswith(shorter)


def _plausible_same_person(bbref_name: str, candidate: dict) -> bool:
    """Guard for the slug-stem pass.

    The stem is only seven characters, so it collides (Chaney Johnson and
    Chris Johnson both stem to "johnsch"). Require the surname to agree exactly
    and the first names to be prefix-compatible before believing it.
    """
    left = normalize_name(bbref_name).split()
    right = normalize_name(candidate["full_name"]).split()
    if not left or not right:
        return False
    if left[-1] != right[-1]:
        return False
    return _first_names_compatible(left[0], right[0])


def match_roster(
    roster: Iterable[tuple[str, str]], nba_people: Sequence[dict]
) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """Resolve [(slug, name)] against the NBA static table.

    Returns ({slug: nba_record}, [(slug, name) that could not be resolved]).
    Pure function over its inputs — no I/O — so it is testable with fixtures.
    """
    by_name: dict[str, list[dict]] = defaultdict(list)
    by_stem: dict[str, list[dict]] = defaultdict(list)
    by_id: dict[int, dict] = {}
    for person in nba_people:
        by_name[normalize_name(person["full_name"])].append(person)
        by_stem[_slug_stem(person["first_name"], person["last_name"])].append(person)
        by_id[person["id"]] = person

    active_names = [
        normalize_name(p["full_name"]) for p in nba_people if p["is_active"]
    ]

    resolved: dict[str, dict] = {}
    unmatched: list[tuple[str, str]] = []
    tally: dict[str, int] = defaultdict(int)

    for slug, name in roster:
        # Pass 0 — hand-maintained overrides win outright.
        if slug in OVERRIDES:
            person = by_id.get(OVERRIDES[slug])
            if person is None:
                log.error(
                    "OVERRIDES[%r] = %d is not in the nba_api static table; "
                    "the entry is stale",
                    slug,
                    OVERRIDES[slug],
                )
            else:
                resolved[slug] = person
                tally["override"] += 1
                continue

        normalized = normalize_name(name)

        # Pass 1/2 — exact normalized name, unique or disambiguated.
        candidates = by_name.get(normalized, [])
        if len(candidates) == 1:
            resolved[slug] = candidates[0]
            tally["exact"] += 1
            continue
        if len(candidates) > 1:
            choice = _disambiguate(slug, name, candidates)
            if choice is not None:
                resolved[slug] = choice
                tally["collision"] += 1
                continue

        # Pass 3 — slug stem, guarded. Catches nicknames and legal-name
        # disagreements: "Ron Holland" -> "Ronald Holland II".
        stem_match = _SLUG_RE.match(slug)
        if stem_match:
            stem_candidates = [
                c
                for c in by_stem.get(stem_match.group("stem"), [])
                if _plausible_same_person(name, c)
            ]
            if len(stem_candidates) == 1:
                resolved[slug] = stem_candidates[0]
                tally["slug_stem"] += 1
                log.info(
                    "%s: %r matched %r via slug stem",
                    slug,
                    name,
                    stem_candidates[0]["full_name"],
                )
                continue
            if len(stem_candidates) > 1:
                choice = _disambiguate(slug, name, stem_candidates)
                if choice is not None:
                    resolved[slug] = choice
                    tally["slug_stem"] += 1
                    continue

        # Pass 4 — near-miss spelling, active players only. Deliberately tight:
        # a loose fuzzy pass on this data invents matches (it will happily read
        # "Josh Oduro" as "Josh Boone").
        close = difflib.get_close_matches(normalized, active_names, n=1, cutoff=0.87)
        if close:
            fuzzy = [
                c
                for c in by_name[close[0]]
                if c["is_active"] and _plausible_same_person(name, c)
            ]
            if len(fuzzy) == 1:
                resolved[slug] = fuzzy[0]
                tally["fuzzy"] += 1
                log.warning(
                    "%s: %r fuzzy-matched to %r — verify and add to OVERRIDES",
                    slug,
                    name,
                    fuzzy[0]["full_name"],
                )
                continue

        unmatched.append((slug, name))

    log.info(
        "crosswalk passes: %s",
        {k: tally[k] for k in sorted(tally)},
    )
    return resolved, unmatched


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def _to_frame(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(records, columns=list(CROSSWALK_SCHEMA))
    for column, kind in CROSSWALK_SCHEMA.items():
        if kind == "int":
            frame[column] = frame[column].astype("int64")
        else:
            frame[column] = frame[column].astype("str")
    return frame


def build_crosswalk(force_refresh: bool = False) -> pd.DataFrame:
    """Build (or reload) the BBRef-slug -> NBA-player-id crosswalk.

    Conforms to CROSSWALK_SCHEMA. Cached to data/processed/crosswalk.json; the
    cache is reused unless force_refresh, since identity changes only when a
    player debuts.
    """
    if not force_refresh and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            frame = _to_frame(cached)
            log.info("crosswalk: %d players from cache %s", len(frame), CACHE_PATH)
            return frame
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.warning("crosswalk cache unreadable (%s); rebuilding", exc)

    roster = _fetch_bbref_roster(use_cache=not force_refresh)
    resolved, unmatched = match_roster(roster, _load_nba_players())

    records = [
        {
            "bbref_slug": slug,
            "nba_player_id": int(person["id"]),
            "name": name,
            "name_normalized": normalize_name(name),
            "headshot_url": config.HEADSHOT_URL.format(player_id=person["id"]),
        }
        for slug, name in roster
        if (person := resolved.get(slug)) is not None
    ]
    frame = _to_frame(records)

    total = len(roster)
    rate = len(frame) / total if total else 0.0
    if unmatched:
        # Loud by design. These players are dropped from every downstream join,
        # so the build log must name them rather than report a percentage.
        log.warning(
            "crosswalk: %d of %d players unresolved (%.1f%% matched): %s",
            len(unmatched),
            total,
            rate * 100,
            ", ".join(f"{name} [{slug}]" for slug, name in sorted(unmatched)),
        )
    log.info("crosswalk: %d/%d matched (%.1f%%)", len(frame), total, rate * 100)

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(frame.to_dict(orient="records"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return frame


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    build_crosswalk(force_refresh=True)
