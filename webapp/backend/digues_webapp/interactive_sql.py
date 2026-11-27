"""Exécution SQL interactive, indépendante du circuit de l'assistant IA."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from .readonly_sql import (
    READONLY_SQL_FETCH_SIZE,
    READONLY_SQL_MAX_RESULT_BYTES,
    READONLY_SQL_MAX_ROWS,
    READONLY_SQL_STATEMENT_TIMEOUT_MS,
    ReadonlySqlValidationError,
    serialize_sql_value,
    serialized_sql_row_size,
    validate_readonly_sql,
)


LOGGER = logging.getLogger(__name__)
INTERACTIVE_SQL_MAX_CHARS = 50_000


class InteractiveSqlValidationError(ValueError):
    """Requête utilisateur refusée avant exécution."""


class InteractiveSqlExecutionError(RuntimeError):
    """Erreur PostgreSQL nettoyée pour l'interface SQL."""

    def __init__(
        self,
        message: str,
        *,
        sqlstate: str | None = None,
        position: int | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.position = position
        self.timed_out = timed_out

    def public_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"message": str(self)}
        if self.sqlstate:
            detail["sqlstate"] = self.sqlstate
        if self.position is not None:
            detail["position"] = self.position
        return detail


def validate_interactive_sql(sql: str) -> str:
    """Applique les limites propres à l'éditeur puis la politique read-only."""

    if not isinstance(sql, str) or not sql.strip():
        raise InteractiveSqlValidationError("Une requête SQL non vide est requise.")
    if len(sql) > INTERACTIVE_SQL_MAX_CHARS:
        raise InteractiveSqlValidationError(
            "La requête SQL dépasse la limite de 50 000 caractères."
        )
    try:
        return validate_readonly_sql(sql)
    except ReadonlySqlValidationError as exc:
        raise InteractiveSqlValidationError(str(exc)) from exc


def _postgres_error(exc: Exception) -> InteractiveSqlExecutionError:
    sqlstate = getattr(exc, "sqlstate", None)
    diagnostics = getattr(exc, "diag", None)
    position_value = getattr(diagnostics, "statement_position", None)
    try:
        position = int(position_value) if position_value is not None else None
    except (TypeError, ValueError):
        position = None

    if sqlstate == "57014":
        return InteractiveSqlExecutionError(
            "La requête SQL a dépassé le délai autorisé.",
            sqlstate=sqlstate,
            position=position,
            timed_out=True,
        )

    messages_by_sqlstate = {
        "42601": "La syntaxe de la requête SQL est invalide.",
        "42703": "La requête SQL utilise une colonne inconnue.",
        "42P01": "La requête SQL utilise une table inconnue.",
        "42501": "La requête SQL n’est pas autorisée.",
        "22P02": "Une valeur de la requête SQL est invalide.",
    }
    public_message = messages_by_sqlstate.get(
        sqlstate,
        "La requête SQL a échoué.",
    )
    return InteractiveSqlExecutionError(
        public_message,
        sqlstate=sqlstate if isinstance(sqlstate, str) else None,
        position=position,
    )


def execute_interactive_query(
    connection: Any,
    sql: str,
    *,
    clock: Callable[[], float] = monotonic,
) -> dict[str, Any]:
    """Exécute une requête utilisateur dans une transaction réellement read-only."""

    validated_sql = validate_interactive_sql(sql)
    started = clock()
    row_count = 0
    truncated = False
    try:
        with connection.transaction():
            with connection.cursor() as control_cursor:
                control_cursor.execute("SET TRANSACTION READ ONLY")
                control_cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{READONLY_SQL_STATEMENT_TIMEOUT_MS}ms",),
                )

            cursor_name = f"interactive_sql_{uuid4().hex}"
            with connection.cursor(name=cursor_name) as cursor:
                cursor.execute(validated_sql)
                columns = [column.name for column in (cursor.description or ())]
                rows: list[list[Any]] = []
                result_bytes = serialized_sql_row_size(columns)
                while True:
                    remaining = READONLY_SQL_MAX_ROWS - len(rows)
                    fetch_size = min(READONLY_SQL_FETCH_SIZE, remaining + 1)
                    batch = cursor.fetchmany(fetch_size)
                    if not batch:
                        break
                    for raw_row in batch:
                        if len(rows) >= READONLY_SQL_MAX_ROWS:
                            truncated = True
                            break
                        row = [serialize_sql_value(value) for value in raw_row]
                        row_size = serialized_sql_row_size(row)
                        if result_bytes + row_size > READONLY_SQL_MAX_RESULT_BYTES:
                            truncated = True
                            break
                        rows.append(row)
                        result_bytes += row_size
                    if truncated:
                        break

                row_count = len(rows)
    except Exception as exc:
        error = _postgres_error(exc)
        LOGGER.warning(
            "Requête SQL interactive échouée "
            "(duration_ms=%d, sqlstate=%s, timeout=%s, error=%s)",
            round((clock() - started) * 1_000),
            error.sqlstate or "unknown",
            error.timed_out,
            exc,
        )
        raise error from exc

    duration_ms = max(0, round((clock() - started) * 1_000))
    LOGGER.info(
        "Requête SQL interactive réussie (duration_ms=%d, rows=%d, truncated=%s)",
        duration_ms,
        row_count,
        truncated,
    )
    return {
        "columns": columns,
        "rows": rows,
        "rowCount": row_count,
        "durationMs": duration_ms,
        "truncated": truncated,
    }
