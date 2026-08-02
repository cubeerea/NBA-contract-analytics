import { useMemo } from 'react';
import ScatterChart from '../chart/ScatterChart.jsx';
import PlayerCard from '../components/PlayerCard.jsx';
import Headshot from '../components/Headshot.jsx';
import { EmptyScreen } from '../components/StateScreens.jsx';
import { money } from '../model/format.js';
import { surplusOf } from '../model/valuation.js';

export default function ScatterView({
  players,
  meta,
  mode,
  denominator,
  matching,
  filtersActive,
  pinnedSlug,
  onPin,
  onCompare,
  reduceMotion
}) {
  const pinned = useMemo(
    () => players.find((p) => p.bbref_slug === pinnedSlug) ?? null,
    [players, pinnedSlug]
  );

  const extremes = useMemo(() => {
    const pool = players.filter((p) => !matching || matching.has(p.bbref_slug));
    if (!pool.length) return { best: [], worst: [] };
    const sorted = [...pool].sort((a, b) => surplusOf(b, mode.id) - surplusOf(a, mode.id));
    return { best: sorted.slice(0, 3), worst: sorted.slice(-3).reverse() };
  }, [players, matching, mode.id]);

  return (
    <div className="scatter-view">
      <div className="scatter-main">
        <ScatterChart
          players={players}
          meta={meta}
          mode={mode}
          denominator={denominator}
          matching={matching}
          filtersActive={filtersActive}
          pinnedSlug={pinnedSlug}
          onPin={onPin}
          reduceMotion={reduceMotion}
        />
      </div>

      <div className="scatter-side">
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
          <div className="side-stack">
            <EmptyScreen
              title="No player pinned"
              body="Click any face on the chart, or start from the extremes below."
            />
            <ExtremeList
              title="Best value"
              caption="Most surplus under the model"
              rows={extremes.best}
              mode={mode}
              onPin={onPin}
            />
            <ExtremeList
              title="Worst value"
              caption="Largest shortfall under the model"
              rows={extremes.worst}
              mode={mode}
              onPin={onPin}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function ExtremeList({ title, caption, rows, mode, onPin }) {
  if (!rows.length) return null;
  return (
    <section className="extreme-list">
      <header className="extreme-head">
        <h3>{title}</h3>
        <p>{caption}</p>
      </header>
      <ul>
        {rows.map((p, i) => {
          const s = surplusOf(p, mode.id);
          const moved = p.stats_team && p.stats_team !== p.team;
          return (
            <li key={p.bbref_slug}>
              <button type="button" onClick={() => onPin(p.bbref_slug)}>
                <span className="ex-rank">{i + 1}</span>
                <Headshot player={p} size={34} />
                <span className="ex-name">
                  <span className="ex-name-text">{p.name}</span>
                  <span className="ex-name-sub">
                    <span className="ex-team">{p.team}</span>
                    {moved && <span className="ex-from">from {p.stats_team}</span>}
                    <span className="ex-salary">{money(p.salary)} paid</span>
                  </span>
                </span>
                <span className={s >= 0 ? 'ex-value is-pos' : 'ex-value is-neg'}>
                  {money(s, { signed: true })}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
