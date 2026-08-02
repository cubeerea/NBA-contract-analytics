/**
 * Text matching for every search box in the app.
 *
 * Roster names carry diacritics that almost nobody types: `Dončić`, `Jokić`,
 * `Šengün`, `Porziņģis`. Comparing raw strings meant a reader who typed
 * "Doncic" got an empty result set and reasonably concluded the player was
 * missing from the dataset. Decomposing to NFD and dropping the combining
 * marks folds both sides onto the same plain-ASCII form, so "Doncic",
 * "doncic" and "Dončić" are the same query.
 *
 * Folding is applied to BOTH sides on purpose - a reader may equally well
 * paste the accented spelling in.
 */

const COMBINING = /[\u0300-\u036f]/g;

export const fold = (value) =>
  String(value ?? '')
    .normalize('NFD')
    .replace(COMBINING, '')
    .toLowerCase();

/** True when the folded query appears in any of the folded fields. */
export function matchesQuery(query, ...fields) {
  const q = fold(query).trim();
  if (!q) return true;
  return fields.some((f) => fold(f).includes(q));
}
