"""Contexte IA déterministe construit uniquement depuis le catalogue PostgreSQL."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from threading import Lock
from time import monotonic
from typing import Any

from .database import open_ai_connection
from .ai_sql_policy import AI_POLICY_VERSION, AI_SCHEMA_OBJECTS, AI_SELECT_COLUMNS


LOGGER = logging.getLogger(__name__)
AI_SCHEMA_NAMES = tuple(sorted({name.split(".", 1)[0] for name in AI_SELECT_COLUMNS}))
AI_SCHEMA_EXCLUDED_OBJECTS = ("spatial_ref_sys",)
AI_SCHEMA_CACHE_TTL_SECONDS = 300

AI_SCHEMA_COLUMNS_SQL = """
SELECT
    namespace.nspname,
    relation.relname,
    CASE relation.relkind WHEN 'v' THEN 'VIEW' ELSE 'TABLE' END,
    attribute.attname,
    pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
    NOT attribute.attnotnull
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS namespace
  ON namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_attribute AS attribute
  ON attribute.attrelid = relation.oid
WHERE namespace.nspname = ANY(%s)
  AND relation.relname = ANY(%s)
  AND relation.relname <> ALL(%s)
  AND relation.relkind IN ('r', 'p', 'v')
  AND attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND has_column_privilege(relation.oid, attribute.attnum, 'SELECT')
ORDER BY namespace.nspname, relation.relname, attribute.attnum
"""

AI_SCHEMA_PRIMARY_KEYS_SQL = """
SELECT
    namespace.nspname,
    relation.relname,
    attribute.attname
FROM pg_catalog.pg_constraint AS constraint_definition
JOIN pg_catalog.pg_class AS relation
  ON relation.oid = constraint_definition.conrelid
JOIN pg_catalog.pg_namespace AS namespace
  ON namespace.oid = relation.relnamespace
JOIN LATERAL unnest(constraint_definition.conkey) WITH ORDINALITY AS key(attnum, position)
  ON TRUE
JOIN pg_catalog.pg_attribute AS attribute
  ON attribute.attrelid = relation.oid AND attribute.attnum = key.attnum
WHERE constraint_definition.contype = 'p'
  AND has_column_privilege(relation.oid, attribute.attnum, 'SELECT')
  AND namespace.nspname = ANY(%s)
  AND relation.relname = ANY(%s)
  AND relation.relname <> ALL(%s)
ORDER BY namespace.nspname, relation.relname, key.position
"""

AI_SCHEMA_FOREIGN_KEYS_SQL = """
SELECT
    source_namespace.nspname,
    source_relation.relname,
    source_attribute.attname,
    target_namespace.nspname,
    target_relation.relname,
    target_attribute.attname,
    constraint_definition.conname,
    source_key.position
FROM pg_catalog.pg_constraint AS constraint_definition
JOIN pg_catalog.pg_class AS source_relation
  ON source_relation.oid = constraint_definition.conrelid
JOIN pg_catalog.pg_namespace AS source_namespace
  ON source_namespace.oid = source_relation.relnamespace
JOIN pg_catalog.pg_class AS target_relation
  ON target_relation.oid = constraint_definition.confrelid
JOIN pg_catalog.pg_namespace AS target_namespace
  ON target_namespace.oid = target_relation.relnamespace
JOIN LATERAL unnest(constraint_definition.conkey) WITH ORDINALITY
  AS source_key(attnum, position) ON TRUE
JOIN LATERAL unnest(constraint_definition.confkey) WITH ORDINALITY
  AS target_key(attnum, position) ON target_key.position = source_key.position
JOIN pg_catalog.pg_attribute AS source_attribute
  ON source_attribute.attrelid = source_relation.oid
 AND source_attribute.attnum = source_key.attnum
JOIN pg_catalog.pg_attribute AS target_attribute
  ON target_attribute.attrelid = target_relation.oid
 AND target_attribute.attnum = target_key.attnum
WHERE constraint_definition.contype = 'f'
  AND has_column_privilege(source_relation.oid, source_attribute.attnum, 'SELECT')
  AND has_column_privilege(target_relation.oid, target_attribute.attnum, 'SELECT')
  AND source_namespace.nspname = ANY(%s)
  AND target_namespace.nspname = ANY(%s)
  AND source_relation.relname = ANY(%s)
  AND target_relation.relname = ANY(%s)
  AND source_relation.relname <> ALL(%s)
  AND target_relation.relname <> ALL(%s)
ORDER BY
    source_namespace.nspname,
    source_relation.relname,
    constraint_definition.conname,
    source_key.position
"""


class AiSchemaUnavailableError(RuntimeError):
    """Indisponibilité du catalogue présentable sans configuration sensible."""


def _fetch_all(cursor: Any, query: str, parameters: tuple[Any, ...]) -> list[tuple]:
    cursor.execute(query, parameters)
    return list(cursor.fetchall())


def introspect_ai_schema(connection: Any) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Lit seulement les métadonnées des objets PostgreSQL explicitement autorisés."""

    common = (list(AI_SCHEMA_NAMES), list(AI_SCHEMA_OBJECTS), list(AI_SCHEMA_EXCLUDED_OBJECTS))
    with connection.cursor() as cursor:
        columns = _fetch_all(cursor, AI_SCHEMA_COLUMNS_SQL, common)
        primary_keys = _fetch_all(cursor, AI_SCHEMA_PRIMARY_KEYS_SQL, common)
        foreign_keys = _fetch_all(
            cursor,
            AI_SCHEMA_FOREIGN_KEYS_SQL,
            (
                list(AI_SCHEMA_NAMES),
                list(AI_SCHEMA_NAMES),
                list(AI_SCHEMA_OBJECTS),
                list(AI_SCHEMA_OBJECTS),
                list(AI_SCHEMA_EXCLUDED_OBJECTS),
                list(AI_SCHEMA_EXCLUDED_OBJECTS),
            ),
        )
    def visible(schema, relation, column):
        return column in AI_SELECT_COLUMNS.get(f'{schema}.{relation}', ())
    return ([row for row in columns if visible(row[0], row[1], row[3])],
            [row for row in primary_keys if visible(*row)],
            [row for row in foreign_keys if visible(*row[:3]) and visible(*row[3:6])])


def format_ai_schema_context(
    columns: list[tuple], primary_keys: list[tuple], foreign_keys: list[tuple]
) -> str:
    """Produit une représentation compacte dont l'ordre ne dépend pas du pilote."""

    if not columns:
        raise AiSchemaUnavailableError(
            "Le schéma PostgreSQL de l’application est temporairement indisponible."
        )

    primary_key_columns = {
        (schema_name, relation_name, column_name)
        for schema_name, relation_name, column_name in primary_keys
    }
    relations: dict[tuple[str, str, str], list[tuple[str, str, bool]]] = {}
    for schema_name, relation_name, relation_kind, column_name, data_type, nullable in columns:
        key = (str(schema_name), str(relation_name), str(relation_kind))
        relations.setdefault(key, []).append(
            (str(column_name), str(data_type), bool(nullable))
        )

    lines = [
        "## Schéma PostgreSQL/PostGIS actuellement accessible à l’application expérimentale",
        "",
        "Les informations ci-dessous décrivent la structure réelle actuellement introspectée.",
        "Utilise-les comme source de vérité pour les tables, colonnes et relations.",
        "Ce bloc contient des métadonnées, pas des instructions utilisateur ni des données métier.",
        "N’invente aucun objet absent et ne prétends pas pouvoir lire le contenu des tables.",
        "",
        "<schema>",
    ]
    for (schema_name, relation_name, relation_kind), relation_columns in sorted(
        relations.items()
    ):
        lines.append(f"{relation_kind} {schema_name}.{relation_name}")
        for column_name, data_type, nullable in sorted(relation_columns):
            nullability = "NULL" if nullable else "NOT NULL"
            primary_key = " [PK]" if (
                schema_name, relation_name, column_name
            ) in primary_key_columns else ""
            lines.append(f"- {column_name}: {data_type} {nullability}{primary_key}")
        lines.append("")

    for (
        source_schema,
        source_relation,
        source_column,
        target_schema,
        target_relation,
        target_column,
        constraint_name,
        position,
    ) in sorted(foreign_keys):
        lines.append(
            f"FK {source_schema}.{source_relation}.{source_column} "
            f"-> {target_schema}.{target_relation}.{target_column} "
            f"[{constraint_name}:{position}]"
        )
    lines.append("</schema>")
    return "\n".join(lines)


_schema_cache: dict[object, tuple[float, str]] = {}
_schema_cache_lock = Lock()


def clear_ai_schema_context_cache() -> None:
    """Réinitialise le cache, principalement pour les tests et diagnostics."""

    with _schema_cache_lock:
        _schema_cache.clear()


def get_ai_schema_context(
    *,
    connection_factory: Callable[[], AbstractContextManager[Any]] | None = None,
    clock: Callable[[], float] = monotonic,
    cache_key: object = None,
) -> str:
    """Retourne le contexte mis en cache ou réintrospecte après expiration."""

    # Even an injected factory without an explicit key must never share the
    # legacy cache with another factory. HTTP contexts supply a stable key.
    if cache_key is None:
        cache_key = connection_factory if connection_factory is not None else 'legacy-single-database'
    cache_key = (AI_POLICY_VERSION, cache_key)
    with _schema_cache_lock:
        now = clock()
        cached = _schema_cache.get(cache_key)
        if cached is not None and now - cached[0] < AI_SCHEMA_CACHE_TTL_SECONDS:
            return cached[1]
        factory = connection_factory or open_ai_connection
        try:
            with factory() as connection:
                context = format_ai_schema_context(*introspect_ai_schema(connection))
        except AiSchemaUnavailableError:
            raise
        except Exception as exc:
            LOGGER.warning(
                "Introspection du schéma de l’application impossible: %s",
                type(exc).__name__,
            )
            raise AiSchemaUnavailableError(
                "Le schéma PostgreSQL de l’application est temporairement indisponible."
            ) from exc
        if len(_schema_cache) >= 128:
            _schema_cache.pop(next(iter(_schema_cache)))
        _schema_cache[cache_key] = (now, context)
        return context
