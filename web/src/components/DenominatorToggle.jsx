import { DENOMINATORS, DENOM_ORDER } from '../model/denominator.js';
import { money } from '../model/format.js';

/**
 * Compact segmented control for the Y-axis denominator (ADR-015). Sibling in
 * form and behaviour to ModeToggle, because it is the same kind of decision:
 * an arguable constant surfaced in the UI rather than picked silently.
 *
 * The sub-line under each segment is the argument. "League cap" shows one
 * figure because there IS one figure; "Team payroll" shows the league's actual
 * range, which is the reader's own point - teams paying luxury tax or sitting
 * past an apron are committed well beyond the cap.
 */
export default function DenominatorToggle({ denominator, onChange, teams }) {
  const payrolls = (teams ?? []).map((t) => t.total_salary).filter((v) => v > 0);
  const range = payrolls.length
    ? `${money(Math.min(...payrolls), { precision: 1 })}–${money(Math.max(...payrolls), { precision: 1 })}`
    : '30 rosters';

  const sub = {
    cap: money(denominator.cap, { precision: 1 }),
    team: range
  };

  return (
    <div className="mode-toggle">
      <span className="mode-label">Salary measured against</span>
      <div className="segmented" role="radiogroup" aria-label="Salary measured against">
        {DENOM_ORDER.map((id) => {
          const d = DENOMINATORS[id];
          const active = denominator.id === id;
          return (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={active}
              className={active ? 'seg active' : 'seg'}
              title={`${d.label} — ${d.blurb}`}
              onClick={() => onChange(id)}
            >
              <span className="seg-name">{d.short}</span>
              <span className="seg-value">{sub[id]}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
