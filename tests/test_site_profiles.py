import os
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from app import site_profiles as sites
from app import site_crawler as crawler

class ProfileTests(unittest.TestCase):
    def test_auto_matches_ford_and_mock(self):
        self.assertEqual(sites.resolve(['https://www.ford.com/support/?gnav=x'])[0],'ford')
        self.assertEqual(sites.resolve(['http://mock_oem:8003/parts'])[0],'mock')
    def test_login_requires_explicit_profile(self):
        urls=['https://quotes.toscrape.com/']
        self.assertEqual(sites.resolve(urls)[0],'quotes_public')
        self.assertEqual(sites.resolve(urls,'quotes_login')[1]['mode'],'form_login')
    def test_unknown_and_mixed_origins_rejected(self):
        for urls in [['https://example.com/'],['https://www.ford.com/support/','https://quotes.toscrape.com/']]:
            with self.assertRaises(ValueError):
                sites.resolve(urls)
    def test_profile_metadata_contains_no_login_secrets(self):
        for item in sites.metadata():
            self.assertNotIn('login',item)
            self.assertNotIn('password',str(item))
    def test_login_and_robots_network_scope(self):
        self.assertTrue(sites.allowed_network('https://quotes.toscrape.com/login'))
        self.assertTrue(sites.allowed_network('https://www.ford.com/robots.txt'))
        self.assertFalse(sites.allowed_network('http://169.254.169.254/latest'))
        self.assertFalse(sites.allowed_network('https://www.ford.com/account/'))

class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_form_preserves_csrf_and_confirms_success(self):
        profile=sites.profiles()['quotes_login']
        get=AsyncMock(side_effect=[
            (200,{},'<form method="post"><input type="hidden" name="csrf_token" value="abc"></form>'),
            (302,{'location':'/'},''),
            (200,{},'<a href="/logout">Logout</a>')])
        async with httpx.AsyncClient(trust_env=False) as client:
            with patch.dict(os.environ,{'QUOTES_USER':'demo','QUOTES_PASSWORD':'demo'}),patch.object(crawler.r,'get',AsyncMock(return_value=None)),patch.object(crawler.r,'set',AsyncMock(return_value=True)) as saved:
                key=await crawler.authenticate(client,'quotes_login',profile,'demo',crawler.Robots(''),get)
        payload=get.await_args_list[1].kwargs['data']
        self.assertEqual(payload,{'csrf_token':'abc','username':'demo','password':'demo'})
        self.assertTrue(key.startswith('http-session:demo:quotes_login:'))
        self.assertNotIn('"password"',saved.await_args.args[1])
    async def test_cross_origin_form_action_never_receives_credentials(self):
        profile=sites.profiles()['quotes_login']
        get=AsyncMock(return_value=(200,{},'<form method="post" action="https://evil.example/login"></form>'))
        async with httpx.AsyncClient(trust_env=False) as client:
            with patch.dict(os.environ,{'QUOTES_USER':'demo','QUOTES_PASSWORD':'demo'}),patch.object(crawler.r,'get',AsyncMock(return_value=None)):
                with self.assertRaises(ValueError):
                    await crawler.authenticate(client,'quotes_login',profile,'demo',crawler.Robots(''),get)
        self.assertEqual(get.await_count,1)
    async def test_no_login_without_credentials(self):
        profile=sites.profiles()['quotes_login']
        get=AsyncMock()
        with patch.dict(os.environ,{'QUOTES_USER':'','QUOTES_PASSWORD':''}):
            with self.assertRaises(ValueError):
                await crawler.authenticate(None,'quotes_login',profile,'demo',crawler.Robots(''),get)
        get.assert_not_awaited()

class DynamicPublicTests(unittest.TestCase):
    def test_generated_public_profile_rejects_private_literals(self):
        for url in ['http://example.com/','https://127.0.0.1/','https://169.254.169.254/','https://localhost/','https://user:pass@example.com/']:
            with self.assertRaises(ValueError):
                sites.public_profile(url)
        self.assertEqual(sites.public_profile('https://example.com/help/')['mode'],'public')
    def test_recaptcha_asset_is_not_a_blocked_page(self):
        from app.policy import blocked
        self.assertFalse(blocked(200,'<script src="https://example.com/recaptcha.js"></script><h1>Support</h1>'))
    def test_auto_accepts_public_origin_and_registers_proxy_grant(self):
        from fastapi.testclient import TestClient
        from app import orchestrator
        from app.common import INTERNAL, principal
        from unittest.mock import MagicMock
        orchestrator.app.dependency_overrides[principal]=lambda:{'tenant':'demo','role':'crawler'}
        pipe=MagicMock()
        pipe.execute=AsyncMock(return_value=[])
        pipe.__aenter__=AsyncMock(return_value=pipe)
        pipe.__aexit__=AsyncMock(return_value=False)
        try:
            with patch.dict(os.environ,{'ALLOW_PUBLIC_URLS':'true'}),patch.object(orchestrator.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch.object(orchestrator.r,'set',AsyncMock(return_value=True)) as grant,patch.object(orchestrator.r,'pipeline',return_value=pipe),patch.object(orchestrator,'audit',AsyncMock()):
                response=TestClient(orchestrator.app).post('/jobs',headers={'x-internal-secret':INTERNAL},json={'urls':['https://example.com/help/']})
            self.assertEqual(response.status_code,202)
            self.assertEqual(grant.await_args.args[0],'public-origin:example.com')
            record=__import__('json').loads(pipe.set.call_args.args[1])
            self.assertEqual(record['engine'],'public_http')
            self.assertEqual(record['profile']['origin'],'https://example.com')
        finally:
            orchestrator.app.dependency_overrides.clear()
    def test_public_origin_rejects_mixed_private_dns(self):
        from fastapi.testclient import TestClient
        from app import orchestrator
        from app.common import INTERNAL, principal
        orchestrator.app.dependency_overrides[principal]=lambda:{'tenant':'demo','role':'crawler'}
        try:
            with patch.dict(os.environ,{'ALLOW_PUBLIC_URLS':'true'}),patch.object(orchestrator.socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('10.0.0.1',443))]):
                response=TestClient(orchestrator.app).post('/jobs',headers={'x-internal-secret':INTERNAL},json={'urls':['https://example.com/help/']})
            self.assertEqual(response.status_code,400)
        finally:
            orchestrator.app.dependency_overrides.clear()
