"""Contrats de routage purs ; aucun PostgreSQL ni secret extérieur."""

from contextlib import nullcontext
import asyncio
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
from digues_app.control.auth import UserIdentity
from digues_webapp import auth, business_context as bc, database, schema_context, ai
from digues_webapp.app import create_app
from digues_webapp.connection_profiles import resolve_profile
from digues_webapp.resource_journal import stream_events
from digues_webapp.business_routes import BusinessRoutingMiddleware
from digues_webapp.help_docs import register_routes as register_help_routes


def context():
    return bc.BusinessContext(uuid4(), uuid4(), uuid4(), frozenset({'business.read', 'sql.read'}),
        1, 'revision', 'local', 'alpha', 'alpha', uuid4(), uuid4(), 'v1')


class BusinessContextTest(unittest.TestCase):
    def test_immutable_and_no_credentials(self):
        ctx = context()
        with self.assertRaises(FrozenInstanceError):
            ctx.database_name = 'beta'
        self.assertFalse(any(key in ctx.__slots__ for key in ('dsn', 'password', 'token')))
        for changed in (replace(ctx, business_database_id=uuid4()), replace(ctx, data_epoch=uuid4()),
                        replace(ctx, schema_version='v2'), replace(ctx, permission_revision='new'),
                        replace(ctx, connection_profile_ref='other')):
            self.assertNotEqual(ctx.schema_cache_key, changed.schema_cache_key)

    def test_denial_opens_no_business_connection(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        user = UserIdentity(uuid4(), 'admin', 'Admin', 'active', True)
        with patch.object(bc, 'open_control_connection', return_value=nullcontext(conn)), \
                patch.object(database, '_context_connection') as opened:
            with self.assertRaises(HTTPException) as caught:
                bc.resolve_business_context(user, uuid4(), 'business.read', session_token=secrets.token_urlsafe(32))
            self.assertEqual(caught.exception.status_code, 403)
            opened.assert_not_called()

    def test_profile_requires_explicit_target_and_distinct_readonly(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.json'
            profiles = {'alpha': {'cluster_ref': 'local', 'database_name': 'alpha',
                'runtime': 'host=localhost dbname=alpha user=alpha_runtime',
                'sql_readonly': 'host=localhost dbname=alpha user=alpha_readonly',
                'ai_readonly': 'host=localhost dbname=alpha user=alpha_ai'}}
            path.write_text(json.dumps(profiles))
            with patch.dict(os.environ, {'BUSINESS_CONNECTION_PROFILES_FILE': str(path)}):
                self.assertEqual(resolve_profile(context(), kind='sql_readonly')['user'], 'alpha_readonly')
                self.assertEqual(resolve_profile(context(), kind='ai_readonly')['user'], 'alpha_ai')
                for bad in ('host=localhost dbname=beta user=alpha_readonly',
                            'host=localhost dbname=alpha user=alpha_runtime',
                            'service=other dbname=alpha user=alpha_readonly',
                            'host=localhost dbname=alpha user=alpha_readonly options=-csearch_path=public'):
                    profiles['alpha']['sql_readonly'] = bad
                    path.write_text(json.dumps(profiles))
                    with self.assertRaises(database.WebDatabaseConfigurationError):
                        resolve_profile(context(), kind='sql_readonly')
                del profiles['alpha']['sql_readonly']
                path.write_text(json.dumps(profiles))
                with self.assertRaises(database.WebDatabaseConfigurationError):
                    resolve_profile(context(), kind='sql_readonly')

    def test_modes_are_exclusive_and_default_is_closed(self):
        for enabled in ('false', 'true'):
            with patch.dict(os.environ, {'LEGACY_SINGLE_DATABASE_ENABLED': enabled}):
                app = create_app()
            app.dependency_overrides[auth.get_auth_settings] = lambda: auth.AuthSettings('https://testserver', True)
            with TestClient(app, base_url='https://testserver') as client:
                self.assertEqual(client.get('/api/health').status_code, 200)
                route = '/api/systemes-endiguement' if enabled == 'false' else f'/api/bases/{uuid4()}/systemes-endiguement'
                with patch.object(database, '_connection') as opened:
                    self.assertEqual(client.get(route).status_code, 404)
                    opened.assert_not_called()
                if enabled == 'false':
                    self.assertEqual(client.get(f'/api/bases/{uuid4()}/systemes-endiguement').status_code, 401)
                    self.assertEqual(client.get(f'/api/bases/{uuid4()}/photos/{uuid4()}').status_code, 401)
                    self.assertEqual(client.get(f'/api/bases/{uuid4()}/troncons-lit/{uuid4()}').status_code, 401)
                    self.assertEqual(client.get(f'/api/bases/{uuid4()}/elements-morphologiques-lit/{uuid4()}').status_code, 401)

    def test_multibase_keeps_help_global_and_business_routes_closed(self):
        app = FastAPI()
        root = Path(self.enterContext(TemporaryDirectory()))
        (root / 'README.md').write_text('# Test help\n\nContenu.', encoding='utf-8')
        register_help_routes(app, root)
        async def contextual_route(business_database_id: str, photo_id: str):
            return {'ok': True}
        app.add_api_route('/api/bases/{business_database_id}/photos/{photo_id}', contextual_route)
        app.add_middleware(BusinessRoutingMiddleware, legacy=False)
        async def requests():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='https://testserver') as client:
                documents_response = await client.get('/api/help/documents')
                documents = documents_response.json()['documents']
                document_response = await client.get(f"/api/help/documents/{documents[0]['id']}")
                refused = await client.get('/api/systemes-endiguement')
                contextual = await client.get('/api/bases/base/photos/photo')
                return documents_response, documents, document_response, refused, contextual

        (
            documents_response,
            documents,
            document_response,
            refused,
            contextual,
        ) = asyncio.run(requests())
        self.assertEqual(documents_response.status_code, 200)
        self.assertTrue(documents)

        self.assertEqual(document_response.status_code, 200)
        self.assertEqual(document_response.json()['id'], documents[0]['id'])

        self.assertEqual(refused.status_code, 404)
        self.assertEqual(refused.json()['detail'], 'Route indisponible dans ce mode serveur.')
        self.assertEqual(contextual.status_code, 200)

    def test_schema_cache_partition_and_epoch(self):
        schema_context.clear_ai_schema_context_cache()
        a, b = context(), context()
        factory = Mock(return_value=nullcontext(Mock()))
        with patch.object(schema_context, 'introspect_ai_schema', return_value=([], [], [])), \
                patch.object(schema_context, 'format_ai_schema_context', side_effect=['Alpha', 'Beta', 'restored']):
            self.assertEqual(schema_context.get_ai_schema_context(connection_factory=factory, cache_key=a.schema_cache_key), 'Alpha')
            self.assertEqual(schema_context.get_ai_schema_context(connection_factory=factory, cache_key=b.schema_cache_key), 'Beta')
            self.assertEqual(schema_context.get_ai_schema_context(connection_factory=factory, cache_key=a.schema_cache_key), 'Alpha')
            self.assertEqual(schema_context.get_ai_schema_context(connection_factory=factory,
                cache_key=replace(a, data_epoch=uuid4()).schema_cache_key), 'restored')
        self.assertEqual(factory.call_count, 3)
        schema_context.clear_ai_schema_context_cache()

    def test_ai_sql_factory_is_server_only(self):
        factory = Mock()
        call = {'function': {'name': ai.BUSINESS_DATABASE_TOOL_NAME, 'arguments': json.dumps({'sql': 'SELECT 1'})}}
        with patch.object(ai, 'execute_readonly_query', return_value={}) as run:
            ai._run_tool(call, connection_factory=factory)
        run.assert_called_once_with('SELECT 1', connection_factory=factory)
        call['function']['arguments'] = json.dumps({'sql': 'SELECT 1', 'database_id': str(uuid4())})
        with patch.object(ai, 'execute_readonly_query') as run:
            ai._run_tool(call, connection_factory=factory)
            run.assert_not_called()

    def test_ai_environment_factory_has_separate_runtime_cache_connection(self):
        target = replace(context(), permissions=frozenset({"ai.read"}))
        user = Mock()
        ai_connection = object()
        cache_connection = object()
        with (
            patch.object(bc, "resolve_business_context", return_value=target),
            patch.object(
                database,
                "open_ai_connection",
                return_value=nullcontext(ai_connection),
            ) as open_ai,
            patch.object(
                database,
                "open_write_connection",
                return_value=nullcontext(cache_connection),
            ) as open_cache,
        ):
            factory, _validate = bc.revalidated_factory(
                target, user, "session", "ai.read", ai=True
            )
            with factory() as connection:
                self.assertIs(connection, ai_connection)
            with factory.hydrometry_cache_factory() as connection:
                self.assertIs(connection, cache_connection)

        open_ai.assert_called_once_with(target)
        open_cache.assert_called_once_with(target)

    def test_sse_revalidates_factory_and_stops_after_revocation(self):
        from contextlib import contextmanager
        from unittest.mock import AsyncMock
        calls = []

        @contextmanager
        def factory():
            calls.append(True)
            raise HTTPException(403, 'revoked')
            yield

        async def collect():
            request = Mock()
            request.is_disconnected = AsyncMock(return_value=False)
            return [part async for part in stream_events(request, 0, connection_factory=factory, poll_interval=0)]
        self.assertEqual(asyncio.run(collect()), [': connected\n\n'])
        self.assertEqual(len(calls), 1)

    def test_target_mismatch_is_closed_and_error_redacted(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ('beta', 'runtime', 'runtime')
        with patch.object(database, '_check_context_available'), \
                patch('digues_webapp.connection_profiles.resolve_profile', return_value={'user': 'runtime'}), \
                patch('psycopg.connect', return_value=conn):
            with self.assertRaises(database.WebDatabaseError) as error:
                with database.open_read_connection(context()):
                    self.fail('Wrong target was accepted')
            self.assertIsNone(error.exception.__cause__)
            conn.close.assert_called_once()

    def test_different_factories_without_key_never_share_schema_cache(self):
        schema_context.clear_ai_schema_context_cache()
        first, second = Mock(return_value=nullcontext(Mock())), Mock(return_value=nullcontext(Mock()))
        with patch.object(schema_context, 'introspect_ai_schema', return_value=([], [], [])), \
                patch.object(schema_context, 'format_ai_schema_context', side_effect=['Alpha', 'Beta']):
            self.assertEqual(schema_context.get_ai_schema_context(connection_factory=first), 'Alpha')
            self.assertEqual(schema_context.get_ai_schema_context(connection_factory=second), 'Beta')
        schema_context.clear_ai_schema_context_cache()
