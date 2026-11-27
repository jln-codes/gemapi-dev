"""LOT 7: real transactions, commit ordering, HTTP replay and SSE."""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from starlette.requests import Request

from digues_app.target.schema import MIGRATION_TABLES
from digues_webapp import resource_journal as journal
from digues_webapp.resource_mutations import execute_mutation
from webapp.tests import test_resource_versions as versions


class JournalContractTest(unittest.TestCase):
    @staticmethod
    def request(*headers):
        return Request({
            "type": "http",
            "method": "POST",
            "path": "/resource",
            "headers": [(name.lower().encode("ascii"), value.encode("ascii")) for name, value in headers],
        })

    def test_cursor_and_migrator_boundary(self):
        self.assertNotIn("technique.evenements_ressources", MIGRATION_TABLES)
        self.assertEqual(journal.parse_cursor("9007199254740993"), 9007199254740993)
        for invalid in ("-1", "1.0", " 2", "9223372036854775808", "garbage"):
            with self.assertRaises(HTTPException):
                journal.parse_cursor(invalid)

    def test_origin_headers_support_canonical_names_and_absence(self):
        canonical = journal.mutation_origin(self.request(
            ("x-digues-app-client-id", "browser-a"),
            ("X-Digues-App-Instance-Id", "page-a"),
        ))
        self.assertEqual(canonical, {
            "originClientId": "browser-a",
            "originInstanceId": "page-a",
        })
        generated = journal.mutation_origin(self.request())
        self.assertRegex(generated["originClientId"], r"^api-[0-9a-f-]{36}$")
        self.assertRegex(generated["originInstanceId"], r"^api-[0-9a-f-]{36}$")

        obsolete = journal.mutation_origin(self.request(
            ("X-Sirs-Client-Id", "old-browser"),
            ("X-Sirs-Instance-Id", "old-page"),
        ))
        self.assertRegex(obsolete["originClientId"], r"^api-[0-9a-f-]{36}$")
        self.assertRegex(obsolete["originInstanceId"], r"^api-[0-9a-f-]{36}$")

    def test_origin_headers_reject_conflicts_invalid_and_ambiguous_repetitions(self):
        invalid_sets = (
            (("X-Digues-App-Client-Id", "first"), ("X-Digues-App-Client-Id", "second")),
            (("X-Digues-App-Instance-Id", " leading"),),
            (("X-Digues-App-Instance-Id", ""),),
        )
        for headers in invalid_sets:
            with self.subTest(headers=headers):
                with self.assertRaises(HTTPException) as caught:
                    journal.mutation_origin(self.request(*headers))
                self.assertEqual(caught.exception.status_code, 422)
        repeated = journal.mutation_origin(self.request(
            ("X-Digues-App-Client-Id", "same"),
            ("x-digues-app-client-id", "same"),
        ))
        self.assertEqual(repeated["originClientId"], "same")


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class ResourceJournalPostgresTest(unittest.TestCase):
    setUpClass = classmethod(versions.ResourceVersionsPostgresTest.setUpClass.__func__)
    create_disorder = versions.ResourceVersionsPostgresTest.create_disorder
    write = versions.ResourceVersionsPostgresTest.write

    def events_for(self, identifier):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            return conn.execute("SELECT payload FROM technique.evenements_ressources WHERE payload->'ids' ? %s",
                                (str(identifier),)).fetchall()

    def test_success_exact_receipt_conflict_and_rollback(self):
        identifier = self.create_disorder()
        result = self.write(identifier, 1, "winner")
        self.assertEqual(self.events_for(identifier), [(result["mutation"],)])
        self.assertEqual(result["mutation"]["versions"], {str(identifier): 2})
        with self.assertRaises(HTTPException):
            self.write(identifier, 1, "conflict")
        with self.assertRaises(RuntimeError):
            self.write(identifier, 2, "rollback", failing=True)
        self.assertEqual(len(self.events_for(identifier)), 1)
        # Even a failure at the journal boundary rolls back data AND version.
        with patch("digues_webapp.resource_mutations.append_event", side_effect=RuntimeError("journal failure")):
            with self.assertRaises(RuntimeError):
                self.write(identifier, 2, "not committed")
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT designation,version FROM desordres WHERE id=%s",
                                          (identifier,)).fetchone(), ("winner", 2))

    def test_delete_collection_contract_needs_no_surviving_business_row(self):
        event = {"schemaVersion": 1, "eventId": str(uuid4()), "resource": "desordres",
                 "action": "delete", "ids": None, "originClientId": "api-test",
                 "originInstanceId": "test-instance", "occurredAt": 1}
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            with conn.transaction():
                journal.append_event(conn.cursor(), event)
            payload = conn.execute("SELECT payload FROM technique.evenements_ressources WHERE event_id=%s",
                                   (event["eventId"],)).fetchone()[0]
        self.assertEqual(payload, event)

    def test_journal_sequence_cannot_pass_uncommitted_predecessor(self):
        # C4 serializes reference/Photo/Disorder families before the journal.
        # Two independent SE rows still reach the global journal concurrently:
        # keep the positive and negative proof of commit-order serialization.
        a,b=uuid4(),uuid4()
        with psycopg.connect(self.dsn,autocommit=True) as setup:
            setup.execute("INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'A',true),(%s,'B',true)",(a,b))
        def second_writer():
            with psycopg.connect(self.dsn,autocommit=True) as database:
                def write():
                    database.execute("UPDATE public.systemes SET libelle='second' WHERE id=%s",(b,))
                    return {'id':str(b)}
                return execute_mutation(database,'systemes','update',write,identifier=b,if_match='"1"')
        started = threading.Event()
        def append_after_signal(cursor, event):
            started.set()
            return journal.append_event(cursor, event)
        with psycopg.connect(self.dsn, autocommit=True) as conn, ThreadPoolExecutor(1) as pool:
            with conn.transaction():
                receipt = execute_mutation(conn, "systemes", "update", lambda: {"id": str(a)},
                                           identifier=a, if_match='"1"')
                with patch("digues_webapp.resource_mutations.append_event", side_effect=append_after_signal):
                    pending = pool.submit(second_writer)
                    self.assertTrue(started.wait(5))
                    # T2 really reached append; without the transaction lock it
                    # would now commit past the invisible T1 sequence.
                    with self.assertRaises(FutureTimeout):
                        pending.result(timeout=0.2)
                    self.assertEqual(self.events_for(a), [])
                    self.assertEqual(self.events_for(b), [])
            other = pending.result(timeout=5)
        with psycopg.connect(self.dsn) as conn:
            ids = conn.execute("SELECT event_id::text FROM technique.evenements_ressources WHERE event_id = ANY(%s::uuid[]) ORDER BY sequence_id",
                ([receipt["mutation"]["eventId"], other["mutation"]["eventId"]],)).fetchall()
        self.assertEqual([r[0] for r in ids], [receipt["mutation"]["eventId"], other["mutation"]["eventId"]])

    def test_http_pagination_sse_resume_identity_and_exclusions(self):
        from digues_webapp.app import create_app
        from digues_webapp.database import get_connection, get_write_connection
        app = create_app()
        @contextmanager
        def connection():
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                yield conn
        def dependency():
            with connection() as conn:
                yield conn
        app.dependency_overrides[get_connection] = dependency
        app.dependency_overrides[get_write_connection] = dependency
        with patch.object(journal, "open_read_connection", connection), versions.http_client(app) as client:
            head = client.get("/api/resource-events/head").json()["cursor"]
            receipts = []
            for label in ("first", "second", "third"):
                response = client.post("/api/systemes-endiguement", json={"libelle": label},
                    headers={"X-Digues-App-Client-Id": "browser-a", "X-Digues-App-Instance-Id": "instance-a"})
                self.assertEqual(response.status_code, 201, response.text)
                receipts.append(response.json()["mutation"])
            page = client.get(f"/api/resource-events?after={head}&limit=2")
            self.assertIn("no-store", page.headers["cache-control"])
            first = page.json()
            self.assertTrue(first["hasMore"])
            self.assertEqual([e["event"] for e in first["events"]], receipts[:2])
            second = client.get(f"/api/resource-events?after={first['nextCursor']}&limit=2").json()
            self.assertFalse(second["hasMore"])
            self.assertEqual(second["events"][0]["event"], receipts[2])
            self.assertEqual(receipts[0]["originInstanceId"], "instance-a")
            # Last-Event-ID takes precedence; the stream must replay the third event.
            response = client.get("/api/resource-events/stream?after=0", stream=True,
                                  headers={"Last-Event-ID": first["nextCursor"]})
            try:
                lines = []
                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    lines.append(line)
                    if line.startswith("data:"):
                        break
                self.assertIn("event: resource-mutated", lines)
                self.assertIn(receipts[2]["eventId"], lines[-1])
                self.assertIn(f"id: {second['nextCursor']}", lines)
            finally:
                response.close()
            self.assertEqual(client.get("/api/resource-events?after=-1").status_code, 422)
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            for resource in ("territoires-administratifs", "meteo-france", "vigicrues"):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    journal.append_event(conn.cursor(), {**receipts[0], "resource": resource, "eventId": str(uuid4())})
