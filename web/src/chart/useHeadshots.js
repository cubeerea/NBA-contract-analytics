import { useEffect, useMemo, useRef, useState } from 'react';
import { ICON_SIZE } from '../model/constants.js';
import { headshotUrl } from './headshotUrl.js';

/**
 * Headshot loading for the IconLayer's dynamically-packed atlas.
 *
 * deck.gl will pack remote URLs into an atlas on its own, but it has no notion
 * of a failed image: a 404 from the NBA CDN would simply leave a hole in the
 * chart. So every URL is probed here first. Resolved URLs are handed to
 * deck.gl to pack; failures - and anything still in flight - fall back to a
 * silhouette placeholder, which is packed exactly once because every player
 * shares its icon id.
 *
 * Loading is lazy (nothing is fetched until the chart is zoomed in far enough
 * for faces to matter) and progressive: results are committed in batches so
 * portraits fill in as they arrive rather than after the last one lands.
 */

const PLACEHOLDER_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="70" viewBox="0 0 96 70">
  <g fill="#c9c8c0">
    <circle cx="48" cy="27" r="15"/>
    <path d="M48 46c-15 0-27 10-30 24h60c-3-14-15-24-30-24z"/>
  </g>
</svg>`;

export const PLACEHOLDER_ICON = {
  id: '__placeholder__',
  url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(PLACEHOLDER_SVG)}`,
  width: ICON_SIZE.width,
  height: ICON_SIZE.height,
  mask: false
};

const CONCURRENCY = 32;
const COMMIT_EVERY = 24;

/** Probe one image. Resolves true on load, false on error/timeout/CORS block. */
function probe(url, timeoutMs = 12000) {
  return new Promise((resolve) => {
    const img = new Image();
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      img.onload = null;
      img.onerror = null;
      resolve(ok);
    };
    const timer = setTimeout(() => done(false), timeoutMs);
    // deck.gl has to upload this into a WebGL texture, so it must be
    // CORS-clean. Probing with the same policy means a cross-origin image
    // fails here rather than silently blanking the atlas later.
    img.crossOrigin = 'anonymous';
    img.onload = () => done(img.naturalWidth > 0);
    img.onerror = () => done(false);
    img.src = url;
  });
}

/**
 * @param {Array} players full player list - never a filtered subset, because
 *   filtering only changes emphasis, not membership
 * @param {boolean} enabled start loading; flipped on by the zoom threshold
 */
export function useHeadshots(players, enabled) {
  const [version, setVersion] = useState(0);
  const store = useRef({ icons: new Map(), failed: 0 });

  useEffect(() => {
    if (!enabled || !players?.length) return undefined;

    let cancelled = false;
    const queue = players
      .map((p) => ({ slug: p.bbref_slug, url: headshotUrl(p) }))
      .filter((p) => p.url && !store.current.icons.has(p.slug));

    if (!queue.length) return undefined;

    let cursor = 0;
    let sinceCommit = 0;
    const commit = () => {
      sinceCommit = 0;
      if (!cancelled) setVersion((v) => v + 1);
    };

    const worker = async () => {
      while (!cancelled) {
        const i = cursor++;
        if (i >= queue.length) return;
        const { slug, url } = queue[i];
        const ok = await probe(url);
        if (cancelled) return;
        if (ok) {
          store.current.icons.set(slug, {
            id: slug,
            url,
            width: ICON_SIZE.width,
            height: ICON_SIZE.height,
            mask: false
          });
        } else {
          store.current.failed += 1;
          store.current.icons.set(slug, PLACEHOLDER_ICON);
        }
        if (++sinceCommit >= COMMIT_EVERY) commit();
      }
    };

    Promise.all(Array.from({ length: CONCURRENCY }, worker)).then(commit);

    return () => {
      cancelled = true;
    };
  }, [enabled, players]);

  return useMemo(() => {
    const { icons, failed } = store.current;
    const total = players?.length ?? 0;
    return {
      icons,
      // `version` is the cheap integer deck.gl's updateTriggers keys off; the
      // Map itself is mutated in place so no per-batch copy is made.
      version,
      loaded: icons.size - failed,
      failed,
      total,
      done: total > 0 && icons.size >= total
    };
  }, [version, players]);
}
