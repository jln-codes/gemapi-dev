"""UX-GEO-1 — API/SQL contract, real PostgreSQL 16/PostGIS, level B.

Run via tests.run_postgres. Each scenario owns its UUIDs in the disposable
cluster. Verify source geometry preservation, rejected validity and version
drift, transaction rollback and replay; estimated cost below one minute.
"""
import os
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from tests.postgres_safety import require_test_dsn
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from digues_webapp.geometry_capabilities import load_geometry_model


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class GeometryCapabilitiesPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = require_test_dsn("DIGUES_OCC_TEST_DSN")
        cls.previous_legacy = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
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

    def state(self, identifier):
        with psycopg.connect(self.dsn) as connection:
            return connection.execute(
                "SELECT ST_AsEWKB(geometry),ST_SRID(geometry),valid,version,designation "
                "FROM public.ouvrages_particuliers WHERE id=%s", (identifier,),
            ).fetchone()

    def test_exposed_shapes_are_accepted_by_api_and_postgis(self):
        geometries = {
            "Point": {"type": "Point", "coordinates": [2, 48]},
            "LineString": {"type": "LineString", "coordinates": [[2, 48], [2.1, 48.1]]},
            "Polygon": {"type": "Polygon", "coordinates": [
                [[2, 48], [2.1, 48], [2.1, 48.1], [2, 48]],
            ]},
        }
        with TestClient(self.app) as client:
            response = client.get("/api/geometry-capabilities/ouvrages-particuliers")
            self.assertEqual(response.status_code, 200, response.text)
            contract = response.json()
            self.assertEqual(contract["status"], "compatibility")
            self.assertTrue(contract["required"])
            self.assertEqual(set(contract["allowedTypes"]), set(geometries))
            for shape in contract["allowedTypes"]:
                created = client.post("/api/ouvrages-particuliers", json={
                    "geometry": geometries[shape], "valid": True,
                }, headers={"Geometry-Contract-Version": contract["contractVersion"]})
                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual(created.json()["geometry"]["type"], shape)
                self.assertTrue(self.state(created.json()["properties"]["id"])[2])
            for payload in ({}, {"geometry": None}, {"geometry": {
                "type": "MultiPoint", "coordinates": [[2, 48]],
            }}):
                refused = client.post("/api/ouvrages-particuliers", json=payload)
                self.assertEqual(refused.status_code, 422, refused.text)

    def test_historical_geometry_is_retained_but_cannot_be_made_valid(self):
        for wkt in ("MULTIPOINT((2 48),(3 49))", "POINT EMPTY", None):
            with self.subTest(wkt=wkt):
                identifier = uuid4()
                with psycopg.connect(self.dsn) as connection:
                    connection.execute(
                        "INSERT INTO public.ouvrages_particuliers(id,designation,geometry,valid) "
                        "VALUES(%s,'Historique',ST_GeomFromText(%s,3950),false)",
                        (identifier, wkt),
                    )
                before = self.state(identifier)
                with TestClient(self.app) as client:
                    read = client.get(f"/api/ouvrages-particuliers/{identifier}")
                    self.assertEqual(read.status_code, 200, read.text)
                    updated = client.put(f"/api/ouvrages-particuliers/{identifier}",
                        headers={"If-Match": '"1"'},
                        json={"designation": "Corrigé", "valid": False})
                    self.assertEqual(updated.status_code, 200, updated.text)
                    after = self.state(identifier)
                    self.assertEqual(after[:3], before[:3])
                    self.assertEqual(after[3:], (2, "Corrigé"))
                    refused = client.put(f"/api/ouvrages-particuliers/{identifier}",
                        headers={"If-Match": '"2"'}, json={"valid": True})
                    self.assertEqual(refused.status_code, 422, refused.text)
                    self.assertEqual(self.state(identifier), after)
                    null_geometry = client.put(f"/api/ouvrages-particuliers/{identifier}",
                        headers={"If-Match": '"2"'}, json={"geometry": None})
                    self.assertEqual(null_geometry.status_code, 422, null_geometry.text)
                    self.assertEqual(self.state(identifier), after)

    def test_contract_drift_rejects_new_mutation_but_preserves_receipt_replay(self):
        with TestClient(self.app) as client:
            contract = client.get("/api/geometry-capabilities/ouvrages-particuliers").json()
            created = client.post("/api/ouvrages-particuliers", json={
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()["properties"]["id"]
            before = self.state(identifier)
            refused = client.put(f"/api/ouvrages-particuliers/{identifier}",
                headers={"If-Match": '"1"', "Geometry-Contract-Version": "obsolete"},
                json={"designation": "Refusé"})
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(self.state(identifier), before)
            headers = {"If-Match": '"1"', "Operation-Id": str(uuid4()),
                       "Geometry-Contract-Version": contract["contractVersion"]}
            payload = {"designation": "Confirmé"}
            confirmed = client.put(f"/api/ouvrages-particuliers/{identifier}",
                                   headers=headers, json=payload)
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            replay = client.put(f"/api/ouvrages-particuliers/{identifier}",
                headers={**headers, "Geometry-Contract-Version": "obsolete"}, json=payload)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json(), confirmed.json())
            self.assertEqual(self.state(identifier)[3], 2)

    def test_approved_shape_change_controls_both_metadata_and_mutation(self):
        # A synthetic approved target exercises the mechanism without changing
        # the actual, still pending Ouvrage decision in the master registry.
        candidate = deepcopy(load_geometry_model())
        target = next(item for item in candidate["registry"]["targets"]
                      if item["id"] == "da:concept:ouvrage-particulier")
        target["geometry"].update(status="DECIDE", target_shapes=["POLYGON"],
                                   required_for_valid=True)
        with TestClient(self.app) as client:
            previous = client.get("/api/geometry-capabilities/ouvrages-particuliers").json()
            historical = client.post("/api/ouvrages-particuliers", json={
                "geometry": {"type": "Point", "coordinates": [2, 48]}, "valid": False,
            })
            self.assertEqual(historical.status_code, 201, historical.text)
            identifier = historical.json()["properties"]["id"]
            before = self.state(identifier)
            with patch("digues_webapp.geometry_capabilities.load_geometry_model", return_value=candidate):
                changed = client.get("/api/geometry-capabilities/ouvrages-particuliers").json()
                self.assertEqual(changed["status"], "approved")
                self.assertEqual(changed["allowedTypes"], ["Polygon"])
                self.assertNotEqual(changed["contractVersion"], previous["contractVersion"])
                for valid in (True, False):
                    refused = client.post("/api/ouvrages-particuliers", json={
                        "geometry": {"type": "Point", "coordinates": [2, 48]}, "valid": valid,
                    })
                    self.assertEqual(refused.status_code, 422, refused.text)
                kept = client.put(f"/api/ouvrages-particuliers/{identifier}",
                    headers={"If-Match": '"1"'}, json={"designation": "Conservé"})
                self.assertEqual(kept.status_code, 200, kept.text)
                self.assertEqual(self.state(identifier)[:3], before[:3])
                refused = client.put(f"/api/ouvrages-particuliers/{identifier}",
                    headers={"If-Match": '"2"'}, json={"valid": True})
                self.assertEqual(refused.status_code, 422, refused.text)
                corrected = client.put(f"/api/ouvrages-particuliers/{identifier}",
                    headers={"If-Match": '"2"',
                             "Geometry-Contract-Version": changed["contractVersion"]},
                    json={"valid": True, "geometry": {"type": "Polygon", "coordinates": [
                        [[2, 48], [2.1, 48], [2.1, 48.1], [2, 48]],
                    ]}})
                self.assertEqual(corrected.status_code, 200, corrected.text)
                self.assertEqual(corrected.json()["geometry"]["type"], "Polygon")
