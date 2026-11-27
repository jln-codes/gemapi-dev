"""CRUD-LIT-1, level B: real HTTP/PG16/PostGIS and aggregate transactions.

Disposable runner only, unique fixtures per scenario. Proves identity, retention,
explicit relations, receipts, rollback and canonical Base rows; under two minutes.
"""
import os
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql

from tests.postgres_safety import require_test_dsn
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from digues_webapp.geometry_capabilities import load_geometry_model
from webapp.tests.test_equipement_mesure_postgres import http_client

POINT = {"type": "Point", "coordinates": [2, 48]}
LINE = {"type": "LineString", "coordinates": [[2, 48], [2.01, 48.01]]}
POLYGON = {"type": "Polygon", "coordinates": [[[2,48],[2.01,48],[2.01,48.01],[2,48]]]}


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class LitCrudPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = require_test_dsn("DIGUES_OCC_TEST_DSN")
        cls.previous_legacy = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.addClassCleanup(cls.restore_environment)
        cls.app = create_app()
        def connection():
            with psycopg.connect(cls.dsn, autocommit=True) as conn:
                yield conn
        cls.app.dependency_overrides[get_connection] = connection
        cls.app.dependency_overrides[get_write_connection] = connection
        cls.client = cls.enterClassContext(http_client(cls.app))

    @classmethod
    def restore_environment(cls):
        if cls.previous_legacy is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous_legacy

    def request(self, method, path, payload=None, status=200, version=None, operation=None, **headers):
        headers["Operation-Id"] = operation or str(uuid4())
        if version is not None:
            headers["If-Match"] = f'"{version}"'
        response = self.client.request(method, "/api/" + path, json=payload, headers=headers)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def create(self, resource="troncons-lit", **payload):
        defaults = {"id": str(uuid4()), "geometry": LINE, "valid": True}
        if resource == "elements-morphologiques-lit":
            defaults.update(type_morphologie="INDETERMINE", geometry=POINT)
        return self.request("POST", resource, {**defaults, **payload}, 201)

    def state(self, identifier, table="segments_hydrographiques"):
        with psycopg.connect(self.dsn) as conn:
            return conn.execute(sql.SQL("SELECT ST_AsEWKB(geometry),ST_SRID(geometry),valid,version,designation FROM public.{} WHERE id=%s").format(sql.Identifier(table)), (identifier,)).fetchone()

    def digue(self):
        identifier = str(uuid4())
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO public.digues(id,libelle,designation,valid) VALUES(%s,'CRUD Lit','CRUD Lit',false)", (identifier,))
        return identifier

    def catalogue(self, name):
        identifier = uuid4().hex
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("INSERT INTO referentiel.{}(id,designation,libelle,valid) VALUES(%s,%s,'CRUD Lit',true)").format(sql.Identifier(name)), (identifier, identifier))
        return identifier

    def test_parent_shapes_relations_and_precise_mutation_receipts(self):
        d1, d2 = self.digue(), self.digue()
        first = self.create(digue_ids=[d1,d2], designation="Deux digues")
        identifier = first["properties"]["id"]
        self.assertEqual(set(first["properties"]["digue_ids"]), {d1,d2})
        self.assertEqual(first["geometry"]["type"], "LineString")
        second = self.create(geometry=POLYGON, digue_ids=[d1])
        self.assertEqual(second["geometry"]["type"], "Polygon")
        with psycopg.connect(self.dsn) as conn:
            srid = conn.execute("SELECT srid FROM technique.metadonnees_base").fetchone()[0]
        self.assertEqual(self.state(identifier)[1], srid)
        update = {"commentaire": "Conservé"}
        operation = str(uuid4())
        changed = self.request("PUT", f"troncons-lit/{identifier}", update, version=1, operation=operation)
        self.assertEqual(set(changed["properties"]["digue_ids"]), {d1,d2})
        replay = self.request("PUT", f"troncons-lit/{identifier}", update, version=1, operation=operation,
                              **{"Geometry-Contract-Version": "obsolete"})
        self.assertEqual(changed, replay)
        self.request("PUT", f"troncons-lit/{identifier}", {"commentaire":"Autre"}, 409, version=1, operation=operation)
        self.request("PUT", f"troncons-lit/{identifier}", {"commentaire":"Stale"}, 409, version=1)
        before = self.state(identifier)
        self.request("PUT", f"troncons-lit/{identifier}", {"designation":"Rollback", "digue_ids":[str(uuid4())]}, 422, version=2)
        self.assertEqual(self.state(identifier), before)
        cleared = self.request("PUT", f"troncons-lit/{identifier}", {"digue_ids":[]}, version=2)
        self.assertEqual(cleared["properties"]["digue_ids"], [])
        read = self.request("GET", f"troncons-lit/{identifier}")
        self.assertEqual(read["properties"]["version"], 3)
        collection = self.request("GET", "troncons-lit")
        self.assertIn(identifier, [f["properties"]["id"] for f in collection["features"]])
        options = self.request("GET", "troncons-lit/options")
        self.assertIn(identifier, [item["id"] for item in options["troncons_lit"]])

    def test_digue_link_with_historical_ouvrage_cannot_be_destroyed(self):
        digue=self.digue()
        parent=self.create(digue_ids=[digue])["properties"]["id"]
        detail=str(uuid4())
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO public.segments_hydrographiques__ouvrages(id,segment_id,digue_id,designation,valid) VALUES(%s,%s,%s,'Historique',false)",(detail,parent,digue))
        before=self.state(parent)
        self.request("PUT",f"troncons-lit/{parent}",{"designation":"Rollback","digue_ids":[]},422,1)
        self.assertEqual(self.state(parent),before)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT segment_id::text,digue_id::text FROM public.segments_hydrographiques__ouvrages WHERE id=%s",(detail,)).fetchone(),(parent,digue))
        self.assertEqual(self.request("GET",f"troncons-lit/{parent}")["properties"]["digue_ids"],[digue])

    def test_creation_idempotence_atomic_failures_and_contract_version(self):
        payload = {"id":str(uuid4()),"geometry":LINE,"valid":True}
        operation = str(uuid4())
        first = self.request("POST", "troncons-lit", payload, 201, operation=operation)
        again = self.request("POST", "troncons-lit", payload, 201, operation=operation)
        self.assertEqual(first, again)
        self.request("POST", "troncons-lit", payload, 409)
        self.assertEqual(self.client.post("/api/troncons-lit", json=payload).status_code,422)
        failed_id = str(uuid4())
        self.request("POST", "troncons-lit", {**payload,"id":failed_id,"digue_ids":[str(uuid4())]},422)
        self.assertIsNone(self.state(failed_id))
        self.request("POST", "troncons-lit", {**payload,"id":failed_id},409,
                     **{"Geometry-Contract-Version":"stale"})
        self.assertIsNone(self.state(failed_id))
        created = self.create(id=failed_id)
        self.assertEqual(created["properties"]["version"],1)
        self.request("PUT", f"troncons-lit/{failed_id}", {"designation":"Missing version"},428)
        self.request("POST", "troncons-lit", {"id":str(uuid4()),"geometry":POINT},422)
        self.request("POST", "troncons-lit", {"id":str(uuid4()),"valid":True},422)
        absent = self.create(geometry=None, valid=False)
        self.assertIsNone(absent["geometry"])

    def test_five_identitary_characteristics_and_parent_version(self):
        parent = self.create()["properties"]["id"]
        other = self.create()["properties"]["id"]
        families = [
            ("largeurs","largeurs_lit",{"largeur_moyenne":"0.000000000000000001"}),
            ("pentes","pentes_lit",{"pente_moyenne":"0"}),
            ("domanialites","domanialites_lit",{"type_id":self.catalogue("domanialites_lit")}),
            ("regimes-ecoulement","regimes_ecoulement_lit",{"regime_id":self.catalogue("regimes_ecoulement_lit")}),
            ("occupations-riveraines","occupations_riveraines_lit",{"type_occupation_id":self.catalogue("occupations_riveraines_lit"),"rive_id":self.catalogue("rives")}),
        ]
        version = 1
        for family, key, values in families:
            ids = [str(uuid4()),str(uuid4())]
            for identifier in ids:
                payload = {"id":identifier,**values,"pr_debut":"0","pr_fin":"1.00000000000001", "date_debut":"2020-01-02","valid":True}
                operation = str(uuid4())
                result = self.request("POST",f"troncons-lit/{parent}/caracteristiques/{family}",payload,201,version,operation)
                replay = self.request("POST",f"troncons-lit/{parent}/caracteristiques/{family}",payload,201,version,operation)
                self.assertEqual(result,replay)
                version += 1
                self.assertEqual(result["properties"]["version"],version)
            occurrences = result["properties"][key]
            if family == "largeurs":
                self.assertEqual(occurrences[0]["largeur_moyenne"],"0.000000000000000001")
            self.assertEqual(occurrences[0]["pr_fin"],"1.00000000000001")
            self.assertEqual({item["id"] for item in occurrences},set(ids))
            self.assertTrue(all(str(item["pr_debut"]) in {"0","0.0"} for item in occurrences))
            for item in occurrences:
                self.assertEqual(item["segment_id"],parent)
                self.assertEqual(item["date_debut"],"2020-01-02")
            edited = self.request("PUT",f"troncons-lit/{parent}/caracteristiques/{family}/{ids[0]}",{"commentaire":"Édité"},version=version)
            version += 1
            self.assertEqual(edited["properties"]["version"],version)
            self.request("PUT",f"troncons-lit/{other}/caracteristiques/{family}/{ids[0]}",{"commentaire":"Mauvais parent"},404,1)
        self.request("POST",f"troncons-lit/{uuid4()}/caracteristiques/largeurs",{"id":str(uuid4()),"largeur_moyenne":"0"},404,1)
        before = self.state(parent)
        self.request("POST",f"troncons-lit/{parent}/caracteristiques/domanialites",{"id":str(uuid4()),"type_id":"missing"},422,version)
        self.request("POST",f"troncons-lit/{parent}/caracteristiques/largeurs",{"id":str(uuid4()),"regime_id":None},422,version)
        self.request("POST",f"troncons-lit/{parent}/caracteristiques/largeurs",{"id":str(uuid4()),"geometry":POINT},422,version)
        self.assertEqual(self.state(parent),before)
        self.assertEqual(self.request("GET",f"troncons-lit/{other}")["properties"]["largeurs_lit"],[])

    def test_historical_characteristic_positions_and_invalid_catalog_retained(self):
        parent = self.create()["properties"]["id"]
        identifier = str(uuid4())
        category = self.catalogue("domanialites_lit")
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE referentiel.domanialites_lit SET valid=false WHERE id=%s",(category,))
            conn.execute("INSERT INTO public.domanialites_lit(id,segment_id,type_id,valid,position_debut,borne_debut_distance,borne_debut_aval,date_maj) "
                         "VALUES(%s,%s,%s,false,ST_GeomFromText('POINT(2 48)',4326),0,false,'2000-01-01')",(identifier,parent,category))
            before=conn.execute("SELECT ST_AsEWKB(position_debut),borne_debut_distance,borne_debut_aval,date_maj FROM public.domanialites_lit WHERE id=%s",(identifier,)).fetchone()
        result=self.request("PUT",f"troncons-lit/{parent}/caracteristiques/domanialites/{identifier}",{"commentaire":"Conservé","type_id":category},version=1)
        self.assertEqual(result["properties"]["domanialites_lit"][0]["type_id"],category)
        with psycopg.connect(self.dsn) as conn:
            after=conn.execute("SELECT ST_AsEWKB(position_debut),borne_debut_distance,borne_debut_aval,date_maj FROM public.domanialites_lit WHERE id=%s",(identifier,)).fetchone()
        self.assertEqual(before,after)
        self.request("POST",f"troncons-lit/{parent}/caracteristiques/domanialites",{"id":str(uuid4()),"type_id":category},422,2)
        self.request("PUT",f"troncons-lit/{parent}/caracteristiques/domanialites/{identifier}",{"valid":True},422,2)

    def test_morphology_four_subtypes_shapes_parent_and_attributes(self):
        parent = self.create(geometry=POLYGON)["properties"]["id"]
        category = self.catalogue("types_zone_atterrissement_lit")
        for subtype, geometry in (("ILE",POINT),("BANC",LINE),("ATTERISSEMENT",POLYGON),("INDETERMINE",POINT)):
            payload = {"type_morphologie":subtype,"geometry":geometry,"libelle":"Identique",
                       "longueur":"0","largeur":"0.0000000000000001","rattache_berge":False,"vegetalise":None}
            if subtype == "ATTERISSEMENT":
                payload.update(type_zone_id=category,segment_id=parent)
            first = self.create("elements-morphologiques-lit",**payload)
            second = self.create("elements-morphologiques-lit",**payload)
            props = first["properties"]
            identifier = props["id"]
            self.assertNotEqual(identifier,second["properties"]["id"])
            self.assertEqual(props["type_morphologie"],subtype)
            self.assertEqual(props["largeur"],"0.0000000000000001")
            self.assertFalse(props["rattache_berge"])
            self.assertIsNone(props["vegetalise"])
            self.assertEqual(first["geometry"]["type"],geometry["type"])
            edited = self.request("PUT",f"elements-morphologiques-lit/{identifier}",{"segment_id":parent,"commentaire":"Modifié"},version=1)
            self.assertEqual(edited["properties"]["segment_id"],parent)
            self.assertEqual(edited["properties"]["type_morphologie"],subtype)
            before = self.state(identifier,"elements_morphologiques_lit")
            self.request("PUT",f"elements-morphologiques-lit/{identifier}",{"segment_id":str(uuid4()),"designation":"Rollback"},422,2)
            self.assertEqual(self.state(identifier,"elements_morphologiques_lit"),before)
            self.request("PUT",f"elements-morphologiques-lit/{identifier}",{"segment_id":None},version=2)
            self.assertIsNone(self.request("GET",f"elements-morphologiques-lit/{identifier}")["properties"]["segment_id"])
        absent = self.create("elements-morphologiques-lit",geometry=None,valid=False)
        self.assertIsNone(absent["geometry"])
        self.request("POST","elements-morphologiques-lit",{"id":str(uuid4()),"type_morphologie":"ILE","valid":True},422)
        self.request("POST","elements-morphologiques-lit",{"id":str(uuid4()),"geometry":POINT},422)
        self.request("POST","elements-morphologiques-lit",{"id":str(uuid4()),"type_morphologie":"BANC","geometry":POINT,"type_zone_id":category},422)
        self.request("POST","elements-morphologiques-lit",{"id":str(uuid4()),"type_morphologie":"ATTERISSEMENT","geometry":POINT,"type_zone_id":"missing"},422)

    def test_approved_capability_change_controls_existing_valid_geometry(self):
        identifier=self.create()["properties"]["id"]
        candidate=deepcopy(load_geometry_model())
        target=next(item for item in candidate["registry"]["targets"] if item["id"]=="da:concept:troncon-lit")
        target["geometry"]["target_shapes"]=["POLYGON"]
        with patch("digues_webapp.geometry_capabilities.load_geometry_model",return_value=candidate):
            contract=self.request("GET","geometry-capabilities/troncons-lit")
            self.assertEqual(contract["allowedTypes"],["Polygon"])
            self.request("PUT",f"troncons-lit/{identifier}",{"designation":"Non conforme"},422,1)
            invalid=self.request("PUT",f"troncons-lit/{identifier}",{"valid":False},version=1)
            self.assertEqual(invalid["geometry"]["type"],"LineString")
            self.assertIn("FORME_HORS_CONTRAT",invalid["properties"]["geometry_diagnostics"])
            self.request("PUT",f"troncons-lit/{identifier}",{"valid":True},422,2)
            corrected=self.request("PUT",f"troncons-lit/{identifier}",{"geometry":POLYGON,"valid":True},version=2)
            self.assertEqual(corrected["geometry"]["type"],"Polygon")

    def test_historical_geometry_exact_retention_base_and_correction(self):
        for resource, table, extra in (("troncons-lit","segments_hydrographiques",""),
                ("elements-morphologiques-lit","elements_morphologiques_lit",",type_morphologie")):
            for wkt, srid in (("MULTIPOINT((2 48),(3 49))",4326),("POINT(2 48)",0),(None,0)):
                identifier = str(uuid4())
                with psycopg.connect(self.dsn) as conn:
                    values = ", 'INDETERMINE'" if extra else ""
                    conn.execute(sql.SQL(f"INSERT INTO public.{{}}(id,designation,geometry,valid{extra}) VALUES(%s,'Historique',ST_GeomFromText(%s,%s),false{values})").format(sql.Identifier(table)),(identifier,wkt,srid))
                before = self.state(identifier,table)
                detail = self.request("GET",f"{resource}/{identifier}")
                self.assertEqual(detail["properties"]["id"],identifier)
                self.request("PUT",f"{resource}/{identifier}",{"designation":"Corrigé"},version=1)
                self.assertEqual(self.state(identifier,table)[:3],before[:3])
                self.request("PUT",f"{resource}/{identifier}",{"valid":True},422,2)
                self.request("PUT",f"{resource}/{identifier}",{"geometry":None},422,2)
                self.request("PUT",f"{resource}/{identifier}",{"geometry":{"type":"MultiPoint","coordinates":[[2,48]]}},422,2)
                self.request("GET",resource)
                root = "troncons_lit" if resource == "troncons-lit" else "elements_morphologiques_lit"
                response = self.client.post("/api/data/view-query",json={"root":root,"fields":[{"table":root,"field":"designation"}]})
                self.assertEqual(response.status_code,200,response.text)
                corrected = self.request("PUT",f"{resource}/{identifier}",{"geometry":LINE,"valid":True},version=2)
                self.assertTrue(corrected["properties"]["valid"])
