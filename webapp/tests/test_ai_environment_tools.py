import json
import os
import unittest
from unittest.mock import Mock, patch

from digues_webapp.ai import (
    HYDROMETRIC_DATA_TOOL,
    HYDROMETRIC_DATA_TOOL_NAME,
    HYDROMETRIC_SITES_TOOL,
    HYDROMETRIC_SITES_TOOL_NAME,
    PRECIPITATION_TOOL,
    PRECIPITATION_TOOL_NAME,
    chat_with_mistral,
)
from digues_webapp.prompts import SYSTEM_PROMPT


def mistral_response(message):
    response = Mock(status_code=200, ok=True)
    response.json.return_value = {"choices": [{"message": message}]}
    return response


def tool_call(identifier, name, arguments):
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class AiEnvironmentToolsTest(unittest.TestCase):
    def chat_context(self, responses):
        return (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch("digues_webapp.ai.requests.post", side_effect=responses),
        )

    def test_tool_schemas_are_read_only_and_accept_no_url_or_secret(self):
        tools = (
            PRECIPITATION_TOOL,
            HYDROMETRIC_SITES_TOOL,
            HYDROMETRIC_DATA_TOOL,
        )
        for tool in tools:
            parameters = tool["function"]["parameters"]
            self.assertFalse(parameters["additionalProperties"])
            self.assertNotIn("url", parameters["properties"])
            serialized = json.dumps(tool)
            self.assertNotIn("api_key", serialized.casefold())
            self.assertNotIn("secret", serialized.casefold())
        self.assertEqual(
            HYDROMETRIC_DATA_TOOL["function"]["parameters"]["required"],
            ["station", "kind"],
        )
        self.assertEqual(
            HYDROMETRIC_SITES_TOOL["function"]["name"],
            HYDROMETRIC_SITES_TOOL_NAME,
        )
        hydrometry_prompt = SYSTEM_PROMPT.split(
            "## Données hydrométriques", 1
        )[1].split("## Documentation du projet", 1)[0]
        self.assertIn("Distingue toujours :", hydrometry_prompt)
        self.assertIn("- observation ;", hydrometry_prompt)
        self.assertIn("- prévision ;", hydrometry_prompt)
        self.assertIn("indépendantes de la carte", hydrometry_prompt)
        self.assertIn("de son zoom", hydrometry_prompt)

    def test_hydrometric_result_is_injected_into_mistral(self):
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call(
                "hydro-1",
                HYDROMETRIC_DATA_TOOL_NAME,
                {
                    "station": "Béthune",
                    "kind": "observation",
                    "variable": "H",
                    "start_time": "2026-09-05T00:00:00Z",
                },
            )],
        })
        final = mistral_response({
            "role": "assistant",
            "content": "La hauteur a été consultée.",
        })
        tool_result = {
            "ok": True,
            "source": "Hub'Eau Hydrométrie",
            "station": {"name": "Béthune"},
            "series": [{
                "kind": "observation",
                "variable": "height",
                "unit": "m",
                "points": [{
                    "target_time": "2026-09-07T06:00:00Z",
                    "source_time": "2026-09-07T05:50:00Z",
                    "value": 1.42,
                }],
            }],
        }
        environment, post = self.chat_context([first, final])
        with (
            environment,
            post as mocked_post,
            patch(
                "digues_webapp.ai.get_hydrometric_data",
                return_value=tool_result,
            ) as hydrometry,
        ):
            answer = chat_with_mistral(
                [],
                "schema",
                current_message="Quelle a été l’évolution de la Lawe à Béthune ?",
            )

        hydrometry.assert_called_once_with(
            station="Béthune",
            kind="observation",
            variable="H",
            start_time="2026-09-05T00:00:00Z",
        )
        injected = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertEqual(injected["name"], HYDROMETRIC_DATA_TOOL_NAME)
        self.assertEqual(json.loads(injected["content"]), tool_result)
        self.assertEqual(answer.answer, "La hauteur a été consultée.")
        self.assertEqual(answer.executed_queries, ())
        self.assertEqual(answer.external_sources, ())

    def test_station_and_precipitation_tools_can_be_called_together(self):
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                tool_call(
                    "stations-1",
                    HYDROMETRIC_SITES_TOOL_NAME,
                    {"query": "Lawe"},
                ),
                tool_call("rain-1", PRECIPITATION_TOOL_NAME, {}),
            ],
        })
        final = mistral_response({"role": "assistant", "content": "Données lues."})
        station_result = {"ok": True, "sites": [{"name": "Béthune"}]}
        rain_result = {
            "ok": True,
            "source": "Météo-France",
            "forecast_steps": [],
        }
        environment, post = self.chat_context([first, final])
        with (
            environment,
            post as mocked_post,
            patch(
                "digues_webapp.ai.get_hydrometric_sites",
                return_value=station_result,
            ) as stations,
            patch(
                "digues_webapp.ai.get_precipitation_forecast",
                return_value=rain_result,
            ) as rain,
        ):
            chat_with_mistral(
                [],
                "schema",
                current_message="Hydrologie et pluie du territoire ?",
            )

        stations.assert_called_once_with(query="Lawe")
        rain.assert_called_once_with()
        injected = [
            json.loads(message["content"])
            for message in mocked_post.call_args_list[1].kwargs["json"]["messages"]
            if message["role"] == "tool"
        ]
        self.assertEqual(injected, [station_result, rain_result])

    def test_tool_failure_is_explicit_without_leaking_internal_secret(self):
        secret = "MISTRAL_API_KEY=do-not-leak"
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call(
                "rain-error",
                PRECIPITATION_TOOL_NAME,
                {"valid_time": "2026-09-08T00:00:00Z"},
            )],
        })
        final = mistral_response({"role": "assistant", "content": "Indisponible."})
        environment, post = self.chat_context([first, final])
        with (
            environment,
            post as mocked_post,
            patch(
                "digues_webapp.ai.get_precipitation_forecast",
                side_effect=RuntimeError(secret),
            ),
        ):
            chat_with_mistral(
                [],
                "schema",
                current_message="Quelle pluie demain ?",
            )

        tool_message = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        payload = json.loads(tool_message["content"])
        self.assertFalse(payload["ok"])
        self.assertIn("indisponibles", payload["error"])
        self.assertNotIn(secret, tool_message["content"])

    def test_url_argument_is_rejected_before_environment_service_call(self):
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call(
                "rain-url",
                PRECIPITATION_TOOL_NAME,
                {"url": "https://example.invalid/"},
            )],
        })
        final = mistral_response({"role": "assistant", "content": "Refusé."})
        environment, post = self.chat_context([first, final])
        with (
            environment,
            post as mocked_post,
            patch("digues_webapp.ai.get_precipitation_forecast") as rain,
        ):
            chat_with_mistral(
                [],
                "schema",
                current_message="Charge cette URL.",
            )

        rain.assert_not_called()
        tool_message = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertFalse(json.loads(tool_message["content"])["ok"])


if __name__ == "__main__":
    unittest.main()
