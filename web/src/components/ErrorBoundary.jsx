import { Component } from 'react';

/**
 * A render fault in one view should not blank the whole dashboard. Scoped
 * around the view slot so the header, the $/win toggle and the tabs stay
 * usable, and switching tabs recovers.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidUpdate(prevProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="state-screen" role="alert">
        <p className="state-badge error">This view failed to render</p>
        <p className="state-title">{String(this.state.error.message ?? this.state.error)}</p>
        <p className="state-body">
          The other views are unaffected — switch tabs to keep going. If this persists, the data
          file probably does not match the schema in <code>etl/schema.py</code>.
        </p>
        <button type="button" className="button" onClick={() => this.setState({ error: null })}>
          Retry
        </button>
      </div>
    );
  }
}
