"""AH réglementaire et Zones : API/SQL réels sur cluster PostGIS jetable.

Niveau B (~1 min) : création, FK explicites, géométrie, version, Observations/Photos.
Chaque cas crée ses UUID ; le runner sûr détruit ensuite la base entière.
"""
import os
import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from tests.postgres_safety import connect_test_database


POLYGON = {"type": "Polygon", "coordinates": [
    [[2.0, 48.0], [2.001, 48.0], [2.001, 48.001], [2.0, 48.0]],
]}


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class AmenagementHydrauliquePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.app = create_app()

        def override():
            with connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=True) as db:
                yield db

        cls.app.dependency_overrides[get_connection] = override
        cls.app.dependency_overrides[get_write_connection] = override

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous

    def test_empty_ah_and_explicit_zone_troncon_links(self):
        with TestClient(self.app) as client:
            created = client.post("/api/amenagements-hydrauliques", json={"designation": "AH vide"})
            self.assertEqual(created.status_code, 201, created.text)
            ah = created.json()["properties"]["id"]
            self.assertIsNone(created.json()["geometry"])
            self.assertEqual(created.json()["properties"]["zone_ids"], [])
            self.assertEqual(created.json()["properties"]["troncon_ids"], [])
            self.assertEqual(client.post("/api/amenagements-hydrauliques", json={
                "geometry": POLYGON}).status_code, 422)
            zone_response = client.post("/api/zones-stockage", json={
                "designation": "Zone autonome", "geometry": POLYGON,
                "capacite_stockage": 1200.0})
            self.assertEqual(zone_response.status_code, 201, zone_response.text)
            zone = zone_response.json()["properties"]["id"]
            self.assertIsNone(zone_response.json()["properties"]["amenagement_hydraulique_id"])
            troncon = uuid4()
            with connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=True) as db:
                db.execute("INSERT INTO public.troncons "
                           "(id,type_physique_id,designation,valid) "
                           "VALUES (%s,'INDETERMINE','Trait explicite',false)", (troncon,))
            options = client.get("/api/amenagements-hydrauliques/relations/options")
            self.assertEqual(options.status_code, 200, options.text)
            self.assertIn(str(troncon), {row["id"] for row in options.json()["troncons"]})
            self.assertIn(zone, {row["id"] for row in options.json()["zones"]})
            operation_id = str(uuid4())
            linked = client.put(f"/api/amenagements-hydrauliques/{ah}", json={
                "zone_ids": [zone], "troncon_ids": [str(troncon)], "troncon_versions": {str(troncon):1}},
                headers={"If-Match": '"1"', "Operation-Id": operation_id})
            self.assertEqual(linked.status_code, 200, linked.text)
            replay = client.put(f"/api/amenagements-hydrauliques/{ah}", json={
                "zone_ids": [zone], "troncon_ids": [str(troncon)], "troncon_versions": {str(troncon):1}},
                headers={"If-Match": '"1"', "Operation-Id": operation_id})
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json()["properties"]["version"], linked.json()["properties"]["version"])
            p = linked.json()["properties"]
            self.assertEqual(p["zone_ids"], [zone])
            self.assertEqual(p["troncon_ids"], [str(troncon)])
            self.assertEqual(p["zones_stockage"][0]["id"], zone)
            self.assertEqual(p["troncons"][0]["id"], str(troncon))
            self.assertNotIn("troncon_ids", zone_response.json()["properties"])
            with connect_test_database("DIGUES_OCC_TEST_DSN") as db:
                self.assertEqual(db.execute(
                    "SELECT amenagement_hydraulique_id FROM public.zones_stockage WHERE id=%s",
                    (zone,)).fetchone()[0], UUID(ah))
                self.assertEqual(db.execute(
                    "SELECT amenagement_hydraulique_id FROM public.troncons WHERE id=%s",
                    (troncon,)).fetchone()[0], UUID(ah))
                self.assertEqual(db.execute(
                    "SELECT count(*) FROM public.amenagements_hydrauliques__troncons "
                    "WHERE amenagement_hydraulique_id=%s", (ah,)).fetchone()[0], 0)
            self.assertEqual(client.put(f"/api/amenagements-hydrauliques/{ah}",
                json={"commentaire": "périmé"}, headers={"If-Match": '"1"'}).status_code, 409)

    def test_zone_polygon_validation_and_historical_geometry(self):
        with TestClient(self.app) as client:
            missing = client.post("/api/zones-stockage", json={"designation": "Sans forme"})
            self.assertEqual(missing.status_code, 422)
            point = client.post("/api/zones-stockage", json={
                "geometry": {"type": "Point", "coordinates": [2, 48]}})
            self.assertEqual(point.status_code, 422)
            bowtie = client.post("/api/zones-stockage", json={"geometry": {
                "type": "Polygon", "coordinates": [[
                    [2, 48], [2.01, 48.01], [2.01, 48], [2, 48.01], [2, 48]]]}})
            self.assertEqual(bowtie.status_code, 422, bowtie.text)
            created = client.post("/api/zones-stockage", json={
                "geometry": POLYGON, "designation": "Zone", "superficie": 20.0})
            self.assertEqual(created.status_code, 201, created.text)
            zone = created.json()["properties"]["id"]
            with connect_test_database("DIGUES_OCC_TEST_DSN") as db:
                before = db.execute("SELECT ST_AsEWKB(geometry) FROM public.zones_stockage WHERE id=%s",
                                    (zone,)).fetchone()[0]
            updated = client.put(f"/api/zones-stockage/{zone}", json={"superficie": 21.0},
                                 headers={"If-Match": '"1"', "Operation-Id": str(uuid4())})
            self.assertEqual(updated.status_code, 200, updated.text)
            with connect_test_database("DIGUES_OCC_TEST_DSN") as db:
                after = db.execute("SELECT ST_AsEWKB(geometry) FROM public.zones_stockage WHERE id=%s",
                                   (zone,)).fetchone()[0]
            self.assertEqual(before, after)
            historical = uuid4()
            with connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=True) as db:
                db.execute("INSERT INTO public.zones_stockage(id,designation,geometry,valid) "
                           "VALUES (%s,'Historique',ST_GeomFromText('LINESTRING(0 0,1 1)',3950),false)",
                           (historical,))
            detail = client.get(f"/api/zones-stockage/{historical}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["properties"]["geometry_status"], "HISTORIQUE_NON_CONFORME")
            self.assertEqual(detail.json()["geometry"]["type"], "LineString")
            scalar = client.put(f"/api/zones-stockage/{historical}",
                json={"commentaire": "qualifier"}, headers={"If-Match": '"1"'})
            self.assertEqual(scalar.status_code, 200, scalar.text)
            self.assertEqual(scalar.json()["geometry"]["type"], "LineString")
            premature = client.put(f"/api/zones-stockage/{historical}",
                json={"valid": True}, headers={"If-Match": '"2"'})
            self.assertEqual(premature.status_code, 422, premature.text)
            self.assertIn("Corrigez explicitement", premature.text)
            untransformable = uuid4()
            with connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=True) as db:
                db.execute("INSERT INTO public.zones_stockage(id,designation,geometry,valid) "
                           "VALUES (%s,'SRID historique',ST_GeomFromText('POINT(2 48)',999999),false)",
                           (untransformable,))
            anomalous = client.get(f"/api/zones-stockage/{untransformable}")
            self.assertEqual(anomalous.status_code, 200, anomalous.text)
            self.assertIsNone(anomalous.json()["geometry"])
            self.assertEqual(anomalous.json()["properties"]["geometry_status"], "TRANSFORMATION_IMPOSSIBLE")
            self.assertEqual(client.get("/api/zones-stockage").status_code, 200)

    def test_zone_network_observations_and_photos_remain_on_zone(self):
        reseau, ah = uuid4(), uuid4()
        with connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=True) as db:
            db.execute("INSERT INTO public.reseaux_hydrauliques(id,nature,designation,valid) "
                       "VALUES (%s,'OUVERT','Fossé',true)", (reseau,))
            db.execute("INSERT INTO public.amenagements_hydrauliques(id,designation,valid) "
                       "VALUES (%s,'AH',true)", (ah,))
        with TestClient(self.app) as client:
            created = client.post("/api/zones-stockage", json={
                "geometry": POLYGON, "amenagement_hydraulique_id": str(ah),
                "reseau_hydraulique_ids": [str(reseau)]})
            self.assertEqual(created.status_code, 201, created.text)
            zone = created.json()["properties"]["id"]
            self.assertEqual(created.json()["properties"]["reseau_hydraulique_ids"], [str(reseau)])
            observation, photo = uuid4(), uuid4()
            with connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=True) as db:
                db.execute("INSERT INTO public.observations(id,zone_stockage_id,designation,valid) "
                           "VALUES (%s,%s,'Observation Zone',true)", (observation, zone))
                db.execute("INSERT INTO public.photos(id,observation_id,designation,valid) "
                           "VALUES (%s,%s,'Photo Zone',true)", (photo, observation))
                self.assertEqual(db.execute("SELECT count(*) FROM public.reseaux_hydrauliques__zones_stockage "
                    "WHERE reseau_hydraulique_id=%s AND zone_stockage_id=%s", (reseau, zone)).fetchone()[0], 1)
            listed = client.get(f"/api/zones-stockage/{zone}/observations")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["observations"][0]["id"], str(observation))
            self.assertEqual(listed.json()["observations"][0]["photo_count"], 1)
            detail = client.get(f"/api/observations/{observation}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["parent"]["resource"], "zones_stockage")
            self.assertEqual(detail.json()["photos"][0]["id"], str(photo))
            self.assertEqual(client.get(f"/api/amenagements-hydrauliques/{ah}/observations")
                             .json()["observations"], [])

    def test_missing_explicit_relations_do_not_commit(self):
        with TestClient(self.app) as client:
            ah = client.post("/api/amenagements-hydrauliques", json={"designation": "Référence"})
            self.assertEqual(ah.status_code, 201, ah.text)
            ah_id = ah.json()["properties"]["id"]
            missing = client.put(f"/api/amenagements-hydrauliques/{ah_id}",
                json={"zone_ids": [str(uuid4())]}, headers={"If-Match": '"1"'})
            self.assertEqual(missing.status_code, 422, missing.text)
            zone = client.post("/api/zones-stockage", json={
                "geometry": POLYGON, "reseau_hydraulique_ids": [str(uuid4())]})
            self.assertEqual(zone.status_code, 422, zone.text)
            with connect_test_database("DIGUES_OCC_TEST_DSN") as db:
                self.assertEqual(db.execute("SELECT version FROM public.amenagements_hydrauliques "
                    "WHERE id=%s", (ah_id,)).fetchone()[0], 1)
