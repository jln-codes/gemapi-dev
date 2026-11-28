"""Recette INIT-06B. Lancer comme script avec PostgreSQL/libpq 16+, PostGIS et psycopg2."""
from contextlib import closing
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from unittest.mock import patch

import psycopg2
from psycopg2 import sql


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "SIG essai"
GROUPS = ("groupe_lecture", "groupe_prestations", "groupe_patrimoine")
PROFILES = dict(zip(("lecteur", "editeur", "gestion"), GROUPS))


def connect(folder, login, **options):
    connection = psycopg2.connect(host=str(folder), port=5432, dbname="postgres",
                                  user=login, connect_timeout=4, **options)
    connection.autocommit = True
    return connection


def authenticate(folder, login, password):
    return connect(folder, login, password=password, require_auth="scram-sha-256")


def expect_denied(folder, login, password):
    try:
        authenticate(folder, login, password).close()
    except psycopg2.OperationalError:
        return
    raise AssertionError(f"Connexion inattendue pour {login}")


def prepare(folder, admin, passwords):
    admin.execute("CREATE SCHEMA ext; CREATE EXTENSION postgis SCHEMA ext")
    admin.execute("""
        CREATE ROLE groupe_lecture;
        CREATE ROLE groupe_prestations IN ROLE groupe_lecture;
        CREATE ROLE groupe_patrimoine IN ROLE groupe_prestations;
        GRANT USAGE ON SCHEMA ext TO groupe_lecture
    """)
    env = dict(os.environ, PGHOST=str(folder), PGUSER="postgres", PGPORT="5432")
    for operation, extra in (("install", []), ("permissions", ["--groups", *GROUPS])):
        subprocess.run([sys.executable, ROOT / "model/install.py", operation, "--database",
                        "postgres", "--schema", SCHEMA, *extra], env=env, check=True)
    for login, password in passwords.items():
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(login)),
                      (password,))
    for login, group in PROFILES.items():
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(login)))


def check_profiles(folder, passwords):
    expect_denied(folder, "postgres", secrets.token_urlsafe(32))  # HBA trust sur ce socket.
    expect_denied(folder, "gestion", secrets.token_urlsafe(32))
    for login in PROFILES:
        with closing(authenticate(folder, login, passwords[login])) as db, db.cursor() as cursor:
            cursor.execute("""
                SELECT session_user, current_user,
                       has_table_privilege('"SIG essai".prestations', 'UPDATE'),
                       has_table_privilege('"SIG essai".ah', 'INSERT')
            """)
            assert cursor.fetchone() == (login, login, login != "lecteur", login == "gestion")


def check_delegation(folder, admin, password):
    admin.execute("GRANT gestion TO service_test WITH INHERIT FALSE, SET TRUE")
    with closing(authenticate(folder, "service_test", password)) as db, db.cursor() as cursor:
        cursor.execute("BEGIN; SET LOCAL ROLE gestion")
        cursor.execute("""INSERT INTO "SIG essai".ah(nom) VALUES ('Essai')
                          RETURNING session_user, current_user, modifie_par""")
        assert cursor.fetchone() == ("service_test", "gestion", "service_test")
        cursor.execute("ROLLBACK")
        cursor.execute("SELECT session_user, current_user")
        assert cursor.fetchone() == ("service_test", "service_test")


def check_live_session(folder, admin, password):
    with closing(authenticate(folder, "gestion", password)) as db, db.cursor() as cursor:
        cursor.execute("""INSERT INTO "SIG essai".ah(nom) VALUES ('Essai direct')
                          RETURNING modifie_par, tableoid""")
        author, table_oid = cursor.fetchone()
        assert author == "gestion"
        admin.execute("REVOKE groupe_patrimoine FROM gestion")
        cursor.execute("SELECT has_table_privilege(%s::oid, 'INSERT')", (table_oid,))
        assert cursor.fetchone() == (False,)
        try:
            cursor.execute('SELECT id FROM "SIG essai".ah')
        except psycopg2.errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError("Lecture autorisée après retrait du groupe")

        fresh = secrets.token_urlsafe(32)
        admin.execute("ALTER ROLE gestion PASSWORD %s", (fresh,))
        expect_denied(folder, "gestion", password)
        authenticate(folder, "gestion", fresh).close()
        admin.execute("ALTER ROLE gestion NOLOGIN")
        expect_denied(folder, "gestion", fresh)
        cursor.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname=session_user")
        assert cursor.fetchone() == (False,)
        admin.execute("ALTER ROLE gestion LOGIN VALID UNTIL '2000-01-01'")
        expect_denied(folder, "gestion", fresh)
        cursor.execute("SELECT rolvaliduntil < clock_timestamp() FROM pg_roles WHERE rolname=session_user")
        assert cursor.fetchone() == (True,)


def qualify(folder):
    data = folder / "pg"
    bindir = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
    subprocess.run([bindir / "initdb", "-D", data, "-U", "postgres", "--auth=trust",
                    "--no-locale", "--encoding=UTF8"], stdout=subprocess.DEVNULL, check=True)
    (data / "pg_hba.conf").write_text("local all postgres trust\nlocal all all scram-sha-256\n")
    ctl = [bindir / "pg_ctl", "-D", data]
    subprocess.run([*ctl, "-l", folder / "server.log", "-o",
                    f"-c listen_addresses='' -k '{folder}' -p 5432", "-w", "start"], check=True)
    try:
        passwords = {login: secrets.token_urlsafe(32) for login in (*PROFILES, "service_test")}
        with closing(connect(folder, "postgres")) as db, db.cursor() as admin:
            prepare(folder, admin, passwords)
            check_profiles(folder, passwords)
            check_delegation(folder, admin, passwords["service_test"])
            check_live_session(folder, admin, passwords["gestion"])
        print("SCRAM, trois profils, attribution directe et déléguée, révocation et expiration vérifiées.")
    finally:
        subprocess.run([*ctl, "-m", "fast", "-w", "stop"], check=True)


if __name__ == "__main__":
    # Aucun service libpq, hôte ou credential du poste pour cette recette.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    with patch.dict(os.environ, environment, clear=True), tempfile.TemporaryDirectory(dir="/tmp") as root:
        qualify(Path(root))
