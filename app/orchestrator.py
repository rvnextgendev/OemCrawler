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

app = FastAPI(title="Internal orchestrator", dependencies=[Depends(internal)])

class Login(BaseModel):
    username: str
    password: str

class Crawl(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=10)
    engine: str = Field(default="crawl4ai", pattern="^(crawl4ai|playwright)$")
    enrich: bool = False

@app.post("/token")
async def token(body: Login, x_tenant: str = Header()):
    # Demo account; replace with your IdP. Never reuse OEM passwords for API login.
    attempts = await r.incr("login-attempt:" + x_tenant)
    if attempts == 1:
        await r.expire("login-attempt:" + x_tenant, 300)
    if attempts > 10:
        raise HTTPException(429, "Login throttled for five minutes")
    if not (hmac.compare_digest(body.username, os.environ["APP_USER"])
            and hmac.compare_digest(body.password, os.environ["APP_PASSWORD"])):
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
    if any(not allowed_url(url) for url in body.urls):
        raise HTTPException(400, "URL violates approved-origin policy")
    job_id = uuid.uuid4().hex
    record = {"id": job_id, "tenant": user["tenant"], "state": "queued",
              **body.model_dump(), "results": []}
    async with r.pipeline(transaction=True) as pipe:
        pipe.set("job:" + job_id, json.dumps(record), ex=86400)
        pipe.lpush("frontier", job_id)
        await pipe.execute()
    await audit("job_created", job=job_id, tenant=user["tenant"])
    return {"job_id": job_id, "status_url": "/jobs/" + job_id}

@app.get("/jobs/{job_id}")
async def status(job_id: str, user=Depends(principal)):
    raw = await r.get("job:" + job_id)
    if not raw:
        raise HTTPException(404, "Job not found")
    record = json.loads(raw)
    if record["tenant"] != user["tenant"]:
        raise HTTPException(404, "Job not found")
    return record
