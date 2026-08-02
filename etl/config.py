"""Central configuration.

Every tunable constant in the model lives here. Nothing downstream should
hard-code a threshold, weight, or cap figure — if it is arguable, it belongs in
this file so it can be found, cited, and changed in one place.

See README "Assumptions as UI" under Design patterns.
"""

from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"

# --------------------------------------------------------------------------
# Season framing (ADR-002)
# --------------------------------------------------------------------------
# Production comes from the completed season; salary from the active contract
# year. BBRef labels a season by its end year: 2025-26 -> 2026.

STATS_SEASON_END_YEAR = 2026        # 2025-26 season
STATS_SEASON_LABEL = "2025-26"
SALARY_SEASON_LABEL = "2026-27"

# --------------------------------------------------------------------------
# Salary cap figures (verified 2026-08-01, pr.nba.com)
# --------------------------------------------------------------------------

SALARY_CAP = 164_961_000            # 2026-27
LUXURY_TAX = 200_428_000
FIRST_APRON = 209_015_000
SECOND_APRON = 221_686_000

PRIOR_SALARY_CAP = 154_647_000      # 2025-26, for year-over-year context

# --------------------------------------------------------------------------
# Valuation (ADR-006)
# --------------------------------------------------------------------------
# Two denominators, both computed, toggleable in the UI.
#
# "naive"       - Cap / 41. Assumes a replacement-level roster wins 0 games.
# "replacement" - Cap / 32.8. BBRef pegs replacement level at a .100 win
#                 percentage, i.e. 8.2 wins per 82 games, so wins *above
#                 replacement* available to a team is 41 - 8.2 = 32.8.
#
# The naive figure inflates how overpaid everyone looks by roughly 20%.

WINS_PER_TEAM = 41.0
REPLACEMENT_WIN_PCT = 0.100
GAMES_PER_SEASON = 82
REPLACEMENT_WINS = REPLACEMENT_WIN_PCT * GAMES_PER_SEASON        # 8.2
WINS_ABOVE_REPLACEMENT_PER_TEAM = WINS_PER_TEAM - REPLACEMENT_WINS  # 32.8

DOLLARS_PER_WIN = {
    "naive": SALARY_CAP / WINS_PER_TEAM,                          # ~$4.02M
    "replacement": SALARY_CAP / WINS_ABOVE_REPLACEMENT_PER_TEAM,  # ~$5.03M
}
DEFAULT_DOLLARS_PER_WIN = "replacement"

# --------------------------------------------------------------------------
# WAR conversion constants
# --------------------------------------------------------------------------
# BBRef's documented conversion: VORP is expressed in points above replacement
# per 100 team possessions; multiplying by 2.7 yields wins. VORP is already
# replacement-adjusted (baseline -2.0 BPM), so it needs no further offset and
# serves as the calibration anchor for the other two estimates.

VORP_TO_WINS = 2.7
BPM_REPLACEMENT_LEVEL = -2.0

# DARKO DPM is points per 100 possessions. Replacement level and the
# points-per-win divisor are starting values; war.py recalibrates them
# empirically so the DARKO league total agrees with the VORP league total.
DARKO_REPLACEMENT_DPM = -2.5
POINTS_PER_WIN = 32.0

# Tolerance for the cross-metric calibration assertion in build verification.
WAR_CALIBRATION_TOLERANCE = 0.05    # 5%

# --------------------------------------------------------------------------
# Player scope (ADR-008)
# --------------------------------------------------------------------------
# Per-minute metrics are unstable at low sample. Players below the floor are
# excluded from the chart and from the regression fit.

MIN_MINUTES_PLAYED = 500
MIN_GAMES_PLAYED = 20

# --------------------------------------------------------------------------
# Composite Production Score weights (ADR-005)
# --------------------------------------------------------------------------
# Components are percentile-ranked league-wide before weighting. Percentiles
# rather than z-scores: these distributions are right-skewed and heavy-tailed,
# and a handful of superstars would otherwise compress everyone else into an
# indistinguishable clump.

COMPOSITE_WEIGHTS = {
    "war_blended": 0.40,      # volume-inclusive total impact
    "darko_dpm": 0.25,        # per-possession impact, on-off informed
    "ws48": 0.15,             # per-minute efficiency
    "ts_residual": 0.10,      # efficiency above expectation at that usage
    "availability": 0.10,     # durability
}

# --------------------------------------------------------------------------
# Contract classification (ADR-005 colour coding / clustering)
# --------------------------------------------------------------------------
# Contracts are classified on TWO ORTHOGONAL AXES. See transform/contract_type.py.
#
#   contract_type   HOW the deal was acquired:
#                   rookie_scale | free_agent | extension | minimum | two_way
#                   | unknown. BBRef's payroll notes are authoritative here —
#                   the prose says "Signed 4-yr rookie scale contract" outright.
#
#   salary_tier     WHAT it pays:
#                   designated_veteran | max | mle | minimum | rookie_scale |
#                   standard | unknown. A pure function of cap share and service
#                   time against the published CBA tables. No source overrides it.
#
# These were one field and that was a modelling error. "Extension" and "max" are
# orthogonal: Jokic is on an extension AND a max. One enum cannot hold both, so
# whichever source wrote last destroyed the other fact — which is what happened
# on live data, where BBRef's "Extension" note swallowed every max deal and left
# the "Max / Designated Veteran" colour category empty. minimum and rookie_scale
# legitimately appear on both axes; that is expected, not duplication.
#
# CBA SUPPRESSION. A contract is suppressed if EITHER axis says so, because
# either axis is sufficient evidence that the price was set by a schedule rather
# than a negotiation. Suppressed contracts are still plotted; they just do not
# get a vote in the regression that defines market price. See README
# "Regression population".
#
# "mle" is deliberately NOT suppressed. The mid-level exception is a ceiling,
# not a fixed scale like the rookie or minimum tables, and most MLE deals are
# negotiated below it — they are real market outcomes. Excluding them would
# remove the league's entire middle class from the regression, leaving the line
# fit on only stars and cheap veterans, which would bow it badly.

SUPPRESSED_CONTRACT_TYPES = {"two_way", "rookie_scale", "minimum"}
SUPPRESSED_SALARY_TIERS = {"rookie_scale", "minimum"}
MARKET_PRICED_SALARY_TIERS = {"designated_veteran", "max", "mle", "standard"}

# Union of every label that marks a contract CBA-suppressed on either axis.
# Kept as a single flat set for source adapters that only need to know which
# labels are readable off the payroll notes.
SUPPRESSED_TYPES = SUPPRESSED_CONTRACT_TYPES | SUPPRESSED_SALARY_TIERS

# Minimum salary floor, used when flooring negative expected salary.
#
# DERIVED, NOT HARDCODED. The CBA sets the minimum scale as a fixed percentage
# of the salary cap, so a literal goes stale the moment the cap moves — and it
# had: the previous hardcoded 1_272_870 is the 2025-26 figure
# (154,647,000 × 0.8231% = 1,272,899), left behind when the cap rose to
# 164,961,000. That is a silent, plausible-looking $85k error in every floored
# player's expected salary, and it pushed genuine veteran-minimum deals out of
# the "minimum" band and into the market regression.
#
# Verified against live data: the 10-year veteran minimum derives to $3,876,502
# and LeBron James's actual 2026-27 cap hit is $3,876,529 — a $27 difference.
MINIMUM_SALARY_CAP_SHARE = 0.008231
ROOKIE_MINIMUM_SALARY = round(SALARY_CAP * MINIMUM_SALARY_CAP_SHARE)

# Multipliers on the rookie minimum by years of service (0-10+). The CBA
# publishes these as a table; expressing them as ratios keeps the whole scale
# tied to the cap so no single entry can drift independently.
MINIMUM_SALARY_MULTIPLIERS = {
    0: 1.000,
    1: 1.610,
    2: 1.803,
    3: 1.868,
    4: 1.935,
    5: 2.096,
    6: 2.257,
    7: 2.418,
    8: 2.579,
    9: 2.592,
    10: 2.855,
}

# --------------------------------------------------------------------------
# Scraping etiquette
# --------------------------------------------------------------------------
# Sports-Reference blocks above 20 req/min and jails offenders for up to a day.
# We target well under the limit; a full league pull is only ~30 requests.

SPORTS_REFERENCE_MAX_RPM = 18
SPOTRAC_MAX_RPM = 20
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3

# When a 429 arrives, Sports-Reference sets Retry-After to the remaining jail
# time, which is up to an hour. Sleeping that off inside a retry loop would
# block the build for hours and, in CI, burn the job's entire timeout while
# producing nothing. Past this ceiling we give up on the request immediately
# and let the caller fall back to cache or fail loudly.
MAX_429_BACKOFF_SECONDS = 90

# The in-process token bucket cannot see other processes. When the nightly
# build, a test run, and a developer's REPL all hit Basketball-Reference at
# once, each believes it is under the limit while the host sees the sum — which
# is exactly how a 20 req/min budget turns into a 24-hour ban. Requests are
# therefore also gated through a lock file shared by every process on this
# machine.
RATELIMIT_STATE_DIR = DATA_RAW / ".ratelimit"
USER_AGENT = (
    "nba-contract-value-dashboard/0.1 "
    "(open-source research project; contact via repository issues)"
)

# Cache TTL for raw scrapes, in hours. The nightly job runs on a ~24h cadence;
# a slightly shorter TTL means a manually re-run build refetches rather than
# silently reusing yesterday's HTML.
RAW_CACHE_TTL_HOURS = 20

# --------------------------------------------------------------------------
# Headshots
# --------------------------------------------------------------------------
# Public, unauthenticated NBA CDN. player_id comes from the offline
# nba_api.stats.static.players table — see ADR-010.

HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"
