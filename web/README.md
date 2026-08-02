# web — NBA contract value dashboard (frontend)

Vite + React + deck.gl static SPA. Reads three JSON artifacts produced by the
Python ETL and renders four views over them. No server, no API, no build-time
data coupling — see [ADR-009](../README.md#adr-009--deployment-github-actions-etl--static-site).

---

## Run it

```bash
cd web
npm install
npm run mock      # generate the mock dataset into public/data/  (first run only)
npm run dev       # http://localhost:5173
```

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server, with the headshot proxy (see below) |
| `npm run build` | Production bundle into `dist/` |
| `npm run preview` | Serve `dist/` locally, proxy included |
| `VITE_HEADSHOT_BASE=/headshot npm run build` | build whose portraits use the preview proxy |
| `npm run mock` | Regenerate `public/data/*.json` — deterministic, seeded |
| `npm run link-data` | Point `public/data` at the real `data/processed/` output |

`npm run mock` accepts `--seed <n>` and `--out <dir>`. The same seed always
produces the same dataset, so a screenshot taken today reproduces tomorrow.

---

## The data contract

The frontend fetches exactly three files from `${BASE_URL}data/`:

| File | Shape | Source of truth |
|---|---|---|
| `players.json` | array of `PLAYER_RECORD_SCHEMA` | [`etl/schema.py`](../etl/schema.py) |
| `meta.json` | `META_SCHEMA` | same |
| `teams.json` | array of `TEAM_RECORD_SCHEMA` | same |

`src/data/useDataset.js` validates the required player fields on load and fails
loudly with a legible message rather than rendering a wall of `NaN`. Adding a
field is safe; renaming or removing one is a breaking change.

### Switching from mock to real data

```bash
npm run link-data            # symlink  web/public/data -> data/processed
npm run link-data -- --copy  # or copy, for CI and Windows
npm run link-data -- --undo  # back to a plain directory; then `npm run mock`
```

Nothing under `src/` knows which is in use. `meta.is_mock` (mock only) is what
drives the "Mock data" banner.

### Things the ETL must honour

Three contract points the frontend depends on beyond the field list:

1. **`meta.regression` carries both fits.**
   `regression.naive` and `regression.replacement`, each
   `{slope, intercept, r2, n}`, expressed so that

   ```
   expected cap_pct = intercept + slope × composite_score
   ```

   i.e. the y-units are **percent of the cap**, not dollars and not a fraction.
   Fit over `is_market_priced` players only. Because the fit runs on expected
   salary, it carries the denominator — which is what makes the league line
   visibly move when the user flips the toggle. A single shared fit would make
   the toggle look broken.

2. **Both `$/win` variants are precomputed on every record.**
   `surplus_naive` / `surplus_replacement` and `expected_salary_naive` /
   `expected_salary_replacement`. The toggle is a field lookup, never a
   recompute. Same for `teams.json`: `total_surplus_naive` and
   `total_surplus_replacement`.

3. **`headshot_url` is the canonical portrait URL**, but see the CORS note
   below — the CDN cannot feed the WebGL atlas directly.

Optional but used if present: `meta.luxury_tax`, `meta.first_apron`,
`meta.second_apron`, `meta.excluded_count`, `meta.min_minutes`.

---

## Portraits and CORS — the one real gotcha

`cdn.nba.com` serves headshots with **no `Access-Control-Allow-Origin` header**.
A plain `<img>` does not care, so the cards, tables and roster lists show real
portraits anywhere. But deck.gl's `IconLayer` has to upload each image into a
**WebGL texture**, and the browser refuses to texture a cross-origin image. The
dynamically-packed atlas therefore cannot read the CDN URLs directly from a
browser.

The frontend resolves this with a configurable base:

| Environment | Portrait source |
|---|---|
| `npm run dev` / `npm run preview` | `/headshot/{id}.png`, proxied to the CDN by Vite — same-origin, atlas works |
| Production, `VITE_HEADSHOT_BASE` set | that base, e.g. `./data/headshots` |
| Production, unset | raw `headshot_url` → blocked by CORS → silhouette placeholders |

The last row is a degraded chart, not a broken one: `src/chart/useHeadshots.js`
probes every URL first and substitutes a silhouette for anything that 404s,
times out, or fails CORS, so the scatter never has holes in it.

**Recommended fix on the ETL side:** have the nightly job mirror portraits next
to the JSON — `data/processed/headshots/{nba_player_id}.png` — and build with
`VITE_HEADSHOT_BASE=./data/headshots`. That makes them same-origin, removes the
CDN from the render path entirely, and costs one download per player per night.

---

## How the chart works (ADR-007)

`src/chart/ScatterChart.jsx`. X is `composite_score`, Y is `cap_pct`, drawn in a
fixed 1000×640 cartesian world under an `OrthographicView`.

**Every player is in the dataset at every zoom level.** Only the representation
changes — nothing is ever filtered out to make the picture tidy, including by
the filter bar, which de-emphasises rather than removes.

One continuous number, `lod`, is derived from zoom and drives a three-stage
cross-fade:

| `lod` | What you see |
|---|---|
| 0 | `HexagonLayer` density bins, colour = players per bin |
| ~0.5 | bins dissolving while individual marks fade up underneath |
| 1 | marks inflated into ringed headshots (`IconLayer`) |

The threshold is **derived from the data**, not hard-coded: the median
nearest-neighbour distance between points tells us the zoom at which a
face-sized icon stops colliding with its neighbour, and the band is centred
there. Denser data pushes the swap later automatically.

Ring colour encodes surplus polarity (blue = underpaid, red = overpaid). A
second concentric ring marks CBA-suppressed contracts — the deals the
regression is *not* fit on. That is a shape cue, not a colour cue.

Other chart notes:

- Camera moves are tweened by the component, not by deck's `TransitionManager`.
  With a controlled `viewState`, every interpolated frame echoes back through
  `onViewStateChange`, and an echo carrying no transition props cancels the
  transition on its first frame.
- Zooming in from the home view drifts toward the **data centroid**. `cap_pct`
  is heavily right-skewed, so the geometric centre of the plot is empty space.
- Axes, gridlines and the league line are SVG over/under the WebGL canvas, so
  they stay hairline-crisp at any zoom.
- No WebGL → the scatter is replaced by an explanation, and the other three
  views (plain HTML) still work.

---

## Layout

```
src/
├── chart/
│   ├── ScatterChart.jsx    deck.gl layer stack + LOD cross-fade
│   ├── PlotFrame.jsx       SVG axes, grid, league-average line
│   ├── ChartLegend.jsx     dual legend, cross-fades with the marks
│   ├── useHeadshots.js     atlas preloader, 404/CORS → placeholder
│   ├── headshotUrl.js      canonical URL → same-origin rewrite
│   └── scales.js           data ↔ world mapping, ticks, easing
├── model/
│   ├── constants.js        palette, contract vocabulary, geometry
│   ├── valuation.js        $/win field swap, filters, rollups
│   └── format.js           every number the user reads
├── views/                  scatter | leaderboard | teams | player
├── components/             filters, mode toggle, player card, states
└── data/useDataset.js      fetch + validate + loading/error/empty
```

---

## Design constraints

- **Background is white.** Explicit requirement. Structure comes from
  hairlines, spacing and weight; there are no decorative fills.
- **Colour means one thing:** surplus polarity (diverging blue ↔ red) and bin
  density (sequential blue). Contract type is carried by a glyph plus a word
  and never by hue, so nothing depends on colour vision.
- The palette is validated for CVD separation and contrast against `#ffffff`.
- Click-to-pin over hover: at ~500 marks, hover alone is too fiddly to be the
  only way to read a player.
- `prefers-reduced-motion` disables the camera tween and all transitions.
