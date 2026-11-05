"""JWT/RBAC and tenant-scoped job API. Separate Redis worker owns acquisition."""
import hmac
import json
import os
import time
import uuid
import jwt
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from app.common import r, internal, principal, SECRET, audit
from app.policy import allowed_url
from app.site_profiles import resolve, normalize as normalize_site, metadata, public_profile
from app.common import fingerprint
from app.policy import public_addresses
import asyncio
import socket
from urllib.parse import urlsplit

app = FastAPI(title="Internal orchestrator", dependencies=[Depends(internal)])

class Login(BaseModel):
    username: str
    password: str

class Crawl(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)
    engine: str = Field(default="auto", pattern="^(auto|crawl4ai|playwright|public_http)$")
    site_id: str | None = Field(default=None, max_length=64)
    max_pages: int = Field(default=5, ge=1, le=20)
    max_depth: int = Field(default=1, ge=0, le=2)
    enrich: bool = False

@app.post("/token")
async def token(body: Login, x_tenant: str = Header()):
    # Demo account; replace with your IdP. Never reuse OEM passwords for API login.
    attempts = await r.incr("login-attempt:" + x_tenant)
    if attempts == 1:
        await r.expire("login-attempt:" + x_tenant, 300)
    if attempts > 10:
        raise HTTPException(429, "Login throttled for five minutes")
    demo = os.getenv("DEMO_MODE", "false").lower() == "true"
    username = "demo" if demo else os.environ["APP_USER"]
    password = "demo123" if demo else os.environ["APP_PASSWORD"]
    if not (hmac.compare_digest(body.username, username)
            and hmac.compare_digest(body.password, password)):
        raise HTTPException(401, "Invalid credentials")
    jti = uuid.uuid4().hex
    await r.set("jwt:" + jti, x_tenant, ex=3600)
    return {"access_token": jwt.encode({"sub": body.username, "tenant": x_tenant,
        "role": "crawler", "jti": jti, "iat": int(time.time()),
        "exp": int(time.time()) + 3600, "iss": "orchestrator",
        "aud": "oem-crawler"}, SECRET, algorithm="HS256"), "token_type": "bearer"}

@app.post("/logout")
async def logout(user=Depends(principal)):
    await r.delete("jwt:" + user["jti"])
    return {"revoked": True}

@app.post("/jobs", status_code=202)
async def create(body: Crawl, user=Depends(principal)):
    if user["role"] != "crawler":
        raise HTTPException(403, "Crawler role required")
    try:
        site_id, profile = resolve(body.urls, body.site_id)
    except ValueError as exc:
        if body.site_id or os.getenv("ALLOW_PUBLIC_URLS", "false").lower() != "true":
            raise HTTPException(400, str(exc))
        try:
            profile = public_profile(body.urls[0])
            if not all(normalize_site(url,profile) for url in body.urls):
                raise ValueError("Use one HTTPS origin per job and HTML page URLs")
            host = urlsplit(profile["origin"]).hostname
            infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
            if not public_addresses([info[4][0] for info in infos]):
                raise ValueError("Destination does not resolve exclusively to public addresses")
        except (ValueError, OSError) as policy_error:
            raise HTTPException(400, str(policy_error))
        site_id = "public-" + fingerprint(profile["origin"])[:16]
        await r.set("public-origin:" + host, json.dumps(profile), ex=86400)
    body.site_id = site_id
    body.urls = list(dict.fromkeys(normalize_site(url,profile) for url in body.urls))
    if body.engine == "auto":
        body.engine = "crawl4ai" if profile["mode"] == "mock" else "public_http"
    if profile["mode"] != "mock" and body.engine != "public_http":
        raise HTTPException(400, "This external profile uses HTML HTTP collection. Choose Auto or Public / form-login HTML; dynamic sites need a reviewed browser adapter.")
    if profile["mode"] == "mock" and body.engine == "public_http":
        raise HTTPException(400, "Use Auto, Crawl4AI or Playwright for the mock profile")
    job_id = uuid.uuid4().hex
    record = {"id": job_id, "tenant": user["tenant"], "state": "queued", "created_at": time.time(),
              **body.model_dump(), "profile": profile, "results": []}
    async with r.pipeline(transaction=True) as pipe:
        pipe.set("job:" + job_id, json.dumps(record), ex=86400)
        pipe.lpush("frontier", job_id)
        index = "jobs-index:" + user["tenant"]
        pipe.zremrangebyscore(index, "-inf", time.time() - 86400)
        pipe.zadd(index, {job_id: time.time()})
        pipe.expire(index, 86400)
        await pipe.execute()
    await audit("job_created", job=job_id, tenant=user["tenant"])
    return {"job_id": job_id, "status_url": "/jobs/" + job_id}

@app.get("/sites")
async def sites(user=Depends(principal)):
    return {"sites": metadata()}

@app.get("/jobs")
async def list_jobs(user=Depends(principal)):
    ids = await r.zrevrange("jobs-index:" + user["tenant"], 0, 49)
    if not ids:
        return {"jobs": []}
    records = await r.mget(["job:" + job_id for job_id in ids])
    summaries = []
    for raw in records:
        if not raw:
            continue
        record = json.loads(raw)
        if record["tenant"] != user["tenant"]:
            continue
        summaries.append({key: record.get(key) for key in
                          ("id", "state", "engine", "enrich", "created_at", "urls")})
    return {"jobs": summaries}

@app.get("/jobs/{job_id}")
async def status(job_id: str, user=Depends(principal)):
    raw = await r.get("job:" + job_id)
    if not raw:
        raise HTTPException(404, "Job not found")
    record = json.loads(raw)
    if record["tenant"] != user["tenant"]:
        raise HTTPException(404, "Job not found")
    return record
