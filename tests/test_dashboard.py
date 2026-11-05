import importlib.util
import json
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

for k,v in {'REDIS_URL':'redis://localhost','JWT_SECRET':'test-secret','INTERNAL_SECRET':'test-internal','API_KEY':'test-key','SESSION_KEY':Fernet.generate_key().decode()}.items():
    os.environ.setdefault(k,v)
if importlib.util.find_spec('redis') is None:
    stub=types.ModuleType('redis.asyncio')
    stub.Redis=MagicMock()
    sys.modules['redis']=types.ModuleType('redis')
    sys.modules['redis.asyncio']=stub
from app import gateway, orchestrator
from app.common import INTERNAL, principal

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(orchestrator.app)
        self.headers={'x-internal-secret':INTERNAL,'x-tenant':'alpha'}
    def tearDown(self):
        orchestrator.app.dependency_overrides.clear()
    def test_internal_auth_required(self):
        self.assertEqual(self.client.get('/jobs').status_code,403)
    def test_jwt_required(self):
        self.assertEqual(self.client.get('/jobs',headers=self.headers).status_code,401)
    def test_tenant_list_drops_foreign_and_missing_records(self):
        orchestrator.app.dependency_overrides[principal]=lambda:{'tenant':'alpha'}
        records=[json.dumps({'id':'a','tenant':'alpha','results':[{'text':'source'}]}),json.dumps({'id':'b','tenant':'beta'}),None]
        with patch.object(orchestrator.r,'zrevrange',AsyncMock(return_value=['a','b','c'])) as index,patch.object(orchestrator.r,'mget',AsyncMock(return_value=records)):
            response=self.client.get('/jobs',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual([j['id'] for j in response.json()['jobs']],['a'])
        self.assertNotIn('results',response.json()['jobs'][0])
        index.assert_awaited_once_with('jobs-index:alpha',0,49)
    def test_empty_list(self):
        orchestrator.app.dependency_overrides[principal]=lambda:{'tenant':'alpha'}
        with patch.object(orchestrator.r,'zrevrange',AsyncMock(return_value=[])):
            self.assertEqual(self.client.get('/jobs',headers=self.headers).json(),{'jobs':[]})
    def test_public_ui_protected_data(self):
        client=TestClient(gateway.app)
        response=client.get('/ui')
        self.assertEqual(response.status_code,200)
        self.assertIn('Collection overview',response.text)
        self.assertIn("script-src 'self'",response.headers['content-security-policy'])
        self.assertEqual(client.get('/ui/app.js').status_code,200)
        with patch.object(gateway.r,'get',AsyncMock(return_value=None)):
            self.assertEqual(client.get('/jobs').status_code,401)
    def test_list_forwarding(self):
        upstream=MagicMock()
        upstream.request=AsyncMock(return_value=MagicMock(content=b'{"jobs":[]}',status_code=200))
        context=MagicMock()
        context.__aenter__=AsyncMock(return_value=upstream)
        context.__aexit__=AsyncMock(return_value=False)
        with patch.object(gateway.r,'get',AsyncMock(return_value='alpha')),patch.object(gateway.r,'eval',AsyncMock(return_value=1)),patch.object(gateway.httpx,'AsyncClient',return_value=context):
            response=TestClient(gateway.app).get('/jobs',headers={'x-api-key':'key','authorization':'Bearer sample'})
        self.assertEqual(response.status_code,200)
        args,kwargs=upstream.request.call_args
        self.assertEqual(args,('GET','http://orchestrator:8001/jobs'))
        self.assertEqual(kwargs['headers']['x-tenant'],'alpha')
        self.assertEqual(kwargs['headers']['authorization'],'Bearer sample')
