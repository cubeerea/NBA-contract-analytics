[← Back to README](README.md)

# Modeling specification

What the shipped code does — [`etl/transform/war.py`](etl/transform/war.py),
[`etl/transform/composite.py`](etl/transform/composite.py), [`etl/config.py`](etl/config.py). Every constant is
read back from `data/processed/meta.json` for the **2026-08-02** build.

Inputs are Basketball-Reference columns (`games`, `minutes`, `ts_pct`, `usg_pct`, `ws`, `ws_per_48`, `vorp`)
plus DARKO's `darko_dpm`; nothing else feeds the score. A player is scored only if he cleared
`MIN_MINUTES_PLAYED = 500` and `MIN_GAMES_PLAYED = 20` — **334 kept, 59 contracted players dropped**. The floor
applies *before* percentiles are taken, since ranking a 40-minute call-up compresses everyone toward the middle.

## WAR calibration

WS sums to team wins, VORP is points above a −2.0 BPM replacement, DPM is a rate with no baseline. Averaging
them as published would inherit whichever carries the largest league total, so each becomes wins above
replacement and is calibrated until its league total equals VORP's. VORP anchors: its conversion and baseline
are documented by Basketball-Reference and are not ours to invent.

```
war_vorp    = VORP × 2.7                                            # anchor, no free constant
war_ws      = WS − replacement_ws48 × MP / 48    where  replacement_ws48 = 48 × (Σ WS − anchor) / Σ MP
war_darko   = (DPM − replacement_dpm) × poss / 100 / points_per_win  # poss = MP × league_pace / 48
              where  replacement_dpm = (Σ DPM·poss − 100 × points_per_win × anchor) / Σ poss
war_blended = mean(war_vorp, war_ws, war_darko)                     # over whichever estimates exist
```

Both calibrations are closed-form — each estimate is linear in its unknown, so the constant equating league
totals solves exactly. Fixed: `vorp_to_wins` 2.7, `league_pace` 99.0, `points_per_win` 32.0. Solved here:
`replacement_ws48` **0.029689**, `replacement_dpm` **−2.2887**. The DARKO equation is underdetermined, so the
error lands in the baseline, not `points_per_win` — a constant per-minute shift to everyone beats distorting
the gap between stars and rotation players. All three totals reach **820.53** win units at 334/334 coverage,
asserted within 5%. `war_spread` (max − min) is the uncertainty band on the player card.

## Composite score

`composite_score = Σ (weight × percentile) ÷ Σ weights that resolved for that player`, over these five:

| Component | Definition | Weight | Captures |
|---|---|---|---|
| `war_blended` | calibrated mean, above | **0.40** | volume-inclusive total impact |
| `darko_dpm` | source column | **0.25** | per-possession impact, on-off informed |
| `ws_per_48` | source column | **0.15** | per-minute efficiency |
| `ts_residual` | `TS% − (slope × USG% + intercept)`, league OLS fit | **0.10** | efficiency vs usage expectation |
| `availability` | `min(MP, GP × 36) / (82 × 36)`, clipped to [0, 1] | **0.10** | durability |

The 36 mpg cap is deliberate: a 38-mpg season is a statement about role, not durability, and that volume is
already the largest term in `war_blended`. Weights renormalise over resolved components, so a missing DARKO row
scores a player on his other four instead of costing him a quarter of the axis; all 334 resolved all five here.
Result is 0–100 but not a percentile — 100 means topping all five. Range **2.50–98.31**, median **51.19**.

**Percentiles, not z-scores.** Each component is ranked league-wide (`rank(pct=True, method="average") × 100`)
before weighting. WAR, DPM and WS/48 are right-skewed — Jokić's blended WAR is ~6 SD above the rotation mean,
and z-scoring lets one such player pin the 20th–80th percentile into an indistinguishable clump, exactly where
contract decisions live. The cost: ranking destroys magnitude, best-to-second being one rank just like
200th-to-201st. Hence the raw-WAR toggle, and hence the model line is fit separately in win units.

## Why the TS residual is inert

The intent was to credit a high-usage creator for beating the efficiency expectation *at that volume*. The fit
actually obtained (`meta.composite_fit.ts_fit`) is `TS% = −0.00032309 × USG% + 0.58843`, r² **0.0013**, n 334.

Usage explains one-tenth of one percent of TS% variance. Across the observed usage range (8.5–38.1) the fitted
line moves predicted TS% by 0.0096, against a TS% standard deviation of 0.050. `ts_residual` therefore tracks
raw `ts_pct` at **r = 0.999** and, after ranking, is indistinguishable from "percentile of raw TS%". The honest
description of this 10% component is **a recentred TS%**. The mechanism is not wrong, just near-inert once the
population is 500+ minute rotation players; a working version needs a stronger conditioning variable
(shot-location mix, self-created share) or a within-role fit.

## Worked example

Jalen Brunson — 74 GP, 2,590 MP, .580 TS%, 30.4 USG%, 8.8 WS, .163 WS/48, 3.3 VORP, +3.39 DPM.

| Quantity | Computation | Value | Pctile | Weight | Contribution |
|---|---|---|---|---|---|
| `war_vorp` | 3.3 × 2.7 | 8.910000 | | | |
| `war_ws` | 8.8 − 0.029689 × 2590 / 48 | 7.198043 | | | |
| `war_darko` | (3.39 + 2.2887) × (2590 × 99 / 48) / 100 / 32 | 9.479702 | | | |
| **`war_blended`** | mean of the three | **8.529248** | 96.7066 | 0.40 | 38.682635 |
| `darko_dpm` | source column | +3.39 | 96.7066 | 0.25 | 24.176647 |
| `ws_per_48` | source column | 0.163 | 89.6707 | 0.15 | 13.450599 |
| `ts_residual` | 0.580 − (−0.00032309 × 30.4 + 0.58843) | 0.001388 | 53.5928 | 0.10 | 5.359281 |
| `availability` | min(2590, 74 × 36) / (82 × 36) | 0.877371 | 96.1078 | 0.10 | 9.610778 |
| | | | | **1.00** | **91.279940** |

Published `composite_score` is **91.2799401198** — reconciles to float noise. Priced at the replacement
denominator, $5,029,299/win:

| Quantity | Computation | Value |
|---|---|---|
| `expected_salary` | 8.529248 × 5,029,299 | $42,896,139 |
| `surplus` | 42,896,139 − 37,739,521 cap hit | **+$5,156,618** |
| `cap_pct` | 37,739,521 / 164,961,000 | 0.228778 |
| market line | 0.00203867 × 91.27994 + 0.01823072 | 0.204321 |
| `cap_pct_residual` | 0.228778 − 0.204321 | **+0.024458** |

The market pays ~20.4% of the cap for a 91.3 composite and Brunson is on 22.9% — a modest overpay by the
empirical line while the model line calls him a bargain. A 30-usage guard shooting .580 landing at the *median*
of the efficiency component, costing ~4.4 composite points, is the flat fit in miniature.

## From score to dollars

| Line | Fit on | n | This build |
|---|---|---|---|
| **Market** — `cap_pct ~ composite` | market-priced contracts | 190 of 334 | slope 0.00203867, intercept 0.01823, r² 0.241 |
| **Model** — `(war_blended × $/win) / cap ~ composite` | every scored player | 334 | slope 0.00292534, intercept −0.07181, r² 0.795 |

The market fit excludes CBA-suppressed contracts — rookie-scale, minimum, two-way — on either the
acquisition-route or the price-tier axis. Those players are still plotted and still get residuals; their salary
just gets no vote in setting market price. MLE deals do count: the MLE is a ceiling, not a schedule, and
excluding it would strip the league's middle class out of the fit.

Valuation never touches the composite. It runs off blended WAR in win units:
`expected_salary = max(war_blended × $/win, rookie minimum)`, `surplus = expected_salary − cap hit`. The
composite is a readability device, valuation is a quantitative claim; mixing them would make surplus depend on
the shape of a percentile distribution. It is also why X toggles to raw blended WAR — there the fitted slope is
literally dollars-per-win. The rookie-minimum floor stops surplus going more negative than a player's entire
salary, and is flagged per player. `python -m etl.build` re-emits every constant above.

## Data source hazards

| Hazard | Detail and fix |
|---|---|
| **BBRef serves UTF-8 with no charset header** | `requests` falls back to ISO-8859-1 per RFC 2616, so `Şengün` decodes as `Å\x9eengÃ¼n` — corrupting exactly the accented names that are hardest to join. Fixed in `ratelimit.fetch` by trusting the content sniff; `CACHE_VERSION` invalidates entries written by the old logic. |
| **BBRef hides tables inside HTML comments** | Several tables, including the contracts index, ship as `<!-- <table> -->`. Parsers must check the DOM and the comment blocks. |
| **Traded players combine as `2TM`/`3TM`, not `TOT`** | The 2025-26 page has no `TOT` rows at all. 72 players need collapsing; the combined row comes first, lacks `partial_table`, and its `csk` sort key encodes stint order. |
| **Spotrac's contracts table is JS-paginated at 100 rows** | Regardless of any `limit-N` path segment — which is why Spotrac is scoped to enriching the largest contracts. |
| **A per-process rate limiter is not a rate limiter** | Four processes each pacing below 18 req/min drew a BBRef 429 with `Retry-After: 3597`; the server saw the sum. Requests now share a `flock`-guarded sliding window in `data/raw/.ratelimit/`, with a four-process test asserting one collective budget. |
| **Never sleep off an unbounded `Retry-After`** | A one-hour ban inside a three-attempt retry loop would stall the build for three hours and silently consume the CI job timeout. Waits above `MAX_429_BACKOFF_SECONDS` (90s) now fail fast. |
