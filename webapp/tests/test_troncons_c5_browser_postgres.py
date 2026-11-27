"""C5 level B (~8–10 min): actual browser, sessions and fresh PostgreSQL databases.

Unique fixtures, no mocked business/authorization transport. Official disposable
runner only. The browser exercises the shared form, C4 graph and inter-page bus;
SQL then checks exact EWKB and invisible values. No deployment database is used.
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
from uuid import UUID,uuid4
import psycopg
import uvicorn
from digues_app.target import PostgreSQLConfig
from digues_app.demo.seed import seed_demo_cursor, demo_ids
from digues_app.demo.target import DemoTarget
from digues_app.media_ingestion import FilesystemMediaStorage
from digues_webapp.app import create_app
from webapp.tests import test_level_a_multitenancy_postgres as harness


class CanonicalTronconBrowserTest(unittest.TestCase):
    artifact_root = 'audits/troncons-c5/browser'
    browser_environment = {}
    # The complete Base read -> editor -> read path adds contextual reads.
    # Measured at >300 s on the shared host; keep every scenario and SQL proof.
    browser_timeout = 480
    cleanup_databases=classmethod(harness.LevelAMultitenancyPostgresTest.cleanup_databases.__func__)
    setUpClass=classmethod(harness.LevelAMultitenancyPostgresTest.setUpClass.__func__)
    setUp=harness.LevelAMultitenancyPostgresTest.setUp
    client=harness.LevelAMultitenancyPostgresTest.client
    context=harness.LevelAMultitenancyPostgresTest.context
    url=harness.LevelAMultitenancyPostgresTest.url

    def extend_fixture(self, fixture):
        """Optional test-only extension; no application/session shortcut."""

    def test_browser_common_editor_heritage_reperage_conflict_and_cross_page(self):
        artifacts=Path(self.artifact_root)/uuid4().hex;artifacts.mkdir(parents=True)
        types=('INDETERMINE','BERGE','CRETE','TALUS','TALUS_RISBERME','RISBERME','FRANC_BORD')
        keys={kind:str(uuid4()) for kind in types}
        polygon=str(uuid4());shared=str(uuid4());point=str(uuid4());missing=str(uuid4());curve=str(uuid4())
        borne,sysa,sysb,observation,photo=(uuid4() for _ in range(5))
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[0]}) as c:
            seed_demo_cursor(c.cursor(),target=DemoTarget(PostgreSQLConfig(dsn=c.info.dsn),FilesystemMediaStorage(artifacts/'media')))
            ah=str(demo_ids()['public.amenagements_hydrauliques'][0]);digue=str(c.execute('SELECT id FROM public.digues ORDER BY id LIMIT 1').fetchone()[0])
            for kind,key in keys.items():
                c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,designation,commentaire,valid,geometry) VALUES (%s,%s,%s,'Valeur initiale','Ancien commentaire',true,ST_Transform(ST_GeomFromText('LINESTRING ZM(2 48 3 7,2.001 48.001 4 8)',4326),3950))",(key,kind,'C5 '+kind))
            c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,valid,geometry) VALUES (%s,'INDETERMINE','C5 courbe historique',true,ST_GeomFromText('CIRCULARSTRING(1700000 8200000,1700010 8200010,1700020 8200000)',3950))",(curve,))
            # Populated atypical values (including zero and a reference) remain
            # readable and survive an attribute-only edit byte for byte.
            function_id,function_label=c.execute('SELECT id,libelle FROM referentiel.fonctions_structure WHERE valid ORDER BY id LIMIT 1').fetchone()
            c.execute('UPDATE public.troncons SET hauteur_murette=12.75,num_couche=0,fonction_bas_id=%s WHERE id=%s',(function_id,keys['BERGE']))
            support=str(demo_ids()['troncons'][0])
            support_label=c.execute('SELECT libelle FROM public.troncons WHERE id=%s',(support,)).fetchone()[0]
            c.execute('UPDATE public.troncons SET fonction_haut_id=%s,troncon_support_id=%s WHERE id=%s',(function_id,support,keys['TALUS']))
            c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,valid,geometry,digue_id,amenagement_hydraulique_id) VALUES (%s,'BERGE','C5 double parent',true,ST_Transform(ST_GeomFromText('LINESTRING(2 48,2.001 48.001)',4326),3950),%s,%s)",(shared,digue,ah))
            c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,valid,geometry) VALUES (%s,'BERGE','C5 surface',true,ST_Transform(ST_GeomFromText('POLYGON((2 48,2.001 48,2.001 48.001,2 48))',4326),3950))",(polygon,))
            c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,valid,geometry) VALUES (%s,'BERGE','C5 ponctuel',true,ST_Transform(ST_GeomFromText('POINT(2 48)',4326),3950)),(%s,'BERGE','C5 sans géométrie',true,NULL)",(point,missing))
            c.execute("INSERT INTO public.bornes_reperage(id,libelle,geometry,valid) VALUES (%s,'Borne partagée C5',ST_Transform(ST_GeomFromText('POINT(2 48)',4326),3950),true)",(borne,))
            for owner,system in ((shared,sysa),(keys['TALUS'],sysb)):
                c.execute("INSERT INTO public.systemes_reperage(id,troncon_id,libelle,valid) VALUES (%s,%s,'Système partagé C5',true)",(system,owner))
                c.execute('INSERT INTO public.systemes_reperage__bornes(id,systeme_reperage_id,borne_id,valeur_pr,valid) VALUES (%s,%s,%s,0,true)',(uuid4(),system,borne))
            c.execute('UPDATE public.troncons SET systeme_reperage_defaut_id=%s WHERE id=%s',(sysa,shared))
            c.execute('INSERT INTO public.observations(id,troncon_id,valid) VALUES (%s,%s,true)',(observation,shared))
            c.execute("INSERT INTO public.photos(id,observation_id,systeme_reperage_id,borne_debut_id,geometry,valid) VALUES (%s,%s,%s,%s,ST_Transform(ST_GeomFromText('POINT(2 48)',4326),3950),true)",(photo,observation,sysa,borne))
            historical=c.execute('SELECT version,ST_AsEWKB(geometry) FROM public.photos WHERE id=%s',(photo,)).fetchone()
            banks=[str(x) for x in demo_ids()['public.troncons'][1:]]
            before={str(key):bytes(ewkb) for key,ewkb in c.execute('SELECT id,ST_AsEWKB(geometry) FROM public.troncons WHERE id=ANY(%s::uuid[])',(list(keys.values())+[shared,polygon,curve]+banks,))}
        # Backend prerequisites and indispensable selector/proof enrichments.
        client=self.client('editor_alpha')
        opts=client.get(self.url(0,'troncons/options'));self.assertEqual(opts.status_code,200,opts.text)
        self.assertIn('fonction_haut_id',opts.json()['references']);self.assertTrue(opts.json()['digues'])
        hierarchy=client.get(self.url(0,'systemes-endiguement'));self.assertEqual(hierarchy.status_code,200,hierarchy.text)
        self.assertTrue(set(keys.values())<={row['id'] for row in hierarchy.json()['troncons_sans_parent']})
        fixture={'base':str(self.bases[0]),'password':self.password,'keys':keys,'ah':ah,'digue':digue,'shared':shared,'polygon':polygon,'point':point,'missing':missing,'curve':curve,'borne':str(borne),'photo':str(photo),'demo_bank':banks[0],'demo_ah_troncon':banks[1],'function_id':function_id,'function_label':function_label,'support':support,'support_label':support_label,'artifacts':str(artifacts)}
        self.extend_fixture(fixture)
        fixture_path=artifacts/'fixture.json';fixture_path.write_text(json.dumps(fixture));fixture_path.chmod(0o600)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        with patch.dict(os.environ,AUTH_ORIGIN=origin,AUTH_ALLOW_INSECURE_LOCALHOST='true'):
            server=uvicorn.Server(uvicorn.Config(create_app(),host='127.0.0.1',port=port,log_level='warning'))
            thread=Thread(target=server.run,daemon=True);thread.start()
            try:
                deadline=time.monotonic()+15
                while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.03)
                self.assertTrue(server.started,'HTTP test server readiness')
                with (artifacts/'browser.log').open('w') as log:
                    result=subprocess.run(['node','webapp/tests/troncons-c5-browser.cjs',origin,str(fixture_path)],env={**os.environ,**self.browser_environment},text=True,stdout=log,stderr=subprocess.STDOUT,timeout=getattr(self,'browser_timeout',300))
                self.assertEqual(result.returncode,0,(artifacts/'browser.log').read_text())
            finally:
                server.should_exit=True;thread.join(15);self.assertFalse(thread.is_alive())
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[0]}) as c:
            after={str(key):bytes(ewkb) for key,ewkb in c.execute('SELECT id,ST_AsEWKB(geometry) FROM public.troncons WHERE id=ANY(%s::uuid[])',(list(before),))}
            self.assertEqual(c.execute('SELECT version,ST_AsEWKB(geometry) FROM public.photos WHERE id=%s',(photo,)).fetchone(),historical)
            self.assertEqual(c.execute('SELECT systeme_reperage_defaut_id FROM public.troncons WHERE id=%s',(shared,)).fetchone()[0],sysa,'The default is outside the attribute form and must remain unchanged')
            self.assertEqual(after,before,'Attribute and referencing edits must preserve every physical EWKB, SRID and Z/M')
            self.assertEqual(c.execute('SELECT hauteur_murette FROM public.troncons WHERE id=%s',(keys['BERGE'],)).fetchone()[0],12.75)
            self.assertEqual(c.execute('SELECT num_couche,fonction_bas_id FROM public.troncons WHERE id=%s',(keys['BERGE'],)).fetchone(),(0,function_id))
            self.assertEqual(c.execute('SELECT digue_id,amenagement_hydraulique_id FROM public.troncons WHERE id=%s',(shared,)).fetchone(),(UUID(digue),UUID(ah)))
