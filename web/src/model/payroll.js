/**
 * Team payroll context.
 *
 * The cap is the league's limit, not a ceiling anyone is actually held to:
 * Cleveland's books run to $226.0M against a $164,961,000 cap while Brooklyn
 * sits at $150.8M. An identical contract is survivable under the cap and
 * punitive above the second apron, so "what is this team's payroll, and which
 * spending line is it past" is a first-class fact about every contract rather
 * than a detail of the team view.
 *
 * The vocabulary here is single-sourced: `APRON` is the ordered ladder the
 * team table already renders as a four-notch meter, and every surface that
 * needs the same fact imports it from here rather than restating it.
 */

import { money } from './format.js';

/**
 * Apron state is an ORDERED ladder, not a set of categories: every rung above
 * the one below it costs the front office another tool. `step` drives the
 * four-notch meter so the ordering is visible without reading the label, and
 * without borrowing the blue/red channel that means surplus everywhere else.
 */
export const APRON = {
  under_cap: {
    key: 'under_cap',
    label: 'Under cap',
    step: 0,
    note: 'Room to sign outside players'
  },
  over_cap: {
    key: 'over_cap',
    label: 'Over cap',
    step: 1,
    note: 'Exceptions only'
  },
  over_tax: {
    key: 'over_tax',
    label: 'Over tax',
    step: 2,
    note: 'Paying luxury tax on every dollar above the line'
  },
  first_apron: {
    key: 'first_apron',
    label: 'First apron',
    step: 3,
    note: 'No sign-and-trade, no buyout market, no mid-level'
  },
  second_apron: {
    key: 'second_apron',
    label: 'Second apron',
    step: 4,
    note: 'Frozen picks, no salary aggregation, repeater tax on every dollar'
  }
};

export const APRON_STEPS = 4;

/**
 * The tax and apron lines are NOT published in meta.json — the ETL keeps them
 * in `etl/config.py` and only `salary_cap` crosses into the artifact. Copying
 * three dollar literals into the client would go stale the moment the cap
 * moves, which the project's own rule against cap-pegged literals forbids, so
 * they are expressed as multiples of the live cap instead. Rounded to the
 * nearest $1,000 against the 2026-27 cap these reproduce the published levels
 * exactly: $200,428,000 tax, $209,015,000 first apron, $221,686,000 second.
 *
 * If a future meta.json ever publishes the levels outright, those win.
 */
const LEVEL_OF_CAP = { tax: 1.215002, firstApron: 1.267057, secondApron: 1.343869 };

/** Last-resort cap, used only if meta.json failed to load its own figure. */
const FALLBACK_CAP = 164961000;

const firstNumber = (...values) => {
  for (const v of values) if (typeof v === 'number' && Number.isFinite(v) && v > 0) return v;
  return null;
};

/**
 * The four spending lines, derived from whatever meta.json actually exposes.
 * `derived` records whether the levels above the cap had to be computed, so a
 * surface can caveat them if it wants to.
 */
export function capLadder(meta) {
  const levels = meta?.cap_levels ?? {};
  const cap = firstNumber(meta?.salary_cap, levels.salary_cap, FALLBACK_CAP);
  const published = {
    tax: firstNumber(meta?.luxury_tax, meta?.tax_level, levels.luxury_tax, levels.tax),
    firstApron: firstNumber(meta?.first_apron, levels.first_apron),
    secondApron: firstNumber(meta?.second_apron, levels.second_apron)
  };
  const derive = (ratio) => Math.round((cap * ratio) / 1000) * 1000;
  return {
    cap,
    tax: published.tax ?? derive(LEVEL_OF_CAP.tax),
    firstApron: published.firstApron ?? derive(LEVEL_OF_CAP.firstApron),
    secondApron: published.secondApron ?? derive(LEVEL_OF_CAP.secondApron),
    derived: !(published.tax && published.firstApron && published.secondApron)
  };
}

/** The ladder as an ascending, labelled list. */
export const ladderLines = (ladder) => [
  { key: 'cap', label: 'salary cap', amount: ladder.cap },
  { key: 'tax', label: 'luxury tax', amount: ladder.tax },
  { key: 'firstApron', label: 'first apron', amount: ladder.firstApron },
  { key: 'secondApron', label: 'second apron', amount: ladder.secondApron }
];

/** Fallback for a rollup row that has no `apron_status` of its own. */
export function apronStatusFor(payroll, ladder) {
  if (!(payroll > 0)) return null;
  if (payroll >= ladder.secondApron) return 'second_apron';
  if (payroll >= ladder.firstApron) return 'first_apron';
  if (payroll >= ladder.tax) return 'over_tax';
  if (payroll >= ladder.cap) return 'over_cap';
  return 'under_cap';
}

export const payrollIndex = (teams) => new Map((teams ?? []).map((t) => [t.team, t]));

/**
 * One sentence placing a payroll on the ladder. Always says how far PAST the
 * line it has already cleared and how much room is left before the next one,
 * because both halves are what make a contract cheap or ruinous.
 */
function summarize(payroll, above, next) {
  if (!above) {
    return next ? `${money(next.amount - payroll)} of room under the ${next.label}` : null;
  }
  const over = `${money(payroll - above.amount)} past the ${above.label}`;
  if (!next) return over;
  return `${over}, ${money(next.amount - payroll)} short of the ${next.label}`;
}

/**
 * Everything a player-level surface needs to say about the team paying him.
 * Returns null when teams.json has no row for the player's team, so callers
 * can omit the block rather than print a hole.
 */
export function payrollContextOf(player, byTeam, ladder) {
  if (!player) return null;
  const row = byTeam?.get?.(player.team) ?? null;
  const payroll = row?.total_salary ?? null;
  if (!(payroll > 0)) return null;

  const status = row.apron_status ?? apronStatusFor(payroll, ladder);
  const lines = ladderLines(ladder);
  const above = [...lines].reverse().find((l) => payroll >= l.amount) ?? null;
  const next = lines.find((l) => payroll < l.amount) ?? null;

  return {
    team: player.team,
    payroll,
    rosterCount: row.roster_count ?? null,
    capSpace: row.cap_space ?? ladder.cap - payroll,
    status,
    apron: APRON[status] ?? null,
    /** The player's own contract as a share of that payroll. */
    share: player.salary / payroll,
    /** The payroll as a multiple of the cap — 1.37 for Cleveland. */
    vsCap: payroll / ladder.cap,
    above,
    next,
    headroom: next ? next.amount - payroll : null,
    summary: summarize(payroll, above, next)
  };
}
