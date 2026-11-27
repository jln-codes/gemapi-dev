"""Contrat PostgreSQL du lot 5G pour les relations Objet–Désordre."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.errors import ForeignKeyViolation
from fastapi.testclient import TestClient

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from webapp.tests.test_equipement_mesure_postgres import http_client


@unittest.skipUnless(
    os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré",
)
class ObjectDisorderRelations5GPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_legacy = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.disorders = [uuid4(), uuid4(), uuid4(), uuid4()]
        cls.type_ids = {
            "mobilier": f"M5G-{uuid4()}",
            "equipements-mesure": f"E5G-{uuid4()}",
            "reseaux-techniques": f"R5G-{uuid4()}",
        }
        with psycopg.connect(cls.dsn, autocommit=True) as connection:
            for table, resource in (
                ("types_mobilier", "mobilier"),
                ("types_equipement_mesure", "equipements-mesure"),
                ("types_reseau_technique", "reseaux-techniques"),
            ):
                identifier = cls.type_ids[resource]
                connection.execute(sql.SQL(
                    "INSERT INTO referentiel.{}"
                    "(id,code,abrege,libelle,valid) VALUES(%s,%s,%s,%s,true)"
                ).format(sql.Identifier(table)), (
                    identifier, identifier, "5G", f"Type {resource} 5G",
                ))
            for index, identifier in enumerate(cls.disorders):
                connection.execute(
                    "INSERT INTO public.desordres"
                    "(id,designation,geometry,valid) VALUES(%s,%s,"
                    "ST_SetSRID(ST_Point(%s,%s),3950),%s)",
                    (identifier, f"Désordre 5G {index}", index + 1,
                     index + 1, index != 1),
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

    @classmethod
    def _n_to_n_cases(cls):
        point = {"type": "Point", "coordinates": [2.1, 48.2]}
        return (
            {
                "resource": "mobilier", "table": "mobilier",
                "link": "mobilier__desordres", "parent": "mobilier_id",
                "payload": {"type_id": cls.type_ids["mobilier"],
                            "geometry": point},
                "changed_geometry": {"type": "Point", "coordinates": [2.2, 48.3]},
            },
            {
                "resource": "equipements-mesure", "table": "equipements_mesure",
                "link": "equipements_mesure__desordres",
                "parent": "equipement_mesure_id",
                "payload": {"type_id": cls.type_ids["equipements-mesure"],
                            "geometry": point},
                "changed_geometry": {"type": "Point", "coordinates": [2.3, 48.4]},
            },
            {
                "resource": "reseaux-techniques", "table": "reseaux_techniques",
                "link": "reseaux_techniques__desordres",
                "parent": "reseau_technique_id",
                "payload": {"type_id": cls.type_ids["reseaux-techniques"],
                            "geometry": {"type": "LineString", "coordinates": [
                                [2.1, 48.2], [2.2, 48.3],
                            ]}},
                "changed_geometry": {"type": "Point", "coordinates": [2.4, 48.5]},
            },
            {
                "resource": "vegetation", "table": "vegetation",
                "link": "vegetation__desordres", "parent": "vegetation_id",
                "payload": {"famille_vegetation": "ARBRES", "disposition": "ISOLE",
                            "sous_type": "ARBRE_SUR_PIED", "geometry": point},
                "changed_geometry": {"type": "Point", "coordinates": [2.5, 48.6]},
            },
        )

    def _link_rows(self, case, identifier):
        with psycopg.connect(self.dsn) as connection:
            return {
                row[0]: (row[1], row[2])
                for row in connection.execute(sql.SQL(
                    "SELECT desordre_id,xmin::text,ctid::text FROM public.{} "
                    "WHERE {}=%s ORDER BY desordre_id"
                ).format(
                    sql.Identifier(case["link"]), sql.Identifier(case["parent"]),
                ), (identifier,)).fetchall()
            }

    def _source_v2_counts(self):
        with psycopg.connect(self.dsn) as connection:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='source_v2' "
                "ORDER BY tablename"
            ).fetchall()
            return tuple(
                (table, connection.execute(sql.SQL(
                    "SELECT count(*) FROM source_v2.{}"
                ).format(sql.Identifier(table))).fetchone()[0])
                for (table,) in tables
            )

    def test_four_n_to_n_resources_share_crud_and_differential_contract(self):
        source_before = self._source_v2_counts()
        with TestClient(self.app) as client:
            for case in self._n_to_n_cases():
                with self.subTest(resource=case["resource"]):
                    endpoint = f"/api/{case['resource']}"
                    without = client.post(endpoint, json=case["payload"])
                    self.assertEqual(without.status_code, 201, without.text)
                    self.assertEqual(
                        without.json()["properties"]["desordre_ids"], [],
                    )
                    with psycopg.connect(self.dsn) as connection:
                        parent_count = connection.execute(sql.SQL(
                            "SELECT count(*) FROM public.{}"
                        ).format(sql.Identifier(case["table"]))).fetchone()[0]
                    rejected_create = client.post(endpoint, json={
                        **case["payload"], "desordre_ids": [str(uuid4())],
                    })
                    self.assertEqual(
                        rejected_create.status_code, 422, rejected_create.text,
                    )
                    with psycopg.connect(self.dsn) as connection:
                        persisted_count = connection.execute(sql.SQL(
                            "SELECT count(*) FROM public.{}"
                        ).format(sql.Identifier(case["table"]))).fetchone()[0]
                    self.assertEqual(persisted_count, parent_count)

                    created = client.post(endpoint, json={
                        **case["payload"],
                        "desordre_ids": [str(self.disorders[0]),
                                         str(self.disorders[1])],
                    })
                    self.assertEqual(created.status_code, 201, created.text)
                    body = created.json()
                    identifier = UUID(body["properties"]["id"])
                    self.assertEqual(
                        set(body["properties"]["desordre_ids"]),
                        {str(self.disorders[0]), str(self.disorders[1])},
                    )
                    self.assertFalse(body["properties"].get("valid") is None)
                    listed = client.get(endpoint)
                    self.assertEqual(listed.status_code, 200, listed.text)
                    selected = next(
                        item for item in listed.json()["features"]
                        if item["properties"]["id"] == str(identifier)
                    )
                    self.assertNotIn("desordre_ids", selected["properties"])

                    initial = self._link_rows(case, identifier)
                    operation = str(uuid4())
                    attribute = client.put(
                        f"{endpoint}/{identifier}",
                        json={"commentaire": "Attribut 5G"},
                        headers={"If-Match": '"1"', "Operation-Id": operation},
                    )
                    self.assertEqual(attribute.status_code, 200, attribute.text)
                    self.assertEqual(attribute.json()["properties"]["version"], 2)
                    self.assertEqual(
                        attribute.json()["mutation"]["resource"], case["resource"],
                    )
                    self.assertEqual(self._link_rows(case, identifier), initial)
                    with psycopg.connect(self.dsn) as connection:
                        receipt = connection.execute(
                            "SELECT event_id::text FROM "
                            "technique.operations_ressources WHERE operation_id=%s",
                            (operation,),
                        ).fetchone()
                        self.assertEqual(
                            receipt[0], attribute.json()["mutation"]["eventId"],
                        )
                        self.assertEqual(connection.execute(
                            "SELECT count(*) FROM technique.evenements_ressources "
                            "WHERE event_id=%s", (receipt[0],),
                        ).fetchone()[0], 1)
                    replay = client.put(
                        f"{endpoint}/{identifier}",
                        json={"commentaire": "Attribut 5G"},
                        headers={"If-Match": '"1"', "Operation-Id": operation},
                    )
                    self.assertEqual(replay.json(), attribute.json())

                    changed = client.put(
                        f"{endpoint}/{identifier}",
                        json={
                            "geometry": case["changed_geometry"],
                            "desordre_ids": [str(self.disorders[1]),
                                             str(self.disorders[2])],
                        },
                        headers={"If-Match": '"2"',
                                 "Operation-Id": str(uuid4())},
                    )
                    self.assertEqual(changed.status_code, 200, changed.text)
                    self.assertEqual(changed.json()["properties"]["version"], 3)
                    changed_rows = self._link_rows(case, identifier)
                    self.assertEqual(set(changed_rows), {
                        self.disorders[1], self.disorders[2],
                    })
                    self.assertEqual(
                        changed_rows[self.disorders[1]], initial[self.disorders[1]],
                    )

                    identical = client.put(
                        f"{endpoint}/{identifier}",
                        json={"desordre_ids": [str(self.disorders[2]),
                                               str(self.disorders[1])]},
                        headers={"If-Match": '"3"',
                                 "Operation-Id": str(uuid4())},
                    )
                    self.assertEqual(identical.status_code, 200, identical.text)
                    self.assertEqual(identical.json()["properties"]["version"], 4)
                    self.assertEqual(self._link_rows(case, identifier), changed_rows)

                    for invalid in (
                        {"desordre_ids": [str(uuid4())]},
                        {"desordre_ids": None},
                        {"desordre_ids": [str(self.disorders[1])] * 2},
                    ):
                        rejected = client.put(
                            f"{endpoint}/{identifier}", json=invalid,
                            headers={"If-Match": '"4"',
                                     "Operation-Id": str(uuid4())},
                        )
                        self.assertEqual(rejected.status_code, 422, rejected.text)
                    current = client.get(f"{endpoint}/{identifier}").json()
                    self.assertEqual(current["properties"]["version"], 4)
                    self.assertEqual(self._link_rows(case, identifier), changed_rows)

                    emptied = client.put(
                        f"{endpoint}/{identifier}", json={"desordre_ids": []},
                        headers={"If-Match": '"4"',
                                 "Operation-Id": str(uuid4())},
                    )
                    self.assertEqual(emptied.status_code, 200, emptied.text)
                    self.assertEqual(emptied.json()["properties"]["desordre_ids"], [])
                    self.assertEqual(self._link_rows(case, identifier), {})
        self.assertEqual(self._source_v2_counts(), source_before)

    def test_amenagement_one_to_many_refuses_reassignment_and_is_differential(self):
        endpoint = "/api/amenagements-hydrauliques"
        polygon = {"type": "Polygon", "coordinates": [[
            [2.0, 48.0], [2.2, 48.0], [2.2, 48.2], [2.0, 48.0],
        ]]}
        with TestClient(self.app) as client:
            without = client.post(endpoint, json={"designation": "AH vide"})
            self.assertEqual(without.status_code, 201, without.text)
            self.assertEqual(without.json()["properties"]["desordre_ids"], [])

            created = client.post(endpoint, json={
                "designation": "AH 5G", "geometry": polygon,
                "desordre_ids": [str(self.disorders[0]), str(self.disorders[1])],
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = UUID(created.json()["properties"]["id"])
            case = {
                "link": "amenagements_hydrauliques__desordres",
                "parent": "amenagement_hydraulique_id",
            }
            initial = self._link_rows(case, identifier)
            self.assertEqual(set(initial), {self.disorders[0], self.disorders[1]})

            refused = client.post(endpoint, json={
                "designation": "Réaffectation interdite",
                "desordre_ids": [str(self.disorders[0])],
            })
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(
                refused.json()["detail"]["code"], "disorder-already-associated",
            )
            with psycopg.connect(self.dsn) as connection:
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM public.amenagements_hydrauliques "
                    "WHERE designation='Réaffectation interdite'"
                ).fetchone()[0], 0)
            self.assertEqual(self._link_rows(case, identifier), initial)

            attribute_operation = str(uuid4())
            attribute = client.put(
                f"{endpoint}/{identifier}", json={"commentaire": "Conservé"},
                headers={"If-Match": '"1"',
                         "Operation-Id": attribute_operation},
            )
            self.assertEqual(attribute.status_code, 200, attribute.text)
            self.assertEqual(self._link_rows(case, identifier), initial)
            self.assertEqual(
                attribute.json()["mutation"]["resource"],
                "amenagements-hydrauliques",
            )
            with psycopg.connect(self.dsn) as connection:
                receipt = connection.execute(
                    "SELECT event_id::text FROM technique.operations_ressources "
                    "WHERE operation_id=%s", (attribute_operation,),
                ).fetchone()
                self.assertEqual(
                    receipt[0], attribute.json()["mutation"]["eventId"],
                )
            replay = client.put(
                f"{endpoint}/{identifier}", json={"commentaire": "Conservé"},
                headers={"If-Match": '"1"',
                         "Operation-Id": attribute_operation},
            )
            self.assertEqual(replay.json(), attribute.json())

            changed = client.put(
                f"{endpoint}/{identifier}", json={
                    "geometry": {"type": "Point", "coordinates": [2.5, 48.5]},
                    "desordre_ids": [str(self.disorders[1])],
                },
                headers={"If-Match": '"2"', "Operation-Id": str(uuid4())},
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()["geometry"]["type"], "Point")
            changed_rows = self._link_rows(case, identifier)
            self.assertEqual(set(changed_rows), {self.disorders[1]})
            self.assertEqual(
                changed_rows[self.disorders[1]], initial[self.disorders[1]],
            )

            identical = client.put(
                f"{endpoint}/{identifier}",
                json={"desordre_ids": [str(self.disorders[1])]},
                headers={"If-Match": '"3"', "Operation-Id": str(uuid4())},
            )
            self.assertEqual(identical.status_code, 200, identical.text)
            self.assertEqual(self._link_rows(case, identifier), changed_rows)
            emptied = client.put(
                f"{endpoint}/{identifier}", json={"desordre_ids": []},
                headers={"If-Match": '"4"', "Operation-Id": str(uuid4())},
            )
            self.assertEqual(emptied.status_code, 200, emptied.text)
            self.assertEqual(emptied.json()["properties"]["desordre_ids"], [])

    def test_amenagement_concurrent_claim_has_one_winner(self):
        endpoint = "/api/amenagements-hydrauliques"
        with http_client(self.app) as client:
            parents = []
            for designation in ("Concurrent A", "Concurrent B"):
                created = client.post(endpoint, json={"designation": designation})
                self.assertEqual(created.status_code, 201, created.text)
                parents.append(created.json()["properties"]["id"])
            barrier = Barrier(2)
            operations = [str(uuid4()), str(uuid4())]

            def attach(item):
                index, parent = item
                barrier.wait(timeout=5)
                return client.put(
                    f"{endpoint}/{parent}",
                    json={"desordre_ids": [str(self.disorders[3])]},
                    headers={"If-Match": '"1"',
                             "Operation-Id": operations[index]},
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(attach, enumerate(parents)))
            self.assertEqual(sorted(row.status_code for row in responses), [200, 409])

        with psycopg.connect(self.dsn) as connection:
            rows = connection.execute(
                "SELECT amenagement_hydraulique_id FROM "
                "public.amenagements_hydrauliques__desordres "
                "WHERE desordre_id=%s", (self.disorders[3],),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            versions = dict(connection.execute(
                "SELECT id,version FROM public.amenagements_hydrauliques "
                "WHERE id=ANY(%s::uuid[])", (parents,),
            ).fetchall())
            self.assertEqual(sorted(versions.values()), [1, 2])
            receipts = connection.execute(
                "SELECT count(*) FROM technique.operations_ressources "
                "WHERE operation_id=ANY(%s::uuid[])", (operations,),
            ).fetchone()[0]
            self.assertEqual(receipts, 1)
            events = sum(
                connection.execute(
                    "SELECT count(*) FROM technique.evenements_ressources "
                    "WHERE resource='amenagements-hydrauliques' AND "
                    "payload->'ids' @> jsonb_build_array(%s::text)",
                    (parent,),
                ).fetchone()[0]
                for parent in parents
            )
            self.assertEqual(events, 3)

    def test_combined_geometry_relation_failure_rolls_back_everything(self):
        case = self._n_to_n_cases()[0]
        with TestClient(self.app) as client:
            created = client.post("/api/mobilier", json={
                **case["payload"], "desordre_ids": [str(self.disorders[0])],
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = UUID(created.json()["properties"]["id"])
            operation = uuid4()
            trigger = f"reject_5g_{uuid4().hex}"
            function = f"reject_5g_{uuid4().hex}"
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                before = connection.execute(
                    "SELECT commentaire,version,ST_AsEWKB(geometry) "
                    "FROM public.mobilier WHERE id=%s", (identifier,),
                ).fetchone()
                events_before = connection.execute(
                    "SELECT count(*) FROM technique.evenements_ressources"
                ).fetchone()[0]
                connection.execute(sql.SQL(
                    "CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN IF NEW.desordre_id={}::uuid THEN "
                    "RAISE EXCEPTION 'rollback 5G'; END IF; RETURN NEW; END $$"
                ).format(sql.Identifier(function),
                         sql.Literal(str(self.disorders[2]))))
                connection.execute(sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON public.mobilier__desordres "
                    "FOR EACH ROW EXECUTE FUNCTION public.{}()"
                ).format(sql.Identifier(trigger), sql.Identifier(function)))
            try:
                failed = client.put(
                    f"/api/mobilier/{identifier}", json={
                        "commentaire": "Ne doit pas persister",
                        "geometry": case["changed_geometry"],
                        "desordre_ids": [str(self.disorders[0]),
                                         str(self.disorders[2])],
                    },
                    headers={"If-Match": '"1"',
                             "Operation-Id": str(operation)},
                )
                self.assertEqual(failed.status_code, 503, failed.text)
            finally:
                with psycopg.connect(self.dsn, autocommit=True) as connection:
                    connection.execute(sql.SQL(
                        "DROP TRIGGER {} ON public.mobilier__desordres"
                    ).format(sql.Identifier(trigger)))
                    connection.execute(sql.SQL(
                        "DROP FUNCTION public.{}()"
                    ).format(sql.Identifier(function)))

            invalid_geometry = client.put(
                f"/api/mobilier/{identifier}", json={
                    "geometry": {"type": "LineString", "coordinates": [
                        [2, 48], [2.1, 48.1],
                    ]},
                    "desordre_ids": [str(self.disorders[2])],
                },
                headers={"If-Match": '"1"', "Operation-Id": str(uuid4())},
            )
            self.assertEqual(invalid_geometry.status_code, 422)

        with psycopg.connect(self.dsn) as connection:
            after = connection.execute(
                "SELECT commentaire,version,ST_AsEWKB(geometry) "
                "FROM public.mobilier WHERE id=%s", (identifier,),
            ).fetchone()
            self.assertEqual(after, before)
            self.assertEqual(
                set(self._link_rows(case, identifier)), {self.disorders[0]},
            )
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.evenements_ressources"
            ).fetchone()[0], events_before)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.operations_ressources "
                "WHERE operation_id=%s", (operation,),
            ).fetchone()[0], 0)

    def test_schema_constraints_are_explicit_unique_and_restrictive(self):
        tables = {
            "mobilier__desordres": (1, 0, 2),
            "equipements_mesure__desordres": (1, 0, 2),
            "reseaux_techniques__desordres": (1, 0, 2),
            "vegetation__desordres": (1, 0, 2),
            "amenagements_hydrauliques__desordres": (1, 1, 2),
        }
        with psycopg.connect(self.dsn) as connection:
            for table, expected in tables.items():
                with self.subTest(table=table):
                    row = connection.execute(
                        "SELECT count(*) FILTER (WHERE contype='p'),"
                        "count(*) FILTER (WHERE contype='u'),"
                        "count(*) FILTER (WHERE contype='f'),"
                        "bool_and(confdeltype='a') FILTER (WHERE contype='f') "
                        "FROM pg_constraint WHERE conrelid=%s::regclass",
                        (f"public.{table}",),
                    ).fetchone()
                    self.assertEqual(row[:3], expected)
                    self.assertTrue(row[3])

        with TestClient(self.app) as client:
            created = client.post("/api/mobilier", json={
                **self._n_to_n_cases()[0]["payload"],
                "desordre_ids": [str(self.disorders[0])],
            })
            self.assertEqual(created.status_code, 201, created.text)
            parent = UUID(created.json()["properties"]["id"])
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            with self.assertRaises(ForeignKeyViolation):
                connection.execute(
                    "DELETE FROM public.mobilier WHERE id=%s", (parent,),
                )
            with self.assertRaises(ForeignKeyViolation):
                connection.execute(
                    "DELETE FROM public.desordres WHERE id=%s",
                    (self.disorders[0],),
                )


if __name__ == "__main__":
    unittest.main()
