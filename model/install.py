"""Maintenance explicite de la livraison v0.6.1. Python standard et psql 16+."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys

MODEL = Path(__file__).resolve().parent
DDL = MODEL / "schema_v0.6.1.sql"
DDL_HASH = "42b5f662485ea51d1ec8bc10af283fa1e49d272dcb814015bbf55fa1dd38bab8"
PROFILES = ("milieux_consultation", "milieux_prestations", "milieux_patrimoine")
# Lexèmes de la livraison figée. Les corps de fonctions sont parcourus séparément.
TOKEN = re.compile(r"""--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|"(?:""|[^"])*"|(?P<tag>\$(?:[A-Za-z_]\w*)?\$)(?P<body>.*?)(?P=tag)|[A-Za-z_]\w*""", re.S)


def identifier(name):
    if not name or len(name.encode()) > 63 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Identifiant PostgreSQL vide, trop long ou contenant un caractère de contrôle")
    return '"' + name.replace('"', '""') + '"'


def literal(value):
    # La commande fixe standard_conforming_strings avant l'analyse de ces instructions.
    return "'" + value.replace("'", "''") + "'"


def render(source, schema, roles=()):
    names = {"gemapi": identifier(schema), **dict(zip(PROFILES, map(identifier, roles)))}

    def replace(match):
        word = match[0]
        if match["tag"]:
            body = render(match["body"], schema, roles)
            tag = match["tag"]
            while tag in body:
                tag = tag[:-1] + "_$"
            return tag + body + tag
        if word in names:
            return names[word]
        # Seules ces deux instructions dynamiques utilisent le nom du schéma.
        if (word.startswith(("'CREATE TRIGGER tr_stamp_%I ", "'CREATE INDEX %I "))
                and source.endswith("EXECUTE format(", 0, match.start())):
            return literal(word[1:-1].replace("''", "'").replace("gemapi.", names["gemapi"].replace("%", "%%") + "."))
        return word

    return TOKEN.sub(replace, source)


def trusted_path(schema):
    if schema.startswith("pg_") or schema == "information_schema":
        raise ValueError("Schéma système réservé")
    return f"""
SET LOCAL search_path = pg_catalog, pg_temp;
SELECT set_config('gemapi_install.destination', {literal(schema)}, true);
DO $guard$
DECLARE dest text := current_setting('gemapi_install.destination'); ext text;
BEGIN
  SELECT n.nspname INTO ext FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
   WHERE e.extname='postgis';
  IF ext IS NULL THEN RAISE EXCEPTION 'PostGIS absent, installation réservée au DBA'; END IF;
  IF dest=ext THEN RAISE EXCEPTION 'Les schémas métier et PostGIS doivent être distincts'; END IF;
  IF EXISTS (SELECT FROM pg_namespace n, LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
             WHERE n.nspname IN (ext,dest) AND a.privilege_type='CREATE' AND a.grantee<>n.nspowner)
  THEN RAISE EXCEPTION 'CREATE accordé à un tiers dans le chemin des fonctions'; END IF;
  PERFORM set_config('search_path',format('pg_catalog,%I,%I,pg_temp',ext,dest),true);
END $guard$;
"""


def install(schema):
    original = DDL.read_bytes()
    if hashlib.sha256(original).hexdigest() != DDL_HASH:
        raise ValueError("La source v0.6.1 a changé, vérifier la transformation")
    # Le préambule donneur gère la base entière. Il est remplacé par les contrôles
    # circonscrits à la destination. Les deux search_path proviennent du même contrôle.
    body = original.decode().split("CREATE TABLE ", 1)[1].removesuffix("COMMIT;\n")
    start = body.index("SELECT pg_catalog.set_config(")
    end = body.index(";", start) + 1
    body = "CREATE TABLE " + body[:start] + body[end:]
    identifier(schema)
    return trusted_path(schema) + f"""
SELECT pg_advisory_xact_lock(hashtextextended(current_setting('gemapi_install.destination'),0));
DO $guard$
DECLARE dest text := current_setting('gemapi_install.destination');
BEGIN
  IF EXISTS (SELECT FROM pg_depend d JOIN pg_namespace n ON n.oid=d.refobjid
             WHERE d.refclassid='pg_namespace'::regclass AND n.nspname=dest)
  THEN RAISE EXCEPTION 'Installation neuve requise : schéma occupé'; END IF;
  IF NOT EXISTS (SELECT FROM pg_namespace WHERE nspname=dest) THEN
    EXECUTE format('CREATE SCHEMA %I',dest);
  END IF;
END $guard$;
""" + render(body, schema)


def permissions(schema, roles):
    if len(roles) != 3 or len(set(roles)) != 3:
        raise ValueError("Fournir trois groupes DBA distincts")
    source = (MODEL / "roles_milieux.sql").read_text()
    source = source[source.index("-- Droit de lecture commun"):source.index("COMMIT;")]
    reader = identifier(roles[0])
    # Le propriétaire des objets peut appliquer les GRANT sans CREATEROLE.
    return trusted_path(schema) + f"""
SELECT set_config('gemapi_install.profiles', ARRAY[{','.join(map(literal, roles))}]::text,true);
DO $guard$
DECLARE groups text[] := current_setting('gemapi_install.profiles')::text[];
BEGIN
  IF (SELECT count(*) FROM pg_roles WHERE rolname=ANY(groups) AND NOT
      (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) <> 3
  THEN RAISE EXCEPTION 'Groupes DBA absents ou privilégiés'; END IF;
  IF NOT pg_has_role(groups[2],groups[1],'USAGE') OR NOT pg_has_role(groups[3],groups[2],'USAGE')
  THEN RAISE EXCEPTION 'Hiérarchie Consultation / Prestations / Patrimoine absente'; END IF;
  IF NOT has_schema_privilege(groups[1],(SELECT extnamespace FROM pg_extension WHERE extname='postgis'),'USAGE')
  THEN RAISE EXCEPTION 'USAGE PostGIS à accorder par le DBA'; END IF;
END $guard$;
GRANT USAGE ON SCHEMA {identifier(schema)} TO {reader};
""" + render(source, schema, roles)


def transaction(body):
    return "BEGIN;\nSET LOCAL standard_conforming_strings = on;\n" + body + "\nCOMMIT;\n"


def execute(database, body, env=None):
    # libpq accepte un DSN dans -d. Le nom fourni ici désigne uniquement une base.
    if not database or '=' in database or '://' in database:
        raise ValueError("Fournir un nom de base, configurer la connexion avec libpq")
    subprocess.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "--dbname", database],
                   input=transaction(body), text=True, check=True, env=env)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("operation", choices=("install", "permissions"))
    cli.add_argument("--database", required=True)
    cli.add_argument("--schema", required=True)
    cli.add_argument("--groups", nargs=3, metavar=("LECTURE", "PRESTATIONS", "PATRIMOINE"))
    args = cli.parse_args()
    if args.operation == "permissions" and not args.groups:
        cli.error("permissions exige --groups avec les groupes approuvés par le DBA")
    body = install(args.schema) if args.operation == "install" else permissions(args.schema, args.groups)
    execute(args.database, body)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
