"""Public API: authenticate API key, apply quota, forward to orchestrator."""
import os
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response, RedirectResponse, FileResponse
from pathlib import Path
from app.common import r, init_key, fingerprint, INTERNAL

@asynccontextmanager
async def lifespan(app):
    await init_key()
    yield
    await r.aclose()

app = FastAPI(title="OEM Platform API", lifespan=lifespan)
UI_DIR = Path(__file__).parent / "ui"

@app.get("/", include_in_schema=False)
async def home():
    return RedirectResponse("/ui")

@app.get("/ui", include_in_schema=False)
async def dashboard():
    return FileResponse(UI_DIR / "index.html", headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})

@app.get("/ui/config", include_in_schema=False)
async def dashboard_config():
    return {"demo_mode": os.getenv("DEMO_MODE", "false").lower() == "true", "allow_public_urls": os.getenv("ALLOW_PUBLIC_URLS", "false").lower() == "true"}

@app.get("/ui/app.js", include_in_schema=False)
async def dashboard_script():
    return FileResponse(UI_DIR / "app.js", media_type="text/javascript")

@app.get("/ui/style.css", include_in_schema=False)
async def dashboard_style():
    return FileResponse(UI_DIR / "style.css", media_type="text/css")

QUOTA = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], 86400) end
return n
"""

@app.api_route("/{path:path}", methods=["GET", "POST"])
async def forward(path: str, request: Request):
    import datetime
    tenant = await r.get("key:" + fingerprint(request.headers.get("x-api-key", "")))
    if not tenant and not request.headers.get("x-api-key") and os.getenv("DEMO_MODE", "false").lower() == "true":
        tenant = "demo"
    if not tenant:
        raise HTTPException(401, "Invalid API key")
    # Expose only explicitly supported routes, never a generic internal relay.
    valid = ((request.method == "POST" and path in ("token", "jobs", "logout"))
             or (request.method == "GET" and path in ("jobs", "sites"))
             or (request.method == "GET" and path.startswith("jobs/")
                 and len(path.split("/")) == 2))
    if not valid:
        raise HTTPException(404, "Unknown endpoint")
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    count = await r.eval(QUOTA, 1, f"quota:{tenant}:{day}")
    if int(count) > int(os.getenv("DAILY_QUOTA", "1000")):
        raise HTTPException(429, "Daily API request quota exceeded")
    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(413, "Request too large")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            result = await client.request(request.method,
                "http://orchestrator:8001/" + path, content=body,
                headers={"content-type": "application/json", "x-tenant": tenant,
                         "x-internal-secret": INTERNAL,
                         "authorization": request.headers.get("authorization", "")})
        except httpx.RequestError:
            raise HTTPException(503, "Orchestrator unavailable")
    return Response(result.content, status_code=result.status_code,
                    media_type="application/json")
