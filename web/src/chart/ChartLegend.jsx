import { COLOR } from '../model/constants.js';

/**
 * The chart's only key. Colour carries exactly one meaning here, so the legend
 * says so out loud rather than presenting two swatches and hoping.
 *
 * The reference lines are NOT in here on purpose: both label themselves on the
 * line, so putting them in the legend would only add a round trip. The one
 * exception is the team-payroll axis, where the model reference is a band
 * rather than a line and the reader is owed the reason in a few words - the
 * fits are no longer the server's league-cap fits (ADR-015).
 */
export default function ChartLegend({ denominator }) {
  const banded = denominator?.fits?.model?.kind === 'band';

  return (
    <div className="chart-legend">
      <span className="legend-label">Colour</span>
      <span className="legend-item pos">
        <i className="swatch ring" style={{ borderColor: COLOR.surplusPos }} aria-hidden="true" />
        Underpaid
      </span>
      <span className="legend-item neg">
        <i className="swatch ring" style={{ borderColor: COLOR.surplusNeg }} aria-hidden="true" />
        Overpaid
      </span>
      {banded && (
        <span className="legend-item legend-refit">
          <i className="swatch band" aria-hidden="true" />
          Model value across the league&rsquo;s payroll range — both lines refit on this axis
        </span>
      )}
      <span className="legend-hint">Zoom in for headshots</span>
    </div>
  );
}
