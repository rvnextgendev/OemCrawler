"""Public adapter behavior tested with local HTML and network doubles, never live Ford."""
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from cryptography.fernet import Fernet
for key,value in {'REDIS_URL':'redis://localhost','JWT_SECRET':'test-secret','INTERNAL_SECRET':'test-internal','API_KEY':'test-key','SESSION_KEY':Fernet.generate_key().decode()}.items():
    os.environ.setdefault(key,value)
from fastapi.testclient import TestClient
from app import site_crawler as ford, gateway, orchestrator
from app.common import INTERNAL, principal
from app.site_profiles import allowed_network as ford_proxy_url

class FordTests(unittest.TestCase):
    def tearDown(self):
        orchestrator.app.dependency_overrides.clear()
    def test_scope_and_tracking_removal(self):
        self.assertEqual(ford.normalize('https://www.ford.com/support/?gnav=header-support#faq'),'https://www.ford.com/support/')
        for url in ['https://evil.com/support/', 'https://www.ford.com.evil/support/',
                    'https://user:pass@www.ford.com/support/', 'http://www.ford.com/support/',
                    'https://www.ford.com/support/../account/', 'https://www.ford.com/support/%2e%2e/',
                    'https://www.ford.com/support/manual.pdf', 'https://www.ford.com/support/login/',
                    'https://www.ford.com/support/?' + 'x'*3000]:
            self.assertIsNone(ford.normalize(url),url)
    def test_proxy_exact_origin(self):
        self.assertTrue(ford_proxy_url('https://www.ford.com/robots.txt'))
        self.assertFalse(ford_proxy_url('https://www.ford.com/account/'))
        self.assertFalse(ford_proxy_url('https://www.ford.com/support/%2e%2e/'))
    def test_html_content_and_scoped_links(self):
        html='<title>Support</title><h1>Owner help</h1><script>secret script</script><table><tr><th>Part</th></tr><tr><td>123</td></tr></table><a href="/support/faq/?x=1">FAQ</a><a href="https://evil.com/">No</a>'
        item=ford.extract(html,'https://www.ford.com/support/')
        self.assertEqual(item['title'],'Support')
        self.assertEqual(item['headings'],['Owner help'])
        self.assertEqual(item['tables'],[[['Part'],['123']]])
        self.assertEqual(item['links'],['https://www.ford.com/support/faq/'])
        self.assertNotIn('secret script',item['text'])
    def test_robots_wildcards_groups_and_delay(self):
        robots=ford.Robots('User-agent: *\nDisallow: /support/private*\nCrawl-delay: 20\n\nUser-agent: OtherBot\nDisallow: /\n')
        self.assertTrue(robots.allows('https://www.ford.com/support/'))
        self.assertFalse(robots.allows('https://www.ford.com/support/private-article'))
        self.assertEqual(robots.delay,20)
        self.assertEqual(ford.Robots('User-agent: *\nCrawl-delay: 1').delay,10)
    def test_secure_mode_requires_key(self):
        with patch.dict(os.environ,{'DEMO_MODE':'false'}),patch.object(gateway.r,'get',AsyncMock(return_value=None)):
            self.assertEqual(TestClient(gateway.app).get('/jobs').status_code,401)
    def test_demo_mode_key_optional_but_still_forwards_for_jwt_check(self):
        upstream=MagicMock()
        upstream.request=AsyncMock(return_value=MagicMock(content=b'{"detail":"Invalid or expired token"}',status_code=401))
        context=MagicMock(__aenter__=AsyncMock(return_value=upstream),__aexit__=AsyncMock(return_value=False))
        with patch.dict(os.environ,{'DEMO_MODE':'true'}),patch.object(gateway.r,'get',AsyncMock(return_value=None)),patch.object(gateway.r,'eval',AsyncMock(return_value=1)),patch.object(gateway.httpx,'AsyncClient',return_value=context):
            response=TestClient(gateway.app).get('/jobs')
        self.assertEqual(response.status_code,401)
        self.assertEqual(upstream.request.call_args.kwargs['headers']['x-tenant'],'demo')
    def test_demo_login(self):
        with patch.dict(os.environ,{'DEMO_MODE':'true'}),patch.object(orchestrator.r,'incr',AsyncMock(return_value=2)),patch.object(orchestrator.r,'set',AsyncMock(return_value=True)):
            response=TestClient(orchestrator.app).post('/token',headers={'x-internal-secret':INTERNAL,'x-tenant':'demo'},json={'username':'demo','password':'demo123'})
        self.assertEqual(response.status_code,200)
        self.assertIn('access_token',response.json())
    def test_wrong_engine_and_non_ford_url_rejected(self):
        orchestrator.app.dependency_overrides[principal]=lambda:{'tenant':'demo','role':'crawler'}
        client=TestClient(orchestrator.app)
        for body in [{'urls':['https://www.ford.com/support/'],'engine':'crawl4ai'}, {'urls':['https://evil.com/'],'engine':'public_http'}, {'urls':['https://www.ford.com/support/'],'engine':'public_http','max_pages':21}]:
            self.assertIn(client.post('/jobs',headers={'x-internal-secret':INTERNAL},json=body).status_code,(400,422))

class FordNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_robot_block_stops_before_any_page_fetch(self):
        job={'id':'test','tenant':'demo','urls':['https://www.ford.com/support/'],'enrich':False}
        with patch.object(Path,'exists',return_value=True),patch.object(ford.ssl,'create_default_context',return_value=True),patch.object(ford,'public_get',AsyncMock(return_value=(531,{},'Access denied'))) as fetch:
            await ford.acquire_site(job)
        self.assertEqual(job['state'],'blocked')
        self.assertEqual(fetch.await_count,1)
    async def test_stream_size_limit(self):
        class Response:
            status_code=200
            headers={}
            async def aiter_bytes(self):
                yield b'x'*(ford.MAX_BYTES+1)
        context=MagicMock(__aenter__=AsyncMock(return_value=Response()),__aexit__=AsyncMock(return_value=False))
        client=MagicMock()
        client.stream.return_value=context
        with self.assertRaises(ValueError):
            await ford.public_get(client,'https://www.ford.com/support/')
