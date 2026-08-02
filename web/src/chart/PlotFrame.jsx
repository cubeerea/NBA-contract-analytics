import { useId, useMemo } from 'react';
import { COLOR } from '../model/constants.js';
import { GUTTER, ticks } from './scales.js';

const GUTTER_LEFT = GUTTER.left;
const GUTTER_BOTTOM = GUTTER.bottom;

/**
 * Axes, gridlines and the two reference lines, drawn as SVG so they stay
 * hairline-crisp at every zoom instead of being rasterised into the WebGL
 * canvas. The grid sits behind the deck.gl canvas; the lines and the tick
 * labels sit in front of it.
 *
 * Y ticks are dual-unit ONLY when the denominator is shared across the league:
 * share of the cap on top, the dollar figure it works out to underneath, so
 * "% of cap" never needs translating in the reader's head. On the team-payroll
 * axis the same percentage is a different dollar figure on every roster, so
 * `dollarsAt` arrives null and the sub-tick is dropped rather than printed
 * wrong. The cap comes from the dataset, never a literal.
 *
 * A reference line may carry a `band` - two boundary predictors - in which case
 * the region between them is filled behind the line. That is how the model
 * reference survives the team-payroll axis: one dollar figure is a different
 * share on every roster, so it is honestly a wedge, not a line.
 */
export default function PlotFrame({
  width,
  height,
  viewState,
  scales,
  lines,
  dollarsAt,
  axisTitle,
  axisUnit,
  caption,
  view
}) {
  const clipId = useId();
  const viewport = useMemo(() => {
    try {
      return view.makeViewport({ width, height, viewState });
    } catch {
      return null;
    }
  }, [view, width, height, viewState]);

  const geometry = useMemo(() => {
    if (!viewport) return null;
    const project = (dx, dy) => viewport.project([scales.toWorldX(dx), scales.toWorldY(dy), 0]);
    const plotBottomPx = height - GUTTER_BOTTOM;

    /**
     * Ticks are generated over the VISIBLE data range, not the full domain.
     * Generating them over the domain means that once you zoom past a couple
     * of steps no round number is on screen and the axes silently go blank -
     * exactly when a reader most needs to know where they are.
     */
    const topLeft = viewport.unproject([GUTTER_LEFT, 0]);
    const bottomRight = viewport.unproject([width, plotBottomPx]);
    const clampTo = ([lo, hi], a, b) => [Math.max(lo, Math.min(a, b)), Math.min(hi, Math.max(a, b))];
    const [xLo, xHi] = clampTo(scales.domain.x, scales.toDataX(topLeft[0]), scales.toDataX(bottomRight[0]));
    const [yLo, yHi] = clampTo(scales.domain.y, scales.toDataY(topLeft[1]), scales.toDataY(bottomRight[1]));

    /**
     * Tick density is a function of the room available, not a constant. A y
     * tick is two lines tall (share of cap over dollars), so it needs ~110px
     * of vertical room before the pairs start reading as one grey block; an x
     * tick needs ~130px because the labels sit side by side.
     */
    const plotW = Math.max(1, width - GUTTER_LEFT);
    const density = (px, per, lo, hi) => Math.max(lo, Math.min(hi, Math.round(px / per)));
    const xScale = ticks(xLo, xHi, density(plotW, 130, 4, 8));
    const yScale = ticks(yLo, yHi, density(plotBottomPx, 110, 3, 6));
    const xTicks = xScale.values
      .map((v) => ({ v, label: v.toFixed(xScale.decimals), x: project(v, 0)[0] }))
      .filter((t) => t.x > GUTTER_LEFT - 12 && t.x < width + 12);

    const yTicks = yScale.values
      .map((v) => ({
        v,
        label: `${v.toFixed(yScale.decimals)}%`,
        dollars: dollarsAt ? dollarsAt(v) : null,
        y: project(0, v)[1]
      }))
      .filter((t) => t.y > -12 && t.y < plotBottomPx + 12);

    /**
     * Each reference line is straight, so it is drawn from its two clipped
     * endpoints rather than sampled: the intercepts sit below the plot floor
     * (nobody is paid a negative salary), so a line only enters the frame above
     * a certain score, and at high zoom a sampled version would thin to nothing.
     */
    const at = [0.36, 0.66];
    const fitted = (lines ?? []).map((ln, i) => {
      const [dyLo, dyHi] = scales.domain.y;
      let a = Math.max(xLo, scales.domain.x[0]);
      let b = Math.min(xHi, scales.domain.x[1]);
      if (Math.abs(ln.slope) > 1e-9) {
        const xAt = (y) => (y - ln.intercept) / ln.slope;
        const [c, d] = [xAt(dyLo), xAt(dyHi)].sort((m, n) => m - n);
        a = Math.max(a, c);
        b = Math.min(b, d);
      } else if (ln.intercept < dyLo || ln.intercept > dyHi) {
        a = b = NaN;
      }
      if (!Number.isFinite(a) || !Number.isFinite(b) || !(b > a)) return null;
      const p0 = project(a, ln.predict(a));
      const p1 = project(b, ln.predict(b));

      /**
       * A banded reference is a wedge, not a line: its two edges are drawn
       * over the whole visible x range and clipped to the plot rect, because
       * clipping them analytically the way the centre line is clipped would
       * lop off the half of the wedge that leaves the y domain.
       */
      let band = null;
      if (ln.band) {
        const ba = Math.max(xLo, scales.domain.x[0]);
        const bb = Math.min(xHi, scales.domain.x[1]);
        if (bb > ba) {
          const corners = [
            project(ba, ln.band.lo(ba)),
            project(bb, ln.band.lo(bb)),
            project(bb, ln.band.hi(bb)),
            project(ba, ln.band.hi(ba))
          ];
          band = corners.map((pt) => `${pt[0].toFixed(1)},${pt[1].toFixed(1)}`).join(' ');
        }
      }

      /**
       * The label rides ON the line rather than floating beside it: a pill
       * that carries a sample of the stroke plus the words, rotated to the
       * line's own angle and nudged clear of it. That removes the round trip
       * to a legend - dashed-versus-solid is settled inside the label itself.
       * The fraction along the segment is fixed per line so the two never
       * collide and neither lands under the zoom cluster.
       */
      const t = at[i % at.length];
      let angle = (Math.atan2(p1[1] - p0[1], p1[0] - p0[0]) * 180) / Math.PI;
      if (angle > 90) angle -= 180;
      if (angle < -90) angle += 180;
      const pillW = Math.round(ln.label.length * 6.1) + 46;
      const halfW = pillW / 2;
      return {
        id: ln.id,
        label: ln.label,
        dashed: ln.dashed,
        color: ln.color,
        angle,
        pillW,
        band,
        d: `M${p0[0].toFixed(1)},${p0[1].toFixed(1)}L${p1[0].toFixed(1)},${p1[1].toFixed(1)}`,
        anchor: [
          Math.min(Math.max(p0[0] + (p1[0] - p0[0]) * t, GUTTER_LEFT + halfW + 6), width - halfW - 6),
          // The pill rides 24px above the anchor, so the top clamp has to
          // clear that plus a little air or it gets sliced by the frame.
          Math.min(Math.max(p0[1] + (p1[1] - p0[1]) * t, 34), plotBottomPx - 20)
        ]
      };
    });

    return { xTicks, yTicks, fitted: fitted.filter(Boolean) };
  }, [viewport, scales, lines, dollarsAt, width, height]);

  if (!geometry) return null;
  const { xTicks, yTicks, fitted } = geometry;
  const plotBottom = height - GUTTER_BOTTOM;

  return (
    <>
      {/* Structure, not data: the grid is drawn a full step lighter than the
          axis spines and the vertical set lighter still, so the marks read as
          the only thing in the frame with weight. */}
      <svg className="plot-grid" width={width} height={height} aria-hidden="true">
        {yTicks.map((t) => (
          <line
            key={`gy${t.v}`}
            x1={GUTTER_LEFT}
            x2={width}
            y1={t.y}
            y2={t.y}
            stroke={COLOR.grid}
            strokeOpacity={t.v === 0 ? 1 : 0.85}
            strokeWidth={1}
            shapeRendering="crispEdges"
          />
        ))}
        {xTicks.map((t) => (
          <line
            key={`gx${t.v}`}
            x1={t.x}
            x2={t.x}
            y1={0}
            y2={plotBottom}
            stroke={COLOR.grid}
            strokeOpacity={0.65}
            strokeWidth={1}
            shapeRendering="crispEdges"
          />
        ))}
      </svg>

      <svg className="plot-overlay" width={width} height={height}>
        <title>{caption}</title>
        <defs>
          <clipPath id={clipId}>
            <rect x={GUTTER_LEFT} y={0} width={Math.max(0, width - GUTTER_LEFT)} height={plotBottom} />
          </clipPath>
        </defs>

        {/* Bands sit under every line so no stroke is muddied by the fill. */}
        <g clipPath={`url(#${clipId})`}>
          {fitted.map((ln) =>
            ln.band ? <polygon key={`band-${ln.id}`} className="fit-band" points={ln.band} /> : null
          )}
        </g>

        {fitted.map((ln) => (
          <g key={ln.id} className="fit-line" data-line={ln.id}>
            <path d={ln.d} stroke="#ffffff" strokeWidth={5} fill="none" strokeLinecap="round" opacity={0.85} />
            <path
              d={ln.d}
              stroke={ln.color}
              strokeWidth={1.75}
              strokeDasharray={ln.dashed ? '7 5' : undefined}
              fill="none"
              strokeLinecap="round"
            />
            <g transform={`translate(${ln.anchor[0]},${ln.anchor[1]}) rotate(${ln.angle})`}>
              <rect
                className="fit-pill"
                x={-ln.pillW / 2}
                y={-24}
                width={ln.pillW}
                height={19}
                rx={9.5}
              />
              <line
                x1={-ln.pillW / 2 + 10}
                x2={-ln.pillW / 2 + 26}
                y1={-14.5}
                y2={-14.5}
                stroke={ln.color}
                strokeWidth={1.75}
                strokeLinecap="round"
                strokeDasharray={ln.dashed ? '5 3.5' : undefined}
              />
              <text
                x={-ln.pillW / 2 + 32}
                y={-10.5}
                textAnchor="start"
                className="fit-label"
                fill={ln.color}
              >
                {ln.label}
              </text>
            </g>
          </g>
        ))}

        {/* gutters: keep tick labels legible over dense marks */}
        <rect x={0} y={plotBottom} width={width} height={GUTTER_BOTTOM} fill={COLOR.surface} />
        <rect x={0} y={0} width={GUTTER_LEFT} height={height} fill={COLOR.surface} />
        <line x1={GUTTER_LEFT} x2={width} y1={plotBottom} y2={plotBottom} stroke={COLOR.axis} strokeWidth={1} />
        <line x1={GUTTER_LEFT} x2={GUTTER_LEFT} y1={0} y2={plotBottom} stroke={COLOR.axis} strokeWidth={1} />

        {xTicks.map((t) => (
          <g key={`tx${t.v}`}>
            <line
              x1={t.x}
              x2={t.x}
              y1={plotBottom}
              y2={plotBottom + 4}
              stroke={COLOR.axis}
              strokeWidth={1}
              shapeRendering="crispEdges"
            />
            <text x={t.x} y={plotBottom + 17} textAnchor="middle" className="tick">
              {t.label}
            </text>
          </g>
        ))}

        {/* Dual-unit y tick: the share of the cap, and directly under it the
            dollar figure that share works out to. The pair is centred on its
            gridline so the two lines read as one label, not two ticks. */}
        {yTicks.map((t) => (
          <g key={`ty${t.v}`}>
            <line
              x1={GUTTER_LEFT - 4}
              x2={GUTTER_LEFT}
              y1={t.y}
              y2={t.y}
              stroke={COLOR.axis}
              strokeWidth={1}
              shapeRendering="crispEdges"
            />
            <text x={GUTTER_LEFT - 10} y={t.y - 1} textAnchor="end" className="tick">
              {t.label}
            </text>
            {t.dollars && (
              <text x={GUTTER_LEFT - 10} y={t.y + 10} textAnchor="end" className="tick tick-alt">
                {t.dollars}
              </text>
            )}
          </g>
        ))}

        {/* Axis titles are two-tier: the quantity on top, its unit underneath.
            Neither line needs a separator character to be read correctly. */}
        <g transform={`translate(${GUTTER_LEFT + (width - GUTTER_LEFT) / 2}, ${plotBottom})`}>
          <text y={32} textAnchor="middle" className="axis-title">
            2025-26 production score
          </text>
          <text y={45} textAnchor="middle" className="axis-unit">
            0 to 100, higher is better
          </text>
        </g>
        <g transform={`translate(0, ${plotBottom / 2}) rotate(-90)`}>
          <text y={15} textAnchor="middle" className="axis-title">
            {axisTitle}
          </text>
          <text y={28} textAnchor="middle" className="axis-unit">
            {axisUnit}
          </text>
        </g>
      </svg>
    </>
  );
}
