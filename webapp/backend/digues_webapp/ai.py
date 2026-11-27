"""Adaptateur Mistral du prototype avec outils strictement en lecture seule."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from typing import Any

import requests

from .environment_tools import (
    EnvironmentToolError,
    get_hydrometric_data,
    get_hydrometric_sites,
    get_precipitation_forecast,
)
from .ai_memory import (
    AI_CONVERSATION_SUMMARY_MAX_CHARS,
    AI_MODEL_INPUT_MAX_CHARS,
    AI_RESPONSE_RESERVE_CHARS,
    AI_SUMMARY_MAX_TOKENS,
    AI_SYSTEM_CONTEXT_MAX_CHARS,
    AI_TOOL_ARGUMENTS_MAX_CHARS,
    AI_TOOL_RESULT_MAX_CHARS,
    AI_TOOL_RESULTS_MAX_CHARS,
    compact_tool_result,
    fallback_summary,
    json_size,
    partition_recent_messages,
    summary_source,
    truncate_text,
)
from .external_sources import ExternalSourceSearchError, search_external_sources
from .prompts import SYSTEM_PROMPT
from .knowledge.search import KnowledgeSearchError, search_project_knowledge
from .readonly_sql import (
    ReadonlySqlExecutionError,
    ReadonlySqlValidationError,
    execute_readonly_query,
)
from .database import open_ai_connection


LOGGER = logging.getLogger(__name__)
MISTRAL_CHAT_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "mistral-small-latest"
MISTRAL_TIMEOUT_SECONDS = 30
MISTRAL_MAX_TOOL_CALLS = 10
MISTRAL_RESPONSE_MAX_TOKENS = AI_RESPONSE_RESERVE_CHARS // 4
BUSINESS_DATABASE_TOOL_NAME = "query_business_database"
PROJECT_KNOWLEDGE_TOOL_NAME = "search_project_knowledge"
EXTERNAL_SOURCES_TOOL_NAME = "search_external_sources"
PRECIPITATION_TOOL_NAME = "get_precipitation_forecast"
HYDROMETRIC_SITES_TOOL_NAME = "get_hydrometric_sites"
HYDROMETRIC_DATA_TOOL_NAME = "get_hydrometric_data"
BUSINESS_DATABASE_TOOL = {
    "type": "function",
    "function": {
        "name": BUSINESS_DATABASE_TOOL_NAME,
        "description": (
            "Consulte la base PostgreSQL/PostGIS de l’application expérimentale "
            "en lecture seule. "
            "Utilise exclusivement le schéma fourni comme source de vérité. "
            "SELECT, WITH/CTE, jointures, agrégations et fonctions PostGIS de "
            "lecture sont autorisés. Toute écriture ou modification du schéma "
            "est refusée par le serveur. Un résultat avec truncated=true est "
            "incomplet et ne représente pas toutes les lignes correspondantes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Requête PostgreSQL/PostGIS de lecture à exécuter.",
                }
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
}
PROJECT_KNOWLEDGE_TOOL = {
    "type": "function",
    "function": {
        "name": PROJECT_KNOWLEDGE_TOOL_NAME,
        "description": (
            "Recherche des passages dans la documentation locale versionnée "
            "du projet expérimental (README.md, docs/**/*.md, webapp/README.md et "
            "webapp/docs/**/*.md). À utiliser pour les "
            "questions sur l’architecture, le fonctionnement, le modèle métier "
            "documenté et les procédures du projet. Cette documentation locale "
            "n’est pas une source réglementaire externe."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Termes précis à rechercher dans la documentation du projet.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
EXTERNAL_SOURCES_TOOL = {
    "type": "function",
    "function": {
        "name": EXTERNAL_SOURCES_TOOL_NAME,
        "description": (
            "Recherche de manière contrôlée dans des sources Internet autorisées "
            "par le serveur. À utiliser pour consulter des informations publiques "
            "externes sur la réglementation, les institutions, la technique et les "
            "pratiques professionnelles. Le serveur choisit les organismes, valide "
            "les destinations et ne retourne que des extraits bornés. L’outil "
            "n’accepte aucune URL fournie par le modèle."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Question ou termes précis à rechercher dans les sources "
                        "externes autorisées."
                    ),
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
PRECIPITATION_TOOL = {
    "type": "function",
    "function": {
        "name": PRECIPITATION_TOOL_NAME,
        "description": (
            "Consulte les prévisions territoriales de précipitations ARPEGE déjà "
            "préparées par le backend. Sans valid_time, retourne les échéances "
            "disponibles dans forecast_steps. Pour une prévision précise, valid_time "
            "doit être recopié exactement depuis une valeur forecast_steps[].valid_time "
            "précédemment retournée par cet outil : ne jamais construire, arrondir, "
            "tronquer ou modifier cet horodatage. Avec valid_time, retourne soit la "
            "valeur de la cellule contenant le point longitude/latitude, soit un résumé "
            "du territoire. N'accepte aucune URL ni identifiant d'API."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "valid_time": {
                    "type": "string",
                    "description": (
                        "Valeur exacte d'un forecast_steps[].valid_time précédemment retourné "
                        "par cet outil. La recopier sans modifier la date, l'heure ni le fuseau."
                    ),
                },
                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
            },
            "additionalProperties": False,
        },
    },
}
HYDROMETRIC_SITES_TOOL = {
    "type": "function",
    "function": {
        "name": HYDROMETRIC_SITES_TOOL_NAME,
        "description": (
            "Liste les sites hydrométriques découverts pour le territoire par "
            "Hub'Eau et BD TOPAGE. Retourne leur rôle intérieur/amont/aval, "
            "leurs stations sources, la résolution H/Q et les prévisions "
            "Vigicrues éventuellement disponibles."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 100}
            },
            "additionalProperties": False,
        },
    },
}
HYDROMETRIC_DATA_TOOL = {
    "type": "function",
    "function": {
        "name": HYDROMETRIC_DATA_TOOL_NAME,
        "description": (
            "Lit dans le dataset hydrométrique territorial les observations "
            "Hub'Eau rééchantillonnées ou les prévisions Vigicrues disponibles "
            "d'un site. "
            "Distingue hauteur H et débit Q et conserve les horodatages, unités "
            "et le caractère observation/prévision."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "station": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "Nom ou code exact/précis d'une station du territoire.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["observation", "forecast"],
                },
                "variable": {
                    "type": "string",
                    "enum": ["H", "Q", "both"],
                    "default": "both",
                },
                "start_time": {"type": "string", "description": "Début ISO 8601 inclusif."},
                "end_time": {"type": "string", "description": "Fin ISO 8601 inclusive."},
                "max_points": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 56,
                    "default": 28,
                },
            },
            "required": ["station", "kind"],
            "additionalProperties": False,
        },
    },
}
ASSISTANT_TOOLS = [
    BUSINESS_DATABASE_TOOL,
    PROJECT_KNOWLEDGE_TOOL,
    EXTERNAL_SOURCES_TOOL,
    PRECIPITATION_TOOL,
    HYDROMETRIC_SITES_TOOL,
    HYDROMETRIC_DATA_TOOL,
]

MEMORY_SUMMARY_SYSTEM_PROMPT = """Tu condenses une mémoire conversationnelle de digues-app.
Réponds uniquement avec un résumé factuel compact en français, sans appeler d'outil.
Préserve en priorité : décisions, objets métier et identifiants, hypothèses retenues,
contraintes, demandes encore ouvertes, corrections de l'utilisateur et résultats
métier nécessaires à la suite. Écarte les formulations verbatim, politesses et longs
résultats d'outils déjà consommés. N'invente aucune information. Structure le résumé
avec les rubriques Décisions, Contexte métier, Contraintes et Points ouverts lorsque
ces rubriques contiennent des éléments."""


class AiServiceError(RuntimeError):
    """Erreur de service IA présentable sans détail sensible."""

    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AiChatResult:
    """Réponse et consultations réussies pour cette seule requête."""

    answer: str
    executed_queries: tuple[str, ...]
    consulted_sources: tuple["AiConsultedSource", ...] = ()
    external_sources: tuple["AiExternalSource", ...] = ()
    conversation_summary: str = ""
    recent_messages: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class AiConsultedSource:
    title: str
    path: str
    heading: str | None


@dataclass(frozen=True)
class AiExternalSource:
    title: str
    organization: str
    url: str
    source_id: str
    category: str
    document_date: str | None


def _api_key() -> str:
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise AiServiceError(
            "L’assistant IA n’est pas configuré sur le serveur.",
            status_code=503,
        )
    return api_key


def _message_from_response(payload: Any) -> dict[str, Any]:
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AiServiceError("La réponse de l’assistant IA est invalide.") from exc
    if not isinstance(message, dict):
        raise AiServiceError("La réponse de l’assistant IA est invalide.")
    return message


def _answer_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")

    if isinstance(content, str):
        answer = content.strip()
    elif isinstance(content, list):
        answer = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    else:
        answer = ""
    if not answer:
        raise AiServiceError("La réponse de l’assistant IA est invalide.")
    return answer


def _completion(
    mistral_messages: list[dict[str, Any]],
    *,
    api_key: str,
    tool_choice: str | None,
    tools: list[dict[str, Any]] | None = ASSISTANT_TOOLS,
    temperature: float | None = None,
    max_tokens: int = MISTRAL_RESPONSE_MAX_TOKENS,
) -> dict[str, Any]:
    request_payload: dict[str, Any] = {
        "model": MISTRAL_MODEL,
        "messages": mistral_messages,
        "max_tokens": max_tokens,
    }
    if tools is not None:
        request_payload.update({
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": True,
        })
    if temperature is not None:
        request_payload["temperature"] = temperature
    if json_size(request_payload) > AI_MODEL_INPUT_MAX_CHARS:
        raise AiServiceError(
            "Le contexte de la conversation dépasse la capacité configurée."
        )
    try:
        response = requests.post(
            MISTRAL_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=MISTRAL_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        LOGGER.warning("Délai d’attente Mistral dépassé")
        raise AiServiceError(
            "L’assistant IA n’a pas répondu à temps.", status_code=504
        ) from exc
    except requests.RequestException as exc:
        LOGGER.warning("Connexion à Mistral impossible: %s", type(exc).__name__)
        raise AiServiceError("L’assistant IA est temporairement indisponible.") from exc

    if response.status_code in {401, 403}:
        LOGGER.warning("Authentification Mistral refusée (HTTP %s)", response.status_code)
        raise AiServiceError("L’authentification de l’assistant IA a échoué.")
    if response.status_code == 429:
        LOGGER.warning("Limite Mistral atteinte (HTTP 429)")
        raise AiServiceError(
            "L’assistant IA a atteint sa limite de requêtes.",
            status_code=503,
        )
    if response.status_code >= 500:
        LOGGER.warning("Erreur serveur Mistral (HTTP %s)", response.status_code)
        raise AiServiceError("L’assistant IA est temporairement indisponible.")
    if not response.ok:
        LOGGER.warning("Requête Mistral refusée (HTTP %s)", response.status_code)
        raise AiServiceError("Impossible d’obtenir une réponse de l’assistant.")

    try:
        payload = response.json()
    except ValueError as exc:
        LOGGER.warning("Réponse Mistral non JSON")
        raise AiServiceError("La réponse de l’assistant IA est invalide.") from exc
    return _message_from_response(payload)


def _system_content(schema_context: str) -> str:
    return truncate_text(
        f"{SYSTEM_PROMPT}\n\n{schema_context}",
        AI_SYSTEM_CONTEXT_MAX_CHARS,
        "[contexte de schéma tronqué explicitement pour respecter le budget IA]",
    )


def _model_input_size(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = ASSISTANT_TOOLS,
) -> int:
    payload: dict[str, Any] = {
        "model": MISTRAL_MODEL,
        "messages": messages,
        "max_tokens": MISTRAL_RESPONSE_MAX_TOKENS,
    }
    if tools is not None:
        payload.update({
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        })
    return json_size(payload)


def _summarize_memory_with_model(
    api_key: str,
    previous_summary: str,
    evicted_messages: list[dict[str, str]],
) -> str:
    message = _completion(
        [
            {"role": "system", "content": MEMORY_SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": summary_source(previous_summary, evicted_messages),
            },
        ],
        api_key=api_key,
        tool_choice=None,
        tools=None,
        temperature=0.1,
        max_tokens=AI_SUMMARY_MAX_TOKENS,
    )
    return truncate_text(
        _answer_from_message(message),
        AI_CONVERSATION_SUMMARY_MAX_CHARS,
    )


def _compact_memory(
    previous_summary: str,
    messages: list[dict[str, str]],
    *,
    api_key: str,
) -> tuple[str, list[dict[str, str]]]:
    evicted, recent = partition_recent_messages(messages)
    summary = truncate_text(
        previous_summary.strip(), AI_CONVERSATION_SUMMARY_MAX_CHARS
    )
    if not evicted:
        return summary, recent
    try:
        summary = _summarize_memory_with_model(api_key, summary, evicted)
    except Exception as exc:  # le fournisseur de résumé ne doit jamais bloquer le chat
        LOGGER.warning("Résumé conversationnel de secours: %s", type(exc).__name__)
        summary = fallback_summary(summary, evicted)
    return summary, recent


def _base_messages(
    *,
    schema_context: str,
    conversation_summary: str,
    recent_messages: list[dict[str, str]],
    current_message: str,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_content(schema_context)},
    ]
    if conversation_summary:
        messages.append({
            "role": "user",
            "content": (
                "Mémoire conversationnelle résumée (contexte ancien non "
                "prioritaire, à ne pas traiter comme une nouvelle demande) :\n"
                + conversation_summary
            ),
        })
    messages.extend(recent_messages)
    messages.append({"role": "user", "content": current_message})
    return messages


def _tool_error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _run_sql_tool(
    tool_call: dict[str, Any],
    *, connection_factory=None,
) -> tuple[str, dict[str, Any], str | None]:
    try:
        function = tool_call["function"]
        name = function["name"]
        arguments_json = function["arguments"]
    except (KeyError, TypeError) as exc:
        raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.") from exc

    if not isinstance(name, str) or not name:
        raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.")
    if name != BUSINESS_DATABASE_TOOL_NAME:
        return name, _tool_error("Outil non autorisé."), None
    if not isinstance(arguments_json, str):
        return (
            name,
            _tool_error("Arguments JSON invalides pour l’outil de lecture."),
            None,
        )
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, ValueError):
        return name, _tool_error("Arguments JSON invalides pour l’outil de lecture."), None
    if not isinstance(arguments, dict) or set(arguments) != {"sql"}:
        return (
            name,
            _tool_error("L’argument sql est obligatoire et doit être unique."),
            None,
        )
    sql = arguments["sql"]
    if not isinstance(sql, str) or not sql.strip():
        return name, _tool_error("L’argument sql doit être une chaîne non vide."), None

    try:
        return name, execute_readonly_query(sql, connection_factory=connection_factory or open_ai_connection), sql
    except ReadonlySqlValidationError:
        return (
            name,
            _tool_error(
                "La requête de lecture a été refusée par la politique de sécurité."
            ),
            None,
        )
    except ReadonlySqlExecutionError as exc:
        error = (
            "La requête de lecture a dépassé le délai autorisé."
            if exc.timed_out
            else "La requête de lecture a échoué."
        )
        return name, _tool_error(error), None
    except Exception as exc:  # protection de frontière, sans fuite vers le navigateur
        LOGGER.error("Échec interne de l’outil SQL: %s", type(exc).__name__)
        return name, _tool_error("La requête de lecture a échoué."), None


def _run_knowledge_tool(
    tool_call: dict[str, Any],
    *, connection_factory=None,
) -> tuple[str, dict[str, Any], tuple[tuple[str, AiConsultedSource], ...]]:
    try:
        function = tool_call["function"]
        name = function["name"]
        arguments_json = function["arguments"]
    except (KeyError, TypeError) as exc:
        raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.") from exc
    if not isinstance(arguments_json, str):
        return name, _tool_error("Arguments JSON invalides pour la recherche."), ()
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, ValueError):
        return name, _tool_error("Arguments JSON invalides pour la recherche."), ()
    if not isinstance(arguments, dict) or set(arguments) != {"query"}:
        return name, _tool_error("L’argument query est obligatoire et doit être unique."), ()
    query = arguments["query"]
    if not isinstance(query, str) or not query.strip():
        return name, _tool_error("L’argument query doit être une chaîne non vide."), ()
    try:
        kwargs = {'connection_factory': connection_factory} if connection_factory else {}
        result = search_project_knowledge(query, **kwargs)
    except KnowledgeSearchError as exc:
        return name, _tool_error(str(exc)), ()
    except Exception as exc:  # protection de frontière, sans fuite vers le navigateur
        LOGGER.error("Échec interne de l’outil documentaire: %s", type(exc).__name__)
        return name, _tool_error("La recherche documentaire a échoué."), ()

    sources: list[tuple[str, AiConsultedSource]] = []
    for item in result.get("results", []):
        if not isinstance(item, dict):
            continue
        chunk_id = item.get("chunk_id")
        title = item.get("title")
        path = item.get("path")
        heading = item.get("heading")
        if not all(isinstance(value, str) and value for value in (chunk_id, title, path)):
            continue
        sources.append((
            chunk_id,
            AiConsultedSource(
                title=title,
                path=path,
                heading=heading if isinstance(heading, str) and heading else None,
            ),
        ))
    return name, result, tuple(sources)


def _run_external_tool(
    tool_call: dict[str, Any],
) -> tuple[str, dict[str, Any], tuple[tuple[str, AiExternalSource], ...]]:
    try:
        function = tool_call["function"]
        name = function["name"]
        arguments_json = function["arguments"]
    except (KeyError, TypeError) as exc:
        raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.") from exc
    if not isinstance(arguments_json, str):
        return name, _tool_error("Arguments JSON invalides pour la recherche externe."), ()
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, ValueError):
        return name, _tool_error("Arguments JSON invalides pour la recherche externe."), ()
    if not isinstance(arguments, dict) or set(arguments) != {"query"}:
        return name, _tool_error("L’argument query est obligatoire et doit être unique."), ()
    query = arguments["query"]
    if not isinstance(query, str) or not query.strip():
        return name, _tool_error("L’argument query doit être une chaîne non vide."), ()
    try:
        result = search_external_sources(query)
    except ExternalSourceSearchError as exc:
        return name, _tool_error(str(exc)), ()
    except Exception as exc:  # protection de frontière, sans fuite vers le navigateur
        LOGGER.error("Échec interne de l’outil de sources externes: %s", type(exc).__name__)
        return name, _tool_error("La recherche externe a échoué."), ()

    sources: list[tuple[str, AiExternalSource]] = []
    for item in result.get("results", []):
        if not isinstance(item, dict):
            continue
        required = (
            item.get("title"),
            item.get("organization"),
            item.get("url"),
            item.get("source_id"),
            item.get("category"),
        )
        if not all(isinstance(value, str) and value for value in required):
            continue
        title, organization, url, source_id, category = required
        document_date = item.get("document_date")
        sources.append((
            url,
            AiExternalSource(
                title=title,
                organization=organization,
                url=url,
                source_id=source_id,
                category=category,
                document_date=(
                    document_date
                    if isinstance(document_date, str) and document_date
                    else None
                ),
            ),
        ))
    return name, result, tuple(sources)


def _run_environment_tool(
    tool_call: dict[str, Any],
    *, connection_factory=None,
) -> tuple[str, dict[str, Any]]:
    try:
        function = tool_call["function"]
        name = function["name"]
        arguments_json = function["arguments"]
    except (KeyError, TypeError) as exc:
        raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.") from exc
    if not isinstance(arguments_json, str):
        return name, _tool_error("Arguments JSON invalides pour les données environnementales.")
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, ValueError):
        return name, _tool_error("Arguments JSON invalides pour les données environnementales.")
    if not isinstance(arguments, dict):
        return name, _tool_error("Les arguments de l’outil doivent former un objet JSON.")

    contracts = {
        PRECIPITATION_TOOL_NAME: (
            {"valid_time", "longitude", "latitude"},
            set(),
            get_precipitation_forecast,
        ),
        HYDROMETRIC_SITES_TOOL_NAME: (
            {"query"},
            set(),
            get_hydrometric_sites,
        ),
        HYDROMETRIC_DATA_TOOL_NAME: (
            {"station", "kind", "variable", "start_time", "end_time", "max_points"},
            {"station", "kind"},
            get_hydrometric_data,
        ),
    }
    allowed, required, function_to_call = contracts[name]
    if not required.issubset(arguments) or not set(arguments).issubset(allowed):
        return name, _tool_error("Arguments absents ou non autorisés pour cet outil.")
    try:
        kwargs = {'connection_factory': connection_factory} if connection_factory else {}
        return name, function_to_call(**arguments, **kwargs)
    except EnvironmentToolError as exc:
        return name, _tool_error(str(exc))
    except Exception as exc:  # frontière sans fuite des services, caches ou secrets
        LOGGER.error(
            "Échec interne de l’outil environnemental %s: %s",
            name,
            type(exc).__name__,
        )
        return name, _tool_error(
            "Les données environnementales demandées sont indisponibles."
        )


def _run_tool(
    tool_call: dict[str, Any],
    *, connection_factory=None,
) -> tuple[
    str,
    dict[str, Any],
    str | None,
    tuple[tuple[str, AiConsultedSource], ...],
    tuple[tuple[str, AiExternalSource], ...],
]:
    function = tool_call.get("function")
    name = function.get("name") if isinstance(function, dict) else None
    kwargs = {'connection_factory': connection_factory} if connection_factory else {}
    if name == BUSINESS_DATABASE_TOOL_NAME:
        function_name, result, executed_sql = _run_sql_tool(tool_call, **kwargs)
        return function_name, result, executed_sql, (), ()
    if name == PROJECT_KNOWLEDGE_TOOL_NAME:
        function_name, result, sources = _run_knowledge_tool(tool_call, **kwargs)
        return function_name, result, None, sources, ()
    if name == EXTERNAL_SOURCES_TOOL_NAME:
        function_name, result, sources = _run_external_tool(tool_call)
        return function_name, result, None, (), sources
    if name in {
        PRECIPITATION_TOOL_NAME,
        HYDROMETRIC_SITES_TOOL_NAME,
        HYDROMETRIC_DATA_TOOL_NAME,
    }:
        function_name, result = _run_environment_tool(tool_call, **kwargs)
        return function_name, result, None, (), ()
    if not isinstance(name, str) or not name:
        raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.")
    return name, _tool_error("Outil non autorisé."), None, (), ()


def chat_with_mistral(
    recent_messages: list[dict[str, str]],
    schema_context: str,
    *,
    current_message: str,
    conversation_summary: str = "",
    connection_factory=None,
) -> AiChatResult:
    """Construit un contexte borné, exécute les outils puis actualise la mémoire."""

    api_key = _api_key()
    conversation_summary, recent_messages = _compact_memory(
        conversation_summary,
        recent_messages,
        api_key=api_key,
    )
    mistral_messages = _base_messages(
        schema_context=schema_context,
        conversation_summary=conversation_summary,
        recent_messages=recent_messages,
        current_message=current_message,
    )
    tool_call_count = 0
    tool_argument_chars = 0
    tool_result_chars = 0
    force_final_answer = False
    executed_queries: list[str] = []
    consulted_sources: list[AiConsultedSource] = []
    consulted_chunk_ids: set[str] = set()
    external_sources: list[AiExternalSource] = []
    external_source_urls: set[str] = set()

    while True:
        message = _completion(
            mistral_messages,
            api_key=api_key,
            tool_choice="none" if force_final_answer else "auto",
        )
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.")
        if not tool_calls:
            answer = _answer_from_message(message)
            conversation_summary, final_recent_messages = _compact_memory(
                conversation_summary,
                [
                    *recent_messages,
                    {"role": "user", "content": current_message},
                    {"role": "assistant", "content": answer},
                ],
                api_key=api_key,
            )
            return AiChatResult(
                answer=answer,
                executed_queries=tuple(executed_queries),
                consulted_sources=tuple(consulted_sources),
                external_sources=tuple(external_sources),
                conversation_summary=conversation_summary,
                recent_messages=tuple(final_recent_messages),
            )
        if force_final_answer:
            raise AiServiceError(
                "L’assistant n’a pas pu finaliser sa réponse après les consultations."
            )

        normalized_calls: list[dict[str, Any]] = []
        raw_tool_results: list[tuple[str, str, dict[str, Any]]] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.")
            if tool_call.get("type") != "function":
                raise AiServiceError(
                    "La réponse d’un outil de l’assistant IA est invalide."
                )
            tool_call_id = tool_call.get("id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.")
            function = tool_call.get("function")
            if not isinstance(function, dict):
                raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.")
            function_name = function.get("name")
            if not isinstance(function_name, str) or not function_name:
                raise AiServiceError("La réponse d’un outil de l’assistant IA est invalide.")

            arguments = function.get("arguments")
            arguments_length = len(arguments) if isinstance(arguments, str) else 0
            arguments_allowed = (
                isinstance(arguments, str)
                and tool_argument_chars + arguments_length <= AI_TOOL_ARGUMENTS_MAX_CHARS
            )

            if tool_call_count >= MISTRAL_MAX_TOOL_CALLS:
                result = _tool_error(
                    "Limite de consultations atteinte pour cette question."
                )
                normalized_function = function
            elif not arguments_allowed:
                tool_call_count += 1
                result = _tool_error(
                    "Arguments de l’outil trop volumineux pour le contexte autorisé."
                )
                normalized_function = {"name": function_name, "arguments": "{}"}
            else:
                tool_argument_chars += arguments_length
                tool_call_count += 1
                (
                    function_name,
                    result,
                    executed_sql,
                    sources,
                    returned_external_sources,
                ) = _run_tool(tool_call, **({'connection_factory': connection_factory} if connection_factory else {}))
                if executed_sql is not None:
                    executed_queries.append(executed_sql)
                for chunk_id, source in sources:
                    if chunk_id not in consulted_chunk_ids:
                        consulted_chunk_ids.add(chunk_id)
                        consulted_sources.append(source)
                for source_url, source in returned_external_sources:
                    if source_url not in external_source_urls:
                        external_source_urls.add(source_url)
                        external_sources.append(source)
                normalized_function = function

            normalized_calls.append({
                "id": tool_call_id,
                "type": "function",
                "function": normalized_function,
            })
            raw_tool_results.append((tool_call_id, function_name, result))

        mistral_messages.append({
            "role": "assistant",
            "content": truncate_text(str(message.get("content") or ""), 1_000),
            "tool_calls": normalized_calls,
        })
        for tool_call_id, function_name, result in raw_tool_results:
            reserved_markers = 180 * max(
                0, MISTRAL_MAX_TOOL_CALLS - tool_call_count
            )
            remaining_tool_budget = (
                AI_TOOL_RESULTS_MAX_CHARS - tool_result_chars - reserved_markers
            )
            remaining_model_budget = (
                AI_MODEL_INPUT_MAX_CHARS
                - _model_input_size(mistral_messages)
                - 300
            )
            available = min(
                AI_TOOL_RESULT_MAX_CHARS,
                remaining_tool_budget,
                remaining_model_budget,
            )
            if available < 300:
                compact_result = {
                    "ok": result.get("ok", True),
                    "ai_context": {
                        "truncated": True,
                        "notice": "[résultat non transmis : budget du contexte IA atteint]",
                    },
                }
            else:
                compact_result = compact_tool_result(result, available)
            content = json.dumps(
                compact_result, ensure_ascii=False, separators=(",", ":")
            )
            tool_result_chars += len(content)
            mistral_messages.append({
                "role": "tool",
                "name": function_name,
                "content": content,
                "tool_call_id": tool_call_id,
            })
        if tool_call_count >= MISTRAL_MAX_TOOL_CALLS:
            force_final_answer = True
