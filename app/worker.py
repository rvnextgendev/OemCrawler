"""Run exactly one worker in this demo; sessions and documents are tenant scoped."""
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from app.common import r, fernet, INTERNAL, audit, pace, fingerprint
from app.enrichment import enrich
from app.policy import ALLOWED_HOST, allowed_url, blocked, in_window

async def session(tenant, client):
    key = "session:" + tenant + ":" + ALLOWED_HOST
    encrypted = await r.get(key)
    if encrypted:
        return json.loads(fernet.decrypt(encrypted.encode()))
    response = await client.post("http://browser_service:8002/login")
    response.raise_for_status()
    state = response.json()
    await r.set(key, fernet.encrypt(json.dumps(state).encode()).decode(), ex=900)
    return state

async def fetch(url, state, engine, client):
    if engine == "playwright":
        result = await client.post("http://browser_service:8002/fetch",
                                  json={"url": url, "storage_state": state})
        result.raise_for_status()
        return result.json()
    from crawl4ai import CrawlerRunConfig, CacheMode
    crawler = await pool.get(state)
    result = await crawler.arun(url=url, config=CrawlerRunConfig(
        proxy_config={"server": "http://egress_proxy:8080"},
        cache_mode=CacheMode.BYPASS, page_timeout=30000))
    return {"status": result.status_code or 502, "html": (result.html or "")[:2000000]}

class BrowserPool:
    """Bounded live Crawl4AI browsers; state fingerprint prevents cross-account reuse.

    Single-worker sequential use only. Redis contains encrypted state, never live
    browser objects. A changed/expired login produces a new pool entry.
    """
    def __init__(self):
        self.entries = {}

    async def get(self, state):
        from crawl4ai import AsyncWebCrawler, BrowserConfig
        key = fingerprint(json.dumps(state, sort_keys=True))
        if key in self.entries:
            return self.entries[key]
        if len(self.entries) >= 2:
            oldest = next(iter(self.entries))
            await self.entries.pop(oldest).close()
        crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, storage_state=state))
        await crawler.start()
        self.entries[key] = crawler
        return crawler

pool = BrowserPool()

async def acquire(job):
    tenant = job["tenant"]
    async with httpx.AsyncClient(timeout=90, headers={"x-internal-secret": INTERNAL}) as client:
        for url in dict.fromkeys(job["urls"]):
            if not allowed_url(url):
                raise RuntimeError("Policy changed; origin denied")
            key = "cache:" + tenant + ":" + fingerprint(url + str(job["enrich"]) + os.getenv("ENRICH_PROVIDER", "local") + os.getenv("LLM_MODEL", ""))
            cached = await r.get(key)
            if cached:
                job["results"].append({**json.loads(cached), "cached": True})
                continue
            if await r.exists("blocked:" + ALLOWED_HOST):
                job["state"] = "blocked"
                return
            # Never start outside the window; recheck after waiting for pacing.
            if not in_window():
                job["state"] = "deferred"
                return
            await pace(ALLOWED_HOST)
            if not in_window():
                job["state"] = "deferred"
                return
            state = await session(tenant, client)
            result = await fetch(url, state, job["engine"], client)
            if blocked(result["status"], result["html"]):
                await r.set("blocked:" + ALLOWED_HOST, "1", ex=300)
                await r.delete("session:" + tenant + ":" + ALLOWED_HOST)
                job["state"] = "blocked"
                await audit("acquisition_blocked", job=job["id"], status=result["status"])
                return
            if result["status"] >= 400:
                raise RuntimeError("Upstream HTTP " + str(result["status"]))
            soup = BeautifulSoup(result["html"], "html.parser")
            # A successful HTTP 200 login page is NOT a successful acquisition.
            if soup.select_one("input[type=password]") or not soup.select_one("#parts"):
                job["state"] = "login_required"
                await r.delete("session:" + tenant + ":" + ALLOWED_HOST)
                return
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            content_hash = hashlib.sha256(result["html"].encode()).hexdigest()
            dest = Path("/data") / tenant / (content_hash + ".html")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(result["html"], encoding="utf-8")
            item = {"url": url, "sha256": content_hash, "stored_path": str(dest),
                    "text": text, "fetched_at": time.time(), "cached": False}
            if job["enrich"]:
                try:
                    item["enrichment"] = await enrich(text, soup.title.get_text() if soup.title else "")
                except Exception as exc:
                    item["enrichment"] = {"error": type(exc).__name__, "needs_review": True}
            await r.set(key, json.dumps(item), ex=3600)
            job["results"].append(item)
            await audit("page_saved", job=job["id"], sha256=content_hash)
        job["state"] = "completed"

async def run():
    # Recover interrupted work. Safe ONLY with one worker process.
    while await r.rpoplpush("processing", "frontier"):
        pass
    while True:
        job_id = await r.brpoplpush("frontier", "processing", timeout=5)
        if not job_id:
            continue
        raw = await r.get("job:" + job_id)
        if not raw:
            await r.lrem("processing", 1, job_id)
            continue
        job = json.loads(raw)
        if job["state"] == "completed":
            await r.lrem("processing", 1, job_id)
            continue
        job["state"] = "running"
        job["results"] = []
        await r.set("job:" + job_id, json.dumps(job), ex=86400)
        lease = r.lock("lock:acquire:" + job["tenant"] + ":" + ALLOWED_HOST,
                       timeout=660, blocking_timeout=2)
        if not await lease.acquire():
            await r.lrem("processing", 1, job_id)
            await r.lpush("frontier", job_id)
            await asyncio.sleep(2)
            continue
        try:
            await asyncio.wait_for(acquire(job), timeout=600)
        except Exception as exc:
            job["state"] = "failed"
            job["error"] = type(exc).__name__  # Avoid secrets from upstream errors.
            await audit("job_failed", job=job_id, error=type(exc).__name__)
        finally:
            await lease.release()
        async with r.pipeline(transaction=True) as pipe:
            pipe.set("job:" + job_id, json.dumps(job), ex=86400)
            pipe.lrem("processing", 1, job_id)
            await pipe.execute()
        if job["state"] == "deferred":
            await asyncio.sleep(30)
            await r.lpush("frontier", job_id)

if __name__ == "__main__":
    asyncio.run(run())
