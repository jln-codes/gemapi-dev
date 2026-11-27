"""FORM-LOCALISATION-2, level B, ~10 s with the seeded disposable runner.

Read-only HTTP/PostGIS conversion, including incompatible references and
incomplete requests. Mutations/reopening are covered by form-regression-live.
"""
import unittest
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from tests.postgres_safety import require_test_dsn
from digues_webapp.app import create_app
from digues_webapp.database import get_connection


class BornageGeometryPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = require_test_dsn("DIGUES_OCC_TEST_DSN")

    def setUp(self):
        app = create_app()

        def read_connection():
            with psycopg.connect(self.dsn) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                yield connection

        app.dependency_overrides[get_connection] = read_connection
        self.client = self.enterContext(TestClient(app))
        trunks = self.client.get("/api/troncons/options").json()["troncons"]
        self.trunk = trunks[0]["id"]
        self.options = self.client.get(f"/api/troncons/{self.trunk}/reperage-options").json()
        self.path = f"/api/troncons/{self.trunk}/geometrie-bornage"
        self.params = {
            "systeme_reperage_id": self.options["systeme_reperage_id"],
            "borne_debut_id": self.options["bornes"][0]["id"],
            "distance_debut_m": "1.34",
            "position_debut_relative": "APRES_BORNE",
        }

    def test_point_and_line_match_real_postgis_in_a_readonly_transaction(self):
        point = self.client.get(self.path, params=self.params)
        self.assertEqual(point.status_code, 200, point.text)
        with psycopg.connect(self.dsn) as connection:
            expected = connection.execute(
                "SELECT ST_AsGeoJSON(ST_Transform(point_xy,4326))::jsonb "
                "FROM public.borne_offset_vers_xy(%s,%s,%s,%s)",
                (self.trunk, self.params["systeme_reperage_id"], self.params["borne_debut_id"], 1.34),
            ).fetchone()[0]
        self.assertEqual(point.json()["geometry"], expected)
        line = self.client.get(self.path, params={
            **self.params, "borne_fin_id": self.options["bornes"][-1]["id"],
            "distance_fin_m": 0, "position_fin_relative": "SUR_BORNE",
        })
        self.assertEqual(line.status_code, 200, line.text)
        self.assertEqual(line.json()["geometry"]["type"], "LineString")
        self.assertEqual(line.json()["geometry"]["coordinates"][0], expected["coordinates"])
        # The preview must follow the actual referencing axis, including its
        # intermediate vertices; it must not invent a straight chord.
        with psycopg.connect(self.dsn) as connection:
            axis = connection.execute(
                "SELECT ST_AsGeoJSON(ST_Transform(coalesce(geometry_reperage,geometry),4326))::jsonb "
                "FROM public.troncons WHERE id=%s", (self.trunk,),
            ).fetchone()[0]
        self.assertEqual(line.json()["geometry"]["coordinates"],
                         [expected["coordinates"], *axis["coordinates"][1:]])

    def test_wrong_system_missing_borne_negative_distance_and_incomplete_end_are_refused(self):
        for changes in [
            {"systeme_reperage_id": str(uuid4())},
            {"borne_debut_id": str(uuid4())},
            {"distance_debut_m": -1},
            {"distance_debut_m": "nan"},
            {"position_debut_relative": "SUR_BORNE"},
            {"borne_fin_id": self.options["bornes"][-1]["id"]},
        ]:
            with self.subTest(changes=changes):
                response = self.client.get(self.path, params={**self.params, **changes})
                self.assertEqual(response.status_code, 422, response.text)
        absent = self.client.get(
            f"/api/troncons/{uuid4()}/geometrie-bornage", params=self.params,
        )
        self.assertEqual(absent.status_code, 422, absent.text)
