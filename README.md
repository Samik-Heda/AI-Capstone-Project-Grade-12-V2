# GrowthCompass v2

Ranks Maharashtra's 36 districts for industrial investment with a transparent, deterministic
weighted-scoring model. Describe what you want to build in plain English; the app picks a starting
industry preset, moves the factor sliders for you, ranks the districts and recolors the map. An optional
local LLM (Ollama) only narrates pre-computed facts, and every narration is checked for groundedness.

## Requirements
- Python 3.12, Node 24
- Ollama (optional; the app works without it): `ollama pull qwen2.5:3b-instruct` and `ollama pull nomic-embed-text`
- Chrome or Edge (only for the automated UI walkthrough in the benchmarks)

## Run
```
start.bat
```
It reads ports from `config.toml`, installs missing dependencies, builds `frontend/public/map.json` and
`data/gc.db` if they are missing, starts the API and the Vite dev server, and opens the browser.

Manual equivalent:
```
pip install -r requirements.txt
python pipeline/build_map.py     # GeoJSON -> frontend/public/map.json + data/centroids.csv
python pipeline/seed.py          # CSVs + fields.json + presets -> data/gc.db
python -m backend.serve          # API on backend_port (8001)
npm --prefix frontend install && npm --prefix frontend run dev   # UI on frontend_port (5176)
```

## Configuration
`config.toml` is the single source of truth for ports (backend 8001, frontend 5176), the Ollama URL
(`http://127.0.0.1:11434`, never `localhost`), model names (narration `qwen2.5:3b-instruct`, fallback
`qwen2.5:1.5b-instruct`, embeddings `nomic-embed-text`), timeouts (4 s to the first token, 30 s total),
feature flags and the calibrated intent thresholds (τ, δ). Two environment variables exist only for the
test harness: `GC_BACKEND_PORT` (the Vite proxy target) and `GC_OLLAMA_URL` (point the API at another,
or a dead, Ollama).

## Using it
- **Chat** (left): "food processing plant, water is critical" → *This sounds like Food & Beverages* with
  **[Apply Food & Beverages]**. Chat never switches the industry by itself. Then adjust in words ("power is
  important", "ignore crop output"), compare ("compare Nashik and Pune") or ask about a district
  ("explain Sangli"). Ranking replies are small cards (what changed, the top 3, who leads), and every reply
  shows what the language step understood (industry and how it was matched, factors, districts). Compare,
  explain, applied-industry and adjustment replies add an AI note automatically; a note the groundedness
  checker flags shows ⚠ check numbers.
- **Advisor** (§18): replies end with follow-up chips ("Why is Nashik first?", "Compare Nashik and Pune",
  "Make a business brief"), a heads-up when the leader is weak on something that matters, and now and then one
  plain question ("Will your plant use a lot of water?") whose answer would change the top 3. Answer in your own
  words; the reply previews the effect ("That would move Sangli to #1") with **Apply this change** / **Keep as
  is**. When the rules cannot read a message ("bamboo sticks for incense"), the local AI tries ("thinking it
  over…") and, if it is sure, suggests an industry with an Apply chip. It never changes anything by itself.
- **Questions** (§19): ask anything ("any other factors I should be concerned about?", "do I need a big plot?").
  You get an instant answer with the industry's "Also check (not in our data)" list, then the AI's answer in plain
  words; its own general advice is shown apart as "General advice — please verify". **Tell me more** asks the
  larger model for a detailed answer (slower, ~30–90 s). Questions never change your priorities.
- **What-if**: "what if land were cheaper?", "what if I didn't need a port" previews how the top 3 would change,
  with **Apply this change** / **Keep as is**.
- **▤ Business brief** (header, or say "make a business brief"): a one-page summary of the top 3 districts,
  your priorities and answers, risks, next steps and data sources. Four short paragraphs are written by the
  local AI (checked against the data, else replaced by plain text), plus "Also check before investing" for the
  industry; **Print / Save as PDF** prints only the brief.
- **Stale server**: if the server is running older code than the files on disk, a banner asks you to restart
  (start.bat stops old servers first).
- **Map**: rank tiers (#1, #2, #3, #4–5, #6–10, the rest); hatched = not eligible (no industrial land).
  Scroll to zoom, drag to pan. Click a district for a floating popup you can drag by its top bar: rank,
  score, confidence band, strengths, weaknesses and the 13-factor breakdown. Two can be open side by side.
- **⚖ Weights** (bottom right): 13 visual sliders, no numbers. Dragging one rebalances the others.
- **Profile dropdown → Build my own…**: one sentence → starting preset + slider positions, a live top-5
  preview, Save / Use now. Saved profiles can be renamed or deleted from **My profiles**.
- **◈ Clusters**: district types from K-means over all 13 factors, shown as colored outlines with cards
  and the silhouette score.
- **⊕ Sources**: every factor's source and honest status (Sourced / Derived / Estimated), the known data
  limitations, an About note and the last benchmark run (P@5, intent accuracy, groundedness, …), all read
  from files the harness and the dataset produce.
- **If something fails**: a banner appears while the server is unreachable and clears when it is back; if the
  server restarted, the chat starts a fresh session on the same profile and says so. A district popup that
  cannot load shows the reason and a Retry button. Without Ollama, every reply is still the templated one.
- **Keyboard**: Tab reaches every district (Enter or Space opens its popup, Escape closes it and returns focus),
  slider (arrow keys), chip and menu; dialogs keep focus inside until closed.

## API (all under `/api`, JSON; errors are `{error: {code, message}}`)
| Method | Path | Notes |
|---|---|---|
| GET | `/health` | status, Ollama reachability/model/warm (background-checked every 30 s), embeddings, dataset hash |
| GET | `/meta` | field registry, districts, profiles, ports, About text and known data limitations (`data/about.json`) |
| POST | `/rank` | stateless ranking for `{weights, constraints?}` (previews) |
| GET | `/district/{id}?session_id=` | district facts under the session's weights (or `?profile_id=`) |
| POST · GET | `/session` · `/session/{id}` | create / read a server-side session (in memory, 2-hour TTL) |
| POST | `/message` | `{session_id, text}` → `{turn_id, kind, reply_md, chips, state, facts, narration}` |
| POST | `/session/{id}/profile` | `{profile_id}` from the menu or an Apply chip; clears modifiers and overrides, then applies a pending suggestion's modifiers |
| POST | `/session/{id}/weights` | `{overrides: {field: share}, constraints?}` from the sliders |
| POST | `/session/{id}/save_profile` | `{name}`: saves the current effective weights as a user profile and makes it the session profile |
| POST | `/session/{id}/reset` | back to the profile's base weights |
| POST | `/explain` | `{session_id, district_id}` → templated explanation (narrate it via `/narrate`) |
| POST | `/narrate` | `{session_id, turn_id, quality?}` → **SSE**: `token {t}` …, then `done {grounding, model, ms, …}` or `error {message}` |
| POST | `/brief` | `{session_id}` → **SSE**: `brief {…}`, then per AI paragraph `section`, `token {id, t}`, `section_done {id, text, source}`, then `done` (§18.3) |
| POST | `/interpret` | `{session_id, turn_id}` → `{status, message?, result, info}`: the LLM's reading of a turn the rules could not read (§18.1); `message` is a suggestion or preview with Apply chips, never an applied change |
| GET | `/profiles` | `{preset: [...], user: [...]}` |
| POST | `/profiles/draft` | `{text, base_preset_id?}` → `{base_preset_id, preset_confidence, modifiers, weights, constraints, rationale_draft, preview_top5, …}` |
| POST | `/profiles` | `{name, weights, constraints?, description?, base_profile_id?, provenance?}` → `{profile}`; the rationale is generated server-side |
| PUT · DELETE | `/profiles/{id}` | rename/edit or delete a user profile (presets: 403) |
| POST | `/clusters` | `{k?}` (3–6) → `{k, silhouette, silhouettes, clusters: [{id, name, label, color, district_ids, centroid_z}]}` |
| GET | `/benchmarks` | the last full `run_benchmarks.py` run (`eval/benchmark_summary.json`): headline tiles and every gate, or `{available: false}` |

## How it works
- **Scoring** (`backend/scoring.py`, `facts.py`): weighted sum of 13 normalized factors, eligibility
  constraints, rank confidence from 300 Dirichlet-perturbed weight vectors (Monte Carlo), strengths and
  weaknesses only from factors that really are strong or weak.
- **12 presets** (`data/profiles_presets.json`), checked against ground truth (P@5, hit@10, distinctness).
- **Language understanding without an LLM** (`backend/router.py`, `intent.py`, `modifiers.py`): ordered
  routing rules; industry from a whole-token lexicon, typo tolerance, then embedding kNN over
  `data/intent_exemplars.json` with calibrated thresholds, and a clarify with chips when none is confident;
  priorities from factor synonyms, intensity cues (exclude ×0, low ×0.4, high ×1.8, critical ×3.0) and
  negation scope. Without Ollama the embedding tier is skipped and everything else still works.
- **Profile builder** (`backend/profiles.py`): the same resolver picks the starting preset (General
  Manufacturing if none), the modifier parser reads the priorities, clauses with no factor word are compared
  with per-factor exemplar phrases (`data/field_exemplars.json`, cosine ≥ 0.6 → high), and the weights come
  from the same `modifiers.apply` that chat uses.
- **Clusters** (`backend/clusters.py`): z-scored, unweighted factors; KMeans for k = 3…6 (n_init 20,
  random_state 7); k chosen by silhouette; unique labels from each centroid's top and bottom factors.
- **Advisor, AI reading and brief** (§18, `backend/advisor.py`, `interpret.py`, `brief.py`): the advisor is
  rule-based (questions are asked only when an answer would change the top 3). The AI reading is a JSON-schema
  call (`{industry}` or `{answer}`) validated against fixed lists; it uses `qwen2.5:7b-instruct` once that is
  loaded on AC (the health loop loads it in the background, ~95 s cold), else the 3B model. The brief's
  paragraphs use the same 7B model on AC. One Ollama request runs at a time (a shared lane).
- **Narration** (`backend/narrate.py`): a 60–110-token fact sheet per reply, streamed from
  `qwen2.5:3b-instruct` over SSE. The groundedness checker verifies every number, district name and
  "X is better than Y on Z" claim against the sheet; the `done` event carries the verdict. The model is
  warmed up at startup; any failure becomes an `error` event and the templated reply stands.
  Out-of-scope questions get a fixed refusal (`backend/scope.py`) and change nothing.

## Tests and benchmarks
```
python -m pytest
python eval/run_benchmarks.py --llm            # every phase gate (P0–P10), ~90 min with live Ollama
python eval/run_benchmarks.py --only 10 --llm  # just the P10 (conversation) gates
python eval/run_benchmarks.py --only 9 --llm   # just the P9 (advisor) gates, ~20 min
python eval/interpret_eval.py                  # AI reading of free text: accuracy and latency per split
python eval/run_benchmarks.py --phase 4        # cumulative: P0–P4 only
python eval/profile_eval.py -v                 # profile builder corpus (§14.3)
python eval/narration_eval.py                  # chat suite, prints every narration and its grounding
python eval/ui_runner.py                       # headless UI walkthrough, steps 1–20, both resolutions
python eval/ui_runner.py --demo 3              # the 5-minute demo script (spec §16), 3 runs in a row
python eval/cold_start.py                      # fresh copy of the repo → npm install, seed, servers → usable UI
python eval/intent_eval.py --calibrate         # re-fit τ/δ on the calibration split
```
The harness prints a gate table, writes `eval/results/<timestamp>.json`, appends to `STAGE_LOG.md` and, after a
full run, writes `eval/benchmark_summary.json` for the Data modal. The live narration gates (first-token
latency) assume the reference laptop on mains power: on battery its CPU runs at about 1.6 GHz and prompt
processing is several times slower.
The UI walkthrough builds the frontend, starts its own backend and `vite preview` on free ports and drives
headless Chrome/Edge; step 20 (Ollama stopped) uses a second backend pointed at a dead Ollama port.

## Data honesty
Every field's provenance status (Sourced / Derived / Estimated) lives in `data/fields.json` and is shown in
the ⊕ Sources modal. Values marked Estimated are approximations for demonstration; verify them before a real
investment decision.
