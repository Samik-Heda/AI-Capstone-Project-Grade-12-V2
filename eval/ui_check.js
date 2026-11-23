// UI walkthrough (eval/ui_walkthrough.md), run inside the app page: steps 1–14 (P6), 15–20 (P7), 21–25 (P9, §18),
// 26–29 (P10, §19).
// Step 20 needs a backend whose Ollama is unreachable (eval/ui_runner.py starts one).
// Usage: open http://127.0.0.1:5176 in a fresh tab, paste this file into the console, then run
//   await gcUiCheck()            // all steps; or gcUiCheck({from: 8, to: 10})
// It drives the real UI (clicks, typing, pointer drags) and checks the DOM against the API.
// Returns {steps:[{step, name, pass, detail}], perf, errors}. Profiles it saves are deleted at the end.

globalThis.gcUiCheck = async function gcUiCheck({ from = 1, to = 14 } = {}) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms))
  const $ = (s, root = document) => root.querySelector(s)
  const $$ = (s, root = document) => [...root.querySelectorAll(s)]
  const errors = []
  const origError = console.error
  const origWarn = console.warn
  console.error = (...a) => (errors.push('error: ' + a.join(' ')), origError(...a))
  console.warn = (...a) => (errors.push('warn: ' + a.join(' ')), origWarn(...a))
  const onErr = (e) => errors.push('uncaught: ' + (e.message || e.reason))
  addEventListener('error', onErr)
  addEventListener('unhandledrejection', onErr)

  const steps = []
  const saved = []
  const check = async (step, name, fn) => {
    if (step < from || step > to) return
    try {
      const detail = await fn()
      steps.push({ step, name, pass: true, detail })
    } catch (e) {
      steps.push({ step, name, pass: false, detail: String(e.message || e) })
    }
  }
  const ok = (cond, msg) => {
    if (!cond) throw new Error(msg)
  }
  const until = async (cond, ms = 4000, msg = 'timed out') => {
    const t0 = performance.now()
    while (performance.now() - t0 < ms) {
      const v = cond()
      if (v) return v
      await wait(40)
    }
    throw new Error(msg)
  }
  const lastBot = () => $$('.msg.bot:not(.typing)').pop()
  const botCount = () => $$('.msg.bot:not(.typing)').length
  const clickChip = async (prefix) => {
    const n = botCount()
    const chip = await until(() => $$('.chip:not(:disabled)').find((b) => b.textContent.startsWith(prefix)), 4000, `no chip "${prefix}"`)
    chip.click()
    await until(() => botCount() > n, 6000, `no reply after chip "${prefix}"`)
    return lastBot()
  }
  const type = async (text) => {
    const n = botCount()
    const ta = $('.chat-input textarea')
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
    set.call(ta, text)
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    await wait(30)
    $('.chat-input form, form.chat-input').requestSubmit()
    await until(() => botCount() > n, 6000, `no reply to "${text}"`)
    return lastBot()
  }
  const sid = () => window.__gcStore.getState().sessionId
  const state = () => window.__gcStore.getState().state
  const api = (p) => fetch(p).then((r) => r.json())
  const pathOf = (id) => $(`path.district[data-id="${id}"]`)
  const clickDistrict = async (id) => {
    const p = pathOf(id)
    const r = p.getBoundingClientRect()
    p.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }))
    const pop = await until(() => $(`.district-popup[data-district="${id}"]`), 3000, `popup ${id} did not open`)
    await until(() => !pop.classList.contains('refreshing') && ($('.popup-rank', pop) || $('.popup-ineligible', pop)), 4000, `popup ${id} did not load`)
    return pop
  }
  const meta = await api('/api/meta')
  const plain = Object.fromEntries(meta.fields.map((f) => [f.key, f.plain_label]))
  const nameOf = (id) => meta.districts.find((d) => d.id === id).name

  // Popup DOM vs GET /api/district/{id}?session_id= — every number must match exactly.
  const popupMatchesApi = async (id) => {
    const pop = $(`.district-popup[data-district="${id}"]`)
    const d = await api(`/api/district/${id}?session_id=${sid()}`)
    const bad = []
    if (d.entry.eligible) {
      const r = $('.popup-rank', pop)
      if (Number(r.dataset.rank) !== d.entry.rank) bad.push(`rank ${r.dataset.rank} vs ${d.entry.rank}`)
      if ($('.score-num', pop).textContent !== d.entry.score.toFixed(1)) bad.push(`score ${$('.score-num', pop).textContent} vs ${d.entry.score}`)
      if (!pop.innerText.includes(`likely ${d.entry.band[0]}–${d.entry.band[1]}`)) bad.push('band')
      if (!pop.innerText.includes(`${d.entry.confidence} confidence`)) bad.push('confidence')
    } else {
      // the popup lists each reason in short form: "crop output below the minimum (10 k t)" -> "low crop output (10 k t)"
      const short = d.reason.split('; ').map((r) => r.replace(/^(.*) below the minimum \((.*)\)$/, 'low $1 ($2)'))
      if (!short.every((r) => $('.popup-ineligible', pop).innerText.includes(r))) bad.push('reason')
    }
    for (const v of d.values) {
      const row = $(`.bd-item[data-field="${v.field}"]`, pop)
      if (!row) {
        bad.push(`missing ${v.field}`)
        continue
      }
      if ($('[data-display]', row).textContent !== v.display) bad.push(`${v.field} display ${$('[data-display]', row).textContent} vs ${v.display}`)
      if ($('[data-points]', row).textContent !== v.points.toFixed(1)) bad.push(`${v.field} pts ${$('[data-points]', row).textContent} vs ${v.points}`)
    }
    const strengths = $$('[data-list="strengths"] li', pop).map((li) => li.textContent)
    d.strengths.forEach((s, i) => {
      if (!strengths[i]?.startsWith(plain[s.field]) || !strengths[i].includes(s.display)) bad.push(`strength ${i}`)
    })
    const weak = $$('[data-list="weaknesses"] li', pop).map((li) => li.textContent)
    d.weaknesses.forEach((s, i) => {
      if (!weak[i]?.startsWith(plain[s.field]) || !weak[i].includes(s.display)) bad.push(`weakness ${i}`)
    })
    ok(!bad.length, `popup ${nameOf(id)} differs from /district: ${bad.join('; ')}`)
    return d
  }
  // Drag a weights-panel slider to `frac` of the track with real pointer events.
  const drag = async (field, frac) => {
    const track = $(`.slider-row[data-field="${field}"] .slider-track`)
    const r = track.getBoundingClientRect()
    const at = (f) => ({ bubbles: true, pointerId: 7, button: 0, clientX: r.left + r.width * f, clientY: r.top + r.height / 2 })
    const start = Number(track.parentElement.querySelector('.slider-thumb').style.left.replace('%', '')) / 100
    track.dispatchEvent(new PointerEvent('pointerdown', at(start)))
    for (let i = 1; i <= 8; i++) {
      track.dispatchEvent(new PointerEvent('pointermove', at(start + ((frac - start) * i) / 8)))
      await wait(25)
    }
    track.dispatchEvent(new PointerEvent('pointerup', at(frac)))
  }
  const noNumbersOnControls = () => {
    const panel = $('.weights-panel')
    ok(panel?.classList.contains('open'), 'weights panel closed')
    // Every control in the panel (the heading only repeats the profile name, which users choose).
    const controls = $$('.sliders, .slider-ends, .toggle, .panel-actions', panel).map((el) => el.innerText).join('\n')
    ok(!/\d/.test(controls), `digits visible on a weight control: ${controls.match(/.{0,20}\d.{0,20}/)?.[0]}`)
    ok(!$$('[style*="font-family"], .num, .score-num', panel).length, 'numeric element inside weights panel')
    ok(!$$('input, textarea, select, [contenteditable="true"]', panel).length, 'input box inside weights panel')
    const labels = $$('.slider-row', panel).map((r) => r.querySelector('.slider-label .has-tip').firstChild.textContent.trim())
    ok(labels.length === 13, `${labels.length} sliders`)
    ok(labels.every((l, i) => l === plain[$$('.slider-row', panel)[i].dataset.field]), 'slider labels are not the plain labels')
    return labels
  }

  await check(1, 'empty state shows example chips', async () => {
    const chips = $$('.chat-empty .chip').map((c) => c.textContent)
    ok(chips.length >= 5, `${chips.length} chips`)
    ok(!$$('path.district').some((p) => p.dataset.fill), 'map colored before any profile')
    return chips
  })

  await check(2, 'industry example answers with an Apply chip; nothing switches', async () => {
    const before = state().profile
    const m = await clickChip('Food processing plant, water is critical')
    ok(m.innerText.includes('This sounds like Food & Beverages'), m.innerText)
    ok($$('.chip', m).some((c) => c.textContent === 'Apply Food & Beverages'), 'no Apply chip')
    ok(state().profile === before, 'profile switched without Apply')
    return m.innerText.split('\n')[0]
  })

  await check(3, 'Apply chip → rank, map recolor, rankings card, legend', async () => {
    const m = await clickChip('Apply Food & Beverages')
    await wait(300)
    const fills = $$('path.district').map((p) => p.dataset.fill)
    ok(fills.filter((f) => f.startsWith('#')).length === 34, 'eligible districts not colored')
    ok(fills.filter((f) => f.includes('hatch')).length === 2, 'metro districts not hatched')
    ok($$('[data-badge]').length === 5, 'rank badges')
    const top = $$('.rank-row[data-district]').slice(0, 2).map((r) => nameOf(Number(r.dataset.district)))
    ok(top.join() === 'Nashik,Pune', `top 2 ${top}`)
    ok($('[data-testid=status-chip] .status-name').innerText === 'Food & Beverages', 'status chip')
    ok(/not eligible/i.test($('.legend').innerText), 'legend hatch explanation')
    const tiers = { 1: '#38BDF8', 2: '#2DD4BF', 3: '#22D3EE', 4: '#1D9FBF', 5: '#1D9FBF', 10: '#1A6B8A', 11: '#1B2537' }
    for (const [rank, color] of Object.entries(tiers)) {
      const e = state().ranking.find((x) => x.eligible && x.rank === Number(rank))
      ok(pathOf(e.district_id).dataset.fill === color, `rank ${rank} fill ${pathOf(e.district_id).dataset.fill}, want ${color}`)
    }
    ok($$('.region-label').length === 5, 'region names')
    ok(m.querySelector('.reply-card .mini-top') && m.innerText.includes('Top districts for Food & Beverages'), 'no reply card')
    return m.innerText.split('\n')[0]
  })

  await check(4, 'adjust in chat ("power is important")', async () => {
    const w0 = state().weights.power
    const m = await type('power is important')
    ok(/Reliable electricity\*{0,2} now matters more/.test(m.innerText) || m.innerText.includes('now matters more'), m.innerText)
    ok(state().weights.power > w0, 'power weight not raised')
    ok($$('.chip', m).some((c) => c.textContent === 'Save as my profile'), 'no Save chip')
    ok(!/%/.test(m.innerText), 'percent in reply')
    return m.innerText.split('\n')[0]
  })

  await check(5, 'different industry → Apply chip; ranking unchanged until clicked', async () => {
    const before = JSON.stringify(state().ranking.map((e) => e.district_id))
    const m = await type('now an automobile plant, power is critical')
    ok($$('.chip', m).some((c) => c.textContent === 'Apply Automobile'), 'no Apply Automobile chip')
    ok(JSON.stringify(state().ranking.map((e) => e.district_id)) === before, 'ranking changed before Apply')
    ok(state().profile.name === 'Food & Beverages', 'profile switched before Apply')
    await clickChip('Apply Automobile')
    ok(state().profile.name === 'Automobile', 'Apply did not switch')
    ok(state().modifiers.power === 'critical', 'pending modifier not applied')
    return `leader ${state().ranking[0].name}`
  })

  await check(6, 'save from chat', async () => {
    const m1 = await clickChip('Save as my profile')
    ok(/call it|name/i.test(m1.innerText), m1.innerText)
    const name = `UI check ${Date.now() % 100000}`
    const m2 = await type(name)
    ok(m2.innerText.includes(`Saved as ${name}`), m2.innerText)
    saved.push(state().profile.id)
    await until(() => window.__gcStore.getState().profiles.user.some((p) => p.name === name), 3000, 'menu not refreshed')
    $('.profile-menu .header-btn').click()
    await wait(50)
    const menu = $('.menu').textContent
    $('.profile-menu .header-btn').click()
    ok(menu.includes('My profiles') && menu.includes(name), 'saved profile missing from menu')
    return name
  })

  await check(7, 'compare table from facts', async () => {
    const m = await type('compare Nashik and Pune')
    const t = $('[data-testid=compare-table]', m)
    ok(t, 'no compare table')
    ok($$('tbody tr', t).length === 13, 'compare rows')
    const cmp = window.__gcStore.getState().messages.at(-1).facts.compare
    for (const r of cmp.rows) {
      const tr = $$('tbody tr', t).find((x) => x.cells[0].textContent === plain[r.field])
      ok(tr, `row ${r.field}`)
      ok(tr.cells[1].textContent === r.a.display && tr.cells[2].textContent === r.b.display, `values ${r.field}`)
      const lead = r.leader === 'a' ? 1 : r.leader === 'b' ? 2 : 0
      if (lead) ok(tr.cells[lead].classList.contains('lead'), `leader ${r.field}`)
    }
    return m.innerText.split('\n')[0]
  })

  await check(8, 'floating popups: DOM equals /district, two side by side, drag by the bar', async () => {
    window.__gcStore.getState().setUi({ popups: [] })
    const id = meta.districts.find((d) => d.name === 'Sangli').id
    const pop = await clickDistrict(id)
    const d = await popupMatchesApi(id)
    ok(!/Explain with AI|Pin to compare|Ask in chat|Why not #1/.test(pop.innerText), 'removed buttons/sections are back')
    const nid = meta.districts.find((x) => x.name === 'Nashik').id
    const pop2 = await clickDistrict(nid)
    await popupMatchesApi(nid)
    await wait(250) // let the open animation (4 px rise) finish before measuring
    const [a, b] = [pop.getBoundingClientRect(), pop2.getBoundingClientRect()]
    ok($$('.district-popup').length === 2, 'two popups not open together')
    ok(a.right <= b.left + 1 || b.right <= a.left + 1, 'popups overlap')
    // drag the second popup by its bar; the map must not pan
    const view = $('.map .zoom-layer').style.transform
    const bar = $('.popup-bar', pop2)
    const r = bar.getBoundingClientRect()
    const at = (dx, dy) => ({ bubbles: true, pointerId: 9, button: 0, clientX: r.left + 40 + dx, clientY: r.top + 10 + dy })
    bar.dispatchEvent(new PointerEvent('pointerdown', at(0, 0)))
    await wait(30)
    for (let i = 1; i <= 5; i++) {
      dispatchEvent(new PointerEvent('pointermove', at(-12 * i, 18 * i)))
      await wait(20)
    }
    dispatchEvent(new PointerEvent('pointerup', at(-60, 90)))
    await wait(50)
    const moved = pop2.getBoundingClientRect()
    ok(Math.abs(moved.top - b.top - 90) < 3 && Math.abs(moved.left - b.left + 60) < 3, `popup did not follow the drag (${moved.left - b.left}, ${moved.top - b.top})`)
    ok($('.map .zoom-layer').style.transform === view, 'map panned while dragging a popup')
    // a third district replaces the oldest
    const third = meta.districts.find((x) => x.name === 'Pune').id
    await clickDistrict(third)
    const open = window.__gcStore.getState().ui.popups.map((p) => p.id)
    ok(open.length === 2 && !open.includes(id) && open.includes(third), `popups ${open}`)
    return `#${d.entry.rank} ${d.entry.score}`
  })

  await check(9, 'district popup (ineligible)', async () => {
    const pop = await clickDistrict(1)
    ok($('.popup-ineligible', pop).innerText.includes('no industrial land'), 'reason')
    ok(pop.innerText.includes('Include districts without industrial land'), 'toggle constraint offer')
    await popupMatchesApi(1)
    return $('.popup-ineligible', pop).innerText.split('\n')[0]
  })

  await check(10, 'all 36 clickable, both Mumbai districts on the main map', async () => {
    const failed = []
    for (const d of meta.districts) {
      try {
        await clickDistrict(d.id)
      } catch {
        failed.push(d.name)
      }
    }
    ok(!failed.length, `not clickable: ${failed}`)
    await clickDistrict(2)
    await popupMatchesApi(2)
    return '36/36'
  })

  await check(11, 'slider drag re-ranks the map; no numbers/inputs; plain labels', async () => {
    const rid = meta.districts.find((d) => d.name === 'Raigad').id
    await clickDistrict(rid)
    if (!$('.weights-panel.open')) $('.weights-panel .head-btn').click()
    await until(() => $('.weights-panel.open'))
    noNumbersOnControls()
    const before = state().ranking.find((e) => e.district_id === rid).rank
    const nSlider = globalThis.__gcPerf.slider.length
    await drag('port', 0.95)
    await until(() => state().slider_overrides.port !== undefined, 3000, 'override not applied')
    await wait(400)
    noNumbersOnControls()
    const after = state().ranking.find((e) => e.district_id === rid).rank
    ok(after < before, `Raigad did not climb (${before} → ${after})`)
    ok($$('.slider-row.changed').length > 1, 'other sliders not visibly rebalanced')
    const timings = globalThis.__gcPerf.slider.slice(nSlider)
    ok(timings.length, 'no slider timing recorded')
    return { raigad: `${before} → ${after}`, last: timings.at(-1) }
  })

  await check(12, 'open popup re-fetches after weights change (never stale)', async () => {
    const rid = meta.districts.find((d) => d.name === 'Raigad').id
    const pop = await until(() => $(`.district-popup[data-district="${rid}"]`), 2000, 'popup closed')
    await until(() => !pop.classList.contains('refreshing'))
    await popupMatchesApi(rid)
    return 'matches'
  })

  await check(13, 'reset', async () => {
    $$('.panel-actions .btn').find((b) => b.textContent === 'Reset').click()
    await until(() => !Object.keys(state().slider_overrides).length && !Object.keys(state().modifiers).length, 3000, 'reset failed')
    const s = state()
    ok(Object.keys(s.weights).every((k) => Math.abs(s.weights[k] - s.base_weights[k]) < 1e-9), 'weights ≠ base')
    await wait(400)
    ok(!$('[data-testid=status-chip]').innerText.includes('adjusted'), 'status still adjusted')
    ok(!$$('.slider-row.changed').length, 'sliders still highlighted')
    return s.profile.name
  })

  await check(14, 'data modal shows honest statuses', async () => {
    $('.sources-btn').click()
    const modal = await until(() => $('.modal.data-modal'))
    for (const f of meta.fields) {
      const tr = $(`tr[data-field="${f.key}"]`, modal)
      ok(tr && tr.innerText.includes(f.status) && tr.innerText.includes(f.plain_label), `row ${f.key}`)
    }
    ok(modal.innerText.includes('Ports used'), 'ports box')
    dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await until(() => !$('.modal'), 1000, 'Esc did not close')
    ok($$('.district-popup').length > 0, 'Escape on the modal also closed a popup')
    $('.sources-btn').click()
    const bd = await until(() => $('.backdrop'), 1000, 'modal did not reopen')
    bd.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    await until(() => !$('.modal'), 1000, 'outside click did not close')
    return `${meta.fields.length} fields`
  })

  // ---------- P7: profile builder, clusters, offline ----------
  const setField = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }
  const builder = () => $('.modal.builder')
  const thumbPos = (field) => Number($(`.builder .slider-row[data-field="${field}"] .slider-thumb`).style.left.replace('%', '')) / 100
  const previewNames = () => $$('.builder-preview li').map((li) => li.textContent)
  const openMenu = async () => {
    if (!$('.menu')) $('.profile-btn').click()
    return until(() => $('.menu'), 1000, 'profile menu did not open')
  }
  const BUILD_TEXT = 'Pharma plant needing clean water, skilled graduates; land cost does not matter'
  let builtId = null

  await check(15, 'Profile menu → Build my own… opens a centered modal', async () => {
    await openMenu()
    $$('.menu-item').find((b) => b.textContent.includes('Build my own')).click()
    const m = await until(builder, 1500, 'builder did not open')
    const r = m.getBoundingClientRect()
    ok(Math.abs(r.left + r.width / 2 - innerWidth / 2) < 4, 'modal not centered')
    ok(r.width <= 761, `modal ${Math.round(r.width)}px wide`)
    await until(() => $$('.builder .slider-row').length === 13, 3000, 'builder sliders not loaded')
    return `${Math.round(r.width)}px`
  })

  await check(16, 'sentence → Start from Pharmaceuticals; water and skilled workers right, cheap land left', async () => {
    setField($('#builder-text'), BUILD_TEXT)
    await until(() => $('[data-testid=builder-start]').value === 'preset:pharma', 4000, `start from ${$('[data-testid=builder-start]').value}`)
    const d = await fetch('/api/profiles/draft', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: BUILD_TEXT }),
    }).then((r) => r.json())
    await wait(400)
    for (const f of meta.fields.map((x) => x.key)) {
      const want = Math.sqrt(d.weights[f])
      ok(Math.abs(thumbPos(f) - want) < 0.01, `${f} thumb ${thumbPos(f).toFixed(3)} vs draft ${want.toFixed(3)}`)
    }
    ok(d.weights.water > d.base_weights.water && d.weights.literacy > d.base_weights.literacy, 'water/skilled did not rise')
    ok(d.weights.land < d.base_weights.land || d.weights.land === 0, 'cheap land did not fall')
    const changed = $$('.builder .slider-row.changed').map((r) => r.dataset.field).sort()
    ok(['literacy', 'water'].every((f) => changed.includes(f)), `highlighted: ${changed}`)
    ok(!/\d/.test($$('.builder .sliders, .builder .slider-ends').map((e) => e.innerText).join(' ')), 'digits on builder sliders')
    ok(!$$('input, textarea, select', $('.builder .sliders')).length, 'input box inside builder sliders')
    await until(() => previewNames().length === 5, 3000, 'no top-5 preview')
    return { start: 'Pharmaceuticals', changed, top5: previewNames() }
  })

  await check(17, 'drag a builder slider → top-5 preview updates', async () => {
    const before = previewNames().join()
    let calls = 0
    const origFetch = window.fetch
    window.fetch = (u, o) => {
      if (String(u).includes('/api/rank')) calls++
      return origFetch(u, o)
    }
    try {
      const track = $('.builder .slider-row[data-field="port"] .slider-track')
      const r = track.getBoundingClientRect()
      const at = (f) => ({ bubbles: true, pointerId: 11, button: 0, clientX: r.left + r.width * f, clientY: r.top + r.height / 2 })
      const start = thumbPos('port')
      track.dispatchEvent(new PointerEvent('pointerdown', at(start)))
      for (let i = 1; i <= 6; i++) {
        track.dispatchEvent(new PointerEvent('pointermove', at(start + ((0.97 - start) * i) / 6)))
        await wait(25)
      }
      track.dispatchEvent(new PointerEvent('pointerup', at(0.97)))
      await until(() => calls > 0, 2000, 'preview did not re-rank')
      await until(() => previewNames().join() !== before, 2000, 'preview unchanged after a big port drag')
    } finally {
      window.fetch = origFetch
    }
    ok($('.builder .slider-row[data-field="port"]').classList.contains('changed'), 'dragged slider not highlighted')
    return { before, after: previewNames().join() }
  })

  await check(18, 'Save → Use now; rename and delete from My profiles (delete asks first)', async () => {
    const name = `UI builder ${Math.floor(Math.random() * 1e5)}`
    setField($('#builder-name'), name)
    await wait(50)
    $$('.builder .btn').find((b) => b.textContent === 'Use now').click()
    await until(() => !builder() && state().profile?.name === name, 4000, 'Use now did not activate the profile')
    builtId = state().profile.id
    saved.push(builtId)
    ok(state().profile.kind === 'user', 'not a user profile')
    const prof = (await api('/api/profiles')).user.find((p) => p.id === builtId)
    ok(prof && /^Based on Pharmaceuticals/.test(prof.rationale), `rationale: ${prof?.rationale}`)
    await openMenu()
    $(`.user-item[data-profile="${builtId}"] [aria-label^="Rename"]`).click()
    const inp = await until(() => $(`.user-item[data-profile="${builtId}"] input`), 1000, 'no rename box')
    setField(inp, name + ' renamed')
    await wait(30)
    inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await until(() => $(`.user-item[data-profile="${builtId}"] .user-pick`)?.textContent === name + ' renamed', 3000, 'rename not shown')
    await until(() => $('[data-testid=status-chip] .status-name')?.innerText === name + ' renamed', 2000, 'status chip kept the old name')
    $(`.user-item[data-profile="${builtId}"] [aria-label^="Delete"]`).click()
    const conf = await until(() => $(`.user-item.confirm[data-profile="${builtId}"]`), 1000, 'no confirmation')
    ok((await api('/api/profiles')).user.some((p) => p.id === builtId), 'deleted before confirming')
    $$('.mini-btn', conf).find((b) => b.textContent === 'Delete').click()
    await until(() => !$(`.user-item[data-profile="${builtId}"]`), 3000, 'still listed after delete')
    ok(!(await api('/api/profiles')).user.some((p) => p.id === builtId), 'still on the server')
    saved.splice(saved.indexOf(builtId), 1)
    dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    return name
  })

  await check(19, 'Clusters on and off: labelled types, silhouette, badges stay', async () => {
    await window.__gcStore.getState().setProfile('preset:automobile')
    await wait(300)
    const scoreFills = $$('path.district').map((p) => p.dataset.fill).join()
    $('.cluster-toggle').click()
    const cl = await until(() => window.__gcStore.getState().ui.clusterMode && window.__gcStore.getState().ui.clusters, 4000, 'clusters did not load')
    await wait(100)
    const legend = $('.cluster-card').innerText
    ok(/silhouette \d\.\d\d/.test(legend), 'no silhouette in the cluster card')
    ok(cl.k >= 3 && cl.k <= 6, `k=${cl.k}`)
    const labels = cl.clusters.map((c) => c.label)
    ok(new Set(labels).size === labels.length, 'duplicate cluster labels')
    ok(new Set(cl.clusters.map((c) => c.color)).size === cl.k, 'colors repeat')
    cl.clusters.forEach((c) => ok(legend.includes(c.name), `legend missing ${c.name}`))
    const byId = Object.fromEntries(cl.clusters.flatMap((c) => c.district_ids.map((id) => [String(id), c.color])))
    for (const p of $$('path.district')) ok(p.dataset.stroke === byId[p.dataset.id], `district ${p.dataset.id} outline ${p.dataset.stroke}`)
    ok($$('path.district').map((p) => p.dataset.fill).join() === scoreFills, 'rank fills changed in cluster mode')
    ok($$('[data-badge]').length === 5, 'rank badges hidden in cluster mode')
    $('.cluster-toggle').click()
    await until(() => $$('path.district').every((p) => !p.dataset.stroke) && $('.rankings-card'), 2000, 'cluster outlines not removed')
    return { k: cl.k, silhouette: cl.silhouette, labels }
  })

  await check(20, 'Ollama stopped: templated replies, "AI note unavailable", no errors', async () => {
    const h = await api('/api/health')
    ok(!h.ollama.reachable, 'Ollama is reachable: run step 20 against a backend with Ollama stopped')
    await window.__gcStore.getState().setProfile('preset:automobile')
    const m1 = await type('power is critical')
    // §18: ranking replies get an AI note too; offline it must say so, never hang
    ok(m1.innerText.includes('Reliable electricity'), `ranking reply: ${m1.innerText.slice(0, 80)}`)
    if (m1.querySelector('.ai-note')) {
      await until(() => m1.querySelector('.ai-note').innerText.includes('AI note unavailable'), 6000, 'ranking note not "unavailable"')
    }
    const top = state().ranking.find((e) => e.rank === 1).name
    const m2 = await type(`explain ${top}`)
    ok(m2.dataset.kind === 'explain_district' && m2.innerText.includes(top), `explain reply: ${m2.innerText.slice(0, 80)}`)
    await until(() => m2.querySelector('.ai-note')?.innerText.includes('AI note unavailable'), 6000, 'no "AI note unavailable"')
    ok(!m2.querySelector('.badge.ok'), 'grounded badge shown')
    return m2.querySelector('.ai-note').innerText.split('\n').pop()
  })

  // ---------- §18 advisor (P9) ----------
  const ranks = () => state().ranking.filter((e) => e.eligible).map((e) => e.district_id).join()

  await check(21, 'Advisor: follow-up chips, open question, answer previews, Apply this change', async () => {
    await type('Food processing plant')
    const m = await clickChip('Apply Food')
    const chips = $$('.chip', m).map((c) => c.textContent)
    ok(chips.some((c) => c.startsWith('Why is')) && chips.some((c) => c.startsWith('Compare')), `follow-ups: ${chips}`)
    ok(chips.includes('Make a business brief'), 'no brief chip')
    const q = $('[data-testid=advisor-question]', m)
    ok(q && state().pending_question, 'no advisor question after applying an industry')
    ok(/your own words/.test($('.chat-input textarea').placeholder), 'placeholder does not invite an answer')
    const before = ranks()
    const field = state().pending_question.field
    const a = await type('yes, a lot')
    ok(a.dataset.kind === 'advisor_answer' && /would/.test(a.innerText), `answer reply: ${a.innerText.slice(0, 80)}`)
    ok(ranks() === before && !state().modifiers[field], 'the answer changed the ranking before Apply')
    const done = await clickChip('Apply this change')
    ok(done.dataset.kind === 'adjust' && state().modifiers[field] === 'critical', 'Apply this change did not apply')
    return { question: q.innerText.replace('Quick question', '').trim().slice(0, 60), field }
  })

  await check(22, 'Follow-up chip "Compare …" opens the compare table', async () => {
    const m = await clickChip('Compare')
    ok(m.dataset.kind === 'compare' && $('[data-testid=compare-table]', m), 'no compare table')
    const follow = $$('.chip', m).map((c) => c.textContent)
    ok(follow.some((c) => c.startsWith('Tell me about')), `compare follow-ups: ${follow}`)
    return follow
  })

  await check(23, 'Free text the rules cannot read: AI reading suggests, nothing changes before Apply', async () => {
    const h = await api('/api/health')
    const prof = state().profile?.id
    const before = ranks()
    const m = await type('washing powder and dish bars')
    if (!h.ollama.reachable) return 'skipped (Ollama offline)'
    await until(() => m.querySelector('[data-testid=interpreting]'), 3000, 'no "thinking it over" shimmer')
    await until(() => !m.querySelector('[data-testid=interpreting]'), 60000, 'AI reading never finished')
    const last = lastBot()
    ok(state().profile?.id === prof && ranks() === before, 'the AI reading changed the session')
    if (last === m) return 'AI had no confident reading (shimmer cleared, nothing added)'
    ok(last.querySelector('.ai-read') && $$('.chip', last).some((c) => c.textContent.startsWith('Apply')), 'AI reply has no Apply chip')
    return last.innerText.split('\n')[0].slice(0, 80)
  })

  await check(24, 'Business brief: header button opens it; facts at once; closes cleanly', async () => {
    ok(!$('[data-testid=brief-btn]').disabled, 'brief button disabled with a profile')
    $('[data-testid=brief-btn]').click()
    const b = await until(() => $('[data-testid=brief]'), 2000, 'brief did not open')
    await until(() => $$('.brief-district', b).length === 3, 3000, 'top 3 not shown within 3 s')
    const names = $$('.bd-name', b).map((x) => x.textContent)
    const top = state().ranking.filter((e) => e.eligible).slice(0, 3).map((e) => e.name)
    ok(names.join() === top.join(), `brief top 3 ${names} vs ranking ${top}`)
    ok($$('.brief-list.steps li', b).length > 0 && $('.brief-sources table', b), 'no next steps or sources')
    $('[data-testid=brief] [aria-label=Close]').click()
    await until(() => !$('[data-testid=brief]'), 1000, 'brief did not close')
    return names
  })

  await check(25, 'Print stylesheet shows only the brief', async () => {
    const rules = [...document.styleSheets].flatMap((s) => { try { return [...s.cssRules] } catch { return [] } })
    const pr = rules.filter((r) => r.media && [...r.media].includes('print')).map((r) => r.cssText).join(' ')
    ok(/body \*\s*\{[^}]*visibility: hidden/.test(pr), 'print CSS does not hide the app')
    ok(/\.brief-modal \*[^{]*\{[^}]*visibility: visible/.test(pr), 'print CSS does not show the brief')
    return 'ok'
  })

  // ---------- §19 conversation (P10) ----------
  await check(26, 'A question gets an instant answer with the industry checklist, then an AI answer', async () => {
    ok(!$('[data-testid=stale-banner]'), 'stale-server banner shown against a fresh server')
    await window.__gcStore.getState().setProfile('preset:electronics')
    const before = ranks()
    const m = await type('any other factors i should be concerned about?')
    ok(m.dataset.kind === 'ask' && /Also check \(not in our data\)/.test(m.innerText), `ask reply: ${m.innerText.slice(0, 90)}`)
    ok(ranks() === before, 'a question changed the ranking')
    ok($$('.chip', m).some((c) => c.textContent === 'Tell me more'), 'no Tell me more chip')
    const h = await api('/api/health')
    if (!h.ollama.reachable) return 'AI offline: templated answer only'
    await until(() => ['done', 'error'].includes(m.querySelector('.ai-note')?.dataset.status), 60000, 'AI answer did not finish')
    ok(m.querySelector('.ai-note').dataset.status === 'done', m.querySelector('.ai-note').innerText.slice(0, 80))
    return { advice: !!m.querySelector('[data-testid=general-advice]'), answer: m.querySelector('.ai-text')?.innerText.slice(0, 80) }
  })

  await check(27, 'Tell me more: the advanced model adds a detailed answer under the same reply', async () => {
    const h = await api('/api/health')
    if (!h.ollama.reachable) return 'skipped (Ollama offline)'
    const m = lastBot()
    $$('.chip', m).find((c) => c.textContent === 'Tell me more').click()
    const d = await until(() => m.querySelector('[data-testid=detail-answer]'), 3000, 'no detailed answer box')
    await until(() => ['done', 'error'].includes(d.dataset.status), 200000, 'detailed answer did not finish')
    ok(d.dataset.status === 'done', d.innerText.slice(0, 80))
    ok(!$$('.chip', m).some((c) => c.textContent === 'Tell me more'), 'Tell me more chip still offered')
    return d.innerText.replace('Detailed answer (advanced model)', '').trim().slice(0, 80)
  })

  await check(28, 'What-if previews, nothing changes until Apply this change', async () => {
    const before = ranks()
    const m = await type("what if I didn't need a port")
    ok(m.dataset.kind === 'what_if' && /Nothing has changed yet/.test(m.innerText), `what-if reply: ${m.innerText.slice(0, 80)}`)
    ok(ranks() === before && !state().modifiers.port, 'what-if changed the session')
    const done = await clickChip('Apply this change')
    ok(done.dataset.kind === 'adjust' && ['low', 'exclude'].includes(state().modifiers.port), 'Apply this change did not apply')
    return m.innerText.split('\n')[0].slice(0, 90)
  })

  await check(29, 'Brief shows "Also check before investing"', async () => {
    $('[data-testid=brief-btn]').click()
    const b = await until(() => $('[data-testid=brief-also]'), 3000, 'no Also check section')
    const n = $$('li', b).length
    ok(n >= 4, `only ${n} items`)
    $('[data-testid=brief] [aria-label=Close]').click()
    await until(() => !$('[data-testid=brief]'), 1000, 'brief did not close')
    return `${n} items`
  })

  for (const id of saved) await fetch(`/api/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (saved.length) await window.__gcStore.getState().refreshProfiles()
  console.error = origError
  console.warn = origWarn
  removeEventListener('error', onErr)
  removeEventListener('unhandledrejection', onErr)
  return {
    viewport: `${innerWidth}x${innerHeight}`,
    passed: steps.filter((s) => s.pass).length,
    total: steps.length,
    steps,
    perf: { recolor: globalThis.__gcPerf.recolor, slider: globalThis.__gcPerf.slider },
    errors,
  }
}
