"""BASE-FICHES-02, niveau B (~3–5 min) : Chromium/HTTP/SQL réels.

Le runner tests.run_postgres crée le cluster privé ; les bases Alpha/Beta sont
propres à ce test. Même UUID dans deux bases, readers, éditeurs, conflits et
navigation utilisent les vraies sessions, sans route métier simulée.
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

from digues_app.target import PostgreSQLConfig
from digues_app.demo.seed import seed_demo_cursor
from digues_app.demo.target import DemoTarget
from digues_app.media_ingestion import FilesystemMediaStorage
from digues_webapp.app import create_app
from webapp.tests import test_level_a_multitenancy_postgres as harness


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Runner PostgreSQL jetable requis')
class BaseDetailBrowserTest(unittest.TestCase):
    cleanup_databases = classmethod(harness.LevelAMultitenancyPostgresTest.cleanup_databases.__func__)
    setUpClass = classmethod(harness.LevelAMultitenancyPostgresTest.setUpClass.__func__)
    setUp = harness.LevelAMultitenancyPostgresTest.setUp
    client = harness.LevelAMultitenancyPostgresTest.client
    context = harness.LevelAMultitenancyPostgresTest.context
    url = harness.LevelAMultitenancyPostgresTest.url

    def test_consultation_families_layout_navigation_editors_and_isolation(self):
        artifacts = Path('audits/base-fiches-02/live') / uuid4().hex
        artifacts.mkdir(parents=True)
        tables = {
            'systemes': 'systemes', 'digues': 'digues', 'troncons': 'troncons',
            'desordres': 'desordres', 'mobilier': 'mobilier', 'equipements_mesure': 'equipements_mesure',
            'reseaux_techniques': 'reseaux_techniques', 'ouvrages_particuliers': 'ouvrages_particuliers',
            'cheminements': 'cheminements', 'vegetation': 'vegetation',
            'amenagements_hydrauliques': 'amenagements_hydrauliques', 'zones_stockage': 'zones_stockage',
            'prestations': 'prestations', 'troncons_lit': 'segments_hydrographiques',
            'elements_morphologiques_lit': 'elements_morphologiques_lit', 'observations': 'observations',
        }
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[0]}) as connection:
            seed_demo_cursor(connection.cursor(), target=DemoTarget(
                PostgreSQLConfig(dsn=connection.info.dsn), FilesystemMediaStorage(artifacts / 'media')))
            # The demo covers fifteen families but has no Ouvrage particulier.
            connection.execute("INSERT INTO public.ouvrages_particuliers(id,designation,valid) VALUES (%s,'Ouvrage particulier du banc Base',false)", (uuid4(),))
            # Table names below are fixed test constants, never user input.
            ids = {}
            for resource, table in tables.items():
                row = connection.execute(f'SELECT id FROM public.{table} ORDER BY id LIMIT 1').fetchone()
                self.assertIsNotNone(row, f'Fixture manquante pour {resource}')
                ids[resource] = str(row[0])
            trunk, digue, system = connection.execute("""SELECT t.id,d.id,d.systeme_endiguement_id
                FROM public.troncons t JOIN public.digues d ON d.id=t.digue_id
                WHERE t.type_physique_id='INDETERMINE' ORDER BY t.id LIMIT 1""").fetchone()
            ids.update(troncons=str(trunk), digues=str(digue), systemes=str(system))
            # Known historical complement, outside the INDETERMINE profile.
            connection.execute('UPDATE public.troncons SET hauteur_murette=12.75,num_couche=0 WHERE id=%s', (trunk,))
            before = connection.execute('SELECT ST_AsEWKB(geometry),hauteur_murette,num_couche FROM public.troncons WHERE id=%s', (trunk,)).fetchone()
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[2]}) as connection:
            connection.execute("INSERT INTO public.systemes(id,designation,libelle,valid) VALUES (%s,'Système Beta exclusif','Beta exclusif',true)", (system,))
        fixture = { 'base': str(self.bases[0]), 'beta': str(self.bases[2]), 'ids': ids,
                    'password': self.password, 'artifacts': str(artifacts) }
        fixture_path = artifacts / 'fixture.json'
        fixture_path.write_text(json.dumps(fixture)); fixture_path.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        with patch.dict(os.environ, AUTH_ORIGIN=origin, AUTH_ALLOW_INSECURE_LOCALHOST='true'):
            server = uvicorn.Server(uvicorn.Config(create_app(), host='127.0.0.1', port=port, log_level='warning'))
            thread = Thread(target=server.run, daemon=True); thread.start()
            try:
                deadline = time.monotonic() + 15
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.03)
                self.assertTrue(server.started, 'HTTP server readiness')
                with (artifacts / 'browser.log').open('w') as log:
                    result = subprocess.run(['node', 'webapp/tests/base-detail-browser.cjs', origin, str(fixture_path)],
                        text=True, stdout=log, stderr=subprocess.STDOUT, timeout=300)
                self.assertEqual(result.returncode, 0, (artifacts / 'browser.log').read_text())
            finally:
                server.should_exit = True; thread.join(15); self.assertFalse(thread.is_alive())
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[0]}) as connection:
            after = connection.execute('SELECT ST_AsEWKB(geometry),hauteur_murette,num_couche FROM public.troncons WHERE id=%s', (trunk,)).fetchone()
            self.assertEqual(after, before, 'La consultation et l’édition attributaire conservent la géométrie et les compléments historiques.')
