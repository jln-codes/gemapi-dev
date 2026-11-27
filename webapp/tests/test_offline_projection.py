"""Read-only scope snapshots against an explicitly disposable local PostGIS."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi import HTTPException

from digues_webapp.offline_projection import read_scope, read_pinned
from digues_webapp import queries
from digues_webapp.resource_mutations import execute_mutation
from webapp.tests import test_resource_versions


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class OfflineScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_resource_versions.ResourceVersionsPostgresTest.setUpClass()
        cls.dsn = test_resource_versions.ResourceVersionsPostgresTest.dsn

    def setUp(self):
        self.systeme, self.digue, self.troncon, self.disorder, self.observation = [uuid4() for _ in range(5)]
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("INSERT INTO systemes(id,libelle,valid) VALUES (%s,'Offline SE',true)", (self.systeme,))
            connection.execute("INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) VALUES (%s,%s,'Offline Digue',true)", (self.digue,self.systeme))
            connection.execute("INSERT INTO troncons(id,type_physique_id,digue_id,libelle,geometry,valid) VALUES (%s,'INDETERMINE',%s,'Offline Tronçon',ST_Transform(ST_GeomFromText('LINESTRING(2 46,2.01 46.01)',4326),3950),true)", (self.troncon,self.digue))
            connection.execute("INSERT INTO desordres(id,designation,geometry,valid) VALUES (%s,'Offline D',ST_Transform(ST_GeomFromText('POINT(2 46)',4326),3950),true)", (self.disorder,))
            connection.execute("INSERT INTO public.desordres__troncons(desordre_id,troncon_id) VALUES (%s,%s)", (self.disorder,self.troncon))
            connection.execute("INSERT INTO observations(id,desordre_id,designation,valid) VALUES (%s,%s,'Offline Observation',true)", (self.observation,self.disorder))

    def snapshot(self):
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            return read_scope(connection, [self.troncon])

    def test_five_resources_authoritative_versions_relations_no_media_or_environment(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["schemaVersion"], 3)
        self.assertEqual(snapshot["crs"]["code"], "EPSG:3950")
        self.assertEqual(
            snapshot["reperageReferences"],
            {"schemaVersion": 2, "crs": snapshot["crs"], "systems": []},
        )
        self.assertEqual(set(snapshot["observationReferences"]), {"suitesApporter", "urgences"})
        self.assertEqual(snapshot["tronconIds"], [str(self.troncon)])
        self.assertEqual({r["resource"] for r in snapshot["records"]}, {"systemes", "digues", "troncons", "desordres", "observations"})
        for row in snapshot["records"]:
            values = row["payload"].get("properties", row["payload"])
            self.assertEqual(row["serverVersion"], values["version"])
            self.assertEqual(row["id"], values["id"])
            self.assertNotIn("photos", row["payload"])
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(int(snapshot["cursor"]), connection.execute("SELECT COALESCE(MAX(sequence_id),0) FROM technique.evenements_ressources").fetchone()[0])

    def test_initial_projection_carries_latest_observation_urgency_contract(self):
        urgency = "RefUrgence:1"
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO referentiel.urgences(id,libelle,valid) "
                "VALUES (%s,'Faible urgence',true) "
                "ON CONFLICT(id) DO UPDATE SET libelle=EXCLUDED.libelle,valid=true",
                (urgency,),
            )
            connection.execute(
                "UPDATE observations SET date_observation=DATE '2026-10-07',"
                "urgence_id=%s WHERE id=%s",
                (urgency, self.observation),
            )
            authoritative = queries.fetch_desordre(connection, self.disorder)
            snapshot = read_scope(connection, [self.troncon])
        projected = next(
            row["payload"] for row in snapshot["records"]
            if row["resource"] == "desordres" and row["id"] == str(self.disorder)
        )
        for field in (
            "dernier_niveau_urgence_id",
            "dernier_niveau_urgence_libelle",
            "derniere_observation_id",
            "derniere_observation_date",
        ):
            self.assertEqual(
                projected["properties"][field], authoritative["properties"][field],
            )

    def test_observation_references_are_prepared_with_scope_and_exclude_invalid_choices(self):
        suite, urgency, invalid = (str(uuid4()) for _ in range(3))
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("INSERT INTO referentiel.suites_apporter(id,designation,libelle,valid) VALUES (%s,'Suivi','Suivi',true),(%s,'Ancien','Ancien',false)",
                               (suite, invalid))
            connection.execute("INSERT INTO referentiel.urgences(id,libelle,valid) VALUES (%s,'Urgent',true)", (urgency,))
            snapshot = read_scope(connection, [self.troncon])
        self.assertIn(suite, [row["id"] for row in snapshot["observationReferences"]["suitesApporter"]])
        self.assertIn(urgency, [row["id"] for row in snapshot["observationReferences"]["urgences"]])
        self.assertNotIn(invalid, [row["id"] for row in snapshot["observationReferences"]["suitesApporter"]])

    def test_pinned_recovery_is_read_only_parents_only_without_cursor(self):
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            before = connection.execute("SELECT count(*) FROM technique.evenements_ressources").fetchone()[0]
            recovered = read_pinned(connection, "observations", self.observation)
            self.assertNotIn("cursor", recovered)
            self.assertEqual({r["resource"] for r in recovered["records"]},
                             {"systemes", "digues", "troncons", "desordres", "observations"})
            self.assertEqual(len(read_pinned(connection, "desordres", self.disorder)["records"]), 4)
            self.assertNotIn("photos", next(r["payload"] for r in recovered["records"] if r["resource"] == "observations"))
            self.assertEqual(read_pinned(connection, "desordres", uuid4())["records"], [])
            for resource in ("territoires-administratifs", "photos", "systemes", "meteo", "vigicrues"):
                with self.assertRaises(HTTPException):
                    read_pinned(connection, resource, self.disorder)
            self.assertEqual(connection.execute("SELECT count(*) FROM technique.evenements_ressources").fetchone()[0], before)

    def test_reperage_references_are_bounded_and_follow_calibration_changes(self):
        system, borne, link = uuid4(), uuid4(), uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("INSERT INTO systemes_reperage(id,troncon_id,libelle,valid) VALUES (%s,%s,'R',true)",
                               (system, self.troncon))
            connection.execute("INSERT INTO bornes_reperage(id,libelle,geometry,valid) VALUES (%s,'B',"
                               "ST_Transform(ST_SetSRID(ST_Point(2,46),4326),3950),true)", (borne,))
            connection.execute("INSERT INTO public.systemes_reperage__bornes(id,systeme_reperage_id,borne_id,valeur_pr,valid) "
                               "VALUES (%s,%s,%s,125,true)", (link, system, borne))
        first = self.snapshot()["reperageReferences"]["systems"]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["bornes"][0]["pr"], "125")
        self.assertEqual(first[0]["crs"]["code"], "EPSG:3950")
        self.assertEqual(first[0]["bornes"][0]["geometry"]["type"], "Point")
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("UPDATE public.systemes_reperage__bornes SET valeur_pr=126 WHERE id=%s", (link,))
        second = self.snapshot()["reperageReferences"]["systems"]
        self.assertNotEqual(first[0]["revision"], second[0]["revision"])

    def test_trunk_observations_in_scope_and_targeted_recovery(self):
        identifier = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("INSERT INTO observations(id,troncon_id,designation,valid) VALUES (%s,%s,'Observation Tronçon',true)", (identifier, self.troncon))
            snapshot = read_scope(connection, [self.troncon])
            row = next(r for r in snapshot["records"] if r["id"] == str(identifier))
            self.assertEqual(row["payload"]["parent"], {
                "resource": "troncons", "canonical_resource": "troncons", "relation_role": "troncon_id",
                "type_physique_id": "INDETERMINE", "id": str(self.troncon), "designation": "Offline Tronçon"})
            recovered = read_pinned(connection, "observations", identifier)
            self.assertEqual({r["resource"] for r in recovered["records"]}, {"observations", "troncons", "digues", "systemes"})
            self.assertNotIn("photos", row["payload"])
            self.assertEqual(row["serverVersion"], 1)

    def test_snapshot_keeps_head_and_rows_consistent_during_concurrent_write(self):
        original = queries.fetch_troncon
        receipts = []
        def concurrent_read(connection, identifier):
            with psycopg.connect(self.dsn, autocommit=True) as writer:
                def update():
                    return writer.execute("UPDATE systemes SET libelle='New name' WHERE id=%s RETURNING id::text,version", (self.systeme,)).fetchone()
                def write():
                    row = update()
                    return {"id":row[0], "version":row[1]}
                receipts.append(execute_mutation(writer, "systemes", "update", write,
                    identifier=self.systeme, if_match='"1"'))
            return original(connection, identifier)
        with patch.object(queries, "fetch_troncon", concurrent_read):
            snapshot = self.snapshot()
        systeme = next(row for row in snapshot["records"] if row["resource"] == "systemes")
        self.assertEqual(systeme["payload"]["libelle"], "Offline SE")
        self.assertEqual(systeme["serverVersion"], 1)
        newer = self.snapshot()
        self.assertGreater(int(newer["cursor"]), int(snapshot["cursor"]))
        self.assertEqual(next(row for row in newer["records"] if row["resource"] == "systemes")["serverVersion"], 2)

    def test_deleted_children_removed_and_root_limit_not_silently_truncated(self):
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("DELETE FROM observations WHERE id=%s", (self.observation,))
            self.assertNotIn("observations", {r["resource"] for r in read_scope(connection, [self.troncon])["records"]})
            for roots in ([], [uuid4() for _ in range(21)]):
                with self.assertRaises(HTTPException) as error:
                    read_scope(connection, roots)
                self.assertEqual(error.exception.status_code, 422)

    def test_batch_snapshot_preserves_mvcc_for_disorders(self):
        original = queries.fetch_desordres_batch
        before = self.snapshot()
        version = next(r['serverVersion'] for r in before['records'] if r['id'] == str(self.disorder))
        # Linking touches the disorder's version; don't assume a creation counter.
        def guarded_batch(connection, identifiers):
            with psycopg.connect(self.dsn, autocommit=True) as writer:
                def write():
                    row = writer.execute("UPDATE desordres SET designation='Concurrent' WHERE id=%s RETURNING id::text,version", (self.disorder,)).fetchone()
                    return {"id": row[0], "version": row[1]}
                execute_mutation(writer, 'desordres', 'update', write,
                                 identifier=self.disorder, if_match=f'"{version}"')
            return original(connection, identifiers)
        with patch.object(queries, 'fetch_desordres_batch', guarded_batch):
            during = self.snapshot()
        self.assertEqual(during, before)
        after = self.snapshot()
        self.assertGreater(int(after['cursor']), int(before['cursor']))
        changed = next(r for r in after['records'] if r['id'] == str(self.disorder))
        self.assertEqual(changed['serverVersion'], version + 1)
        self.assertEqual(changed['payload']['properties']['designation'], 'Concurrent')

    def test_exactly_2000_records_and_2001_refused_before_batch(self):
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("WITH added AS (INSERT INTO desordres(id,designation,geometry,valid) "
                "SELECT gen_random_uuid(),'Bounded fixture',ST_Transform(ST_SetSRID(ST_Point(2,46),4326),3950),true "
                "FROM generate_series(1,1995) RETURNING id) "
                "INSERT INTO public.desordres__troncons(desordre_id,troncon_id) SELECT id,%s FROM added", (self.troncon,))
            connection.execute('ANALYZE')
            with patch.object(queries, 'fetch_desordre', side_effect=AssertionError('No per-object read')):
                self.assertEqual(len(read_scope(connection, [self.troncon])['records']), 2000)
            connection.execute("INSERT INTO observations(id,troncon_id,designation,valid) VALUES (%s,%s,'2001',true)", (uuid4(), self.troncon))
            with patch.object(queries, 'fetch_desordres_batch') as batch:
                with self.assertRaises(HTTPException) as error:
                    read_scope(connection, [self.troncon])
                self.assertEqual(error.exception.status_code, 422)
                self.assertEqual(error.exception.detail['code'], 'scope-too-large')
                batch.assert_not_called()
