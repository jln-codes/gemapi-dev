"""ENTITY-TYPE-03 level B (~2 min): real Chromium/HTTP/sessions/PostGIS.

Owned disposable multi-base harness; unique rows, active/inactive catalogues.
No transport mocks. Both form hosts, draft detours, partial writes and real OCC
are exercised. SQL checks retained geometry and classifications afterwards.
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


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Official disposable runner required')
class HistoricalClassificationsBrowserTest(unittest.TestCase):
    cleanup_databases=classmethod(harness.LevelAMultitenancyPostgresTest.cleanup_databases.__func__)
    setUpClass=classmethod(harness.LevelAMultitenancyPostgresTest.setUpClass.__func__)
    setUp=harness.LevelAMultitenancyPostgresTest.setUp
    client=harness.LevelAMultitenancyPostgresTest.client
    context=harness.LevelAMultitenancyPostgresTest.context
    url=harness.LevelAMultitenancyPostgresTest.url

    def test_both_hosts_preserve_history_drafts_and_conflicts(self):
        artifacts=Path('audits/entity-type-03/browser')/uuid4().hex;artifacts.mkdir(parents=True)
        keys={name:str(uuid4()) for name in ('ouvrage','zone','chemin','historical','tree','observation','photo')}
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[0]}) as c:
            seed_demo_cursor(c.cursor(),target=DemoTarget(PostgreSQLConfig(dsn=c.info.dsn),FilesystemMediaStorage(artifacts/'media')))
            for table in ('types_ouvrage_particulier','types_amenagement_hydraulique','types_cheminement','orientations_photo','cotes'):
                # Inactive canonical source retained; the test does not alter a
                # reference shared by another scenario or a deployment database.
                from psycopg import sql
                c.execute(sql.SQL("INSERT INTO referentiel.{} SELECT (jsonb_populate_record(NULL::referentiel.{},to_jsonb(src)||jsonb_build_object('id','ET03_BROWSER','code','ET03_BROWSER','abrege','ET03_BROWSER','libelle','Historique inactif','valid',false))).* FROM referentiel.{} src LIMIT 1").format(*[sql.Identifier(table)]*3))
            for name,table,field,geom in [('ouvrage','ouvrages_particuliers','type_id','POINT(2 48)'),('zone','zones_stockage','type_id','POLYGON((2 48,2.001 48,2.001 48.001,2 48))'),('chemin','cheminements','type_cheminement_id','LINESTRING(2 48,2.001 48.001)')]:
                c.execute(sql.SQL("INSERT INTO public.{}(id,{},designation,valid,geometry) VALUES(%s,'ET03_BROWSER','Historique ET03',false,ST_Transform(ST_GeomFromText(%s,4326),3950))").format(sql.Identifier(table),sql.Identifier(field)),(keys[name],geom))
            c.execute("INSERT INTO public.vegetation(id,designation,valid) VALUES(%s,'Incomplète ET03',false)",(keys['historical'],))
            c.execute("INSERT INTO public.vegetation(id,designation,famille_vegetation,disposition,sous_type,valid,geometry) VALUES(%s,'Souche ET03','ARBRES','ISOLE','SOUCHE',true,ST_Transform(ST_GeomFromText('POINT(2 48)',4326),3950))",(keys['tree'],))
            c.execute('INSERT INTO public.observations(id,vegetation_id,valid) VALUES(%s,%s,true)',(keys['observation'],keys['tree']))
            c.execute("INSERT INTO public.photos(id,observation_id,orientation_photo_id,cote_id,valid) VALUES(%s,%s,'ET03_BROWSER','ET03_BROWSER',false)",(keys['photo'],keys['observation']))
            before=c.execute('SELECT ST_AsEWKB(geometry) FROM public.vegetation WHERE id=%s',(keys['tree'],)).fetchone()
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[2]}) as c:
            c.execute("INSERT INTO public.vegetation(id,commentaire,valid) VALUES(%s,'Autre base intacte',false)",(keys['historical'],))
        # Real permission and base selection checks on the changed endpoint.
        reader=self.client('reader_alpha');editor=self.client('editor_alpha')
        r=reader.put(self.url(0,'vegetation/'+keys['historical']),json={'commentaire':'interdit'},headers={**self.headers,'If-Match':'"1"','Operation-Id':str(uuid4())})
        self.assertEqual(r.status_code,403,r.text)
        self.assertEqual(editor.get(self.url(1,'vegetation/'+keys['historical'])).status_code,404)
        fixture={'base':str(self.bases[0]),'password':self.password,'keys':keys,'artifacts':str(artifacts)}
        path=artifacts/'fixture.json';path.write_text(json.dumps(fixture));path.chmod(0o600)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        with patch.dict(os.environ,AUTH_ORIGIN=origin,AUTH_ALLOW_INSECURE_LOCALHOST='true'):
            server=uvicorn.Server(uvicorn.Config(create_app(),host='127.0.0.1',port=port,log_level='warning'))
            thread=Thread(target=server.run,daemon=True);thread.start()
            try:
                deadline=time.monotonic()+15
                while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.03)
                self.assertTrue(server.started,'HTTP readiness')
                with (artifacts/'browser.log').open('w') as log:
                    r=subprocess.run(['node','webapp/tests/historical-classifications-browser.cjs',origin,str(path)],text=True,stdout=log,stderr=subprocess.STDOUT,timeout=300)
                self.assertEqual(r.returncode,0,(artifacts/'browser.log').read_text())
            finally:
                server.should_exit=True;thread.join(15);self.assertFalse(thread.is_alive())
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[0]}) as c:
            self.assertEqual(c.execute('SELECT famille_vegetation,disposition,sous_type,valid,geometry FROM public.vegetation WHERE id=%s',(keys['historical'],)).fetchone(),(None,None,None,False,None))
            self.assertEqual(c.execute('SELECT ST_AsEWKB(geometry) FROM public.vegetation WHERE id=%s',(keys['tree'],)).fetchone(),before)
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[2]}) as c:
            self.assertEqual(c.execute('SELECT commentaire,version FROM public.vegetation WHERE id=%s',(keys['historical'],)).fetchone(),('Autre base intacte',1))
