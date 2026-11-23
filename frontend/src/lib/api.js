export const OFFLINE_MESSAGE = 'cannot reach the GrowthCompass server'

async function request(method, path, body) {
  let res
  try {
    res = await fetch(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error(OFFLINE_MESSAGE) // network failure: the server is down or restarting
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  // The dev/preview proxy answers 502/504 without an API error body when the server is down.
  if (!res.ok && !data?.error && [502, 503, 504].includes(res.status)) throw new Error(OFFLINE_MESSAGE)
  if (!res.ok) throw new Error(data?.error?.message || `${method} ${path} failed (${res.status})`)
  return data
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  put: (path, body) => request('PUT', path, body),
  del: (path) => request('DELETE', path),
}

// POST that reads a Server-Sent Events stream (§11). Events are `event: <type>\ndata: <json>\n\n`;
// chunks can split anywhere, so partial lines are buffered until a blank line ends the event.
export async function streamSSE(path, body, onEvent, signal) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null)
    throw new Error(data?.error?.message || `POST ${path} failed (${res.status})`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    let cut
    while ((cut = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, cut)
      buf = buf.slice(cut + 2)
      let type = 'message'
      const data = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) type = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
      }
      if (!data.length) continue
      let payload
      try {
        payload = JSON.parse(data.join('\n'))
      } catch {
        continue
      }
      onEvent(type, payload)
    }
  }
}
