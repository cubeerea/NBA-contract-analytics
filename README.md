# NBA Contract Value Dashboard

Every contracted NBA player's production plotted against what he is paid. A league-average price line is fit
through the scatter; the largest residuals are the best and worst contracts in the league. Rebuilt nightly.

Every assumption is visible and toggleable. Valuation turns on a few arbitrary constants — what a win costs,
what replacement level means, which contracts set market price — so they live in the UI, not in one confident
number. Full derivation and worked example: **[METHODOLOGY.md](METHODOLOGY.md)**.

## The problem

No public tool answers "which NBA contracts are good value?" transparently. EPM and LEBRON are paywalled,
RAPTOR is frozen (discontinued 2023), the rest is editorial. This is the free, reproducible alternative.

## Reading the chart

2025-26 production against 2026-27 salary, both settled, cap $164,961,000. Scope is a 2026-27 contract plus
**500 minutes and 20 games** — **334 in, 59 excluded** (live counts in `meta.json`); all percentiles use those 334.

| Axis | Quantity | Range |
|---|---|---|
| **X** | Composite score: blended WAR 40%, DARKO DPM 25%, WS/48 15%, TS% residual 10%, availability 10%. Toggles to raw blended WAR. | 0–100 |
| **Y** | 2026-27 cap hit as a share of the $164,961,000 cap (default) or of the player's own team payroll. | 0–~40% |

Y is a share, not dollars, so height reads as "how much of a roster this player consumes" across cap seasons.
Sources: Basketball-Reference (stats, `/contracts/`), DARKO (DPM), Spotrac (structure), NBA CDN (portraits).

### Two lines

| Line | Fit | n | Answers | Moves with $/win? |
|---|---|---|---|---|
| **Market** | OLS `cap_pct ~ composite`, market-priced deals only — slope 0.00204, r² 0.241 | 190 | what teams *do* pay | no, it is empirical |
| **Model** | `(blended WAR × $/win) / cap ~ composite` — r² 0.795 | 334 | what production *should* earn | yes |

Rookie-scale, minimum, and two-way deals are plotted but get no vote in the market fit — a schedule set their
salary, not a negotiation. Residuals and leaderboard surplus measure against the **market** line; the gap
between the lines is how far revealed pricing sits from the model.

### Toggles

| Toggle | Options | Effect |
|---|---|---|
| **$/win** | $4.02M naive, **$5.03M replacement-adjusted** (default) | `surplus = blended WAR × $/win − cap hit`. Moves the model line only. |
| **Y denominator** | **league cap** (default), team payroll | The cap keeps one ruler under a league-wide regression. Team payroll inverts real comparisons: Curry ($62.6M) is paid $4.1M more than Davis ($58.5M) yet plots cheaper, purely because Golden State spent more on everyone else. |

Team mode invalidates the ETL's cap-relative numbers, so the client refits (`web/src/model/denominator.js`):
market line by OLS on the active axis, residuals recomputed, Y-tick dollar sub-labels dropped, model reference
widened to a **band** — one dollar figure is thirty different shares. Player detail also shows team payroll and
its rung on the CBA's four spending lines, derived as multiples of the live cap in `web/src/model/payroll.js`.

### Views

| View | For |
|---|---|
| **Scatter** | the whole league at once — shape, clusters, outliers |
| **Leaderboard** | the same data sorted. Rookie deals structurally dominate the top; see [Known limitations](#known-limitations) |
| **Team rollup** | surplus by franchise. Player filters apply to surplus and WAR; payroll and apron state come from `teams.json`, unfiltered |
| **Compare** | up to three players, with the WAR uncertainty band and league percentile bars |

## Requirements

Twelve questions from requirements refinement, answered verbatim. The ADRs are downstream of these.

| # | Question | Answer | Drives |
|---|---|---|---|
| Q1 | EPM is paywalled. Plan for the headline metric? | *"ideally use both 1 and 2, focus on obtain raw data. we can always implement our own formula to calculate those scores/metrics."* | ADR-001 |
| Q2 | Which production year against which salary year? | *"2025-26 stats vs 2026-27 salary"* | ADR-002 |
| Q3 | How should salary data be sourced? | *"i just want it to be live data or at least be scraped periodically with a cron job… would rather take a hybrid approach"* | ADR-003 |
| Q4 | What does "live" mean here? | *"Nightly scheduled refresh"* | ADR-004 |
| Q5 | What stack? | *"Doesn't really what tech stack it is, I want the data points to be the headshot of each player."* | ADR-007 |
| Q6 | How are composite weights decided? | *"You propose a defensible default"* | ADR-005 |
| Q7 | $/win denominator and regression population? | *"Compute both, toggle in UI"* | ADR-006 |
| Q8 | How deep should trade eligibility go? | *"Simple flags only"* | ADR-011 |
| Q9 | ~500 headshots will overlap badly. Handling? | *"Binned overview, faces on zoom"* | ADR-007 |
| Q10 | Which players make the chart? | *"Under contract + minutes threshold"* | ADR-008 |
| Q11 | Where does this run? | *"GitHub Actions ETL → static hosted site"* | ADR-009, ADR-010 |
| Q12 | What else besides the scatter? | *"Ranked surplus-value leaderboard, Team-level rollup, Player detail / comparison view"* | ADR-012 |

Q1 makes this an ingestion pipeline with a swappable metric layer. Q5 made the stack a consequence of a
rendering requirement. Q7 set the principle: contested assumptions become features.

## Architecture decisions

| ADR | Decision | Why |
|---|---|---|
| **001** | Ingest raw BBRef and DARKO data; compute every derived metric ourselves. No EPM dependency. | EPM is paywalled and therefore non-reproducible; DARKO is free, nightly, and genuinely projective. A provider interface keeps EPM addable behind a flag. |
| **002** | Score completed 2025-26 production against active 2026-27 salary. | Answers "who is overpaid right now", and both inputs are settled. Players with a contract but no 2025-26 production become an explicit `no_prior_season` class, excluded from the fit rather than plotted at zero. |
| **003** | Bulk salary from BBRef `/contracts/`; contract structure from Spotrac. | BBRef covers the league in ~30 polite requests; Spotrac adds options, guarantees, and kickers. Spotrac is enrichment, so its outage degrades the build but never breaks it. |
| **004** | A nightly GitHub Actions cron rebuilds and commits the dataset. | Contracts move on trade timescales, stats on daily ones. Real-time is impossible under source rate limits and meaningless for season-long metrics. |
| **005** | Percentile-weighted composite with every weight documented, plus a raw-WAR axis toggle. | The blend is readable; the WAR axis makes the regression slope literally dollars-per-win. Readable or rigorous without forking the code. |
| **006** | Expose both $/win denominators; default to replacement-adjusted ($5.03M, not $4.02M). | The naive `cap / 41` assumes a replacement roster wins zero games. It wins roughly 8–15, so the naive figure inflates apparent overpay by about 20% league-wide. |
| **007** | Dots when zoomed out, headshot icons on zoom-in. Superseded the original hex-bin overview. | 334 headshots collide into illegibility in one viewport, but hex bins read as a heatmap of nobody. Dots keep one mark per player at every zoom, bleaching to white as the portrait fades up inside them so surplus colour survives as a ring. deck.gl `ScatterplotLayer` + `IconLayer` on a data-derived zoom threshold. |
| **008** | Require a 2026-27 contract plus 500 MP and 20 GP. | Per-minute metrics are unstable at low sample; a 40-minute call-up produces a wild surplus that would own the outlier view and discredit the chart. |
| **009** | Nightly Actions ETL commits data; a static frontend consumes it. | Free, no server, reproducible from a clean checkout — viable only because ADR-010 removes the stats.nba.com dependency. |
| **010** | `nba_api` only for `stats.static.players`, which is bundled local data and issues zero HTTP. All statistics from BBRef. | stats.nba.com blacklists datacenter IPs and fingerprints TLS handshakes, which breaks any cloud-run ETL. The offline module still yields the NBA player IDs that headshot URLs need. |
| **011** | Trade eligibility as simple flags: dates, no-trade clauses, recent-signing locks. No apron-aware salary matching. | Full 2023-CBA matching means modeling every team's live payroll against apron rules, base year compensation, and the poison pill — a project the size of the dashboard. The team rollup already produces the payroll state a future engine would need. |
| **012** | Three views beyond the scatter: leaderboard, team rollup, compare. | The scatter shows shape; a sortable table answers "who exactly is #1"; the rollup shows which front offices extract the most value per dollar. |
| **013** | Draw both an empirical market line and a model line. | An empirical fit of observed salaries cannot depend on our $/win choice, because that constant appears nowhere in the data being fit. Residuals rank against the market line, so "underpaid" means underpaid versus what teams pay comparable players. |
| **014** | Join DARKO on its published `NBA ID` column; normalized-name matching only as a fallback. | An exact integer key beats fuzzy matching and sidesteps accents: 35 of 530 DARKO names differ from BBRef by diacritics alone (`Nikola Jokic` / `Nikola Jokić`). The fallback means a dropped ID column degrades rather than fails. |
| **015** | Keep share-of-cap as the default Y denominator; add a team-payroll toggle that refits client-side. | The cap is a limit, not a ceiling — Cleveland's books run to $226.0M, Brooklyn's to $150.8M. But a league-wide market line needs one ruler, and team payroll inverts real comparisons. Both fits and `cap_pct_residual` are cap-relative in the ETL, so team mode must refit rather than redraw. |

## Design patterns

| Pattern | Substance |
|---|---|
| **Canonical identity spine** | All sources join through one crosswalk (BBRef slug ↔ NBA player ID ↔ Spotrac slug), built before any other stage, so name-join failures on suffixes and accents exist in exactly one asserted-against place. |
| **Source adapter** | Each source implements the same `fetch() → DataFrame` contract in `etl/sources/`, ranked essential vs enrichment: essential failure fails the build loudly, enrichment logs and degrades. Adding EPM is one adapter. |
| **Calibrate before blending** | WS, VORP, and DPM live on different scales; each is converted to WAR and calibrated to a common league total *before* averaging. Averaging uncalibrated metrics is the most common error in public contract-value work. |
| **Precompute heavy, recompute light** | The ETL emits static artifacts; the client recomputes only what a toggle invalidates — a multiply for $/win, a full refit for the Y denominator. |
| **Derive anything pegged to the cap** | The CBA defines minimums and exceptions as percentages of the cap, so a dollar literal is silently wrong the next season. This bit us: a hardcoded `ROOKIE_MINIMUM_SALARY` put an $85k error into every floored expected salary and pushed real veteran-minimum deals into the market regression. It is now `SALARY_CAP × 0.8231%`. |
| **Orthogonal facts need orthogonal fields** | A contract's acquisition route and its price tier are independent — an extension can also be a max. Collapsing both into one enum let BBRef's "Extension" note overwrite every max deal and empty the Max category on live data. `contract_type` and `salary_tier` are now separate axes. |

## Known limitations

1. **Rookie-scale deals structurally dominate the best-value list.** The CBA caps what those players earn, so
   an All-NBA rookie contract always shows huge surplus. Fitting on market-priced deals mitigates, not removes.
2. **Box-score metrics undervalue defense and off-ball gravity.** DARKO's on-off component helps, not solves.
3. **One season is a noisy talent estimate** — role, health, and teammate quality are all in there.
4. **Cap hit ≠ cash paid.** Options, guarantees, incentives, kickers, and stretched dead money all diverge.
5. **The cap is not what teams spend.** Tax payers spend well above it, so a cap-derived $/win understates the
   true market price of a win.
6. **The TS%-vs-usage residual is inert.** The league fit of `TS% ~ USG%` is flat (slope −0.00032, r² 0.0013),
   so the residual tracks raw TS% at r = 0.999 — that 10% component is a recentred TS%.
   [Detail](METHODOLOGY.md#why-the-ts-residual-is-inert).
7. **DARKO is counted twice.** It carries 25% alone and is one of three estimates inside `war_blended` at 40%,
   so it drives ~38% of the score. Win Shares double-counts the same way; the weights overstate independence.

## Reference data

Checked 2026-08-01.

| Fact | Value | Source |
|---|---|---|
| 2026-27 salary cap | $164,961,000 | [pr.nba.com](https://pr.nba.com/2026-27-salary-cap/) |
| 2026-27 luxury tax | $200,428,000 | pr.nba.com |
| 2026-27 first / second apron | $209,015,000 / $221,686,000 | pr.nba.com |
| 2025-26 salary cap (prior-year context) | $154,647,000 | pr.nba.com |
| DARKO | free public [Google Sheet](https://docs.google.com/spreadsheets/d/1mhwOLqPu2F9026EQiVxFPIN1t9RGafGpl-dokaIsm9c/edit), nightly | darko.app |
| Sports-Reference rate limit | 20 req/min; up to 24h ban | [bot policy](https://www.sports-reference.com/bot-traffic.html) |
| stats.nba.com from cloud | blocked — IP blacklist + Akamai TLS fingerprinting | [nba_api#633](https://github.com/swar/nba_api/issues/633) |
| `nba_api.stats.static.players` | bundled local data, zero network calls | [docs](https://github.com/swar/nba_api/blob/master/docs/nba_api/stats/static/players.md) |
| Headshot CDN | `cdn.nba.com/headshots/nba/latest/1040x760/{id}.png` | public, unauthenticated |

## Repository layout

```
etl/               sources/ adapters · transform/ (war, composite, valuation, contract_type)
                   crosswalk.py identity spine · ratelimit.py shared token bucket · build.py orchestrator
data/processed/    committed JSON artifacts the frontend reads (data/raw/ is cached scrapes, gitignored)
web/               Vite + React + deck.gl SPA — chart/ views/ model/ styles/ scripts/
tests/             pytest suite   ·   .github/workflows/   ci.yml, nightly.yml
```

## Deployment

Static site on **Vercel**, data rebuilt nightly by **GitHub Actions**. Vercel never runs Python. Committing
`data/processed/` is what makes the site reproducible from a clean checkout and buildable without credentials.

| Workflow | Trigger | Network | Does |
|---|---|---|---|
| `ci.yml` | push to `main`, any PR | none | ADR-010 guard, `pytest`, frontend build, asserts the site shipped real (non-mock) data |
| `nightly.yml` | cron `0 11 * * *`, manual | BBRef, DARKO, Spotrac, NBA CDN | re-scrape, rebuild, commit `data/processed`, build, deploy |

`ci.yml` never runs the scrapers — hitting BBRef on every push is slow and hostile under a 20 req/min limit.
11:00 UTC is ~06:00 ET, after BBRef ingests the previous night's box scores. `vercel.json` sits at the repo
root so `data/processed/` is in the build context; cache headers go per asset class (bundles a year, headshots
a week, dataset JSON `s-maxage=3600`). The nightly deploy step is gated on `secrets.VERCEL_TOKEN`: with it,
Actions runs `vercel build` + `vercel deploy --prebuilt`; without it the step no-ops, correct if you use
Vercel's Git integration. Enable one, not both.

```
python -m etl.build --refresh --verify   # rebuild data/processed/*.json
npm ci --prefix web
npm run link-data --prefix web           # symlink data/processed into web/public/data
npm run dev --prefix web
```

`npm run build` runs `web/scripts/stage-data.mjs`, copying JSON and mirrored headshots into the gitignored
`web/public/data/` and leaving an existing valid symlink alone. Headshots must be same-origin — `cdn.nba.com`
sends no CORS header and WebGL will not texture a cross-origin image — so a build without them uses placeholders.
