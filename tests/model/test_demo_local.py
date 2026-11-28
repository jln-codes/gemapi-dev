"""Comparer la commande publique d'import à la livraison brute sur un cluster privé."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "demo/data"))
from import_demo import DIRECTORY, FILES
from install import DDL
from test_sirs_postgis import projection


def snapshot(cursor, schema):
    tables = sorted(t.split(".")[1] for t in projection()[0])
    hashes = {}
    for table in tables:
        # Les quatre champs alimentés par l'horloge ou le compte d'installation
        # varient entre deux imports. Les dates métier et versions sont incluses.
        cursor.execute(sql.SQL("""
            SELECT (to_jsonb(t)-ARRAY['cree_le','modifie_par','date_modification','date_validation'])::text
            FROM {}.{} t ORDER BY 1
        """).format(sql.Identifier(schema), sql.Identifier(table)))
        rows = cursor.fetchall()
        hashes[table] = (len(rows), hashlib.sha256('\n'.join(r[0] for r in rows).encode()).hexdigest())
    cursor.execute("""
        SELECT c.relkind,c.relname,a.attname,t.typname,a.atttypmod,a.attnotnull,a.attgenerated
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
        JOIN pg_type t ON t.oid=a.atttypid WHERE n.nspname=%s AND c.relkind IN ('v','r')
        AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,a.attnum
    """, (schema,))
    structure = cursor.fetchall()
    cursor.execute("""
        SELECT c.relname,co.conname,co.contype,co.conkey,co.confkey,f.relname,
               f.relnamespace=c.relnamespace,co.condeferrable,co.condeferred
        FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
        LEFT JOIN pg_class f ON f.oid=co.confrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s ORDER BY 1,2
    """, (schema,))
    constraints = cursor.fetchall()
    for view in ("prestations_carte","prestations_fiches","prestations__sites_terrestres",
                 "prestations__ah","digues_carte","ah_sections_carte"):
        cursor.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(view)))
        hashes[view] = cursor.fetchone()[0]
    return hashes, structure, constraints


def qualify(folder):
    bindir = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
    data = folder / "data"
    subprocess.run([bindir / "initdb", "-D", data, "-U", "postgres", "-A", "trust",
                    "--no-locale", "--encoding=UTF8"], check=True, stdout=subprocess.DEVNULL)
    pg_ctl = [bindir / "pg_ctl", "-D", data]
    subprocess.run([*pg_ctl, "-l", folder / "postgres.log", "-o", f"-c listen_addresses='' -k '{folder}'",
                    "-w", "start"], check=True)
    env = {**os.environ, "PGHOST":str(folder), "PGPORT":"5432", "PGUSER":"postgres"}
    for key in ("PGSERVICE","PGHOSTADDR","PGDATABASE","PGOPTIONS"):
        env.pop(key,None)
    try:
        snapshots = []
        for db, schema, extension in (("demo_reference","gemapi","public"), ("demo_simple","gemapi","public"),
                                      ("demo_sig","Territoire d'essai","extensions")):
            subprocess.run([bindir/"createdb",db],env=env,check=True)
            conn = psycopg2.connect(host=str(folder),port=5432,user="postgres",dbname=db)
            with conn, conn.cursor() as cursor:
                if extension != "public":
                    cursor.execute("CREATE SCHEMA extensions")
                cursor.execute(sql.SQL("CREATE EXTENSION postgis SCHEMA {}").format(sql.Identifier(extension)))
            if db == "demo_reference":
                with conn, conn.cursor() as cursor:
                    cursor.execute(DDL.read_text())
                    for stem in FILES:
                        cursor.execute((DIRECTORY / (stem+".sql")).read_text())
            else:
                cmd = ["bash", DIRECTORY/"importer_base_neuve.sh",db,"--schema",schema]
                subprocess.run(cmd,env=env,check=True,stdout=subprocess.DEVNULL)
                rejected = subprocess.run(cmd,env=env,text=True,capture_output=True)
                assert rejected.returncode and "Base locale vide requise" in rejected.stderr
            with conn, conn.cursor() as cursor:
                snapshots.append(snapshot(cursor,schema))
            conn.close()
        assert snapshots[0] == snapshots[1] == snapshots[2]
        print("48 empreintes SHA-256, six vues, colonnes et contraintes identiques au donneur.", flush=True)
        print("Prestations", snapshots[0][0]["prestations"])
    finally:
        subprocess.run([*pg_ctl,"-m","fast","-w","stop"],check=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="gemapi-demo-") as path:
        qualify(Path(path))
