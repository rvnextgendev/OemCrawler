"""An intentionally tiny OEM-like portal. No real OEM is contacted."""
import os
from urllib.parse import parse_qs
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.common import r

app = FastAPI()

@app.get("/login", response_class=HTMLResponse)
async def login():
    return """<form method='post' action='/login'>
    <input id='username' name='username'><input id='password' name='password' type='password'>
    <button type='submit'>Sign in</button></form>"""

@app.post("/login")
async def signin(request: Request):
    import secrets
    form = parse_qs((await request.body()).decode())
    if (form.get("username") != [os.environ["OEM_USER"]]
            or form.get("password") != [os.environ["OEM_PASSWORD"]]):
        return HTMLResponse("Invalid credentials", status_code=401)
    token = secrets.token_urlsafe(24)
    await r.set("mock-session:" + token, "ok", ex=900)
    response = RedirectResponse("/parts", status_code=303)
    response.set_cookie("sid", token, httponly=True, samesite="strict", max_age=900)
    return response

@app.get("/parts", response_class=HTMLResponse)
async def parts(request: Request):
    if not await r.exists("mock-session:" + request.cookies.get("sid", "")):
        return RedirectResponse("/login")
    return """<html><title>Demo OEM parts</title><main id='parts'>
    <h1>Parts catalogue</h1><table><tr><th>Part</th><th>Description</th></tr>
    <tr><td>DEMO-1001</td><td>Oil filter</td></tr></table></main></html>"""

@app.get("/blocked", response_class=HTMLResponse)
async def denied():
    return HTMLResponse("Access denied. Verify you are human", status_code=403)
