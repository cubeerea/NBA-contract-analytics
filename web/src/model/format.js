/** Display formatting. Every number the user reads passes through here. */

export function money(value, { signed = false, precision } = {}) {
  if (value == null || Number.isNaN(value)) return '—';
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : signed ? '+' : '';
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) {
    const p = precision ?? (abs >= 1e8 ? 0 : 1);
    return `${sign}$${(abs / 1e6).toFixed(p)}M`;
  }
  if (abs >= 1e3) return `${sign}$${Math.round(abs / 1e3)}K`;
  return `${sign}$${Math.round(abs)}`;
}

export function moneyExact(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return `$${Math.round(value).toLocaleString('en-US')}`;
}

// Takes a FRACTION (0.379), renders a percentage ("37.9%"). Every call site
// passes `cap_pct`, which players.json stores as salary / salary_cap. TS% and
// USG% deliberately do not route through here — usg_pct already arrives scaled.
export const pct = (value, digits = 1) =>
  value == null || Number.isNaN(value) ? '—' : `${(value * 100).toFixed(digits)}%`;

export const num = (value, digits = 1) =>
  value == null || Number.isNaN(value) ? '—' : value.toFixed(digits);

export const signedNum = (value, digits = 1) => {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}`;
};

export const ordinal = (n) => {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
};

export function timeAgo(iso) {
  if (!iso) return '';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return '';
  return then.toLocaleString('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC'
  });
}
