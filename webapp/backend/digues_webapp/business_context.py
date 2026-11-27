"""Autorisation du catalogue, sans connexion métier ni état de base active."""

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from psycopg.rows import dict_row

from digues_app.control.auth import SESSION_IDENTITY_SQL, UserIdentity, token_digest
from digues_app.control.database import (
    open_control_connection,
)
from .auth import AuthSettings, get_auth_settings, require_authenticated_user, require_same_origin


@dataclass(frozen=True, slots=True)
class BusinessContext:
    user_id: UUID
    structure_id: UUID
    business_database_id: UUID
    permissions: frozenset[str]
    auth_version: int
    permission_revision: str
    cluster_ref: str
    database_name: str
    connection_profile_ref: str
    media_namespace: UUID
    data_epoch: UUID
    schema_version: str | None
    role_code: str = ''

    @property
    def schema_cache_key(self):
        return (self.business_database_id, self.schema_version, self.data_epoch,
                self.connection_profile_ref, self.permission_revision,
                tuple(sorted(self.permissions)))


# The same joins and revision construction serve ordinary business requests and
# deferred business requests. No superadmin bypass and no claims from the browser.
_CONTEXT_SQL = """
    SELECT b.id AS business_database_id, b.structure_id, b.cluster_ref,
           b.database_name, b.connection_profile_ref, b.media_namespace,
           b.data_epoch, b.schema_version,
           m.updated_at AS membership_revision, r.id AS role_id, r.code AS role_code,
           r.version AS role_version, r.updated_at AS role_revision,
           ARRAY(SELECT rp.permission_code FROM public.role_permissions rp
                 WHERE rp.role_id = r.id AND rp.scope_kind = 'database'
                 ORDER BY rp.permission_code) AS permissions
    FROM public.memberships m
    JOIN public.business_databases b ON b.id = m.business_database_id
        AND b.structure_id = m.structure_id
    JOIN public.structures s ON s.id = b.structure_id
    JOIN public.roles r ON r.id = m.role_id AND r.scope_kind = 'database'
    WHERE m.user_id = identity.id AND b.id = %s
      AND m.status = 'active' AND b.status = 'active' AND s.status = 'active'
"""


def _context_from_row(row, user_id, permission):
    if (row is None or row['business_database_id'] is None or not row['permissions']
            or permission not in row['permissions'] or 'business.read' not in row['permissions']):
        raise HTTPException(403, "Accès métier refusé.")
    row = dict(row)
    permissions = frozenset(row.pop('permissions'))
    revision = sha256(repr(tuple(row.pop(key) for key in (
        'membership_revision', 'role_id', 'role_version', 'role_revision'
    )) + tuple(sorted(permissions))).encode()).hexdigest()
    return BusinessContext(user_id=user_id, permissions=permissions,
                           permission_revision=revision, **row)


def resolve_business_context(user: UserIdentity, business_database_id: UUID,
                             permission: str, *, session_token: str) -> BusinessContext:
    """Live session/membership read, also for deferred HTTP work."""
    digest = token_digest(session_token)
    if digest is None:
        raise HTTPException(401, "Authentification requise.")
    with open_control_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                'WITH identity AS (' + SESSION_IDENTITY_SQL + ') '
                'SELECT identity.auth_version, business.* FROM identity '
                'JOIN LATERAL (' + _CONTEXT_SQL + ') business ON true '
                'WHERE identity.id = %s', (digest, business_database_id, user.id))
            row = cursor.fetchone()
    return _context_from_row(row, user.id, permission)


def check_client_context(context, request, instance_id):
    """Client assertions only; authority always comes from the live SQL read."""
    expected = request.query_params
    if expected.get('context_offline_write') == '1' and 'offline.write' not in context.permissions:
        raise HTTPException(403, 'Écriture hors ligne non autorisée.')
    if any(key in expected for key in ('context_user_id', 'context_data_epoch', 'context_instance_id')):
        if (expected.get('context_user_id') != str(context.user_id)
                or expected.get('context_data_epoch') != str(context.data_epoch)
                or expected.get('context_instance_id') != str(instance_id)):
            raise HTTPException(409, 'Contexte navigateur périmé ; reconnexion requise.')


def require_business_permission(permission: str):
    def dependency(business_database_id: UUID, request: Request,
                   user: UserIdentity = Depends(require_authenticated_user),
                   settings: AuthSettings = Depends(get_auth_settings)) -> BusinessContext:
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            require_same_origin(request, settings)
        context = resolve_business_context(user, business_database_id, permission,
                                           session_token=request.cookies.get(settings.cookie_name, ''))
        # Assertions d'origine du client, jamais des autorisations. Elles ferment
        # la course entre un replay et le changement de cookie dans un autre onglet.
        expected = request.query_params
        instance = None
        if any(key in expected for key in ('context_user_id', 'context_data_epoch', 'context_instance_id')):
            with open_control_connection() as connection:
                instance = connection.execute('SELECT instance_id FROM public.control_schema_metadata').fetchone()[0]
        check_client_context(context, request, instance)
        return context
    return dependency


def business_connection_dependency(permission: str, *, write: bool = False, sql: bool = False,
                                   offline_prepare: bool = False):
    from .database import (open_read_connection, open_write_connection,
                           open_sql_connection, open_offline_prepare_connection)

    if sum((write, sql, offline_prepare)) > 1:
        raise ValueError('Une dépendance métier ne peut avoir plusieurs profils.')

    def dependency(context: BusinessContext = Depends(require_business_permission(permission))):
        factory = (open_sql_connection if sql else open_offline_prepare_connection if offline_prepare
                   else open_write_connection if write else open_read_connection)
        with factory(context) as connection:
            if not write:
                yield connection
                return
            from digues_app.control.contact_associations import verified_contact
            with open_control_connection() as control:
                # Keep the association/membership read lock until the business
                # request finishes. Recheck Contact inside the mutation below.
                contact_id = verified_contact(control, context.user_id,
                                              context.business_database_id)
                connection.execute("SELECT set_config('digues.actor_checked', 'on', false)")
                connection.execute("SELECT set_config('digues.actor_contact_id', %s, false)",
                                   (str(contact_id) if contact_id else '',))
                yield connection
    return dependency


get_business_read_connection = business_connection_dependency('business.read')
get_business_create_connection = business_connection_dependency('business.create', write=True)
get_business_update_connection = business_connection_dependency('business.update', write=True)
get_business_delete_connection = business_connection_dependency('business.delete', write=True)
get_business_sql_connection = business_connection_dependency('sql.read', sql=True)


def revalidated_factory(context, user, session_token, permission, *, sql=False, ai=False):
    """Capture explicite et immuable, aussi pour SSE et outils IA hors dépendances."""
    from .database import (
        open_ai_connection,
        open_read_connection,
        open_sql_connection,
        open_write_connection,
    )

    if ai != (permission == 'ai.read') or (ai and sql):
        raise ValueError('Une tâche IA exige exclusivement sa factory IA.')

    def validate():
        fresh = resolve_business_context(user, context.business_database_id, permission,
                                         session_token=session_token)
        if fresh != context:
            raise HTTPException(403, 'Contexte métier modifié ; nouvelle requête requise.')

    @contextmanager
    def factory():
        validate()
        opener = open_ai_connection if ai else open_sql_connection if sql else open_read_connection
        with opener(context) as connection:
            yield connection

    if ai:
        @contextmanager
        def hydrometry_cache_factory():
            validate()
            with open_write_connection(context) as connection:
                yield connection

        factory.hydrometry_cache_factory = hydrometry_cache_factory
    return factory, validate
