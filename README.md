# Controlled OEM collection — Python sample

A small service-based example of authorized login, collection and optional AI cleanup.
It runs against **a local mock OEM portal**, not Kia, Nissan, Porsche, BMW or Ford.
Those sites require separately approved adapters and credentials. Crawl4AI is a fetch
engine; it does not guarantee access through anti-bot controls. Both engines use
ordinary headless Chromium. CAPTCHA, access denial and throttling stop collection.

## Architecture

| Component | Port | Responsibility |
|---|---:|---|
| platform_api | 8000 | API-key lookup, atomic daily request quota, narrow forwarding allowlist |
| orchestrator | 8001 internal | JWT minting/revocation, role checks, tenant ownership, URL policy, job submission/status |
| worker (orchestrator engine) | none | Redis frontier, acquisition windows, locks, encrypted sessions, browser pool, cache, audit, enrichment |
| browser_service | 8002 internal | Fixed mock login adapter and optional plain Playwright fetching |
| egress_proxy | 8080 internal | Exact origin allowlist, DNS address checks, shared per-domain pacing, HTTP 531 conversion |
| Crawl4AI | in worker | In-process collection, using authenticated browser state and the proxy |
| Redis | 6379 internal | API keys, quota, JWT state, locks, frontier, processing queue, sessions, pace slots, cache, audit stream |
| mock_oem | 8003 internal | Demo sign-in form, protected parts catalogue, blocked endpoint |
| document volume | none | Content-addressed raw HTML under `/data/<tenant>/<sha256>.html` |

Only port 8000 is published, bound to localhost. This is the public gateway boundary
in the sample; add a TLS ingress before remote access.

The worker belongs logically to the orchestrator but runs separately so accepted
jobs survive API restarts. Exactly **one worker replica** is supported. Redis AOF and
named volumes persist across ordinary container restarts.

## Quick start (Windows 11 / PowerShell, Docker only)

Install Docker Desktop with Linux containers and Git. No host Python is required.

```powershell
git clone https://github.com/rvnextgendev/OemCrawler.git
cd OemCrawler

# Generate local secrets with a temporary Python container.
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim sh -c "pip install cryptography && python configure.py"

# Build and start all seven services.
docker compose up --build -d
docker compose ps
```

The first build downloads Chromium and can take several minutes. Wait for the
services to start, then run the demo inside the gateway container:

```powershell
docker compose exec platform_api python scripts_demo.py
```

On Linux/macOS, use `-v "$PWD:/work"` instead of the PowerShell mount expression.
The remaining Docker Compose commands are identical.

The demo gets a JWT through the gateway, submits a Crawl4AI job and polls its
status. Expected final state: `completed`, with part `DEMO-1001`, raw HTML
location, SHA-256 hash, extracted text and local enrichment metadata.
Run it a second time within an hour: the result should report `cached: True`.
The browser pool keeps up to two authenticated Crawl4AI instances in the worker;
encrypted login state is shared through Redis for 15 minutes.

The public API is available at `http://localhost:8000`. Internal ports are not
published. View logs and stop services with:

```powershell
docker compose logs -f worker browser_service egress_proxy
docker compose down
```

### Alternative: use host Python for setup and demo

If Python 3.10+ is already installed:

```powershell
python -m pip install cryptography httpx
python configure.py
docker compose up --build -d
python scripts_demo.py
```

Do not commit `.env`. The setup script generates independent API, application,
OEM, Redis and encryption secrets. It never overwrites an existing `.env`.

## Web dashboard

After starting Docker, open **http://localhost:8000/ui** (the root URL redirects here).
No frontend container or JavaScript build is required; FastAPI serves the dashboard
on the same origin as the API.

1. Open your local `.env` in an editor. Enter `API_KEY`, `APP_USER` and `APP_PASSWORD`
   into the dashboard sign-in form. Do not enter OEM credentials here.
2. Submit the prefilled demo URL with Crawl4AI or Playwright. Enrichment is optional.
3. Select **View** on a recent job. Inspect status, extracted text, cache usage,
   source hash, stored path, enrichment and the full JSON record.
4. Use **Refresh**, or opt into 15-second auto-refresh for the selected active job.
   Refresh calls count against the API quota; polling stops for terminal jobs and
   hidden browser tabs. Auto-refresh is disabled by default.
5. Use **Export JSON** to download the selected job, or **Sign out** to revoke its JWT.

API keys and JWTs live only in tab memory, not browser storage. A reload requires
sign-in again; jobs remain in Redis. The list shows up to 50 recent jobs from your
tenant, retained for 24 hours. Jobs created before the dashboard index was added
can still be opened by their ID until their original records expire.

Extracted source content is rendered as plain text, never as active HTML. Summary
cards reflect the loaded recent-job list, not lifetime totals or service health.
The mock URL and server allowlist remain unchanged. Real OEM collection still
requires a reviewed site adapter. No credentials are embedded in the frontend.

Update an existing checkout:

```powershell
git pull origin main
docker compose up --build -d
# Then open http://localhost:8000/ui
```

## API example

All requests go through `http://localhost:8000` with `X-API-Key` from `.env`.

1. `POST /token` with `{"username":"demo","password":"<APP_PASSWORD>"}`.
2. Add `Authorization: Bearer <access_token>` to subsequent requests.
3. `POST /jobs` with the body below.
4. `GET /jobs/<job_id>` to inspect state and results.
5. `POST /logout` to revoke the current JWT immediately in Redis.

```json
{
  "urls": ["http://mock_oem:8003/parts"],
  "engine": "crawl4ai",
  "enrich": true
}
```

Use `"engine": "playwright"` for browser_service fetching. Login is performed by
browser_service for both engines; Crawl4AI then reuses the authenticated state.
The submitted URL list is a bounded crawl frontier (maximum ten URLs), not an
automatic whole-site discovery crawler. No user-supplied JS or selectors run.

Job states: `queued`, `running`, `deferred`, `completed`, `blocked`,
`login_required`, `failed`. Failed/blocked jobs require a reviewed new submission;
there is no automatic block bypass or credential retry loop.

## Demonstrate policy and block handling

- Submit `http://169.254.169.254/latest`: job creation returns HTTP 400.
- Submit `http://mock_oem:8003/blocked`: proxy converts upstream 403 to private
  HTTP 531; the job becomes `blocked`, login state is discarded, and that domain
  is paused for five minutes. The public job endpoint still returns JSON normally;
  531 is the internal proxy signal.
- Set `WINDOW_START_UTC=6` and `WINDOW_END_UTC=18` in `.env`, then recreate the
  services. An uncached job outside 06:00–18:00 UTC becomes `deferred` and is
  requeued every 30 seconds. Overnight windows are supported. Default: all day.
- Set `DAILY_QUOTA=5`, restart, and make requests to see HTTP 429. This is a
  **daily API-request quota**, including polling and login, not a page quota.
- Set `PACE_SECONDS=3` to change the shared minimum spacing. Both navigation
  and proxy requests are paced, so a page with multiple resources can take longer.

## Optional AI cleanup

Default `ENRICH_PROVIDER=local` only returns title and word count; **this is not
an LLM call**. To use an external provider, append deployment settings to `.env`:

```dotenv
ENRICH_PROVIDER=openai
ALLOW_EXTERNAL_ENRICHMENT=true
LLM_MODEL=<a model available in your API account>
OPENAI_API_KEY=<your API key>
```

Or use `ENRICH_PROVIDER=claude` and `ANTHROPIC_API_KEY` with an available Claude
model. Recreate worker after changing configuration. Set `enrich: true` on the job.
External mode sends up to 10,000 characters of extracted page text to the selected
provider and may incur API charges. Enable it only for content approved for that
provider. Credentials and browser cookies are not intentionally included; sensitive
page content still needs your own redaction policy. API subscriptions are separate
from consumer ChatGPT/Claude subscriptions.

Source HTML stays unchanged. AI output is separate, marked `needs_review`, and
must not be treated as verified technical specifications. Provider failure preserves
the acquired document and returns an enrichment error. The provider cannot call
tools, navigate sites or modify the acquisition policy.

## Files to study

| File | What to learn |
|---|---|
| `app/gateway.py` | Dashboard delivery, API keys, quota and forwarding |
| `app/ui/` | Responsive dashboard in HTML, CSS and JavaScript |
| `app/orchestrator.py` | JWT/RBAC, tenant isolation and job APIs |
| `app/worker.py` | Session reuse, pool, frontier, locking, pacing, cache and storage |
| `app/browser_service.py` | Playwright login and extraction |
| `app/proxy.py` | mitmproxy addon and HTTP 531 |
| `app/policy.py` | URL, address, time-window and block rules |
| `app/enrichment.py` | Local/OpenAI/Claude adapters |
| `app/mock_oem.py` | Safe demo login target |

## Tests and troubleshooting

```powershell
docker compose exec platform_api python -m unittest discover -s tests -v
docker compose exec platform_api python -m compileall -q app configure.py scripts_demo.py
docker compose ps
docker compose logs worker browser_service egress_proxy
```

Ten tests cover origin restrictions, private/mixed DNS answers, block detection,
overnight windows, dashboard assets, authentication, tenant filtering and gateway
forwarding. Dashboard tests use Redis/client doubles; they do not require live services. The demo script is the integration
smoke test; it requires the running containers. Python compilation and these ten
tests were verified during creation; Docker, Crawl4AI runtime and provider calls
were not available for end-to-end execution in the authoring environment.
Dependencies are version ranges, not a resolved lockfile; resolve and pin a tested
set before deploying. If Crawl4AI's proxy/config API changes, start with the plain
Playwright engine and update `fetch()` against the installed version.

Use `docker compose down` to stop without deleting stored data. Deleting Docker
volumes deletes documents and job state. Raw HTML files are intentionally not
served directly by the public API because they can contain active page content.

## What a real OEM deployment still needs

This is a learning scaffold, not a production-complete anti-bot platform.

- Add reviewed login/extraction adapters per OEM, licensed URLs/path scopes,
  entitlement checks, permitted collection hours, robots/contract policy and
  official export/API integrations where available. `#parts` is a mock-only
  success selector. MFA/CAPTCHA requires an approved human handoff; none is
  solved or bypassed by this sample.
- Replace demo single-user auth with an IdP and configured tenant/account grants.
  Do not expose the mock portal or reuse demo secrets. Move secrets to a vault.
- The proxy validates DNS answers, but it does not pin them to the eventual socket.
  Add network egress enforcement, DNS pinning and subnet deny rules to prevent
  rebinding and browser proxy bypass. Container networking here is **not** a
  hardened SSRF isolation boundary.
- HTTPS OEM traffic needs the mitmproxy CA installed in the browser trust store.
  The mock uses HTTP so the sample needs no trust-store setup. Do not disable TLS
  verification as a shortcut. Trusting a CA requires appropriate internal controls.
- Only one approved origin is supported; external/CDN resources are denied. Build
  explicit reviewed per-OEM resource allowlists without widening navigation scope.
- A collection window prevents new acquisitions; in-flight requests may finish
  after closing time. Add hard cancellation for strict cutoffs. Redis-time slots
  assume synchronized host clocks and bounded queue wait.
- The pool holds live browsers in worker memory, with encrypted serialized login
  state in Redis. Redis cannot store a running Playwright browser. Extend pool
  eviction, account ownership and idle cleanup for production.
- Use Redis Streams consumer groups/leases for multiple workers. Current recovery
  requeues interrupted jobs at startup and is safe only with one worker. At-least-
  once execution can repeat requests after crashes. Content hashing reduces file
  duplication; it does not provide exactly-once collection.
- Add page/byte quotas, response streaming limits (the current HTML cap is after
  fetch), download/PDF handling, per-page checkpoints, retention, browser memory
  budgets, structured parts parsing, durable object storage and monitoring.
- Redis audit is a capped operational stream, not an immutable compliance log.
  Job records expire after 24 hours, cached results after one hour, browser state
  after 15 minutes; raw files and Redis mock session records need retention rules.

## API references

- https://docs.crawl4ai.com/api/parameters/
- https://docs.crawl4ai.com/advanced/proxy-security/
- https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
- https://platform.claude.com/docs/en/api/messages/create
