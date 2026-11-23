"""GrowthCompass v2 FastAPI app. Run: uvicorn backend.main:app --host 127.0.0.1 --port 8001"""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException

from . import benchmarks, brief, clusters, config, db, facts, interpret, narrate, profiles, router, scoring, templates
from .embeddings import INDEX
from .models import (BriefRequest, ClustersRequest, ExplainRequest, InterpretRequest, MessageRequest, NarrateRequest, ProfileCreate,
                     ProfileDraftRequest, ProfileSelect, ProfileUpdate, RankRequest, SaveProfileRequest, WeightsUpdate)
from .ollama_health import OllamaStatus
from .session import STORE, Session


def code_fingerprint() -> str:
    """§19.6 mtimes and sizes of the server's code, config and data files. /api/health compares it with
    the value taken at startup, so the UI can say when this server is running older code."""
    root = config.DATA_DIR.parent
    files = sorted([*(root / "backend").glob("*.py"), root / "config.toml", *config.DATA_DIR.glob("*.json")])
    parts = [f"{f.name}:{f.stat().st_mtime_ns}:{f.stat().st_size}" for f in files if f.exists()]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


STARTED_CODE = code_fingerprint()


async def _embedding_index_loop(status: OllamaStatus) -> None:
    """Build the exemplar index once Ollama has the embedding model; retry on the health cadence."""
    while INDEX.enabled and not INDEX.ready:
        if status.reachable and status.embedding_present:
            await asyncio.to_thread(INDEX.build)
        else:
            INDEX.mark_unavailable()
        if not INDEX.ready:
            await asyncio.sleep(status.interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.path("db").exists():
        raise RuntimeError("data/gc.db missing: run `python pipeline/seed.py` first")
    status = OllamaStatus()
    narrator = narrate.Narrator.from_config()
    app.state.ollama, app.state.narrator = status, narrator
    feats = config.load()["features"]
    if feats.get("narration", True) and feats.get("warmup", True):
        # Background warm-up at startup and whenever the health loop sees the model unloaded (§8.1).
        status.on_cold = lambda: narrator.warm_up(status)
        status.on_quality_cold = lambda: narrator.warm_quality(status, interpret.industry_system())
    await status.check()  # one check at startup; afterwards only the background loop
    status.start()
    INDEX.reset_wait()
    embed_task = asyncio.create_task(_embedding_index_loop(status))
    # KMeans for k = 3…6 takes ~1 s (several on battery); fill the cache so the first Clusters toggle is instant.
    cluster_task = asyncio.create_task(asyncio.to_thread(clusters.compute))
    yield
    cluster_task.cancel()
    embed_task.cancel()
    with suppress(asyncio.CancelledError):
        await embed_task
    await status.stop()
    await narrator.aclose()


app = FastAPI(title="GrowthCompass v2", lifespan=lifespan)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return _error(exc.status_code, f"http_{exc.status_code}", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    msgs = [e["msg"].removeprefix("Value error, ") for e in exc.errors()]
    return _error(422, "validation_error", "; ".join(msgs))


@app.get("/api/health")
async def health(request: Request):
    st: OllamaStatus = request.app.state.ollama
    return {
        "status": "ok",
        "ollama": st.public(),
        "embeddings": {"ready": INDEX.ready, "model": INDEX.model, "exemplars": len(INDEX.labels)},
        "dataset_hash": db.meta_value("dataset_hash"),
        "code": STARTED_CODE, "code_stale": code_fingerprint() != STARTED_CODE,
    }


@app.get("/api/meta")
async def meta():
    return {
        "fields": db.fields(),
        "display_only": db.registry()["display_only"],
        "status_note": db.registry()["status_note"],
        "districts": [
            {k: d[k] for k in ("id", "name", "region", "metro_core", "alt_names")} for d in db.districts()
        ],
        "profiles": db.profiles(),
        "ports": db.ports(),
        "nearest_port": json.loads(db.meta_value("nearest_port") or "{}"),
        "about": json.loads((config.DATA_DIR / "about.json").read_text(encoding="utf-8")),
    }


@app.get("/api/benchmarks")
async def benchmark_summary():
    """Last `run_benchmarks.py` summary for the Data modal; the app computes none of these numbers."""
    return benchmarks.summary()


DEFAULT_PROFILE = "preset:general_manufacturing"


@app.post("/api/rank")
def rank(req: RankRequest):
    """Stateless ranking for previews (sliders, profile builder)."""
    c = req.constraints.model_dump() if req.constraints else None
    return scoring.rank(req.weights, c)


@app.get("/api/district/{district_id}")
def district(district_id: int, profile_id: str = DEFAULT_PROFILE, session_id: str | None = None):
    """District facts under the session's effective weights (`?session_id=`), else under a profile."""
    d = next((x for x in db.districts() if x["id"] == district_id), None)
    if d is None:
        raise HTTPException(404, f"unknown district {district_id}")
    s = _session(session_id) if session_id else None
    if s is not None and s.profile_id:
        ranking, w = s.ranking, s.weights
        prof_view = {"id": s.profile_id, "kind": s.profile_kind, "name": s.profile_name}
    else:
        prof = scoring.load_profile(profile_id)
        if prof is None:
            raise HTTPException(404, f"unknown profile {profile_id}")
        res = scoring.rank(prof["weights"], prof["constraints"])
        ranking, w = res["ranking"], res["weights"]
        prof_view = {k: prof[k] for k in ("id", "kind", "name")}
    entry = next(e for e in ranking if e["district_id"] == district_id)
    strengths = facts.strengths(district_id, w)
    return {
        "district": {k: d[k] for k in ("id", "name", "region", "metro_core", "alt_names", "population")},
        "profile": prof_view,
        "weights": w,
        "values": facts.district_values(district_id, w),
        "entry": entry,
        "strengths": strengths,
        "strengths_note": None if strengths else facts.NO_STRENGTHS,
        "weaknesses": facts.weaknesses(district_id, w),
        "why_not_first": facts.why_not_first(district_id, ranking, w),
        "eligible": entry["eligible"],
        "reason": entry["reason"],
    }


# ---------- sessions and chat (Phase 4) ----------

def _session(sid: str) -> Session:
    s = STORE.get(sid)
    if s is None:
        raise HTTPException(404, f"unknown or expired session {sid}")
    return s


def _narration_ready(request: Request) -> bool:
    st: OllamaStatus = request.app.state.ollama
    return bool(config.load()["features"].get("narration", True) and st.reachable and st.model_present)


@app.post("/api/session")
def create_session():
    s = STORE.create()
    return {"session_id": s.id, "state": s.public()}


@app.get("/api/session/{sid}")
def get_session(sid: str):
    return {"state": _session(sid).public()}


@app.post("/api/message")
def message(req: MessageRequest, request: Request):
    s = _session(req.session_id)
    return router.handle(s, req.text, narration_ready=_narration_ready(request))


@app.post("/api/session/{sid}/profile")
def select_profile(sid: str, req: ProfileSelect):
    """From the preset menu or an Apply chip: clears modifiers and overrides. If this is the profile
    the chat just suggested, its modifiers ("water is critical") are applied on top (§11); any
    other profile starts clean."""
    s = _session(sid)
    if scoring.load_profile(req.profile_id) is None:
        raise HTTPException(404, f"unknown profile {req.profile_id}")
    s.set_profile(req.profile_id, apply_pending=True)
    s.pending_clarify = None
    return {"state": s.public()}


@app.post("/api/session/{sid}/save_profile")
def save_session_profile(sid: str, req: SaveProfileRequest):
    """Save the current effective weights as a user profile and make it the session profile."""
    s = _session(sid)
    if not s.profile_id:
        raise HTTPException(409, "select a profile before saving")
    p = router.save_current(s, req.name)
    return {"state": s.public(), "profile": p}


@app.post("/api/session/{sid}/weights")
def set_weights(sid: str, req: WeightsUpdate):
    s = _session(sid)
    if not s.profile_id:
        raise HTTPException(409, "select a profile before adjusting weights")
    s.set_overrides(req.overrides, req.constraints)
    return {"state": s.public()}


@app.post("/api/session/{sid}/reset")
def reset_session(sid: str):
    s = _session(sid)
    s.reset()
    return {"state": s.public()}


@app.post("/api/explain")
def explain(req: ExplainRequest, request: Request):
    s = _session(req.session_id)
    if not any(d["id"] == req.district_id for d in db.districts()):
        raise HTTPException(404, f"unknown district {req.district_id}")
    router.ensure_ranking(s)
    reply = templates.explain(req.district_id, s.ranking, s.weights, s.profile_name, [])
    s.focus_district = req.district_id
    fact = {"district_id": req.district_id}
    turn = s.add_turn("assistant", reply, "explain_district", fact,
                      s.narration_ctx("explain_district", None, fact, focus=[], mentioned=[req.district_id]))
    return {"turn_id": turn.turn_id, "reply_md": reply, "state": s.public(),
            "narration": {"available": _narration_ready(request)}}


@app.post("/api/narrate")
async def narrate_turn(req: NarrateRequest, request: Request):
    """SSE: `token` {t} … then `done` {grounding, model, ms, first_token_ms} or `error` {message}.
    Every failure (offline, timeout, unknown model) is an `error` event; the templated reply stands."""
    s = _session(req.session_id)
    turn = s.find_turn(req.turn_id)
    if turn is None or turn.role != "assistant":
        raise HTTPException(404, f"unknown turn {req.turn_id}")
    body = narrate.narrate_events(request.app.state.narrator, request.app.state.ollama, turn, req.quality)
    return StreamingResponse(body, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/interpret")
async def interpret_turn(req: InterpretRequest, request: Request):
    """§18.1 LLM fallback for a turn the rules could not read. Returns {status, message, info}:
    status "suggested" adds `message` (a suggestion or preview turn with Apply chips; nothing is applied),
    "none" when the LLM had nothing valid, "error" when it was offline, busy or timed out."""
    s = _session(req.session_id)
    turn = s.find_turn(req.turn_id)
    if turn is None or turn.role != "assistant":
        raise HTTPException(404, f"unknown turn {req.turn_id}")
    if not turn.interpret or turn.interpret.get("done"):
        return {"status": "none", "message": None, "info": {"error": "nothing to interpret"}}
    turn.interpret["done"] = True
    result, info = await interpret.ask(request.app.state.narrator, request.app.state.ollama, turn.interpret)
    msg = interpret.apply_result(s, turn, result)
    status = "suggested" if msg else ("error" if info.get("error") else "none")
    return {"status": status, "message": msg, "result": result, "info": {k: v for k, v in info.items() if k != "raw"}}


@app.post("/api/brief")
async def business_brief(req: BriefRequest, request: Request):
    """§18.3 SSE: `brief` {deterministic sections} → per AI section `section`, `token` {id, t}, `section_done`
    {id, text, source} → `done`. A section that fails the groundedness check is replaced by its template."""
    s = _session(req.session_id)
    body = brief.events(request.app.state.narrator, request.app.state.ollama, s)
    return StreamingResponse(body, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- profiles: user CRUD and the plain-language draft builder (§9) ----------

@app.get("/api/profiles")
def list_profiles():
    return profiles.list_all()


@app.post("/api/profiles/draft")
def draft_profile(req: ProfileDraftRequest):
    if req.base_preset_id is not None:
        p = profiles.get(req.base_preset_id)
        if p is None or p["kind"] != "preset":
            raise HTTPException(422, f"unknown preset {req.base_preset_id}")
    return profiles.draft(req.text, req.base_preset_id)


@app.post("/api/profiles", status_code=201)
def create_profile(req: ProfileCreate):
    c = req.constraints.model_dump() if req.constraints else None
    base = req.base_profile_id
    if base is not None and profiles.get(base) is None:
        raise HTTPException(422, f"unknown base profile {base}")
    # §11: the rationale is generated server-side from the base preset and the changes
    mods = (req.provenance or {}).get("modifiers") or None
    rationale = req.rationale or profiles.rationale_for(base, scoring.normalize_weights(req.weights), modifiers=mods)
    return {"profile": profiles.create(req.name, req.weights, c, rationale, req.description, base, req.provenance)}


@app.put("/api/profiles/{pid}")
def update_profile(pid: str, req: ProfileUpdate):
    patch = req.model_dump(exclude_unset=True)
    if req.constraints is not None:
        patch["constraints"] = req.constraints.model_dump()
    try:
        p = profiles.update(pid, patch)
    except profiles.ProfileForbidden:
        raise HTTPException(403, "preset profiles are read-only")
    if p is None:
        raise HTTPException(404, f"unknown profile {pid}")
    STORE.rename_profile(pid, p["name"])
    return {"profile": p}


@app.delete("/api/profiles/{pid}", status_code=204)
def delete_profile(pid: str):
    try:
        ok = profiles.delete(pid)
    except profiles.ProfileForbidden:
        raise HTTPException(403, "preset profiles are read-only")
    if not ok:
        raise HTTPException(404, f"unknown profile {pid}")
    return Response(status_code=204)


# ---------- clusters (§10) ----------

@app.post("/api/clusters")
def cluster_districts(req: ClustersRequest | None = None):
    return clusters.compute(req.k if req else None)
