"""Photo localisation against the real engine, OCC boundary and historical rows."""
import os
import unittest
import psycopg
from uuid import uuid4
from pydantic import ValidationError
from digues_webapp.photo_localisation import CONTEXT_SQL, PhotoLocalisationUpdate
from webapp.tests import test_observation_detail as parents
from webapp.tests.test_resource_versions import http_client


class PhotoLocalisationModelTest(unittest.TestCase):
    def test_photo_context_resolves_ouvrage_particulier_parent(self):
        self.assertIn(
            "SELECT troncon_id FROM ouvrages_particuliers WHERE id=o.ouvrage_particulier_id",
            CONTEXT_SQL,
        )

    def test_commands_reject_incoherent_or_nonfinite_values(self):
        invalid = [dict(mode='point',srid=4326,coordinates=[181,2]),
            dict(mode='point',srid=3950,coordinates=[float('nan'),2]),
            dict(mode='point',srid=3950,coordinates=[1,float('inf')]),
            dict(mode='point',srid=3950,coordinates=[1,2,3]),
            dict(mode='point',geometry={'type':'LineString','coordinates':[]}),
            dict(mode='effacer',pr=1), dict(mode='bornage',distance_m=-1),
            dict(mode='pr',pr=12), dict(mode='effacer',observation_id=str(uuid4()))]
        for command in invalid:
            with self.subTest(command=command), self.assertRaises(ValidationError):
                PhotoLocalisationUpdate(**command)


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'PostGIS local explicitement requis')
class PhotoLocalisationPostgresTest(unittest.TestCase):
    def test_ouvrage_particulier_photo_uses_explicit_troncon_context(self):
        self.connection.execute(
            "UPDATE ouvrages_particuliers SET troncon_id=%s WHERE id=%s",
            (self.troncon, self.parents['ouvrages_particuliers']),
        )
        photo_id = uuid4()
        self.connection.execute(
            "INSERT INTO photos(id,observation_id,storage_key,valid) "
            "VALUES (%s,%s,'ouvrage.jpg',true)",
            (photo_id, self.observations['ouvrages_particuliers']),
        )
        with http_client(self.app) as client:
            response = client.get(f'/api/photos/{photo_id}/localisation-options')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item['id'] for item in response.json()['context_troncons']],
            [str(self.troncon)],
        )

    @classmethod
    def setUpClass(cls):
        parents.ObservationDetailPostgresTest.setUpClass()
        cls.dsn = os.environ['DIGUES_OCC_TEST_DSN']

    def setUp(self):
        parents.ObservationDetailPostgresTest.setUp(self)
        self.troncon = self.parents['troncons']; self.system = uuid4(); self.bornes = [uuid4(),uuid4()]
        self.connection.execute("UPDATE troncons SET geometry=ST_GeomFromText('LINESTRING(1700000 8200000,1701000 8200000)',3950) WHERE id=%s",(self.troncon,))
        self.connection.execute("INSERT INTO systemes_reperage(id,troncon_id,libelle,valid) VALUES (%s,%s,'Calibration',true)",(self.system,self.troncon))
        for i, borne in enumerate(self.bornes):
            self.connection.execute("INSERT INTO bornes_reperage(id,libelle,geometry,valid) VALUES (%s,%s,ST_SetSRID(ST_Point(%s,8200000),3950),true)",(borne,f'B{i}',1700000+i*1000))
            self.connection.execute('INSERT INTO public.troncons_bornes(troncon_id,borne_id) VALUES (%s,%s)',(self.troncon,borne))
            self.connection.execute('INSERT INTO public.systemes_reperage__bornes(id,systeme_reperage_id,borne_id,valeur_pr,valid) VALUES (%s,%s,%s,%s,true)',(uuid4(),self.system,borne,100+i*200))
        self.url = f'/api/photos/{self.photo}'
        self.context = dict(troncon_id=str(self.troncon),systeme_reperage_id=str(self.system))

    def put(self, client, payload, version=1, operation=None):
        return client.put(self.url+'/localisation',json=payload,headers={'If-Match':f'"{version}"','Operation-Id':operation or str(uuid4())})

    def imported_photo_history(self):
        """Real native import + authenticated C2 report in a NEW disposable DB.

        The old source ID is never restored as a business column. The original
        warning, confirmation and byte-preservation assertions remain below.
        """
        from pathlib import Path
        import tempfile
        from uuid import UUID
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict,make_conninfo
        from unittest.mock import patch
        from tests.postgres_safety import connect_test_database
        from tests.test_migration_core import source_fixture,IDS
        from scripts import prototype_native_migration as native
        from digues_app.migration.core import prepare_core_migration
        from digues_app.migration.source_inventory import digest
        from digues_app.target.import_proof import ReceiptContext
        from digues_webapp.database import get_connection,get_write_connection
        admin=self.enterContext(connect_test_database('DIGUES_MULTIBASE_TEST_ADMIN_DSN',kind='admin',autocommit=True))
        name='digues_occ_test_photo_c4_'+uuid4().hex[:8]
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        self.addCleanup(lambda:admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name))))
        dsn=make_conninfo(**{**conninfo_to_dict(self.dsn),'dbname':name})
        root=self.enterContext(tempfile.TemporaryDirectory(dir=Path('audits/troncons-c4')))
        self.enterContext(patch.dict(os.environ,DIGUES_DIRECT_TEST_DSN=dsn,DIGUES_IMPORT_REPORT_ROOT=root))
        self.connection=connect_test_database('DIGUES_DIRECT_TEST_DSN',autocommit=True);self.addCleanup(self.connection.close)
        self.connection.execute('CREATE EXTENSION postgis');self.connection.execute('CREATE EXTENSION pgcrypto')
        identity,epoch,job=(uuid4() for _ in range(3))
        contract=native.initialize(self.connection,identity,epoch)
        documents=source_fixture();photo=documents['Desordre'][0]['observations'][0]['photos'][0]
        photo['geometry']=None;photo['systemeRepId']='orphan'
        context=ReceiptContext(identity,epoch,job,digest(documents))
        prepared=prepare_core_migration(documents,historical_proofs=False)
        self.history_report=Path(root)/str(job)/'bilan-exemple.json'
        native.migrate(self.connection,prepared,documents,Path(root)/str(job),context=context,contract=contract)
        self.photo=UUID(IDS['photo_1']);self.url=f'/api/photos/{self.photo}'
        def database():
            with connect_test_database('DIGUES_DIRECT_TEST_DSN',autocommit=True) as c:yield c
        self.app.dependency_overrides[get_connection]=database
        self.app.dependency_overrides[get_write_connection]=database

    def test_empty_and_historical_read_never_mutates(self):
        self.imported_photo_history()
        with http_client(self.app) as client:
            photo=client.get(self.url).json(); self.assertIsNone(photo['localisation']['geometry']); self.assertEqual(photo['version'],1)
            self.connection.execute("UPDATE photos SET geometry=ST_GeomFromText('LINESTRING(1700000 8200000,1700100 8200000)',3950),position_debut=ST_SetSRID(ST_Point(1700000,8200000),3950),position_fin=ST_SetSRID(ST_Point(1700100,8200000),3950),borne_debut_aval=true,borne_debut_distance=10 WHERE id=%s",(self.photo,))
            before=self.connection.execute('SELECT ST_AsEWKB(geometry),version FROM photos WHERE id=%s',(self.photo,)).fetchone()
            loc=client.get(self.url).json()['localisation']; self.assertTrue(loc['remplacement_requis'])
            self.assertIn('SYSTEME_HISTORIQUE_NON_RESOLU',loc['warnings']); self.assertEqual(loc['position_debut_relative'],'AVANT_BORNE')
            self.assertEqual(loc['geometry']['type'],'LineString')
            self.assertEqual(self.put(client,dict(mode='effacer'),version=2).status_code,422)
            self.assertEqual(self.connection.execute('SELECT ST_AsEWKB(geometry),version FROM photos WHERE id=%s',(self.photo,)).fetchone(),before)
            response=self.put(client,dict(mode='effacer',remplacer_historique=True),version=2)
            self.assertEqual(response.status_code,200,response.text); self.assertIsNone(response.json()['localisation']['position_fin'])
            self.connection.execute("UPDATE photos SET geometry=ST_GeomFromText('POINT EMPTY',3950) WHERE id=%s",(self.photo,))
            empty=client.get(self.url).json()['localisation']
            self.assertIsNone(empty['geometry_4326']); self.assertTrue(empty['remplacement_requis'])
            self.assertIn('GEOMETRIE_HISTORIQUE_INVALIDE',empty['warnings'])
            # Real artifact authentication, not a mocked historical warning.
            payload=self.history_report.read_bytes()
            before=self.connection.execute('SELECT version,ST_AsEWKB(geometry) FROM photos WHERE id=%s',(self.photo,)).fetchone()
            self.history_report.write_bytes(payload+b' ')
            self.assertEqual(client.get(self.url).status_code,503)
            self.assertEqual(self.connection.execute('SELECT version,ST_AsEWKB(geometry) FROM photos WHERE id=%s',(self.photo,)).fetchone(),before)
            self.history_report.write_bytes(payload)
            self.assertEqual(client.get(self.url).status_code,200)

    def test_map_point_coordinate_update_clear_occ_journal_retry(self):
        operation=str(uuid4()); command=dict(mode='point',srid=4326,coordinates=[2,46])
        with http_client(self.app) as client:
            missing=client.put(self.url+'/localisation',json=command,headers={'Operation-Id':str(uuid4())})
            self.assertEqual(missing.status_code,428)
            response=self.put(client,command,operation=operation); self.assertEqual(response.status_code,200,response.text)
            photo=response.json(); self.assertEqual(photo['version'],2); self.assertEqual(photo['mutation']['resource'],'photos'); self.assertEqual(photo['mutation']['action'],'update')
            self.assertAlmostEqual(photo['localisation']['geometry_4326']['coordinates'][0],2,places=6)
            self.assertEqual(self.put(client,command,operation=operation).json(),photo)
            self.assertEqual(self.put(client,dict(mode='effacer'),operation=operation).status_code,409)
            self.assertEqual(self.put(client,command).status_code,409)
            self.assertEqual(self.connection.execute("SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s",(str(self.photo),)).fetchone()[0],1)
            second=self.put(client,dict(mode='point',srid=3950,coordinates=[1700250,8200040]),version=2)
            self.assertEqual(second.status_code,200,second.text)
            self.assertEqual(second.json()['localisation']['geometry']['coordinates'],[1700250,8200040])
            cleared=self.put(client,dict(mode='effacer'),version=3).json()
            self.assertEqual(cleared['version'],4); self.assertIsNone(cleared['localisation']['geometry'])
            self.assertEqual(cleared['observation_id'],str(self.observations['desordres']))

    def test_bornage_sign_pr_and_off_axis_photographer(self):
        with http_client(self.app) as client:
            command=dict(mode='bornage',**self.context,borne_id=str(self.bornes[1]),distance_m=100,position_relative='AVANT_BORNE')
            response=self.put(client,command); self.assertEqual(response.status_code,200,response.text)
            loc=response.json()['localisation']; self.assertEqual(loc['geometry']['coordinates'],[1700900,8200000])
            self.assertTrue(loc['borne_debut_aval']); self.assertEqual(loc['pr_debut'],280)
            response=self.put(client,dict(mode='pr',**self.context,pr=200),version=2)
            self.assertEqual(response.status_code,200,response.text); self.assertEqual(response.json()['localisation']['geometry']['coordinates'],[1700500,8200000])
            response=self.put(client,dict(mode='point',srid=3950,coordinates=[1700200,8200090],**self.context),version=3)
            self.assertEqual(response.status_code,200,response.text); loc=response.json()['localisation']
            self.assertEqual(loc['geometry']['coordinates'],[1700200,8200090]); self.assertEqual(loc['pr_debut'],140)
            self.assertEqual(loc['position_debut'],loc['geometry']); self.assertIsNone(loc['position_fin'])

    def test_optional_offline_reference_revision_is_atomic_and_idempotent(self):
        from digues_webapp.reperage_references import system_graph
        command=dict(mode='pr',**self.context,pr=200)
        with psycopg.connect(self.dsn) as reader:
            revision=system_graph(reader,self.troncon,self.system)['revision']
        operation=str(uuid4())
        headers={'If-Match':'"1"','Operation-Id':operation,'Reference-Revision':revision}
        with http_client(self.app) as client:
            first=client.put(self.url+'/localisation',json=command,headers=headers)
            self.assertEqual(first.status_code,200,first.text)
            receipt=first.json()
            self.assertEqual(receipt['version'],2)
            self.connection.execute('UPDATE public.systemes_reperage__bornes SET valeur_pr=valeur_pr+10 WHERE systeme_reperage_id=%s',(self.system,))
            retry=client.put(self.url+'/localisation',json=command,headers=headers)
            self.assertEqual(retry.status_code,200,retry.text)
            self.assertEqual(retry.json(),receipt)
            second=client.put(self.url+'/localisation',json=command,
                headers={'If-Match':'"2"','Operation-Id':str(uuid4()),'Reference-Revision':revision})
            self.assertEqual(second.status_code,409,second.text)
            self.assertEqual(second.json()['detail']['code'],'photo-references-changed')
            self.assertEqual(self.connection.execute('SELECT version FROM photos WHERE id=%s',(self.photo,)).fetchone()[0],2)
            self.assertEqual(self.connection.execute("SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s",(str(self.photo),)).fetchone()[0],1)
            old_client=client.put(self.url+'/localisation',json={'mode':'effacer'},
                headers={'If-Match':'"2"','Operation-Id':str(uuid4())})
            self.assertEqual(old_client.status_code,200,old_client.text)

    def test_server_rejects_bad_references_domains_geometry_and_srid(self):
        commands=[dict(mode='bornage',**self.context,borne_id=str(uuid4()),distance_m=1,position_relative='APRES_BORNE'),
            dict(mode='bornage',**self.context,borne_id=str(self.bornes[0]),distance_m=1,position_relative='AVANT_BORNE'),
            dict(mode='pr',**self.context,pr=400), dict(mode='pr',**(self.context|{'systeme_reperage_id':str(uuid4())}),pr=200),
            dict(mode='point',srid=3857,coordinates=[1,2]),dict(mode='point',srid=3950,coordinates=['NaN',2]),
            dict(mode='point',srid=3950,coordinates=[1e200,2]),
            dict(mode='point',srid=3950,coordinates=[1,2],geometry={'type':'Polygon','coordinates':[]})]
        with http_client(self.app) as client:
            for command in commands:
                with self.subTest(command=command):
                    response=self.put(client,command); self.assertEqual(response.status_code,422,response.text)
            self.connection.execute('UPDATE bornes_reperage SET valid=false WHERE id=%s',(self.bornes[0],))
            self.assertEqual(self.put(client,dict(mode='pr',**self.context,pr=150)).status_code,422)
            self.assertEqual(client.get(f'/api/photos/{uuid4()}').status_code,404)
            self.assertEqual(self.connection.execute('SELECT version FROM photos WHERE id=%s',(self.photo,)).fetchone()[0],1)

    def test_context_ambiguity_requires_choice_and_all_nine_families_resolve(self):
        with http_client(self.app) as client:
            result=client.get(self.url+'/localisation-options',params={'troncon_id':str(self.troncon)})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['context_troncons'][0]['id'],str(self.troncon))
            self.assertEqual(result.json()['systemes'][0]['bornes'][0]['id'],str(self.bornes[0]))
            other=uuid4()
            self.connection.execute("INSERT INTO troncons(id,type_physique_id,digue_id,libelle,geometry,valid) SELECT %s,'INDETERMINE',digue_id,'Autre',geometry,true FROM troncons WHERE id=%s",(other,self.troncon))
            self.connection.execute('INSERT INTO public.desordres__troncons(desordre_id,troncon_id) VALUES (%s,%s)',(self.parents['desordres'],other))
            self.assertEqual(len(client.get(self.url+'/localisation-options').json()['context_troncons']),2)
            self.assertEqual(self.put(client,dict(mode='pr',pr=200)).status_code,422)
            self.assertEqual(self.put(client,dict(mode='pr',**self.context,pr=200)).status_code,200)
            for obs in self.observations.values():
                p=uuid4(); self.connection.execute("INSERT INTO photos(id,observation_id,storage_key,valid) VALUES (%s,%s,%s,true)",(p,obs,f'fixtures/{p}.jpg'))
                response=client.get(f'/api/photos/{p}/localisation-options'); self.assertEqual(response.status_code,200,response.text)

    def test_base_uses_v2_labels_without_reinterpreting_history(self):
        from digues_webapp.data_views import VIEW_FIELDS_BY_KEY
        for end in ('debut','fin'):
            expression=VIEW_FIELDS_BY_KEY[f'photos.sens_{end}'].expression
            for flag,distance,expected in [(True,12,'Amont'),(False,12,'Aval'),(True,0,'Sur la borne'),(False,0,'Sur la borne'),(None,0,'Sur la borne'),(None,12,None),(None,None,None)]:
                with self.subTest(end=end,flag=flag,distance=distance):
                    self.connection.execute(f'UPDATE photos SET borne_{end}_aval=%s,borne_{end}_distance=%s WHERE id=%s',(flag,distance,self.photo))
                    label=self.connection.execute(f'SELECT {expression} FROM photos p WHERE p.id=%s',(self.photo,)).fetchone()[0]
                    self.assertEqual(label,expected)

    def test_refs_outside_parent_context_require_explicit_compatible_system(self):
        other=uuid4(); foreign_borne=uuid4()
        self.connection.execute("INSERT INTO troncons(id,type_physique_id,digue_id,libelle,geometry,valid) SELECT %s,'INDETERMINE',digue_id,'Autre tronçon',geometry,true FROM troncons WHERE id=%s",(other,self.troncon))
        self.connection.execute("INSERT INTO bornes_reperage(id,libelle,geometry,valid) VALUES (%s,'Hors système',ST_SetSRID(ST_Point(1700500,8200000),3950),true)",(foreign_borne,))
        with http_client(self.app) as client:
            bad=self.put(client,dict(mode='bornage',**self.context,borne_id=str(foreign_borne),position_relative='SUR_BORNE',distance_m=0))
            self.assertEqual(bad.status_code,422,bad.text)
            bad=self.put(client,dict(mode='pr',**(self.context|{'troncon_id':str(other)}),pr=200))
            self.assertEqual(bad.status_code,422,bad.text)
            # A new system/borne relation on an explicitly chosen different tronçon
            # is legitimate. No need to move the Observation or its parent.
            new_system=uuid4()
            self.connection.execute("INSERT INTO systemes_reperage(id,troncon_id,libelle,valid) VALUES (%s,%s,'Autre système',true)",(new_system,other))
            for i,borne in enumerate(self.bornes):
                self.connection.execute('INSERT INTO public.troncons_bornes(troncon_id,borne_id) VALUES (%s,%s)',(other,borne))
                self.connection.execute('INSERT INTO public.systemes_reperage__bornes(id,systeme_reperage_id,borne_id,valeur_pr,valid) VALUES (%s,%s,%s,%s,true)',(uuid4(),new_system,borne,100+i*200))
            good=self.put(client,dict(mode='pr',troncon_id=str(other),systeme_reperage_id=str(new_system),pr=200))
            self.assertEqual(good.status_code,200,good.text)
            self.assertEqual(good.json()['localisation']['troncon_id'],str(other))
            self.assertEqual(good.json()['observation_id'],str(self.observations['desordres']))
            command=dict(mode='effacer')
            absent=client.put(f'/api/photos/{uuid4()}/localisation',json=command,headers={'If-Match':'"1"','Operation-Id':str(uuid4())})
            self.assertEqual(absent.status_code,404)

    def test_read_keeps_geometry_and_version_from_same_snapshot(self):
        from unittest.mock import patch
        from digues_webapp.photo_localisation import read_localisation
        self.connection.execute("UPDATE photos SET geometry=ST_SetSRID(ST_Point(1700000,8200000),3950) WHERE id=%s",(self.photo,))
        def concurrent_change(connection,identifier,raw=None):
            self.connection.execute("UPDATE photos SET geometry=ST_SetSRID(ST_Point(1700100,8200000),3950) WHERE id=%s",(self.photo,))
            return read_localisation(connection,identifier,raw)
        with http_client(self.app) as client, patch('digues_webapp.photos.read_localisation',side_effect=concurrent_change):
            response=client.get(self.url)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['version'],2)
            self.assertEqual(response.json()['localisation']['geometry']['coordinates'],[1700000,8200000])
        self.assertEqual(self.connection.execute('SELECT version FROM photos WHERE id=%s',(self.photo,)).fetchone()[0],3)
