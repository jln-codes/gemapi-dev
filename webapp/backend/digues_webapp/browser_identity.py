"""Catalogue public de session : identités logiques, jamais de cible SQL/secrets."""

from fastapi import HTTPException
from psycopg.rows import dict_row

from digues_app.control.auth import token_digest
from digues_app.control.database import open_control_connection


def discover_databases(user, token):
    with open_control_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT meta.instance_id, sess.expires_at
                FROM public.sessions sess JOIN public.users u ON u.id = sess.user_id
                CROSS JOIN public.control_schema_metadata meta
                WHERE sess.token_hash = %s AND u.id = %s AND u.status = 'active'
                  AND sess.revoked_at IS NULL AND sess.expires_at > clock_timestamp()
                  AND sess.auth_version = u.auth_version
                """, (token_digest(token), user.id))
            session = cursor.fetchone()
            if session is None:
                raise HTTPException(401, "Authentification requise.")
            cursor.execute("""
                SELECT b.id AS business_database_id, b.structure_id,
                       s.name AS structure_name, b.display_name, b.slug,
                       b.status, b.data_epoch, r.code AS role_code,
                       ARRAY(SELECT rp.permission_code FROM public.role_permissions rp
                             WHERE rp.role_id = r.id AND rp.scope_kind = 'database'
                             ORDER BY rp.permission_code) AS permissions
                FROM public.memberships m
                JOIN public.business_databases b ON b.id = m.business_database_id
                    AND b.structure_id = m.structure_id
                JOIN public.structures s ON s.id = b.structure_id
                JOIN public.roles r ON r.id = m.role_id AND r.scope_kind = 'database'
                WHERE m.user_id = %s AND m.status = 'active'
                  AND b.status = 'active' AND s.status = 'active'
                  AND EXISTS (SELECT 1 FROM public.role_permissions rp
                              WHERE rp.role_id = r.id AND rp.permission_code = 'business.read')
                ORDER BY s.name, b.display_name, b.id
                """, (user.id,))
            return {**session, 'user_id': user.id, 'databases': cursor.fetchall()}
