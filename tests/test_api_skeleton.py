"""Phase 0: /health and /meta."""
import statistics
import time

from backend import config


def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["ollama"]) == {"reachable", "narration_model", "model_present", "warm", "battery_mode",
                                   "quality_model", "quality_warm"}
    assert body["ollama"]["narration_model"] == config.load()["ollama"]["narration_model"]
    assert body["dataset_hash"]


def test_ollama_url_is_ipv4():
    assert "localhost" not in config.load()["ollama"]["base_url"]
    assert config.load()["ollama"]["base_url"].startswith("http://127.0.0.1")


def test_health_p50_under_20ms(client):
    times = []
    for _ in range(50):
        t = time.perf_counter()
        client.get("/api/health")
        times.append((time.perf_counter() - t) * 1000)
    assert statistics.median(times) < 20, f"p50 {statistics.median(times):.1f} ms"


def test_meta_shape(client):
    body = client.get("/api/meta").json()
    assert len(body["fields"]) == 13
    assert len(body["districts"]) == 36
    assert len(body["ports"]) == 5
    assert body["status_note"].startswith("Values marked Estimated")


def test_unknown_route_error_shape(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_404"
