import { useMemo, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import ApronMeter from '../components/ApronMeter.jsx';
import ContractChip from '../components/ContractChip.jsx';
import Headshot from '../components/Headshot.jsx';
import { EmptyScreen } from '../components/StateScreens.jsx';
import { money, num, pct } from '../model/format.js';
import { APRON } from '../model/payroll.js';
import { rollupByTeam, surplusOf } from '../model/valuation.js';

const SORTS = {
  surplus: { label: 'Rotation surplus', get: (r) => r.surplus },
  perDollar: { label: 'Surplus per $1 paid', get: (r) => r.surplusPerDollar },
  war: { label: 'Rotation WAR', get: (r) => r.war },
  warPer10m: { label: 'WAR per $10M', get: (r) => r.warPer10m },
  payroll: { label: 'Full payroll', get: (r) => r.payroll },
  capSpace: { label: 'Cap space', get: (r) => r.cap_space ?? -Infinity }
};

/**
 * Which front offices extract the most value per dollar (ADR-012).
 *
 * Two populations meet in this view and they are deliberately kept apart on
 * screen. Payroll, roster size, cap space and apron state describe the FULL
 * roster straight from teams.json. Surplus and WAR are recomputed from the
 * rotation players in the current filter selection - a smaller group that
 * cleared the minutes floor. The header band and the vertical rule between
 * the two column groups are what stop the reader conflating them.
 */
export default function TeamsView({ players, teams, mode, denominator, matching, onPin }) {
  const [sortKey, setSortKey] = useState('surplus');
  const [expanded, setExpanded] = useState(null);
  const reduce = useReducedMotion();

  const pool = useMemo(
    () => players.filter((p) => !matching || matching.has(p.bbref_slug)),
    [players, matching]
  );

  const rows = useMemo(() => {
    const rollup = rollupByTeam(pool, mode.id);
    const byTeam = new Map(teams.map((t) => [t.team, t]));
    const keys = new Set([...rollup.keys(), ...byTeam.keys()]);
    return [...keys]
      .map((team) => {
        const r = rollup.get(team) ?? {
          team,
          player_count: 0,
          salary: 0,
          war: 0,
          surplus: 0,
          surplusPerDollar: 0,
          warPer10m: 0,
          best: null,
          worst: null
        };
        const official = byTeam.get(team);
        return {
          ...r,
          payroll: official?.total_salary ?? r.salary,
          rotation_salary: official?.rotation_salary ?? r.salary,
          cap_space: official?.cap_space,
          apron_status: official?.apron_status,
          roster_count: official?.roster_count ?? null
        };
      })
      .sort((a, b) => SORTS[sortKey].get(b) - SORTS[sortKey].get(a));
  }, [pool, teams, mode.id, sortKey]);

  if (!rows.length) {
    return <EmptyScreen title="No team data" body="teams.json is empty or failed to parse." />;
  }

  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.surplus)));
  const maxPayroll = Math.max(1, ...rows.map((r) => r.payroll));

  return (
    <div className="teams-view">
      <div className="view-head">
        <label className="field inline">
          <span className="field-label">Rank by</span>
          <select value={sortKey} onChange={(e) => setSortKey(e.target.value)}>
            {Object.entries(SORTS).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <p className="teams-legend">
          <span className="teams-legend-item">
            <span className="teams-legend-key">Roster</span>
            <span className="teams-legend-body">every contract on the books</span>
          </span>
          <span className="teams-legend-item">
            <span className="teams-legend-key">Rotation</span>
            <span className="teams-legend-body">players past the minutes floor, after filters</span>
          </span>
        </p>
      </div>

      <section className="team-table">
        <header className="team-head" aria-hidden="true">
          <span className="team-head-group th-roster">Full roster</span>
          <span className="team-head-group th-rotation">Rotation players</span>
          <span className="th th-rank">#</span>
          <span className="th th-team">Team</span>
          <span className="th th-payroll">Payroll</span>
          <span className="th th-roster-n">Size</span>
          <span className="th th-apron">Apron</span>
          <span className="th th-surplus">Surplus</span>
          <span className="th th-bar" />
          <span className="th th-war">WAR</span>
          <span className="th th-count">Players</span>
          <span className="th th-disc" />
        </header>

        <div className="team-rows">
          {rows.map((r, i) => {
            const isOpen = expanded === r.team;
            const apron = APRON[r.apron_status];
            // The player-level helper works off a player record; the team row
            // already has the payroll, so a one-field stand-in is enough.
            const ladder = denominator?.contextOf?.({ team: r.team, salary: 0 }) ?? null;
            return (
              <motion.div
                key={r.team}
                layout={reduce ? false : 'position'}
                transition={{ duration: reduce ? 0 : 0.42, ease: [0.77, 0, 0.175, 1] }}
                className={isOpen ? 'team-row is-open' : 'team-row'}
              >
                <button
                  type="button"
                  className="team-summary"
                  aria-expanded={isOpen}
                  onClick={() => setExpanded(isOpen ? null : r.team)}
                >
                  <span className="team-rank">{i + 1}</span>
                  <span className="team-abbr">{r.team}</span>

                  <span className="team-payroll">
                    <span className="team-payroll-value">{money(r.payroll, { precision: 0 })}</span>
                    <span className="team-payroll-track" aria-hidden="true">
                      <span
                        className="team-payroll-fill"
                        style={{ width: `${(r.payroll / maxPayroll) * 100}%` }}
                      />
                    </span>
                  </span>
                  <span className="team-roster-n">{r.roster_count ?? '—'}</span>
                  <ApronMeter status={r.apron_status} />

                  <span className={r.surplus >= 0 ? 'team-value is-pos' : 'team-value is-neg'}>
                    {money(r.surplus, { signed: true })}
                  </span>
                  <span className={r.surplus >= 0 ? 'team-bar is-pos' : 'team-bar is-neg'} aria-hidden="true">
                    <span className="team-bar-axis" />
                    <span
                      className="team-bar-fill"
                      style={{
                        width: `${(Math.abs(r.surplus) / maxAbs) * 50}%`,
                        [r.surplus >= 0 ? 'left' : 'right']: '50%'
                      }}
                    />
                  </span>
                  <span className="team-war">{num(r.war, 1)}</span>
                  <span className="team-count">{r.player_count}</span>
                  <Disclosure open={isOpen} />
                </button>

                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.div
                      className="team-detail-wrap"
                      initial={reduce ? false : { height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={reduce ? undefined : { height: 0, opacity: 0 }}
                      transition={{ duration: reduce ? 0 : 0.32, ease: [0.77, 0, 0.175, 1] }}
                      style={{ overflow: 'hidden' }}
                    >
                      <div className="team-detail">
                        <div className="team-facts">
                          <dl className="team-stats">
                            <div className="team-stat">
                              <dt>Cap space</dt>
                              <dd className={r.cap_space > 0 ? 'is-pos' : undefined}>
                                {r.cap_space != null ? money(r.cap_space, { signed: true }) : '—'}
                              </dd>
                              <p>{r.cap_space > 0 ? 'Room under the cap' : 'Committed above the cap'}</p>
                            </div>
                            <div className="team-stat">
                              <dt>Rotation payroll</dt>
                              <dd>{money(r.rotation_salary, { precision: 0 })}</dd>
                              <p>
                                {r.roster_count
                                  ? `${Math.round((r.rotation_salary / r.payroll) * 100)}% of the full payroll`
                                  : 'Share of full payroll'}
                              </p>
                            </div>
                            <div className="team-stat">
                              <dt>Rotation WAR</dt>
                              <dd>{num(r.war, 1)}</dd>
                              <p>
                                {num(r.warPer10m, 2)} wins per $10M paid
                              </p>
                            </div>
                            <div className="team-stat">
                              <dt>Apron state</dt>
                              <dd>{apron?.label ?? '—'}</dd>
                              <p>{apron?.note ?? 'Not reported'}</p>
                            </div>
                            {/* Where the books sit between the four spending
                                lines, in dollars rather than a rung name -
                                headroom is what decides whether a front office
                                can act at all. */}
                            <div className="team-stat">
                              <dt>Against the ladder</dt>
                              <dd>{ladder ? `${num(ladder.vsCap, 2)}× the cap` : '—'}</dd>
                              <p>{ladder?.summary ?? 'No payroll reported'}</p>
                            </div>
                          </dl>
                          <p className="team-caveat">
                            Payroll, size, cap space and apron cover all {r.roster_count ?? '—'} contracts.
                            Surplus and WAR cover the {r.player_count} rotation {r.player_count === 1 ? 'player' : 'players'} listed below.
                          </p>
                        </div>
                        <TeamRoster
                          team={r.team}
                          pool={pool}
                          mode={mode}
                          denominator={denominator}
                          onPin={onPin}
                        />
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function Disclosure({ open }) {
  return (
    <svg
      className="team-disclosure"
      data-open={open ? 'true' : 'false'}
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M2 3.6 L5 6.6 L8 3.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TeamRoster({ team, pool, mode, denominator, onPin }) {
  const roster = useMemo(
    () =>
      pool
        .filter((p) => p.team === team)
        .sort((a, b) => surplusOf(b, mode.id) - surplusOf(a, mode.id)),
    [pool, team, mode.id]
  );

  if (!roster.length) return <p className="roster-empty">No rotation players match the current filters.</p>;

  return (
    <div className="roster">
      <div className="roster-head" aria-hidden="true">
        <span />
        <span>Player</span>
        <span>Contract</span>
        <span className="num">{denominator?.shareLabel ?? 'Cap share'}</span>
        <span className="num">Surplus</span>
      </div>
      <ul className="roster-list">
        {roster.map((p) => {
          const s = surplusOf(p, mode.id);
          const moved = p.stats_team && p.stats_team !== team;
          return (
            <li key={p.bbref_slug}>
              <button type="button" onClick={() => onPin(p.bbref_slug)}>
                <Headshot player={p} size={30} />
                <span className="roster-name">
                  <span className="roster-name-text">{p.name}</span>
                  <span className="roster-name-sub">
                    <span className="roster-pos">{p.position}</span>
                    {moved && <span className="roster-from">from {p.stats_team}</span>}
                  </span>
                </span>
                <ContractChip type={p.contract_type} compact />
                <span className="roster-share">
                  {pct(denominator ? denominator.shareOf(p) : p.cap_pct, 1)}
                </span>
                <span className={s >= 0 ? 'roster-value is-pos' : 'roster-value is-neg'}>
                  {money(s, { signed: true })}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
