import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from digues_webapp.ai import (
    MEMORY_SUMMARY_SYSTEM_PROMPT,
    BUSINESS_DATABASE_TOOL_NAME,
    _compact_memory,
    _summarize_memory_with_model,
    chat_with_mistral,
)
from digues_webapp.ai_memory import (
    AI_MODEL_INPUT_MAX_CHARS,
    AI_RECENT_MESSAGES_MAX,
    AI_RECENT_MESSAGES_MAX_CHARS,
    compact_tool_result,
    json_size,
)
from digues_webapp.models import AiChatRequest


def mistral_response(message):
    response = Mock(status_code=200, ok=True)
    response.json.return_value = {"choices": [{"message": message}]}
    return response


class AiLongMemoryTest(unittest.TestCase):
    def test_large_assistant_answer_can_be_sent_on_the_next_turn(self):
        long_answer = "réponse " * 1_200
        self.assertGreater(len(long_answer), 8_000)
        with (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch(
                "digues_webapp.ai.requests.post",
                return_value=mistral_response({
                    "role": "assistant", "content": long_answer
                }),
            ),
        ):
            first_result = chat_with_mistral(
                [], "schema", current_message="Question précédente"
            )
        self.assertEqual(first_result.answer, long_answer.strip())
        self.assertEqual(first_result.recent_messages[-1]["content"], long_answer.strip())

        request = AiChatRequest.model_validate({
            "conversation_summary": "",
            "recent_messages": list(first_result.recent_messages),
            "message": "Question suivante",
        })
        self.assertEqual(request.recent_messages[-1].content, long_answer.strip())

        with self.assertRaises(ValidationError):
            AiChatRequest.model_validate({
                "messages": [
                    {"role": "user", "content": "Question précédente"},
                    {"role": "assistant", "content": long_answer},
                    {"role": "user", "content": "Question suivante"},
                ]
            })

    def test_user_message_limit_remains_independent(self):
        with self.assertRaises(ValidationError):
            AiChatRequest(message="u" * 8_001)
        AiChatRequest(
            message="suite",
            recent_messages=[{"role": "assistant", "content": "a" * 9_000}],
        )

    def test_long_conversation_is_summarized_and_latest_turns_are_kept(self):
        messages = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"message-{index}-" + ("x" * 1_000),
            }
            for index in range(30)
        ]
        with patch(
            "digues_webapp.ai._summarize_memory_with_model",
            return_value="Décisions : conserver le tronçon T-42.",
        ) as summarize:
            summary, recent = _compact_memory("", messages, api_key="key")

        self.assertEqual(summary, "Décisions : conserver le tronçon T-42.")
        self.assertLessEqual(len(recent), AI_RECENT_MESSAGES_MAX)
        self.assertLessEqual(
            sum(len(message["content"]) for message in recent),
            AI_RECENT_MESSAGES_MAX_CHARS,
        )
        self.assertEqual(recent[-1], messages[-1])
        self.assertEqual(recent[-2], messages[-2])
        self.assertEqual(recent[0]["role"], "user")
        old_summary, evicted = summarize.call_args.args[1:]
        self.assertEqual(old_summary, "")
        self.assertGreater(len(evicted), 0)

    def test_previous_summary_is_merged_with_evicted_messages(self):
        messages = [
            {"role": "user", "content": "Décision ancienne " + "x" * 10_000},
            {"role": "assistant", "content": "Confirmé " + "y" * 10_000},
            {"role": "user", "content": "Question récente"},
        ]

        def summarize(_key, previous, evicted):
            self.assertEqual(previous, "Contrainte : ne pas modifier la digue D1.")
            self.assertEqual(evicted, messages[:2])
            return "Décisions : D1 reste inchangée.\nPoints ouverts : question récente."

        with patch(
            "digues_webapp.ai._summarize_memory_with_model",
            side_effect=summarize,
        ):
            summary, recent = _compact_memory(
                "Contrainte : ne pas modifier la digue D1.",
                messages,
                api_key="key",
            )
        self.assertIn("D1 reste inchangée", summary)
        self.assertEqual(recent, [messages[-1]])

    def test_summary_failure_uses_bounded_fallback(self):
        messages = [
            {"role": "user", "content": "Objet métier : SE-12. " + "x" * 11_000},
            {"role": "assistant", "content": "Décision confirmée. " + "y" * 11_000},
            {"role": "user", "content": "Suite"},
        ]
        with patch(
            "digues_webapp.ai._summarize_memory_with_model",
            side_effect=RuntimeError("provider unavailable"),
        ):
            summary, recent = _compact_memory("Ancienne contrainte", messages, api_key="key")
        self.assertIn("Ancienne contrainte", summary)
        self.assertIn("Objet métier", summary)
        self.assertEqual(recent, [messages[-1]])

    def test_summary_prompt_preserves_business_memory_categories(self):
        for expected in (
            "décisions",
            "objets métier",
            "contraintes",
            "demandes encore ouvertes",
            "corrections de l'utilisateur",
        ):
            self.assertIn(expected, MEMORY_SUMMARY_SYSTEM_PROMPT.lower())
        self.assertIn("sans appeler d'outil", MEMORY_SUMMARY_SYSTEM_PROMPT)

    def test_summary_call_has_no_tools_and_is_deterministic(self):
        response = mistral_response({
            "role": "assistant",
            "content": "Décisions : conserver D-8.",
        })
        with patch("digues_webapp.ai.requests.post", return_value=response) as post:
            summary = _summarize_memory_with_model(
                "key",
                "Contrainte : lecture seule.",
                [{"role": "user", "content": "Conserver la digue D-8."}],
            )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(summary, "Décisions : conserver D-8.")
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertEqual(payload["temperature"], 0.1)
        self.assertIn("lecture seule", payload["messages"][1]["content"])
        self.assertIn("D-8", payload["messages"][1]["content"])

    def test_large_sql_result_is_compacted_with_counts_and_marker(self):
        original = {
            "columns": ["id", "designation"],
            "rows": [[index, "tronçon-" + "x" * 100] for index in range(500)],
            "row_count": 12_430,
            "truncated": True,
        }
        snapshot = json.loads(json.dumps(original))
        compact = compact_tool_result(original, 2_000)
        self.assertLessEqual(json_size(compact), 2_000)
        self.assertTrue(compact["ai_context"]["truncated"])
        self.assertIn("12430", compact["ai_context"]["notice"])
        self.assertLess(len(compact["rows"]), len(original["rows"]))
        self.assertEqual(compact["columns"], original["columns"])
        self.assertEqual(original, snapshot)

    def test_empty_and_error_tool_results_are_not_changed(self):
        for result in ({"columns": [], "rows": [], "truncated": False}, {"ok": False, "error": "indisponible"}):
            self.assertIs(compact_tool_result(result, 2_000), result)

    def test_every_provider_request_stays_inside_global_budget(self):
        old_messages = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (f"ancien-{index} " + "x" * 2_500),
            }
            for index in range(10)
        ]
        large_result = {
            "columns": ["id", "designation"],
            "rows": [[index, "x" * 500] for index in range(1_000)],
            "row_count": 15_000,
            "truncated": True,
        }
        responses = [
            mistral_response({"role": "assistant", "content": "Décisions : garder T1."}),
            mistral_response({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "sql-1",
                    "type": "function",
                    "function": {
                        "name": BUSINESS_DATABASE_TOOL_NAME,
                        "arguments": '{"sql":"SELECT * FROM public.troncons"}',
                    },
                }],
            }),
            mistral_response({"role": "assistant", "content": "Résultat synthétisé."}),
        ]
        with (
            patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}),
            patch("digues_webapp.ai.requests.post", side_effect=responses) as post,
            patch("digues_webapp.ai.execute_readonly_query", return_value=large_result),
        ):
            result = chat_with_mistral(
                old_messages,
                "TABLE public.troncons\n" + "schema " * 10_000,
                current_message="Analyse les tronçons",
            )
        self.assertEqual(result.answer, "Résultat synthétisé.")
        for call in post.call_args_list:
            self.assertLessEqual(
                json_size(call.kwargs["json"]),
                AI_MODEL_INPUT_MAX_CHARS,
            )
        final_tools = [
            message
            for message in post.call_args_list[-1].kwargs["json"]["messages"]
            if message["role"] == "tool"
        ]
        tool_payload = json.loads(final_tools[0]["content"])
        self.assertTrue(tool_payload["ai_context"]["truncated"])
        self.assertIn("15000", tool_payload["ai_context"]["notice"])


class AiFrontendMemoryTest(unittest.TestCase):
    def test_frontend_sends_and_restores_summary_and_recent_window(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const AiPanel = require("./webapp/frontend/js/ai-panel.js");
class Element {
  constructor(id = "") {
    this.id = id; this.children = []; this.listeners = {}; this.value = "";
    this.disabled = false; this.hidden = true; this.attributes = {};
    this.className = ""; this.classList = { add: () => {} };
    this.scrollHeight = 10; this.scrollTop = 0; this.textContent = "";
  }
  append(...items) { this.children.push(...items); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  remove() { this.removed = true; }
  focus() { this.focused = true; }
  requestSubmit() { return this.listeners.submit?.({ preventDefault() {} }); }
}
function makeRoot() {
  const ids = ["ai-panel", "toggle-ai", "close-ai", "ai-conversation",
    "ai-conversation-empty", "ai-chat-form", "ai-message", "ai-send", "ai-chat-status"];
  const elements = Object.fromEntries(ids.map((id) => [id, new Element(id)]));
  return { elements, querySelector(selector) { return elements[selector.slice(1)]; } };
}
globalThis.document = {
  createElement(tag) { return new Element(tag); },
  createTextNode(value) { const node = new Element("text"); node.textContent = String(value); return node; },
  body: new Element("body"), execCommand() { return true; },
};
globalThis.navigator = {};
globalThis.window = { setTimeout(fn) { fn(); } };
const values = {};
const storage = {
  getItem(key) { return values[key] || null; },
  setItem(key, value) { values[key] = value; },
};
const requests = [];
const responseMemory = {
  answer: "Réponse longue",
  conversation_summary: "Décisions : conserver SE-1.",
  recent_messages: [
    { role: "user", content: "Nouvelle question" },
    { role: "assistant", content: "Réponse longue" },
  ],
  executed_queries: [], consulted_sources: [], external_sources: [],
};
async function run() {
  const first = makeRoot();
  AiPanel.createController({ rootElement: first, storage, fetchJson: async (_url, options) => {
    requests.push(JSON.parse(options.body)); return responseMemory;
  }});
  first.elements["ai-message"].value = "Nouvelle question";
  await first.elements["ai-chat-form"].listeners.submit({ preventDefault() {} });

  const second = makeRoot();
  AiPanel.createController({ rootElement: second, storage, fetchJson: async (_url, options) => {
    requests.push(JSON.parse(options.body)); return responseMemory;
  }});
  second.elements["ai-message"].value = "Question après reload";
  await second.elements["ai-chat-form"].listeners.submit({ preventDefault() {} });
  process.stdout.write(JSON.stringify({ requests, stored: JSON.parse(Object.values(values)[0]),
    restoredCount: second.elements["ai-conversation"].children.length - 2 }));
}
run();
'''
        result = json.loads(subprocess.check_output(
            [node, "-e", program], text=True, cwd=os.getcwd()
        ))
        self.assertEqual(result["requests"][0], {
            "conversation_summary": "",
            "recent_messages": [],
            "message": "Nouvelle question",
        })
        self.assertEqual(
            result["requests"][1]["conversation_summary"],
            "Décisions : conserver SE-1.",
        )
        self.assertEqual(len(result["requests"][1]["recent_messages"]), 2)
        self.assertEqual(result["requests"][1]["message"], "Question après reload")
        self.assertEqual(result["stored"]["conversation_summary"], "Décisions : conserver SE-1.")
        self.assertEqual(result["restoredCount"], 2)


if __name__ == "__main__":
    unittest.main()
