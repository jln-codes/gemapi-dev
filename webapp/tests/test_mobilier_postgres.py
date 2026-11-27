"""CRUD F2 Mobilier sur PostgreSQL/PostGIS strictement jetable."""

import os
from contextlib import contextmanager
from decimal import Decimal
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
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()

    class Client:
        def request(self, method, url, **kwargs):
            return requests.request(
                method,
                f"http://127.0.0.1:{sock.getsockname()[1]}{url}",
                timeout=10,
                **kwargs,
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
        if not server.started:
            raise RuntimeError("Serveur HTTP de test indisponible.")
        yield Client()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class MobilierPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_legacy_mode = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.type_id = "MRE"
        cls.other_type_id = "MRE-INACTIVE"
        cls.systeme_id = uuid4()
        cls.digue_id = uuid4()
        cls.troncon_id = uuid4()
        with psycopg.connect(cls.dsn, autocommit=True) as connection:
            # R6 already initializes its C1 catalog. Reuse the canonical ID,
            # keep all standard rows, and add only this inactive test reference.
            if connection.execute("SELECT valid FROM referentiel.types_mobilier WHERE id=%s", (cls.type_id,)).fetchone() != (True,):
                raise AssertionError('Type C1 Mobilier requis par la fixture absent')
            connection.execute(
                "INSERT INTO referentiel.types_mobilier (id,code,abrege,libelle,valid) "
                "VALUES (%s,'F2-INACTIVE','F2I','Type inactif',false)", (cls.other_type_id,))
            cls.valid_type_ids = [r[0] for r in connection.execute(
                "SELECT id FROM referentiel.types_mobilier WHERE valid ORDER BY libelle,id")]
            connection.execute(
                "INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'F2',true)",
                (cls.systeme_id,),
            )
            connection.execute(
                "INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) "
                "VALUES (%s,%s,'F2',true)",
                (cls.digue_id, cls.systeme_id),
            )
            connection.execute(
                "INSERT INTO public.troncons(id,digue_id,libelle,geometry,valid) "
                "VALUES (%s,%s,'Tronçon F2',ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)",
                (cls.troncon_id, cls.digue_id),
            )

        cls.app = create_app()

        def connection_override():
            with psycopg.connect(cls.dsn, autocommit=True) as connection:
                yield connection

        cls.app.dependency_overrides[get_connection] = connection_override
        cls.app.dependency_overrides[get_write_connection] = connection_override

    @classmethod
    def tearDownClass(cls):
        if cls.previous_legacy_mode is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous_legacy_mode

    def test_attribute_update_keeps_existing_point(self):
        with http_client(self.app) as client:
            created = client.post("/api/mobilier", json={
                "type_id": self.type_id,
                "geometry": {"type": "Point", "coordinates": [2.1, 48.2]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            original = created.json()
            identifier = original["properties"]["id"]
            updated = client.put(f"/api/mobilier/{identifier}",
                                 headers={"If-Match": '"1"'},
                                 json={"designation": "Depuis Base"})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["properties"]["version"], 2)
            self.assertEqual(updated.json()["geometry"], original["geometry"])
            self.assertEqual(client.get(f"/api/mobilier/{identifier}").json()["geometry"],
                             original["geometry"])

    def test_create_read_update_occ_geometry_and_rollback(self):
        with http_client(self.app) as client:
            minimal = client.post("/api/mobilier", json={
                "type_id": self.type_id,
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            self.assertEqual(minimal.status_code, 201, minimal.text)
            minimal_body = minimal.json()
            minimal_id = minimal_body["properties"]["id"]
            self.assertEqual(minimal_body["geometry"]["type"], "Point")
            self.assertEqual(minimal_body["properties"]["version"], 1)

            complete = client.post("/api/mobilier", json={
                "type_id": self.type_id,
                "designation": "Pilote",
                "commentaire": "Complet",
                "date_debut": "2026-09-28",
                "troncon_id": str(self.troncon_id),
                "valid": False,
                "geometry": {"type": "Point", "coordinates": [2.1, 48.2]},
            })
            self.assertEqual(complete.status_code, 201, complete.text)
            body = complete.json()
            identifier = body["properties"]["id"]
            self.assertEqual(body["geometry"]["type"], "Point")
            self.assertFalse(body["properties"]["valid"])
            self.assertEqual(body["properties"]["troncon_id"], str(self.troncon_id))

            read = client.get(f"/api/mobilier/{identifier}")
            self.assertEqual(read.status_code, 200, read.text)
            self.assertEqual(read.json()["properties"]["version"], 1)

            operation_id = str(uuid4())
            update = client.put(
                f"/api/mobilier/{identifier}",
                headers={"If-Match": '"1"', "Operation-Id": operation_id},
                json={
                    "designation": "Modifié",
                    "commentaire": "",
                    "date_debut": None,
                    "type_id": self.type_id,
                    "troncon_id": None,
                    "valid": True,
                    "geometry": {"type": "Point", "coordinates": [2.2, 48.3]},
                },
            )
            self.assertEqual(update.status_code, 200, update.text)
            updated = update.json()
            self.assertEqual(updated["properties"]["version"], 2)
            self.assertEqual(updated["properties"]["designation"], "Modifié")
            self.assertIsNone(updated["properties"]["commentaire"])
            self.assertIsNone(updated["properties"]["troncon_id"])
            self.assertEqual(updated["mutation"]["resource"], "mobilier")

            # Une valeur historique devenue inactive reste éditable tant que
            # le formulaire la conserve à l'identique.
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                connection.execute(
                    "UPDATE referentiel.types_mobilier SET valid=false WHERE id=%s",
                    (self.type_id,),
                )
                connection.execute(
                    "UPDATE public.troncons SET valid=false WHERE id=%s",
                    (self.troncon_id,),
                )
            preserved = client.put(
                f"/api/mobilier/{minimal_id}",
                headers={"If-Match": '"1"'},
                json={
                    "designation": "Référence historique conservée",
                    "type_id": self.type_id,
                },
            )
            self.assertEqual(preserved.status_code, 200, preserved.text)
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                connection.execute(
                    "UPDATE referentiel.types_mobilier SET valid=true WHERE id=%s",
                    (self.type_id,),
                )
                connection.execute(
                    "UPDATE public.troncons SET valid=true WHERE id=%s",
                    (self.troncon_id,),
                )

            replay = client.put(
                f"/api/mobilier/{identifier}",
                headers={"If-Match": '"1"', "Operation-Id": operation_id},
                json={
                    "designation": "Modifié",
                    "commentaire": "",
                    "date_debut": None,
                    "type_id": self.type_id,
                    "troncon_id": None,
                    "valid": True,
                    "geometry": {"type": "Point", "coordinates": [2.2, 48.3]},
                },
            )
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json()["mutation"], updated["mutation"])

            conflict = client.put(
                f"/api/mobilier/{identifier}",
                headers={"If-Match": '"1"'},
                json={"designation": "Obsolète"},
            )
            self.assertEqual(conflict.status_code, 409, conflict.text)

            invalid = client.put(
                f"/api/mobilier/{identifier}",
                headers={"If-Match": '"2"'},
                json={"type_id": self.other_type_id},
            )
            self.assertEqual(invalid.status_code, 422, invalid.text)
            persisted = client.get(f"/api/mobilier/{identifier}").json()
            self.assertEqual(persisted["properties"]["designation"], "Modifié")
            self.assertEqual(persisted["properties"]["version"], 2)

        with psycopg.connect(self.dsn) as connection:
            row = connection.execute(
                "SELECT ST_SRID(geometry), GeometryType(geometry), "
                "round(ST_X(ST_Transform(geometry,4326))::numeric,3), "
                "round(ST_Y(ST_Transform(geometry,4326))::numeric,3) "
                "FROM public.mobilier WHERE id=%s",
                (identifier,),
            ).fetchone()
            self.assertEqual(
                row,
                (3950, "POINT", Decimal("2.200"), Decimal("48.300")),
            )
            self.assertIsNotNone(connection.execute(
                "SELECT geometry FROM public.mobilier WHERE id=%s", (minimal_id,)
            ).fetchone()[0])

    def test_validation_404_422_and_reference_catalog(self):
        missing = uuid4()
        with http_client(self.app) as client:
            self.assertEqual(client.get(f"/api/mobilier/{missing}").status_code, 404)
            invalid_payloads = [
                {},
                {"type_id": "invalide"},
                {"type_id": self.type_id, "source_class": "Mobilier"},
                {"type_id": self.type_id, "geometry": {
                    "type": "LineString", "coordinates": [[2, 48], [3, 49]],
                }},
                {"type_id": self.other_type_id},
                {"type_id": self.type_id, "troncon_id": str(uuid4())},
            ]
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    response = client.post("/api/mobilier", json=payload)
                    self.assertEqual(response.status_code, 422, response.text)
            references = client.get("/api/referentiels/types-mobilier")
            self.assertEqual(references.status_code, 200, references.text)
            self.assertEqual(
                [row["id"] for row in references.json()["references"]],
                self.valid_type_ids,
            )

    def test_observation_photo_parent_and_exactly_one_parent(self):
        with http_client(self.app) as client:
            mobilier = client.post(
                "/api/mobilier", json={"type_id": self.type_id, "designation": "Observé",
                                        "geometry": {"type": "Point", "coordinates": [2, 48]}}
            ).json()
            mobilier_id = mobilier["properties"]["id"]
            observation_id = str(uuid4())
            observation = client.post(
                "/api/observations",
                headers={"Operation-Id": str(uuid4())},
                json={
                    "id": observation_id,
                    "parent": {"resource": "mobilier", "id": mobilier_id},
                    "designation": "Observation Mobilier",
                },
            )
            self.assertEqual(observation.status_code, 201, observation.text)
            self.assertEqual(observation.json()["parent"]["resource"], "mobilier")
            photo_id = uuid4()
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                connection.execute(
                    "INSERT INTO public.photos"
                    "(id,observation_id,storage_key,designation,valid) "
                    "VALUES (%s,%s,'f2.jpg','Photo F2',true)",
                    (photo_id, observation_id),
                )
                parents = connection.execute(
                    f"SELECT num_nonnulls({','.join(c for c, _, _ in OBSERVATION_READ_PARENTS)}), mobilier_id "
                    "FROM public.observations WHERE id=%s",
                    (observation_id,),
                ).fetchone()
                self.assertEqual(parents[0], 1)
                self.assertEqual(str(parents[1]), mobilier_id)
                self.assertEqual(connection.execute(
                    "SELECT observation_id FROM public.photos WHERE id=%s", (photo_id,)
                ).fetchone()[0], UUID(observation_id))

            listed = client.get(f"/api/mobilier/{mobilier_id}/observations")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["observations"][0]["photo_count"], 1)


if __name__ == "__main__":
    unittest.main()
