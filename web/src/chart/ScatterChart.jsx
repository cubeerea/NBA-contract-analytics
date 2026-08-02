import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import DeckGL from '@deck.gl/react';
import { OrthographicView } from '@deck.gl/core';
import { IconLayer, ScatterplotLayer } from '@deck.gl/layers';

import { COLOR, CONTRACT_TYPES, FACE_PX, RGB, WORLD } from '../model/constants.js';
import { money, pct } from '../model/format.js';
import { medianNeighbourDistance } from '../model/valuation.js';
import { makeScales, shareToAxis, GUTTER, clamp, smoothstep, lerp } from './scales.js';
import { useHeadshots, PLACEHOLDER_ICON } from './useHeadshots.js';
import ApronMeter from '../components/ApronMeter.jsx';
import PlotFrame from './PlotFrame.jsx';
import ChartLegend from './ChartLegend.jsx';

const VIEW = new OrthographicView({ id: 'plot', flipY: false, controller: true });
const PAD = 0.06;
const TWEEN_MS = 460;
const DOT_PX = 3.4;

/** Detected once: without WebGL there is no chart, only an explanation. */
const HAS_WEBGL = (() => {
  if (typeof document === 'undefined') return true;
  try {
    const c = document.createElement('canvas');
    return Boolean(c.getContext('webgl2') || c.getContext('webgl'));
  } catch {
    return false;
  }
})();

/**
 * One scatter, two representations of the same 336 marks:
 *
 *   zoomed out   a plain dot per player, coloured by contract surplus
 *   zoomed in    the dot inflates into a ringed headshot
 *
 * Nothing is ever filtered out to make the picture tidy - only the drawing
 * changes. The swap is driven by one continuous number, `lod`, derived from
 * zoom, so it cross-fades rather than flips. Its threshold is computed from the
 * data: the median nearest-neighbour spacing tells us the zoom at which a
 * face-sized icon stops colliding with its neighbour, so faces arrive exactly
 * when they become legible rather than at a hard-coded number. (At the home
 * view that spacing is ~12px against a 57px-wide portrait, which is why dots
 * exist at all.)
 */
export default function ScatterChart({
  players,
  meta,
  mode,
  denominator,
  matching,
  pinnedSlug,
  onPin,
  onHoverPlayer,
  reduceMotion
}) {
  const containerRef = useRef(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [viewState, setViewState] = useState(null);
  const [hoverInfo, setHoverInfo] = useState(null);

  /* ---- measure ------------------------------------------------------- */
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width: Math.round(width), height: Math.round(height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /* ---- geometry ------------------------------------------------------ */
  const shareOf = denominator.shareOf;

  const bounds = useMemo(() => {
    let maxShare = 0;
    for (const p of players) {
      const s = shareOf(p);
      if (s > maxShare) maxShare = s;
    }
    // Axis units are percentage points of the active denominator; both the cap
    // share and the team-payroll share arrive as fractions.
    return { composite: [0, 100], share: [0, Math.ceil((shareToAxis(maxShare) + 1) / 5) * 5] };
  }, [players, shareOf]);

  const scales = useMemo(() => makeScales(bounds, shareOf), [bounds, shareOf]);

  const positioned = useMemo(
    () => players.map((p) => ({ player: p, position: scales.position(p) })),
    [players, scales]
  );

  /**
   * Fit the world into the PLOT RECT (canvas minus the axis gutters), not the
   * canvas: the gutter strips are opaque and would otherwise sit on top of the
   * cheapest contracts, which is exactly where the league is densest.
   */
  const fitZoom = useMemo(() => {
    if (!size.width || !size.height) return 0;
    const plotW = Math.max(1, size.width - GUTTER.left);
    const plotH = Math.max(1, size.height - GUTTER.bottom);
    return Math.log2(
      Math.min(plotW / (WORLD.width * (1 + PAD)), plotH / (WORLD.height * (1 + PAD)))
    );
  }, [size]);

  /**
   * The zoom at which headshots stop overlapping. `spacing` is the median
   * nearest-neighbour distance in world units; a face needs about FACE_PX of
   * screen room, and screen pixels per world unit is 2^zoom.
   */
  const lodBand = useMemo(() => {
    const spacing = medianNeighbourDistance(positioned.map((d) => d.position));
    const high = clamp(Math.log2((FACE_PX * 1.05) / Math.max(spacing, 0.5)), fitZoom + 0.5, fitZoom + 4);
    return { low: high - 1.25, high };
  }, [positioned, fitZoom]);

  /**
   * Where the players actually are. cap_pct is heavily right-skewed - most of
   * the league sits in a band under 10% of the cap - so the geometric centre
   * of the plot is empty space. Zooming in from the home view drifts toward
   * this instead, so the first click always lands on faces.
   */
  const dataCentroid = useMemo(() => {
    if (!positioned.length) return [WORLD.width / 2, WORLD.height / 2];
    const xs = positioned.map((d) => d.position[0]).sort((a, b) => a - b);
    const ys = positioned.map((d) => d.position[1]).sort((a, b) => a - b);
    const mid = Math.floor(xs.length / 2);
    return [xs[mid], ys[mid]];
  }, [positioned]);

  /** Camera target that lands the world centre in the middle of the plot rect. */
  const homeView = useMemo(() => {
    const scale = 2 ** fitZoom;
    return {
      target: [
        WORLD.width / 2 - GUTTER.left / (2 * scale),
        WORLD.height / 2 - GUTTER.bottom / (2 * scale),
        0
      ],
      zoom: fitZoom,
      minZoom: fitZoom - 0.4,
      maxZoom: fitZoom + 4.6
    };
  }, [fitZoom]);

  /**
   * On resize, shift zoom by the change in fit zoom rather than leaving it be.
   * Zoom is absolute (pixels per world unit), so a viewport that halves in
   * width would otherwise leave the user looking at a quarter of what they
   * were looking at before - which on a phone meant most of the league fell
   * outside the frame on first paint.
   */
  const lastFitRef = useRef(null);
  useEffect(() => {
    if (!size.width) return;
    // Bookkeeping stays OUTSIDE the updater: React invokes state updaters twice
    // under StrictMode, and a ref written in there would zero out the delta on
    // the second pass - which silently cancelled the whole adjustment.
    const prevFit = lastFitRef.current;
    lastFitRef.current = fitZoom;
    setViewState((prev) => {
      if (!prev) return homeView;
      const delta = prevFit == null ? 0 : fitZoom - prevFit;
      return {
        ...prev,
        zoom: clamp(prev.zoom + delta, homeView.minZoom, homeView.maxZoom),
        minZoom: homeView.minZoom,
        maxZoom: homeView.maxZoom
      };
    });
  }, [homeView, fitZoom, size.width]);

  const zoom = viewState?.zoom ?? fitZoom;
  const lod = smoothstep((zoom - lodBand.low) / (lodBand.high - lodBand.low));

  /* Choreography of the swap: dots grow, faces fade up inside them. */
  const faceOpacity = smoothstep((lod - 0.3) / 0.7);
  // The ring must be wide enough to contain the portrait: NBA headshots are
  // 1040x760, so a 42px-tall icon is 57px wide and needs a 62px circle.
  const markRadiusPx = lerp(DOT_PX, FACE_PX * 0.74, smoothstep(lod));
  const markLineWidth = lerp(1.1, 1.9, smoothstep(lod));
  const facePx = lerp(FACE_PX * 0.72, FACE_PX, smoothstep(lod));
  const showFaces = faceOpacity > 0.01;

  /* ---- portraits ----------------------------------------------------- */
  const headshots = useHeadshots(players, lod > 0.02);

  /* ---- view helpers -------------------------------------------------- */
  /**
   * Camera moves are tweened here rather than handed to deck's
   * TransitionManager. The viewState is controlled, so every interpolated frame
   * echoes back through onViewStateChange - and an echo that carries no
   * transition props cancels the transition on its first frame. Owning the
   * tween keeps the pan/zoom clamp below authoritative and keeps the
   * dots-to-faces cross-fade continuous, since the fade is a function of zoom.
   */
  const tweenRef = useRef(null);
  useEffect(() => () => cancelAnimationFrame(tweenRef.current), []);

  const applyView = useCallback(
    (next, animate = true) => {
      cancelAnimationFrame(tweenRef.current);
      if (!animate || reduceMotion || !viewState) {
        setViewState(next);
        return;
      }
      const from = { zoom: viewState.zoom, target: viewState.target };
      const t0 = performance.now();
      const step = (now) => {
        const t = clamp((now - t0) / TWEEN_MS, 0, 1);
        const e = smoothstep(t);
        setViewState({
          ...next,
          zoom: lerp(from.zoom, next.zoom, e),
          target: [
            lerp(from.target[0], next.target[0], e),
            lerp(from.target[1], next.target[1], e),
            0
          ]
        });
        if (t < 1) tweenRef.current = requestAnimationFrame(step);
      };
      tweenRef.current = requestAnimationFrame(step);
    },
    [reduceMotion, viewState]
  );

  const onViewStateChange = useCallback(
    ({ viewState: vs }) => {
      cancelAnimationFrame(tweenRef.current);
      // Keep the plot on screen: allow panning only a little past the frame.
      const scale = 2 ** vs.zoom;
      const halfW = size.width / 2 / scale;
      const halfH = size.height / 2 / scale;
      const slackX = Math.max(WORLD.width * 0.12, halfW * 0.25);
      const slackY = Math.max(WORLD.height * 0.12, halfH * 0.25);
      setViewState({
        ...vs,
        target: [
          clamp(vs.target[0], -slackX, WORLD.width + slackX),
          clamp(vs.target[1], -slackY, WORLD.height + slackY),
          0
        ]
      });
    },
    [size]
  );

  const zoomBy = useCallback(
    (delta) => {
      if (!viewState) return;
      const atHome =
        Math.abs(viewState.target[0] - homeView.target[0]) < 2 &&
        Math.abs(viewState.target[1] - homeView.target[1]) < 2;
      applyView({
        ...viewState,
        target: delta > 0 && atHome ? [dataCentroid[0], dataCentroid[1], 0] : viewState.target,
        zoom: clamp(viewState.zoom + delta, homeView.minZoom, homeView.maxZoom)
      });
    },
    [viewState, applyView, homeView, dataCentroid]
  );

  const resetView = useCallback(() => applyView(homeView), [applyView, homeView]);

  /* ---- interaction --------------------------------------------------- */
  const handleHover = useCallback(
    (info) => {
      if (info?.layer?.id === 'players-marks' && info.object) {
        setHoverInfo({ player: info.object.player, x: info.x, y: info.y });
        onHoverPlayer?.(info.object.player);
      } else {
        setHoverInfo(null);
        onHoverPlayer?.(null);
      }
    },
    [onHoverPlayer]
  );

  const handleClick = useCallback(
    (info) => {
      onPin?.(info?.layer?.id === 'players-marks' && info.object ? info.object.player.bbref_slug : null);
    },
    [onPin]
  );

  /* ---- layers -------------------------------------------------------- */
  const isMatch = useCallback((p) => !matching || matching.has(p.bbref_slug), [matching]);

  const layers = useMemo(() => {
    const surplusKey = mode.surplusKey;
    // The dot's fill bleaches to white as the portrait fades up inside it, so
    // the colour survives as a ring rather than disappearing on a hard flip.
    const bleach = smoothstep(faceOpacity);
    const fillOf = (base) => [
      Math.round(lerp(base[0], 255, bleach)),
      Math.round(lerp(base[1], 255, bleach)),
      Math.round(lerp(base[2], 255, bleach))
    ];

    const out = [
      new ScatterplotLayer({
        id: 'players-marks',
        data: positioned,
        getPosition: (d) => d.position,
        radiusUnits: 'pixels',
        getRadius: markRadiusPx,
        stroked: true,
        filled: true,
        lineWidthUnits: 'pixels',
        getLineWidth: (d) => (d.player.bbref_slug === pinnedSlug ? markLineWidth + 1.3 : markLineWidth),
        getFillColor: (d) => {
          const on = isMatch(d.player);
          const base = d.player[surplusKey] >= 0 ? RGB.surplusPos : RGB.surplusNeg;
          return [...fillOf(base), on ? 235 : 55];
        },
        getLineColor: (d) => {
          const on = isMatch(d.player);
          if (d.player.bbref_slug === pinnedSlug) return [...RGB.ink, 255];
          const base = d.player[surplusKey] >= 0 ? RGB.surplusPos : RGB.surplusNeg;
          return [...base, on ? 255 : 60];
        },
        pickable: true,
        autoHighlight: true,
        highlightColor: [9, 9, 11, 38],
        // deck's own transition system, not a DOM one: when a filter changes,
        // the marks that drop out of the match fade rather than snap. Radius
        // is deliberately NOT transitioned - it is a function of live zoom and
        // a tween there would lag the dots-to-faces cross-fade.
        transitions: reduceMotion ? {} : { getLineColor: 220, getFillColor: 220 },
        updateTriggers: {
          getRadius: markRadiusPx,
          getFillColor: [surplusKey, matching, bleach],
          getLineColor: [surplusKey, matching, pinnedSlug],
          getLineWidth: [pinnedSlug, markLineWidth]
        }
      })
    ];

    if (showFaces) {
      out.push(
        new IconLayer({
          id: 'players-faces',
          data: positioned,
          // Dynamically-packed atlas: no iconAtlas / iconMapping is supplied,
          // so deck.gl fetches each `url` and packs it into a texture atlas on
          // the fly. Unresolved and 404'd portraits share one placeholder id,
          // so the silhouette is packed exactly once.
          getIcon: (d) => headshots.icons.get(d.player.bbref_slug) ?? PLACEHOLDER_ICON,
          getPosition: (d) => d.position,
          sizeUnits: 'pixels',
          getSize: facePx,
          getColor: (d) => [255, 255, 255, isMatch(d.player) ? 255 : 70],
          opacity: faceOpacity,
          pickable: false,
          updateTriggers: {
            getIcon: headshots.version,
            getSize: facePx,
            getColor: matching
          }
        })
      );
    }

    /**
     * Pinning has to be unmistakable at any zoom, including when the mark is
     * one dot among 334. A thicker outline alone is not enough, so the pinned
     * player also gets a two-ring reticle drawn ABOVE the faces: a tight ink
     * ring just outside the mark and a wide faint one beyond it.
     */
    const pin = pinnedSlug ? positioned.find((d) => d.player.bbref_slug === pinnedSlug) : null;
    if (pin) {
      out.push(
        new ScatterplotLayer({
          id: 'pin-reticle',
          data: [
            { position: pin.position, r: markRadiusPx + 4.5, w: 1.6, a: 215 },
            { position: pin.position, r: markRadiusPx + 9.5, w: 1, a: 65 }
          ],
          getPosition: (d) => d.position,
          radiusUnits: 'pixels',
          getRadius: (d) => d.r,
          stroked: true,
          filled: false,
          lineWidthUnits: 'pixels',
          getLineWidth: (d) => d.w,
          getLineColor: (d) => [...RGB.ink, d.a],
          pickable: false,
          updateTriggers: { getRadius: markRadiusPx }
        })
      );
    }

    return out;
  }, [
    positioned,
    reduceMotion,
    faceOpacity,
    markRadiusPx,
    markLineWidth,
    facePx,
    showFaces,
    mode.surplusKey,
    matching,
    isMatch,
    pinnedSlug,
    headshots.icons,
    headshots.version
  ]);

  /**
   * Two reference lines, both in axis units (percentage points of the ACTIVE
   * denominator): what the market actually pays for output, and what the $/win
   * model says it is worth. Neither is a literal - `denominator.fits` hands
   * over the server fit on the cap axis and a client refit on the team axis,
   * so nothing stale ever reaches the frame (ADR-015).
   *
   * On the team axis the model reference is a BAND rather than a line, because
   * one dollar figure is a different share of every roster. Its edges are the
   * league's leanest and richest payrolls and its centre is the median team.
   *
   * The vocabulary is shared with the tooltip on purpose: the solid line is
   * "Model value" and so is the expected-salary figure it predicts, so a
   * reader who reads one has already read the other.
   */
  const lines = useMemo(() => {
    const out = [];
    const { market, model } = denominator.fits;
    const asAxis = (fit) => (x) => (fit.intercept + fit.slope * x) * 100;
    if (model) {
      const mid = model.mid ?? model;
      out.push({
        id: 'model',
        label: denominator.modelLabel,
        color: COLOR.inkSecondary,
        dashed: false,
        slope: mid.slope * 100,
        intercept: mid.intercept * 100,
        predict: asAxis(mid),
        band: model.kind === 'band' ? { lo: asAxis(model.lo), hi: asAxis(model.hi) } : null
      });
    }
    if (market) {
      out.push({
        id: 'market',
        label: 'Market price',
        color: COLOR.ink,
        dashed: true,
        slope: market.slope * 100,
        intercept: market.intercept * 100,
        predict: asAxis(market)
      });
    }
    return out;
  }, [denominator]);

  /** Players inside the current viewport - drives the "you have zoomed into
   *  empty space" escape hatch. */
  const visibleCount = useMemo(() => {
    if (!viewState || !size.width) return positioned.length;
    const scale = 2 ** viewState.zoom;
    const halfW = size.width / 2 / scale;
    const halfH = size.height / 2 / scale;
    const [cx, cy] = viewState.target;
    let n = 0;
    for (const d of positioned) {
      if (Math.abs(d.position[0] - cx) <= halfW && Math.abs(d.position[1] - cy) <= halfH) n += 1;
    }
    return n;
  }, [positioned, viewState, size]);

  const ready = size.width > 0 && size.height > 0 && viewState;

  /** Drives the disabled state of Reset - there is nothing to go back to. */
  const atHomeView =
    !viewState ||
    (Math.abs(viewState.zoom - homeView.zoom) < 0.02 &&
      Math.abs(viewState.target[0] - homeView.target[0]) < 1 &&
      Math.abs(viewState.target[1] - homeView.target[1]) < 1);

  if (!HAS_WEBGL) {
    return (
      <div className="chart-shell">
        <div className="chart-canvas no-webgl">
          <div className="state-screen">
            <p className="state-title">This browser has WebGL disabled</p>
            <p className="state-body">
              The same numbers are in the leaderboard, team and comparison views.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="chart-shell">
      <div className="chart-canvas" ref={containerRef}>
        {ready && (
          <>
            <PlotFrame
              width={size.width}
              height={size.height}
              viewState={viewState}
              scales={scales}
              lines={lines}
              dollarsAt={denominator.dollarsAt}
              axisTitle={denominator.axisTitle}
              axisUnit={denominator.axisUnit}
              caption={`Production score in ${meta?.stats_season ?? '2025-26'} against ${
                meta?.salary_season ?? '2026-27'
              } salary, as a ${denominator.axisUnit}.`}
              view={VIEW}
            />
            <DeckGL
              views={VIEW}
              viewState={viewState}
              onViewStateChange={onViewStateChange}
              controller={{
                dragRotate: false,
                touchRotate: false,
                // `smooth` is deliberately off: deck would emit transitioned
                // view states that this controlled component has to echo back,
                // which cancels the transition on its first frame. Raw wheel
                // deltas are already continuous, so the cross-fade stays smooth.
                scrollZoom: { smooth: false, speed: 0.012 },
                doubleClickZoom: true,
                inertia: false
              }}
              layers={layers}
              onHover={handleHover}
              onClick={handleClick}
              getCursor={({ isDragging, isHovering }) =>
                isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'
              }
              style={{ position: 'absolute', inset: 0 }}
            />
          </>
        )}

        {/* One control cluster, not three loose buttons: a segmented zoom
            stepper, and Reset held apart from it because it is a different
            kind of action - and disabled outright when there is nothing to
            reset to, so the frame never offers a dead affordance. */}
        <div className="chart-controls">
          <div className="zoom-cluster" role="group" aria-label="Zoom">
            <button type="button" onClick={() => zoomBy(0.7)} aria-label="Zoom in">
              <PlusIcon />
            </button>
            <button type="button" onClick={() => zoomBy(-0.7)} aria-label="Zoom out">
              <MinusIcon />
            </button>
          </div>
          <button
            type="button"
            className="chart-reset"
            onClick={resetView}
            disabled={atHomeView}
          >
            Reset view
          </button>
        </div>

        {ready && visibleCount === 0 && (
          <div className="empty-viewport">
            <p className="empty-viewport-title">Nothing in frame</p>
            <button type="button" className="button" onClick={resetView}>
              Back to the whole league
            </button>
          </div>
        )}

        {hoverInfo && (
          <HoverReadout
            key={hoverInfo.player.bbref_slug}
            info={hoverInfo}
            mode={mode}
            denominator={denominator}
            bounds={size}
          />
        )}
      </div>

      <ChartLegend denominator={denominator} />
    </div>
  );
}

/* ---------------------------------------------------------------------- */

const PlusIcon = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
    <path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

const MinusIcon = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
    <path d="M3.2 8h9.6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

const READOUT = { width: 244, height: 268 };

/**
 * The primary hover surface, built as a card from the same parts as the player
 * card in the rail: name, a row of LABELLED facts, the payroll the contract
 * sits inside, a rule, the two dollar figures the whole chart is about, and the
 * difference between them.
 *
 * Every fact carries a word above it. This used to be "HOU · PG · 23", which
 * left a bare age reading as a stray number and made the separators do work
 * that only a label can do.
 */
function HoverReadout({ info, mode, denominator, bounds }) {
  const p = info.player;
  const surplus = p[mode.surplusKey];
  const expected = p[mode.expectedKey];
  const positive = surplus >= 0;
  const contract = CONTRACT_TYPES[p.contract_type]?.label ?? null;
  const payroll = denominator.contextOf(p);
  // The record now carries two teams: `team` is who pays the 2026-27 contract,
  // `stats_team` is who the 2025-26 production was earned for. They differ for
  // 61 players, so the tooltip names both rather than quietly showing one.
  const traded = p.stats_team && p.stats_team !== p.team;

  // Flip across the cursor rather than run off the canvas edge.
  const flipX = bounds.width && info.x + READOUT.width + 24 > bounds.width;
  const flipY = bounds.height && info.y + READOUT.height + 24 > bounds.height;

  return (
    <div
      className="hover-readout"
      style={{
        left: flipX ? info.x - READOUT.width - 14 : info.x + 14,
        top: flipY ? Math.max(6, info.y - READOUT.height - 12) : info.y + 14
      }}
    >
      <div className="readout-head">
        <span className="readout-name">{p.name}</span>
        {contract && <span className="readout-contract">{contract}</span>}
      </div>

      <dl className="readout-facts">
        <div>
          <dt>Team</dt>
          <dd>{p.team}</dd>
        </div>
        <div>
          <dt>Position</dt>
          <dd>{p.position}</dd>
        </div>
        <div>
          <dt>Age</dt>
          <dd>{p.age}</dd>
        </div>
        {traded && (
          <div className="readout-span">
            <dt>Played 2025-26 for</dt>
            <dd>{p.stats_team}</dd>
          </div>
        )}
      </dl>

      {/* The payroll this contract actually sits inside. A cap hit is
          survivable under the cap and punitive past the second apron, so the
          rung is shown with the same four-notch meter the team table uses. */}
      {payroll && (
        <div className="readout-payroll">
          <div className="readout-payroll-top">
            <span className="readout-payroll-key">{payroll.team} payroll</span>
            <span className="readout-payroll-value">{money(payroll.payroll, { precision: 1 })}</span>
          </div>
          <ApronMeter status={payroll.status} />
          <p className="readout-payroll-note">
            {pct(payroll.share, 1)} of it is this contract. {payroll.summary}.
          </p>
        </div>
      )}

      <dl className="readout-money">
        <div>
          <dt>Paid</dt>
          <dd>{money(p.salary)}</dd>
        </div>
        <div>
          <dt>Model value</dt>
          <dd>{money(expected)}</dd>
        </div>
      </dl>

      <div className={`readout-surplus ${positive ? 'pos' : 'neg'}`}>
        <span className="readout-surplus-label">{positive ? 'Underpaid by' : 'Overpaid by'}</span>
        <span className="readout-surplus-value">{money(Math.abs(surplus))}</span>
      </div>
    </div>
  );
}
