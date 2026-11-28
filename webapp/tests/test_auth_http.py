import os
from unittest import TestCase
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from digues_webapp.app import create_app
from digues_webapp import session_database as db


class SecurityHTTP(TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"AUTH_ORIGIN": "https://site.test",
                                                  "AUTH_ATTEMPTS_PER_MINUTE": "3"}))
        self.client = self.enterContext(TestClient(create_app(), base_url='https://site.test'))
        self.headers = {'Origin': 'https://site.test', 'X-Gemapi-CSRF': '1'}
        self.body = {'login': 'fixture', 'password': 'synthetic-only'}

    def post(self, **kwargs):
        return self.client.post('/api/auth/login', headers=self.headers, **kwargs)

    def test_origin_for_each_write(self):
        for endpoint in ('login', 'logout', 'activity', 'demo'):
            for headers in ({}, {'Origin': 'https://site.test'}, {**self.headers, 'Origin': 'null'},
                            {**self.headers, 'Origin': 'https://other.site.test'}):
                res = self.client.post('/api/auth/'+endpoint, headers=headers, json=self.body)
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.headers['cache-control'], 'no-store')

    def test_input_and_size(self):
        for field in ('host', 'port', 'schema', 'dbname', 'options', 'service'):
            res = self.post(json={**self.body, field: 'somewhere'})
            self.assertEqual(res.status_code, 400)
            self.assertNotIn(self.body['password'], res.text)
        self.assertEqual(self.post(json={**self.body,'password':'x'*9000}).status_code, 413)
        self.assertEqual(self.post(data=self.body).status_code, 415)
        self.assertEqual(self.post(json={'login':'','password':''}).status_code,400)

    def test_cookie_and_logout(self):
        self.client.cookies.set('__Host-gemapi_session', 'fixed')
        with patch.object(db, 'open_individual', return_value=(MagicMock(), 'some schema')):
            res = self.post(json=self.body)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.json()), {'context', 'login'})
        cookie = res.headers['set-cookie']
        for item in ('__Host-gemapi_session=', 'HttpOnly', 'Secure', 'SameSite=strict', 'Path=/', 'Max-Age=43200'):
            self.assertIn(item, cookie)
        for item in ('fixed', 'Domain=', self.body['password']):
            self.assertNotIn(item, cookie)
        self.assertNotIn(self.body['password'], res.text)
        result = self.client.post('/api/auth/logout', headers={**self.headers,'X-Gemapi-Context':res.json()['context']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)

    def test_clean_errors_and_throttle(self):
        for error, status in ((db.BadCredentials,401),(db.AccessDenied,403),(db.DatabaseUnavailable,503)):
            with patch.object(db, 'open_individual', side_effect=error(self.body['password'])):
                res = self.post(json=self.body)
                self.assertEqual(res.status_code, status)
                self.assertNotIn(self.body['password'], res.text)
                self.assertEqual(res.headers['cache-control'], 'no-store')
        self.assertEqual(self.post(json=self.body).status_code, 429)

    def test_no_production_prefill(self):
        self.assertEqual(self.client.post('/api/auth/demo', headers=self.headers).json(), {})
        with patch.dict(os.environ, {'AUTH_DEMO_PREFILL':'1','AUTH_DEPLOYMENT':'production'}):
            with self.assertRaises(ValueError):
                create_app()

    def test_technical_credentials_cannot_be_distributed(self):
        from digues_webapp.auth import Authentication
        with patch.dict(os.environ, {
            'AUTH_DEPLOYMENT': 'demo', 'AUTH_DEMO_PREFILL': '1', 'AUTH_DEMO_LOGIN': 'public',
            'AUTH_DEMO_PASSWORD': 'public-example', 'DATABASE_URL': 'host=/socket dbname=fixture',
            'PGUSER': 'technical', 'PGPASSWORD': 'public-example',
        }), patch.object(db, 'open_individual') as connect:
            auth = Authentication()
            auth.verify_demo()
            self.assertFalse(auth.demo_verified)
            connect.assert_not_called()

    def test_single_worker(self):
        with patch.dict(os.environ, {'WEB_CONCURRENCY':'2'}), self.assertRaises(RuntimeError):
            with TestClient(create_app()):
                self.fail()
