"""C3 level B (~1 min): real HTTP repositories, C1 SQL and durable journal.

Only the official disposable runner DSN is accepted. UUID fixtures are distinct
per scenario; no application database is read. Negative writes compare SQL
values, exact EWKB and events, including concurrent writes through two routes.
"""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4
from psycopg import sql
from fastapi.testclient import TestClient
from digues_webapp.app import create_app
from digues_webapp.database import get_connection,get_write_connection
from digues_app.demo.seed import seed_demo_cursor,demo_ids
from tests.postgres_safety import connect_test_database

TYPES=('INDETERMINE','BERGE','CRETE','TALUS','TALUS_RISBERME','RISBERME','FRANC_BORD')
LINE={'type':'LineString','coordinates':[[2,48],[2.001,48.001]]}
POINT={'type':'Point','coordinates':[2,48]}
POLYGON={'type':'Polygon','coordinates':[[[2,48],[2.001,48],[2.001,48.001],[2,48]]]}


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'),'Explicit disposable runner required')
class CanonicalTronconAPITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env=patch.dict(os.environ,LEGACY_SINGLE_DATABASE_ENABLED='true');cls.env.start();cls.addClassCleanup(cls.env.stop)
        cls.app=create_app()
        def database():
            with connect_test_database('DIGUES_OCC_TEST_DSN',autocommit=True) as c:yield c
        cls.app.dependency_overrides[get_connection]=database
        cls.app.dependency_overrides[get_write_connection]=database

    def setUp(self):
        self.client=TestClient(self.app,raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.digue,self.ah=uuid4(),uuid4()
        with self.db() as c:
            c.execute("INSERT INTO public.digues(id,libelle,valid) VALUES (%s,'Digue C3',true)",(self.digue,))
            c.execute("INSERT INTO public.amenagements_hydrauliques(id,designation,valid) VALUES (%s,'AH C3',true)",(self.ah,))

    def db(self):return connect_test_database('DIGUES_OCC_TEST_DSN',autocommit=True)
    def create(self,**payload):
        r=self.client.post('/api/troncons',json=payload)
        self.assertEqual(r.status_code,201,r.text);return r.json()
    def update(self,identifier,version=1,**payload):
        return self.client.put('/api/troncons/'+str(identifier),json=payload,headers={'If-Match':f'"{version}"'})
    def state(self,identifier):
        with self.db() as c:
            fields=[r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='troncons' AND udt_name<>'geometry' ORDER BY ordinal_position")]
            return c.execute(sql.SQL('SELECT to_jsonb(attrs),ST_AsEWKB(t.geometry) FROM public.troncons t CROSS JOIN LATERAL (SELECT {}) attrs WHERE t.id=%s')
                .format(sql.SQL(',').join(sql.SQL('t.{}').format(sql.Identifier(f)) for f in fields)),(identifier,)).fetchone()
    def events(self,identifier):
        with self.db() as c:return c.execute("SELECT event_id,payload FROM technique.evenements_ressources WHERE payload->'ids' @> jsonb_build_array(%s::text) ORDER BY sequence_id",(str(identifier),)).fetchall()

    def test_seven_types_parent_matrix_shapes_and_specialised_references(self):
        for kind in TYPES:
            for parent in ({},{'digue_id':str(self.digue)},{'amenagement_hydraulique_id':str(self.ah)},
                           {'digue_id':str(self.digue),'amenagement_hydraulique_id':str(self.ah)}):
                with self.subTest(kind=kind,parent=parent):
                    source={'type_physique_id':kind,**parent,'designation':'','commentaire':None}
                    if kind in ('RISBERME','FRANC_BORD'):source['geometry']=POLYGON
                    if kind=='CRETE':source.update(num_couche=0,epaisseur=0,largeur_moyenne=2.5,fonction_id='RefFonction:1')
                    item=self.create(**source);key=item['id'];p=item['properties']
                    self.assertEqual(p['type_physique_id'],kind);self.assertTrue(p['type_physique_libelle'])
                    self.assertEqual(p['designation'],'');self.assertIsNone(p['libelle'])
                    self.assertIsNone(p['systeme_reperage_defaut_id']);self.assertFalse(p['geometry_reperage_disponible'])
                    self.assertEqual(self.client.get('/api/troncons/'+key).json()['id'],key)
                    before=self.state(key)
                    changed=self.update(key,commentaire='Texte commun')
                    self.assertEqual(changed.status_code,200,changed.text)
                    self.assertEqual(changed.json()['properties']['version'],2)
                    self.assertEqual(self.state(key)[1],before[1])
                    self.assertEqual(changed.json()['properties']['digue_id'],parent.get('digue_id'))
                    self.assertEqual(changed.json()['properties']['amenagement_hydraulique_id'],parent.get('amenagement_hydraulique_id'))
            shapes=[POINT,LINE,POLYGON,{'type':'MultiPoint','coordinates':[[2,48],[2.001,48.001]]},
                {'type':'MultiLineString','coordinates':[LINE['coordinates']]},
                {'type':'MultiPolygon','coordinates':[POLYGON['coordinates']]},
                {'type':'GeometryCollection','geometries':[POINT,LINE]}]
            for shape in shapes:
                with self.subTest(kind=kind,shape=shape['type']):
                    allowed=kind not in ('RISBERME','FRANC_BORD') or shape['type'] in ('LineString','Polygon')
                    r=self.client.post('/api/troncons',json={'type_physique_id':kind,'geometry':shape})
                    self.assertEqual(r.status_code,201 if allowed else 422,r.text)
            # Invalid=true B4 rules never turn missing geometry into an axis.
            r=self.client.post('/api/troncons',json={'type_physique_id':kind})
            self.assertEqual(r.status_code,422 if kind in ('RISBERME','FRANC_BORD') else 201,r.text)
            self.create(type_physique_id=kind,valid=False)

    def test_demo_bank_and_ah_troncon_keep_identity_classification_and_reads(self):
        # Full seed uses its explicit disposable media target in its own suite.
        # Here those same UUIDs/values are loaded via the reviewed scenario writer.
        # Use the actual normal seed with its owned filesystem media store.
        from digues_app.demo.target import DemoTarget
        from digues_app.media_ingestion import FilesystemMediaStorage
        from digues_app.target import PostgreSQLConfig
        from pathlib import Path
        import tempfile
        folder=tempfile.TemporaryDirectory(dir=Path('audits/troncons-c3'));self.addCleanup(folder.cleanup)
        with connect_test_database('DIGUES_OCC_TEST_DSN',autocommit=False) as c:
            seed_demo_cursor(c.cursor(),target=DemoTarget(PostgreSQLConfig(dsn=c.info.dsn),FilesystemMediaStorage(folder.name)))
        ids=demo_ids();ah=str(ids['public.amenagements_hydrauliques'][0]);bank,ah_troncon=ids['public.troncons'][1:]
        detail=self.client.get('/api/amenagements-hydrauliques/'+ah)
        self.assertEqual(detail.status_code,200,detail.text)
        composed={r['id'] for r in detail.json()['properties']['troncons']}
        self.assertIn(str(ah_troncon),composed);self.assertNotIn(str(bank),composed)
        for identifier,kind,parent in ((bank,'BERGE',None),(ah_troncon,'INDETERMINE',ah)):
            r=self.client.get('/api/troncons/'+str(identifier));self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['id'],str(identifier))
            self.assertEqual(r.json()['properties']['type_physique_id'],kind)
            self.assertEqual(r.json()['properties']['amenagement_hydraulique_id'],parent)
            update=self.update(identifier,r.json()['properties']['version'],commentaire='C3')
            self.assertEqual(update.status_code,200,update.text)

    def test_omitted_geometry_keeps_srid_zm_and_binary_null_and_empty_are_distinct(self):
        for wkt in ('SRID=4326;LINESTRING ZM(2 48 3 4,2.01 48.01 5 6)','SRID=0;POINT(2 48)','SRID=4326;CIRCULARSTRING(2 48,2.005 48.005,2.01 48)',None):
            key=uuid4()
            with self.db() as c:c.execute("INSERT INTO public.troncons(id,type_physique_id,geometry,valid,designation,libelle) VALUES (%s,'BERGE',ST_GeomFromEWKT(%s),false,'Initial',' Libellé ')",(key,wkt))
            before=self.state(key)
            r=self.update(key,commentaire='',designation=None)
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.state(key)[1],before[1])
            self.assertEqual(r.json()['properties']['libelle'],' Libellé ')
            self.assertEqual(r.json()['properties']['commentaire'],'');self.assertIsNone(r.json()['properties']['designation'])
            r=self.update(key,2,geometry=None);self.assertEqual(r.status_code,200,r.text)
            self.assertIsNone(self.state(key)[1])

    def test_constraints_and_unsupported_idempotence_do_not_partially_write(self):
        key=str(uuid4());op=str(uuid4());body={'id':key,'type_physique_id':'BERGE','designation':'Source'}
        refused=self.client.post('/api/troncons',json=body,headers={'Operation-Id':op})
        self.assertEqual(refused.status_code,422,refused.text)
        self.assertIsNone(self.state(key));self.assertEqual(self.events(key),[])
        first=self.client.post('/api/troncons',json=body)
        self.assertEqual(first.status_code,201,first.text)
        before=self.state(key);events=self.events(key)
        duplicate=self.client.post('/api/troncons',json=body)
        self.assertEqual(duplicate.status_code,409,duplicate.text)
        self.assertEqual(self.state(key),before);self.assertEqual(self.events(key),events)
        for body in ({'type_physique_id':'FRANC_BORD','designation':'Perte'}, {'digue_id':str(uuid4()),'designation':'Perte'},
                     {'berge_support_id':key}, {'source_id':'absent'}, {'valid':None}):
            r=self.update(key,**body);self.assertEqual(r.status_code,422,r.text)
            self.assertEqual(self.state(key),before);self.assertEqual(self.events(key),events)
        r=self.update(key,9,designation='Périmé');self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(r.json()['detail']['currentVersion'],1);self.assertEqual(self.events(key),events)
        headers={'If-Match':'"1"','Operation-Id':str(uuid4())}
        refused=self.client.put('/api/troncons/'+key,json={'commentaire':'Non enregistré'},headers=headers)
        self.assertEqual(refused.status_code,422,refused.text)
        self.assertEqual(self.state(key),before);self.assertEqual(self.events(key),events)

    def test_typed_supports_and_qualified_parent_cannot_be_silently_invalidated(self):
        bank=self.create(type_physique_id='BERGE')['id']
        classic=self.create(type_physique_id='INDETERMINE',digue_id=str(self.digue),libelle='Support')['id']
        child=self.create(type_physique_id='TALUS',troncon_support_id=classic)['id']
        for payload in ({'type_physique_id':'TALUS','berge_support_id':classic},
                        {'type_physique_id':'TALUS','troncon_support_id':bank},
                        {'type_physique_id':'CRETE','berge_support_id':bank}):
            r=self.client.post('/api/troncons',json=payload)
            self.assertEqual(r.status_code,422,r.text)
        before=self.state(classic);events=self.events(classic)
        r=self.update(classic,digue_id=None,commentaire='Ne pas enregistrer')
        self.assertEqual(r.status_code,422,r.text);self.assertEqual(self.state(classic),before)
        self.assertEqual(self.events(classic),events);self.assertEqual(self.state(child)[0]['troncon_support_id'],classic)

    def test_ah_composition_child_versions_atomic_events_and_two_route_concurrency(self):
        item=self.create(type_physique_id='BERGE',digue_id=str(self.digue));key=item['id']
        payload={'troncon_ids':[key],'troncon_versions':{key:1}}
        url='/api/amenagements-hydrauliques/'+str(self.ah)
        missing=self.client.put(url,json={'troncon_ids':[key]},headers={'If-Match':'"1"'})
        self.assertEqual(missing.status_code,428,missing.text)
        gate=Barrier(2)
        def root():gate.wait();return self.update(key,commentaire='Édition directe')
        def composition():gate.wait();return self.client.put(url,json=payload,headers={'If-Match':'"1"'})
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(root);b=pool.submit(composition);results=[a.result(),b.result()]
        self.assertEqual(sorted(r.status_code for r in results),[200,409],[r.text for r in results])
        self.assertEqual(self.state(key)[0]['version'],2);self.assertEqual(self.state(key)[0]['digue_id'],str(self.digue))
        self.assertEqual(len(self.events(key)),2)
        ah=self.client.get(url).json()['properties'];current=self.state(key)[0]
        # A failed secondary journal append must rollback both aggregates.
        before=self.state(key);root_events=self.events(key);ah_events=self.events(self.ah)
        original=__import__('digues_webapp.resource_mutations',fromlist=['append_event']).append_event
        def fail_related(cursor,event):
            if event['resource']=='troncons':raise RuntimeError('journal failure')
            original(cursor,event)
        change=[] if current['amenagement_hydraulique_id'] else [key]
        with patch('digues_webapp.resource_mutations.append_event',side_effect=fail_related):
            r=self.client.put(url,json={'troncon_ids':change,'troncon_versions':{key:2}},headers={'If-Match':f'"{ah["version"]}"'})
        self.assertEqual(r.status_code,500,r.text);self.assertEqual(self.state(key),before)
        self.assertEqual(self.events(key),root_events);self.assertEqual(self.events(self.ah),ah_events)
        self.assertEqual(self.client.get(url).json()['properties']['version'],ah['version'])

    def test_direct_reparent_touches_both_ah_and_preserves_digue(self):
        other_ah=uuid4()
        with self.db() as c:c.execute("INSERT INTO public.amenagements_hydrauliques(id,valid) VALUES (%s,true)",(other_ah,))
        root=self.create(type_physique_id='BERGE',digue_id=str(self.digue),amenagement_hydraulique_id=str(self.ah))
        first_ah=self.client.get('/api/amenagements-hydrauliques/'+str(self.ah)).json()['properties']
        changed=self.update(root['id'],amenagement_hydraulique_id=str(other_ah))
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['properties']['digue_id'],str(self.digue))
        related=changed.json()['mutation']['relatedMutations']
        self.assertEqual(len(related),1)
        self.assertEqual(related[0]['resource'],'amenagements-hydrauliques')
        self.assertEqual(related[0]['versions'],{str(self.ah):first_ah['version']+1,str(other_ah):2})
        for key in (self.ah,other_ah):
            detail=self.client.get('/api/amenagements-hydrauliques/'+str(key)).json()['properties']
            self.assertEqual(root['id'] in detail['troncon_ids'],key==other_ah)
            self.assertEqual(self.events(key)[-1][1]['eventId'],related[0]['eventId'])
        # Composition cannot steal an already attached root, even with its right version.
        before=self.state(root['id']);events=self.events(root['id'])
        r=self.client.put('/api/amenagements-hydrauliques/'+str(self.ah),
            json={'troncon_ids':[root['id']],'troncon_versions':{root['id']:2}},
            headers={'If-Match':f'"{first_ah["version"]+1}"'})
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.state(root['id']),before);self.assertEqual(self.events(root['id']),events)

    def test_observations_and_existing_reperage_references_for_every_subtype(self):
        foreign_root=self.create(type_physique_id='BERGE')['id'];foreign_system=uuid4()
        with self.db() as c:c.execute("INSERT INTO public.systemes_reperage(id,troncon_id,valid) VALUES (%s,%s,true)",(foreign_system,foreign_root))
        for kind in TYPES:
            with self.subTest(type=kind):
                root=self.create(type_physique_id=kind,valid=False)['id']
                obs,photo,system=uuid4(),uuid4(),uuid4()
                body={'id':str(obs),'parent':{'resource':'troncons','id':root},'designation':'Observation C3'}
                headers={'Operation-Id':str(uuid4())}
                created=self.client.post('/api/observations',json=body,headers=headers)
                self.assertEqual(created.status_code,201,created.text)
                self.assertEqual(self.client.post('/api/observations',json=body,headers=headers).json(),created.json())
                with self.db() as c:
                    c.execute('INSERT INTO public.photos(id,observation_id,valid) VALUES (%s,%s,true)',(photo,obs))
                    c.execute('INSERT INTO public.systemes_reperage(id,troncon_id,valid) VALUES (%s,%s,true)',(system,root))
                updated=self.client.put('/api/observations/'+str(obs),json={'suite':'Suivi'},headers={'If-Match':'"1"','Operation-Id':str(uuid4())})
                self.assertEqual(updated.status_code,200,updated.text)
                self.assertEqual(updated.json()['parent']['type_physique_id'],kind)
                self.assertEqual(updated.json()['parent']['relation_role'],'troncon_id')
                self.assertEqual([p['id'] for p in updated.json()['photos']],[str(photo)])
                listing=self.client.get('/api/troncons/'+root+'/observations').json()
                self.assertIn(str(obs),[o['id'] for o in listing['observations']])
                before=self.state(root);events=self.events(root)
                wrong=self.update(root,systeme_reperage_defaut_id=str(foreign_system))
                self.assertEqual(wrong.status_code,422,wrong.text)
                self.assertEqual(self.state(root),before);self.assertEqual(self.events(root),events)
                chosen=self.update(root,systeme_reperage_defaut_id=str(system))
                self.assertEqual(chosen.status_code,200,chosen.text)
                self.assertEqual(chosen.json()['properties']['systeme_reperage_defaut_id'],str(system))
                self.assertEqual(chosen.json()['properties']['nombre_systemes_reperage'],1)
                self.assertFalse(chosen.json()['properties']['geometry_reperage_disponible'])

    def test_every_c1_attribute_is_exposed_and_specialised_values_roundtrip(self):
        from digues_webapp.models import TronconFields
        from digues_webapp.troncons import CATALOGS
        with self.db() as c:
            physical={row[0] for row in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='troncons' AND is_generated='NEVER'")}
            readonly={'id','version','created_by','edited_by','geometry_reperage'}
            self.assertEqual(set(TronconFields.model_fields),physical-readonly)
            catalogs={field:c.execute('SELECT id FROM referentiel.'+table+' WHERE valid ORDER BY id LIMIT 1').fetchone()[0]
                      for field,table in CATALOGS.items() if field!='type_physique_id'}
        numbers=dict(num_couche=2,epaisseur=.25,largeur_moyenne=3.5,epaisseur_sommet=.75,
            pente_interieure=.4,longueur_rampant_haut=4.5,longueur_rampant_bas=5.5,hauteur_murette=.8,largeur=6.5)
        forbidden={'cote_id','position_id','fonction_haut_id','fonction_bas_id','nature_haut_id','nature_bas_id',
            'materiau_haut_id','materiau_bas_id','largeur_franc_bord_id','epaisseur_sommet','pente_interieure',
            'longueur_rampant_haut','longueur_rampant_bas','hauteur_murette','largeur'}
        for kind in TYPES:
            with self.subTest(type=kind):
                payload={**catalogs,**numbers,'designation':'Attributs C1','commentaire':'Historique',
                    'date_debut':'2020-01-02','date_fin':'2025-01-03','valid':False,'type_physique_id':kind}
                if kind=='CRETE':payload={k:v for k,v in payload.items() if k not in forbidden}
                root=self.create(**payload)
                for field,value in payload.items():self.assertEqual(root['properties'][field],value,field)
                changed=self.update(root['id'],**{k:v for k,v in payload.items() if k!='type_physique_id'})
                self.assertEqual(changed.status_code,200,changed.text)
                for field,value in payload.items():self.assertEqual(changed.json()['properties'][field],value,field)
