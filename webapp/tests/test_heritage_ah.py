"""Patrimoine AH : identités/FK réelles et isolation R6, sans géométries dupliquées.

Niveau B (~30 s de préparation) : cluster tests.run_postgres jetable uniquement.
Chaque méthode crée ses propres UUID ; aucune donnée de démonstration modifiée.
"""
import os
import unittest
from uuid import uuid4

import psycopg
from webapp.tests import test_level_a_multitenancy_postgres as runtime


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'PostgreSQL jetable requis')
class HeritageAHPostgresTest(unittest.TestCase):
    cleanup_databases = classmethod(runtime.LevelAMultitenancyPostgresTest.cleanup_databases.__func__)
    setUpClass = classmethod(runtime.LevelAMultitenancyPostgresTest.setUpClass.__func__)
    setUp = runtime.LevelAMultitenancyPostgresTest.setUp
    client = runtime.LevelAMultitenancyPostgresTest.client
    context = runtime.LevelAMultitenancyPostgresTest.context
    url = runtime.LevelAMultitenancyPostgresTest.url

    def seed(self, index, label='Alpha', ids=None):
        ids = ids or {key: uuid4() for key in ('s','d','t','u','b','a','empty','z','orphan')}
        with psycopg.connect(**{**self.params, 'dbname':self.dbnames[index]}) as c:
            c.execute("INSERT INTO systemes(id,libelle,valid) VALUES (%s,'SE Patrimoine',true)", (ids['s'],))
            c.execute("INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) VALUES (%s,%s,'Digue Patrimoine',true)", (ids['d'],ids['s']))
            c.execute("INSERT INTO amenagements_hydrauliques(id,designation,valid) VALUES (%s,%s,true),(%s,'AH vide',NULL)", (ids['a'],label,ids['empty']))
            # Le trigger courant crée la racine unifiée de ce Tronçon applicatif.
            c.execute("INSERT INTO troncons(id,type_physique_id,digue_id,libelle,geometry,valid) VALUES (%s,'INDETERMINE',%s,'Tronçon partagé',ST_Transform(ST_GeomFromText('LINESTRING(2 46,2.01 46.01)',4326),3950),true)", (ids['t'],ids['d']))
            c.execute("UPDATE troncons SET amenagement_hydraulique_id=%s WHERE id=%s", (ids['a'],ids['t']))
            c.execute("INSERT INTO troncons(id,type_physique_id,designation,amenagement_hydraulique_id,valid) VALUES (%s,'INDETERMINE','Sans extension',%s,false),(%s,'BERGE','Berge',%s,false)", (ids['u'],ids['a'],ids['b'],ids['a']))
            c.execute("INSERT INTO zones_stockage(id,designation,amenagement_hydraulique_id,valid) VALUES (%s,'Zone associée',%s,false),(%s,'Zone sans AH',NULL,false)", (ids['z'],ids['a'],ids['orphan']))
        return ids

    def hierarchy(self, index=0, login='reader_alpha'):
        result=self.client(login).get(self.url(index,'systemes-endiguement'))
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def test_exact_relations_multiple_paths_empty_and_unbound_objects(self):
        ids=self.seed(0)
        data=self.hierarchy()
        ah=next(row for row in data['amenagements_hydrauliques'] if row['id']==str(ids['a']))
        empty=next(row for row in data['amenagements_hydrauliques'] if row['id']==str(ids['empty']))
        self.assertEqual(empty['zones_stockage'],[])
        self.assertEqual(empty['troncons'],[])
        self.assertEqual([z['id'] for z in ah['zones_stockage']],[str(ids['z'])])
        self.assertEqual(ah['zones_stockage'][0]['amenagement_hydraulique_id'], ah['id'])
        trunks={row['id']:row for row in ah['troncons']}
        self.assertEqual(set(trunks),{str(ids[k]) for k in ('t','u','b')})
        self.assertEqual(trunks[str(ids['t'])]['resource'],'troncons')
        self.assertEqual(trunks[str(ids['u'])]['resource'],'troncons')
        self.assertEqual(trunks[str(ids['b'])]['type_physique_id'],'BERGE')
        self.assertEqual(trunks[str(ids['b'])]['resource'],'troncons')
        for row in trunks.values(): self.assertEqual(row['amenagement_hydraulique_id'],ah['id'])
        se=next(row for row in data['systemes'] if row['id']==str(ids['s']))
        self.assertEqual(se['digues'][0]['troncons'][0]['id'],str(ids['t']))
        def no_geometry(value):
            if isinstance(value,dict):
                self.assertNotIn('geometry',value)
                for child in value.values(): no_geometry(child)
            elif isinstance(value,list):
                for child in value: no_geometry(child)
        no_geometry(data)
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[0]}) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM troncons WHERE id=%s',(ids['t'],)).fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM zones_stockage WHERE id=%s AND amenagement_hydraulique_id IS NULL',(ids['orphan'],)).fetchone()[0],1)
            c.execute('UPDATE zones_stockage SET amenagement_hydraulique_id=%s WHERE id=%s',(ids['empty'],ids['z']))
        refreshed=self.hierarchy()['amenagements_hydrauliques']
        self.assertEqual(next(a for a in refreshed if a['id']==str(ids['a']))['zones_stockage'],[])
        self.assertEqual(next(a for a in refreshed if a['id']==str(ids['empty']))['zones_stockage'][0]['id'],str(ids['z']))

    def test_same_uuid_isolated_and_read_permissions_enforced(self):
        ids=self.seed(0,'Alpha'); self.seed(2,'Beta',ids)
        for index,login,label in ((0,'reader_alpha','Alpha'),(2,'admin_beta','Beta')):
            ah=next(a for a in self.hierarchy(index,login)['amenagements_hydrauliques'] if a['id']==str(ids['a']))
            self.assertEqual(ah['designation'],label)
        reader=self.client('reader_alpha')
        self.assertEqual(reader.get(self.url(2,'systemes-endiguement')).status_code,403)
        self.assertEqual(reader.put(self.url(0,f"amenagements-hydrauliques/{ids['a']}"),json={'designation':'Interdit'},headers={**self.headers,'If-Match':'"1"'}).status_code,403)
        self.assertEqual(next(a for a in self.hierarchy()['amenagements_hydrauliques'] if a['id']==str(ids['a']))['designation'],'Alpha')
