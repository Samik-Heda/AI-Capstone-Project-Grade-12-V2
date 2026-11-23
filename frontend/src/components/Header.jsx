import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store.js'

function ProfileMenu() {
  const open = useStore((s) => s.ui.menuOpen)
  const setUi = useStore((s) => s.setUi)
  const profiles = useStore((s) => s.profiles)
  const current = useStore((s) => s.state?.profile?.id)
  const currentName = useStore((s) => s.state?.profile?.name)
  const setProfile = useStore((s) => s.setProfile)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e) => !ref.current?.contains(e.target) && setUi({ menuOpen: false })
    const onKey = (e) => e.key === 'Escape' && setUi({ menuOpen: false })
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, setUi])

  const openBuilder = useStore((s) => s.openBuilder)
  const presets = [...profiles.preset].sort((a, b) => a.name.localeCompare(b.name))
  const item = (p) => (
    <button
      key={p.id}
      role="menuitemradio"
      aria-checked={p.id === current}
      className={`menu-item${p.id === current ? ' current' : ''}`}
      onClick={() => setProfile(p.id)}
    >
      {p.name}
    </button>
  )

  return (
    <div className="profile-menu" ref={ref}>
      <button className="header-btn profile-btn" aria-haspopup="menu" aria-expanded={open} aria-label="Profile"
        onClick={() => setUi({ menuOpen: !open })}>
        <span className={currentName ? '' : 'dim'}>{currentName || 'Select profile'}</span>
        <span className="dim">▾</span>
      </button>
      {open && (
        <div className="menu" role="menu" aria-label="Profiles">
          <div className="menu-head">Industries</div>
          {presets.map(item)}
          {profiles.user.length > 0 && <div className="menu-head">My profiles</div>}
          {profiles.user.map((p) => (
            <UserProfileItem key={p.id} p={p} current={p.id === current} onPick={() => setProfile(p.id)} />
          ))}
          <div className="menu-sep" />
          <button role="menuitem" className="menu-item build" onClick={() => openBuilder()}>
            + Build my own…
          </button>
        </div>
      )}
    </div>
  )
}

// A saved profile: pick it, rename it in place, or delete it (asks first, §9.2).
function UserProfileItem({ p, current, onPick }) {
  const renameProfile = useStore((s) => s.renameProfile)
  const deleteProfile = useStore((s) => s.deleteProfile)
  const [mode, setMode] = useState(null) // null | 'rename' | 'delete'
  const [name, setName] = useState(p.name)
  const [error, setError] = useState(null)

  const commit = async () => {
    const n = name.trim()
    if (!n || n === p.name) return setMode(null)
    try {
      await renameProfile(p.id, n)
      setMode(null)
    } catch (e) {
      setError(e.message)
    }
  }

  if (mode === 'rename') {
    return (
      <div className="menu-item user-item editing" data-profile={p.id}>
        <input
          aria-label={`New name for ${p.name}`}
          value={name}
          maxLength={80}
          autoFocus
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setMode(null)
          }}
        />
        <button className="mini-btn" onClick={commit}>Save</button>
        {error && <span className="error-text small">{error}</span>}
      </div>
    )
  }
  if (mode === 'delete') {
    return (
      <div className="menu-item user-item confirm" data-profile={p.id} role="alertdialog" aria-label={`Delete ${p.name}?`}>
        <span>Delete “{p.name}”?</span>
        <button className="mini-btn danger" onClick={() => deleteProfile(p.id).catch((e) => setError(e.message))}>Delete</button>
        <button className="mini-btn" onClick={() => setMode(null)}>Keep</button>
        {error && <span className="error-text small">{error}</span>}
      </div>
    )
  }
  return (
    <div className={`menu-item user-item${current ? ' current' : ''}`} data-profile={p.id}>
      <button role="menuitemradio" aria-checked={current} className="user-pick" onClick={onPick} title={p.rationale || ''}>
        {p.name}
      </button>
      <button className="mini-btn" aria-label={`Rename ${p.name}`} onClick={() => { setName(p.name); setMode('rename') }}>✎</button>
      <button className="mini-btn" aria-label={`Delete ${p.name}`} onClick={() => setMode('delete')}>🗑</button>
    </div>
  )
}

export default function Header() {
  const state = useStore((s) => s.state)
  const busy = useStore((s) => s.ui.busy)
  const setUi = useStore((s) => s.setUi)
  const openBrief = useStore((s) => s.openBrief)
  const adjusted =
    state?.profile &&
    (Object.keys(state.modifiers).length > 0 ||
      Object.keys(state.slider_overrides).length > 0 ||
      state.constraints.requires_industrial_land === false)

  return (
    <header className="header">
      <div className="logo">
        <span className="logo-mark" aria-hidden="true">◈</span> GrowthCompass
      </div>
      <ProfileMenu />
      <div className="status" data-testid="status-chip">
        {state?.profile ? (
          <>
            <span className="dim">Showing: </span>
            <span className="status-name">{state.profile.name}</span>
            {adjusted && <span className="adjusted"> · Weights adjusted ↑</span>}
          </>
        ) : (
          <span className="dim">{busy ? 'Analyzing…' : 'Select a profile or describe an industry in the chat'}</span>
        )}
      </div>
      <div className="spacer" />
      <button className="header-btn brief-btn" onClick={openBrief} disabled={!state?.profile}
        title={state?.profile ? undefined : 'Pick an industry first'} data-testid="brief-btn">
        ▤ Business brief
      </button>
      <button className="header-btn sources-btn" onClick={() => setUi({ modal: { type: 'data' } })}>
        ⊕ Sources
      </button>
    </header>
  )
}
