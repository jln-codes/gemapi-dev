"""Recherche bornée des Désordres sur PostgreSQL/PostGIS jetable."""

import os
import unittest
from uuid import uuid4

import psycopg

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.app import create_app
from digues_webapp.database import get_connection
from webapp.tests.test_equipement_mesure_postgres import http_client


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class DesordreSearchPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.token = uuid4().hex[:10]
        cls.systeme, cls.digue, cls.troncon = uuid4(), uuid4(), uuid4()
        cls.invalid, cls.percent, cls.twins = uuid4(), uuid4(), [uuid4(), uuid4()]
        cls.bulk = [uuid4() for _ in range(2_500)]
        with psycopg.connect(cls.dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO systemes(id,libelle,valid) VALUES(%s,'Recherche',true)",
                (cls.systeme,),
            )
            connection.execute(
                "INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) "
                "VALUES(%s,%s,'Recherche',true)",
                (cls.digue, cls.systeme),
            )
            connection.execute(
                "INSERT INTO troncons(id,digue_id,libelle,geometry,valid) "
                "VALUES(%s,%s,'Tronçon filtrant',"
                "ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)",
                (cls.troncon, cls.digue),
            )
            rows = [
                (cls.invalid, f"{cls.token} donnée non validée", False),
                (cls.percent, f"{cls.token} repère 50 % aval", True),
                *((identifier, f"{cls.token} libellé jumeau", True)
                  for identifier in cls.twins),
                *((identifier, f"{cls.token} volumétrique {index:04d}", True)
                  for index, identifier in enumerate(cls.bulk)),
            ]
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO desordres(id,designation,geometry,valid) "
                    "VALUES(%s,%s,ST_SetSRID(ST_Point(1,1),3950),%s)",
                    rows,
                )
            connection.execute(
                "INSERT INTO desordres__troncons(desordre_id,troncon_id) VALUES(%s,%s)",
                (cls.percent, cls.troncon),
            )
        cls.app = create_app()

        def override():
            with psycopg.connect(cls.dsn, autocommit=True) as connection:
                yield connection

        cls.app.dependency_overrides[get_connection] = override

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous

    def test_default_limit_projection_and_valid_false_visibility(self):
        with http_client(self.app) as client:
            response = client.get("/api/desordres/search")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 50)
        self.assertTrue(payload["has_more"])
        self.assertIsNotNone(payload["next_cursor"])
        self.assertTrue(all("geometry" not in item for item in payload["items"]))
        with http_client(self.app) as client:
            invalid = client.get("/api/desordres/search", params={"q": self.token})
        invalid_item = next(item for item in invalid.json()["items"]
                            if item["id"] == str(self.invalid))
        self.assertFalse(invalid_item["valid"])

    def test_text_limit_cursor_duplicates_and_literal_wildcard(self):
        with http_client(self.app) as client:
            first = client.get(
                "/api/desordres/search", params={"q": f"{self.token} libellé jumeau", "limit": 1},
            )
            self.assertEqual(first.status_code, 200, first.text)
            second = client.get("/api/desordres/search", params={
                "q": f"{self.token} libellé jumeau", "limit": 1,
                "cursor": first.json()["next_cursor"],
            })
            literal = client.get(
                "/api/desordres/search", params={"q": f"{self.token} repère 50 %"},
            )
            maximum = client.get("/api/desordres/search", params={"limit": 100})
            refused = client.get("/api/desordres/search", params={"limit": 101})
            invalid_cursor = client.get("/api/desordres/search", params={"cursor": "***"})
        identifiers = [first.json()["items"][0]["id"], second.json()["items"][0]["id"]]
        self.assertEqual(set(identifiers), {str(value) for value in self.twins})
        self.assertFalse(second.json()["has_more"])
        self.assertEqual([item["id"] for item in literal.json()["items"]], [str(self.percent)])
        self.assertEqual(len(maximum.json()["items"]), 100)
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(invalid_cursor.status_code, 400)

    def test_troncon_filter_and_bounded_selection_resolution(self):
        unknown = uuid4()
        with http_client(self.app) as client:
            filtered = client.get(
                "/api/desordres/search", params={"troncon_id": str(self.troncon)},
            )
            selection = client.get("/api/desordres/selection", params=[
                ("ids", str(self.percent)), ("ids", str(self.invalid)),
                ("ids", str(unknown)),
            ])
            oversized = client.get("/api/desordres/selection", params=[
                ("ids", str(uuid4())) for _ in range(101)
            ])
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual([item["id"] for item in filtered.json()["items"]], [str(self.percent)])
        self.assertEqual(selection.status_code, 200, selection.text)
        self.assertEqual(
            [item["id"] for item in selection.json()["items"]],
            [str(self.percent), str(self.invalid)],
        )
        self.assertEqual(selection.json()["missing_ids"], [str(unknown)])
        self.assertEqual(oversized.status_code, 400)
        self.assertNotIn("geometry", selection.text)


if __name__ == "__main__":
    unittest.main()
