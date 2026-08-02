import { APRON, APRON_STEPS } from '../model/payroll.js';

/**
 * Four notches; the number lit is the rung. Neutral ramp only - blue and red
 * mean surplus everywhere else in this app and are not borrowed here.
 *
 * Lifted out of the team table so every surface that states a team's apron
 * state - the chart tooltip, the pinned card, the comparison grid - draws the
 * same encoding rather than inventing a second visual language for one fact.
 */
export default function ApronMeter({ status, className }) {
  const apron = APRON[status] ?? null;
  if (!apron) {
    return <span className="apron apron-unknown">Not reported</span>;
  }
  return (
    <span
      className={className ? `apron ${className}` : 'apron'}
      data-step={apron.step}
      title={apron.note}
    >
      <span className="apron-meter" aria-hidden="true">
        {Array.from({ length: APRON_STEPS }, (_, i) => (
          <span key={i} className={i < apron.step ? 'notch is-on' : 'notch'} />
        ))}
      </span>
      <span className="apron-label">{apron.label}</span>
    </span>
  );
}
