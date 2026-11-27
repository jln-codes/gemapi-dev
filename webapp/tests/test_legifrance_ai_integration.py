import json
import unittest
from unittest.mock import patch

from digues_webapp.ai import (
    AiChatResult,
    AiExternalSource,
    EXTERNAL_SOURCES_TOOL_NAME,
    _run_external_tool,
)
from digues_webapp.app import app
from digues_webapp.models import AiChatRequest


LEGIFRANCE_RESULT = {
    "results": [{
        "title": "Code de l'environnement — Article R. 214-1",
        "organization": "Légifrance",
        "url": "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000012345678",
        "source_id": "legifrance",
        "category": "LEGAL_PRIMARY",
        "document_date": "2024-02-03",
        "retrieved_at": "2026-09-07T12:00:00+00:00",
        "excerpt": "Disposition juridique vérifiée.",
        "metadata": {
            "legifrance_id": "LEGIARTI000012345678",
            "legal_status": "VIGUEUR",
        },
    }]
}


class LegifranceAiIntegrationTest(unittest.TestCase):
    def test_legifrance_result_is_transmitted_to_mistral_tool_result(self):
        tool_call = {
            "id": "external-legifrance",
            "type": "function",
            "function": {
                "name": EXTERNAL_SOURCES_TOOL_NAME,
                "arguments": json.dumps({"query": "article R. 214-1"}),
            },
        }
        with patch(
            "digues_webapp.ai.search_external_sources", return_value=LEGIFRANCE_RESULT
        ) as search:
            name, result, sources = _run_external_tool(tool_call)
        search.assert_called_once_with("article R. 214-1")
        self.assertEqual(name, EXTERNAL_SOURCES_TOOL_NAME)
        self.assertEqual(result, LEGIFRANCE_RESULT)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0][1].source_id, "legifrance")

    def test_chat_api_exposes_only_common_legifrance_provenance(self):
        endpoint = next(
            route.endpoint for route in app.routes if route.path == "/api/ai/chat"
        )
        source = AiExternalSource(
            title=LEGIFRANCE_RESULT["results"][0]["title"],
            organization="Légifrance",
            url=LEGIFRANCE_RESULT["results"][0]["url"],
            source_id="legifrance",
            category="LEGAL_PRIMARY",
            document_date="2024-02-03",
        )
        with (
            patch(
                "digues_webapp.app.chat_with_mistral",
                return_value=AiChatResult(
                    answer="Réponse juridique.",
                    executed_queries=("SELECT 1",),
                    external_sources=(source,),
                ),
            ),
            patch("digues_webapp.app.get_ai_schema_context", return_value="schema"),
        ):
            response = endpoint(AiChatRequest(message="Quelle obligation ?"))
        self.assertEqual(response["external_sources"], [{
            "title": source.title,
            "organization": "Légifrance",
            "url": source.url,
            "source_id": "legifrance",
            "category": "LEGAL_PRIMARY",
            "document_date": "2024-02-03",
        }])
        serialized = json.dumps(response)
        self.assertNotIn("legifrance_id", serialized)
        self.assertNotIn("access_token", serialized)
        self.assertEqual(response["executed_queries"], [{"sql": "SELECT 1"}])


if __name__ == "__main__":
    unittest.main()
