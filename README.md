# OEM Crawler — public and form-login collection

A Python/FastAPI acquisition sample with a browser dashboard, Redis jobs, a policy
proxy, cached documents and optional enrichment. It supports public HTML sites,
configured HTML form logins, and an authenticated Playwright/Crawl4AI mock OEM demo.

The old **URL violates approved-origin policy** problem came from accepting only
`mock_oem`. Auto now selects a matching profile. In local demo mode it also accepts
other public HTTPS origins after public-address checks. Login sites use explicit
server-side profiles so their credentials are never sent to a guessed destination.

**There is no guarantee of access to every website.** SSO, MFA, CAPTCHA, blocked
responses, JavaScript-only pages and site-specific entitlements need additional
approved adapters or a human handoff. Nothing bypasses a site's access controls.

## Update and run (Windows PowerShell)

Install Docker Desktop with Linux containers and Git. For an existing checkout:

```powershell
cd OemCrawler
git pull origin main

# Enable local demo sign-in and public HTTPS URLs; preserves existing secrets.
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim sh -c "pip install cryptography && python configure.py --demo"

docker compose up --build -d
docker compose ps
```

For a fresh checkout, first run:

```powershell
git clone https://github.com/rvnextgendev/OemCrawler.git
cd OemCrawler
```

Then run the setup/build commands above. Initial builds download Chromium and can
take several minutes. On Linux/macOS, use `-v "$PWD:/work"` instead of `${PWD}`.

Open **http://localhost:8000/ui**, then hard-refresh (`Ctrl+F5`) if an older UI is
still visible.

| Local demo login field | Value |
|---|---|
| Username | `demo` |
| Password | `demo123` |
| API key | Leave blank; optional only in demo mode |

`configure.py --demo` sets `DEMO_MODE=true` and `ALLOW_PUBLIC_URLS=true`. The demo
login is independent of the randomly generated `APP_PASSWORD`. Bind only to
localhost, as Compose already does. This example login is not for public hosting.

To return to generated credentials and configured origins only, run the setup
command with `--secure`, then recreate services. Secure mode uses `API_KEY`,
`APP_USER` and `APP_PASSWORD` from `.env` and requires the API key. Do not share or
commit `.env`.

## Use the UI

1. Sign in. Leave **Auto** selected for the source and engine.
2. Paste an approved public HTTPS URL, or choose an example from **Demo source**.
3. Choose page limit (1–20) and link depth (0–2), then start extraction.
4. Open the job to inspect status, extracted text and the full JSON record.
5. Refresh manually, or enable active-job polling every 15 seconds. Polling stops
   when the job finishes or the browser tab is hidden. API quotas include polls.
6. Export JSON to save the results. Source HTML is stored in the document volume;
   the UI renders text only, never active source HTML.

The recent list shows the latest 50 tenant jobs retained for 24 hours. Old jobs
created before the list index was added can still be opened by ID until expiry.

### Included examples

| Source | Mode | What to expect |
|---|---|---|
| Mock OEM | Authenticated browser | Fixed demo login; Crawl4AI or Playwright; catalogue part `DEMO-1001` |
| Ford Support | Public HTTP HTML | `https://www.ford.com/support/`; no Ford password required; support scope only |
| Quotes public | Public HTTP HTML | A public scraping-practice catalogue |
| Quotes login | HTTP form login | Select this profile explicitly; server credentials `QUOTES_USER` and `QUOTES_PASSWORD` |

The demo setup initializes both Quotes credential variables to `demo`. These are
practice-site example values, not real OEM credentials. If the external practice
site changes its behavior, review its form/profile; the local mock remains the
controlled test target.

Ford returned HTTP 403 to the authoring-environment check. This code does not
assert that Ford collection succeeded. A denied page becomes `blocked` rather
than triggering a bypass. Ford's robots.txt advertised a 10-second crawl delay.

## What gets extracted

External HTML collection saves the original HTML, title, visible source text,
headings, table rows, in-scope links, SHA-256 hash, timestamp and cache metadata.
It follows same-origin links with bounded breadth-first traversal. Tracking query
strings/fragments are removed by default; profiles may set `keep_query: true`.
PDFs, images, downloads and content rendered only by JavaScript are excluded.

`max_pages` limits page attempts, including redirects/skips, not a promise of that
many saved pages. `completed` means the configured scope finished. Inspect
`limit_reached`, `depth_limit_reached`, `frontier_remaining`, `coverage` and `skipped`
to assess coverage. It is not an "all content on the whole site" claim.

Job states include `queued`, `running`, `deferred`, `completed`, `blocked`,
`login_required`, `no_content` and `failed`. Acquisition windows prevent starting
new requests outside the window; in-flight requests can finish afterward.

## Public URLs and custom sites

With `ALLOW_PUBLIC_URLS=true`, Auto can create a public-only profile for an
explicitly submitted HTTPS origin. It validates all DNS answers as public,
registers a 24-hour proxy grant and rechecks addresses at the proxy. IP literals,
private hosts, non-HTTPS destinations and mixed-origin jobs are rejected. Crawls
stay on the original origin; cross-origin redirects are not followed.

For persistent reviewed public profiles, edit `sites.json` or run:

```powershell
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim python manage_sites.py --id my_site --name "My website" --url https://example.com/help/ --prefix /help/
docker compose up --build -d
```

Replace example.com with the actual website you are authorized to collect from.
Profile changes require rebuilding the services, including the proxy. This keeps
its configured-origin policy aligned with the orchestrator.

## Add a real form-login website

Use a profile rather than pasting credentials into a URL or frontend source:

```powershell
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim python manage_sites.py --id my_login_site --name "My authenticated site" --url https://example.com/docs/ --prefix /docs/ --login-url https://example.com/login --username-field email --password-field password --username-env MY_SITE_USER --password-env MY_SITE_PASSWORD --success-selector "a[href='/logout']"
```

Add your own credentials locally to `.env`:

```dotenv
MY_SITE_USER=your-authorized-account
MY_SITE_PASSWORD=your-password
```

Then rebuild and select that profile explicitly in the UI. Form field names,
form selector and success selector must match the actual site. The adapter:

- Fetches robots rules before acquisition and login.
- Uses a same-origin POST form and preserves hidden fields, including CSRF tokens.
- Posts credentials only to the exact configured login URL.
- Follows only approved GET redirects and requires a positive success selector.
- Encrypts cookie state in Redis for 15 minutes; cache/session keys are scoped by
  tenant, profile and credential identity.

This handles basic HTML forms. It does not automatically handle JavaScript login,
SSO, OAuth, MFA, CAPTCHA, a different form submission endpoint or arbitrary browser
scripts. Those need a reviewed site-specific adapter. Mock browser engines are
kept separate from external HTTP collection; Auto chooses the supported engine.

## Architecture

| Component | Port | Responsibility |
|---|---:|---|
| platform_api | 8000 | UI, API key or local demo mode, request quota, narrow forwarding |
| orchestrator | 8001 internal | JWT/RBAC, profile resolution, public DNS admission, tenant job APIs |
| worker | none | Frontier, locks, windows, login state, pool, acquisition, cache, audit, enrichment |
| browser_service | 8002 internal | Mock OEM Playwright login and optional browser fetching |
| egress_proxy | 8080 internal | Origin policy, DNS checks, per-domain pacing, blocked-response conversion |
| Crawl4AI | in worker | In-process authenticated mock acquisition |
| HTTPX | in worker | Public and basic HTML form-login collection |
| Redis | 6379 internal | Keys, quotas, JWT state, queues, locks, cookies, pace slots, caches, audit index |
| mock_oem | 8003 internal | Controlled sign-in/parts/blocked demo pages |

Only port 8000 is published, on loopback. Exactly **one worker replica** is supported.
Its startup recovery requeues interrupted work, giving at-least-once execution.
Live Crawl4AI browsers are held in worker memory (pool limit two); serialized login
state belongs in Redis, not live browser objects.

The proxy's generated CA lives in `proxy_ca`. External HTTPX clients explicitly
trust its certificate and keep upstream TLS verification enabled. Private CA keys
stay in the proxy volume. If a very early job reports `ProxyCertificateNotReady`,
wait for proxy startup, then submit again. Browser CA trust for external Chromium
sites is not configured because current external profiles use HTTPX.

Robots 404/410 means no published policy; unavailable/redirected/error policies
fail closed. Conservative Disallow matching honors wildcards, but may skip an
Allow exception. HTTP HTML requests wait at least 10 seconds, and honor a longer
crawl-delay; the proxy also paces every domain. A blocked domain pauses for five
minutes. HTTP **531** is a private proxy signal, not a standard HTTP status.

## API

Use the gateway at `http://localhost:8000`:

- `POST /token`: application username/password; returns JWT.
- `GET /sites`: safe configured-profile metadata, not credentials.
- `POST /jobs`: create a tenant job.
- `GET /jobs`: up to 50 recent tenant summaries.
- `GET /jobs/{id}`: full tenant-owned job record.
- `POST /logout`: revoke JWT.

All data endpoints require `Authorization: Bearer <JWT>`. Secure mode also requires
`X-API-Key`. Internal services require the separate internal secret. Public UI
assets/configuration disclose no generated secrets.

```json
{
  "urls": ["https://www.ford.com/support/?gnav=header-support"],
  "engine": "auto",
  "site_id": null,
  "max_pages": 5,
  "max_depth": 1,
  "enrich": false
}
```

`site_id: "quotes_login"` selects the configured login example. A job's seeds must
belong to one profile/origin. Default quota is 1,000 API requests/day; public HTML
responses are capped at two megabytes, with at most 20 page attempts per job and a
600-second total worker deadline. `WINDOW_START_UTC`/`WINDOW_END_UTC` default to all day.

## Optional enrichment

Default `ENRICH_PROVIDER=local` returns title/word count; it is not an LLM call.
For external AI, set `ENRICH_PROVIDER=openai` or `claude`, `LLM_MODEL`, the appropriate
`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`, and `ALLOW_EXTERNAL_ENRICHMENT=true` in `.env`.
Recreate worker and request `enrich: true`. Up to 10,000 text characters are sent
to that provider; enable only for approved content. Credentials/cookies are not
intentionally included, but source-page sensitive content needs your redaction policy.
AI output is separate, marked for review; raw source remains unchanged. Provider
failure preserves acquisition and records an enrichment error.

## Checks and troubleshooting

```powershell
docker compose exec platform_api python -m unittest discover -s tests -v
docker compose exec platform_api python -m compileall -q app configure.py manage_sites.py scripts_demo.py
docker compose exec platform_api python scripts_demo.py
docker compose logs -f worker browser_service egress_proxy
docker compose down
```

32 tests pass during authoring, using in-process Redis/network doubles. Checks
cover auth, tenant boundaries, profile matching, dynamic-origin admission, public
DNS rejection, login/CSRF handling, cross-origin credential protection, response
limits, robots handling and block detection. Python compilation and JavaScript
syntax checks also pass. Docker and live external acquisition/provider calls have
not been verified in the authoring environment; Ford's observed 403 remains a limit.

**Still seeing the old policy message?** Run `git pull`, rebuild **all** services,
then hard-refresh the browser. Existing environment variables require container
recreation, not just `docker compose restart`. If Auto rejects an unknown site in
secure mode, add a profile or explicitly enable public URLs. Do not disable
private-address checks to make a destination work.

Named volumes survive `docker compose down`. Deleting volumes deletes documents,
Redis state and proxy CA material. Jobs expire after 24 hours, raw extraction caches
after one hour, encrypted sessions after 15 minutes. Document files need a retention
policy. The audit stream is capped, not an immutable compliance ledger.

For production: IdP/entitlements, secrets vault, network egress isolation and DNS
pinning (current checks do not prevent every rebinding race), durable object storage,
Redis Streams/leases for multiple workers, telemetry, retention, and reviewed OEM
adapters are still required. Only collect content your account/licence permits.

References: https://docs.crawl4ai.com/api/parameters/,
https://www.ford.com/robots.txt, https://quotes.toscrape.com/login.
