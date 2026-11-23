"""Battery mode: on battery Windows throttles the CPU and a cold narration prompt took ~39 s to its first
token (vs well under 4 s on AC). Battery mode switches narration to the smaller fallback model and allows
longer waits. config.toml [ollama] battery_mode = "auto" (detect), "on" or "off"."""
from __future__ import annotations

import ctypes
import sys
import time

from . import config

_CACHE_S = 10.0
_cache: tuple[float, bool] | None = None


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def _on_battery() -> bool:
    """True only when Windows reports AC offline; unknown or non-Windows counts as AC."""
    if sys.platform != "win32":
        return False
    s = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return False
    return s.ACLineStatus == 0


def battery_mode() -> bool:
    mode = config.load()["ollama"].get("battery_mode", "auto")
    if mode in ("on", "off"):
        return mode == "on"
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > _CACHE_S:
        _cache = (now, _on_battery())
    return _cache[1]
