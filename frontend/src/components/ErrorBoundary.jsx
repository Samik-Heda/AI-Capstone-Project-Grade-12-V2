import { Component } from 'react'
import { useStore } from '../store.js'

// A render error in a popup or modal shows a message instead of silently drawing nothing.
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const close = () => useStore.getState().setUi({ modal: null })
    const box = (
      <div className="error-card" role="alert">
        <p>Could not show {this.props.label}: {String(error.message || error)}</p>
        <p className="muted small">If the server was updated, restart it with start.bat and reload the page.</p>
        {this.props.modal && <button className="btn" onClick={close}>Close</button>}
      </div>
    )
    return this.props.modal ? (
      <div className="backdrop" onPointerDown={(e) => e.target === e.currentTarget && close()}>{box}</div>
    ) : (
      box
    )
  }
}
