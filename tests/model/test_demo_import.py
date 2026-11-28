"""Rebuild demo databases via the real shell importer in disposable PostGIS.

Run explicitly with Docker. No host port is published and no real data is read.
"""

import json
import csv
import time
import hashlib
import shutil
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from uuid import uuid4
from zipfile import ZipFile

from osgeo import ogr

from demo_container import ROOT, command, sql, import_fixture


def source_spatial_expectations():
    """Independent GDAL comparison against the current fictitious geometries."""
    communes = ogr.Open(str(ROOT / 'demo/gpkg/communes.gpkg'))
    areas = [(f.GetField('id'), f.GetGeometryRef().Clone()) for f in communes.GetLayer(0)]
    prestations = ogr.Open(str(ROOT / 'demo/gpkg/prestations.gpkg'))
    zones_source = ogr.Open(str(ROOT / 'demo/gpkg/sites_terrestres.gpkg'))
    zones = {f.GetField('id'):f.GetGeometryRef().Clone()
             for family in ('mobilier','cheminements','vegetation') for f in zones_source.GetLayerByName(family)}
    pairs, groups, multipart = [], set(), 0
    for layer in prestations:
        for feature in layer:
            geom = feature.GetGeometryRef()
            multipart += int(geom is not None and geom.GetGeometryName().startswith('MULTI'))
            zone = feature['vegetation_id'] or feature['mobilier_id'] or feature['cheminement_id']
            if zone:
                assert geom is None
                geom = zones[zone]
            assert geom is not None and geom.IsValid()
            for uid, area in areas:
                if geom.Intersects(area):
                    pairs.append(uid)
                    groups.add((uid, feature.GetField('type_prestation_id'), feature.GetField('statut')))
    return multipart, f'{len(set(pairs))}|{len(pairs)}', f'{len(groups)}|{len(pairs)}'


def check_patrimony(query):
    from test_sirs_postgis import projection
    tables, columns, _, catalogs = projection()
    assert query("SELECT to_regclass('gemapi.sites_zones') IS NULL") == "t"
    for table, expected in catalogs.items():
        keys = ", ".join(expected[0])
        rows = json.loads(query(f"SELECT json_agg(r ORDER BY id) FROM (SELECT {keys} FROM {table}) r"))
        assert rows == sorted(expected, key=lambda row: row["id"]), table
    assert query("SELECT count(DISTINCT type_troncon_id) FROM gemapi.troncons WHERE digue_id IS NOT NULL") == "7"
    for table,num in (("troncons",7),("ouvrages_particuliers",1),("ouvrages_hydrauliques",1),
                      ("equipements_mesure",1),("desordres",2),("observations",4),("photos",3)):
        assert int(query(f"SELECT count(*) FROM gemapi.{table}")) == num
    assert query("SELECT count(*) FROM gemapi.desordres WHERE date_fin IS NULL") == "1"
    assert query("SELECT max(n) FROM (SELECT count(DISTINCT date_observation) n FROM gemapi.observations GROUP BY desordre_id) h") == "2"
    assert query("SELECT count(*) FROM gemapi.observations WHERE equipement_mesure_id IS NOT NULL AND desordre_id IS NULL") == "1"
    assert query("SELECT count(*) FROM information_schema.columns WHERE table_schema='gemapi' AND table_name='photos' AND udt_name='geometry'") == "0"
    assert query("SELECT is_nullable FROM information_schema.columns WHERE table_schema='gemapi' AND table_name='photos' AND column_name='observation_id'") == "NO"
    assert query("SELECT max(n) FROM (SELECT count(*) n FROM gemapi.photos GROUP BY observation_id) g") == "2"
    assert query("SELECT count(*) FROM gemapi.photos p JOIN gemapi.observations o ON o.id=p.observation_id "
                 "WHERE p.date_photo=o.date_observation AND p.designation IS NOT NULL") == "3"
    keys = query("SELECT storage_key FROM gemapi.photos ORDER BY id").splitlines()
    assert sorted((ROOT/'demo'/key).is_file() for key in keys) == [False, True, True]
    assert query("BEGIN; UPDATE gemapi.desordres SET date_fin='2026-06-30' WHERE date_fin IS NULL; "
                 "SELECT count(*) FROM gemapi.photos p JOIN gemapi.observations o ON o.id=p.observation_id "
                 "JOIN gemapi.desordres d ON d.id=o.desordre_id WHERE d.date_fin IS NOT NULL; ROLLBACK;").splitlines()[-2] == "3"
    src = ogr.Open(str(ROOT/'demo/gpkg/sites_terrestres.gpkg'))
    for family, fk in (("mobilier","mobilier_id"),("cheminements","cheminement_id"),("vegetation","vegetation_id")):
        expected = {f["id"]:(f["site_terrestre_id"],f.GetGeometryRef().ExportToWkb().hex())
                    for f in src.GetLayerByName(family)}
        data = query(f"SELECT id,site_terrestre_id,encode(ST_AsBinary(geometry),'hex') FROM gemapi.{family} "
                     "WHERE num_nonnulls(ah_stockage_id,ah_section_id,ah_ouvrage_id,troncon_id)=0")
        assert {uid:(site,wkb) for uid,site,wkb in (line.split('|') for line in data.splitlines())} == expected
        # Exercise fallback for all three families without adding a historical prestation.
        clear = "vegetation_id=NULL, " if fk != "vegetation_id" else ""
        result = query(f"BEGIN; UPDATE gemapi.prestations SET {clear}{fk}=(SELECT id FROM gemapi.{family} ORDER BY id LIMIT 1) "
                       "WHERE vegetation_id IS NOT NULL; "
                       f"SELECT count(*) FROM gemapi.prestations p JOIN gemapi.{family} f ON p.{fk}=f.id "
                       "JOIN gemapi.prestations_carte c ON c.id=p.id WHERE p.geom IS NULL "
                       "AND p.origine_mesures='Patrimoine de rattachement' AND ST_OrderingEquals(c.geom,f.geometry); ROLLBACK;"
                       )
        assert "12" in result.splitlines()
    package = ogr.Open(str(ROOT/'demo/gpkg/prestations.gpkg'))
    rows = json.loads(query("""
        SELECT json_agg(to_jsonb(p) || jsonb_build_object('statut', s.code, 'quantite', p.quantite::text,
                                                          'wkb', encode(ST_AsBinary(p.geom),'hex')))
        FROM gemapi.prestations p JOIN gemapi.statuts_prestations s ON s.id=p.statut_id
    """))
    imported = {row["id"]: row for row in rows}
    assert len(imported) == 100
    for layer in package:
        for feature in layer:
            row = imported[feature["id"]]
            for field in feature.keys():
                assert row[field] == feature[field], (feature["id"], field)
            geom = feature.GetGeometryRef()
            assert row["wkb"] == (geom.ExportToWkb().hex() if geom is not None else None)
    # Independent digests for every business identity and geometry, including the frozen axes.
    digests = []
    for table in sorted(tables):
        value = "(to_jsonb(t)-ARRAY['cree_le','modifie_par','date_modification','date_validation'])::text"
        for (owner, col), (typ, _, _) in columns.items():
            if owner == table and typ == "geometry":
                value += f" || coalesce(encode(ST_AsEWKB({col}),'hex'),'NULL')"
        digests.append(f"SELECT '{table}' AS name, md5(string_agg(value,'|' ORDER BY value)) AS digest "
                       f"FROM (SELECT {value} AS value FROM {table} t) x")
    return query("SELECT * FROM (" + " UNION ALL ".join(digests) + ") hashes ORDER BY name")


def main():
    ogr.UseExceptions()
    from test_demo_topologie import main as check_sources
    check_sources()
    refused = command("bash", str(ROOT/"demo/data/importer_base_neuve.sh"), check=False)
    assert refused.returncode == 2
    snapshots = []
    with TemporaryDirectory(dir=ROOT / 'audits') as temporary:
        from pathlib import Path
        demo = Path(temporary) / 'demo'
        (demo / 'data').mkdir(parents=True)
        (demo / 'gpkg').mkdir()
        shutil.copy2(ROOT / 'demo/gpkg/digues.gpkg', demo / 'gpkg/digues.gpkg')
        shutil.copy2(ROOT / 'demo/data/generer_digues.py', demo / 'data/generer_digues.py')
        command(sys.executable, str(demo / 'data/generer_digues.py'))
        assert (demo / 'data/digues.sql').read_bytes() == (ROOT / 'demo/data/digues.sql').read_bytes()
        exports = (('cours_eau', 'cours_eau.gpkg', '11_cours_eau.sql'),
                   ('ah_sections', 'ah.gpkg', '12_ah_sections.sql'),
                   ('sites', 'sites_terrestres.gpkg', '10_sites_terrestres.sql'),
                   ('prestations', 'prestations.gpkg', '15_prestations.sql'))
        for stem, package, name in exports:
            shutil.copy2(ROOT/f'demo/gpkg/{package}',demo/f'gpkg/{package}')
            shutil.copy2(ROOT/f'demo/data/generer_{stem}.py',demo/f'data/generer_{stem}.py')
            command(sys.executable,str(demo/f'data/generer_{stem}.py'))
            if stem=='ah_sections':
                before=hashlib.sha256((demo/'gpkg'/package).read_bytes()).hexdigest()
                refused=command(sys.executable,str(demo/f'data/generer_{stem}.py'),
                                '--accept-local','1',check=False)
                assert refused.returncode and 'Section AH déjà présente' in refused.stderr
                assert hashlib.sha256((demo/'gpkg'/package).read_bytes()).hexdigest()==before
            assert (demo/'data'/name).read_bytes()==(ROOT/'demo/data'/name).read_bytes()
    multipart_count, commune_intersections, commune_groups = source_spatial_expectations()
    photos_package = ogr.Open(str(ROOT / "demo/gpkg/prestations.gpkg"), 0)
    photo_rows = []
    for photos_layer in photos_package:
        for item in photos_layer:
            photo_rows.append(tuple(item.GetField(name) for name in
                                    ("id", "photo_1", "photo_1_commentaire",
                                     "photo_2", "photo_2_commentaire")))
    photo_text = "\n".join("|".join("<NULL>" if value is None else value
                                      for value in row) for row in sorted(photo_rows))
    photo_hash = hashlib.md5(photo_text.encode("utf-8")).hexdigest()
    assert len(photo_rows) == 100
    photo_directory = ROOT / "demo/photos"
    local_prefix = "photos/"
    local_references = [value for row in photo_rows for value in (row[1], row[3])
                        if value and value.startswith(local_prefix)]
    windows_references = [value for row in photo_rows for value in (row[1], row[3])
                          if value and value.startswith("J:\\")]
    with (photo_directory / "photos_serveur.csv").open(
            newline="", encoding="utf-8-sig") as source:
        expected_windows = [row["chemin_photo"] for row in csv.DictReader(source)]
    assert len(local_references) == 45
    assert all((ROOT / "demo" / value).is_file()
               for value in local_references)
    assert sorted(windows_references) == sorted(expected_windows)
    assert all(value.count(local_prefix) == 1 for value in local_references)
    with ZipFile(ROOT / "demo/prestations_demo.qgz") as project:
        project_root = ElementTree.fromstring(project.read("prestations_demo.qgs"))
    layer = next(item for item in project_root.findall("./projectlayers/maplayer")
                 if '"prestations_fiches"' in item.findtext("datasource", ""))
    for photo_field in ("photo_1", "photo_2"):
        field = next(item for item in layer.findall("./fieldConfiguration/field")
                     if item.get("name") == photo_field)
        widget = field.find("./editWidget")
        assert widget is not None and widget.get("type") == "ExternalResource"
        options = {item.get("name"): item.get("value")
                   for item in widget.findall("./config/Option/Option")}
        assert options["DocumentViewer"] == "1"
        assert options["RelativeStorage"] == "0"
    photos_package = None
    package = ogr.Open(str(ROOT / "demo/gpkg/digues.gpkg"), 0)
    layer = package.GetLayerByName("digues") if package is not None else None
    assert layer is not None and layer.GetSpatialRef().GetAuthorityCode(None) == "3950"
    digue_features = []
    for feature in layer:
        geometry = feature.GetGeometryRef()
        assert geometry is not None and geometry.IsValid() and geometry.Length() > 0
        assert ogr.GT_Flatten(geometry.GetGeometryType()) == ogr.wkbLineString
        digue_features.append((feature.GetField("id"), feature.GetField("digue_id"),
                               geometry.ExportToWkb().hex(),feature.GetField("geom_riviere_signature")))
    assert len(digue_features) == 3
    assert len({row[0] for row in digue_features}) == 3
    assert len({row[1] for row in digue_features}) == 3
    package = None
    container = "sig-prestations-demo-" + uuid4().hex[:10]
    command("docker", "run", "--rm", "-d", "--name", container,
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgis/postgis:16-3.4")
    try:
        for _ in range(60):
            if command("docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres",
                       check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("PostGIS de test indisponible")
        sql(container, "SELECT 1;")  # Wait for initialization to stabilize.
        for extension_schema in ("public", "extensions"):
            database = "demo_" + extension_schema
            command("docker", "exec", container, "createdb", "-U", "postgres",
                    "-T", "template0", database)

            def query(statement):
                return command("docker", "exec", "-i", "-e",
                               "PGOPTIONS=-c search_path=pg_catalog,public,extensions,gemapi",
                               container, "psql", "-XAt",
                               "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", database,
                               input_text=statement).stdout.strip()

            query(f"CREATE SCHEMA IF NOT EXISTS {extension_schema}; "
                  f"CREATE EXTENSION postgis SCHEMA {extension_schema};")
            import_fixture(container, database)
            from demo_checks import check_database
            check_database(query)
            assert query("SELECT count(*) FROM gemapi.prestations;") == "100"
            assert query("SELECT count(*) FROM gemapi.ah_sections") == "1"
            assert query("SELECT id FROM gemapi.cours_eau WHERE direction IS NOT NULL") == "90368380-5d42-4a28-9889-6ae657f84a75"
            assert query("SELECT count(*) FROM gemapi.ah a CROSS JOIN LATERAL gemapi.ah_sections_candidates(a.id)") == "1"
            sections=ogr.Open(str(ROOT/'demo/gpkg/ah.gpkg'))
            section=next(iter(sections.GetLayerByName('ah_sections')))
            assert query("SELECT id::text||'|'||geom_cours_eau_signature||'|'||encode(ST_AsBinary(geom_reference),'hex') FROM gemapi.ah_sections") == '|'.join([section['id'],section['geom_cours_eau_signature'],section.GetGeometryRef().ExportToWkb().hex()])
            assert query("SELECT etat_reference FROM gemapi.ah_sections_carte") == 'CONFORME'
            # Replaying the accepted decision must not create duplicates or rewrite trace.
            query((ROOT/'demo/data/12_ah_sections.sql').read_text())
            assert query("SELECT count(*) FROM gemapi.ah_sections") == '1'
            assert query("SELECT count(*) FROM gemapi.prestations_fiches;") == "100"
            assert query("SELECT count(DISTINCT id) FROM gemapi.prestations;") == "100"
            assert query("SELECT md5(string_agg(id::text || '|' || "
                         "coalesce(photo_1,'<NULL>') || '|' || "
                         "coalesce(photo_1_commentaire,'<NULL>') || '|' || "
                         "coalesce(photo_2,'<NULL>') || '|' || "
                         "coalesce(photo_2_commentaire,'<NULL>'), E'\\n' ORDER BY id)) "
                         "FROM gemapi.prestations;") == photo_hash
            assert query("SELECT count(*) FILTER (WHERE photo_1 LIKE 'photos/%'),"
                         "count(*) FILTER (WHERE photo_1 LIKE 'J:%'),"
                         "count(*) FILTER (WHERE photo_1 IS NULL),"
                         "count(*) FILTER (WHERE photo_2 IS NOT NULL) "
                         "FROM gemapi.prestations;") == "25|25|50|35"
            assert query("SELECT count(*) FROM gemapi.prestations WHERE "
                         "photo_1 LIKE '/%' OR photo_2 LIKE '/%';") == "0"
            assert query("SELECT count(*) FROM gemapi.prestations WHERE "
                         "(photo_1 IS NULL AND (photo_1_commentaire IS NOT NULL OR "
                         "photo_2 IS NOT NULL OR photo_2_commentaire IS NOT NULL)) "
                         "OR (photo_2 IS NULL AND photo_2_commentaire IS NOT NULL);") == "0"
            assert query("SELECT count(*) FROM gemapi.prestations p "
                         "JOIN gemapi.statuts_prestations s ON s.id=p.statut_id "
                         "WHERE p.date_debut IS NOT NULL "
                         "AND p.date_fin >= p.date_debut;") == "100"
            assert query("SELECT min(date_debut)::text || '|' || max(date_fin)::text "
                         "FROM gemapi.prestations;") == "2026-01-01|2028-12-15"
            assert query("SELECT string_agg(code || ':' || n, ',' ORDER BY code) FROM ("
                         "SELECT s.code,count(*)::text AS n FROM gemapi.prestations p "
                         "JOIN gemapi.statuts_prestations s ON s.id=p.statut_id "
                         "GROUP BY s.code) counts") == "ANNULE:21,EN_COURS:26,PLANIFIE:30,TERMINE:23"
            assert query("SELECT count(*) FROM gemapi.prestations WHERE technicien_id IS NULL") == "0"
            assert query("SELECT count(DISTINCT technicien_id) FROM gemapi.prestations") == "4"
            assert query("SELECT count(*) FROM gemapi.prestations WHERE modifie_par=session_user "
                         "AND date_modification IS NOT NULL") == "100"
            if extension_schema == "public":
                query("CREATE ROLE data04_editor NOLOGIN; CREATE ROLE data04_login LOGIN; "
                      "GRANT data04_editor TO data04_login;")
            query(
                  "GRANT USAGE ON SCHEMA gemapi TO data04_editor; "
                  "GRANT SELECT, UPDATE ON gemapi.prestations TO data04_editor;")
            first_id = query("SELECT id::text FROM gemapi.prestations ORDER BY id LIMIT 1")
            query("SET SESSION AUTHORIZATION data04_login; SET ROLE data04_editor; "
                  "UPDATE gemapi.prestations SET modifie_par='injection' "
                  f"WHERE id='{first_id}'::uuid; RESET ROLE; RESET SESSION AUTHORIZATION;")
            assert query(f"SELECT modifie_par FROM gemapi.prestations WHERE id='{first_id}'") == "data04_login"
            rejected = command("docker", "exec", "-i", container, "psql", "-XAt",
                               "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", database,
                               input_text="INSERT INTO gemapi.prestations "
                               "(type_prestation_id,prestataire_id,statut_id,date_debut,date_fin,geom) "
                               "SELECT type_prestation_id,prestataire_id,statut_id,date_debut,date_fin,geom "
                               "FROM gemapi.prestations LIMIT 1;", check=False)
            assert rejected.returncode != 0 and "technicien_id" in rejected.stderr
            assert query("SELECT count(*) FROM information_schema.tables WHERE "
                         "table_schema='gemapi' AND table_name='prestations__techniciens'") == "0"
            for column, expected in (("ah_stockage_id","13"),("ah_ouvrage_id","6"),("cours_eau_id","62"),("digue_id","1"),("site_terrestre_id","6")):
                assert query(f"SELECT count({column}) FROM gemapi.prestations") == expected
            assert query("SELECT count(vegetation_id) FROM gemapi.prestations") == "12"
            assert query("SELECT to_regclass('gemapi.prestations__sites_zones') IS NULL") == "t"
            assert query("SELECT count(*) FROM gemapi.digues;") == "3"
            assert query("SELECT count(*) FROM gemapi.digues_reperes;") == "3"
            assert query("SELECT count(*) FROM gemapi.digues_carte;") == "3"
            assert query("SELECT count(*) FROM gemapi.digues_carte WHERE "
                         "etat_referentiel='COURS_EAU_NON_QUALIFIE' AND ST_SRID(geom)=3950 "
                         "AND ST_IsValid(geom) AND ST_Length(geom)>0;") == "3"
            assert query("SELECT tgenabled FROM pg_trigger WHERE tgrelid='gemapi.digues_reperes'::regclass AND tgname='digues_reperes_prepare_trg'") == "O"
            refused = command('docker','exec','-i',container,'psql','-XAt','-v','ON_ERROR_STOP=1',
                              '-U','postgres','-d',database,input_text=(
                                  "BEGIN; INSERT INTO gemapi.digues(nom) VALUES ('Nouveau refusé'); "
                                  "INSERT INTO gemapi.digues_reperes(digue_id,riviere_id,rive,debut_fraction,fin_fraction) "
                                  "SELECT d.id,r.id,'G',.1,.2 FROM gemapi.digues d CROSS JOIN gemapi.cours_eau r "
                                  "WHERE d.nom='Nouveau refusé' LIMIT 1; COMMIT;"),check=False)
            assert refused.returncode != 0 and 'non admissible' in refused.stderr
            assert query('SELECT count(*) FROM gemapi.digues') == '3'
            for uid, digue_id, wkb, signature in digue_features:
                assert query(f"SELECT encode(ST_AsBinary(geom_reference),'hex')='{wkb}' AND geom_riviere_signature='{signature}' FROM gemapi.digues_reperes WHERE id='{uid}'") == "t"
                distance = query(
                    "SELECT ST_HausdorffDistance(geom, "
                    f"ST_GeomFromWKB(decode('{wkb}','hex'),3950)) "
                    f"FROM gemapi.digues_carte WHERE id='{uid}' AND digue_id='{digue_id}';"
                )
                assert distance and float(distance) < 1e-6, (uid, distance)
            assert query("SELECT count(*) FROM gemapi.prestations WHERE geom IS NOT NULL;") == "88"
            assert query("SELECT count(*) FROM gemapi.prestations WHERE vegetation_id IS NOT NULL AND geom IS NULL") == "12"
            assert query("SELECT count(*) FROM gemapi.prestations_carte WHERE geom IS NOT NULL") == "100"
            assert query("SELECT count(*) FROM gemapi.prestations p JOIN gemapi.vegetation z ON z.id=p.vegetation_id JOIN gemapi.prestations_carte c ON c.id=p.id WHERE ST_OrderingEquals(c.geom,z.geometry)") == "12"
            assert query("SELECT count(*) FROM gemapi.prestations WHERE position='Indéfini';") == "100"
            assert query("SELECT count(*) FROM gemapi.prestations p WHERE "
                         "num_nonnulls(cours_eau_id,digue_id,ah_stockage_id,ah_ouvrage_id,site_terrestre_id,troncon_id,ah_section_id,vegetation_id,cheminement_id,mobilier_id)=1") == "100"
            assert query("SELECT count(DISTINCT ST_GeometryType(geom)) "
                         "FROM gemapi.prestations;") == "6"
            assert query("SELECT count(*) FROM gemapi.prestations WHERE "
                         "ST_GeometryType(geom) IN ('ST_MultiPoint','ST_MultiLineString',"
                         "'ST_MultiPolygon');") == str(multipart_count)
            assert query("SELECT count(*) FROM gemapi.prestations WHERE "
                         "NOT ST_IsValid(geom) OR ST_IsEmpty(geom) OR ST_SRID(geom)<>3950;") == "0"
            assert query("SELECT count(DISTINCT EXTRACT(YEAR FROM date_debut)) FROM "
                         "gemapi.prestations;") == "3"
            for column, expected in (("type_prestation_id", "9"),
                                     ("prestataire_id", "6"),
                                     ("technicien_id", "4"), ("statut_id", "4")):
                assert query(f"SELECT count(DISTINCT {column}) FROM gemapi.prestations;") == expected
            assert query("SELECT count(DISTINCT c.id)::text || '|' || count(*)::text "
                         "FROM gemapi.prestations_carte p JOIN gemapi.communes c "
                         "ON ST_Intersects(p.geom,c.geom);") == commune_intersections
            assert query("SELECT string_agg(n::text || ':' || groups::text,',' ORDER BY n) "
                         "FROM (SELECT n,count(*) groups FROM ("
                         "SELECT EXTRACT(YEAR FROM date_debut)::int,"
                         "type_prestation_id,statut_id,count(*) n "
                         "FROM gemapi.prestations GROUP BY 1,2,3) leaves "
                         "GROUP BY n) sizes;") == "1:4,2:4,3:24,4:4"
            for dimensions, expected in (("EXTRACT(YEAR FROM p.date_debut),"
                                          "p.type_prestation_id,p.statut_id", "36|100"),
                                         ("c.id,p.type_prestation_id,p.statut_id", commune_groups),
                                         ("p.prestataire_id,p.technicien_id,p.statut_id", "48|100")):
                join = ("JOIN gemapi.communes c ON ST_Intersects(p.geom,c.geom) "
                        if dimensions.startswith("c.id") else "")
                assert query("SELECT count(*)::text || '|' || sum(n)::text FROM ("
                             f"SELECT {dimensions},count(*) n FROM gemapi.prestations_carte p "
                             f"{join}GROUP BY 1,2,3) terminal;") == expected
            # Every seed table must have been populated, including the last import.
            expected_counts = {
                "techniciens": 4, "communes": 100, "prestataires": 6,
                "types_prestations": 9, "ah": 10, "ah_stockage": 10,
                "ah_ouvrage": 13, "sites_terrestres": 12,

                "cours_eau": 58,
            }
            for table, expected in expected_counts.items():
                assert int(query(f"SELECT count(*) FROM gemapi.{table};")) == expected, table
            assert query("SELECT count(*) FROM gemapi.cours_eau WHERE valide=false;") == "57"
            assert query("SELECT count(*) FROM information_schema.columns WHERE table_schema='gemapi' "
                         "AND table_name='cours_eau' AND column_name='code_topage';") == "0"
            assert query("SELECT data_type||'|'||is_nullable||'|'||is_generated "
                         "FROM information_schema.columns WHERE table_schema='gemapi' "
                         "AND table_name='cours_eau' AND column_name='valide';") == "boolean|NO|ALWAYS"
            null_result = command("docker", "exec", "-i", container, "psql", "-XAt",
                                  "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", database,
                                  input_text="UPDATE gemapi.cours_eau SET valide=NULL "
                                  "WHERE id=(SELECT id FROM gemapi.cours_eau LIMIT 1);",
                                  check=False)
            assert null_result.returncode != 0 and "valide" in null_result.stderr
            assert query("SELECT count(*) FROM (SELECT ah_id FROM gemapi.ah_ouvrage "
                         "GROUP BY ah_id HAVING count(*)>1) parents;") == "3"
            assert query("SELECT count(*) FROM gemapi.sites_terrestres s WHERE "
                         "NOT EXISTS (SELECT site_terrestre_id FROM gemapi.vegetation WHERE site_terrestre_id=s.id UNION ALL SELECT site_terrestre_id FROM gemapi.cheminements WHERE site_terrestre_id=s.id UNION ALL SELECT site_terrestre_id FROM gemapi.mobilier WHERE site_terrestre_id=s.id);") == "0"
            assert query("SELECT count(*) FROM gemapi.ah_stockage WHERE ST_IsEmpty(geom) "
                         "OR NOT ST_IsValid(geom) OR ST_GeometryType(geom)<>'ST_Polygon';") == "0"
            assert query("SELECT count(*) FROM gemapi.ah_ouvrage WHERE ST_IsEmpty(geom) "
                         "OR NOT ST_IsValid(geom) OR ST_GeometryType(geom)<>'ST_LineString';") == "0"
            snapshots.append(check_patrimony(query))
            rerun = import_fixture(container, database, check=False)
            assert rerun.returncode and "Base locale vide requise" in rerun.stderr
            print(f"Import complet du modèle v0.6.1 : PostGIS dans {extension_schema}, OK")
        assert snapshots[0] == snapshots[1], "Reconstructions divergentes"
    finally:
        command("docker", "stop", container, check=False)


if __name__ == "__main__":
    main()
