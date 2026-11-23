"""Phase 8: About/limitations in /meta, the benchmark summary endpoint, README vs code."""
import json

from backend import benchmarks


def test_meta_serves_about_and_limitations(client):
    about = client.get("/api/meta").json()["about"]
    assert len(about["limitations"]) == 4  # spec §4.7
    assert any("Census 2011" in t for t in about["limitations"])
    assert about["about"]


def test_benchmarks_without_a_run(client, tmp_path, monkeypatch):
    monkeypatch.setattr(benchmarks, "SUMMARY", tmp_path / "missing.json")
    body = client.get("/api/benchmarks").json()
    assert body["available"] is False
    assert "run_benchmarks.py" in body["message"]


def test_benchmarks_passes_the_harness_summary_through(client, tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"timestamp": "t", "llm": True, "passed": 1, "total": 2,
                             "headline": [{"label": "Mean P@5", "value": "0.76", "target": "≥ 0.70", "pass": True}],
                             "gates": []}), encoding="utf-8")
    monkeypatch.setattr(benchmarks, "SUMMARY", f)
    body = client.get("/api/benchmarks").json()
    assert body["available"] is True and body["passed"] == 1 and body["headline"][0]["value"] == "0.76"


def test_benchmarks_bad_file_is_unavailable(client, tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(benchmarks, "SUMMARY", f)
    assert client.get("/api/benchmarks").json()["available"] is False


def test_readme_matches_the_code():
    from eval.run_benchmarks import readme_mismatches
    assert readme_mismatches() == []
