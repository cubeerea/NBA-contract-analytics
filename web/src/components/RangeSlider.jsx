import { useCallback, useRef, useState } from 'react';

/**
 * Dual-handle range slider built on pointer events. No dependency, no
 * overlapping native inputs: one track, two `role="slider"` handles that
 * cannot cross, full keyboard support, and touch parity via pointer capture.
 */
export default function RangeSlider({
  min,
  max,
  value,
  onChange,
  step = 1,
  label = 'Range',
  format = (v) => String(v)
}) {
  const trackRef = useRef(null);
  const handleRefs = [useRef(null), useRef(null)];
  const dragIndex = useRef(null);
  const [dragging, setDragging] = useState(false);

  const span = Math.max(step, max - min);
  const [lo, hi] = value;
  const pct = (v) => ((v - min) / span) * 100;

  const snap = useCallback(
    (raw) => {
      const stepped = min + Math.round((raw - min) / step) * step;
      return Math.min(max, Math.max(min, stepped));
    },
    [min, max, step]
  );

  const commit = useCallback(
    (index, next) => {
      if (index === 0) onChange([Math.min(next, hi), hi]);
      else onChange([lo, Math.max(next, lo)]);
    },
    [lo, hi, onChange]
  );

  const valueAt = useCallback(
    (clientX) => {
      const el = trackRef.current;
      if (!el) return min;
      const rect = el.getBoundingClientRect();
      if (!rect.width) return min;
      return snap(min + ((clientX - rect.left) / rect.width) * span);
    },
    [min, span, snap]
  );

  const onPointerDown = (e) => {
    if (e.button != null && e.button !== 0) return;
    const v = valueAt(e.clientX);
    // Nearest handle wins; on a tie the direction of travel decides, so a
    // collapsed range can always be re-opened either way.
    let index;
    if (lo === hi) index = v < lo ? 0 : 1;
    else index = Math.abs(v - lo) <= Math.abs(v - hi) ? 0 : 1;

    dragIndex.current = index;
    setDragging(true);
    try {
      e.currentTarget.setPointerCapture?.(e.pointerId);
    } catch {
      /* synthetic pointers have no capture target */
    }
    handleRefs[index].current?.focus({ preventScroll: true });
    commit(index, v);
    e.preventDefault();
  };

  const onPointerMove = (e) => {
    if (dragIndex.current == null) return;
    commit(dragIndex.current, valueAt(e.clientX));
  };

  const endDrag = (e) => {
    if (dragIndex.current == null) return;
    dragIndex.current = null;
    setDragging(false);
    try {
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    } catch {
      /* nothing was captured */
    }
  };

  const onKeyDown = (index) => (e) => {
    const cur = index === 0 ? lo : hi;
    const big = Math.max(step, Math.round(span / 10));
    let next = null;
    switch (e.key) {
      case 'ArrowLeft':
      case 'ArrowDown':
        next = cur - step;
        break;
      case 'ArrowRight':
      case 'ArrowUp':
        next = cur + step;
        break;
      case 'PageDown':
        next = cur - big;
        break;
      case 'PageUp':
        next = cur + big;
        break;
      case 'Home':
        next = index === 0 ? min : lo;
        break;
      case 'End':
        next = index === 0 ? hi : max;
        break;
      default:
        return;
    }
    e.preventDefault();
    commit(index, snap(next));
  };

  const handleProps = (index) => {
    const cur = index === 0 ? lo : hi;
    return {
      ref: handleRefs[index],
      role: 'slider',
      tabIndex: 0,
      'aria-label': `${label} ${index === 0 ? 'minimum' : 'maximum'}`,
      'aria-orientation': 'horizontal',
      'aria-valuemin': index === 0 ? min : lo,
      'aria-valuemax': index === 0 ? hi : max,
      'aria-valuenow': cur,
      'aria-valuetext': format(cur),
      className: 'range-handle',
      style: { left: `${pct(cur)}%` },
      onKeyDown: onKeyDown(index)
    };
  };

  return (
    <div className={dragging ? 'range range-dragging' : 'range'}>
      <div
        className="range-track"
        ref={trackRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <span className="range-rail" />
        <span
          className="range-fill"
          style={{ left: `${pct(lo)}%`, right: `${100 - pct(hi)}%` }}
        />
        <span {...handleProps(0)} />
        <span {...handleProps(1)} />
      </div>
      <div className="range-scale" aria-hidden="true">
        <span>{format(min)}</span>
        <span>{format(max)}</span>
      </div>
    </div>
  );
}
