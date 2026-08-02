#!/usr/bin/env node
/**
 * Generates a statistically plausible MOCK dataset conforming exactly to the
 * contract in `etl/schema.py` (PLAYER_RECORD_SCHEMA / META_SCHEMA /
 * TEAM_RECORD_SCHEMA), so the frontend can be built and verified before the
 * Python ETL exists.
 *
 *   node scripts/generate-mock-data.mjs [--out public/data] [--seed 20262027]
 *
 * Real `nba_player_id` values are used (sourced from nba_api's offline static
 * player table, ADR-010) so the CDN headshots actually resolve. Everything
 * else — salaries, box-score lines, contract structure — is synthetic.
 *
 * Once the ETL is live these files are replaced by data/processed/*.json;
 * see `npm run link-data`. Nothing in src/ knows the difference.
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const argOf = (flag, fallback) => {
  const i = argv.indexOf(flag);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};

const OUT_DIR = resolve(HERE, '..', argOf('--out', 'public/data'));
const SEED = Number(argOf('--seed', '20262027'));

// --------------------------------------------------------------------------
// Verified constants (README "Verified reference data", checked 2026-08-01)
// --------------------------------------------------------------------------
const SALARY_CAP = 164_961_000;
const LUXURY_TAX = 200_428_000;
const FIRST_APRON = 209_015_000;
const SECOND_APRON = 221_686_000;
const DPW_NAIVE = SALARY_CAP / 41; // $4.02M — replacement roster wins 0
const DPW_REPLACEMENT = SALARY_CAP / 32.8; // $5.03M — replacement is .100 win pct
const ROOKIE_MIN = 1_310_000; // expected-salary floor
const MIN_MINUTES = 500; // ADR-008 playing-time floor
const TARGET_PLAYERS = 465;

const TEAMS = [
  'ATL', 'BOS', 'BKN', 'CHA', 'CHI', 'CLE', 'DAL', 'DEN', 'DET', 'GSW',
  'HOU', 'IND', 'LAC', 'LAL', 'MEM', 'MIA', 'MIL', 'MIN', 'NOP', 'NYK',
  'OKC', 'ORL', 'PHI', 'PHX', 'POR', 'SAC', 'SAS', 'TOR', 'UTA', 'WAS'
];
const POSITIONS = ['PG', 'SG', 'SF', 'PF', 'C'];

// --------------------------------------------------------------------------
// Deterministic RNG (mulberry32) — the mock must be reproducible.
// --------------------------------------------------------------------------
function mulberry32(a) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = mulberry32(SEED);
const gauss = () => {
  let u = 0;
  let v = 0;
  while (u === 0) u = rand();
  while (v === 0) v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
};
const pick = (arr) => arr[Math.floor(rand() * arr.length)];
const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
const round = (x, p = 0) => {
  const f = 10 ** p;
  return Math.round(x * f) / f;
};

// --------------------------------------------------------------------------
// Identity: real NBA player ids + names, synthetic BBRef slugs.
// --------------------------------------------------------------------------
const pool = JSON.parse(readFileSync(join(HERE, 'nba_player_ids.json'), 'utf8'));

const stripAccents = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '');
const slugFor = (name, seen) => {
  const clean = stripAccents(name)
    .replace(/[^A-Za-z\s'-]/g, '')
    .replace(/\b(Jr|Sr|II|III|IV|V)\b/g, '')
    .trim();
  const parts = clean.split(/\s+/);
  const first = (parts[0] || 'x').toLowerCase().replace(/[^a-z]/g, '');
  const last = (parts[parts.length - 1] || 'x').toLowerCase().replace(/[^a-z]/g, '');
  const stem = `${last.slice(0, 5)}${first.slice(0, 2)}`;
  let n = 1;
  let slug = `${stem}${String(n).padStart(2, '0')}`;
  while (seen.has(slug)) slug = `${stem}${String(++n).padStart(2, '0')}`;
  seen.add(slug);
  return slug;
};

// Shuffle the pool deterministically, then take the roster.
const shuffled = [...pool];
for (let i = shuffled.length - 1; i > 0; i--) {
  const j = Math.floor(rand() * (i + 1));
  [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
}
const roster = shuffled.slice(0, Math.min(TARGET_PLAYERS, shuffled.length));

// --------------------------------------------------------------------------
// Latent talent -> production. Right-skewed: a few stars, a long tail.
// --------------------------------------------------------------------------
const seenSlugs = new Set();

const draft = roster.map(([nbaId, name]) => {
  // Talent is standard normal with a modest right tail (superstars are rarer
  // and further out than a normal would put them). The tail is deliberately
  // bounded: unbounded it produced 27-WAR seasons, roughly triple what the
  // best real season on record looks like.
  let talent = gauss();
  if (talent > 1.1) talent = 1.1 + Math.min((talent - 1.1) * 1.55, 1.75);

  // Age: centred near the real league mean (~26) with a right tail of
  // veterans, since contract type keys off years of service.
  const age = clamp(Math.round(25.4 + gauss() * 3.6 + Math.max(0, gauss()) * 1.1), 19, 41);
  // Availability + role. Better players play more, with injury noise.
  const roleFactor = clamp(0.5 + 0.28 * talent + gauss() * 0.22, 0.08, 1.32);
  const games = clamp(Math.round(82 * clamp(roleFactor * 0.86 + rand() * 0.2, 0.12, 1)), 12, 82);
  const mpg = clamp(10 + roleFactor * 19 + gauss() * 3.2, 8.5, 38.5);
  const minutes = Math.round(games * mpg);

  return { nbaId, name, talent, age, games, minutes, mpg, roleFactor };
});

// ADR-008: enforce the minutes floor. Rebuild anyone who falls under it
// rather than dropping them (keeps the roster count stable).
for (const p of draft) {
  if (p.minutes < MIN_MINUTES) {
    p.games = clamp(p.games, 30, 82);
    p.mpg = clamp(p.mpg, MIN_MINUTES / p.games + 1.5, 38.5);
    p.minutes = Math.round(p.games * p.mpg);
  }
}

// Box-score / impact metrics, all driven off the same latent so they agree
// on average but disagree per player (that disagreement is war_spread).
for (const p of draft) {
  const t = p.talent;
  p.usg_pct = round(clamp(15 + t * 4.4 + gauss() * 2.6, 8.5, 38.5), 1);
  // TS% rises with talent but falls with usage — the README's usage/efficiency
  // tradeoff, which the composite folds in as a residual.
  const tsExpected = 0.545 + (p.usg_pct - 19) * -0.0022;
  p.ts_residual = round(t * 0.0125 + gauss() * 0.0165, 4);
  p.ts_pct = round(clamp(tsExpected + p.ts_residual, 0.4, 0.72), 3);

  p.bpm = round(clamp(t * 2.45 + gauss() * 1.15 - 1.35, -6.5, 11.5), 1);
  // Real single-season VORP tops out near 10; keep the mock inside that.
  p.vorp = round(clamp(((p.bpm + 2) * p.minutes) / 2400 + gauss() * 0.18, -0.9, 10.2), 1);
  p.ws_per_48 = round(clamp(0.098 + t * 0.043 + gauss() * 0.021, -0.06, 0.32), 3);
  p.ws = round((p.ws_per_48 * p.minutes) / 48, 1);
  p.darko_dpm = round(t * 2.05 + gauss() * 0.95 - 0.85, 2);

  // Three raw WAR estimates, each carrying its own error.
  const poss = (p.minutes / 48) * 100.4;
  p._war_vorp = p.vorp * 2.7;
  p._war_ws = p.ws - (0.0345 * p.minutes) / 48 + gauss() * 0.25;
  p._war_darko = ((p.darko_dpm + 2.1) * poss) / 100 / 31.5 + gauss() * 0.3;
}

// "Calibrate before blending" (README design patterns). WS, VORP and DARKO are
// expressed in incompatible units, so each is rescaled until its league total
// agrees with VORP's before any averaging happens. Averaging uncalibrated
// metrics is the most common error in public contract-value analysis; the mock
// makes the same correction the real pipeline has to, which is why `war_spread`
// here is genuine per-player disagreement rather than a units mismatch.
{
  const total = (key) => draft.reduce((a, p) => a + p[key], 0);
  const target = total('_war_vorp');
  const kWs = target / total('_war_ws');
  const kDarko = target / total('_war_darko');
  for (const p of draft) {
    p.war_vorp = round(p._war_vorp, 2);
    p.war_ws = round(p._war_ws * kWs, 2);
    p.war_darko = round(p._war_darko * kDarko, 2);
    p.war_blended = round((p.war_vorp + p.war_ws + p.war_darko) / 3, 2);
    p.war_spread = round(
      Math.max(p.war_vorp, p.war_ws, p.war_darko) - Math.min(p.war_vorp, p.war_ws, p.war_darko),
      2
    );
  }
}

// --------------------------------------------------------------------------
// Composite score: percentile-rank each component, weight, re-percentile to
// 0-100 (ADR-005 weights, verbatim from the README table).
// --------------------------------------------------------------------------
const COMPOSITE_WEIGHTS = {
  war_blended: 0.4,
  darko_dpm: 0.25,
  ws_per_48: 0.15,
  ts_residual: 0.1,
  availability: 0.1
};

function percentileRanks(values) {
  const order = values.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
  const out = new Array(values.length);
  const n = values.length;
  order.forEach(([, i], rank) => {
    out[i] = n === 1 ? 100 : (rank / (n - 1)) * 100;
  });
  return out;
}

const pr = {
  war_blended: percentileRanks(draft.map((p) => p.war_blended)),
  darko_dpm: percentileRanks(draft.map((p) => p.darko_dpm)),
  ws_per_48: percentileRanks(draft.map((p) => p.ws_per_48)),
  ts_residual: percentileRanks(draft.map((p) => p.ts_residual)),
  availability: percentileRanks(draft.map((p) => p.games * p.mpg))
};

const blend = draft.map((_, i) =>
  Object.entries(COMPOSITE_WEIGHTS).reduce((acc, [k, w]) => acc + w * pr[k][i], 0)
);
const composite = percentileRanks(blend);
draft.forEach((p, i) => {
  p.composite_score = round(composite[i], 1);
});

// --------------------------------------------------------------------------
// Contracts. Salary correlates with production, but the CBA distorts it:
// rookie-scale and minimum deals are capped regardless of output, which is
// exactly where the interesting residuals live.
// --------------------------------------------------------------------------
const MARKET_PRICED = new Set(['free_agent', 'mle', 'max', 'extension']);
const MAX_TIERS = [0.25, 0.3, 0.35]; // cap share by years of service

function assignContractType(p) {
  const yearsExp = clamp(p.age - 19 - Math.floor(rand() * 2), 0, 20);
  if (yearsExp <= 3 && rand() < 0.82) return { type: 'rookie_scale', yearsExp };
  if (rand() < 0.035) return { type: 'two_way', yearsExp };
  if (p.composite_score > 92 && rand() < 0.62) return { type: 'max', yearsExp };
  if (p.composite_score > 74 && rand() < 0.42) return { type: 'extension', yearsExp };
  if (p.composite_score < 34 && rand() < 0.58) return { type: 'minimum', yearsExp };
  if (rand() < 0.3) return { type: 'mle', yearsExp };
  return { type: 'free_agent', yearsExp };
}

const MINIMUM_BY_EXP = (exp) =>
  [1_310_000, 2_110_000, 2_360_000, 2_450_000, 2_540_000, 2_740_000, 2_960_000, 3_180_000][
    Math.min(exp, 7)
  ];

for (const p of draft) {
  const { type, yearsExp } = assignContractType(p);
  p.contract_type = type;
  p.years_exp = yearsExp;

  let salary;
  switch (type) {
    case 'rookie_scale': {
      // Pick order proxy: better players were drafted higher, loosely.
      const slot = clamp(Math.round(31 - p.composite_score * 0.26 + gauss() * 8), 1, 60);
      const base = slot <= 30 ? 10_900_000 * Math.exp(-0.072 * (slot - 1)) : 1_900_000;
      salary = base * (1 + 0.26 * Math.min(yearsExp, 3)) * (0.96 + rand() * 0.09);
      break;
    }
    case 'two_way':
      salary = 620_000 * (0.98 + rand() * 0.05);
      break;
    case 'minimum':
      salary = MINIMUM_BY_EXP(yearsExp) * (0.99 + rand() * 0.03);
      break;
    case 'mle':
      salary = 13_400_000 * (0.55 + rand() * 0.62);
      break;
    case 'max': {
      const tier = yearsExp >= 10 ? 2 : yearsExp >= 7 ? 1 : 0;
      salary = SALARY_CAP * MAX_TIERS[tier] * (0.93 + rand() * 0.13);
      break;
    }
    case 'extension':
    case 'free_agent':
    default: {
      // Market deals: paid for past production with heavy noise — the noise is
      // what makes the residuals (and the outliers) real.
      // Diminishing returns at the top: the CBA caps individual salaries, so
      // market pay is concave in production rather than proportional to it.
      const fair = Math.max(1.6e6, 0.62 * Math.max(0, p.war_blended) ** 1.28 * DPW_REPLACEMENT);
      const noise = Math.exp(gauss() * 0.42);
      salary = clamp(fair * noise * (0.82 + rand() * 0.5), 2_100_000, 0.345 * SALARY_CAP);
      break;
    }
  }

  // A handful of genuine, deliberate outliers in both directions: an
  // albatross deal for a declining veteran, a superstar on a bargain.
  if (rand() < 0.035 && type !== 'rookie_scale' && type !== 'minimum') {
    salary *= p.age > 31 ? 1.9 + rand() * 0.9 : 0.42 + rand() * 0.2;
  }

  p.salary = Math.round(salary / 1000) * 1000;
  p.cap_pct = round((p.salary / SALARY_CAP) * 100, 3);
  p.is_market_priced = MARKET_PRICED.has(type);

  p.years_remaining =
    type === 'two_way' ? 1 : clamp(1 + Math.floor(rand() * (type === 'max' ? 5 : 4)), 1, 5);
  const guaranteedShare = type === 'minimum' || type === 'two_way' ? rand() * 0.7 : 0.72 + rand() * 0.28;
  p.guaranteed_remaining = Math.round((p.salary * p.years_remaining * guaranteedShare) / 1000) * 1000;
  p.no_trade_clause = type === 'max' && p.years_exp >= 8 && rand() < 0.1;

  // ADR-011: simple flags only.
  if (p.no_trade_clause) {
    p.trade_eligible = false;
    p.trade_restriction_reason = 'no_trade_clause';
  } else if (type === 'two_way') {
    p.trade_eligible = false;
    p.trade_restriction_reason = 'two_way_contract';
  } else if (rand() < 0.07) {
    p.trade_eligible = false;
    p.trade_restriction_reason = 'recently_signed';
  } else if (rand() < 0.03) {
    p.trade_eligible = false;
    p.trade_restriction_reason = 'poison_pill_provision';
  } else {
    p.trade_eligible = true;
    p.trade_restriction_reason = '';
  }
}

// --------------------------------------------------------------------------
// Valuation. Both denominators precomputed (ADR-006) so the UI toggle is a
// field lookup, never a rebuild.
// --------------------------------------------------------------------------
for (const p of draft) {
  p.expected_salary_naive = Math.round(Math.max(ROOKIE_MIN, p.war_blended * DPW_NAIVE));
  p.expected_salary_replacement = Math.round(
    Math.max(ROOKIE_MIN, p.war_blended * DPW_REPLACEMENT)
  );
  p.surplus_naive = Math.round(p.expected_salary_naive - p.salary);
  p.surplus_replacement = Math.round(p.expected_salary_replacement - p.salary);
}

// --------------------------------------------------------------------------
// Regression: the league-average price of a unit of output.
//
// For each $/win denominator, OLS of expected_salary (as a % of the cap) on
// composite_score, fit over MARKET-PRICED contracts only — free-agent, MLE,
// max and extension deals. Rookie-scale and minimum contracts are
// CBA-suppressed rather than market-priced and would drag the line down
// (README "Regression population"); they are still plotted, they just do not
// get a vote in defining market price.
//
//     expected cap_pct = intercept + slope * composite_score
//
// Because expected salary carries the denominator, the line MOVES when the
// toggle flips — which is the whole point of ADR-006 being a user-facing
// control rather than a hidden constant.
// --------------------------------------------------------------------------
function ols(points) {
  const n = points.length;
  const mx = points.reduce((a, [x]) => a + x, 0) / n;
  const my = points.reduce((a, [, y]) => a + y, 0) / n;
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (const [x, y] of points) {
    sxy += (x - mx) * (y - my);
    sxx += (x - mx) ** 2;
    syy += (y - my) ** 2;
  }
  const slope = sxy / sxx;
  const intercept = my - slope * mx;
  const r2 = (sxy * sxy) / (sxx * syy);
  return { slope, intercept, r2, n };
}

const marketPriced = draft.filter((p) => p.is_market_priced);

const regressionFor = (key) => {
  const fit = ols(marketPriced.map((p) => [p.composite_score, (p[key] / SALARY_CAP) * 100]));
  return {
    slope: round(fit.slope, 6),
    intercept: round(fit.intercept, 6),
    r2: round(fit.r2, 4),
    n: fit.n
  };
};

// --------------------------------------------------------------------------
// Emit
// --------------------------------------------------------------------------
const players = draft.map((p) => ({
  bbref_slug: slugFor(p.name, seenSlugs),
  nba_player_id: p.nbaId,
  name: p.name,
  headshot_url: `https://cdn.nba.com/headshots/nba/latest/1040x760/${p.nbaId}.png`,
  team: pick(TEAMS),
  position: pick(POSITIONS),
  age: p.age,

  games: p.games,
  minutes: p.minutes,
  ts_pct: p.ts_pct,
  usg_pct: p.usg_pct,
  ws: p.ws,
  ws_per_48: p.ws_per_48,
  vorp: p.vorp,
  bpm: p.bpm,
  darko_dpm: p.darko_dpm,

  war_vorp: p.war_vorp,
  war_ws: p.war_ws,
  war_darko: p.war_darko,
  war_blended: p.war_blended,
  war_spread: p.war_spread,

  ts_residual: p.ts_residual,
  composite_score: p.composite_score,

  salary: p.salary,
  cap_pct: p.cap_pct,
  expected_salary_naive: p.expected_salary_naive,
  expected_salary_replacement: p.expected_salary_replacement,
  surplus_naive: p.surplus_naive,
  surplus_replacement: p.surplus_replacement,

  contract_type: p.contract_type,
  is_market_priced: p.is_market_priced,
  guaranteed_remaining: p.guaranteed_remaining,
  years_remaining: p.years_remaining,
  no_trade_clause: p.no_trade_clause,
  trade_eligible: p.trade_eligible,
  trade_restriction_reason: p.trade_restriction_reason
}));

// Team assignment. A uniform random draw produces absurd payrolls (one team at
// $96M, another at $304M), which would make the team rollup view useless. Give
// each franchise a spend appetite spanning the real 2026-27 range, then fill
// rosters greedily from the top of the salary sheet down.
const byTeam = new Map(TEAMS.map((t) => [t, []]));
const appetites = new Map(
  TEAMS.map((t, i) => [t, 138_000_000 + (70_000_000 * i) / (TEAMS.length - 1)])
);
{
  const order = [...TEAMS];
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  const shuffledAppetites = TEAMS.map((t) => appetites.get(t));
  order.forEach((t, i) => appetites.set(t, shuffledAppetites[i]));

  const spend = new Map(TEAMS.map((t) => [t, 0]));
  const capacity = new Map(TEAMS.map((t, i) => [t, i < players.length % TEAMS.length ? 16 : 15]));
  for (const p of [...players].sort((a, b) => b.salary - a.salary)) {
    let best = null;
    let bestNeed = -Infinity;
    for (const t of TEAMS) {
      if (byTeam.get(t).length >= capacity.get(t)) continue;
      const need = appetites.get(t) - spend.get(t);
      if (need > bestNeed) {
        bestNeed = need;
        best = t;
      }
    }
    p.team = best;
    byTeam.get(best).push(p);
    spend.set(best, spend.get(best) + p.salary);
  }
}

const meta = {
  generated_at: new Date('2026-08-01T07:12:44Z').toISOString(),
  stats_season: '2025-26',
  salary_season: '2026-27',
  salary_cap: SALARY_CAP,
  luxury_tax: LUXURY_TAX,
  first_apron: FIRST_APRON,
  second_apron: SECOND_APRON,
  dollars_per_win: { naive: round(DPW_NAIVE, 2), replacement: round(DPW_REPLACEMENT, 2) },
  regression: {
    naive: regressionFor('expected_salary_naive'),
    replacement: regressionFor('expected_salary_replacement')
  },
  composite_weights: COMPOSITE_WEIGHTS,
  min_minutes: MIN_MINUTES,
  player_count: players.length,
  excluded_count: 118,
  is_mock: true
};

const teams = [...byTeam.entries()]
  .map(([team, list]) => {
    const total_salary = list.reduce((a, p) => a + p.salary, 0);
    const apron_status =
      total_salary >= SECOND_APRON
        ? 'second_apron'
        : total_salary >= FIRST_APRON
          ? 'first_apron'
          : total_salary >= LUXURY_TAX
            ? 'over_tax'
            : total_salary > SALARY_CAP
              ? 'over_cap'
              : 'under_cap';
    return {
      team,
      total_salary,
      total_war: round(list.reduce((a, p) => a + p.war_blended, 0), 2),
      total_surplus_naive: list.reduce((a, p) => a + p.surplus_naive, 0),
      total_surplus_replacement: list.reduce((a, p) => a + p.surplus_replacement, 0),
      player_count: list.length,
      cap_space: SALARY_CAP - total_salary,
      apron_status
    };
  })
  .sort((a, b) => a.team.localeCompare(b.team));

mkdirSync(OUT_DIR, { recursive: true });
const write = (file, obj) => {
  writeFileSync(join(OUT_DIR, file), `${JSON.stringify(obj, null, 0)}\n`);
  return obj;
};
write('players.json', players);
write('meta.json', meta);
write('teams.json', teams);

// --------------------------------------------------------------------------
// Sanity report — a mock that isn't plausible is worse than no mock.
// --------------------------------------------------------------------------
const salaries = players.map((p) => p.salary).sort((a, b) => a - b);
const q = (f) => salaries[Math.floor(f * (salaries.length - 1))];
const corr = (() => {
  const xs = players.map((p) => p.composite_score);
  const ys = players.map((p) => p.cap_pct);
  const mx = xs.reduce((a, b) => a + b) / xs.length;
  const my = ys.reduce((a, b) => a + b) / ys.length;
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  xs.forEach((x, i) => {
    sxy += (x - mx) * (ys[i] - my);
    sxx += (x - mx) ** 2;
    syy += (ys[i] - my) ** 2;
  });
  return sxy / Math.sqrt(sxx * syy);
})();
const fmt$ = (v) => `$${(v / 1e6).toFixed(1)}M`;

console.log(`wrote ${players.length} players, ${teams.length} teams -> ${OUT_DIR}`);
console.log(`  salary p10/p50/p90/max  ${fmt$(q(0.1))} / ${fmt$(q(0.5))} / ${fmt$(q(0.9))} / ${fmt$(q(1))}`);
console.log(`  mean/median ratio       ${(salaries.reduce((a, b) => a + b) / salaries.length / q(0.5)).toFixed(2)}  (>1 = right-skewed)`);
console.log(`  corr(composite, cap%)   ${corr.toFixed(3)}`);
console.log(`  composite range         ${Math.min(...players.map((p) => p.composite_score))} .. ${Math.max(...players.map((p) => p.composite_score))}`);
console.log(`  regression (replacement) cap% = ${meta.regression.replacement.intercept.toFixed(3)} + ${meta.regression.replacement.slope.toFixed(4)}·composite  r2=${meta.regression.replacement.r2}  n=${meta.regression.replacement.n}`);
console.log(`  regression (naive)       cap% = ${meta.regression.naive.intercept.toFixed(3)} + ${meta.regression.naive.slope.toFixed(4)}·composite`);
const cts = {};
players.forEach((p) => (cts[p.contract_type] = (cts[p.contract_type] || 0) + 1));
console.log('  contract types          ', cts);
