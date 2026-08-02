"""Parser tests for the BBRef advanced-stats and DARKO source adapters.

These exercise the parts that are genuinely easy to get wrong and expensive to
get wrong quietly: collapsing a traded player's several rows onto one, and
turning BBRef's ".594"/"" cells into numbers. Nothing here touches the network —
the fixtures below are trimmed from the real 2025-26 markup so a BBRef layout
change is caught by the end-to-end fetch, not by a mock drifting out of date.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from etl import ratelimit
from etl.schema import DARKO_SCHEMA, STATS_SCHEMA
from etl.sources import bbref_stats, darko

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

_HEADER = (
    '<thead><tr>'
    '<th data-stat="ranker">Rk</th><th data-stat="name_display">Player</th>'
    '</tr></thead>'
)


def _row(
    *,
    slug: str,
    name: str,
    team: str,
    csk_suffix: str,
    partial: bool = False,
    age: str = "30",
    games: str = "70",
    started: str = "70",
    mp: str = "2400",
    per: str = "18.9",
    ts: str = ".594",
    usg: str = "20.0",
    ws: str = "10.3",
    ws48: str = ".167",
    vorp: str = "3.4",
    pos: str = "PG",
) -> str:
    """One <tr> in BBRef's shape: data-stat attributes, csk sort keys, hrefs."""
    cls = ' class="partial_table"' if partial else ""
    team_cell = (
        f'<a href="/teams/{team}/2026.html">{team}</a>' if partial else team
    )
    return (
        f"<tr{cls}>"
        f'<th data-stat="ranker" csk="1">1</th>'
        f'<td data-stat="name_display" csk="{name}{csk_suffix}" '
        f'data-append-csv="{slug}"><a href="/players/{slug[0]}/{slug}.html">'
        f"{name}</a></td>"
        f'<td data-stat="age">{age}</td>'
        f'<td data-stat="team_name_abbr">{team_cell}</td>'
        f'<td data-stat="pos">{pos}</td>'
        f'<td data-stat="games">{games}</td>'
        f'<td data-stat="games_started">{started}</td>'
        f'<td data-stat="mp">{mp}</td>'
        f'<td data-stat="per">{per}</td>'
        f'<td data-stat="ts_pct">{ts}</td>'
        f'<td data-stat="fg3a_per_fga_pct">.415</td>'
        f'<td data-stat="usg_pct">{usg}</td>'
        f'<td data-stat="ows">6.5</td>'
        f'<td data-stat="dws">3.8</td>'
        f'<td data-stat="ws">{ws}</td>'
        f'<td data-stat="ws_per_48">{ws48}</td>'
        f'<td data-stat="obpm">1.6</td>'
        f'<td data-stat="dbpm">1.0</td>'
        f'<td data-stat="bpm">2.6</td>'
        f'<td data-stat="vorp">{vorp}</td>'
        f'<td data-stat="awards"></td>'
        f"</tr>"
    )


# One untraded player; one two-team player; one three-team player; the
# "League Average" row BBRef appends; a repeated header row; and a player whose
# rate stats are blank because he never attempted a shot.
_ROWS = "".join(
    [
        _row(slug="thompam01", name="Amen Thompson", team="HOU", csk_suffix="-1",
             ws="10.3", vorp="3.4"),
        # 2TM: combined line first, stints in chronological order.
        _row(slug="hardeja01", name="Harden James", team="2TM", csk_suffix="--98",
             games="70", mp="2438", ws="8.0", vorp="3.0"),
        _row(slug="hardeja01", name="Harden James", team="LAC", csk_suffix="-1",
             partial=True, games="44", mp="1559", ws="5.0", vorp="2.0"),
        _row(slug="hardeja01", name="Harden James", team="CLE", csk_suffix="-2",
             partial=True, games="26", mp="879", ws="3.0", vorp="1.0"),
        '<tr class="thead"><td data-stat="name_display">Player</td></tr>',
        # 3TM, deliberately out of document order to prove csk drives the pick.
        _row(slug="basseych01", name="Bassey Charles", team="3TM",
             csk_suffix="--98", games="13", mp="153", ws="0.5", vorp="0.1"),
        _row(slug="basseych01", name="Bassey Charles", team="GSW",
             csk_suffix="-3", partial=True, games="5", mp="100"),
        _row(slug="basseych01", name="Bassey Charles", team="MEM",
             csk_suffix="-1", partial=True, games="2", mp="31"),
        _row(slug="basseych01", name="Bassey Charles", team="PHI",
             csk_suffix="-2", partial=True, games="1", mp="5"),
        # No field goal attempts -> blank rate cells.
        _row(slug="zeroshzz01", name="Zero Shots", team="SAS", csk_suffix="-1",
             games="21", mp="42", ts="", usg="", per=""),
        # Mojibake exactly as BBRef's headers cause requests to decode it.
        _row(slug="sengual01", name="Alperen Şengün".encode("utf-8")
             .decode("latin-1"), team="HOU", csk_suffix="-1"),
        '<tr class="norank"><th data-stat="ranker"></th>'
        '<td data-stat="name_display">League Average</td>'
        '<td data-stat="age"></td><td data-stat="team_name_abbr"></td>'
        '<td data-stat="games"></td><td data-stat="mp"></td>'
        '<td data-stat="ts_pct">.581</td></tr>',
    ]
)

LIVE_HTML = f'<html><body><table id="advanced">{_HEADER}<tbody>{_ROWS}</tbody></table></body></html>'

# The same table buried in a comment, which is how BBRef ships most tables.
COMMENTED_HTML = (
    '<html><body><div id="all_advanced"><!--'
    f'<table id="advanced">{_HEADER}<tbody>{_ROWS}</tbody></table>'
    "--></div></body></html>"
)

DARKO_CSV = (
    "NBA ID,Player Name,Position,Age,DPM,Offensive DPM,Defensive DPM,"
    "Box Only O-DPM,Box Only D-DPM,On Off O-DPM,On Off D-DPM\n"
    + "\n".join(
        f"{1000 + i},Filler Player {i},c_pos,25.0,1.0,0.6,0.4,0.5,0.3,0.7,0.5"
        for i in range(darko.MIN_EXPECTED_ROWS)
    )
    + "\n203999,Nikola Jokic,c_pos,31.3,7.03,5.13,1.9,4.3,1.18,5.26,1.97"
    + "\n1641705,Victor Wembanyama,pf_pos,22.4,6.36,3.97,2.39,4.2,2.0,3.79,2.58"
    + "\n1642261,Kasparas Jakučionis,pg_pos,20.1,-1.5,-0.9,-0.6,-1.1,-0.4,-0.8,-0.7"
    + "\n9999,Missing DPM,sg_pos,28.0,,,,,,,\n"
)


@pytest.fixture(scope="module")
def stats() -> pd.DataFrame:
    records = bbref_stats._collapse_traded(bbref_stats._parse_advanced_html(LIVE_HTML))
    return bbref_stats._to_frame(records)


# --------------------------------------------------------------------------
# Numeric parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (".594", 0.594),      # BBRef drops the leading zero on rate stats
        (".167", 0.167),
        ("18.9", 18.9),
        ("-2.6", -2.6),
        (" 3.4 ", 3.4),
        ("0", 0.0),
    ],
)
def test_to_float_parses_bbref_numbers(raw: str, expected: float) -> None:
    assert bbref_stats._to_float(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "   ", None, "—", "n/a"])
def test_to_float_returns_nan_without_raising(raw: str | None) -> None:
    assert math.isnan(bbref_stats._to_float(raw))


def test_to_int_handles_blanks_and_values() -> None:
    assert bbref_stats._to_int("2438") == 2438.0
    assert math.isnan(bbref_stats._to_int(""))


def test_blank_rate_cells_become_nan_not_zero() -> None:
    """A player with no attempts has undefined TS%, not 0% TS%.

    Coercing to zero would drag him into the regression as a maximally
    inefficient player instead of excluding him.
    """
    records = bbref_stats._parse_advanced_html(LIVE_HTML)
    zero = next(r for r in records if r["bbref_slug"] == "zeroshzz01")
    assert math.isnan(zero["ts_pct"])
    assert math.isnan(zero["usg_pct"])
    assert zero["games"] == 21


def test_mojibake_in_names_is_repaired() -> None:
    records = bbref_stats._parse_advanced_html(LIVE_HTML)
    sengun = next(r for r in records if r["bbref_slug"] == "sengual01")
    assert sengun["name"] == "Alperen Şengün"


def test_fix_mojibake_leaves_clean_text_alone() -> None:
    assert bbref_stats._fix_mojibake("Amen Thompson") == "Amen Thompson"
    assert bbref_stats._fix_mojibake("Nikola Jokić") == "Nikola Jokić"


# --------------------------------------------------------------------------
# Table extraction
# --------------------------------------------------------------------------


def _comparable(records: list[dict]) -> list[dict]:
    """NaN != NaN, so blank cells would make any two parses compare unequal."""
    return [
        {k: ("<nan>" if isinstance(v, float) and math.isnan(v) else v)
         for k, v in record.items()}
        for record in records
    ]


def test_extracts_table_hidden_in_html_comment() -> None:
    """BBRef hides most tables in comments; parsing must survive the switch."""
    live = bbref_stats._parse_advanced_html(LIVE_HTML)
    commented = bbref_stats._parse_advanced_html(COMMENTED_HTML)
    assert _comparable(commented) == _comparable(live)
    assert len(live) == 10   # 5 players, 1 of them 2TM and 1 of them 3TM


def test_missing_table_raises_source_unavailable() -> None:
    with pytest.raises(ratelimit.SourceUnavailable):
        bbref_stats._parse_advanced_html("<html><body>nope</body></html>")


def test_non_player_rows_are_dropped() -> None:
    """League Average and repeated header rows carry no slug and must not survive."""
    records = bbref_stats._parse_advanced_html(LIVE_HTML)
    assert "League Average" not in {r["name"] for r in records}
    assert "Player" not in {r["name"] for r in records}


# --------------------------------------------------------------------------
# Traded-player collapsing
# --------------------------------------------------------------------------


def test_collapse_yields_exactly_one_row_per_slug(stats: pd.DataFrame) -> None:
    assert not stats["bbref_slug"].duplicated().any()
    assert len(stats) == 5  # Thompson, Harden, Bassey, Zero Shots, Şengün


def test_traded_player_keeps_combined_season_totals(stats: pd.DataFrame) -> None:
    """Stats come from the combined line, not from either stint."""
    harden = stats.set_index("bbref_slug").loc["hardeja01"]
    assert harden["games"] == 70          # 44 + 26, i.e. the 2TM row
    assert harden["minutes"] == 2438
    assert harden["ws"] == pytest.approx(8.0)
    assert harden["vorp"] == pytest.approx(3.0)


def test_traded_player_team_is_final_team_not_the_marker(stats: pd.DataFrame) -> None:
    """The dashboard filters by team; '2TM' is not a team anyone can filter to."""
    harden = stats.set_index("bbref_slug").loc["hardeja01"]
    assert harden["team"] == "CLE"


def test_final_team_uses_stint_order_not_document_order(stats: pd.DataFrame) -> None:
    """Bassey's fixture rows are GSW, MEM, PHI in the DOM but 1,2,3 by csk."""
    bassey = stats.set_index("bbref_slug").loc["basseych01"]
    assert bassey["team"] == "GSW"
    assert bassey["minutes"] == 153       # combined, not the 100-minute GSW stint


def test_no_combined_marker_survives_into_output(stats: pd.DataFrame) -> None:
    assert not stats["team"].str.match(bbref_stats.COMBINED_TEAM_RE).any()


def test_untraded_player_is_untouched(stats: pd.DataFrame) -> None:
    thompson = stats.set_index("bbref_slug").loc["thompam01"]
    assert thompson["team"] == "HOU"
    assert thompson["minutes"] == 2400
    assert thompson["ts_pct"] == pytest.approx(0.594)


@pytest.mark.parametrize("marker", ["TOT", "2TM", "3TM", "4TM"])
def test_both_combined_markers_are_recognised(marker: str) -> None:
    """2026 uses nTM; older pages use TOT. Neither may leak through."""
    assert bbref_stats.COMBINED_TEAM_RE.match(marker)


@pytest.mark.parametrize("not_marker", ["HOU", "TM", "PHI", "2T"])
def test_real_team_codes_are_not_mistaken_for_markers(not_marker: str) -> None:
    assert not bbref_stats.COMBINED_TEAM_RE.match(not_marker)


def test_collapse_falls_back_when_combined_row_is_absent(caplog) -> None:
    """If BBRef ever stops emitting the combined line, still emit one row."""
    html = (
        f'<html><table id="advanced">{_HEADER}<tbody>'
        + _row(slug="aaaaaa01", name="No Combined", team="LAC", csk_suffix="-1",
               partial=True, games="44", mp="1559")
        + _row(slug="aaaaaa01", name="No Combined", team="CLE", csk_suffix="-2",
               partial=True, games="26", mp="879")
        + "</tbody></table></html>"
    )
    with caplog.at_level("WARNING"):
        frame = bbref_stats._to_frame(
            bbref_stats._collapse_traded(bbref_stats._parse_advanced_html(html))
        )
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["team"] == "CLE"      # still the final stint
    assert row["minutes"] == 1559    # largest stint stands in for the total
    assert "no combined row" in caplog.text


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------


def test_output_matches_stats_schema(stats: pd.DataFrame) -> None:
    assert list(stats.columns) == list(STATS_SCHEMA)
    for column, kind in STATS_SCHEMA.items():
        dtype = stats[column].dtype
        if kind == "int":
            assert np.issubdtype(dtype, np.integer), f"{column} is {dtype}"
        elif kind == "float":
            assert np.issubdtype(dtype, np.floating), f"{column} is {dtype}"
        else:
            assert dtype == object, f"{column} is {dtype}"


def test_duplicate_slugs_trip_the_assertion() -> None:
    records = bbref_stats._parse_advanced_html(LIVE_HTML)
    with pytest.raises(AssertionError, match="duplicate bbref_slug"):
        bbref_stats._to_frame(records)   # uncollapsed, so Harden appears 3x


# --------------------------------------------------------------------------
# DARKO
# --------------------------------------------------------------------------


def test_darko_maps_sheet_headers_to_schema() -> None:
    frame = darko._parse_csv(DARKO_CSV)
    assert list(frame.columns) == list(DARKO_SCHEMA)
    jokic = frame.set_index("name").loc["Nikola Jokic"]
    assert jokic["nba_player_id"] == 203999
    assert jokic["dpm"] == pytest.approx(7.03)
    assert jokic["o_dpm"] == pytest.approx(5.13)
    assert jokic["d_dpm"] == pytest.approx(1.90)


def test_darko_box_dpm_is_the_sum_of_the_box_only_halves() -> None:
    """The sheet has no single box column; O + D is the consistent composition."""
    frame = darko._parse_csv(DARKO_CSV)
    jokic = frame.set_index("name").loc["Nikola Jokic"]
    assert jokic["box_dpm"] == pytest.approx(4.30 + 1.18)


def test_darko_dpm_decomposes_into_offense_and_defense() -> None:
    frame = darko._parse_csv(DARKO_CSV)
    residual = (frame["o_dpm"] + frame["d_dpm"] - frame["dpm"]).abs().max()
    assert residual < 0.02


def test_darko_repairs_mojibake_names() -> None:
    """Crosswalk matches on name, so a mangled name is a silently lost player."""
    frame = darko._parse_csv(DARKO_CSV)
    assert "Kasparas Jakučionis" in set(frame["name"])


def test_darko_blank_metrics_become_nan() -> None:
    """A player DARKO has not rated yet must be missing, never zero-impact."""
    frame = darko._parse_csv(DARKO_CSV)
    missing = frame["name"] == "Missing DPM"
    assert missing.sum() == 1
    for column in ("dpm", "o_dpm", "d_dpm", "box_dpm"):
        assert frame.loc[missing, column].isna().all(), column


def test_darko_rejects_a_short_response() -> None:
    """An error or login page parses as CSV; row count is what catches it."""
    short = "\n".join(DARKO_CSV.splitlines()[:5]) + "\n"
    with pytest.raises(ValueError, match="rows"):
        darko._parse_csv(short)


def test_darko_rejects_renamed_columns() -> None:
    renamed = DARKO_CSV.replace("Offensive DPM", "O-DPM", 1)
    with pytest.raises(ValueError, match="missing expected column"):
        darko._parse_csv(renamed)


def test_darko_unavailable_raises_source_unavailable(monkeypatch) -> None:
    """Enrichment source: the orchestrator needs a typed signal to degrade on."""
    def boom(url: str, **kwargs: object) -> str:
        raise ratelimit.SourceUnavailable("network down")

    monkeypatch.setattr(darko.ratelimit, "fetch", boom)
    with pytest.raises(ratelimit.SourceUnavailable, match="all endpoints"):
        darko.fetch_darko()


def test_darko_falls_back_to_the_second_endpoint(monkeypatch) -> None:
    calls: list[str] = []

    def flaky(url: str, **kwargs: object) -> str:
        calls.append(url)
        if url == darko.ENDPOINTS[0]:
            raise ratelimit.SourceUnavailable("500")
        return DARKO_CSV

    monkeypatch.setattr(darko.ratelimit, "fetch", flaky)
    frame = darko.fetch_darko()
    assert calls == list(darko.ENDPOINTS)
    assert len(frame) > darko.MIN_EXPECTED_ROWS
