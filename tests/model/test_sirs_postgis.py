"""Qualification SQL indépendante de QGIS. Exécuter avec Python, psycopg2 et PostgreSQL 16+/PostGIS.

Chaque classe initialise un cluster privé, sans TCP, sans DSN externe. Les deux
installations vérifient aussi les droits avec de véritables sessions de connexion.
"""
import sys
import hashlib
import itertools
import json
import re
import subprocess
import tempfile
import threading
import time
import unittest
from collections import defaultdict
from contextlib import contextmanager, closing
from pathlib import Path
from uuid import uuid4

from test_roles_postgres import check_hydrography

import psycopg2

MODEL = Path(__file__).resolve().parents[2] / "model"
sys.path.insert(0, str(MODEL))
import install

FAMILIES = {
    "mobilier_id": ("gemapi.mobilier", "type_id", "PAN"),
    "cheminement_id": ("gemapi.cheminements", "type_cheminement_id", "PAS"),
    "vegetation_id": ("gemapi.vegetation", "famille_vegetation", "ARBRES"),
}
PARENTS = {
    "site_terrestre_id": "gemapi.sites_terrestres",
    "troncon_id": "gemapi.troncons",
    "ah_ouvrage_id": "gemapi.ah_ouvrage",
    "ah_stockage_id": "gemapi.ah_stockage",
    "ah_section_id": "gemapi.ah_sections",
}
PHYSICAL = {"INDETERMINE", "BERGE", "CRETE", "TALUS", "TALUS_RISBERME", "RISBERME", "FRANC_BORD"}
LINE = "SRID=3950;LINESTRING(0 0,3 4)"
AREA = "SRID=3950;POLYGON((0 0,2 0,2 3,0 3,0 0))"
MULTI = "SRID=3950;MULTIPOINT((1 1),(2 2))"


def projection():
    text = (MODEL / "schema_v0.6.1.mmd").read_text()
    aliases = dict(re.findall(r'(\w+)\["([\w.]+)"\]', text))
    columns = {}
    for alias, body in re.findall(r'(\w+)\["[\w.]+"\] \{(.*?)\n    }', text, re.S):
        for line in body.strip().splitlines():
            declaration, _, note = line.strip().partition(' "')
            typ, col, *keys = declaration.split()
            columns[aliases[alias], col] = (typ, ' '.join(keys), note.rstrip('"'))
    labels = dict(regroupe="categorie_id", responsable="technicien_id", realise="prestataire_id",
                  classifie="type_prestation_id", qualifie="statut_id", stockage_facultatif="ah_id",
                  ouvrages="ah_id", sections_acceptees="ah_id", support="cours_eau_id",
                  contient="digue_id", repere="riviere_id")
    relations = set()
    for parent, child, label in re.findall(r'^    (\w+) [|o.{\-]+ (\w+) : "?([\w, ]+)"?$', text, re.M):
        child_cols = tuple(labels.get(label, label).split(", "))
        relations.add((aliases[child], child_cols, aliases[parent],
                       ("id", "type_troncon_id") if len(child_cols) == 2 else ("id",)))
    seeds = defaultdict(list)
    for encoded in re.findall(r'^    %% VALUE (.*)$', text, re.M):
        row = json.loads(encoded)
        seeds[aliases[row.pop("table")]].append(row)
    return set(aliases.values()), columns, relations, seeds


def insert(cursor, table, **fields):
    columns = ", ".join(fields)
    params = ", ".join(["%s"] * len(fields))
    cursor.execute(f"INSERT INTO {table} ({columns}) VALUES ({params}) RETURNING id", tuple(fields.values()))
    return cursor.fetchone()[0]


def family(cursor, spec, parents, **values):
    table, type_col, type_value = spec
    return insert(cursor, table, **{"designation": "Essai", type_col: type_value, "geometry": LINE,
                                     **parents, **values})


def fixtures(c):
    ids = {}
    ids["prestataire_id"] = insert(c, "gemapi.prestataires", nom="Entreprise")
    ids["technicien_id"] = insert(c, "gemapi.techniciens", nom="Responsable")
    for col, table in [("statut_id", "statuts_prestations"), ("type_prestation_id", "types_prestations")]:
        c.execute(f"SELECT id FROM gemapi.{table} ORDER BY id LIMIT 1")
        ids[col] = c.fetchone()[0]
    ids["cours_eau_id"] = insert(c, "gemapi.cours_eau", nom="Axe", source="Essai", direction=True,
                                 geom="SRID=3950;MULTILINESTRING((0 0,10 0))")
    ids["digue_id"] = insert(c, "gemapi.digues", nom="Digue")
    insert(c, "gemapi.digues_reperes", digue_id=ids["digue_id"], riviere_id=ids["cours_eau_id"],
           rive="G", debut_fraction=0.1, fin_fraction=0.9)
    ah = insert(c, "gemapi.ah", nom="AH")
    ids["ah_stockage_id"] = insert(c, "gemapi.ah_stockage", ah_id=ah, nom="Stockage", geom=AREA)
    ids["ah_ouvrage_id"] = insert(c, "gemapi.ah_ouvrage", ah_id=ah, geom=LINE)
    ids["ah_section_id"] = insert(c, "gemapi.ah_sections", ah_id=ah, cours_eau_id=ids["cours_eau_id"],
                                  debut_fraction=0.2, fin_fraction=0.8)
    ids["site_terrestre_id"] = insert(c, "gemapi.sites_terrestres", nom="Site",
                                      geom="SRID=3950;MULTIPOLYGON(((0 0,2 0,2 2,0 0)))")
    ids["troncon_id"] = insert(c, "gemapi.troncons", digue_id=ids["digue_id"], type_troncon_id="BERGE")
    for key, spec in FAMILIES.items():
        ids[key] = family(c, spec, {"site_terrestre_id": ids["site_terrestre_id"]})
    ids["ouvrage_hydraulique_id"] = insert(c, "gemapi.ouvrages_hydrauliques", troncon_id=ids["troncon_id"])
    ids["ouvrage_particulier_id"] = insert(c, "gemapi.ouvrages_particuliers", troncon_id=ids["troncon_id"])
    c.execute("SELECT id FROM gemapi.types_equipement_mesure ORDER BY id LIMIT 1")
    ids["equipement_mesure_id"] = insert(c, "gemapi.equipements_mesure", type_id=c.fetchone()[0])
    ids["desordre_id"] = insert(c, "gemapi.desordres", designation="Fissure")
    return ids


class Qualification:
    @classmethod
    def connect(cls, login="postgres", database="qualification"):
        db = psycopg2.connect(host=str(cls.directory), port=5432, dbname=database, user=login,
                              options=f"-c search_path=pg_catalog,{cls.extension},gemapi -c statement_timeout=15000")
        with db.cursor() as c:
            c.execute("SELECT inet_server_addr() IS NULL, current_database()")
            destination = c.fetchone()
        db.rollback()
        if destination != (True, database) or db.info.host != str(cls.directory):
            db.close()
            raise RuntimeError(f"Destination de qualification inattendue {destination}")
        return db

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="gemapi-sirs-pg-")
        cls.addClassCleanup(temporary.cleanup)
        cls.directory = Path(temporary.name).resolve()
        data = cls.directory / "data"
        binaries = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
        subprocess.run([binaries / "initdb", "-D", data, "-U", "postgres", "-A", "trust",
                        "--no-locale", "--encoding=UTF8"], check=True, stdout=subprocess.DEVNULL)
        pg_ctl = [binaries / "pg_ctl", "-D", data]
        try:
            subprocess.run([*pg_ctl, "-l", cls.directory / "log", "-o",
                            f"-c listen_addresses='' -k '{cls.directory}' -p 5432", "-w", "start"], check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError((cls.directory / "log").read_text()) from exc
        cls.addClassCleanup(subprocess.run, [*pg_ctl, "-m", "fast", "-w", "stop"], check=True)
        with closing(cls.connect(database="postgres")) as admin:
            admin.autocommit = True
            with admin.cursor() as c:
                c.execute("SHOW data_directory")
                assert Path(c.fetchone()[0]).resolve() == data
                c.execute("CREATE DATABASE qualification")
        with closing(cls.connect()) as db, db.cursor() as c:
            db.autocommit = True
            # The extension installation may take longer than an individual assertion.
            c.execute("SET statement_timeout = '5min'")
            if cls.extension == "public":
                c.execute("CREATE EXTENSION postgis WITH SCHEMA public")
            else:
                c.execute("CREATE SCHEMA extensions; CREATE EXTENSION postgis WITH SCHEMA extensions")
            c.execute(install.transaction(install.install("gemapi")))
            for group in install.PROFILES:
                c.execute(f"CREATE ROLE {group} NOLOGIN")
            c.execute("GRANT milieux_consultation TO milieux_prestations; GRANT milieux_prestations TO milieux_patrimoine")
            c.execute(f"GRANT USAGE ON SCHEMA {cls.extension} TO milieux_consultation")
            c.execute(install.transaction(install.permissions("gemapi", install.PROFILES)))
            c.execute("SELECT current_setting('server_version'), PostGIS_Lib_Version()")
            print(f"Qualification {cls.extension}, PostgreSQL/PostGIS {c.fetchone()}", flush=True)
            for role in ("consultation", "prestations", "patrimoine"):
                c.execute(f"CREATE ROLE test_{role} LOGIN IN ROLE milieux_{role}")
            db.autocommit = False
            cls.ids = fixtures(c)
            db.commit()

    def setUp(self):
        self.db = self.connect()
        self.addCleanup(self.db.close)
        self.c = self.db.cursor()
        self.addCleanup(self.c.close)

    def rows(self, statement, params=()):
        self.c.execute(statement, params or None)
        return self.c.fetchall()

    def one(self, statement, params=()):
        return self.rows(statement, params)[0][0]

    @contextmanager
    def rejected(self, code, constraint=None, message=None):
        self.c.execute("SAVEPOINT refused")
        with self.assertRaises(psycopg2.Error) as error:
            yield
        self.c.execute("ROLLBACK TO SAVEPOINT refused; RELEASE SAVEPOINT refused")
        self.assertEqual(error.exception.pgcode, code)
        if message:
            self.assertIn(message, str(error.exception))
        if constraint:
            self.assertEqual(error.exception.diag.constraint_name, constraint)

    def prestation(self, c, **values):
        fields = {k: self.ids[k] for k in ("technicien_id", "prestataire_id", "statut_id", "type_prestation_id")}
        return insert(c, "gemapi.prestations", **fields, date_debut="2026-01-01", date_fin="2026-01-01",
                      quantite=7, unite="unités déclarées", **values)

    def test_catalogue(self):
        self.assertEqual(hashlib.sha256((MODEL / "schema_v0.6.1.mmd").read_bytes()).hexdigest(),
                         "acb6ddf92ea02fe613eec553ea8614ba7a1ae49435fed81a14cbaf493a09b06d")
        tables, cols, fks, seeds = projection()
        self.assertEqual((len(tables), len(cols), len(fks), len(seeds), sum(map(len, seeds.values()))),
                         (48, 431, 109, 22, 298))
        associations = {t for t in tables if "__" in t}
        self.assertEqual((len(associations), len(tables - seeds.keys() - associations)), (4, 22))
        self.assertEqual(self.one("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname IN ('public','milieux','referentiel') AND c.relname=ANY(%s)""",
              ([t.split(".")[1] for t in tables],)), 0)
        actual = self.rows("""
            SELECT n.nspname||'.'||c.relname, a.attname, format_type(a.atttypid,a.atttypmod),
                   a.attnotnull, pg_get_expr(d.adbin,d.adrelid), a.attgenerated
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped
              AND n.nspname='gemapi'
              AND NOT EXISTS (SELECT FROM pg_depend dep WHERE dep.classid='pg_class'::regclass
                              AND dep.objid=c.oid AND dep.deptype='e')""")
        self.assertEqual({(t, c) for t, c, *_ in actual}, set(cols))
        uk, pk, spatial = set(), defaultdict(list), set()
        for (table, column), (_, tags, _) in cols.items():
            if "PK" in tags:
                pk[table].append(column)
        for t, c, dtype, required, default, generated in actual:
            typ, tags, note = cols[t,c]
            if "UK" in tags:
                uk.add((t, (c,)))
            if typ == "geometry":
                match = re.search(r'geometry\((\w+),3950\)', note, re.I)
                typ = f"geometry({match[1] if match else 'Geometry'},3950)"
                spatial.add((t, c))
            typ = {"double": "double precision", "double_precision": "double precision",
                   "timestamptz": "timestamp with time zone"}.get(typ, typ)
            self.assertEqual((dtype.lower(), required), (typ.lower(), "NOT NULL" in note or "PK" in tags), (t,c))
            if c == "cree_le":
                self.assertEqual(default, "clock_timestamp()")
            if c == "version":
                self.assertEqual(default, "1")
            if c in {"section", "valide", "berge_id_type"} or (t,c)==("gemapi.prestations","surface_ha"):
                self.assertEqual(generated, "s")
        constraints = self.rows("""
            SELECT n.nspname||'.'||t.relname, co.contype,
                ARRAY(SELECT attname FROM unnest(co.conkey) WITH ORDINALITY k(i,o)
                      JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.i ORDER BY k.o),
                pn.nspname||'.'||p.relname,
                ARRAY(SELECT attname FROM unnest(co.confkey) WITH ORDINALITY k(i,o)
                      JOIN pg_attribute a ON a.attrelid=p.oid AND a.attnum=k.i ORDER BY k.o),
                co.condeferrable,co.condeferred,co.confdeltype
            FROM pg_constraint co JOIN pg_class t ON t.oid=co.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_class p ON p.oid=co.confrelid
            LEFT JOIN pg_namespace pn ON pn.oid=p.relnamespace
            WHERE n.nspname||'.'||t.relname=ANY(%s) AND co.contype IN ('p','u','f')""", (list(tables),))
        got = {(t,tuple(cs),p,tuple(ps)) for t,k,cs,p,ps,*_ in constraints if k=="f"}
        self.assertEqual(got, fks)
        self.assertEqual(sum(row[1]=="f" for row in constraints),109)
        self.assertEqual({(t,tuple(cs)) for t,k,cs,*_ in constraints if k=="p"},
                         {(t,tuple(cs)) for t,cs in pk.items()})
        uk.update({("gemapi.troncons", ("id","type_troncon_id")),
                   ("gemapi.ah_sections", ("ah_id","cours_eau_id","debut_fraction","fin_fraction"))})
        self.assertEqual({(t,tuple(cs)) for t,k,cs,*_ in constraints if k=="u"},uk)
        for t,k,cs,p,ps,defer,initial,action in constraints:
            if k=="f":
                composite = len(cs)==2
                self.assertEqual((defer,initial,action),(composite,composite,"a" if composite else "r"))
        metier = tables - set(seeds) - {t for t in tables if "__" in t}
        self.assertEqual(set(self.rows("""
            SELECT n.nspname||'.'||c.relname FROM pg_trigger tr JOIN pg_class c ON c.oid=tr.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE tr.tgfoid='gemapi.stamp_modification()'::regprocedure AND tr.tgnargs=1""")), {(t,) for t in metier})
        self.assertEqual(self.rows("""SELECT condeferrable,condeferred FROM pg_constraint
            WHERE conrelid='gemapi.prestations'::regclass AND conname='prestation_relations_parent'"""), [(True,True)])
        indexed = self.rows("""SELECT n.nspname||'.'||t.relname,a.attname FROM pg_index i
            JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace
            JOIN pg_class ix ON ix.oid=i.indexrelid JOIN pg_am am ON am.oid=ix.relam
            JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=i.indkey[0] WHERE am.amname='gist'""")
        self.assertLessEqual(spatial, set(indexed))
        for t, rows in seeds.items():
            keys = list(rows[0])
            self.assertCountEqual(self.rows(f"SELECT {', '.join(keys)} FROM {t}"),
                                  [tuple(r[k] for k in keys) for r in rows], t)
        self.assertEqual(set(self.rows("SELECT id FROM gemapi.types_troncon")), {(x,) for x in PHYSICAL})
        self.assertEqual(set(self.rows("SELECT abrege FROM gemapi.typologies_troncon")),
                         {(x,) for x in ("TER","TEN","PAL","GCI","AUT")})

    def test_schema_attribue_et_coexistence(self):
        sys.path[:0] = [str(MODEL.parent), str(MODEL.parent / "webapp/backend")]
        from digues_webapp.database import MODEL_STRUCTURE, WebDatabaseSchemaError, prepare_model

        other = """SIG O'Brien "% $$"""
        dest = install.identifier(other)
        self.c.execute("CREATE TABLE public.temoin(id int); INSERT INTO public.temoin VALUES (19)")
        self.c.execute("CREATE ROLE installateur NOLOGIN")
        self.c.execute(f"CREATE SCHEMA {dest} AUTHORIZATION installateur")
        self.c.execute(f"GRANT USAGE ON SCHEMA {self.extension} TO installateur")
        self.c.execute("SET ROLE installateur")
        self.c.execute(install.install(other))
        self.c.execute(install.permissions(other, install.PROFILES))
        self.c.execute("RESET ROLE")
        self.c.execute(f"INSERT INTO {dest}.sites_terrestres(nom,geom) VALUES "
                       "('Site isolé','SRID=3950;MULTIPOLYGON(((0 0,2 0,2 2,0 0)))')")
        self.assertEqual(self.one(f"SELECT count(*) FROM {dest}.prestations_carte"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM gemapi.sites_terrestres WHERE nom='Site isolé'"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM pg_constraint WHERE contype='f' "
                                  "AND connamespace=(SELECT oid FROM pg_namespace WHERE nspname=%s) "
                                  "AND (SELECT relnamespace FROM pg_class WHERE oid=confrelid)<>connamespace", (other,)), 0)
        self.assertEqual(self.one("SELECT id FROM public.temoin"), 19)
        self.c.execute("SET ROLE test_consultation")
        self.c.execute(MODEL_STRUCTURE, (other,))
        fingerprint, readable = self.c.fetchone()
        print("Structure v0.6.1", fingerprint, flush=True)
        self.assertTrue(readable)
        prepare_model(self.c, other)
        self.assertEqual(self.one("SELECT count(*) FROM sites_terrestres"), 1)
        with self.assertRaisesRegex(WebDatabaseSchemaError, "absent"):
            prepare_model(self.c, "absent")
        with self.assertRaisesRegex(WebDatabaseSchemaError, "incompatible"):
            prepare_model(self.c, "public")
        self.assertFalse(self.one("SELECT has_schema_privilege(current_user,%s,'CREATE')", (other,)))
        self.c.execute("RESET ROLE")
        with self.rejected("P0001", message="schéma occupé"):
            self.c.execute(install.install(other))
        self.assertEqual(self.one("SELECT id FROM public.temoin"), 19)
        self.c.execute(f"GRANT CREATE ON SCHEMA {dest} TO PUBLIC")
        with self.rejected("P0001", message="CREATE accordé"):
            self.c.execute(install.install(other))

    def test_postgis_absent_sans_modification(self):
        with closing(self.connect(database="postgres")) as empty, empty.cursor() as c:
            with self.assertRaisesRegex(psycopg2.Error, "PostGIS absent"):
                c.execute(install.transaction(install.install("sans_extension")))
            empty.rollback()
            c.execute("SELECT to_regnamespace('sans_extension'), EXISTS "
                      "(SELECT FROM pg_catalog.pg_extension WHERE extname='postgis')")
            self.assertEqual(c.fetchone(), (None, False))

    def test_droits_par_defaut_du_schema_neuf(self):
        with self.assertRaisesRegex(ValueError, "réservé"):
            install.install("$user")
        self.c.execute("ALTER DEFAULT PRIVILEGES GRANT CREATE ON SCHEMAS TO PUBLIC")
        with self.rejected("P0001", message="CREATE accordé"):
            self.c.execute(install.install("destination_ouverte"))
        self.assertIsNone(self.one("SELECT to_regnamespace('destination_ouverte')"))

    def test_installation_sur_base_occupee(self):
        # Le script gère sa propre transaction. Une deuxième installation doit laisser le premier état intact.
        with self.assertRaisesRegex(psycopg2.Error, "Installation neuve requise"):
            self.c.execute(install.transaction(install.install("gemapi")))
        self.db.rollback()
        self.assertEqual(self.one("SELECT count(*) FROM gemapi.types_troncon"), 7)

    def test_cinq_parents(self):
        for logical, spec in FAMILIES.items():
            table = spec[0]
            for mask in itertools.product((False, True), repeat=5):
                chosen = {c:self.ids[c] for c,flag in zip(PARENTS,mask) if flag}
                with self.subTest(family=logical, mask=mask):
                    if len(chosen)!=1:
                        with self.rejected("23514", table.split(".")[1]+"_parent_unique"):
                            family(self.c, spec, chosen)
                        continue
                    row = family(self.c, spec, chosen)
                    col = next(iter(chosen))
                    with self.rejected("23503"):
                        self.c.execute(f"DELETE FROM {PARENTS[col]} WHERE id=%s",(chosen[col],))
                    with self.rejected("23503"):
                        self.c.execute(f"UPDATE {table} SET {col}=%s WHERE id=%s",(str(uuid4()), row))
                    other = next(name for name in PARENTS if name!=col)
                    self.c.execute(f"UPDATE {table} SET {col}=NULL,{other}=%s WHERE id=%s",(self.ids[other],row))
                    self.assertEqual(self.one(f"SELECT version FROM {table} WHERE id=%s",(row,)),2)
            parent = {"site_terrestre_id":self.ids["site_terrestre_id"]}
            for shape in ["SRID=3950;POINT EMPTY", "SRID=3950;POLYGON((0 0,2 2,0 2,2 0,0 0))",
                          "SRID=3950;GEOMETRYCOLLECTION(POINT(1 1))"]:
                with self.rejected("23514"):
                    family(self.c,spec,parent,geometry=shape)
            for shape in ("SRID=4326;POINT(0 0)","SRID=3950;POINT Z(0 0 0)"):
                with self.rejected("22023"):
                    family(self.c,spec,parent,geometry=shape)
            with self.rejected("23514"):
                family(self.c,spec,parent,designation="   ")
            with self.rejected("23514"):
                family(self.c,spec,parent,date_debut="2026-02-01",date_fin="2026-01-01")
            with self.rejected("23514"):
                self.c.execute(f"UPDATE {table} SET cree_le=now() WHERE id=%s",(self.ids[logical],))

    def test_ouvrages_et_berge(self):
        parents = ["troncon_id","ah_ouvrage_id","ah_stockage_id"]
        for mask in itertools.product((False,True),repeat=3):
            fields = {c:self.ids[c] if flag else None for c,flag in zip(parents,mask)}
            if sum(mask)==1:
                insert(self.c,"gemapi.ouvrages_hydrauliques",**fields)
            else:
                with self.rejected("23514","ck_ouvrage_parent_unique"):
                    insert(self.c,"gemapi.ouvrages_hydrauliques",**fields)
        for kind in sorted(PHYSICAL):
            uid=insert(self.c,"gemapi.troncons",digue_id=self.ids["digue_id"],type_troncon_id=kind,valid=True,geometry=LINE)
            insert(self.c,"gemapi.observations",troncon_id=uid,date_observation="2026-01-01")
            def link():
                insert(self.c,"gemapi.ouvrages_hydrauliques__objets_lies",
                       ouvrage_hydraulique_id=self.ids["ouvrage_hydraulique_id"],berge_id=uid)
                self.c.execute("SET CONSTRAINTS ALL IMMEDIATE")
            if kind=="BERGE":
                link()
            else:
                with self.rejected("23503","fk_ouvrage_lie_berge"):
                    link()
            self.c.execute("SET CONSTRAINTS ALL DEFERRED")
        for kind in ("RISBERME","FRANC_BORD"):
            with self.rejected("23514","ck_troncons_geometry_b4"):
                insert(self.c,"gemapi.troncons",digue_id=self.ids["digue_id"],type_troncon_id=kind,valid=True)
        with self.rejected("23514","ck_troncons_crete_fields"):
            insert(self.c,"gemapi.troncons",digue_id=self.ids["digue_id"],type_troncon_id="CRETE",
                   cote_id=self.one("SELECT id FROM gemapi.cotes LIMIT 1"))
        with self.rejected("23514","ck_troncons_axe_reperage"):
            insert(self.c,"gemapi.troncons",digue_id=self.ids["digue_id"],type_troncon_id="BERGE",
                   geometry_reperage="SRID=3950;LINESTRING EMPTY")

    def test_desordres_observations_photos(self):
        direct = ["digue_id", *PARENTS, *FAMILIES]
        obs = ["desordre_id", "equipement_mesure_id", "ouvrage_particulier_id", "ouvrage_hydraulique_id",
               *PARENTS, *FAMILIES]
        self.assertEqual((len(direct), len(obs)), (9, 12))
        for target in direct:
            insert(self.c, "gemapi.desordres", **{target:self.ids[target]})
        for a,b in itertools.combinations(direct, 2):
            with self.rejected("23514", "ck_desordre_cible_directe"):
                insert(self.c, "gemapi.desordres", **{k:self.ids[k] for k in (a,b)})
        for target in obs:
            insert(self.c,"gemapi.observations",date_observation="2026-02-01",**{target:self.ids[target]})
        for pair in [(), *itertools.combinations(obs, 2)]:
            with self.rejected("23514","ck_cible_unique"):
                insert(self.c,"gemapi.observations",date_observation="2026-02-01",**{k:self.ids[k] for k in pair})
        disorder = self.ids["desordre_id"]
        with self.rejected("23502"):
            insert(self.c, "gemapi.observations", desordre_id=disorder)
        uid = insert(self.c,"gemapi.observations",date_observation="2026-02-01",desordre_id=disorder)
        photo = insert(self.c,"gemapi.photos",observation_id=uid,storage_key="constats/essai.jpg")
        with self.rejected("23505"):
            insert(self.c,"gemapi.photos",observation_id=uid,storage_key="constats/essai.jpg")
        self.c.execute("UPDATE gemapi.desordres SET date_fin='2026-03-01' WHERE id=%s",(disorder,))
        self.assertEqual(self.one("""SELECT p.id FROM gemapi.photos p JOIN gemapi.observations o ON o.id=p.observation_id
              JOIN gemapi.desordres d ON d.id=o.desordre_id WHERE d.id=%s AND d.date_fin IS NOT NULL""",(disorder,)),photo)
        other = insert(self.c,"gemapi.desordres",designation="Actif")
        self.assertTrue(self.one("SELECT date_fin IS NULL FROM gemapi.desordres WHERE id=%s",(other,)))
        self.c.execute("INSERT INTO gemapi.equipements_mesure__desordres VALUES (%s,%s)",
                       (self.ids["equipement_mesure_id"],disorder))
        self.c.execute("UPDATE gemapi.equipements_mesure__desordres SET desordre_id=%s WHERE desordre_id=%s",(other,disorder))
        self.assertEqual(self.one("SELECT version FROM gemapi.desordres WHERE id=%s",(disorder,)),4)
        self.assertEqual(self.one("SELECT version FROM gemapi.desordres WHERE id=%s",(other,)),2)
        with self.rejected("23514"):
            insert(self.c,"gemapi.desordres",version=9007199254740992)
        with self.rejected("23514"):
            self.c.execute("UPDATE gemapi.observations SET cree_le=now() WHERE id=%s",(uid,))

    def test_prestations(self):
        own = ["cours_eau_id","digue_id","troncon_id","ah_section_id","site_terrestre_id"]
        fallback = ["ah_ouvrage_id","ah_stockage_id",*FAMILIES]
        for target in own + fallback:
            with self.subTest(target=target):
                uid = self.prestation(self.c, **{target:self.ids[target]}, geom=MULTI)
                self.c.execute("SET CONSTRAINTS ALL IMMEDIATE")
                self.assertEqual(self.rows("SELECT nombre_points,origine_mesures,quantite FROM gemapi.prestations_fiches WHERE id=%s",
                                           (uid,)), [(2,"Prestation",7)])
                if target in own:
                    with self.rejected("23514"):
                        self.prestation(self.c, **{target:self.ids[target]})
                else:
                    uid = self.prestation(self.c, **{target:self.ids[target]})
                    self.assertEqual(self.one("SELECT origine_mesures FROM gemapi.prestations WHERE id=%s",(uid,)),
                                     "Patrimoine de rattachement")
        self.c.execute("SET CONSTRAINTS ALL DEFERRED")
        empty = self.prestation(self.c)
        with self.rejected("23514"):
            self.c.execute("SET CONSTRAINTS ALL IMMEDIATE")
        self.c.execute("UPDATE gemapi.prestations SET mobilier_id=%s WHERE id=%s",(self.ids["mobilier_id"],empty))
        self.c.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with self.rejected("23514","prestations_direct_modes"):
            self.c.execute("UPDATE gemapi.prestations SET vegetation_id=%s WHERE id=%s",(self.ids["vegetation_id"],empty))
        with self.rejected("23514"):
            self.c.execute("UPDATE gemapi.prestations SET position='Berge' WHERE id=%s",(empty,))
        with self.rejected("23514","prestations_site_multi_geometry"):
            self.prestation(self.c,site_terrestre_id=self.ids["site_terrestre_id"],geom=LINE)

    def test_repli_et_agregations(self):
        for key,(table,_,_) in FAMILIES.items():
            child = self.ids[key]
            inherited = self.prestation(self.c, **{key:child})
            own = self.prestation(self.c, **{key:child},geom=MULTI)
            trace = self.one("SELECT date_modification FROM gemapi.prestations WHERE id=%s",(inherited,))
            for shape, expected in [(LINE,(None,5,None)),(AREA,(None,None,6)),(MULTI,(2,None,None))]:
                self.c.execute(f"UPDATE {table} SET geometry=%s WHERE id=%s",(shape,child))
                self.assertEqual(self.rows("""SELECT p.nombre_points,p.longueur_m,p.surface_m2,p.quantite,
                    p.origine_mesures,p.date_modification,p.geom IS NULL,ST_Equals(v.geom,%s::geometry)
                    FROM gemapi.prestations p JOIN gemapi.prestations_carte v USING(id) WHERE p.id=%s""",(shape,inherited)),
                    [(*expected,7,"Patrimoine de rattachement",trace,True,True)])
                self.assertEqual(self.rows("SELECT nombre_points,origine_mesures FROM gemapi.prestations WHERE id=%s",(own,)),
                                 [(2,"Prestation")])
            self.assertEqual(self.one("SELECT count(*) FROM gemapi.prestations__sites_terrestres WHERE prestation_id=%s",
                                      (inherited,)),1)
            for parent in ("ah_stockage_id","ah_section_id"):
                previous = "site_terrestre_id" if parent=="ah_stockage_id" else "ah_stockage_id"
                self.c.execute(f"UPDATE {table} SET {previous}=NULL,{parent}=%s WHERE id=%s",(self.ids[parent],child))
                for view in ("prestations__sites_terrestres","prestations__ah"):
                    self.assertEqual(self.one(f"SELECT count(*) FROM gemapi.{view} WHERE prestation_id=%s",(inherited,)),0)
        for col in ("ah_ouvrage_id","ah_stockage_id","ah_section_id"):
            uid=self.prestation(self.c,**{col:self.ids[col]},geom=LINE)
            self.assertEqual(self.one("SELECT count(*) FROM gemapi.prestations__ah WHERE prestation_id=%s",(uid,)),1)

    def test_profils_et_tracabilite(self):
        tables,_,_,_ = projection()
        baseline={"gemapi."+t for t in ("cours_eau","digues","digues_reperes","ah","ah_stockage","ah_ouvrage",
                                         "ah_sections","sites_terrestres")}
        writable = baseline | {"gemapi." + t for t in (
            "troncons", "mobilier", "cheminements", "vegetation", "desordres", "observations", "photos",
            "ouvrages_particuliers", "equipements_mesure", "ouvrages_hydrauliques")} | {t for t in tables if "__" in t}
        views = [row[0] for row in self.rows("SELECT 'gemapi.'||viewname FROM pg_views WHERE schemaname='gemapi'")]
        self.assertEqual(len(views),6)
        for role in ("consultation","prestations","patrimoine"):
            with closing(self.connect("test_"+role)) as db, db.cursor() as c:
                for t in sorted(tables | set(views)):
                    c.execute(f"SELECT count(*) FROM {t}")
                    expected = ((t=="gemapi.prestations" and role!="consultation") or
                                (t in writable and role=="patrimoine"))
                    for priv in ("INSERT","UPDATE","DELETE","TRUNCATE"):
                        c.execute("SELECT has_table_privilege(%s,%s)",(t,priv))
                        self.assertEqual(c.fetchone()[0],expected and priv!="TRUNCATE",(role,t,priv))
                if role=="consultation":
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        self.prestation(c,mobilier_id=self.ids["mobilier_id"])
                else:
                    uid=self.prestation(c,mobilier_id=self.ids["mobilier_id"])
                    c.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    c.execute("SELECT modifie_par,cree_le<=date_modification FROM gemapi.prestations WHERE id=%s",(uid,))
                    self.assertEqual(c.fetchone(),("test_"+role,True))
                    if role=="patrimoine":
                        c.execute("UPDATE gemapi.mobilier SET geometry=%s WHERE id=%s",(AREA,self.ids["mobilier_id"]))
                        c.execute("SELECT surface_m2,quantite FROM gemapi.prestations_fiches WHERE id=%s",(uid,))
                        self.assertEqual(c.fetchone(),(6,7))
                    else:
                        with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                            c.execute("UPDATE gemapi.mobilier SET geometry=%s WHERE id=%s",(AREA,self.ids["mobilier_id"]))

    def test_reperage_et_digue_supprimer(self):
        with closing(self.connect()) as admin, admin.cursor() as c:
            ah = insert(c, "gemapi.ah", nom="Qualification des révisions")
            admin.commit()
        check_hydrography(self.connect, "test_patrimoine", {"ah":ah, "river":self.ids["cours_eau_id"]})
        digue = insert(self.c, "gemapi.digues", nom="Qualification de suppression")
        repere = insert(self.c, "gemapi.digues_reperes", digue_id=digue, riviere_id=self.ids["cours_eau_id"],
                        rive="D", debut_fraction=0.12, fin_fraction=0.67)
        stamp = self.one("SELECT date_modification FROM gemapi.digues WHERE id=%s", (digue,))
        for table, fields, diagnostic in [
            ("gemapi.troncons", {"type_troncon_id": "BERGE"}, "tronçon"),
            ("gemapi.desordres", {}, "désordre"),
        ]:
            child = insert(self.c, table, digue_id=digue, **fields)
            with self.rejected("23503", message=diagnostic):
                self.c.execute("SELECT gemapi.digue_supprimer(%s,1,%s)", (repere, stamp))
            self.c.execute(f"DELETE FROM {table} WHERE id=%s", (child,))
        self.c.execute("SELECT gemapi.digue_supprimer(%s,1,%s)", (repere, stamp))
        self.c.execute("SET CONSTRAINTS ALL IMMEDIATE")
        self.assertEqual(self.one("SELECT count(*) FROM gemapi.digues WHERE id=%s", (digue,)), 0)

    def test_suppression_apres_reaffectation(self):
        # Chaque ancien parent n’a qu’un seul enfant. Le diagnostic doit identifier cet enfant.
        for spec in FAMILIES.values():
            for key, parent_table in PARENTS.items():
                if key == "site_terrestre_id":
                    fields = {"nom":"Site", "geom":"SRID=3950;MULTIPOLYGON(((0 0,1 0,1 1,0 0)))"}
                elif key == "troncon_id":
                    fields = {"type_troncon_id":"CRETE", "digue_id":self.ids["digue_id"]}
                else:
                    ah = insert(self.c, "gemapi.ah", nom="Parent indépendant")
                    fields = {"ah_ouvrage_id":{"ah_id":ah, "geom":LINE},
                              "ah_stockage_id":{"ah_id":ah, "geom":AREA, "nom":"Stockage"},
                              "ah_section_id":{"ah_id":ah, "cours_eau_id":self.ids["cours_eau_id"],
                                               "debut_fraction":0, "fin_fraction":1}}[key]
                parent = insert(self.c, parent_table, **fields)
                child = family(self.c, spec, {key:parent})
                with self.rejected("23503"):
                    self.c.execute(f"DELETE FROM {parent_table} WHERE id=%s", (parent,))
                self.c.execute(f"UPDATE {spec[0]} SET {key}=%s WHERE id=%s", (self.ids[key], child))
                self.c.execute(f"DELETE FROM {parent_table} WHERE id=%s", (parent,))
                self.assertEqual(self.c.rowcount, 1)

    def wait_for_writer(self, holder, waiter, operation):
        """Commit the holder only after PostgreSQL confirms the other session is waiting."""
        errors = []

        def run():
            try:
                operation()
                waiter.commit()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self.one("SELECT %s=ANY(pg_blocking_pids(%s))",
                            (holder.get_backend_pid(), waiter.get_backend_pid())):
                    break
                if errors:
                    raise errors[0]
                time.sleep(0.03)
            else:
                self.fail("PostgreSQL n’a pas détecté le verrou attendu entre les deux sessions")
        finally:
            holder.commit()
            worker.join(16)
        self.assertFalse(worker.is_alive())
        if errors:
            raise errors[0]

    def test_concurrence(self):
        for parent_first in (True, False):
            with self.subTest(parent_first=parent_first), closing(self.connect("test_patrimoine")) as parent, \
                    closing(self.connect("test_prestations")) as writer:
                with parent.cursor() as c:
                    child = family(c, FAMILIES["cheminement_id"], {"site_terrestre_id": self.ids["site_terrestre_id"]})
                parent.commit()

                def change_geometry():
                    with parent.cursor() as c:
                        c.execute("UPDATE gemapi.cheminements SET geometry=%s WHERE id=%s", (AREA, child))

                def create_prestation():
                    with writer.cursor() as c:
                        self.prestation(c, cheminement_id=child)

                if parent_first:
                    change_geometry()
                    self.wait_for_writer(parent, writer, create_prestation)
                else:
                    create_prestation()
                    self.wait_for_writer(writer, parent, change_geometry)
                self.assertEqual(self.rows("""SELECT surface_m2, quantite, origine_mesures
                    FROM gemapi.prestations WHERE cheminement_id=%s""", (child,)),
                    [(6, 7, "Patrimoine de rattachement")])


class PostgisPublic(Qualification, unittest.TestCase):
    extension = "public"


class PostgisDedie(Qualification, unittest.TestCase):
    extension = "extensions"


if __name__ == "__main__":
    unittest.main(verbosity=2)
