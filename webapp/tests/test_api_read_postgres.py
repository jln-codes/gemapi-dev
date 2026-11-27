"""API-READ-01, niveau B (~1 min): vrais rôles/sessions, SQL canonique jetable.

Les treize identités historiques ne portent ici que des valeurs fictives.
GET doit préserver lignes, versions, relations et journal. Les cas négatifs
gardent les FK, les types géométriques et l'isolation inter-bases effectifs.
Exécution : python -m tests.run_postgres webapp.tests.test_api_read_postgres
"""
import json
import os
from pathlib import Path
import socket
import subprocess
from threading import Thread
import time
import unittest
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from fastapi.testclient import TestClient

from digues_webapp.app import create_app
from digues_webapp.queries import fetch_desordre, fetch_desordres_batch
from tests.geometry_fixtures import LINE_SQL, POINT_SQL
from webapp.tests import test_business_postgres as business
from webapp.tests import test_media_tenancy_postgres as canonical


TRONCONS = tuple((UUID(t), UUID(d)) for t, d in (
    ('09578402-d2d6-625e-e7e2-8b5adb00021c', '58cc4868-7c08-2a2d-7601-024da470380a'),
    ('09578402-d2d6-625e-e7e2-8b5adb001205', '58cc4868-7c08-2a2d-7601-024da4707ca2'),
    ('496d26f1-4278-405a-4172-bf66ec00068f', '58cc4868-7c08-2a2d-7601-024da46ed83c'),
    ('496d26f1-4278-405a-4172-bf66ec009931', '58cc4868-7c08-2a2d-7601-024da470905a'),
    ('496d26f1-4278-405a-4172-bf66ec00c2f6', '58cc4868-7c08-2a2d-7601-024da470905a'),
    ('6a561744-e246-e603-ed6f-cd210a16099d', '58cc4868-7c08-2a2d-7601-024da4707ca2'),
    ('abae3ef5-47ab-5a82-9e8d-be2c5d00185c', 'abae3ef5-47ab-5a82-9e8d-be2c5d003bac'),
    ('d6d8083f-9cac-3e30-37fc-935f09002f40', 'd6d8083f-9cac-3e30-37fc-935f09006c50'),
    ('f5e5abcf-38c3-6e27-afd7-35570e03be85', '58cc4868-7c08-2a2d-7601-024da4706bba'),
    ('f5e5abcf-38c3-6e27-afd7-35570e03cf4b', '58cc4868-7c08-2a2d-7601-024da4705cb1'),
))
DESORDRES = tuple((UUID(d), UUID(z) if z else None) for d, z in (
    ('bb404c68-6144-992f-f4ec-d939ea01a137', 'bb404c68-6144-992f-f4ec-d939ea005d75'),
    ('36fa22fb-9b4e-8655-b831-00233000c5b4', None),
    ('6a561744-e246-e603-ed6f-cd210a068efa', 'f5e5abcf-38c3-6e27-afd7-35570e039ade'),
))
POLYGON_SQL = "ST_GeomFromText('POLYGON((1660000 9250000,1660010 9250000,1660010 9250010,1660000 9250000))',3950)"


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Cluster jetable requis')
class OptionalReadPostgresTest(unittest.TestCase):
    cleanup_databases = classmethod(business.BusinessPostgresTest.cleanup_databases.__func__)
    client = business.BusinessPostgresTest.client
    url = business.BusinessPostgresTest.url

    @classmethod
    def setUpClass(cls):
        canonical.MediaPostgresTest.setUpClass.__func__(cls)
        cls.normal_troncon, cls.false_troncon, cls.systeme = uuid4(), uuid4(), uuid4()
        cls.ordinary = {kind: uuid4() for kind in ('POINT', 'LINESTRING', 'POLYGON', 'MULTIPOINT')}
        cls.observation = uuid4()
        for index in (0, 2):
            with psycopg.connect(**{**cls.params, 'dbname': cls.dbnames[index]}) as c:
                c.execute("INSERT INTO systemes(id,libelle,valid) VALUES(%s,'Système témoin',true)", (cls.systeme,))
                for digue in sorted({d for _, d in TRONCONS}):
                    c.execute("INSERT INTO digues(id,libelle,valid) VALUES(%s,%s,true)",
                              (digue, f'Digue {digue}'))
                for identifier, digue in TRONCONS:
                    c.execute(f"INSERT INTO troncons(id,digue_id,designation,libelle,geometry,valid) VALUES(%s,%s,'Tronçon consultable',%s,{LINE_SQL},true)",
                              (identifier, digue, 'Tronçon sans système'))
                linked_digue = uuid4()
                c.execute("INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) VALUES(%s,%s,'Digue complète',true)", (linked_digue, cls.systeme))
                for identifier, digue, valid in ((cls.normal_troncon, linked_digue, True),
                                                (cls.false_troncon, TRONCONS[0][1], False)):
                    c.execute(f"INSERT INTO troncons(id,digue_id,libelle,geometry,valid) VALUES(%s,%s,'Témoin',{LINE_SQL},%s)", (identifier, digue, valid))
                c.execute("INSERT INTO referentiel.categories_desordre(id,libelle) VALUES('READ01','Catégorie conservée')")
                c.execute("INSERT INTO referentiel.types_desordre(id,libelle,categorie_id) VALUES('READ01','Type conservé','READ01')")
                for identifier, zone in DESORDRES:
                    c.execute("INSERT INTO desordres(id,designation,commentaire,lieu_dit,date_debut,date_fin,type_desordre_id,categorie_desordre_id,geometry,valid) "
                              "VALUES(%s,'Désordre non localisé','',NULL,'2025-09-08','2026-05-18','READ01','READ01',NULL,false)", (identifier,))
                    if zone:
                        c.execute("INSERT INTO zones_stockage(id,designation,valid) VALUES(%s,'Zone conservée',false)", (zone,))
                        c.execute("INSERT INTO zones_stockage__desordres(zone_stockage_id,desordre_id) VALUES(%s,%s)", (zone, identifier))
                c.execute("INSERT INTO observations(id,desordre_id,designation,valid) VALUES(%s,%s,'Observation conservée',true)", (cls.observation, DESORDRES[0][0]))
                for kind, geometry in (('POINT', POINT_SQL), ('LINESTRING', LINE_SQL), ('POLYGON', POLYGON_SQL),
                                       ('MULTIPOINT', "ST_GeomFromText('MULTIPOINT(1660000 9250000,1660001 9250001)',3950)")):
                    c.execute(f"INSERT INTO desordres(id,designation,geometry,valid) VALUES(%s,%s,{geometry},true)", (cls.ordinary[kind], kind))
        # Same UUID in B denotes a different object, visible only with B's rights.
        with psycopg.connect(**{**cls.params, 'dbname': cls.dbnames[2]}) as c:
            c.execute("UPDATE desordres SET designation='Sentinelle B' WHERE id=%s", (DESORDRES[0][0],))

    def setUp(self):
        business.BusinessPostgresTest.setUp(self)

    def snapshot(self):
        result = []
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[0]}) as c:
            c.execute('SET TRANSACTION READ ONLY')
            for table in ('troncons', 'digues', 'desordres', 'zones_stockage__desordres',
                          'observations', 'technique.evenements_ressources', 'technique.import_eligibility'):
                result.append(c.execute(sql.SQL('SELECT md5(coalesce(string_agg(row_to_json(r)::text,\'\' ORDER BY row_to_json(r)::text),\'\')) FROM {} r').format(sql.Identifier(*table.split('.')))).fetchone()[0])
        return result

    def test_thirteen_historical_identities_and_no_get_write(self):
        before = self.snapshot()
        client = self.client('reader_alpha')
        for identifier, digue in TRONCONS:
            with self.subTest(troncon=str(identifier)):
                response = client.get(self.url(0, f'troncons/{identifier}'))
                self.assertEqual(response.status_code, 200, response.text)
                p = response.json()['properties']
                self.assertEqual(p['id'], str(identifier)); self.assertTrue(p['valid'])
                self.assertEqual(p['digue_id'], str(digue)); self.assertEqual(p['digue_libelle'], f'Digue {digue}')
                self.assertIsNone(p['systeme_endiguement_id']); self.assertIsNone(p['systeme_endiguement_libelle'])
                self.assertEqual(response.json()['geometry']['type'], 'LineString')
        for identifier, zone in DESORDRES:
            with self.subTest(desordre=str(identifier)):
                response = client.get(self.url(0, f'desordres/{identifier}'))
                self.assertEqual(response.status_code, 200, response.text)
                p = response.json()['properties']
                self.assertEqual(p['id'], str(identifier)); self.assertFalse(p['valid'])
                self.assertIsNone(response.json()['geometry']); self.assertIsNone(p['type_geometrie'])
                self.assertEqual(p['commentaire'], ''); self.assertIsNone(p['lieu_dit'])
                self.assertEqual(p['date_debut'], '2025-09-08'); self.assertEqual(p['date_fin'], '2026-05-18')
                self.assertEqual(p['type_desordre_id'], 'READ01'); self.assertEqual(p['categorie_desordre_libelle'], 'Catégorie conservée')
                self.assertEqual([z['id'] for z in p['zones_stockage']], [str(zone)] if zone else [])
                self.assertEqual(p['troncon_ids'], []); self.assertFalse(p['reperage']['disponible'])
        self.assertEqual(before, self.snapshot())

    def test_ordinary_dispatch_batch_and_unknown_geometry(self):
        client = self.client('reader_alpha')
        for identifier, valid, systeme in ((self.normal_troncon, True, str(self.systeme)),
                                           (self.false_troncon, False, None)):
            r = client.get(self.url(0, f'troncons/{identifier}'))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIs(r.json()['properties']['valid'], valid)
            self.assertEqual(r.json()['properties']['systeme_endiguement_id'], systeme)
        ids = [self.ordinary[k] for k in ('POINT', 'LINESTRING', 'POLYGON')] + [d for d, _ in DESORDRES]
        for identifier in ids:
            self.assertEqual(client.get(self.url(0, f'desordres/{identifier}')).status_code, 200)
        for kind in ('troncons', 'desordres'):
            self.assertEqual(client.get(self.url(0, f'{kind}/{uuid4()}')).status_code, 404)
        self.assertEqual(client.get(self.url(0, f'desordres/{self.ordinary["MULTIPOINT"]}')).status_code, 404)
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[0]}) as c:
            c.execute('SET TRANSACTION READ ONLY')
            self.assertEqual(fetch_desordres_batch(c, ids), [fetch_desordre(c, i) for i in ids])
        # Digue is mandatory and FK-backed, unlike its optional System parent.
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[0]}) as c:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                c.execute("INSERT INTO troncons(digue_id,libelle,valid) VALUES(%s,'Orphelin interdit',true)", (uuid4(),))

    def test_real_sessions_scope_and_permissions(self):
        for resource, identifier in (('troncons', TRONCONS[0][0]), ('desordres', DESORDRES[0][0])):
            for login in ('reader_alpha', 'editor_alpha', 'admin_alpha'):
                self.assertEqual(self.client(login).get(self.url(0, f'{resource}/{identifier}')).status_code, 200)
            for login in ('admin_beta', 'external_reader', 'superadmin'):
                self.assertEqual(self.client(login).get(self.url(0, f'{resource}/{identifier}')).status_code, 403)
            with TestClient(create_app(), base_url='https://testserver') as anonymous:
                self.assertEqual(anonymous.get(self.url(0, f'{resource}/{identifier}')).status_code, 401)
        r = self.client('admin_beta').get(self.url(2, f'desordres/{DESORDRES[0][0]}'))
        self.assertEqual(r.status_code, 200); self.assertEqual(r.json()['properties']['designation'], 'Sentinelle B')

    def test_chromium_real_base_forms(self):
        import uvicorn
        before = self.snapshot()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        fixture = Path(self.tmp.name) / 'read-browser.json'
        fixture.write_text(json.dumps({'base': str(self.bases[0]), 'password': self.password,
            'troncons': [str(t) for t, _ in TRONCONS], 'desordres': [str(d) for d, _ in DESORDRES]}))
        fixture.chmod(0o600)
        from unittest.mock import patch
        artifacts = Path(os.getenv('DIGUES_RC_ARTIFACT_DIR', 'audits/API-READ-01')) / 'browser'; artifacts.mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, AUTH_ORIGIN=origin, AUTH_ALLOW_INSECURE_LOCALHOST='true'):
            server = uvicorn.Server(uvicorn.Config(create_app(), host='127.0.0.1', port=port, log_level='warning'))
            thread = Thread(target=server.run, daemon=True); thread.start()
            try:
                deadline = time.monotonic() + 15
                while not server.started and time.monotonic() < deadline: time.sleep(.05)
                self.assertTrue(server.started)
                completed = subprocess.run(['node_modules/.bin/playwright', 'test', 'tests/e2e/api-read-live.spec.js',
                    '--workers=1', '--output', str(artifacts)], env={**os.environ,
                    'DIGUES_E2E_BASE_URL': origin, 'DIGUES_E2E_GUARDED_HARNESS': 'true',
                    'DIGUES_API_READ_FIXTURE': str(fixture), 'DIGUES_PLAYWRIGHT_TRACE': 'off'}, capture_output=True, text=True, timeout=120)
                (artifacts/'runner.log').write_text(completed.stdout + completed.stderr)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            finally:
                server.should_exit = True; thread.join(15); fixture.unlink(missing_ok=True)
                self.assertFalse(thread.is_alive())
        self.assertEqual(before, self.snapshot())
