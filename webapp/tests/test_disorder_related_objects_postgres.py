"""Consultation inverse Objet–Désordre sur PostgreSQL/PostGIS jetable."""

import os
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.app import create_app
from digues_webapp.database import get_connection
from webapp.tests.test_equipement_mesure_postgres import http_client


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class DisorderRelatedObjectsPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.target, cls.empty, cls.missing = uuid4(), uuid4(), uuid4()
        cls.systeme, cls.digue, cls.troncon = uuid4(), uuid4(), uuid4()
        cls.parents = {
            "mobilier": [uuid4(), uuid4()],
            "equipements_mesure": [uuid4()],
            "reseaux_techniques": [uuid4()],
            "ouvrages_particuliers": [uuid4()],
            "cheminements": [uuid4()],
            "vegetation": [uuid4()],
            "amenagements_hydrauliques": [uuid4()],
        }
        token = uuid4().hex
        cls.references = {
            "mobilier": f"I-{token}",
            "equipement": f"E-{token}",
            "reseau": f"R-{token}",
            "cheminement": f"C-{token}",
        }
        with psycopg.connect(cls.dsn, autocommit=True) as connection:
            for table, identifier in (
                ("types_mobilier", cls.references["mobilier"]),
                ("types_equipement_mesure", cls.references["equipement"]),
                ("types_reseau_technique", cls.references["reseau"]),
                ("types_cheminement", cls.references["cheminement"]),
            ):
                connection.execute(sql.SQL(
                    "INSERT INTO referentiel.{}(id,code,abrege,libelle,valid) "
                    "VALUES(%s,%s,'5I','Type 5I',true)"
                ).format(sql.Identifier(table)), (identifier, identifier))
            connection.execute(
                "INSERT INTO systemes(id,libelle,valid) VALUES(%s,'SE 5I',true)",
                (cls.systeme,),
            )
            connection.execute(
                "INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) "
                "VALUES(%s,%s,'Digue 5I',true)", (cls.digue, cls.systeme),
            )
            connection.execute(
                "INSERT INTO troncons(id,digue_id,libelle,geometry,valid) "
                "VALUES(%s,%s,'Tronçon 5I',ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)",
                (cls.troncon, cls.digue),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO desordres(id,designation,geometry,valid) "
                    "VALUES(%s,%s,ST_SetSRID(ST_Point(1,1),3950),true)",
                    [(cls.target, "Désordre cible 5I"),
                     (cls.empty, "Désordre vide 5I")],
                )
            for identifier in cls.parents["mobilier"]:
                connection.execute(
                    "INSERT INTO mobilier(id,type_id,designation,geometry,valid) "
                    "VALUES(%s,%s,'Libellé jumeau',ST_SetSRID(ST_Point(1,1),3950),false)",
                    (identifier, cls.references["mobilier"]),
                )
            connection.execute(
                "INSERT INTO equipements_mesure(id,type_id,designation,geometry,valid) "
                "VALUES(%s,%s,'Équipement 5I',ST_SetSRID(ST_Point(1,1),3950),true)",
                (cls.parents["equipements_mesure"][0], cls.references["equipement"]),
            )
            connection.execute(
                "INSERT INTO reseaux_techniques(id,type_id,designation,valid) "
                "VALUES(%s,%s,'Réseau non localisé 5I',true)",
                (cls.parents["reseaux_techniques"][0], cls.references["reseau"]),
            )
            connection.execute(
                "INSERT INTO ouvrages_particuliers(id,designation,geometry,valid) "
                "VALUES(%s,'Ouvrage 5I',ST_SetSRID(ST_Point(1,1),3950),true)",
                (cls.parents["ouvrages_particuliers"][0],),
            )
            connection.execute(
                "INSERT INTO cheminements(id,type_cheminement_id,designation,geometry,valid) "
                "VALUES(%s,%s,'Cheminement 5I',"
                "ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)",
                (cls.parents["cheminements"][0], cls.references["cheminement"]),
            )
            connection.execute(
                "INSERT INTO vegetation(id,famille_vegetation,disposition,sous_type,designation,geometry,valid) "
                "VALUES(%s,'ARBRES','ISOLE','ARBRE_SUR_PIED','Végétation 5I',"
                "ST_SetSRID(ST_Point(1,1),3950),true)",
                (cls.parents["vegetation"][0],),
            )
            connection.execute(
                "INSERT INTO amenagements_hydrauliques(id,designation,valid) "
                "VALUES(%s,'AH non localisé 5I',true)",
                (cls.parents["amenagements_hydrauliques"][0],),
            )
            connection.execute(
                "INSERT INTO desordres__troncons(desordre_id,troncon_id) VALUES(%s,%s)",
                (cls.target, cls.troncon),
            )
            links = (
                ("mobilier__desordres", "mobilier_id", cls.parents["mobilier"]),
                ("equipements_mesure__desordres", "equipement_mesure_id", cls.parents["equipements_mesure"]),
                ("reseaux_techniques__desordres", "reseau_technique_id", cls.parents["reseaux_techniques"]),
                ("ouvrages_particuliers__desordres", "ouvrage_particulier_id", cls.parents["ouvrages_particuliers"]),
                ("cheminements__desordres", "cheminement_id", cls.parents["cheminements"]),
                ("vegetation__desordres", "vegetation_id", cls.parents["vegetation"]),
                ("amenagements_hydrauliques__desordres", "amenagement_hydraulique_id", cls.parents["amenagements_hydrauliques"]),
            )
            for table, parent_column, identifiers in links:
                for identifier in identifiers:
                    connection.execute(sql.SQL(
                        "INSERT INTO public.{}({},desordre_id) VALUES(%s,%s)"
                    ).format(sql.Identifier(table), sql.Identifier(parent_column)),
                    (identifier, cls.target))

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

    def test_empty_missing_validation_and_projection_without_geometry(self):
        with http_client(self.app) as client:
            empty = client.get(f"/api/desordres/{self.empty}/objets-associes")
            missing = client.get(f"/api/desordres/{self.missing}/objets-associes")
            malformed_id = client.get("/api/desordres/not-an-id/objets-associes")
            malformed_cursor = client.get(
                f"/api/desordres/{self.target}/objets-associes", params={"cursor": "***"},
            )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json(), {"items": [], "next_cursor": None, "has_more": False})
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(malformed_id.status_code, 422)
        self.assertEqual(malformed_cursor.status_code, 400)

    def test_global_cursor_covers_eight_categories_without_duplication(self):
        items = []
        cursor = None
        with http_client(self.app) as client:
            while True:
                parameters = {"limit": 3}
                if cursor:
                    parameters["cursor"] = cursor
                response = client.get(
                    f"/api/desordres/{self.target}/objets-associes", params=parameters,
                )
                self.assertEqual(response.status_code, 200, response.text)
                page = response.json()
                self.assertLessEqual(len(page["items"]), 3)
                items.extend(page["items"])
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break
                self.assertIsNotNone(cursor)
        self.assertEqual(len(items), 9)
        self.assertEqual(len({(item["resource"], item["id"]) for item in items}), 9)
        self.assertEqual({item["resource"] for item in items}, {
            "troncons", "mobilier", "equipements_mesure", "reseaux_techniques",
            "ouvrages_particuliers", "cheminements", "vegetation",
            "amenagements_hydrauliques",
        })
        self.assertTrue(all("geometry" not in item for item in items))
        mobilier = [item for item in items if item["resource"] == "mobilier"]
        self.assertEqual(
            [item["id"] for item in mobilier],
            sorted(str(identifier) for identifier in self.parents["mobilier"]),
        )
        self.assertTrue(all(item["valid"] is False for item in mobilier))
        self.assertEqual(
            next(item for item in items if item["resource"] == "reseaux_techniques")["label"],
            "Réseau non localisé 5I",
        )

    def test_canonical_inverse_indexes_and_measured_access_path(self):
        expected = {
            "ix_cheminements_desordres__desordre",
            "ix_equipements_mesure_desordres__desordre",
            "ix_mobilier_desordres__desordre",
            "ix_ouvrages_particuliers_desordres__desordre",
            "ix_reseaux_techniques_desordres__desordre",
            "ix_vegetation_desordres__desordre",
        }
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            found = {row[0] for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
                "AND indexname=ANY(%s)", (list(expected),),
            )}
            self.assertEqual(found, expected)
            connection.execute(
                "CREATE TEMP TABLE inverse_index_probe("
                "parent_id uuid NOT NULL, desordre_id uuid NOT NULL,"
                "PRIMARY KEY(parent_id,desordre_id))"
            )
            connection.execute(
                "INSERT INTO inverse_index_probe(parent_id,desordre_id) "
                "SELECT (md5('p'||g::text)::uuid), "
                "(md5('d'||(g%100)::text)::uuid) FROM generate_series(1,50000) g"
            )
            connection.execute("ANALYZE inverse_index_probe")
            target = connection.execute(
                "SELECT desordre_id FROM inverse_index_probe LIMIT 1"
            ).fetchone()[0]
            before = connection.execute(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "
                "SELECT parent_id FROM inverse_index_probe WHERE desordre_id=%s",
                (target,),
            ).fetchone()[0][0]
            connection.execute(
                "CREATE INDEX inverse_index_probe_disorder ON inverse_index_probe(desordre_id)"
            )
            connection.execute("ANALYZE inverse_index_probe")
            after = connection.execute(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "
                "SELECT parent_id FROM inverse_index_probe WHERE desordre_id=%s",
                (target,),
            ).fetchone()[0][0]
        self.assertEqual(before["Plan"]["Node Type"], "Seq Scan")
        self.assertIn(after["Plan"]["Node Type"], {"Bitmap Heap Scan", "Index Scan"})
        self.assertLess(after["Execution Time"], before["Execution Time"])

    def test_uuid_existing_only_in_another_database_is_not_exposed(self):
        admin_dsn = os.getenv("DIGUES_MULTIBASE_TEST_ADMIN_DSN")
        if not admin_dsn:
            self.skipTest("DSN d’administration multibase jetable non configurée")
        database_name = f"digues_multi_test_inverse_{uuid4().hex[:12]}"
        params = conninfo_to_dict(admin_dsn)
        other_dsn = make_conninfo(
            **{**params, "dbname": database_name},
        )
        only_elsewhere = uuid4()
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(database_name),
            ))
        try:
            with psycopg.connect(other_dsn, autocommit=True) as connection:
                connection.execute("CREATE EXTENSION postgis")
                connection.execute("CREATE EXTENSION pgcrypto")
            initialize_schema(PostgreSQLConfig(dsn=other_dsn), business_crs=3950)
            with psycopg.connect(other_dsn, autocommit=True) as connection:
                connection.execute(
                    "INSERT INTO desordres(id,designation,geometry,valid) "
                    "VALUES(%s,'Autre base',ST_SetSRID(ST_Point(1,1),3950),true)",
                    (only_elsewhere,),
                )
            with http_client(self.app) as client:
                response = client.get(
                    f"/api/desordres/{only_elsewhere}/objets-associes"
                )
            self.assertEqual(response.status_code, 404, response.text)
        finally:
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=%s AND pid<>pg_backend_pid()",
                    (database_name,),
                )
                admin.execute(sql.SQL("DROP DATABASE {}").format(
                    sql.Identifier(database_name),
                ))


if __name__ == "__main__":
    unittest.main()
