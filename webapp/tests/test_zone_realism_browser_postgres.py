"""B (~2–4 min): real reader, Leaflet and disposable PostGIS, no route mocks.

Mutable resources are owned by tests.run_postgres. Historic invalid Zone shapes
are isolated fixtures; no application/VPS database is selected by this test.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
from threading import Thread
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
import uvicorn
from digues_app.demo.seed import seed_demo_cursor, _extended_id
from digues_app.demo.target import DemoTarget
from digues_app.media_ingestion import FilesystemMediaStorage
from digues_app.target import PostgreSQLConfig
from digues_webapp.app import create_app
from webapp.tests import test_level_a_multitenancy_postgres as harness


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Runner PostgreSQL jetable requis')
class ZoneRealismBrowserTest(unittest.TestCase):
    cleanup_databases = classmethod(harness.LevelAMultitenancyPostgresTest.cleanup_databases.__func__)
    setUpClass = classmethod(harness.LevelAMultitenancyPostgresTest.setUpClass.__func__)
    setUp = harness.LevelAMultitenancyPostgresTest.setUp
    client = harness.LevelAMultitenancyPostgresTest.client
    context = harness.LevelAMultitenancyPostgresTest.context
    url = harness.LevelAMultitenancyPostgresTest.url

    def test_zone_fill_selection_and_unchanged_line_styles(self):
        artifacts = Path('audits/demo-ah-realism-01/browser') / uuid4().hex
        artifacts.mkdir(parents=True)
        line, point = uuid4(), uuid4()
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[0]}) as connection:
            seed_demo_cursor(connection.cursor(), target=DemoTarget(
                PostgreSQLConfig(dsn=connection.info.dsn), FilesystemMediaStorage(artifacts/'media')))
            connection.execute("INSERT INTO public.zones_stockage(id,designation,valid,geometry) VALUES (%s,'Zone linéaire historique',false,ST_SetSRID(ST_MakeLine(ST_MakePoint(1674400,9263900),ST_MakePoint(1674400,9264100)),3950)),(%s,'Zone ponctuelle historique',false,ST_SetSRID(ST_MakePoint(1675600,9264000),3950))",(line,point))
        fixture = dict(base=str(self.bases[0]), password=self.password,
                       zone=str(_extended_id('public.zones_stockage','zone-stockage-pres-carbonade')),
                       trunk=str(_extended_id('public.troncons','troncon-ah-pres-carbonade')),
                       line=str(line), point=str(point), artifacts=str(artifacts))
        path=artifacts/'fixture.json';path.write_text(json.dumps(fixture));path.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        with patch.dict(os.environ,AUTH_ORIGIN=origin,AUTH_ALLOW_INSECURE_LOCALHOST='true'):
            server=uvicorn.Server(uvicorn.Config(create_app(),host='127.0.0.1',port=port,log_level='warning'))
            thread=Thread(target=server.run,daemon=True);thread.start()
            try:
                deadline=time.monotonic()+15
                while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.03)
                self.assertTrue(server.started,'HTTP readiness')
                with (artifacts/'browser.log').open('w') as log:
                    result=subprocess.run(['node','webapp/tests/zone-realism-browser.cjs',origin,str(path)],stdout=log,stderr=subprocess.STDOUT,text=True,timeout=180)
                self.assertEqual(result.returncode,0,(artifacts/'browser.log').read_text())
            finally:
                server.should_exit=True;thread.join(15);self.assertFalse(thread.is_alive())
