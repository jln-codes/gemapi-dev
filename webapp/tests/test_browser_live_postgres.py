"""Opt-in Chromium + HTTP + PostgreSQL; synthetic local cluster only.

No mocked identity, business SQL, cookies, memberships or offline receipt.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import uvicorn

from digues_app.control.catalog import standard_role_id
from digues_app.deployment.bootstrap import RUNTIME_GRANTS
from digues_app.control import directory
from digues_app.deployment.public_demo import configure_public_demo
from digues_webapp.app import create_app
from webapp.tests import test_business_postgres as business
from webapp.tests import test_media_tenancy_postgres as media


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN') and os.getenv('DIGUES_BROWSER_MULTIBASE_TEST') == 'true',
                     'Chromium/PostgreSQL multi-base local non demandé')
class BrowserLivePostgresTest(unittest.TestCase):
    cleanup_databases = classmethod(business.BusinessPostgresTest.cleanup_databases.__func__)

    @classmethod
    def setUpClass(cls):
        media.MediaPostgresTest.setUpClass.__func__(cls)
        role = f'digues_test_control_runtime_{uuid4().hex[:10]}'
        password = secrets.token_urlsafe(32)
        verifier = cls.admin.pgconn.encrypt_password(
            password.encode(), role.encode(), b'scram-sha-256'
        ).decode()
        cls.admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}').format(
            sql.Identifier(role), sql.Literal(verifier)))
        cls.roles.append(role)
        cls.admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
            sql.Identifier(cls.control_name), sql.Identifier(role)))
        with psycopg.connect(cls.control_dsn) as conn:
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            for statement in RUNTIME_GRANTS:
                conn.execute(sql.SQL(statement + ' TO {}').format(sql.Identifier(role)))
        cls.runtime_control_dsn = make_conninfo(
            **{**cls.params, 'dbname': cls.control_name, 'user': role, 'password': password}
        )
        os.environ['CONTROL_DATABASE_URL'] = cls.runtime_control_dsn
        cls.observation, cls.trunk, cls.system, cls.digue = (uuid4() for _ in range(4))
        cls.session_observation, cls.tabs_observation = uuid4(), uuid4()
        cls.additional_observations = (uuid4(), uuid4(), uuid4())
        for index, name in enumerate(cls.dbnames):
            with psycopg.connect(**{**cls.params, 'dbname': name}) as conn:
                # This browser scenario now creates/updates one isolated system.
                # Keep real application permission checks and real runtime SQL.
                conn.execute(sql.SQL('GRANT INSERT, UPDATE ON public.systemes TO {}')
                             .format(sql.Identifier(f'{name}_rw')))
                metadata = conn.execute(
                    'SELECT srid FROM technique.metadonnees_base WHERE singleton IS TRUE'
                ).fetchall()
                if metadata != [(3950,)]:
                    raise AssertionError(f'Métadonnée CRS de test inattendue dans {name}: {metadata!r}')
                conn.execute('INSERT INTO systemes(id,libelle,valid) VALUES (%s,%s,true)', (cls.system, f'Sentinelle {index}'))
                conn.execute('INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) VALUES (%s,%s,%s,true)', (cls.digue, cls.system, f'Digue {index}'))
                conn.execute("INSERT INTO troncons(id,type_physique_id,digue_id,libelle,geometry,valid) VALUES (%s,'INDETERMINE',%s,%s,ST_GeomFromText('LINESTRING(800000 6450000,800100 6450100)',3950),true)", (cls.trunk, cls.digue, f'Tronçon {index}'))
                conn.execute('INSERT INTO observations(id,troncon_id,designation,valid) VALUES (%s,%s,%s,true)', (cls.observation, cls.trunk, f'Observation {index}'))
                for observation in (cls.session_observation, cls.tabs_observation):
                    conn.execute('INSERT INTO observations(id,troncon_id,designation,valid) VALUES (%s,%s,%s,true)',
                                 (observation, cls.trunk, f'Observation {index}'))
                for position, observation in enumerate(cls.additional_observations):
                    conn.execute('INSERT INTO observations(id,troncon_id,designation,valid) VALUES (%s,%s,%s,true)',
                                 (observation, cls.trunk, f'Observation {index}-{position}'))
        # Explicit enrollment in this suite-owned instance, using the real runtime
        # role. This is also the activation path for an already populated demo.
        cls.demo_root = Path(cls.tmp.name) / 'public-demo'
        cls.demo_root.mkdir(mode=0o700)
        for name in ('provisioning', 'runtime-config'):
            (cls.demo_root / name).mkdir(mode=0o700)
        with psycopg.connect(cls.runtime_control_dsn) as connection:
            directory.change_membership(connection, cls.users['superadmin'].id, 'database',
                                        cls.bases[0], cls.users['superadmin'].id,
                                        role='database_admin', create=True)
        cls.public_demo = configure_public_demo(cls.users['superadmin'].id, cls.bases[0], root=cls.demo_root)

    def test_real_browser_login_offline_replay_and_other_account(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        artifacts = (Path(os.environ['DIGUES_RC_ARTIFACT_DIR']) / 'browser'
                     if os.environ.get('DIGUES_RC_ARTIFACT_DIR') else
                     Path(__file__).resolve().parents[2] / 'audits' / 'browser-live' / uuid4().hex)
        artifacts.mkdir(parents=True)
        fixture = Path(self.tmp.name) / 'browser.json'
        control_token = secrets.token_urlsafe(32)
        testcase = self
        class ControlHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.headers.get('Authorization') != f'Bearer {control_token}':
                    self.send_response(403); self.end_headers(); return
                if self.path == '/login-attempts/reset':
                    # Each Playwright scenario starts with a fresh limiter window;
                    # the real app still enforces its per-IP production limit.
                    limiter = browser_app.state.auth_login_limiter
                    with limiter.lock:
                        limiter.attempts.clear()
                    self.send_response(204); self.end_headers(); return
                with psycopg.connect(testcase.control_dsn) as conn:
                    if self.path == '/sessions/revoke-editor':
                        conn.execute('UPDATE public.sessions SET revoked_at=clock_timestamp() '
                                     'WHERE user_id=%s AND revoked_at IS NULL',
                                     (testcase.users['editor_alpha'].id,))
                    elif self.path in ('/permissions/reader', '/permissions/editor'):
                        role = 'reader' if self.path.endswith('reader') else 'editor'
                        conn.execute('UPDATE public.memberships SET role_id=%s WHERE user_id=%s '
                                     'AND business_database_id=%s',
                                     (standard_role_id(role), testcase.users['editor_alpha'].id,
                                      testcase.bases[0]))
                    else:
                        self.send_response(404); self.end_headers(); return
                self.send_response(204); self.end_headers()
            def log_message(self, _format, *_args):
                pass
        control = ThreadingHTTPServer(('127.0.0.1', 0), ControlHandler)
        control_thread = Thread(target=control.serve_forever, daemon=True)
        control_thread.start()
        fixture.write_text(json.dumps({
            'password': self.password, 'bases': list(map(str, self.bases)),
            'observation': str(self.observation), 'trunk': str(self.trunk),
            'session_observation': str(self.session_observation),
            'tabs_observation': str(self.tabs_observation),
            'additional_observations': list(map(str, self.additional_observations)),
            'control_url': f'http://127.0.0.1:{control.server_address[1]}',
            'control_token': control_token,
            'public_demo': self.public_demo,
            'superadmin_id': str(self.users['superadmin'].id),
        }))
        fixture.chmod(0o600)
        with patch.dict(os.environ, AUTH_ORIGIN=origin, AUTH_ALLOW_INSECURE_LOCALHOST='true',
                        PUBLIC_DEMO_CONFIG_FILE=str(self.demo_root / 'runtime-config/public-demo.json'),
                        MEDIA_BACKEND='filesystem', MEDIA_ROOT=str(Path(self.tmp.name) / 'media'),
                        DATABASE_URL='host=/tmp/digues-no-database dbname=unavailable connect_timeout=1',
                        DIGUES_POSTGRES_HOST='/tmp/digues-no-database'):
            browser_app = create_app()
            server = uvicorn.Server(uvicorn.Config(browser_app, host='127.0.0.1', port=port, log_level='warning'))
            thread = Thread(target=server.run, daemon=True); thread.start()
            try:
                for _ in range(200):
                    if server.started: break
                    time.sleep(.05)
                self.assertTrue(server.started)
                command = ['npx', 'playwright', 'test', 'tests/e2e/multi-tenant-live.spec.js',
                           'tests/e2e/pwa-shell.spec.js', '--workers=1',
                           '--output', str(artifacts)]
                if os.environ.get('DIGUES_RC_RUN_ID'):
                    command += ['--reporter=line,./tests/rc_playwright_reporter.cjs']
                if os.getenv('DIGUES_BROWSER_GREP'):
                    command.extend(['--grep', os.environ['DIGUES_BROWSER_GREP']])
                result = subprocess.run(command,
                    env={**os.environ, 'DIGUES_E2E_BASE_URL': origin, 'DIGUES_BROWSER_FIXTURE': str(fixture),
                         'DIGUES_E2E_GUARDED_HARNESS': 'true'},
                    capture_output=True, text=True, timeout=600)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                server.should_exit = True; thread.join(15)
                control.shutdown(); control.server_close(); control_thread.join(15)
                self.assertFalse(thread.is_alive(), 'Le serveur HTTP de test est encore actif')
                self.assertFalse(control_thread.is_alive(), 'Le serveur de contrôle de test est encore actif')
                with socket.socket() as probe:
                    self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0,
                                        'Le port HTTP de test est encore occupé')
                with socket.socket() as probe:
                    self.assertNotEqual(probe.connect_ex(control.server_address), 0,
                                        'Le port du serveur de contrôle est encore occupé')
