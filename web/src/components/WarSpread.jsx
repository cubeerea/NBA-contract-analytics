import { COLOR } from '../model/constants.js';
import { num } from '../model/format.js';


/**
 * The three WAR estimates and the band between them.
 *
 * VORP, Win Shares and DARKO are calibrated to a common league total but they
 * still disagree per player, and that disagreement is signal about how
 * confident the valuation is. Averaging it away would be the dishonest move,
 * so `war_spread` is drawn as an explicit uncertainty band with each estimate
 * marked inside it.
 */
export default function WarSpread({ player, width = 260 }) {
  const estimates = [
    { key: 'war_vorp', label: 'VORP', value: player.war_vorp },
    { key: 'war_ws', label: 'Win Shares', value: player.war_ws },
    { key: 'war_darko', label: 'DARKO', value: player.war_darko }
  ].filter((e) => Number.isFinite(e.value));

  if (!estimates.length) return null;

  const values = estimates.map((e) => e.value);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = Math.max(0.6, (hi - lo) * 0.45);
  const min = Math.min(lo - pad, 0);
  const max = hi + pad;
  const span = max - min || 1;

  const H = 34;
  const track = 17;
  const x = (v) => 6 + ((v - min) / span) * (width - 12);

  return (
    <div className="war-spread">
      <div className="war-spread-head">
        <p className="war-headline">
          <span className="war-blended">{num(player.war_blended, 1)}</span>
          <span className="war-unit">blended WAR</span>
        </p>
        <dl className="war-band">
          <div>
            <dt>Spread</dt>
            <dd>{num(player.war_spread, 1)}</dd>
          </div>
          <div>
            <dt>Range</dt>
            <dd>
              {num(lo, 1)} to {num(hi, 1)}
            </dd>
          </div>
        </dl>
      </div>

      <svg width={width} height={H} role="img" aria-label={`WAR estimates: ${estimates
        .map((e) => `${e.label} ${num(e.value, 1)}`)
        .join(', ')}. Blended ${num(player.war_blended, 1)}, spread ${num(player.war_spread, 1)}.`}>
        <line x1={6} x2={width - 6} y1={track} y2={track} stroke={COLOR.grid} strokeWidth={1} />
        {min < 0 && max > 0 && (
          <line x1={x(0)} x2={x(0)} y1={track - 9} y2={track + 9} stroke={COLOR.axis} strokeWidth={1} />
        )}

        {/* the uncertainty band */}
        <rect
          x={x(lo)}
          y={track - 6}
          width={Math.max(2, x(hi) - x(lo))}
          height={12}
          rx={4}
          fill={COLOR.surplusPos}
          opacity={0.16}
        />
        <line x1={x(lo)} x2={x(lo)} y1={track - 7} y2={track + 7} stroke={COLOR.surplusPos} strokeWidth={1.5} opacity={0.55} />
        <line x1={x(hi)} x2={x(hi)} y1={track - 7} y2={track + 7} stroke={COLOR.surplusPos} strokeWidth={1.5} opacity={0.55} />

        {estimates.map((e) => (
          <circle
            key={e.key}
            cx={x(e.value)}
            cy={track}
            r={4}
            fill={COLOR.surface}
            stroke={COLOR.surplusPos}
            strokeWidth={1.6}
          />
        ))}

        <g>
          <circle cx={x(player.war_blended)} cy={track} r={5.5} fill={COLOR.ink} />
          <circle cx={x(player.war_blended)} cy={track} r={2} fill={COLOR.surface} />
        </g>
      </svg>

      {/* Values are listed rather than pinned to the markers: three estimates
          often land close together and their labels would collide. */}
      <ul className="war-estimates" style={{ maxWidth: width }}>
        {estimates.map((e) => (
          <li key={e.key}>
            <span>{e.label}</span>
            <strong>{num(e.value, 1)}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}
