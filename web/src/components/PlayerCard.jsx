import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { SALARY_TIERS, TRADE_RESTRICTIONS } from '../model/constants.js';
import { money, moneyExact, num, pct } from '../model/format.js';
import { expectedOf, surplusOf } from '../model/valuation.js';
import ContractChip from './ContractChip.jsx';
import Headshot from './Headshot.jsx';
import PayrollContext from './PayrollContext.jsx';
import WarSpread from './WarSpread.jsx';

/**
 * The pinned player card. At ~500 marks, hover alone is fiddly - hovering
 * gives a one-line readout on the chart, and clicking pins this, which stays
 * put while you keep exploring.
 *
 * The card has two faces:
 *   front - who this is and the single judgement (surplus or shortfall),
 *   back  - the evidence behind that judgement (contract terms, production).
 *
 * The flip is driven by an explicit labelled button, never by hover. This is a
 * pinned panel in a side rail, not a marketing tile: a hover flip would make
 * the contract terms impossible to read, select or copy. The hidden face is
 * `inert` + `aria-hidden`, so Tab and screen readers never fall into it, and
 * focus is handed to the twin button on the face that just arrived.
 */
export default function PlayerCard({
  player,
  mode,
  meta,
  denominator,
  onClose,
  onCompare,
  dense = false
}) {
  const backId = useId();
  const [flipped, setFlipped] = useState(false);
  const frontRef = useRef(null);
  const backRef = useRef(null);
  const frontButtonRef = useRef(null);
  const backButtonRef = useRef(null);
  const moveFocus = useRef(false);
  const [size, setSize] = useState({ front: 0, back: 0 });

  const slug = player?.bbref_slug;

  // A new player is a new card: always land on the identity face.
  useEffect(() => {
    setFlipped(false);
    moveFocus.current = false;
  }, [slug]);

  // The two faces are stacked in 3D, so the shell has no intrinsic height of
  // its own - it is told the height of whichever face is showing, and eases
  // between the two alongside the rotation.
  useLayoutEffect(() => {
    const measure = () => {
      const front = frontRef.current?.offsetHeight ?? 0;
      const back = backRef.current?.offsetHeight ?? 0;
      setSize((prev) => (prev.front === front && prev.back === back ? prev : { front, back }));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    if (frontRef.current) ro.observe(frontRef.current);
    if (backRef.current) ro.observe(backRef.current);
    return () => ro.disconnect();
  }, [slug, mode?.id, dense]);

  useEffect(() => {
    if (!moveFocus.current) return;
    moveFocus.current = false;
    const target = flipped ? backButtonRef.current : frontButtonRef.current;
    target?.focus();
  }, [flipped]);

  const toggle = useCallback(() => {
    moveFocus.current = true;
    setFlipped((f) => !f);
  }, []);

  if (!player) return null;

  const surplus = surplusOf(player, mode.id);
  const expected = expectedOf(player, mode.id);
  const positive = surplus >= 0;
  const moved = Boolean(player.stats_team) && player.stats_team !== player.team;
  const statsSeason = meta?.stats_season ?? '2025-26';
  const salarySeason = meta?.salary_season ?? '2026-27';
  const shellHeight = flipped ? size.back : size.front;
  const payroll = denominator?.contextOf?.(player) ?? null;
  const shareLabel = denominator?.shareLabel ?? 'Cap share';
  const share = denominator?.shareOf ? denominator.shareOf(player) : player.cap_pct;

  return (
    <aside
      className={dense ? 'player-card dense' : 'player-card'}
      aria-label={`${player.name} contract detail`}
    >
      <div
        className="pc-flip"
        data-flipped={flipped ? 'true' : 'false'}
        style={shellHeight ? { height: `${shellHeight}px` } : undefined}
      >
        {/* ------------------------------------------------------- front */}
        <section
          className="pc-face pc-front"
          ref={frontRef}
          aria-hidden={flipped || undefined}
          inert={flipped ? '' : undefined}
        >
          <header className="pc-head">
            <Headshot player={player} size={dense ? 60 : 68} />
            <div className="pc-identity">
              <h3>{player.name}</h3>
              <p className="pc-eyebrow">{salarySeason} contract</p>
            </div>
            {onClose && (
              <button
                type="button"
                className="icon-button"
                onClick={onClose}
                aria-label="Close player card"
              >
                <CloseIcon />
              </button>
            )}
          </header>

          <dl className="pc-meta">
            <div>
              <dt>{moved ? 'Contract team' : 'Team'}</dt>
              <dd>{player.team}</dd>
            </div>
            <div>
              <dt>Position</dt>
              <dd>{player.position}</dd>
            </div>
            <div>
              <dt>Age</dt>
              <dd>{player.age}</dd>
            </div>
          </dl>

          {moved && (
            <p className="pc-moved">
              <span className="pc-moved-label">Changed teams</span>
              <span className="pc-moved-text">
                {statsSeason} production earned with {player.stats_team}
              </span>
            </p>
          )}

          <div className={positive ? 'pc-verdict pos' : 'pc-verdict neg'}>
            <p className="pc-verdict-label">{positive ? 'Surplus value' : 'Value shortfall'}</p>
            <p className="pc-verdict-value" title={moneyExact(surplus)}>
              {money(surplus, { signed: true })}
            </p>
            <dl className="pc-ledger">
              <div>
                <dt>Expected</dt>
                <dd title={moneyExact(expected)}>{money(expected)}</dd>
              </div>
              <div>
                <dt>Paid</dt>
                <dd title={moneyExact(player.salary)}>{money(player.salary)}</dd>
              </div>
              <div>
                <dt>{shareLabel}</dt>
                <dd>{pct(share, 1)}</dd>
              </div>
            </dl>
            <p className="pc-verdict-basis">Valued on the {mode.short.toLowerCase()} denominator</p>
          </div>

          <footer className="pc-foot">
            <button
              type="button"
              className="button flip-button"
              ref={frontButtonRef}
              onClick={toggle}
              aria-expanded={flipped}
              aria-controls={backId}
            >
              Contract details
            </button>
            {onCompare && (
              <button type="button" className="button ghost" onClick={() => onCompare(player)}>
                Compare
              </button>
            )}
          </footer>
        </section>

        {/* -------------------------------------------------------- back */}
        <section
          className="pc-face pc-back"
          id={backId}
          ref={backRef}
          aria-label={`${player.name} contract and production detail`}
          aria-hidden={!flipped || undefined}
          inert={flipped ? undefined : ''}
        >
          <header className="pc-head pc-head-back pc-reveal" style={{ '--enter-index': 0 }}>
            <div className="pc-identity">
              <p className="pc-eyebrow">{player.name}</p>
              <h3>The evidence</h3>
            </div>
            <button
              type="button"
              className="button flip-button"
              ref={backButtonRef}
              onClick={toggle}
              aria-expanded={flipped}
              aria-controls={backId}
            >
              Back
            </button>
          </header>

          <section className="pc-block pc-reveal" style={{ '--enter-index': 1 }}>
            <h4 className="pc-block-title">
              Contract
              <span className="pc-block-note">{salarySeason}</span>
            </h4>
            <dl className="stat-grid">
              <div>
                <dt>Type</dt>
                <dd>
                  <ContractChip
                    type={player.contract_type}
                    marketPriced={player.is_market_priced}
                  />
                  {!player.is_market_priced && (
                    <span className="muted block">Outside the regression fit</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Tier</dt>
                <dd>{SALARY_TIERS[player.salary_tier] ?? tidy(player.salary_tier)}</dd>
              </div>
              <div>
                <dt>Cap hit</dt>
                <dd title={moneyExact(player.salary)}>
                  {money(player.salary)} <span className="muted">{pct(player.cap_pct, 1)} of cap</span>
                </dd>
              </div>
              <div>
                <dt>Guaranteed</dt>
                <dd title={moneyExact(player.guaranteed_remaining)}>
                  {money(player.guaranteed_remaining)}
                </dd>
              </div>
              <div>
                <dt>Years left</dt>
                <dd>
                  {player.years_remaining == null ? (
                    <span className="muted">Not published</span>
                  ) : (
                    <>
                      {num(player.years_remaining, 0)}{' '}
                      <span className="muted">
                        {player.years_remaining === 1 ? 'year' : 'years'} after this one
                      </span>
                    </>
                  )}
                </dd>
              </div>
              <div>
                <dt>Trade</dt>
                <dd>
                  {/* The mark is a labelled SVG, not a typographic glyph: a
                      bare ✓/✕ next to a word reads as a stray symbol and
                      carries no meaning the word does not already carry. */}
                  <span className={player.trade_eligible ? 'flag ok' : 'flag blocked'}>
                    {player.trade_eligible ? <CheckIcon /> : <BlockedIcon />}
                    {player.trade_eligible ? 'Eligible' : 'Restricted'}
                  </span>
                  {!player.trade_eligible && player.trade_restriction_reason && (
                    <span className="muted block">
                      {TRADE_RESTRICTIONS[player.trade_restriction_reason] ??
                        tidy(player.trade_restriction_reason)}
                    </span>
                  )}
                  {player.no_trade_clause && <span className="muted block">No-trade clause</span>}
                </dd>
              </div>
            </dl>
          </section>

          {/* Payroll context is the fact a cap share alone hides: an identical
              contract is survivable under the cap and punitive past the second
              apron. It sits directly under Contract because it is a term of
              the deal in every practical sense. */}
          {payroll && (
            <section className="pc-block pc-reveal" style={{ '--enter-index': 2 }}>
              <h4 className="pc-block-title">
                Payroll it sits inside
                <span className="pc-block-note">{payroll.team}</span>
              </h4>
              <PayrollContext context={payroll} seasonLabel={salarySeason} />
            </section>
          )}

          <section className="pc-block pc-reveal" style={{ '--enter-index': 3 }}>
            <h4
              className="pc-block-title"
              title="Three independent WAR estimates, calibrated to a common league total. The band is their disagreement."
            >
              Production
              <span className="pc-block-note">
                {statsSeason}
                {moved ? ` with ${player.stats_team}` : ''}
              </span>
            </h4>
            <WarSpread player={player} width={dense ? 236 : 288} />
          </section>

          <section className="pc-block pc-reveal" style={{ '--enter-index': 4 }}>
            <h4 className="pc-block-title">Box profile</h4>
            <ul className="mini-stats">
              <li>
                <span>True shooting</span>
                <strong>{(player.ts_pct * 100).toFixed(1)}%</strong>
              </li>
              <li>
                <span>Usage</span>
                <strong>{num(player.usg_pct, 1)}%</strong>
              </li>
              <li>
                <span>WS / 48</span>
                <strong>{num(player.ws_per_48, 3)}</strong>
              </li>
              <li>
                <span>Games</span>
                <strong>{player.games}</strong>
              </li>
              <li>
                <span>Minutes</span>
                <strong>{player.minutes.toLocaleString()}</strong>
              </li>
              <li>
                <span>Composite</span>
                <strong>
                  {num(player.composite_score, 1)} <span className="muted">/ 100</span>
                </strong>
              </li>
            </ul>
          </section>
        </section>
      </div>
    </aside>
  );
}

/* ---------------------------------------------------------------------- */
/* Marks. All three are inline SVG rather than typographic glyphs, and all
   three are aria-hidden because the word beside them already says it.       */

const CloseIcon = () => (
  <svg width="11" height="11" viewBox="0 0 10 10" aria-hidden="true" focusable="false">
    <path d="M2 2 L8 8 M8 2 L2 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

const CheckIcon = () => (
  <svg
    className="flag-mark"
    width="11"
    height="11"
    viewBox="0 0 12 12"
    aria-hidden="true"
    focusable="false"
  >
    <path
      d="M2.5 6.4 L4.8 8.7 L9.5 3.6"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const BlockedIcon = () => (
  <svg
    className="flag-mark"
    width="11"
    height="11"
    viewBox="0 0 12 12"
    aria-hidden="true"
    focusable="false"
  >
    <circle cx="6" cy="6" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.5" />
    <path d="M3.2 8.8 L8.8 3.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

/** Snake-cased enum values that have no display label yet. */
function tidy(value) {
  if (!value) return '—';
  const words = String(value).replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}
