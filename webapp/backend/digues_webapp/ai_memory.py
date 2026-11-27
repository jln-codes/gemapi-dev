"""Budgets et opérations pures de la mémoire conversationnelle IA."""

from __future__ import annotations

import json
from typing import Any, Iterable


# Les limites sont exprimées en caractères sérialisés : c'est une borne stable et
# testable, indépendante du tokenizer du fournisseur. Le plafond d'entrée conserve
# une marge séparée pour la réponse du modèle.
AI_MODEL_CONTEXT_MAX_CHARS = 100_000
AI_RESPONSE_RESERVE_CHARS = 20_000
AI_MODEL_INPUT_MAX_CHARS = AI_MODEL_CONTEXT_MAX_CHARS - AI_RESPONSE_RESERVE_CHARS

AI_USER_MESSAGE_MAX_CHARS = 8_000
AI_ASSISTANT_HISTORY_MAX_CHARS = 100_000
AI_CONVERSATION_SUMMARY_MAX_CHARS = 6_000
AI_CONVERSATION_SUMMARY_INPUT_MAX_CHARS = 8_000
AI_RECENT_MESSAGES_MAX = 24
AI_RECENT_MESSAGES_MAX_CHARS = 20_000
AI_SYSTEM_CONTEXT_MAX_CHARS = 36_000
AI_TOOL_RESULTS_MAX_CHARS = 12_000
AI_TOOL_RESULT_MAX_CHARS = 6_000
AI_TOOL_ARGUMENTS_MAX_CHARS = 10_000
AI_SUMMARY_SOURCE_MAX_CHARS = 30_000
AI_SUMMARY_MAX_TOKENS = 1_200

TRUNCATION_MARKER = "[contenu tronqué pour respecter le budget du contexte IA]"


def json_size(value: Any) -> int:
    """Taille déterministe de la représentation JSON envoyée au fournisseur."""

    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def truncate_text(value: str, maximum: int, marker: str = TRUNCATION_MARKER) -> str:
    """Tronque explicitement un texte, en conservant une marque visible."""

    if len(value) <= maximum:
        return value
    if maximum <= len(marker) + 2:
        return marker[:maximum]
    kept = maximum - len(marker) - 2
    return f"{value[:kept]}\n{marker}"


def messages_content_size(messages: Iterable[dict[str, str]]) -> int:
    return sum(len(message.get("content", "")) for message in messages)


def partition_recent_messages(
    messages: Iterable[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Sépare les messages à résumer de la fenêtre récente conservée.

    La coupe s'effectue au début d'un tour utilisateur afin de ne pas laisser une
    réponse assistant orpheline en tête de fenêtre.
    """

    kept = [
        {"role": str(message["role"]), "content": str(message["content"])}
        for message in messages
    ]
    evicted: list[dict[str, str]] = []
    while (
        len(kept) > AI_RECENT_MESSAGES_MAX
        or messages_content_size(kept) > AI_RECENT_MESSAGES_MAX_CHARS
    ):
        if not kept:
            break
        evicted.append(kept.pop(0))
    while kept and kept[0]["role"] == "assistant":
        evicted.append(kept.pop(0))
    return evicted, kept


def fallback_summary(
    previous_summary: str,
    evicted_messages: Iterable[dict[str, str]],
) -> str:
    """Repli déterministe borné si le résumé par le modèle échoue."""

    sections: list[str] = []
    if previous_summary.strip():
        sections.append(
            "Mémoire cumulative précédente :\n"
            + truncate_text(previous_summary.strip(), AI_CONVERSATION_SUMMARY_MAX_CHARS // 2)
        )
    exchanges = []
    for message in evicted_messages:
        label = "Utilisateur" if message.get("role") == "user" else "Assistant"
        exchanges.append(
            f"- {label} : {truncate_text(str(message.get('content', '')).strip(), 900)}"
        )
    if exchanges:
        sections.append("Échanges anciens condensés :\n" + "\n".join(exchanges))
    return truncate_text(
        "\n\n".join(sections).strip(),
        AI_CONVERSATION_SUMMARY_MAX_CHARS,
    )


def summary_source(previous_summary: str, messages: Iterable[dict[str, str]]) -> str:
    """Source bornée et structurée fournie au modèle de résumé."""

    source_messages = [
        {
            "role": message.get("role"),
            "content": truncate_text(str(message.get("content", "")), 1_500),
        }
        for message in messages
    ]
    payload = {
        "conversation_summary": truncate_text(previous_summary, 6_000),
        "messages_to_merge": source_messages,
    }
    if json_size(payload) <= AI_SUMMARY_SOURCE_MAX_CHARS:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    selected: list[dict[str, str]] = []
    for message in reversed(source_messages):
        candidate = {
            "conversation_summary": payload["conversation_summary"],
            "messages_to_merge": [message, *selected],
            "omitted_old_messages": len(source_messages) - len(selected) - 1,
        }
        if json_size(candidate) > AI_SUMMARY_SOURCE_MAX_CHARS:
            break
        selected.insert(0, message)
    bounded = {
        "conversation_summary": payload["conversation_summary"],
        "messages_to_merge": selected,
        "omitted_old_messages": len(source_messages) - len(selected),
        "notice": "[anciens messages source omis pour respecter le budget du résumé]",
    }
    return json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))


def _collection_total(result: dict[str, Any], key: str, values: list[Any]) -> int | str:
    if key == "columns":
        return len(values)
    for total_key in (
        f"{key}_total",
        "total_count",
        "total",
        "row_count",
        "rowCount",
        "count",
    ):
        value = result.get(total_key)
        if isinstance(value, int) and value >= len(values):
            return value
    if key == "rows" and result.get("truncated") is True:
        return f"au moins {len(values)}"
    return len(values)


def _preview_value(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, str):
        return truncate_text(value, 700)
    if isinstance(value, list):
        return [_preview_value(item, depth=depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        return {
            str(key): _preview_value(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    return value


def compact_tool_result(result: dict[str, Any], maximum: int) -> dict[str, Any]:
    """Produit une copie compacte destinée uniquement au contexte du modèle."""

    maximum = max(500, maximum)
    if json_size(result) <= maximum:
        return result

    collections = {
        key: value
        for key, value in result.items()
        if isinstance(value, list)
    }
    counts = {
        key: {"shown": 0, "total": _collection_total(result, key, value)}
        for key, value in collections.items()
    }
    compact: dict[str, Any] = {
        key: _preview_value(value)
        for key, value in result.items()
        if not isinstance(value, list)
    }
    for key in collections:
        compact[key] = []
    compact["ai_context"] = {
        "truncated": True,
        "notice": "[résultat tronqué pour le contexte IA]",
        "collections": counts,
    }

    for key, values in collections.items():
        for item in values:
            candidate_item = _preview_value(item)
            compact[key].append(candidate_item)
            counts[key]["shown"] = len(compact[key])
            if json_size(compact) > maximum:
                compact[key].pop()
                counts[key]["shown"] = len(compact[key])
                break

    descriptions = [
        f"{details['shown']} éléments transmis sur {details['total']} pour {key}"
        for key, details in counts.items()
    ]
    if descriptions:
        compact["ai_context"]["notice"] = (
            "[résultat tronqué : " + "; ".join(descriptions) + "]"
        )

    while json_size(compact) > maximum:
        populated = [key for key in collections if compact[key]]
        if not populated:
            break
        key = max(populated, key=lambda name: json_size(compact[name][-1]))
        compact[key].pop()
        counts[key]["shown"] = len(compact[key])
        descriptions = [
            f"{details['shown']} éléments transmis sur {details['total']} pour {name}"
            for name, details in counts.items()
        ]
        compact["ai_context"]["notice"] = (
            "[résultat tronqué : " + "; ".join(descriptions) + "]"
        )

    if json_size(compact) <= maximum:
        return compact

    # Des métadonnées ou objets imbriqués peuvent eux-mêmes être très volumineux.
    # Ce dernier repli reste du JSON valide et signale explicitement la perte.
    essential = {
        key: value
        for key, value in compact.items()
        if key in {"ok", "error", "columns", "truncated", "count", "total"}
    }
    preview_budget = max(100, maximum - json_size(essential) - 260)
    essential["preview"] = truncate_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        preview_budget,
    )
    essential["ai_context"] = {
        "truncated": True,
        "notice": "[résultat tronqué : aperçu partiel, données complètes non transmises au modèle]",
        "collections": counts,
    }
    while json_size(essential) > maximum and len(essential["preview"]) > 100:
        essential["preview"] = truncate_text(essential["preview"], len(essential["preview"]) - 100)
    return essential
