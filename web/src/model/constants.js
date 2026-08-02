/**
 * Design tokens and domain vocabulary.
 *
 * The palette is validated for CVD separation and contrast against a pure
 * white surface (the background is white by explicit requirement, so every
 * contrast figure below is measured against #ffffff, not an off-white).
 *
 * Rules this file encodes:
 *   - Diverging blue <-> red for surplus polarity, neutral gray at zero.
 *   - One sequential blue ramp for density (magnitude), light -> dark.
 *   - Contract type is NEVER encoded by colour alone. The primary encoding is
 *     the WORD (`label`, or `short` where space is tight); the redundant
 *     channel is border style, not hue. `glyph` is retained for the compact
 *     filter menu and the chart legend, where a mark sits beside the word.
 */

export const MODES = {
  replacement: {
    id: 'replacement',
    label: 'Replacement-adjusted',
    short: 'Replacement level',
    surplusKey: 'surplus_replacement',
    expectedKey: 'expected_salary_replacement',
    teamSurplusKey: 'total_surplus_replacement',
    formula: 'Cap / 32.8',
    assumption: 'A replacement-level roster wins about 8 games, not zero.',
    detail:
      'Divides the cap by 32.8 wins instead of 41, because a roster of freely available players still wins roughly 8-15 games. This is the more defensible denominator and the default.'
  },
  naive: {
    id: 'naive',
    label: 'Naive',
    short: 'Naive',
    surplusKey: 'surplus_naive',
    expectedKey: 'expected_salary_naive',
    teamSurplusKey: 'total_surplus_naive',
    formula: 'Cap / 41',
    assumption: 'A replacement-level roster wins zero games.',
    detail:
      'Divides the full cap across all 41 wins an average team collects. It quietly assumes a roster of replacement players would win nothing, which makes a win look cheaper and therefore makes almost everyone look overpaid - by roughly 20% across the board.'
  }
};

export const MODE_ORDER = ['replacement', 'naive'];

/** Positions, in floor order rather than alphabetical. */
export const POSITIONS = ['PG', 'SG', 'SF', 'PF', 'C'];

/**
 * Contract types. `label` is the primary encoding and `short` is the same
 * word trimmed for tight surfaces - both are words, never abbreviations a
 * reader has to decode. Colour is deliberately absent so no meaning ever
 * rests on hue alone.
 *
 * `glyph` is kept because the filter menu and the chart legend still draw a
 * mark beside the word; it is never shown without its label.
 *
 * `marketPriced` mirrors `is_market_priced` on the record - those are the
 * contracts the league-average line is fit on.
 */
export const CONTRACT_TYPES = {
  max: { label: 'Max', short: 'Max', glyph: '◆', marketPriced: true, blurb: 'Maximum-salary deal' },
  extension: { label: 'Extension', short: 'Extension', glyph: '▲', marketPriced: true, blurb: 'Veteran extension' },
  free_agent: { label: 'Free agent', short: 'Free agent', glyph: '●', marketPriced: true, blurb: 'Signed on the open market' },
  mle: { label: 'Mid-level', short: 'Mid-level', glyph: '■', marketPriced: true, blurb: 'Mid-level exception' },
  rookie_scale: { label: 'Rookie scale', short: 'Rookie', glyph: '△', marketPriced: false, blurb: 'CBA-capped rookie contract' },
  minimum: { label: 'Minimum', short: 'Minimum', glyph: '○', marketPriced: false, blurb: 'Veteran minimum' },
  two_way: { label: 'Two-way', short: 'Two-way', glyph: '□', marketPriced: false, blurb: 'Two-way G League contract' }
};

/**
 * `salary_tier` on the record is a coarser bucket than `contract_type` (it
 * includes designated-veteran deals, which are a max variant). Displayed as
 * words on the card's contract face.
 */
export const SALARY_TIERS = {
  standard: 'Standard',
  rookie_scale: 'Rookie scale',
  minimum: 'Minimum',
  max: 'Max',
  mle: 'Mid-level',
  designated_veteran: 'Designated veteran'
};

export const CONTRACT_ORDER = [
  'max',
  'extension',
  'free_agent',
  'mle',
  'rookie_scale',
  'minimum',
  'two_way'
];

export const TRADE_RESTRICTIONS = {
  no_trade_clause: 'No-trade clause',
  two_way_contract: 'Two-way contract',
  recently_signed: 'Recently signed',
  poison_pill_provision: 'Poison pill provision'
};

/* ---------------------------------------------------------------------- */
/* Colour                                                                  */
/* ---------------------------------------------------------------------- */

/**
 * Diverging pair. Blue = the team is getting more than it pays for.
 *
 * These MUST stay in step with the CSS tokens in src/styles/tokens.css —
 * deck.gl reads its colours from this file as RGB arrays and the chart would
 * otherwise drift away from the surrounding UI. The neutrals below are the
 * same cool-neutral (zinc) ramp the stylesheet uses:
 *   grid   = --n-200  #e4e4e7      inkMuted = --n-500 #71717a
 *   axis   = --n-400  #a1a1aa      inkSecondary = --n-600 #52525b
 *   neutral= --n-300  #d4d4d8      ink = --n-950 #09090b
 * `surplusPos`/`surplusNeg` are the MARK colours (--pos-mark / --neg-mark);
 * `surplusPosInk`/`surplusNegInk` are the darker text-safe siblings
 * (--pos-ink / --neg-ink, >= 5:1 on white) for any label drawn in colour.
 */
export const COLOR = {
  surplusPos: '#2a78d6',
  surplusNeg: '#e34948',
  surplusPosInk: '#1f68c4',
  surplusNegInk: '#c62f2e',
  neutral: '#d4d4d8',
  ink: '#09090b',
  inkSecondary: '#52525b',
  inkMuted: '#71717a',
  grid: '#e4e4e7',
  axis: '#a1a1aa',
  surface: '#ffffff'
};

export const RGB = {
  surplusPos: [42, 120, 214],
  surplusNeg: [227, 73, 72],
  neutral: [212, 212, 216],
  ink: [9, 9, 11],
  white: [255, 255, 255],
  ghost: [161, 161, 170]
};

/* ---------------------------------------------------------------------- */
/* Chart geometry                                                          */
/* ---------------------------------------------------------------------- */

/** The plot lives in a fixed cartesian world; scales map data into it. */
export const WORLD = { width: 1000, height: 640 };

/** Rendered height of a headshot at full zoom, in screen pixels. */
export const FACE_PX = 42;

/** Icons are packed into the atlas at this resolution. */
export const ICON_SIZE = { width: 96, height: 70 };
