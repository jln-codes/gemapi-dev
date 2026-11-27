"""CRUD F3 Équipement de mesure sur PostgreSQL/PostGIS jetable."""

import os
from contextlib import contextmanager
import socket
import threading
import time
import unittest
from uuid import UUID, uuid4

import psycopg
import requests
import uvicorn

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.queries import OBSERVATION_READ_PARENTS
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection


@contextmanager
def http_client(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True,
    )
    thread.start()

    class Client:
        def request(self, method, url, **kwargs):
            return requests.request(
                method, f"http://127.0.0.1:{sock.getsockname()[1]}{url}",
                timeout=10, **kwargs,
            )

        def get(self, url, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url, **kwargs):
            return self.request("POST", url, **kwargs)

        def put(self, url, **kwargs):
            return self.request("PUT", url, **kwargs)

    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.025)
        yield Client()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class EquipementMesurePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_legacy = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.systeme_id, cls.digue_id, cls.troncon_id = uuid4(), uuid4(), uuid4()
        with psycopg.connect(cls.dsn, autocommit=True) as connection:

            if connection.execute("SELECT valid FROM referentiel.types_equipement_mesure WHERE id='PIE'").fetchone() != (True,):
                raise AssertionError('Type C1 Piézomètre requis par la fixture absent')
            connection.execute("INSERT INTO referentiel.types_equipement_mesure "
                               "(id,code,abrege,libelle,valid) VALUES ('OLD','OLD','OLD','Historique',false)")
            cls.valid_type_ids = [r[0] for r in connection.execute(
                "SELECT id FROM referentiel.types_equipement_mesure WHERE valid ORDER BY libelle,id")]
            connection.execute(
                "INSERT INTO systemes(id,libelle,valid) VALUES(%s,'F3',true)",
                (cls.systeme_id,),
            )
            connection.execute(
                "INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) "
                "VALUES(%s,%s,'F3',true)", (cls.digue_id, cls.systeme_id),
            )
            connection.execute(
                "INSERT INTO troncons(id,digue_id,libelle,geometry,valid) "
                "VALUES(%s,%s,'F3',ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)", (cls.troncon_id, cls.digue_id),
            )
        cls.app = create_app()

        def connection_override():
            with psycopg.connect(cls.dsn, autocommit=True) as connection:
                yield connection

        cls.app.dependency_overrides[get_connection] = connection_override
        cls.app.dependency_overrides[get_write_connection] = connection_override

    @classmethod
    def tearDownClass(cls):
        if cls.previous_legacy is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous_legacy

    def test_attribute_update_keeps_existing_point(self):
        with http_client(self.app) as client:
            created = client.post("/api/equipements-mesure", json={
                "type_id": "PIE",
                "geometry": {"type": "Point", "coordinates": [2.1, 48.2]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            original = created.json()
            identifier = original["properties"]["id"]
            updated = client.put(f"/api/equipements-mesure/{identifier}",
                                 headers={"If-Match": '"1"'},
                                 json={"designation": "Depuis Base"})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["properties"]["version"], 2)
            self.assertEqual(updated.json()["geometry"], original["geometry"])
            self.assertEqual(client.get(f"/api/equipements-mesure/{identifier}").json()["geometry"],
                             original["geometry"])

    def test_create_read_update_geometry_occ_and_rollback(self):
        with http_client(self.app) as client:
            minimal = client.post("/api/equipements-mesure", json={
                "type_id": "PIE", "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            self.assertEqual(minimal.status_code, 201, minimal.text)
            minimal_id = minimal.json()["properties"]["id"]
            self.assertEqual(minimal.json()["geometry"]["type"], "Point")
            complete = client.post("/api/equipements-mesure", json={
                "type_id": "PIE", "designation": "Capteur", "valid": False,
                "troncon_id": str(self.troncon_id),
                "geometry": {"type": "Point", "coordinates": [2.1, 48.2]},
            })
            self.assertEqual(complete.status_code, 201, complete.text)
            identifier = complete.json()["properties"]["id"]
            operation = str(uuid4())
            update = client.put(
                f"/api/equipements-mesure/{identifier}",
                headers={"If-Match": '"1"', "Operation-Id": operation},
                json={"designation": "Modifié", "commentaire": "", "valid": True,
                      "geometry": {"type": "Point", "coordinates": [2.2, 48.3]}},
            )
            self.assertEqual(update.status_code, 200, update.text)
            self.assertEqual(update.json()["properties"]["version"], 2)
            replay = client.put(
                f"/api/equipements-mesure/{identifier}",
                headers={"If-Match": '"1"', "Operation-Id": operation},
                json={"designation": "Modifié", "commentaire": "", "valid": True,
                      "geometry": {"type": "Point", "coordinates": [2.2, 48.3]}},
            )
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(client.put(
                f"/api/equipements-mesure/{identifier}",
                headers={"If-Match": '"1"'}, json={"designation": "Conflit"},
            ).status_code, 409)
            self.assertEqual(client.put(
                f"/api/equipements-mesure/{identifier}",
                headers={"If-Match": '"2"'}, json={"type_id": "ABSENT"},
            ).status_code, 422)
            self.assertEqual(
                client.get(f"/api/equipements-mesure/{identifier}").json()
                ["properties"]["designation"], "Modifié",
            )
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute(
                "SELECT ST_SRID(geometry),GeometryType(geometry) "
                "FROM equipements_mesure WHERE id=%s", (identifier,),
            ).fetchone(), (3950, "POINT"))
            self.assertIsNotNone(connection.execute(
                "SELECT geometry FROM equipements_mesure WHERE id=%s", (minimal_id,),
            ).fetchone()[0])

    def test_validation_catalog_404_and_historical_value(self):
        with http_client(self.app) as client:
            self.assertEqual(client.get(
                f"/api/equipements-mesure/{uuid4()}"
            ).status_code, 404)
            for payload in (
                {}, {"type_id": "ABSENT"},
                {"type_id": "PIE", "troncon_id": str(uuid4())},
                {"type_id": "PIE", "source_class": "source"},
                {"type_id": "PIE", "geometry": {
                    "type": "LineString", "coordinates": [[2, 48], [3, 49]],
                }},
            ):
                self.assertEqual(
                    client.post("/api/equipements-mesure", json=payload).status_code,
                    422,
                )
            catalog = client.get("/api/referentiels/types-equipement-mesure")
            self.assertEqual(catalog.status_code, 200, catalog.text)
            self.assertEqual([r["id"] for r in catalog.json()["references"]], self.valid_type_ids)

    def test_observation_photo_parent_unique(self):
        with http_client(self.app) as client:
            equipment = client.post(
                "/api/equipements-mesure", json={"type_id": "PIE",
                                            "geometry": {"type": "Point", "coordinates": [2, 48]}},
            ).json()
            identifier = equipment["properties"]["id"]
            observation_id = str(uuid4())
            observation = client.post(
                "/api/observations", headers={"Operation-Id": str(uuid4())},
                json={"id": observation_id, "parent": {
                    "resource": "equipements_mesure", "id": identifier,
                }, "designation": "Mesure"},
            )
            self.assertEqual(observation.status_code, 201, observation.text)
            photo_id = uuid4()
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                connection.execute(
                    "INSERT INTO photos(id,observation_id,storage_key,valid) "
                    "VALUES(%s,%s,'f3.jpg',true)", (photo_id, observation_id),
                )
                row = connection.execute(
                    f"SELECT equipement_mesure_id,num_nonnulls({','.join(c for c, _, _ in OBSERVATION_READ_PARENTS)}) "
                    "FROM observations WHERE id=%s", (observation_id,),
                ).fetchone()
                self.assertEqual((str(row[0]), row[1]), (identifier, 1))
                self.assertEqual(connection.execute(
                    "SELECT observation_id FROM photos WHERE id=%s", (photo_id,),
                ).fetchone()[0], UUID(observation_id))
            listed = client.get(
                f"/api/equipements-mesure/{identifier}/observations"
            )
            self.assertEqual(listed.json()["observations"][0]["photo_count"], 1)


if __name__ == "__main__":
    unittest.main()
