"""Cold start (P8 gate): fresh clone → start.bat → usable UI in under 2 minutes on reference hardware.

Usage: python eval/cold_start.py [--keep]

Copies exactly what a clone of the working tree would contain (`git ls-files --cached --others
--exclude-standard`: no node_modules, no gc.db, no build output) into a temp folder, then times the
steps start.bat takes there: Python dependency check, `npm install`, map build if missing, database seed,
then the API and the Vite dev server on free ports (start.bat's `start` windows can't be timed or
stopped from a script, so the same commands run as child processes). "Usable" means headless Chrome
has loaded the app with a session and all 36 districts drawn. Ollama is left as it is (the embedding
cache starts empty in a fresh clone and fills in the background; the UI does not wait for it).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval import ui_runner as ur  # noqa: E402

LIMIT_S = 120


def clone_tree(dst: Path) -> int:
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split("\0")
    n = 0
    for f in filter(None, files):
        src = ROOT / f
        if src.is_file():
            (dst / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / f)
            n += 1
    return n


async def wait_usable(debug_port: int, url: str, timeout: float) -> float | None:
    async with ur.httpx.AsyncClient() as c:
        target = (await c.put(f"http://127.0.0.1:{debug_port}/json/new?about:blank")).json()
    async with ur.websockets.connect(target["webSocketDebuggerUrl"], max_size=2**24) as ws:
        cdp = ur.CDP(ws)
        await cdp.send("Runtime.enable")
        await cdp.send("Page.enable")
        await cdp.send("Page.navigate", {"url": url})
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            with contextlib.suppress(RuntimeError, asyncio.TimeoutError):
                if await cdp.evaluate("!!(window.__gcStore && window.__gcStore.getState().sessionId && "
                                      "document.querySelectorAll('path.district').length === 36)", 5):
                    cdp.reader.cancel()
                    return time.perf_counter() - t0
            await asyncio.sleep(0.25)
        cdp.reader.cancel()
    return None


def run(keep: bool = False) -> dict:
    browser = ur.find_browser()
    if not browser:
        return {"error": "no Chrome/Edge found"}
    npm = shutil.which("npm") or "npm"
    work = Path(tempfile.mkdtemp(prefix="gc-cold-"))
    procs, steps = [], {}
    t_all = time.perf_counter()

    def step(name: str, cmd, cwd: Path, shell: bool = False) -> None:
        t = time.perf_counter()
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=shell)
        steps[name] = round(time.perf_counter() - t, 1)
        if r.returncode != 0:
            raise RuntimeError(f"{name} failed: {(r.stdout + r.stderr)[-500:]}")

    try:
        t = time.perf_counter()
        n = clone_tree(work)
        steps["clone (copy tracked files)"] = round(time.perf_counter() - t, 1)
        step("python requirements check", [sys.executable, "-c",
                                           "import fastapi, uvicorn, numpy, sklearn, rapidfuzz, shapely, httpx"], work)
        step("npm install", f'"{npm}" install --silent --no-audit --no-fund', work / "frontend", shell=True)
        if not (work / "frontend" / "public" / "map.json").exists():
            step("build map", [sys.executable, "pipeline/build_map.py"], work)
        step("seed database", [sys.executable, "pipeline/seed.py"], work)
        bport, fport, dport = ur.free_port(), ur.free_port(), ur.free_port()
        env = {**os.environ, "GC_BACKEND_PORT": str(bport)}
        t = time.perf_counter()
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
                                       "--port", str(bport)], cwd=work, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen(f'"{npm}" exec -- vite --port {fport} --strictPort', cwd=work / "frontend", env=env,
                                      shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen([browser, "--headless=new", f"--remote-debugging-port={dport}",
                                       f"--user-data-dir={work / 'chrome'}", "--no-first-run", "about:blank"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not ur.wait_http(f"http://127.0.0.1:{bport}/api/health", 90) or not ur.wait_http(f"http://127.0.0.1:{fport}/", 60):
            return {"error": "servers did not start", "steps": steps}
        steps["servers up"] = round(time.perf_counter() - t, 1)
        ur.wait_http(f"http://127.0.0.1:{dport}/json/version", 20)
        page = asyncio.run(wait_usable(dport, f"http://127.0.0.1:{fport}/", 60))
        if page is None:
            return {"error": "UI never became usable", "steps": steps}
        steps["UI usable after page open"] = round(page, 1)
        total = round(time.perf_counter() - t_all, 1)
        return {"files": n, "steps": steps, "total_s": total, "limit_s": LIMIT_S, "pass": total < LIMIT_S}
    finally:
        for p in reversed(procs):
            with contextlib.suppress(Exception):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                else:
                    p.kill()
        if not keep:
            time.sleep(1)
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    res = run(ap.parse_args().keep)
    print(json.dumps(res, indent=2))
    return 0 if res.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
