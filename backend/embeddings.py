"""Ollama /api/embed client with an on-disk cache, plus the intent exemplar index (spec §7.3 tier 3).

nomic-embed-text wants task prefixes: `search_query: ` for user text, `search_document: ` for
exemplars. The exemplar index is built once in a background thread at startup (vectors come from
the `embedding_cache` table after the first run). If Ollama is down, `ready` stays False and the
resolver skips this tier."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict

import httpx
import numpy as np

from . import config, db

log = logging.getLogger("growthcompass.embeddings")

QUERY_PREFIX = "search_query: "
DOC_PREFIX = "search_document: "
BATCH = 64
VOTE_T = 0.03  # softmax temperature for the kNN vote


class EmbeddingIndex:
    def __init__(self) -> None:
        cfg = config.load()["ollama"]
        self.base_url: str = cfg["base_url"]
        self.model: str = cfg["embedding_model"]
        self.keep_alive: str = cfg["keep_alive"]
        self.enabled: bool = bool(config.load()["features"].get("embeddings", True))
        self.ready = False
        self.error: str | None = None
        self.labels: list[str] = []          # profile id per exemplar row
        self.texts: list[str] = []
        self.matrix: np.ndarray | None = None  # (n, d), L2-normalized
        self._lock = threading.Lock()
        self._built = threading.Event()
        self._qcache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._client: httpx.Client | None = None

    # ---------- low level ----------

    def _hash(self, text: str) -> str:
        return hashlib.sha1(f"{self.model}\n{text}".encode("utf-8")).hexdigest()

    def _post(self, texts: list[str], timeout: float) -> list[np.ndarray]:
        # One shared client: building a new httpx.Client per call costs ~500 ms on Windows
        # (SSL context + connection setup), versus ~40 ms for the embedding itself.
        if self._client is None:
            self._client = httpx.Client()
        r = self._client.post(f"{self.base_url}/api/embed", timeout=timeout,
                              json={"model": self.model, "input": texts, "keep_alive": self.keep_alive})
        r.raise_for_status()
        return [np.asarray(v, dtype=np.float32) for v in r.json()["embeddings"]]

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed with the document prefix, reading and filling the on-disk cache."""
        full = [DOC_PREFIX + t for t in texts]
        hashes = [self._hash(t) for t in full]
        found: dict[str, np.ndarray] = {}
        with db.connect() as con:
            for i in range(0, len(hashes), 500):
                chunk = hashes[i:i + 500]
                q = f"SELECT text_hash, vector FROM embedding_cache WHERE model = ? AND text_hash IN ({','.join('?' * len(chunk))})"
                for row in con.execute(q, [self.model, *chunk]):
                    found[row["text_hash"]] = np.frombuffer(row["vector"], dtype=np.float32)
        missing = [i for i, h in enumerate(hashes) if h not in found]
        for i in range(0, len(missing), BATCH):
            idx = missing[i:i + BATCH]
            vecs = self._post([full[j] for j in idx], timeout=120)
            with db.connect() as con:
                con.executemany("INSERT OR REPLACE INTO embedding_cache (text_hash, model, vector) VALUES (?,?,?)",
                                [(hashes[j], self.model, v.tobytes()) for j, v in zip(idx, vecs)])
            for j, v in zip(idx, vecs):
                found[hashes[j]] = v
        return np.stack([found[h] for h in hashes])

    def embed_query(self, text: str, timeout: float = 3.0) -> np.ndarray | None:
        key = text.strip().lower()
        with self._lock:
            if key in self._qcache:
                self._qcache.move_to_end(key)
                return self._qcache[key]
        try:
            v = self._post([QUERY_PREFIX + key], timeout=timeout)[0]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("query embedding failed: %s", e)
            return None
        v = v / (np.linalg.norm(v) or 1.0)
        with self._lock:
            self._qcache[key] = v
            if len(self._qcache) > 512:
                self._qcache.popitem(last=False)
        return v

    # ---------- exemplar index ----------

    def build(self) -> bool:
        """Embed all intent exemplars (cached on disk). Safe to call more than once."""
        if not self.enabled:
            self._built.set()
            return False
        try:
            ex = json.loads((config.DATA_DIR / "intent_exemplars.json").read_text(encoding="utf-8"))
            labels, texts = [], []
            for pid, phrases in ex["profiles"].items():
                for p in phrases:
                    labels.append(pid)
                    texts.append(p)
            m = self.embed_documents(texts)
            m = m / np.linalg.norm(m, axis=1, keepdims=True)
            self.labels, self.texts, self.matrix = labels, texts, m
            self.ready, self.error = True, None
            self.embed_query("warm up")  # load the model now, not on the first user message
            log.info("embedding index ready: %d exemplars", len(texts))
        except (httpx.HTTPError, ValueError, KeyError, OSError) as e:
            self.ready, self.error = False, str(e)
            log.warning("embedding index unavailable: %s", e)
        finally:
            self._built.set()
        return self.ready

    def reset_wait(self) -> None:
        """Make `wait_ready` block until the next build attempt finishes (or is skipped)."""
        if not self.ready:
            self._built.clear()

    def mark_unavailable(self) -> None:
        """Ollama or the embedding model is missing: stop anyone waiting; the resolver skips tier 3."""
        self.error = self.error or "Ollama or the embedding model is not available"
        self._built.set()

    def wait_ready(self, timeout: float = 120.0) -> bool:
        self._built.wait(timeout)
        return self.ready

    @property
    def available(self) -> bool:
        return self.enabled and self.ready

    def similarities(self, text: str) -> np.ndarray | None:
        if not self.available:
            return None
        q = self.embed_query(text)
        if q is None:
            return None
        return self.matrix @ q

    def knn(self, text: str, k: int = 5, restrict: list[str] | None = None) -> dict | None:
        """Similarity-weighted k-NN vote. Returns {top_sim, votes:[(pid, share)], neighbors}.

        Weights are exp((sim − top)/T): with raw similarities the 4 weaker neighbours (all ~0.5)
        outvoted a clearly better nearest exemplar ("wiring harness" → rubber). `restrict` limits
        the vote to some profiles (used to settle a lexicon conflict between them)."""
        sims = self.similarities(text)
        if sims is None:
            return None
        if restrict:
            mask = np.array([lab in restrict for lab in self.labels])
            sims = np.where(mask, sims, -1.0)
        top = np.argsort(-sims)[:k]
        best = float(sims[top[0]])
        votes: dict[str, float] = {}
        for i in top:
            votes[self.labels[i]] = votes.get(self.labels[i], 0.0) + float(np.exp((sims[i] - best) / VOTE_T))
        total = sum(votes.values()) or 1.0
        ranked = sorted(((p, v / total) for p, v in votes.items()), key=lambda x: -x[1])
        return {"top_sim": float(sims[top[0]]), "votes": ranked,
                "neighbors": [(self.labels[i], self.texts[i], round(float(sims[i]), 3)) for i in top]}

    def nearest_profiles(self, text: str, n: int = 3) -> list[str] | None:
        """Profiles ordered by their best-matching exemplar."""
        sims = self.similarities(text)
        if sims is None:
            return None
        best: dict[str, float] = {}
        for lab, s in zip(self.labels, sims):
            best[lab] = max(best.get(lab, -1.0), float(s))
        return [p for p, _ in sorted(best.items(), key=lambda x: -x[1])[:n]]


INDEX = EmbeddingIndex()
