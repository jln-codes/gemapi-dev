"""F8 : neuf CRUD racines et leurs liens dans une même base PostGIS."""
import os
import unittest
from tests.geometry_fixtures import LINE_SQL
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from digues_app.target import PostgreSQLConfig, initialize_schema
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class LevelAIntegrationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        cls.mobilier_type, cls.contact, cls.parcelle = uuid4(), uuid4(), uuid4()
        cls.troncon, cls.digue = uuid4(), uuid4()
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        with psycopg.connect(cls.dsn, autocommit=True) as db:
            for table, code in (
                ("referentiel.types_mobilier", str(cls.mobilier_type)),
                ("referentiel.types_equipement_mesure", "F8-E"),
                ("referentiel.types_reseau_technique", "F8-R"),
                ("referentiel.types_cheminement", "F8-C"),
            ):
                db.execute(f"INSERT INTO {table}(id,code,abrege,libelle,valid) "
                           "VALUES (%s,%s,'F8','F8',true)", (code, code))
            db.execute("INSERT INTO referentiel.types_prestation "
                       "(id,classe_source,designation,valid,standard_present,"
                       "standard_present_fields,standard_null_fields,local_present,"
                       "local_present_fields,local_null_fields) "
                       "VALUES ('F8-P','RefPrestation','F8',true,true,'{}','{}',"
                       "false,'{}','{}')")
            db.execute("INSERT INTO digues(id,libelle,valid) VALUES (%s,'F8',true)",
                       (cls.digue,))
            db.execute("INSERT INTO troncons(id,digue_id,libelle,geometry,valid) "
                       f"VALUES (%s,%s,'F8',{LINE_SQL},true)", (cls.troncon, cls.digue))
            db.execute("INSERT INTO source_v2.parcelles_gestion_vegetation(id,designation,geometry,valid) "
                       "VALUES (%s,'F8',ST_SetSRID(ST_MakeLine("
                       "ST_Point(700000,7000000),ST_Point(700010,7000010)),3950),true)",
                       (cls.parcelle,))
            db.execute("INSERT INTO contacts(id,classe_source,nom,valid) "
                       "VALUES (%s,'Contact','F8',true)", (cls.contact,))
        cls.app = create_app()

        def override():
            with psycopg.connect(cls.dsn, autocommit=True) as db:
                yield db

        cls.app.dependency_overrides[get_connection] = override
        cls.app.dependency_overrides[get_write_connection] = override

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous

    def test_all_roots_share_one_schema_journal_and_observation_parent(self):
        point = {"type": "Point", "coordinates": [2, 48]}
        line = {"type": "LineString", "coordinates": [[2, 48], [3, 49]]}
        payloads = {
            "desordres": {"designation": "F8 désordre", "geometry": point,
                          "troncon_ids": [str(self.troncon)]},
            "mobilier": {"type_id": str(self.mobilier_type), "troncon_id": str(self.troncon),
                         "geometry": point},
            "equipements-mesure": {"type_id": "F8-E", "troncon_id": str(self.troncon),
                                   "geometry": point},
            "reseaux-techniques": {"type_id": "F8-R", "troncon_id": str(self.troncon),
                                   "geometry": line},
            "ouvrages-particuliers": {"troncon_id": str(self.troncon), "geometry": point},
            "cheminements": {"type_cheminement_id": "F8-C",
                             "troncon_ids": [str(self.troncon)],
                             "geometry": line},
            "vegetation": {"troncon_ids": [str(self.troncon)],
                           "famille_vegetation": "ARBRES", "disposition": "ISOLE",
                           "sous_type": "ARBRE_SUR_PIED", "geometry": point},
            "amenagements-hydrauliques": {"troncon_ids": [str(self.troncon)],
                                         "geometry": line, "valid": True},
            "prestations": {"id": str(uuid4()), "date_fin": "2026-09-29",
                            "type_prestation_id": "F8-P", "geometry": point,
                            "troncon_ids": [str(self.troncon)]},
        }
        ids, versions, observations = {}, {}, {}
        with TestClient(self.app) as client:
            for resource, payload in payloads.items():
                response = client.post("/api/"+resource, json=payload)
                self.assertEqual(response.status_code, 201, (resource, response.text))
                ids[resource] = response.json()["properties"]["id"]
                versions[resource] = response.json()["properties"]["version"]
                self.assertGreaterEqual(versions[resource], 1, resource)
                self.assertEqual(response.json()["geometry"]["type"], payload["geometry"]["type"])
            prestation = "/api/prestations/"+ids["prestations"]
            links = {"troncon_ids": [str(self.troncon)],
                     "intervenant_ids": [str(self.contact)],
                     "object_ids": {
                         "desordres": [ids["desordres"]],
                         "mobilier": [ids["mobilier"]],
                         "equipements_mesure": [ids["equipements-mesure"]],
                         "reseaux_techniques": [ids["reseaux-techniques"]],
                         "ouvrages_particuliers": [ids["ouvrages-particuliers"]],
                         "cheminements": [ids["cheminements"]],
                         "vegetation": [ids["vegetation"]],
                         "amenagements_hydrauliques": [ids["amenagements-hydrauliques"]],
                     }}
            changed = client.put(prestation, json=links,
                headers={"If-Match": f'"{versions["prestations"]}"',
                         "Operation-Id": str(uuid4())})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertGreater(changed.json()["properties"]["version"], versions["prestations"])
            versions["prestations"] = changed.json()["properties"]["version"]
            self.assertEqual(len(changed.json()["properties"]["object_ids"]), 9)
            self.assertEqual(client.put(prestation, json={"designation": "conflit"},
                headers={"If-Match": '"1"'}).status_code, 409)
            for resource, identifier in ids.items():
                url = f"/api/{resource}/{identifier}"
                detail = client.get(url)
                self.assertEqual(detail.status_code, 200, (resource, detail.text))
                self.assertEqual(detail.json()["properties"]["version"], versions[resource])
                obs_id = str(uuid4())
                obs = client.post("/api/observations", json={"id": obs_id,
                    "parent": {"resource": resource.replace("-", "_"), "id": identifier},
                    "designation": "F8"},
                    headers={"Operation-Id": str(uuid4())})
                self.assertEqual(obs.status_code, 201, (resource, obs.text))
                observations[resource] = obs_id
                listed = client.get(f"{url}/observations")
                self.assertEqual(listed.status_code, 200, (resource, listed.text))
                parent = listed.json()["parent"]
                self.assertEqual(parent["resource"], resource.replace("-", "_"), resource)
                self.assertEqual(parent["id"], identifier, resource)
                self.assertEqual(listed.json()["observations"][0]["id"], obs_id, resource)
                self.assertEqual(listed.json()["observations"][0]["parent"], parent, resource)
                base_version = versions[resource]
                operation = str(uuid4())
                headers = {"If-Match": f'"{base_version}"', "Operation-Id": operation}
                result = client.put(url, json={"designation": "F8 édité"},
                                    headers=headers)
                self.assertEqual(result.status_code, 200, (resource, result.text))
                self.assertGreater(result.json()["properties"]["version"], versions[resource])
                if resource in {"desordres", "mobilier", "cheminements", "prestations"}:
                    self.assertEqual(client.put(url, json={"designation": "F8 édité"},
                        headers=headers).json(), result.json(), resource)
                    self.assertEqual(client.put(url, json={"designation": "Stale"},
                        headers={"If-Match": f'"{base_version}"'}).status_code,
                        409, resource)
                versions[resource] = result.json()["properties"]["version"]
            with psycopg.connect(self.dsn) as db:
                for obs_id in observations.values():
                    db.execute("INSERT INTO photos(id,observation_id,chemin_source,valid) "
                               "VALUES (%s,%s,'f8.jpg',true)", (uuid4(), obs_id))
                for resource, identifier in ids.items():
                    table = resource.replace("-", "_")
                    self.assertEqual(db.execute(
                        f"SELECT ST_SRID(geometry) FROM {table} WHERE id=%s",
                        (identifier,)).fetchone()[0], 3950)
                    self.assertTrue(db.execute(
                        "SELECT count(*) FROM technique.evenements_ressources WHERE resource=%s "
                        "AND payload->'ids' @> jsonb_build_array(%s::text)",
                        (resource, identifier)).fetchone()[0] >= 2)
                self.assertEqual(db.execute("SELECT count(*) FROM prestations_objets "
                    "WHERE prestation_id=%s", (ids["prestations"],)).fetchone()[0], 8)
                for table in ("source_v2.troncons_historiques_prestations", "source_v2.concordances_prestations_desordres",
                              "source_v2.concordances_prestations_ouvrages", "source_v2.reperages_prestations",
                              "source_v2.appartenances_groupes_prestations"):
                    self.assertEqual(db.execute(f"SELECT count(*) FROM {table} "
                        "WHERE prestation_id=%s", (ids["prestations"],)).fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM observations "
                    "WHERE prestation_id=%s", (ids["prestations"],)).fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM photos WHERE "
                    "observation_id=ANY(%s::uuid[])", (list(observations.values()),))
                    .fetchone()[0], 9)

    def test_troncon_and_ouvrage_hydraulique_observation_parents(self):
        ouvrage = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as db:
            db.execute("INSERT INTO referentiel.types_ouvrage_hydraulique "
                       "(id,code,abrege,libelle,valid) VALUES "
                       "('F8-O','F8-O','F8','F8',true)")
            db.execute("INSERT INTO ouvrages_hydrauliques "
                       "(id,type_id,valid) VALUES (%s,'F8-O',true)", (ouvrage,))
        with TestClient(self.app) as client:
            for resource, identifier in (("troncons", self.troncon),
                                         ("ouvrages_hydrauliques", ouvrage)):
                obs_id, operation = str(uuid4()), str(uuid4())
                body = {"id": obs_id, "parent": {"resource": resource,
                        "id": str(identifier)}, "designation": "F8"}
                response = client.post("/api/observations", json=body,
                                       headers={"Operation-Id": operation})
                self.assertEqual(response.status_code, 201, response.text)
                repeat = client.post("/api/observations", json=body,
                                     headers={"Operation-Id": operation})
                self.assertEqual(repeat.json(), response.json())
                with psycopg.connect(self.dsn) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM observations "
                        "WHERE id=%s", (obs_id,)).fetchone()[0], 1)

    def test_schema_is_complete_and_no_business_delete_is_exposed(self):
        tables = ("desordres", "mobilier", "equipements_mesure",
                  "reseaux_techniques", "ouvrages_particuliers", "cheminements",
                  "vegetation", "amenagements_hydrauliques", "prestations",
                  "observations", "photos", "technique.evenements_ressources", "technique.operations_ressources",
                  "prestations_troncons",
                  "prestations_objets", "prestations_intervenants",
                  )
        with psycopg.connect(self.dsn) as db:
            for table in tables:
                self.assertIsNotNone(db.execute("SELECT to_regclass(%s)",
                    (table if "." in table else "public."+table,)).fetchone()[0], table)
            for table in tables[:9]:
                self.assertTrue(db.execute("SELECT EXISTS(SELECT 1 FROM "
                    "information_schema.columns WHERE table_name=%s "
                    "AND column_name='version')", (table,)).fetchone()[0], table)
            self.assertGreater(db.execute("SELECT count(*) FROM pg_indexes WHERE "
                "schemaname='public' AND tablename = ANY(%s)", (list(tables),))
                .fetchone()[0], len(tables))
            self.assertGreater(db.execute("SELECT count(*) FROM pg_trigger WHERE "
                "tgrelid='public.desordres'::regclass AND NOT tgisinternal")
                .fetchone()[0], 0)
        roots = {"desordres", "mobilier", "equipements-mesure", "reseaux-techniques",
                 "ouvrages-particuliers", "cheminements", "vegetation",
                 "amenagements-hydrauliques", "prestations"}
        for route in self.app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/") and path.split("/")[2] in roots:
                self.assertNotIn("DELETE", route.methods, path)
