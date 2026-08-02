import { Fragment, useMemo, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import ApronMeter from '../components/ApronMeter.jsx';
import ContractChip from '../components/ContractChip.jsx';
import Headshot from '../components/Headshot.jsx';
import WarSpread from '../components/WarSpread.jsx';
import { EmptyScreen } from '../components/StateScreens.jsx';
import { MODES, MODE_ORDER, TRADE_RESTRICTIONS } from '../model/constants.js';
import { money, moneyExact, num, ordinal, pct, signedNum } from '../model/format.js';
import { matchesQuery } from '../model/search.js';
import { expectedOf, surplusOf } from '../model/valuation.js';

const MAX_SLOTS = 3;

/** Metrics shown against a shared league-percentile axis. */
const PROFILE = [
  { key: 'composite_score', label: 'Composite score', fmt: (v) => num(v, 1), digits: 1, raw: true },
  { key: 'war_blended', label: 'Blended WAR', fmt: (v) => num(v, 1), digits: 1 },
  { key: 'darko_dpm', label: 'DARKO DPM', fmt: (v) => num(v, 2), digits: 2 },
  { key: 'ws_per_48', label: 'Win shares per 48', fmt: (v) => num(v, 3), digits: 3 },
  { key: 'ts_residual', label: 'True shooting vs usage', fmt: (v) => signedNum(v * 100, 1), digits: 1 },
  { key: 'minutes', label: 'Minutes played', fmt: (v) => v.toLocaleString(), digits: 0 },
  { key: 'usg_pct', label: 'Usage rate', fmt: (v) => num(v, 1), digits: 1 },
  { key: 'bpm', label: 'Box plus-minus', fmt: (v) => num(v, 1), digits: 1 }
];

const lastName = (name) => name.split(' ').slice(-1)[0];

export default function PlayerView({
  players,
  mode,
  meta,
  denominator,
  selection,
  onSelectionChange
}) {
  const [query, setQuery] = useState('');
  const reduce = useReducedMotion();

  const percentiles = useMemo(() => {
    const table = {};
    for (const { key } of PROFILE) {
      const sorted = players.map((p) => p[key]).sort((a, b) => a - b);
      table[key] = (value) => {
        let lo = 0;
        let hi = sorted.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (sorted[mid] < value) lo = mid + 1;
          else hi = mid;
        }
        return (lo / Math.max(1, sorted.length - 1)) * 100;
      };
    }
    return table;
  }, [players]);

  const selected = useMemo(
    () => selection.map((slug) => players.find((p) => p.bbref_slug === slug)).filter(Boolean),
    [selection, players]
  );

  const results = useMemo(() => {
    if (!query.trim()) return [];
    // Diacritic-folded on both sides, so "Doncic" finds Dončić.
    return players.filter((p) => matchesQuery(query, p.name, p.team)).slice(0, 8);
  }, [query, players]);

  const add = (slug) => {
    if (selection.includes(slug)) return;
    onSelectionChange([...selection, slug].slice(-MAX_SLOTS));
    setQuery('');
  };

  return (
    <div className="player-view">
      <div className="compare-head">
        <div className="compare-search">
          <label className="field">
            <span className="field-label">Add a player</span>
            <input
              type="search"
              value={query}
              placeholder="Search by name or team…"
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          {results.length > 0 && (
            <ul className="search-results" role="listbox">
              {results.map((p) => (
                <li key={p.bbref_slug}>
                  <button type="button" onClick={() => add(p.bbref_slug)}>
                    <Headshot player={p} size={28} />
                    <span className="sr-name">{p.name}</span>
                    <span className="sr-team">{p.team}</span>
                    <span className="sr-salary">{money(p.salary)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="compare-status">
          <span className="compare-slots">
            <strong>{selected.length}</strong> of {MAX_SLOTS} slots filled
          </span>
          {selected.length > 0 && (
            <button type="button" className="link-button" onClick={() => onSelectionChange([])}>
              Clear comparison
            </button>
          )}
        </div>
      </div>

      {selected.length === 0 ? (
        <EmptyScreen
          title="Nothing selected yet"
          body={`Search above to line up as many as ${MAX_SLOTS} contracts side by side.`}
        />
      ) : (
        <CompareMatrix
          selected={selected}
          mode={mode}
          meta={meta}
          denominator={denominator}
          percentiles={percentiles}
          reduce={reduce}
          onRemove={(slug) => onSelectionChange(selection.filter((s) => s !== slug))}
        />
      )}
    </div>
  );
}

/**
 * One grid, one row per fact. The left column names the fact and every player
 * occupies the same row, so the eye compares horizontally instead of holding
 * three separate columns of numbers in memory. Numeric rows carry a shared
 * scale plus an explicit gap to whichever player leads the row.
 */
function CompareMatrix({ selected, mode, meta, denominator, percentiles, reduce, onRemove }) {
  const n = selected.length;
  const names = selected.map((p) => lastName(p.name));
  const other = MODES[MODE_ORDER.find((id) => id !== mode.id)];
  const payrolls = selected.map((p) => denominator?.contextOf?.(p) ?? null);
  const anyPayroll = payrolls.some(Boolean);

  const surpluses = selected.map((p) => surplusOf(p, mode.id));
  const surplusScale = Math.max(1, ...surpluses.map(Math.abs));

  const gap = (values, i, fmt) => {
    if (values.length < 2) return null;
    let top = 0;
    for (let k = 1; k < values.length; k += 1) if (values[k] > values[top]) top = k;
    if (i === top) return 'highest';
    return `${fmt(values[i] - values[top])} vs ${names[top]}`;
  };

  const moneyRow = (label, get, note) => ({ kind: 'money', label, get, note });

  return (
    <section className="cmp" style={{ '--cmp-cols': n }}>
      <div className="cmp-grid">
        <div className="cmp-corner">
          <span className="cmp-corner-key">Comparing</span>
          <span className="cmp-corner-value">{n === 1 ? '1 contract' : `${n} contracts`}</span>
        </div>
        {selected.map((p) => {
            const moved = p.stats_team && p.stats_team !== p.team;
            return (
              <motion.div
                key={p.bbref_slug}
                className="cmp-identity"
                layout={reduce ? false : 'position'}
                initial={reduce ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: reduce ? 0 : 0.32, ease: [0.77, 0, 0.175, 1] }}
              >
                <button
                  type="button"
                  className="cmp-remove"
                  onClick={() => onRemove(p.bbref_slug)}
                  aria-label={`Remove ${p.name} from the comparison`}
                >
                  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" focusable="false">
                    <path
                      d="M2 2 L8 8 M8 2 L2 8"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>
                <Headshot player={p} size={64} />
                <h3>{p.name}</h3>
                <dl className="cmp-facts">
                  <div>
                    <dt>Team</dt>
                    <dd>{p.team}</dd>
                  </div>
                  <div>
                    <dt>Position</dt>
                    <dd>{p.position}</dd>
                  </div>
                  <div>
                    <dt>Age</dt>
                    <dd>{p.age}</dd>
                  </div>
                </dl>
                <ContractChip type={p.contract_type} />
                {moved && (
                  <p className="cmp-moved">
                    <span className="cmp-moved-key">{meta?.stats_season ?? '2025-26'} production</span>
                    <span className="cmp-moved-value">earned with {p.stats_team}</span>
                  </p>
                )}
              </motion.div>
            );
        })}

        <Band label="Valuation" note={`${mode.label} model`} />

        <Fragment key="surplus">
          <RowHead label="Surplus" note="Expected value minus cap hit" />
          {selected.map((p, i) => {
            const s = surpluses[i];
            return (
              <div className="cmp-cell is-lead" key={p.bbref_slug}>
                <span className={s >= 0 ? 'cmp-big is-pos' : 'cmp-big is-neg'}>
                  {money(s, { signed: true })}
                </span>
                <span className={s >= 0 ? 'cmp-scale is-pos' : 'cmp-scale is-neg'} aria-hidden="true">
                  <span className="cmp-scale-axis" />
                  <span
                    className="cmp-scale-fill"
                    style={{
                      width: `${(Math.abs(s) / surplusScale) * 50}%`,
                      [s >= 0 ? 'left' : 'right']: '50%'
                    }}
                  />
                </span>
                <span className="cmp-gap">{gap(surpluses, i, (v) => money(v, { signed: true }))}</span>
              </div>
            );
          })}
        </Fragment>

        {[
          moneyRow('Expected value', (p) => expectedOf(p, mode.id)),
          moneyRow('Cap hit', (p) => p.salary, meta?.salary_season),
          moneyRow('Guaranteed', (p) => p.guaranteed_remaining, 'Remaining')
        ].map((row) => {
          const values = selected.map(row.get);
          return (
            <Fragment key={row.label}>
              <RowHead label={row.label} note={row.note} />
              {selected.map((p, i) => (
                <div className="cmp-cell" key={p.bbref_slug}>
                  <span className="cmp-val" title={moneyExact(values[i])}>
                    {money(values[i])}
                  </span>
                  <span className="cmp-gap">{gap(values, i, (v) => money(v, { signed: true }))}</span>
                </div>
              ))}
            </Fragment>
          );
        })}

        {/* The two denominators sit adjacent on purpose. This is where the
            inversion is legible without touching the toggle: Curry is paid
            $4.1M MORE than Davis and reads higher on the cap row, then lower
            on the payroll row, purely because Golden State spent more on
            everyone else. */}
        <Fragment key="capshare">
          <RowHead
            label="Share of league cap"
            note={`Of ${money(meta?.salary_cap ?? denominator?.cap, { precision: 1 })}`}
          />
          {selected.map((p) => (
            <div className="cmp-cell" key={p.bbref_slug}>
              <span className="cmp-val">{pct(p.cap_pct, 1)}</span>
            </div>
          ))}
        </Fragment>

        {anyPayroll && (
          <Fragment key="payrollshare">
            <RowHead label="Share of team payroll" note="The same salary, team-relative" />
            {selected.map((p, i) => {
              const shares = payrolls.map((c) => c?.share ?? 0);
              return (
                <div className="cmp-cell" key={p.bbref_slug}>
                  <span className="cmp-val">{payrolls[i] ? pct(payrolls[i].share, 1) : '—'}</span>
                  <span className="cmp-gap">
                    {gap(shares, i, (v) => `${signedNum(v * 100, 1)} pts`)}
                  </span>
                </div>
              );
            })}
          </Fragment>
        )}

        <Fragment key="othermode">
          <RowHead label="Surplus" note={`${other.short} model`} />
          {selected.map((p) => {
            const s = p[other.surplusKey];
            return (
              <div className="cmp-cell" key={p.bbref_slug}>
                <span className={s >= 0 ? 'cmp-val is-pos' : 'cmp-val is-neg'}>
                  {money(s, { signed: true })}
                </span>
                {p.expected_salary_naive === p.expected_salary_replacement && (
                  <span className="cmp-gap">floored at the minimum</span>
                )}
              </div>
            );
          })}
        </Fragment>

        <Band label="Contract" note={meta?.salary_season ?? '2026-27'} />

        <Fragment key="years">
          <RowHead label="Years left" />
          {selected.map((p) => (
            <div className="cmp-cell" key={p.bbref_slug}>
              <span className="cmp-val">{p.years_remaining ?? '—'}</span>
            </div>
          ))}
        </Fragment>

        <Fragment key="trade">
          <RowHead label="Trade status" />
          {selected.map((p) => (
            <div className="cmp-cell" key={p.bbref_slug}>
              <span className={p.trade_eligible ? 'cmp-flag is-ok' : 'cmp-flag is-blocked'}>
                {p.trade_eligible ? 'Eligible' : 'Restricted'}
              </span>
              {!p.trade_eligible && p.trade_restriction_reason && (
                <span className="cmp-gap">
                  {TRADE_RESTRICTIONS[p.trade_restriction_reason] ??
                    p.trade_restriction_reason.replace(/_/g, ' ')}
                </span>
              )}
            </div>
          ))}
        </Fragment>

        <Fragment key="timeline">
          <RowHead label="Cap hits ahead" note="Flat projection past year one" />
          {selected.map((p) => (
            <div className="cmp-cell" key={p.bbref_slug}>
              <YearStrip player={p} cap={meta?.salary_cap ?? 164961000} />
            </div>
          ))}
        </Fragment>

        {/* An identical contract is survivable under the cap and punitive past
            the second apron, so the books it sits inside are a term of the deal
            in every practical sense. */}
        {anyPayroll && <Band label="Payroll it sits inside" note="Full roster, every contract" />}

        {anyPayroll && (
          <Fragment key="teampayroll">
            <RowHead label="Team payroll" note="All contracts on the books" />
            {selected.map((p, i) => (
              <div className="cmp-cell" key={p.bbref_slug}>
                <span className="cmp-val" title={moneyExact(payrolls[i]?.payroll)}>
                  {payrolls[i] ? money(payrolls[i].payroll, { precision: 1 }) : '—'}
                </span>
                <span className="cmp-gap">
                  {payrolls[i] ? `${num(payrolls[i].vsCap, 2)}× the cap` : null}
                </span>
              </div>
            ))}
          </Fragment>
        )}

        {anyPayroll && (
          <Fragment key="apronstate">
            <RowHead label="Apron state" note="What it costs the front office" />
            {selected.map((p, i) => (
              <div className="cmp-cell" key={p.bbref_slug}>
                {payrolls[i] ? (
                  <>
                    <ApronMeter status={payrolls[i].status} />
                    <span className="cmp-gap">{payrolls[i].summary}</span>
                  </>
                ) : (
                  <span className="cmp-val">—</span>
                )}
              </div>
            ))}
          </Fragment>
        )}

        <Band label="Production" note={meta?.stats_season ?? '2025-26'} />

        <Fragment key="warspread">
          <RowHead label="WAR estimates" note="VORP, win shares, DARKO" />
          {selected.map((p) => (
            <div className="cmp-cell" key={p.bbref_slug}>
              <WarSpread player={p} width={260} />
            </div>
          ))}
        </Fragment>

        {PROFILE.map((row) => {
          const values = selected.map((p) => p[row.key]);
          return (
            <Fragment key={row.key}>
              <RowHead label={row.label} />
              {selected.map((p, i) => {
                const value = values[i];
                const rank = row.raw ? value : percentiles[row.key](value);
                return (
                  <div className="cmp-cell is-metric" key={p.bbref_slug}>
                    <span className="cmp-val">{row.fmt(value)}</span>
                    <span className="cmp-pctl-track" aria-hidden="true">
                      <span
                        className={rank >= 50 ? 'cmp-pctl-fill is-high' : 'cmp-pctl-fill'}
                        style={{ width: `${Math.max(1.5, rank)}%` }}
                      />
                    </span>
                    <span className="cmp-pctl-label">{ordinal(Math.round(rank))} percentile</span>
                    <span className="cmp-gap">
                      {gap(values, i, (v) => signedNum(row.key === 'ts_residual' ? v * 100 : v, row.digits))}
                    </span>
                  </div>
                );
              })}
            </Fragment>
          );
        })}
      </div>

      <p className="cmp-foot">
        Percentiles rank against the {meta?.min_minutes ?? 500}-minute qualifiers in this dataset, not the
        whole league.
      </p>
    </section>
  );
}

function Band({ label, note }) {
  return (
    <div className="cmp-band">
      <span className="cmp-band-label">{label}</span>
      {note && <span className="cmp-band-note">{note}</span>}
    </div>
  );
}

function RowHead({ label, note }) {
  return (
    <div className="cmp-rowhead">
      <span className="cmp-key">{label}</span>
      {note && <span className="cmp-note">{note}</span>}
    </div>
  );
}

function YearStrip({ player, cap }) {
  // The record carries a single cap hit plus years and guarantees; later years
  // are a flat projection and the row header says so.
  const years = useMemo(() => {
    const n = Math.max(1, player.years_remaining ?? 1);
    return Array.from({ length: n }, (_, i) => ({
      season: `${2026 + i}-${String((27 + i) % 100).padStart(2, '0')}`,
      value: player.salary,
      guaranteed: i * player.salary < player.guaranteed_remaining
    }));
  }, [player]);

  return (
    <ul className="year-strip">
      {years.map((y) => (
        <li key={y.season} className={y.guaranteed ? 'is-guaranteed' : undefined}>
          <span className="year-bar-slot">
            <span
              className="year-bar"
              style={{ height: `${Math.max(4, Math.min(1, y.value / (cap * 0.35)) * 48)}px` }}
            />
          </span>
          <span className="year-label">{y.season}</span>
          <span className="year-value">{money(y.value)}</span>
        </li>
      ))}
    </ul>
  );
}
