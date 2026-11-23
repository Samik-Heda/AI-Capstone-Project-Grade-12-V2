import { create } from 'zustand'
import { api, streamSSE } from './lib/api.js'
import { excludedCauses } from './lib/reasons.js'

// Mirror of server state (§12.2). The UI keeps no local copies of weights, industry or rankings:
// everything shown is derived from `state`, which only the server changes.

// §12.3 explain/compare, and since §18.2 also the ranking replies (applied industry, adjustments)
const AUTO_NARRATE = new Set(['explain_district', 'compare', 'apply_preset', 'adjust', 'clarify_answer', 'ask', 'chat'])
const WEIGHTS_THROTTLE_MS = 60

let msgSeq = 0
const nextId = () => `m${++msgSeq}`
let narrationAbort = null
let briefAbort = null
let weightsTimer = null
let weightsPending = null // {overrides, constraints, tInput}
let weightsLastSent = 0
let weightsSeq = 0
const SESSION_GONE = /unknown or expired session/i

// Perf marks for the P6 gates; read by eval/ui_check.js through window.__gcPerf.
const perf = (globalThis.__gcPerf = globalThis.__gcPerf || { recolor: [], slider: [], pending: null })

const MAX_POPUPS = 2 // two floating popups side by side double as a visual compare

const leaderOf = (state) => state?.ranking?.find((e) => e.eligible && e.rank === 1) || null
const topOf = (state, n = 3) => (state?.ranking || []).filter((e) => e.eligible).slice(0, n)

// What a reply card needs beyond the server's facts: the profile, who led before, and the exclusions,
// captured when the reply arrives so later changes never rewrite an old card.
function cardContext(prev, next) {
  const second = topOf(next, 2)[1]
  return {
    profile: next?.profile?.name || null,
    prevLeader: leaderOf(prev)?.name || null,
    excluded: next?.excluded?.length || 0,
    excludedReason: excludedCauses(next?.excluded).slice(0, 3).join(', ') || null,
    eligible: next?.ranking?.filter((e) => e.eligible).length || 0,
    runnerUp: second ? { name: second.name, score: second.score } : null,
  }
}

export const useStore = create((set, get) => ({
  sessionId: null,
  meta: null,
  mapData: null,
  profiles: { preset: [], user: [] },
  state: null,
  messages: [],
  health: null,
  backendDown: false, // the last /health poll failed after the app had loaded
  loadError: null,
  draft: '',
  // How the last ranking change arrived: 'slider' (drag: quick recolor) or 'step' (profile, chat,
  // reset: the map plays its full recolor wave).
  changeKind: 'step',
  brief: null, // §18.3 {status: loading|streaming|done|error, data, sections: {id: {status, text, source}}, error}
  ui: { popups: [], modal: null, busy: false, rankingsAll: false, weightsOpen: false, menuOpen: false,
        clusterMode: false, clusters: null },

  async init() {
    set({ loadError: null })
    try {
      const [meta, mapData, profiles, sess] = await Promise.all([
        api.get('/api/meta'),
        fetch('/map.json').then((r) => r.json()),
        api.get('/api/profiles'),
        api.post('/api/session'),
      ])
      set({ meta, mapData, profiles, sessionId: sess.session_id, state: sess.state })
    } catch (e) {
      set({ loadError: String(e.message || e) })
    }
  },

  async pollHealth() {
    const wasDown = get().backendDown
    try {
      set({ health: await api.get('/api/health'), backendDown: false })
      if (wasDown) await get()._resumeSession()
    } catch {
      set({ health: null, backendDown: true })
    }
  },

  // Sessions live in server memory with a 2-hour idle TTL (§7.1): after a restart or a long idle the
  // old one is gone. Start a new one on the same profile so the app keeps working, and say so in the chat.
  async _resumeSession() {
    const { sessionId, state } = get()
    if (!sessionId) return get().init()
    try {
      await api.get(`/api/session/${sessionId}`)
      return
    } catch (e) {
      if (!/session/i.test(e.message)) return
    }
    try {
      const sess = await api.post('/api/session')
      let next = sess.state
      if (state?.profile) next = (await api.post(`/api/session/${sess.session_id}/profile`, { profile_id: state.profile.id })).state
      set({ sessionId: sess.session_id })
      get()._applyState(next)
      get()._push({ id: nextId(), role: 'assistant', kind: 'info',
                    md: 'The server restarted or this conversation sat idle too long, so it started fresh' +
                        (state?.profile ? ` on **${state.profile.name}** (slider changes were not kept).` : '.') })
    } catch {
      /* still unreachable; the next poll tries again */
    }
  },

  // Run a call that needs the session; if the server no longer knows it, resume once and retry.
  async _withSession(call) {
    try {
      return await call(get().sessionId)
    } catch (e) {
      if (!SESSION_GONE.test(e.message)) throw e
      await get()._resumeSession()
      return call(get().sessionId)
    }
  },

  setUi(patch) {
    set((s) => ({ ui: { ...s.ui, ...patch } }))
  },
  setDraft(draft) {
    set({ draft })
  },

  // Apply a server state; marks the time so the map can report recolor latency.
  _applyState(state, extra = {}) {
    if (state && get().state && state.ranking !== get().state.ranking) perf.pending = { t: performance.now(), kind: 'recolor' }
    set({ state, changeKind: 'step', ...extra })
  },

  _push(msg) {
    set((s) => ({ messages: [...s.messages, msg] }))
    return msg.id
  },
  _patchMsg(id, patch) {
    set((s) => ({ messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)) }))
  },

  async send(text) {
    const t = String(text || '').trim()
    if (!t) return
    if (t === 'action:show_all') {
      get().setUi({ rankingsAll: true })
      return
    }
    if (t === 'action:more') {
      // §19.1 "Tell me more": the advanced model answers the last question in detail, under the same reply
      const m = [...get().messages].reverse().find((x) => x.role === 'assistant' && (x.kind === 'ask' || x.kind === 'chat'))
      if (m) get().narrate(m.id, { detail: true })
      return
    }
    narrationAbort?.abort() // a new message aborts the previous stream (§12.3)
    const userLabel = t.startsWith('action:') ? null : t
    if (userLabel) get()._push({ id: nextId(), role: 'user', md: userLabel })
    get().setUi({ busy: true })
    try {
      const res = await get()._withSession((sid) => api.post('/api/message', { session_id: sid, text: t }))
      const id = res.turn_id
      const msg = { id, role: 'assistant', kind: res.kind, md: res.reply_md, chips: res.chips || [], facts: res.facts,
                    understood: res.understood, question: res.question || null, ctx: cardContext(get().state, res.state) }
      get()._applyState(res.state, { messages: [...get().messages, msg] }) // one render for reply + map
      if (res.kind === 'save_profile' && res.facts?.profile) get().refreshProfiles()
      if (AUTO_NARRATE.has(res.kind)) {
        if (res.narration?.available) get().narrate(id)
        else get()._patchMsg(id, () => ({ narration: { text: '', status: 'error', error: 'AI is offline' } }))
      }
      if (res.interpret?.available) get().interpret(id)
      if (res.kind === 'brief' && res.facts?.open_brief) get().openBrief()
    } catch (e) {
      get()._push({ id: nextId(), role: 'assistant', kind: 'error', md: `Something went wrong: ${e.message}` })
    } finally {
      get().setUi({ busy: false })
    }
  },

  // §18.3 business brief: stream it into the modal. Closing the modal stops the writing.
  openBrief() {
    const { sessionId } = get()
    briefAbort?.abort()
    narrationAbort?.abort() // the brief needs the AI; a running note would make it wait
    const ctrl = new AbortController()
    briefAbort = ctrl
    set((s) => ({ brief: { status: 'loading', data: null, sections: {} }, ui: { ...s.ui, modal: { type: 'brief' } } }))
    const patch = (fn) => set((s) => (s.brief && briefAbort === ctrl ? { brief: { ...s.brief, ...fn(s.brief) } } : {}))
    const sec = (id, fn) => patch((b) => ({ sections: { ...b.sections, [id]: { ...(b.sections[id] || {}), ...fn(b.sections[id] || {}) } } }))
    const onEvent = (type, data) => {
      if (type === 'brief') {
        const sections = Object.fromEntries(data.sections.map((x) => [x.id, { status: 'waiting', text: '' }]))
        patch(() => ({ status: 'streaming', data, sections }))
      } else if (type === 'section') sec(data.id, () => ({ status: 'writing', text: '' }))
      else if (type === 'token') sec(data.id, (x) => ({ text: (x.text || '') + data.t }))
      else if (type === 'section_done') sec(data.id, () => ({ status: 'done', text: data.text, source: data.source }))
      else if (type === 'done') patch(() => ({ status: 'done', model: data.model }))
      else if (type === 'error') patch(() => ({ status: 'error', error: data.message }))
    }
    streamSSE('/api/brief', { session_id: sessionId }, onEvent, ctrl.signal)
      .catch((e) => {
        if (e.name !== 'AbortError') patch(() => ({ status: 'error', error: `Could not make the brief: ${e.message}` }))
      })
      .finally(() => {
        if (briefAbort === ctrl) briefAbort = null
      })
  },
  closeBrief() {
    briefAbort?.abort()
    briefAbort = null
    set((s) => ({ ui: { ...s.ui, modal: null } }))
  },

  // §18.1 the rules could not read the message: ask the LLM. A useful reading arrives as a new reply
  // (a suggestion or preview with Apply chips); otherwise the shimmer just goes away.
  async interpret(turnId) {
    const { sessionId } = get()
    get()._patchMsg(turnId, () => ({ interpreting: true }))
    try {
      const res = await api.post('/api/interpret', { session_id: sessionId, turn_id: turnId })
      get()._patchMsg(turnId, () => ({ interpreting: false }))
      const m = res.message
      if (m && get().messages[get().messages.length - 1]?.id === turnId) {
        const msg = { id: m.turn_id, role: 'assistant', kind: m.kind, md: m.reply_md, chips: m.chips || [],
                      facts: m.facts, understood: m.understood, question: null, viaAI: true,
                      ctx: cardContext(get().state, m.state) }
        get()._applyState(m.state, { messages: [...get().messages, msg] })
      }
    } catch {
      get()._patchMsg(turnId, () => ({ interpreting: false }))
    }
  },

  // detail: the §19.1 "Tell me more" answer from the advanced model, kept in `m.detail` beside the short one.
  narrate(turnId, { detail = false } = {}) {
    const { sessionId } = get()
    narrationAbort?.abort()
    const ctrl = new AbortController()
    narrationAbort = ctrl
    const key = detail ? 'detail' : 'narration'
    get()._patchMsg(turnId, (m) => ({
      [key]: { text: '', status: 'streaming' },
      ...(detail ? { chips: (m.chips || []).filter((c) => c.value !== 'action:more') } : {}),
    }))
    const onEvent = (type, data) => {
      if (type === 'token') get()._patchMsg(turnId, (m) => ({ [key]: { ...m[key], text: m[key].text + data.t } }))
      else if (type === 'done') get()._patchMsg(turnId, (m) => ({ [key]: { ...m[key], status: 'done', grounding: data.grounding } }))
      else if (type === 'error') get()._patchMsg(turnId, (m) => ({ [key]: { ...m[key], status: 'error', error: data.message } }))
    }
    streamSSE('/api/narrate', { session_id: sessionId, turn_id: turnId, quality: detail }, onEvent, ctrl.signal)
      .catch((e) => {
        const aborted = e.name === 'AbortError'
        get()._patchMsg(turnId, (m) =>
          m[key]?.status === 'streaming'
            ? { [key]: { ...m[key], status: aborted ? 'aborted' : 'error', error: aborted ? null : e.message } }
            : {},
        )
      })
      .finally(() => {
        if (narrationAbort === ctrl) narrationAbort = null
      })
  },

  async setProfile(profileId) {
    get().setUi({ menuOpen: false })
    try {
      const prev = get().state
      const { state } = await get()._withSession((sid) => api.post(`/api/session/${sid}/profile`, { profile_id: profileId }))
      const msg = { id: nextId(), role: 'assistant', kind: 'rank', md: '', chips: [], facts: { top: topOf(state) },
                    ctx: { ...cardContext(prev, state), prevLeader: null } }
      get()._applyState(state, { messages: [...get().messages, msg] })
    } catch (e) {
      get()._push({ id: nextId(), role: 'assistant', kind: 'error', md: `Could not switch profile: ${e.message}` })
    }
  },

  async refreshProfiles() {
    try {
      set({ profiles: await api.get('/api/profiles') })
    } catch {
      /* the menu keeps the last list */
    }
  },

  // Slider drags and the industrial-area toggle. Throttled (leading + trailing) so the map
  // re-ranks while dragging; stale responses are dropped by sequence number.
  setWeights(overrides, constraints) {
    const p = weightsPending || { overrides: {}, constraints: undefined }
    weightsPending = {
      overrides: { ...p.overrides, ...overrides },
      constraints: constraints ? { ...(p.constraints || {}), ...constraints } : p.constraints,
      tInput: performance.now(),
    }
    const flush = async () => {
      weightsTimer = null
      const body = weightsPending
      weightsPending = null
      if (!body) return
      weightsLastSent = performance.now()
      const seq = ++weightsSeq
      try {
        const payload = { overrides: body.overrides }
        if (body.constraints) payload.constraints = body.constraints
        const { state } = await get()._withSession((sid) => api.post(`/api/session/${sid}/weights`, payload))
        if (seq !== weightsSeq) return
        perf.pending = { t: body.tInput, kind: 'slider' }
        set({ state, changeKind: 'slider' })
      } catch (e) {
        get()._push({ id: nextId(), role: 'assistant', kind: 'error', md: `Could not update priorities: ${e.message}` })
      }
    }
    if (weightsTimer) return
    const wait = Math.max(0, WEIGHTS_THROTTLE_MS - (performance.now() - weightsLastSent))
    weightsTimer = setTimeout(flush, wait)
  },

  async reset() {
    try {
      const { state } = await get()._withSession((sid) => api.post(`/api/session/${sid}/reset`))
      get()._applyState(state)
    } catch (e) {
      get()._push({ id: nextId(), role: 'assistant', kind: 'error', md: `Could not reset: ${e.message}` })
    }
  },

  // Floating district popups (max 2). A new one opens beside the first; a third replaces the oldest.
  // viaKey: opened with Enter/Space, so the popup takes focus and gives it back to the district on close.
  openDistrict(id, viaKey = false) {
    const { popups } = get().ui
    if (popups.some((p) => p.id === id)) {
      // already open: bring it to the front
      return get().setUi({ popups: [...popups.filter((p) => p.id !== id), popups.find((p) => p.id === id)] })
    }
    const kept = popups.slice(-(MAX_POPUPS - 1))
    const taken = new Set(kept.map((p) => p.slot))
    const slot = [0, 1].find((n) => !taken.has(n)) ?? 0
    get().setUi({ popups: [...kept, { id, viaKey, slot }] })
  },
  closePopup(id) {
    const { popups } = get().ui
    const p = id === undefined ? popups[popups.length - 1] : popups.find((x) => x.id === id)
    if (!p) return
    get().setUi({ popups: popups.filter((x) => x !== p) })
    if (p.viaKey) document.querySelector(`path.district[data-id="${p.id}"]`)?.focus()
  },

  // §9.2 builder modal. `start`: a preset to start from; `text`: prefill.
  openBuilder(opts = {}) {
    get().setUi({ modal: { type: 'builder', ...opts }, menuOpen: false })
  },

  async renameProfile(id, name) {
    await api.put(`/api/profiles/${encodeURIComponent(id)}`, { name })
    await get().refreshProfiles()
    if (get().state?.profile?.id === id) {
      const { state } = await get()._withSession((sid) => api.get(`/api/session/${sid}`))
      set({ state })
    }
  },

  // Deleting the active profile leaves the session on its current weights (the server resets to
  // the stored base weights); the menu simply no longer lists it.
  async deleteProfile(id) {
    await api.del(`/api/profiles/${encodeURIComponent(id)}`)
    await get().refreshProfiles()
  },

  // §10 cluster mode: fetched once (the server caches until the dataset changes).
  async toggleClusters() {
    const { ui } = get()
    if (ui.clusterMode) return get().setUi({ clusterMode: false })
    try {
      const clusters = ui.clusters || (await api.post('/api/clusters', {}))
      get().setUi({ clusterMode: true, clusters })
    } catch (e) {
      // A 404 here means the server process predates clusters: it needs a restart, not a retry.
      const md = /not found/i.test(e.message)
        ? 'District clusters need a newer server. Close the backend window and run start.bat again.'
        : `Could not load district clusters: ${e.message}`
      get()._push({ id: nextId(), role: 'assistant', kind: 'error', md })
    }
  },
}))

// Read-only handle for eval/ui_check.js (DOM vs API checks).
globalThis.__gcStore = useStore
