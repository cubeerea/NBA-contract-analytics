import { useMemo, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import ContractChip from '../components/ContractChip.jsx';
import Headshot from '../components/Headshot.jsx';
import PlayerCard from '../components/PlayerCard.jsx';
import { EmptyScreen } from '../components/StateScreens.jsx';
import { money, num, pct } from '../model/format.js';

import { expectedOf, surplusOf } from '../model/valuation.js';

/**
 * Column order groups the table into four bands, left to right: who the
 * player is, what he produced, what he is paid, and what that is worth. The
 * `group` flag on the first column of each band draws the vertical rule and
 * spans the group header above it.
 */
const COLUMNS = [
  { key: 'rank', label: '#', align: 'right', sortable: false, width: 34, group: 'who' },
  { key: 'name', label: 'Player', align: 'left', sortable: true, group: 'who' },
  { key: 'team', label: 'Team', align: 'left', sortable: true, width: 68, group: 'who' },
  { key: 'position', label: 'Pos', align: 'left', sortable: true, width: 48, group: 'who' },
  { key: 'age', label: 'Age', align: 'right', sortable: true, width: 46, group: 'who' },
  { key: 'composite_score', label: 'Score', align: 'right', sortable: true, width: 64, group: 'made' },
  { key: 'war_blended', label: 'WAR', align: 'right', sortable: true, width: 56, group: 'made' },
  { key: 'salary', label: 'Cap hit', align: 'right', sortable: true, width: 86, group: 'paid' },
  { key: 'contract_type', label: 'Type', align: 'left', sortable: true, width: 96, group: 'paid' },
  { key: 'expected', label: 'Expected', align: 'right', sortable: true, width: 84, group: 'worth' },
  { key: 'surplus', label: 'Surplus', align: 'right', sortable: true, width: 206, group: 'worth' }
];

/** Header band above the column labels. Order matches COLUMNS. */
const GROUPS = [
  { id: 'who', label: 'Player' },
  { id: 'made', label: 'Production' },
  { id: 'paid', label: 'Contract' },
  { id: 'worth', label: 'Valuation' }
];

const SORT_LABEL = Object.fromEntries(COLUMNS.map((c) => [c.key, c.label]));

const PAGE = 60;

export default function LeaderboardView({
  players,
  mode,
  meta,
  denominator,
  matching,
  pinnedSlug,
  onPin,
  onCompare
}) {
  const [sort, setSort] = useState({ key: 'surplus', dir: 'desc' });
  const [limit, setLimit] = useState(PAGE);
  const reduce = useReducedMotion();

  const rows = useMemo(() => {
    const pool = players.filter((p) => !matching || matching.has(p.bbref_slug));
    const valueOf = (p) => {
      switch (sort.key) {
        case 'surplus':
          return surplusOf(p, mode.id);
        case 'expected':
          return expectedOf(p, mode.id);
        default:
          return p[sort.key];
      }
    };
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...pool].sort((a, b) => {
      const av = valueOf(a);
      const bv = valueOf(b);
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av).localeCompare(String(bv)) * dir;
      }
      return ((av ?? 0) - (bv ?? 0)) * dir;
    });
  }, [players, matching, sort, mode.id]);

  // One pass for the whole pool, so the bar in every row is drawn against the
  // same scale no matter how the table is sorted or paged.
  const maxAbs = useMemo(() => {
    let max = 1;
    for (const r of rows) {
      const v = Math.abs(surplusOf(r, mode.id));
      if (v > max) max = v;
    }
    return max;
  }, [rows, mode.id]);

  const pinned = useMemo(
    () => players.find((p) => p.bbref_slug === pinnedSlug) ?? null,
    [players, pinnedSlug]
  );

  const toggleSort = (key) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' }));

  if (!rows.length) {
    return <EmptyScreen title="No players match these filters" body="Widen or clear a filter to see contracts again." />;
  }

  const visible = rows.slice(0, limit);
  const groupSpan = (id) => COLUMNS.filter((c) => c.group === id).length;

  return (
    <div className="leaderboard-view">
      <section className="lb-panel">
        <header className="lb-caption">
          <p className="lb-count">
            <strong>{visible.length}</strong>
            <span className="lb-count-of">shown of {rows.length} contracts</span>
          </p>
          <p className="lb-sortnote">
            <span className="lb-sortnote-key">Sorted by</span>
            <span className="lb-sortnote-value">{SORT_LABEL[sort.key]}</span>
            <span className="lb-sortnote-dir">{sort.dir === 'desc' ? 'high to low' : 'low to high'}</span>
          </p>
        </header>

        <div className="table-scroll">
          <table className="data-table">
            {/* Fixed table layout reads its widths from the colgroup, not from
                the header cells - the group band above them spans columns. */}
            <colgroup>
              {COLUMNS.map((c) => (
                <col key={c.key} style={c.width ? { width: c.width } : undefined} />
              ))}
            </colgroup>
            <thead>
              <tr className="group-row">
                {GROUPS.map((g) => (
                  <th key={g.id} scope="colgroup" colSpan={groupSpan(g.id)} className="group-head">
                    <span>{g.label}</span>
                  </th>
                ))}
              </tr>
              <tr>
                {COLUMNS.map((c, i) => {
                  const active = sort.key === c.key;
                  const starts = i === 0 || COLUMNS[i - 1].group !== c.group;
                  return (
                    <th
                      key={c.key}
                      scope="col"
                      className={starts ? 'group-start' : undefined}
                      style={{ width: c.width, textAlign: c.align }}
                      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                    >
                      {c.sortable ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(c.key)}
                          className={active ? 'th-button is-active' : 'th-button'}
                          title={`Sort by ${c.label}`}
                        >
                          <span>{c.label}</span>
                          <SortIcon state={active ? sort.dir : 'none'} />
                        </button>
                      ) : (
                        <span>{c.label}</span>
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false}>
                {visible.map((p, i) => {
                  const s = surplusOf(p, mode.id);
                  const moved = p.stats_team && p.stats_team !== p.team;
                  return (
                    <motion.tr
                      key={p.bbref_slug}
                      layout={reduce ? false : 'position'}
                      initial={reduce ? false : { opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={reduce ? undefined : { opacity: 0 }}
                      transition={{ duration: reduce ? 0 : 0.24, ease: [0.77, 0, 0.175, 1] }}
                      className={p.bbref_slug === pinnedSlug ? 'is-pinned' : undefined}
                      onClick={() => onPin(p.bbref_slug)}
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          onPin(p.bbref_slug);
                        }
                      }}
                    >
                      <td className="num rank-cell">{i + 1}</td>
                      <td>
                        {/* The flex/grid lives on a span, never on the <td>:
                            a td that is not display:table-cell stops taking
                            the row height and its rule breaks mid-row. */}
                        <span className="name-cell">
                          <Headshot player={p} size={28} />
                          <span className="name-text">{p.name}</span>
                        </span>
                      </td>
                      <td>
                        <span className="team-cell">
                          <span className="team-now">{p.team}</span>
                          {moved && (
                            <span
                              className="team-from"
                              title={`Signed with ${p.team} for ${meta?.salary_season ?? '2026-27'}; the ${
                                meta?.stats_season ?? '2025-26'
                              } production below was earned with ${p.stats_team}`}
                            >
                              from {p.stats_team}
                            </span>
                          )}
                        </span>
                      </td>
                      <td>{p.position}</td>
                      <td className="num">{p.age}</td>
                      <td className="num group-start">{num(p.composite_score, 1)}</td>
                      <td className="num">{num(p.war_blended, 1)}</td>
                      <td
                        className="num group-start"
                        title={payrollTitle(p, denominator, meta?.salary_cap)}
                      >
                        {money(p.salary)}
                      </td>
                      <td>
                        <ContractChip type={p.contract_type} compact />
                      </td>
                      <td className="num muted group-start">{money(expectedOf(p, mode.id))}</td>
                      <td>
                        <span className={s >= 0 ? 'surplus-cell is-pos' : 'surplus-cell is-neg'}>
                          <span className="surplus-figure">{money(s, { signed: true })}</span>
                          <span className="surplus-track" aria-hidden="true">
                            <span className="surplus-axis" />
                            <span
                              className="surplus-fill"
                              style={{
                                width: `${Math.min(50, (Math.abs(s) / maxAbs) * 50)}%`,
                                [s >= 0 ? 'left' : 'right']: '50%'
                              }}
                            />
                          </span>
                        </span>
                      </td>
                    </motion.tr>
                  );
                })}
              </AnimatePresence>
            </tbody>
          </table>
        </div>

        {limit < rows.length && (
          <footer className="lb-more">
            <button type="button" className="button" onClick={() => setLimit((l) => l + PAGE)}>
              Show {Math.min(PAGE, rows.length - limit)} more
            </button>
            <span className="lb-more-note">{rows.length - limit} contracts remaining</span>
          </footer>
        )}
      </section>

      <div className="leaderboard-side">
        {pinned ? (
          <PlayerCard
            player={pinned}
            mode={mode}
            meta={meta}
            denominator={denominator}
            onClose={() => onPin(null)}
            onCompare={onCompare}
            dense
          />
        ) : (
          <EmptyScreen title="No contract selected" body="Pick any row to open its full valuation here." />
        )}
      </div>
    </div>
  );
}

/**
 * Both denominators at once, because the cap hit cell is where a reader is
 * most likely to want the second one: a share of the league cap says nothing
 * about whether the team carrying it is under the cap or past an apron.
 */
function payrollTitle(player, denominator, cap) {
  const capShare = `${pct(player.cap_pct, 1)} of the ${money(cap ?? denominator?.cap)} cap`;
  const ctx = denominator?.contextOf?.(player);
  if (!ctx) return capShare;
  return `${capShare} · ${pct(ctx.share, 1)} of ${ctx.team}'s ${money(ctx.payroll, {
    precision: 1
  })} payroll (${ctx.apron?.label ?? 'unreported'})`;
}

/**
 * Sort affordance. One chevron: dimmed and pointing down when the column is
 * merely sortable, solid and flipped when it is the active key. Rotation
 * carries the direction, so there is no glyph to decode.
 */
function SortIcon({ state }) {
  return (
    <svg
      className="sort-icon"
      data-state={state}
      width="9"
      height="9"
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
