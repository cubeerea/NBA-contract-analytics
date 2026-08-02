import { useEffect, useMemo, useRef, useState } from 'react';

/**
 * Popover multi-select. Typeahead filtering kicks in once the option list is
 * long enough to be worth searching; optional group headers keep a 30-item
 * list scannable.
 */
export default function FacetSelect({
  label,
  options,
  selected,
  onToggle,
  onClear,
  searchable = options.length > 8,
  columns = 1
}) {
  const [open, setOpen] = useState(false);
  const [alignRight, setAlignRight] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef(null);
  const searchRef = useRef(null);
  const triggerRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      triggerRef.current?.focus();
      setOpen(false);
    };
    document.addEventListener('pointerdown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      setQuery('');
      return;
    }
    // Flip the panel to the right edge rather than let it run off screen.
    const rect = ref.current?.getBoundingClientRect();
    if (rect) setAlignRight(rect.left + 276 > window.innerWidth - 12);
    if (searchable) searchRef.current?.focus();
  }, [open, searchable]);

  const q = query.trim().toLowerCase();
  const visible = useMemo(
    () =>
      q
        ? options.filter(
            (o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)
          )
        : options,
    [options, q]
  );

  const grouped = useMemo(() => {
    if (q || !visible.some((o) => o.group)) return [[null, visible]];
    const map = new Map();
    for (const o of visible) {
      const key = o.group ?? '';
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(o);
    }
    return [...map.entries()];
  }, [visible, q]);

  const count = selected.length;

  return (
    <div className="field facet" ref={ref}>
      <span className="field-label">{label}</span>
      <button
        type="button"
        ref={triggerRef}
        className={count ? 'facet-trigger has-value' : 'facet-trigger'}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="facet-trigger-text">{count ? `${count} selected` : 'Any'}</span>
        <span className="facet-caret" aria-hidden="true" />
      </button>

      {open && (
        <div className={alignRight ? 'facet-panel align-right' : 'facet-panel'}>
          {searchable && (
            <input
              ref={searchRef}
              type="search"
              className="facet-search"
              value={query}
              placeholder={`Filter ${label.toLowerCase()}`}
              aria-label={`Filter ${label.toLowerCase()}`}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
          <div
            className="facet-options"
            role="listbox"
            aria-multiselectable="true"
            aria-label={label}
          >
            {grouped.map(([group, items]) => (
              <div className="facet-group" key={group ?? '_'}>
                {group ? <div className="facet-group-label">{group}</div> : null}
                <div
                  className="facet-group-items"
                  style={
                    columns > 1 ? { gridTemplateColumns: `repeat(${columns}, 1fr)` } : undefined
                  }
                >
                  {items.map((o) => {
                    const active = selected.includes(o.value);
                    return (
                      <button
                        key={o.value}
                        type="button"
                        role="option"
                        aria-selected={active}
                        className={active ? 'facet-option active' : 'facet-option'}
                        onClick={() => onToggle(o.value)}
                      >
                        <span className="facet-mark" aria-hidden="true" />
                        {o.glyph ? (
                          <span className="glyph" aria-hidden="true">
                            {o.glyph}
                          </span>
                        ) : null}
                        <span className="facet-option-text">{o.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
            {!visible.length && <p className="facet-empty">No match</p>}
          </div>
          {count > 0 && (
            <button type="button" className="facet-clear" onClick={onClear}>
              Clear
            </button>
          )}
        </div>
      )}
    </div>
  );
}
