"""LOT 10–11: real transactional idempotence on an explicitly disposable PG."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi import HTTPException

from tests.geometry_fixtures import POINT_SQL
from digues_webapp.resource_mutations import execute_mutation
from webapp.tests import test_resource_versions as fixtures


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class ResourceOperationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ResourceVersionsPostgresTest.setUpClass()
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]

    def setUp(self):
        self.identifier, self.operation = uuid4(), str(uuid4())
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(f"INSERT INTO desordres(id,designation,geometry,valid) VALUES (%s,'initial',{POINT_SQL},true)", (self.identifier,))

    def write(self, operation=None, label="offline", version=1):
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            def change():
                connection.execute("UPDATE desordres SET designation=%s WHERE id=%s", (label, self.identifier))
                return {"id": str(self.identifier), "designation": label}
            return execute_mutation(connection, "desordres", "update", change, identifier=self.identifier,
                                    if_match=f'"{version}"', operation_id=operation or self.operation,
                                    changes={"designation": label})

    def assert_counts(self, version, operations, events):
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute("SELECT version FROM desordres WHERE id=%s", (self.identifier,)).fetchone()[0], version)
            self.assertEqual(connection.execute("SELECT count(*) FROM technique.operations_ressources WHERE resource_id=%s", (self.identifier,)).fetchone()[0], operations)
            self.assertEqual(connection.execute("SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s", (str(self.identifier),)).fetchone()[0], events)

    def test_retry_lost_response_and_collision(self):
        first = self.write()
        self.assertEqual(first["version"], 2)
        # Treat first response as lost; retry with the SAME old If-Match.
        self.assertEqual(self.write(), first)
        self.assert_counts(2, 1, 1)
        with self.assertRaises(HTTPException) as error:
            self.write(label="different intention")
        self.assertEqual(error.exception.detail["code"], "operation-id-collision")
        with self.assertRaises(HTTPException) as conflict:
            self.write(operation=str(uuid4()), label="concurrent")
        self.assertEqual(conflict.exception.detail["code"], "version-conflict")
        self.assert_counts(2, 1, 1)
        self.write(operation=str(uuid4()), label="later", version=2)
        self.assertEqual(self.write(), first)  # original receipt even after another mutation
        self.assert_counts(3, 2, 2)

    def test_concurrent_duplicate_is_one_transaction(self):
        barrier = Barrier(2)
        def request(_):
            barrier.wait(timeout=5)
            return self.write()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, [1, 2]))
        self.assertEqual(results[0], results[1])
        self.assert_counts(2, 1, 1)

    def test_rollback_between_mutation_journal_and_receipt(self):
        from digues_webapp.resource_journal import append_event
        def fail_after_journal(cursor, event):
            append_event(cursor, event)
            raise RuntimeError("crash before operation receipt")
        with patch("digues_webapp.resource_mutations.append_event", fail_after_journal):
            with self.assertRaises(RuntimeError):
                self.write()
        self.assert_counts(1, 0, 0)
        self.write()
        self.assert_counts(2, 1, 1)

    def test_http_contract_validated_payload_and_observations(self):
        from digues_webapp.app import create_app
        from digues_webapp.database import get_connection, get_write_connection
        app = create_app()
        def database():
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                yield connection
        app.dependency_overrides[get_connection] = database
        app.dependency_overrides[get_write_connection] = database
        observation = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute("INSERT INTO observations(id,desordre_id,valid) VALUES (%s,%s,true)", (observation, self.identifier))
        with fixtures.http_client(app) as client:
            url = f"/api/observations/{observation}"
            headers = {"Operation-Id": self.operation, "If-Match": '"1"'}
            first = client.put(url, headers=headers, json={"suite": "terrain"})
            self.assertEqual(first.status_code, 200, first.text)
            retry = client.put(url, headers=headers, json={"suite": "terrain"})
            self.assertEqual(retry.json(), first.json())
            collision = client.put(url, headers=headers, json={"suite": "autre"})
            self.assertEqual(collision.status_code, 409)
            bad = client.put(url, headers={**headers, "Operation-Id": "invalid"}, json={"suite": "terrain"})
            self.assertEqual(bad.status_code, 422)
            self.assertEqual(client.get(url).json()["version"], first.json()["version"])
