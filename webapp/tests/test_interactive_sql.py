import asyncio
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from digues_webapp.interactive_sql import (
    INTERACTIVE_SQL_MAX_CHARS,
    InteractiveSqlExecutionError,
    InteractiveSqlValidationError,
    execute_interactive_query,
    validate_interactive_sql,
)
from digues_webapp.database import get_sql_connection
from digues_webapp.models import InteractiveSqlRequest
from digues_webapp.sql_presets import SQL_PRESETS, list_sql_presets

try:
    from digues_webapp.app import app
except ModuleNotFoundError as exc:
    if exc.name != "fastapi":
        raise
    app = None


class FakeTransaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append(("transaction_enter",))

    def __exit__(self, exc_type, *_args):
        self.events.append(("transaction_exit", exc_type))
        return False


class FakeControlCursor:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=None):
        self.events.append(("control", query, parameters))


class FakeResultCursor:
    def __init__(self, events, columns, rows, error=None):
        self.events = events
        self.description = [SimpleNamespace(name=column) for column in columns]
        self.rows = list(rows)
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query):
        self.events.append(("query", query))
        if self.error:
            raise self.error

    def fetchmany(self, size):
        self.events.append(("fetchmany", size))
        batch, self.rows = self.rows[:size], self.rows[size:]
        return batch


class FakeConnection:
    def __init__(self, columns=(), rows=(), error=None):
        self.events = []
        self.columns = columns
        self.rows = rows
        self.error = error

    def transaction(self):
        return FakeTransaction(self.events)

    def cursor(self, name=None):
        if name is None:
            return FakeControlCursor(self.events)
        self.events.append(("server_cursor", name))
        return FakeResultCursor(
            self.events, self.columns, self.rows, error=self.error
        )


class InteractiveSqlValidationTest(unittest.TestCase):
    def test_accepts_select_and_with_select(self):
        for sql in (
            "SELECT id FROM public.troncons",
            "WITH actifs AS (SELECT id FROM public.troncons) SELECT * FROM actifs",
        ):
            with self.subTest(sql=sql):
                self.assertEqual(validate_interactive_sql(sql), sql)

    def test_rejects_mutations_ddl_and_multiple_statements(self):
        rejected = (
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET value = 1",
            "DELETE FROM t",
            "WITH removed AS (DELETE FROM t RETURNING *) SELECT * FROM removed",
            "CREATE TABLE t (id integer)",
            "ALTER TABLE t ADD COLUMN value text",
            "DROP TABLE t",
            "TRUNCATE t",
            "GRANT SELECT ON t TO role",
            "REVOKE SELECT ON t FROM role",
            "COPY t TO '/tmp/t'",
            "CALL procedure_name()",
            "DO $$ BEGIN END $$",
            "VACUUM t",
            "ANALYZE t",
            "REFRESH MATERIALIZED VIEW v",
            "SELECT 1; SELECT 2",
            'SELECT pg_catalog."pg_terminate_backend"(12345)',
            "SELECT pg_catalog.\"set_config\"('statement_timeout', '0', true)",
            "SELECT * FROM vault.decrypted_secrets",
        )
        for sql in rejected:
            with self.subTest(sql=sql), self.assertRaises(
                InteractiveSqlValidationError
            ):
                validate_interactive_sql(sql)

    def test_rejects_queries_longer_than_documented_limit(self):
        with self.assertRaisesRegex(
            InteractiveSqlValidationError, "50 000 caractères"
        ):
            validate_interactive_sql("SELECT '" + "x" * INTERACTIVE_SQL_MAX_CHARS)


class InteractiveSqlPresetTest(unittest.TestCase):
    def test_catalog_contains_two_unique_readonly_presets(self):
        self.assertEqual(len(SQL_PRESETS), 2)
        self.assertEqual(
            [preset["id"] for preset in SQL_PRESETS],
            ["demo_rows", "demo_datetime"],
        )
        self.assertEqual(len({preset["id"] for preset in SQL_PRESETS}), 2)
        for preset in SQL_PRESETS:
            with self.subTest(preset=preset["id"]):
                self.assertEqual(
                    set(preset), {"id", "label", "description", "sql"}
                )
                self.assertTrue(all(value.strip() for value in preset.values()))
                self.assertEqual(validate_interactive_sql(preset["sql"]), preset["sql"])

    def test_listing_returns_a_copy(self):
        result = list_sql_presets()
        result[0]["label"] = "modifié"
        self.assertEqual(SQL_PRESETS[0]["label"], "Test — 10 lignes")


class InteractiveSqlExecutionTest(unittest.TestCase):
    def test_returns_metadata_and_json_safe_values(self):
        identifier = uuid.uuid4()
        connection = FakeConnection(
            ["id", "amount", "day", "created", "enabled", "nothing", "binary"],
            [(
                identifier,
                Decimal("2.40"),
                date(2026, 9, 6),
                datetime(2026, 9, 6, 12, 30),
                True,
                None,
                b"\x01\x02",
            )],
        )
        times = iter((1.0, 1.037))
        result = execute_interactive_query(
            connection, "SELECT values", clock=lambda: next(times)
        )

        self.assertEqual(result["rowCount"], 1)
        self.assertEqual(result["durationMs"], 37)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["rows"], [[
            str(identifier), "2.40", "2026-09-06", "2026-09-06T12:30:00",
            True, None, "\\x0102",
        ]])
        self.assertEqual(connection.events[0], ("transaction_enter",))
        self.assertEqual(
            connection.events[1], ("control", "SET TRANSACTION READ ONLY", None)
        )
        self.assertEqual(
            connection.events[2],
            (
                "control",
                "SELECT set_config('statement_timeout', %s, true)",
                ("30000ms",),
            ),
        )

    def test_row_and_size_limits_report_truncation(self):
        rows = [(index,) for index in range(4)]
        with patch("digues_webapp.interactive_sql.READONLY_SQL_MAX_ROWS", 2):
            times = iter((1.0, 1.001))
            result = execute_interactive_query(
                FakeConnection(["id"], rows),
                "SELECT id FROM t",
                clock=lambda: next(times),
            )
        self.assertEqual(result["rows"], [[0], [1]])
        self.assertEqual(result["rowCount"], 2)
        self.assertTrue(result["truncated"])

        with patch("digues_webapp.interactive_sql.READONLY_SQL_MAX_RESULT_BYTES", 12):
            times = iter((2.0, 2.001))
            oversized = execute_interactive_query(
                FakeConnection(["text"], [("long value",)]),
                "SELECT text FROM t",
                clock=lambda: next(times),
            )
        self.assertEqual(oversized["rowCount"], 0)
        self.assertTrue(oversized["truncated"])

    def test_postgresql_error_exposes_only_clean_diagnostics(self):
        error = RuntimeError("connection secret and stack detail")
        error.sqlstate = "42703"
        error.diag = SimpleNamespace(
            message_primary='column "foo" does not exist',
            statement_position="15",
        )
        times = iter((1.0, 1.002))
        with self.assertRaises(InteractiveSqlExecutionError) as raised:
            execute_interactive_query(
                FakeConnection(error=error),
                "SELECT foo",
                clock=lambda: next(times),
            )
        self.assertEqual(raised.exception.public_detail(), {
            "message": "La requête SQL utilise une colonne inconnue.",
            "sqlstate": "42703",
            "position": 15,
        })
        self.assertNotIn("secret", json.dumps(raised.exception.public_detail()))

    def test_timeout_is_identified(self):
        error = RuntimeError("internal timeout detail")
        error.sqlstate = "57014"
        error.diag = SimpleNamespace(message_primary="canceling statement", statement_position=None)
        times = iter((1.0, 31.0))
        with self.assertRaises(InteractiveSqlExecutionError) as raised:
            execute_interactive_query(
                FakeConnection(error=error),
                "SELECT pg_sleep(60)",
                clock=lambda: next(times),
            )
        self.assertTrue(raised.exception.timed_out)
        self.assertEqual(raised.exception.sqlstate, "57014")


@unittest.skipIf(app is None, "FastAPI indisponible dans l’environnement de test")
class InteractiveSqlEndpointTest(unittest.TestCase):
    @staticmethod
    def endpoint():
        return next(
            route.endpoint for route in app.routes if route.path == "/api/sql/query"
        )

    @staticmethod
    def presets_endpoint():
        return next(
            route.endpoint for route in app.routes if route.path == "/api/sql/presets"
        )

    def test_endpoint_uses_dedicated_service_without_calling_mistral(self):
        expected = {
            "columns": ["value"], "rows": [[1]], "rowCount": 1,
            "durationMs": 2, "truncated": False,
        }
        connection = object()
        with (
            patch("digues_webapp.app.execute_interactive_query", return_value=expected) as execute,
            patch("digues_webapp.app.chat_with_mistral") as mistral,
        ):
            result = self.endpoint()(InteractiveSqlRequest(sql="SELECT 1"), connection)
        self.assertEqual(result, expected)
        execute.assert_called_once_with(connection, "SELECT 1")
        mistral.assert_not_called()

    def test_endpoint_dependency_is_the_dedicated_sql_connection(self):
        route = next(route for route in app.routes if route.path == "/api/sql/query")
        self.assertEqual(len(route.dependant.dependencies), 1)
        self.assertIs(route.dependant.dependencies[0].call, get_sql_connection)

    def test_presets_endpoint_returns_catalog_without_calling_mistral(self):
        with patch("digues_webapp.app.chat_with_mistral") as mistral:
            result = self.presets_endpoint()()
        self.assertEqual(result, SQL_PRESETS)
        mistral.assert_not_called()

    def test_error_handler_returns_clean_structured_diagnostics(self):
        error = InteractiveSqlExecutionError(
            "La requête SQL utilise une colonne inconnue.",
            sqlstate="42703",
            position=15,
        )
        handler = app.exception_handlers[InteractiveSqlExecutionError]
        response = asyncio.run(handler(None, error))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body), {
            "error": {
                "message": "La requête SQL utilise une colonne inconnue.",
                "sqlstate": "42703",
                "position": 15,
            }
        })
