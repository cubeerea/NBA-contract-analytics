# NBA Contract Value Dashboard

A nightly-refreshed dashboard that plots every contracted NBA player's on-court
production against what they are paid, fits a league-average price-per-unit-of-output
line, and surfaces the largest positive and negative residuals — **the best and worst
contracts in the league**.

Player headshots serve as the plot marks, so the chart reads as a wall of faces
rather than a wall of dots.

**Design principle:** every modeling assumption is visible and toggleable, not buried.
Contract-value analysis is unusually sensitive to a handful of arbitrary constants
(what a win costs, what "replacement level" means, which contracts define market
price). This project surfaces those choices in the UI instead of hiding them behind
a single confident-looking number.

---

## Table of contents

- [What problem this solves](#what-problem-this-solves)
- [What the dashboard shows](#what-the-dashboard-shows) — how to read it
- [Requirements decision record](#requirements-decision-record) — verbatim Q&A
- [Architecture Decision Records](#architecture-decision-records) — ADR-001…015
- [Design patterns](#design-patterns)
- [Modeling specification](#modeling-specification)
  - [How the production score is calculated](#how-the-production-score-is-calculated) — end to end, with a worked example
- [Known limitations](#known-limitations)
- [Verified reference data](#verified-reference-data)
- [Repository layout](#repository-layout)
- [Deployment](#deployment) — Vercel + nightly GitHub Actions

---

## What problem this solves

There is no public tool that answers "which NBA contracts are actually good value?"
transparently. Existing options are:

- **Paywalled** — EPM (Dunks & Threes), LEBRON (BBall Index)
- **Frozen** — RAPTOR, discontinued by FiveThirtyEight in 2023
- **Editorial** — hot takes rather than models

This builds the modeled, reproducible, free alternative.

---

## What the dashboard shows

The dashboard itself carries almost no prose — it shows numbers, labels, and faces. This
section is the explanation that used to live on the page. Read it once and the interface
should be self-evident afterwards.

### Season framing

**2025-26 on-court production, scored against 2026-27 salary.** Both inputs are settled:
the 2025-26 season is complete, and the 2026-27 cap is officially set at **$164,961,000**.
The question the chart answers is therefore forward-looking — *given what a player just
did, is next season's contract good value?* See
[ADR-002](#adr-002--season-framing-2025-26-production-vs-2026-27-salary).

### The axes

| Axis | Quantity | Range |
|---|---|---|
| **X** | Composite production score — a percentile-weighted blend of blended WAR, DARKO DPM, WS/48, TS%-vs-usage residual, and availability. Switchable to raw blended WAR. | 0–100 |
| **Y** | The player's **2026-27 cap hit as a share of a denominator you choose**: the $164,961,000 league cap (default) or the player's own team payroll. | 0–~40% |

Y is expressed as a share rather than in dollars so the chart stays comparable across
seasons as the cap moves, and so the vertical position reads directly as "how much of a
roster's money does this player consume." A player at 30% of the cap is taking up roughly
a third of a team's cap room.

Each mark is the player's headshot; clicking one pins their full contract card. The
composite weights are documented in [Modeling specification](#modeling-specification).

### Who is on the chart

Players with a 2026-27 contract who cleared a 2025-26 playing-time floor of **500 minutes
and 20 games** — **334 players** on the current build, with **59** otherwise-contracted
players excluded by the floor. Sub-500-minute samples produce wild per-minute rates and
meaningless surplus figures, so they are dropped rather than allowed to dominate the
outlier view ([ADR-008](#adr-008--player-scope-under-contract--minutes-threshold)). The
exact counts move slightly with each nightly rebuild; `data/processed/meta.json` carries
the live figures.

One consequence worth knowing: the percentile bars on the player detail view, and every
percentile inside the composite score, are computed against these 334 — not against every
player who appeared in an NBA game. They are percentiles among rotation players.

### The two regression lines

Two lines are drawn over the same scatter, and they answer different questions.

| Line | What it is | Answers | Moves with the $/win toggle? |
|---|---|---|---|
| **Market line** | OLS fit of `cap_pct ~ composite_score` on market-priced contracts only — free agency, extensions, max/designated-veteran, and MLE deals (n = 190, slope 0.00204, r² 0.241) | What teams *actually* pay for this production | **No** — it is empirical |
| **Model line** | `(blended WAR × $/win) / cap` against composite score (n = 334, r² 0.795) | What this production *should* earn | **Yes** |

Rookie-scale, minimum, and two-way deals are plotted but excluded from the market fit:
they are CBA-suppressed rather than negotiated, and including them drags the line down
until every market-priced veteran looks overpaid.

The gap between the two lines is the degree to which the league's revealed pricing
disagrees with the model — which is itself the interesting question. Player residuals, and
therefore the surplus/shortfall numbers on the leaderboard, are measured against the
**market** line. Full reasoning in [ADR-013](#adr-013--two-regression-lines-not-one).

Both fits are computed server-side against `cap_pct`. Switching the Y axis to team payroll
therefore invalidates both, and the client refits rather than leaving a stale line on a
rescaled axis — see [The denominator toggle](#the-denominator-toggle) below.

### The denominator toggle

The cap is the league's *limit*, not a ceiling anyone is held to. Cleveland's books run to
**$226.0M** against a $164,961,000 cap (1.37×); Brooklyn's to **$150.8M** (0.91×). So
"share of the cap" is one honest denominator and "share of the payroll this contract
actually sits inside" is another, and the dashboard exposes both rather than picking
silently — the same treatment [ADR-006](#adr-006--valuation-compute-both-denominators-toggle-in-ui)
gives the $/win constant.

**League cap is the default**, because the chart's load-bearing element is a single
league-wide regression line and a regression only means anything when every point is
measured against the same ruler. Team payroll also inverts real comparisons: Stephen Curry
($62.6M) is paid **$4.1M more** than Anthony Davis ($58.5M), yet reads as the cheaper
contract on the team axis — 29.7% of Golden State's payroll against 31.3% of Washington's —
purely because Golden State spent more on everyone else. That measures front-office
profligacy, not player cost.

Switching to team payroll changes three things, all of them visibly:

| | League cap | Team payroll |
|---|---|---|
| **Market line** | server OLS fit on `cap_pct` | refit client-side on the same market-priced contracts (n = 190) against the team-relative share |
| **Model line** | one line | a **band** — one dollar figure is a different share on every roster, so it spans the league's leanest to richest payroll with the median team as its centre |
| **Y tick sub-label** | the dollar figure that share works out to | dropped — the same percentage is a different dollar figure on every team |

Full reasoning in [ADR-015](#adr-015--y-axis-denominator-league-cap-default-team-payroll-toggle).

### Team payroll context

Wherever a player is shown in detail — the chart tooltip, the pinned contract card, the
comparison grid, the leaderboard's cap-hit cell — the dashboard states the payroll the
contract sits inside, that team's rung on the CBA's four spending lines, and how far past
it the books already are. An identical $40M contract is survivable for a team with room
and punitive past the second apron, where every dollar carries repeater tax and the front
office has lost salary aggregation, sign-and-trade and its own picks.

The rung is drawn as the same four-notch ordered meter the team rollup uses, on the
neutral ramp, because blue/red is reserved for surplus polarity. The cap comes from
`meta.json`; the tax and apron lines are not published there, so they are derived as
multiples of the live cap in `web/src/model/payroll.js` rather than copied in as dollar
literals that would go stale the moment the cap moves.

### The $/win toggle

Surplus value is `expected salary − cap hit`, where expected salary is `blended WAR × $/win`.
The $/win denominator is a genuinely contested constant, so both options are exposed in the
UI rather than one being picked silently: **$4.02M** (naive) or **$5.03M**
(replacement-adjusted, the default). See [ADR-006](#adr-006--valuation-compute-both-denominators-toggle-in-ui).

### The views

- **Value scatter** — the whole league at once; shape, clusters, and outliers.
- **Leaderboard** — the same data sorted, for when the question is "who exactly is #1." Note
  that rookie-scale deals structurally dominate the top; see [Known limitations](#known-limitations).
- **Team rollup** — surplus aggregated by franchise. A team high on this list is getting more
  production than its payroll implies, usually a sign of good drafting still on rookie
  deals. Player filters apply to the surplus and WAR columns; payroll and apron state come
  straight from `teams.json` and are deliberately shown unfiltered, because they are
  properties of the franchise rather than of your current selection.
- **Compare** — up to three players side by side, including the WAR uncertainty band and
  league percentile bars.

### Data provenance

| What | Source |
|---|---|
| Box-score and advanced production | Basketball-Reference |
| Projective impact (DPM) | DARKO — free public Google Sheet, nightly |
| Salaries and contract years | Basketball-Reference `/contracts/`, enriched by Spotrac for structure (options, guarantees, kickers) |
| Player portraits | Public NBA CDN |

The dataset is rebuilt nightly by a GitHub Actions job and committed to `data/processed/`;
the frontend is a static site that reads those artifacts. Nothing is fetched from
stats.nba.com at any point ([ADR-010](#adr-010--no-live-statsnbacom-calls)).

### Before you trust a number

Every model of this kind has load-bearing weaknesses, and this one's are listed in
[Known limitations](#known-limitations). The short version: rookie-scale deals will always
top the best-value list, box scores undervalue defense, one season is a noisy talent
estimate, and cap hit is not cash paid.

---

## Requirements decision record

Twelve questions were asked during requirements refinement. Both the questions and
the answers are recorded verbatim, because the ADRs below are downstream of them —
if a premise changes, this is where to look to see which decisions must be revisited.

### Round 1 — data foundation

> **Q1. EPM is paywalled (Dunks & Threes Premium+API). What's the plan for your
> headline metric?**
>
> **A:** *"ideally use both 1 and 2, focus on obtain raw data. we can always implement
> our own formula to calculate those scores/metrics."*
> (Options 1 and 2 were "substitute BPM/VORP/WS" and "add DARKO".)

→ Drives [ADR-001](#adr-001--metrics-free-raw-data-own-formulas). The operative phrase
is *focus on obtaining raw data* — it reframes the project as an ingestion pipeline with
a swappable metric layer on top, rather than a consumer of someone else's ratings.

> **Q2. It's August 2026 — the offseason. Which production year should be scored
> against which salary year?**
>
> **A:** *"2025-26 stats vs 2026-27 salary"*

→ Drives [ADR-002](#adr-002--season-framing-2025-26-production-vs-2026-27-salary).
Forward-looking framing.

> **Q3. How should salary and contract data be sourced?**
>
> **A:** *"I don't care how the data is sourced, i just want it to be live data or at
> least be scraped periodically with a cron job. leaning towards option 2 but would
> rather take a hybrid approach of 1 and 2."*
> (Option 1 was Basketball-Reference `/contracts/`; option 2 was scraping Spotrac.)

→ Drives [ADR-003](#adr-003--salary-sourcing-basketball-reference-primary-spotrac-enrichment).

> **Q4. What does "live" actually mean for this dashboard?**
>
> **A:** *"Nightly scheduled refresh"*

→ Drives [ADR-004](#adr-004--refresh-nightly-scheduled-etl).

### Round 2 — modeling and stack

> **Q5. What stack should this be built on?**
>
> **A:** *"Doesn't really what tech stack it is, I want the data points to be the
> headshot of each player."*

→ Drives [ADR-007](#adr-007--overlap-binned-overview-faces-on-zoom) and the frontend
choice. This answer inverted the usual dependency: the stack became a *consequence* of
a rendering requirement rather than an independent decision. deck.gl was selected
because it solves the headshot problem, not on general merit.

> **Q6. The X-axis composite score needs defined weights and normalization. How do
> you want that decided?**
>
> **A:** *"You propose a defensible default"*

→ Drives [ADR-005](#adr-005--composite-score-defensible-default-fully-documented).
Every weight is documented and tunable; see [Modeling specification](#modeling-specification).

> **Q7. On the valuation methodology concerns — $/win denominator and regression fit
> population?**
>
> **A:** *"Compute both, toggle in UI"*

→ Drives [ADR-006](#adr-006--valuation-compute-both-denominators-toggle-in-ui). This is
the answer that set the project's guiding principle: contested assumptions become
features, not hidden defaults.

> **Q8. How deep should trade eligibility go? (2023 CBA logic is substantial)**
>
> **A:** *"Simple flags only"*

→ Drives [ADR-011](#adr-011--trade-eligibility-simple-flags-only).

### Round 3 — visualization and scope

> **Q9. ~500 headshots will overlap badly on a single scatter. How should that be
> handled?**
>
> **A:** *"Binned overview, faces on zoom"*

→ Drives [ADR-007](#adr-007--overlap-binned-overview-faces-on-zoom).

> **Q10. Which players make it onto the chart?**
>
> **A:** *"Under contract + minutes threshold"*

→ Drives [ADR-008](#adr-008--player-scope-under-contract--minutes-threshold).

> **Q11. Where does this run? (affects the nba_api cloud-IP ban and scraping exposure)**
>
> **A:** *"GitHub Actions ETL → static hosted site"*

→ Drives [ADR-009](#adr-009--deployment-github-actions-etl--static-site) and forced
[ADR-010](#adr-010--no-live-statsnbacom-calls), which is what makes it viable.

> **Q12. Beyond the scatter, what else does the dashboard need?** *(multi-select)*
>
> **A:** *"Ranked surplus-value leaderboard, Team-level rollup, Player detail /
> comparison view"*

→ Drives [ADR-012](#adr-012--supporting-views-leaderboard-team-rollup-player-detail).
Note the option **not** selected: a separate usage-vs-efficiency chart. Consequently the
usage/efficiency relationship is folded into the composite score as a residual term
rather than given its own view.

---

## Architecture Decision Records

### ADR-001 — Metrics: free raw data, own formulas

**Decision.** Ingest raw box-score and impact data from Basketball-Reference and DARKO;
compute all derived metrics ourselves. Do not depend on EPM.

**Rationale.** EPM sits behind a paid subscription, which would make the project
non-reproducible for anyone without a key. DARKO is free, publicly downloadable, updates
nightly, and is genuinely *projective* — which matches the "Projected Win Shares" term in
the valuation model better than EPM does. Owning the formulas makes the metric layer
auditable and tunable rather than a black box.

**Consequences.** A metric-provider interface keeps EPM addable later behind a config
flag without restructuring. The tradeoff accepted: box-score-derived metrics are weaker
at capturing off-ball and defensive impact than true plus-minus models. DARKO's on-off
component partially offsets this.

### ADR-002 — Season framing: 2025-26 production vs 2026-27 salary

**Decision.** Score completed 2025-26 on-court production against currently-active
2026-27 contracts.

**Rationale.** Forward-looking framing answers "who is overpaid *right now*," the
decision-relevant question. Both inputs are fully settled: the season is complete and
the cap is officially set at $164,961,000.

**Consequences.** Players with a 2026-27 contract but no 2025-26 NBA production — 2026
draftees, international signings — have no X-value. They are handled as an explicit
`no_prior_season` class and excluded from the regression fit, rather than silently
dropped or plotted at zero.

### ADR-003 — Salary sourcing: Basketball-Reference primary, Spotrac enrichment

**Decision.** Bulk salary from BBRef `/contracts/`; enrich contract structure from
Spotrac. Both scraped on schedule.

**Rationale.** BBRef gives complete year-by-year league coverage in ~30 polite requests.
Spotrac adds the structural detail BBRef lacks — options, guarantees, incentives, trade
kickers — which the player detail view needs.

**Consequences.** Scrapers must be rate-limit-aware and fail soft: a Spotrac outage
degrades enrichment but must not break the nightly build. Note that
`jaebradley/basketball_reference_web_scraper` covers BBRef *stats* only — the contracts
pages are not in its API surface and require a purpose-built parser.

### ADR-004 — Refresh: nightly scheduled ETL

**Decision.** GitHub Actions cron rebuilds the dataset nightly and commits the output.

**Rationale.** Contracts change on trade/signing timescales, season stats on daily
timescales; nightly is the natural cadence for both. Real-time is impossible given source
rate limits and meaningless for season-long value metrics — a single game cannot
meaningfully move a full-season WAR figure.

### ADR-005 — Composite score: defensible default, fully documented

**Decision.** Ship a percentile-normalized weighted blend with every weight documented,
plus a UI toggle to a raw wins-above-replacement axis.

**Rationale.** A blend captures the multi-metric picture requested; a WAR axis makes the
regression slope directly interpretable as dollars-per-win. Supporting both lets the chart
be either *readable* or *rigorous* without forking the codebase.

### ADR-006 — Valuation: compute both denominators, toggle in UI

**Decision.** Expose both the naive `Cap / 41` and the replacement-adjusted `Cap / 32.8`
dollars-per-win figures as a user-facing toggle. Default to replacement-adjusted.

**Rationale.** The naive denominator assumes a replacement-level roster wins zero games.
It actually wins roughly 8–15, so the naive figure systematically inflates how overpaid
everyone appears — by about 20% across the board. Rather than silently picking one,
surfacing both makes the assumption visible and converts a hidden bias into an explorable
feature.

| Denominator | $/win | Assumption |
|---|---|---|
| `164,961,000 / 41` | $4.02M | A replacement roster wins 0 games |
| `164,961,000 / 32.8` | $5.03M | Replacement level is .100 win pct (8.2 wins/82) |

### ADR-007 — Overlap: binned overview, faces on zoom

**Decision.** Render density bins when zoomed out; swap to headshot icons on zoom-in.

**Rationale.** ~500 headshots in one viewport collide into illegibility. Binning keeps the
macro distribution readable and defers per-player detail to the zoom level where there is
physical room for it. It is also the best-performing option — hundreds of images are only
rasterized once they are actually distinguishable.

**Consequences.** Dictates the frontend stack. deck.gl provides `HexagonLayer` for the
binned tier and `IconLayer` with dynamically-packed atlases built from remote image URLs
(documented for exactly this profile-picture use case), with zoom-threshold layer swapping
as a native pattern.

### ADR-008 — Player scope: under contract + minutes threshold

**Decision.** Include players with a 2026-27 contract who cleared a 2025-26 playing-time
floor. Default 500 MP, configurable.

**Rationale.** Per-minute metrics are unstable at low sample. A player with 40 career
minutes produces a wild, meaningless surplus figure that would dominate the outlier view
and discredit the whole chart.

### ADR-009 — Deployment: GitHub Actions ETL → static site

**Decision.** Nightly Actions job runs the ETL and commits processed data; a static
frontend consumes the committed artifacts.

**Rationale.** Free, public, no server to maintain, fully reproducible from the repo.
Viable specifically because ADR-010 removes the stats.nba.com dependency.

### ADR-010 — No live stats.nba.com calls

**Decision.** Use `nba_api` **only** for `stats.static.players`, which reads bundled local
data and issues no HTTP request. All statistics come from Basketball-Reference.

**Rationale.** stats.nba.com blacklists datacenter IP ranges and sits behind Akamai bot
protection that fingerprints TLS handshakes — it would break any cloud-run ETL, including
GitHub Actions. Confining `nba_api` to its offline static module still yields the NBA
player IDs needed to construct headshot URLs, while eliminating the ban surface entirely.

**This is the load-bearing decision that makes ADR-009 work.** It is why the project needs
no proxy, no residential IP, and no scraping infrastructure beyond ordinary HTTP.

### ADR-011 — Trade eligibility: simple flags only

**Decision.** Date-based restrictions, no-trade clauses, recently-signed ineligibility.
No apron-aware salary matching.

**Rationale.** Full 2023-CBA matching logic requires modeling every team's live payroll
state against first/second apron rules, base year compensation, and the poison pill
provision — a project comparable in size to the dashboard itself. Flags cover the common
cases cheaply.

**Consequences.** Deliberately deferred, not forgotten. ADR-012's team rollup produces the
team payroll state a future matching engine would need.

### ADR-012 — Supporting views: leaderboard, team rollup, player detail

**Decision.** Three views beyond the scatter.

**Rationale.** The scatter shows *shape*; a sortable table answers "who exactly is #1."
Team rollup identifies which front offices extract the most value per dollar — and
incidentally produces the payroll state noted in ADR-011.

### ADR-013 — Two regression lines, not one

**Decision.** Draw both an empirical **market line** and a model-derived **model line**
over the same scatter.

**Rationale.** The original spec called for one "league average cost per unit of output"
line that would move when the $/win denominator toggled (ADR-006). Those two
requirements are incompatible, and discovering that exposed a genuine conflation:

| Line | Definition | Answers | Moves with toggle? |
|---|---|---|---|
| **Market** | OLS fit of `cap_pct ~ composite_score` on market-priced deals | "What do teams *actually* pay for this production?" | No — it's empirical |
| **Model** | `(war_blended × $/win) / cap` vs `composite_score` | "What *should* this production earn?" | Yes |

An empirical fit of observed salaries cannot depend on our choice of $/win — that
constant appears nowhere in the data being fitted. Showing both lines is strictly more
informative than either: the gap between them is the degree to which the league's
revealed pricing disagrees with the model's, which is itself the interesting question.

**Consequences.** Player residuals rank contracts against the **market** line, not the
model line — "underpaid" means underpaid relative to what teams actually pay comparable
players, which is the more defensible claim.

### ADR-014 — DARKO joins on NBA player ID, not name

**Decision.** Join DARKO on its published `NBA ID` column; fall back to normalized-name
matching only for rows lacking one.

**Rationale.** The DARKO sheet publishes an NBA player ID for all 530 rows. An exact
integer key is strictly better than fuzzy name matching and sidesteps the accent problem
entirely — 35 of 530 DARKO names differ from Basketball-Reference by diacritics alone
(`Nikola Jokic` vs `Nikola Jokić`). Name matching survives as a fallback so a future
sheet change that drops the ID column degrades rather than fails.

### ADR-015 — Y-axis denominator: league cap default, team payroll toggle

**Decision.** Keep **share of the league cap** as the Y-axis denominator, and add a toggle
that switches it to **share of the player's own team payroll**. In team mode, refit both
reference lines client-side rather than reusing the server's cap-relative fits.

**Rationale.** The objection that prompted this is factually correct: the cap is the
league's limit, not a ceiling teams are held to. Cleveland is at $226.0M against a
$164,961,000 cap and Brooklyn at $150.8M, so a "share of the cap" figure is not a share of
anybody's actual books. Two things nonetheless keep the cap as the default:

1. **The regression only means anything under a shared ruler.** The chart's load-bearing
   element is one league-wide market line fit across all 30 teams. Measuring each point
   against a different denominator makes that fit a comparison of incomparable quantities.
2. **Team payroll inverts real comparisons.** Curry is paid $62.6M and Anthony Davis
   $58.5M — Curry costs **$4.1M more**. On the cap axis that is exactly what you see:
   37.9% against 35.4%. On the team axis it flips: Curry reads 29.7% of Golden State's
   payroll against Davis's 31.3% of Washington's, so the more expensive contract plots as
   the cheaper one. Nothing about either player changed; Golden State simply spent more on
   everyone else. That is a measurement of front-office profligacy wearing the costume of
   a player-cost figure.

Neither point makes team payroll *wrong* — it answers a real question the cap cannot, and
a reader who wants to judge that trade-off should be able to see it rather than take our
word for it. So this follows the precedent ADR-006 already set for the $/win constant:
compute both, expose the switch, name the default and say why.

**Consequences.** Team mode requires a **client-side refit**, and this is the sharp edge.
Both reference lines and `cap_pct_residual` are computed by the ETL against `cap_pct`. On a
team-relative axis they are on a different scale entirely, and leaving them in place would
be a wrong answer drawn in ink. So `web/src/model/denominator.js` owns the fits:

| | League cap | Team payroll |
|---|---|---|
| **Market line** | server fit, untouched — the ETL already got it right and recomputing would only introduce drift | plain OLS refit over the same market-priced contracts (n = 190) on the active axis |
| **Model line** | server fit | rescaled per team — and therefore a **band**, not a line |
| **Residuals** | `cap_pct_residual` | recomputed against the refit market line |

The model reference *has* to become a band. Model value is one dollar figure per
production score; divided by 30 different payrolls it is 30 different shares. Drawing a
single line would be a choice of which team to privilege. The band spans the leanest
payroll (top edge — a smaller denominator makes the same dollars a bigger share) to the
richest (bottom edge), with the median team as its centre line. Its width is not
decoration: it *is* the objection this ADR answers, made visible.

Dual-unit Y ticks are dropped in team mode for the same reason — the dollar figure under
"20%" is different on every roster, so printing one would be printing a wrong number.

Team payroll is also promoted to a first-class fact throughout, independent of the toggle:
payroll, apron rung and distance to the next spending line now appear on the chart tooltip,
the contract card, the comparison grid and the leaderboard. The tax and apron thresholds
are derived as multiples of `meta.salary_cap` in `web/src/model/payroll.js`, never hard-coded
as dollars, and defer to `meta.json` if a future build ever publishes them outright.

---

## Design patterns

**Canonical identity spine.** All sources are joined through one crosswalk table
(BBRef slug ↔ NBA player ID ↔ Spotrac slug) built and cached before any other stage.
Name-based joins across basketball data sources fail on suffixes (Jr./III), accents,
and collisions; centralizing identity resolution means that failure mode exists in
exactly one place and is asserted against in CI.

**Source adapter.** Each source implements the same `fetch() → DataFrame` contract
behind `etl/sources/`. Adding EPM, LEBRON, or an alternate salary source means writing
one adapter, not touching the transform layer.

**Calibrate before blending.** Win Shares, VORP, and DARKO DPM are expressed in
incompatible units — WS sums to team wins, VORP is replacement-adjusted, DPM is points
per 100 possessions. Each is converted to wins-above-replacement and calibrated so its
league total agrees before any averaging occurs. Averaging uncalibrated metrics is the
single most common error in public contract-value analysis.

**Fail-soft ingestion.** Sources are ranked essential vs enrichment. Essential source
failure fails the build loudly; enrichment failure logs and degrades. The nightly job
must not go dark because Spotrac changed a CSS class.

**Precompute heavy, recompute light.** The ETL emits static artifacts; the client
recomputes anything that must respond to a UI toggle. The $/win denominator switch
(ADR-006) is a client-side multiply against precomputed WAR — no server round trip, no
rebuild, instant feedback. The corollary is that a toggle which invalidates a precomputed
figure must recompute it rather than display it anyway: the Y-axis denominator switch
(ADR-015) refits both regression lines client-side, because the server's fits are
cap-relative and would be silently wrong on a team-relative axis.

**Zoom-driven level of detail.** The chart is layered by information density rather than
by data subset. Every player is always present; only the representation changes with
zoom. This keeps the visualization honest — nothing is filtered out to make it look clean.

**Assumptions as UI.** Where the literature genuinely disagrees, expose the choice rather
than picking silently. Applies to the $/win denominator, the composite weights, and the
minutes threshold.

**Derive, don't hardcode, anything pegged to the cap.** The CBA defines minimum salaries,
maximum salaries, and exception amounts as *percentages of the salary cap*. A literal
dollar figure is correct for exactly one season and then silently wrong — and "silently"
is the problem, because a stale minimum is still a plausible-looking number. This bit us:
`ROOKIE_MINIMUM_SALARY` was hardcoded to the 2025-26 figure, which put an $85k error into
every floored expected salary and pushed genuine veteran-minimum deals out of the
`minimum` band and into the market regression. It is now `SALARY_CAP × 0.8231%`, which
self-corrects when the cap moves. Verified: the derived 10-year veteran minimum lands
$27 from a real player's actual cap hit.

**Orthogonal facts need orthogonal fields.** A contract's *acquisition route* (extension,
free agency, rookie scale) and its *price tier* (max, MLE, minimum) are independent — a
player can be on an extension that is also a max. Forcing both into one enum means
whichever writer runs last destroys the other fact. That happened: Basketball-Reference's
"Extension" note overwrote every max deal and left the Max/Designated-Veteran category
empty on live data, silently breaking a stated product requirement. `contract_type` and
`salary_tier` are now separate axes, and a contract is CBA-suppressed if *either* says so.

---

## Modeling specification

### Wins Above Replacement — the dollar-model input

Three independent estimates, each calibrated to a common league total, then averaged:

| Source | Conversion |
|---|---|
| VORP | `WAR = VORP × 2.7` — BBRef's documented conversion; already replacement-adjusted at −2.0 BPM |
| Win Shares | `WAR = WS − (replacement_WS48 × MP / 48)`, with `replacement_WS48` calibrated so the league total matches VORP's |
| DARKO DPM | `WAR = (DPM − replacement_DPM) × possessions / 100 / points_per_win`, calibrated to the same total |

Disagreement between the three is itself signal — the spread is surfaced as an
uncertainty band on the player detail view rather than averaged away.

### Composite Production Score — X-axis default, 0–100

Each component is percentile-ranked league-wide, then weighted:

| Component | Weight | Captures |
|---|---|---|
| Blended WAR | 40% | Volume-inclusive total impact |
| DARKO DPM (rate) | 25% | Per-possession impact, on-off informed |
| WS/48 | 15% | Per-minute efficiency |
| TS% residual vs usage | 10% | Scoring efficiency relative to a league fit of `TS% ~ USG%` — see the caveat below |
| Availability | 10% | Durability — `min(MP, GP × 36) / (82 × 36)` |

The **TS% residual** was meant to resolve the usage-vs-efficiency requirement: fit
`TS% ~ USG%` league-wide and take each player's residual, collapsing a two-variable
relationship into one scalar. On the shipped data the fit is essentially flat
(r² = 0.0013), so the residual is in practice a recentred TS% rather than an
efficiency-above-expectation term. This is documented honestly in
[How the production score is calculated](#how-the-production-score-is-calculated) and
listed in [Known limitations](#known-limitations) rather than glossed over.

Percentile normalization (rather than z-scoring) is deliberate — these distributions are
right-skewed and heavy-tailed, and percentiles keep a handful of superstars from
compressing everyone else into an indistinguishable clump.

The full derivation, the fitted constants from the live build, and a worked example that
reconciles to a published `composite_score` are in
[How the production score is calculated](#how-the-production-score-is-calculated).

### How the production score is calculated

Everything below is what the shipped code does, not what the plan said it would do.
The implementation lives in [`etl/transform/war.py`](etl/transform/war.py),
[`etl/transform/composite.py`](etl/transform/composite.py) and
[`etl/config.py`](etl/config.py); every constant quoted here is read back from
`data/processed/meta.json` for the build of **2026-08-02** (334 scored players), so the
numbers can be checked against the artifact the site is serving.

Units matter and are easy to mix up. Three different things appear below: **win units**
(WAR, a count of wins), **percentiles** (0–100, rank within the scored population), and
**dollars / cap share**. The composite score is a weighted average of percentiles and is
therefore *not* in win units — that distinction is the whole reason the valuation model
is kept separable from the score (see [How the score is used](#8-how-the-score-is-used)).

#### 1. The inputs

| Column | Meaning | Source |
|---|---|---|
| `games`, `minutes` | GP and total MP (not per-game) | Basketball-Reference |
| `ts_pct`, `usg_pct` | True shooting %, usage rate | Basketball-Reference |
| `ws`, `ws_per_48` | Win Shares, Win Shares per 48 | Basketball-Reference |
| `vorp`, `bpm` | Value Over Replacement Player, Box Plus/Minus | Basketball-Reference |
| `darko_dpm` | Daily Plus-Minus, points per 100 possessions | DARKO (public Google Sheet) |

Nothing else feeds the score. There is no proprietary input and no hand-tuned per-player
adjustment.

**Playing-time floor.** A player is scored only if he cleared **both**
`MIN_MINUTES_PLAYED = 500` and `MIN_GAMES_PLAYED = 20` in 2025-26 (`etl/build.py`,
`_apply_scope`). The current build keeps **334** players and drops **59** contracted
players below the floor. The floor is applied *before* the percentiles are taken, which
is load-bearing: ranking a 40-minute call-up alongside the league would compress
everyone else toward the middle of the axis.

#### 2. The three WAR estimates, and why they are calibrated

Win Shares, VORP and DARKO DPM are expressed in incompatible units. WS sums to *team
wins* (a replacement-level roster still wins about 8 games per 82, so raw WS is not
"above replacement" at all). VORP is points above a −2.0 BPM replacement per 100 team
possessions. DPM is a pure rate with no volume term and no fixed replacement baseline.

Averaging them as published would silently inherit whichever metric happens to carry the
largest league total, and the resulting dollars-per-win would be off by tens of percent.
So each estimate is first converted to wins above replacement and then **calibrated**:
its one free constant is solved for so that its league total equals VORP's. VORP is the
anchor because both its conversion (× 2.7) and its baseline (−2.0 BPM) are documented by
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

Both calibrations are closed-form, not fudge factors: each estimate is linear in its
unknown constant, so the constant that equates league totals solves exactly. The DARKO
equation is underdetermined (one equation, two unknowns — `replacement_dpm` and
`points_per_win`); `points_per_win` is held fixed at 32 because roughly 32 points per win
is an empirical property of NBA scoring margin, while "replacement level" is a modelling
convention with no ground truth. Putting the error in the baseline shifts every player by
a constant per-minute amount; putting it in `points_per_win` would distort the *gap*
between stars and rotation players, which is worse.

Each estimate is calibrated on the subset of players where it *and* the anchor both
exist, so a metric with partial coverage does not get its baseline dragged down by
players it never covered.

**Fitted constants and realised league totals, 2026-08-02 build** (`meta.war_constants`):

| Constant | Value | Status |
|---|---|---|
| `vorp_to_wins` | 2.7 | fixed (BBRef) |
| `league_pace` | 99.0 poss / 48 min | fixed assumption |
| `points_per_win` | 32.0 | fixed assumption |
| `replacement_ws48` | **0.029689** WS per 48 | solved |
| `replacement_dpm` | **−2.2887** pts / 100 poss | solved |

| League total (win units) | Value | Coverage |
|---|---|---|
| `war_vorp` | **820.53** | 334 / 334 |
| `war_ws` | **820.53** | 334 / 334 |
| `war_darko` | **820.53** | 334 / 334 |

The three totals agreeing to the cent is what makes the mean of the three a legitimate
quantity rather than an average of three different scales. The build asserts the totals
agree within `WAR_CALIBRATION_TOLERANCE` (5%) and fails loudly if they do not.

Disagreement *between* players is kept: `war_spread` (max − min across the three) is
surfaced as an uncertainty band on the player card rather than averaged away.

#### 3. The five components and their weights

From `config.COMPOSITE_WEIGHTS`, echoed to `meta.composite_weights`. No component was
dropped in this build, so requested and effective weights are identical.

| Weight key | Underlying column | Weight | Units before ranking | Captures |
|---|---|---|---|---|
| `war_blended` | `war_blended` | **0.40** | wins | Volume-inclusive total impact |
| `darko_dpm` | `darko_dpm` | **0.25** | pts / 100 poss | Per-possession impact, on-off informed |
| `ws48` | `ws_per_48` | **0.15** | WS per 48 min | Per-minute efficiency |
| `ts_residual` | `ts_residual` | **0.10** | TS% points | Efficiency vs. the league `TS% ~ USG%` fit |
| `availability` | `availability` | **0.10** | fraction of a season | Durability |

Two of the five are derived rather than read straight off a source:

```
ts_residual  = TS% − (slope × USG% + intercept)          # league OLS fit, see §5
availability = min(MP, GP × 36) / (82 × 36),  clipped to [0, 1]
```

The availability cap at 36 minutes per game is deliberate. 36 mpg is a full starter's
load; a 38-mpg season is a statement about role, not durability, and the extra volume it
represents is already the largest term in `war_blended`. Note this differs from a plain
`GP × MP` product — both halves are present, but the minutes half saturates.

**Per-player renormalisation.** Weights are renormalised over the components that
actually resolved for that player, so a missing DARKO row scores a player on his other
four components instead of pushing him a quarter of the way down the axis for a data gap.
`composite_n_components` records how many were used — it is 5 for all 334 players in this
build.

#### 4. Percentile normalization, not z-scores

Each of the five components is **percentile-ranked across the whole scored population**
before any weighting happens (`series.rank(pct=True, method="average") × 100`). Ties share
the average rank. Only the ranking survives into the score; the original units do not.

The reason is distributional. WAR, DPM and WS/48 are all right-skewed and heavy-tailed:
Jokić's blended WAR is roughly six standard deviations of the rotation-player
distribution above its mean. Under z-scoring a single player like that dominates the
blend and pins everyone from the 20th to the 80th percentile into a narrow,
visually indistinguishable clump. The chart's entire job is to separate the middle of the
league, because that is where nearly every contract decision actually lives.

The cost is real and worth stating: percentile ranking **destroys magnitude**. The gap
between the best and second-best player on the axis is one rank, exactly like the gap
between the 200th and 201st. That is why raw blended WAR is available as an alternate X
axis, and why the model line (§8) is fit separately in win units.

#### 5. The TS% residual — and why it does not do what it claims

The intent: regress TS% on USG% league-wide, and score each player on his residual, so
that a high-usage creator is credited for beating the efficiency expectation *at that
volume* rather than penalised for not shooting like a play-finisher.

The fit actually obtained on this build (`meta.composite_fit.ts_fit`):

```
TS% = −0.00032309 × USG% + 0.58843        r² = 0.0013     n = 334
```

**This does not survive contact with the data.** An r² of 0.0013 means usage explains
about one-tenth of one percent of the league-wide variance in true shooting. Across the
full observed usage range (8.5 to 38.1), the fitted line moves predicted TS% by
0.0096 — under one point of true shooting — while the standard deviation of TS% itself is
0.050. The consequence: `ts_residual` correlates with raw `ts_pct` at **r = 0.999**
(Spearman 0.999). After percentile ranking, the component is indistinguishable from
"percentile of raw TS%" with the usage adjustment rounding to noise.

So the honest description of this 10% component is **a recentred TS%**, not "efficiency
above expectation at that usage". The original justification is retained above as the
design intent, but it should not be read as a description of the shipped behaviour. See
[Known limitations](#known-limitations) item 6.

The mechanism is not wrong — it is simply near-inert at league scale, because usage
barely predicts efficiency once you have already restricted to 500+ minute rotation
players. A version of this idea that works would need a stronger conditioning variable
(shot location mix, self-created share) or a within-role fit rather than a single
league-wide line.

The code degrades safely: fewer than three usable players, or no variance in usage,
yields all-zero residuals, which contribute an identical percentile to everyone.

#### 6. The final formula

For player *i*, with `P(x)` denoting percentile rank within the scored population:

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

Because each `P(·)` is on 0–100 and the weights sum to 1.0, the result is on 0–100. It is
a weighted average of percentiles, not a percentile itself — a player would have to top
all five components to reach 100. Observed range on this build: **2.50 to 98.31**, median
**51.19**, with Jokić at the top.

#### 7. Worked example — Jalen Brunson, composite 91.28

Every number below is reproduced from `data/processed/players.json` and
`data/processed/meta.json` for the 2026-08-02 build.

**Raw inputs**

| GP | MP | TS% | USG% | WS | WS/48 | VORP | DARKO DPM |
|---|---|---|---|---|---|---|---|
| 74 | 2,590 | .580 | 30.4 | 8.8 | .163 | 3.3 | +3.39 |

**Step 1 — three WAR estimates** (win units)

```
war_vorp  = 3.3 × 2.7                                        =  8.910000
war_ws    = 8.8 − 0.029689 × 2590 / 48
          = 8.8 − 1.601957                                   =  7.198043
poss      = 2590 × 99 / 48                                   =  5341.875
war_darko = (3.39 − (−2.2887)) × 5341.875 / 100 / 32
          = 5.678726 × 53.41875 / 32                         =  9.479702

war_blended = (8.910000 + 7.198043 + 9.479702) / 3           =  8.529248
```

**Step 2 — the two derived components**

```
ts_residual  = 0.580 − (−0.00032309 × 30.4 + 0.58843)
             = 0.580 − 0.578612                              =  0.001388
availability = min(2590, 74 × 36 = 2664) / (82 × 36 = 2952)
             = 2590 / 2952                                   =  0.877371
```

Note how little the usage adjustment does: at a 30.4 usage rate — well into primary-creator
territory — the expected-TS% correction is under one thousandth. His residual is
essentially "his TS% minus the league mean TS%".

**Step 3 — percentile-rank each component against the other 333 players**

| Component | Value | Rank (of 334) | Percentile | Weight | Contribution |
|---|---|---|---|---|---|
| `war_blended` | 8.529248 wins | 323 | 96.7066 | 0.40 | 38.682635 |
| `darko_dpm` | +3.39 pts/100 | 323 | 96.7066 | 0.25 | 24.176647 |
| `ws_per_48` | 0.163 | 299.5 | 89.6707 | 0.15 | 13.450599 |
| `ts_residual` | 0.001388 | 179 | 53.5928 | 0.10 | 5.359281 |
| `availability` | 0.877371 | 321 | 96.1078 | 0.10 | 9.610778 |
| | | | | **1.00** | **91.279940** |

Published `composite_score` for Brunson: **91.2799401198**. The sum above reconciles to
within 4 × 10⁻¹¹, i.e. exactly, up to float noise.

The 179th-of-334 TS% residual is the flat-fit problem in miniature: a 30-usage guard
shooting .580 lands at the *median* of the efficiency component and drags roughly 4.4
points off his composite relative to his other four components. Whether that is a fair
verdict on his scoring is exactly the question §5 says the residual is not equipped to
answer.

**Step 4 — how that score prices out** (dollars; replacement denominator, $5,029,299/win)

```
expected_salary = 8.529248 × 5,029,299                       = $42,896,139
cap hit                                                       = $37,739,521
surplus         = 42,896,139 − 37,739,521                     = +$5,156,618

cap_pct         = 37,739,521 / 164,961,000                    =  0.228778
market line     = 0.00203867 × 91.27994 + 0.01823072          =  0.204321
cap_pct_residual= 0.228778 − 0.204321                          = +0.024458
```

Read that last line as: the market pays about 20.4% of the cap for a 91.3 composite, and
Brunson is on 22.9% — roughly **2.4 points of cap above market rate**, a modest overpay by
the empirical line, even though the model line (which prices his win total directly)
calls him a small bargain. The two lines disagreeing is the point; see
[ADR-013](#adr-013--two-regression-lines-not-one).

#### 8. How the score is used

The composite score is the **X axis** of the value scatter. Y is the 2026-27 cap hit as a
share of the cap. Two lines are drawn over that scatter and they are fit differently:

| Line | Fit on | Population | This build |
|---|---|---|---|
| **Market** — `cap_pct ~ composite_score` | market-priced contracts only | **190** of 334 | slope 0.00203867, intercept 0.01823, r² 0.241 |
| **Model** — `(war_blended × $/win) / cap ~ composite_score` | every scored player | 334 | slope 0.00292534, intercept −0.07181, r² 0.795 |

The market fit **excludes CBA-suppressed contracts** — rookie-scale, minimum and two-way
deals, on either the "how it was acquired" or "what it pays" axis. Those players are still
plotted and still get residuals; they just do not get a vote in defining market price,
because their salary was set by a schedule rather than a negotiation. Including them drags
the line down until every market-priced veteran looks overpaid. Mid-level exception deals
*are* counted as market-priced: the MLE is a ceiling rather than a fixed scale, and
excluding it would strip the league's middle class out of the fit.

Expected salary and surplus, by contrast, never touch the composite score at all — they
run off blended WAR in win units:

```
expected_salary = max(war_blended × $/win, rookie minimum)
surplus         = expected_salary − 2026-27 cap hit
```

This separation is deliberate. The composite is a *readability* device (percentiles,
which spread the middle of the league out legibly); the valuation is a *quantitative*
claim (win units × dollars per win). Mixing them would make the surplus figures depend on
the shape of a percentile distribution, which has no dollar meaning.

Which is also why the X axis has a **toggle to raw blended WAR**. On that axis both
regressions are in win units against cap share, so the fitted slope is literally
dollars-per-win — a directly interpretable quantity that the percentile axis cannot
produce. The chart is readable in one mode and rigorous in the other without forking the
model ([ADR-005](#adr-005--composite-score-defensible-default-fully-documented)).

**Reproducing this.** `python -m etl.build` regenerates `data/processed/*.json`, and the
constants quoted above are re-emitted into `meta.json` on every run, so a reader can
re-derive any player's score from the published artifacts alone.

### Valuation

```
$/win (naive)            = 164,961,000 / 41   = $4.02M
$/win (replacement-adj.) = 164,961,000 / 32.8 = $5.03M    ← default
Expected Salary          = Blended WAR × $/win
Surplus Value            = Expected Salary − 2026-27 cap hit
```

**Negative production.** Expected Salary is floored at the rookie minimum rather than
allowed to go negative, and affected players are flagged. An unfloored model produces
surplus values more negative than the player's entire salary, which is not a coherent
statement about a contract.

**Regression population.** The market line is fit on **negotiated contracts only** —
free-agent deals, extensions, max/designated-veteran deals, and mid-level exceptions.
Rookie-scale, minimum, and two-way deals are excluded: they are CBA-suppressed rather
than market-priced, and including them drags the line down until every market-priced
veteran looks overpaid. Those players are still plotted — they just do not get a vote in
defining market price.

The mid-level exception is counted as market-priced. It is a *ceiling*, not a fixed
scale like the rookie and minimum tables, and most MLE deals sign below it. Excluding
them would strip the league's entire middle class out of the fit and leave the line
determined by stars and cheap veterans alone.

See [ADR-013](#adr-013--two-regression-lines-not-one) for why there are two lines rather
than one.

### Known data-source hazards

Recorded because each cost real debugging time and would silently recur.

**Basketball-Reference serves UTF-8 with no charset header.** `Content-Type: text/html`
with no `charset` parameter means `requests` falls back to ISO-8859-1 per RFC 2616, and
`Alperen Şengün` decodes as `Alperen Å\x9eengÃ¼n`. The corruption lands in `resp.text`
and therefore in the on-disk cache, and it hits precisely the accented names that are
hardest to match anyway — a silent join loss disguised as a matching problem. Fixed once
in `ratelimit.fetch` by trusting the content sniff over the absent header. The scrape
cache is versioned (`CACHE_VERSION`) so entries written by the old logic are invalidated
rather than silently reused.

**Basketball-Reference hides tables inside HTML comments.** Several tables, including the
contracts index, are served as `<!-- <table> ... -->`. Parsers must check both the live
DOM and comment blocks.

**The combined row for traded players is `2TM`/`3TM`/`4TM`, not `TOT`.** The 2025-26 page
contains no `TOT` rows at all. 72 players require collapsing; the combined row is emitted
first and carries no `partial_table` class, and the name cell's `csk` sort key encodes
stint order explicitly.

**Spotrac's contracts table is JS-paginated at 100 rows** regardless of any `limit-N` path
segment. This is why Spotrac is scoped to enrichment on the largest contracts (ADR-003).

**A per-process rate limiter is not a rate limiter.** This one was learned the hard way.
Running the orchestrator alongside three parallel scraper processes drew a
Basketball-Reference 429 with `Retry-After: 3597` — a one-hour ban — even though every
individual process was correctly pacing itself below 18 req/min. Each interpreter had its
own token bucket and believed it was compliant; the server saw the sum. Requests now pass
through a `flock`-guarded sliding window in `data/raw/.ratelimit/` shared by every process
on the machine. A four-process test asserts they collectively receive exactly the budget,
not four times it.

**Never sleep off a `Retry-After` you did not bound.** The original 429 handler slept for
whatever the header said, inside a three-attempt retry loop — so a one-hour ban would have
stalled the build for three hours while producing nothing, and in CI would have silently
consumed the entire job timeout. Waits above `MAX_429_BACKOFF_SECONDS` (90s) now fail fast
so the caller can fall back to cache or surface the ban immediately.

---

## Verified output (first full build, 2026-08-01)

A dated snapshot, kept as the record of the first end-to-end run. Every constant is
refit on each nightly rebuild, so these figures drift slightly from the live
`data/processed/meta.json` — the worked example under
[How the production score is calculated](#how-the-production-score-is-calculated) quotes
the 2026-08-02 build instead.

30 teams, 509 contract rows, $5.89B in 2026-27 commitments. 506 unique contracted
players → 428 with 2025-26 production → 396 with a cap hit → **336 after the
500MP/20G scope filter**. 336 headshots mirrored (5.4 MB).

All three WAR estimates calibrate to a common league total: `vorp=819.2 ws=819.2
darko=820.3`. Fitted `replacement_ws48 = 0.0297`, `replacement_dpm = −2.285`.

| | slope | r² | n |
|---|---|---|---|
| Market line (both denominators) | 0.00202 | 0.244 | 192 |
| Model line — naive | 0.00233 | 0.795 | 336 |
| Model line — replacement | 0.00292 | 0.795 | 336 |

Model-line slope ratio is exactly **1.2500**, matching $5.03M ÷ $4.02M — the $/win
toggle demonstrably moves the line.

Top surplus: Wembanyama +$42.5M (rookie scale), Gilgeous-Alexander +$36.9M (max),
Jokić +$28.0M (designated veteran, at 35.8% of the cap). Bottom: Davis −$51.1M,
LaVine −$47.2M. Team extremes: Boston +$88.6M, Sacramento −$113.1M.

Jokić posting a large positive surplus *while earning a supermax* is the model
working: he is one of the few players whose production justifies that price.

## Known limitations

Stated up front, because a value model that hides its weaknesses is not useful.

1. **Rookie-scale contracts will structurally dominate the "best value" list.** The CBA
   caps what those players can earn, so an All-NBA player on a rookie deal shows enormous
   surplus every single time. That is a fact about the CBA, not a discovery about the
   player. Fitting the regression on free-agent deals mitigates the distortion but does
   not remove it. A future refinement would measure surplus *relative to expectation for
   that contract class*.

2. **Box-score metrics undervalue defense and off-ball gravity.** DARKO's on-off component
   helps; it does not fully solve it.

3. **One season of production is a noisy estimate of true talent.** A player's 2025-26
   line reflects role, health, and teammate quality, not just ability.

4. **Cap hit ≠ cash paid.** Guarantees, options, incentives, trade kickers, and stretched
   dead money all diverge from the cap number. Spotrac enrichment surfaces these on the
   player card, but the model's Y-axis remains the cap hit.

5. **The cap is not what teams actually spend.** Tax-paying teams spend well above it, so
   a cap-derived $/win understates the true market price of a win.

6. **The TS%-vs-usage residual does not measure what its name says.** The league-wide fit
   of `TS% ~ USG%` comes out essentially flat (slope −0.00032, r² = 0.0013 on the current
   build), so the residual correlates with raw TS% at r = 0.999. The 10% component is in
   practice a recentred TS%, not "efficiency above expectation at that usage" — usage
   barely predicts efficiency once the population is restricted to 500+ minute rotation
   players. It is left in because a TS% term at 10% is defensible on its own merits, but
   the usage adjustment should be treated as inert until it is replaced with a stronger
   conditioning variable. Full detail in
   [How the production score is calculated](#how-the-production-score-is-calculated).

7. **The composite's components are not independent, and DARKO is counted twice.**
   `darko_dpm` carries a 25% weight in its own right *and* contributes one of the three
   estimates inside `war_blended`, which carries 40% — so DARKO drives roughly 38% of the
   score. The same double count applies to Win Shares (`ws_per_48` at 15%, plus `war_ws`
   inside the blend). The weights therefore overstate how many independent views of a
   player the score actually combines.

---

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

---

## Repository layout

```
nba_analysis/
├── etl/
│   ├── sources/            # one adapter per data source
│   │   ├── bbref_stats.py         2025-26 advanced + per-game
│   │   ├── bbref_contracts.py     2026-27 salaries (custom parser)
│   │   ├── spotrac.py             contract structure enrichment
│   │   ├── darko.py               public Google Sheet → CSV
│   │   └── player_ids.py          nba_api static module (OFFLINE)
│   ├── transform/
│   │   ├── war.py                 three WAR estimates + calibration
│   │   ├── composite.py           percentile blend + TS% residual
│   │   ├── valuation.py           $/win, expected salary, surplus
│   │   └── contract_type.py       rookie / min / MLE / max classification
│   ├── crosswalk.py        canonical identity spine
│   ├── ratelimit.py        shared token bucket, <20 req/min
│   └── build.py            orchestrator → data/processed/*.json
├── data/
│   ├── raw/                cached scrapes (gitignored)
│   └── processed/          committed artifacts the frontend reads
├── web/                    Vite + React + deck.gl static SPA
│   ├── src/chart/          ScatterplotLayer (dots) ↔ IconLayer (faces)
│   ├── src/views/          scatter | leaderboard | team | player
│   ├── src/model/          client-side $/win + denominator recomputation
│   ├── src/styles/         design tokens
│   └── scripts/            mock data, dev symlink, build-time staging
├── tests/
├── vercel.json
└── .github/workflows/
    ├── ci.yml              tests + build on every push/PR
    └── nightly.yml         scrape → rebuild → commit → deploy
```

---

## Deployment

Hosted as a static site on **Vercel**, rebuilt nightly by **GitHub Actions**.

### How the pieces fit

The ETL and the site deploy are deliberately decoupled. Vercel never runs
Python and never touches Basketball-Reference — it only ever builds a frontend
from a dataset that is already committed to the repository.

```
GitHub Actions (nightly, 11:00 UTC)
  pytest  →  etl.build --refresh --verify  →  commit data/processed
                                                     │
                                                     ▼
                                        npm run build (stages data)
                                                     │
                                                     ▼
                                          vercel deploy --prebuilt
```

`data/processed/` is committed, not gitignored. That is what makes the site
reproducible from a clean checkout and what lets Vercel build without any
credentials for the upstream sources.

### Workflows

| Workflow | Trigger | Network access | What it does |
|---|---|---|---|
| `ci.yml` | push to `main`, any PR | none | ADR-010 guard, `pytest`, frontend build, asserts the built site shipped real (non-mock) data |
| `nightly.yml` | cron `0 11 * * *`, manual | Basketball-Reference, DARKO, Spotrac, NBA CDN | re-scrapes, rebuilds, commits `data/processed`, builds and deploys |

`ci.yml` deliberately does **not** run the scrapers. Basketball-Reference
allows 20 requests/minute and bans for up to an hour on violation, so hitting
it on every push would be both slow and hostile. The nightly job is the only
workflow permitted to touch the network sources.

11:00 UTC is ~06:00 ET — late enough that every game has finalized and BBRef
has ingested the previous night's box scores.

### Build-time data staging

`npm run build` runs `prebuild` → `web/scripts/stage-data.mjs`, which copies
`data/processed/*.json` plus the mirrored headshots into `web/public/data/`.

`web/public/data/` is **gitignored**. During local development
`npm run link-data` symlinks it at `data/processed` so an ETL run shows up
instantly; on a fresh clone (Vercel, CI) that symlink does not exist, so the
staging script materialises real files instead. It detects an existing valid
symlink and leaves it alone, so a local `npm run build` does not destroy your
dev setup.

Headshots must be same-origin: `cdn.nba.com` sends no
`Access-Control-Allow-Origin` header and WebGL refuses to texture a
cross-origin image, so a build without mirrored portraits degrades to
placeholder marks. See `etl/sources/headshots.py`.

### Vercel configuration

`vercel.json` lives at the repository root and drives the build from there
rather than setting a Vercel "Root Directory", so `data/processed/` is
guaranteed present in the build context:

```json
"installCommand": "npm ci --prefix web",
"buildCommand":   "npm run build --prefix web",
"outputDirectory": "web/dist"
```

Cache headers are set per asset class: hashed bundles immutable for a year,
headshots a week (they change at most once a season), and the JSON dataset
`max-age=0, s-maxage=3600` so a browser always revalidates while the CDN
absorbs the load between nightly refreshes.

### Two deploy modes

The nightly workflow's deploy step is gated on `secrets.VERCEL_TOKEN`:

- **Token present** — Actions runs `vercel build` + `vercel deploy --prebuilt`,
  publishing the exact artifact that passed the verification step rather than
  letting Vercel rebuild from source.
- **Token absent** — the step no-ops. This is the correct behaviour if you
  connected Vercel's own Git integration instead, since the nightly data commit
  already triggers a redeploy there.

Pick one. Enabling both means every nightly run deploys twice.
