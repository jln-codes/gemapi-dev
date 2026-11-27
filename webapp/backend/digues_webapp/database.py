"""Accès PostgreSQL du prototype web, sans état ni secret côté navigateur."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from typing import Any

from digues_app.crs import BusinessCRS, BusinessCRSError, qualified_business_crs
from digues_app.control.environment import postgres_environment_value




class WebDatabaseError(RuntimeError):
    """Erreur de lecture de la base présentable par l'API sans secret."""


class BusinessMaintenanceError(RuntimeError):
    """Refus volontaire vérifié, distinct des erreurs SQL et des permissions."""

    code = "BUSINESS_DATABASE_MAINTENANCE"
    message = "Cette base est temporairement en maintenance. Le suivi de l’import reste accessible aux administrateurs autorisés."


class WebDatabaseConfigurationError(ValueError):
    """Configuration PostgreSQL absente ou incohérente côté webapp."""


class WebDatabaseSchemaError(RuntimeError):
    """Schéma PostgreSQL incomplet pour les sessions webapp."""


class BusinessCRSMetadataError(WebDatabaseError):
    """La base sélectionnée ne publie pas un CRS métier fiable."""


@dataclass(frozen=True)
class PostgreSQLConfig:
    """Configuration PostgreSQL autonome du backend web.

    DATABASE_URL reste prioritaire. Les variables DIGUES_POSTGRES_* séparées
    ne sont lues que lorsqu'elles contribuent réellement à la connexion.
    """

    dsn: str | None = None
    host: str = "127.0.0.1"
    port: int = 5432
    database: str = "digues_app"
    user: str = "postgres"
    password: str | None = None
    connect_timeout: int = 10

    @classmethod
    def from_env(cls) -> "PostgreSQLConfig":
        dsn = os.getenv("DATABASE_URL") or None
        try:
            common = {
                "connect_timeout": int(postgres_environment_value("CONNECT_TIMEOUT", default="10")),
            }
            separate = {} if dsn else {
                "host": postgres_environment_value("HOST", default="127.0.0.1"),
                "port": int(postgres_environment_value("PORT", default="5432")),
                "database": postgres_environment_value("DATABASE", default="digues_app"),
                "user": postgres_environment_value("USER", default="postgres"),
                "password": postgres_environment_value("PASSWORD") or None,
            }
            config = cls(dsn=dsn, **common, **separate)
        except (TypeError, ValueError):
            raise WebDatabaseConfigurationError(
                "Configuration PostgreSQL invalide ou contradictoire."
            ) from None
        config.validate()
        return config

    def validate(self) -> None:
        if not self.dsn and (not self.host or not self.database or not self.user):
            raise WebDatabaseConfigurationError(
                "L’hôte, la base et l’utilisateur PostgreSQL sont obligatoires."
            )
        if not self.dsn and not 1 <= self.port <= 65_535:
            raise WebDatabaseConfigurationError("Le port PostgreSQL est invalide.")
        if self.connect_timeout <= 0:
            raise WebDatabaseConfigurationError(
                "Le délai de connexion PostgreSQL doit être positif."
            )

    def connect_kwargs(self, *, autocommit: bool = True) -> dict[str, Any]:
        common: dict[str, Any] = {
            "connect_timeout": self.connect_timeout,
            "autocommit": autocommit,
        }
        if self.dsn:
            return {"conninfo": self.dsn, **common}
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            **common,
        }

    @property
    def safe_location(self) -> str:
        if self.dsn:
            return "DSN fourni par DATABASE_URL"
        return f"{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class PostgreSQLReadonlyConfig:
    """Configuration legacy obligatoire du rôle dédié au SQL interactif."""

    dsn: str
    connect_timeout: int = 10

    @classmethod
    def from_env(cls) -> "PostgreSQLReadonlyConfig":
        dsn = os.getenv("DATABASE_READONLY_URL", "").strip()
        if not dsn:
            raise WebDatabaseConfigurationError(
                "DATABASE_READONLY_URL est obligatoire pour le SQL interactif legacy."
            )
        try:
            config = cls(
                dsn=dsn,
                connect_timeout=int(postgres_environment_value("CONNECT_TIMEOUT", default="10")),
            )
        except (TypeError, ValueError):
            raise WebDatabaseConfigurationError(
                "Configuration PostgreSQL readonly invalide ou contradictoire."
            ) from None
        if config.connect_timeout <= 0:
            raise WebDatabaseConfigurationError(
                "Le délai de connexion PostgreSQL doit être positif."
            )
        return config

    def connect_kwargs(self, *, autocommit: bool = True) -> dict[str, Any]:
        return {
            "conninfo": self.dsn,
            "connect_timeout": self.connect_timeout,
            "autocommit": autocommit,
        }

    @property
    def safe_location(self) -> str:
        return "DSN fourni par DATABASE_READONLY_URL"


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


def _connection(*, read_only: bool) -> Iterator[Any]:
    """Ouvre une connexion courte avec le niveau d'accès demandé."""

    config: PostgreSQLConfig | None = None
    try:
        config = PostgreSQLConfig.from_env()
        import psycopg

        options = "-c default_transaction_read_only=on" if read_only else None
        kwargs = config.connect_kwargs(autocommit=True)
        if options:
            kwargs["options"] = options
        connection = psycopg.connect(**kwargs)
        try:
            with connection.cursor() as cursor:
                _reject_managed_target_in_legacy(cursor)
                configure_extension_search_path(cursor)
        except Exception:
            connection.close()
            raise
    except Exception as exc:
        location = config.safe_location if config else "configuration cible"
        raise WebDatabaseError(
            f"Base PostgreSQL indisponible ({location})."
        ) from exc

    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def open_read_connection(context=None) -> Iterator[Any]:
    """Connexion réutilisable par les services serveur en lecture seule."""

    yield from (_context_connection(context, read_only=True) if context is not None
                else _connection(read_only=True))


@contextmanager
def open_write_connection(context=None) -> Iterator[Any]:
    """Connexion explicite des tâches serveur d'administration contrôlées."""

    yield from (_context_connection(context, read_only=False) if context is not None
                else _connection(read_only=False))


@contextmanager
def open_offline_prepare_connection(context) -> Iterator[Any]:
    """Burn eligibility before any snapshot can be returned to an appliance."""
    yield from _context_connection(context, read_only=False, usage='offline_prepare')


@contextmanager
def open_sql_connection(context=None) -> Iterator[Any]:
    """Connexion SQL humain ; jamais utilisée comme repli du credential IA."""

    if context is not None:
        yield from _context_connection(context, read_only=True, profile_kind='sql_readonly')
        return
    config: PostgreSQLReadonlyConfig | None = None
    try:
        config = PostgreSQLReadonlyConfig.from_env()
        import psycopg

        kwargs = config.connect_kwargs(autocommit=True)
        kwargs["options"] = "-c default_transaction_read_only=on"
        connection = psycopg.connect(**kwargs)
        try:
            with connection.cursor() as cursor:
                _reject_managed_target_in_legacy(cursor)
                validate_sql_role_security(cursor)
                configure_sql_search_path(cursor)
        except Exception:
            connection.close()
            raise
    except Exception as exc:
        location = config.safe_location if config else "configuration SQL/IA restreinte"
        raise WebDatabaseError(
            f"Connexion PostgreSQL SQL/IA indisponible ({location})."
        ) from exc

    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def open_ai_connection(context=None) -> Iterator[Any]:
    """IA : profil propre obligatoire, y compris en legacy (aucun dotenv)."""
    if context is not None:
        yield from _context_connection(context, read_only=True, profile_kind='ai_readonly')
        return
    import psycopg
    dsn = os.getenv('DATABASE_AI_READONLY_URL', '').strip()
    if not dsn:
        raise WebDatabaseError('Connexion IA dédiée non configurée.')
    connection = None
    try:
        connection = psycopg.connect(dsn, autocommit=True, connect_timeout=10,
                                     options='-c default_transaction_read_only=on')
        with connection.cursor() as cursor:
            _reject_managed_target_in_legacy(cursor)
            validate_ai_role_security(cursor)
            configure_sql_search_path(cursor)
    except Exception:
        if connection is not None:
            connection.close()
        raise WebDatabaseError('Connexion IA indisponible ou non conforme.') from None
    try:
        yield connection
    finally:
        connection.close()


def _reject_managed_target_in_legacy(cursor):
    """A legacy DSN may never bypass a managed base's control-plane barrier."""
    cursor.execute("""SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='digues_metadata' AND c.relname='schema_version')""")
    if cursor.fetchone()[0]:
        raise WebDatabaseConfigurationError('Base gérée inaccessible en mode mono-base.')


def validate_ai_role_security(cursor):
    from digues_app.control.ai_policy import validate_ai_select_security
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


def _check_context_available(context):
    # The caller already resolved permissions. Check before CONNECT, which an
    # import deliberately revokes, and again under the shared access lock.
    from digues_app.control.database import open_control_connection
    with open_control_connection() as control:
        row = control.execute("""SELECT b.status, b.schema_version, b.data_epoch,
            EXISTS (SELECT 1 FROM public.business_import_barriers barrier
                    WHERE barrier.business_database_id=b.id)
            FROM public.business_databases b WHERE b.id=%s""",
            (context.business_database_id,)).fetchone()
    if not row or row[:3] != ('active', context.schema_version, context.data_epoch):
        raise WebDatabaseConfigurationError('Contexte métier périmé ou indisponible.')
    if row[3]:
        raise BusinessMaintenanceError()


def _context_connection(context, *, read_only, profile_kind='runtime', usage=None):
    """Connexions courtes, cible explicite ; aucune configuration globale mutée."""
    import psycopg
    from .connection_profiles import resolve_profile

    connection = None
    try:
        _check_context_available(context)
        kwargs = resolve_profile(context, kind=profile_kind)
        kwargs['options'] = ('-c default_transaction_read_only=on ' if read_only else '') + (
            '-c statement_timeout=15000 -c lock_timeout=5000')
        connection = psycopg.connect(**kwargs)
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), current_user, session_user')
            if cursor.fetchone() != (context.database_name, kwargs['user'], kwargs['user']):
                raise WebDatabaseConfigurationError('Cible métier incohérente.')
            from digues_app.target.business_version import BUSINESS_ACCESS_LOCK
            # Session lock lives until close, including SQL transactions and SSE
            # reads. The operator cannot upgrade underneath an in-flight query.
            cursor.execute('SELECT pg_try_advisory_lock_shared(%s)', (BUSINESS_ACCESS_LOCK,))
            if not cursor.fetchone()[0]:
                raise BusinessMaintenanceError()
            _check_context_available(context)
            if profile_kind != 'runtime':
                (validate_ai_role_security if profile_kind == 'ai_readonly'
                 else validate_sql_role_security)(cursor)
                # No bridge to another business/control database, including
                # explicit dblink/FDW calls. CONNECT is a separate ACL.
                cursor.execute("""
                    SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_database
                        WHERE datallowconn AND datname <> current_database()
                        AND has_database_privilege(current_user, oid, 'CONNECT')),
                        EXISTS (SELECT 1 FROM pg_catalog.pg_extension
                                WHERE extname IN ('dblink', 'postgres_fdw')),
                        EXISTS (SELECT 1 FROM pg_catalog.pg_foreign_server)
                    """)
                if any(cursor.fetchone()):
                    raise WebDatabaseConfigurationError('Cloisonnement SQL/IA insuffisant.')
                configure_sql_search_path(cursor)
            else:
                configure_extension_search_path(cursor)
            if profile_kind == 'runtime':
                from digues_app.target.business_version import BUSINESS_SCHEMA_VERSION
                if context.schema_version == BUSINESS_SCHEMA_VERSION:
    except BusinessMaintenanceError:
        if connection is not None:
            connection.close()
        raise
    except Exception:
        if connection is not None:
            connection.close()
        else:
            # CONNECT may have been revoked after the first control check.
            # Recheck control only; never retry a business connection.
            try:
                _check_context_available(context)
            except BusinessMaintenanceError:
                raise
            except Exception:
                pass
        raise WebDatabaseError('Connexion métier ciblée indisponible ou non conforme.') from None
    try:
        yield connection
    finally:
        connection.close()


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
