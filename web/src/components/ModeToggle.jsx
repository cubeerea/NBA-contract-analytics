import { MODES, MODE_ORDER } from '../model/constants.js';
import { money } from '../model/format.js';

/** Compact segmented control for the $/win denominator. */
export default function ModeToggle({ mode, onChange, meta }) {
  const dpw = meta?.dollars_per_win ?? {};

  return (
    <div className="mode-toggle">
      <span className="mode-label">Cost of a win</span>
      <div className="segmented" role="radiogroup" aria-label="Cost of a win">
        {MODE_ORDER.map((id) => {
          const m = MODES[id];
          const activeMode = mode.id === id;
          return (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={activeMode}
              className={activeMode ? 'seg active' : 'seg'}
              title={`${m.label} — ${m.assumption}`}
              onClick={() => onChange(id)}
            >
              <span className="seg-name">{m.short}</span>
              <span className="seg-value">{money(dpw[id], { precision: 2 })}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
