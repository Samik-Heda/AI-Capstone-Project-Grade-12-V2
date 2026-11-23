"""Start the API on the host/port from config.toml: python -m backend.serve"""
import uvicorn

from backend import config

if __name__ == "__main__":
    s = config.load()["server"]
    uvicorn.run("backend.main:app", host=s["backend_host"], port=s["backend_port"])
