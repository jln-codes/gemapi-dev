"""Exécuté uniquement par qualify_images dans le processus de recette de l'image."""
from concurrent.futures import ThreadPoolExecutor
import os
import psycopg
from psycopg.pq import TransactionStatus

from digues_webapp.sessions import Sessions
from digues_webapp.session_database import DatabaseUnavailable, open_individual
from unittest.mock import patch
from psycopg.conninfo import make_conninfo


def verify():
    sessions = Sessions()
    password = os.environ["GEMAPI_TEST_PASSWORD"]
    # Même serveur via un autre alias DNS, puis certificat racine indisponible.
    # Dans les deux cas, la connexion doit échouer avant la création de la session.
    source = os.environ["DATABASE_URL"]
    for invalid in (make_conninfo(source, host="alias-sql"),
                    make_conninfo(source, sslrootcert="/tmp/missing-root.crt")):
        with patch.dict(os.environ, {"DATABASE_URL": invalid}):
            try:
                conn, _ = open_individual("lecteur", password, sessions.sql_idle_seconds)
            except DatabaseUnavailable:
                pass
            else:
                conn.close()
                raise AssertionError("Connexion TLS acceptée avec un certificat non vérifiable")
    editor, identity = sessions.login("gestion", password, None)
    reader, _ = sessions.login("lecteur", password, None)
    try:
        try:
            with sessions.transaction(editor, expected=identity["context"]) as current:
                conn = current.connection
                author = conn.execute("INSERT INTO ah(nom) VALUES ('Qualification') "
                                      "RETURNING session_user,current_user,modifie_par").fetchone()
                assert author == ("gestion", "gestion", "gestion")
                conn.execute("SELECT 1 / 0")
        except psycopg.errors.DivisionByZero:
            pass
        else:
            raise AssertionError("Erreur SQL attendue")
        assert conn.info.transaction_status == TransactionStatus.IDLE
        with sessions.transaction(editor) as current:
            assert current.connection.execute("SELECT count(*) FROM ah WHERE nom='Qualification'").fetchone() == (0,)
        try:
            with sessions.transaction(reader) as current:
                current.connection.execute("INSERT INTO ah(nom) VALUES ('Refus')")
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError("Écriture du lecteur acceptée")

        def execute(token):
            with sessions.transaction(token) as current:
                return current.connection.execute("SELECT session_user,pg_backend_pid() FROM pg_sleep(0.05)").fetchone()
        with ThreadPoolExecutor(max_workers=4) as pool:
            first, second, third, fourth = pool.map(execute, [reader, editor, reader, editor])
        assert first == third and second == fourth and first[0] == "lecteur" and second[0] == "gestion"
        assert first[1] != second[1]
    finally:
        sessions.clean(shutdown=True)


verify()
