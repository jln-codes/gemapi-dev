"""GEO-2 : parcours API sur le cluster PostGIS vierge du runner ciblé."""

import os
import unittest
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from tests.postgres_safety import require_test_dsn


POINT = {"type": "Point", "coordinates": [2.1, 48.2]}
LINE = {"type": "LineString", "coordinates": [[2.1, 48.2], [2.2, 48.3]]}
POLYGON = {"type": "Polygon", "coordinates": [[[2, 48], [3, 48], [3, 49], [2, 48]]]}


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class Geo2PostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = require_test_dsn("DIGUES_OCC_TEST_DSN")
        cls.previous = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.systeme, cls.digue, cls.troncon = uuid4(), uuid4(), uuid4()
        cls.systeme_reperage, cls.borne_debut, cls.borne_fin = uuid4(), uuid4(), uuid4()
        with psycopg.connect(cls.dsn, autocommit=True) as connection:
            for table, identifier in (
                ("types_mobilier", "GEO2-M"),
                ("types_equipement_mesure", "GEO2-E"),
                ("types_cheminement", "GEO2-C"),
            ):
                connection.execute(
                    f"INSERT INTO referentiel.{table}(id,code,abrege,libelle,valid) "
                    "VALUES(%s,%s,%s,%s,true)",
                    (identifier, identifier, identifier, identifier),
                )
            connection.execute("INSERT INTO public.systemes(id,libelle,valid) VALUES(%s,'GEO-2',true)", (cls.systeme,))
            connection.execute(
                "INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) VALUES(%s,%s,'GEO-2',true)",
                (cls.digue, cls.systeme),
            )
            connection.execute(
                "INSERT INTO public.troncons(id,digue_id,libelle,geometry,valid) "
                "VALUES(%s,%s,'Bornage GEO-2',ST_GeomFromText('LINESTRING(0 0,100 0)',3950),true)",
                (cls.troncon, cls.digue),
            )
            connection.execute(
                "INSERT INTO public.systemes_reperage(id,troncon_id,libelle,valid) "
                "VALUES(%s,%s,'Repérage GEO-2',true)",
                (cls.systeme_reperage, cls.troncon),
            )
            for identifier, x, pr in (
                (cls.borne_debut, 0, 1000), (cls.borne_fin, 100, 1100),
            ):
                connection.execute(
                    "INSERT INTO public.bornes_reperage(id,libelle,geometry,valid) "
                    "VALUES(%s,'GEO-2',ST_SetSRID(ST_Point(%s,0),3950),true)",
                    (identifier, x),
                )
                connection.execute(
                    "INSERT INTO public.troncons_bornes(troncon_id,borne_id) VALUES(%s,%s)",
                    (cls.troncon, identifier),
                )
                connection.execute(
                    "INSERT INTO public.systemes_reperage__bornes"
                    "(id,systeme_reperage_id,borne_id,valeur_pr,valid) "
                    "VALUES(%s,%s,%s,%s,true)",
                    (uuid4(), cls.systeme_reperage, identifier, pr),
                )
            connection.execute(
                "UPDATE public.troncons SET systeme_reperage_defaut_id=%s WHERE id=%s",
                (cls.systeme_reperage, cls.troncon),
            )
        cls.app = create_app()

        def override():
            with psycopg.connect(cls.dsn, autocommit=True) as connection:
                yield connection

        cls.app.dependency_overrides[get_connection] = override
        cls.app.dependency_overrides[get_write_connection] = override

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous

    def test_seven_families_post_put_and_rollback(self):
        cases = (
            ("troncons", {"digue_id": str(self.digue), "libelle": "API GEO-2"}, LINE,
             {"libelle": "API GEO-2 modifié"}),
            ("desordres", {"designation": "API GEO-2"}, POINT,
             {"designation": "API GEO-2 modifié"}),
            ("mobilier", {"type_id": "GEO2-M"}, POINT, {"commentaire": "attribut"}),
            ("equipements-mesure", {"type_id": "GEO2-E"}, POINT, {"commentaire": "attribut"}),
            ("ouvrages-particuliers", {"designation": "API GEO-2"}, POLYGON,
             {"commentaire": "attribut"}),
            ("cheminements", {"type_cheminement_id": "GEO2-C"}, LINE,
             {"commentaire": "attribut"}),
            ("vegetation", {"famille_vegetation": "ARBRES", "disposition": "ISOLE",
                            "sous_type": "ARBRE_SUR_PIED"}, POINT,
             {"commentaire": "attribut"}),
        )
        tables = {"equipements-mesure": "equipements_mesure",
                  "ouvrages-particuliers": "ouvrages_particuliers"}
        with TestClient(self.app) as client:
            for route, base, geometry, patch in cases:
                with self.subTest(route=route):
                    for payload in (base, {**base, "geometry": None}):
                        response = client.post(f"/api/{route}", json=payload)
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertIn("géométrie", response.json()["detail"].lower())
                    response = client.post(f"/api/{route}", json={**base, "geometry": geometry})
                    self.assertEqual(response.status_code, 201, response.text)
                    identifier = response.json()["properties"]["id"]
                    self.assertEqual(response.json()["geometry"]["type"], geometry["type"])
                    with psycopg.connect(self.dsn) as connection:
                        persisted = connection.execute(
                            f"SELECT ST_SRID(geometry), GeometryType(geometry), version "
                            f"FROM public.{tables.get(route, route)} WHERE id=%s", (identifier,),
                        ).fetchone()
                    self.assertEqual(persisted[:2], (3950, geometry["type"].upper()))
                    version = response.json()["properties"]["version"]
                    self.assertEqual(version, persisted[2])
                    operation_id = str(uuid4())
                    headers = {"If-Match": f'"{version}"', "Operation-Id": operation_id}
                    with psycopg.connect(self.dsn) as connection:
                        event_count = connection.execute(
                            "SELECT count(*) FROM technique.evenements_ressources",
                        ).fetchone()[0]
                    refused = client.put(f"/api/{route}/{identifier}", json={"geometry": None}, headers=headers)
                    self.assertEqual(refused.status_code, 422, refused.text)
                    with psycopg.connect(self.dsn) as connection:
                        self.assertEqual(connection.execute(
                            f"SELECT version FROM public.{tables.get(route, route)} WHERE id=%s",
                            (identifier,),
                        ).fetchone()[0], version)
                        self.assertIsNone(connection.execute(
                            "SELECT 1 FROM technique.operations_ressources WHERE operation_id=%s",
                            (operation_id,),
                        ).fetchone())
                        self.assertEqual(connection.execute(
                            "SELECT count(*) FROM technique.evenements_ressources",
                        ).fetchone()[0], event_count)
                    changed = client.put(f"/api/{route}/{identifier}", json=patch,
                                         headers={"If-Match": f'"{version}"'})
                    self.assertEqual(changed.status_code, 200, changed.text)
                    self.assertEqual(changed.json()["geometry"]["type"], geometry["type"])
                    next_version = changed.json()["properties"]["version"]
                    replacement = (
                        {"type": "Point", "coordinates": [2.3, 48.4]}
                        if geometry["type"] == "Point" else
                        {"type": "LineString", "coordinates": [[2.3, 48.4], [2.4, 48.5]]}
                        if geometry["type"] == "LineString" else
                        {"type": "Polygon", "coordinates": [[[2.2, 48.2], [2.8, 48.2],
                                                               [2.8, 48.8], [2.2, 48.2]]]}
                    )
                    replacement_patch = (
                        {"longitude": 2.3, "latitude": 48.4}
                        if route == "desordres" else
                        {"libelle": "API GEO-2 modifié", "geometry": replacement}
                        if route == "troncons" else {"geometry": replacement}
                    )
                    replaced = client.put(f"/api/{route}/{identifier}", json=replacement_patch,
                                          headers={"If-Match": f'"{next_version}"'})
                    self.assertEqual(replaced.status_code, 200, replaced.text)
                    self.assertEqual(replaced.json()["geometry"]["type"], geometry["type"])
                    stale = client.put(f"/api/{route}/{identifier}", json=patch,
                                       headers={"If-Match": f'"{version}"'})
                    self.assertEqual(stale.status_code, 409, stale.text)

    def test_vegetation_taxonomy_and_invalid_polygon(self):
        valid = (
            ("ARBRES", "ARBRE_SUR_PIED"), ("ARBRES", "SOUCHE"),
            ("BUISSONS_ARBUSTES", None), ("HERBACEES", None),
        )
        with TestClient(self.app) as client:
            for family, subtype in valid:
                for geometry in (POINT, LINE, POLYGON):
                    with self.subTest(family=family, subtype=subtype, geometry=geometry["type"]):
                        payload = {"famille_vegetation": family, "disposition": "ISOLE",
                                   "geometry": geometry}
                        if subtype is not None:
                            payload["sous_type"] = subtype
                        response = client.post("/api/vegetation", json=payload)
                        self.assertEqual(response.status_code, 201, response.text)
                        self.assertEqual(response.json()["geometry"]["type"], geometry["type"])
            for family, subtype in (("ARBRES", None), ("HERBACEES", "SOUCHE"),
                                    ("BUISSONS_ARBUSTES", "ARBRE_SUR_PIED")):
                with self.subTest(family=family, subtype=subtype):
                    response = client.post("/api/vegetation", json={
                        "famille_vegetation": family, "disposition": "ISOLE",
                        "sous_type": subtype, "geometry": POINT,
                    })
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn("ck_", response.text)
            bowtie = {"type": "Polygon", "coordinates": [[[2, 48], [3, 49], [3, 48], [2, 49], [2, 48]]]}
            response = client.post("/api/ouvrages-particuliers", json={"geometry": bowtie})
            self.assertEqual(response.status_code, 422, response.text)

    def test_desordre_location_authorities_persist_geometry(self):
        reperage = {
            "borne_debut_id": str(self.borne_debut), "distance_debut_m": 10,
            "position_debut_relative": "APRES_BORNE",
        }
        cases = (
            ({"geometry": POINT}, "POINT"),
            ({"geometry": LINE}, "LINESTRING"),
            ({"geometry": POLYGON}, "POLYGON"),
            ({"x": 10, "y": 20}, "POINT"),
            ({"longitude": 2.1, "latitude": 48.2}, "POINT"),
            ({"line_endpoints": {"crs": "EPSG:4326", "debut": [2.1, 48.2],
                                 "fin": [2.2, 48.3]}}, "LINESTRING"),
            ({"geometry_type": "Point", "troncon_ids": [str(self.troncon)],
              "reperage": reperage}, "POINT"),
            ({"geometry_type": "LineString", "troncon_ids": [str(self.troncon)],
              "reperage": {**reperage, "borne_fin_id": str(self.borne_fin),
                           "distance_fin_m": 10, "position_fin_relative": "AVANT_BORNE"}}, "LINESTRING"),
        )
        with TestClient(self.app) as client:
            for authority, expected in cases:
                with self.subTest(authority=authority):
                    response = client.post("/api/desordres", json=authority)
                    self.assertEqual(response.status_code, 201, response.text)
                    identifier = response.json()["properties"]["id"]
                    with psycopg.connect(self.dsn) as connection:
                        stored = connection.execute(
                            "SELECT GeometryType(geometry), ST_SRID(geometry), "
                            "public.geometrie_metier_simple_valide(geometry) "
                            "FROM public.desordres WHERE id=%s", (identifier,),
                        ).fetchone()
                    self.assertEqual(stored, (expected, 3950, True))

    def test_desordre_specialized_edits_and_degenerate_bornage(self):
        with TestClient(self.app) as client:
            created = client.post("/api/desordres", json={
                "geometry": LINE, "troncon_ids": [str(self.troncon)],
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()["properties"]["id"]
            version = created.json()["properties"]["version"]
            changed = client.put(f"/api/desordres/{identifier}/geometry", json={
                "geometry": {"type": "LineString", "coordinates": [[2.2, 48.2], [2.3, 48.3]]},
            }, headers={"If-Match": f'"{version}"'})
            self.assertEqual(changed.status_code, 200, changed.text)
            version = changed.json()["properties"]["version"]
            refused = client.put(f"/api/desordres/{identifier}/geometry", json={
                "geometry": {"type": "LineString", "coordinates": [[2, 48], [2, 48]]},
            }, headers={"If-Match": f'"{version}"'})
            self.assertEqual(refused.status_code, 422, refused.text)
            endpoints = client.put(f"/api/desordres/{identifier}/endpoints", json={
                "crs": "EPSG:4326", "debut": [2.25, 48.25], "fin": [2.35, 48.35],
            }, headers={"If-Match": f'"{version}"'})
            self.assertEqual(endpoints.status_code, 200, endpoints.text)
            version = endpoints.json()["properties"]["version"]
            degenerate = client.put(f"/api/desordres/{identifier}/reperage", json={
                "borne_debut_id": str(self.borne_debut), "distance_debut_m": 10,
                "position_debut_relative": "APRES_BORNE",
                "borne_fin_id": str(self.borne_debut), "distance_fin_m": 10,
                "position_fin_relative": "APRES_BORNE",
            }, headers={"If-Match": f'"{version}"'})
            self.assertIn(degenerate.status_code, (400, 422), degenerate.text)
            self.assertNotIn("ck_geometrie", degenerate.text)
            with psycopg.connect(self.dsn) as connection:
                stored_version = connection.execute(
                    "SELECT version FROM public.desordres WHERE id=%s", (identifier,),
                ).fetchone()[0]
            self.assertEqual(stored_version, version)
            valid_reperage = client.put(f"/api/desordres/{identifier}/reperage", json={
                "borne_debut_id": str(self.borne_debut), "distance_debut_m": 10,
                "position_debut_relative": "APRES_BORNE",
                "borne_fin_id": str(self.borne_fin), "distance_fin_m": 10,
                "position_fin_relative": "AVANT_BORNE",
            }, headers={"If-Match": f'"{version}"'})
            self.assertEqual(valid_reperage.status_code, 200, valid_reperage.text)
            self.assertEqual(valid_reperage.json()["geometry"]["type"], "LineString")


if __name__ == "__main__":
    unittest.main()
