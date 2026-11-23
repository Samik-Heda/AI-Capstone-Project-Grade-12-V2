// Demo script (spec §16), run inside the app page like ui_check.js: `await gcDemoCheck()`.
// Each of the 9 demo steps is driven through the real UI and checked. P8 runs it 3 times in a row
// (eval/ui_runner.py --demo 3) against live Ollama: the compare takeaway must pass the groundedness check.
// Profiles it saves are deleted at the end.

globalThis.gcDemoCheck = async function gcDemoCheck() {
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
  const ok = (cond, msg) => {
    if (!cond) throw new Error(msg)
  }
  const check = async (step, name, fn) => {
    const t0 = performance.now()
    try {
      steps.push({ step, name, pass: true, detail: await fn(), ms: Math.round(performance.now() - t0) })
    } catch (e) {
      steps.push({ step, name, pass: false, detail: String(e.message || e), ms: Math.round(performance.now() - t0) })
    }
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
  const store = () => window.__gcStore.getState()
  const state = () => store().state
  const api = (p) => fetch(p).then((r) => r.json())
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
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(ta, text)
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    await wait(30)
    $('form.chat-input').requestSubmit()
    await until(() => botCount() > n, 6000, `no reply to "${text}"`)
    return lastBot()
  }
  const setField = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }
  const meta = await api('/api/meta')
  const idOf = (name) => meta.districts.find((d) => d.name === name).id
  const rankOf = (name) => state().ranking.find((e) => e.district_id === idOf(name)).rank
  const narrationDone = (m, ms = 45000) =>
    until(() => {
      const note = $('.ai-note', m)
      if (!note) return null
      if (note.dataset.status === 'done') return note.dataset.grounded === 'true' ? '✓ grounded' : '⚠ ' + ($('.badge', note)?.title || 'flagged')
      if (note.innerText.includes('AI note unavailable')) return 'unavailable: ' + note.innerText.split('\n').pop()
      return null
    }, ms, 'narration did not finish')

  await check(1, 'example chip → "This sounds like Food & Beverages" → Apply → Nashik and Pune lead, metro hatching explained', async () => {
    ok($$('.chat-empty .chip').length >= 6, 'example chips missing')
    const m = await clickChip('Food processing plant, water is critical')
    ok(m.innerText.includes('This sounds like Food & Beverages'), m.innerText.slice(0, 80))
    await clickChip('Apply Food & Beverages')
    await wait(300)
    const top = state().ranking.filter((e) => e.eligible).slice(0, 2).map((e) => e.name).sort()
    ok(top.join() === 'Nashik,Pune', `leaders ${top}`)
    ok($$('path.district').filter((p) => p.dataset.fill?.startsWith('#')).length === 34, 'map not recolored')
    ok(/not eligible \(2\)\s*no industrial land/i.test($('.legend').innerText), 'metro hatching not explained')
    return top.join(', ')
  })

  await check(2, '"compare Nashik and Pune": table at once, AI takeaway appears with no ⚠', async () => {
    const m = await type('compare Nashik and Pune')
    ok($('[data-testid=compare-table]', m), 'no compare table')
    ok($$('[data-testid=compare-table] tbody tr', m).length === 13, 'table rows')
    const badge = await narrationDone(m)
    ok(badge.startsWith('✓'), `takeaway: ${badge}`)
    ok(!$('.badge.ok', m), 'grounded badge shown (hidden by design)')
    return badge
  })

  await check(3, 'click Sangli and Pune: two floating popups (strengths, confidence band); "explain Sangli" → AI note', async () => {
    const open = async (name) => {
      const p = $(`path.district[data-id="${idOf(name)}"]`)
      const r = p.getBoundingClientRect()
      p.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }))
      const pop = await until(() => $(`.district-popup[data-district="${idOf(name)}"]`), 3000, `${name} popup did not open`)
      await until(() => !pop.classList.contains('refreshing') && $('.popup-rank', pop), 4000, `${name} popup did not load`)
      ok($('[data-list="strengths"]', pop), `${name}: no strengths`)
      ok(/likely \d+–\d+|stable at/.test(pop.innerText), `${name}: no confidence band`)
      return pop
    }
    const a = (await open('Sangli')).getBoundingClientRect()
    const b = (await open('Pune')).getBoundingClientRect()
    ok(a.right <= b.left + 1 || b.right <= a.left + 1, 'popups overlap')
    const m = await type('explain Sangli')
    ok(m.dataset.kind === 'explain_district', `kind ${m.dataset.kind}`)
    const badge = await narrationDone(m)
    ok(!badge.startsWith('unavailable'), `explain narration ${badge}`)
    store().closePopup()
    store().closePopup()
    return badge
  })

  await check(4, '"now an automobile plant, power is critical" → Apply Automobile; power raised', async () => {
    const m = await type('now an automobile plant, power is critical')
    ok($$('.chip', m).some((c) => c.textContent === 'Apply Automobile'), 'no Apply Automobile chip')
    await clickChip('Apply Automobile')
    await until(() => state().profile?.id === 'preset:automobile', 3000, 'profile not Automobile')
    ok(state().modifiers.power === 'critical' && state().weights.power > state().base_weights.power, 'power not raised')
    return `power ${state().modifiers.power}`
  })

  await check(5, 'drag "Near a port" right: Raigad and Thane climb, others shift; Save as my profile', async () => {
    const before = { raigad: rankOf('Raigad'), thane: rankOf('Thane') }
    if (!$('.weights-panel.open')) $('.weights-panel .head-btn').click()
    await until(() => $('.weights-panel.open'))
    const track = $('.slider-row[data-field="port"] .slider-track')
    const r = track.getBoundingClientRect()
    const at = (f) => ({ bubbles: true, pointerId: 5, button: 0, clientX: r.left + r.width * f, clientY: r.top + r.height / 2 })
    const start = Number($('.slider-row[data-field="port"] .slider-thumb').style.left.replace('%', '')) / 100
    track.dispatchEvent(new PointerEvent('pointerdown', at(start)))
    for (let i = 1; i <= 8; i++) {
      track.dispatchEvent(new PointerEvent('pointermove', at(start + ((0.95 - start) * i) / 8)))
      await wait(25)
    }
    track.dispatchEvent(new PointerEvent('pointerup', at(0.95)))
    await until(() => state().slider_overrides.port !== undefined, 3000, 'drag not applied')
    await wait(400)
    const after = { raigad: rankOf('Raigad'), thane: rankOf('Thane') }
    ok(after.raigad < before.raigad, `Raigad ${before.raigad} → ${after.raigad}`)
    ok(after.thane <= before.thane, `Thane ${before.thane} → ${after.thane}`)
    ok($$('.slider-row.changed').length > 1, 'other sliders did not visibly shift')
    const n = botCount()
    $$('.panel-actions .btn').find((b) => b.textContent === 'Save as my profile').click()
    await until(() => botCount() > n, 4000, 'no save reply')
    ok(lastBot().innerText.includes('What should I call it?'), lastBot().innerText.slice(0, 60))
    await clickChip('Automobile (custom)')
    await until(() => state().profile?.kind === 'user', 3000, 'not saved')
    saved.push(state().profile.id)
    return { before, after, saved: state().profile.name }
  })

  await check(6, '"cement plant": clarify explains the missing resource data, offers chips', async () => {
    const m = await type('cement plant')
    ok(m.dataset.kind === 'clarify', `kind ${m.dataset.kind}`)
    ok(/limestone|resource|data/i.test(m.innerText), m.innerText.slice(0, 100))
    ok($$('.chip', m).length >= 2, 'no chips')
    return m.innerText.split('\n')[0].slice(0, 100)
  })

  await check(7, 'Build my own…: pharma sentence → Pharmaceuticals, sliders move, top 5, Save and Use now', async () => {
    store().openBuilder()
    await until(() => $('.modal.builder') && $$('.builder .slider-row').length === 13, 3000, 'builder did not open')
    const text = 'Pharma plant needing clean water, skilled graduates; land cost does not matter'
    setField($('#builder-text'), text)
    await until(() => $('[data-testid=builder-start]').value === 'preset:pharma', 4000, 'Start from not Pharmaceuticals')
    await until(() => $$('.builder .slider-row.changed').length >= 2, 3000, 'sliders did not move')
    const changed = $$('.builder .slider-row.changed').map((r) => r.dataset.field)
    ok(changed.includes('water') && changed.includes('literacy'), `moved ${changed}`)
    await until(() => $$('.builder-preview li').length === 5, 3000, 'no top-5 preview')
    const top5 = $$('.builder-preview li').map((li) => li.textContent)
    const name = `Demo pharma ${Math.floor(Math.random() * 1e5)}`
    setField($('#builder-name'), name)
    await wait(50)
    $$('.builder .btn').find((b) => b.textContent === 'Save').click()
    await until(() => $('.builder .ok-text'), 3000, 'save did not confirm')
    $$('.builder .btn').find((b) => b.textContent === 'Use now').click()
    await until(() => !$('.modal.builder') && state().profile?.kind === 'user', 4000, 'Use now failed')
    const mine = (await api('/api/profiles')).user.filter((x) => x.name === name)
    for (const p of mine) saved.push(p.id)
    ok(mine.length === 1, `Save then Use now stored ${mine.length} profiles`)
    return { top5, active: state().profile.name }
  })

  await check(8, 'Clusters: labelled types with the silhouette score', async () => {
    $('.cluster-toggle').click()
    await until(() => store().ui.clusterMode && store().ui.clusters, 4000, 'clusters did not load')
    await wait(100)
    ok(/silhouette \d\.\d\d/.test($('.cluster-card').innerText), 'no silhouette')
    const n = $$('.cluster-list li').length
    ok(n === store().ui.clusters.k, `${n} cluster cards`)
    ok($$('path.district').every((p) => p.dataset.stroke), 'districts without a cluster outline')
    $('.cluster-toggle').click()
    return `${n} types`
  })

  await check(9, 'Sources: honest statuses and the benchmark summary (P@5, intent accuracy, groundedness)', async () => {
    $('.sources-btn').click()
    const modal = await until(() => $('.modal.data-modal'), 2000, 'data modal')
    for (const f of meta.fields) ok($(`tr[data-field="${f.key}"]`, modal)?.innerText.includes(f.status), `status ${f.key}`)
    $$('.section-btn', modal).find((b) => b.textContent.includes('How well it works')).click()
    const bench = await until(() => $('[data-testid=bench-summary]', modal), 3000, 'no benchmark summary')
    const txt = bench.innerText
    ok(/P@5/.test(txt) && /intent accuracy/i.test(txt) && /grounded/i.test(txt), txt.slice(0, 120))
    dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await until(() => !$('.modal'), 1000, 'Esc did not close')
    return txt.split('\n').slice(0, 3).join(' | ')
  })

  for (const id of new Set(saved)) await fetch(`/api/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' })
  await store().refreshProfiles()
  console.error = origError
  console.warn = origWarn
  removeEventListener('error', onErr)
  removeEventListener('unhandledrejection', onErr)
  return { viewport: `${innerWidth}x${innerHeight}`, passed: steps.filter((s) => s.pass).length, total: steps.length, steps, errors }
}
