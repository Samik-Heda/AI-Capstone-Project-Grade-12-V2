# UI walkthrough (§14.6)

Run in a browser at **1280×720** and **1920×1080**. Pass criteria: zero console errors or warnings
from app code, every step's expected text or state visible.

All 20 steps are automated by [`ui_check.js`](ui_check.js), which drives the real UI (clicks, typing,
pointer drags on the sliders) and compares the DOM with the API. Steps 1–14 are the P6 gate, 15–20 the P7
gate. `run_benchmarks.py` (P6/P7) runs it through [`ui_runner.py`](ui_runner.py): a fresh production build
served by `vite preview`, its own backend on free ports, headless Chrome or Edge over the DevTools
protocol, both resolutions, console errors collected outside the page too. Step 20 runs against a second
backend whose Ollama URL is a dead port (`GC_OLLAMA_URL`), so the real Ollama is never stopped.
Manual use: open the app in a fresh tab, paste `ui_check.js` into the devtools console and run
`await gcUiCheck({to: 19})` (step 20 only passes with Ollama stopped).

| # | Step | Expected |
|---|---|---|
| 1 | Open the app | Empty chat shows the example chips; the map is uncolored |
| 2 | Click **"Food processing plant, water is critical"** | "This sounds like **Food & Beverages**. I'll also make water supply matter most." with [Apply Food & Beverages] [Pick another] and an Understood line (industry, ↑↑ Water supply); the profile does not change |
| 3 | Click **Apply Food & Beverages** | Reply card (top 3); map recolors in rank tiers (#1, #2, #3, #4–5, #6–10, rest; Mumbai City/Suburban hatched), "#1"–"#5" on the map, 5 region names, rankings card led by Nashik, Pune; legend explains "2 not eligible: no industrial land"; header shows "Showing: Food & Beverages · Weights adjusted ↑" |
| 4 | Type "power is important" | "Reliable electricity now matters more …" (words, no %), Save chip; power weight rises |
| 5 | Type "now an automobile plant, power is critical" | [Apply Automobile] chip; ranking unchanged until clicked; after the click the profile is Automobile with power = critical |
| 6 | Click **Save as my profile**, type a name | "What should I call it?" then "Saved as **name**"; it appears under **My profiles** in the Profile menu |
| 7 | Type "compare Nashik and Pune" | Compare table (13 factor rows, leader bolded) from `facts.compare`; AI takeaway streams when Ollama is up (⚠ check numbers only if the checker flags it) |
| 8 | Click **Sangli**, then **Nashik**, then **Pune** | Floating popups: rank, "likely a–b", score bar, confidence, strengths, weaknesses, 13-factor breakdown bars; every number equals `GET /api/district/{id}?session_id=`; Sangli and Nashik open side by side without overlapping; dragging a popup's top bar moves it and not the map; Pune replaces the oldest (Sangli); no AI or action buttons |
| 9 | Click **Mumbai City** | Popup says "Not eligible: no industrial land" with "Include districts without industrial land" |
| 10 | Click every district, including both Mumbai districts on the main map | 36/36 open their popup |
| 11 | Open **⚖ Weights**, drag **Near a port** right | Raigad climbs; other sliders visibly rebalance and changed ones are highlighted; no digits and no input boxes on any control; every slider uses its plain label |
| 12 | Keep a popup open while dragging | The popup re-fetches and still equals `/district` (never stale) |
| 13 | Click **Reset** | Weights return to the profile's base; highlights and "weights adjusted" disappear |
| 14 | Click **⊕ Sources** | Every factor with its source and honest Sourced / Derived / Estimated status, and the ports; Esc closes it (open popups stay); a click outside closes it |
| 15 | Profile dropdown → **Build my own…** | Centered modal opens over the map |
| 16 | Type "Pharma plant needing clean water, skilled graduates; land cost does not matter" | Start from = Pharmaceuticals; Water supply and Skilled workers slide right, Cheap land slides left |
| 17 | Drag a builder slider | Top-5 preview updates |
| 18 | Save, then **Use now** | Profile is active in the session; rename and delete work from My profiles (delete asks first) |
| 19 | Toggle **◈ Clusters** on and off | Cluster cards with labelled types and the silhouette score; every district outlined in its cluster color; rank fills and "#n" stay |
| 20 | Stop Ollama, send a ranking message and an explain | Templated replies still appear; "AI note unavailable"; no console errors |
| 21 | Type "Food processing plant", click **Apply Food & Beverages**, answer the **Quick question** with "yes, a lot", click **Apply this change** (§18.2) | Follow-up chips ("Why is … first?", "Compare … and …", "Make a business brief"); the question appears and the input invites an answer; the answer previews the effect ("would …") and changes nothing until **Apply this change** |
| 22 | Click the **Compare …** follow-up chip | Compare table, with "Tell me about …" offered next |
| 23 | Type "washing powder and dish bars" (§18.1) | "AI · thinking it over…" under the reply; then either a suggestion marked "Understood with AI reading" with an Apply chip, or nothing more; the map and profile do not change |
| 24 | Click **▤ Business brief** (§18.3) | The brief opens with the same top 3 as the map, priorities, risks, next steps and sources at once; four paragraphs fill in; × closes it |
| 25 | Print preview from the brief | Only the brief prints (dark-on-white), not the app |
| 26 | On Electronics, type "any other factors i should be concerned about?" (§19.1) | Instant reply with "Also check (not in our data)"; then an AI answer, with any general advice in its own "General advice — please verify" box; a Tell me more chip; the map does not change |
| 27 | Click **Tell me more** | A "Detailed answer (advanced model)" streams under the same reply; the chip goes away |
| 28 | Type "what if I didn't need a port", then **Apply this change** (§19.2) | The preview says what would move and "Nothing has changed yet"; the map changes only after Apply |
| 29 | Open **▤ Business brief** (§19.4) | An "Also check before investing" checklist for the industry |
