"""Commandes de recette du donneur, conteneurs PostGIS jetables."""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "demo/data"))

def command(*args, input_text=None, check=True):
    if args and args[0] == "docker":
        args = (os.environ.get("SIG_PRESTATIONS_TEST_DOCKER", "docker"), *args[1:])
    result = subprocess.run(args, input=input_text, text=True, encoding="utf-8",
                            capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Échec de {args[0]}")
    return result


def sql(container, statement, check=True, database="postgres"):
    for _ in range(30):
        result = command("docker", "exec", "-i", container, "psql", "-X", "-A", "-t",
                         "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", database,
                         input_text=statement, check=False)
        if ("database system is shutting down" in result.stderr or
                "database system is starting up" in result.stderr or
                "No such file or directory" in result.stderr):
            time.sleep(0.5)
            continue
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result
    raise RuntimeError("PostgreSQL de test n'est pas stabilisé après son initialisation")



def import_fixture(container, database, schema="gemapi", check=True):
    from import_demo import demo
    from install import transaction
    return command("docker", "exec", "-i", container, "psql", "-Xq", "-v", "ON_ERROR_STOP=1",
                   "-U", "postgres", "-d", database, input_text=transaction(demo(database, schema)), check=check)
