import { WORLD } from '../model/constants.js';

/**
 * Data <-> world-space mapping for the orthographic plot.
 *
 * The chart is drawn in a fixed 1000x640 cartesian world so icon spacing and
 * the zoom thresholds are expressed in one stable coordinate system regardless
 * of viewport size.
 *
 * Axis units: x is the composite score (0-100), y is PERCENTAGE POINTS of the
 * ACTIVE denominator - the league cap by default, the player's own team payroll
 * when that mode is on (ADR-015). Both are supplied as fractions (0.0743), so
 * everything entering the plot goes through `shareToAxis` exactly once.
 */

export const shareToAxis = (fraction) => (Number.isFinite(fraction) ? fraction * 100 : 0);

/**
 * Axis gutters, in screen pixels. Shared with the chart because the home view
 * has to fit the world into the plot rect, not the whole canvas - otherwise the
 * cheapest contracts render underneath the x-axis label strip.
 *
 * Both gutters are sized for TWO tiers of text: the y gutter carries a
 * dual-unit tick (cap share over the dollar figure) plus a two-line axis
 * title, the x gutter carries a tick row plus its own two-line title.
 */
export const GUTTER = { left: 84, bottom: 54 };

/**
 * `shareOf` returns the player's salary as a FRACTION of the active
 * denominator. It defaults to `cap_pct` so a caller with no denominator in
 * hand still gets the league-cap axis.
 */
export function makeScales(bounds, shareOf = (p) => p.cap_pct) {
  const [x0, x1] = bounds?.composite ?? [0, 100];
  const [y0, y1] = bounds?.share ?? bounds?.capPct ?? [0, 40];
  const xSpan = x1 - x0 || 1;
  const ySpan = y1 - y0 || 1;

  return {
    domain: { x: [x0, x1], y: [y0, y1] },
    toWorldX: (composite) => ((composite - x0) / xSpan) * WORLD.width,
    toWorldY: (shareAxis) => ((shareAxis - y0) / ySpan) * WORLD.height,
    toDataX: (wx) => x0 + (wx / WORLD.width) * xSpan,
    toDataY: (wy) => y0 + (wy / WORLD.height) * ySpan,
    position: (player) => [
      ((player.composite_score - x0) / xSpan) * WORLD.width,
      ((shareToAxis(shareOf(player)) - y0) / ySpan) * WORLD.height
    ]
  };
}

/**
 * Human-friendly tick values across [lo, hi], targeting ~`count` steps.
 * Returns `{values, step, decimals}` - `decimals` is how many the step needs,
 * so a zoomed-in axis reads "8.4%" rather than "8.400000000000001%".
 */
export function ticks(lo, hi, count = 6) {
  const span = hi - lo;
  if (!(span > 0)) return { values: [lo], step: 1, decimals: 0 };
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm >= 7.5 ? 10 : norm >= 3.5 ? 5 : norm >= 1.5 ? 2 : 1) * mag;
  const decimals = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  const start = Math.ceil(lo / step) * step;
  const values = [];
  for (let v = start; v <= hi + step * 1e-6; v += step) {
    values.push(Number((Math.round(v / step) * step).toFixed(decimals)));
  }
  return { values, step, decimals };
}

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Hermite smoothstep - used for the dots-to-faces cross-fade. */
export const smoothstep = (t) => {
  const x = clamp(t, 0, 1);
  return x * x * (3 - 2 * x);
};

export const lerp = (a, b, t) => a + (b - a) * t;
