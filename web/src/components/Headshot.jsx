import { useEffect, useState } from 'react';
import { headshotUrl } from '../chart/headshotUrl.js';
import { PLACEHOLDER_ICON } from '../chart/useHeadshots.js';

/**
 * Portrait for the HTML surfaces (cards, tables, detail view).
 *
 * An <img> needs no CORS header, so this works against the raw CDN even where
 * the WebGL atlas cannot. Failures fall through: proxy -> canonical
 * `headshot_url` -> silhouette.
 */
export default function Headshot({ player, size = 56, className = '' }) {
  const [stage, setStage] = useState(0);
  useEffect(() => setStage(0), [player?.bbref_slug]);

  if (!player) return null;

  const sources = [headshotUrl(player), player.headshot_url, PLACEHOLDER_ICON.url].filter(Boolean);
  const src = sources[Math.min(stage, sources.length - 1)];

  return (
    <span
      className={`headshot ${className}`}
      style={{ width: size, height: size }}
      data-fallback={stage >= sources.length - 1 ? 'true' : undefined}
    >
      <img
        src={src}
        alt=""
        loading="lazy"
        decoding="async"
        width={size}
        height={size}
        onError={() => setStage((s) => s + 1)}
      />
    </span>
  );
}
