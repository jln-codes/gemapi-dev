"""Connexion individuelle. La destination et les règles d’admission restent côté serveur."""
import getpass
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .database import MODEL_SIGNATURE, MODEL_STRUCTURE, PostgreSQLConfig, quote_identifier


class AccessDenied(Exception):
    pass


class BadCredentials(Exception):
    pass


class DatabaseUnavailable(Exception):
    pass


def default_login():
    if os.name == "posix":
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    return getpass.getuser()


def technical_credentials():
    for name in ("DATABASE_URL", "DATABASE_READONLY_URL", "DATABASE_AI_READONLY_URL"):
        if os.environ.get(name):
            params = conninfo_to_dict(os.environ[name])
            # Ces valeurs implicites sont aussi utilisables par les diagnostics techniques.
            user = params.get("user") or os.getenv("PGUSER") or default_login()
            yield user, params.get("password") or os.getenv("PGPASSWORD")


def check_admission(connection, login, schema):
    """À appeler dans la transaction de l'utilisateur, avant le travail protégé."""
    connection.execute("SET LOCAL search_path = pg_catalog, pg_temp")
    row = connection.execute("""
        SELECT session_user, current_user, x.nspname,
               r.rolcanlogin AND (r.rolvaliduntil IS NULL OR r.rolvaliduntil > clock_timestamp())
               AND has_database_privilege(d.oid, 'CONNECT')
               AND NOT has_database_privilege(d.oid, 'CREATE')
               AND has_schema_privilege(n.oid,'USAGE') AND has_schema_privilege(x.oid,'USAGE')
               AND NOT has_schema_privilege(n.oid,'CREATE') AND NOT has_schema_privilege(x.oid,'CREATE')
               AND pg_has_role(g.oid,'USAGE') AND NOT g.rolcanlogin
               AND NOT EXISTS (SELECT FROM pg_auth_members m
                               WHERE m.admin_option AND pg_has_role(m.member,'MEMBER'))
               AND NOT EXISTS (
                 SELECT FROM pg_roles p WHERE pg_has_role(p.oid,'MEMBER') AND
                 (p.rolsuper OR p.rolcreatedb OR p.rolcreaterole OR p.rolreplication OR p.rolbypassrls
                  OR left(p.rolname,3)='pg_' OR p.oid IN (d.datdba,n.nspowner,x.nspowner)
                  OR EXISTS (SELECT FROM pg_class c WHERE c.relnamespace=n.oid AND c.relowner=p.oid)
                  OR EXISTS (SELECT FROM pg_proc f WHERE f.pronamespace=n.oid AND f.proowner=p.oid)))
               AND NOT EXISTS (
                 SELECT FROM pg_class c WHERE c.relnamespace=n.oid AND c.relkind IN ('r','v','p','m','f')
                   AND NOT has_table_privilege(c.oid,'SELECT'))
        FROM pg_roles r, pg_roles g, pg_database d, pg_namespace n,
             pg_extension e JOIN pg_namespace x ON x.oid=e.extnamespace
        WHERE r.rolname=session_user AND g.rolname=%s AND d.datname=current_database()
          AND n.nspname=%s AND e.extname='postgis'
    """, (os.getenv("AUTH_READER_GROUP", "milieux_consultation"), schema)).fetchone()
    if not row or row[:2] != (login, login) or not row[3] or row[2] == "$user":
        raise AccessDenied
    path = ", ".join(map(quote_identifier, ("pg_catalog", row[2], schema, "pg_temp")))
    connection.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (path,))


def open_individual(login, password, idle_seconds):
    try:
        config = PostgreSQLConfig.from_env()
        source = conninfo_to_dict(config.dsn)
        # Explicit host, port and dbname prevent libpq deriving a destination from the login.
        host = source["host"]
        database = source["dbname"]
        if not host or not database or "," in host or "=" in database or "://" in database:
            raise DatabaseUnavailable
        # Service files/hostaddr could redirect an otherwise fixed endpoint.
        if (any(key in source for key in ("service", "hostaddr", "options"))
                or any(os.getenv(name) for name in ("PGSERVICE", "PGHOSTADDR", "PGOPTIONS"))):
            raise DatabaseUnavailable
        tls = {k: source[k] for k in ("sslrootcert", "sslcert", "sslkey", "sslcrl", "sslcrldir", "requirepeer",
                                       "ssl_min_protocol_version",
                                       "ssl_max_protocol_version", "channel_binding") if k in source}
        if not host.startswith("/") and source.get("sslmode", "verify-full") != "verify-full":
            raise DatabaseUnavailable
        # Après une partition, PostgreSQL peut ignorer longtemps la disparition du client.
        # Le délai serveur reste borné même lorsque le FIN TCP ne peut pas lui parvenir.
        idle_ms = min(idle_seconds * 1000, 2_147_483_647)
        connection = psycopg.connect(
            host=host, port=source.get("port", "5432"), dbname=database, user=login, password=password,
            sslmode="verify-full", gssencmode="disable", **tls, application_name="gemapi-web",
            tcp_user_timeout=5000, keepalives_idle=15, keepalives_interval=5, keepalives_count=2,
            require_auth="scram-sha-256", connect_timeout=config.connect_timeout, autocommit=True,
            options=("-c search_path=pg_catalog,pg_temp -c statement_timeout=15000 -c lock_timeout=5000 "
                     f"-c idle_session_timeout={idle_ms} -c idle_in_transaction_session_timeout=15000"),
        )
    except psycopg.OperationalError as exc:
        # libpq doesn't consistently expose an SQLSTATE for startup failures.
        msg = str(exc).lower()
        if exc.sqlstate in ("28P01", "28000") or "password authentication failed" in msg or "is not permitted to log in" in msg:
            raise BadCredentials from None
        raise DatabaseUnavailable from None
    except (KeyError, ValueError, psycopg.Error):
        raise DatabaseUnavailable from None
    try:
        if any(user == login for user, _ in technical_credentials()):
            raise AccessDenied
        with connection.transaction():
            check_admission(connection, login, config.schema)
            signature, readable = connection.execute(MODEL_STRUCTURE, (config.schema,)).fetchone()
            if signature != MODEL_SIGNATURE:
                raise DatabaseUnavailable
            if not readable:
                raise AccessDenied
        return connection, config.schema
    except psycopg.Error:
        connection.close()
        raise DatabaseUnavailable from None
    except BaseException:
        connection.close()
        raise
