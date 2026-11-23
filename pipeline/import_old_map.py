"""Write frontend/public/map.json from the old project's district shapes.

Usage: python pipeline/import_old_map.py [--source reference/frontend/src/maharashtraMap.js]

The old shapes are smoother than build_map.py's and are the look the app uses. They are cropped
to the state's outline (plus a small margin) so the map fills its pane. Region names sit at the
old project's positions. map.json is committed, so this only needs re-running if the shapes change;
build_map.py still writes data/centroids.csv.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import config  # noqa: E402

DEFAULT_SOURCE = ROOT / "reference" / "frontend" / "src" / "maharashtraMap.js"
MARGIN = 14
ENTRY = re.compile(r'\{ id: (\d+), name: "([^"]+)", cx: ([\d.]+), cy: ([\d.]+), d: "([^"]+)" \}')
POINT = re.compile(r"(-?[\d.]+),(-?[\d.]+)")
# Old MapPanel.jsx region labels (x, y in the old 900x750 frame).
REGIONS = [("KONKAN", 196, 700), ("N. MAHARASHTRA", 290, 220), ("W. MAHARASHTRA", 294, 590),
           ("MARATHWADA", 490, 478), ("VIDARBHA", 640, 490)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    src = Path(ap.parse_args().source).read_text(encoding="utf-8")
    entries = [(int(i), n, float(cx), float(cy), d) for i, n, cx, cy, d in ENTRY.findall(src)]
    if sorted(e[0] for e in entries) != list(range(1, 37)):
        raise SystemExit(f"expected district ids 1..36, got {len(entries)} entries")

    pts = [(float(x), float(y)) for e in entries for x, y in POINT.findall(e[4])]
    x0 = min(p[0] for p in pts) - MARGIN
    y0 = min(p[1] for p in pts) - MARGIN
    w = max(p[0] for p in pts) + MARGIN - x0
    h = max(p[1] for p in pts) + MARGIN - y0

    def shift(m: re.Match) -> str:
        return f"{float(m.group(1)) - x0:.1f},{float(m.group(2)) - y0:.1f}"

    districts = []
    for did, name, cx, cy, d in sorted(entries):
        districts.append({
            "id": did, "name": name, "d": POINT.sub(shift, d),
            # the old labels were drawn with their baseline at cy + 4: this is their visual middle
            "label_x": round(cx - x0, 1), "label_y": round(cy + 1.5 - y0, 1),
            "parts": d.count("M"),
        })
    regions = [{"name": n, "x": round(x - x0, 1), "y": round(y - y0, 1)} for n, x, y in REGIONS]
    out = {"viewBox": f"0 0 {w:.0f} {h:.0f}", "districts": districts, "regions": regions}
    path = config.path("map_json")
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"map.json: {len(districts)} districts, viewBox {out['viewBox']}")


if __name__ == "__main__":
    main()
