"""Headless-browser runner for the UI walkthrough (§14.6), used by run_benchmarks.py for the P6/P7 gates.

Usage: python eval/ui_runner.py [--to 14] [--no-build]
       python eval/ui_runner.py --demo 3     # the §16 demo script (eval/demo_check.js), N runs in a row

Builds the frontend, starts its own backend (uvicorn) and `vite preview` on free ports (so a running
dev setup is never touched and the current code is what gets tested), then drives the installed
Chrome or Edge in headless mode over the DevTools protocol. At each resolution it opens a fresh
page, injects eval/ui_check.js, runs `await gcUiCheck({to})`, then runs a timing pass (10 profile
switches through the store, 6 slider drags with a popup open). Console errors and warnings from the
page are collected through the protocol as well as inside the page. Headless Chrome runs animation
frames, so `paint_ms` (input → next frame after the DOM commit) is a measured value here.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
UI_CHECK = ROOT / "eval" / "ui_check.js"
DEMO_CHECK = ROOT / "eval" / "demo_check.js"
RESOLUTIONS = [(1280, 720), (1920, 1080)]
# Port 9 (discard) never listens; a free_port() pick could be reused by the backend itself.
DEAD_OLLAMA = "http://127.0.0.1:9"
FILL_TRANSITION_MS = 200  # MapView FILL_TRANSITION_MS; part of the §2 recolor budget
FRAME_MS = 17
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]

# Timing pass, run in the page after the walkthrough. Profile switches go through the store exactly
# as the profile menu does; drags use real pointer events on the weights-panel track.
TIMING_JS = r"""
(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms))
  const st = () => window.__gcStore.getState()
  const perf = globalThis.__gcPerf
  const r0 = perf.recolor.length, s0 = perf.slider.length
  const ids = ['preset:automobile','preset:dairy','preset:textile','preset:pharma','preset:logistics',
               'preset:chemicals','preset:electronics','preset:agro_cold','preset:sugar_ethanol','preset:food_beverages']
  for (const id of ids) { await st().setProfile(id); await wait(450) }
  const path = document.querySelector('path.district[data-id="20"]') || document.querySelector('path.district')
  const r = path.getBoundingClientRect()
  path.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }))
  await wait(400)
  if (!document.querySelector('.weights-panel')) document.querySelector('.weights-pill').click()
  await wait(300)
  const fields = ['port', 'water', 'power', 'land', 'rail', 'literacy']
  for (const [i, f] of fields.entries()) {
    const track = document.querySelector(`.slider-row[data-field="${f}"] .slider-track`)
    const b = track.getBoundingClientRect()
    const at = (x) => ({ bubbles: true, pointerId: 9, button: 0, clientX: b.left + b.width * x, clientY: b.top + b.height / 2 })
    const thumb = track.parentElement.querySelector('.slider-thumb')
    const start = Number(thumb.style.left.replace('%', '')) / 100
    const to = i % 2 ? 0.2 : 0.9
    track.dispatchEvent(new PointerEvent('pointerdown', at(start)))
    for (let k = 1; k <= 8; k++) { track.dispatchEvent(new PointerEvent('pointermove', at(start + (to - start) * k / 8))); await wait(25) }
    track.dispatchEvent(new PointerEvent('pointerup', at(to)))
    await wait(500)
  }
  await st().reset()
  await wait(300)
  return { recolor: perf.recolor.slice(r0), slider: perf.slider.slice(s0) }
})()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_browser() -> str | None:
    env = os.environ.get("GC_BROWSER")
    if env and Path(env).exists():
        return env
    return next((b for b in BROWSERS if Path(b).exists()), None)


def wait_http(url: str, timeout: float = 60) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        with contextlib.suppress(httpx.HTTPError):
            if httpx.get(url, timeout=1).status_code < 500:
                return True
        time.sleep(0.3)
    return False


def pctl(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


class CDP:
    """Minimal Chrome DevTools Protocol client over one page websocket."""

    def __init__(self, ws):
        self.ws, self.n, self.pending, self.console = ws, 0, {}, []
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg)
            elif msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] in ("error", "warning", "assert"):
                args = " ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"])
                self.console.append(f"{msg['params']['type']}: {args}")
            elif msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.console.append("exception: " + (d.get("exception", {}).get("description") or d.get("text", "")))
            elif msg.get("method") == "Log.entryAdded" and msg["params"]["entry"]["level"] in ("error", "warning"):
                e = msg["params"]["entry"]
                self.console.append(f"log-{e['level']}: {e.get('text', '')} {e.get('url', '')}".strip())

    async def send(self, method: str, params: dict | None = None, timeout: float = 30):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        msg = await asyncio.wait_for(fut, timeout)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def evaluate(self, expr: str, timeout: float = 300):
        r = await self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True},
                            timeout=timeout)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"])
        return r["result"].get("value")


async def run_page(debug_port: int, url: str, w: int, h: int, frm: int, to: int, timing: bool, demo: bool = False) -> dict:
    async with httpx.AsyncClient() as c:
        target = (await c.put(f"http://127.0.0.1:{debug_port}/json/new?about:blank")).json()
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=2**26) as ws:
        cdp = CDP(ws)
        for dom in ("Runtime", "Log", "Page"):
            await cdp.send(f"{dom}.enable")
        await cdp.send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1,
                                                              "mobile": False})
        await cdp.send("Page.navigate", {"url": url})
        # wait for the app: store present, session created, map drawn
        t0 = time.time()
        while time.time() - t0 < 30:
            with contextlib.suppress(RuntimeError):
                if await cdp.evaluate("!!(window.__gcStore && window.__gcStore.getState().sessionId && "
                                      "document.querySelectorAll('path.district').length === 36)", 5):
                    break
            await asyncio.sleep(0.25)
        else:
            return {"viewport": f"{w}x{h}", "passed": 0, "total": to - frm + 1, "steps": [], "errors": ["app did not load"],
                    "console": cdp.console}
        if demo:
            await cdp.evaluate(DEMO_CHECK.read_text(encoding="utf-8") + "\n;true")
            res = await cdp.evaluate("gcDemoCheck()", timeout=600)
        else:
            await cdp.evaluate(UI_CHECK.read_text(encoding="utf-8") + "\n;true")
            res = await cdp.evaluate(f"gcUiCheck({{from: {frm}, to: {to}}})", timeout=600)
        if timing:
            res["timing"] = await cdp.evaluate(TIMING_JS, timeout=120)
        await asyncio.sleep(0.3)
        res["console"] = list(cdp.console)
        cdp.reader.cancel()
    async with httpx.AsyncClient() as c:
        await c.get(f"http://127.0.0.1:{debug_port}/json/close/{target['id']}")
    return res


def lint() -> dict:
    """`npm run lint` over src/ must be clean, and a probe using the banned patterns must fail."""
    npm = shutil.which("npm") or "npm"
    src = subprocess.run([npm, "run", "-s", "lint"], cwd=FRONTEND, capture_output=True, text=True, shell=os.name == "nt")
    probe = FRONTEND / "src" / "__lint_probe__.jsx"
    probe.write_text("export const P = ({h}) => <div dangerouslySetInnerHTML={{ __html: h }} />\n"
                     "export const Q = (el, h) => { el.innerHTML = h }\n", encoding="utf-8")
    try:
        pr = subprocess.run([npm, "exec", "--", "eslint", str(probe.relative_to(FRONTEND))], cwd=FRONTEND,
                            capture_output=True, text=True, shell=os.name == "nt")
    finally:
        probe.unlink(missing_ok=True)
    probe_errors = pr.stdout.count(" error ")
    return {"src_clean": src.returncode == 0, "src_out": (src.stdout + src.stderr)[-400:],
            "probe_errors": probe_errors, "probe_rejected": pr.returncode != 0 and probe_errors >= 2}


def start_pair(npm: str, workdir: str, tag: str, procs: list, extra_env: dict | None = None) -> tuple[int, str | None]:
    """A backend on a free port plus `vite preview` proxying to it. Returns (preview port, error)."""
    bport, fport = free_port(), free_port()
    env = {**os.environ, "GC_BACKEND_PORT": str(bport), **(extra_env or {})}
    blog, flog = open(Path(workdir) / f"backend-{tag}.log", "w"), open(Path(workdir) / f"preview-{tag}.log", "w")
    procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
                                   "--port", str(bport), "--log-level", "warning"], cwd=ROOT, env=env,
                                  stdout=blog, stderr=subprocess.STDOUT))
    procs.append(subprocess.Popen(f'"{npm}" exec -- vite preview --port {fport} --strictPort', cwd=FRONTEND, env=env,
                                  shell=True, stdout=flog, stderr=subprocess.STDOUT))
    if not wait_http(f"http://127.0.0.1:{bport}/api/health") or not wait_http(f"http://127.0.0.1:{fport}/"):
        blog.close(), flog.close()
        logs = [(Path(workdir) / f"{n}-{tag}.log").read_text(errors="replace")[-800:] for n in ("backend", "preview")]
        return fport, f"{tag} servers did not start (ports {bport}/{fport}):\n" + "\n---\n".join(logs)
    return fport, None


def run(to: int = 14, build: bool = True, timing: bool = True, demo: int = 0, frm: int = 1) -> dict:
    """Steps 1..min(to, 19) against the normal backend; step 20 (Ollama stopped) against a second
    backend whose Ollama URL is a dead port (GC_OLLAMA_URL), so the real Ollama is never touched.
    With demo=N, runs the §16 demo script N times in a row instead (1280×720, live Ollama, one
    backend, a fresh page and session each time)."""
    browser = find_browser()
    if not browser:
        return {"error": "no Chrome/Edge found (set GC_BROWSER)"}
    npm = shutil.which("npm") or "npm"
    if build:
        b = subprocess.run([npm, "run", "-s", "build"], cwd=FRONTEND, capture_output=True, text=True,
                           shell=os.name == "nt")
        if b.returncode != 0:
            return {"error": "frontend build failed: " + (b.stdout + b.stderr)[-600:]}
    procs: list = []
    workdir = tempfile.mkdtemp(prefix="gc-ui-")
    try:
        fport, err = start_pair(npm, workdir, "online", procs)
        if err:
            return {"error": err}
        offline_port = None
        if frm <= 20 <= to and not demo:
            offline_port, err = start_pair(npm, workdir, "offline", procs, {"GC_OLLAMA_URL": DEAD_OLLAMA})
            if err:
                return {"error": err}
        dport = free_port()
        procs.append(subprocess.Popen([browser, "--headless=new", f"--remote-debugging-port={dport}",
                                       f"--user-data-dir={Path(workdir) / 'chrome'}", "--no-first-run",
                                       "--no-default-browser-check", "--disable-extensions", "--window-size=1920,1080",
                                       "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not wait_http(f"http://127.0.0.1:{dport}/json/version", 20):
            return {"error": "browser did not start"}
        out = {"browser": Path(browser).name, "runs": []}
        if demo:
            w, h = RESOLUTIONS[0]
            for _ in range(demo):
                out["runs"].append(asyncio.run(run_page(dport, f"http://127.0.0.1:{fport}/", w, h, 0, 0, False, True)))
            return out
        for w, h in RESOLUTIONS:
            if frm > 20:  # §18/§19 steps only (P9, P10): online backend, no timing pass
                out["runs"].append(asyncio.run(run_page(dport, f"http://127.0.0.1:{fport}/", w, h, frm, to, False)))
                continue
            r = asyncio.run(run_page(dport, f"http://127.0.0.1:{fport}/", w, h, 1, min(to, 19), timing))
            if offline_port:
                off = asyncio.run(run_page(dport, f"http://127.0.0.1:{offline_port}/", w, h, 20, 20, False))
                r["steps"] += off.get("steps", [])
                r["passed"] = sum(st["pass"] for st in r["steps"])
                r["total"] = len(r["steps"])
                r["errors"] = r.get("errors", []) + off.get("errors", [])
                # the offline page's only allowed console noise is the failed narration request itself
                r["console"] = r.get("console", []) + [c for c in off.get("console", []) if "/api/narrate" not in c]
            out["runs"].append(r)
        return out
    finally:
        for p in reversed(procs):
            with contextlib.suppress(Exception):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                else:
                    p.kill()
        shutil.rmtree(workdir, ignore_errors=True)


def summarize_timing(runs: list[dict]) -> dict:
    """Recolor: response → next frame + the fill transition. Slider: last input → next frame.
    paint_ms is measured (headless runs frames); commit + 1 frame is the fallback."""
    def samples(r: dict, kind: str) -> list[dict]:
        return (r.get("perf") or {}).get(kind, []) + (r.get("timing") or {}).get(kind, [])

    def paint(x: dict) -> int:
        return x["paint_ms"] if x.get("paint_ms") is not None else x["commit_ms"] + FRAME_MS

    rec = [paint(x) + FILL_TRANSITION_MS for r in runs for x in samples(r, "recolor")]
    sld = [paint(x) for r in runs for x in samples(r, "slider")]
    return {"recolor_p95": round(pctl(rec, 95)), "recolor_n": len(rec),
            "slider_p95": round(pctl(sld, 95)), "slider_n": len(sld)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", type=int, default=20)
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--demo", type=int, default=0, help="run the §16 demo script N times in a row")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    res = run(a.to, build=not a.no_build, demo=a.demo)
    if "error" in res:
        print(res["error"])
        return 1
    for r in res["runs"]:
        print(f"{r['viewport']}: {r['passed']}/{r['total']}  console={len(r.get('console', []))} "
              f"in-page errors={len(r.get('errors', []))}")
        for s in r["steps"]:
            print(f"  {'PASS' if s['pass'] else 'FAIL'} {s['step']:>2} {s['name']}: {json.dumps(s['detail'])[:140]}")
        for e in r.get("console", []) + r.get("errors", []):
            print("  !", e[:200])
    if a.demo:
        return 0 if all(r.get("total") == 9 and r["passed"] == 9 and not r.get("console") and not r.get("errors")
                        for r in res["runs"]) else 1
    print(summarize_timing(res["runs"]))
    print(lint())
    return 0


if __name__ == "__main__":
    sys.exit(main())
