"""Run on the host with Python 3.10+ and httpx installed."""
import os
import time
import httpx
from pathlib import Path
env_path = Path(__file__).with_name(".env")
for line in env_path.read_text().splitlines() if env_path.exists() else []:
    if line and not line.startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)
base = "http://localhost:8000"
headers = {"x-api-key": os.environ["API_KEY"]}
with httpx.Client(base_url=base, headers=headers, timeout=30) as client:
    response = client.post("/token", json={"username": "demo" if os.getenv("DEMO_MODE", "false").lower() == "true" else os.environ["APP_USER"],
                                           "password": "demo123" if os.getenv("DEMO_MODE", "false").lower() == "true" else os.environ["APP_PASSWORD"]})
    response.raise_for_status()
    client.headers["authorization"] = "Bearer " + response.json()["access_token"]
    response = client.post("/jobs", json={"urls": ["http://mock_oem:8003/parts"],
                                        "engine": "crawl4ai", "enrich": True})
    response.raise_for_status()
    job_id = response.json()["job_id"]
    for _ in range(60):
        response = client.get("/jobs/" + job_id)
        response.raise_for_status()
        job = response.json()
        print(job["state"])
        if job["state"] in {"completed", "failed", "blocked", "login_required"}:
            print(job)
            break
        time.sleep(2)
    else:
        raise SystemExit("Job still pending; inspect service logs")
