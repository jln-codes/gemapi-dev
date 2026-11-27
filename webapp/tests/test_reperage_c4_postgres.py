"""C4 level B: native HTTP, actual SQL engine, two owned fresh CRS databases.

~1 minute. Every scenario owns UUID fixtures; reference edits compare EWKB,
versions and events. The official runner is the only connection source.
"""
import json
import os
import unittest
from contextlib import nullcontext
from uuid import uuid4
from unittest.mock import patch
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo
from digues_app.target import PostgreSQLConfig,initialize_schema
from digues_app.migration.references import initialize_references
from digues_webapp.app import create_app
from digues_webapp.database import get_connection,get_write_connection
from tests.postgres_safety import connect_test_database,require_test_dsn

TYPES=('INDETERMINE','BERGE','CRETE','TALUS','TALUS_RISBERME','RISBERME','FRANC_BORD')


class TransversalReperagePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsns={3950:require_test_dsn('DIGUES_OCC_TEST_DSN')}
        cls.admin=connect_test_database('DIGUES_MULTIBASE_TEST_ADMIN_DSN',kind='admin',autocommit=True)
        cls.addClassCleanup(cls.admin.close)
        cls.name='digues_occ_test_c4_'+uuid4().hex[:10]
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        def cleanup():cls.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.name)))
        cls.addClassCleanup(cleanup)
        cls.dsns[2154]=make_conninfo(**{**conninfo_to_dict(cls.dsns[3950]),'dbname':cls.name})
        cls.environment=patch.dict(os.environ,DIGUES_C4_TEST_DSN=cls.dsns[2154]);cls.environment.start();cls.addClassCleanup(cls.environment.stop)
        with connect_test_database('DIGUES_C4_TEST_DSN',autocommit=True) as c:
            c.execute('CREATE EXTENSION postgis');c.execute('CREATE EXTENSION pgcrypto')
        initialize_schema(PostgreSQLConfig(dsn=cls.dsns[2154]),business_crs=2154)
        initialize_references(target_config=PostgreSQLConfig(dsn=cls.dsns[2154]))

    def db(self,srid=3950):
        return connect_test_database('DIGUES_OCC_TEST_DSN' if srid==3950 else 'DIGUES_C4_TEST_DSN',autocommit=True)
    def client(self,srid=3950):
        app=create_app()
        def database():
            with self.db(srid) as c:yield c
        app.dependency_overrides[get_connection]=database;app.dependency_overrides[get_write_connection]=database
        return self.enterContext(TestClient(app,raise_server_exceptions=False))
    def root(self,srid=3950,kind='BERGE',shape='LINESTRING(0 0,100 0)'):
        key=uuid4()
        with self.db(srid) as c:
            c.execute('INSERT INTO public.troncons(id,type_physique_id,valid,geometry) VALUES (%s,%s,true,ST_GeomFromText(%s,%s))',(key,kind,shape,srid))
        return str(key)
    def state(self,client,key):
        r=client.get('/api/troncons/'+key+'/reperage');self.assertEqual(r.status_code,200,r.text);return r.json()
    def write(self,client,key,state,**body):
        return client.put('/api/troncons/'+key+'/reperage',json={'reference_revision':state['reference_revision'],**body},headers={'If-Match':f'"{state["version"]}"'})
    def calc(self,client,key,**body):return client.post('/api/troncons/'+key+'/reperage/calcul',json=body)
    def install(self,client,key,state,srid):
        system=uuid4();bornes=[uuid4(),uuid4()];links=[uuid4(),uuid4()]
        actions=[{'action':'creer_systeme','id':str(system),'libelle':'Calibrage conservé'}]
        for i,b in enumerate(bornes):
            actions.extend([{'action':'creer_borne','id':str(b),'coordinates':[i*100,0],'srid':srid,'fictive':False},
                {'action':'lier_borne','id':str(links[i]),'systeme_reperage_id':str(system),'borne_id':str(b),
                 'valeur_pr':str(1000+i*1000),'ordre_borne':i}])
        r=self.write(client,key,state,actions=actions,systeme_reperage_defaut_id=str(system))
        self.assertEqual(r.status_code,200,r.text);return r.json(),str(system),[str(b) for b in bornes],[str(l) for l in links]
    def stored(self,key,srid):
        with self.db(srid) as c:return c.execute('SELECT version,ST_AsEWKB(geometry),ST_AsEWKB(geometry_reperage) FROM public.troncons WHERE id=%s',(key,)).fetchone()

    def test_seven_types_immediate_implicit_then_explicit_system_in_both_crs(self):
        for srid in (3950,2154):
            client=self.client(srid)
            for kind in TYPES:
                with self.subTest(srid=srid,type=kind):
                    key=self.root(srid,kind);initial=self.stored(key,srid);state=self.state(client,key)
                    self.assertEqual(state['systemes'],[]);self.assertEqual(state['liaisons'],[]);self.assertEqual(state['bornes'],[])
                    self.assertTrue(state['reperage_implicite_disponible']);self.assertEqual(len(state['reperes_virtuels']),2)
                    self.assertEqual(state['axe']['longueur_m'],100)
                    r=self.calc(client,key,mode='abscisse',abscisse_m=25)
                    self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['geometry']['coordinates'],[25,0])
                    r=self.calc(client,key,mode='xy',coordinates=[25,3],srid=srid)
                    self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['abscisse_m'],25)
                    self.assertEqual(r.json()['distance_axe_m'],3)
                    self.assertEqual(self.stored(key,srid),initial)
                    state,system,bornes,links=self.install(client,key,state,srid)
                    r=self.calc(client,key,mode='pr',reference='explicite',systeme_reperage_id=system,pr=1250)
                    self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['geometry']['coordinates'],[25,0])
                    # Explicit calibration never shadows geometric abscissa.
                    self.assertEqual(self.calc(client,key,mode='abscisse',abscisse_m=25).json()['geometry']['coordinates'],[25,0])
                    r=self.calc(client,key,mode='pr',reference='defaut',pr=1750)
                    self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['geometry']['coordinates'],[75,0])
                    self.assertEqual(self.stored(key,srid)[1],initial[1]);self.assertIsNone(self.stored(key,srid)[2])

    def test_polygon_never_implies_axis_and_explicit_axis_preserves_physical_shape(self):
        for srid in (3950,2154):
            client=self.client(srid);key=self.root(srid,shape='POLYGON((0 0,100 0,100 10,0 0))')
            before=self.stored(key,srid);state=self.state(client,key)
            self.assertFalse(state['reperage_implicite_disponible']);self.assertEqual(state['reperes_virtuels'],[])
            r=self.calc(client,key,mode='abscisse',abscisse_m=20);self.assertEqual(r.status_code,422,r.text)
            r=self.write(client,key,state,axe={'srid':srid,'coordinates':[[0,0],[100,0]]})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['axe']['source'],'EXPLICITE')
            self.assertEqual(self.calc(client,key,mode='abscisse',abscisse_m=20).json()['geometry']['coordinates'],[20,0])
            self.assertEqual(self.stored(key,srid)[1],before[1])
            state=r.json();r=self.write(client,key,state,inverser_axe=True)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(self.calc(client,key,mode='abscisse',abscisse_m=20).json()['geometry']['coordinates'],[80,0])
            self.assertEqual(self.stored(key,srid)[1],before[1])

    def test_orientation_closed_degenerate_and_domain_diagnostics(self):
        client=self.client()
        key=self.root(shape='LINESTRING(100 0,0 0)')
        self.assertEqual(self.calc(client,key,mode='abscisse',abscisse_m=25).json()['geometry']['coordinates'],[75,0])
        for value in (-1,101):self.assertEqual(self.calc(client,key,mode='abscisse',abscisse_m=value).status_code,422)
        closed=self.root(shape='LINESTRING(0 0,100 0,0 0)')
        self.assertEqual(self.calc(client,closed,mode='abscisse',abscisse_m=150).json()['geometry']['coordinates'],[50,0])
        self.assertEqual(self.calc(client,closed,mode='xy',coordinates=[50,0],srid=3950).status_code,422)
        tied=self.root(shape='LINESTRING(0 0,100 0,100 10,0 10)')
        self.assertEqual(self.calc(client,tied,mode='xy',coordinates=[50,5],srid=3950).status_code,422)
        tied_state,tied_system,_,_=self.install(client,tied,self.state(client,tied),3950)
        with self.db() as c:
            result=c.execute("SELECT statut,details FROM public.xy_vers_reperage(%s,%s,ST_GeomFromText('POINT(50 5)',3950))",(tied,tied_system)).fetchone()
            self.assertEqual(result[0],'AMBIGU');self.assertEqual(result[1]['cause'],'PROJECTION_AXE_NON_UNIVOQUE')
        # A persisted system without an owner must not authorize a guessed root.
        with self.db() as c:
            unowned=uuid4()
            c.execute('INSERT INTO public.systemes_reperage(id,valid) VALUES (%s,true)',(unowned,))
            self.assertEqual(c.execute('SELECT statut FROM public.pr_vers_xy(%s,%s,0)',(key,unowned)).fetchone()[0],'CONFLIT_SYSTEME')
        for shape in ('LINESTRING EMPTY','LINESTRING(0 0,0 0)','POINT(0 0)'):
            key=self.root(shape=shape)
            self.assertFalse(self.state(client,key)['reperage_implicite_disponible'])
            self.assertEqual(self.calc(client,key,mode='abscisse',abscisse_m=0).status_code,422)

    def test_shared_borne_versions_dependents_and_rollback_without_recalculation(self):
        client=self.client();a=self.root();b=self.root()
        state,system,bornes,links=self.install(client,a,self.state(client,a),3950)
        system_b=str(uuid4())
        actions=[{'action':'creer_systeme','id':system_b}]+[
            {'action':'lier_borne','systeme_reperage_id':system_b,'borne_id':key,'valeur_pr':i*100}
            for i,key in enumerate(bornes)]
        r=self.write(client,b,self.state(client,b),actions=actions)
        self.assertEqual(r.status_code,200,r.text)
        obs,photo=uuid4(),uuid4()
        with self.db() as c:
            c.execute('INSERT INTO public.observations(id,troncon_id,valid) VALUES (%s,%s,true)',(obs,a))
            c.execute("INSERT INTO public.photos(id,observation_id,systeme_reperage_id,borne_debut_id,geometry,position_debut,valid) "
                "VALUES (%s,%s,%s,%s,ST_GeomFromText('POINT(20 0)',3950),ST_GeomFromText('POINT(20 0)',3950),true)",(photo,obs,system,bornes[0]))
            historical=c.execute('SELECT version,ST_AsEWKB(geometry),ST_AsEWKB(position_debut) FROM public.photos WHERE id=%s',(photo,)).fetchone()
        state=self.state(client,a);before_a=self.stored(a,3950);before_b=self.stored(b,3950)
        body={'actions':[{'action':'modifier_borne','id':bornes[0],'srid':3950,'coordinates':[0,1]}]}
        for extra,status in (({},428),({'troncon_versions':{b:999}},409)):
            refused=self.write(client,a,state,**body,**extra);self.assertEqual(refused.status_code,status,refused.text)
            self.assertEqual(self.stored(a,3950),before_a);self.assertEqual(self.stored(b,3950),before_b)
        changed=self.write(client,a,state,**body,troncon_versions=state['troncon_versions'])
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['mutation']['relatedMutations'][0]['versions'],{b:before_b[0]+1})
        self.assertTrue(any(str(photo) in entry['ids'] for entry in changed.json()['localisations_dependantes']))
        with self.db() as c:self.assertEqual(c.execute('SELECT version,ST_AsEWKB(geometry),ST_AsEWKB(position_debut) FROM public.photos WHERE id=%s',(photo,)).fetchone(),historical)
        stale=self.write(client,a,state,**body,troncon_versions=state['troncon_versions'])
        self.assertEqual(stale.status_code,409,stale.text)
        # No partial creation and no event when a later action fails.
        state=self.state(client,a);new_system=str(uuid4())
        with self.db() as c:events=c.execute('SELECT count(*) FROM technique.evenements_ressources').fetchone()[0]
        failed=self.write(client,a,state,actions=[{'action':'creer_systeme','id':new_system},
            {'action':'lier_borne','systeme_reperage_id':new_system,'borne_id':str(uuid4())}])
        self.assertEqual(failed.status_code,422,failed.text)
        with self.db() as c:
            self.assertIsNone(c.execute('SELECT id FROM public.systemes_reperage WHERE id=%s',(new_system,)).fetchone())
            self.assertEqual(c.execute('SELECT count(*) FROM technique.evenements_ressources').fetchone()[0],events)
        self.assertEqual(self.state(client,a)['version'],state['version'])

    def test_explicit_foreign_default_ambiguous_pr_domain_and_retirement(self):
        client=self.client();a=self.root();b=self.root()
        state,system,bornes,links=self.install(client,a,self.state(client,a),3950)
        before_b=self.stored(b,3950)
        refused=self.write(client,b,self.state(client,b),systeme_reperage_defaut_id=system)
        self.assertEqual(refused.status_code,422,refused.text);self.assertEqual(self.stored(b,3950),before_b)
        for mode,values in (('pr',{'pr':999}),('pr',{'pr':2001}),('bornage',{'borne_id':bornes[0],'offset_m':-1}),
                            ('bornage',{'borne_id':str(uuid4()),'offset_m':0})):
            r=self.calc(client,a,mode=mode,reference='explicite',systeme_reperage_id=system,**values)
            self.assertEqual(r.status_code,422,r.text)
        duplicate=self.write(client,a,state,actions=[{'action':'modifier_liaison','id':links[1],'valeur_pr':1000}])
        self.assertEqual(duplicate.status_code,200,duplicate.text)
        ambiguous=self.calc(client,a,mode='pr',reference='defaut',pr=1000)
        self.assertEqual(ambiguous.status_code,422,ambiguous.text)
        self.assertEqual(ambiguous.json()['detail']['code'],'reperage-ambigu')
        # Geometric abscissa is independent from ambiguous calibrated PR.
        self.assertEqual(self.calc(client,a,mode='abscisse',abscisse_m=50).status_code,200)
        state=duplicate.json()
        refused=self.write(client,a,state,actions=[{'action':'retirer_systeme','id':system}])
        self.assertEqual(refused.status_code,422,refused.text)
        retired=self.write(client,a,state,actions=[{'action':'retirer_systeme','id':system}],systeme_reperage_defaut_id=None)
        self.assertEqual(retired.status_code,200,retired.text)
        with self.db() as c:
            self.assertEqual(c.execute('SELECT valid FROM public.systemes_reperage WHERE id=%s',(system,)).fetchone(),(False,))
            self.assertEqual(c.execute('SELECT count(*) FROM public.systemes_reperage__bornes WHERE systeme_reperage_id=%s',(system,)).fetchone()[0],2)
        self.assertEqual(self.calc(client,a,mode='abscisse',abscisse_m=50).status_code,200)

    def test_photo_implicit_abscissa_requires_frozen_axis_revision_and_preserves_occ(self):
        client=self.client();key=self.root();obs,photo=uuid4(),uuid4()
        with self.db() as c:
            c.execute('INSERT INTO public.observations(id,berge_id,valid) VALUES (%s,%s,true)',(obs,key))
            c.execute('INSERT INTO public.photos(id,observation_id,valid) VALUES (%s,%s,true)',(photo,obs))
        state=self.state(client,key);url='/api/photos/'+str(photo)+'/localisation'
        body={'mode':'abscisse','troncon_id':key,'abscisse_m':25}
        headers={'If-Match':'"1"','Operation-Id':str(uuid4())}
        refused=client.put(url,json=body,headers=headers);self.assertEqual(refused.status_code,428,refused.text)
        headers['Reference-Revision']=state['reference_revision']
        saved=client.put(url,json=body,headers=headers)
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(saved.json()['localisation']['geometry']['coordinates'],[25,0])
        self.assertIsNone(saved.json()['localisation']['systeme_reperage_id'])
        self.assertIsNone(saved.json()['localisation']['borne_debut_id']);self.assertIsNone(saved.json()['localisation']['pr_debut'])
        self.assertEqual(client.put(url,json=body,headers=headers).json(),saved.json())
        changed=self.write(client,key,state,inverser_axe=True);self.assertEqual(changed.status_code,200,changed.text)
        stale=client.put(url,json=body,headers={**headers,'If-Match':'"2"','Operation-Id':str(uuid4())})
        self.assertEqual(stale.status_code,409,stale.text)
        self.assertEqual(client.get('/api/photos/'+str(photo)).json()['version'],2)
        with self.db() as c:c.execute('UPDATE public.troncons SET valid=false WHERE id=%s',(key,))
        inactive=self.state(client,key)
        refused=client.put(url,json=body,headers={'If-Match':'"2"','Operation-Id':str(uuid4()),
            'Reference-Revision':inactive['reference_revision']})
        self.assertEqual(refused.status_code,422,refused.text)
        self.assertEqual(client.get('/api/photos/'+str(photo)).json()['version'],2)

    def test_c2_imported_linear_berge_is_immediate_and_historical_borne_relations_survive(self):
        from digues_app.migration.berges import prepare_bc4l_migration,insert_prepared_bc4l
        from digues_app.migration.reperage import prepare_reperage_migration,insert_prepared_reperage
        from digues_app.migration.crs import CRSInfo
        from tests.canonical_import_evidence import ProjectionEvidence
        for srid in (3950,2154):
            a,b,system,borne0,borne1=(uuid4() for _ in range(5))
            source={'Berge':[{'_id':a.hex,'geometry':f'SRID={srid};LINESTRING(0 0,100 0)','valid':True},
                {'_id':b.hex,'geometry':f'SRID={srid};LINESTRING(0 0,100 0)','valid':True,
                 'borneIds':[borne0.hex,borne1.hex],'systemeRepDefautId':system.hex}],
                'BorneDigue':[{'_id':key.hex,'geometry':f'POINT({x} 0)','valid':True} for key,x in ((borne0,0),(borne1,100))],
                'SystemeReperage':[{'_id':system.hex,'linearId':b.hex,'valid':True,'systemeReperageBornes':[
                    {'id':uuid4().hex,'borneId':key.hex,'valeurPR':pr,'valid':True} for key,pr in ((borne0,10),(borne1,110))]}]}
            with self.db(srid) as c:
                refs={name:{r[0] for r in c.execute('SELECT id FROM referentiel.'+name)} for name in
                    ('rives','types_troncon','typologies_troncon','sources_information','fonctions_structure','natures_structure','materiaux_structure')}
            roots=prepare_bc4l_migration(source,existing_ids=set(),digue_ids=set(),amenagement_ids=set(),
                borne_ids={borne0,borne1},organisme_ids=set(),rive_ids=refs['rives'],type_troncon_ids=refs['types_troncon'],
                typologie_troncon_ids=refs['typologies_troncon'],source_ids=refs['sources_information'],
                fonction_ids=refs['fonctions_structure'],nature_ids=refs['natures_structure'],materiau_ids=refs['materiaux_structure'])
            with self.db(srid) as c:
                with c.transaction():
                    prepared=prepare_reperage_migration({'BorneDigue':source['BorneDigue']},troncon_ids=set())
                    insert_prepared_reperage(c.cursor(),prepared,crs_info=CRSInfo(srid,srid))
                    insert_prepared_bc4l(c.cursor(),roots,crs_info=CRSInfo(srid,srid),on_projection=ProjectionEvidence(roots))
                self.assertEqual({row[0] for row in c.execute('SELECT borne_id FROM public.troncons_bornes WHERE troncon_id=%s',(b,))},{borne0,borne1})
            client=self.client(srid)
            implicit=self.state(client,str(a));self.assertEqual(implicit['systemes'],[])
            self.assertEqual(implicit['bornes'],[]);self.assertIsNone(implicit['systeme_reperage_defaut_id'])
            self.assertEqual(self.calc(client,str(a),mode='abscisse',abscisse_m=25).json()['geometry']['coordinates'],[25,0])
            historical=self.state(client,str(b));self.assertEqual(historical['systeme_reperage_defaut_id'],str(system))
            self.assertEqual({row['borne_id'] for row in historical['liaisons']},{str(borne0),str(borne1)})
            self.assertEqual([row['ordre_borne'] for row in sorted(historical['liaisons'],key=lambda r:r['ordre_borne'])],[0,1])
            r=self.calc(client,str(b),mode='pr',reference='defaut',pr=35)
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['geometry']['coordinates'],[25,0])


    def test_zm_geometry_is_never_rewritten_by_geometric_or_calibrated_read(self):
        client=self.client();key=self.root(shape='LINESTRING ZM(0 0 3 4,100 0 5 6)')
        before=self.stored(key,3950)
        self.assertEqual(self.calc(client,key,mode='abscisse',abscisse_m=25).json()['geometry']['coordinates'],[25,0])
        state,system,bornes,links=self.install(client,key,self.state(client,key),3950)
        pr=self.calc(client,key,mode='pr',reference='defaut',pr=1250)
        self.assertEqual(pr.status_code,200,pr.text);self.assertEqual(pr.json()['geometry']['coordinates'],[25,0])
        line=client.get('/api/troncons/'+key+'/geometrie-bornage',params={'systeme_reperage_id':system,
            'borne_debut_id':bornes[0],'distance_debut_m':10,'position_debut_relative':'APRES_BORNE',
            'borne_fin_id':bornes[1],'distance_fin_m':0,'position_fin_relative':'SUR_BORNE'})
        self.assertEqual(line.status_code,200,line.text)
        self.assertTrue(all(len(p)==2 for p in line.json()['geometry']['coordinates']))
        self.assertEqual(self.stored(key,3950)[1:],before[1:])

    def test_reference_metadata_precision_and_logical_retirement_preserve_inventory(self):
        client=self.client();key=self.root()
        state,system,bornes,links=self.install(client,key,self.state(client,key),3950)
        before=self.stored(key,3950)
        precise='123.000000000000000000001'
        changed=self.write(client,key,state,actions=[{'action':'modifier_systeme','id':system,'libelle':'','commentaire':' Texte '},
            {'action':'modifier_borne','id':bornes[0],'libelle':None,'fictive':False,'date_debut':'2000-01-02'},
            {'action':'modifier_liaison','id':links[0],'valeur_pr':precise,'ordre_borne':7}])
        self.assertEqual(changed.status_code,200,changed.text);state=changed.json()
        self.assertEqual(state['systemes'][0]['libelle'],'');self.assertEqual(state['systemes'][0]['commentaire'],' Texte ')
        self.assertEqual(next(l for l in state['liaisons'] if l['id']==links[0])['valeur_pr'],precise)
        self.assertEqual(self.stored(key,3950)[1:],before[1:])
        rejected=self.write(client,key,state,actions=[{'action':'modifier_systeme','id':system,'valid':False}])
        self.assertEqual(rejected.status_code,422,rejected.text)
        retired=self.write(client,key,state,actions=[{'action':'retirer_liaison','id':links[1]}])
        self.assertEqual(retired.status_code,200,retired.text)
        with self.db() as c:
            self.assertEqual(c.execute('SELECT valid FROM public.systemes_reperage__bornes WHERE id=%s',(links[1],)).fetchone(),(False,))
            self.assertEqual(c.execute('SELECT count(*) FROM public.bornes_reperage WHERE id=ANY(%s::uuid[])',(bornes,)).fetchone()[0],2)
        self.assertEqual(self.calc(client,key,mode='pr',reference='defaut',pr=1500).status_code,422)
        self.assertEqual(self.calc(client,key,mode='abscisse',abscisse_m=50).status_code,200)

    def test_physical_edit_refreshes_virtual_endpoints_without_rewriting_explicit_axis_or_photo(self):
        """UX-03 B: ordinary creation/PUT and C4 reads share the authoritative
        geometry; no persisted virtual bornes. Owned UUIDs, both metric CRS,
        exact dependent snapshots. Negative preservation case: explicit axis.
        """
        for srid in (3950, 2154):
            with self.subTest(srid=srid):
                client = self.client(srid)
                response = client.post('/api/troncons', json={'type_physique_id':'INDETERMINE',
                    'geometry':{'type':'LineString','coordinates':[[2,48],[2.001,48]]},'valid':True})
                self.assertEqual(response.status_code,201,response.text)
                key=response.json()['id'];state=self.state(client,key)
                self.assertEqual([p['role'] for p in state['reperes_virtuels']],['DEBUT','FIN'])
                self.assertEqual(state['reperes_virtuels'][0]['abscisse_m'],0)
                self.assertEqual(state['bornes'],[]);self.assertEqual(state['systemes'],[])
                initial_length=state['axe']['longueur_m']
                replacement={'type':'LineString','coordinates':[[2,48],[2.002,48]]}
                response=client.put('/api/troncons/'+key,headers={'If-Match':f'"{state["version"]}"'},json={'geometry':replacement})
                self.assertEqual(response.status_code,200,response.text)
                state=self.state(client,key)
                self.assertGreater(state['axe']['longueur_m'],initial_length*1.9)
                self.assertEqual(state['reperes_virtuels'][-1]['abscisse_m'],state['axe']['longueur_m'])
                with self.db(srid) as c:
                    self.assertAlmostEqual(state['axe']['longueur_m'],c.execute('SELECT ST_Length(geometry) FROM public.troncons WHERE id=%s',(key,)).fetchone()[0])
                    self.assertEqual(c.execute('SELECT count(*) FROM public.troncons_bornes WHERE troncon_id=%s',(key,)).fetchone()[0],0)
                response=self.write(client,key,state,axe={'srid':srid,'coordinates':[[0,0],[100,0]]})
                self.assertEqual(response.status_code,200,response.text)
                state,system,bornes,_=self.install(client,key,response.json(),srid)
                observation,photo=uuid4(),uuid4()
                with self.db(srid) as c:
                    c.execute('INSERT INTO public.observations(id,troncon_id,valid) VALUES (%s,%s,true)',(observation,key))
                    c.execute("INSERT INTO public.photos(id,observation_id,systeme_reperage_id,borne_debut_id,geometry,position_debut,valid) VALUES (%s,%s,%s,%s,ST_GeomFromText('POINT(20 0)',%s),ST_GeomFromText('POINT(20 0)',%s),true)",(photo,observation,system,bornes[0],srid,srid))
                    before=c.execute('SELECT to_jsonb(p),ST_AsEWKB(geometry),ST_AsEWKB(position_debut) FROM public.photos p WHERE id=%s',(photo,)).fetchone()
                axis=self.stored(key,srid)[2]
                response=client.put('/api/troncons/'+key,headers={'If-Match':f'"{state["version"]}"'},json={'geometry':{'type':'LineString','coordinates':[[2,48],[2.003,48]]}})
                self.assertEqual(response.status_code,200,response.text)
                after=self.state(client,key)
                self.assertEqual(after['axe']['source'],'EXPLICITE');self.assertEqual(after['axe']['longueur_m'],100)
                self.assertEqual(after['systemes'],state['systemes']);self.assertEqual(after['liaisons'],state['liaisons']);self.assertEqual(after['bornes'],state['bornes'])
                self.assertEqual(self.stored(key,srid)[2],axis)
                with self.db(srid) as c:
                    self.assertEqual(c.execute('SELECT to_jsonb(p),ST_AsEWKB(geometry),ST_AsEWKB(position_debut) FROM public.photos p WHERE id=%s',(photo,)).fetchone(),before)
