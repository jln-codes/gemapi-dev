"""Reconstruire la démo fictive dans une base locale vide, en une transaction."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(DIRECTORY.parents[1] / "model"))
from install import execute, install, literal, render

FILES = ("01_techniciens", "03_communes", "04_prestataires", "09_ah", "10_sites_terrestres",
         "11_cours_eau", "12_ah_sections", "digues", "14_patrimoine", "15_prestations", "16_suivi")


def demo(database, schema):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", database) or database in {"postgres", "template0", "template1"}:
        raise ValueError("Nom explicite d’une base locale dédiée requis")
    guard = """
SET LOCAL search_path = pg_catalog, pg_temp;
SELECT pg_advisory_xact_lock(hashtextextended('gemapi.demo.reconstruction', 0));
DO $guard$
BEGIN
    IF inet_server_addr() IS NOT NULL OR EXISTS (
        SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','p','v','m','S','f')
          AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass
                          AND d.objid=c.oid AND d.deptype='e')
    ) THEN RAISE EXCEPTION 'Base locale vide requise'; END IF;
END $guard$;
"""
    # Défense côté serveur également si le SQL est exécuté par un banc de test.
    check = "DO $$ BEGIN IF current_database() <> " + literal(database) + (
        " THEN RAISE EXCEPTION 'Destination inattendue'; END IF; END $$;\n")
    parts = [guard, check, install(schema)]
    for stem in FILES:
        source = (DIRECTORY / (stem + ".sql")).read_text()
        _, begin, data = source.partition("\nBEGIN;\n")
        if not begin or not data.rstrip().endswith("COMMIT;"):
            raise ValueError(f"Enveloppe de transaction inattendue pour {stem}")
        data = data.rstrip().removesuffix("COMMIT;")
        # Les exports portent en tête leur propre search_path. L'import garde
        # celui de l'installation, y compris pour le SQL des triggers différés.
        if data.startswith("SELECT pg_catalog.set_config('search_path',"):
            _, data = data.split(";", 1)
        parts.extend([render(data, schema), "SET CONSTRAINTS ALL IMMEDIATE; SET CONSTRAINTS ALL DEFERRED;"])
    return "\n".join(parts)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("database")
    cli.add_argument("--schema", default="gemapi")
    args = cli.parse_args()
    env = os.environ.copy()
    socket = env.get("PGHOST", "/var/run/postgresql")
    if not Path(socket).is_absolute() or "," in socket or not Path(socket).is_dir():
        cli.error("PGHOST doit être un répertoire de socket local")
    env["PGHOST"] = socket
    for key in ("PGHOSTADDR", "PGSERVICE"):
        env.pop(key, None)
    execute(args.database, demo(args.database, args.schema), env)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
