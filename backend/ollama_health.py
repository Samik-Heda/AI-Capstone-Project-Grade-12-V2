"""Background Ollama health monitor. Never call Ollama on the request path for health."""
from __future__ import annotations

import asyncio
import logging

import httpx

from . import config, power

log = logging.getLogger("growthcompass.ollama")


class OllamaStatus:
    def __init__(self) -> None:
        cfg = config.load()["ollama"]
        self.base_url: str = cfg["base_url"]
        self.narration_model: str = cfg["narration_model"]
        self.fallback_model: str = cfg["fallback_model"]
        self.quality_model: str = cfg["quality_model"]
        self.embedding_model: str = cfg["embedding_model"]
        self.interval: float = cfg["health_interval_s"]
        self.timeout: float = cfg["health_timeout_s"]
        self.reachable = False
        self.models: set[str] = set()
        self.loaded: set[str] = set()  # models Ollama has in memory (/api/ps)
        self.on_quality_cold = None  # async callback: load the quality model in the background (§18.1)
        self.warm = False  # narration model loaded in Ollama (from /api/ps, or after a warm-up/narration)
        self.on_cold = None  # async callback: re-warm when the model has been unloaded
        self._task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()

    @staticmethod
    def _present(name: str, models: set[str]) -> bool:
        return name in models or (":" not in name and f"{name}:latest" in models)

    @property
    def active_model(self) -> str:
        """The model narration will use: the fallback in battery mode when it is installed."""
        if power.battery_mode() and self._present(self.fallback_model, self.models):
            return self.fallback_model
        return self.narration_model

    @property
    def model_present(self) -> bool:
        return self._present(self.active_model, self.models)

    @property
    def quality_present(self) -> bool:
        return self._present(self.quality_model, self.models)

    @property
    def quality_warm(self) -> bool:
        return self._present(self.quality_model, self.loaded)

    @property
    def wants_quality(self) -> bool:
        """Keep the quality model loaded for free-text reading and the brief: on AC, when installed and enabled."""
        return bool(config.load().get("interpret", {}).get("prefer_quality_model", True)) and self.quality_present \
            and not power.battery_mode()

    @property
    def embedding_present(self) -> bool:
        return self._present(self.embedding_model, self.models)

    async def check(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
                self.models = {m["name"] for m in r.json().get("models", [])}
                self.reachable = True
                ps = await client.get(f"{self.base_url}/api/ps")
                loaded = {m["name"] for m in ps.json().get("models", [])} if ps.status_code == 200 else set()
                self.loaded = loaded
                self.warm = self._present(self.active_model, loaded)
        except (httpx.HTTPError, ValueError) as e:
            if self.reachable:
                log.warning("Ollama unreachable: %s", e)
            self.reachable = False
            self.models = set()
            self.loaded = set()
            self.warm = False

    async def _loop(self) -> None:
        while True:
            await self.check()
            if self.reachable and self.model_present and not self.warm and self.on_cold is not None:
                t = asyncio.create_task(self.on_cold())
                self._bg.add(t)
                t.add_done_callback(self._bg.discard)
            elif self.reachable and self.warm and self.on_quality_cold is not None and self.wants_quality \
                    and not self.quality_warm:
                t = asyncio.create_task(self.on_quality_cold())  # only once the chat model is ready
                self._bg.add(t)
                t.add_done_callback(self._bg.discard)
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        for t in list(self._bg):
            t.cancel()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def public(self) -> dict:
        return {"reachable": self.reachable, "narration_model": self.active_model,
                "model_present": self.model_present, "warm": self.warm, "battery_mode": power.battery_mode(),
                "quality_model": self.quality_model if self.quality_present else None, "quality_warm": self.quality_warm}
