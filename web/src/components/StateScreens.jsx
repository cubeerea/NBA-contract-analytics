/** Loading, error and empty states. Every fetch surface has all three. */

export function LoadingScreen({ label = 'Loading league data' }) {
  return (
    <div className="state-screen is-loading" role="status" aria-live="polite">
      <div className="skeleton-chart" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
        <span />
      </div>
      <p className="state-title">{label}</p>
      <p className="state-body">Contracts, production and team payrolls.</p>
    </div>
  );
}

export function ErrorScreen({ error, onRetry, dataBase }) {
  return (
    <div className="state-screen is-error" role="alert">
      <p className="state-badge">Dataset unavailable</p>
      <p className="state-title">{error?.message ?? 'Unknown error'}</p>
      <p className="state-body">
        Nothing was served from <code>{dataBase}/</code>.
      </p>
      {onRetry && (
        <button type="button" className="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyScreen({ title, body, action }) {
  return (
    <div className="state-screen is-empty" role="status">
      <p className="state-title">{title}</p>
      {body && <p className="state-body">{body}</p>}
      {action}
    </div>
  );
}

export function WarningBanner({ warnings }) {
  if (!warnings?.length) return null;
  return (
    <div className="warning-banner" role="status">
      <span className="warning-icon" aria-hidden="true">
        !
      </span>
      <div>
        {warnings.map((w) => (
          <p key={w}>{w}</p>
        ))}
      </div>
    </div>
  );
}
