import asyncio
from contextlib import contextmanager
import json
from functools import lru_cache
import inspect
import os
from pathlib import Path
import re
import requests
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid

from fastapi import HTTPException, Request
from pydantic import ValidationError
from digues_webapp.ai import (
    AiChatResult,
    AiConsultedSource,
    AiExternalSource,
    AiServiceError,
    MISTRAL_MODEL,
    chat_with_mistral,
)
from digues_webapp.ai_memory import (
    AI_RECENT_MESSAGES_MAX,
    AI_RECENT_MESSAGES_MAX_CHARS,
)
from digues_webapp.database import WebDatabaseError
from digues_webapp.data_views import (
    HOME_VIEW_FIELDS,
    HOME_VIEW_ROOT,
    VIEW_FIELDS_BY_KEY,
    build_view_query,
)
from digues_webapp.models import (
    AiChatRequest,
    DataHomeViewQueryRequest,
    DataViewQueryRequest,
    DesordreCreate,
    DigueCreate,
    DigueUpdate,
    DesordreGeometryUpdate,
    LineEndpoints,
    PointDesordreUpdate,
    PointReperageUpdate,
    SystemeEndiguementCreate,
    SystemeEndiguementUpdate,
    TronconCreate,
    TronconUpdate,
)
from digues_webapp.queries import (
    DIGUE_DETAIL_SQL,
    DESORDRE_OBSERVATIONS_SQL,
    DESORDRES_GEOJSON_SQL,
    OBSERVATION_DETAIL_SQL,
    POINT_DESORDRE_SQL,
    LINE_DESORDRE_SQL,
    POLYGON_DESORDRE_SQL,
    SYSTEMES_ENDIGUEMENT_SQL,
    SYSTEME_ENDIGUEMENT_DETAIL_SQL,
    TRONCON_DETAIL_SQL,
    TRONCONS_GEOJSON_SQL,
    create_digue,
    create_desordre,
    create_systeme_endiguement,
    create_troncon,
    DesordreCreationError,
    fetch_desordres,
    fetch_desordre_observations,
    fetch_desordre,
    fetch_line_desordre,
    fetch_observation,
    fetch_point_desordre,
    fetch_systemes_endiguement,
    fetch_troncons,
    HeritageCreationError,
    HeritageNotFoundError,
    update_point_desordre,
    update_point_reperage,
    update_line_desordre_geometry,
    update_line_desordre_endpoints,
    update_digue,
    update_systeme_endiguement,
    update_troncon,
    LineDesordreUpdateError,
    PointReperageUpdateError,
    PointReperageUnavailableError,
    PointDesordreUpdateError,
)

from digues_webapp.prompts import SYSTEM_PROMPT
from digues_webapp.schema_context import (
    AI_SCHEMA_CACHE_TTL_SECONDS,
    AI_SCHEMA_COLUMNS_SQL,
    AI_SCHEMA_EXCLUDED_OBJECTS,
    AI_SCHEMA_FOREIGN_KEYS_SQL,
    AI_SCHEMA_NAMES,
    AI_SCHEMA_OBJECTS,
    AI_SCHEMA_PRIMARY_KEYS_SQL,
    AiSchemaUnavailableError,
    clear_ai_schema_context_cache,
    format_ai_schema_context,
    get_ai_schema_context,
    introspect_ai_schema,
)
from digues_webapp.vigicrues import (
    VigicruesDataError,
    VigicruesServiceUnavailableError,
)
from digues_webapp.weather import (
    WeatherDataUnavailableError,
    WeatherServiceUnavailableError,
)

try:
    from digues_webapp.app import (
        FRONTEND_DIRECTORY,
        RequestValidationError,
        app,
        web_show_uuid,
    )
except ModuleNotFoundError as exc:
    if exc.name != "fastapi":
        raise
    FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"
    app = None
    web_show_uuid = None


EMPTY_COLLECTION = {"type": "FeatureCollection", "features": []}


@lru_cache(maxsize=1)
def mounted_map_html():
    """Inspect the same rendered controls as the real map, not retired HTML."""
    return subprocess.check_output(["node", "webapp/tests/map-form-snapshot.cjs"],
        cwd=FRONTEND_DIRECTORY.parents[1], text=True)


@lru_cache(maxsize=1)
def mounted_mode_states():
    return json.loads(subprocess.check_output(["node", "webapp/tests/map-form-snapshot.cjs", "modes"],
        cwd=FRONTEND_DIRECTORY.parents[1], text=True))


class FakeCursor:
    def __init__(self, result):
        self.result = result
        self.query = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.query = query
        self.params = params

    def fetchone(self):
        return (self.result,)

    def fetchall(self):
        if "technique.metadonnees_base" in self.query:
            return [(3950,)]
        raise AssertionError("fetchall inattendu pour ce faux curseur")


class FakeConnection:
    def __init__(self, result):
        self.cursor_instance = FakeCursor(result)

    def cursor(self):
        return self.cursor_instance


class FakeSchemaCursor:
    def __init__(self, results):
        self.results = iter(results)
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchall(self):
        return next(self.results)


class FakeSchemaConnection:
    def __init__(self, results):
        self.cursor_instance = FakeSchemaCursor(results)

    def cursor(self):
        return self.cursor_instance


class AiSchemaContextTest(unittest.TestCase):
    columns = [
        ("public", "troncons", "TABLE", "geometry", "geometry(LineString,3950)", True),
        ("public", "troncons", "TABLE", "digue_id", "uuid", False),
        ("public", "troncons", "TABLE", "id", "uuid", False),
        ("public", "digues", "TABLE", "id", "uuid", False),
        ("public", "desordres_points_saisie", "VIEW", "id", "uuid", True),
    ]
    primary_keys = [
        ("public", "troncons", "id"),
        ("public", "digues", "id"),
    ]
    foreign_keys = [
        (
            "public",
            "troncons",
            "digue_id",
            "public",
            "digues",
            "id",
            "troncons_digues_fk",
            1,
        )
    ]

    def tearDown(self):
        clear_ai_schema_context_cache()

    def test_introspection_reads_catalog_metadata_only_with_explicit_allowlist(self):
        connection = FakeSchemaConnection([
            self.columns,
            self.primary_keys,
            self.foreign_keys,
        ])
        result = introspect_ai_schema(connection)
        self.assertEqual(result, (self.columns, self.primary_keys, self.foreign_keys))
        self.assertEqual(len(connection.cursor_instance.executions), 3)
        for query, parameters in connection.cursor_instance.executions:
            normalized = " ".join(query.lower().split())
            self.assertIn("pg_catalog", normalized)
            self.assertNotIn("select *", normalized)
            self.assertIn(list(AI_SCHEMA_NAMES), parameters)
            self.assertIn(list(AI_SCHEMA_OBJECTS), parameters)
            self.assertIn(list(AI_SCHEMA_EXCLUDED_OBJECTS), parameters)

        self.assertEqual(AI_SCHEMA_NAMES, ("public", "referentiel", "technique"))
        self.assertNotIn("source_v2", AI_SCHEMA_NAMES)
        self.assertIn("spatial_ref_sys", AI_SCHEMA_EXCLUDED_OBJECTS)
        self.assertNotIn("users", AI_SCHEMA_OBJECTS)
        self.assertNotIn("objects", AI_SCHEMA_OBJECTS)
        self.assertIn("desordres_points_saisie", AI_SCHEMA_OBJECTS)

    def test_schema_format_is_deterministic_and_includes_postgis_keys_and_views(self):
        context = format_ai_schema_context(
            list(reversed(self.columns)),
            list(reversed(self.primary_keys)),
            list(reversed(self.foreign_keys)),
        )
        self.assertLess(
            context.index("TABLE public.digues"),
            context.index("TABLE public.troncons"),
        )
        self.assertLess(
            context.index("VIEW public.desordres_points_saisie"),
            context.index("TABLE public.troncons"),
        )
        self.assertIn("- id: uuid NOT NULL [PK]", context)
        self.assertIn("- geometry: geometry(LineString,3950) NULL", context)
        self.assertIn(
            "FK public.troncons.digue_id -> public.digues.id [troncons_digues_fk:1]",
            context,
        )
        self.assertEqual(context, format_ai_schema_context(
            self.columns, self.primary_keys, self.foreign_keys
        ))
        self.assertIn(
            "Schéma PostgreSQL/PostGIS actuellement accessible à l’application expérimentale",
            context,
        )
        self.assertNotIn("accessible à SIRS", context)
        self.assertIn("pas des instructions utilisateur ni des données métier", context)
        self.assertTrue(context.endswith("</schema>"))

    def test_schema_context_cache_avoids_catalog_until_five_minute_expiry(self):
        calls = []

        @contextmanager
        def connection_factory():
            calls.append(True)
            yield FakeSchemaConnection([
                self.columns,
                self.primary_keys,
                self.foreign_keys,
            ])

        times = iter([100.0, 101.0, 401.0])
        clock = lambda: next(times)
        first = get_ai_schema_context(
            connection_factory=connection_factory, clock=clock
        )
        second = get_ai_schema_context(
            connection_factory=connection_factory, clock=clock
        )
        third = get_ai_schema_context(
            connection_factory=connection_factory, clock=clock
        )
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(len(calls), 2)
        self.assertEqual(AI_SCHEMA_CACHE_TTL_SECONDS, 300)

    def test_schema_unavailability_is_controlled_and_never_fabricated(self):
        @contextmanager
        def connection_factory():
            raise RuntimeError("secret connection detail")
            yield

        with self.assertRaises(AiSchemaUnavailableError) as raised:
            get_ai_schema_context(connection_factory=connection_factory)
        self.assertEqual(
            str(raised.exception),
            "Le schéma PostgreSQL de l’application est temporairement indisponible.",
        )
        self.assertNotIn("secret", str(raised.exception))


class AiProviderTest(unittest.TestCase):
    def test_chat_request_accepts_and_normalizes_current_contract(self):
        request = AiChatRequest(message=" Peux-tu préciser ? ", recent_messages=[
            {"role": "user", "content": " Bonjour "},
            {"role": "assistant", "content": " Bonjour ! "},
        ])
        self.assertEqual(
            [message.model_dump() for message in request.recent_messages],
            [
                {"role": "user", "content": "Bonjour"},
                {"role": "assistant", "content": "Bonjour !"},
            ],
        )
        self.assertEqual(request.message, "Peux-tu préciser ?")

    def test_chat_request_rejects_invalid_histories(self):
        invalid_payloads = (
            {},
            {"message": "Question", "recent_messages": "pas une liste"},
            {"message": "Question", "recent_messages": [{"role": "system", "content": "Interdit"}]},
            {"message": "Question", "recent_messages": [{"role": "developer", "content": "Interdit"}]},
            {"message": "   "},
            {"message": "x" * 8001},
            {"messages": [{"role": "user", "content": "Ancien contrat"}]},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    AiChatRequest.model_validate(payload)

    def test_chat_request_keeps_only_recent_messages_within_server_limits(self):
        recent_messages = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": str(index)}
            for index in range(AI_RECENT_MESSAGES_MAX)
        ]
        request = AiChatRequest(message="Question", recent_messages=recent_messages)
        self.assertEqual(len(request.recent_messages), AI_RECENT_MESSAGES_MAX)
        self.assertEqual(request.recent_messages[0].content, "0")
        self.assertEqual(request.message, "Question")

        with self.assertRaises(ValidationError):
            AiChatRequest(message="Question", recent_messages=[
                *recent_messages,
                {"role": "user", "content": "trop"},
            ])

        large_messages = [
            {"role": "assistant", "content": "a" * 8000}
            for _ in range(5)
        ]
        request = AiChatRequest(message="u" * 8000, recent_messages=large_messages)
        self.assertEqual(len(request.recent_messages), 5)
        self.assertEqual(request.message, "u" * 8000)
        self.assertGreater(
            sum(len(message.content) for message in request.recent_messages),
            AI_RECENT_MESSAGES_MAX_CHARS,
        )

    def test_mistral_call_is_mocked_and_answer_is_normalized(self):
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {
            "choices": [{"message": {"content": "  Bonjour depuis Mistral.  "}}]
        }

        with (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch("digues_webapp.ai.requests.post", return_value=response) as post,
        ):
            messages = [
                {"role": "user", "content": "Bonjour"},
                {"role": "assistant", "content": "Bonjour !"},
                {"role": "user", "content": "Peux-tu préciser ?"},
            ]
            schema_context = "<schema>\nTABLE public.systemes\n</schema>"
            answer = chat_with_mistral(
                messages[:-1], schema_context, current_message=messages[-1]["content"]
            )

        self.assertEqual(answer.answer, "Bonjour depuis Mistral.")
        self.assertEqual(answer.executed_queries, ())

        request = post.call_args

        self.assertEqual(
            request.args[0],
            "https://api.mistral.ai/v1/chat/completions",
        )

        payload = request.kwargs["json"]

        self.assertEqual(payload["model"], MISTRAL_MODEL)
        self.assertEqual(len(payload["messages"]), len(messages) + 1)

        self.assertEqual(
            payload["messages"][0],
            {
                "role": "system",
                "content": f"{SYSTEM_PROMPT}\n\n{schema_context}",
            },
        )
        system_content = payload["messages"][0]["content"]
        self.assertTrue(system_content.startswith(SYSTEM_PROMPT))
        self.assertGreater(system_content.index(schema_context), 0)
        response_style = system_content.split("## Style de réponse", 1)[1]
        self.assertIn(
            "n’échappe pas les `|` séparant les colonnes",
            response_style,
        )
        self.assertIn(
            "place l’en-tête, la ligne de séparation et chaque ligne de données "
            "sur des lignes distinctes",
            response_style,
        )
        self.assertIn(
            "Cette application est un prototype expérimental inspiré de SIRS Digues",
            system_content,
        )
        self.assertIn("Elle n’est pas SIRS Digues", system_content)
        self.assertIn(
            "données réellement obtenues depuis PostgreSQL/PostGIS",
            system_content,
        )
        self.assertIn("contexte de schéma fourni", system_content)
        self.assertNotIn("assistant IA de SIRS, une application", system_content)
        self.assertNotIn("utilisateurs de SIRS", system_content)

        self.assertEqual(
            payload["messages"][1:],
            messages,
        )

        self.assertEqual(
            request.kwargs["headers"]["Authorization"],
            "Bearer test-key",
        )

    def test_mistral_answer_preserves_markdown_table_newlines(self):
        table = "\n".join([
            "| **Outil** | **Rôle** | **Public cible** | **Statut** |",
            "|---|---|---|---|",
            "| **QGIS** | Outil de contrôle. | Experts SIG. | Complémentaire. |",
            "| **Application web** | Interface métier. | Gestionnaires. | Interface cible. |",
        ])
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {
            "choices": [{"message": {"content": f"\n{table}\n"}}]
        }

        with (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch("digues_webapp.ai.requests.post", return_value=response),
        ):
            answer = chat_with_mistral([], "schema", current_message="Fais un tableau")

        self.assertEqual(answer.answer, table)
        self.assertIn("|\n|---|", answer.answer)
        self.assertNotIn("| |---|", answer.answer)

    def test_mistral_provider_errors_are_transformed_without_response_body(self):
        cases = (
            (401, "L’authentification de l’assistant IA a échoué."),
            (429, "limite de requêtes"),
            (503, "temporairement indisponible"),
        )
        for status_code, expected in cases:
            with self.subTest(status_code=status_code):
                response = Mock(status_code=status_code, ok=False)
                response.text = "provider-secret-detail"
                with (
                    patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
                    patch("digues_webapp.ai.requests.post", return_value=response),
                    self.assertRaises(AiServiceError) as raised,
                ):
                    chat_with_mistral([], "schema", current_message="Bonjour")
                self.assertIn(expected, str(raised.exception))
                self.assertNotIn(response.text, str(raised.exception))

    def test_mistral_timeout_and_invalid_response_are_transformed(self):
        with (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch(
                "digues_webapp.ai.requests.post",
                side_effect=requests.Timeout(),
            ),
            self.assertRaises(AiServiceError) as timeout,
        ):
            chat_with_mistral([], "schema", current_message="Bonjour")
        self.assertEqual(timeout.exception.status_code, 504)

        with (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch(
                "digues_webapp.ai.requests.post",
                side_effect=requests.ConnectionError(),
            ),
            self.assertRaisesRegex(AiServiceError, "temporairement indisponible"),
        ):
            chat_with_mistral([], "schema", current_message="Bonjour")

        response = Mock(status_code=200, ok=True)
        response.json.return_value = {"choices": []}
        with (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch("digues_webapp.ai.requests.post", return_value=response),
            self.assertRaisesRegex(AiServiceError, "réponse.*invalide"),
        ):
            chat_with_mistral([], "schema", current_message="Bonjour")


@unittest.skipIf(app is None, "FastAPI indisponible dans l’environnement de test")
class WebApplicationTest(unittest.TestCase):
    def test_application_metadata_uses_digues_app_identity(self):
        self.assertEqual(app.title, "digues-app — API")
        self.assertNotIn("SIRS", app.title)

    def test_application_exposes_expected_routes(self):
        paths = {route.path for route in app.routes}
        self.assertTrue(
            {
                "/",
                "/api/troncons",
                "/api/troncons/options",
                "/api/troncons/{troncon_id}/reperage-options",
                "/api/config",
                "/api/ai/chat",
                "/api/meteo/precipitations",
                "/api/meteo/precipitations/echances",
                "/api/systemes-endiguement",
                "/api/systemes-endiguement/{systeme_id}",
                "/api/digues",
                "/api/digues/{digue_id}",
                "/api/troncons/{troncon_id}",
                "/api/desordres",
                "/api/data/desordres",
                "/api/data/desordres/query",
                "/api/data/view-catalog",
                "/api/data/view-query",
                "/api/data/export-csv",
                "/api/data/export-gpkg",
                "/api/referentiels/types-desordre",
                "/api/referentiels/categories-desordre",
                "/api/referentiels/{catalog}",
                "/api/desordres/{desordre_id}",
                "/api/desordres/{desordre_id}/observations",
                "/api/desordres/{desordre_id}/reperage",
                "/api/desordres/{desordre_id}/geometry",
                "/api/desordres/{desordre_id}/endpoints",
                "/api/observations/{observation_id}",
            }
            <= paths
        )
        point_methods = {
            method
            for route in app.routes
            if route.path == "/api/desordres/{desordre_id}"
            for method in route.methods
        }
        self.assertTrue({"GET", "PUT"} <= point_methods)
        observation_methods = {
            method
            for route in app.routes
            if route.path == "/api/observations/{observation_id}"
            for method in route.methods
        }
        self.assertEqual(observation_methods, {"GET", "PUT"})
        methods_by_path = {
            path: {
                method
                for route in app.routes
                if route.path == path
                for method in route.methods
            }
            for path in (
                "/api/systemes-endiguement",
                "/api/digues",
                "/api/troncons",
            )
        }
        self.assertTrue({"GET", "POST"} <= methods_by_path["/api/systemes-endiguement"])
        self.assertEqual(methods_by_path["/api/digues"], {"POST"})
        self.assertTrue({"GET", "POST"} <= methods_by_path["/api/troncons"])
        for path in (
            "/api/systemes-endiguement/{systeme_id}",
            "/api/digues/{digue_id}",
            "/api/troncons/{troncon_id}",
        ):
            methods = {
                method
                for route in app.routes
                if route.path == path
                for method in route.methods
            }
            self.assertEqual(methods, {"GET", "PUT"})
        desordre_methods = {
            method
            for route in app.routes
            if route.path == "/api/desordres"
            for method in route.methods
        }
        self.assertTrue({"GET", "POST"} <= desordre_methods)
        ai_methods = {
            method
            for route in app.routes
            if route.path == "/api/ai/chat"
            for method in route.methods
        }
        self.assertEqual(ai_methods, {"POST"})
        weather_methods = {
            method
            for route in app.routes
            if route.path == "/api/meteo/precipitations"
            for method in route.methods
        }
        self.assertEqual(weather_methods, {"GET"})
        forecast_step_methods = {
            method
            for route in app.routes
            if route.path == "/api/meteo/precipitations/echances"
            for method in route.methods
        }
        self.assertEqual(forecast_step_methods, {"GET"})

    def test_weather_routes_preserve_per_step_and_grid_provenance(self):
        routes = {
            route.path: route.endpoint
            for route in app.routes
            if "GET" in getattr(route, "methods", set())
        }
        steps = {
            "model": "MIXED",
            "run": None,
            "forecast_steps": [{
                "index": 0,
                "label": "J+1",
                "valid_time": "2026-10-03T00:00:00Z",
                "model": "AROME",
                "run": "2026-10-01T18:00:00Z",
                "lead_hours": 30,
                "status": "ready",
            }],
        }
        grid = {
            "model": "AROME",
            "run": "2026-10-01T18:00:00Z",
            "valid_time": "2026-10-03T00:00:00Z",
            "lead_hours": 30,
            "accumulation_hours": 24,
            "unit": "mm",
            "cells": [],
        }
        with patch(
            "digues_webapp.app.fetch_precipitation_forecast_steps",
            return_value=steps,
        ), patch(
            "digues_webapp.app.fetch_precipitations",
            return_value=grid,
        ):
            self.assertEqual(
                routes["/api/meteo/precipitations/echances"](connection=object()),
                steps,
            )
            self.assertEqual(
                routes["/api/meteo/precipitations"](
                    valid_time="2026-10-03T00:00:00Z",
                    connection=object(),
                ),
                grid,
            )

    def test_frontend_pages_use_french_routes(self):
        routes = {
            route.path: route
            for route in app.routes
            if "GET" in getattr(route, "methods", set())
        }

        self.assertIn("/", routes)
        self.assertIn("/carte", routes)
        self.assertIn("/base", routes)

        root_response = routes["/"].endpoint()
        self.assertEqual(root_response.status_code, 200)
        self.assertEqual(root_response.path, FRONTEND_DIRECTORY / "map.html")

        map_response = routes["/carte"].endpoint()
        self.assertEqual(map_response.path, FRONTEND_DIRECTORY / "map.html")

        data_response = routes["/base"].endpoint()
        self.assertEqual(data_response.path, FRONTEND_DIRECTORY / "data.html")

    def test_business_routes_return_feature_collections(self):
        routes = {
            route.path: route
            for route in app.routes
            if "GET" in getattr(route, "methods", set())
        }
        for path in ("/api/troncons", "/api/desordres"):
            response = routes[path].endpoint(FakeConnection(EMPTY_COLLECTION))
            self.assertEqual(response, EMPTY_COLLECTION)
            self.assertEqual(
                routes[path].response_class.media_type,
                "application/geo+json",
            )

    def test_data_disorders_route_uses_the_fixed_generic_home_view(self):
        route = next(
            route for route in app.routes
            if route.path == "/api/data/desordres" and "GET" in route.methods
        )
        payload = {"root": "desordres", "columns": [], "rows": []}
        connection = object()
        with patch(
            "digues_webapp.app.execute_home_view", return_value=payload
        ) as execute:
            self.assertEqual(route.endpoint(connection), payload)
        execute.assert_called_once_with(connection, [], 0, 2_000)

    def test_data_view_route_forwards_the_explicit_root(self):
        route = next(
            route for route in app.routes
            if route.path == "/api/data/view-query" and "POST" in route.methods
        )
        request = DataViewQueryRequest(root="troncons", fields=[
            {"table": "troncons", "field": "libelle"},
        ])
        connection = object()
        payload = {"root": "troncons", "columns": [], "rows": []}
        with patch(
            "digues_webapp.app.execute_view_query", return_value=payload
        ) as execute:
            self.assertEqual(route.endpoint(request, connection), payload)
        execute.assert_called_once_with(
            connection, "troncons", request.fields, [], 0, 2_000
        )

    def test_data_home_query_keeps_configuration_internal(self):
        route = next(
            route for route in app.routes
            if route.path == "/api/data/desordres/query" and "POST" in route.methods
        )
        request = DataHomeViewQueryRequest(
            hidden_troncon_ids=["00000000-0000-0000-0000-000000000001"],
            offset=2_000,
            page_size=500,
        )
        connection = object()
        payload = {"root": "desordres", "columns": [], "rows": []}
        with patch(
            "digues_webapp.app.execute_home_view", return_value=payload
        ) as execute:
            self.assertEqual(route.endpoint(request, connection), payload)
        execute.assert_called_once_with(
            connection, request.hidden_troncon_ids, 2_000, 500
        )
        with self.assertRaises(ValidationError):
            DataHomeViewQueryRequest(root="troncons")

    def test_data_view_route_reports_parallel_branches_as_a_business_error(self):
        route = next(
            route for route in app.routes
            if route.path == "/api/data/view-query" and "POST" in route.methods
        )
        request = DataViewQueryRequest(root="troncons", fields=[
            {"table": "desordres", "field": "designation"},
            {"table": "equipements_mesure", "field": "designation"},
        ])
        with self.assertRaises(Exception) as raised:
            route.endpoint(request, object())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Désordres", raised.exception.detail)
        self.assertIn("Équipements de mesure", raised.exception.detail)


@unittest.skipIf(app is None, "FastAPI indisponible dans l’environnement de test")
class AiEndpointTest(unittest.TestCase):
    @staticmethod
    def endpoint():
        return next(
            route.endpoint for route in app.routes if route.path == "/api/ai/chat"
        )

    def test_empty_message_is_rejected_without_provider_call(self):
        with patch("digues_webapp.app.chat_with_mistral") as chat:
            with self.assertRaises(ValidationError):
                AiChatRequest(message="   ")
        chat.assert_not_called()

    def test_missing_api_key_returns_explicit_safe_error(self):
        with (
            patch.dict(os.environ, {"MISTRAL_API_KEY": ""}),
            patch("digues_webapp.ai.requests.post") as post,
            patch("digues_webapp.app.get_ai_schema_context", return_value="schema"),
            self.assertRaises(AiServiceError) as raised,
        ):
            self.endpoint()(AiChatRequest(message="Bonjour"))
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            str(raised.exception),
            "L’assistant IA n’est pas configuré sur le serveur.",
        )
        post.assert_not_called()

    def test_endpoint_returns_normalized_answer_and_successful_queries_only(self):
        with (
            patch(
                "digues_webapp.app.chat_with_mistral",
                return_value=AiChatResult(
                    answer="Réponse simulée",
                    executed_queries=("SELECT 1", "SELECT 1"),
                    conversation_summary="Décision antérieure.",
                    recent_messages=(
                        {"role": "user", "content": "Suite"},
                        {"role": "assistant", "content": "Réponse simulée"},
                    ),
                    consulted_sources=(
                        AiConsultedSource(
                            title="Architecture SIRS",
                            path="docs/architecture.md",
                            heading="Backend web",
                        ),
                    ),
                    external_sources=(
                        AiExternalSource(
                            title="Guide public",
                            organization="Cerema",
                            url="https://www.cerema.fr/fr/guide",
                            source_id="cerema",
                            category="TECHNICAL_PUBLIC",
                            document_date=None,
                        ),
                    ),
                ),
            ) as chat,
            patch(
                "digues_webapp.app.get_ai_schema_context",
                return_value="CONTEXTE SCHEMA",
            ),
        ):
            response = self.endpoint()(AiChatRequest(
                message=" Suite ",
                recent_messages=[
                    {"role": "user", "content": " Bonjour "},
                    {"role": "assistant", "content": " Bonjour ! "},
                ],
            ))
        self.assertEqual(response, {
            "answer": "Réponse simulée",
            "conversation_summary": "Décision antérieure.",
            "recent_messages": [
                {"role": "user", "content": "Suite"},
                {"role": "assistant", "content": "Réponse simulée"},
            ],
            "executed_queries": [{"sql": "SELECT 1"}, {"sql": "SELECT 1"}],
            "consulted_sources": [{
                "title": "Architecture SIRS",
                "path": "docs/architecture.md",
                "heading": "Backend web",
            }],
            "external_sources": [{
                "title": "Guide public",
                "organization": "Cerema",
                "url": "https://www.cerema.fr/fr/guide",
                "source_id": "cerema",
                "category": "TECHNICAL_PUBLIC",
                "document_date": None,
            }],
        })
        serialized = json.dumps(response)
        for forbidden in ("columns", "rows", "tool_call_id", "MISTRAL_API_KEY"):
            self.assertNotIn(forbidden, serialized)
        chat.assert_called_once_with(
            [
                {"role": "user", "content": "Bonjour"},
                {"role": "assistant", "content": "Bonjour !"},
            ],
            "CONTEXTE SCHEMA",
            conversation_summary="",
            current_message="Suite",
        )

    def test_endpoint_preserves_markdown_table_newlines_in_answer_json(self):
        table = "\n".join([
            "| **Outil** | **Rôle** | **Public cible** | **Statut** |",
            "|---|---|---|---|",
            "| **QGIS** | Outil de contrôle. | Experts SIG. | Complémentaire. |",
            "| **Application web** | Interface métier. | Gestionnaires. | Interface cible. |",
        ])
        with (
            patch(
                "digues_webapp.app.chat_with_mistral",
                return_value=AiChatResult(answer=table, executed_queries=()),
            ),
            patch("digues_webapp.app.get_ai_schema_context", return_value="schema"),
        ):
            response = self.endpoint()(AiChatRequest(message="Tableau"))

        self.assertEqual(response["answer"], table)
        self.assertIn("|\n|---|", response["answer"])
        self.assertNotIn("| |---|", response["answer"])
        serialized = json.dumps(response, ensure_ascii=False)
        self.assertIn("\\n|---|", serialized)
        self.assertNotIn("| |---|", serialized)

    def test_endpoint_returns_empty_query_list_without_tool_call(self):
        with (
            patch(
                "digues_webapp.app.chat_with_mistral",
                return_value=AiChatResult(
                    answer="Réponse directe", executed_queries=()
                ),
            ),
            patch("digues_webapp.app.get_ai_schema_context", return_value="schema"),
        ):
            response = self.endpoint()(AiChatRequest(message="Question générale"))
        self.assertEqual(
            response,
            {
                "answer": "Réponse directe",
                "conversation_summary": "",
                "recent_messages": [],
                "executed_queries": [],
                "consulted_sources": [],
                "external_sources": [],
            },
        )

    def test_provider_error_is_returned_without_stack_trace(self):
        with (
            patch(
                "digues_webapp.app.chat_with_mistral",
                side_effect=AiServiceError("Impossible de joindre le service IA."),
            ),
            patch("digues_webapp.app.get_ai_schema_context", return_value="schema"),
            self.assertRaises(AiServiceError) as raised,
        ):
            self.endpoint()(AiChatRequest(message="Bonjour"))
        handler = app.exception_handlers[AiServiceError]
        response = asyncio.run(handler(None, raised.exception))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            json.loads(response.body),
            {"detail": "Impossible de joindre le service IA."},
        )

    def test_schema_error_is_returned_without_database_details(self):
        error = AiSchemaUnavailableError(
            "Le schéma PostgreSQL SIRS est temporairement indisponible."
        )
        handler = app.exception_handlers[AiSchemaUnavailableError]
        response = asyncio.run(handler(None, error))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.body),
            {"detail": "L’assistant IA est temporairement indisponible."},
        )

    def test_boundary_errors_keep_technical_details_out_of_responses(self):
        cases = (
            (
                WebDatabaseError("postgresql://user:secret@internal/db"),
                503,
                "La base de données est temporairement indisponible.",
            ),
            (
                WeatherDataUnavailableError("coverage-id=private-run"),
                502,
                "Les données météo demandées sont indisponibles.",
            ),
            (
                VigicruesDataError("invalid payload at internal URL"),
                502,
                "Les données Vigicrues reçues sont invalides.",
            ),
        )
        for error, status_code, message in cases:
            with self.subTest(error=type(error).__name__):
                handler = app.exception_handlers[type(error)]
                response = asyncio.run(handler(None, error))
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(json.loads(response.body), {"detail": message})
                self.assertNotIn("internal", response.body.decode())
                self.assertNotIn("secret", response.body.decode())

    def test_external_service_messages_preserve_timeout_and_unavailability(self):
        cases = (
            (
                WeatherServiceUnavailableError("Météo-France n’a pas répondu à temps."),
                "Météo-France n’a pas répondu à temps.",
            ),
            (
                VigicruesServiceUnavailableError(
                    "Vigicrues est temporairement indisponible."
                ),
                "Vigicrues est temporairement indisponible.",
            ),
        )
        for error, message in cases:
            handler = app.exception_handlers[type(error)]
            response = asyncio.run(handler(None, error))
            self.assertEqual(json.loads(response.body), {"detail": message})

    def test_request_validation_error_is_short_and_french(self):
        error = RequestValidationError([
            {
                "type": "missing",
                "loc": ("body", "libelle"),
                "msg": "Field required",
                "input": {},
            }
        ])
        handler = app.exception_handlers[RequestValidationError]
        request = Request({"type": "http", "method": "POST", "path": "/api/ai/chat", "headers": []})
        response = asyncio.run(handler(request, error))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            json.loads(response.body),
            {"detail": "Un champ obligatoire est manquant."},
        )

    def test_unknown_heritage_update_returns_not_found(self):
        error = HeritageNotFoundError("Le tronçon est introuvable.")
        handler = app.exception_handlers[HeritageNotFoundError]
        response = asyncio.run(handler(None, error))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            json.loads(response.body),
            {"detail": "Le tronçon est introuvable."},
        )


class WebAssetsAndQueriesTest(unittest.TestCase):
    def test_frontend_files_exist(self):
        self.assertTrue((FRONTEND_DIRECTORY / "map.html").is_file())
        self.assertTrue((FRONTEND_DIRECTORY / "css" / "app.css").is_file())
        self.assertTrue((FRONTEND_DIRECTORY / "js" / "map.js").is_file())
        self.assertTrue((FRONTEND_DIRECTORY / "js" / "top-navigation.js").is_file())
        self.assertTrue((FRONTEND_DIRECTORY / "js" / "sql-tool.js").is_file())

    def test_frontend_displays_backend_errors_without_action_prefixes(self):
        map_script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(
            encoding="utf-8"
        )
        sql_script = (FRONTEND_DIRECTORY / "js" / "sql-tool.js").read_text(
            encoding="utf-8"
        )
        for prefix in (
            "Vigicrues indisponible : ${error.message}",
            "Météo indisponible : ${error.message}",
            "Lecture impossible : ${error.message}",
            "Chargement impossible : ${error.message}",
            "Création refusée : ${error.message}",
            "Géométrie refusée : ${error.message}",
            "Enregistrement refusé : ${error.message}",
            "Validation refusée : ${error.message}",
        ):
            self.assertNotIn(prefix, map_script)
        self.assertIn("heritageCreateMessage.textContent = error.message", map_script)
        self.assertIn('sqlErrorMessage(payload, "La requête SQL a échoué.")', sql_script)
        self.assertNotIn("SQLSTATE ${error.sqlstate}", sql_script)

    def test_frontend_http_error_fallbacks_are_user_readable(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test des erreurs frontend.")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(
            encoding="utf-8"
        )
        source = (
            "function errorDetail"
            + script.split("function errorDetail", 1)[1]
            .split("async function fetchJson", 1)[0]
        )
        program = source + r'''
process.stdout.write(JSON.stringify({
  detail: errorDetail({ detail: "Le libellé est obligatoire." }, "fallback"),
  empty: errorDetail({ detail: "  " }, httpErrorFallback(503)),
  missing: httpErrorFallback(404),
  invalid: httpErrorFallback(422),
  timeout: httpErrorFallback(504),
}));
'''
        result = json.loads(subprocess.run(
            [node, "-e", program],
            check=True,
            capture_output=True,
            text=True,
        ).stdout)
        self.assertEqual(result["detail"], "Le libellé est obligatoire.")
        self.assertEqual(result["empty"], "Le serveur est temporairement indisponible.")
        self.assertEqual(result["missing"], "La ressource demandée est introuvable.")
        self.assertEqual(result["invalid"], "La demande a été refusée.")
        self.assertEqual(result["timeout"], "Le serveur n’a pas répondu à temps.")

    def test_error_styles_use_color_without_specific_bold_weight(self):
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        for selector in (".sql-result-error", "#status.error", ".editor-message.error"):
            blocks = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", css)
            self.assertTrue(blocks)
            self.assertIn("color:", "\n".join(blocks))
            self.assertNotIn("font-weight:", "\n".join(blocks))
        ai_error = re.search(r"\.ai-message-error\s*\{([^}]*)\}", css)
        self.assertIn("border:", ai_error.group(1))
        self.assertIn("color:", ai_error.group(1))

    def test_frontend_map_url_crosshair_and_directions_contract(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")

        self.assertIn('class="map-crosshair" aria-hidden="true"', page)
        self.assertIn('id="open-directions"', page)
        self.assertIn("pointer-events: none", css)
        self.assertIn("function mapViewportFromUrl", script)
        self.assertIn('parameters.get("lat")', script)
        self.assertIn('parameters.get("lon")', script)
        self.assertIn('parameters.get("zoom")', script)
        self.assertIn("window.history.replaceState", script)
        self.assertNotIn("window.history.pushState", script)
        self.assertIn("map.on(\"moveend\", syncMapViewportUrl)", script)
        self.assertIn("restoreInitialMapViewport();\n  revealMapAfterInitialViewport();", script)
        self.assertIn(
            "https://waze.com/ul?ll=${center.lat},${center.lng}&navigate=yes",
            script,
        )
        self.assertIn('"noopener,noreferrer"', script)

        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test URL de carte frontend.")
        source = "\n".join(
            "function " + name + script.split("function " + name, 1)[1].split(end, 1)[0]
            for name, end in (
                ("mapViewportFromUrl", "const initialMapViewport"),
                ("restoreInitialMapViewport", "function syncMapViewportUrl"),
                ("syncMapViewportUrl", 'window.addEventListener("digues-app:url-mode-changed"'),
                ("openDirections", "function renderUserPositionStatus"),
            )
        )
        program = r'''
const replaceCalls = [];
const openedUrls = [];
const listeners = {};
const EntityNavigation = require("./webapp/frontend/js/entity-navigation.js");
const window = {
  location: {
    href: "https://example.test/?keep=value&lat=50.5&lon=2.25&zoom=11",
    search: "?keep=value&lat=50.5&lon=2.25&zoom=11",
    pathname: "/", hash: "",
  },
  history: { state: { source: "test" }, replaceState(...args) { replaceCalls.push(args); } },
  open(...args) { openedUrls.push(args); },
};
let mapViewportReady = false;
let initialMapViewportApplied = false;
let initialEntityTarget = null;
let mapEntityNavigationComplete = true;
let selectedMapUrlTarget = null;
const map = {
  center: { lat: 50.5, lng: 2.25 },
  zoom: 11,
  on(name, callback) { listeners[name] = callback; },
  getCenter() { return this.center; },
  getZoom() { return this.zoom; },
  setView(center, zoom, options) { this.setViewCall = { center, zoom, options }; },
};
const directionsButton = { addEventListener(name, callback) { this.listener = { name, callback }; } };
''' + source + r'''
const initialMapViewport = mapViewportFromUrl();
const valid = mapViewportFromUrl("?lat=50.5&lon=2.25&zoom=11");
const invalid = mapViewportFromUrl("?lat=95&lon=2.25&zoom=11");
const empty = mapViewportFromUrl("?lat=&lon=2.25&zoom=11");
const restored = restoreInitialMapViewport();
syncMapViewportUrl();
const beforeReady = replaceCalls.length;
mapViewportReady = true;
syncMapViewportUrl();
openDirections();
process.stdout.write(JSON.stringify({
  valid,
  invalid,
  empty,
  restored,
  restoredView: map.setViewCall,
  beforeReady,
  syncedUrl: replaceCalls[0][2].toString(),
  directions: openedUrls[0],
}));
'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["valid"], {"latitude": 50.5, "longitude": 2.25, "zoom": 11})
        self.assertIsNone(result["invalid"])
        self.assertIsNone(result["empty"])
        self.assertTrue(result["restored"])
        self.assertEqual(
            result["restoredView"],
            {"center": [50.5, 2.25], "zoom": 11, "options": {"animate": False}},
        )
        self.assertEqual(result["beforeReady"], 0)
        self.assertIn("lat=50.500000", result["syncedUrl"])
        self.assertIn("lon=2.250000", result["syncedUrl"])
        self.assertIn("zoom=11", result["syncedUrl"])
        self.assertEqual(
            result["directions"],
            [
                "https://waze.com/ul?ll=50.5,2.25&navigate=yes",
                "_blank",
                "noopener,noreferrer",
            ],
        )

    def test_queries_transform_to_4326_without_updating_geometry(self):
        for query in (TRONCONS_GEOJSON_SQL, DESORDRES_GEOJSON_SQL):
            normalized = " ".join(query.lower().split())
            self.assertIn("st_transform(", normalized)
            self.assertIn("4326", normalized)
            self.assertNotIn(" update ", f" {normalized} ")

    def test_desordres_collection_exposes_existing_troncon_links(self):
        normalized = " ".join(DESORDRES_GEOJSON_SQL.lower().split())
        self.assertIn("'troncon_ids'", normalized)
        self.assertIn("public.desordres__troncons", normalized)
        self.assertIn("where lien.desordre_id = d.id", normalized)
        self.assertIn("'[]'::jsonb", normalized)

    def test_data_disorders_home_configuration_uses_generic_view_engine(self):
        fields = [
            VIEW_FIELDS_BY_KEY[f"{field.table}.{field.field}"]
            for field in HOME_VIEW_FIELDS
        ]
        query, granularity = build_view_query(HOME_VIEW_ROOT, fields)
        normalized = " ".join(query.lower().split())
        self.assertEqual(granularity, "desordres")
        self.assertIn("from public.desordres as d", normalized)
        self.assertIn("public.desordres__troncons", normalized)
        self.assertIn("join public.troncons", normalized)
        self.assertIn("left join public.digues", normalized)
        self.assertIn("left join public.systemes", normalized)
        self.assertIn("st_asgeojson(st_transform(d.geometry, 4326))", normalized)
        self.assertNotIn("geometry is not null", normalized)

    def test_query_result_is_a_feature_collection(self):
        connection = FakeConnection(EMPTY_COLLECTION)
        self.assertEqual(fetch_troncons(connection), EMPTY_COLLECTION)
        self.assertIn("public.troncons", connection.cursor_instance.query)

        serialized = json.dumps(EMPTY_COLLECTION)
        connection = FakeConnection(serialized)
        self.assertEqual(fetch_desordres(connection), EMPTY_COLLECTION)
        self.assertIn("public.desordres", connection.cursor_instance.query)

    def test_frontend_uses_native_single_marker_dragging(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn("L.marker(latlng", script)
        self.assertIn("activePointLayer.dragging.enable()", script)
        self.assertIn('layer.on("dragend"', script)
        self.assertIn('stageDisorderGeometry({type:"Point",coordinates:[provisionalLatLng.lng,provisionalLatLng.lat]})', script)
        self.assertNotIn("Leaflet.Draw", script)
        self.assertNotIn("L.circleMarker", script)

    def test_frontend_has_heritage_tree_visibility_and_zoom_actions(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        heritage_script = (
            FRONTEND_DIRECTORY / "js" / "heritage-panel.js"
        ).read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        self.assertIn('id="toggle-heritage"', header)
        self.assertIn('id="heritage-panel"', page)
        self.assertIn('id="heritage-tree"', page)
        self.assertNotIn('id="zoom-troncon"', page)
        self.assertNotIn("zoomTronconButton", script)
        self.assertNotIn("#zoom-troncon", css)
        self.assertIn("zoomControl: false", script)
        self.assertIn('fetchJson("/api/systemes-endiguement")', heritage_script)
        self.assertIn("tronconLayersById", script)
        self.assertIn('visibility.className = "tree-visibility"', heritage_script)
        self.assertIn('zoom.className = "tree-zoom"', heritage_script)
        self.assertIn("onZoom: zoomToHeritageObject", script)
        # C5 transfers the actual fitBounds guarantee to Chromium: center on
        # the canonical Point, bounded zoom, and no action for absent geometry.
        self.assertIn("hiddenTronconIds", script)
        self.assertIn("tronconsGeoJsonLayer.removeLayer(layer)", script)
        self.assertIn("tronconsGeoJsonLayer.addLayer(layer)", script)
        self.assertIn("function applyIndividualDesordreVisibility()", script)
        self.assertIn("hiddenTronconIds.has(id)", script)
        self.assertIn("group.removeLayer(layer)", script)
        self.assertIn("group.addLayer(layer)", script)
        self.assertGreaterEqual(script.count("applyIndividualDesordreVisibility();"), 6)
        self.assertIn("if (input.checked) layer.addTo(map)", script)
        self.assertIn("else map.removeLayer(layer)", script)

    def test_heritage_tree_actions_derive_visibility_and_aggregate_zoom_bounds(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test de l’arbre Patrimoine.")
        program = r'''
const HeritagePanel = require("./webapp/frontend/js/heritage-panel.js");
class Element {
  constructor(tagName = "div") {
    this.tagName = tagName; this.children = []; this.listeners = {};
    this.attributes = {}; this.dataset = {}; this.className = "";
    this.checked = false; this.indeterminate = false; this.disabled = false;
    this.hidden = false; this.value = ""; this.textContent = "";
    this.classList = {
      add: (...names) => { this.className += " " + names.join(" "); },
      remove: (...names) => {
        this.className = this.className.split(/\s+/)
          .filter((name) => name && !names.includes(name)).join(" ");
      },
    };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  querySelectorAll(selector) {
    const matches = [];
    const visit = (element) => {
      if (selector === ".tree-name" && String(element.className).includes("tree-name")) {
        matches.push(element);
      }
      (element.children || []).forEach(visit);
    };
    this.children.forEach(visit);
    return matches;
  }
  click() { this.listeners.click?.({ stopPropagation() {} }); }
}
const elements = {};
[
  "heritage-panel", "toggle-heritage", "close-heritage", "heritage-tree",
  "heritage-loading",
].forEach((id) => { elements[id] = new Element(); });
const rootElement = { querySelector(selector) { return elements[selector.slice(1)]; } };
global.document = {
  createElement: (tag) => new Element(tag),
  createElementNS: (_namespace, tag) => new Element(tag),
};
const hidden = new Set();
const visibilitySnapshots = [];
const zooms = [];
let selected = null;
const controller = HeritagePanel.createController({
  rootElement,
  hiddenTronconIds: hidden,
  canZoom: (ids) => ids.length > 0,
  onZoom: (_kind, _item, ids) => zooms.push(ids),
  onSelection: (value) => { selected = value; },
  onVisibilityChange: () => visibilitySnapshots.push([...hidden].sort()),
});
const t1 = { id: "t1", libelle: "T1", valid: true };
const t2 = { id: "t2", libelle: "T2", valid: true };
const d1 = { id: "d1", libelle: "D1", valid: true, troncons: [t1, t2] };
const s1 = { id: "s1", libelle: "SE1", valid: true, digues: [d1] };
controller.render({ systemes: [s1] });
function find(className) {
  const values = [];
  const visit = (element) => {
    if (String(element.className).includes(className)) values.push(element);
    (element.children || []).forEach(visit);
  };
  visit(elements["heritage-tree"]);
  return values;
}
const visibility = find("tree-visibility");
const zoom = find("tree-zoom");
const names = find("tree-name");
visibility[0].listeners.click({ stopPropagation() {} });
zoom[3].listeners.click({ stopPropagation() {} });
names[0].click();
process.stdout.write(JSON.stringify({
  descendants: HeritagePanel.descendantTronconIds("Système d'endiguement", s1),
  hidden: [...hidden].sort(),
  visibilitySnapshots,
  zooms,
  selectedKind: selected.kind,
  selectedLabel: selected.item.libelle,
}));
'''
        result = json.loads(subprocess.check_output(
            [node, "-e", program], cwd=FRONTEND_DIRECTORY.parents[1], text=True
        ))
        self.assertEqual(result["descendants"], ["t1", "t2"])
        self.assertEqual(result["hidden"], ["t1", "t2"])
        self.assertEqual(result["visibilitySnapshots"], [["t1", "t2"]])
        self.assertEqual(result["zooms"], [["t2"]])
        self.assertEqual(result["selectedKind"], "Système d'endiguement")
        self.assertEqual(result["selectedLabel"], "SE1")

    def test_frontend_has_queries_main_view_and_docked_ai_states(self):
        map_page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(encoding="utf-8")
        map_script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        data_script = (
            FRONTEND_DIRECTORY / "js" / "data-view.js"
        ).read_text(encoding="utf-8")
        navigation_script = (
            FRONTEND_DIRECTORY / "js" / "top-navigation.js"
        ).read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")

        for expected in ('id="toggle-queries"', 'aria-controls="queries-view"'):
            self.assertIn(expected, header)
        for expected in (
            'id="queries-view"', 'id="close-queries"', 'id="sql-query-form"',
            'id="sql-query-editor"', 'id="sql-query-submit"',
            'id="sql-result-table"', 'src="/static/js/sql-tool.js"',
        ):
            self.assertIn(expected, data_page)
        for forbidden in ('id="queries-view"', "sql-tool.js"):
            self.assertNotIn(forbidden, map_page)
        for page in (map_page, data_page):
            for expected in (
                'id="ai-panel"', 'id="close-ai"', 'id="ai-conversation"', 'id="ai-chat-form"',
                'id="ai-message"', 'id="ai-send"', "ai-panel.js",
            ):
                self.assertIn(expected, page)
        self.assertIn('id="toggle-ai"', header)
        self.assertIn('aria-controls="ai-panel"', header)

        self.assertNotIn("setQueriesViewOpen", map_script)
        self.assertIn("function setQueriesOpen(open)", data_script)
        self.assertIn('queriesView.hidden = centralMode !== "sql"', data_script)
        self.assertIn("TopNavigation.createController", map_script)
        self.assertIn("TopNavigation.createController", data_script)
        self.assertIn('entry.closeButton?.addEventListener("click"', navigation_script)
        self.assertIn("map.invalidateSize()", map_script)
        self.assertIn("display: flex", css.split(".app-workspace", 1)[1].split("}", 1)[0])
        self.assertIn(
            "flex: 0 0 min(var(--side-panel-width), var(--side-panel-max-width))",
            css.split(".ai-panel {", 1)[1].split("}", 1)[0],
        )
        self.assertIn(
            "flex-basis: min(33.333%, 32rem)",
            css.split('main[data-layout="split-docked"] .ai-panel {', 1)[1]
            .split("}", 1)[0],
        )
        self.assertIn('class="queries-view tool-panel"', data_page)
        self.assertIn('class="ai-panel tool-panel"', map_page)
        self.assertIn('class="ai-panel tool-panel"', data_page)

    def test_frontend_lot_one_layout_guards_are_present(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        navigation_script = (
            FRONTEND_DIRECTORY / "js" / "top-navigation.js"
        ).read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        portrait_rules = re.search(
            r"@media \(orientation: portrait\) and \(max-width: 1024px\)\s*\{"
            r"\s*\.create-menu-list,.*?\.settings-menu-list\s*\{([^}]*)\}",
            css, re.S,
        )
        landscape_rules = re.search(
            r"@media \(orientation: landscape\) and \(max-width: 1024px\),"
            r"\s*\(orientation: landscape\) and \(max-height: 620px\)\s*\{"
            r"\s*\.create-menu-list,.*?\.settings-menu-list\s*\{([^}]*)\}",
            css, re.S,
        )
        self.assertIsNotNone(portrait_rules)
        self.assertIsNotNone(landscape_rules)
        portrait_rules = portrait_rules.group(1)
        landscape_rules = landscape_rules.group(1)

        self.assertNotIn(".primary-area.queries-open", css)
        modal_css = (FRONTEND_DIRECTORY / "css" / "modal.css").read_text(encoding="utf-8")
        self.assertIn("max-block-size: calc(100dvh - 2rem)", modal_css)
        self.assertIn(
            "overflow-y: auto",
            modal_css.split(".photo-editor-dialog {", 1)[1].split("}", 1)[0],
        )
        self.assertIn(
            "z-index: 2800",
            css.split(".pwa-update-notice {", 1)[1].split("}", 1)[0],
        )
        self.assertIn("function positionCompactLandscapeMenu", script)
        self.assertIn("appHeader.getBoundingClientRect().bottom", script)
        self.assertIn(
            "new ResizeObserver(syncCompactLandscapeMenus).observe(appHeader)", script
        )
        self.assertIn('appNavigationViewport.addEventListener("scroll"', script)
        self.assertIn('window.addEventListener("resize"', script)
        self.assertIn("function fixedMenuPosition", navigation_script)
        self.assertIn("position: fixed", portrait_rules)
        self.assertIn(
            "top: var(--compact-menu-top, var(--app-header-height))",
            portrait_rules,
        )
        self.assertIn("width: 100%", portrait_rules)
        self.assertIn("position: fixed", landscape_rules)
        self.assertIn("width: min(18rem, calc(100vw - 1rem))", landscape_rules)
        self.assertNotIn("width: 100%", landscape_rules)

    def test_responsive_workspace_and_map_controls_structure(self):
        from html.parser import HTMLParser

        class Parents(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.parents = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                identity = attrs.get("id", attrs.get("class", tag))
                if "id" in attrs:
                    self.parents[attrs["id"]] = list(self.stack)
                if tag not in {"input", "meta", "link", "img", "br", "hr"}:
                    self.stack.append(identity)

            def handle_endtag(self, tag):
                if self.stack:
                    self.stack.pop()

        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        parser = Parents()
        parser.feed(page)
        workspace_children = (
            "heritage-panel",
            "primary-area",
            "editor-panel",
            "ai-panel",
        )
        for child in workspace_children:
            self.assertEqual(parser.parents[child][-1], "app-workspace")
        self.assertLess(
            page.index('id="heritage-panel"'), page.index('id="primary-area"')
        )
        self.assertLess(
            page.index('id="primary-area"'), page.index('id="editor-panel"')
        )
        self.assertLess(
            page.index('id="editor-panel"'), page.index('id="ai-panel"')
        )
        self.assertEqual(parser.parents["map"][-1], "primary-area")
        self.assertEqual(parser.parents["editor-map-interaction"][-1], "primary-area")
        for control in ("map-legend", "weather-timeline", "open-directions"):
            expected_parent = "map-action-buttons" if control == "open-directions" else "map-bottom-layout"
            self.assertEqual(parser.parents[control][-1], expected_parent)
            self.assertIn("map-bottom-layout", parser.parents[control])
            self.assertIn("map-bottom-controls", parser.parents[control])
        self.assertNotIn("primary-area", parser.parents["heritage-panel"])
        self.assertNotIn("primary-area", parser.parents["editor-panel"])
        data_parser = Parents()
        data_parser.feed(data_page)
        self.assertNotIn("map-bottom-controls", data_parser.parents["queries-view"])
        self.assertIn('grid-template-areas: "legend timeline directions"', css)
        self.assertIn('grid-template-areas: "timeline timeline" "legend directions"', css)
        self.assertIn("@container map-controls (max-width: 620px)", css)
        self.assertIn("minmax(0, max-content) minmax(0, 1fr) auto", css)
        hidden_rule = css.split("#map[hidden],", 1)[1].split("}", 1)[0]
        self.assertIn(".weather-timeline[hidden]", hidden_rule)
        self.assertIn("display: none", hidden_rule)
        self.assertIn("row-gap: 0", css)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr)", css)
        self.assertIn(".heritage-panel {", css)
        self.assertIn(".map-page .editor-panel", css)
        heritage_panel = css.split(".heritage-panel {", 1)[1].split("}", 1)[0]
        self.assertIn("position: absolute", heritage_panel)
        self.assertIn("left: 0", heritage_panel)
        self.assertIn('id="heritage-selection-summary"', page)
        self.assertIn('id="heritage-selection-summary"', data_page)
        self.assertIn('main[data-layout="single-tool"].ai-open .primary-area', css)
        self.assertIn('main[data-layout="split-balanced"].queries-open.ai-open .ai-panel', css)
        self.assertIn("flex-basis: 50%", css)
        # Patrimoine is an overlay. Opening it must not narrow the docked
        # editor (the real rectangle/invalidation cases live in map-panel-layout).
        editor_panel = css.split(".map-page .editor-panel {", 1)[1].split("}", 1)[0]
        self.assertIn("flex: 0 1 min(var(--side-panel-width), var(--side-panel-max-width))", editor_panel)
        self.assertNotIn(
            '.heritage-open.editor-open .editor-panel {', css,
        )
        self.assertIn(
            ".map-page main.heritage-open.editor-open .endpoint-grid", css
        )
        self.assertNotIn("left: calc(min(26rem, 100%) + 1rem)", css)
        self.assertNotIn("right: calc(min(26rem, 100%) + 1rem)", css)
        self.assertIn("@container queries (min-width: 960px)", css)
        self.assertIn("main:not(.ai-open) .sql-tool", css)
        self.assertIn("grid-template-columns: minmax(0, 2fr) minmax(0, 3fr)", css)
        self.assertIn(".sql-result-table-wrapper { grid-row: 4; }", css)
        self.assertIn("appRoot.dataset.layout = mode", script)
        self.assertIn("topNavigation.setLayout(appWorkspace.clientWidth", script)
        self.assertIn('topNavigation[open ? "open" : "close"]("editor")', script)
        self.assertNotIn("editorPanel.hidden = false", script)
        self.assertNotIn("editorPanel.hidden = true", script)

    def test_frontend_sql_tool_contract_is_safe_and_read_only(self):
        page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "sql-tool.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")

        self.assertIn('maxlength="50000"', page)
        self.assertIn('(globalThis.BusinessRuntime?.fetch || fetch)("/api/sql/query"', script)
        self.assertIn('(globalThis.BusinessRuntime?.fetch || fetch)("/api/sql/presets"', script)
        self.assertIn('method: "POST"', script)
        self.assertIn("event.ctrlKey && event.key === \"Enter\"", script)
        self.assertIn("sqlQueryForm.requestSubmit()", script)
        self.assertIn('sqlQueryStatus.textContent = pending ? "Exécution…" : ""', script)
        self.assertIn("sqlQuerySubmit.disabled = pending", script)
        self.assertIn("header.textContent = String(column)", script)
        self.assertIn("cell.textContent = sqlCellText", script)
        self.assertIn("result.truncated", script)
        self.assertIn("sqlResultError.textContent", script)
        self.assertIn("sqlQueryEditor.value = preset.sql", script)
        self.assertIn("new Blob([csvContentForResult(currentSqlResult)]", script)
        self.assertIn('type: "text/csv;charset=utf-8"', script)
        self.assertIn('return `\\uFEFF${lines.join("\\r\\n")}\\r\\n`', script)
        self.assertNotIn("requestSubmit()", script.split("function selectSqlPreset", 1)[1].split("}", 1)[0])
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("MISTRAL", script)
        self.assertIn("#sql-query-editor", css)
        self.assertIn("#sql-result-table", css)

        queries_view_css = css.split(".queries-view {", 1)[1].split("}", 1)[0]
        sql_tool_css = css.split(".sql-tool {", 1)[1].split("}", 1)[0]
        sql_query_form_css = css.split(".sql-query-form {", 1)[1].split("}", 1)[0]
        sql_query_editor_css = css.split(
            "#sql-query-editor {", 1
        )[1].split("}", 1)[0]
        sql_results_css = css.split(".sql-results {", 1)[1].split("}", 1)[0]
        table_wrapper_css = css.split(
            ".sql-result-table-wrapper {", 1
        )[1].split("}", 1)[0]
        self.assertIn("min-height: 0", queries_view_css)
        self.assertIn("overflow: hidden", queries_view_css)
        self.assertIn("min-height: 0", sql_tool_css)
        self.assertIn("overflow: hidden", sql_tool_css)
        self.assertIn(
            "grid-template-rows: minmax(0, 1fr) minmax(0, 1fr)",
            sql_tool_css,
        )
        self.assertIn(
            "grid-template-rows: auto auto minmax(0, 1fr) auto",
            sql_query_form_css,
        )
        self.assertIn("min-height: 0", sql_query_form_css)
        self.assertIn("overflow: hidden", sql_query_form_css)
        self.assertIn("height: 100%", sql_query_editor_css)
        self.assertIn("min-height: 0", sql_query_editor_css)
        self.assertIn("resize: none", sql_query_editor_css)
        self.assertNotIn("min-height: 11rem", sql_query_editor_css)
        self.assertIn("minmax(0, 1fr)", sql_results_css)
        self.assertIn("overflow: hidden", sql_results_css)
        self.assertIn("min-height: 0", table_wrapper_css)
        self.assertIn("overflow: auto", table_wrapper_css)
        self.assertNotIn("max-height: 100%", table_wrapper_css)

    def test_frontend_sql_tool_renders_success_error_and_pending_state(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test de l’outil SQL.")
        source = (FRONTEND_DIRECTORY / "js" / "sql-tool.js").read_text(
            encoding="utf-8"
        )
        program = r'''
class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
    this.value = "";
    this.href = "";
    this.download = "";
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  querySelector(selector) {
    if (selector === "thead") return this.thead;
    if (selector === "tbody") return this.tbody;
    return null;
  }
  click() { this.clicked = true; }
}
const elements = new Map();
[
  "sql-query-form", "sql-query-editor", "sql-query-submit", "sql-query-status",
  "sql-preset-select", "sql-preset-description", "sql-export-csv",
  "sql-results", "sql-result-summary", "sql-result-error", "sql-result-empty",
  "sql-result-table-wrapper", "sql-result-table", "sql-result-actions",
].forEach((id) => elements.set(`#${id}`, new FakeElement()));
const table = elements.get("#sql-result-table");
table.thead = new FakeElement("thead");
table.tbody = new FakeElement("tbody");
global.document = {
  querySelector(selector) { return elements.get(selector) || null; },
  createElement(tagName) { return new FakeElement(tagName); },
};
''' + source + r'''
(async () => {
  let queryFetchCalls = 0;
  global.fetch = async (url) => {
    if (url === "/api/sql/presets") {
      return {
        ok: true,
        status: 200,
        async json() {
          return [{
            id: "demo_rows",
            label: "Test — 10 lignes",
            description: "Retourne 10 lignes de démonstration.",
            sql: "SELECT 10;",
          }];
        },
      };
    }
    queryFetchCalls += 1;
    throw new Error("appel SQL inattendu");
  };
  await loadSqlPresets();
  sqlPresetSelect.value = "demo_rows";
  sqlPresetSelect.listeners.change({});
  const preset = {
    sql: sqlQueryEditor.value,
    description: sqlPresetDescription.textContent,
    descriptionHidden: sqlPresetDescription.hidden,
    queryFetchCalls,
  };

  sqlQueryEditor.value = "SELECT '<img src=x onerror=alert(1)>' AS value";
  let resolveFetch;
  global.fetch = () => new Promise((resolve) => { resolveFetch = resolve; });
  const request = executeSqlQuery();
  const pending = {
    buttonDisabled: sqlQuerySubmit.disabled,
    editorDisabled: sqlQueryEditor.disabled,
    status: sqlQueryStatus.textContent,
    ariaBusy: sqlQueryForm.attributes["aria-busy"],
  };
  resolveFetch({
    ok: true,
    status: 200,
    async json() {
      return {
        columns: ["value"],
        rows: [["<img src=x onerror=alert(1)>"]],
        rowCount: 1,
        durationMs: 37,
        truncated: true,
      };
    },
  });
  await request;
  const success = {
    summary: sqlResultSummary.textContent,
    summaryHidden: sqlResultSummary.hidden,
    tableHidden: sqlResultTableWrapper.hidden,
    header: table.thead.children[0].children[0].textContent,
    cell: table.tbody.children[0].children[0].textContent,
    cellChildren: table.tbody.children[0].children[0].children.length,
    buttonDisabled: sqlQuerySubmit.disabled,
    exportDisabled: sqlExportCsv.disabled,
  };

  renderSqlResult({
    columns: ["libellé", "valeur"],
    rows: [["été; \"chaud\"\nligne 2", null]],
    rowCount: 1,
    durationMs: 1,
    truncated: false,
  });
  let exportedBlob = null;
  let revokedUrl = null;
  global.URL = {
    createObjectURL(blob) { exportedBlob = blob; return "blob:csv"; },
    revokeObjectURL(url) { revokedUrl = url; },
  };
  sqlExportCsv.listeners.click({});
  const csvBytes = new Uint8Array(await exportedBlob.arrayBuffer());
  const csv = {
    content: await exportedBlob.text(),
    bom: Array.from(csvBytes.slice(0, 3)),
    revokedUrl,
    filename: sqlExportFilename(new Date(2026, 8, 6, 12, 34, 56)),
  };
  const protectedCsvCells = [
    "=1+1", "+1+1", "-1+1", "@SUM(A1:A2)", "   =1+1",
    "texte normal", "", null, "Été", 'lieu; "dit"\nligne 2', 42, -7,
  ].map(csvCellText);

  sqlQueryEditor.disabled = false;
  global.fetch = async () => { throw new Error("indisponible"); };
  await loadSqlPresets();
  const presetFailure = {
    editorDisabled: sqlQueryEditor.disabled,
    selectDisabled: sqlPresetSelect.disabled,
    message: sqlPresetDescription.textContent,
  };

  let submitCalls = 0;
  let prevented = false;
  sqlQueryForm.requestSubmit = () => { submitCalls += 1; };
  sqlQueryEditor.listeners.keydown({
    ctrlKey: true,
    key: "Enter",
    preventDefault() { prevented = true; },
  });

  global.fetch = async () => ({
    ok: false,
    status: 400,
    async json() {
      return { error: { message: "colonne inconnue", sqlstate: "42703", position: 15 } };
    },
  });
  await executeSqlQuery();
  const failure = {
    hidden: sqlResultError.hidden,
    message: sqlResultError.textContent,
    buttonDisabled: sqlQuerySubmit.disabled,
    ariaBusy: sqlQueryForm.attributes["aria-busy"],
  };
  process.stdout.write(JSON.stringify({
    preset, pending, success, csv, protectedCsvCells,
    presetFailure, submitCalls, prevented, failure
  }));
})();
'''
        result = json.loads(subprocess.check_output(
            [node, "-e", program], text=True, encoding="utf-8"
        ))
        self.assertEqual(result["pending"], {
            "buttonDisabled": True,
            "editorDisabled": True,
            "status": "Exécution…",
            "ariaBusy": "true",
        })
        self.assertEqual(result["preset"], {
            "sql": "SELECT 10;",
            "description": "Retourne 10 lignes de démonstration.",
            "descriptionHidden": False,
            "queryFetchCalls": 0,
        })
        self.assertEqual(result["success"]["summary"], "1 ligne · 37 ms · résultat tronqué")
        self.assertFalse(result["success"]["summaryHidden"])
        self.assertFalse(result["success"]["tableHidden"])
        self.assertEqual(result["success"]["header"], "value")
        self.assertEqual(result["success"]["cell"], "<img src=x onerror=alert(1)>")
        self.assertEqual(result["success"]["cellChildren"], 0)
        self.assertFalse(result["success"]["buttonDisabled"])
        self.assertFalse(result["success"]["exportDisabled"])
        self.assertEqual(
            result["csv"]["content"],
            '"libellé";"valeur"\r\n'
            '"été; ""chaud""\nligne 2";""\r\n',
        )
        self.assertEqual(result["csv"]["bom"], [0xEF, 0xBB, 0xBF])
        self.assertEqual(result["csv"]["revokedUrl"], "blob:csv")
        self.assertEqual(result["protectedCsvCells"], [
            "'=1+1",
            "'+1+1",
            "'-1+1",
            "'@SUM(A1:A2)",
            "'   =1+1",
            "texte normal",
            "",
            "",
            "Été",
            'lieu; "dit"\nligne 2',
            "42",
            "-7",
        ])
        self.assertEqual(
            result["csv"]["filename"], "resultats-sql-20260906-123456.csv"
        )
        self.assertEqual(result["presetFailure"], {
            "editorDisabled": False,
            "selectDisabled": True,
            "message": "Vous pouvez toujours saisir une requête manuellement.",
        })
        self.assertEqual(result["submitCalls"], 1)
        self.assertTrue(result["prevented"])
        self.assertFalse(result["failure"]["hidden"])
        self.assertEqual(
            result["failure"]["message"],
            "colonne inconnue",
        )
        self.assertFalse(result["failure"]["buttonDisabled"])
        self.assertEqual(result["failure"]["ariaBusy"], "false")

    def test_frontend_has_tools_menu_and_territory_modal(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        for expected in (
            'id="toggle-tools-menu"',
            'aria-controls="tools-menu-list"',
            'id="tools-menu-list"',
            'data-tool-action="territoire-administratif"',
            "Territoire administratif…",
        ):
            self.assertIn(expected, header)
        for expected in (
            'id="territoire-modal"',
            'aria-labelledby="territoire-modal-title"',
            'id="territoire-current-state"',
            'id="territoire-libelle"',
            'id="territoire-file" name="file" type="file" accept=".gpkg,.zip"',
            'id="territoire-layer"',
            'id="submit-territoire"',
        ):
            self.assertIn(expected, page)
        self.assertIn('fetchGeoJSON("/api/territoire-administratif")', script)
        self.assertIn('mapElement.style.visibility = "hidden"', script)
        self.assertIn("maxBoundsViscosity: 1", script)
        self.assertIn("applyTerritoireCartography(historicalViewportBounds)", script)
        self.assertNotIn("map.fitBounds(bounds, { padding: [30, 30], maxZoom: 17 })", script)
        self.assertIn("let territoireAdministratifGeoJSON", script)
        self.assertIn("function setToolsMenuOpen(open)", script)
        self.assertIn("function openTerritoireModal()", script)
        self.assertIn("function submitTerritoireImport()", script)
        self.assertIn(".tools-menu-list", css)
        self.assertIn(".territoire-modal", css)
        self.assertIn(".territoire-dialog", css)

    def test_offline_consultation_is_an_accessible_map_tool_modal(self):
        script = (FRONTEND_DIRECTORY / "js" / "offline-consultation.js").read_text(
            encoding="utf-8"
        )
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(encoding="utf-8")
        operations = (FRONTEND_DIRECTORY / "js" / "offline-operations.js").read_text(
            encoding="utf-8"
        )
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        settings = header.split('function settings()', 1)[1].split(
            "function render", 1
        )[0]
        self.assertNotIn("Consultation hors ligne", settings)
        self.assertIn('data-tool-action="offline-consultation"', header)
        for expected in (
                'id="offline-consultation-modal"',
                'role="dialog"',
                'aria-modal="true"',
                'aria-labelledby="offline-consultation-modal-title"',
                'id="prepare-offline"',
                'id="clear-offline"',
                'id="offline-operations"',
                "Préparer la sélection actuelle",
                "Effacer les données locales",
                "Synchroniser maintenant",
        ):
            self.assertIn(expected, page)
        self.assertEqual(page.count('id="prepare-offline"'), 1)
        self.assertEqual(page.count('id="offline-operations"'), 1)
        self.assertEqual(data_page.count('id="offline-consultation-modal"'), 1)
        self.assertEqual(data_page.count('id="offline-operations"'), 1)
        self.assertIn('data-tool-action="offline-consultation"', data_page)
        self.assertIn('Synchronisation locale', data_page)
        self.assertNotIn('id="prepare-offline"', data_page)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn("event.target === modal", script)
        self.assertIn("returnFocusTarget", script)
        self.assertIn('new CustomEvent("digues-app:offline-dialog-open")', script)
        self.assertIn('root.addEventListener("digues-app:offline-dialog-open"', operations)
        self.assertIn('"X-Digues-App-Local-Projection": "1"', script)
        self.assertIn('"X-Digues-App-Local-Creation": "1"', script)
        self.assertNotIn("X-Sirs-Local-", script)
        self.assertIn("await engine.synchronize(true)", operations)

    def test_frontend_has_map_print_tool_and_modal_defaults(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        print_script = (FRONTEND_DIRECTORY / "js" / "map-print.js").read_text(
            encoding="utf-8"
        )
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        for expected in (
            'data-tool-action="print-map"',
            "Imprimer la carte…",
        ):
            self.assertIn(expected, header)
        for expected in (
            'id="map-print-modal"',
            'id="map-print-form"',
            'name="print-format" value="A4" checked',
            'name="print-format" value="A3"',
            'name="print-orientation" value="portrait" checked',
            'name="print-orientation" value="landscape"',
            'name="print-output" value="pdf" checked',
            'name="print-output" value="png"',
            'name="print-legend" checked',
            'name="print-scale" checked',
            'name="print-north" checked',
            'id="cancel-map-print-modal"',
            'id="submit-map-print"',
            "/static/vendor/html2canvas/html2canvas.min.js",
            "/static/vendor/jspdf/jspdf.umd.min.js",
            "/static/js/map-print.js",
        ):
            self.assertIn(expected, page)
        self.assertIn("function openMapPrintModal()", script)
        self.assertIn("map.getBounds()", script)
        self.assertIn("function closeMapPrintModal()", script)
        self.assertIn("currentMapPrintState()", script)
        self.assertIn('button.dataset.toolAction === "print-map"', script)
        self.assertIn("mapPrintModal.hidden = false", script)
        self.assertIn("mapPrintModal.hidden = true", script)
        self.assertIn("serializeVisibleLayerGroup(map, layer)", script)
        self.assertIn('crossOrigin: "anonymous"', print_script)
        self.assertIn(".map-print-composition", css)
        self.assertIn(".map-print-legend", css)
        self.assertIn(".map-print-scale", css)
        self.assertIn(".map-print-north", css)

    def test_frontend_map_print_bounds_visible_state_and_legend(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test d'impression cartographique.")
        program = r'''
const printTool = require("./webapp/frontend/js/map-print.js");
const formats = [
  ["A4", "portrait"],
  ["A4", "landscape"],
  ["A3", "portrait"],
  ["A3", "landscape"],
].map(([format, orientation]) => {
  const dimensions = printTool.paperDimensions(format, orientation);
  const original = { minX: 1, maxX: 5, minY: 2, maxY: 4 };
  const expanded = printTool.expandProjectedBounds(original, dimensions.aspectRatio);
  return {
    format,
    orientation,
    dimensions,
    expanded,
    contains: expanded.minX <= original.minX
      && expanded.maxX >= original.maxX
      && expanded.minY <= original.minY
      && expanded.maxY >= original.maxY,
    ratio: (expanded.maxX - expanded.minX) / (expanded.maxY - expanded.minY),
  };
});
const leaf = {
  options: { color: "#39735a", weight: 4, interactive: true },
  toGeoJSON() { return { type: "Feature", geometry: { type: "LineString" } }; },
};
const group = { eachLayer(callback) { callback(leaf); } };
const visibleMap = { hasLayer(layer) { return layer === group; } };
const hiddenMap = { hasLayer() { return false; } };
const visible = printTool.serializeVisibleLayerGroup(visibleMap, group);
const hidden = printTool.serializeVisibleLayerGroup(hiddenMap, group);
const legend = printTool.legendItemsForState({
  territoryOutline: [{}],
  troncons: visible,
  disorderPoints: [],
  disorderLines: [{}],
  disorderPolygons: [],
  weather: [{}],
});
console.log(JSON.stringify({ formats, visible, hidden, legend }));
'''
        completed = subprocess.run(
            [node, "-e", program],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(len(result["formats"]), 4)
        for item in result["formats"]:
            self.assertTrue(item["contains"])
            self.assertAlmostEqual(
                item["ratio"], item["dimensions"]["aspectRatio"], places=12
            )
            self.assertGreater(item["dimensions"]["widthPx"], 1000)
            self.assertGreater(item["dimensions"]["heightPx"], 1000)
        self.assertEqual(len(result["visible"]), 1)
        self.assertEqual(result["visible"][0]["style"], {
            "color": "#39735a",
            "weight": 4,
        })
        self.assertEqual(result["hidden"], [])
        self.assertEqual(
            [item["id"] for item in result["legend"]],
            ["territory", "troncons", "disorder-lines", "weather"],
        )

    def test_frontend_map_print_modal_opens_closes_and_snapshots_bounds(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test de modale d'impression.")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        source = (
            "function openMapPrintModal"
            + script.split("function openMapPrintModal", 1)[1]
              .split("function territoireContentType", 1)[0]
        )
        program = r'''
const sourceBounds = {
  isValid() { return true; },
  getSouthWest() { return { lat: 50, lng: 2 }; },
  getNorthEast() { return { lat: 51, lng: 3 }; },
};
const map = { getBounds() { return sourceBounds; }, hasLayer() { return false; } };
const L = {
  latLngBounds(southWest, northEast) {
    return { southWest, northEast, isValid() { return true; } };
  },
};
const mapPrintModal = {
  hidden: true,
  attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; },
};
const mapPrintMessage = {
  textContent: "ancienne erreur",
  classList: { remove() {}, add() {} },
};
const submitMapPrintButton = { focus() { this.focused = true; } };
const mapPrintForm = {
  elements: [],
  reset() { this.wasReset = true; },
  querySelector() { return null; },
};
let mapPrintPending = false;
let mapPrintSourceBounds = null;
const window = { MapPrint: {} };
const territoireMaskLayer = null;
const territoireContourLayer = null;
const tronconsGeoJsonLayer = null;
const desordrePointLayer = null;
const desordreLineLayer = null;
const desordrePolygonLayer = null;
const precipitationLayer = null;
const weatherForecastSession = null;
const selectedWeatherStepIndex = 0;
const WEATHER_LEGEND_CLASSES = [];
function formatWeatherDate(value) { return value; }
'''+ source + r'''
openMapPrintModal();
const afterOpen = {
  hidden: mapPrintModal.hidden,
  focused: submitMapPrintButton.focused,
  copiedBounds: mapPrintSourceBounds !== sourceBounds,
  southWest: mapPrintSourceBounds.southWest,
};
closeMapPrintModal();
console.log(JSON.stringify({
  afterOpen,
  afterClose: {
    hidden: mapPrintModal.hidden,
    reset: mapPrintForm.wasReset,
    boundsCleared: mapPrintSourceBounds === null,
  },
}));
'''
        result = json.loads(subprocess.check_output(
            [node, "-e", program],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
        ))
        self.assertEqual(result["afterOpen"], {
            "hidden": False,
            "focused": True,
            "copiedBounds": True,
            "southWest": {"lat": 50, "lng": 2},
        })
        self.assertEqual(result["afterClose"], {
            "hidden": True,
            "reset": True,
            "boundsCleared": True,
        })

    def test_frontend_map_print_points_keep_exact_position_in_svg(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test des points imprimés.")
        program = r'''
const printTool = require("./webapp/frontend/js/map-print.js");
let generatedPoint = null;
let receivedOriginalLatLng = false;
let serializationPrecision = null;
const originalLatLng = { lat: 50.54823717, lng: 2.394043399 };
const sourceLayer = {
  toGeoJSON(precision) {
    serializationPrecision = precision;
    const coordinates = precision === false
      ? [originalLatLng.lng, originalLatLng.lat]
      : [2.394043, 50.548237];
    return {
      type: "Feature",
      properties: { id: "point-1" },
      geometry: { type: "Point", coordinates },
    };
  },
};
const group = { eachLayer(callback) { callback(sourceLayer); } };
const sourceMap = { hasLayer(layer) { return layer === group; } };
const L = {
  marker() { throw new Error("L'impression ne doit pas créer de marqueur HTML."); },
  circleMarker(latlng, options) {
    generatedPoint = { latlng, options };
    receivedOriginalLatLng = latlng === originalLatLng;
    return generatedPoint;
  },
  geoJSON(geojson, options) {
    const [lng, lat] = geojson.geometry.coordinates;
    originalLatLng.lng = lng;
    originalLatLng.lat = lat;
    options.pointToLayer(null, originalLatLng);
    return { addTo() { return this; } };
  },
};
const serialized = printTool.serializeVisibleLayerGroup(sourceMap, group);
printTool.addSerializedLayers(
  L,
  {},
  "print-business",
  serialized,
  (latlng, pane) => printTool.createPrintDesordrePointMarker(L, latlng, pane),
);
console.log(JSON.stringify({
  serializationPrecision,
  receivedOriginalLatLng,
  serializedCoordinates: serialized[0].geojson.geometry.coordinates,
  latlng: generatedPoint.latlng,
  pane: generatedPoint.options.pane,
  className: generatedPoint.options.className,
  radius: generatedPoint.options.radius,
  interactive: generatedPoint.options.interactive,
}));
'''
        result = json.loads(subprocess.check_output(
            [node, "-e", program],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
        ))
        self.assertIs(result["serializationPrecision"], False)
        self.assertTrue(result["receivedOriginalLatLng"])
        self.assertEqual(result["serializedCoordinates"], [2.394043399, 50.54823717])
        self.assertEqual(
            result["latlng"], {"lat": 50.54823717, "lng": 2.394043399}
        )
        self.assertEqual(result["pane"], "print-business")
        self.assertEqual(result["className"], "map-print-desordre-point")
        self.assertEqual(result["radius"], 8)
        self.assertFalse(result["interactive"])

    def test_frontend_has_weather_legend_disabled_by_default_without_startup_fetch(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        self.assertIn('data-layer-toggle="precipitations"', page)
        self.assertIn("Précipitations prévues", page)
        self.assertNotIn("Précipitations ARPEGE", page)
        self.assertIn('id="weather-legend"', page)
        self.assertIn('id="toggle-weather-legend"', page)
        self.assertIn('aria-controls="weather-legend"', page)
        self.assertIn('aria-expanded="false"', page)
        self.assertEqual(page.count('id="weather-legend"'), 1)
        legend_start = page.index('id="map-legend"')
        weather_start = page.index('class="legend-weather-item"', legend_start)
        self.assertLess(weather_start, page.index('id="weather-legend"', weather_start))
        self.assertLess(page.index('id="weather-legend"', weather_start), page.index('id="weather-timeline"'))
        checkbox = re.search(r'<input[^>]*data-layer-toggle="precipitations"[^>]*>', page).group()
        self.assertNotIn("checked", checkbox)
        self.assertIn('id="weather-timeline"', page)
        self.assertIn('id="weather-timeline-range"', page)
        self.assertIn('id="weather-timeline-provenance"', page)
        self.assertIn('`${selected.model} · cumul 24 h`', script)
        self.assertIn("WEATHER_PANE", script)
        self.assertIn("function togglePrecipitationLayer(input)", script)
        self.assertIn(
            'weatherLegendToggle?.addEventListener("click", toggleWeatherLegend)',
            script,
        )
        self.assertIn('"/api/meteo/precipitations/echances"', script)
        self.assertIn("/api/meteo/precipitations?valid_time=", script)
        self.assertIn('className: "weather-precipitation-cell"', script)
        self.assertIn("stroke: false", script)
        legend_render_source = script.split(
            "function renderWeatherLegend", 1
        )[1].split("function renderPrecipitationLayer", 1)[0]
        self.assertNotIn("Cumul sur 24 h", legend_render_source)
        self.assertNotIn("Échéance :", legend_render_source)
        for label in (
            '"0 mm"', '">0–1 mm"', '"1–5 mm"', '"5–10 mm"',
            '"10–20 mm"', '"20–40 mm"', '">40 mm"',
        ):
            self.assertIn(label, script)
        startup_source = script.split("async function loadMapData()", 1)[1].split(
            "loadMapData();", 1
        )[0]
        self.assertNotIn("/api/meteo/precipitations", startup_source)
        self.assertIn(".weather-legend", css)
        weather_legend_css = css.split(".weather-legend {", 1)[1].split("}", 1)[0]
        for independent_panel_property in (
            "  background:", "  bottom:", "  box-shadow:", "  left:",
            "position: absolute",
        ):
            self.assertNotIn(independent_panel_property, weather_legend_css)
        self.assertIn("padding-left:", weather_legend_css)
        self.assertIn(".weather-timeline", css)
        self.assertIn(".weather-precipitation-cell:focus", css)
        self.assertIn("outline: none", css.split(".weather-precipitation-cell:focus", 1)[1])

    def test_frontend_app_status_manager_prioritizes_sources_and_restores_current_base(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test du statut frontend.")
        status_source = (
            "const APP_STATUS_PRIORITY"
            + script.split("const APP_STATUS_PRIORITY", 1)[1]
              .split("const entityEditor", 1)[0]
        )
        self.assertNotIn("setTimeout", status_source)
        self.assertEqual(script.count("statusElement.textContent"), 1)
        self.assertEqual(script.count('statusElement.classList.add("error")'), 1)
        self.assertEqual(script.count('statusElement.classList.remove("error")'), 1)
        program = r'''
const statusElement = {
  textContent: "Chargement des données…",
  classList: {
    add(name) { this[name] = true; },
    remove(name) { this[name] = false; },
  },
};
''' + status_source + r'''
const initial = statusElement.textContent;
setAppBaseStatus("34 tronçons · 126 désordres");
const weatherLoading = beginAppStatus("weather", "loading", "Météo en cours");
const vigicruesLoading = beginAppStatus("vigicrues", "loading", "Vigicrues en cours");
const latestLoading = statusElement.textContent;
updateAppStatus("weather", weatherLoading, "error", "Erreur météo");
const errorBeforeLoading = statusElement.textContent;
const errorClass = statusElement.classList.error === true;
setAppBaseStatus("3 tronçons · 9 désordres");
const baseHiddenByError = statusElement.textContent;
clearAppStatus("weather", weatherLoading);
const revealedLoading = statusElement.textContent;
clearAppStatus("vigicrues", vigicruesLoading);
const startupStillVisible = statusElement.textContent;
clearAppStatus("startup", startupStatusToken);
const currentBase = statusElement.textContent;
const nonErrorClass = statusElement.classList.error === false;
const staleUpdateAccepted = updateAppStatus(
  "weather", weatherLoading, "error", "Erreur obsolète",
);
const loadingOverCurrentBase = beginAppStatus(
  "weather", "loading", "Nouveau chargement météo",
);
setAppBaseStatus("1 tronçon · 4 désordres");
const changedBaseHiddenByLoading = statusElement.textContent;
clearAppStatus("weather", loadingOverCurrentBase);
const changedBaseAfterLoading = statusElement.textContent;
process.stdout.write(JSON.stringify({
  initial,
  latestLoading,
  errorBeforeLoading,
  errorClass,
  baseHiddenByError,
  revealedLoading,
  startupStillVisible,
  currentBase,
  nonErrorClass,
  staleUpdateAccepted,
  changedBaseHiddenByLoading,
  changedBaseAfterLoading,
}));
'''
        result = json.loads(subprocess.check_output([node, "-"], input=program, text=True))
        self.assertEqual(result["initial"], "Chargement des données…")
        self.assertEqual(result["latestLoading"], "Vigicrues en cours")
        self.assertEqual(result["errorBeforeLoading"], "Erreur météo")
        self.assertTrue(result["errorClass"])
        self.assertEqual(result["baseHiddenByError"], "Erreur météo")
        self.assertEqual(result["revealedLoading"], "Vigicrues en cours")
        self.assertEqual(result["startupStillVisible"], "Chargement des données…")
        self.assertEqual(result["currentBase"], "3 tronçons · 9 désordres")
        self.assertTrue(result["nonErrorClass"])
        self.assertFalse(result["staleUpdateAccepted"])
        self.assertEqual(result["changedBaseHiddenByLoading"], "Nouveau chargement météo")
        self.assertEqual(result["changedBaseAfterLoading"], "1 tronçon · 4 désordres")

    def test_frontend_vigicrues_status_ignores_obsolete_responses_and_errors(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test Vigicrues frontend.")
        status_source = (
            "const APP_STATUS_PRIORITY"
            + script.split("const APP_STATUS_PRIORITY", 1)[1]
              .split("const entityEditor", 1)[0]
        )
        vigicrues_source = (
            "function clearVigicruesLayer"
            + script.split("function clearVigicruesLayer", 1)[1]
              .split("function renderWeatherTimeline", 1)[0]
        )
        program = r'''
const statusElement = {
  textContent: "",
  classList: {
    add(name) { this[name] = true; },
    remove(name) { this[name] = false; },
  },
};
''' + status_source + r'''
let vigicruesStationLayer = null;
let vigicruesRequestVersion = 0;
let currentFeature = { type: "Feature" };
const removedLayers = [];
const addedLayers = [];
const map = { removeLayer(layer) { removedLayers.push(layer); } };
const vigicruesStationIcon = {};
function configureVigicruesStation() {}
const L = {
  marker() { return {}; },
  geoJSON(data) {
    return {
      data,
      addTo(target) { addedLayers.push(this); this.target = target; return this; },
    };
  },
};
function currentTerritoireFeature() { return currentFeature; }
const pending = [];
let fetchGeoJSON = () => new Promise((resolve, reject) => pending.push({ resolve, reject }));
''' + vigicrues_source + r'''
setAppBaseStatus("8 tronçons · 31 désordres");
clearAppStatus("startup", startupStatusToken);

(async () => {
  const input = { checked: true, disabled: false };
  const first = toggleVigicruesLayer(input);
  const firstLoading = statusElement.textContent;
  pending.shift().resolve({ type: "FeatureCollection", features: [{ id: 1 }] });
  await first;
  const firstSuccess = {
    status: statusElement.textContent,
    checked: input.checked,
    added: addedLayers.length,
  };

  input.checked = false;
  await toggleVigicruesLayer(input);
  const disabled = {
    status: statusElement.textContent,
    removed: removedLayers.length,
  };

  clearVigicruesLayer();
  input.checked = true;
  const currentErrorRequest = toggleVigicruesLayer(input);
  pending.shift().reject(new Error("service indisponible"));
  await currentErrorRequest;
  const currentError = {
    status: statusElement.textContent,
    checked: input.checked,
    error: statusElement.classList.error === true,
  };

  input.checked = true;
  const staleResponse = toggleVigicruesLayer(input);
  const staleResponsePending = pending.shift();
  input.checked = false;
  await toggleVigicruesLayer(input);
  staleResponsePending.resolve({ type: "FeatureCollection", features: [{ id: 2 }] });
  await staleResponse;
  const afterStaleResponse = {
    status: statusElement.textContent,
    checked: input.checked,
    hasLayer: vigicruesStationLayer !== null,
  };

  input.checked = true;
  const staleError = toggleVigicruesLayer(input);
  const staleErrorPending = pending.shift();
  input.checked = false;
  await toggleVigicruesLayer(input);
  staleErrorPending.reject(new Error("ancienne erreur"));
  await staleError;
  const afterStaleError = {
    status: statusElement.textContent,
    checked: input.checked,
    error: statusElement.classList.error === true,
  };

  input.checked = true;
  const oldActivation = toggleVigicruesLayer(input);
  const oldPending = pending.shift();
  input.checked = false;
  await toggleVigicruesLayer(input);
  input.checked = true;
  const newActivation = toggleVigicruesLayer(input);
  const newPending = pending.shift();
  oldPending.resolve({ type: "FeatureCollection", features: [{ id: 3 }] });
  await oldActivation;
  const afterOldActivation = {
    status: statusElement.textContent,
    checked: input.checked,
    hasLayer: vigicruesStationLayer !== null,
  };
  newPending.resolve({ type: "FeatureCollection", features: [{ id: 4 }] });
  await newActivation;
  const afterNewActivation = {
    status: statusElement.textContent,
    checked: input.checked,
    hasLayer: vigicruesStationLayer !== null,
  };

  process.stdout.write(JSON.stringify({
    firstLoading,
    firstSuccess,
    disabled,
    currentError,
    afterStaleResponse,
    afterStaleError,
    afterOldActivation,
    afterNewActivation,
  }));
})();
'''
        result = json.loads(subprocess.check_output([node, "-"], input=program, text=True))
        self.assertEqual(result["firstLoading"], "Chargement des stations hydrométriques…")
        self.assertEqual(result["firstSuccess"]["status"], "8 tronçons · 31 désordres")
        self.assertTrue(result["firstSuccess"]["checked"])
        self.assertEqual(result["firstSuccess"]["added"], 1)
        self.assertEqual(result["disabled"]["status"], "8 tronçons · 31 désordres")
        self.assertGreaterEqual(result["disabled"]["removed"], 1)
        self.assertIn("service indisponible", result["currentError"]["status"])
        self.assertFalse(result["currentError"]["checked"])
        self.assertTrue(result["currentError"]["error"])
        self.assertEqual(result["afterStaleResponse"]["status"], "8 tronçons · 31 désordres")
        self.assertFalse(result["afterStaleResponse"]["checked"])
        self.assertFalse(result["afterStaleResponse"]["hasLayer"])
        self.assertEqual(result["afterStaleError"]["status"], "8 tronçons · 31 désordres")
        self.assertFalse(result["afterStaleError"]["checked"])
        self.assertFalse(result["afterStaleError"]["error"])
        self.assertEqual(
            result["afterOldActivation"]["status"],
            "Chargement des stations hydrométriques…",
        )
        self.assertTrue(result["afterOldActivation"]["checked"])
        self.assertFalse(result["afterOldActivation"]["hasLayer"])
        self.assertEqual(result["afterNewActivation"]["status"], "8 tronçons · 31 désordres")
        self.assertTrue(result["afterNewActivation"]["checked"])
        self.assertTrue(result["afterNewActivation"]["hasLayer"])

    def test_frontend_weather_layer_fetches_on_demand_and_reuses_session_cache(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test météo frontend.")
        weather_legend_source = (
            "const WEATHER_LEGEND_CLASSES"
            + script.split("const WEATHER_LEGEND_CLASSES", 1)[1]
              .split("const mapElement", 1)[0]
        )
        weather_source = weather_legend_source + (
            "function precipitationColor"
            + script.split("function precipitationColor", 1)[1]
              .split("function setToolsMenuOpen", 1)[0]
        )
        status_source = (
            "const APP_STATUS_PRIORITY"
            + script.split("const APP_STATUS_PRIORITY", 1)[1]
              .split("const entityEditor", 1)[0]
        )
        program = weather_source + r'''
function element() {
  return {
    hidden: false,
    textContent: "",
    disabled: false,
    checked: false,
    children: [],
    style: {},
    className: "",
    dataset: {},
    attributes: {},
    classList: {
      add(name) { this[name] = true; },
      remove(name) { this[name] = false; },
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] ?? null; },
    replaceChildren() { this.children = []; },
    append(...items) { this.children.push(...items); },
  };
}
const statusElement = element();
''' + status_source + r'''
const weatherLegend = element();
const weatherLegendToggle = element();
weatherLegend.hidden = true;
weatherLegendToggle.hidden = true;
weatherLegendToggle.setAttribute("aria-expanded", "false");
const weatherTimeline = element();
const weatherTimelineLabels = element();
const weatherTimelineRange = element();
const weatherTimelineProvenance = element();
let precipitationLayer = null;
let weatherForecastSession = null;
let precipitationDataByValidTime = new Map();
let weatherFrontendCacheDate = null;
let weatherTerritoryCacheKey = null;
let selectedWeatherStepIndex = 0;
let precipitationRequestPending = false;
let precipitationRequestController = null;
let precipitationRequestVersion = 0;
let currentFeature = null;
let fakeToday = "2026-09-06";
currentCivilDateKey = () => fakeToday;
const WEATHER_PANE = "weather-precipitations";
const document = {
  precipitationInput: element(),
  createElement() { return element(); },
  querySelector(selector) {
    if (selector === '[data-layer-toggle="precipitations"]') {
      return this.precipitationInput;
    }
    return null;
  },
};
const addedGroups = [];
const removedLayers = [];
const rectangles = [];
const map = {
  removeLayer(layer) { removedLayers.push(layer); },
};
const L = {
  layerGroup() {
    return {
      layers: [],
      addTo(target) {
        this.addedTo = target;
        addedGroups.push(this);
        return this;
      },
    };
  },
  rectangle(bounds, options) {
    const rectangle = {
      bounds,
      options,
      tooltip: "",
      bindTooltip(value) {
        this.tooltip = value;
        return this;
      },
      addTo(group) {
        group.layers.push(this);
        rectangles.push(this);
        return this;
      },
    };
    return rectangle;
  },
};
function currentTerritoireFeature() {
  return currentFeature;
}
const calls = [];
const validTimes = [
  "2026-09-06T18:00:00Z",
  "2026-09-07T18:00:00Z",
  "2026-09-08T18:00:00Z",
];
function forecastResponse() {
  const models = ["AROME", "AROME", "ARPEGE"];
  return {
    model: "MIXED",
    product: "P1D",
    cache_date: fakeToday,
    forecast_steps: validTimes.map((validTime, index) => ({
      index,
      label: `J+${index + 1}`,
      valid_time: validTime,
      model: models[index],
      run: index < 2 ? "2026-09-05T18:00:00Z" : "2026-09-05T00:00:00Z",
      lead_hours: (index + 1) * 24,
      status: index === 0 ? "ready" : "loading",
    })),
  };
}
function gridResponse(validTime, value = 2.4) {
  const index = validTimes.indexOf(validTime);
  const model = index < 2 ? "AROME" : "ARPEGE";
  return {
    model,
    product: "P1D",
    run: model === "AROME" ? "2026-09-05T18:00:00Z" : "2026-09-05T00:00:00Z",
    valid_time: validTime,
    lead_hours: (index + 1) * 24,
    accumulation_hours: 24,
    unit: "mm",
    cells: [
      { bounds: [[50.4, 2.45], [50.5, 2.55]], value_mm: value },
      { bounds: [[50.5, 2.45], [50.6, 2.55]], value_mm: null },
    ],
  };
}
let firstForecastResolve = null;
let holdFirstForecast = true;
let fetchJson = async function(url) {
  calls.push(url);
  if (url.endsWith("/echances")) {
    if (holdFirstForecast) {
      return new Promise((resolve) => {
        firstForecastResolve = () => resolve(forecastResponse());
      });
    }
    return forecastResponse();
  }
  const validTime = decodeURIComponent(url.split("valid_time=", 2)[1]);
  return gridResponse(validTime);
};
setAppBaseStatus("3 tronçons · 5 désordres");
clearAppStatus("startup", startupStatusToken);

(async () => {
  const withoutTerritoryInput = document.precipitationInput;
  withoutTerritoryInput.checked = true;
  await togglePrecipitationLayer(withoutTerritoryInput);
  const withoutTerritory = {
    checked: withoutTerritoryInput.checked,
    fetchCalls: calls.length,
    message: statusElement.textContent,
    error: statusElement.classList.error === true,
  };

  currentFeature = { type: "Feature", geometry: { type: "Polygon" } };
  const withTerritoryInput = document.precipitationInput;
  withTerritoryInput.checked = true;
  const firstActivation = togglePrecipitationLayer(withTerritoryInput);
  await Promise.resolve();
  const whileLoading = {
    legendHidden: weatherLegend.hidden,
    toggleHidden: weatherLegendToggle.hidden,
  };
  holdFirstForecast = false;
  firstForecastResolve();
  await firstActivation;
  const afterEnable = {
    fetchCalls: calls.length,
    calls: calls.slice(),
    layers: addedGroups.length,
    rectangles: rectangles.length,
    pane: rectangles[0].options.pane,
    interactive: rectangles[0].options.interactive,
    firstOpacity: rectangles[0].options.fillOpacity,
    nodataOpacity: rectangles[1].options.fillOpacity,
    className: rectangles[0].options.className,
    stroke: rectangles[0].options.stroke,
    legendHidden: weatherLegend.hidden,
    legendToggleHidden: weatherLegendToggle.hidden,
    legendExpanded: weatherLegendToggle.getAttribute("aria-expanded"),
    legendRows: weatherLegend.children.length,
    legendLabels: weatherLegend.children.map((row) => row.children[1].textContent),
    timelineVisible: weatherTimeline.hidden === false,
    timelineLabels: weatherTimelineLabels.children.map((item) => item.textContent),
    timelineMax: weatherTimelineRange.max,
    provenance: weatherTimelineProvenance.textContent,
    status: statusElement.textContent,
  };

  const firstLegendRow = weatherLegend.children[0];
  toggleWeatherLegend();
  const afterLegendOpen = {
    legendHidden: weatherLegend.hidden,
    expanded: weatherLegendToggle.getAttribute("aria-expanded"),
    checked: withTerritoryInput.checked,
  };

  const j2Status = beginAppStatus(
    "weather", "loading", "Chargement des précipitations prévues…",
  );
  await selectWeatherForecastIndex(
    1, withTerritoryInput, prepareWeatherFrontendContext(), j2Status,
  );
  const afterJ2 = {
    fetchCalls: calls.length,
    request: calls.at(-1),
    selected: weatherTimelineRange.value,
    provenance: weatherTimelineProvenance.textContent,
    legendHidden: weatherLegend.hidden,
    legendExpanded: weatherLegendToggle.getAttribute("aria-expanded"),
    legendRebuilt: weatherLegend.children[0] !== firstLegendRow,
    legendColors: weatherLegend.children.map((row) => row.children[0].style.background),
  };
  toggleWeatherLegend();
  const afterLegendClose = {
    legendHidden: weatherLegend.hidden,
    expanded: weatherLegendToggle.getAttribute("aria-expanded"),
    checked: withTerritoryInput.checked,
  };
  const j1Status = beginAppStatus(
    "weather", "loading", "Chargement des précipitations prévues…",
  );
  await selectWeatherForecastIndex(
    0, withTerritoryInput, prepareWeatherFrontendContext(), j1Status,
  );
  const afterBackToJ1 = {
    fetchCalls: calls.length,
    status: statusElement.textContent,
  };

  withTerritoryInput.checked = false;
  await togglePrecipitationLayer(withTerritoryInput);
  const afterDisable = {
    removed: removedLayers.length,
    legendHidden: weatherLegend.hidden,
    legendToggleHidden: weatherLegendToggle.hidden,
    legendExpanded: weatherLegendToggle.getAttribute("aria-expanded"),
    timelineHidden: weatherTimeline.hidden,
    provenance: weatherTimelineProvenance.textContent,
    status: statusElement.textContent,
  };

  withTerritoryInput.checked = true;
  await togglePrecipitationLayer(withTerritoryInput);
  const afterReenable = {
    fetchCalls: calls.length,
    layers: addedGroups.length,
    timelineVisible: weatherTimeline.hidden === false,
    legendHidden: weatherLegend.hidden,
    legendToggleHidden: weatherLegendToggle.hidden,
    legendExpanded: weatherLegendToggle.getAttribute("aria-expanded"),
    status: statusElement.textContent,
  };

  fakeToday = "2026-09-07";
  withTerritoryInput.checked = true;
  await togglePrecipitationLayer(withTerritoryInput);
  const afterDayChange = {
    fetchCalls: calls.length,
    cacheSize: precipitationDataByValidTime.size,
  };

  const deferred = new Map();
  fetchJson = async function(url) {
    calls.push(url);
    const validTime = decodeURIComponent(url.split("valid_time=", 2)[1]);
    return new Promise((resolve) => deferred.set(validTime, resolve));
  };
  precipitationDataByValidTime = new Map();
  const raceContext = prepareWeatherFrontendContext();
  const oldStatus = beginAppStatus(
    "weather", "loading", "Ancienne échéance météo",
  );
  const oldRequest = selectWeatherForecastIndex(
    1, withTerritoryInput, raceContext, oldStatus,
  );
  const newStatus = beginAppStatus(
    "weather", "loading", "Nouvelle échéance météo",
  );
  const newRequest = selectWeatherForecastIndex(
    2, withTerritoryInput, raceContext, newStatus,
  );
  deferred.get(validTimes[1])(gridResponse(validTimes[1], 6));
  await Promise.resolve();
  deferred.get(validTimes[2])(gridResponse(validTimes[2], 21));
  await Promise.all([oldRequest, newRequest]);
  const afterRace = {
    selected: selectedWeatherStepIndex,
    cachedOld: precipitationDataByValidTime.has(validTimes[1]),
    cachedNew: precipitationDataByValidTime.has(validTimes[2]),
    finalColor: rectangles.at(-2).options.fillColor,
    status: statusElement.textContent,
  };

  fetchJson = async function(url) {
    calls.push(url);
    if (url.endsWith("/echances")) return forecastResponse();
    return { valid_time: validTimes[0], cells: "invalid" };
  };
  fakeToday = "2026-09-08";
  await togglePrecipitationLayer(withTerritoryInput);
  const afterInvalid = {
    checked: withTerritoryInput.checked,
    message: statusElement.textContent,
    error: statusElement.classList.error === true,
  };

  currentFeature = { type: "Feature", geometry: { type: "Polygon", changed: true } };
  document.precipitationInput.checked = true;
  invalidatePrecipitationCache();
  const afterInvalidation = {
    cacheSize: precipitationDataByValidTime.size,
    checked: document.precipitationInput.checked,
    legendHidden: weatherLegend.hidden,
    timelineHidden: weatherTimeline.hidden,
    status: statusElement.textContent,
    error: statusElement.classList.error === true,
  };

  const pendingWeather = [];
  fetchJson = (url) => new Promise((resolve, reject) => {
    calls.push(url);
    pendingWeather.push({ url, resolve, reject });
  });
  const raceInput = document.precipitationInput;
  raceInput.checked = true;
  const obsoleteActivation = togglePrecipitationLayer(raceInput);
  const obsoleteForecast = pendingWeather.shift();
  raceInput.checked = false;
  await togglePrecipitationLayer(raceInput);
  raceInput.checked = true;
  const currentActivation = togglePrecipitationLayer(raceInput);
  const currentForecast = pendingWeather.shift();
  obsoleteForecast.reject(new Error("ancienne erreur réseau"));
  await obsoleteActivation;
  const afterObsoleteError = {
    checked: raceInput.checked,
    status: statusElement.textContent,
    error: statusElement.classList.error === true,
    layers: addedGroups.length,
  };
  currentForecast.resolve(forecastResponse());
  await Promise.resolve();
  await Promise.resolve();
  const currentGrid = pendingWeather.shift();
  currentGrid.resolve(gridResponse(validTimes[0], 12));
  await currentActivation;
  const afterCurrentActivation = {
    checked: raceInput.checked,
    status: statusElement.textContent,
    error: statusElement.classList.error === true,
    layers: addedGroups.length,
  };

  process.stdout.write(JSON.stringify({
    withoutTerritory,
    whileLoading,
    afterEnable,
    afterLegendOpen,
    afterJ2,
    afterLegendClose,
    afterBackToJ1,
    afterDisable,
    afterReenable,
    afterDayChange,
    afterRace,
    afterInvalid,
    afterInvalidation,
    afterObsoleteError,
    afterCurrentActivation,
  }));
})();
'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertFalse(result["withoutTerritory"]["checked"])
        self.assertEqual(result["withoutTerritory"]["fetchCalls"], 0)
        self.assertIn("territoire administratif", result["withoutTerritory"]["message"])
        self.assertTrue(result["withoutTerritory"]["error"])
        self.assertEqual(result["afterEnable"]["fetchCalls"], 2)
        self.assertTrue(result["afterEnable"]["calls"][0].endswith("/echances"))
        self.assertIn("valid_time=2026-09-06T18%3A00%3A00Z", result["afterEnable"]["calls"][1])
        self.assertEqual(result["afterEnable"]["layers"], 1)
        self.assertEqual(result["afterEnable"]["rectangles"], 2)
        self.assertEqual(result["afterEnable"]["pane"], "weather-precipitations")
        self.assertTrue(result["afterEnable"]["interactive"])
        self.assertGreater(result["afterEnable"]["firstOpacity"], 0)
        self.assertEqual(result["afterEnable"]["nodataOpacity"], 0)
        self.assertEqual(result["afterEnable"]["className"], "weather-precipitation-cell")
        self.assertFalse(result["afterEnable"]["stroke"])
        self.assertTrue(result["whileLoading"]["legendHidden"])
        self.assertTrue(result["whileLoading"]["toggleHidden"])
        self.assertTrue(result["afterEnable"]["legendHidden"])
        self.assertFalse(result["afterEnable"]["legendToggleHidden"])
        self.assertEqual(result["afterEnable"]["legendExpanded"], "false")
        self.assertEqual(result["afterEnable"]["legendRows"], 7)
        self.assertEqual(
            result["afterEnable"]["legendLabels"],
            [
                "0 mm", ">0–1 mm", "1–5 mm", "5–10 mm",
                "10–20 mm", "20–40 mm", ">40 mm",
            ],
        )
        self.assertFalse(result["afterLegendOpen"]["legendHidden"])
        self.assertEqual(result["afterLegendOpen"]["expanded"], "true")
        self.assertTrue(result["afterLegendOpen"]["checked"])
        self.assertTrue(result["afterEnable"]["timelineVisible"])
        self.assertEqual(result["afterEnable"]["timelineLabels"], ["J+1", "J+2", "J+3"])
        self.assertEqual(result["afterEnable"]["timelineMax"], "2")
        self.assertEqual(result["afterEnable"]["provenance"], "AROME · cumul 24 h")
        self.assertEqual(result["afterEnable"]["status"], "3 tronçons · 5 désordres")
        self.assertEqual(result["afterJ2"]["fetchCalls"], 3)
        self.assertIn("valid_time=2026-09-07T18%3A00%3A00Z", result["afterJ2"]["request"])
        self.assertEqual(result["afterJ2"]["selected"], "1")
        self.assertEqual(result["afterJ2"]["provenance"], "AROME · cumul 24 h")
        self.assertFalse(result["afterJ2"]["legendHidden"])
        self.assertEqual(result["afterJ2"]["legendExpanded"], "true")
        self.assertTrue(result["afterJ2"]["legendRebuilt"])
        self.assertEqual(
            result["afterJ2"]["legendColors"],
            [
                "transparent", "#d7ecfb", "#96cdec", "#4b9fd8",
                "#2377b7", "#15508a", "#6f2c8f",
            ],
        )
        self.assertTrue(result["afterLegendClose"]["legendHidden"])
        self.assertEqual(result["afterLegendClose"]["expanded"], "false")
        self.assertTrue(result["afterLegendClose"]["checked"])
        self.assertEqual(result["afterBackToJ1"]["fetchCalls"], 3)
        self.assertEqual(result["afterBackToJ1"]["status"], "3 tronçons · 5 désordres")
        self.assertEqual(result["afterDisable"]["removed"], 3)
        self.assertTrue(result["afterDisable"]["legendHidden"])
        self.assertTrue(result["afterDisable"]["legendToggleHidden"])
        self.assertEqual(result["afterDisable"]["legendExpanded"], "false")
        self.assertTrue(result["afterDisable"]["timelineHidden"])
        self.assertEqual(result["afterDisable"]["provenance"], "")
        self.assertEqual(result["afterDisable"]["status"], "3 tronçons · 5 désordres")
        self.assertEqual(result["afterReenable"]["fetchCalls"], 3)
        self.assertGreaterEqual(result["afterReenable"]["layers"], 3)
        self.assertTrue(result["afterReenable"]["timelineVisible"])
        self.assertTrue(result["afterReenable"]["legendHidden"])
        self.assertFalse(result["afterReenable"]["legendToggleHidden"])
        self.assertEqual(result["afterReenable"]["legendExpanded"], "false")
        self.assertEqual(result["afterReenable"]["status"], "3 tronçons · 5 désordres")
        self.assertEqual(result["afterDayChange"]["fetchCalls"], 5)
        self.assertEqual(result["afterDayChange"]["cacheSize"], 1)
        self.assertEqual(result["afterRace"]["selected"], 2)
        self.assertFalse(result["afterRace"]["cachedOld"])
        self.assertTrue(result["afterRace"]["cachedNew"])
        self.assertEqual(result["afterRace"]["finalColor"], "#15508a")
        self.assertEqual(result["afterRace"]["status"], "3 tronçons · 5 désordres")
        self.assertFalse(result["afterInvalid"]["checked"])
        self.assertIn("Réponse météo invalide", result["afterInvalid"]["message"])
        self.assertTrue(result["afterInvalid"]["error"])
        self.assertEqual(result["afterInvalidation"]["cacheSize"], 0)
        self.assertFalse(result["afterInvalidation"]["checked"])
        self.assertTrue(result["afterInvalidation"]["legendHidden"])
        self.assertTrue(result["afterInvalidation"]["timelineHidden"])
        self.assertEqual(result["afterInvalidation"]["status"], "3 tronçons · 5 désordres")
        self.assertFalse(result["afterInvalidation"]["error"])
        self.assertTrue(result["afterObsoleteError"]["checked"])
        self.assertEqual(
            result["afterObsoleteError"]["status"],
            "Chargement des précipitations prévues…",
        )
        self.assertFalse(result["afterObsoleteError"]["error"])
        self.assertTrue(result["afterCurrentActivation"]["checked"])
        self.assertEqual(
            result["afterCurrentActivation"]["status"], "3 tronçons · 5 désordres"
        )
        self.assertFalse(result["afterCurrentActivation"]["error"])
        self.assertGreater(
            result["afterCurrentActivation"]["layers"],
            result["afterObsoleteError"]["layers"],
        )

    def test_frontend_territory_cartography_prefers_territory_and_constrains_navigation(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test territoire frontend.")
        territory_source = (
            "function territoryBoundsFromFeature"
            + script.split("function territoryBoundsFromFeature", 1)[1]
              .split("function setToolsMenuOpen", 1)[0]
        )
        program = territory_source + r'''
function makeBounds(label, zoom) {
  return {
    label,
    zoom,
    isValid() { return true; },
    pad(ratio) {
      return makeBounds(`${label}.pad(${ratio})`, zoom);
    },
  };
}
const TERRITORY_VIEW_PADDING_RATIO = 0.08;
const TERRITORY_VIEW_MAX_ZOOM = 17;
const territoryMaskRenderer = {};
const TERRITORY_MASK_PANE = "territory-mask";
const TERRITORY_OUTLINE_PANE = "territory-outline";
let territoireAdministratifGeoJSON = { type: "FeatureCollection", features: [] };
let territoireContourLayer = null;
let territoireMaskLayer = null;
let mapViewportReady = false;
let historicalViewportBounds = null;
let pendingUserPositionCenter = null;
function focusPendingMapCandidate() {}
function restoreInitialMapViewport() { return false; }
function syncMapViewportUrl() {}
const mapElement = { style: { visibility: "hidden" } };
const createdLayers = [];
const removals = [];
const fitBoundsCalls = [];
const setMaxBoundsCalls = [];
const setMinZoomCalls = [];
const viewportCalls = [];
const invalidateSizeCalls = [];
const map = {
  zoom: null,
  panes: {},
  removeLayer(layer) {
    removals.push(layer);
  },
  fitBounds(bounds, options) {
    viewportCalls.push("fitBounds");
    fitBoundsCalls.push({ label: bounds.label, options });
    this.zoom = bounds.zoom;
  },
  setMaxBounds(bounds) {
    viewportCalls.push("setMaxBounds");
    setMaxBoundsCalls.push(bounds.label);
    this.maxBounds = bounds;
  },
  setMinZoom(zoom) {
    viewportCalls.push("setMinZoom");
    setMinZoomCalls.push(zoom);
    this.minZoom = zoom;
  },
  getZoom() {
    return this.zoom;
  },
  invalidateSize(options) {
    viewportCalls.push("invalidateSize");
    invalidateSizeCalls.push({ options, visibility: mapElement.style.visibility });
    this.invalidations = (this.invalidations || 0) + 1;
  },
  createPane(name) {
    this.panes[name] = { style: {} };
  },
  getPane(name) {
    return this.panes[name];
  },
};
const L = {
  geoJSON(feature, options = {}) {
    const layer = {
      feature,
      options,
      getBounds() {
        return feature.bounds;
      },
      addTo(target) {
        this.addedTo = target;
        createdLayers.push(this);
        return this;
      },
    };
    return layer;
  },
};
function currentTerritoireFeature() {
  return territoireAdministratifGeoJSON.features[0] || null;
}

map.createPane(TERRITORY_MASK_PANE);
map.getPane(TERRITORY_MASK_PANE).style.zIndex = "350";
map.getPane(TERRITORY_MASK_PANE).style.pointerEvents = "none";
map.createPane(TERRITORY_OUTLINE_PANE);
map.getPane(TERRITORY_OUTLINE_PANE).style.zIndex = "360";
map.getPane(TERRITORY_OUTLINE_PANE).style.pointerEvents = "none";

const territoryA = {
  type: "Feature",
  geometry: {
    type: "Polygon",
    coordinates: [
      [[2, 48], [3, 48], [3, 49], [2, 49], [2, 48]],
      [[2.2, 48.2], [2.4, 48.2], [2.4, 48.4], [2.2, 48.4], [2.2, 48.2]],
    ],
  },
  bounds: makeBounds("territoryA", 12),
};
const territoryB = {
  type: "Feature",
  geometry: {
    type: "Polygon",
    coordinates: [
      [[4, 44], [5, 44], [5, 45], [4, 45], [4, 44]],
    ],
  },
  bounds: makeBounds("territoryB", 11),
};
const fallbackBounds = makeBounds("fallback", 8);

territoireAdministratifGeoJSON = {
  type: "FeatureCollection",
  features: [territoryA],
};
const firstBounds = applyTerritoireCartography(fallbackBounds);
const afterFirst = {
  firstBounds: firstBounds.label,
  fitBounds: fitBoundsCalls[0].label,
  setMaxBounds: setMaxBoundsCalls[0],
  minZoom: setMinZoomCalls[0],
  zoom: map.zoom,
  visibility: mapElement.style.visibility,
  maskPane: createdLayers[0].options.pane,
  contourPane: createdLayers[1].options.pane,
  maskInteractive: createdLayers[0].options.interactive,
  contourInteractive: createdLayers[1].options.interactive,
  maskGeometryType: createdLayers[0].feature.geometry.type,
  maskGeometryCount: createdLayers[0].feature.geometry.coordinates.length,
  invalidations: map.invalidations,
  fitBoundsOptions: fitBoundsCalls[0].options,
  viewportCalls: viewportCalls.slice(0, 4),
  invalidateSizeCall: invalidateSizeCalls[0],
};

territoireAdministratifGeoJSON = {
  type: "FeatureCollection",
  features: [territoryB],
};
const secondBounds = applyTerritoireCartography(fallbackBounds);
const afterSecond = {
  secondBounds: secondBounds.label,
  fitBounds: fitBoundsCalls[1].label,
  setMaxBounds: setMaxBoundsCalls[1],
  minZoom: setMinZoomCalls[1],
  zoom: map.zoom,
  removals: removals.length,
  contourLabel: territoireContourLayer.feature.bounds.label,
  maskLabel: territoireMaskLayer.feature.geometry.type,
};

territoireAdministratifGeoJSON = {
  type: "FeatureCollection",
  features: [],
};
const thirdBounds = applyTerritoireCartography(fallbackBounds);
const afterThird = {
  thirdBounds: thirdBounds.label,
  fitBounds: fitBoundsCalls[2].label,
  setMaxBounds: setMaxBoundsCalls[2],
  minZoom: setMinZoomCalls[2],
  zoom: map.zoom,
  createdLayers: createdLayers.length,
  removals: removals.length,
  currentVisibility: mapElement.style.visibility,
};

process.stdout.write(JSON.stringify({ afterFirst, afterSecond, afterThird }));
'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["afterFirst"]["firstBounds"], "territoryA")
        self.assertEqual(result["afterFirst"]["fitBounds"], "territoryA.pad(0.08)")
        self.assertEqual(result["afterFirst"]["setMaxBounds"], "territoryA.pad(0.08)")
        self.assertEqual(result["afterFirst"]["minZoom"], 12)
        self.assertEqual(result["afterFirst"]["zoom"], 12)
        self.assertEqual(result["afterFirst"]["visibility"], "")
        self.assertEqual(result["afterFirst"]["maskPane"], "territory-mask")
        self.assertEqual(result["afterFirst"]["contourPane"], "territory-outline")
        self.assertFalse(result["afterFirst"]["maskInteractive"])
        self.assertFalse(result["afterFirst"]["contourInteractive"])
        self.assertEqual(result["afterFirst"]["maskGeometryType"], "MultiPolygon")
        self.assertEqual(result["afterFirst"]["maskGeometryCount"], 2)
        self.assertGreaterEqual(result["afterFirst"]["invalidations"], 1)
        self.assertEqual(
            result["afterFirst"]["fitBoundsOptions"],
            {"maxZoom": 17, "animate": False},
        )
        self.assertEqual(
            result["afterFirst"]["viewportCalls"],
            ["fitBounds", "setMaxBounds", "setMinZoom", "invalidateSize"],
        )
        self.assertEqual(
            result["afterFirst"]["invalidateSizeCall"],
            {
                "options": {"pan": False, "animate": False},
                "visibility": "hidden",
            },
        )
        self.assertEqual(result["afterSecond"]["secondBounds"], "territoryB")
        self.assertEqual(result["afterSecond"]["fitBounds"], "territoryB.pad(0.08)")
        self.assertEqual(result["afterSecond"]["setMaxBounds"], "territoryB.pad(0.08)")
        self.assertEqual(result["afterSecond"]["minZoom"], 11)
        self.assertEqual(result["afterSecond"]["zoom"], 11)
        self.assertEqual(result["afterSecond"]["removals"], 2)
        self.assertEqual(result["afterSecond"]["contourLabel"], "territoryB")
        self.assertEqual(result["afterSecond"]["maskLabel"], "MultiPolygon")
        self.assertEqual(result["afterThird"]["thirdBounds"], "fallback")
        self.assertEqual(result["afterThird"]["fitBounds"], "fallback.pad(0.08)")
        self.assertEqual(result["afterThird"]["setMaxBounds"], "fallback.pad(0.08)")
        self.assertEqual(result["afterThird"]["minZoom"], 8)
        self.assertEqual(result["afterThird"]["zoom"], 8)
        self.assertEqual(result["afterThird"]["createdLayers"], 4)
        self.assertEqual(result["afterThird"]["removals"], 4)
        self.assertEqual(result["afterThird"]["currentVisibility"], "")

    def test_frontend_territory_modal_state_and_upload_contract(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test territoire frontend.")
        territory_source = (
            "function setTerritoireAdministratifState"
            + script.split("function setTerritoireAdministratifState", 1)[1]
              .split("function appendDefinition", 1)[0]
        )
        program = territory_source + r'''
function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}
function inputText(value) {
  return value === null || value === undefined ? "" : String(value);
}
function featureIdentifier(feature) {
  const identifier = feature?.properties?.id ?? feature?.id;
  return identifier == null || String(identifier).trim() === ""
    ? null : String(identifier);
}
const publishedMutations = [];
function publishConfirmedMutation(resource, action, ids) {
  publishedMutations.push({ resource, action, ids });
}
function element() {
  return {
    hidden: false,
    disabled: false,
    textContent: "",
    value: "",
    files: [],
    children: [],
    dataset: {},
    style: {},
    attributes: {},
    classList: { add(name) { this[name] = true; }, remove(name) { this[name] = false; } },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    focus() { this.focused = true; },
    reset() { this.wasReset = true; },
    replaceChildren() { this.children = []; },
  };
}
const territoireCurrentState = element();
const territoireLibelleInput = element();
const territoireFileInput = element();
const territoireLayerInput = element();
const territoireMessage = element();
const submitTerritoireButton = element();
const territoireModal = element();
territoireModal.hidden = true;
const territoireForm = element();
const cancelTerritoireModalButton = element();
const closeTerritoireModalButton = element();
const weatherLegend = element();
const weatherLegendToggle = element();
const weatherTimeline = element();
const weatherTimelineLabels = element();
const weatherTimelineRange = element();
const weatherTimelineProvenance = element();
const document = { querySelector() { return null; } };
const TERRITORY_VIEW_PADDING_RATIO = 0.08;
const TERRITORY_VIEW_MAX_ZOOM = 17;
const territoryMaskRenderer = {};
const TERRITORY_MASK_PANE = "territory-mask";
const TERRITORY_OUTLINE_PANE = "territory-outline";
let territoireAdministratifGeoJSON = { type: "FeatureCollection", features: [] };
let territoireImportPending = false;
let territoireContourLayer = null;
let territoireMaskLayer = null;
let precipitationLayer = null;
let weatherForecastSession = null;
let precipitationDataByValidTime = new Map();
let weatherFrontendCacheDate = null;
let weatherTerritoryCacheKey = null;
let selectedWeatherStepIndex = 0;
let precipitationRequestPending = false;
let precipitationRequestController = null;
let precipitationRequestVersion = 0;
let vigicruesRequestVersion = 0;
let territoireRequestSequence = 0;
let historicalViewportBounds = null;
let pendingUserPositionCenter = null;
function focusPendingMapCandidate() {}
let mapViewportReady = false;
function clearAppStatus() {}
function restoreInitialMapViewport() { return false; }
function syncMapViewportUrl() {}
const mapElement = { style: { visibility: "hidden" } };
const map = {
  zoom: 0,
  removeLayer() {},
  fitBounds(bounds) { this.zoom = bounds?.zoom || 0; },
  setMaxBounds() {},
  setMinZoom() {},
  getZoom() { return this.zoom; },
  invalidateSize() {},
};
const L = {
  geoJSON(feature, options = {}) {
    return {
      feature,
      options,
      getBounds() {
        return feature.bounds || {
          label: "invalid",
          isValid() { return false; },
          pad() { return this; },
        };
      },
      addTo() {
        return this;
      },
    };
  },
};
let confirmations = [];
const territoryEvents = [];
const window = {
  confirm(message) { confirmations.push(message); return window.nextConfirm; },
  dispatchEvent(event) { territoryEvents.push(event.type); return true; },
};
const calls = [];
let fetchJson = async function(url, options) {
  calls.push({ url, options });
  return {
    type: "FeatureCollection",
    features: [{
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [] },
      bounds: {
        label: "uploaded",
        zoom: 9,
        isValid() { return true; },
        pad(ratio) { return { label: `uploaded.pad(${ratio})`, zoom: 9, isValid: this.isValid, pad: this.pad }; },
      },
      properties: { libelle: options.headers["X-Filename"] },
    }],
  };
};
async function fetchGeoJSON() {
  return { type: "FeatureCollection", features: [] };
}

(async () => {
  openTerritoireModal();
  const emptyState = territoireCurrentState.textContent;
  const emptyButton = submitTerritoireButton.textContent;
  const openBeforeSuccess = territoireModal.hidden;

  territoireLibelleInput.value = "Nouveau territoire";
  territoireFileInput.files = [{ name: "contour.zip" }];
  territoireLayerInput.value = "";
  await submitTerritoireImport();
  const initial = calls.at(-1);
  const closedAfterSuccess = territoireModal.hidden;
  const resetAfterSuccess = territoireForm.wasReset === true;

  setTerritoireAdministratifState({
    type: "FeatureCollection",
    features: [{ properties: { libelle: "Territoire existant" } }],
  });
  openTerritoireModal();
  const existingState = territoireCurrentState.textContent;
  const existingLibelle = territoireLibelleInput.value;
  const existingButton = submitTerritoireButton.textContent;
  const openBeforeError = territoireModal.hidden;

  territoireFileInput.files = [{ name: "contour.gpkg" }];
  territoireLayerInput.value = "limite";
  window.nextConfirm = false;
  await submitTerritoireImport();
  const callsAfterCancel = calls.length;
  const stillOpenAfterCancel = territoireModal.hidden;

  fetchJson = async function() {
    throw new Error("Polygon invalide");
  };
  window.nextConfirm = true;
  await submitTerritoireImport().catch((error) => {
    territoireMessage.textContent = error.message;
    territoireMessage.classList.add("error");
  });
  const openAfterError = territoireModal.hidden;
  const errorMessage = territoireMessage.textContent;

  fetchJson = async function(url, options) {
    calls.push({ url, options });
    return {
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [] },
        properties: { libelle: options.headers["X-Filename"] },
      }],
    };
  };
  await submitTerritoireImport();
  const replacement = calls.at(-1);
  const closedAfterReplacement = territoireModal.hidden;

  process.stdout.write(JSON.stringify({
    emptyState,
    emptyButton,
    openBeforeSuccess,
    closedAfterSuccess,
    resetAfterSuccess,
    existingState,
    existingLibelle,
    existingButton,
    openBeforeError,
    callsAfterCancel,
    stillOpenAfterCancel,
    openAfterError,
    errorMessage,
    callCount: calls.length,
    confirmations: confirmations.length,
    initialUrl: initial.url,
    initialFilename: initial.options.headers["X-Filename"],
    initialContentType: initial.options.headers["Content-Type"],
    replacementUrl: replacement.url,
    replacementFilename: replacement.options.headers["X-Filename"],
    replacementContentType: replacement.options.headers["Content-Type"],
    replacementIfMatch: replacement.options.headers["If-Match"] || null,
    closedAfterReplacement,
    publishedMutations,
    territoryEvents,
  }));
})();
'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["emptyState"], "Aucun territoire configuré")
        self.assertEqual(result["emptyButton"], "Importer")
        self.assertFalse(result["openBeforeSuccess"])
        self.assertTrue(result["closedAfterSuccess"])
        self.assertTrue(result["resetAfterSuccess"])
        self.assertEqual(result["existingState"], "Territoire actuel : Territoire existant")
        self.assertEqual(result["existingLibelle"], "Territoire existant")
        self.assertEqual(result["existingButton"], "Remplacer")
        self.assertFalse(result["openBeforeError"])
        self.assertIn("replace=false", result["initialUrl"])
        self.assertNotIn("layer=", result["initialUrl"])
        self.assertEqual(result["initialFilename"], "contour.zip")
        self.assertEqual(result["initialContentType"], "application/zip")
        self.assertEqual(result["callsAfterCancel"], 1)
        self.assertFalse(result["stillOpenAfterCancel"])
        self.assertFalse(result["openAfterError"])
        self.assertEqual(result["errorMessage"], "Polygon invalide")
        self.assertEqual(result["callCount"], 2)
        self.assertEqual(result["confirmations"], 3)
        self.assertIn("replace=true", result["replacementUrl"])
        self.assertIn("layer=limite", result["replacementUrl"])
        self.assertEqual(result["replacementFilename"], "contour.gpkg")
        self.assertEqual(
            result["replacementContentType"],
            "application/geopackage+sqlite3",
        )
        self.assertIsNone(result["replacementIfMatch"])
        self.assertEqual(result["publishedMutations"], [
            {"resource": "territoires-administratifs", "action": "create", "ids": None},
            {"resource": "territoires-administratifs", "action": "update", "ids": None},
        ])
        self.assertEqual(result["territoryEvents"], [
            "digues-app:gps-territory-changed",
            "digues-app:gps-territory-changed",
            "digues-app:gps-territory-changed",
        ])
        self.assertTrue(result["closedAfterReplacement"])

    def test_frontend_territory_errors_display_backend_detail(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        territory_listener = script.split(
            'territoireForm.addEventListener("submit"', 1
        )[1].split("closeTerritoireModalButton", 1)[0]
        self.assertIn("submitTerritoireImport().catch", territory_listener)
        self.assertIn("territoireMessage.textContent = error.message", territory_listener)
        self.assertIn('territoireMessage.classList.add("error")', territory_listener)
        self.assertIn("detail = errorDetail(await response.json(), detail)", script)

    def test_frontend_queries_and_ai_effects_remain_independent(self):
        map_page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        map_script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        data_script = (
            FRONTEND_DIRECTORY / "js" / "data-view.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn("queriesView", map_script)
        self.assertNotIn('id="queries-view"', map_page)
        self.assertIn('id="queries-view"', data_page)
        self.assertIn("function setQueriesOpen(open)", data_script)
        self.assertIn('queriesView.hidden = centralMode !== "sql"', data_script)
        self.assertIn("AiPanel.createController", map_script)
        self.assertIn("AiPanel.createController", data_script)
        self.assertIn("HeritagePanel.createController", map_script)
        self.assertIn("HeritagePanel.createController", data_script)

    def test_frontend_ai_chat_posts_text_and_renders_with_text_content(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "ai-panel.js").read_text(encoding="utf-8")
        self.assertIn('fetchJson("/api/ai/chat"', script)
        self.assertIn("const aiConversationHistory = []", script)
        self.assertIn("aiConversationHistory.push({ role, content })", script)
        self.assertIn("conversation_summary: aiMemory.conversation_summary", script)
        self.assertIn("recent_messages: aiMemory.recent_messages", script)
        self.assertIn("message,", script)
        self.assertIn("sessionStorage", script)
        self.assertIn("{ error: true, remember: false }", script)
        self.assertIn("aiMessageInput.disabled = pending", script)
        self.assertIn("aiSendButton.disabled = pending", script)
        self.assertIn('event.key === "Enter"', script)
        self.assertIn('src="/static/vendor/marked/marked.umd.js"', page)
        self.assertIn("function renderAiMarkdown(content)", script)
        self.assertIn('role === "assistant"', script)
        self.assertIn('role === "user"', script)
        self.assertIn("body.textContent = content", script)
        self.assertIn("consultedSources: response.consulted_sources", script)
        self.assertNotIn("body.innerHTML", script)
        self.assertNotIn("MISTRAL_API_KEY", page)
        self.assertNotIn("MISTRAL_API_KEY", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("indexedDB", script)

    def test_frontend_renders_assistant_markdown_with_safe_dom(self):
        script = (FRONTEND_DIRECTORY / "js" / "ai-panel.js").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test Markdown IA.")

        render_source = "async function copyTextToClipboard" + script.split(
            "async function copyTextToClipboard", 1
        )[1].split("function setAiRequestPending", 1)[0]
        program = render_source + r'''
class TextNode {
  constructor(value) {
    this.nodeType = 3;
    this.value = String(value);
  }
  get textContent() { return this.value; }
  set textContent(value) { this.value = String(value); }
}
class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.className = "";
    this.classList = { add: (...names) => { this.className += ` ${names.join(" ")}`; } };
    this._text = "";
    this.open = false;
    this.scrollHeight = 10;
  }
  append(...children) { this.children.push(...children); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  remove() { this.removed = true; }
  select() { this.selected = true; }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
}
function findAll(root, tagName) {
  const matches = [];
  (root.children || []).forEach((child) => {
    if (child.tagName === tagName) matches.push(child);
    matches.push(...findAll(child, tagName));
  });
  return matches;
}
const document = {
  body: new Element("body"),
  createElement(tagName) { return new Element(tagName); },
  createTextNode(value) { return new TextNode(value); },
  execCommand(command) { return command === "copy"; },
};
globalThis.marked = require("./webapp/frontend/vendor/marked/marked.umd.js");
let copiedText = null;
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { clipboard: { async writeText(text) { copiedText = text; } } },
});
const window = { setTimeout(callback) { callback(); } };
const aiConversation = new Element("conversation");
const aiConversationEmpty = { remove() {} };
const aiConversationHistory = [];

(async () => {
  const markdown = [
    "**SIRS**",
    "",
    "### Mon rôle",
    "",
    "- point 1",
    "- point 2",
    "",
    "La table `troncons` et *son contenu*.",
    "",
    "```sql",
    "SELECT '<tag>';",
    "```",
    "",
    "> citation",
    "",
    "[Documentation](https://example.test/guide)",
  ].join("\n");
  const body = renderAiMarkdown(markdown);
  const codeBlocks = findAll(body, "pre");
  const code = findAll(codeBlocks[0], "code")[0];
  const copyButton = findAll(body, "button")[0];
  await copyButton.listeners.click();

  const unsafe = renderAiMarkdown([
    "<script>alert(1)</script>",
    "",
    "<img src=x onerror=alert(1)>",
    "",
    "[test](javascript:alert(1))",
  ].join("\n"));

  appendAiMessage("user", "**pas de Markdown** <img src=x onerror=alert(1)>");
  const userBody = aiConversation.children[0].children[1];
  const safeLink = findAll(body, "a")[0];
  process.stdout.write(JSON.stringify({
    strong: findAll(body, "strong").map((item) => item.textContent),
    headings: findAll(body, "h3").map((item) => item.textContent),
    listItems: findAll(body, "li").map((item) => item.textContent),
    inlineCode: findAll(body, "code").map((item) => item.textContent),
    paragraphs: findAll(body, "p").map((item) => item.textContent),
    quoteCount: findAll(body, "blockquote").length,
    codeBlockCount: codeBlocks.length,
    codeText: code.textContent,
    codeLanguage: code.dataset.language,
    copiedText,
    copyButtonText: copyButton.textContent,
    safeLink: { href: safeLink.href, target: safeLink.target, rel: safeLink.rel },
    unsafeTags: findAll(unsafe, "script").length + findAll(unsafe, "img").length,
    unsafeLinks: findAll(unsafe, "a").length,
    unsafeText: unsafe.textContent,
    userTag: userBody.tagName,
    userText: userBody.textContent,
    userMarkupTags: findAll(userBody, "strong").length + findAll(userBody, "img").length,
  }));
})();
'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["strong"], ["SIRS"])
        self.assertEqual(result["headings"], ["Mon rôle"])
        self.assertEqual(result["listItems"], ["point 1", "point 2"])
        self.assertIn("troncons", result["inlineCode"])
        self.assertEqual(result["codeBlockCount"], 1)
        self.assertEqual(result["codeText"], "SELECT '<tag>';" )
        self.assertEqual(result["codeLanguage"], "sql")
        self.assertEqual(result["copiedText"], result["codeText"])
        self.assertEqual(result["copyButtonText"], "Copier")
        self.assertEqual(result["quoteCount"], 1)
        self.assertEqual(result["safeLink"], {
            "href": "https://example.test/guide",
            "target": "_blank",
            "rel": "noopener noreferrer",
        })
        self.assertEqual(result["unsafeTags"], 0)
        self.assertEqual(result["unsafeLinks"], 0)
        self.assertIn("<script>alert(1)</script>", result["unsafeText"])
        self.assertIn("<img src=x onerror=alert(1)>", result["unsafeText"])
        self.assertEqual(result["userTag"], "span")
        self.assertEqual(
            result["userText"],
            "**pas de Markdown** <img src=x onerror=alert(1)>",
        )
        self.assertEqual(result["userMarkupTags"], 0)
        self.assertNotIn("innerHTML", render_source)
        self.assertNotIn("Exécuter", render_source)

    def test_frontend_renders_markdown_tables_without_altering_code_pipes(self):
        script = (FRONTEND_DIRECTORY / "js" / "ai-panel.js").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test Markdown IA.")

        render_source = "async function copyTextToClipboard" + script.split(
            "async function copyTextToClipboard", 1
        )[1].split("function setAiRequestPending", 1)[0]
        program = render_source + r'''
class TextNode {
  constructor(value) { this.nodeType = 3; this.value = String(value); }
  get textContent() { return this.value; }
  set textContent(value) { this.value = String(value); }
}
class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.className = "";
    this._text = "";
  }
  append(...children) { this.children.push(...children); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
}
function findAll(root, tagName) {
  const matches = [];
  (root.children || []).forEach((child) => {
    if (child.tagName === tagName) matches.push(child);
    matches.push(...findAll(child, tagName));
  });
  return matches;
}
const document = {
  createElement(tagName) { return new Element(tagName); },
  createTextNode(value) { return new TextNode(value); },
  execCommand() { return true; },
};
const window = { setTimeout(callback) { callback(); } };
const markedLibrary = require("./webapp/frontend/vendor/marked/marked.umd.js");
let markdownInputsPassedToMarked = [];
globalThis.marked = {
  lexer(content, options) {
    markdownInputsPassedToMarked.push(String(content));
    return markedLibrary.lexer(content, options);
  },
};

const standardTable = [
  "| Action | Autorisée ? | Responsable |",
  "|--------|-------------|-------------|",
  "| Lire | **Oui** | `SIRS` |",
  "| Réparer | Non | Gestionnaire |",
].join("\n");
const escapedTable = [
  "\\| Action \\| Autorisée ? \\| Responsable \\|",
  "\\|--------\\|-------------\\|-------------\\|",
  "\\| Lire \\| **Oui** \\| `SIRS` \\|",
  "\\| Réparer \\| Non \\| Gestionnaire \\|",
].join("\n");
const codeWithPipes = [
  "```sql",
  "SELECT 'a \\| b | c';",
  "```",
].join("\n");
const hostile = [
  "| Colonne | Valeur |",
  "|---------|--------|",
  "| XSS | <img src=x onerror=alert(1)> |",
  "| Lien | [test](javascript:alert(1)) |",
].join("\n");
const exactMistralRegressionTable = [
  "| **Outil** | **Rôle** | **Public cible** | **Statut** |",
  "|---|---|---|---|",
  "| **QGIS** | Outil de contrôle. | Experts SIG. | Complémentaire. |",
  "| **Application web** | Interface métier. | Gestionnaires. | Interface cible. |",
].join("\n");
const flattenedMistralTable = "| **Outil** | **Rôle** | **Public cible** | **Statut** | |---|---|---|---| | **QGIS** | Outil de contrôle. | Experts SIG. | Complémentaire. | | **Application web** | Interface métier. | Gestionnaires. | Interface cible. |";

const standardTokens = markedLibrary.lexer(standardTable, { gfm: true, breaks: false });
const escapedTokens = markedLibrary.lexer(escapedTable, { gfm: true, breaks: false });
const normalizedEscapedTokens = markedLibrary.lexer(
  normalizeEscapedMarkdownTables(escapedTable),
  { gfm: true, breaks: false },
);
const flattenedTokens = markedLibrary.lexer(
  flattenedMistralTable,
  { gfm: true, breaks: false },
);

const standardBody = renderAiMarkdown(standardTable);
const escapedBody = renderAiMarkdown(escapedTable);
const codeBody = renderAiMarkdown(codeWithPipes);
const hostileBody = renderAiMarkdown(hostile);
markdownInputsPassedToMarked = [];
const exactBody = renderAiMarkdown(exactMistralRegressionTable);

process.stdout.write(JSON.stringify({
  tokenTypes: {
    standard: standardTokens.map((token) => token.type),
    escaped: escapedTokens.map((token) => token.type),
    normalizedEscaped: normalizedEscapedTokens.map((token) => token.type),
    flattened: flattenedTokens.map((token) => token.type),
  },
  standard: {
    tableCount: findAll(standardBody, "table").length,
    headers: findAll(standardBody, "th").map((cell) => cell.textContent),
    cells: findAll(standardBody, "td").map((cell) => cell.textContent),
    strong: findAll(standardBody, "strong").map((item) => item.textContent),
    code: findAll(standardBody, "code").map((item) => item.textContent),
    rawText: standardBody.textContent,
  },
  escaped: {
    tableCount: findAll(escapedBody, "table").length,
    headers: findAll(escapedBody, "th").map((cell) => cell.textContent),
    cells: findAll(escapedBody, "td").map((cell) => cell.textContent),
    rawText: escapedBody.textContent,
  },
  code: {
    blocks: findAll(codeBody, "pre").length,
    text: findAll(codeBody, "code")[0].textContent,
  },
  hostile: {
    images: findAll(hostileBody, "img").length,
    links: findAll(hostileBody, "a").length,
    text: hostileBody.textContent,
  },
  exactRegression: {
    tableCount: findAll(exactBody, "table").length,
    headers: findAll(exactBody, "th").map((cell) => cell.textContent),
    cells: findAll(exactBody, "td").map((cell) => cell.textContent),
    markedInput: markdownInputsPassedToMarked[0],
  },
}));
'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["tokenTypes"]["standard"], ["table"])
        self.assertEqual(result["tokenTypes"]["escaped"], ["paragraph"])
        self.assertEqual(result["tokenTypes"]["normalizedEscaped"], ["table"])
        self.assertEqual(result["tokenTypes"]["flattened"], ["paragraph"])
        self.assertEqual(result["standard"]["tableCount"], 1)
        self.assertEqual(
            result["standard"]["headers"],
            ["Action", "Autorisée ?", "Responsable"],
        )
        self.assertIn("Lire", result["standard"]["cells"])
        self.assertIn("Réparer", result["standard"]["cells"])
        self.assertEqual(result["standard"]["strong"], ["Oui"])
        self.assertIn("SIRS", result["standard"]["code"])
        self.assertNotIn("|--------|", result["standard"]["rawText"])
        self.assertEqual(result["escaped"]["tableCount"], 1)
        self.assertEqual(
            result["escaped"]["headers"],
            ["Action", "Autorisée ?", "Responsable"],
        )
        self.assertIn("Réparer", result["escaped"]["cells"])
        self.assertNotIn("\\|", result["escaped"]["rawText"])
        self.assertEqual(result["code"]["blocks"], 1)
        self.assertEqual(result["code"]["text"], "SELECT 'a \\| b | c';")
        self.assertEqual(result["hostile"]["images"], 0)
        self.assertEqual(result["hostile"]["links"], 0)
        self.assertIn("<img src=x onerror=alert(1)>", result["hostile"]["text"])
        self.assertEqual(result["exactRegression"]["tableCount"], 1)
        self.assertEqual(
            result["exactRegression"]["headers"],
            ["Outil", "Rôle", "Public cible", "Statut"],
        )
        self.assertIn("Application web", result["exactRegression"]["cells"])
        self.assertIn("|\n|---|", result["exactRegression"]["markedInput"])
        self.assertNotIn("| |---|", result["exactRegression"]["markedInput"])

    def test_frontend_renders_safe_collapsible_sql_with_copy_only(self):
        script = (FRONTEND_DIRECTORY / "js" / "ai-panel.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test de rendu IA.")

        render_source = "async function copyTextToClipboard" + script.split(
            "async function copyTextToClipboard", 1
        )[1].split("function setAiRequestPending", 1)[0]
        program = render_source + r'''
class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.listeners = {};
    this.classList = { add: (...names) => { this.classes = names; } };
    this.textContent = "";
    this.open = false;
    this.scrollHeight = 10;
  }
  append(...children) { this.children.push(...children); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(name, value) { this[name] = value; }
  remove() { this.removed = true; }
  select() { this.selected = true; }
}
const document = {
  body: new Element("body"),
  createElement(tagName) { return new Element(tagName); },
  execCommand(command) { return command === "copy"; },
};
let copiedText = null;
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { clipboard: { async writeText(text) { copiedText = text; } } },
});
const window = { setTimeout(callback) { callback(); } };
const aiConversation = new Element("conversation");
const aiConversationEmpty = { remove() {} };
const aiConversationHistory = [];

(async () => {
  appendAiMessage("assistant", "Sans SQL");
  const noSqlChildren = aiConversation.children[0].children.length;
  const unsafeSql = "SELECT '<script>alert(1)</script>'";
  appendAiMessage("assistant", "Avec SQL", {
    executedQueries: [{ sql: unsafeSql }],
  });
  appendAiMessage("assistant", "Deux SQL", {
    executedQueries: [{ sql: "SELECT 1" }, { sql: "SELECT 1" }],
  });
  const details = aiConversation.children[1].children[2];
  const query = details.children[1];
  const code = query.children[1].children[0];
  const button = query.children[2];
  await button.listeners.click();
  const multiple = aiConversation.children[2].children[2];
  process.stdout.write(JSON.stringify({
    noSqlChildren,
    detailsTag: details.tagName,
    detailsOpen: details.open,
    summary: details.children[0].textContent,
    code: code.textContent,
    copiedText,
    buttonText: button.textContent,
    multipleQueries: multiple.children.length - 1,
    history: aiConversationHistory,
  }));
})();
'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["noSqlChildren"], 2)
        self.assertEqual(result["detailsTag"], "details")
        self.assertFalse(result["detailsOpen"])
        self.assertIn("SQL", result["summary"])
        self.assertIn("1", result["summary"])
        self.assertEqual(result["code"], "SELECT '<script>alert(1)</script>'")
        self.assertEqual(result["copiedText"], result["code"])
        self.assertEqual(result["buttonText"], "Copier")
        self.assertEqual(result["multipleQueries"], 2)
        self.assertEqual(len(result["history"]), 3)
        self.assertTrue(all(set(item) == {"role", "content"} for item in result["history"]))
        self.assertIn('document.createElement("details")', render_source)
        self.assertIn("code.textContent = query.sql", render_source)
        self.assertNotIn("innerHTML", render_source)
        self.assertIn('document.execCommand("copy")', render_source)
        self.assertIn('copyButton.textContent = "Copié"', render_source)
        self.assertNotIn("Exécuter", render_source)
        self.assertNotIn("Ouvrir dans Requêtes", render_source)
        self.assertIn(".ai-sql-details summary", css)

    def test_frontend_renders_safe_document_sources_separately_from_history(self):
        script = (FRONTEND_DIRECTORY / "js" / "ai-panel.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test des sources IA.")

        render_source = "async function copyTextToClipboard" + script.split(
            "async function copyTextToClipboard", 1
        )[1].split("function setAiRequestPending", 1)[0]
        program = render_source + r'''
class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.listeners = {};
    this.className = "";
    this.classList = { add: (...names) => { this.className += names.join(" "); } };
    this._text = "";
    this.scrollHeight = 10;
  }
  append(...children) { this.children.push(...children); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(name, value) { this[name] = value; }
  remove() {}
  get textContent() { return this._text + this.children.map((item) => item.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
}
const document = {
  body: new Element("body"),
  createElement(tagName) { return new Element(tagName); },
  execCommand() { return true; },
};
const navigator = {};
const window = { setTimeout(callback) { callback(); } };
const aiConversation = new Element("conversation");
const aiConversationEmpty = { remove() {} };
const aiConversationHistory = [];

appendAiMessage("assistant", "Sans source");
appendAiMessage("assistant", "Avec source", {
  consultedSources: [{
    title: "<img src=x onerror=alert(1)>",
    path: "docs/<script>.md",
    heading: "Architecture <b>serveur</b>",
  }],
});
appendAiMessage("assistant", "Avec SQL et sources", {
  executedQueries: [{ sql: "SELECT 1" }],
  consultedSources: [
    { title: "README", path: "README.md", heading: null },
    { title: "Guide", path: "docs/guide.md", heading: "Procédure" },
  ],
  externalSources: [
    {
      title: "<img src=x onerror=alert(1)>",
      organization: "Cerema <script>alert(1)</script>",
      url: "https://www.cerema.fr/fr/guide",
    },
    {
      title: "Lien dangereux",
      organization: "Source hostile",
      url: "javascript:alert(1)",
    },
  ],
});
const none = aiConversation.children[0];
const one = aiConversation.children[1].children[2];
const both = aiConversation.children[2];
process.stdout.write(JSON.stringify({
  noSourceChildren: none.children.length,
  oneTag: one.tagName,
  oneSummary: one.children[0].textContent,
  unsafeText: one.children[1].textContent,
  unsafeChildTags: one.children[1].children.map((item) => item.tagName),
  sqlClass: both.children[2].className,
  sourceClass: both.children[3].className,
  sourceSummary: both.children[3].children[0].textContent,
  sourceCount: both.children[3].children.length - 1,
  headinglessChildren: both.children[3].children[1].children.length,
  externalClass: both.children[4].className,
  externalSummary: both.children[4].children[0].textContent,
  externalCount: both.children[4].children.length - 1,
  externalTitle: both.children[4].children[1].children[0].textContent,
  externalOrganization: both.children[4].children[1].children[1].textContent,
  externalHref: both.children[4].children[1].children[0].href,
  externalTarget: both.children[4].children[1].children[0].target,
  externalRel: both.children[4].children[1].children[0].rel,
  history: aiConversationHistory,
}));
'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["noSourceChildren"], 2)
        self.assertEqual(result["oneTag"], "details")
        self.assertEqual(result["oneSummary"], "Sources consultées — 1")
        self.assertIn("<img src=x onerror=alert(1)>", result["unsafeText"])
        self.assertEqual(result["unsafeChildTags"], ["strong", "code", "span"])
        self.assertEqual(result["sqlClass"], "ai-sql-details")
        self.assertEqual(result["sourceClass"], "ai-source-details")
        self.assertEqual(result["sourceSummary"], "Sources consultées — 2")
        self.assertEqual(result["sourceCount"], 2)
        self.assertEqual(result["headinglessChildren"], 2)
        self.assertEqual(result["externalClass"], "ai-external-source-details")
        self.assertEqual(result["externalSummary"], "Sources externes consultées — 1")
        self.assertEqual(result["externalCount"], 1)
        self.assertEqual(result["externalTitle"], "<img src=x onerror=alert(1)>")
        self.assertIn("<script>alert(1)</script>", result["externalOrganization"])
        self.assertEqual(result["externalHref"], "https://www.cerema.fr/fr/guide")
        self.assertEqual(result["externalTarget"], "_blank")
        self.assertEqual(result["externalRel"], "noopener noreferrer")
        self.assertEqual(len(result["history"]), 3)
        self.assertTrue(all(set(item) == {"role", "content"} for item in result["history"]))
        self.assertIn(".ai-source-details summary", css)
        self.assertIn(".ai-external-source-details summary", css)
        self.assertIn("title.textContent = source.title", render_source)
        self.assertIn("path.textContent = source.path", render_source)
        self.assertIn("link.textContent = source.title", render_source)
        self.assertIn('link.rel = "noopener noreferrer"', render_source)
        self.assertNotIn("innerHTML", render_source)

    def test_frontend_has_generic_creation_mode_and_context_prefill(self):
        page = mounted_map_html()
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        for expected in (
            '>Créer</button>',
            'data-create-type="systeme"',
            'data-create-type="digue"',
            'data-create-type="troncon"',
            'data-create-type="desordre"',
        ):
            self.assertIn(expected, header)
        for expected in (
            'id="heritage-object-editor"',
            'id="start-troncon-draw"',
        ):
            self.assertIn(expected, page)
        self.assertIn('entityEditor.setContext({ mode: "create", objectType })', script)
        self.assertIn('selectedHeritageObject?.kind === "Système d\'endiguement"', script)
        self.assertIn('selectedHeritageObject?.kind === "Digue"', script)
        self.assertIn("fillHeritageParentOptions", script)


    def test_frontend_exposes_migrated_business_fields_without_raw_source_ids(self):
        page = mounted_map_html()
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        detail_script = (
            FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        ).read_text(encoding="utf-8")
        for field_id in (
            "heritage-object-designation",
            "heritage-object-commentaire",
            "heritage-object-population",
            "heritage-object-classement",
            "heritage-object-niveau",
            "heritage-object-date-debut",
            "heritage-object-date-fin",
            "heritage-object-type-rive",
            "heritage-object-type-troncon",
            "heritage-object-typologie-troncon",
            "desordre-create-categorie-reference",
            "desordre-create-cote",
            "desordre-create-position",
            "desordre-create-source",
            "desordre-create-lieu-dit",
            "observation-suite-form",
        ):
            self.assertIn(f'id="{field_id}"', page)
        self.assertRegex(page, r'<input(?=[^>]*id="heritage-object-population")(?=[^>]*type="number")(?=[^>]*min="0")(?=[^>]*step="1")[^>]*>')
        self.assertRegex(page, r'<input(?=[^>]*name="date_debut")(?=[^>]*type="date")[^>]*>')
        self.assertRegex(page, r'<input(?=[^>]*name="date_fin")(?=[^>]*type="date")[^>]*>')
        self.assertNotIn("heritage-properties", page)
        self.assertIn('method: "PUT"', detail_script)
        self.assertIn('"population_protegee"', detail_script)
        self.assertIn("categorie_desordre_id: optionalPayloadValue(", script)
        self.assertIn("lieu_dit: optionalPayloadValue(desordreCreateLieuDit.value)", script)
        for field in ("date_debut", "date_fin", "designation", "commentaire"):
            self.assertIn(f'"{field}"', detail_script)
        self.assertIn("disorderFields.lieuDit.value = inputText(properties.lieu_dit)", script)
        self.assertIn("properties.categorie_desordre_id", script)
        self.assertIn('"Suite à donner",', (FRONTEND_DIRECTORY / "js" / "observation-form.js").read_text())
        self.assertIn('"Suite à donner — complément",', (FRONTEND_DIRECTORY / "js" / "observation-form.js").read_text())
        self.assertIn('"Nombre de désordres"', (FRONTEND_DIRECTORY / "js" / "observation-form.js").read_text())
        self.assertIn("photo.libelle || photo.designation", script)
        for forbidden in (
            "gestionnaire_decret_source_id",
            "gestionnaire_technique_source_id",
            "type_rive_source_id",
            "type_troncon_source_id",
            "typologie_troncon_source_id",
            "amenagement_hydraulique_source_id",
            "cote_source_id",
            "position_source_id",
            "source_source_id",
            "photographe_source_id",
            "orientation_photo_source_id",
            "systeme_reperage_source_id",
            "borne_debut_source_id",
            "borne_fin_source_id",
        ):
            self.assertNotIn(f'name="{forbidden}"', page)


    def test_frontend_desordre_drafts_are_local_and_support_three_geometries(self):
        page = mounted_map_html()
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        for expected in (
            'id="desordre-editor"',
            '<option value="Point">Point</option>',
            '<option value="LineString">LineString</option>',
            '<option value="Polygon">Polygon</option>',
            'id="desordre-create-troncons"',
        ):
            self.assertIn(expected, page)
        drawing = script.split(
            'startDesordreDrawButton.addEventListener("click"', 1
        )[1].split(
            '});\n\nfunction cancelDesordreCreationDrawing', 1
        )[0]
        self.assertIn("startMarker", drawing)
        self.assertIn("startPolyline", drawing)
        self.assertIn("startPolygon", drawing)
        self.assertNotIn("fetchJson", drawing)
        self.assertIn('fetchJson("/api/desordres"', script)
        self.assertIn('fetchJson("/api/troncons/options")', script)
        self.assertIn("addCreatedDesordreToMap", script)
        self.assertIn("desordreLayersById", script)


    def test_frontend_keeps_creation_and_geometry_local_until_submit(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        drawing = script.split(
            'startTronconDrawButton.addEventListener("click"', 1
        )[1].split(
            'heritageObjectForm.addEventListener("submit"', 1
        )[0]
        self.assertIn("map.editTools.startPolyline", drawing)
        self.assertIn("clearTronconDraft()", drawing)
        self.assertIn("previous", drawing)
        self.assertNotIn("fetchJson", drawing)
        cancellation = script.split("function closeHeritageDraft()", 1)[1].split(
            "function selectCreatedHeritageObject", 1
        )[0]
        self.assertIn("clearTronconDraft()", cancellation)
        self.assertNotIn("fetchJson", cancellation)
        submission = script.split(
            'heritageObjectForm.addEventListener("submit"', 1
        )[1].split("function selectedCoordinateFamily", 1)[0]
        self.assertIn('method: "POST"', submission)
        self.assertIn("addCreatedObjectToHeritage", submission)
        self.assertIn("addCreatedTronconToMap", submission)
        self.assertIn("showCreatedObject", submission)

    def test_creation_queries_use_transactions_reloads_and_postgis_transform(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "digues_webapp"
            / "queries.py"
        ).read_text()
        self.assertIn("with connection.transaction()", source)
        self.assertIn("return fetch_systeme_endiguement", source)
        self.assertIn("return fetch_digue", source)
        # Tronçon delegates to the canonical writer. Real C3 PostgreSQL tests
        # prove rollback/EWKB; this check only guards the repository boundary.
        self.assertIn("fetch=fetch_troncon", source)
        source += (Path(__file__).resolve().parents[1] / "backend" /
                   "digues_webapp" / "troncons.py").read_text()
        normalized = " ".join(source.lower().split())
        self.assertIn("insert into public.troncons", normalized)
        self.assertIn("st_transform(st_setsrid(", normalized)
        self.assertIn("business_srid = read_business_crs(connection).srid", normalized)
        self.assertIn("st_geomfromgeojson(%s), 4326), {business_srid}", normalized)
        self.assertNotIn("st_geomfromgeojson(%s), 4326), 3950)", normalized)
        self.assertIn("public.geometrie_metier_simple_valide(candidate.g)", normalized)

    def test_hierarchy_query_uses_real_relations_without_artificial_geometry(self):
        normalized = " ".join(SYSTEMES_ENDIGUEMENT_SQL.lower().split())
        self.assertIn("public.systemes", normalized)
        self.assertIn("d.systeme_endiguement_id = s.id", normalized)
        self.assertIn("public.digues", normalized)
        self.assertIn("t.digue_id = d.id", normalized)
        self.assertIn("public.troncons", normalized)
        self.assertNotIn("geometry", normalized)

    def test_hierarchy_result_keeps_identifiers_labels_and_relations(self):
        hierarchy = {
            "systemes": [
                {
                    "id": "systeme-1",
                    "libelle": "SE A",
                    "valid": True,
                    "digues": [
                        {
                            "id": "digue-1",
                            "systeme_endiguement_id": "systeme-1",
                            "libelle": "Digue 1",
                            "valid": True,
                            "troncons": [
                                {
                                    "id": "troncon-1",
                                    "digue_id": "digue-1",
                                    "systeme_reperage_defaut_id": None,
                                    "libelle": "Tronçon 1",
                                    "valid": True,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        connection = FakeConnection(hierarchy)
        self.assertEqual(fetch_systemes_endiguement(connection), hierarchy)
        self.assertIn("public.systemes", connection.cursor_instance.query)

    def test_observation_queries_preserve_both_parent_child_relations(self):
        observations_query = " ".join(DESORDRE_OBSERVATIONS_SQL.lower().split())
        detail_query = " ".join(OBSERVATION_DETAIL_SQL.lower().split())
        self.assertIn("o.desordre_id = d.id", observations_query)
        self.assertIn("p.observation_id = o.id", observations_query)
        self.assertIn("p.observation_id = o.id", detail_query)
        self.assertIn("'suite', o.suite", detail_query)
        self.assertIn("'nombre_desordres', o.nombre_desordres", detail_query)
        self.assertIn("'libelle', p.libelle", detail_query)
        self.assertIn("'commentaire', p.commentaire", detail_query)
        self.assertNotIn("p.desordre_id", observations_query)
        self.assertNotIn("p.desordre_id", detail_query)

    def test_point_read_query_exposes_real_reperage_and_filtered_bornes(self):
        normalized = " ".join(POINT_DESORDRE_SQL.lower().split())
        self.assertIn("public.desordres__troncons", normalized)
        self.assertIn("public.localisations_desordres", normalized)
        self.assertIn("public.bornes_systemes_reperage_lecture", normalized)
        self.assertIn("disponible.systeme_reperage_id = sr.id", normalized)
        self.assertIn("liens.nombre_troncons = 1", normalized)

    def test_desordre_detail_queries_share_common_business_metadata(self):
        common_fields = (
            "designation", "type_desordre_id", "categorie_desordre_id",
            "cote_id", "position_id", "source_id", "commentaire", "lieu_dit",
            "date_debut", "date_fin", "troncon_ids", "valid",
            "dernier_niveau_urgence_id", "dernier_niveau_urgence_libelle",
            "derniere_observation_id", "derniere_observation_date",
        )
        for query in (POINT_DESORDRE_SQL, LINE_DESORDRE_SQL, POLYGON_DESORDRE_SQL):
            with self.subTest(query=query.splitlines()[1].strip()):
                normalized = " ".join(query.lower().split())
                for field in common_fields:
                    self.assertIn(f"'{field}',", normalized)

    def test_desordre_latest_observation_contract_uses_shared_deterministic_rule(self):
        for query in (
            DESORDRES_GEOJSON_SQL,
            POINT_DESORDRE_SQL,
            LINE_DESORDRE_SQL,
            POLYGON_DESORDRE_SQL,
        ):
            with self.subTest(query=query.splitlines()[1].strip()):
                normalized = " ".join(query.lower().split())
                self.assertIn("left join lateral", normalized)
                self.assertIn("left join referentiel.urgences", normalized)
                self.assertIn(
                    "order by o.date_observation desc nulls last, o.id::text",
                    normalized,
                )
                self.assertIn("limit 1", normalized)
                self.assertNotIn("o.valid", normalized)

    def test_point_read_preserves_start_and_end_dates(self):
        feature = {
            "type": "Feature",
            "id": "00000000-0000-0000-0000-000000000001",
            "geometry": {"type": "Point", "coordinates": [2.1, 50.5]},
            "properties": {
                "id": "00000000-0000-0000-0000-000000000001",
                "date_debut": "2025-01-02",
                "date_fin": "2025-03-04",
            },
        }
        result = fetch_point_desordre(
            FakeConnection(feature),
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
        )
        self.assertEqual(result["properties"]["date_debut"], "2025-01-02")
        self.assertEqual(result["properties"]["date_fin"], "2025-03-04")

    def test_line_read_query_preserves_all_vertices_and_reads_reperage(self):
        normalized = " ".join(LINE_DESORDRE_SQL.lower().split())
        self.assertIn("public.desordres", normalized)
        self.assertIn("st_npoints(d.geometry)", normalized)
        self.assertIn("public.localisations_desordres_lecture", normalized)
        self.assertIn("st_startpoint", normalized)
        self.assertIn("st_endpoint", normalized)
        self.assertIn("'debut_x'", normalized)
        self.assertIn("'fin_longitude'", normalized)
        self.assertNotIn("debut_x_3950", normalized)

    def test_line_authorities_use_distinct_postgis_operations(self):
        endpoint_source = inspect.getsource(update_line_desordre_endpoints).lower()
        reperage_source = inspect.getsource(update_point_reperage).lower()
        self.assertIn("st_setpoint", endpoint_source)
        self.assertNotIn("st_linesubstring", endpoint_source)
        self.assertIn("public.localisations_desordres", reperage_source)
        self.assertNotIn("st_setpoint", reperage_source)

    def test_frontend_modes_legend_and_uuid_configuration_are_centralized(self):
        page = mounted_map_html()
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        self.assertIn("Mode de localisation", page)
        for identifier in ("desordre-create-line-coordinates", "desordre-create-bornage",
                           "polygon-representative-point"):
            self.assertIn(f'id="{identifier}"', page)
        self.assertIn('id="polygon-representative-x" type="text" readonly', page)
        self.assertNotIn('name="line-edit-mode"', page)
        self.assertIn('data-layer-toggle="Polygon"', page)
        self.assertNotIn("L.control.layers", script)
        self.assertIn("map.removeLayer(layer)", script)
        self.assertIn('fetchJson("/api/config")', script)
        self.assertIn("body:not(.show-uuid) .technical-identifier", css)


    def test_disorder_forms_share_visual_components_without_id_selectors(self):
        page = mounted_map_html()
        css = (FRONTEND_DIRECTORY / "css" / "entity-forms.css").read_text()
        self.assertEqual(page.count('id="desordre-editor"'), 1)
        for identifier in ("desordre-mode-selector", "desordre-create-bornage-end", "desordre-create-geometry", "point-map-editor", "line-map-editor"):
            self.assertEqual(page.count(f'id="{identifier}"'), 1)
        for common_rule in (".entity-form__field", ".entity-form__control", ".entity-form__field--checkbox", ".entity-form__section", ".entity-form__section-title", ".entity-form__geometry", ".entity-form [hidden]"):
            self.assertIn(common_rule, css)
        self.assertIn("display: none !important", css.split(".entity-form [hidden]", 1)[1].split("}", 1)[0])
        for obsolete in ("desordre-create-editor", "point-editor", "line-editor"):
            self.assertNotIn(f'id="{obsolete}"', page)
            for stylesheet in (css, (FRONTEND_DIRECTORY / "css" / "app.css").read_text()):
                self.assertNotIn(f'#{obsolete} label', stylesheet)
                self.assertNotIn(f'#{obsolete} fieldset', stylesheet)

    def test_shared_form_styles_preserve_hidden_modes_and_control_ids(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        hosts = (FRONTEND_DIRECTORY / "js" / "map-form-hosts.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "entity-forms.css").read_text(encoding="utf-8")
        hidden_rule = css.split(".entity-form [hidden]", 1)[1].split("}", 1)[0]
        self.assertIn("display: none !important", hidden_rule)
        # Ports are mounted once by the common view, not static duplicate inputs.
        for identifier in ("desordre-create-geometry-type", "desordre-mode-selector", "bornage-mode"):
            self.assertEqual(hosts.count(f"'{identifier}'"), 1)
        self.assertEqual(page.count('id="polygon-representative-point"'), 1)
        for retired in ("reproject-bornage", "save-line-bornage", "save-line-endpoints"):
            self.assertNotIn(retired, page)

    def test_frontend_centralizes_modes_and_never_queries_empty_reperage(self):
        page = mounted_map_html()
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        self.assertEqual(page.count('id="desordre-editor"'), 1)
        self.assertIn("function availableDisorderModes", script)
        self.assertIn('return ["map"]', script)
        self.assertIn('const modes = ["map", "xy", "lonlat"]', script)
        self.assertIn(".filter(Boolean)", script)
        availability = script.split(
            "async function refreshCreationReperageAvailability", 1
        )[1].split("async function openDesordreCreation", 1)[0]
        self.assertLess(availability.index("if (!eligible)"), availability.index(
            "loadTronconReperageOptions"
        ))
        self.assertIn("requestVersion === creationReperageRequestVersion", availability)
        self.assertIn("editorGeneration === legacyEditorGeneration", availability)
        self.assertIn("if (!current()", availability)
        self.assertNotIn("Not Found", script)


    def test_disorder_mode_selectors_use_direct_compact_authorities(self):
        page = mounted_map_html()
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")

        def choices(name):
            pattern = (
                rf'<input[^>]*name="{re.escape(name)}"[^>]*value="([^"]+)"[^>]*>'
                rf'\s*<span[^>]*>\s*([^<]+?)\s*</span>\s*</label>'
            )
            return re.findall(pattern, page)

        expected_full = [
            ("map", "Carto"),
            ("xy", "X/Y"),
            ("lonlat", "Lat/Lon"),
            ("bornage", "Bornage"),
        ]
        self.assertEqual(choices("desordre-mode"), expected_full)
        for obsolete_name in (
            "desordre-point-method", "desordre-line-method",
            "desordre-polygon-method", "coordinate-family", "line-edit-mode",
        ):
            self.assertEqual(choices(obsolete_name), [])

        self.assertNotRegex(
            page,
            r'<input[^>]*type="radio"[^>]*value="coordinates"',
        )
        self.assertNotRegex(page, r'>\s*Coordonnées\s*</(?:label|button)>')
        self.assertIn(
            'desordreCreateLineCrs.value = method === "lonlat" '
            '? "EPSG:4326" : businessCRS?.code || ""',
            script,
        )
        common_css = (FRONTEND_DIRECTORY / "css" / "entity-forms.css").read_text()
        mode_rule = common_css.split(".entity-form__mode-options {", 1)[1].split("}", 1)[0]
        choice_rule = common_css.split(".entity-form__mode-choice {", 1)[1].split("}", 1)[0]
        self.assertIn("display: flex", mode_rule)
        self.assertIn("gap: .25rem", mode_rule)
        self.assertIn("min-width: 5ch", choice_rule)
        self.assertIn("min-height: 2.75rem", choice_rule)
        self.assertIn("flex-wrap: wrap", mode_rule)  # narrow screens retain all choices

    def test_single_disorder_editor_routes_state_to_existing_operations(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")

        shell_tag = 'data-business-src="/static/js/entity-editor-shell.js"'
        relation_tag = 'data-business-src="/static/js/relation-selector.js"'
        map_tag = 'data-business-src="/static/js/map.js"'
        self.assertEqual(page.count(shell_tag), 1)
        self.assertEqual(page.count(relation_tag), 1)
        self.assertLess(page.index(shell_tag), page.index(map_tag))
        self.assertLess(page.index(relation_tag), page.index(map_tag))
        self.assertIn("EntityEditorShell.create", script)
        self.assertIn("entityEditor.setContext", script)
        self.assertIn("RelationSelector.fill", script)
        self.assertIn("RelationSelector.values", script)

        hosts = (FRONTEND_DIRECTORY / "js" / "map-form-hosts.js").read_text(encoding="utf-8")
        self.assertEqual(hosts.count("id(disorder.form,'desordre-editor')"), 1)
        self.assertEqual(
            script.count('desordreEditorForm.addEventListener("submit"'), 1
        )
        for state_field in (
            "mode,", "geometryType,", "objectId,", "data,",
        ):
            self.assertIn(state_field, script.split(
                "function setDisorderEditorState", 1
            )[1].split("}", 1)[0])
        self.assertIn('setDisorderEditorState("create", "Point")', script)
        for geometry_type in ("Point", "LineString", "Polygon"):
            self.assertIn(
                f'prepareDisorderEditorForEdit("{geometry_type}"', script
            )

        self.assertIn('fetchJson("/api/desordres", {', script)
        self.assertIn("async function saveDisorderDraft()", script)
        self.assertIn("payload.line_endpoints", script)
        self.assertIn("payload.reperage", script)
        self.assertNotIn("saveLineRequest", script)
        self.assertIn("st_setpoint", inspect.getsource(
            update_line_desordre_endpoints
        ).lower())

        # Mounting identity uniqueness and runtime resolution are exercised by the
        # real form-geometry-interactions browser scenarios; retain retired-port guards.
        self.assertIn("MapFormViews.disorder", script)
        for removed_id in (
            "desordre-create-editor", "point-editor", "line-editor",
        ):
            self.assertNotIn(removed_id, page)
            self.assertNotIn(f'#{removed_id}', script)

        create_shell = script.split("async function openDesordreCreation", 1)[1].split(
            "function closeDesordreDraft", 1
        )[0]
        self.assertIn('showEditorTab("general")', create_shell)
        self.assertIn("observationsTab.hidden = true", create_shell)

    def test_create_linestring_bornage_follows_rendered_troncon_cardinality(self):
        states = mounted_mode_states()
        self.assertEqual(states["cardinality"], [
            {"count": count, "available": count == 1, "hidden": count != 1, "disabled": count != 1}
            for count in (0,1,2,3,2,1)
        ])
        self.assertEqual(states["polygon"], {"visible": False, "enabled": False})
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text()
        handler = script.split('desordreCreateTroncons.addEventListener("change"',1)[1].split('startDesordreDrawButton.addEventListener',1)[0]
        self.assertIn("void refreshCreationReperageAvailability()", handler)
        availability = script.split("async function refreshCreationReperageAvailability", 1)[1].split("async function openDesordreCreation", 1)[0]
        self.assertLess(availability.index("renderDisorderModeChoices(false)"), availability.index("await loadTronconReperageOptions"))
        self.assertIn("Choisissez les nouvelles références.", handler)
        payload = script.split("function buildDesordreCreationPayload",1)[1].split("function configureDesordreLayer",1)[0]
        self.assertIn('if (!modes.includes("bornage"))', payload)

    def test_create_linestring_replacement_never_flashes_bornage_visibility(self):
        states = mounted_mode_states()
        self.assertTrue(all(state["visible"] for state in states["pending"]))
        self.assertEqual([state["enabled"] for state in states["pending"]], [True,False,True,True])
        self.assertEqual(states["zero"], {"visible": False, "enabled": False})
        self.assertEqual(states["many"], {"visible": False, "enabled": False})
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text()
        renderer = script.split("function renderDisorderModeChoices",1)[1].split("function updateLineCoordinateLabels",1)[0]
        self.assertIn("creationBornageChoiceState", renderer)

    def test_point_graphical_edit_supports_deliberate_map_tap(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        tap_handler = script.split('map.on("click", (event)', 1)[1].split(
            'cancelMapPositionButton.addEventListener', 1
        )[0]
        self.assertIn("graphicEditActive", tap_handler)
        self.assertIn("setProvisionalPointPosition(event.latlng)", tap_handler)
        helper = script.split('function setProvisionalPointPosition', 1)[1].split('map.on("click"', 1)[0]
        self.assertIn("activePointLayer.setLatLng(provisionalLatLng)", helper)
        self.assertIn("validateMapPositionButton.disabled = false", helper)
        self.assertNotIn("fetchJson", helper)
        self.assertNotIn("fetchJson", tap_handler)

    def test_observation_results_keep_identifiers_and_nested_photos(self):
        desordre_id = uuid.uuid4()
        observation_id = uuid.uuid4()
        parent = {
            "resource": "desordres", "id": str(desordre_id),
            "designation": "Désordre",
        }
        summary = {
            "parent": parent,
            "observations": [{"id": str(observation_id), "parent": parent,
                              "photo_count": 1}],
        }
        connection = FakeConnection(summary)
        self.assertEqual(fetch_desordre_observations(connection, desordre_id), summary)
        self.assertEqual(connection.cursor_instance.params, (desordre_id,))

        detail = {
            "id": str(observation_id),
            "photos": [
                {
                    "id": str(uuid.uuid4()),
                    "observation_id": str(observation_id),
                    "content_available": False,
                }
            ],
        }
        connection = FakeConnection({**detail, "_parent_candidates": [
            {**parent, "matched_id": str(desordre_id)},
        ]})
        detail["parent"] = parent
        self.assertEqual(fetch_observation(connection, observation_id), detail)
        self.assertEqual(
            detail["photos"][0]["observation_id"],
            detail["id"],
        )

    def test_frontend_has_observation_navigation_and_photo_lightbox(self):
        page = mounted_map_html()
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        for identifier in (
            'id="general-tab-button"',
            'id="observations-tab-button"',
            'id="observations-list"',
            'id="observation-detail-view"',
            'id="observation-photos"',
            'id="photo-lightbox"',
        ):
            self.assertIn(identifier, page)
        self.assertIn("/observations`", script)
        self.assertIn("/api/observations/${encodeURIComponent", script)
        self.assertIn("showPhotoInLightbox", script)
        self.assertIn("backToObservationsButton", script)


    def test_frontend_has_exclusive_bornage_mode_and_server_reload(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        hosts = (FRONTEND_DIRECTORY / "js" / "map-form-hosts.js").read_text(encoding="utf-8")
        self.assertIn("modeName:'desordre-mode'", hosts)
        for field in ("Borne", "Distance (m)", "Sens"):
            self.assertIn(field, hosts)
        for source in ("buildPointReperagePayload", "buildLineReperagePayload",
                       "payload.reperage", "options.systeme_reperage_id",
                       "renderPointServerFeature(feature)", "updatePointLayer(feature)"):
            self.assertIn(source, script)
        # The original specialized repérage calculation now runs inside the general
        # mutation transaction; no independent browser PUT is allowed.
        self.assertNotIn(')}/reperage`', script)
        save = script.split("async function saveDisorderDraft()", 1)[1].split(
            "function selectedDesordreTronconIds", 1
        )[0]
        self.assertIn("await writeDesordre", save)


    def test_general_save_retains_geometry_specific_bornage_warnings(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        for retired in ("desordre-bornage-actions", "reproject-bornage", "save-line-bornage"):
            self.assertNotIn(retired, page)
        self.assertIn("modifier le bornage repositionne le point", page)
        self.assertIn("Les sommets de la géométrie actuelle sont perdus", page)
        self.assertIn('pointBornageWarning.hidden = !editing || !point || method !== "bornage"', script)
        self.assertIn('lineBornageWarning.hidden = !editing || !line || method !== "bornage"', script)
        modes = script.split("function availableDisorderModes", 1)[1].split(
            "function canonicalDisorderBornageAvailable", 1
        )[0]
        self.assertIn('if (geometryType === "Polygon") return ["map"]', modes)

    def test_general_save_uses_current_bornage_payload_even_without_dirty_check(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        save = script.split("async function saveDisorderDraft()", 1)[1].split(
            "function selectedDesordreTronconIds", 1
        )[0]
        # Unchanged repérage deliberately remains a request to reproject, unlike
        # merely viewing numeric coordinates. SQL tests verify the actual result.
        self.assertIn('if (mode === "bornage") payload.reperage', save)
        self.assertIn("buildPointReperagePayload() : buildLineReperagePayload()", save)
        self.assertNotIn("initialFormValues", save)
        self.assertNotIn("initialLineReperageValues", save)

    def test_frontend_has_explicit_linestring_editing_without_drag_writes(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn(
            'src="/static/vendor/leaflet-editable/Leaflet.Editable.js"',
            page,
        )
        hosts = (FRONTEND_DIRECTORY / "js" / "map-form-hosts.js").read_text(encoding="utf-8")
        for identifier in ("start-line-edit", "validate-line-edit", "cancel-line-edit"):
            self.assertEqual(hosts.count(f"'{identifier}'"), 1)
        self.assertIn("activeLineLayer.enableEdit(map)", script)
        self.assertIn("activeLineLayer?.disableEdit?.()", script)
        self.assertIn('map.on("editable:editing"', script)
        finish = script.split("function validateExistingLineEdit()", 1)[1].split(
            "validateLineEditButton.addEventListener", 1
        )[0]
        self.assertIn("stageDisorderGeometry", finish)
        self.assertIn("finishDisorderGeometryEdit()", finish)
        self.assertNotIn("writeDesordre", finish)
        self.assertNotIn("fetchJson", finish)
        editing_handler = script.split('map.on("editable:editing"', 1)[1].split(
            "startLineEditButton.addEventListener", 1
        )[0]
        self.assertNotIn("fetchJson", editing_handler)
        self.assertNotIn('method: "PUT"', editing_handler)

    def test_disorder_geometry_type_and_reperage_labels_remain_stable(self):
        page = mounted_map_html()
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")

        self.assertIn("Type de dessin", page)
        for geometry_type in ("Point", "LineString", "Polygon"):
            self.assertIn(
                f'<option value="{geometry_type}">{geometry_type}</option>',
                page,
            )
        self.assertNotIn("Nombre de sommets", page)
        self.assertNotIn('id="line-vertex-count"', page)
        self.assertNotIn("Repérage relu", page)
        self.assertIn("Mode de localisation", page)
        self.assertNotIn("vertexCount", script)

        renderer = script.split("function renderLineServerFeature", 1)[1].split(
            "function lineBornageDraftModified", 1
        )[0]
        self.assertIn(
            "disorderFields.geometryType.value = feature.geometry.type;",
            renderer,
        )
        self.assertNotIn("properties.type_geometrie", renderer)
        self.assertIn("fillLineDisorderBornage(properties.reperage || {})", renderer)

        point_renderer = script.split("function renderPointServerFeature", 1)[1].split(
            "function lineReperageSummary", 1
        )[0]
        self.assertIn(
            "renderReperage(properties.reperage);",
            point_renderer,
        )
        controls = script.split("function updateDisorderEditorControls", 1)[1].split(
            "async function refreshCreationReperageAvailability", 1
        )[0]
        self.assertIn(
            "polygonRepresentativePoint.hidden = !editing || geometryType !== \"Polygon\";",
            controls,
        )



class PointUpdateValidationTest(unittest.TestCase):
    def test_accepts_xy_pair(self):
        update = PointDesordreUpdate(x=12.5, y=9.25)
        self.assertEqual(update.x, 12.5)

    def test_accepts_lonlat_pair(self):
        update = PointDesordreUpdate(longitude=2.25, latitude=48.75)
        self.assertEqual(update.latitude, 48.75)
        self.assertEqual(
            set(update.model_dump(exclude_unset=True)),
            {"longitude", "latitude"},
        )

    def test_rejects_incomplete_xy_pair(self):
        with self.assertRaisesRegex(ValidationError, "X et Y"):
            PointDesordreUpdate(x=12.5)

    def test_rejects_incomplete_lonlat_pair(self):
        with self.assertRaisesRegex(ValidationError, "Longitude et latitude"):
            PointDesordreUpdate(longitude=2.25)

    def test_rejects_two_coordinate_families(self):
        with self.assertRaisesRegex(ValidationError, "Une seule famille"):
            PointDesordreUpdate(
                x=12.5,
                y=9.25,
                longitude=2.25,
                latitude=48.75,
            )

    def test_accepts_complete_reperage_family(self):
        borne_id = uuid.uuid4()
        update = PointReperageUpdate(
            borne_debut_id=borne_id,
            distance_debut_m=12.5,
            position_debut_relative="APRES_BORNE",
        )
        self.assertEqual(update.borne_debut_id, borne_id)

    def test_rejects_invalid_reperage_distance_and_sense(self):
        with self.assertRaisesRegex(ValidationError, "positive ou nulle"):
            PointReperageUpdate(
                borne_debut_id=uuid.uuid4(),
                distance_debut_m=-1,
                position_debut_relative="APRES_BORNE",
            )
        with self.assertRaisesRegex(ValidationError, "nulle"):
            PointReperageUpdate(
                borne_debut_id=uuid.uuid4(),
                distance_debut_m=1,
                position_debut_relative="SUR_BORNE",
            )
        with self.assertRaises(ValidationError):
            PointReperageUpdate(
                borne_debut_id=uuid.uuid4(),
                distance_debut_m=1,
                position_debut_relative="SENS_INCONNU",
            )

    def test_reperage_payload_rejects_coordinate_families(self):
        with self.assertRaises(ValidationError):
            PointReperageUpdate(
                borne_debut_id=uuid.uuid4(),
                distance_debut_m=10,
                position_debut_relative="APRES_BORNE",
                x=10,
                y=0,
            )

    def test_accepts_multivertex_linestring_geometry(self):
        update = DesordreGeometryUpdate(
            geometry={
                "type": "LineString",
                "coordinates": [[2.1, 50.5], [2.11, 50.51], [2.12, 50.52]],
            }
        )
        self.assertEqual(len(update.geometry.coordinates), 3)

    def test_line_endpoints_require_two_distinct_complete_positions(self):
        endpoints = LineEndpoints(crs="EPSG:3950", debut=(1, 2), fin=(3, 4))
        self.assertEqual(endpoints.fin, (3, 4))
        with self.assertRaises(ValidationError):
            LineEndpoints(crs="EPSG:4326", debut=(2, 50), fin=(2, 50))

    def test_rejects_non_line_short_or_invalid_coordinates(self):
        for geometry in (
            {"type": "Point", "coordinates": [2.1, 50.5]},
            {"type": "LineString", "coordinates": [[2.1, 50.5]]},
            {
                "type": "LineString",
                "coordinates": [[2.1, 50.5], [float("nan"), 50.6]],
            },
            {
                "type": "LineString",
                "coordinates": [[2.1, 50.5], [181, 50.6]],
            },
        ):
            with self.subTest(geometry=geometry):
                with self.assertRaises(ValidationError):
                    DesordreGeometryUpdate(geometry=geometry)

    def test_geometry_update_accepts_valid_polygon(self):
        update = DesordreGeometryUpdate(geometry={
            "type": "Polygon",
            "coordinates": [[[2, 50], [2.1, 50], [2.1, 50.1], [2, 50]]],
        })
        self.assertEqual(update.geometry.type, "Polygon")


class HeritageCreationValidationTest(unittest.TestCase):
    def test_named_objects_trim_labels_and_default_to_valid(self):
        creation = SystemeEndiguementCreate(libelle="  SE neuf  ")
        self.assertEqual(creation.libelle, "SE neuf")
        self.assertTrue(creation.valid)
        with self.assertRaisesRegex(ValidationError, "libellé"):
            SystemeEndiguementCreate(libelle="   ")

    def test_digue_keeps_its_parent_and_troncon_accepts_optional_parents(self):
        with self.assertRaises(ValidationError):
            DigueCreate(libelle="Digue")
        troncon = TronconCreate(type_physique_id="BERGE")
        self.assertIsNone(troncon.digue_id)
        self.assertIsNone(troncon.amenagement_hydraulique_id)
        self.assertIsNone(troncon.geometry)

    def test_troncon_accepts_every_linestring_vertex(self):
        creation = TronconCreate(
            digue_id=uuid.uuid4(),
            libelle="Tronçon sinueux",
            geometry={
                "type": "LineString",
                "coordinates": [
                    [2.10, 48.50],
                    [2.11, 48.52],
                    [2.13, 48.51],
                    [2.15, 48.54],
                ],
            },
        )
        self.assertEqual(len(creation.geometry.coordinates), 4)

    def test_new_heritage_fields_preserve_null_dates_and_integer_types(self):
        systeme = SystemeEndiguementCreate(
            libelle="SE",
            commentaire="  Commentaire long  ",
            designation="  Désignation  ",
            population_protegee=1250,
            classement="  B  ",
            niveau_protection="  10 m  ",
        )
        self.assertEqual(systeme.commentaire, "Commentaire long")
        self.assertEqual(systeme.population_protegee, 1250)
        self.assertEqual(systeme.classement, "B")
        with self.assertRaises(ValidationError):
            SystemeEndiguementCreate(libelle="SE", population_protegee=-1)

        troncon = TronconCreate(
            digue_id=uuid.uuid4(),
            libelle="Tronçon",
            date_debut="2020-01-02",
            date_fin=None,
            commentaire=" ",
            designation=" T1 ",
            type_rive_id=" rive-gauche ",
            type_troncon_id=" remblai ",
            typologie_troncon_id=" fluviale ",
            geometry={
                "type": "LineString",
                "coordinates": [[2.1, 48.5], [2.2, 48.6]],
            },
        )
        self.assertEqual(troncon.date_debut.isoformat(), "2020-01-02")
        self.assertIsNone(troncon.date_fin)
        self.assertEqual(troncon.commentaire, " ")
        self.assertEqual(troncon.designation, " T1 ")
        self.assertEqual(troncon.type_rive_id, " rive-gauche ")
        self.assertEqual(troncon.type_troncon_id, " remblai ")
        self.assertEqual(troncon.typologie_troncon_id, " fluviale ")

    def test_heritage_updates_reject_unknown_ids_as_not_found(self):
        cases = (
            (
                update_systeme_endiguement,
                SystemeEndiguementUpdate(libelle="SE"),
            ),
            (update_digue, DigueUpdate(libelle="Digue")),
            (update_troncon, TronconUpdate(libelle="Tronçon")),
        )
        for update_function, update in cases:
            with self.subTest(update_function=update_function.__name__):
                connection = MagicMock()
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchone.return_value = None
                with self.assertRaises(HeritageNotFoundError):
                    update_function(connection, uuid.uuid4(), update)


class DesordreCreationValidationTest(unittest.TestCase):
    def test_accepts_point_line_and_polygon(self):
        geometries = (
            {"type": "Point", "coordinates": [2.1, 50.5]},
            {
                "type": "LineString",
                "coordinates": [[2.1, 50.5], [2.2, 50.6], [2.3, 50.55]],
            },
            {
                "type": "Polygon",
                "coordinates": [[
                    [2.1, 50.5], [2.2, 50.5], [2.2, 50.6], [2.1, 50.5]
                ]],
            },
        )
        for geometry in geometries:
            with self.subTest(geometry=geometry["type"]):
                creation = DesordreCreate(geometry=geometry)
                self.assertEqual(creation.geometry.type, geometry["type"])

    def test_accepts_category_and_lieu_dit_as_business_fields(self):
        creation = DesordreCreate(
            categorie_desordre_id=" categorie-1 ",
            lieu_dit=" Les Prés ",
            geometry={"type": "Point", "coordinates": [2.1, 50.5]},
        )
        self.assertEqual(creation.categorie_desordre_id, "categorie-1")
        self.assertEqual(creation.lieu_dit, "Les Prés")

    def test_accepts_point_xy_or_lonlat_as_postgresql_authority(self):
        self.assertEqual(
            DesordreCreate(x=12, y=9).x,
            12,
        )
        self.assertEqual(
            DesordreCreate(longitude=2.25, latitude=50.5).latitude,
            50.5,
        )

    def test_rejects_missing_or_multiple_location_authorities(self):
        with self.assertRaisesRegex(ValidationError, "exactement une"):
            DesordreCreate(designation="Sans géométrie")
        with self.assertRaisesRegex(ValidationError, "exactement une"):
            DesordreCreate(
                geometry={"type": "Point", "coordinates": [2.1, 50.5]},
                x=1,
                y=2,
            )

    def test_rejects_invalid_polygon_and_unsupported_geometry(self):
        invalid = (
            {
                "type": "Polygon",
                "coordinates": [[[2.1, 50.5], [2.2, 50.5], [2.2, 50.6]]],
            },
            {
                "type": "Polygon",
                "coordinates": [[
                    [2.1, 50.5], [2.2, 50.5], [2.2, 50.6], [2.0, 50.4]
                ]],
            },
            {"type": "MultiLineString", "coordinates": []},
        )
        for geometry in invalid:
            with self.subTest(geometry=geometry):
                with self.assertRaises(ValidationError):
                    DesordreCreate(geometry=geometry)

    def test_rejects_non_finite_out_of_domain_and_duplicate_links(self):
        for coordinates in ([float("nan"), 50.5], [181, 50.5], [2.1, 91]):
            with self.subTest(coordinates=coordinates):
                with self.assertRaises(ValidationError):
                    DesordreCreate(
                        geometry={"type": "Point", "coordinates": coordinates}
                    )
        troncon_id = uuid.uuid4()
        with self.assertRaisesRegex(ValidationError, "qu'une fois"):
            DesordreCreate(
                geometry={"type": "Point", "coordinates": [2.1, 50.5]},
                troncon_ids=[troncon_id, troncon_id],
            )
        with self.assertRaises(ValidationError):
            DesordreCreate(
                longitude=2.1,
                latitude=50.5,
                troncon_ids=[""],
            )
        self.assertIsNone(DesordreCreate(
            longitude=2.1,
            latitude=50.5,
            type_desordre_id="",
        ).type_desordre_id)

    def test_point_accepts_zero_or_one_troncon_and_rejects_more(self):
        geometry = {"type": "Point", "coordinates": [2.1, 50.5]}
        DesordreCreate(geometry=geometry)
        DesordreCreate(geometry=geometry, troncon_ids=[uuid.uuid4()])
        for count in (2, 3):
            with self.subTest(count=count), self.assertRaisesRegex(
                ValidationError, "au plus un tronçon"
            ):
                DesordreCreate(
                    geometry=geometry,
                    troncon_ids=[uuid.uuid4() for _ in range(count)],
                )

    def test_line_and_polygon_accept_multiple_troncons(self):
        links = [uuid.uuid4(), uuid.uuid4()]
        DesordreCreate(geometry={
            "type": "LineString", "coordinates": [[2, 50], [2.1, 50.1]],
        }, troncon_ids=links)
        DesordreCreate(geometry={
            "type": "Polygon",
            "coordinates": [[[2, 50], [2.1, 50], [2.1, 50.1], [2, 50]]],
        }, troncon_ids=links)


@unittest.skipIf(web_show_uuid is None, "FastAPI indisponible")
class WebConfigurationTest(unittest.TestCase):
    def test_uuid_visibility_defaults_false_and_can_be_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(web_show_uuid())
            os.environ["DIGUES_WEB_SHOW_UUID"] = "true"
            self.assertTrue(web_show_uuid())

    def test_uuid_visibility_rejects_invalid_canonical_value_and_ignores_old_name(self):
        with patch.dict(os.environ, {"SIRS_WEB_SHOW_UUID": "true"}, clear=True):
            self.assertFalse(web_show_uuid())
        with patch.dict(os.environ, {"DIGUES_WEB_SHOW_UUID": "invalid"}, clear=True):
            with self.assertRaises(ValueError):
                web_show_uuid()
        with patch.dict(os.environ, {"DIGUES_WEB_SHOW_UUID": ""}, clear=True):
            with self.assertRaises(ValueError):
                web_show_uuid()


class WebPostGISIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.postgres_safety import connect_test_database, require_test_dsn
        require_test_dsn("DIGUES_WEB_TEST_DSN")
        try:
            cls.connection = connect_test_database("DIGUES_WEB_TEST_DSN", autocommit=False)
            with cls.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('public.troncons'), "
                    "to_regclass('public.desordres'), "
                    "to_regprocedure('public.appliquer_reperage_desordre()') IS NOT NULL"
                )
                if cursor.fetchone() != (
                    "troncons",
                    "desordres",
                    True,
                ):
                    raise AssertionError("Le DDL canonique de Désordre est incomplet")
                cls.desordre_id = uuid.uuid4()
                cls.observation_new_id = uuid.uuid4()
                cls.observation_old_id = uuid.uuid4()
                cls.photo_new_id = uuid.uuid4()
                cls.photo_old_id = uuid.uuid4()
                cls.systeme_endiguement_id = uuid.uuid4()
                cls.digue_id = uuid.uuid4()
                cls.reperage_troncon_id = uuid.uuid4()
                cls.second_troncon_id = uuid.uuid4()
                cls.systeme_reperage_id = uuid.uuid4()
                cls.second_systeme_reperage_id = uuid.uuid4()
                cls.borne_debut_id = uuid.uuid4()
                cls.borne_fin_id = uuid.uuid4()
                cls.incompatible_borne_id = uuid.uuid4()
                cls.second_borne_fin_id = uuid.uuid4()
                cls.reperage_desordre_id = uuid.uuid4()
                cls.many_troncons_desordre_id = uuid.uuid4()
                cls.line_desordre_id = uuid.uuid4()
                cls.categorie_desordre_id = f"categorie-web-{uuid.uuid4()}"
                cls.type_desordre_id = f"type-web-{uuid.uuid4()}"
                cursor.execute(
                    "INSERT INTO public.desordres "
                    "(id, designation, commentaire, geometry, valid) "
                    "VALUES (%s, 'Web test', 'Initial', "
                    "ST_SetSRID(ST_Point(10, 7), 3950), true)",
                    (cls.desordre_id,),
                )
                cursor.execute(
                    "INSERT INTO public.observations "
                    "(id, desordre_id, designation, date_observation, evolution, valid) VALUES "
                    "(%s, %s, 'Observation récente', DATE '2025-05-02', "
                    "'Évolution récente', true), "
                    "(%s, %s, 'Observation ancienne', DATE '2024-01-03', "
                    "'Évolution ancienne', true)",
                    (
                        cls.observation_new_id,
                        cls.desordre_id,
                        cls.observation_old_id,
                        cls.desordre_id,
                    ),
                )
                cursor.execute(
                    "INSERT INTO public.photos "
                    "(id, observation_id, storage_key, date_photo, designation, valid) "
                    "VALUES (%s, %s, %s, DATE '2025-05-03', 'Vue aval', true), "
                    "(%s, %s, %s, DATE '2025-05-01', 'Vue amont', true)",
                    (
                        cls.photo_new_id,
                        cls.observation_new_id,
                        r"C:\\archives\\vue-aval.jpg",
                        cls.photo_old_id,
                        cls.observation_new_id,
                        f"web-test/{cls.photo_old_id}.jpg",
                    ),
                )
                cursor.execute(
                    "INSERT INTO referentiel.categories_desordre "
                    "(id, libelle, valid) VALUES (%s, 'Catégorie web', true)",
                    (cls.categorie_desordre_id,),
                )
                cursor.execute(
                    "INSERT INTO referentiel.types_desordre "
                    "(id, categorie_id, libelle, valid) "
                    "VALUES (%s, %s, 'Type web', true)",
                    (cls.type_desordre_id, cls.categorie_desordre_id),
                )
                cursor.execute(
                    "INSERT INTO public.systemes (id, libelle, valid) "
                    "VALUES (%s, 'SE web bornage', true)",
                    (cls.systeme_endiguement_id,),
                )
                cursor.execute(
                    "INSERT INTO public.digues "
                    "(id, systeme_endiguement_id, libelle, valid) "
                    "VALUES (%s, %s, 'Digue web bornage', true)",
                    (cls.digue_id, cls.systeme_endiguement_id),
                )
                cursor.execute(
                    "INSERT INTO public.troncons "
                    "(id, type_physique_id, digue_id, libelle, geometry, valid) VALUES "
                    "(%s, 'INDETERMINE', %s, 'Tronçon web bornage', "
                    "ST_GeomFromText('LINESTRING(0 0,100 0)', 3950), true), "
                    "(%s, 'INDETERMINE', %s, 'Second tronçon web', "
                    "ST_GeomFromText('LINESTRING(0 10,100 10)', 3950), true)",
                    (
                        cls.reperage_troncon_id,
                        cls.digue_id,
                        cls.second_troncon_id,
                        cls.digue_id,
                    ),
                )
                cursor.execute(
                    "INSERT INTO public.systemes_reperage "
                    "(id, troncon_id, libelle, valid) VALUES "
                    "(%s, %s, 'Repérage web', true), "
                    "(%s, %s, 'Second repérage web', true)",
                    (
                        cls.systeme_reperage_id,
                        cls.reperage_troncon_id,
                        cls.second_systeme_reperage_id,
                        cls.second_troncon_id,
                    ),
                )
                cursor.execute(
                    "INSERT INTO public.bornes_reperage "
                    "(id, libelle, geometry, valid) VALUES "
                    "(%s, 'Borne A', ST_SetSRID(ST_Point(0, 0), 3950), true), "
                    "(%s, 'Borne B', ST_SetSRID(ST_Point(100, 0), 3950), true), "
                    "(%s, 'Borne C', ST_SetSRID(ST_Point(0, 10), 3950), true), "
                    "(%s, 'Borne D', ST_SetSRID(ST_Point(100, 10), 3950), true)",
                    (
                        cls.borne_debut_id,
                        cls.borne_fin_id,
                        cls.incompatible_borne_id,
                        cls.second_borne_fin_id,
                    ),
                )
                for troncon_id, systeme_id, debut_id, fin_id in (
                    (
                        cls.reperage_troncon_id,
                        cls.systeme_reperage_id,
                        cls.borne_debut_id,
                        cls.borne_fin_id,
                    ),
                    (
                        cls.second_troncon_id,
                        cls.second_systeme_reperage_id,
                        cls.incompatible_borne_id,
                        cls.second_borne_fin_id,
                    ),
                ):
                    cursor.execute(
                        "INSERT INTO public.troncons_bornes "
                        "(troncon_id, borne_id) VALUES (%s, %s), (%s, %s)",
                        (troncon_id, debut_id, troncon_id, fin_id),
                    )
                    cursor.execute(
                        "INSERT INTO public.systemes_reperage__bornes "
                        "(id, systeme_reperage_id, borne_id, valeur_pr, valid) "
                        "VALUES (%s, %s, %s, 0, true), "
                        "(%s, %s, %s, 100, true)",
                        (
                            uuid.uuid4(),
                            systeme_id,
                            debut_id,
                            uuid.uuid4(),
                            systeme_id,
                            fin_id,
                        ),
                    )
                    cursor.execute(
                        "UPDATE public.troncons "
                        "SET systeme_reperage_defaut_id = %s WHERE id = %s",
                        (systeme_id, troncon_id),
                    )
                cursor.execute(
                    "INSERT INTO public.desordres "
                    "(id, designation, geometry, valid) VALUES "
                    "(%s, 'Point web bornage', "
                    "ST_SetSRID(ST_Point(10, 7), 3950), true), "
                    "(%s, 'Point web multilien', "
                    "ST_SetSRID(ST_Point(10, 7), 3950), true), "
                    "(%s, 'Ligne web', "
                    "ST_GeomFromText('LINESTRING(5 2,30 8,80 3)', 3950), true)",
                    (
                        cls.reperage_desordre_id,
                        cls.many_troncons_desordre_id,
                        cls.line_desordre_id,
                    ),
                )
                cursor.execute(
                    "INSERT INTO public.desordres__troncons "
                    "(desordre_id, troncon_id) VALUES "
                    "(%s, %s), (%s, %s), (%s, %s), (%s, %s)",
                    (
                        cls.reperage_desordre_id,
                        cls.reperage_troncon_id,
                        cls.many_troncons_desordre_id,
                        cls.reperage_troncon_id,
                        cls.many_troncons_desordre_id,
                        cls.second_troncon_id,
                        cls.line_desordre_id,
                        cls.reperage_troncon_id,
                    ),
                )
        except Exception:
            connection = getattr(cls, "connection", None)
            if connection is not None:
                connection.rollback()
                connection.close()
            raise

    @classmethod
    def tearDownClass(cls):
        connection = getattr(cls, "connection", None)
        if connection is not None:
            connection.rollback()
            connection.close()

    def test_ai_introspection_matches_current_postgis_business_catalog(self):
        columns, primary_keys, foreign_keys = introspect_ai_schema(self.connection)
        relation_names = {(row[0], row[1], row[2]) for row in columns}
        self.assertIn(("public", "systemes", "TABLE"), relation_names)
        self.assertIn(("public", "desordres_points_saisie", "VIEW"), relation_names)
        self.assertNotIn(("public", "spatial_ref_sys", "TABLE"), relation_names)
        self.assertTrue(any(row[:3] == ("public", "systemes", "id") for row in primary_keys))
        self.assertTrue(any(
            row[:6] == (
                "public", "troncons", "digue_id", "public", "digues", "id"
            )
            for row in foreign_keys
        ))
        advertised = next(row[4] for row in columns
                          if row[:2] == ('public', 'troncons') and row[3] == 'geometry')
        installed = self.connection.execute(
            "SELECT format_type(atttypid,atttypmod) FROM pg_attribute "
            "WHERE attrelid='public.troncons'::regclass AND attname='geometry' "
            "AND NOT attisdropped").fetchone()[0]
        self.assertEqual(advertised, installed)

    def setUp(self):
        self.connection.execute("SAVEPOINT web_api_test")

    def tearDown(self):
        self.connection.execute("ROLLBACK TO SAVEPOINT web_api_test")
        self.connection.execute("RELEASE SAVEPOINT web_api_test")

    def test_real_endpoints_queries_return_feature_collections(self):
        for resource, collection in (
            ("troncons",fetch_troncons(self.connection)),
            ("desordres",fetch_desordres(self.connection)),
        ):
            self.assertEqual(collection["type"], "FeatureCollection")
            self.assertIsInstance(collection["features"], list)
            for feature in collection["features"]:
                self.assertEqual(feature["type"], "Feature")
                if feature["geometry"] is None:
                    self.assertEqual(resource,"troncons")
                else:
                    allowed={"Point","LineString","Polygon"}
                    if resource=="troncons":allowed|={"MultiPoint","MultiLineString","MultiPolygon","GeometryCollection"}
                    self.assertIn(feature["geometry"]["type"],allowed)
                self.assertIsInstance(feature["properties"], dict)

    def test_real_hierarchy_has_coherent_parent_relations(self):
        hierarchy = fetch_systemes_endiguement(self.connection)
        self.assertIsInstance(hierarchy["systemes"], list)
        for systeme in hierarchy["systemes"]:
            self.assertTrue({"id", "libelle", "valid", "digues"} <= set(systeme))
            self.assertNotIn("geometry", systeme)
            for digue in systeme["digues"]:
                self.assertEqual(digue["systeme_endiguement_id"], systeme["id"])
                self.assertTrue({"id", "libelle", "valid", "troncons"} <= set(digue))
                self.assertNotIn("geometry", digue)
                for troncon in digue["troncons"]:
                    self.assertEqual(troncon["digue_id"], digue["id"])
                    self.assertTrue({"id", "libelle", "valid"} <= set(troncon))

    def test_create_systeme_reloads_the_persisted_object(self):
        created = create_systeme_endiguement(
            self.connection,
            SystemeEndiguementCreate(
                libelle="  SE créé par le web  ",
                designation="SE test",
                commentaire="Conservé",
                population_protegee=321,
                classement="B",
                niveau_protection="9 m",
            ),
        )
        self.assertEqual(created["libelle"], "SE créé par le web")
        self.assertTrue(created["valid"])
        self.assertEqual(created["digues"], [])
        self.assertEqual(created["population_protegee"], 321)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT libelle, designation, commentaire, population_protegee, "
                "classement, niveau_protection, valid "
                "FROM public.systemes WHERE id = %s",
                (created["id"],),
            )
            self.assertEqual(
                cursor.fetchone(),
                ("SE créé par le web", "SE test", "Conservé", 321, "B", "9 m", True),
            )

    def test_create_digue_validates_and_reloads_its_parent(self):
        created = create_digue(
            self.connection,
            DigueCreate(
                systeme_endiguement_id=self.systeme_endiguement_id,
                libelle="Digue créée par le web",
                designation="Digue test",
                commentaire="Commentaire digue",
            ),
        )
        self.assertEqual(
            created["systeme_endiguement_id"],
            str(self.systeme_endiguement_id),
        )
        self.assertEqual(created["systeme_endiguement_libelle"], "SE web bornage")
        self.assertEqual(created["troncons"], [])
        self.assertEqual(created["designation"], "Digue test")
        self.assertEqual(created["commentaire"], "Commentaire digue")

    def test_create_digue_rejects_unknown_parent_without_partial_insert(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.digues")
            before = cursor.fetchone()[0]
        with self.assertRaisesRegex(HeritageCreationError, "parent"):
            create_digue(
                self.connection,
                DigueCreate(
                    systeme_endiguement_id=uuid.uuid4(),
                    libelle="Ne doit pas exister",
                ),
            )
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.digues")
            self.assertEqual(cursor.fetchone()[0], before)

    def test_create_troncon_transforms_and_preserves_all_vertices(self):
        coordinates = [
            [2.101, 48.801],
            [2.104, 48.804],
            [2.108, 48.802],
            [2.112, 48.807],
        ]
        created = create_troncon(
            self.connection,
            TronconCreate(
                digue_id=self.digue_id,
                libelle="Tronçon web multi-sommets",
                date_debut="2021-04-05",
                date_fin=None,
                designation="Tronçon test",
                commentaire="Commentaire tronçon",
                geometry={"type": "LineString", "coordinates": coordinates},
            ),
        )
        self.assertEqual(created["type"], "Feature")
        self.assertEqual(created["geometry"]["type"], "LineString")
        self.assertEqual(len(created["geometry"]["coordinates"]), 4)
        self.assertEqual(created["properties"]["nombre_sommets"], 4)
        self.assertEqual(created["properties"]["date_debut"], "2021-04-05")
        self.assertIsNone(created["properties"]["date_fin"])
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_SRID(geometry), ST_NPoints(geometry), "
                "ST_AsGeoJSON(ST_Transform(geometry, 4326))::jsonb "
                "FROM public.troncons WHERE id = %s",
                (created["properties"]["id"],),
            )
            srid, vertices, geometry = cursor.fetchone()
        self.assertEqual((srid, vertices), (3950, 4))
        self.assertEqual(geometry, created["geometry"])

    def test_create_troncon_b4_rejects_degenerate_geometry_without_insert(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.troncons")
            before = cursor.fetchone()[0]
        with self.assertRaises(HTTPException) as refused:
            create_troncon(
                self.connection,
                TronconCreate(type_physique_id="RISBERME",
                    digue_id=self.digue_id,
                    libelle="Tronçon dégénéré",
                    geometry={
                        "type": "LineString",
                        "coordinates": [[2.1, 48.8], [2.1, 48.8]],
                    },
                ),
            )
        self.assertEqual(refused.exception.status_code,422)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.troncons")
            self.assertEqual(cursor.fetchone()[0], before)

    def test_update_heritage_business_fields_and_clear_nullable_values(self):
        systeme = update_systeme_endiguement(
            self.connection,
            self.systeme_endiguement_id,
            SystemeEndiguementUpdate(
                libelle="SE modifié",
                designation="Protection urbaine",
                commentaire=None,
                population_protegee=456,
                classement="A",
                niveau_protection="12 m",
                valid=True,
            ),
        )
        self.assertEqual(systeme["population_protegee"], 456)
        self.assertIsNone(systeme["commentaire"])

        digue = update_digue(
            self.connection,
            self.digue_id,
            DigueUpdate(
                libelle="Digue modifiée",
                designation="Rive droite",
                commentaire=None,
                valid=True,
            ),
        )
        self.assertEqual(digue["designation"], "Rive droite")
        self.assertIsNone(digue["commentaire"])

        troncon = update_troncon(
            self.connection,
            self.reperage_troncon_id,
            TronconUpdate(
                libelle="Tronçon modifié",
                designation="Secteur nord",
                commentaire=None,
                date_debut="2022-03-04",
                date_fin=None,
                valid=True,
            ),
        )
        self.assertEqual(troncon["properties"]["date_debut"], "2022-03-04")
        self.assertIsNone(troncon["properties"]["date_fin"])
        self.assertIsNone(troncon["properties"]["commentaire"])

    def test_create_point_desordre_transforms_reloads_and_keeps_reference(self):
        created = create_desordre(
            self.connection,
            DesordreCreate(
                designation="Point créé",
                type_desordre_id=self.type_desordre_id,
                categorie_desordre_id=self.categorie_desordre_id,
                lieu_dit="Les Prés",
                geometry={"type": "Point", "coordinates": [2.25, 50.50]},
            ),
        )
        self.assertEqual(created["geometry"]["type"], "Point")
        self.assertEqual(created["properties"]["designation"], "Point créé")
        self.assertEqual(
            created["properties"]["type_desordre_id"], self.type_desordre_id
        )
        self.assertEqual(
            created["properties"]["categorie_desordre_id"],
            self.categorie_desordre_id,
        )
        self.assertEqual(created["properties"]["lieu_dit"], "Les Prés")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_SRID(geometry), "
                "ST_X(ST_Transform(geometry, 4326)), "
                "ST_Y(ST_Transform(geometry, 4326)) "
                "FROM public.desordres WHERE id = %s",
                (created["properties"]["id"],),
            )
            srid, longitude, latitude = cursor.fetchone()
        self.assertEqual(srid, 3950)
        self.assertAlmostEqual(longitude, 2.25, places=7)
        self.assertAlmostEqual(latitude, 50.50, places=7)

    def test_create_point_desordre_from_xy_uses_writable_view(self):
        created = create_desordre(
            self.connection,
            DesordreCreate(
                designation="Point XY créé",
                x=12.5,
                y=9.25,
                troncon_ids=[self.reperage_troncon_id],
            ),
        )
        self.assertEqual(created["properties"]["x"], 12.5)
        self.assertEqual(created["properties"]["y"], 9.25)
        self.assertEqual(created["properties"]["reperage"]["nombre_troncons"], 1)
        self.assertTrue(created["properties"]["reperage"]["disponible"])

    def test_create_point_from_lonlat_with_optional_contexts(self):
        cases = (
            ([], None),
            ([self.reperage_troncon_id], self.type_desordre_id),
        )
        for troncon_ids, type_id in cases:
            with self.subTest(troncon_ids=troncon_ids, type_id=type_id):
                created = create_desordre(
                    self.connection,
                    DesordreCreate(
                        designation="Point longitude latitude",
                        longitude=2.25,
                        latitude=50.50,
                        troncon_ids=troncon_ids,
                        type_desordre_id=type_id,
                    ),
                )
                self.assertAlmostEqual(
                    created["properties"]["longitude"], 2.25, places=7
                )
                self.assertAlmostEqual(
                    created["properties"]["latitude"], 50.50, places=7
                )
                self.assertEqual(
                    created["properties"]["reperage"]["nombre_troncons"],
                    len(troncon_ids),
                )

    def test_create_multivertex_line_desordre_preserves_every_vertex(self):
        coordinates = [
            [2.101, 50.501], [2.104, 50.504],
            [2.108, 50.502], [2.112, 50.507],
        ]
        created = create_desordre(
            self.connection,
            DesordreCreate(
                designation="Ligne créée",
                geometry={"type": "LineString", "coordinates": coordinates},
            ),
        )
        self.assertEqual(created["geometry"]["type"], "LineString")
        self.assertEqual(created["properties"]["nombre_sommets"], 4)
        self.assertEqual(len(created["geometry"]["coordinates"]), 4)

    def test_create_polygon_desordre_and_disable_longitudinal_reperage(self):
        created = create_desordre(
            self.connection,
            DesordreCreate(
                designation="Polygone créé",
                geometry={
                    "type": "Polygon",
                    "coordinates": [[
                        [2.10, 50.50], [2.12, 50.50],
                        [2.12, 50.52], [2.10, 50.50],
                    ]],
                },
                troncon_ids=[self.reperage_troncon_id, self.second_troncon_id],
            ),
        )
        self.assertEqual(created["geometry"]["type"], "Polygon")
        self.assertEqual(created["properties"]["nombre_troncons"], 2)
        self.assertFalse(created["properties"]["reperage"]["disponible"])
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_SRID(geometry), ST_NPoints(geometry), "
                "(SELECT count(*) FROM public.localisations_desordres "
                "WHERE desordre_id = d.id) "
                "FROM public.desordres AS d WHERE id = %s",
                (created["properties"]["id"],),
            )
            self.assertEqual(cursor.fetchone(), (3950, 4, 0))

    def test_polygon_graphical_update_recalculates_representative_point(self):
        created = create_desordre(
            self.connection,
            DesordreCreate(geometry={
                "type": "Polygon",
                "coordinates": [[[2, 50], [2.02, 50], [2.02, 50.02], [2, 50]]],
            }),
        )
        before = created["properties"]["longitude"]
        feature = update_line_desordre_geometry(
            self.connection,
            uuid.UUID(created["properties"]["id"]),
            DesordreGeometryUpdate(geometry={
                "type": "Polygon",
                "coordinates": [[[3, 49], [3.02, 49], [3.02, 49.02], [3, 49]]],
            }),
        )
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        self.assertNotEqual(feature["properties"]["longitude"], before)

    def test_create_desordre_invalid_reference_and_polygon_roll_back(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.desordres")
            before = cursor.fetchone()[0]
        with self.assertRaisesRegex(DesordreCreationError, "type"):
            create_desordre(
                self.connection,
                DesordreCreate(
                    type_desordre_id="type-absent",
                    geometry={"type": "Point", "coordinates": [2.1, 50.5]},
                ),
            )
        with self.assertRaisesRegex(DesordreCreationError, "tronçon"):
            create_desordre(
                self.connection,
                DesordreCreate(
                    geometry={"type": "Point", "coordinates": [2.1, 50.5]},
                    troncon_ids=[uuid.uuid4()],
                ),
            )
        with self.assertRaisesRegex(DesordreCreationError, "valide, non vide"):
            create_desordre(
                self.connection,
                DesordreCreate(
                    designation="Polygone croisé",
                    geometry={
                        "type": "Polygon",
                        "coordinates": [[
                            [2.10, 50.50], [2.12, 50.52],
                            [2.12, 50.50], [2.10, 50.52], [2.10, 50.50],
                        ]],
                    },
                ),
            )
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.desordres")
            self.assertEqual(cursor.fetchone()[0], before)

    def test_get_real_point_desordre(self):
        feature = fetch_point_desordre(self.connection, self.desordre_id)
        self.assertEqual(feature["type"], "Feature")
        self.assertEqual(feature["geometry"]["type"], "Point")
        self.assertEqual(feature["properties"]["id"], str(self.desordre_id))
        self.assertEqual(feature["properties"]["x"], 10)
        self.assertEqual(feature["properties"]["y"], 7)

    def test_batch_details_equal_unit_readers_for_all_geometries_and_links(self):
        from digues_webapp.queries import fetch_desordres_batch, DesordreNotFoundError
        polygon = create_desordre(self.connection, DesordreCreate(
            designation="Batch polygon", troncon_ids=[self.reperage_troncon_id],
            geometry={"type": "Polygon", "coordinates": [
                [[2, 50], [2.1, 50], [2.1, 50.1], [2, 50.1], [2, 50]]]},
        ))
        ids = [self.line_desordre_id, self.desordre_id,
               uuid.UUID(polygon["properties"]["id"]), self.reperage_desordre_id,
               self.many_troncons_desordre_id]
        self.connection.execute("UPDATE desordres SET designation='Version suivante', valid=false WHERE id=%s", (ids[0],))
        self.connection.execute("UPDATE desordres SET commentaire='Conservé', lieu_dit='Lieu' WHERE id=%s", (ids[0],))
        expected = [fetch_desordre(self.connection, identifier) for identifier in ids]
        self.assertEqual(fetch_desordres_batch(self.connection, ids), expected)
        self.assertEqual(fetch_desordres_batch(self.connection, ids + ids), expected)
        self.assertEqual({item['geometry']['type'] for item in expected}, {'Point', 'LineString', 'Polygon'})
        self.assertGreater(expected[0]['properties']['version'], expected[1]['properties']['version'])
        self.assertTrue(expected[3]['properties']['reperage']['disponible'])
        self.assertEqual(expected[4]['properties']['reperage']['nombre_troncons'], 2)
        with self.assertRaises(DesordreNotFoundError):
            fetch_desordres_batch(self.connection, [*ids, uuid.uuid4()])

    def test_latest_observation_urgency_contract_collection_and_all_details(self):
        urgency_1 = "RefUrgence:1"
        urgency_99 = "RefUrgence:99"
        null_dated = uuid.uuid4()
        line_old, line_latest = uuid.uuid4(), uuid.uuid4()
        polygon_id, polygon_observation = uuid.uuid4(), uuid.uuid4()
        tie_low = uuid.UUID("00000000-0000-0000-0000-000000000101")
        tie_high = uuid.UUID("00000000-0000-0000-0000-000000000102")
        self.connection.execute(
            "INSERT INTO referentiel.urgences(id,libelle,valid) VALUES "
            "(%s,'Faible urgence',true),(%s,'Indéfini',true) "
            "ON CONFLICT(id) DO UPDATE SET libelle=EXCLUDED.libelle,valid=true",
            (urgency_1, urgency_99),
        )
        self.connection.execute(
            "UPDATE public.observations SET urgence_id=%s,"
            "valid=false WHERE id=%s",
            (urgency_1, self.observation_new_id),
        )
        self.connection.execute(
            "INSERT INTO public.observations"
            "(id,desordre_id,date_observation,urgence_id,valid) "
            "VALUES (%s,%s,NULL,%s,true)",
            (null_dated, self.desordre_id, urgency_99),
        )
        self.connection.execute(
            "INSERT INTO public.observations"
            "(id,desordre_id,date_observation,urgence_id,valid) "
            "VALUES (%s,%s,DATE '2024-01-01',%s,true),"
            "(%s,%s,DATE '2025-01-01',NULL,true)",
            (
                line_old, self.line_desordre_id, urgency_99,
                line_latest, self.line_desordre_id,
            ),
        )
        self.connection.execute(
            "INSERT INTO public.desordres(id,designation,geometry,valid) VALUES "
            "(%s,'Polygon urgence',ST_GeomFromText("
            "'POLYGON((0 0,10 0,10 10,0 0))',3950),true)",
            (polygon_id,),
        )
        self.connection.execute(
            "INSERT INTO public.observations"
            "(id,desordre_id,date_observation,urgence_id,valid) "
            "VALUES (%s,%s,DATE '2025-06-01',%s,true),"
            "(%s,%s,DATE '2026-01-01',%s,true),"
            "(%s,%s,DATE '2026-01-01',%s,true)",
            (
                polygon_observation, polygon_id, urgency_99,
                tie_low, self.many_troncons_desordre_id, urgency_1,
                tie_high, self.many_troncons_desordre_id, urgency_99,
            ),
        )

        fields = (
            "dernier_niveau_urgence_id",
            "dernier_niveau_urgence_libelle",
            "derniere_observation_id",
            "derniere_observation_date",
        )
        collection = {
            uuid.UUID(feature["properties"]["id"]): feature["properties"]
            for feature in fetch_desordres(self.connection)["features"]
        }
        expected = {
            self.desordre_id: (
                urgency_1, "Faible urgence", str(self.observation_new_id), "2025-05-02",
            ),
            self.line_desordre_id: (
                None, None, str(line_latest), "2025-01-01",
            ),
            polygon_id: (
                urgency_99, "Indéfini", str(polygon_observation), "2025-06-01",
            ),
            self.reperage_desordre_id: (None, None, None, None),
            self.many_troncons_desordre_id: (
                urgency_1, "Faible urgence", str(tie_low), "2026-01-01",
            ),
        }
        for identifier, values in expected.items():
            with self.subTest(identifier=identifier):
                detail = fetch_desordre(self.connection, identifier)["properties"]
                self.assertEqual(tuple(detail[field] for field in fields), values)
                self.assertEqual(
                    tuple(collection[identifier][field] for field in fields), values,
                )

    def test_real_observations_are_related_and_ordered_by_descending_date(self):
        result = fetch_desordre_observations(self.connection, self.desordre_id)
        self.assertEqual(result["parent"]["id"], str(self.desordre_id))
        self.assertEqual(result["parent"]["resource"], "desordres")
        self.assertEqual(
            [item["id"] for item in result["observations"][:2]],
            [str(self.observation_new_id), str(self.observation_old_id)],
        )
        self.assertEqual(result["observations"][0]["photo_count"], 2)

    def test_real_observation_returns_only_its_photo_children(self):
        result = fetch_observation(self.connection, self.observation_new_id)
        self.assertEqual(result["parent"]["id"], str(self.desordre_id))
        self.assertNotIn("desordre_id", result)
        self.assertEqual(
            [photo["observation_id"] for photo in result["photos"]],
            [str(self.observation_new_id), str(self.observation_new_id)],
        )
        self.assertEqual(
            [photo["id"] for photo in result["photos"]],
            [str(self.photo_new_id), str(self.photo_old_id)],
        )
        self.assertEqual(
            [photo["nom_fichier"] for photo in result["photos"]],
            [None, None],
        )
        # These R6 rows only reference unavailable opaque storage keys. A name
        # cannot be reconstructed from a former CouchDB path or from the UUID.
        self.assertTrue(all(not photo["content_available"] for photo in result["photos"]))
        self.assertTrue(all("chemin_source" not in photo for photo in result["photos"]))

    def test_real_observation_photo_name_comes_from_its_owned_media_manifest(self):
        import hashlib
        import io
        from tempfile import TemporaryDirectory
        from PIL import Image
        from digues_webapp.media_storage import FilesystemMediaStorage, validate_image

        stream = io.BytesIO()
        Image.new("RGB", (12, 8), "blue").save(stream, format="PNG")
        data = stream.getvalue()
        variant = validate_image(io.BytesIO(data), "terrain.png", "image/png",
                                 hashlib.sha256(data).hexdigest())
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            "MEDIA_BACKEND": "filesystem", "MEDIA_ROOT": folder,
        }):
            storage = FilesystemMediaStorage(folder)
            manifest = storage.prepare(io.BytesIO(data), self.photo_new_id,
                                       self.observation_new_id, "terrain.png", variant)
            self.connection.execute("UPDATE public.photos SET storage_key=%s WHERE id=%s",
                                    (manifest["storage_key"], self.photo_new_id))
            photos = {photo["id"]: photo for photo in
                      fetch_observation(self.connection, self.observation_new_id)["photos"]}
            self.assertEqual(photos[str(self.photo_new_id)]["nom_fichier"], "terrain.png")
            self.assertTrue(photos[str(self.photo_new_id)]["content_available"])
            self.assertIsNone(photos[str(self.photo_old_id)]["nom_fichier"])
            self.assertFalse(photos[str(self.photo_old_id)]["content_available"])
            # An unrelated owner's valid bundle must not leak a name/content.
            self.connection.execute("UPDATE public.photos SET storage_key=NULL WHERE id=%s",
                                    (self.photo_new_id,))
            self.connection.execute("UPDATE public.photos SET storage_key=%s WHERE id=%s",
                                    (manifest["storage_key"], self.photo_old_id))
            photos = {photo["id"]: photo for photo in
                      fetch_observation(self.connection, self.observation_new_id)["photos"]}
            self.assertIsNone(photos[str(self.photo_old_id)]["nom_fichier"])
            self.assertFalse(photos[str(self.photo_old_id)]["content_available"])

    def test_reperage_availability_follows_zero_one_many_rule(self):
        zero = fetch_point_desordre(self.connection, self.desordre_id)
        one = fetch_point_desordre(self.connection, self.reperage_desordre_id)
        many = fetch_point_desordre(
            self.connection,
            self.many_troncons_desordre_id,
        )
        self.assertEqual(zero["properties"]["reperage"]["nombre_troncons"], 0)
        self.assertFalse(zero["properties"]["reperage"]["disponible"])
        self.assertEqual(one["properties"]["reperage"]["nombre_troncons"], 1)
        self.assertTrue(one["properties"]["reperage"]["disponible"])
        self.assertEqual(
            {borne["id"] for borne in one["properties"]["reperage"]["bornes"]},
            {str(self.borne_debut_id), str(self.borne_fin_id)},
        )
        self.assertEqual(many["properties"]["reperage"]["nombre_troncons"], 2)
        self.assertFalse(many["properties"]["reperage"]["disponible"])

    def test_put_reperage_rebuilds_geometry_coordinates_and_reloads_state(self):
        feature = update_point_reperage(
            self.connection,
            self.reperage_desordre_id,
            PointReperageUpdate(
                borne_debut_id=self.borne_debut_id,
                distance_debut_m=25,
                position_debut_relative="APRES_BORNE",
            ),
        )
        properties = feature["properties"]
        reperage = properties["reperage"]
        self.assertEqual(reperage["borne_debut_id"], str(self.borne_debut_id))
        self.assertAlmostEqual(reperage["distance_debut_m"], 25)
        self.assertEqual(reperage["position_debut_relative"], "APRES_BORNE")
        self.assertAlmostEqual(properties["x"], 25)
        self.assertAlmostEqual(properties["y"], 0)
        self.assertIsNotNone(properties["longitude"])
        self.assertIsNotNone(properties["latitude"])
        self.assertEqual(feature["geometry"]["type"], "Point")

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_X(d.geometry), ST_Y(d.geometry), "
                "ST_X(ST_Transform(d.geometry, 4326)), "
                "ST_Y(ST_Transform(d.geometry, 4326)), "
                "l.borne_debut_id, l.distance_debut_m, "
                "l.position_debut_relative, l.pr_debut "
                "FROM public.desordres AS d "
                "JOIN public.localisations_desordres AS l "
                "ON l.desordre_id = d.id WHERE d.id = %s",
                (self.reperage_desordre_id,),
            )
            stored = cursor.fetchone()
        self.assertEqual(stored[0:2], (25, 0))
        self.assertAlmostEqual(stored[2], properties["longitude"], places=8)
        self.assertAlmostEqual(stored[3], properties["latitude"], places=8)
        self.assertEqual(
            stored[4:7],
            (self.borne_debut_id, 25, "APRES_BORNE"),
        )
        self.assertEqual(stored[7], reperage["pr_debut"])

    def test_put_unchanged_point_reperage_still_repositions_geometry(self):
        before = fetch_point_desordre(
            self.connection,
            self.reperage_desordre_id,
        )["properties"]["reperage"]
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('digues_app.reperage_guard', 'REPERAGE', true)")
            cursor.execute(
                "UPDATE public.desordres SET geometry = "
                "ST_SetSRID(ST_Point(70, 30), 3950) WHERE id = %s",
                (self.reperage_desordre_id,),
            )
            cursor.execute("SELECT set_config('digues_app.reperage_guard', '', true)")
        feature = update_point_reperage(
            self.connection,
            self.reperage_desordre_id,
            PointReperageUpdate(
                borne_debut_id=before["borne_debut_id"],
                distance_debut_m=before["distance_debut_m"],
                position_debut_relative=before["position_debut_relative"],
            ),
        )
        self.assertAlmostEqual(feature["properties"]["x"], 10)
        self.assertAlmostEqual(feature["properties"]["y"], 0)

    def test_put_reperage_rejects_zero_or_many_associated_troncons(self):
        update = PointReperageUpdate(
            borne_debut_id=self.borne_debut_id,
            distance_debut_m=10,
            position_debut_relative="APRES_BORNE",
        )
        for desordre_id in (
            self.desordre_id,
            self.many_troncons_desordre_id,
        ):
            with self.assertRaises(PointReperageUnavailableError):
                update_point_reperage(self.connection, desordre_id, update)

    def test_put_reperage_rejects_borne_from_another_system(self):
        with self.assertRaises(PointReperageUpdateError):
            update_point_reperage(
                self.connection,
                self.reperage_desordre_id,
                PointReperageUpdate(
                    borne_debut_id=self.incompatible_borne_id,
                    distance_debut_m=10,
                    position_debut_relative="APRES_BORNE",
                ),
            )

    def test_get_real_linestring_desordre_keeps_multivertex_geometry(self):
        feature = fetch_desordre(self.connection, self.line_desordre_id)
        self.assertEqual(feature["geometry"]["type"], "LineString")
        self.assertEqual(len(feature["geometry"]["coordinates"]), 3)
        self.assertEqual(feature["properties"]["nombre_sommets"], 3)
        self.assertTrue(feature["properties"]["reperage"]["disponible"])
        self.assertEqual(
            fetch_line_desordre(self.connection, self.line_desordre_id),
            feature,
        )

    def test_put_linestring_transforms_preserves_vertices_and_reloads_db(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_AsGeoJSON(ST_Transform(ST_GeomFromText("
                "'LINESTRING(7 1,25 9,55 12,90 2)', 3950), 4326))::jsonb"
            )
            geometry = cursor.fetchone()[0]
        feature = update_line_desordre_geometry(
            self.connection,
            self.line_desordre_id,
            DesordreGeometryUpdate(geometry=geometry),
        )
        self.assertEqual(feature["geometry"]["type"], "LineString")
        self.assertEqual(len(feature["geometry"]["coordinates"]), 4)
        self.assertEqual(feature["properties"]["nombre_sommets"], 4)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_AsGeoJSON(ST_Transform(geometry, 4326))::jsonb, "
                "ST_NPoints(geometry), ST_SRID(geometry) "
                "FROM public.desordres WHERE id = %s",
                (self.line_desordre_id,),
            )
            stored_geometry, stored_vertices, stored_srid = cursor.fetchone()
        self.assertEqual(stored_geometry, feature["geometry"])
        self.assertEqual(stored_vertices, 4)
        self.assertEqual(stored_srid, 3950)

    def test_put_linestring_endpoints_preserves_intermediate_vertices(self):
        feature = update_line_desordre_endpoints(
            self.connection,
            self.line_desordre_id,
            LineEndpoints(crs="EPSG:3950", debut=(-5, 4), fin=(95, 6)),
        )
        self.assertEqual(feature["properties"]["nombre_sommets"], 3)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_X(ST_PointN(geometry, 1)), ST_Y(ST_PointN(geometry, 1)), "
                "ST_X(ST_PointN(geometry, 2)), ST_Y(ST_PointN(geometry, 2)), "
                "ST_X(ST_PointN(geometry, 3)), ST_Y(ST_PointN(geometry, 3)) "
                "FROM public.desordres WHERE id = %s",
                (self.line_desordre_id,),
            )
            self.assertEqual(cursor.fetchone(), (-5, 4, 30, 8, 95, 6))

    def test_put_linestring_bornage_rebuilds_from_troncon(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.troncons SET geometry = "
                "ST_GeomFromText('LINESTRING(0 0,20 0,40 10,60 10,80 0,100 0)', 3950) "
                "WHERE id = %s",
                (self.reperage_troncon_id,),
            )
        feature = update_point_reperage(
            self.connection,
            self.line_desordre_id,
            PointReperageUpdate(
                borne_debut_id=self.borne_debut_id,
                distance_debut_m=10,
                position_debut_relative="APRES_BORNE",
                borne_fin_id=self.borne_fin_id,
                distance_fin_m=15,
                position_fin_relative="AVANT_BORNE",
            ),
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_Equals(d.geometry, ST_LineSubstring(t.geometry, "
                "10 / ST_Length(t.geometry), "
                "(ST_Length(t.geometry) - 15) / ST_Length(t.geometry))), "
                "ST_NPoints(d.geometry), "
                "ST_DWithin(d.geometry, ST_SetSRID(ST_Point(30, 8), 3950), 0.01) "
                "FROM public.desordres AS d JOIN public.troncons AS t ON t.id = %s "
                "WHERE d.id = %s",
                (self.reperage_troncon_id, self.line_desordre_id),
            )
            equals_substring, vertex_count, keeps_old_middle = cursor.fetchone()
        self.assertTrue(equals_substring)
        self.assertGreater(vertex_count, 2)
        self.assertFalse(keeps_old_middle)

    def test_put_unchanged_line_reperage_still_replaces_free_geometry(self):
        before = fetch_line_desordre(
            self.connection,
            self.line_desordre_id,
        )["properties"]["reperage"]
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('digues_app.reperage_guard', 'REPERAGE', true)")
            cursor.execute(
                "UPDATE public.desordres SET geometry = ST_GeomFromText("
                "'LINESTRING(5 0,30 25,60 -20,80 0)', 3950) WHERE id = %s",
                (self.line_desordre_id,),
            )
            cursor.execute("SELECT set_config('digues_app.reperage_guard', '', true)")
        feature = update_point_reperage(
            self.connection,
            self.line_desordre_id,
            PointReperageUpdate(
                borne_debut_id=before["borne_debut_id"],
                distance_debut_m=before["distance_debut_m"],
                position_debut_relative=before["position_debut_relative"],
                borne_fin_id=before["borne_fin_id"],
                distance_fin_m=before["distance_fin_m"],
                position_fin_relative=before["position_fin_relative"],
            ),
        )
        self.assertEqual(feature["geometry"]["type"], "LineString")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_Equals(d.geometry, ST_LineSubstring(t.geometry, 0.05, 0.8)), "
                "ST_DWithin(d.geometry, ST_SetSRID(ST_Point(30, 25), 3950), 0.01) "
                "FROM public.desordres AS d JOIN public.troncons AS t ON t.id = %s "
                "WHERE d.id = %s",
                (self.reperage_troncon_id, self.line_desordre_id),
            )
            equals_substring, keeps_free_middle = cursor.fetchone()
        self.assertTrue(equals_substring)
        self.assertFalse(keeps_free_middle)

    def test_point_link_update_rejects_multiple_troncons_transactionally(self):
        with self.assertRaisesRegex(PointDesordreUpdateError, "au plus un"):
            update_point_desordre(
                self.connection,
                self.desordre_id,
                PointDesordreUpdate(troncon_ids=[
                    self.reperage_troncon_id, self.second_troncon_id,
                ]),
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.desordres__troncons "
                "WHERE desordre_id = %s", (self.desordre_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_link_transitions_keep_fk_and_reperage_coherent(self):
        troncon_a = self.reperage_troncon_id
        troncon_b = self.second_troncon_id

        def create_line(initial_links):
            feature = create_desordre(
                self.connection,
                DesordreCreate(
                    geometry={
                        "type": "LineString",
                        "coordinates": [[2.10, 50.50], [2.11, 50.51], [2.12, 50.50]],
                    },
                    troncon_ids=initial_links,
                ),
            )
            return uuid.UUID(feature["properties"]["id"])

        transitions = (
            ([], [troncon_a]),
            ([troncon_a], []),
            ([troncon_a], [troncon_b]),
            ([troncon_a], [troncon_a, troncon_b]),
            ([troncon_a, troncon_b], [troncon_b]),
            ([troncon_a, troncon_b], []),
        )
        for initial_links, final_links in transitions:
            with self.subTest(initial=initial_links, final=final_links):
                desordre_id = create_line(initial_links)
                feature = update_point_desordre(
                    self.connection,
                    desordre_id,
                    PointDesordreUpdate(troncon_ids=final_links),
                )
                self.assertEqual(
                    set(feature["properties"]["troncon_ids"]),
                    {str(item) for item in final_links},
                )
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT count(*) FROM public.localisations_desordres "
                        "WHERE desordre_id = %s",
                        (desordre_id,),
                    )
                    localisation_count = cursor.fetchone()[0]
                self.assertEqual(localisation_count, 1 if len(final_links) == 1 else 0)

        point = create_desordre(
            self.connection,
            DesordreCreate(longitude=2.25, latitude=50.5),
        )
        point_id = uuid.UUID(point["properties"]["id"])
        for final_links in ([troncon_a], [], [troncon_b]):
            feature = update_point_desordre(
                self.connection,
                point_id,
                PointDesordreUpdate(troncon_ids=final_links),
            )
            self.assertEqual(
                set(feature["properties"]["troncon_ids"]),
                {str(item) for item in final_links},
            )

    def test_type_desordre_is_editable_for_all_geometries_and_nullable(self):
        for desordre_id in (self.desordre_id, self.line_desordre_id):
            feature = update_point_desordre(
                self.connection, desordre_id,
                PointDesordreUpdate(type_desordre_id=self.type_desordre_id),
            )
            self.assertEqual(
                feature["properties"]["type_desordre_id"], self.type_desordre_id
            )
            feature = update_point_desordre(
                self.connection, desordre_id,
                PointDesordreUpdate(type_desordre_id=None),
            )
            self.assertIsNone(feature["properties"]["type_desordre_id"])
        polygon = create_desordre(
            self.connection,
            DesordreCreate(geometry={
                "type": "Polygon",
                "coordinates": [[[2, 50], [2.1, 50], [2.1, 50.1], [2, 50]]],
            }),
        )
        polygon = update_point_desordre(
            self.connection, uuid.UUID(polygon["properties"]["id"]),
            PointDesordreUpdate(type_desordre_id=self.type_desordre_id),
        )
        self.assertEqual(
            polygon["properties"]["type_desordre_id"], self.type_desordre_id
        )
        with self.assertRaisesRegex(PointDesordreUpdateError, "type"):
            update_point_desordre(
                self.connection, self.desordre_id,
                PointDesordreUpdate(type_desordre_id="type-inactif-ou-absent"),
            )

    def test_put_linestring_rejects_degenerate_geometry_before_sql(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT ST_AsGeoJSON(ST_Transform("
                "ST_SetSRID(ST_Point(10, 5), 3950), 4326))::jsonb"
            )
            point = cursor.fetchone()[0]["coordinates"]
            cursor.execute("SELECT ST_AsEWKB(geometry), version FROM public.desordres WHERE id=%s",
                           (self.line_desordre_id,))
            before = cursor.fetchone()
        with self.assertRaises(ValidationError):
            DesordreGeometryUpdate(
                geometry={"type": "LineString", "coordinates": [point, point]}
            )
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT ST_AsEWKB(geometry), version FROM public.desordres WHERE id=%s",
                           (self.line_desordre_id,))
            self.assertEqual(cursor.fetchone(), before)

    def test_put_xy_returns_postgresql_recalculated_state(self):
        feature = update_point_desordre(
            self.connection,
            self.desordre_id,
            PointDesordreUpdate(x=12.5, y=9.25),
        )
        properties = feature["properties"]
        self.assertAlmostEqual(properties["x"], 12.5)
        self.assertAlmostEqual(properties["y"], 9.25)
        self.assertIsNotNone(properties["longitude"])
        self.assertIsNotNone(properties["latitude"])
        self.assertEqual(feature["geometry"]["type"], "Point")

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT coord_x, coord_y, longitude_4326, latitude_4326 "
                "FROM public.desordres_points_saisie WHERE id = %s",
                (self.desordre_id,),
            )
            stored = cursor.fetchone()
        self.assertEqual(
            stored,
            (
                properties["x"],
                properties["y"],
                properties["longitude"],
                properties["latitude"],
            ),
        )

    def test_put_point_preserves_category_and_nullable_lieu_dit(self):
        feature = update_point_desordre(
            self.connection,
            self.desordre_id,
            PointDesordreUpdate(
                type_desordre_id=self.type_desordre_id,
                categorie_desordre_id=self.categorie_desordre_id,
                lieu_dit="Les Prés",
            ),
        )
        self.assertEqual(
            feature["properties"]["categorie_desordre_id"],
            self.categorie_desordre_id,
        )
        self.assertEqual(feature["properties"]["lieu_dit"], "Les Prés")
        cleared = update_point_desordre(
            self.connection,
            self.desordre_id,
            PointDesordreUpdate(lieu_dit=None),
        )
        self.assertIsNone(cleared["properties"]["lieu_dit"])

    def test_put_point_dates_survive_the_server_reread(self):
        updated = update_point_desordre(
            self.connection,
            self.desordre_id,
            PointDesordreUpdate(date_debut="2025-01-02", date_fin="2025-03-04"),
        )
        self.assertEqual(updated["properties"]["date_debut"], "2025-01-02")
        self.assertEqual(updated["properties"]["date_fin"], "2025-03-04")

        reread = fetch_point_desordre(self.connection, self.desordre_id)
        self.assertEqual(reread["properties"]["date_debut"], "2025-01-02")
        self.assertEqual(reread["properties"]["date_fin"], "2025-03-04")

    def test_put_lonlat_returns_xy_recalculated_by_postgresql(self):
        feature = update_point_desordre(
            self.connection,
            self.desordre_id,
            PointDesordreUpdate(
                longitude=2.25,
                latitude=48.75,
            ),
        )
        properties = feature["properties"]
        self.assertAlmostEqual(properties["longitude"], 2.25, places=8)
        self.assertAlmostEqual(properties["latitude"], 48.75, places=8)
        self.assertNotAlmostEqual(properties["x"], 10)
        self.assertNotAlmostEqual(properties["y"], 7)


if __name__ == "__main__":
    unittest.main()
