"""ENTITY-TYPE-02, level B (~2 min excluding cluster initialization).

Real API, PostgreSQL and journal; unique synthetic UUIDs per scenario. No
application DSN. Proves all 49 compatible transitions, exact stored geometry,
associated rows, typed rejections, actor trace and transactional rollback.
The C1 companion retains actual deferred FK / two-session concurrency tests.
"""
import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from webapp.tests import test_troncons_c3_postgres as c3
from digues_webapp import troncons


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'Official disposable runner required')
class TronconRequalificationPostgresTest(unittest.TestCase):
    setUpClass = classmethod(c3.CanonicalTronconAPITest.setUpClass.__func__)
    setUp = c3.CanonicalTronconAPITest.setUp
    db = c3.CanonicalTronconAPITest.db
    create = c3.CanonicalTronconAPITest.create
    update = c3.CanonicalTronconAPITest.update
    state = c3.CanonicalTronconAPITest.state
    events = c3.CanonicalTronconAPITest.events

    def graph(self, key):
        """Stored snapshots, including fields absent from GeoJSON/DTOs."""
        with self.db() as c:
            root = c.execute('SELECT to_jsonb(t)-\'geometry\'-\'geometry_reperage\',ST_AsEWKB(geometry),'
                             'ST_AsEWKB(geometry_reperage),ST_SRID(geometry),ST_NDims(geometry) '
                             'FROM public.troncons t WHERE id=%s', (key,)).fetchone()
            children = {}
            for table, predicate in (
                ('systemes_reperage', 'troncon_id=%s'),
                ('troncons_bornes', 'troncon_id=%s'),
                ('observations', 'troncon_id=%s OR structure_digue_id=%s OR berge_id=%s'),
                ('localisations_objets', 'structure_digue_id=%s'),
                ('composants_berges', 'berge_id=%s'),
                ('traces_positions_troncons', 'troncon_position_id=%s'),
                ('cheminements__troncons', 'troncon_id=%s'),
                ('systemes_reperage__bornes', 'systeme_reperage_id IN (SELECT id FROM public.systemes_reperage WHERE troncon_id=%s)'),
                ('photos', 'observation_id IN (SELECT id FROM public.observations WHERE troncon_id=%s OR structure_digue_id=%s OR berge_id=%s)'),
            ):
                children[table] = c.execute(f'SELECT to_jsonb(x) FROM public.{table} x WHERE {predicate} ORDER BY id',
                                           (key,) * predicate.count('%s')).fetchall() if table != 'troncons_bornes' else c.execute(
                    'SELECT to_jsonb(x) FROM public.troncons_bornes x WHERE troncon_id=%s ORDER BY borne_id', (key,)).fetchall()
        return root, children

    def test_all_49_transitions_keep_identity_geometry_attributes_graph_and_events(self):
        for source in c3.TYPES:
            for target in c3.TYPES:
                with self.subTest(source=source, target=target):
                    key, system, observation, photo, borne, cheminement = (uuid4() for _ in range(6))
                    with self.db() as c, c.transaction():
                        c.execute("INSERT INTO public.troncons(id,type_physique_id,libelle,designation,commentaire,date_debut,valid,geometry,geometry_reperage,digue_id,amenagement_hydraulique_id,num_couche,epaisseur) "
                                  "VALUES(%s,%s,' Libellé conservé ','Désignation','Historique','1990-01-02',true,"
                                  "ST_GeomFromEWKT('SRID=2154;LINESTRING ZM(1 2 3 4,5 6 7 8)'),"
                                  "ST_GeomFromText('LINESTRING(0 0,100 0)',3950),%s,%s,0,1.25)",
                                  (key, source, self.digue, self.ah))
                        c.execute("INSERT INTO public.systemes_reperage(id,troncon_id,libelle,valid) VALUES(%s,%s,'Système historique',true)", (system,key))
                        c.execute('UPDATE public.troncons SET systeme_reperage_defaut_id=%s WHERE id=%s', (system,key))
                        c.execute('INSERT INTO public.bornes_reperage(id,valid) VALUES(%s,true)', (borne,))
                        c.execute('INSERT INTO public.troncons_bornes(troncon_id,borne_id) VALUES(%s,%s)', (key,borne))
                        c.execute('INSERT INTO public.systemes_reperage__bornes(id,systeme_reperage_id,borne_id,valeur_pr,valid) VALUES(%s,%s,%s,12.5,true)', (uuid4(),system,borne))
                        c.execute('INSERT INTO public.observations(id,troncon_id,valid) VALUES(%s,%s,true)', (observation,key))
                        c.execute("INSERT INTO public.photos(id,observation_id,libelle,valid) VALUES(%s,%s,'Photo conservée',true)", (photo,observation))
                        c.execute('INSERT INTO public.cheminements(id,valid) VALUES(%s,false)', (cheminement,))
                        c.execute('INSERT INTO public.cheminements__troncons(id,cheminement_id,troncon_id) VALUES(%s,%s,%s)', (uuid4(),cheminement,key))
                    before, children = self.graph(key)
                    version = before[0]['version']
                    response = self.update(key, version, type_physique_id=target)
                    self.assertEqual(response.status_code,200,response.text)
                    body = response.json(); p = body['properties']
                    self.assertEqual(body['id'],str(key));self.assertEqual(p['type_physique_id'],target)
                    self.assertTrue(p['requalification_autorisee']);self.assertTrue(p['requalification_conditionnelle'])
                    after, associated = self.graph(key)
                    self.assertEqual(after[1:],before[1:]);self.assertEqual(associated,children)
                    for field in (*troncons.FIELDS, 'created_by', 'edited_by'):
                        if field != 'type_physique_id':self.assertEqual(after[0][field],before[0][field],field)
                    self.assertEqual(after[0]['version'],version+1)
                    events=self.events(key);self.assertEqual(len(events),1)
                    event=events[0][1];self.assertEqual(event,body['mutation'])
                    self.assertEqual(event['ids'],[str(key)]);self.assertEqual(event['versions'],{str(key):version+1})
                    if source != target:
                        self.assertEqual(event['typeChange'],{'previousType':source,'newType':target,'actorContactId':None})
                    else:self.assertNotIn('typeChange',event)

    def assert_refused(self, key, target, code, **changes):
        before=self.graph(key);events=self.events(key)
        r=self.update(key,before[0][0]['version'],type_physique_id=target,commentaire='Ne pas enregistrer',**changes)
        self.assertEqual(r.status_code,422,r.text)
        detail=r.json()['detail'];self.assertEqual(detail['code'],code,detail)
        self.assertIn('conserv',detail['message'])
        self.assertNotIn('fk_',detail['message']);self.assertNotIn('ck_',detail['message'])
        self.assertNotIn(str(key),detail['message'])
        self.assertEqual(self.graph(key),before);self.assertEqual(self.events(key),events)
        return detail

    def test_incompatible_roles_supports_traces_fields_and_geometries_are_atomic(self):
        for role in ('observation','component'):
            key=self.create(type_physique_id='BERGE')['id']
            with self.db() as c:
                if role=='observation':c.execute('INSERT INTO public.observations(id,berge_id,valid) VALUES(%s,%s,true)', (uuid4(),key))
                else:c.execute("INSERT INTO public.composants_berges(id,berge_id,type_composant,valid) VALUES(%s,%s,'TALUS',true)", (uuid4(),key))
            self.assert_refused(key,'INDETERMINE','troncon-berge-role-in-use')
        structure=self.create(type_physique_id='TALUS')['id']
        with self.db() as c:c.execute('INSERT INTO public.localisations_objets(id,structure_digue_id) VALUES(%s,%s)', (uuid4(),structure))
        self.assert_refused(structure,'BERGE','troncon-structure-role-in-use')
        classic=self.create(type_physique_id='INDETERMINE',digue_id=str(self.digue),libelle='Support classique')['id']
        self.create(type_physique_id='TALUS',troncon_support_id=classic)
        self.assert_refused(classic,'BERGE','troncon-classic-support-in-use')
        supported=self.create(type_physique_id='TALUS',troncon_support_id=classic)['id']
        self.assert_refused(supported,'INDETERMINE','troncon-support-incompatible')
        for position,target in (('PIED','RISBERME'),('SOMMET','TALUS_RISBERME')):
            key=self.create(type_physique_id='BERGE',geometry=c3.LINE)['id']
            with self.db() as c:c.execute('INSERT INTO public.traces_positions_troncons(id,position_code,troncon_position_id,valid) VALUES(%s,%s,%s,true)', (uuid4(),position,key))
            self.assertEqual(self.assert_refused(key,target,'troncon-position-incompatible')['position'],position)
        key=self.create(type_physique_id='TALUS',largeur=7.5,hauteur_murette=2)['id']
        detail=self.assert_refused(key,'CRETE','troncon-crete-fields-incompatible')
        self.assertEqual(set(detail['fields']),{'largeur','hauteur_murette'})
        # Atypical fields survive a compatible requalification.
        r=self.update(key,type_physique_id='INDETERMINE');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['properties']['largeur'],7.5)
        for target in ('RISBERME','FRANC_BORD'):
            for geometry in (None,c3.POINT):
                key=self.create(type_physique_id='BERGE',geometry=geometry)['id']
                self.assert_refused(key,target,'troncon-geometry-incompatible')
        key=self.create(type_physique_id='BERGE')['id']
        self.assert_refused(key,'INDETERMINE','troncon-reference-invalid',source_id='absent')

    def test_compatible_typed_structure_and_position_children_survive(self):
        key=self.create(type_physique_id='TALUS',geometry=c3.LINE)['id']
        with self.db() as c, c.transaction():
            observation,photo=uuid4(),uuid4()
            c.execute('INSERT INTO public.observations(id,structure_digue_id,valid) VALUES(%s,%s,true)', (observation,key))
            c.execute('INSERT INTO public.photos(id,observation_id,valid) VALUES(%s,%s,true)', (photo,observation))
            c.execute('INSERT INTO public.localisations_objets(id,structure_digue_id) VALUES(%s,%s)', (uuid4(),key))
            c.execute("INSERT INTO public.traces_positions_troncons(id,position_code,troncon_position_id,valid) VALUES(%s,'SOMMET',%s,true)", (uuid4(),key))
        before=self.graph(key)
        r=self.update(key,type_physique_id='RISBERME');self.assertEqual(r.status_code,200,r.text)
        after=self.graph(key);self.assertEqual(after[1],before[1]);self.assertEqual(after[0][1:],before[0][1:])

    def test_historical_geometry_without_geojson_keeps_unknown_srid_and_zm(self):
        # Level B: real SQL geometry is the authority, not a null display DTO.
        for target in ('RISBERME','FRANC_BORD'):
            key=uuid4()
            with self.db() as c:
                c.execute("INSERT INTO public.troncons(id,type_physique_id,valid,geometry) "
                          "VALUES(%s,'BERGE',true,ST_GeomFromEWKT('SRID=0;LINESTRING ZM(1 2 3 4,5 6 7 8)'))",(key,))
            read=self.client.get('/api/troncons/'+str(key)).json()
            self.assertIsNone(read['geometry']);self.assertTrue(read['properties']['geometry_valide'])
            self.assertFalse(read['properties']['geometry_vide'])
            before=self.graph(key)
            r=self.update(key,type_physique_id=target);self.assertEqual(r.status_code,200,r.text)
            self.assertIsNone(r.json()['geometry']);self.assertEqual(r.json()['properties']['type_physique_id'],target)
            after=self.graph(key);self.assertEqual(after[0][1:],before[0][1:]);self.assertEqual(after[1],before[1])
            self.assertEqual(after[0][3:],(0,4));self.assertEqual(after[0][0]['version'],2)
            self.assertEqual(len(self.events(key)),1)

    def test_author_and_transactional_type_trace_rollback_with_journal(self):
        from digues_webapp.resource_mutations import execute_mutation
        from digues_webapp.models import TronconUpdate
        from digues_webapp.queries import update_troncon
        key=self.create(type_physique_id='BERGE')['id'];actor=uuid4()
        with self.db() as c:
            c.execute('INSERT INTO public.contacts(id,valid) VALUES(%s,true)', (actor,))
            c.execute("SELECT set_config('digues.actor_checked','on',false),set_config('digues.actor_contact_id',%s,false)",(str(actor),))
            command=TronconUpdate(type_physique_id='INDETERMINE')
            result=execute_mutation(c,'troncons','update',lambda:update_troncon(c,UUID(key),command),identifier=key,if_match='"1"')
            self.assertEqual(result['properties']['edited_by'],str(actor))
            self.assertEqual(result['mutation']['typeChange']['actorContactId'],str(actor))
            self.assertIsInstance(result['mutation']['occurredAt'],int)
            before=self.graph(key);events=self.events(key)
            with patch('digues_webapp.resource_mutations.append_event',side_effect=RuntimeError('journal unavailable')):
                with self.assertRaisesRegex(RuntimeError,'journal unavailable'):
                    execute_mutation(c,'troncons','update',lambda:update_troncon(c,UUID(key),TronconUpdate(type_physique_id='TALUS')),
                                     identifier=key,if_match='"2"')
            self.assertEqual(self.graph(key),before);self.assertEqual(self.events(key),events)
