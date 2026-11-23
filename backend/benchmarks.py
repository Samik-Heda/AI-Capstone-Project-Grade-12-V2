"""Benchmark summary for the Data modal (spec §16 step 9, P8 "benchmark report page").

eval/run_benchmarks.py writes eval/benchmark_summary.json after every full run; this module only reads
it. Every number shown in the UI therefore comes from the harness (spec §17), never from the app."""
from __future__ import annotations

import json

from .config import ROOT

SUMMARY = ROOT / "eval" / "benchmark_summary.json"


def summary() -> dict:
    try:
        data = json.loads(SUMMARY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False,
                "message": "No benchmark run recorded yet. Run: python eval/run_benchmarks.py --llm"}
    return {"available": True, **data}
