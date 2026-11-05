"""Plain Playwright: fixed login adapter, no stealth, arbitrary JS or URL scripts."""
import os
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright
from app.common import internal
from app.policy import allowed_url, ALLOWED_HOST, ALLOWED_PORT

app = FastAPI(title="Internal browser service", dependencies=[Depends(internal)])

class Fetch(BaseModel):
    url: str
    storage_state: dict

async def guard(route):
    if allowed_url(route.request.url):
        await route.continue_()
    else:
        await route.abort()

async def browser_task(url, state=None, login=False):
    if not allowed_url(url):
        raise HTTPException(400, "Origin denied")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True,
                        proxy={"server": "http://egress_proxy:8080"})
        try:
            context = await browser.new_context(storage_state=state, service_workers="block")
            await context.route("**/*", guard)
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if login:
                # Demo adapter only. Add a reviewed server-side adapter for each OEM.
                await page.locator("#username").fill(os.environ["OEM_USER"])
                await page.locator("#password").fill(os.environ["OEM_PASSWORD"])
                await page.locator("button[type=submit]").click()
                await page.wait_for_url("**/parts")
                if not await page.locator("#parts").count():
                    raise RuntimeError("Login not confirmed")
                return await context.storage_state()
            return {"status": response.status if response else 502,
                    "html": (await page.content())[:2000000]}
        finally:
            await browser.close()

@app.post("/login")
async def login():
    # Intentionally no customer-supplied URLs, selectors or credentials.
    if ALLOWED_HOST != "mock_oem":
        raise HTTPException(501, "Configure a reviewed OEM login adapter first")
    return await browser_task(f"http://{ALLOWED_HOST}:{ALLOWED_PORT}/login", login=True)

@app.post("/fetch")
async def fetch(body: Fetch):
    return await browser_task(body.url, body.storage_state)
