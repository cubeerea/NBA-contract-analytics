import { CONTRACT_TYPES } from '../model/constants.js';

/**
 * Contract type, carried by a WORD in a bordered pill - never by colour, and
 * no longer by an abstract glyph either. Seven shapes read as noise next to a
 * name; the word is the encoding a reader can actually say out loud.
 *
 * The redundant, non-colour channel is the border: solid means the contract
 * sits in the regression fit the league-average line is drawn from, dashed
 * means it is CBA-capped and therefore excluded. That distinction is repeated
 * in words wherever it matters, so nothing rests on the border alone.
 */
export default function ContractChip({ type, compact = false, marketPriced }) {
  const meta = CONTRACT_TYPES[type];
  const label = meta ? (compact ? meta.short : meta.label) : humanise(type);
  // The record's own `is_market_priced` wins when the caller has it: a
  // free-agent deal at the veteran minimum is still outside the fit, and the
  // chip must not say otherwise while the line beneath it does.
  const inFit = marketPriced ?? meta?.marketPriced ?? true;

  return (
    <span
      className={compact ? 'contract-chip compact' : 'contract-chip'}
      title={
        meta?.blurb ? (inFit ? meta.blurb : `${meta.blurb} (outside the regression fit)`) : label
      }
      data-market={inFit ? 'true' : 'false'}
    >
      <span className="chip-text">{label}</span>
    </span>
  );
}

function humanise(value) {
  if (!value) return 'Unknown';
  const words = String(value).replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}
