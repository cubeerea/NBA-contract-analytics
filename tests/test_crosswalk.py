"""Tests for the identity spine.

Everything here runs offline. The matcher is exercised through match_roster,
which is a pure function over (roster, nba_people) precisely so the hard part —
name resolution — can be tested with fixtures instead of the network.
"""

from __future__ import annotations

import logging

import pytest

from etl import crosswalk
from etl.crosswalk import match_roster, normalize_name
from etl.schema import CROSSWALK_SCHEMA


def person(pid: int, full_name: str, active: bool = True) -> dict:
    """Build a record shaped like nba_api.stats.static.players output."""
    first, _, last = full_name.partition(" ")
    return {
        "id": pid,
        "full_name": full_name,
        "first_name": first,
        "last_name": last,
        "is_active": active,
    }


# --------------------------------------------------------------------------
# normalize_name
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Nikola Jokić", "nikola jokic"),
        ("Luka Dončić", "luka doncic"),
        ("Kristaps Porziņģis", "kristaps porzingis"),
        ("Alperen Şengün", "alperen sengun"),
        ("Jonas Valančiūnas", "jonas valanciunas"),
        ("Dennis Schröder", "dennis schroder"),
        ("Moussa Diabaté", "moussa diabate"),
        ("Dario Šarić", "dario saric"),
        ("Skal Labissière", "skal labissiere"),
        ("Chris Mañon", "chris manon"),
        ("Yanic Konan Niederhäuser", "yanic konan niederhauser"),
        # U+0451 CYRILLIC SMALL LETTER IO: NFKD folds it to Cyrillic е, not
        # Latin e, so it needs the explicit transliteration table.
        ("Egor Dёmin", "egor demin"),
    ],
)
def test_accents_are_folded_to_ascii(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected
    assert normalize_name(raw).isascii()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jaren Jackson Jr.", "jaren jackson"),
        ("Jabari Smith Jr.", "jabari smith"),
        ("Tim Hardaway Jr", "tim hardaway"),
        ("Gary Payton II", "gary payton"),
        ("Marvin Bagley III", "marvin bagley"),
        ("Robert Griffin IV", "robert griffin"),
        ("Xavier Tillman Sr.", "xavier tillman"),
        ("Kelly Oubre Jr.", "kelly oubre"),
        ("Ronald Holland II", "ronald holland"),
    ],
)
def test_suffixes_are_stripped(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_suffix_stripping_never_eats_the_whole_name() -> None:
    # A single token that happens to look like a suffix is a name, not a
    # suffix — stripping it would produce an empty key that collides with
    # every other empty key.
    assert normalize_name("Jr") == "jr"
    assert normalize_name("V") == "v"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("De'Aaron Fox", "deaaron fox"),
        ("DeAaron Fox", "deaaron fox"),
        ("Shai Gilgeous-Alexander", "shai gilgeous alexander"),
        ("Karl-Anthony Towns", "karl anthony towns"),
        ("Karl Anthony Towns", "karl anthony towns"),
        ("R.J. Barrett", "rj barrett"),
        ("RJ Barrett", "rj barrett"),
        ("  Stephen   Curry  ", "stephen curry"),
        ("Nah'Shon Hyland", "nahshon hyland"),
        ("Adama-Alpha Bal", "adama alpha bal"),
    ],
)
def test_punctuation_and_spacing_are_normalized(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_normalize_name_is_idempotent() -> None:
    for raw in ("Kristaps Porziņģis", "Jaren Jackson Jr.", "De'Aaron Fox"):
        once = normalize_name(raw)
        assert normalize_name(once) == once


def test_normalize_name_handles_empty() -> None:
    assert normalize_name("") == ""


def test_bbref_and_nba_spellings_converge() -> None:
    """The whole point: the two sources' spellings must land on one key."""
    pairs = [
        ("Nikola Jokić", "Nikola Jokic"),
        ("Nic Claxton", "Nic Claxton"),
        ("Jaren Jackson Jr.", "Jaren Jackson"),
        ("Bogdan Bogdanović", "Bogdan Bogdanovic"),
        ("Vít Krejčí", "Vit Krejci"),
    ]
    for bbref, nba in pairs:
        assert normalize_name(bbref) == normalize_name(nba)


# --------------------------------------------------------------------------
# Name collisions
# --------------------------------------------------------------------------


def test_collision_resolved_by_suffix() -> None:
    """Jabari Smith Jr. must not silently resolve to his father."""
    people = [
        person(2074, "Jabari Smith", active=False),
        person(1631095, "Jabari Smith Jr."),
    ]
    resolved, unmatched = match_roster([("smithja05", "Jabari Smith Jr.")], people)
    assert not unmatched
    assert resolved["smithja05"]["id"] == 1631095


def test_collision_resolved_by_active_flag() -> None:
    """Same name, no suffix on either side: the retired one is the wrong one."""
    people = [
        person(1585, "Brandon Williams", active=False),
        person(1630314, "Brandon Williams"),
    ]
    resolved, _ = match_roster([("willibr03", "Brandon Williams")], people)
    assert resolved["willibr03"]["id"] == 1630314


def test_collision_resolved_by_slug_stem() -> None:
    """When both namesakes are inactive, the slug's legal-name stem decides."""
    people = [
        person(78561, "Nate Williams", active=False),
        person(1631466, "Jeenathan Williams", active=False),
    ]
    resolved, _ = match_roster([("willije02", "Nate Williams")], people)
    # willi + je == Jeenathan Williams, not Nate Williams.
    assert resolved["willije02"]["id"] == 1631466


def test_unresolvable_collision_warns_and_prefers_the_modern_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A coin flip is allowed, but it may never be silent."""
    people = [
        person(78561, "Nate Williams", active=False),
        person(1631466, "Nate Williams", active=False),
    ]
    with caplog.at_level(logging.WARNING, logger="etl.crosswalk"):
        resolved, _ = match_roster([("willixx02", "Nate Williams")], people)
    assert resolved["willixx02"]["id"] == 1631466
    assert "ambiguous name" in caplog.text
    assert "Nate Williams" in caplog.text


def test_first_match_is_never_taken_blindly() -> None:
    """Ordering the fixture the other way must not change the answer."""
    forward = [person(56, "Gary Payton", active=False), person(1627780, "Gary Payton II")]
    resolved_a, _ = match_roster([("paytoga02", "Gary Payton II")], forward)
    resolved_b, _ = match_roster([("paytoga02", "Gary Payton II")], forward[::-1])
    assert resolved_a["paytoga02"]["id"] == resolved_b["paytoga02"]["id"] == 1627780


# --------------------------------------------------------------------------
# Nickname / legal-name disagreement
# --------------------------------------------------------------------------


def test_nickname_resolved_via_slug_stem() -> None:
    """BBRef "Ron Holland" vs NBA.com "Ronald Holland II"."""
    people = [person(1641842, "Ronald Holland II")]
    resolved, unmatched = match_roster([("hollaro01", "Ron Holland")], people)
    assert not unmatched
    assert resolved["hollaro01"]["id"] == 1641842


def test_slug_stem_does_not_match_a_different_person() -> None:
    """"Chaney Johnson" and "Chris Johnson" share the stem "johnsch"."""
    people = [person(202419, "Chris Johnson", active=False)]
    resolved, unmatched = match_roster([("johnsch06", "Chaney Johnson")], people)
    assert resolved == {}
    assert unmatched == [("johnsch06", "Chaney Johnson")]


def test_short_first_name_prefix_does_not_match() -> None:
    """Two characters of first name is not evidence: Ja/Jalen vs Ja/Jarrett."""
    people = [person(999, "Jarrett Allen")]
    resolved, unmatched = match_roster([("allenja99", "Ja Allen")], people)
    assert unmatched == [("allenja99", "Ja Allen")]
    assert resolved == {}


def test_missing_player_is_reported_not_invented() -> None:
    """Players absent from the bundled static table must come back unmatched."""
    people = [person(1630286, "Trevon Scott"), person(999, "Josh Boone", active=False)]
    resolved, unmatched = match_roster([("odurojo01", "Josh Oduro")], people)
    assert resolved == {}
    assert unmatched == [("odurojo01", "Josh Oduro")]


def test_unmatched_players_are_logged_by_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    people = [person(203999, "Nikola Jokic")]
    roster = [("jokicni01", "Nikola Jokić"), ("mashaja01", "Jahmai Mashack")]
    with caplog.at_level(logging.INFO, logger="etl.crosswalk"):
        resolved, unmatched = match_roster(roster, people)
    assert len(resolved) == 1
    assert unmatched == [("mashaja01", "Jahmai Mashack")]


# --------------------------------------------------------------------------
# Overrides
# --------------------------------------------------------------------------


def test_override_wins_over_automatic_match() -> None:
    people = [person(1, "Wrong Guy"), person(2, "Right Guy")]
    original = dict(crosswalk.OVERRIDES)
    crosswalk.OVERRIDES["wrongg01"] = 2
    try:
        resolved, _ = match_roster([("wrongg01", "Wrong Guy")], people)
    finally:
        crosswalk.OVERRIDES.clear()
        crosswalk.OVERRIDES.update(original)
    assert resolved["wrongg01"]["id"] == 2


def test_stale_override_logs_an_error_and_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    people = [person(203999, "Nikola Jokic")]
    original = dict(crosswalk.OVERRIDES)
    crosswalk.OVERRIDES["jokicni01"] = 12345678
    try:
        with caplog.at_level(logging.ERROR, logger="etl.crosswalk"):
            resolved, _ = match_roster([("jokicni01", "Nikola Jokić")], people)
    finally:
        crosswalk.OVERRIDES.clear()
        crosswalk.OVERRIDES.update(original)
    assert "stale" in caplog.text
    assert resolved["jokicni01"]["id"] == 203999


def test_overrides_are_well_formed() -> None:
    for slug, player_id in crosswalk.OVERRIDES.items():
        assert crosswalk._SLUG_RE.match(slug), slug
        assert isinstance(player_id, int) and player_id > 0


def test_overrides_point_at_real_players() -> None:
    """Offline check (ADR-010): every override must exist in the static table."""
    known = {p["id"] for p in crosswalk._load_nba_players()}
    stale = {s: i for s, i in crosswalk.OVERRIDES.items() if i not in known}
    assert not stale, f"stale OVERRIDES entries: {stale}"


# --------------------------------------------------------------------------
# Frame shape
# --------------------------------------------------------------------------


def test_frame_conforms_to_schema() -> None:
    frame = crosswalk._to_frame(
        [
            {
                "bbref_slug": "jokicni01",
                "nba_player_id": 203999,
                "name": "Nikola Jokić",
                "name_normalized": "nikola jokic",
                "headshot_url": "https://example.invalid/203999.png",
            }
        ]
    )
    assert list(frame.columns) == list(CROSSWALK_SCHEMA)
    assert frame["nba_player_id"].dtype == "int64"
    assert frame.loc[0, "bbref_slug"] == "jokicni01"


def test_headshot_url_uses_the_nba_id() -> None:
    from etl import config

    url = config.HEADSHOT_URL.format(player_id=203999)
    assert url.endswith("/203999.png")
    assert "{" not in url


def test_slug_stem_matches_bbref_convention() -> None:
    assert crosswalk._slug_stem("Nicolas", "Claxton") == "claxtni"
    assert crosswalk._slug_stem("Stephen", "Curry") == "curryst"
    assert crosswalk._slug_stem("Yanic", "Konan Niederhauser") == "konanya"
    assert crosswalk._slug_stem("Adama-Alpha", "Bal") == "balad"


def test_mojibake_repair() -> None:
    """BBRef serves UTF-8 without a charset header; requests decodes Latin-1."""
    broken = "Alperen Å\x9eengÃ¼n"
    assert crosswalk._repair_mojibake(broken) == "Alperen Şengün"
    # Well-formed text is left alone.
    assert crosswalk._repair_mojibake("Alperen Şengün") == "Alperen Şengün"


def test_find_table_recovers_a_commented_table() -> None:
    """BBRef hides most tables inside HTML comments."""
    html = '<div><!-- <table id="players"><tr><td>x</td></tr></table> --></div>'
    table = crosswalk._find_table(html, "players")
    assert table is not None
    assert table.get_text(strip=True) == "x"
    assert crosswalk._find_table(html, "nope") is None
