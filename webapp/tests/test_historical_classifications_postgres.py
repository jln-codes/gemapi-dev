"""ENTITY-TYPE-03 level B (~2 min): real HTTP/SQL, unique synthetic fixtures.

Risk: an unchanged inactive FK blocks an unrelated edit, a new inactive choice
is accepted, or incomplete historical Vegetation is silently requalified. Each
field has isolated UUIDs/references; failed writes compare full rows, EWKB,
events and operation receipts. Official disposable cluster only, no app DSN.
"""
import os
import unittest
from uuid import uuid4
from psycopg import sql
from psycopg.types.json import Jsonb
from webapp.tests import test_troncons_c3_postgres as c3
from digues_webapp import queries


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'Official disposable runner required')
class HistoricalClassificationsPostgresTest(unittest.TestCase):
    setUpClass=classmethod(c3.CanonicalTronconAPITest.setUpClass.__func__)
    setUp=c3.CanonicalTronconAPITest.setUp
    db=c3.CanonicalTronconAPITest.db

    def reference(self, table, active=False):
        identifier='ET03_'+uuid4().hex
        with self.db() as c:
            source=c.execute(sql.SQL('SELECT id FROM {} LIMIT 1').format(sql.Identifier(*table.split('.')))).fetchone()
            data=dict(id=identifier,code=identifier,abrege=identifier,libelle='Référence historique de test',valid=active)
            if source:
                c.execute(sql.SQL('INSERT INTO {} SELECT (jsonb_populate_record(NULL::{},to_jsonb(src)||%s)).* FROM {} src WHERE id=%s')
                          .format(*[sql.Identifier(*table.split('.'))]*3),(Jsonb(data),source[0]))
            else:
                c.execute(sql.SQL('INSERT INTO {} SELECT (jsonb_populate_record(NULL::{},%s)).*')
                          .format(*[sql.Identifier(*table.split('.'))]*2),(Jsonb(data),))
        return identifier

    def state(self, table, key):
        with self.db() as c:
            row=c.execute(sql.SQL('SELECT to_jsonb(t),ST_AsEWKB(geometry) FROM public.{} t WHERE id=%s').format(sql.Identifier(table)),(key,)).fetchone()
            events=c.execute("SELECT payload FROM technique.evenements_ressources WHERE payload->'ids' @> jsonb_build_array(%s::text) ORDER BY sequence_id",(str(key),)).fetchall()
            receipts=c.execute('SELECT operation_id,fingerprint,response FROM technique.operations_ressources WHERE resource_id=%s ORDER BY operation_id',(key,)).fetchall()
        return row,events,receipts

    def put(self, path, key, version, changes):
        return self.client.put('/api/'+path+'/'+str(key),json=changes,
                               headers={'If-Match':f'"{version}"','Operation-Id':str(uuid4())})

    def historical(self, table, field, value):
        key=uuid4()
        with self.db() as c:
            if table=='photos':
                trunk,observation=uuid4(),uuid4()
                c.execute("INSERT INTO public.troncons(id,type_physique_id,valid) VALUES(%s,'BERGE',false)",(trunk,))
                c.execute('INSERT INTO public.observations(id,troncon_id,valid) VALUES(%s,%s,false)',(observation,trunk))
                c.execute(sql.SQL('INSERT INTO public.photos(id,observation_id,{},valid) VALUES(%s,%s,%s,false)').format(sql.Identifier(field)),(key,observation,value))
            else:
                geometry='POLYGON((0 0,1 0,1 1,0 0))' if table=='zones_stockage' else 'LINESTRING(0 0,1 1)' if table=='cheminements' else 'POINT(0 0)'
                extra=",type_cheminement_id" if table=='cheminements' and field!='type_cheminement_id' else ''
                chosen=",'CAC'" if extra else ''
                c.execute(sql.SQL('INSERT INTO public.{}(id,{},valid,geometry'+extra+') VALUES(%s,%s,false,ST_GeomFromText(%s,3950)'+chosen+')')
                          .format(sql.Identifier(table),sql.Identifier(field)),(key,value,geometry))
        return key

    def test_inactive_included_omitted_new_unknown_and_active_choices_for_every_field(self):
        chemin={'type_cheminement_id':'referentiel.types_cheminement',**{
            field:queries.EDITABLE_REFERENCE_TABLES[alias][0] for field,alias in queries.CHEMINEMENT_REFERENCE_FIELDS.items()}}
        families=[('ouvrages-particuliers','ouvrages_particuliers',{'type_id':'referentiel.types_ouvrage_particulier'}),
                  ('zones-stockage','zones_stockage',{'type_id':'referentiel.types_amenagement_hydraulique','fonctionnement_id':'referentiel.fonctionnements_amenagement_hydraulique'}),
                  ('cheminements','cheminements',chemin),
                  ('photos','photos',{'orientation_photo_id':'referentiel.orientations_photo','cote_id':'referentiel.cotes','photographe_id':'public.contacts'})]
        for path,table,fields in families:
            for field,catalogue in fields.items():
                with self.subTest(family=path,field=field):
                    if catalogue=='public.contacts':
                        old,other,active=(uuid4() for _ in range(3))
                        with self.db() as c:
                            for value,valid in [(old,False),(other,False),(active,True)]:c.execute('INSERT INTO public.contacts(id,valid) VALUES(%s,%s)',(value,valid))
                        old,other,active=map(str,(old,other,active));missing=str(uuid4())
                    else:
                        old=self.reference(catalogue);other=self.reference(catalogue);active=self.reference(catalogue,True);missing='ET03_ABSENT'
                    key=self.historical(table,field,old);description='libelle' if table=='photos' else 'commentaire'
                    before=self.state(table,key)
                    r=self.put(path,key,1,{field:old,description:'Retouche historique'})
                    self.assertEqual(r.status_code,200,r.text)
                    state=self.state(table,key);self.assertEqual(str(state[0][0][field]),old)
                    self.assertEqual(state[0][1],before[0][1]);self.assertEqual(state[0][0]['version'],2)
                    self.assertEqual({k:v for k,v in state[0][0].items() if k not in (description,'version')},
                                     {k:v for k,v in before[0][0].items() if k not in (description,'version')})
                    self.assertEqual((len(state[1]),len(state[2])),(1,1))
                    r=self.put(path,key,2,{description:'Classification omise'})
                    self.assertEqual(r.status_code,200,r.text)
                    stable=self.state(table,key);self.assertEqual(str(stable[0][0][field]),old)
                    self.assertEqual(stable[0][1],before[0][1])
                    for refused in (other,missing):
                        r=self.put(path,key,3,{field:refused,description:'Ne pas enregistrer'})
                        self.assertEqual(r.status_code,422,r.text);self.assertEqual(self.state(table,key),stable)
                    r=self.put(path,key,1,{description:'Version périmée',field:old})
                    self.assertEqual(r.status_code,409,r.text);self.assertEqual(self.state(table,key),stable)
                    r=self.put(path,key,3,{field:active});self.assertEqual(r.status_code,200,r.text)
                    chosen=self.state(table,key)
                    self.assertEqual(str(chosen[0][0][field]),active);self.assertEqual(chosen[0][1],before[0][1])
                    r=self.put(path,key,4,{field:old});self.assertEqual(r.status_code,422,r.text)
                    self.assertEqual(self.state(table,key),chosen)

    def test_creation_never_treats_inactive_values_as_historical(self):
        for path,catalogue,field,geometry in [
            ('ouvrages-particuliers','referentiel.types_ouvrage_particulier','type_id',c3.POINT),
            ('zones-stockage','referentiel.types_amenagement_hydraulique','type_id',c3.POLYGON),
            ('zones-stockage','referentiel.fonctionnements_amenagement_hydraulique','fonctionnement_id',c3.POLYGON),
            ('cheminements','referentiel.types_cheminement','type_cheminement_id',c3.LINE),
            ('cheminements','referentiel.materiaux_cheminement','materiau_id',c3.LINE),
        ]:
            inactive=self.reference(catalogue)
            body={field:inactive,'geometry':geometry}
            if path=='cheminements' and field!='type_cheminement_id':body['type_cheminement_id']='CAC'
            r=self.client.post('/api/'+path,json=body);self.assertEqual(r.status_code,422,r.text)
        # Real API rejects the reference before any media is consumed; no media
        # boundary is mocked or claimed as qualified by this negative scenario.
        photo=self.historical('photos','orientation_photo_id',self.reference('referentiel.orientations_photo'))
        with self.db() as c:observation=c.execute('SELECT observation_id FROM public.photos WHERE id=%s',(photo,)).fetchone()[0]
        for field,catalogue in [('orientation_photo_id','referentiel.orientations_photo'),('cote_id','referentiel.cotes')]:
            inactive=self.reference(catalogue)
            r=self.client.post('/api/photos',json={'id':str(uuid4()),'observation_id':str(observation),'upload_ref':str(uuid4()),
                'file_sha256':'0'*64,'file_size':1,field:inactive},headers={'Operation-Id':str(uuid4())})
            self.assertEqual(r.status_code,422,r.text);self.assertEqual(r.json()['detail']['code'],'photo-reference-invalid')

    def test_historical_vegetation_partial_edits_validation_and_explicit_taxonomy(self):
        for family,disposition,subtype in [(None,None,None),('HERBACEES',None,None),(None,'ISOLE','SOUCHE')]:
            key=uuid4()
            with self.db() as c:c.execute('INSERT INTO public.vegetation(id,famille_vegetation,disposition,sous_type,valid) VALUES(%s,%s,%s,%s,false)',(key,family,disposition,subtype))
            r=self.put('vegetation',key,1,{'commentaire':'Retouche sans qualification'})
            self.assertEqual(r.status_code,200,r.text)
            stable=self.state('vegetation',key)
            self.assertEqual(tuple(stable[0][0][f] for f in ('famille_vegetation','disposition','sous_type','valid')),(family,disposition,subtype,False))
            self.assertIsNone(stable[0][1])
            for change in ({'valid':True},{'famille_vegetation':None},{'disposition':None},{'famille_vegetation':'ARBRES','sous_type':None}):
                r=self.put('vegetation',key,2,change);self.assertEqual(r.status_code,422,r.text)
                self.assertEqual(self.state('vegetation',key),stable)
            r=self.put('vegetation',key,1,{'commentaire':'Périmé'});self.assertEqual(r.status_code,409,r.text)
            self.assertEqual(self.state('vegetation',key),stable)
            r=self.put('vegetation',key,2,{'famille_vegetation':'ARBRES','disposition':'ISOLE','sous_type':'SOUCHE','geometry':c3.POINT,'valid':True})
            self.assertEqual(r.status_code,200,r.text)
            stable=self.state('vegetation',key)
            r=self.put('vegetation',key,3,{'famille_vegetation':'HERBACEES'})
            self.assertEqual(r.status_code,422,r.text);self.assertEqual(self.state('vegetation',key),stable)
            r=self.put('vegetation',key,3,{'famille_vegetation':'HERBACEES','sous_type':None})
            self.assertEqual(r.status_code,200,r.text)
            self.assertIsNone(self.state('vegetation',key)[0][0]['sous_type'])
