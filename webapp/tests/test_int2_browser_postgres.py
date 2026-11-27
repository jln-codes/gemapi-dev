"""INT-2: real HTTP/auth/SQL/Chromium; local synthetic raster only.

Run through tests.run_postgres. All databases, ports and files belong to the
guarded test fixture; no application DSN or external tile service is used.
"""
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import secrets
import select
import subprocess
from threading import Thread
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import Response, Request
from PIL import Image
import psycopg
from psycopg import sql
import uvicorn

from digues_app.migration.references import initialize_references
from digues_app.target import PostgreSQLConfig
from digues_webapp.app import create_app
from webapp.tests import test_browser_live_postgres as live


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Disposable cluster required')
class Int2BrowserPostgresTest(unittest.TestCase):
    cleanup_databases = classmethod(live.BrowserLivePostgresTest.cleanup_databases.__func__)

    @classmethod
    def setUpClass(cls):
        live.BrowserLivePostgresTest.setUpClass.__func__(cls)
        cls.disorder, cls.path = uuid4(), uuid4()
        cls.vegetation, cls.ah, cls.prestation = uuid4(), uuid4(), uuid4()
        for name in cls.dbnames:
            from psycopg.conninfo import make_conninfo
            dsn = make_conninfo(**{**cls.params, 'dbname': name})
            initialize_references(target_config=PostgreSQLConfig(dsn=dsn))
            with psycopg.connect(dsn) as conn:
                # Writable synthetic fixture; authorization still runs through real
                # sessions/memberships and the readonly profile remains readonly.
                conn.execute(sql.SQL('GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(f'{name}_rw')))
                conn.execute("INSERT INTO referentiel.types_cheminement(id,code,abrege,libelle,valid) "
                             "VALUES('CAC','chemin_acces_technique','CAC','Chemin accès',true),"
                             "('INT2-LINE','int2_line','L','Linéaire',true) ON CONFLICT DO NOTHING")
                conn.execute("INSERT INTO desordres(id,designation,geometry,valid) VALUES(%s,'INT2 Désordre',"
                             "ST_Transform(ST_SetSRID(ST_Point(2.35,48.85),4326),3950),false)", (cls.disorder,))
                conn.execute("INSERT INTO desordres__troncons(desordre_id,troncon_id) VALUES(%s,%s)", (cls.disorder, cls.trunk))
                conn.execute("INSERT INTO cheminements(id,type_cheminement_id,designation,geometry,valid) "
                             "VALUES(%s,'CAC','INT2 accès polygonal',ST_Transform(ST_GeomFromText("
                             "'POLYGON((2.35 48.85,2.351 48.85,2.351 48.851,2.35 48.85))',4326),3950),true)", (cls.path,))
                conn.execute("INSERT INTO cheminements__desordres(cheminement_id,desordre_id) VALUES(%s,%s)", (cls.path,cls.disorder))
                conn.execute("INSERT INTO vegetation(id,famille_vegetation,disposition,sous_type,designation,geometry,valid) "
                             "VALUES(%s,'ARBRES','ISOLE','ARBRE_SUR_PIED','INT2 Végétation',"
                             "ST_Transform(ST_SetSRID(ST_Point(2.35,48.85),4326),3950),true)",
                             (cls.vegetation,))
                conn.execute("INSERT INTO vegetation__desordres(vegetation_id,desordre_id) VALUES(%s,%s)",
                             (cls.vegetation, cls.disorder))
                conn.execute("INSERT INTO amenagements_hydrauliques(id,designation,valid) VALUES(%s,'INT2 AH',true)", (cls.ah,))
                conn.execute("INSERT INTO amenagements_hydrauliques__desordres(amenagement_hydraulique_id,desordre_id) VALUES(%s,%s)",
                             (cls.ah, cls.disorder))
                conn.execute("INSERT INTO prestations(id,designation,date_fin,"
                             "valid) "
                             "VALUES(%s,'INT2 Prestation','2026-10-11',true)", (cls.prestation,))

    def test_integrated_browser(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        directory = Path(self.tmp.name)
        fixture = directory / 'int2.json'
        login_reset_token = secrets.token_urlsafe(32)
        fixture.write_text(json.dumps({'password': self.password, 'bases': list(map(str, self.bases)),
            'trunk': str(self.trunk), 'observation': str(self.observation),
            'disorder': str(self.disorder), 'path': str(self.path), 'vegetation': str(self.vegetation),
            'ah': str(self.ah), 'prestation': str(self.prestation),
            'login_reset_token': login_reset_token}))
        fixture.chmod(0o600)
        catalog = directory / 'basemaps.toml'
        template = (Path(__file__).parents[1] / 'backend/digues_webapp/basemaps.toml').read_text()
        epure = template.split('[[basemaps]]')[1]
        chunks = []
        for index, (identifier, minimum, maximum) in enumerate([
                ('test-standard', 0, 18), ('test-detail', 0, 19), ('test-aerial', 6, 19)]):
            block = epure.replace('ign-epure', identifier).replace('PLAN.IGN Épuré', identifier)
            block = block.replace('ign/epure', identifier).replace('© IGN — PLAN.IGN', 'Synthetic INT-2')
            block = block.replace('/basemap/plan-ign.json', f'/basemap/{identifier}.json')
            block = block.replace('order = 10', f'order = {10 + index}')
            block = block.replace('default = true', f'default = {str(index == 0).lower()}')
            block = block.replace('min_zoom = 0', f'min_zoom = {minimum}')
            block = block.replace('max_zoom = 18', f'max_zoom = {maximum}')
            block = block.replace('display_max_zoom = 19', f'display_max_zoom = {maximum + 1}')
            block = block.replace('"planned"', '"available"').replace('"review-required"', '"authorized"')
            chunks.append('[[basemaps]]' + block)
        catalog.write_text('contract_version = 1\nmin_free_bytes = 1048576\n' + ''.join(chunks))
        # The cache owner consumes the product registry, not the display TOML.
        # Reuse the official product contract with these three synthetic IDs.
        products = directory / 'cartographic_caches.json'
        registry = json.loads((Path(__file__).parents[1] /
            'backend/digues_webapp/cartographic_caches.json').read_text())
        epure_product = next(p for p in registry['products'] if p['id'] == 'ign-epure')
        registry['min_free_bytes'] = 1048576
        registry['products'] = [{**epure_product, 'id': identifier,
            'display_name': identifier, 'directory': identifier}
            for identifier in ('test-standard', 'test-detail', 'test-aerial')]
        products.write_text(json.dumps(registry))
        tiles = {}
        for name, color in [('test-standard', 'navy'), ('test-detail', 'green'), ('test-aerial', 'orange')]:
            out = io.BytesIO()
            Image.new('RGB', (256, 256), color).save(out, format='WEBP')
            tiles[name] = out.getvalue()
        state = {'generation': 'v1', 'delay': 0, 'fail': False, 'requests': []}
        admin_token = secrets.token_urlsafe(32)
        cache_error = (Path(os.environ['DIGUES_RC_ARTIFACT_DIR'])
                       if os.environ.get('DIGUES_RC_ARTIFACT_DIR') else directory)
        cache_error.mkdir(parents=True, exist_ok=True)
        cache_error = cache_error / 'cache-owner.stderr.log'
        cache_error_stream = cache_error.open('w')
        self.addCleanup(cache_error_stream.close)
        cache_owner = subprocess.Popen(['node', 'tests/int2_cache_server.mjs',
            str(directory / 'cache'), str(products)], stdout=subprocess.PIPE, stderr=cache_error_stream,
            text=True, env={**os.environ, 'BASEMAP_ADMIN_TOKEN': admin_token})
        def stop_cache_owner():
            cache_owner.terminate()
            cache_owner.wait(timeout=10)
            cache_owner.stdout.close()
        self.addCleanup(stop_cache_owner)
        self.assertTrue(select.select([cache_owner.stdout], [], [], 15)[0],
                        f'Cache owner startup timed out after 15s; pid={cache_owner.pid}; '
                        f'stderr={cache_error}')
        cache_line = cache_owner.stdout.readline().strip()
        if not cache_line.isdecimal():
            cache_error_stream.flush()
            diagnostic = cache_error.read_text(errors='replace')[-4000:].replace(
                admin_token, '[REDACTED]')
            raise RuntimeError(f'Cache owner startup failed: pid={cache_owner.pid}, '
                               f'exit={cache_owner.poll()}, port={cache_line!r}, '
                               f'stderr={diagnostic}')
        cache_port = int(cache_line)
        with patch.dict(os.environ, AUTH_ORIGIN=origin, AUTH_ALLOW_INSECURE_LOCALHOST='true',
                        ADMIN_ORIGIN=f'http://localhost:{port}', ADMIN_ALLOW_INSECURE_LOCALHOST='true',
                        BASEMAP_ADMIN_URL=f'http://127.0.0.1:{cache_port}', BASEMAP_ADMIN_TOKEN=admin_token,
                        BASEMAP_CATALOG_FILE=str(catalog), CARTOGRAPHIC_CACHE_PRODUCTS_FILE=str(products), BASEMAP_TILEJSON_URL='/basemap/test-standard.json',
                        MEDIA_BACKEND='filesystem', MEDIA_ROOT=str(directory / 'media')):
            app = create_app()

            @app.get('/basemap/{identifier}.json')
            def tilejson(identifier: str):
                minimum, maximum = (6, 19) if identifier == 'test-aerial' else (0, 19 if identifier == 'test-detail' else 18)
                return {'tilejson': '3.0.0', 'tiles': [f'/basemap/{identifier}/{state["generation"]}/{{z}}/{{x}}/{{y}}.webp'],
                    'minzoom': minimum, 'maxzoom': maximum, 'attribution': 'Synthetic INT-2', 'scheme': 'xyz'}

            @app.get('/basemap/{identifier}/{generation}/{z}/{x}/{y}.webp')
            async def tile(identifier: str, generation: str, z: int, x: int, y: int):
                state['requests'].append([identifier, generation, z, x, y])
                await asyncio.sleep(state['delay'])
                return Response(tiles.get(identifier, b''), status_code=404 if state['fail'] else 200,
                                media_type='image/webp', headers={'Cache-Control': 'no-store'})

            @app.post('/int2/control')
            async def control(request: Request):
                # Test-only endpoint in an ephemeral server on loopback, never registered by production.
                changes = await request.json()
                for key in ('generation', 'delay', 'fail'):
                    if key in changes:
                        state[key] = changes[key]
                if changes.get('reset'):
                    state['requests'].clear()
                return state

            @app.post('/int2/reset-login-attempts')
            def reset_login_attempts(request: Request):
                # Test-only loopback route: isolate scenarios without changing
                # the real per-IP login limit or accepting an unauthenticated reset.
                if request.headers.get('Authorization') != f'Bearer {login_reset_token}':
                    return Response(status_code=403)
                limiter = app.state.auth_login_limiter
                with limiter.lock:
                    limiter.attempts.clear()
                return Response(status_code=204)

            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
            thread = Thread(target=server.run, daemon=True)
            thread.start()
            try:
                for _ in range(200):
                    if server.started:
                        break
                    time.sleep(.05)
                self.assertTrue(server.started)
                artifacts = (Path(os.environ['DIGUES_RC_ARTIFACT_DIR'])
                             if os.environ.get('DIGUES_RC_ARTIFACT_DIR') else
                             Path(__file__).resolve().parents[2] / 'audits' / 'int2' / uuid4().hex)
                artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
                command = ['node_modules/.bin/playwright', 'test', 'tests/e2e/int2-integrated.spec.js', 'tests/e2e/map-panel-layout.spec.js',
                           '--workers=1', '--output', str(artifacts / 'browser')]
                if os.environ.get('DIGUES_RC_RUN_ID'):
                    command += ['--reporter=line,./tests/rc_playwright_reporter.cjs']
                if os.getenv('DIGUES_BROWSER_GREP'):
                    command += ['--grep', os.environ['DIGUES_BROWSER_GREP']]
                log = artifacts / 'browser.log'
                print(f'INT-2 browser artifacts: {artifacts}', flush=True)
                with log.open('w') as stream:
                    result = subprocess.run(command, env={**os.environ, 'DIGUES_E2E_BASE_URL': origin,
                        'DIGUES_INT2_FIXTURE': str(fixture), 'DIGUES_E2E_GUARDED_HARNESS': 'true'},
                        stdout=stream, stderr=subprocess.STDOUT, text=True, timeout=900)
                output = log.read_text()
                print(output, flush=True)
                self.assertEqual(result.returncode, 0, output)
            finally:
                server.should_exit = True
                thread.join(15)
                self.assertFalse(thread.is_alive())
