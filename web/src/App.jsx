import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDataset } from './data/useDataset.js';
import { MODES } from './model/constants.js';
import { makeDenominator } from './model/denominator.js';
import { EMPTY_FILTERS, filtersAreActive, matchingSlugs, surplusOf } from './model/valuation.js';
import { money, timeAgo } from './model/format.js';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import DenominatorToggle from './components/DenominatorToggle.jsx';
import FilterBar from './components/FilterBar.jsx';
import ModeToggle from './components/ModeToggle.jsx';
import { ErrorScreen, LoadingScreen, EmptyScreen, WarningBanner } from './components/StateScreens.jsx';
import ScatterView from './views/ScatterView.jsx';
import LeaderboardView from './views/LeaderboardView.jsx';
import TeamsView from './views/TeamsView.jsx';
import PlayerView from './views/PlayerView.jsx';

const VIEWS = [
  { id: 'scatter', label: 'Value scatter' },
  { id: 'leaderboard', label: 'Leaderboard' },
  { id: 'teams', label: 'Team rollup' },
  { id: 'player', label: 'Compare' }
];

const readHash = () => {
  const id = window.location.hash.replace('#', '');
  return VIEWS.some((v) => v.id === id) ? id : 'scatter';
};

export default function App() {
  const { status, error, warnings, data, bounds, teamList, contractTypeList, reload, dataBase } =
    useDataset();

  const [view, setView] = useState(readHash);
  const [modeId, setModeId] = useState('replacement');
  // The league cap is the default denominator (ADR-015): the chart's whole
  // point is one league-wide regression, and a regression only means anything
  // when every point is measured against the same ruler.
  const [denomId, setDenomId] = useState('cap');
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [pinnedSlug, setPinnedSlug] = useState(null);
  const [comparison, setComparison] = useState([]);

  const reduceMotion = useMemo(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    []
  );

  useEffect(() => {
    const onHash = () => setView(readHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const goto = useCallback((id) => {
    window.location.hash = id;
    setView(id);
  }, []);

  const mode = MODES[modeId];
  const players = data?.players ?? [];
  const meta = data?.meta ?? {};
  const teams = data?.teams ?? [];

  /**
   * The active Y-axis denominator, plus everything derived from it: the share
   * accessor, the two reference fits (server-supplied on the cap axis, refit
   * client-side on the team axis) and the team payroll context every detail
   * surface reads from. One object rather than five loose props.
   */
  const denominator = useMemo(
    () => makeDenominator({ denomId, modeId, meta, players, teams }),
    [denomId, modeId, meta, players, teams]
  );

  const matching = useMemo(
    () => (players.length ? matchingSlugs(players, filters) : new Set()),
    [players, filters]
  );
  const active = useMemo(() => filtersAreActive(filters, bounds), [filters, bounds]);

  const headline = useMemo(() => {
    if (!players.length) return null;
    const sorted = [...players].sort((a, b) => surplusOf(b, modeId) - surplusOf(a, modeId));
    const overpaid = players.filter((p) => surplusOf(p, modeId) < 0).length;
    return {
      best: sorted[0],
      worst: sorted[sorted.length - 1],
      overpaid,
      overpaidPct: Math.round((overpaid / players.length) * 100)
    };
  }, [players, modeId]);

  const openComparison = useCallback(
    (player) => {
      setComparison((prev) => (prev.includes(player.bbref_slug) ? prev : [...prev, player.bbref_slug].slice(-3)));
      goto('player');
    },
    [goto]
  );

  if (status === 'loading') {
    return (
      <Shell>
        <LoadingScreen />
      </Shell>
    );
  }

  if (status === 'error') {
    return (
      <Shell>
        <ErrorScreen error={error} onRetry={reload} dataBase={dataBase} />
      </Shell>
    );
  }

  if (status === 'empty') {
    return (
      <Shell>
        <EmptyScreen title="The dataset is empty" body="players.json contained no records." />
      </Shell>
    );
  }

  return (
    <Shell>
      <header className="app-header">
        <div className="brand">
          <h1>NBA contract value</h1>
          <dl
            className="season-pair"
            title={`${meta.stats_season} production scored against ${meta.salary_season} salary`}
          >
            <div className="season-unit">
              <dt className="season-key">Production</dt>
              <dd className="season-value">{meta.stats_season}</dd>
            </div>
            <div className="season-unit">
              <dt className="season-key">Salary</dt>
              <dd className="season-value">{meta.salary_season}</dd>
            </div>
          </dl>
        </div>
        {/* Two arguable constants, two controls, same visual language: what a
            win costs, and what a salary is measured against. */}
        <div className="header-controls">
          <ModeToggle mode={mode} onChange={setModeId} meta={meta} />
          <DenominatorToggle denominator={denominator} onChange={setDenomId} teams={teams} />
        </div>
      </header>

      {meta.is_mock && (
        <p className="mock-banner" role="status">
          <strong>Mock data.</strong> Salaries and box scores are synthetic.
        </p>
      )}

      <WarningBanner warnings={warnings} />

      {headline && (
        <div className="headline-strip">
          <Stat
            index={0}
            label="Salary cap"
            value={money(meta.salary_cap, { precision: 1 })}
            sub={`Per team, ${meta.salary_season}`}
          />
          <Stat
            index={1}
            label="Price of one win"
            value={money(meta.dollars_per_win?.[modeId], { precision: 2 })}
            sub={`${mode.label} model`}
          />
          <Stat
            index={2}
            label="Overpaid players"
            value={`${headline.overpaidPct}%`}
            sub={`${headline.overpaid} of ${players.length} players`}
          />
          <Stat
            index={3}
            label="Best value"
            value={headline.best.name}
            variant="name"
            sub={`${money(surplusOf(headline.best, modeId), { signed: true })} surplus`}
            tone="pos"
            action="Show on chart"
            onClick={() => {
              setPinnedSlug(headline.best.bbref_slug);
              goto('scatter');
            }}
          />
          <Stat
            index={4}
            label="Worst value"
            value={headline.worst.name}
            variant="name"
            sub={`${money(surplusOf(headline.worst, modeId), { signed: true })} shortfall`}
            tone="neg"
            action="Show on chart"
            onClick={() => {
              setPinnedSlug(headline.worst.bbref_slug);
              goto('scatter');
            }}
          />
        </div>
      )}

      <nav className="view-tabs" aria-label="Views">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            className={view === v.id ? 'tab active' : 'tab'}
            aria-current={view === v.id ? 'page' : undefined}
            onClick={() => goto(v.id)}
          >
            <span className="tab-label">{v.label}</span>
          </button>
        ))}
      </nav>

      {view !== 'player' && (
        <FilterBar
          filters={filters}
          onChange={setFilters}
          bounds={bounds}
          teamList={teamList}
          contractTypeList={contractTypeList}
          matchCount={matching.size}
          total={players.length}
        />
      )}

      <main className="app-main">
        <ErrorBoundary resetKey={`${view}:${modeId}:${denomId}`}>
        {view === 'scatter' && (
          <ScatterView
            players={players}
            meta={meta}
            mode={mode}
            denominator={denominator}
            matching={active ? matching : null}
            filtersActive={active}
            pinnedSlug={pinnedSlug}
            onPin={setPinnedSlug}
            onCompare={openComparison}
            reduceMotion={reduceMotion}
          />
        )}
        {view === 'leaderboard' && (
          <LeaderboardView
            players={players}
            meta={meta}
            mode={mode}
            denominator={denominator}
            matching={active ? matching : null}
            pinnedSlug={pinnedSlug}
            onPin={setPinnedSlug}
            onCompare={openComparison}
          />
        )}
        {view === 'teams' && (
          <TeamsView
            players={players}
            teams={teams}
            mode={mode}
            denominator={denominator}
            matching={active ? matching : null}
            onPin={(slug) => {
              setPinnedSlug(slug);
              goto('leaderboard');
            }}
          />
        )}
        {view === 'player' && (
          <PlayerView
            players={players}
            mode={mode}
            meta={meta}
            denominator={denominator}
            selection={comparison}
            onSelectionChange={setComparison}
          />
        )}
        </ErrorBoundary>
      </main>

      <footer className="app-footer">
        <p>Updated {timeAgo(meta.generated_at)} UTC</p>
      </footer>
    </Shell>
  );
}

function Shell({ children }) {
  return <div className="app-shell">{children}</div>;
}

/**
 * One headline figure. Three fixed typographic ranks, always in this order:
 * a small uppercase label saying what it is, the value at display size in
 * tabular figures, and a plain-English sub-line. Colour touches the sub-line
 * only, so polarity never has to carry the identity of the card.
 */
function Stat({ label, value, sub, tone, onClick, action, variant, index = 0 }) {
  const content = (
    <>
      <span className="stat-label">{label}</span>
      <span className={variant === 'name' ? 'stat-value is-name' : 'stat-value'}>{value}</span>
      {sub && <span className={tone ? `stat-sub ${tone}` : 'stat-sub'}>{sub}</span>}
      {onClick && (
        <span className="stat-action">
          {action ?? 'Open'}
          <Chevron />
        </span>
      )}
    </>
  );
  const style = { '--enter-index': index };
  return onClick ? (
    <button type="button" className="stat interactive enter" style={style} onClick={onClick}>
      {content}
    </button>
  ) : (
    <div className="stat enter" style={style}>
      {content}
    </div>
  );
}

function Chevron() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" focusable="false">
      <path
        d="M3.5 1.5 L7 5 L3.5 8.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
