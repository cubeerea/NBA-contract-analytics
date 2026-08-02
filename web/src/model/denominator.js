/**
 * The Y-axis denominator (ADR-015).
 *
 * A salary is only a number; a salary as a SHARE needs a denominator, and there
 * are two defensible ones:
 *
 *   league cap    every contract against the same $164,961,000 ruler
 *   team payroll  every contract against the payroll it actually sits inside
 *
 * The league cap is the default because the chart's load-bearing element is a
 * single league-wide regression line, and a regression only means anything when
 * every point is measured against the same ruler. Team payroll is offered as a
 * toggle because it answers a real question the cap cannot — but it inverts
 * comparisons: Curry ($62.6M) is paid $4.1M MORE than Anthony Davis ($58.5M)
 * yet plots as the cheaper contract on the team axis (29.7% of Golden State's
 * payroll vs 31.3% of Washington's) purely because Golden State spent more on
 * everyone else.
 *
 * THE HONESTY PROBLEM. Both reference lines are fit server-side against
 * `cap_pct`, and so is `cap_pct_residual` on every record. On a team-relative
 * axis they are on a different scale entirely and leaving them there would be
 * a lie drawn in ink. So:
 *
 *   cap mode   the server fit is the source of truth; nothing is recomputed.
 *   team mode  the MARKET line is refit here by plain OLS over market-priced
 *              contracts on the active axis, and the MODEL line becomes a BAND,
 *              because it has to. Model value is one dollar figure per
 *              production score; divided by 30 different payrolls it is 30
 *              different shares. The band spans the league's payroll range and
 *              its centre line is the median-payroll team. That is not a
 *              cosmetic choice — the spread of the band IS the reader's point
 *              that teams do not all live under the same ceiling.
 */

import { money } from './format.js';
import { capLadder, payrollContextOf, payrollIndex } from './payroll.js';

export const DENOM_ORDER = ['cap', 'team'];

export const DENOMINATORS = {
  cap: {
    id: 'cap',
    label: 'Share of the league cap',
    short: 'League cap',
    shareLabel: 'Cap share',
    shareNote: 'Of the league salary cap',
    axisUnit: 'share of the cap',
    blurb:
      'One ruler for all 30 teams. The regression lines are league-wide, so they only mean anything measured against a shared denominator.'
  },
  team: {
    id: 'team',
    label: 'Share of team payroll',
    short: 'Team payroll',
    shareLabel: 'Payroll share',
    shareNote: "Of this team's own payroll",
    axisUnit: "share of the team's own payroll",
    blurb:
      'Each contract against the payroll it actually sits inside. Beware: a player reads cheaper simply because his front office spent more on everyone else.'
  }
};

/** Plain ordinary least squares over [x, y] pairs. */
function ols(points) {
  const n = points.length;
  if (n < 3) return null;
  let sx = 0;
  let sy = 0;
  for (const [x, y] of points) {
    sx += x;
    sy += y;
  }
  const mx = sx / n;
  const my = sy / n;
  let sxy = 0;
  let sxx = 0;
  for (const [x, y] of points) {
    sxy += (x - mx) * (y - my);
    sxx += (x - mx) * (x - mx);
  }
  if (!(sxx > 0)) return null;
  const slope = sxy / sxx;
  const intercept = my - slope * mx;
  let ssRes = 0;
  let ssTot = 0;
  for (const [x, y] of points) {
    const e = y - (intercept + slope * x);
    ssRes += e * e;
    ssTot += (y - my) * (y - my);
  }
  return { slope, intercept, r2: ssTot > 0 ? 1 - ssRes / ssTot : 0, n };
}

const median = (sorted) =>
  sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;

/** A cap-fraction line rescaled onto a single team's payroll. */
const rescale = (line, cap, payroll) => ({
  slope: (line.slope * cap) / payroll,
  intercept: (line.intercept * cap) / payroll,
  payroll
});

/**
 * Build the active denominator. Everything a surface needs to express a salary
 * as a share — the accessor, the labels, the two reference lines, the residual
 * and the team payroll context — hangs off this one object, so views take a
 * single `denominator` prop rather than five loose ones.
 */
export function makeDenominator({ denomId, modeId, meta, players = [], teams = [] }) {
  const def = DENOMINATORS[denomId] ?? DENOMINATORS.cap;
  const isTeam = def.id === 'team';
  const ladder = capLadder(meta);
  const cap = ladder.cap;
  const byTeam = payrollIndex(teams);

  const payrollOf = (player) => {
    if (!isTeam) return cap;
    const p = byTeam.get(player?.team)?.total_salary;
    return p > 0 ? p : cap;
  };

  /** Always a fraction, never a percentage — exactly like `cap_pct`. */
  const shareOf = (player) => {
    if (!player) return 0;
    if (!isTeam) return player.cap_pct;
    const payroll = payrollOf(player);
    return payroll > 0 ? player.salary / payroll : player.cap_pct;
  };

  const serverMarket = meta?.regression?.[modeId] ?? meta?.regression?.replacement ?? null;
  const serverModel = meta?.model_line?.[modeId] ?? meta?.model_line?.replacement ?? null;

  /* ---- market line -------------------------------------------------- */
  let market = null;
  if (!isTeam) {
    // The server fit is already correct for this axis. Do not recompute what
    // the ETL got right; recomputing would only introduce drift.
    market = serverMarket ? { ...serverMarket, source: 'server' } : null;
  } else {
    const pts = [];
    for (const p of players) {
      if (!p.is_market_priced) continue;
      const y = shareOf(p);
      if (Number.isFinite(y) && Number.isFinite(p.composite_score)) pts.push([p.composite_score, y]);
    }
    const fit = ols(pts);
    market = fit ? { ...fit, source: 'client' } : null;
  }

  /* ---- model line, or model band ------------------------------------ */
  let model = null;
  if (serverModel && !isTeam) {
    model = { kind: 'line', ...serverModel, mid: serverModel, source: 'server' };
  } else if (serverModel && isTeam) {
    const payrolls = [];
    const seen = new Set();
    for (const p of players) {
      if (seen.has(p.team)) continue;
      seen.add(p.team);
      const v = byTeam.get(p.team)?.total_salary;
      if (v > 0) payrolls.push({ team: p.team, payroll: v });
    }
    payrolls.sort((a, b) => a.payroll - b.payroll);
    if (payrolls.length) {
      const mid = median(payrolls.map((r) => r.payroll));
      const leanest = payrolls[0];
      const richest = payrolls[payrolls.length - 1];
      model = {
        kind: 'band',
        source: 'client',
        // A smaller payroll makes the same dollar figure a BIGGER share, so
        // the top edge of the band belongs to the leanest team.
        hi: rescale(serverModel, cap, leanest.payroll),
        lo: rescale(serverModel, cap, richest.payroll),
        mid: rescale(serverModel, cap, mid),
        leanest,
        richest,
        medianPayroll: mid,
        r2: serverModel.r2,
        n: serverModel.n
      };
    }
  }

  const predictMarket = market ? (x) => market.intercept + market.slope * x : null;

  return {
    ...def,
    isTeam,
    ladder,
    cap,
    byTeam,

    shareOf,
    payrollOf,
    /** Team payroll context for one player, or null if teams.json lacks the row. */
    contextOf: (player) => payrollContextOf(player, byTeam, ladder),

    /** Dual-unit axis ticks only mean something under a SHARED denominator. */
    dollarsAt: isTeam ? null : (axisPct) => money((axisPct / 100) * cap),

    axisTitle: meta?.salary_season ? `${meta.salary_season} salary` : 'Salary',
    axisUnit: isTeam ? def.axisUnit : `share of the ${money(cap)} cap`,

    fits: { market, model },
    /**
     * Signed vertical distance from the MARKET line, on the ACTIVE axis. The
     * precomputed `cap_pct_residual` is league-relative and would be wrong here.
     */
    residualOf: (player) => (predictMarket ? shareOf(player) - predictMarket(player.composite_score) : 0),

    /** Label for the model reference, which is a band rather than a line here. */
    modelLabel: model?.kind === 'band' ? 'Model value band' : 'Model value',
    /** A few words the chart can print about what happened to the fits. */
    fitNote: isTeam
      ? 'Both references refit on team payroll. The model becomes a band because one dollar figure is a different share on every roster.'
      : null
  };
}
