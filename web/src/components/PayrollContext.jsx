import { money, pct } from '../model/format.js';
import ApronMeter from './ApronMeter.jsx';

/**
 * Where one contract sits inside the books that carry it.
 *
 * The cap is the league's limit, not a ceiling teams are held to, so a cap
 * share alone understates how expensive a contract actually is: the same
 * $40M is survivable for a team with room and punitive for a team past the
 * second apron, where every dollar carries repeater tax and the front office
 * has lost aggregation, sign-and-trade and its own picks.
 *
 * Three facts, in the order a reader needs them: the payroll, the rung it has
 * reached, and this player's share of it. The rung reuses the four-notch meter
 * from the team table rather than a second encoding for the same fact.
 */
export default function PayrollContext({ context, seasonLabel }) {
  if (!context) return null;

  return (
    <div className="payroll-context">
      <dl className="stat-grid">
        <div>
          <dt>Team payroll</dt>
          <dd>
            {money(context.payroll, { precision: 1 })}{' '}
            <span className="muted">
              {context.rosterCount ? `${context.rosterCount} contracts` : 'full roster'}
            </span>
          </dd>
        </div>
        <div>
          <dt>This contract</dt>
          <dd>
            {pct(context.share, 1)} <span className="muted">of {context.team}&rsquo;s payroll</span>
          </dd>
        </div>
      </dl>

      <div className="payroll-rung">
        <ApronMeter status={context.status} />
        {context.summary && <p className="payroll-summary">{context.summary}</p>}
      </div>

      {context.apron?.note && <p className="payroll-note">{context.apron.note}</p>}

      {seasonLabel && (
        <p className="payroll-caveat">
          Every contract on the {seasonLabel} books, including players below the minutes floor and
          dead money.
        </p>
      )}
    </div>
  );
}
