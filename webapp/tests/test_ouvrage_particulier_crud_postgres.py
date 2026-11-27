"""F4 Ouvrage particulier sur PostgreSQL/PostGIS strictement jetable."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from webapp.tests.test_equipement_mesure_postgres import http_client


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class OuvrageParticulierPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_legacy = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.systeme_id, cls.digue_id, cls.troncon_id = uuid4(), uuid4(), uuid4()
        cls.desordre_ids = (uuid4(), uuid4())
        with psycopg.connect(cls.dsn, autocommit=True) as connection:


            connection.execute(
                "INSERT INTO systemes(id,libelle,valid) VALUES(%s,'F4',true)",
                (cls.systeme_id,),
            )
            connection.execute(
                "INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) "
                "VALUES(%s,%s,'F4',true)", (cls.digue_id, cls.systeme_id),
            )
            connection.execute(
                "INSERT INTO troncons(id,digue_id,type_physique_id,libelle,geometry,valid) "
                "VALUES(%s,%s,'INDETERMINE','F4',ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)", (cls.troncon_id, cls.digue_id),
            )
            for index, identifier in enumerate(cls.desordre_ids):
                connection.execute(
                    "INSERT INTO desordres(id,designation,valid,geometry) "
                    "VALUES(%s,%s,%s,ST_SetSRID(ST_Point(1,1),3950))",
                    (identifier, f"Désordre Ouvrage {index + 1}", index != 0),
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

    def _links(self, identifier):
        with psycopg.connect(self.dsn) as connection:
            return tuple(row[0] for row in connection.execute(
                "SELECT desordre_id FROM public.ouvrages_particuliers__desordres "
                "WHERE ouvrage_particulier_id=%s ORDER BY desordre_id",
                (identifier,),
            ).fetchall())

    def _source_v2_counts(self):
        with psycopg.connect(self.dsn) as connection:
            return {
                table: connection.execute(sql.SQL(
                    "SELECT count(*) FROM source_v2.{}"
                ).format(sql.Identifier(table))).fetchone()[0]
                for (table,) in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='source_v2' "
                    "ORDER BY tablename"
                ).fetchall()
            }

    def test_disorder_relations_are_atomic_differential_and_idempotent(self):
        source_before = self._source_v2_counts()
        extra = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO desordres(id,designation,valid,geometry) "
                "VALUES(%s,'Ajout',true,ST_SetSRID(ST_Point(1,1),3950))",
                (extra,),
            )
        with http_client(self.app) as client:
            created = client.post("/api/ouvrages-particuliers", json={
                "designation": "Lié",
                "desordre_ids": [str(value) for value in self.desordre_ids],
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = UUID(created.json()["properties"]["id"])
            self.assertEqual(
                set(map(UUID, created.json()["properties"]["desordre_ids"])),
                set(self.desordre_ids),
            )
            self.assertEqual(set(self._links(identifier)), set(self.desordre_ids))

            protected = self.desordre_ids[1]
            function_name = f"protect_f5f_{uuid4().hex}"
            trigger_name = f"protect_f5f_{uuid4().hex}"
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                connection.execute(sql.SQL(
                    "CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN IF OLD.desordre_id = {}::uuid THEN "
                    "RAISE EXCEPTION 'unchanged relation deleted'; END IF; "
                    "RETURN OLD; END $$"
                ).format(sql.Identifier(function_name), sql.Literal(str(protected))))
                connection.execute(sql.SQL(
                    "CREATE TRIGGER {} BEFORE DELETE ON "
                    "public.ouvrages_particuliers__desordres FOR EACH ROW "
                    "EXECUTE FUNCTION public.{}()"
                ).format(sql.Identifier(trigger_name), sql.Identifier(function_name)))
            operation = str(uuid4())
            try:
                changed = client.put(
                    f"/api/ouvrages-particuliers/{identifier}",
                    json={"desordre_ids": [str(protected), str(extra)]},
                    headers={"If-Match": '"1"', "Operation-Id": operation},
                )
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(changed.json()["properties"]["version"], 2)
                self.assertEqual(set(self._links(identifier)), {protected, extra})
                replay = client.put(
                    f"/api/ouvrages-particuliers/{identifier}",
                    json={"desordre_ids": [str(protected), str(extra)]},
                    headers={"If-Match": '"1"', "Operation-Id": operation},
                )
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json(), changed.json())
                identical = client.put(
                    f"/api/ouvrages-particuliers/{identifier}",
                    json={"desordre_ids": [str(extra), str(protected)]},
                    headers={"If-Match": '"2"', "Operation-Id": str(uuid4())},
                )
                self.assertEqual(identical.status_code, 200, identical.text)
                self.assertEqual(set(self._links(identifier)), {protected, extra})
            finally:
                with psycopg.connect(self.dsn, autocommit=True) as connection:
                    connection.execute(sql.SQL(
                        "DROP TRIGGER {} ON public.ouvrages_particuliers__desordres"
                    ).format(sql.Identifier(trigger_name)))
                    connection.execute(sql.SQL(
                        "DROP FUNCTION public.{}()"
                    ).format(sql.Identifier(function_name)))

            attribute = client.put(
                f"/api/ouvrages-particuliers/{identifier}",
                json={"commentaire": "Attribut seul"},
                headers={"If-Match": '"3"', "Operation-Id": str(uuid4())},
            )
            self.assertEqual(attribute.status_code, 200, attribute.text)
            self.assertEqual(set(self._links(identifier)), {protected, extra})
            cleared = client.put(
                f"/api/ouvrages-particuliers/{identifier}",
                json={"desordre_ids": []},
                headers={"If-Match": '"4"', "Operation-Id": str(uuid4())},
            )
            self.assertEqual(cleared.status_code, 200, cleared.text)
            self.assertEqual(self._links(identifier), ())

            second = client.post("/api/ouvrages-particuliers", json={
                "desordre_ids": [str(extra)],
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            self.assertEqual(second.status_code, 201, second.text)
            third = client.post("/api/ouvrages-particuliers", json={
                "desordre_ids": [str(extra)],
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            self.assertEqual(third.status_code, 201, third.text)
            self.assertNotEqual(second.json()["properties"]["id"],
                                third.json()["properties"]["id"])
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.operations_ressources "
                "WHERE operation_id=%s", (operation,),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.evenements_ressources "
                "WHERE resource='ouvrages-particuliers' AND "
                "payload->'ids' @> jsonb_build_array(%s::text)", (identifier,),
            ).fetchone()[0], 5)
        self.assertEqual(self._source_v2_counts(), source_before)

    def test_invalid_relation_and_apply_failure_roll_back_parent(self):
        rejected = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO desordres(id,designation,valid,geometry) "
                "VALUES(%s,'Refus',true,ST_SetSRID(ST_Point(1,1),3950))",
                (rejected,),
            )
        with http_client(self.app) as client:
            created = client.post("/api/ouvrages-particuliers", json={
                "desordre_ids": [str(self.desordre_ids[0])],
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            identifier = UUID(created.json()["properties"]["id"])
            function_name = f"reject_f5f_{uuid4().hex}"
            trigger_name = f"reject_f5f_{uuid4().hex}"
            operation = uuid4()
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                events_before = connection.execute(
                    "SELECT count(*) FROM technique.evenements_ressources"
                ).fetchone()[0]
                connection.execute(sql.SQL(
                    "CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN IF NEW.desordre_id = {}::uuid THEN "
                    "RAISE EXCEPTION 'F5F rollback'; END IF; RETURN NEW; END $$"
                ).format(sql.Identifier(function_name), sql.Literal(str(rejected))))
                connection.execute(sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON "
                    "public.ouvrages_particuliers__desordres FOR EACH ROW "
                    "EXECUTE FUNCTION public.{}()"
                ).format(sql.Identifier(trigger_name), sql.Identifier(function_name)))
            try:
                failed = client.put(
                    f"/api/ouvrages-particuliers/{identifier}",
                    json={"commentaire": "Ne doit pas persister",
                          "desordre_ids": [str(rejected)]},
                    headers={"If-Match": '"1"', "Operation-Id": str(operation)},
                )
                self.assertEqual(failed.status_code, 503, failed.text)
            finally:
                with psycopg.connect(self.dsn, autocommit=True) as connection:
                    connection.execute(sql.SQL(
                        "DROP TRIGGER {} ON public.ouvrages_particuliers__desordres"
                    ).format(sql.Identifier(trigger_name)))
                    connection.execute(sql.SQL(
                        "DROP FUNCTION public.{}()"
                    ).format(sql.Identifier(function_name)))
            for invalid in (
                {"desordre_ids": [str(uuid4())]},
                {"desordre_ids": None},
                {"desordre_ids": [str(self.desordre_ids[0])] * 2},
            ):
                response = client.put(
                    f"/api/ouvrages-particuliers/{identifier}", json=invalid,
                    headers={"If-Match": '"1"', "Operation-Id": str(uuid4())},
                )
                self.assertEqual(response.status_code, 422, response.text)
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute(
                "SELECT commentaire,version FROM ouvrages_particuliers WHERE id=%s",
                (identifier,),
            ).fetchone(), (None, 1))
            self.assertEqual(self._links(identifier), (self.desordre_ids[0],))
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.evenements_ressources"
            ).fetchone()[0], events_before)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.operations_ressources "
                "WHERE operation_id=%s", (operation,),
            ).fetchone()[0], 0)

    def test_two_relation_puts_with_same_version_are_serialized(self):
        with http_client(self.app) as client:
            created = client.post("/api/ouvrages-particuliers", json={
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            identifier = created.json()["properties"]["id"]
            barrier = Barrier(2)

            def mutate(disorder):
                barrier.wait(timeout=5)
                return client.put(
                    f"/api/ouvrages-particuliers/{identifier}",
                    json={"desordre_ids": [str(disorder)]},
                    headers={"If-Match": '"1"', "Operation-Id": str(uuid4())},
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(mutate, self.desordre_ids))
            self.assertEqual(sorted(row.status_code for row in responses), [200, 409])
            final = client.get(f"/api/ouvrages-particuliers/{identifier}")
            self.assertEqual(final.json()["properties"]["version"], 2)
            self.assertEqual(len(final.json()["properties"]["desordre_ids"]), 1)

    def test_create_read_update_geometry_and_rollback(self):
        geometries = (
            {"type": "Point", "coordinates": [2.1, 48.2]},
            {"type": "LineString", "coordinates": [[2.1, 48.2], [2.2, 48.3]]},
            {"type": "Polygon", "coordinates": [
                [[2.1, 48.2], [2.2, 48.2], [2.2, 48.3], [2.1, 48.2]],
            ]},
        )
        with http_client(self.app) as client:
            bare = client.post("/api/ouvrages-particuliers", json={
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            self.assertEqual(bare.status_code, 201, bare.text)
            bare_id = bare.json()["properties"]["id"]
            self.assertEqual(bare.json()["geometry"]["type"], "Point")
            self.assertIsNone(bare.json()["properties"]["type_id"])
            self.assertNotIn("type_source_id", bare.json()["properties"])
            identifiers = []
            for geometry in geometries:
                created = client.post("/api/ouvrages-particuliers", json={
                    "designation": "Ouvrage", "valid": False,
                    "troncon_id": str(self.troncon_id), "geometry": geometry,
                })
                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual(created.json()["geometry"]["type"], geometry["type"])
                identifiers.append(created.json()["properties"]["id"])
            identifier = identifiers[0]
            operation = str(uuid4())
            payload = {"designation": "Modifié", "geometry": geometries[1]}
            headers = {"If-Match": '"1"', "Operation-Id": operation}
            update = client.put(
                f"/api/ouvrages-particuliers/{identifier}", headers=headers,
                json=payload,
            )
            self.assertEqual(update.status_code, 200, update.text)
            self.assertEqual(update.json()["properties"]["version"], 2)
            self.assertEqual(client.put(
                f"/api/ouvrages-particuliers/{identifier}", headers=headers,
                json=payload,
            ).status_code, 200)
            self.assertEqual(client.put(
                f"/api/ouvrages-particuliers/{identifier}",
                headers={"If-Match": '"1"'}, json={"designation": "Conflit"},
            ).status_code, 409)
            self.assertEqual(client.put(
                f"/api/ouvrages-particuliers/{identifier}",
                headers={"If-Match": '"2"'},
                json={"troncon_id": str(uuid4())},
            ).status_code, 422)
            self.assertEqual(client.get(
                f"/api/ouvrages-particuliers/{identifier}"
            ).json()["properties"]["designation"], "Modifié")
        with psycopg.connect(self.dsn) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT geometry FROM ouvrages_particuliers WHERE id=%s", (bare_id,),
            ).fetchone()[0])
            self.assertEqual(connection.execute(
                "SELECT ST_SRID(geometry),GeometryType(geometry) "
                "FROM ouvrages_particuliers WHERE id=%s", (identifier,),
            ).fetchone(), (3950, "LINESTRING"))
            self.assertEqual([
                connection.execute(
                    "SELECT GeometryType(geometry) FROM ouvrages_particuliers WHERE id=%s",
                    (row_id,),
                ).fetchone()[0] for row_id in identifiers[1:]
            ], ["LINESTRING", "POLYGON"])

    def test_existing_identity_and_catalog_type_are_preserved(self):
        # DB-2 removed provenance from business tables/DTO. Preserve the same
        # identity/type-on-attribute-update guarantee with its canonical FK,
        # and keep rejecting attempts to submit historical provenance fields.
        historical_id = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO referentiel.types_ouvrage_particulier(id,libelle,valid) "
                "VALUES('UX_GEO_HIST','Type de test conservé',true) ON CONFLICT DO NOTHING"
            )
            connection.execute(
                "INSERT INTO ouvrages_particuliers"
                "(id,type_id,designation,geometry,valid) "
                "VALUES(%s,'UX_GEO_HIST','Ouvrage existant',"
                "ST_SetSRID(ST_Point(1,1),3950),true)",
                (historical_id,),
            )
        with http_client(self.app) as client:
            for payload in (
                {"type_source_id": "RefOuvrageParticulier:9"},
                {"source_class": "OuvrageParticulier"},
                {"geometry": {"type": "MultiPoint", "coordinates": [[2, 48]]}},
                {"troncon_id": str(uuid4())},
            ):
                self.assertEqual(client.post(
                    "/api/ouvrages-particuliers", json=payload,
                ).status_code, 422)
            self.assertEqual(client.get(
                f"/api/ouvrages-particuliers/{uuid4()}"
            ).status_code, 404)
            row = client.get(f"/api/ouvrages-particuliers/{historical_id}")
            self.assertEqual(row.status_code, 200, row.text)
            self.assertEqual(row.json()["properties"]["id"], str(historical_id))
            self.assertEqual(row.json()["properties"]["type_id"], "UX_GEO_HIST")
            self.assertNotIn("type_source_id", row.json()["properties"])
            edited = client.put(
                f"/api/ouvrages-particuliers/{historical_id}",
                headers={"If-Match": '"1"'}, json={"commentaire": "Conservé"},
            )
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual(edited.json()["properties"]["id"], str(historical_id))
            self.assertEqual(edited.json()["properties"]["type_id"], "UX_GEO_HIST")
            self.assertEqual(edited.json()["geometry"], row.json()["geometry"])
            self.assertEqual(client.put(
                f"/api/ouvrages-particuliers/{historical_id}",
                headers={"If-Match": '"2"'},
                json={"type_source_id": "RefOuvrageParticulier:9"},
            ).status_code, 422)

    def test_attribute_put_preserves_polygon_bytes_and_bare_geometry(self):
        with http_client(self.app) as client:
            created = client.post("/api/ouvrages-particuliers", json={
                "designation": "Avant", "geometry": {"type": "Polygon", "coordinates": [
                    [[2.1, 48.2], [2.2, 48.2], [2.2, 48.3], [2.1, 48.2]],
                ]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()["properties"]["id"]
            with psycopg.connect(self.dsn) as connection:
                before = connection.execute(
                    "SELECT ST_AsEWKB(geometry) FROM ouvrages_particuliers WHERE id=%s",
                    (identifier,),
                ).fetchone()[0]
            updated = client.put(f"/api/ouvrages-particuliers/{identifier}",
                                 headers={"If-Match": '"1"'},
                                 json={"designation": "Après", "commentaire": "Base"})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["properties"]["version"], 2)
            bare = client.post("/api/ouvrages-particuliers", json={
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            })
            self.assertEqual(bare.status_code, 201, bare.text)
            bare_id = bare.json()["properties"]["id"]
            bare_update = client.put(f"/api/ouvrages-particuliers/{bare_id}",
                                     headers={"If-Match": '"1"'}, json={"libelle": "Sans géométrie"})
            self.assertEqual(bare_update.status_code, 200, bare_update.text)
            with psycopg.connect(self.dsn) as connection:
                after = connection.execute(
                    "SELECT ST_AsEWKB(geometry) FROM ouvrages_particuliers WHERE id=%s",
                    (identifier,),
                ).fetchone()[0]
                bare_after = connection.execute(
                    "SELECT geometry FROM ouvrages_particuliers WHERE id=%s", (bare_id,),
                ).fetchone()[0]
            self.assertEqual(after, before)
            self.assertIsNotNone(bare_after)

    def test_observation_photo_parent_unique(self):
        with http_client(self.app) as client:
            created = client.post("/api/ouvrages-particuliers", json={
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            }).json()
            identifier = created["properties"]["id"]
            observation_id = str(uuid4())
            observation = client.post(
                "/api/observations", headers={"Operation-Id": str(uuid4())},
                json={"id": observation_id, "parent": {
                    "resource": "ouvrages_particuliers", "id": identifier,
                }, "designation": "Visite"},
            )
            self.assertEqual(observation.status_code, 201, observation.text)
            photo_id = uuid4()
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                connection.execute(
                    "INSERT INTO photos(id,observation_id,storage_key,valid) "
                    "VALUES(%s,%s,'ouvrage.jpg',true)", (photo_id, observation_id),
                )
                row = connection.execute(
                    "SELECT ouvrage_particulier_id,num_nonnulls(desordre_id,troncon_id,"
                    "ouvrage_hydraulique_id,equipement_mesure_id,cheminement_id,"
                    "mobilier_id,reseau_technique_id,amenagement_hydraulique_id,"
                    "vegetation_id,ouvrage_particulier_id,"
                    "structure_amenagement_hydraulique_id,organe_protection_collective_id,"
                    "prestation_id,ouvrage_telecom_energie_id,ouvrage_voirie_id,"
                    "reseau_hydraulique_id,structure_digue_id,profil_travers_id,"
                    "objet_mesure_hydraulique_id,structure_protection_id,"
                    "segment_hydrographique_id,berge_id,composant_berge_id,element_morphologique_id) "
                    "FROM observations WHERE id=%s", (observation_id,),
                ).fetchone()
                self.assertEqual((str(row[0]), row[1]), (identifier, 1))
                self.assertEqual(connection.execute(
                    "SELECT observation_id FROM photos WHERE id=%s", (photo_id,),
                ).fetchone()[0], UUID(observation_id))
            listed = client.get(
                f"/api/ouvrages-particuliers/{identifier}/observations"
            )
            self.assertEqual(listed.json()["observations"][0]["photo_count"], 1)


if __name__ == "__main__":
    unittest.main()
