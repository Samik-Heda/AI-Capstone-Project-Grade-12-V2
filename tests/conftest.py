import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.main import app

# Tests never load the narration model; live-LLM checks live in eval/narration_eval.py (--llm).
config.load()["features"]["warmup"] = False
# Timeout tests assume AC limits; battery mode would stretch them whenever the laptop is unplugged.
config.load()["ollama"]["battery_mode"] = "off"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
