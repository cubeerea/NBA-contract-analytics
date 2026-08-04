[← Back to README](README.md)

## Modeling specification

### Wins Above Replacement — the dollar-model input

Three independent estimates, each calibrated to a common league total, then averaged. Disagreement between them
is signal: the spread becomes an uncertainty band on the player detail view rather than being averaged away.

| Source | Conversion |
|---|---|
| VORP | `WAR = VORP × 2.7` — BBRef's documented conversion; already replacement-adjusted at −2.0 BPM |
| Win Shares | `WAR = WS − (replacement_WS48 × MP / 48)`, with `replacement_WS48` calibrated so the league total matches VORP's |
| DARKO DPM | `WAR = (DPM − replacement_DPM) × possessions / 100 / points_per_win`, calibrated to the same total |

### Composite Production Score — X-axis default, 0–100

Each component is percentile-ranked league-wide, then weighted. Percentile normalization rather than z-scoring
is deliberate: these distributions are right-skewed and heavy-tailed, and percentiles keep a handful of
superstars from compressing everyone else into an indistinguishable clump.

| Component | Weight | Captures |
|---|---|---|
| Blended WAR | 40% | Volume-inclusive total impact |
| DARKO DPM (rate) | 25% | Per-possession impact, on-off informed |
| WS/48 | 15% | Per-minute efficiency |
| TS% residual vs usage | 10% | Efficiency relative to a league fit of `TS% ~ USG%` — see [§5](#5-the-ts-residual--and-why-it-does-not-do-what-it-claims) |
| Availability | 10% | Durability — `min(MP, GP × 36) / (82 × 36)` |

### How the production score is calculated

This is what the shipped code does. Implementation: [`etl/transform/war.py`](etl/transform/war.py),
[`etl/transform/composite.py`](etl/transform/composite.py), [`etl/config.py`](etl/config.py). Every constant
quoted is read back from `data/processed/meta.json` for the build of **2026-08-02** (334 scored players). Three
kinds of quantity appear below and are easy to mix up: **win units** (WAR), **percentiles** (0–100, rank within
the scored population), and **dollars / cap share**. The composite is a weighted average of percentiles and is
therefore *not* in win units — which is why the valuation model is kept separable from it
([§8](#8-how-the-score-is-used)).

#### 1. The inputs

| Column | Meaning | Source |
|---|---|---|
| `games`, `minutes` | GP and total MP (not per-game) | Basketball-Reference |
| `ts_pct`, `usg_pct` | True shooting %, usage rate | Basketball-Reference |
| `ws`, `ws_per_48` | Win Shares, Win Shares per 48 | Basketball-Reference |
| `vorp`, `bpm` | Value Over Replacement Player, Box Plus/Minus | Basketball-Reference |
| `darko_dpm` | Daily Plus-Minus, points per 100 possessions | DARKO (public Google Sheet) |

Nothing else feeds the score — no proprietary input, no hand-tuned per-player adjustment. A player is scored only
if he cleared **both** `MIN_MINUTES_PLAYED = 500` and `MIN_GAMES_PLAYED = 20` (`etl/build.py`, `_apply_scope`),
keeping **334** and dropping **59** contracted players. The floor is applied *before* percentiles are taken,
which is load-bearing: ranking a 40-minute call-up alongside the league would compress everyone else toward the
middle of the axis.

#### 2. The three WAR estimates, and why they are calibrated

WS sums to *team wins* (a replacement roster still wins about 8 per 82, so raw WS is not "above replacement" at
all). VORP is points above a −2.0 BPM replacement per 100 team possessions. DPM is a pure rate with no volume
term and no fixed baseline. Averaging them as published would inherit whichever metric carries the largest
league total, and the resulting dollars-per-win would be off by tens of percent. So each is converted to wins
above replacement and then **calibrated**: its one free constant is solved so its league total equals VORP's.
VORP is the anchor because both its conversion (× 2.7) and its baseline (−2.0 BPM) are documented by
Basketball-Reference and are not ours to invent.

```
war_vorp  = VORP × 2.7                                         (anchor; no free constant)

war_ws    = WS − replacement_ws48 × MP / 48
            where  replacement_ws48 = 48 × (Σ WS − anchor_total) / Σ MP

possessions = MP × league_pace / 48
war_darko = (DPM − replacement_dpm) × possessions / 100 / points_per_win
            where  replacement_dpm =
                   (Σ DPM·poss − 100 × points_per_win × anchor_total) / Σ poss

war_blended = mean(war_vorp, war_ws, war_darko)     # over whichever estimates exist
```

Both calibrations are closed-form, not fudge factors: each estimate is linear in its unknown, so the constant
that equates league totals solves exactly. The DARKO equation is underdetermined (one equation, two unknowns),
so `points_per_win` is held at 32 — roughly 32 points per win is an empirical property of NBA scoring margin,
while "replacement level" is a modelling convention with no ground truth. Putting the error in the baseline
shifts every player by a constant per-minute amount; putting it in `points_per_win` would distort the *gap*
between stars and rotation players, which is worse. Each estimate is calibrated on the subset where it *and* the
anchor both exist, so partial coverage never drags a baseline down.

| Fitted constant, 2026-08-02 build (`meta.war_constants`) | Value | Status |
|---|---|---|
| `vorp_to_wins` | 2.7 | fixed (BBRef) |
| `league_pace` | 99.0 poss / 48 min | fixed assumption |
| `points_per_win` | 32.0 | fixed assumption |
| `replacement_ws48` | **0.029689** WS per 48 | solved |
| `replacement_dpm` | **−2.2887** pts / 100 poss | solved |

Realised league totals in win units: `war_vorp` **820.53**, `war_ws` **820.53**, `war_darko` **820.53**, each at
334 / 334 coverage. The three agreeing to the cent is what makes the mean a legitimate quantity rather than an
average of three different scales; the build asserts agreement within `WAR_CALIBRATION_TOLERANCE` (5%) and fails
loudly otherwise. Disagreement *between* players is kept: `war_spread` (max − min) is the uncertainty band on
the player card.

#### 3. The five components and their weights

From `config.COMPOSITE_WEIGHTS`, echoed to `meta.composite_weights`. No component was dropped in this build, so
requested and effective weights are identical.

| Weight key | Underlying column | Weight | Units before ranking | Captures |
|---|---|---|---|---|
| `war_blended` | `war_blended` | **0.40** | wins | Volume-inclusive total impact |
| `darko_dpm` | `darko_dpm` | **0.25** | pts / 100 poss | Per-possession impact, on-off informed |
| `ws48` | `ws_per_48` | **0.15** | WS per 48 min | Per-minute efficiency |
| `ts_residual` | `ts_residual` | **0.10** | TS% points | Efficiency vs. the league `TS% ~ USG%` fit |
| `availability` | `availability` | **0.10** | fraction of a season | Durability |

Two of the five are derived rather than read off a source:

```
ts_residual  = TS% − (slope × USG% + intercept)          # league OLS fit, see §5
availability = min(MP, GP × 36) / (82 × 36),  clipped to [0, 1]
```

The 36 mpg cap is deliberate: 36 is a full starter's load, and a 38-mpg season is a statement about role rather
than durability — that extra volume is already the largest term in `war_blended`. Both halves of `GP × MP` are
present, but the minutes half saturates. Weights are then renormalised over the components that actually
resolved for that player, so a missing DARKO row scores him on his other four rather than pushing him a quarter
of the way down the axis for a data gap; `composite_n_components` is 5 for all 334 players in this build.

#### 4. Percentile normalization, not z-scores

Each component is percentile-ranked across the whole scored population before any weighting
(`series.rank(pct=True, method="average") × 100`). Ties share the average rank; only the ranking survives into
the score, not the original units. The reason is distributional: WAR, DPM and WS/48 are right-skewed and
heavy-tailed — Jokić's blended WAR is roughly six standard deviations above the rotation-player mean — and under
z-scoring one player like that dominates the blend and pins the 20th through 80th percentile into a visually
indistinguishable clump, when separating the middle of the league is where nearly every contract decision lives.
The cost is real: percentile ranking **destroys magnitude** — the gap between the best and second-best player is
one rank, exactly like the gap between 200th and 201st. That is why raw blended WAR is available as an alternate
X axis, and why the model line (§8) is fit separately in win units.

#### 5. The TS% residual — and why it does not do what it claims

The intent: regress TS% on USG% league-wide and score each player on his residual, so a high-usage creator is
credited for beating the efficiency expectation *at that volume* rather than penalised for not shooting like a
play-finisher. The fit actually obtained (`meta.composite_fit.ts_fit`):

```
TS% = −0.00032309 × USG% + 0.58843        r² = 0.0013     n = 334
```

**This does not survive contact with the data.** r² = 0.0013 means usage explains about one-tenth of one percent
of league-wide TS% variance. Across the observed usage range (8.5 to 38.1) the fitted line moves predicted TS%
by 0.0096 — under one point of true shooting — while the standard deviation of TS% is 0.050. `ts_residual`
therefore correlates with raw `ts_pct` at **r = 0.999** (Spearman 0.999), and after ranking the component is
indistinguishable from "percentile of raw TS%". So the honest description of this 10% component is **a recentred
TS%**, not "efficiency above expectation at that usage"; the intent above is design history, not shipped
behaviour (see [README § Known limitations](README.md#known-limitations) item 6). The mechanism is not wrong, just near-inert at
league scale, because usage barely predicts efficiency once the population is restricted to 500+ minute rotation
players; a working version would need a stronger conditioning variable (shot location mix, self-created share)
or a within-role fit rather than one league-wide line. The code degrades safely: fewer than three usable
players, or no variance in usage, yields all-zero residuals contributing an identical percentile to everyone.

#### 6. The final formula

For player *i*, with `P(x)` the percentile rank within the scored population:

```
composite_score_i =
      0.40 × P(war_blended)_i
    + 0.25 × P(darko_dpm)_i
    + 0.15 × P(ws_per_48)_i
    + 0.10 × P(ts_residual)_i
    + 0.10 × P(availability)_i
    ────────────────────────────
      Σ of the weights whose component resolved for player i
```

Each `P(·)` is on 0–100 and the weights sum to 1.0, so the result is on 0–100. It is a weighted average of
percentiles, not a percentile itself — a player would have to top all five components to reach 100. Observed
range on this build: **2.50 to 98.31**, median **51.19**, Jokić at the top.

#### 7. Worked example — Jalen Brunson, composite 91.28

All figures from `data/processed/players.json` and `meta.json`, 2026-08-02 build.

| GP | MP | TS% | USG% | WS | WS/48 | VORP | DARKO DPM |
|---|---|---|---|---|---|---|---|
| 74 | 2,590 | .580 | 30.4 | 8.8 | .163 | 3.3 | +3.39 |

**Steps 1–2 — the three WAR estimates (win units) and the two derived components**

```
war_vorp  = 3.3 × 2.7                                        =  8.910000
war_ws    = 8.8 − 0.029689 × 2590 / 48
          = 8.8 − 1.601957                                   =  7.198043
poss      = 2590 × 99 / 48                                   =  5341.875
war_darko = (3.39 − (−2.2887)) × 5341.875 / 100 / 32
          = 5.678726 × 53.41875 / 32                         =  9.479702

war_blended  = (8.910000 + 7.198043 + 9.479702) / 3          =  8.529248

ts_residual  = 0.580 − (−0.00032309 × 30.4 + 0.58843)
             = 0.580 − 0.578612                              =  0.001388
availability = min(2590, 74 × 36 = 2664) / (82 × 36 = 2952)
             = 2590 / 2952                                   =  0.877371
```

At a 30.4 usage rate — well into primary-creator territory — the expected-TS% correction is under one
thousandth, so the residual is essentially "his TS% minus the league mean TS%".

**Step 3 — percentile-rank each component against the other 333 players**

| Component | Value | Rank (of 334) | Percentile | Weight | Contribution |
|---|---|---|---|---|---|
| `war_blended` | 8.529248 wins | 323 | 96.7066 | 0.40 | 38.682635 |
| `darko_dpm` | +3.39 pts/100 | 323 | 96.7066 | 0.25 | 24.176647 |
| `ws_per_48` | 0.163 | 299.5 | 89.6707 | 0.15 | 13.450599 |
| `ts_residual` | 0.001388 | 179 | 53.5928 | 0.10 | 5.359281 |
| `availability` | 0.877371 | 321 | 96.1078 | 0.10 | 9.610778 |
| | | | | **1.00** | **91.279940** |

Published `composite_score`: **91.2799401198** — the sum reconciles to within 4 × 10⁻¹¹, i.e. exactly, up to
float noise. The 179th-of-334 TS% residual is the flat-fit problem in miniature: a 30-usage guard shooting .580
lands at the *median* of the efficiency component and drags roughly 4.4 points off his composite. Whether that
is a fair verdict on his scoring is exactly the question §5 says the residual is not equipped to answer.

**Step 4 — how that score prices out** (replacement denominator, $5,029,299/win)

```
expected_salary = 8.529248 × 5,029,299                       = $42,896,139
cap hit                                                       = $37,739,521
surplus         = 42,896,139 − 37,739,521                     = +$5,156,618

cap_pct         = 37,739,521 / 164,961,000                    =  0.228778
market line     = 0.00203867 × 91.27994 + 0.01823072          =  0.204321
cap_pct_residual= 0.228778 − 0.204321                          = +0.024458
```

The market pays about 20.4% of the cap for a 91.3 composite and Brunson is on 22.9% — roughly **2.4 points of
cap above market rate**, a modest overpay by the empirical line even though the model line (which prices his win
total directly) calls him a small bargain. The two lines disagreeing is the point
([ADR-013](README.md#adr-013--two-regression-lines-not-one)).

#### 8. How the score is used

The composite is the **X axis**; Y is the 2026-27 cap hit as a share of the cap. The two lines over that scatter
are fit differently:

| Line | Fit on | Population | This build |
|---|---|---|---|
| **Market** — `cap_pct ~ composite_score` | market-priced contracts only | **190** of 334 | slope 0.00203867, intercept 0.01823, r² 0.241 |
| **Model** — `(war_blended × $/win) / cap ~ composite_score` | every scored player | 334 | slope 0.00292534, intercept −0.07181, r² 0.795 |

The market fit **excludes CBA-suppressed contracts** — rookie-scale, minimum and two-way deals, on either the
"how it was acquired" or "what it pays" axis. Those players are still plotted and still get residuals; they just
do not get a vote in defining market price, because their salary was set by a schedule rather than a
negotiation. Mid-level exception deals *are* counted as market-priced: the MLE is a ceiling rather than a fixed
scale and most MLE deals sign below it, so excluding them would strip the league's middle class out of the fit
and leave the line determined by stars and cheap veterans alone.

Expected salary and surplus never touch the composite — they run off blended WAR in win units:

```
expected_salary = max(war_blended × $/win, rookie minimum)      # $/win = $4.02M or $5.03M, see ADR-006
surplus         = expected_salary − 2026-27 cap hit
```

The separation is deliberate. The composite is a *readability* device (percentiles spread the middle of the
league out legibly); the valuation is a *quantitative* claim (win units × dollars per win). Mixing them would
make surplus depend on the shape of a percentile distribution, which has no dollar meaning. It is also why the X
axis has a **toggle to raw blended WAR**: on that axis both regressions are in win units against cap share, so
the fitted slope is literally dollars-per-win
([ADR-005](README.md#adr-005--composite-score-defensible-default-fully-documented)). Expected salary is floored at the
rookie minimum rather than allowed to go negative, and affected players are flagged — an unfloored model
produces surplus values more negative than the player's entire salary, which is not a coherent statement about a
contract. To reproduce any of this, `python -m etl.build` regenerates `data/processed/*.json` and re-emits every
constant above into `meta.json`, so a player's score can be re-derived from the published artifacts alone.

### Known data-source hazards

Recorded because each cost real debugging time and would silently recur.

| Hazard | Detail and fix |
|---|---|
| **BBRef serves UTF-8 with no charset header** | `Content-Type: text/html` without a `charset` makes `requests` fall back to ISO-8859-1 per RFC 2616, so `Alperen Şengün` decodes as `Alperen Å\x9eengÃ¼n`. The corruption lands in `resp.text` and therefore in the on-disk cache, and it hits precisely the accented names that are hardest to match anyway — a silent join loss disguised as a matching problem. Fixed once in `ratelimit.fetch` by trusting the content sniff over the absent header; the scrape cache is versioned (`CACHE_VERSION`) so entries written by the old logic are invalidated rather than reused. |
| **BBRef hides tables inside HTML comments** | Several tables, including the contracts index, are served as `<!-- <table> ... -->`. Parsers must check both the live DOM and comment blocks. |
| **Traded players combine as `2TM`/`3TM`/`4TM`, not `TOT`** | The 2025-26 page contains no `TOT` rows at all. 72 players require collapsing; the combined row is emitted first, carries no `partial_table` class, and the name cell's `csk` sort key encodes stint order explicitly. |
| **Spotrac's contracts table is JS-paginated at 100 rows** | Regardless of any `limit-N` path segment — which is why Spotrac is scoped to enrichment on the largest contracts (ADR-003). |
| **A per-process rate limiter is not a rate limiter** | Running the orchestrator alongside three parallel scrapers drew a BBRef 429 with `Retry-After: 3597` — a one-hour ban — even though every individual process was correctly pacing below 18 req/min. Each interpreter had its own token bucket and believed it was compliant; the server saw the sum. Requests now pass through a `flock`-guarded sliding window in `data/raw/.ratelimit/` shared by every process on the machine, and a four-process test asserts they collectively receive exactly the budget, not four times it. |
| **Never sleep off a `Retry-After` you did not bound** | The original handler slept for whatever the header said, inside a three-attempt retry loop — so a one-hour ban would have stalled the build for three hours producing nothing, and in CI would have silently consumed the entire job timeout. Waits above `MAX_429_BACKOFF_SECONDS` (90s) now fail fast so the caller can fall back to cache or surface the ban immediately. |

