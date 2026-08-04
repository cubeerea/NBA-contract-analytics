# NBA Contract Value Dashboard

Plots every contracted NBA player's on-court production against what they are paid, fits a league-average
price-per-unit-of-output line, and surfaces the largest positive and negative residuals — **the best and worst
contracts in the league**. Player headshots are the plot marks. Rebuilt nightly.

**Design principle:** every modeling assumption is visible and toggleable. Contract-value analysis is unusually
sensitive to a handful of arbitrary constants (what a win costs, what "replacement level" means, which contracts
define market price), so those choices are surfaced in the UI rather than hidden behind one confident number.

## Table of contents

- [What problem this solves](#what-problem-this-solves)
- [What the dashboard shows](#what-the-dashboard-shows) — how to read it
- [Requirements decision record](#requirements-decision-record) — verbatim Q&A
- [Architecture Decision Records](#architecture-decision-records) — ADR-001…015
- [Design patterns](#design-patterns)
- [Modeling specification](#modeling-specification) — see [METHODOLOGY.md](METHODOLOGY.md) for the full derivation, worked example, and data-source hazards
- [Known limitations](#known-limitations)
- [Verified reference data](#verified-reference-data) — constants and the first full build
- [Repository layout](#repository-layout)
- [Deployment](#deployment) — Vercel + nightly GitHub Actions

## What problem this solves

No public tool answers "which NBA contracts are actually good value?" transparently. Existing options are
**paywalled** (EPM — Dunks & Threes; LEBRON — BBall Index), **frozen** (RAPTOR, discontinued by FiveThirtyEight
in 2023), or **editorial** (hot takes rather than models). This is the modeled, reproducible, free alternative.

## What the dashboard shows

The dashboard itself carries almost no prose — numbers, labels, and faces. This section is the explanation.

**Season framing.** 2025-26 on-court production scored against 2026-27 salary. Both inputs are settled: the
season is complete and the 2026-27 cap is officially set at **$164,961,000**. The question is forward-looking —
*given what a player just did, is next season's contract good value?*
([ADR-002](#adr-002--season-framing-2025-26-production-vs-2026-27-salary))

**Who is on the chart.** Players with a 2026-27 contract who cleared a 2025-26 floor of **500 minutes and 20
games** — **334 players** on the current build, **59** otherwise-contracted players excluded by the floor
([ADR-008](#adr-008--player-scope-under-contract--minutes-threshold)); sub-500-minute samples produce wild
per-minute rates and meaningless surplus figures. Counts move slightly each nightly rebuild;
`data/processed/meta.json` carries the live figures. Every percentile in the app — detail-view bars, and each
component inside the composite — is computed against these 334, so they are percentiles *among rotation players*.

### The axes

| Axis | Quantity | Range |
|---|---|---|
| **X** | Composite production score — percentile-weighted blend of blended WAR, DARKO DPM, WS/48, TS%-vs-usage residual, availability. Switchable to raw blended WAR. | 0–100 |
| **Y** | 2026-27 cap hit as a **share of a denominator you choose**: the $164,961,000 league cap (default) or the player's own team payroll. | 0–~40% |

Y is a share rather than dollars so the chart stays comparable across seasons as the cap moves, and so vertical
position reads as "how much of a roster's money does this player consume." Each mark is the player's headshot;
clicking one pins their contract card. Weights: [METHODOLOGY.md](METHODOLOGY.md#composite-production-score--x-axis-default-0100).

### The two regression lines

| Line | What it is | Answers | Moves with the $/win toggle? |
|---|---|---|---|
| **Market line** | OLS fit of `cap_pct ~ composite_score` on market-priced contracts only — free agency, extensions, max/designated-veteran, MLE (n = 190, slope 0.00204, r² 0.241) | What teams *actually* pay for this production | **No** — it is empirical |
| **Model line** | `(blended WAR × $/win) / cap` against composite score (n = 334, r² 0.795) | What this production *should* earn | **Yes** |

Rookie-scale, minimum, and two-way deals are plotted but excluded from the market fit (rules in
[METHODOLOGY.md §8](METHODOLOGY.md#8-how-the-score-is-used)). Player residuals — and the leaderboard's surplus/shortfall — are measured
against the **market** line. The gap between the two lines is how far the league's revealed pricing sits from
the model, which is itself the interesting question ([ADR-013](#adr-013--two-regression-lines-not-one)). Both
fits are computed server-side against `cap_pct`, so switching Y to team payroll invalidates them and the client
refits.

### The denominator toggle

The cap is the league's *limit*, not a ceiling anyone is held to: Cleveland's books run to **$226.0M** against a
$164,961,000 cap (1.37×), Brooklyn's to **$150.8M** (0.91×). So "share of the cap" is one honest denominator and
"share of the payroll the contract actually sits inside" is another; both are exposed rather than one picked
silently — the same treatment [ADR-006](#adr-006--valuation-compute-both-denominators-toggle-in-ui) gives $/win.
**League cap is the default**, for two reasons. The chart's load-bearing element is one league-wide regression,
and a regression only means anything when every point is measured against the same ruler. And team payroll
inverts real comparisons: Curry ($62.6M) is paid **$4.1M more** than Anthony Davis ($58.5M) — 37.9% vs 35.4% of
the cap — yet reads as the cheaper contract on the team axis (29.7% of Golden State's payroll against 31.3% of
Washington's), purely because Golden State spent more on everyone else. That measures front-office profligacy
wearing the costume of a player-cost figure. Switching to team payroll changes four things, all visibly:

| | League cap | Team payroll |
|---|---|---|
| **Market line** | server OLS fit on `cap_pct`, untouched — the ETL already got it right and refitting would only introduce drift | plain OLS refit client-side over the same market-priced contracts (n = 190) on the active axis |
| **Model line** | one line | a **band** — one dollar figure is a different share on every roster, so it spans the leanest payroll (top edge) to the richest (bottom edge), median team as centre |
| **Residuals** | `cap_pct_residual` from the ETL | recomputed against the refit market line |
| **Y tick sub-label** | the dollar figure that share works out to | dropped — the same percentage is a different dollar figure on every team |

Refits live in `web/src/model/denominator.js`; reasoning in
[ADR-015](#adr-015--y-axis-denominator-league-cap-default-team-payroll-toggle).

### Team payroll context

Wherever a player is shown in detail — chart tooltip, pinned contract card, comparison grid, leaderboard cap-hit
cell — the dashboard states the payroll the contract sits inside, the team's rung on the CBA's four spending
lines, and how far past it the books already are. An identical $40M contract is survivable for a team with room
and punitive past the second apron, where every dollar carries repeater tax and the front office has lost salary
aggregation, sign-and-trade, and its own picks. The rung uses the same four-notch ordered meter as the team
rollup, on the neutral ramp, because blue/red is reserved for surplus polarity. The cap comes from `meta.json`;
tax and apron lines are not published there, so they are derived as multiples of the live cap in
`web/src/model/payroll.js` rather than dollar literals that go stale the moment the cap moves.

### The $/win toggle

`surplus = expected salary − cap hit`, where `expected salary = blended WAR × $/win`. That denominator is
genuinely contested, so both are exposed: **$4.02M** (naive) or **$5.03M** (replacement-adjusted, default). See
[ADR-006](#adr-006--valuation-compute-both-denominators-toggle-in-ui).

### The views

| View | What it is for |
|---|---|
| **Value scatter** | The whole league at once — shape, clusters, outliers |
| **Leaderboard** | The same data sorted, for "who exactly is #1". Rookie-scale deals structurally dominate the top; see [Known limitations](#known-limitations) |
| **Team rollup** | Surplus by franchise; a team high on this list gets more production than its payroll implies, usually good drafting still on rookie deals. Player filters apply to the surplus and WAR columns; payroll and apron state come straight from `teams.json` and are deliberately unfiltered, being properties of the franchise rather than of your selection |
| **Compare** | Up to three players side by side, with the WAR uncertainty band and league percentile bars |

### Data provenance

| What | Source |
|---|---|
| Box-score and advanced production | Basketball-Reference |
| Projective impact (DPM) | DARKO — free public Google Sheet, nightly |
| Salaries and contract years | Basketball-Reference `/contracts/`, enriched by Spotrac for structure (options, guarantees, kickers) |
| Player portraits | Public NBA CDN |

The dataset is rebuilt nightly by GitHub Actions and committed to `data/processed/`; the frontend is a static
site reading those artifacts. Nothing is fetched from stats.nba.com at any point
([ADR-010](#adr-010--no-live-statsnbacom-calls)). Before trusting a number, read
[Known limitations](#known-limitations): rookie-scale deals will always top the best-value list, box scores
undervalue defense, one season is a noisy talent estimate, and cap hit is not cash paid.

## Requirements decision record

Twelve questions asked during requirements refinement — data foundation (Q1–Q4), modeling and stack (Q5–Q8),
visualization and scope (Q9–Q12). Questions and answers are recorded verbatim because the ADRs are downstream of
them: if a premise changes, this is where to look to see which decisions must be revisited.

| # | Question | Answer (verbatim) | Drives |
|---|---|---|---|
| Q1 | EPM is paywalled (Dunks & Threes Premium+API). What's the plan for your headline metric? *(option 1 "substitute BPM/VORP/WS", option 2 "add DARKO")* | *"ideally use both 1 and 2, focus on obtain raw data. we can always implement our own formula to calculate those scores/metrics."* | [ADR-001](#adr-001--metrics-free-raw-data-own-formulas) |
| Q2 | It's August 2026 — the offseason. Which production year should be scored against which salary year? | *"2025-26 stats vs 2026-27 salary"* | [ADR-002](#adr-002--season-framing-2025-26-production-vs-2026-27-salary) |
| Q3 | How should salary and contract data be sourced? *(option 1 BBRef `/contracts/`, option 2 Spotrac)* | *"I don't care how the data is sourced, i just want it to be live data or at least be scraped periodically with a cron job. leaning towards option 2 but would rather take a hybrid approach of 1 and 2."* | [ADR-003](#adr-003--salary-sourcing-basketball-reference-primary-spotrac-enrichment) |
| Q4 | What does "live" actually mean for this dashboard? | *"Nightly scheduled refresh"* | [ADR-004](#adr-004--refresh-nightly-scheduled-etl) |
| Q5 | What stack should this be built on? | *"Doesn't really what tech stack it is, I want the data points to be the headshot of each player."* | [ADR-007](#adr-007--overlap-binned-overview-faces-on-zoom) + frontend choice |
| Q6 | The X-axis composite score needs defined weights and normalization. How do you want that decided? | *"You propose a defensible default"* | [ADR-005](#adr-005--composite-score-defensible-default-fully-documented) |
| Q7 | On the valuation methodology concerns — $/win denominator and regression fit population? | *"Compute both, toggle in UI"* | [ADR-006](#adr-006--valuation-compute-both-denominators-toggle-in-ui) |
| Q8 | How deep should trade eligibility go? (2023 CBA logic is substantial) | *"Simple flags only"* | [ADR-011](#adr-011--trade-eligibility-simple-flags-only) |
| Q9 | ~500 headshots will overlap badly on a single scatter. How should that be handled? | *"Binned overview, faces on zoom"* | [ADR-007](#adr-007--overlap-binned-overview-faces-on-zoom) |
| Q10 | Which players make it onto the chart? | *"Under contract + minutes threshold"* | [ADR-008](#adr-008--player-scope-under-contract--minutes-threshold) |
| Q11 | Where does this run? (affects the nba_api cloud-IP ban and scraping exposure) | *"GitHub Actions ETL → static hosted site"* | [ADR-009](#adr-009--deployment-github-actions-etl--static-site), forcing [ADR-010](#adr-010--no-live-statsnbacom-calls) |
| Q12 | Beyond the scatter, what else does the dashboard need? *(multi-select)* | *"Ranked surplus-value leaderboard, Team-level rollup, Player detail / comparison view"* | [ADR-012](#adr-012--supporting-views-leaderboard-team-rollup-player-detail) |

Four carry consequences beyond the ADR they name. **Q1** — the operative phrase is *focus on obtaining raw
data*, which reframes the project as an ingestion pipeline with a swappable metric layer rather than a consumer
of someone else's ratings. **Q5** — inverted the usual dependency: the stack became a *consequence* of a
rendering requirement, and deck.gl was chosen because it solves the headshot problem, not on general merit.
**Q7** — set the guiding principle that contested assumptions become features, not hidden defaults. **Q12** —
the option *not* selected was a separate usage-vs-efficiency chart, so that relationship is folded into the
composite as a residual term instead of getting its own view.

## Architecture Decision Records

### ADR-001 — Metrics: free raw data, own formulas

**Decision.** Ingest raw box-score and impact data from Basketball-Reference and DARKO; compute all derived
metrics ourselves. Do not depend on EPM. **Why.** EPM sits behind a paid subscription, making the project
non-reproducible without a key; DARKO is free, publicly downloadable, nightly, and genuinely *projective* — a
better match for "Projected Win Shares" than EPM. Owning the formulas makes the metric layer auditable and
tunable rather than a black box. **Consequence.** A metric-provider interface keeps EPM addable later behind a
config flag. Accepted tradeoff: box-score-derived metrics are weaker at off-ball and defensive impact than true
plus-minus models; DARKO's on-off component partially offsets this.

### ADR-002 — Season framing: 2025-26 production vs 2026-27 salary

**Decision.** Score completed 2025-26 production against currently-active 2026-27 contracts. **Why.**
Forward-looking framing answers "who is overpaid *right now*," the decision-relevant question, and both inputs
are settled — the season is complete and the cap is officially set at $164,961,000. **Consequence.** Players
with a 2026-27 contract but no 2025-26 NBA production (2026 draftees, international signings) have no X-value;
they become an explicit `no_prior_season` class, excluded from the regression fit rather than silently dropped
or plotted at zero.

### ADR-003 — Salary sourcing: Basketball-Reference primary, Spotrac enrichment

**Decision.** Bulk salary from BBRef `/contracts/`, contract structure from Spotrac, both scraped on schedule.
**Why.** BBRef gives complete year-by-year league coverage in ~30 polite requests; Spotrac adds the structural
detail BBRef lacks — options, guarantees, incentives, trade kickers — which the player detail view needs.
**Consequence.** Scrapers must be rate-limit-aware and fail soft: a Spotrac outage degrades enrichment but must
not break the nightly build. `jaebradley/basketball_reference_web_scraper` covers BBRef *stats* only; the
contracts pages need a purpose-built parser.

### ADR-004 — Refresh: nightly scheduled ETL

**Decision.** GitHub Actions cron rebuilds the dataset nightly and commits the output. **Why.** Contracts change
on trade/signing timescales, stats on daily timescales; nightly suits both. Real-time is impossible given source
rate limits and meaningless for season-long value metrics — one game cannot move a full-season WAR figure.

### ADR-005 — Composite score: defensible default, fully documented

**Decision.** Ship a percentile-normalized weighted blend with every weight documented, plus a UI toggle to a
raw wins-above-replacement axis. **Why.** The blend captures the multi-metric picture requested; the WAR axis
makes the regression slope directly interpretable as dollars-per-win. **Consequence.** The chart can be
*readable* or *rigorous* without forking the codebase.

### ADR-006 — Valuation: compute both denominators, toggle in UI

**Decision.** Expose both the naive `Cap / 41` and the replacement-adjusted `Cap / 32.8` dollars-per-win figures
as a user-facing toggle; default to replacement-adjusted. **Why.** The naive denominator assumes a
replacement-level roster wins zero games. It actually wins roughly 8–15, so the naive figure inflates how
overpaid everyone appears by about 20% across the board. **Consequence.** A hidden bias becomes an explorable
feature, and the precedent — compute both, expose the switch, name the default — is reused by ADR-015.

| Denominator | $/win | Assumption |
|---|---|---|
| `164,961,000 / 41` | $4.02M | A replacement roster wins 0 games |
| `164,961,000 / 32.8` | $5.03M | Replacement level is .100 win pct (8.2 wins/82) |

### ADR-007 — Overlap: binned overview, faces on zoom

**Decision.** Render density bins when zoomed out; swap to headshot icons on zoom-in. **Why.** ~500 headshots in
one viewport collide into illegibility. Binning keeps the macro distribution readable and defers per-player
detail to the zoom level with room for it; it is also the fastest option, since images rasterize only once they
are distinguishable. **Consequence.** Dictates the frontend stack: deck.gl provides `HexagonLayer` for the
binned tier and `IconLayer` with dynamically-packed atlases built from remote image URLs (documented for exactly
this profile-picture use case), with zoom-threshold layer swapping as a native pattern.

### ADR-008 — Player scope: under contract + minutes threshold

**Decision.** Include players with a 2026-27 contract who cleared a 2025-26 playing-time floor; default 500 MP,
configurable. **Why.** Per-minute metrics are unstable at low sample — a player with 40 career minutes produces
a wild, meaningless surplus figure that would dominate the outlier view and discredit the chart.

### ADR-009 — Deployment: GitHub Actions ETL → static site

**Decision.** A nightly Actions job runs the ETL and commits processed data; a static frontend consumes the
committed artifacts. **Why.** Free, public, no server to maintain, fully reproducible from the repo — viable
specifically because ADR-010 removes the stats.nba.com dependency.

### ADR-010 — No live stats.nba.com calls

**Decision.** Use `nba_api` **only** for `stats.static.players`, which reads bundled local data and issues no
HTTP request; all statistics come from Basketball-Reference. **Why.** stats.nba.com blacklists datacenter IP
ranges and sits behind Akamai bot protection that fingerprints TLS handshakes — it would break any cloud-run
ETL, including GitHub Actions. The offline static module still yields the NBA player IDs needed to construct
headshot URLs, eliminating the ban surface entirely. **Consequence.** This is the load-bearing decision that
makes ADR-009 work: no proxy, no residential IP, no scraping infrastructure beyond ordinary HTTP.

### ADR-011 — Trade eligibility: simple flags only

**Decision.** Date-based restrictions, no-trade clauses, recently-signed ineligibility; no apron-aware salary
matching. **Why.** Full 2023-CBA matching requires modeling every team's live payroll state against
first/second apron rules, base year compensation, and the poison pill provision — a project comparable in size
to the dashboard itself. Flags cover the common cases cheaply. **Consequence.** Deferred, not forgotten:
ADR-012's team rollup produces the payroll state a future matching engine would need.

### ADR-012 — Supporting views: leaderboard, team rollup, player detail

**Decision.** Three views beyond the scatter. **Why.** The scatter shows *shape*; a sortable table answers "who
exactly is #1"; the team rollup identifies which front offices extract the most value per dollar.
**Consequence.** The rollup incidentally produces the team payroll state ADR-011 defers to a future matching
engine.

### ADR-013 — Two regression lines, not one

**Decision.** Draw both an empirical **market line** and a model-derived **model line** over the same scatter
(definitions and fitted values in [The two regression lines](#the-two-regression-lines)).

**Why.** The original spec called for one "league average cost per unit of output" line that would also move
with the $/win toggle (ADR-006). Those requirements are incompatible, and that exposed a genuine conflation: an
empirical fit of observed salaries cannot depend on our choice of $/win, because that constant appears nowhere
in the data being fitted. Showing both is strictly more informative than either — the gap between them is the
degree to which the league's revealed pricing disagrees with the model. **Consequence.** Player residuals rank
contracts against the **market** line, so "underpaid" means underpaid relative to what teams actually pay
comparable players — the more defensible claim.

### ADR-014 — DARKO joins on NBA player ID, not name

**Decision.** Join DARKO on its published `NBA ID` column; fall back to normalized-name matching only for rows
lacking one. **Why.** The sheet publishes an NBA player ID for all 530 rows, and an exact integer key beats
fuzzy name matching while sidestepping accents entirely — 35 of 530 DARKO names differ from Basketball-Reference
by diacritics alone (`Nikola Jokic` vs `Nikola Jokić`). **Consequence.** Name matching survives as a fallback,
so a future sheet change that drops the ID column degrades rather than fails.

### ADR-015 — Y-axis denominator: league cap default, team payroll toggle

**Decision.** Keep **share of the league cap** as the default Y denominator; add a toggle to **share of the
player's own team payroll**. In team mode, refit both reference lines client-side rather than reusing the
server's cap-relative fits.

**Why.** The objection that prompted this is factually correct — the cap is the league's limit, not a ceiling
teams are held to (Cleveland $226.0M, Brooklyn $150.8M against a $164,961,000 cap). Two things keep the cap as
default: the league-wide market line only means something under a shared ruler, and team payroll inverts real
comparisons (Curry costs $4.1M more than Davis yet plots cheaper on the team axis). Neither makes team payroll
*wrong*, so this follows the precedent ADR-006 set: compute both, expose the switch, name the default and say
why. Worked numbers in [The denominator toggle](#the-denominator-toggle).

**Consequence.** Team mode requires a client-side refit, and that is the sharp edge: both reference lines and
`cap_pct_residual` are computed by the ETL against `cap_pct` and would be a wrong answer drawn in ink on a
team-relative axis. The model reference *has* to become a band — one dollar figure divided by 30 payrolls is 30
shares, and drawing one line would privilege one team; the band's width **is** the objection this ADR answers,
made visible. Dual-unit Y ticks are dropped for the same reason. Independently of the toggle, team payroll is
promoted to a first-class fact throughout (tooltip, contract card, comparison grid, leaderboard), with tax and
apron thresholds derived as multiples of `meta.salary_cap` in `web/src/model/payroll.js`, deferring to
`meta.json` if a future build ever publishes them outright.

## Design patterns

| Pattern | Substance |
|---|---|
| **Canonical identity spine** | All sources join through one crosswalk table (BBRef slug ↔ NBA player ID ↔ Spotrac slug), built and cached before any other stage. Name joins across basketball sources fail on suffixes (Jr./III), accents, and collisions; centralizing identity means that failure mode exists in exactly one place and is asserted against in CI. |
| **Source adapter** | Each source implements the same `fetch() → DataFrame` contract behind `etl/sources/`. Adding EPM, LEBRON, or another salary source is one adapter, not a transform-layer change. |
| **Calibrate before blending** | WS sums to team wins, VORP is replacement-adjusted, DPM is points per 100 possessions. Each is converted to wins-above-replacement and calibrated so its league total agrees *before* averaging. Averaging uncalibrated metrics is the single most common error in public contract-value analysis. |
| **Fail-soft ingestion** | Sources are ranked essential vs enrichment. Essential failure fails the build loudly; enrichment failure logs and degrades. The nightly job must not go dark because Spotrac changed a CSS class. |
| **Precompute heavy, recompute light** | The ETL emits static artifacts; the client recomputes whatever a UI toggle invalidates. The $/win switch (ADR-006) is a client-side multiply against precomputed WAR — no round trip, no rebuild. The corollary: a toggle that invalidates a precomputed figure must recompute it rather than display it anyway, which is why the Y-denominator switch (ADR-015) refits both regression lines client-side. |
| **Zoom-driven level of detail** | The chart is layered by information density, not by data subset. Every player is always present; only the representation changes with zoom. Nothing is filtered out to make it look clean. |
| **Assumptions as UI** | Where the literature genuinely disagrees, expose the choice rather than picking silently: the $/win denominator, the composite weights, the minutes threshold. |
| **Derive, don't hardcode, anything pegged to the cap** | The CBA defines minimum salaries, maximum salaries, and exception amounts as *percentages of the cap*. A dollar literal is correct for one season and then silently wrong — and "silently" is the problem, because a stale minimum is still a plausible-looking number. This bit us: `ROOKIE_MINIMUM_SALARY` was hardcoded to the 2025-26 figure, putting an $85k error into every floored expected salary and pushing genuine veteran-minimum deals out of the `minimum` band and into the market regression. It is now `SALARY_CAP × 0.8231%`, which self-corrects when the cap moves. Verified: the derived 10-year veteran minimum lands $27 from a real player's actual cap hit. |
| **Orthogonal facts need orthogonal fields** | A contract's *acquisition route* (extension, free agency, rookie scale) and its *price tier* (max, MLE, minimum) are independent — a player can be on an extension that is also a max. Forcing both into one enum means whichever writer runs last destroys the other fact. That happened: BBRef's "Extension" note overwrote every max deal and left the Max/Designated-Veteran category empty on live data, silently breaking a stated product requirement. `contract_type` and `salary_tier` are now separate axes, and a contract is CBA-suppressed if *either* says so. |

## Modeling specification

Composite weights, WAR calibration, and the full production-score derivation with a worked example — including the known data-source hazards the ETL works around — live in [METHODOLOGY.md](METHODOLOGY.md).

## Known limitations

Stated up front, because a value model that hides its weaknesses is not useful.

1. **Rookie-scale contracts structurally dominate the "best value" list.** The CBA caps what those players can
   earn, so an All-NBA player on a rookie deal shows enormous surplus every time — a fact about the CBA, not a
   discovery about the player. Fitting the regression on free-agent deals mitigates but does not remove the
   distortion; a future refinement would measure surplus *relative to expectation for that contract class*.
2. **Box-score metrics undervalue defense and off-ball gravity.** DARKO's on-off component helps; it does not
   fully solve it.
3. **One season of production is a noisy estimate of true talent.** A 2025-26 line reflects role, health, and
   teammate quality, not just ability.
4. **Cap hit ≠ cash paid.** Guarantees, options, incentives, trade kickers, and stretched dead money all diverge
   from the cap number. Spotrac enrichment surfaces these on the player card, but the Y axis remains the cap hit.
5. **The cap is not what teams actually spend.** Tax-paying teams spend well above it, so a cap-derived $/win
   understates the true market price of a win.
6. **The TS%-vs-usage residual does not measure what its name says.** The league-wide fit of `TS% ~ USG%` is
   essentially flat (slope −0.00032, r² = 0.0013 on the current build), so the residual correlates with raw TS%
   at r = 0.999. The 10% component is in practice a recentred TS%, because usage barely predicts efficiency once
   the population is restricted to 500+ minute rotation players. It is left in because a TS% term at 10% is
   defensible on its own merits, but the usage adjustment should be treated as inert until it is replaced with a
   stronger conditioning variable. Detail in [METHODOLOGY.md §5](METHODOLOGY.md#5-the-ts-residual--and-why-it-does-not-do-what-it-claims).
7. **The composite's components are not independent, and DARKO is counted twice.** `darko_dpm` carries 25% in
   its own right *and* is one of the three estimates inside `war_blended` at 40% — so DARKO drives roughly 38%
   of the score. The same double count applies to Win Shares (`ws_per_48` at 15%, plus `war_ws` inside the
   blend). The weights therefore overstate how many independent views of a player the score actually combines.

## Verified reference data

Checked 2026-08-01.

| Fact | Value | Source |
|---|---|---|
| 2026-27 salary cap | $164,961,000 | [pr.nba.com](https://pr.nba.com/2026-27-salary-cap/) |
| 2026-27 luxury tax | $200,428,000 | pr.nba.com |
| 2026-27 first / second apron | $209,015,000 / $221,686,000 | pr.nba.com |
| 2025-26 salary cap | $154,647,000 | pr.nba.com |
| EPM access | Paid only — Dunks & Threes Premium+API | dunksandthrees.com |
| DARKO access | Free — public Google Sheet, nightly | [sheet](https://docs.google.com/spreadsheets/d/1mhwOLqPu2F9026EQiVxFPIN1t9RGafGpl-dokaIsm9c/edit) |
| Sports-Reference rate limit | 20 req/min; up to 24h ban on violation | [bot policy](https://www.sports-reference.com/bot-traffic.html) |
| stats.nba.com from cloud | Blocked — datacenter IP blacklist + Akamai TLS fingerprinting | [swar/nba_api#633](https://github.com/swar/nba_api/issues/633) |
| `nba_api.stats.static.players` | Bundled local data, zero network calls | [docs](https://github.com/swar/nba_api/blob/master/docs/nba_api/stats/static/players.md) |
| Headshot CDN | `https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png` | public, unauthenticated |

### First full build (2026-08-01)

A dated snapshot of the first end-to-end run; every constant is refit nightly, so these drift slightly from the
live `meta.json` — the worked example above quotes the 2026-08-02 build instead. 30 teams, 509 contract rows,
$5.89B in 2026-27 commitments. 506 unique contracted players → 428 with 2025-26 production → 396 with a cap hit
→ **336 after the 500MP/20G scope filter**; 336 headshots mirrored (5.4 MB). All three WAR estimates calibrated
to a common league total (`vorp=819.2 ws=819.2 darko=820.3`), fitting `replacement_ws48 = 0.0297` and
`replacement_dpm = −2.285`.

| | slope | r² | n |
|---|---|---|---|
| Market line (both denominators) | 0.00202 | 0.244 | 192 |
| Model line — naive | 0.00233 | 0.795 | 336 |
| Model line — replacement | 0.00292 | 0.795 | 336 |

Model-line slope ratio is exactly **1.2500**, matching $5.03M ÷ $4.02M — the $/win toggle demonstrably moves the
line. Top surplus: Wembanyama +$42.5M (rookie scale), Gilgeous-Alexander +$36.9M (max), Jokić +$28.0M
(designated veteran, at 35.8% of the cap). Bottom: Davis −$51.1M, LaVine −$47.2M. Team extremes: Boston +$88.6M,
Sacramento −$113.1M. Jokić posting a large positive surplus *while earning a supermax* is the model working — he
is one of the few players whose production justifies that price.

## Repository layout

```
nba_analysis/
├── etl/
│   ├── sources/                    # one adapter per data source
│   │   ├── bbref_stats.py          2025-26 advanced + per-game
│   │   ├── bbref_contracts.py      2026-27 salaries (custom parser)
│   │   ├── spotrac.py              contract structure enrichment
│   │   ├── darko.py                public Google Sheet → CSV
│   │   └── player_ids.py           nba_api static module (OFFLINE)
│   ├── transform/
│   │   ├── war.py                  three WAR estimates + calibration
│   │   ├── composite.py            percentile blend + TS% residual
│   │   ├── valuation.py            $/win, expected salary, surplus
│   │   └── contract_type.py        rookie / min / MLE / max classification
│   ├── crosswalk.py                canonical identity spine
│   ├── ratelimit.py                shared token bucket, <20 req/min
│   └── build.py                    orchestrator → data/processed/*.json
├── data/raw/                       cached scrapes (gitignored)
├── data/processed/                 committed artifacts the frontend reads
├── web/                            Vite + React + deck.gl static SPA
│   ├── src/chart/                  ScatterplotLayer (dots) ↔ IconLayer (faces)
│   ├── src/views/                  scatter | leaderboard | team | player
│   ├── src/model/                  client-side $/win + denominator recomputation
│   ├── src/styles/                 design tokens
│   └── scripts/                    mock data, dev symlink, build-time staging
├── tests/
├── vercel.json
└── .github/workflows/
    ├── ci.yml                      tests + build on every push/PR
    └── nightly.yml                 scrape → rebuild → commit → deploy
```

## Deployment

Static site on **Vercel**, rebuilt nightly by **GitHub Actions**. The two are deliberately decoupled: Vercel
never runs Python and never touches Basketball-Reference — it only builds a frontend from a dataset already
committed to the repository.

```
GitHub Actions (nightly, 11:00 UTC)
  pytest  →  etl.build --refresh --verify  →  commit data/processed
                        │
                        ▼   npm run build (stages data)  →  vercel deploy --prebuilt
```

`data/processed/` is committed, not gitignored. That is what makes the site reproducible from a clean checkout
and what lets Vercel build without credentials for any upstream source.

| Workflow | Trigger | Network access | What it does |
|---|---|---|---|
| `ci.yml` | push to `main`, any PR | none | ADR-010 guard, `pytest`, frontend build, asserts the built site shipped real (non-mock) data |
| `nightly.yml` | cron `0 11 * * *`, manual | Basketball-Reference, DARKO, Spotrac, NBA CDN | re-scrapes, rebuilds, commits `data/processed`, builds and deploys |

`ci.yml` deliberately does **not** run the scrapers: BBRef allows 20 requests/minute and bans for up to an hour
on violation, so hitting it on every push would be both slow and hostile. The nightly job is the only workflow
permitted to touch the network sources. 11:00 UTC is ~06:00 ET — late enough that every game has finalized and
BBRef has ingested the previous night's box scores.

**Build-time data staging.** `npm run build` runs `prebuild` → `web/scripts/stage-data.mjs`, copying
`data/processed/*.json` plus the mirrored headshots into `web/public/data/`, which is **gitignored**. Locally,
`npm run link-data` symlinks it at `data/processed` so an ETL run shows up instantly; on a fresh clone (Vercel,
CI) that symlink does not exist, so the staging script materialises real files instead. It detects an existing
valid symlink and leaves it alone, so a local `npm run build` does not destroy your dev setup. Headshots must be
same-origin: `cdn.nba.com` sends no `Access-Control-Allow-Origin` header and WebGL refuses to texture a
cross-origin image, so a build without mirrored portraits degrades to placeholder marks
(`etl/sources/headshots.py`).

**Vercel configuration.** `vercel.json` lives at the repository root and drives the build from there rather than
setting a Vercel "Root Directory", so `data/processed/` is guaranteed present in the build context. Cache
headers are set per asset class: hashed bundles immutable for a year, headshots a week (they change at most once
a season), and the JSON dataset `max-age=0, s-maxage=3600` so a browser always revalidates while the CDN absorbs
the load between nightly refreshes.

```json
"installCommand": "npm ci --prefix web",
"buildCommand":   "npm run build --prefix web",
"outputDirectory": "web/dist"
```

**Two deploy modes.** The nightly workflow's deploy step is gated on `secrets.VERCEL_TOKEN`. With the token
present, Actions runs `vercel build` + `vercel deploy --prebuilt`, publishing the exact artifact that passed
verification rather than letting Vercel rebuild from source. Without it the step no-ops — the correct behaviour
if you connected Vercel's own Git integration instead, since the nightly data commit already triggers a redeploy
there. Pick one: enabling both means every nightly run deploys twice.
