"""Accès PostgreSQL du prototype web, sans état ni secret côté navigateur."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from typing import Any

from digues_app.crs import BusinessCRS, BusinessCRSError, qualified_business_crs





class WebDatabaseError(RuntimeError):
    """Erreur de lecture de la base présentable par l'API sans secret."""


class BusinessMaintenanceError(RuntimeError):
    """Refus volontaire vérifié, distinct des erreurs SQL et des permissions."""

    code = "BUSINESS_DATABASE_MAINTENANCE"
    message = "L’instance est temporairement en maintenance."


class WebDatabaseConfigurationError(ValueError):
    """Configuration PostgreSQL absente ou incohérente côté webapp."""


class WebDatabaseSchemaError(RuntimeError):
    """Schéma PostgreSQL incomplet pour les sessions webapp."""


class BusinessCRSMetadataError(WebDatabaseError):
    """La base sélectionnée ne publie pas un CRS métier fiable."""


@dataclass(frozen=True)
class PostgreSQLConfig:
    """Connexion fixée par la configuration privée de l’instance."""

    dsn: str
    connect_timeout: int = 10
    schema: str = "gemapi"

    @classmethod
    def from_env(cls) -> "PostgreSQLConfig":
        dsn = os.getenv("DATABASE_URL", "").strip()
        if not dsn:
            raise WebDatabaseConfigurationError("DATABASE_URL doit être renseignée.")
        schema = os.getenv("DATABASE_SCHEMA", "gemapi")
        if (not schema or len(schema.encode()) > 63 or "\x00" in schema
                or schema.startswith("pg_") or schema == "information_schema"):
            raise WebDatabaseConfigurationError("DATABASE_SCHEMA invalide.")
        return cls(dsn, schema=schema)

    def connect_kwargs(self, *, autocommit: bool = True) -> dict[str, Any]:
        return {
            "conninfo": self.dsn,
            "connect_timeout": self.connect_timeout,
            "autocommit": autocommit,
        }


@dataclass(frozen=True)
class PostgreSQLExtensionStatus:
    postgis_version: str
    postgis_schema: str
    pgcrypto_version: str
    pgcrypto_schema: str


def quote_identifier(identifier: str) -> str:
    """Protège un identifiant PostgreSQL utilisé dans le search_path."""

    if not identifier or "\x00" in identifier:
        raise ValueError("L’identifiant PostgreSQL est invalide.")
    return '"' + identifier.replace('"', '""') + '"'


def extension_search_path(*schemas: str | None) -> str:
    """Construit le search_path de session avec public et les extensions."""

    selected = ["pg_catalog", "public"]
    for schema in schemas:
        if schema is None or schema in selected:
            continue
        selected.append(schema)
    return ", ".join(
        schema if schema in {"pg_catalog", "public"} else quote_identifier(schema)
        for schema in selected
    )


def read_extension_status(cursor: Any) -> PostgreSQLExtensionStatus:
    """Détecte dynamiquement les schémas d'installation PostGIS et pgcrypto."""

    cursor.execute(
        """
        SELECT e.extname, e.extversion, n.nspname
        FROM pg_extension AS e
        JOIN pg_namespace AS n ON n.oid = e.extnamespace
        WHERE e.extname = ANY(%s)
        """,
        (["postgis", "pgcrypto"],),
    )
    extensions = {
        str(name): (str(version), str(schema))
        for name, version, schema in cursor.fetchall()
    }
    missing = [name for name in ("postgis", "pgcrypto") if name not in extensions]
    if missing:
        raise WebDatabaseSchemaError(
            "Des extensions PostgreSQL requises sont absentes : "
            + ", ".join(missing)
            + "."
        )
    postgis_version, postgis_schema = extensions["postgis"]
    pgcrypto_version, pgcrypto_schema = extensions["pgcrypto"]
    return PostgreSQLExtensionStatus(
        postgis_version=postgis_version,
        postgis_schema=postgis_schema,
        pgcrypto_version=pgcrypto_version,
        pgcrypto_schema=pgcrypto_schema,
    )


def configure_extension_search_path(cursor: Any) -> PostgreSQLExtensionStatus:
    """Configure la session courante pour résoudre PostGIS et pgcrypto."""

    status = read_extension_status(cursor)
    cursor.execute(
        "SELECT set_config('search_path', %s, false)",
        (extension_search_path(status.postgis_schema, status.pgcrypto_schema),),
    )
    return status


def read_business_crs(connection: Any) -> BusinessCRS:
    """Lit le CRS singleton de la connexion métier déjà autorisée."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT srid FROM technique.metadonnees_base ORDER BY singleton"
            )
            rows = cursor.fetchall()
    except Exception:
        raise BusinessCRSMetadataError(
            "Métadonnée CRS métier absente ou illisible."
        ) from None
    if len(rows) != 1:
        raise BusinessCRSMetadataError(
            "Métadonnée CRS métier absente ou non unique."
        )
    try:
        return qualified_business_crs(rows[0][0])
    except (BusinessCRSError, TypeError, ValueError):
        raise BusinessCRSMetadataError(
            "SRID métier invalide ou non qualifié dans technique.metadonnees_base."
        ) from None


def business_crs_payload(crs: BusinessCRS) -> dict[str, str | int]:
    """Expose le contrat public commun d'un CRS métier déjà validé."""

    return {
        "authority": crs.authority,
        "srid": crs.srid,
        "code": crs.code,
        "name": crs.name,
        "label": f"{crs.name} — {crs.code}",
    }


def configure_sql_search_path(cursor: Any) -> None:
    """Limite le chemin SQL/IA à pg_catalog, public et au schéma PostGIS."""

    cursor.execute(
        "SELECT namespace.nspname FROM pg_catalog.pg_extension AS extension "
        "JOIN pg_catalog.pg_namespace AS namespace "
        "ON namespace.oid = extension.extnamespace "
        "WHERE extension.extname = 'postgis'"
    )
    row = cursor.fetchone()
    if row is None:
        raise WebDatabaseSchemaError("L’extension PostGIS est absente.")
    cursor.execute(
        "SELECT set_config('search_path', %s, false)",
        (extension_search_path(str(row[0])),),
    )


def validate_sql_role_security(cursor: Any) -> None:
    """Refuse une DSN SQL/IA dont le rôle effectif reste privilégié."""

    cursor.execute(
        "SELECT role.rolname, session_user, role.rolcanlogin, role.rolsuper, "
        "role.rolcreatedb, role.rolcreaterole, role.rolreplication, "
        "role.rolbypassrls, current_setting('transaction_read_only'), "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members AS membership "
        "WHERE membership.member = role.oid), "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_class AS relation "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
        "WHERE namespace.nspname NOT LIKE 'pg_temp_%' "
        "AND namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND relation.relkind IN ('r', 'p', 'v', 'm', 'f') AND ("
        "has_table_privilege(role.rolname, relation.oid, 'INSERT') OR "
        "has_table_privilege(role.rolname, relation.oid, 'UPDATE') OR "
        "has_table_privilege(role.rolname, relation.oid, 'DELETE') OR "
        "has_table_privilege(role.rolname, relation.oid, 'TRUNCATE') OR "
        "has_table_privilege(role.rolname, relation.oid, 'TRIGGER'))), "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_class AS sequence "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = sequence.relnamespace "
        "WHERE namespace.nspname NOT LIKE 'pg_temp_%' "
        "AND sequence.relkind = 'S' AND ("
        "has_sequence_privilege(role.rolname, sequence.oid, 'USAGE') OR "
        "has_sequence_privilege(role.rolname, sequence.oid, 'UPDATE'))), "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_namespace AS namespace "
        "WHERE namespace.nspname NOT LIKE 'pg_temp_%' "
        "AND has_schema_privilege(role.rolname, namespace.oid, 'CREATE')), "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_namespace AS namespace "
        "WHERE namespace.nspname IN ('auth', 'realtime', 'storage', 'vault') "
        "AND has_schema_privilege(role.rolname, namespace.oid, 'USAGE')), "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_proc AS function "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = function.pronamespace "
        "WHERE function.prosecdef "
        "AND namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend AS dependency "
        "JOIN pg_catalog.pg_extension AS extension "
        "ON extension.oid = dependency.refobjid "
        "WHERE dependency.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass "
        "AND dependency.objid = function.oid AND dependency.deptype = 'e' "
        "AND extension.extname = 'postgis') "
        "AND has_schema_privilege(role.rolname, namespace.oid, 'USAGE') "
        "AND has_function_privilege(role.rolname, function.oid, 'EXECUTE')) "
        "FROM pg_catalog.pg_roles AS role WHERE role.rolname = current_user"
    )
    row = cursor.fetchone()
    if row is None:
        raise WebDatabaseConfigurationError("Le rôle SQL/IA courant est introuvable.")
    (
        current_role,
        session_role,
        can_login,
        superuser,
        create_database,
        create_role,
        replication,
        bypass_rls,
        transaction_read_only,
        has_memberships,
        has_write_privilege,
        has_sequence_write_privilege,
        has_schema_create,
        has_forbidden_schema_usage,
        has_security_definer_function,
    ) = row
    insecure = (
        current_role != session_role
        or not can_login
        or superuser
        or create_database
        or create_role
        or replication
        or bypass_rls
        or transaction_read_only != "on"
        or has_memberships
        or has_write_privilege
        or has_sequence_write_privilege
        or has_schema_create
        or has_forbidden_schema_usage
        or has_security_definer_function
    )
    if insecure:
        raise WebDatabaseConfigurationError(
            "Le rôle configuré pour le SQL libre et l’IA n’est pas suffisamment restreint."
        )


@contextmanager
def _connection(config, *, read_only, prepare):
    import psycopg

    kwargs = config.connect_kwargs()
    if read_only:
        kwargs["options"] = "-c default_transaction_read_only=on"
    try:
        connection = psycopg.connect(**kwargs)
        try:
            with connection.cursor() as cursor:
                prepare(cursor)
        except Exception:
            connection.close()
            raise
    except WebDatabaseSchemaError:
        raise
    except Exception:
        raise WebDatabaseError("Connexion PostgreSQL indisponible ou non conforme.") from None
    try:
        yield connection
    finally:
        connection.close()


# Empreinte des relations et colonnes v0.6.1 issue de l'installation témoin.
# Les OID de types dépendent de chaque installation de PostGIS.
MODEL_SIGNATURE = "0b6e72503516fe62a10bdd80ee76f4cb"
MODEL_STRUCTURE = """
    SELECT md5(string_agg(concat_ws('|', c.relkind, c.relname, a.attnum, a.attname,
           t.typname, a.atttypmod, a.attnotnull, a.attgenerated), E'\\n'
           ORDER BY c.relname COLLATE "C", a.attnum)),
           bool_and(has_table_privilege(c.oid, 'SELECT'))
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
    JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
    WHERE n.nspname=%s AND c.relkind IN ('r','v','p','m','f')
"""


def prepare_model(cursor, schema):
    """Diagnostic en lecture seule, également utilisé par les connexions métier."""
    cursor.execute("SET search_path = pg_catalog, pg_temp")
    cursor.execute("""
        SELECT x.nspname, n.oid, has_schema_privilege(n.oid,'USAGE'),
               has_schema_privilege(x.oid,'USAGE')
        FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace x ON x.oid=e.extnamespace
        LEFT JOIN pg_catalog.pg_namespace n ON n.nspname=%s WHERE e.extname='postgis'
    """, (schema,))
    status = cursor.fetchone()
    if not status:
        raise WebDatabaseSchemaError("Extension PostGIS absente.")
    extension, oid, usage, extension_usage = status
    if oid is None:
        raise WebDatabaseSchemaError("Schéma applicatif absent.")
    if not usage or not extension_usage:
        raise WebDatabaseSchemaError("Droit USAGE manquant pour le modèle ou PostGIS.")
    cursor.execute(MODEL_STRUCTURE, (schema,))
    fingerprint, readable = cursor.fetchone()
    if fingerprint != MODEL_SIGNATURE:
        raise WebDatabaseSchemaError("Structure du modèle incompatible avec la v0.6.1.")
    if not readable:
        raise WebDatabaseSchemaError("Droit de consultation du modèle incomplet.")
    cursor.execute("SELECT pg_catalog.set_config('search_path', %s, false)",
                   (", ".join(map(quote_identifier, ("pg_catalog", extension, schema, "pg_temp"))),))


def _model_connection(read_only):
    config = PostgreSQLConfig.from_env()
    return _connection(config, read_only=read_only, prepare=lambda c: prepare_model(c, config.schema))


def open_read_connection():
    return _model_connection(True)


def open_write_connection():
    return _model_connection(False)


def _open_restricted_connection(variable, validate_role):
    dsn = os.getenv(variable, "").strip()
    if not dsn:
        raise WebDatabaseConfigurationError(f"{variable} doit être renseignée.")

    def prepare(cursor):
        validate_role(cursor)
        configure_sql_search_path(cursor)

    return _connection(PostgreSQLConfig(dsn), read_only=True, prepare=prepare)


def open_sql_connection():
    return _open_restricted_connection("DATABASE_READONLY_URL", validate_sql_role_security)


def open_ai_connection():
    return _open_restricted_connection("DATABASE_AI_READONLY_URL", validate_ai_role_security)


def validate_ai_role_security(cursor):
    from .ai_sql_policy import validate_ai_select_security
    validate_sql_role_security(cursor)
    validate_ai_select_security(cursor)
    cursor.execute("""SELECT
        has_database_privilege(current_user,current_database(),'CREATE'),
        has_database_privilege(current_user,current_database(),'TEMPORARY'),
        EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datallowconn
            AND datname<>current_database() AND has_database_privilege(current_user,oid,'CONNECT')),
        EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname IN ('dblink','postgres_fdw')),
        EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_server),
        EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema')
              AND c.relkind IN ('r','p','v','m','f') AND
              (has_any_column_privilege(c.oid,'INSERT') OR has_any_column_privilege(c.oid,'UPDATE')))
        """)
    if any(cursor.fetchone()):
        raise WebDatabaseConfigurationError('Cloisonnement du rôle IA insuffisant.')


def get_connection() -> Iterator[Any]:
    """Connexion des endpoints strictement en lecture seule."""

    with open_read_connection() as connection:
        yield connection


def get_write_connection() -> Iterator[Any]:
    """Connexion d'écriture réservée aux mutations contrôlées par PostGIS."""

    with open_write_connection() as connection:
        yield connection


def get_sql_connection() -> Iterator[Any]:
    """Dépendance FastAPI réservée à l'éditeur SQL libre."""

    with open_sql_connection() as connection:
        yield connection


if __name__ == "__main__":
    try:
        with open_read_connection():
            print("Modèle v0.6.1 et droits de lecture disponibles.")
    except (WebDatabaseConfigurationError, WebDatabaseError, WebDatabaseSchemaError) as error:
        raise SystemExit(str(error)) from None
