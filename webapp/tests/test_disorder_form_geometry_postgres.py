"""FORM-GEOM-1, niveau B (~20 s + cluster) : une mutation atomique par formulaire.

PostGIS jetable exclusivement. Chaque scénario crée ses objets. Les refus de
bornage/forme et les versions périmées ne doivent laisser ni attribut partiel,
ni version/événement supplémentaire ; un rejeu conserve son Operation-Id.
"""
import unittest
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from tests.postgres_safety import require_test_dsn
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection


class DisorderFormGeometryPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = require_test_dsn("DIGUES_OCC_TEST_DSN")

    def setUp(self):
        app = create_app()
        def connection():
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                yield conn
        app.dependency_overrides[get_connection] = connection
        app.dependency_overrides[get_write_connection] = connection
        self.client = self.enterContext(TestClient(app))

    def create(self, kind, trunk=None):
        coordinates = {"Point": [2.35, 50.5],
            "LineString": [[2.35,50.5],[2.351,50.502],[2.352,50.5]],
            "Polygon": [[[2.35,50.5],[2.351,50.502],[2.352,50.5],[2.35,50.5]]]}[kind]
        response=self.client.post('/api/desordres',json={"geometry_type":kind,
            "geometry":{"type":kind,"coordinates":coordinates},
            "designation":"Initial", "troncon_ids":[trunk] if trunk else []})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def put(self, feature, payload, operation=None):
        return self.client.put('/api/desordres/'+feature['properties']['id'],json=payload,
            headers={'If-Match':f'"{feature["properties"]["version"]}"',
                     'Operation-Id':operation or str(uuid4())})

    def read(self, feature):
        response=self.client.get('/api/desordres/'+feature['properties']['id'])
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_attributes_and_each_geometry_are_saved_together_and_replayed_once(self):
        for kind in ['Point','LineString','Polygon']:
            with self.subTest(kind=kind):
                feature=self.create(kind)
                if kind=='Point':spatial={'longitude':2.36,'latitude':50.51}
                else:
                    shape=feature['geometry']
                    coordinates=([[x+.01,y] for x,y in shape['coordinates']] if kind=='LineString'
                        else [[[x+.01,y] for x,y in shape['coordinates'][0]]])
                    spatial={'geometry':{'type':kind,'coordinates':coordinates}}
                operation=str(uuid4());payload={'designation':'Ensemble',**spatial}
                response=self.put(feature,payload,operation)
                self.assertEqual(response.status_code,200,response.text)
                saved=response.json();actual=self.read(feature)
                self.assertEqual(actual['properties']['designation'],'Ensemble')
                self.assertNotEqual(actual['geometry'],feature['geometry'])
                self.assertEqual(actual['geometry'],saved['geometry'])
                replay=self.put(feature,payload,operation)
                self.assertEqual(replay.status_code,200,replay.text)
                self.assertEqual(replay.json()['mutation'],saved['mutation'])
                stale=self.put(feature,{'designation':'Obsolète'})
                self.assertEqual(stale.status_code,409,stale.text)
                self.assertEqual(self.read(feature)['properties']['version'],saved['properties']['version'])

    def test_endpoints_preserve_middle_and_invalid_geometry_rolls_back_attributes(self):
        feature=self.create('LineString')
        response=self.put(feature,{'commentaire':'Extrémités','line_endpoints':{
            'crs':'EPSG:4326','debut':[2.349,50.5],'fin':[2.353,50.5]}})
        self.assertEqual(response.status_code,200,response.text)
        actual=response.json()
        self.assertEqual(actual['geometry']['coordinates'][1],feature['geometry']['coordinates'][1])
        self.assertEqual(actual['properties']['commentaire'],'Extrémités')
        # Shape itself is valid, but changing the persisted family's type is not.
        polygon={'type':'Polygon','coordinates':[[[2.35,50.5],[2.36,50.5],[2.36,50.51],[2.35,50.5]]]}
        refused=self.put(actual,{'designation':'Ne doit pas rester','geometry':polygon})
        self.assertEqual(refused.status_code,404,refused.text)
        reread=self.read(actual)
        self.assertEqual(reread['properties']['designation'],actual['properties']['designation'])
        self.assertEqual(reread['properties']['version'],actual['properties']['version'])
        self.assertEqual(reread['geometry'],actual['geometry'])

    def test_bornage_and_attributes_are_atomic_and_invalid_reference_keeps_baseline(self):
        trunk=self.client.get('/api/troncons/options').json()['troncons'][0]['id']
        options=self.client.get(f'/api/troncons/{trunk}/reperage-options').json()
        for kind in ['Point', 'LineString']:
            feature=self.create(kind,trunk)
            command={'borne_debut_id':options['bornes'][0]['id'],'distance_debut_m':0,
                     'position_debut_relative':'SUR_BORNE'}
            if kind == 'LineString':
                command.update(borne_fin_id=options['bornes'][-1]['id'],distance_fin_m=0,position_fin_relative='SUR_BORNE')
            response=self.put(feature,{'designation':'Borné','reperage':command})
            self.assertEqual(response.status_code,200,response.text)
            saved=response.json()
            self.assertEqual(saved['properties']['designation'],'Borné')
            self.assertEqual(saved['properties']['reperage']['borne_debut_id'],command['borne_debut_id'])
            invalid=self.put(saved,{'designation':'Partiel interdit','reperage':{**command,'borne_debut_id':str(uuid4())}})
            self.assertEqual(invalid.status_code,400,invalid.text)
            self.assertIn("borne", invalid.json()["detail"])
            reread=self.read(saved)
            self.assertEqual(reread['properties']['designation'],'Borné')
            self.assertEqual(reread['properties']['version'],saved['properties']['version'])
            self.assertEqual(reread['geometry'],saved['geometry'])

    def test_two_localisation_authorities_or_null_are_refused(self):
        feature=self.create('LineString')
        for command in [{'geometry':None}, {'reperage':None}, {'line_endpoints':None},
            {'geometry':feature['geometry'],'line_endpoints':{'crs':'EPSG:4326','debut':[2,50],'fin':[3,50]}}]:
            response=self.put(feature,{'designation':'Refusé',**command})
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.read(feature)['properties']['version'],feature['properties']['version'])
