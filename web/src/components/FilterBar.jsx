import { useMemo } from 'react';
import { CONTRACT_TYPES, POSITIONS } from '../model/constants.js';
import FacetSelect from './FacetSelect.jsx';
import RangeSlider from './RangeSlider.jsx';

const EAST = new Set([
  'ATL', 'BOS', 'BRK', 'BKN', 'CHA', 'CHO', 'CHI', 'CLE', 'DET', 'IND',
  'MIA', 'MIL', 'NYK', 'ORL', 'PHI', 'TOR', 'WAS'
]);

const conferenceOf = (team) => (EAST.has(team) ? 'East' : 'West');

/** Fallback for a contract type the ETL emits that constants.js has no entry
 *  for: `designated_veteran` -> `Designated veteran`. Never a bare symbol. */
const humanizeType = (key) =>
  String(key).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

const CLEARED = {
  positions: [],
  contractTypes: [],
  teams: [],
  ageRange: null,
  search: ''
};

export default function FilterBar({
  filters,
  onChange,
  bounds,
  teamList,
  contractTypeList,
  matchCount,
  total
}) {
  const set = (patch) => onChange({ ...filters, ...patch });

  const toggleIn = (key, value) => {
    const cur = filters[key];
    set({ [key]: cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value] });
  };

  const [minAge, maxAge] = bounds?.age ?? [19, 41];
  const range = filters.ageRange ?? [minAge, maxAge];
  const ageIsAll = range[0] <= minAge && range[1] >= maxAge;

  const teamOptions = useMemo(
    () => teamList.map((t) => ({ value: t, label: t, group: conferenceOf(t) })),
    [teamList]
  );

  const contractOptions = useMemo(
    () =>
      // Word-only: the abstract glyphs (◆ ▲ ● ■ △ ○ □) read as noise once they
      // sit next to a text label anyway, and the redundant non-colour channel
      // that rule exists for is carried by the chip border style instead.
      contractTypeList.map((ct) => ({
        value: ct,
        label: CONTRACT_TYPES[ct]?.label ?? humanizeType(ct)
      })),
    [contractTypeList]
  );

  const active = [];
  for (const p of filters.positions) active.push({ key: `pos:${p}`, text: p, clear: () => toggleIn('positions', p) });
  for (const ct of filters.contractTypes)
    active.push({
      key: `ct:${ct}`,
      text: CONTRACT_TYPES[ct]?.label ?? ct,
      clear: () => toggleIn('contractTypes', ct)
    });
  for (const t of filters.teams) active.push({ key: `team:${t}`, text: t, clear: () => toggleIn('teams', t) });
  if (!ageIsAll)
    active.push({
      key: 'age',
      text: `Age ${range[0]}–${range[1]}`,
      clear: () => set({ ageRange: null })
    });
  if (filters.search.trim())
    active.push({
      key: 'search',
      text: `"${filters.search.trim()}"`,
      clear: () => set({ search: '' })
    });

  return (
    <div className="filter-bar">
      <div className="filter-row">
        <label className="field filter-search">
          <span className="field-label">Search</span>
          <input
            type="search"
            value={filters.search}
            placeholder="Player or team"
            onChange={(e) => set({ search: e.target.value })}
          />
        </label>

        <fieldset className="field filter-positions">
          <legend className="field-label">Position</legend>
          <div className="chip-row">
            {POSITIONS.map((pos) => (
              <button
                key={pos}
                type="button"
                aria-pressed={filters.positions.includes(pos)}
                className={filters.positions.includes(pos) ? 'chip active' : 'chip'}
                onClick={() => toggleIn('positions', pos)}
              >
                {pos}
              </button>
            ))}
          </div>
        </fieldset>

        <FacetSelect
          label="Contract"
          options={contractOptions}
          selected={filters.contractTypes}
          onToggle={(v) => toggleIn('contractTypes', v)}
          onClear={() => set({ contractTypes: [] })}
          searchable={false}
        />

        <FacetSelect
          label="Team"
          options={teamOptions}
          selected={filters.teams}
          onToggle={(v) => toggleIn('teams', v)}
          onClear={() => set({ teams: [] })}
          columns={2}
        />

        <div className="field filter-age">
          <div className="filter-age-head">
            <span className="field-label">Age</span>
            <output className="filter-age-value">
              {ageIsAll ? 'Any' : `${range[0]}–${range[1]}`}
            </output>
            {!ageIsAll && (
              <button
                type="button"
                className="filter-reset"
                aria-label="Reset age"
                onClick={() => set({ ageRange: null })}
              >
                Reset
              </button>
            )}
          </div>
          <RangeSlider
            label="Age"
            min={minAge}
            max={maxAge}
            value={range}
            onChange={(next) =>
              set({ ageRange: next[0] <= minAge && next[1] >= maxAge ? null : next })
            }
          />
        </div>

        <p className="filter-count" role="status">
          <strong>{matchCount.toLocaleString()}</strong>
          <span aria-hidden="true">/</span>
          <span className="filter-count-total">{total.toLocaleString()}</span>
        </p>
      </div>

      {active.length > 0 && (
        <div className="filter-active">
          {active.map((a) => (
            <button
              key={a.key}
              type="button"
              className="filter-tag"
              onClick={a.clear}
              aria-label={`Remove filter ${a.text}`}
            >
              {a.text}
              {/* A drawn cross, not a multiplication sign standing in for one. */}
              <svg
                className="filter-tag-x"
                width="9"
                height="9"
                viewBox="0 0 10 10"
                aria-hidden="true"
                focusable="false"
              >
                <path
                  d="M2 2 L8 8 M8 2 L2 8"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          ))}
          <button type="button" className="filter-clear-all" onClick={() => onChange(CLEARED)}>
            Clear all
          </button>
        </div>
      )}
    </div>
  );
}
