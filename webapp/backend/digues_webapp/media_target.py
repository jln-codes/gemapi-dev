"""Explicit targets for media HTTP dependencies and operator-only maintenance."""
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from psycopg.rows import dict_row

from digues_app.control.database import open_control_connection
from .business_context import BusinessContext, require_business_permission
from .database import get_connection, get_write_connection, open_read_connection, open_write_connection


@dataclass(frozen=True, slots=True)
class MediaMaintenanceTarget:
    business_database_id: UUID
    media_namespace: UUID
    database_name: str
    cluster_ref: str
    connection_profile_ref: str
    schema_version: str | None
    data_epoch: UUID


def resolve_media_maintenance_target(identifier: UUID) -> MediaMaintenanceTarget:
    # This is an operator CLI, not a public API: authority is local access to the
    # control connection and private profile file, never a fabricated web user.
    with open_control_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT b.id AS business_database_id, b.media_namespace,
                       b.database_name, b.cluster_ref, b.connection_profile_ref,
                       b.schema_version, b.data_epoch
                FROM public.business_databases b
                JOIN public.structures s ON s.id=b.structure_id
                WHERE b.id=%s AND b.status='active' AND s.status='active'
            """, (identifier,))
            row = cursor.fetchone()
    if row is None:
        raise ValueError("Base de maintenance absente ou inactive.")
    return MediaMaintenanceTarget(**row)


def legacy_context():
    return None


def media_route_dependencies(contextual: bool, permission: str, *, write=False):
    """The SAME cached dependency supplies SQL and storage: no second resolution."""
    if not contextual:
        return legacy_context, get_write_connection if write else get_connection
    context_dependency = require_business_permission(permission)

    def connection_dependency(context: BusinessContext = Depends(context_dependency)):
        with (open_write_connection if write else open_read_connection)(context) as connection:
            yield connection
    return context_dependency, connection_dependency
