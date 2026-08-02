"""Unit tests for the BBRef contract parser.

Everything here runs against fixture strings. Nothing touches the network:
Sports-Reference jails clients that misbehave, and a test suite that hits a live
site is a test suite that fails for reasons unrelated to the code under test.

The fixtures are trimmed but structurally faithful copies of real markup taken
from /contracts/GSW.html and /contracts/MEM.html — including the details that
actually break parsers: the commented table, the `iz` empty cells, the
`salary-pl`/`salary-tm` option classes, the blank `thead` spacer row, the
`partial_table` + <em> dead-money rows, and the latin-1 mangled names.
"""

from __future__ import annotations

import logging
import math

import pytest

from etl.sources import bbref_contracts as bc


# ---------------------------------------------------------------------------
# parse_salary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$62,587,158", 62_587_158.0),
        ("$2,449,421", 2_449_421.0),
        ("$424,672", 424_672.0),
        ("$1", 1.0),
        ("62587158", 62_587_158.0),          # csk-style bare integer
        ("  $6,822,000  ", 6_822_000.0),     # surrounding whitespace
        ("$1,272,870\xa0", 1_272_870.0),     # non-breaking space
        ("($500,000)", -500_000.0),          # parenthesised negative
    ],
)
def test_parse_salary_values(raw, expected):
    assert bc.parse_salary(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "-", "--", "\xa0"])
def test_parse_salary_empty_is_nan(raw):
    """Empty means "no commitment this year" — it must not become 0.0.

    Collapsing it to zero would make an unsigned year indistinguishable from a
    year with a genuinely zero cap hit, and would silently pull team totals
    downward instead of raising a flag.
    """
    assert math.isnan(bc.parse_salary(raw))


def test_parse_salary_garbage_is_nan_not_an_exception():
    """One malformed cell must not abort a 30-team crawl."""
    assert math.isnan(bc.parse_salary("$not-a-number"))


# ---------------------------------------------------------------------------
# Comment extraction / find_table
# ---------------------------------------------------------------------------


LIVE_TABLE_HTML = """
<div id="wrap">
  <table id="contracts"><tbody><tr><td>live</td></tr></tbody></table>
</div>
"""

COMMENTED_TABLE_HTML = """
<div id="wrap">
  <div class="placeholder"></div>
  <!--
     <table id="payroll-notes"><tbody><tr><td>hidden</td></tr></tbody></table>
  -->
</div>
"""

BOTH_TABLES_HTML = LIVE_TABLE_HTML + COMMENTED_TABLE_HTML

MANY_COMMENTS_HTML = (
    "<!-- nav junk -->" * 40
    + '<!-- <table id="players"><tbody><tr><td>x</td></tr></tbody></table> -->'
    + "<!-- footer junk -->" * 20
)


def test_extract_comment_blocks_finds_all_comments():
    blocks = bc.extract_comment_blocks(COMMENTED_TABLE_HTML)
    assert len(blocks) == 1
    assert 'id="payroll-notes"' in blocks[0]


def test_extract_comment_blocks_handles_multiline_and_multiple():
    blocks = bc.extract_comment_blocks(BOTH_TABLES_HTML)
    assert len(blocks) == 1
    blocks = bc.extract_comment_blocks(MANY_COMMENTS_HTML)
    assert len(blocks) == 61


def test_extract_comment_blocks_empty_document():
    assert bc.extract_comment_blocks("<html><body></body></html>") == []


def test_find_table_in_live_dom():
    table = bc.find_table(LIVE_TABLE_HTML, "contracts")
    assert table is not None
    assert "live" in table.get_text()


def test_find_table_inside_html_comment():
    """The whole reason this helper exists — BBRef hides tables in comments."""
    table = bc.find_table(COMMENTED_TABLE_HTML, "payroll-notes")
    assert table is not None
    assert "hidden" in table.get_text()


def test_find_table_prefers_live_dom_but_still_reaches_comments():
    assert "live" in bc.find_table(BOTH_TABLES_HTML, "contracts").get_text()
    assert "hidden" in bc.find_table(BOTH_TABLES_HTML, "payroll-notes").get_text()


def test_find_table_among_many_unrelated_comments():
    table = bc.find_table(MANY_COMMENTS_HTML, "players")
    assert table is not None


def test_find_table_missing_returns_none():
    assert bc.find_table(BOTH_TABLES_HTML, "nope") is None


# ---------------------------------------------------------------------------
# Name repair
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expected",
    [
        "Nikola Jokić",
        "Luka Dončić",
        "Dennis Schröder",
        "Alperen Şengün",
        "Kristaps Porziņģis",
        "Nikola Vučević",
        "David Jones García",
    ],
)
def test_repair_mojibake(expected):
    # Reproduce the exact corruption: UTF-8 bytes read back as latin-1, which is
    # what `requests` does when BBRef omits the charset from its Content-Type.
    mangled = expected.encode("utf-8").decode("latin-1")
    assert mangled != expected
    assert bc.repair_mojibake(mangled) == expected


def test_repair_mojibake_literal_lifted_from_a_live_page():
    """Belt and braces: a byte-for-byte string taken off a real BBRef page."""
    assert bc.repair_mojibake("Dennis SchrÃ¶der") == "Dennis Schröder"


@pytest.mark.parametrize("name", ["Stephen Curry", "De'Anthony Melton", "O.G. Anunoby"])
def test_repair_mojibake_leaves_ascii_alone(name):
    assert bc.repair_mojibake(name) == name


def test_repair_mojibake_leaves_already_correct_unicode_alone():
    """Must never corrupt a name that decoded correctly in the first place."""
    assert bc.repair_mojibake("Nikola Jokić") == "Nikola Jokić"
    assert bc.repair_mojibake("Bogdan Bogdanović") == "Bogdan Bogdanović"


# ---------------------------------------------------------------------------
# Contract-type inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "notes, expected",
    [
        (["Signed two-way contract July 1, 2026."], "two_way"),
        (["Signed 4-yr/$16M rookie scale contract July 3, 2023."], "rookie_scale"),
        (["Signed rookie scale contract on July 2, 2024."], "rookie_scale"),
        (["Signed 1-yr minimum contract July 9, 2026."], "minimum"),
        (["Signed 1-yr/$63M contract extension August 29, 2024."], "extension"),
        (["Signed 3-yr/$50M contract July 6, 2026."], "free_agent"),
        (["Signed Exhibit 10 contract September 1, 2026."], "minimum"),
        # A rookie-scale *extension* is a negotiated, market-priced deal.
        (["Signed 5-yr/$224M rookie scale extension July 22, 2024."], "extension"),
    ],
)
def test_infer_contract_type(notes, expected):
    assert bc._infer_contract_type(notes, is_two_way=False) == expected


def test_infer_contract_type_two_way_flag_wins():
    assert bc._infer_contract_type(["Signed 2-yr contract"], is_two_way=True) == "two_way"


@pytest.mark.parametrize(
    "notes",
    [
        [],
        ["Signed 2-yr contract February 11, 2026."],   # no dollar figure
        ["Signed for remainder of season March 25, 2024."],
        ["Traded from ORL to MEM June 15, 2025."],
        ["2027-28 is a player option."],
    ],
)
def test_infer_contract_type_returns_none_when_unsure(notes):
    """Ambiguous prose must yield None, not a guess — Spotrac fills these in."""
    assert bc._infer_contract_type(notes, is_two_way=False) is None


# ---------------------------------------------------------------------------
# Full team page parse
# ---------------------------------------------------------------------------


def _team_page(rows: str, tfoot_y1: str) -> str:
    """Wrap fixture rows in a structurally faithful team contracts page."""
    return f"""
<div id="all_contracts">
<table class="stats_table" id="contracts">
<thead>
  <tr class="over_header">
    <th class="over_header center" colspan="2" data-stat=" "></th>
    <th class="over_header center" colspan="6" data-stat="header_salary">Salary</th>
    <th></th>
  </tr>
  <tr>
    <th aria-label="Player" data-stat="player" scope="col">Player</th>
    <th aria-label="Age" data-stat="age_today" scope="col">Age</th>
    <th aria-label="2026-27" data-stat="y1" scope="col">2026-27</th>
    <th aria-label="2027-28" data-stat="y2" scope="col">2027-28</th>
    <th aria-label="2028-29" data-stat="y3" scope="col">2028-29</th>
    <th aria-label="2029-30" data-stat="y4" scope="col">2029-30</th>
    <th aria-label="2030-31" data-stat="y5" scope="col">2030-31</th>
    <th aria-label="2031-32" data-stat="y6" scope="col">2031-32</th>
    <th aria-label="Guaranteed" data-stat="remain_gtd" scope="col">Guaranteed</th>
  </tr>
</thead>
<tbody>{rows}</tbody>
<tfoot><tr>
  <th class="left" data-stat="player" scope="row">Team Totals</th>
  <td class="center iz" data-stat="age_today"></td>
  <td class="right" data-stat="y1">{tfoot_y1}</td>
  <td class="right iz" data-stat="y2"></td>
  <td class="right iz" data-stat="y3"></td>
  <td class="right iz" data-stat="y4"></td>
  <td class="right iz" data-stat="y5"></td>
  <td class="right iz" data-stat="y6"></td>
  <td class="right" data-stat="remain_gtd">$1</td>
</tr></tfoot>
</table>
</div>
<!--
<table class="suppress_all sortable stats_table" id="payroll-notes">
<caption>Payroll Notes Table</caption>
<tr class="thead">
  <th data-stat="player">Player</th><th data-stat="notes">Notes</th>
</tr>
<tr><th class="left" data-stat="player" scope="row">
    <a href="/players/c/curryst01.html">Stephen Curry</a></th>
  <td class="left" data-stat="notes"><ul class="bullets">
    <li>Signed 1-yr/$63M contract extension August 29, 2024.</li></ul></td></tr>
<tr><th class="left" data-stat="player" scope="row">
    <a href="/players/p/porzikr01.html">Kristaps Porzingis</a></th>
  <td class="left" data-stat="notes"><ul class="bullets">
    <li>2027-28 is a player option.</li>
    <li>Signed 2-yr/$40M contract extension June 30, 2026.</li></ul></td></tr>
<tr><th class="left" data-stat="player" scope="row">
    <a href="/players/u/udehjer01.html">Ernest Udeh Jr.</a></th>
  <td class="left" data-stat="notes"><ul class="bullets">
    <li>Signed two-way contract July 1, 2026.</li></ul></td></tr>
<tr><th class="left" data-stat="player" scope="row">
    <a href="/players/l/lillada01.html">Damian Lillard</a></th>
  <td class="left" data-stat="notes"><ul class="bullets">
    <li>Waived July 6, 2025.</li>
    <li>Signed 2-yr/$122M contract extension July 9, 2022.</li></ul></td></tr>
</table>
-->
"""


ROW_SIMPLE = """
<tr><th class="left" csk="curryst01" data-stat="player" scope="row">
  <a href="/players/c/curryst01.html">Stephen Curry</a></th>
  <td class="center" data-stat="age_today">38</td>
  <td class="right" csk="62587158" data-stat="y1">$62,587,158</td>
  <td class="right iz" data-stat="y2"></td><td class="right iz" data-stat="y3"></td>
  <td class="right iz" data-stat="y4"></td><td class="right iz" data-stat="y5"></td>
  <td class="right iz" data-stat="y6"></td>
  <td class="right" data-stat="remain_gtd">$62,587,158</td></tr>
"""

# Mangled name, a future player option, and future years to collect.
ROW_OPTIONS = """
<tr><th class="left" csk="porzikr01" data-stat="player" scope="row">
  <a href="/players/p/porzikr01.html">Kristaps PorziÅÄ£is</a></th>
  <td class="center" data-stat="age_today">30</td>
  <td class="right salary-tm" csk="19512195" data-stat="y1">$19,512,195</td>
  <td class="right salary-pl" csk="20487805" data-stat="y2">$20,487,805</td>
  <td class="right iz" data-stat="y3"></td><td class="right iz" data-stat="y4"></td>
  <td class="right iz" data-stat="y5"></td><td class="right iz" data-stat="y6"></td>
  <td class="right" data-stat="remain_gtd">$19,512,195</td></tr>
"""

# Two-way: every money cell empty, no guarantee.
ROW_TWO_WAY = """
<tr><th class="left" csk="udehjer01" data-stat="player" scope="row">
  <a href="/players/u/udehjer01.html">Ernest Udeh Jr.</a></th>
  <td class="center" data-stat="age_today">23</td>
  <td class="right iz" data-stat="y1"></td><td class="right iz" data-stat="y2"></td>
  <td class="right iz" data-stat="y3"></td><td class="right iz" data-stat="y4"></td>
  <td class="right iz" data-stat="y5"></td><td class="right iz" data-stat="y6"></td>
  <td class="right iz" data-stat="remain_gtd"></td></tr>
"""

# Player option with nothing guaranteed.
ROW_UNGUARANTEED = """
<tr><th class="left" csk="meltode01" data-stat="player" scope="row">
  <a href="/players/m/meltode01.html">De'Anthony Melton</a></th>
  <td class="center" data-stat="age_today">28</td>
  <td class="right salary-pl" csk="3451779" data-stat="y1">$3,451,779</td>
  <td class="right iz" data-stat="y2"></td><td class="right iz" data-stat="y3"></td>
  <td class="right iz" data-stat="y4"></td><td class="right iz" data-stat="y5"></td>
  <td class="right iz" data-stat="y6"></td>
  <td class="right iz" data-stat="remain_gtd"></td></tr>
"""

# The blank spacer BBRef inserts before the dead-money block, then the dead
# money itself: partial_table row with the name wrapped in <em>.
ROW_SPACER = '<tr class="thead"><td colspan="10"></td></tr>'

ROW_DEAD_MONEY = """
<tr class="partial_table"><th class="left" csk="lillada01" data-stat="player" scope="row">
  <em><a href="/players/l/lillada01.html">Damian Lillard</a></em></th>
  <td class="center" data-stat="age_today">36</td>
  <td class="right" csk="22516603" data-stat="y1">$22,516,603</td>
  <td class="right" csk="22516603" data-stat="y2">$22,516,603</td>
  <td class="right iz" data-stat="y3"></td><td class="right iz" data-stat="y4"></td>
  <td class="right iz" data-stat="y5"></td><td class="right iz" data-stat="y6"></td>
  <td class="right" data-stat="remain_gtd">$45,033,206</td></tr>
"""

ALL_ROWS = (
    ROW_SIMPLE
    + ROW_OPTIONS
    + ROW_TWO_WAY
    + ROW_UNGUARANTEED
    + ROW_SPACER
    + ROW_DEAD_MONEY
)


@pytest.fixture(scope="module")
def parsed():
    # 62,587,158 + 19,512,195 + 3,451,779 + 22,516,603 (the two-way row is blank)
    records, total = bc._parse_team_page(_team_page(ALL_ROWS, "$108,067,735"), "GSW")
    return {r["bbref_slug"]: r for r in records}, records, total


def test_parses_every_row_and_skips_the_spacer(parsed):
    by_slug, records, _ = parsed
    assert len(records) == 5
    assert set(by_slug) == {
        "curryst01",
        "porzikr01",
        "udehjer01",
        "meltode01",
        "lillada01",
    }


def test_reads_the_tfoot_team_total(parsed):
    assert parsed[2] == 108_067_735.0


def test_simple_row(parsed):
    curry = parsed[0]["curryst01"]
    assert curry["name"] == "Stephen Curry"
    assert curry["team"] == "GSW"
    assert curry["salary_2026_27"] == 62_587_158.0
    assert curry["guaranteed_remaining"] == 62_587_158.0
    assert curry["future_years"] == {}       # empty cells are omitted, not zeroed
    assert curry["salary_2026_27_option"] == ""
    assert curry["is_two_way"] is False
    assert curry["is_dead_money"] is False
    assert curry["contract_type_override"] == "extension"


def test_future_years_and_option_markers(parsed):
    kp = parsed[0]["porzikr01"]
    assert kp["name"] == "Kristaps Porziņģis"   # mojibake repaired
    assert kp["salary_2026_27"] == 19_512_195.0
    assert kp["salary_2026_27_option"] == "team"          # salary-tm on y1
    assert kp["future_years"] == {"2027-28": 20_487_805.0}


def test_two_way_row_has_no_cap_hit_but_is_still_emitted(parsed):
    udeh = parsed[0]["udehjer01"]
    assert math.isnan(udeh["salary_2026_27"])
    assert udeh["is_two_way"] is True
    assert udeh["contract_type_override"] == "two_way"
    assert udeh["future_years"] == {}
    assert udeh["guaranteed_remaining"] == 0.0


def test_unguaranteed_player_option(parsed):
    melton = parsed[0]["meltode01"]
    assert melton["salary_2026_27"] == 3_451_779.0
    assert melton["salary_2026_27_option"] == "player"
    # Empty guarantee cell means genuinely nothing guaranteed, so 0.0 not NaN.
    assert melton["guaranteed_remaining"] == 0.0


def test_dead_money_row_is_flagged(parsed):
    lillard = parsed[0]["lillada01"]
    assert lillard["is_dead_money"] is True
    assert lillard["salary_2026_27"] == 22_516_603.0
    assert lillard["future_years"] == {"2027-28": 22_516_603.0}


def test_parsed_rows_reconcile_with_the_tfoot_total(parsed):
    """The check that actually catches a dropped or duplicated row."""
    _, records, total = parsed
    summed = sum(
        r["salary_2026_27"]
        for r in records
        if not math.isnan(r["salary_2026_27"])
    )
    assert summed == pytest.approx(total, abs=1.0)


def test_season_rollover_is_fatal():
    """If BBRef advances the page, y1 stops meaning 2026-27 — fail loudly."""
    html = _team_page(ROW_SIMPLE, "$62,587,158").replace(
        'aria-label="2026-27" data-stat="y1"', 'aria-label="2027-28" data-stat="y1"'
    )
    with pytest.raises(ValueError, match="rolled the season over"):
        bc._parse_team_page(html, "GSW")


def test_missing_contracts_table_raises():
    with pytest.raises(bc.ratelimit.SourceUnavailable):
        bc._parse_team_page("<html><body>nothing here</body></html>", "GSW")


def test_contracts_table_is_found_even_if_bbref_comments_it_out():
    """Defence against BBRef moving #contracts into a comment block."""
    live = _team_page(ROW_SIMPLE, "$62,587,158")
    start = live.index("<table")
    end = live.index("</table>") + len("</table>")
    commented = live[:start] + "<!--" + live[start:end] + "-->" + live[end:]
    records, total = bc._parse_team_page(commented, "GSW")
    assert len(records) == 1
    assert total == 62_587_158.0


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------


INDEX_HTML = """
<table id="team_summary"><tbody>
<tr><th data-stat="ranker">1</th>
    <td data-stat="team_name"><a href="/contracts/CLE.html">Cleveland Cavaliers</a></td>
    <td data-stat="y1">$226,017,942</td></tr>
<tr><th data-stat="ranker">2</th>
    <td data-stat="team_name"><a href="/contracts/GSW.html">Golden State Warriors</a></td>
    <td data-stat="y1">$210,390,143</td></tr>
</tbody></table>
<!-- <table id="players"><tbody><tr>
   <td data-stat="player"><a href="/players/c/curryst01.html">Stephen Curry</a></td>
   </tr></tbody></table> -->
"""


def test_team_abbreviations_from_index():
    assert bc._team_abbreviations(INDEX_HTML) == ["CLE", "GSW"]


def test_team_abbreviations_ignores_player_links():
    """The commented #players leaderboard must not contribute team codes."""
    assert "curryst01" not in bc._team_abbreviations(INDEX_HTML)


def test_team_abbreviations_missing_table_raises():
    with pytest.raises(bc.ratelimit.SourceUnavailable):
        bc._team_abbreviations("<html><body></body></html>")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _record(team: str, i: int, salary: float) -> dict:
    return {
        "bbref_slug": f"{team.lower()}play{i:02d}",
        "name": f"{team} Player {i}",
        "team": team,
        "salary_2026_27": salary,
        "guaranteed_remaining": salary,
        "future_years": {},
        "contract_type_override": None,
        "is_two_way": False,
        "is_dead_money": False,
        "salary_2026_27_option": "",
    }


def _league(per_team_salary: float, teams: int = 30, per_team: int = 15) -> list[dict]:
    return [
        _record(f"T{t:02d}", i, per_team_salary / per_team)
        for t in range(teams)
        for i in range(per_team)
    ]


def test_validate_accepts_a_plausible_league(caplog):
    records = _league(180_000_000.0)
    totals = {f"T{t:02d}": 180_000_000.0 for t in range(30)}
    with caplog.at_level(logging.DEBUG):
        bc._validate(records, totals)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_validate_logs_the_league_and_per_team_totals(caplog):
    """A human has to be able to eyeball these numbers in the build log."""
    with caplog.at_level(logging.INFO):
        bc._validate(_league(180_000_000.0), {})
    text = caplog.text
    assert "league totals" in text
    assert "450 contract rows across 30 teams" in text
    assert "T00" in text and "T29" in text


def test_validate_errors_when_rows_do_not_reconcile_with_the_tfoot(caplog):
    """The check that actually catches a dropped row: BBRef told us the total."""
    records = _league(180_000_000.0)
    totals = {f"T{t:02d}": 180_000_000.0 for t in range(30)}
    totals["T07"] = 195_000_000.0            # as if we dropped a $15M player
    with caplog.at_level(logging.ERROR):
        bc._validate(records, totals)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "T07" in errors[0]


@pytest.mark.parametrize("total", [40_000_000.0, 400_000_000.0])
def test_validate_warns_on_a_team_total_outside_the_band(total, caplog):
    records = _league(180_000_000.0)
    records = [r for r in records if r["team"] != "T03"] + [
        _record("T03", i, total / 15) for i in range(15)
    ]
    with caplog.at_level(logging.WARNING):
        bc._validate(records, {})
    warnings = [
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("T03" in w and "outside the plausible" in w for w in warnings)


def test_validate_warns_when_the_league_is_short_of_players(caplog):
    with caplog.at_level(logging.WARNING):
        bc._validate(_league(180_000_000.0, per_team=12), {})   # 360 rows
    assert any(
        "league player count 360" in r.getMessage() for r in caplog.records
    )


def test_validate_raises_when_the_parse_has_clearly_collapsed():
    """Too few rows is not an odd roster, it is a broken scrape."""
    with pytest.raises(ValueError, match="parse has failed"):
        bc._validate(_league(180_000_000.0, teams=10, per_team=15), {})   # 150 rows


def test_validate_ignores_nan_salaries_in_team_totals():
    """Two-way rows carry NaN and must not poison the sum."""
    records = _league(180_000_000.0)
    records[0]["salary_2026_27"] = float("nan")
    records[0]["is_two_way"] = True
    totals = {f"T{t:02d}": 180_000_000.0 for t in range(30)}
    totals["T00"] = 180_000_000.0 - 12_000_000.0
    bc._validate(records, totals)   # reconciles: NaN row contributes nothing
