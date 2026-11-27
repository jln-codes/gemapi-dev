from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import uuid

from digues_webapp.database import (
    BusinessCRSMetadataError,
    PostgreSQLConfig,
    PostgreSQLReadonlyConfig,
    WebDatabaseConfigurationError,
    WebDatabaseError,
    configure_extension_search_path,
    configure_sql_search_path,
    extension_search_path,
    open_sql_connection,
    read_business_crs,
    validate_sql_role_security,
)
from digues_webapp.readonly_sql import (
    READONLY_SQL_STATEMENT_TIMEOUT_MS,
    ReadonlySqlExecutionError,
    ReadonlySqlValidationError,
    execute_readonly_query,
    validate_readonly_sql,
)
from digues_app.control.ai_policy import AI_SELECT_COLUMNS
from digues_webapp import schema_context


class FakeTransaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append(("transaction_enter",))
        return self

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

    def execute(self, query, params=None):
        self.events.append(("control", query, params))


class FakeResultCursor:
    def __init__(self, events, columns, rows, error=None):
        self.events = events
        self.description = [SimpleNamespace(name=name) for name in columns]
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


class FakeReadonlyConnection:
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


def factory_for(connection):
    @contextmanager
    def factory():
        yield connection

    return factory


class FakeExtensionCursor:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeCRSConnection:
    def __init__(self, rows=(), error=None):
        self.cursor_instance = FakeExtensionCursor(rows)
        self.error = error

    @contextmanager
    def cursor(self):
        if self.error:
            raise self.error
        yield self.cursor_instance


class WebDatabaseConfigurationTest(unittest.TestCase):
    def test_business_crs_is_read_from_singleton_metadata_and_qualified(self):
        self.assertEqual(
            read_business_crs(FakeCRSConnection([(3950,)])).code, "EPSG:3950"
        )
        self.assertEqual(
            read_business_crs(FakeCRSConnection([(2154,)])).code, "EPSG:2154"
        )

    def test_business_crs_rejects_missing_or_non_unique_metadata(self):
        for rows in ((), [(3950,), (3950,)]):
            with self.subTest(rows=rows), self.assertRaisesRegex(
                BusinessCRSMetadataError, "absente ou non unique",
            ):
                read_business_crs(FakeCRSConnection(rows))

    def test_business_crs_rejects_invalid_unqualified_or_unreadable_metadata(self):
        for value in (None, "invalide", 4326, 3857):
            with self.subTest(value=value), self.assertRaisesRegex(
                BusinessCRSMetadataError, "invalide ou non qualifié",
            ):
                read_business_crs(FakeCRSConnection([(value,)]))
        with self.assertRaisesRegex(BusinessCRSMetadataError, "absente ou illisible"):
            read_business_crs(FakeCRSConnection(error=RuntimeError("relation absente")))

    def test_database_url_takes_priority_over_unused_separate_variables(self):
        environment = {
            "DATABASE_URL": "postgresql://dsn-user:secret@example.invalid/dsn_db",
            "DIGUES_POSTGRES_HOST": "canonical-host",
            "SIRS_POSTGRE_HOST": "legacy-host",
            "DIGUES_POSTGRES_CONNECT_TIMEOUT": "7",
        }
        with patch.dict("os.environ", environment, clear=True):
            config = PostgreSQLConfig.from_env()

        self.assertEqual(config.connect_kwargs()["conninfo"], environment["DATABASE_URL"])
        self.assertEqual(config.connect_kwargs()["connect_timeout"], 7)
        self.assertEqual(config.safe_location, "DSN fourni par DATABASE_URL")

    def test_canonical_postgresql_variables_are_used_without_database_url(self):
        environment = {
            "DIGUES_POSTGRES_HOST": "db.internal",
            "DIGUES_POSTGRES_PORT": "15432",
            "DIGUES_POSTGRES_DATABASE": "digues_business",
            "DIGUES_POSTGRES_USER": "webapp",
            "DIGUES_POSTGRES_PASSWORD": "secret",
            "DIGUES_POSTGRES_CONNECT_TIMEOUT": "12",
        }
        with patch.dict("os.environ", environment, clear=True):
            kwargs = PostgreSQLConfig.from_env().connect_kwargs(autocommit=False)

        self.assertEqual(kwargs["host"], "db.internal")
        self.assertEqual(kwargs["port"], 15432)
        self.assertEqual(kwargs["dbname"], "digues_business")
        self.assertEqual(kwargs["user"], "webapp")
        self.assertEqual(kwargs["password"], "secret")
        self.assertEqual(kwargs["connect_timeout"], 12)
        self.assertFalse(kwargs["autocommit"])

    def test_obsolete_postgresql_name_is_ignored(self):
        with patch.dict("os.environ", {"SIRS_POSTGRE_HOST": "legacy-host"}, clear=True):
            self.assertEqual(PostgreSQLConfig.from_env().host, "127.0.0.1")
        with patch.dict("os.environ", {
            "DIGUES_POSTGRES_HOST": "canonical-host",
            "SIRS_POSTGRE_HOST": "legacy-host",
        }, clear=True):
            self.assertEqual(PostgreSQLConfig.from_env().host, "canonical-host")

    def test_readonly_url_is_mandatory_and_never_falls_back_to_database_url(self):
        with (
            patch.dict(
                "os.environ",
                {"DATABASE_URL": "postgresql://privileged/db"},
                clear=True,
            ),
            self.assertRaisesRegex(
                WebDatabaseConfigurationError, "DATABASE_READONLY_URL",
            ),
        ):
            PostgreSQLReadonlyConfig.from_env()

        environment = {
            "DATABASE_URL": "postgresql://privileged/db",
            "DATABASE_READONLY_URL": "postgresql://readonly/db",
            "DIGUES_POSTGRES_CONNECT_TIMEOUT": "4",
        }
        with patch.dict("os.environ", environment, clear=True):
            config = PostgreSQLReadonlyConfig.from_env()
        self.assertEqual(config.connect_kwargs()["conninfo"], environment[
            "DATABASE_READONLY_URL"
        ])
        self.assertEqual(config.connect_kwargs()["connect_timeout"], 4)

    def test_sql_connection_fails_closed_when_readonly_url_is_missing(self):
        with (
            patch.dict(
                "os.environ",
                {"DATABASE_URL": "postgresql://privileged/db"},
                clear=True,
            ),
            self.assertRaisesRegex(WebDatabaseError, "SQL/IA indisponible"),
        ):
            with open_sql_connection():
                self.fail("La connexion métier ne doit jamais servir de fallback.")

    def test_extension_search_path_quotes_dynamic_extension_schemas(self):
        self.assertEqual(
            extension_search_path("extensions", 'postgis"custom'),
            'pg_catalog, public, "extensions", "postgis""custom"',
        )

    def test_configure_extension_search_path_reads_pg_catalog_and_namespace(self):
        cursor = FakeExtensionCursor([
            ("postgis", "3.5.0", "extensions"),
            ("pgcrypto", "1.3", "crypto"),
        ])
        status = configure_extension_search_path(cursor)

        self.assertEqual(status.postgis_schema, "extensions")
        self.assertEqual(status.pgcrypto_schema, "crypto")
        self.assertIn("FROM pg_extension AS e", cursor.queries[0][0])
        self.assertIn("JOIN pg_namespace AS n", cursor.queries[0][0])
        self.assertEqual(
            cursor.queries[1],
            (
                "SELECT set_config('search_path', %s, false)",
                ('pg_catalog, public, "extensions", "crypto"',),
            ),
        )

    def test_sql_search_path_contains_no_pgcrypto_or_host_specific_schema(self):
        cursor = FakeExtensionCursor([("extensions",)])
        configure_sql_search_path(cursor)
        self.assertIn("extension.extname = 'postgis'", cursor.queries[0][0])
        self.assertEqual(
            cursor.queries[1],
            (
                "SELECT set_config('search_path', %s, false)",
                ('pg_catalog, public, "extensions"',),
            ),
        )

    def test_sql_role_security_rejects_privileged_or_writable_roles(self):
        secure = (
            "digues_sql_readonly", "digues_sql_readonly", True,
            False, False, False, False, False, "on", False, False, False, False,
            False, False,
        )
        validate_sql_role_security(FakeExtensionCursor([secure]))
        invalid_identity = list(secure)
        invalid_identity[1] = "role_de_session_différent"
        with self.assertRaises(WebDatabaseConfigurationError):
            validate_sql_role_security(FakeExtensionCursor([tuple(invalid_identity)]))
        cannot_login = list(secure)
        cannot_login[2] = False
        with self.assertRaises(WebDatabaseConfigurationError):
            validate_sql_role_security(FakeExtensionCursor([tuple(cannot_login)]))
        not_readonly = list(secure)
        not_readonly[8] = "off"
        with self.assertRaises(WebDatabaseConfigurationError):
            validate_sql_role_security(FakeExtensionCursor([tuple(not_readonly)]))
        for index in (3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14):
            with self.subTest(index=index):
                insecure = list(secure)
                insecure[index] = True
                with self.assertRaises(WebDatabaseConfigurationError):
                    validate_sql_role_security(FakeExtensionCursor([tuple(insecure)]))

    def test_human_sql_script_keeps_its_readonly_scope(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "sql"
            / "configure_readonly_role.sql"
        ).read_text(encoding="utf-8")
        grants = script.split("GRANT SELECT ON TABLE", 1)[1].split(
            "TO digues_sql_readonly", 1,
        )[0]
        granted_relations = {
            item.strip()
            for item in grants.replace("\n", " ").split(",")
        }
        # Human SQL covers the business/reference objects; AI-only technical
        # knowledge and territory tables have a separate policy.
        business_ai_relations = {
            table for table in AI_SELECT_COLUMNS if not table.startswith("technique.")
        }
        self.assertTrue(business_ai_relations <= granted_relations)
        self.assertNotIn("GRANT ALL", script)
        self.assertNotIn("GRANT SELECT ON ALL", script)
        self.assertNotIn("GRANT USAGE ON SEQUENCE", script)
        self.assertNotIn("public.spatial_ref_sys", grants)
        self.assertNotIn("vault.", grants)

    def test_ai_schema_context_defaults_to_the_dedicated_ai_connection(self):
        from digues_webapp.database import open_ai_connection
        self.assertIs(schema_context.open_ai_connection, open_ai_connection)


class ReadonlySqlValidationTest(unittest.TestCase):
    def test_accepts_advanced_read_queries_without_rewriting_them(self):
        queries = (
            "SELECT id, libelle FROM public.systemes",
            "WITH actifs AS (SELECT id FROM public.systemes WHERE valid) "
            "SELECT * FROM actifs",
            "SELECT d.id FROM public.digues d JOIN public.systemes s "
            "ON s.id = d.systeme_endiguement_id",
            "SELECT digue_id, count(*) FROM public.troncons GROUP BY digue_id",
            "SELECT ST_Length(geometry) FROM public.troncons",
            "/* SELECT sûre */ SELECT 'UPDATE; DELETE', \"update\" FROM exemple;",
            "SELECT $$INSERT; DROP$$",
        )
        for sql in queries:
            with self.subTest(sql=sql):
                self.assertEqual(validate_readonly_sql(sql), sql.strip())

    def test_rejects_mutating_and_administrative_operations(self):
        queries = (
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET value = 1",
            "DELETE FROM t",
            "CREATE TABLE t (id int)",
            "ALTER TABLE t ADD COLUMN value int",
            "DROP TABLE t",
            "TRUNCATE t",
            "MERGE INTO t USING u ON false WHEN NOT MATCHED THEN INSERT DEFAULT VALUES",
            "GRANT SELECT ON t TO role",
            "REVOKE SELECT ON t FROM role",
            "COMMENT ON TABLE t IS 'x'",
            "VACUUM t",
            "ANALYZE t",
            "REFRESH MATERIALIZED VIEW v",
            "CLUSTER t",
            "REINDEX TABLE t",
            "COPY t TO '/tmp/t.csv'",
            "WITH changed AS (DELETE FROM t RETURNING id) SELECT * FROM changed",
            "SELECT value INTO temporary_table FROM t",
            "SELECT nextval('sequence')",
            "SELECT set_config('search_path', 'public', false)",
            "SELECT pg_advisory_lock(1)",
            "SELECT pg_notify('channel', 'payload')",
            'SELECT pg_catalog."pg_terminate_backend"(12345)',
            "SELECT pg_catalog.\"set_config\"('statement_timeout', '0', true)",
            'SELECT * FROM vault."decrypted_secrets"',
            'SELECT * FROM "vault"."decrypted_secrets"',
            "SELECT public.inverser_troncon('00000000-0000-0000-0000-000000000001')",
        )
        for sql in queries:
            with self.subTest(sql=sql), self.assertRaises(
                ReadonlySqlValidationError
            ):
                validate_readonly_sql(sql)

    def test_rejects_multiple_or_malformed_statements(self):
        queries = (
            "SELECT 1; SELECT 2",
            "SELECT 1;;",
            "SELECT 'unterminated",
            "SELECT 1 /* unterminated",
            "WITH source AS (SELECT 1)",
            "VALUES (1)",
            "",
        )
        for sql in queries:
            with self.subTest(sql=sql), self.assertRaises(
                ReadonlySqlValidationError
            ):
                validate_readonly_sql(sql)


class ReadonlySqlEngineTest(unittest.TestCase):
    def test_default_factory_is_the_dedicated_sql_connection(self):
        connection = FakeReadonlyConnection(["value"], [(1,)])
        with patch(
            "digues_webapp.readonly_sql.open_sql_connection",
            return_value=factory_for(connection)(),
        ) as open_connection:
            result = execute_readonly_query("SELECT 1")
        self.assertEqual(result["rows"], [[1]])
        open_connection.assert_called_once_with()

    def test_configures_read_only_transaction_and_local_timeout(self):
        connection = FakeReadonlyConnection(["value"], [(1,)])
        result = execute_readonly_query(
            "SELECT 1 AS value", connection_factory=factory_for(connection)
        )

        self.assertEqual(
            result, {"columns": ["value"], "rows": [[1]], "truncated": False}
        )
        self.assertEqual(connection.events[0], ("transaction_enter",))
        self.assertEqual(
            connection.events[1], ("control", "SET TRANSACTION READ ONLY", None)
        )
        self.assertEqual(
            connection.events[2],
            (
                "control",
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{READONLY_SQL_STATEMENT_TIMEOUT_MS}ms",),
            ),
        )
        self.assertEqual(connection.events[-1], ("transaction_exit", None))

    def test_returns_columns_empty_rows_and_json_safe_values(self):
        identifier = uuid.uuid4()
        connection = FakeReadonlyConnection(
            ["nothing", "amount", "day", "id", "geometry"],
            [(None, Decimal("1.20"), date(2026, 9, 4), identifier, b"\x01\x02")],
        )
        result = execute_readonly_query(
            "SELECT values", connection_factory=factory_for(connection)
        )
        self.assertEqual(
            result["rows"],
            [[None, "1.20", "2026-09-04", str(identifier), "\\x0102"]],
        )

        empty = execute_readonly_query(
            "SELECT values",
            connection_factory=factory_for(FakeReadonlyConnection(["id"], [])),
        )
        self.assertEqual(
            empty, {"columns": ["id"], "rows": [], "truncated": False}
        )

    def test_transport_row_limit_does_not_rewrite_aggregation(self):
        connection = FakeReadonlyConnection(["count"], [(42,)])
        sql = "SELECT COUNT(*) FROM public.systemes"
        aggregate = execute_readonly_query(
            sql, connection_factory=factory_for(connection)
        )
        self.assertEqual(aggregate["rows"], [[42]])
        self.assertIn(("query", sql), connection.events)

        limited_connection = FakeReadonlyConnection(
            ["id"], [(1,), (2,), (3,)]
        )
        with patch("digues_webapp.readonly_sql.READONLY_SQL_MAX_ROWS", 2):
            limited = execute_readonly_query(
                "SELECT id FROM t",
                connection_factory=factory_for(limited_connection),
            )
        self.assertEqual(limited["rows"], [[1], [2]])
        self.assertTrue(limited["truncated"])

    def test_serialized_size_limit_truncates_before_oversized_row(self):
        connection = FakeReadonlyConnection(["text"], [("a" * 30,), ("b",)])
        with patch("digues_webapp.readonly_sql.READONLY_SQL_MAX_RESULT_BYTES", 20):
            result = execute_readonly_query(
                "SELECT text FROM t", connection_factory=factory_for(connection)
            )
        self.assertEqual(result["rows"], [])
        self.assertTrue(result["truncated"])

    def test_database_timeout_is_normalized_and_rolls_back_transaction(self):
        error = RuntimeError("canceling statement due to statement timeout")
        error.sqlstate = "57014"
        connection = FakeReadonlyConnection(["value"], [], error=error)
        with self.assertRaises(ReadonlySqlExecutionError) as raised:
            execute_readonly_query(
                "SELECT pg_sleep(1)", connection_factory=factory_for(connection)
            )
        self.assertTrue(raised.exception.timed_out)
        self.assertNotIn("statement timeout", str(raised.exception))
        self.assertEqual(
            connection.events[-1], ("transaction_exit", RuntimeError)
        )


class ReadonlySqlPostGISIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.postgres_safety import connect_test_database, require_test_dsn

        dsn = require_test_dsn("DIGUES_READONLY_TEST_DSN")
        try:
            with connect_test_database("DIGUES_READONLY_TEST_DSN", autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT PostGIS_Version()")
                    cursor.fetchone()
        except Exception as exc:
            raise unittest.SkipTest(f"PostGIS local indisponible : {exc}")
        cls.database_env = patch.dict(os.environ, {
            "DATABASE_READONLY_URL": dsn,
            "DATABASE_URL": "host=/tmp/digues-test-unavailable dbname=unavailable connect_timeout=1",
            "PYTHON_DOTENV_DISABLED": "1",
        })
        cls.database_env.start()
        cls.addClassCleanup(cls.database_env.stop)

    def test_real_readonly_engine_supports_aggregations_and_postgis(self):
        transaction = execute_readonly_query(
            "SELECT current_setting('transaction_read_only') AS read_only"
        )
        self.assertEqual(transaction["rows"], [["on"]])

        aggregate = execute_readonly_query(
            "SELECT COUNT(*) AS total FROM public.systemes"
        )
        self.assertEqual(aggregate["columns"], ["total"])
        self.assertEqual(len(aggregate["rows"]), 1)
        self.assertIsInstance(aggregate["rows"][0][0], int)

        spatial = execute_readonly_query(
            "SELECT ST_AsText(ST_SetSRID(ST_Point(1, 2), 3950)) AS geometry_wkt, "
            "ST_SetSRID(ST_Point(1, 2), 3950) AS geometry_raw"
        )
        self.assertEqual(spatial["rows"][0][0], "POINT(1 2)")
        self.assertIsInstance(spatial["rows"][0][1], str)
        self.assertTrue(spatial["rows"][0][1])

    def test_real_role_identity_attributes_and_privileges_are_restricted(self):
        result = execute_readonly_query(
            "SELECT current_user, session_user, "
            "current_setting('transaction_read_only'), "
            "rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "FROM pg_catalog.pg_roles WHERE rolname = current_user"
        )
        row = result["rows"][0]
        self.assertEqual(row[0], row[1])
        self.assertEqual(row[2], "on")
        self.assertEqual(row[3:], [False, False, False, False, False])

        with open_sql_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT "
                "has_table_privilege(current_user, 'public.systemes', 'SELECT'), "
                "has_table_privilege(current_user, 'public.systemes', 'INSERT'), "
                "has_table_privilege(current_user, 'public.systemes', 'UPDATE'), "
                "has_table_privilege(current_user, 'public.systemes', 'DELETE'), "
                "has_schema_privilege(current_user, 'public', 'CREATE'), "
                "pg_catalog.pg_has_role(current_user, 'pg_signal_backend', 'MEMBER')"
            )
            privileges = cursor.fetchone()
        self.assertEqual(privileges, (True, False, False, False, False, False))

    def test_real_role_can_join_business_tables(self):
        result = execute_readonly_query(
            "SELECT s.id, count(d.id) AS digue_count "
            "FROM public.systemes AS s "
            "LEFT JOIN public.digues AS d ON d.systeme_endiguement_id = s.id "
            "GROUP BY s.id LIMIT 1"
        )
        self.assertEqual(result["columns"], ["id", "digue_count"])

    def test_forbidden_relations_and_functions_are_checked_without_access(self):
        with open_sql_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT coalesce(bool_or(has_table_privilege(current_user, relation.oid, "
                "'SELECT') AND has_schema_privilege(current_user, namespace.oid, "
                "'USAGE')), false) "
                "FROM pg_catalog.pg_class AS relation "
                "JOIN pg_catalog.pg_namespace AS namespace "
                "ON namespace.oid = relation.relnamespace "
                "WHERE (namespace.nspname, relation.relname) IN "
                "(('vault', 'decrypted_secrets'), ('pg_catalog', 'pg_authid'))"
            )
            can_read_forbidden_relation = cursor.fetchone()[0]
            cursor.execute(
                "SELECT proname, has_function_privilege(current_user, function.oid, "
                "'EXECUTE') FROM pg_catalog.pg_proc AS function "
                "WHERE proname IN ('pg_terminate_backend', 'pg_cancel_backend', "
                "'set_config') ORDER BY proname"
            )
            function_privileges = cursor.fetchall()
        self.assertFalse(can_read_forbidden_relation)
        self.assertTrue(function_privileges)
        for sql in (
            'SELECT pg_catalog."pg_terminate_backend"(12345)',
            "SELECT pg_catalog.\"set_config\"('statement_timeout', '0', true)",
            "SELECT * FROM vault.decrypted_secrets",
        ):
            with self.subTest(sql=sql), self.assertRaises(ReadonlySqlValidationError):
                validate_readonly_sql(sql)

    def test_postgresql_read_only_barrier_rejects_mutation_after_validation(self):
        with (
            patch(
                "digues_webapp.readonly_sql.validate_readonly_sql",
                return_value="UPDATE public.systemes SET valid = valid",
            ),
            self.assertRaises(ReadonlySqlExecutionError) as raised,
        ):
            execute_readonly_query("SELECT 1")
        self.assertFalse(raised.exception.timed_out)

        import psycopg

        with open_sql_connection() as connection:
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                        cursor.execute(
                            "UPDATE public.systemes SET valid = valid"
                        )

    def test_postgresql_statement_timeout_is_local_to_execution(self):
        with (
            patch("digues_webapp.readonly_sql.READONLY_SQL_STATEMENT_TIMEOUT_MS", 10),
            self.assertRaises(ReadonlySqlExecutionError) as raised,
        ):
            execute_readonly_query("SELECT pg_sleep(0.1)")
        self.assertTrue(raised.exception.timed_out)


if __name__ == "__main__":
    unittest.main()
