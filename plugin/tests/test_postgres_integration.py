"""Disposable PostgreSQL/PostGIS integration check; run explicitly with Docker.

    python plugin/tests/test_postgres_integration.py

The container has no published port and is removed after the checks.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app.record import build_insert  # noqa: E402
from gemapi_app.report_data import build_report_query  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
PRESTATAIRE = "11111111-1111-1111-1111-111111111111"
TYPE_PRESTATION = "99999999-9999-9999-9999-999999999999"
ZEC = "22222222-2222-2222-2222-222222222222"
MISSING = "33333333-3333-3333-3333-333333333333"
FOSSE = "44444444-4444-4444-4444-444444444444"
RIVIERE = "55555555-5555-5555-5555-555555555555"
SITE = "66666666-6666-6666-6666-666666666666"
TECHNICIEN = "77777777-7777-7777-7777-777777777777"
DIGUE = "88888888-8888-8888-8888-888888888888"


(ROOT / "build/qgis-tests").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "tests/model"))
from demo_container import command, import_fixture, sql


def install_demo(container, dedicated_postgis=False):
    """Installer la démo du clone dans une base neuve réservée au test."""
    command("docker", "exec", container, "createdb", "-U", "postgres", "-T", "template0", "demo_qualification")
    extension = "extensions" if dedicated_postgis else "public"
    sql(container, f"CREATE SCHEMA IF NOT EXISTS {extension}; CREATE EXTENSION postgis SCHEMA {extension}",
        database="demo_qualification")
    import_fixture(container, "demo_qualification")


def load_demo_rows(container):
    for source in ("01_techniciens", "03_communes", "04_prestataires", "09_ah",
                   "10_sites_terrestres", "11_cours_eau", "12_ah_sections", "digues",
                   "14_patrimoine", "15_prestations", "16_suivi"):
        sql(container, (ROOT / f"demo/data/{source}.sql").read_text())


def check_ah_relations(container):
    """Validate independent optional storage (0..1) and works (0..N)."""
    parent_ids = [str(uuid4()) for _ in range(4)]
    missing_parent = str(uuid4())
    polygon = "ST_GeomFromText('POLYGON((0 0,2 0,2 2,0 0))',3950)"
    line = "ST_GeomFromText('LINESTRING(0 0,2 2)',3950)"
    for index, parent_id in enumerate(parent_ids):
        sql(container, f"INSERT INTO gemapi.ah(id,nom) VALUES ('{parent_id}','Cas AH {index}')")
    # The parent alone must survive its own committed transaction.
    assert sql(container, f"SELECT count(*) FROM gemapi.ah WHERE id='{parent_ids[0]}' "
               "AND NOT EXISTS (SELECT 1 FROM gemapi.ah_stockage WHERE ah_id=ah.id) "
               "AND NOT EXISTS (SELECT 1 FROM gemapi.ah_ouvrage WHERE ah_id=ah.id)").stdout.strip() == "1"
    for index in (1, 3):
        sql(container, "INSERT INTO gemapi.ah_stockage(ah_id,nom,geom) "
                       f"VALUES ('{parent_ids[index]}','Stockage {index}',{polygon})")
        duplicate = sql(container, "INSERT INTO gemapi.ah_stockage(ah_id,nom,geom) "
                        f"VALUES ('{parent_ids[index]}','Second stockage',{polygon})", check=False)
        assert duplicate.returncode != 0 and "ah_stockage_ah_id_key" in duplicate.stderr, duplicate.stderr
    for index in (2, 3):
        for child in range(2):
            sql(container, "INSERT INTO gemapi.ah_ouvrage(ah_id,geom) "
                           f"VALUES ('{parent_ids[index]}',{line})")
    counts = sql(container, "SELECT a.nom,(SELECT count(*) FROM gemapi.ah_stockage s "
                            "WHERE s.ah_id=a.id),(SELECT count(*) FROM gemapi.ah_ouvrage o "
                            "WHERE o.ah_id=a.id) FROM gemapi.ah a "
                            "WHERE a.id IN (" + ",".join(f"'{uid}'" for uid in parent_ids) + ") "
                            "ORDER BY a.nom").stdout.strip().splitlines()
    assert counts == ["Cas AH 0|0|0", "Cas AH 1|1|0", "Cas AH 2|0|2", "Cas AH 3|1|2"]
    for table, required, geometry in (("ah_stockage", "nom,geom", f"'Orphelin',{polygon}"),
                                      ("ah_ouvrage", "geom", line)):
        orphan = sql(container, f"INSERT INTO gemapi.{table}(ah_id,{required}) "
                       f"VALUES ('{missing_parent}',{geometry})", check=False)
        assert orphan.returncode != 0 and "foreign key" in orphan.stderr.lower(), orphan.stderr
        missing = sql(container, f"INSERT INTO gemapi.{table}({required}) "
                      f"VALUES ({geometry})", check=False)
        assert missing.returncode != 0 and "null value" in missing.stderr.lower(), missing.stderr
    restricted = sql(container, f"DELETE FROM gemapi.ah WHERE id='{parent_ids[3]}'", check=False)
    assert restricted.returncode != 0 and "foreign key" in restricted.stderr.lower(), restricted.stderr


def check_site_relations(container):
    """Sites allow zero or more zones; every zone has one protected parent."""
    parent, missing, first, second = (str(uuid4()) for _ in range(4))
    polygon = "ST_GeomFromText('MULTIPOLYGON(((0 0,2 0,2 2,0 0)))',3950)"
    point = "ST_GeomFromText('MULTIPOINT((1 1))',3950)"
    # Each call commits: an empty site must exist beyond its creation transaction.
    sql(container, "INSERT INTO gemapi.sites_terrestres(id,nom,geom) "
                   f"VALUES ('{parent}','Sans zone',{polygon})")
    assert sql(container, f"SELECT count(*) FROM gemapi.sites_terrestres "
               f"WHERE id='{parent}'").stdout.strip() == "1"
    for zone in (first, second):
        sql(container, "INSERT INTO gemapi.vegetation(famille_vegetation,id,site_terrestre_id,designation,geometry) "
                       f"VALUES ('ARBRES','{zone}','{parent}','Zone',{point})")
    assert sql(container, "SELECT count(*) FROM gemapi.vegetation "
               f"WHERE site_terrestre_id='{parent}'").stdout.strip() == "2"
    for owner, message in (("NULL", "check constraint"), (f"'{missing}'", "foreign key")):
        refused = sql(container, "INSERT INTO gemapi.vegetation(famille_vegetation,site_terrestre_id,designation,geometry) "
                      f"VALUES ('ARBRES',{owner},'Orpheline',{point})", check=False)
        assert refused.returncode != 0 and message in refused.stderr.lower(), refused.stderr
    refused = sql(container, f"DELETE FROM gemapi.sites_terrestres WHERE id='{parent}'", check=False)
    assert refused.returncode != 0 and "foreign key" in refused.stderr.lower(), refused.stderr
    sql(container, f"DELETE FROM gemapi.vegetation WHERE site_terrestre_id='{parent}'")
    sql(container, f"UPDATE gemapi.sites_terrestres SET nom='À nouveau sans zone' WHERE id='{parent}'")
    assert sql(container, f"SELECT count(*) FROM gemapi.sites_terrestres WHERE id='{parent}'").stdout.strip() == "1"
    sql(container, f"DELETE FROM gemapi.sites_terrestres WHERE id='{parent}'")


def check_cartographic_ah(container, values, ouvrage):
    """Real view: AH fallback, own-geometry priority and all six attachments."""
    cases = (
        ('cours_eau', FOSSE, 'POINT(3 4)', 'POINT(3 4)'),
        ('digues', DIGUE, 'LINESTRING(0 0,3 4)', 'LINESTRING(0 0,3 4)'),
        ('sites_terrestres', SITE, 'MULTIPOINT((3 4))', 'MULTIPOINT((3 4))'),
        ('vegetation', SITE, None, 'MULTIPOINT((0 0))'),
        ('ah_stockage', ZEC, None, 'POLYGON((0 0,1 0,1 1,0 1,0 0))'),
        ('ah_ouvrage', ouvrage, None, 'LINESTRING(0 0,1 0)'),
        # Both children may have a different, optional work geometry. Keep it.
        ('ah_stockage', ZEC, 'POINT(3 4)', 'POINT(3 4)'),
        ('ah_ouvrage', ouvrage, 'POLYGON((0 0,3 0,3 4,0 0))',
         'POLYGON((0 0,3 0,3 4,0 0))'),
    )
    for index, (family, parent, own, expected) in enumerate(cases):
        title = f'PERF08-GEOM-{index}'
        sql(container, build_insert({**values, 'intitule': title, 'quantite': '17',
                                     'unite': 'unités'}, [(family, parent)], own))
        actual = sql(container,
            "SELECT ST_AsEWKB(c.geom)=ST_AsEWKB(ST_GeomFromText('" + expected + "',3950)),"
            "p.geom IS NULL,p.quantite=c.quantite AND p.quantite=17 "
            "AND p.unite=c.unite AND p.unite='unités',ST_SRID(c.geom)=3950,"
            "ST_IsValid(c.geom) AND NOT ST_IsEmpty(c.geom) "
            "FROM gemapi.prestations p JOIN gemapi.prestations_carte c USING(id) "
            f"WHERE p.intitule='{title}'").stdout.strip()
        assert actual == ('t|t|t|t|t' if own is None else 't|f|t|t|t'), (family, actual)
    assert sql(container,
        "SELECT (SELECT count(*) FROM gemapi.prestations)=count(*),"
        "count(*)=count(DISTINCT id) FROM gemapi.prestations_carte").stdout.strip() == 't|t'
    assert sql(container,
        "SELECT count(*) FROM gemapi.prestations p "
        "JOIN gemapi.prestations_fiches f USING(id) "
        "WHERE p.intitule IN ('PERF08-GEOM-4','PERF08-GEOM-5') "
        "AND p.geom IS NULL AND f.possede_geometrie AND p.possede_geometrie "
        "AND (f.longueur_m IS NOT NULL OR f.surface_m2 IS NOT NULL) "
        "AND p.origine_mesures='Patrimoine de rattachement'").stdout.strip() == '2'
    for view in ('prestations_carte', 'prestations_fiches'):
        fields = ('nombre_points', 'longueur_m', 'surface_m2', 'surface_ha',
                  'type_geometrie', 'type_geometrie_postgis', 'possede_geometrie',
                  'origine_mesures')
        left = ','.join('p.' + f for f in fields)
        right = ','.join('v.' + f for f in fields)
        assert sql(container, f"SELECT count(*) FROM gemapi.prestations p "
                   f"JOIN gemapi.{view} v USING(id) "
                   f"WHERE ROW({left}) IS DISTINCT FROM ROW({right})").stdout.strip() == '0'
    # The displayed values follow the persisted trigger results after edits;
    # no separate view formula or write-back to prestations.geom is needed.
    sql(container, "UPDATE gemapi.ah_stockage SET geom=ST_Scale(geom,2,2) "
                   f"WHERE id='{ZEC}'")
    assert sql(container, "SELECT p.geom IS NULL,p.surface_m2=4,c.surface_m2=4,"
               "f.surface_m2=4,ST_Area(c.geom)=4,p.quantite=17 "
               "FROM gemapi.prestations p JOIN gemapi.prestations_carte c USING(id) "
               "JOIN gemapi.prestations_fiches f USING(id) "
               "WHERE p.intitule='PERF08-GEOM-4'").stdout.strip() == 't|t|t|t|t|t'
    # A missing parent geometry is forbidden by the canonical NOT NULL contract;
    # do not weaken it to manufacture a production-representative fixture.
    assert sql(container, f"UPDATE gemapi.ah_stockage SET geom=NULL WHERE id='{ZEC}'",
               check=False).returncode != 0
    assert sql(container, f"UPDATE gemapi.ah_ouvrage SET geom=NULL WHERE id='{ouvrage}'",
               check=False).returncode != 0
    print('PERF08-GEOM: six rattachements, repli AH, priorité propre, UUID et mesures validés')


def main():
    container = "sig-prestations-test-" + uuid4().hex[:10]
    command("docker", "run", "--rm", "-d", "--name", container,
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgis/postgis:16-3.4")
    try:
        # The image's temporary initialization server only listens on a Unix
        # socket. Wait for TCP so tests cannot run before its final restart.
        for _ in range(60):
            ready = command("docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres",
                            check=False)
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("PostgreSQL de test n'est pas prêt")
        sql(container, "CREATE EXTENSION IF NOT EXISTS postgis;")
        sql(container, (ROOT / "model" / "schema_v0.6.1.sql").read_text(encoding="utf-8"))
        check_ah_relations(container)
        check_site_relations(container)
        columns = sql(container, "SELECT count(*) FROM information_schema.columns "
                      "WHERE table_schema='gemapi' AND table_name='prestations' "
                      "AND column_name IN ('photo_1','photo_1_commentaire',"
                      "'photo_2','photo_2_commentaire');")
        assert columns.stdout.strip() == "4"
        sql(container, f"BEGIN; INSERT INTO gemapi.prestataires (id, nom) VALUES ('{PRESTATAIRE}', 'Test'); "
                       f"INSERT INTO gemapi.techniciens (id, nom) VALUES ('{TECHNICIEN}', 'Responsable test'); "
                       f"INSERT INTO gemapi.types_prestations (id, code, libelle, categorie_id) "
                       f"VALUES ('{TYPE_PRESTATION}', 'TEST', 'Test', 'cf2572fb-9365-5ac5-bdf4-0d66581d9aaa'); "
                       "INSERT INTO gemapi.ah(id,nom) VALUES ('55555555-5555-5555-5555-555555555555','AH test'); "
                       f"INSERT INTO gemapi.ah_stockage (id, ah_id, nom, geom) VALUES ('{ZEC}', '55555555-5555-5555-5555-555555555555', 'Test', "
                       "ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))',3950)); "
                       "INSERT INTO gemapi.ah_ouvrage(ah_id,geom) VALUES "
                       "('55555555-5555-5555-5555-555555555555',ST_GeomFromText('LINESTRING(0 0,1 0)',3950)); "
                       f"INSERT INTO gemapi.cours_eau (id, nom, type_ecoulement, source, geom) "
                       f"VALUES ('{FOSSE}', 'Test', 'fossé', 'TEST', "
                       "ST_Multi(ST_GeomFromText('LINESTRING(0 0,1 1)',3950))), "
                       f"('{RIVIERE}', 'Test', 'rivière', 'TEST', "
                       "ST_Multi(ST_GeomFromText('LINESTRING(0 0,1 1)',3950))); "
                       "INSERT INTO gemapi.sites_terrestres(id,nom,geom) VALUES ('66666666-6666-6666-6666-666666666666','Site test',ST_GeomFromText('MULTIPOLYGON(((0 0,1 0,1 1,0 0)))',3950)); "
                       f"INSERT INTO gemapi.vegetation(famille_vegetation,id,site_terrestre_id,designation,geometry) VALUES ('ARBRES','{SITE}', '66666666-6666-6666-6666-666666666666', 'Test', "
                       "ST_GeomFromText('MULTIPOINT((0 0))',3950)); "
                       f"INSERT INTO gemapi.digues(id,nom) VALUES ('{DIGUE}','Digue de contrôle'); "
                       f"UPDATE gemapi.cours_eau SET direction=TRUE WHERE id='{RIVIERE}'; "
                       "INSERT INTO gemapi.digues_reperes(digue_id,riviere_id,rive,debut_fraction,fin_fraction) "
                       f"VALUES ('{DIGUE}','{RIVIERE}','G',0.1,0.8); COMMIT;")
        status_id = sql(container, "SELECT id::text FROM gemapi.statuts_prestations "
                        "WHERE code='PLANIFIE'").stdout.strip()
        values = {"intitule": "Test d'atomicité", "prestataire_id": PRESTATAIRE,
                  "type_prestation_id": TYPE_PRESTATION, "statut_id": status_id,
                  "technicien_id": TECHNICIEN,
                  "date_debut": "2026-11-01", "date_fin": "2027-03-01",
                  "photo_1": "essais/une-photo.jpg", "photo_1_commentaire": "Avant travaux"}
        selected = [("cours_eau", FOSSE)]
        result = sql(container, build_insert(values, selected, "POINT(700000 6600000)"))
        assert result.stdout.strip().endswith("|0"), result.stdout
        counts = sql(container, "SELECT (SELECT count(*) FROM gemapi.prestations), "
                                "(SELECT count(ah_stockage_id) FROM gemapi.prestations), "
                                "(SELECT count(cours_eau_id) FROM gemapi.prestations), "
                                "(SELECT count(vegetation_id) FROM gemapi.prestations);")
        assert counts.stdout.strip() == "1|0|1|0", counts.stdout
        assert sql(container, "SELECT photo_1,photo_1_commentaire FROM gemapi.prestations "
                   "WHERE intitule='Test d''atomicité';").stdout.strip() == "essais/une-photo.jpg|Avant travaux"
        failure = sql(container, build_insert(values, [("cours_eau", MISSING)],
                                              "POINT(700000 6600000)"),
                      check=False)
        assert failure.returncode != 0, "La clé étrangère aurait dû bloquer l'insertion"
        counts = sql(container, "SELECT (SELECT count(*) FROM gemapi.prestations), "
                                "(SELECT count(ah_stockage_id) FROM gemapi.prestations), "
                                "(SELECT count(cours_eau_id) FROM gemapi.prestations);")
        assert counts.stdout.strip() == "1|0|1", counts.stdout
        # Existing geometric prestations remain valid with their first link.
        geometries = (
            ("point lié", "POINT(700000 6600000)", "ST_Point", [("cours_eau", FOSSE)]),
            ("ligne liée", "LINESTRING(700000 6600000,700100 6600100)", "ST_LineString", [("cours_eau", FOSSE)]),
            ("polygone lié", "POLYGON((700000 6600000,700100 6600000,700100 6600100,700000 6600000))", "ST_Polygon", [("cours_eau", FOSSE)]),
            ("point avec liens", "POINT(700050 6600050)", "ST_Point",
             [("ah_stockage", ZEC)]),
        )
        for title, wkt, kind, links in geometries:
            row = sql(container, build_insert({**values, "intitule": title}, links, wkt))
            assert row.stdout.strip().endswith("|0"), row.stdout
            stored = sql(container, "SELECT ST_GeometryType(geom), ST_SRID(geom) "
                                    "FROM gemapi.prestations WHERE intitule = "
                                    f"'{title}';")
            assert stored.stdout.strip() == f"{kind}|3950", stored.stdout
        # A link failure must also roll back a prestation with a geometry.
        failure = sql(container, build_insert(values, [("cours_eau", MISSING)],
                                              "POINT(700000 6600000)"), check=False)
        assert failure.returncode != 0
        counts = sql(container, "SELECT (SELECT count(*) FROM gemapi.prestations), "
                                "(SELECT count(ah_stockage_id) FROM gemapi.prestations), "
                                "(SELECT count(cours_eau_id) FROM gemapi.prestations);")
        assert counts.stdout.strip() == "5|1|4", counts.stdout
        assert sql(container, "SELECT count(*) FROM gemapi.prestations WHERE rive='Indéfinie'").stdout.strip() == "5"
        assert sql(container, "UPDATE gemapi.prestations SET rive='Gauche' WHERE id IN "
                   "(SELECT id FROM gemapi.prestations WHERE cours_eau_id IS NOT NULL) "
                   "RETURNING rive").stdout.strip().count("Gauche") > 0
        assert sql(container, "UPDATE gemapi.prestations SET rive='Gauche' WHERE id IN "
                   "(SELECT id FROM gemapi.prestations WHERE ah_stockage_id IS NOT NULL)",
                   check=False).returncode != 0
        assert sql(container, "UPDATE gemapi.prestations SET rive=NULL", check=False).returncode != 0
        terminee = sql(container, "SELECT id::text FROM gemapi.statuts_prestations "
                       "WHERE code='TERMINE'").stdout.strip()
        sql(container, build_insert({**values, "intitule": "Travaux terminés",
                                     "statut_id": terminee,
                                     "position": "Crête", "rive": "Les deux",
                                     "date_debut": "2026-10-01",
                                     "date_fin": "2027-03-01"}, [("digues", DIGUE)],
                                    "POINT(700000 6600000)"))
        assert sql(container, "SELECT count(*) FROM gemapi.prestations "
                   "WHERE rive='Les deux' AND position='Crête'").stdout.strip() == "1"
        for criteria, expected in (({}, "6"), ({"completion": "completed"}, "1"),
                                   ({"completion": "incomplete"}, "5"),
                                   ({"years": [2027]}, "1"),
                                   ({"years": [2027], "completion": "incomplete"}, "0")):
            assert sql(container, build_report_query(criteria, count_only=True)).stdout.strip() == expected
        sql(container, build_insert({**values, "intitule": ""}, [("digues", DIGUE)],
                                    "POINT(700000 6600000)"))
        assert sql(container, "SELECT count(*) FROM gemapi.prestations "
                   "WHERE intitule IS NULL").stdout.strip() == "1"
        for code in ("EN_COURS", "ANNULE"):
            status = sql(container, "SELECT id::text FROM gemapi.statuts_prestations "
                         f"WHERE code='{code}'").stdout.strip()
            sql(container, build_insert({**values, "intitule": code,
                                         "statut_id": status}, [("digues", DIGUE)],
                                        "POINT(700000 6600000)"))
        assert sql(container, build_report_query(
            {"completion": "incomplete"}, count_only=True)).stdout.strip() == "8"
        assert sql(container, build_report_query(
            {"completion": "completed"}, count_only=True)).stdout.strip() == "1"
        ouvrage = sql(container, "SELECT id::text FROM gemapi.ah_ouvrage "
                      "WHERE ah_id='55555555-5555-5555-5555-555555555555'").stdout.strip()
        for title, family, object_id in (("Sans géométrie stockage", "ah_stockage", ZEC),
                                          ("Sans géométrie ouvrage", "ah_ouvrage", ouvrage),
                                          ("Sans géométrie zone", "vegetation", SITE)):
            result = sql(container, build_insert({**values, "intitule": title},
                                                 [(family, object_id)]))
            assert result.stdout.strip().endswith("|0"), result.stdout
            assert sql(container, "SELECT count(*) FROM gemapi.prestations WHERE geom IS NULL "
                       f"AND intitule='{title}'").stdout.strip() == "1"
        for family, object_id in (("cours_eau", FOSSE), ("digues", DIGUE)):
            try:
                build_insert(values, [(family, object_id)])
            except ValueError as error:
                assert "géométrie" in str(error)
            else:
                raise AssertionError(f"Le formulaire doit refuser {family} sans géométrie")
        assert sql(container, "UPDATE gemapi.prestations SET geom=NULL WHERE id IN "
                   "(SELECT id FROM gemapi.prestations WHERE cours_eau_id IS NOT NULL)",
                   check=False).returncode != 0
        assert sql(container, "UPDATE gemapi.prestations SET geom=NULL WHERE id IN "
                   "(SELECT id FROM gemapi.prestations WHERE digue_id IS NOT NULL)",
                   check=False).returncode != 0
        assert sql(container, "UPDATE gemapi.prestations SET geom=NULL WHERE intitule='point avec liens' "
                   "RETURNING geom IS NULL").stdout.strip().startswith("t")
        assert sql(container, "SELECT count(*) FROM gemapi.prestations WHERE geom IS NULL").stdout.strip() == "4"
        check_cartographic_ah(container, values, ouvrage)
        print("PostGIS de test : géométrie conditionnelle, types autorisés et liens atomiques validés")
    finally:
        command("docker", "stop", container, check=False)


if __name__ == "__main__":
    main()
