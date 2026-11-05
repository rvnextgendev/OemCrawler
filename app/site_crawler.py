"""Bounded public HTML collection. Never sends OEM credentials or internal headers."""
import asyncio
import hashlib
import json
import os
import re
import ssl
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
import httpx
from bs4 import BeautifulSoup
from app.policy import blocked, in_window
from app.common import r, audit, fingerprint
from app.enrichment import enrich

ORIGIN = 'https://www.ford.com'
USER_AGENT = 'OEMCrawlerDemo/0.1'
CA_FILE = Path('/proxy-ca/mitmproxy-ca-cert.pem')
MAX_BYTES = 2_000_000

def normalize(url, profile=None):
    # Backwards-compatible default for the Ford demo and its focused tests.
    from app.site_profiles import profiles, normalize as normalize_site
    return normalize_site(url, profile or profiles()['ford'])

def extract(html, url, profile=None):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    links = sorted({target for a in soup.select('a[href]')
                    if (target := normalize(urljoin(url, a['href']), profile))})
    headings = [tag.get_text(' ', strip=True) for tag in soup.select('h1,h2,h3,h4,h5,h6')]
    tables = [[[cell.get_text(' ', strip=True) for cell in row.find_all(['th','td'])]
               for row in table.find_all('tr')] for table in soup.find_all('table')]
    for tag in soup(['script','style','noscript','template']):
        tag.decompose()
    return {'title': title, 'text': soup.get_text('\n', strip=True),
            'headings': headings, 'tables': tables, 'links': links}

class Robots:
    """Conservative wildcard rules: any matching disallow wins; delays >=10s.

    Honour global and specifically named demo groups. Conservative behavior can
    skip an Allow exception, but never uses an Allow to override a disallow.
    """
    def __init__(self, text):
        self.disallowed = []
        self.delay = 10.0
        agents = []
        rules_started = False
        for raw in text.splitlines():
            line = raw.split('#',1)[0].strip()
            if ':' not in line:
                continue
            key, value = (part.strip() for part in line.split(':',1))
            key = key.lower()
            if key == 'user-agent':
                if rules_started:
                    agents = []
                    rules_started = False
                agents.append(value.lower())
                continue
            rules_started = True
            if not any(agent == '*' or agent in USER_AGENT.lower() for agent in agents):
                continue
            if key == 'disallow' and value:
                self.disallowed.append(value)
            if key == 'crawl-delay':
                try:
                    self.delay = max(self.delay, float(value))
                except ValueError:
                    pass
    def allows(self, url):
        path = urlsplit(url).path
        for rule in self.disallowed:
            terminal = rule.endswith('$')
            pattern = re.escape(rule.rstrip('$')).replace(r'\*', '.*')
            if re.search('^' + pattern + ('$' if terminal else ''), path):
                return False
        return True

async def public_get(client, url, data=None):
    """No automatic redirects. Each destination must be policy checked by caller."""
    async with client.stream('POST' if data is not None else 'GET', url, data=data) as response:
        payload = bytearray()
        async for chunk in response.aiter_bytes():
            payload.extend(chunk)
            if len(payload) > MAX_BYTES:
                raise ValueError('Public response exceeds two-megabyte limit')
        return response.status_code, response.headers, payload.decode('utf-8', errors='replace')

async def acquire_site(job):
    # Public and configured HTTP form-login profiles share the bounded collector.
    from app.site_profiles import profiles, normalize as normalize_site
    profile_id = job.get('site_id', 'ford')
    profile = profiles().get(profile_id) or job['profile']
    origin = profile['origin'].rstrip('/')
    host = urlsplit(origin).hostname
    normalize_url = lambda url: normalize_site(url, profile)
    # Shared proxy CA is trusted explicitly. Never disable certificate verification.
    if not CA_FILE.exists():
        job.update(state='failed', error='ProxyCertificateNotReady')
        return
    context = ssl.create_default_context(cafile=str(CA_FILE))
    queue = deque((normalize_url(url), 0) for url in job['urls'])
    visited = set()
    job['skipped'] = []
    job['depth_limit_reached'] = False
    job['coverage'] = 'Server-rendered HTML only; bounded links. PDFs and JavaScript-only content are excluded.'
    async with httpx.AsyncClient(proxy='http://egress_proxy:8080', verify=context,
            follow_redirects=False, timeout=45, trust_env=False,
            headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain'}) as client:
        if not in_window():
            job['state'] = 'deferred'
            return
        status, headers, body = await public_get(client, origin + '/robots.txt')
        if blocked(status, body):
            job.update(state='blocked', error='RobotsAccessBlocked')
            return
        if status in (404,410):
            body = ''  # RFC-style absent robots policy; ordinary public scope still applies.
        elif status != 200 or '<html' in body[:300].lower():
            job.update(state='failed', error='RobotsPolicyUnavailable')
            return
        robots = Robots(body)
        robots.delay = max(robots.delay, float(profile.get('pace_seconds',2)))
        # Additional delay honours a crawl-delay larger than the proxy floor.
        last_request = time.monotonic()
        async def get(url, data=None):
            nonlocal last_request
            await asyncio.sleep(max(0, robots.delay - (time.monotonic() - last_request)))
            if not in_window():
                raise OutsideWindow()
            try:
                return await public_get(client, url, data=data)
            finally:
                last_request = time.monotonic()
        auth_session_key = None
        if profile['mode'] == 'form_login':
            try:
                auth_session_key = await authenticate(client, profile_id, profile, job['tenant'], robots, get)
            except OutsideWindow:
                job['state'] = 'deferred'
                return
            except LoginBlocked:
                job.update(state='blocked', error='LoginAccessBlocked')
                return
            except Exception as exc:
                job.update(state='login_required', error=type(exc).__name__)
                return
        max_pages = job.get('max_pages', 5)
        max_depth = job.get('max_depth', 1)
        while queue and len(visited) < max_pages:
            url, depth = queue.popleft()
            if not url or url in visited:
                continue
            visited.add(url)
            if not robots.allows(url):
                job['skipped'].append({'url':url,'reason':'robots_disallow'})
                continue
            if not in_window():
                job['state'] = 'deferred'
                return
            identity = ''
            if profile['mode'] == 'form_login':
                login = profile['login']
                identity = fingerprint(os.environ.get(login['username_env'],'') + '\0' + os.environ.get(login['password_env'],''))
            cache_key = 'site-cache:' + job['tenant'] + ':' + profile_id + ':' + fingerprint(json.dumps(profile,sort_keys=True) + identity + url)
            cached = await r.get(cache_key)
            if cached:
                item = {**json.loads(cached), 'cached':True}
            else:
                if await r.exists('blocked:' + host):
                    job.update(state='blocked',error='DomainPaused')
                    return
                try:
                    status, headers, html = await get(url)
                except OutsideWindow:
                    job['state'] = 'deferred'
                    return
                if blocked(status, html):
                    await r.set('blocked:' + host,'1',ex=300)
                    job.update(state='blocked',error='SourceAccessBlocked')
                    return
                if 300 <= status < 400:
                    location = urljoin(url, headers.get('location',''))
                    if profile['mode'] == 'form_login' and not normalize_url(location):
                        if auth_session_key:
                            await r.delete(auth_session_key)
                        job.update(state='login_required',error='SessionRedirectOutsideScope')
                        return
                    target = normalize_url(urljoin(url, headers.get('location','')))
                    if target and target not in visited and robots.allows(target):
                        queue.appendleft((target,depth))
                    else:
                        job['skipped'].append({'url':url,'reason':'redirect_outside_scope_or_visited'})
                    continue
                if status != 200 or 'text/html' not in headers.get('content-type','').lower():
                    job['skipped'].append({'url':url,'reason':'non_html_or_http_' + str(status)})
                    continue
                if profile['mode'] == 'form_login' and BeautifulSoup(html,'html.parser').select_one('input[type=password]'):
                    if auth_session_key:
                        await r.delete(auth_session_key)
                    job.update(state='login_required',error='SessionExpired')
                    return
                item = extract(html, url, profile)
                if not item['text'].strip():
                    job['skipped'].append({'url':url,'reason':'empty_content'})
                    continue
                sha = hashlib.sha256(html.encode()).hexdigest()
                path = Path('/data') / job['tenant'] / (sha + '.html')
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(html,encoding='utf-8')
                item.update(url=url,sha256=sha,stored_path=str(path),fetched_at=time.time(),cached=False)
                await r.set(cache_key,json.dumps(item),ex=3600)
                await audit('site_page_saved',job=job['id'],sha256=sha)
            # Cache raw extraction independently of enrichment configuration.
            if job['enrich']:
                try:
                    item['enrichment'] = await enrich(item['text'],item['title'])
                except Exception as exc:
                    item['enrichment'] = {'error':type(exc).__name__,'needs_review':True}
            job['results'].append(item)
            if depth >= max_depth and item['links']:
                job['depth_limit_reached'] = True
            if depth < max_depth:
                for link in item['links']:
                    if link not in visited and len(queue) < 1000:
                        queue.append((link,depth+1))
            job.update(pages_attempted=len(visited),frontier_remaining=len(queue))
            await r.set('job:' + job['id'],json.dumps(job),ex=86400)
        job.update(state='completed' if job['results'] else 'no_content',
                   pages_attempted=len(visited),frontier_remaining=len(queue),
                   limit_reached=bool(queue))

class OutsideWindow(Exception):
    pass


class LoginBlocked(Exception):
    pass

async def authenticate(client, profile_id, profile, tenant, robots, get):
    """Basic HTML forms only. Preserves hidden inputs/CSRF and scopes redirects.

    Credential names/selectors are server-side configuration, never arbitrary JS.
    A successful selector is mandatory; HTTP 200 by itself is not proof of login.
    """
    import os
    from app.common import fernet
    from app.site_profiles import same_origin
    login = profile['login']
    username = os.environ.get(login['username_env'])
    password = os.environ.get(login['password_env'])
    if not username or not password:
        raise ValueError('Missing site credentials')
    # Changing credentials invalidates cached cookies without storing credentials.
    session_key = 'http-session:' + tenant + ':' + profile_id + ':' + fingerprint(username + password + json.dumps(profile,sort_keys=True))
    cached = await r.get(session_key)
    if cached:
        for cookie in json.loads(fernet.decrypt(cached.encode())):
            client.cookies.set(cookie['name'],cookie['value'],domain=cookie['domain'],path=cookie['path'])
        return session_key
    url = login['url']
    if not same_origin(url,profile) or not robots.allows(url):
        raise ValueError('Login outside permitted scope')
    status,headers,html = await get(url)
    if blocked(status,html):
        raise LoginBlocked()
    if status != 200:
        raise ValueError('Login form unavailable')
    form = BeautifulSoup(html,'html.parser').select_one(login.get('form_selector','form'))
    if not form or form.get('method','get').lower() != 'post':
        raise ValueError('POST login form not found')
    action = urljoin(url,form.get('action') or url)
    # Login posts are allowed only to the exact reviewed login URL.
    if action != url or not same_origin(action,profile):
        raise ValueError('Login action needs a reviewed adapter')
    data = {field['name']:field.get('value','') for field in form.select('input[name]') if field.get('type','').lower() == 'hidden'}
    data[login['username_field']] = username
    data[login['password_field']] = password
    status,headers,html = await get(action,data=data)
    for _ in range(5):
        if blocked(status,html):
            raise LoginBlocked()
        if not 300 <= status < 400:
            break
        if status not in (301,302,303):
            raise ValueError('Login redirect would resubmit credentials')
        target = urljoin(action,headers.get('location',''))
        if not normalize(target,profile) or not robots.allows(target):
            raise ValueError('Login redirect outside approved scope')
        action = target
        status,headers,html = await get(target)
    if status != 200 or not BeautifulSoup(html,'html.parser').select_one(login['success_selector']):
        raise ValueError('Login not confirmed')
    cookies = [{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path} for c in client.cookies.jar]
    await r.set(session_key,fernet.encrypt(json.dumps(cookies).encode()).decode(),ex=900)
    return session_key
