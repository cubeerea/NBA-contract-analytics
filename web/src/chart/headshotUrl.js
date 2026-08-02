/**
 * Where portraits are actually fetched from.
 *
 * The record's `headshot_url` is the canonical source of truth and the only
 * thing the ETL has to emit. But `cdn.nba.com` sends no CORS header, and a
 * cross-origin image cannot be uploaded into a WebGL texture - which is what
 * deck.gl's IconLayer atlas has to do. So the URL is rewritten to a
 * same-origin path when one is configured:
 *
 *   dev / preview  -> `/headshot/{id}.png`, proxied by Vite (see vite.config.js)
 *   production     -> VITE_HEADSHOT_BASE, e.g. `./data/headshots` if the
 *                     nightly ETL mirrors the images next to the JSON
 *
 * With neither, the raw CDN URL is used; it will fail CORS and every player
 * falls back to the silhouette placeholder. That is a degraded chart, not a
 * broken one.
 */

const configured = import.meta.env.VITE_HEADSHOT_BASE;
export const HEADSHOT_BASE =
  configured !== undefined && configured !== ''
    ? configured.replace(/\/$/, '')
    : import.meta.env.DEV
      ? '/headshot'
      : null;

export const usingProxy = HEADSHOT_BASE !== null;

export function headshotUrl(player) {
  if (!player) return null;
  if (!HEADSHOT_BASE) return player.headshot_url ?? null;
  if (player.nba_player_id == null) return player.headshot_url ?? null;
  return `${HEADSHOT_BASE}/${player.nba_player_id}.png`;
}
