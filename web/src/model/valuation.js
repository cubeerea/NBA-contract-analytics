/**
 * Client-side model layer.
 *
 * "Precompute heavy, recompute light" - the ETL emits both $/win variants on
 * every record, so switching the denominator (ADR-006) is a field lookup here,
 * not a refetch and not a rebuild. Nothing in this file does arithmetic that
 * the ETL could have done once.
 */

import { MODES, CONTRACT_TYPES } from './constants.js';
import { matchesQuery } from './search.js';

/** Resolve the mode-dependent fields for one player. */
export function valuationOf(player, modeId) {
  const mode = MODES[modeId] ?? MODES.replacement;
  return {
    surplus: player[mode.surplusKey],
    expected: player[mode.expectedKey]
  };
}

export const surplusOf = (player, modeId) => player[(MODES[modeId] ?? MODES.replacement).surplusKey];
export const expectedOf = (player, modeId) => player[(MODES[modeId] ?? MODES.replacement).expectedKey];
export const teamSurplusOf = (team, modeId) =>
  team[(MODES[modeId] ?? MODES.replacement).teamSurplusKey];

/**
 * The reference lines and residuals moved to `model/denominator.js` (ADR-015).
 * They are no longer a pure function of `meta` alone: on the team-payroll axis
 * the server's league-cap fits are on the wrong scale entirely and have to be
 * refit client-side, so the fit and the accessor that produced it must travel
 * together. Nothing here should reconstruct them.
 */

/* ---------------------------------------------------------------------- */
/* Filters                                                                 */
/* ---------------------------------------------------------------------- */

export const EMPTY_FILTERS = {
  positions: [],
  contractTypes: [],
  teams: [],
  ageRange: null, // [min, max] or null for "all"
  search: ''
};

export function filtersAreActive(filters, bounds) {
  if (!filters) return false;
  if (filters.positions.length || filters.contractTypes.length || filters.teams.length) return true;
  if (filters.search.trim()) return true;
  if (filters.ageRange && bounds) {
    return filters.ageRange[0] > bounds.age[0] || filters.ageRange[1] < bounds.age[1];
  }
  return false;
}

/**
 * All predicates compose - a player must satisfy every active facet. Returns a
 * Set of slugs rather than a filtered array, because the chart needs to keep
 * rendering every player and merely de-emphasise the non-matches.
 */
export function matchingSlugs(players, filters) {
  const positions = new Set(filters.positions);
  const contracts = new Set(filters.contractTypes);
  const teams = new Set(filters.teams);
  const [minAge, maxAge] = filters.ageRange ?? [-Infinity, Infinity];
  const q = filters.search;

  const out = new Set();
  for (const p of players) {
    if (positions.size && !positions.has(p.position)) continue;
    if (contracts.size && !contracts.has(p.contract_type)) continue;
    if (teams.size && !teams.has(p.team)) continue;
    if (p.age < minAge || p.age > maxAge) continue;
    // Diacritic-folded on both sides, so "Doncic" finds Dončić.
    if (!matchesQuery(q, p.name, p.team)) continue;
    out.add(p.bbref_slug);
  }
  return out;
}

/* ---------------------------------------------------------------------- */
/* Dataset-derived bounds and scales                                       */
/* ---------------------------------------------------------------------- */

export function boundsOf(players) {
  if (!players?.length) {
    return { age: [19, 41], capPct: [0, 40], composite: [0, 100] };
  }
  let minAge = Infinity;
  let maxAge = -Infinity;
  let maxCap = 0;
  for (const p of players) {
    if (p.age < minAge) minAge = p.age;
    if (p.age > maxAge) maxAge = p.age;
    if (p.cap_pct > maxCap) maxCap = p.cap_pct;
  }
  return {
    age: [minAge, maxAge],
    capPct: [0, Math.ceil((maxCap + 1) / 5) * 5],
    composite: [0, 100]
  };
}

export function teamsInData(players) {
  return [...new Set(players.map((p) => p.team))].sort();
}

/** Contract types actually present, in the canonical display order. */
export function contractTypesInData(players) {
  const present = new Set(players.map((p) => p.contract_type));
  return Object.keys(CONTRACT_TYPES).filter((k) => present.has(k));
}

/* ---------------------------------------------------------------------- */
/* Aggregation                                                             */
/* ---------------------------------------------------------------------- */

/**
 * Roll players up by team. Used by the team view so that the player filters
 * compose there too; `teams.json` still supplies the authoritative payroll,
 * cap space and apron state, which no client-side filter can change.
 */
export function rollupByTeam(players, modeId) {
  const acc = new Map();
  for (const p of players) {
    let row = acc.get(p.team);
    if (!row) {
      row = { team: p.team, player_count: 0, salary: 0, war: 0, surplus: 0, best: null, worst: null };
      acc.set(p.team, row);
    }
    const surplus = surplusOf(p, modeId);
    row.player_count += 1;
    row.salary += p.salary;
    row.war += p.war_blended;
    row.surplus += surplus;
    if (!row.best || surplus > surplusOf(row.best, modeId)) row.best = p;
    if (!row.worst || surplus < surplusOf(row.worst, modeId)) row.worst = p;
  }
  for (const row of acc.values()) {
    row.war = Math.round(row.war * 100) / 100;
    row.surplusPerDollar = row.salary > 0 ? row.surplus / row.salary : 0;
    row.warPer10m = row.salary > 0 ? (row.war / row.salary) * 1e7 : 0;
  }
  return acc;
}

/** Median nearest-neighbour spacing, in data units - drives the LOD threshold. */
export function medianNeighbourDistance(points) {
  if (points.length < 2) return 1;
  const n = points.length;
  const sample = Math.min(n, 220);
  const step = Math.max(1, Math.floor(n / sample));
  const dists = [];
  for (let i = 0; i < n; i += step) {
    let best = Infinity;
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const dx = points[i][0] - points[j][0];
      const dy = points[i][1] - points[j][1];
      const d = dx * dx + dy * dy;
      if (d < best) best = d;
    }
    if (Number.isFinite(best)) dists.push(Math.sqrt(best));
  }
  if (!dists.length) return 1;
  dists.sort((a, b) => a - b);
  return dists[Math.floor(dists.length / 2)] || 1;
}
