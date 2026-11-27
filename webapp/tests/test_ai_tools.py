import json
import os
import unittest
from unittest.mock import Mock, call, patch

from digues_webapp.ai import (
    MISTRAL_MAX_TOOL_CALLS,
    EXTERNAL_SOURCES_TOOL,
    EXTERNAL_SOURCES_TOOL_NAME,
    PROJECT_KNOWLEDGE_TOOL,
    PROJECT_KNOWLEDGE_TOOL_NAME,
    BUSINESS_DATABASE_TOOL,
    ASSISTANT_TOOLS,
    BUSINESS_DATABASE_TOOL_NAME,
    AiServiceError,
    chat_with_mistral,
)
from digues_webapp.readonly_sql import (
    ReadonlySqlValidationError,
)


def mistral_response(message):
    response = Mock(status_code=200, ok=True)
    response.json.return_value = {"choices": [{"message": message}]}
    return response


def tool_call(tool_call_id, arguments, *, name=BUSINESS_DATABASE_TOOL_NAME):
    return {
        "id": tool_call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class MistralSqlToolTest(unittest.TestCase):
    def chat(self, responses):
        return (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch("digues_webapp.ai.requests.post", side_effect=responses),
        )

    def test_direct_answer_does_not_execute_sql(self):
        response = mistral_response({
            "role": "assistant",
            "content": "Réponse générale sans consultation.",
        })
        environment, post = self.chat([response])
        with (
            environment,
            post as mocked_post,
            patch("digues_webapp.ai.execute_readonly_query") as execute,
        ):
            answer = chat_with_mistral(
                [],
                "<schema></schema>",
                current_message="Qu’est-ce qu’une digue ?",
            )

        self.assertEqual(answer.answer, "Réponse générale sans consultation.")
        self.assertEqual(answer.executed_queries, ())
        execute.assert_not_called()
        request = mocked_post.call_args.kwargs["json"]
        self.assertEqual(request["tools"], ASSISTANT_TOOLS)
        self.assertEqual(request["tool_choice"], "auto")
        self.assertTrue(request["parallel_tool_calls"])
        system_prompt = request["messages"][0]["content"]
        self.assertIn("query_business_database", system_prompt)
        database_prompt = system_prompt.split("## Schéma PostgreSQL", 1)[1].split(
            "## Localisation des objets métier", 1
        )[0]
        self.assertIn("Tu ne dois jamais inventer :", database_prompt)
        self.assertIn("- une valeur ;", database_prompt)
        self.assertIn("Ne prétends jamais :", database_prompt)
        self.assertIn(
            "avoir exécuté une requête SQL qui n’a pas réellement été exécutée",
            database_prompt,
        )
        parameters = BUSINESS_DATABASE_TOOL["function"]["parameters"]
        self.assertEqual(parameters["required"], ["sql"])
        self.assertFalse(parameters["additionalProperties"])
        self.assertEqual(PROJECT_KNOWLEDGE_TOOL["function"]["parameters"]["required"], ["query"])
        self.assertEqual(EXTERNAL_SOURCES_TOOL["function"]["parameters"]["required"], ["query"])
        self.assertEqual(
            set(EXTERNAL_SOURCES_TOOL["function"]["parameters"]["properties"]),
            {"query"},
        )
        self.assertFalse(EXTERNAL_SOURCES_TOOL["function"]["parameters"]["additionalProperties"])
        self.assertIn(
            "base PostgreSQL/PostGIS de l’application expérimentale",
            BUSINESS_DATABASE_TOOL["function"]["description"],
        )
        self.assertNotIn(
            "base PostgreSQL/PostGIS SIRS",
            BUSINESS_DATABASE_TOOL["function"]["description"],
        )
        self.assertIn(
            "documentation locale versionnée du projet expérimental",
            PROJECT_KNOWLEDGE_TOOL["function"]["description"],
        )
        self.assertNotIn(
            "projet SIRS",
            PROJECT_KNOWLEDGE_TOOL["function"]["description"],
        )

    def test_document_search_result_is_returned_with_source_and_matching_id(self):
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call(
                "knowledge-1",
                '{"query":"architecture webapp"}',
                name=PROJECT_KNOWLEDGE_TOOL_NAME,
            )],
        })
        final = mistral_response({"role": "assistant", "content": "Architecture trouvée."})
        result = {"results": [{
            "document_id": "doc-1",
            "chunk_id": "chunk-1",
            "title": "SIRS",
            "path": "README.md",
            "heading": "Architecture",
            "content": "Le backend utilise FastAPI.",
            "rank": 0.8,
        }]}
        environment, post = self.chat([first, final])
        with (
            environment,
            post as mocked_post,
            patch("digues_webapp.ai.search_project_knowledge", return_value=result) as search,
        ):
            answer = chat_with_mistral([], "schema", current_message="Architecture ?")

        search.assert_called_once_with("architecture webapp")
        self.assertEqual(answer.executed_queries, ())
        self.assertEqual(len(answer.consulted_sources), 1)
        self.assertEqual(answer.consulted_sources[0].path, "README.md")
        tool = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertEqual(tool["name"], PROJECT_KNOWLEDGE_TOOL_NAME)
        self.assertEqual(tool["tool_call_id"], "knowledge-1")
        self.assertEqual(json.loads(tool["content"]), result)

    def test_empty_document_search_does_not_create_false_source(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call("empty", '{"query":"absent"}', name=PROJECT_KNOWLEDGE_TOOL_NAME)],
            }),
            mistral_response({"role": "assistant", "content": "Aucun passage."}),
        ]
        environment, post = self.chat(responses)
        with environment, post, patch(
            "digues_webapp.ai.search_project_knowledge", return_value={"results": []}
        ):
            answer = chat_with_mistral([], "schema", current_message="Question")
        self.assertEqual(answer.consulted_sources, ())

    def test_repeated_document_chunk_is_exposed_once(self):
        calls = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call(
                    f"doc-{index}", '{"query":"architecture"}',
                    name=PROJECT_KNOWLEDGE_TOOL_NAME,
                )],
            })
            for index in range(2)
        ]
        calls.append(mistral_response({"role": "assistant", "content": "Réponse."}))
        result = {"results": [{
            "document_id": "doc", "chunk_id": "same-chunk", "title": "README",
            "path": "README.md", "heading": "Architecture", "content": "Texte", "rank": 1.0,
        }]}
        environment, post = self.chat(calls)
        with environment, post, patch(
            "digues_webapp.ai.search_project_knowledge", return_value=result
        ):
            answer = chat_with_mistral([], "schema", current_message="Architecture")
        self.assertEqual(len(answer.consulted_sources), 1)

    def test_document_then_sql_tools_preserve_both_call_ids(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call("doc-call", '{"query":"systèmes"}', name=PROJECT_KNOWLEDGE_TOOL_NAME)],
            }),
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call("sql-call", '{"sql":"SELECT COUNT(*) FROM public.systemes"}')],
            }),
            mistral_response({"role": "assistant", "content": "Réponse combinée."}),
        ]
        environment, post = self.chat(responses)
        document_result = {"results": [{
            "document_id": "doc", "chunk_id": "chunk", "title": "Guide",
            "path": "docs/guide.md", "heading": None, "content": "Systèmes", "rank": 1.0,
        }]}
        with (
            environment,
            post as mocked_post,
            patch("digues_webapp.ai.search_project_knowledge", return_value=document_result),
            patch("digues_webapp.ai.execute_readonly_query", return_value={
                "columns": ["count"], "rows": [[3]], "truncated": False,
            }),
        ):
            answer = chat_with_mistral([], "schema", current_message="Explique et compte")

        self.assertEqual(answer.executed_queries, ("SELECT COUNT(*) FROM public.systemes",))
        self.assertEqual(answer.consulted_sources[0].path, "docs/guide.md")
        final_messages = mocked_post.call_args_list[-1].kwargs["json"]["messages"]
        self.assertEqual(
            [message["tool_call_id"] for message in final_messages if message["role"] == "tool"],
            ["doc-call", "sql-call"],
        )

    def test_document_tool_rejects_arbitrary_path_argument(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call(
                    "unsafe-path", '{"query":"secret","path":"private/secret.md"}',
                    name=PROJECT_KNOWLEDGE_TOOL_NAME,
                )],
            }),
            mistral_response({"role": "assistant", "content": "Refusé."}),
        ]
        environment, post = self.chat(responses)
        with environment, post as mocked_post, patch(
            "digues_webapp.ai.search_project_knowledge"
        ) as search:
            answer = chat_with_mistral([], "schema", current_message="Lis un fichier")
        search.assert_not_called()
        self.assertEqual(answer.consulted_sources, ())
        tool = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertFalse(json.loads(tool["content"])["ok"])

    def test_external_search_result_is_returned_with_source_and_matching_id(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call(
                    "external-1",
                    '{"query":"surveillance des digues"}',
                    name=EXTERNAL_SOURCES_TOOL_NAME,
                )],
            }),
            mistral_response({"role": "assistant", "content": "Source trouvée."}),
        ]
        result = {"results": [{
            "title": "Guide de surveillance",
            "organization": "Cerema",
            "url": "https://www.cerema.fr/fr/guide",
            "source_id": "cerema",
            "category": "TECHNICAL_PUBLIC",
            "document_date": None,
            "retrieved_at": "2026-09-06T12:00:00+00:00",
            "excerpt": "La surveillance repose sur des inspections.",
        }]}
        environment, post = self.chat(responses)
        with (
            environment,
            post as mocked_post,
            patch("digues_webapp.ai.search_external_sources", return_value=result) as search,
        ):
            answer = chat_with_mistral([], "schema", current_message="Surveillance ?")

        search.assert_called_once_with("surveillance des digues")
        self.assertEqual(len(answer.external_sources), 1)
        self.assertEqual(answer.external_sources[0].url, result["results"][0]["url"])
        tool = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertEqual(tool["name"], EXTERNAL_SOURCES_TOOL_NAME)
        self.assertEqual(tool["tool_call_id"], "external-1")
        self.assertEqual(json.loads(tool["content"]), result)

    def test_empty_external_search_does_not_create_false_source(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call(
                    "external-empty", '{"query":"absent"}',
                    name=EXTERNAL_SOURCES_TOOL_NAME,
                )],
            }),
            mistral_response({"role": "assistant", "content": "Aucune source."}),
        ]
        environment, post = self.chat(responses)
        with environment, post, patch(
            "digues_webapp.ai.search_external_sources", return_value={"results": []}
        ):
            answer = chat_with_mistral([], "schema", current_message="Question")
        self.assertEqual(answer.external_sources, ())

    def test_external_tool_rejects_arbitrary_url_argument(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call(
                    "external-url",
                    '{"query":"digues","url":"https://example.test/secret"}',
                    name=EXTERNAL_SOURCES_TOOL_NAME,
                )],
            }),
            mistral_response({"role": "assistant", "content": "URL refusée."}),
        ]
        environment, post = self.chat(responses)
        with environment, post as mocked_post, patch(
            "digues_webapp.ai.search_external_sources"
        ) as search:
            answer = chat_with_mistral([], "schema", current_message="Cherche ici")
        search.assert_not_called()
        self.assertEqual(answer.external_sources, ())
        tool = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertFalse(json.loads(tool["content"])["ok"])

    def test_external_sources_are_deduplicated_and_combine_with_sql(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call(
                    "external-a", '{"query":"digues"}', name=EXTERNAL_SOURCES_TOOL_NAME,
                )],
            }),
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [
                    tool_call(
                        "external-b",
                        '{"query":"ouvrages"}',
                        name=EXTERNAL_SOURCES_TOOL_NAME,
                    ),
                    tool_call("sql-b", '{"sql":"SELECT COUNT(*) FROM public.systemes"}'),
                ],
            }),
            mistral_response({"role": "assistant", "content": "Réponse combinée."}),
        ]
        result = {"results": [{
            "title": "Ressource métier", "organization": "France Digues",
            "url": "https://www.france-digues.fr/ressource", "source_id": "france_digues",
            "category": "PROFESSIONAL", "document_date": "2026-01-02",
            "retrieved_at": "2026-09-06T12:00:00+00:00", "excerpt": "Contenu",
        }]}
        environment, post = self.chat(responses)
        with (
            environment,
            post as mocked_post,
            patch("digues_webapp.ai.search_external_sources", return_value=result),
            patch("digues_webapp.ai.execute_readonly_query", return_value={
                "columns": ["count"], "rows": [[2]], "truncated": False,
            }),
        ):
            answer = chat_with_mistral([], "schema", current_message="Explique et compte")

        self.assertEqual(len(answer.external_sources), 1)
        self.assertEqual(answer.executed_queries, ("SELECT COUNT(*) FROM public.systemes",))
        messages = mocked_post.call_args_list[-1].kwargs["json"]["messages"]
        self.assertEqual(
            [message["tool_call_id"] for message in messages if message["role"] == "tool"],
            ["external-a", "external-b", "sql-b"],
        )

    def test_external_and_local_knowledge_tools_can_be_combined(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [
                    tool_call(
                        "external",
                        '{"query":"gestion des digues"}',
                        name=EXTERNAL_SOURCES_TOOL_NAME,
                    ),
                    tool_call("local", '{"query":"architecture"}', name=PROJECT_KNOWLEDGE_TOOL_NAME),
                ],
            }),
            mistral_response({"role": "assistant", "content": "Synthèse."}),
        ]
        external = {"results": [{
            "title": "Guide", "organization": "Cerema",
            "url": "https://www.cerema.fr/fr/guide", "source_id": "cerema",
            "category": "TECHNICAL_PUBLIC", "document_date": None,
            "retrieved_at": "2026-09-06T12:00:00+00:00", "excerpt": "Guide public",
        }]}
        local = {"results": [{
            "document_id": "doc", "chunk_id": "chunk", "title": "README",
            "path": "README.md", "heading": "Architecture", "content": "FastAPI", "rank": 1.0,
        }]}
        environment, post = self.chat(responses)
        with (
            environment,
            post,
            patch("digues_webapp.ai.search_external_sources", return_value=external),
            patch("digues_webapp.ai.search_project_knowledge", return_value=local),
        ):
            answer = chat_with_mistral([], "schema", current_message="Compare")
        self.assertEqual(len(answer.external_sources), 1)
        self.assertEqual(len(answer.consulted_sources), 1)

    def test_simple_sql_call_is_returned_to_mistral_with_matching_id(self):
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call("call-1", '{"sql":"SELECT id FROM public.systemes"}')],
        })
        final = mistral_response({
            "role": "assistant",
            "content": "Un système a été trouvé.",
        })
        result = {"columns": ["id"], "rows": [["systeme-1"]], "truncated": False}
        environment, post = self.chat([first, final])
        with (
            environment,
            post as mocked_post,
            patch(
                "digues_webapp.ai.execute_readonly_query", return_value=result
            ) as execute,
        ):
            answer = chat_with_mistral(
                [],
                "schema",
                current_message="Quels systèmes existent ?",
            )

        self.assertEqual(answer.answer, "Un système a été trouvé.")
        self.assertEqual(
            answer.executed_queries,
            ("SELECT id FROM public.systemes",),
        )
        from digues_webapp.database import open_ai_connection
        execute.assert_called_once_with("SELECT id FROM public.systemes", connection_factory=open_ai_connection)
        self.assertEqual(mocked_post.call_count, 2)
        second_messages = mocked_post.call_args_list[1].kwargs["json"]["messages"]
        assistant = second_messages[-2]
        tool = second_messages[-1]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call-1")
        self.assertEqual(tool["role"], "tool")
        self.assertEqual(tool["name"], BUSINESS_DATABASE_TOOL_NAME)
        self.assertEqual(tool["tool_call_id"], "call-1")
        self.assertEqual(json.loads(tool["content"]), result)

    def test_aggregation_and_truncation_are_preserved_in_tool_results(self):
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                tool_call("aggregate", '{"sql":"SELECT COUNT(*) FROM public.systemes"}'),
                tool_call("details", '{"sql":"SELECT * FROM public.troncons"}'),
            ],
        })
        final = mistral_response({"role": "assistant", "content": "Synthèse."})
        results = [
            {"columns": ["count"], "rows": [[42]], "truncated": False},
            {"columns": ["id"], "rows": [["t1"]], "truncated": True},
        ]
        environment, post = self.chat([first, final])
        with (
            environment,
            post as mocked_post,
            patch("digues_webapp.ai.execute_readonly_query", side_effect=results) as execute,
        ):
            answer = chat_with_mistral(
                [], "schema", current_message="Compte et détaille."
            )

        self.assertEqual(answer.answer, "Synthèse.")
        self.assertEqual(
            answer.executed_queries,
            (
                "SELECT COUNT(*) FROM public.systemes",
                "SELECT * FROM public.troncons",
            ),
        )
        self.assertEqual(execute.call_count, 2)
        messages = mocked_post.call_args_list[1].kwargs["json"]["messages"]
        tool_messages = [message for message in messages if message["role"] == "tool"]
        self.assertEqual(
            [message["tool_call_id"] for message in tool_messages],
            ["aggregate", "details"],
        )
        self.assertEqual(json.loads(tool_messages[0]["content"]), results[0])
        self.assertTrue(json.loads(tool_messages[1]["content"])["truncated"])

    def test_refused_sql_returns_controlled_tool_error(self):
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call("write", '{"sql":"UPDATE public.systemes SET valid=false"}')],
        })
        final = mistral_response({
            "role": "assistant",
            "content": "Je ne peux pas modifier les données.",
        })
        environment, post = self.chat([first, final])
        with (
            environment,
            post as mocked_post,
            patch(
                "digues_webapp.ai.execute_readonly_query",
                side_effect=ReadonlySqlValidationError("UPDATE interdit"),
            ) as execute,
        ):
            answer = chat_with_mistral(
                [], "schema", current_message="Modifie les systèmes."
            )

        self.assertEqual(answer.answer, "Je ne peux pas modifier les données.")
        self.assertEqual(answer.executed_queries, ())
        execute.assert_called_once()
        tool = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        error = json.loads(tool["content"])
        self.assertFalse(error["ok"])
        self.assertIn("politique de sécurité", error["error"])
        self.assertNotIn("UPDATE interdit", tool["content"])

    def test_unknown_tool_is_refused_without_execution(self):
        first = mistral_response({
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call("unknown", "{}", name="delete_everything")],
        })
        final = mistral_response({"role": "assistant", "content": "Outil refusé."})
        environment, post = self.chat([first, final])
        with (
            environment,
            post as mocked_post,
            patch("digues_webapp.ai.execute_readonly_query") as execute,
        ):
            answer = chat_with_mistral(
                [], "schema", current_message="Test"
            )

        execute.assert_not_called()
        self.assertEqual(answer.executed_queries, ())
        tool = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
        self.assertEqual(tool["name"], "delete_everything")
        self.assertEqual(json.loads(tool["content"])["error"], "Outil non autorisé.")

    def test_invalid_arguments_are_reported_without_execution(self):
        cases = ("{", "{}", '{"sql":12}', '{"sql":"   "}', '{"sql":"SELECT 1","extra":true}')
        for index, arguments in enumerate(cases):
            with self.subTest(arguments=arguments):
                first = mistral_response({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [tool_call(f"invalid-{index}", arguments)],
                })
                final = mistral_response({"role": "assistant", "content": "Corrigé."})
                environment, post = self.chat([first, final])
                with (
                    environment,
                    post as mocked_post,
                    patch("digues_webapp.ai.execute_readonly_query") as execute,
                ):
                    answer = chat_with_mistral(
                        [], "schema", current_message="Test"
                    )
                execute.assert_not_called()
                self.assertEqual(answer.executed_queries, ())
                tool = mocked_post.call_args_list[1].kwargs["json"]["messages"][-1]
                self.assertFalse(json.loads(tool["content"])["ok"])

    def test_successive_tool_rounds_are_supported(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call("first", '{"sql":"SELECT 1"}')],
            }),
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call("second", '{"sql":"SELECT 2"}')],
            }),
            mistral_response({"role": "assistant", "content": "Terminé."}),
        ]
        environment, post = self.chat(responses)
        with (
            environment,
            post as mocked_post,
            patch(
                "digues_webapp.ai.execute_readonly_query",
                side_effect=[
                    {"columns": ["one"], "rows": [[1]], "truncated": False},
                    {"columns": ["two"], "rows": [[2]], "truncated": False},
                ],
            ) as execute,
        ):
            answer = chat_with_mistral(
                [], "schema", current_message="Deux analyses."
            )

        self.assertEqual(answer.answer, "Terminé.")
        self.assertEqual(answer.executed_queries, ("SELECT 1", "SELECT 2"))
        from digues_webapp.database import open_ai_connection
        self.assertEqual(execute.call_args_list, [
            call("SELECT 1", connection_factory=open_ai_connection),
            call("SELECT 2", connection_factory=open_ai_connection)])
        self.assertEqual(mocked_post.call_count, 3)

    def test_tool_loop_is_capped_then_final_answer_is_forced(self):
        responses = [
            mistral_response({
                "role": "assistant", "content": "",
                "tool_calls": [tool_call(f"call-{index}", '{"sql":"SELECT 1"}')],
            })
            for index in range(MISTRAL_MAX_TOOL_CALLS)
        ] + [mistral_response({"role": "assistant", "content": "Réponse bornée."})]
        environment, post = self.chat(responses)
        with (
            environment,
            post as mocked_post,
            patch(
                "digues_webapp.ai.execute_readonly_query",
                return_value={"columns": ["one"], "rows": [[1]], "truncated": False},
            ) as execute,
        ):
            answer = chat_with_mistral(
                [], "schema", current_message="Boucle."
            )

        self.assertEqual(answer.answer, "Réponse bornée.")
        self.assertEqual(
            answer.executed_queries,
            tuple("SELECT 1" for _ in range(MISTRAL_MAX_TOOL_CALLS)),
        )
        self.assertEqual(execute.call_count, MISTRAL_MAX_TOOL_CALLS)
        self.assertEqual(mocked_post.call_count, MISTRAL_MAX_TOOL_CALLS + 1)
        self.assertTrue(all(
            item.kwargs["json"]["tool_choice"] == "auto"
            for item in mocked_post.call_args_list[:-1]
        ))
        self.assertEqual(
            mocked_post.call_args_list[-1].kwargs["json"]["tool_choice"], "none"
        )


if __name__ == "__main__":
    unittest.main()
