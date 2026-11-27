"""Real PostGIS transactions. Run only against an explicitly disposable database.

DIGUES_OCC_TEST_DSN must point to a database whose name starts with digues_occ_test.
No configured application database is used, and no existing data is deleted.
"""
import os
from contextlib import contextmanager
import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
from tests.geometry_fixtures import LINE_SQL, POINT_SQL
from fastapi import HTTPException
import requests
import uvicorn

from digues_app.target.versioning import VERSIONED_TABLES
from digues_webapp.resource_mutations import (
    RESOURCE_TABLES,
    execute_mutation,
    parse_base_version,
)


@contextmanager
def http_client(app):
    # Real HTTP, using the existing requests/uvicorn dependencies.
    sock=socket.socket()
    sock.bind(("127.0.0.1",0))
    server=uvicorn.Server(uvicorn.Config(app,log_level="error"))
    thread=threading.Thread(target=server.run,kwargs={"sockets":[sock]},daemon=True)
    thread.start()
    class Client:
        def request(self,method,url,**kwargs):
            return requests.request(method,f"http://127.0.0.1:{sock.getsockname()[1]}{url}",timeout=10,**kwargs)
        def get(self,url,**kwargs): return self.request("GET",url,**kwargs)
        def post(self,url,**kwargs): return self.request("POST",url,**kwargs)
        def put(self,url,**kwargs): return self.request("PUT",url,**kwargs)
    try:
        for _ in range(200):
            if server.started: break
            time.sleep(0.025)
        assert server.started
        yield Client()
    finally:
        server.should_exit=True
        thread.join(timeout=5)
        sock.close()


class VersionHeaderTest(unittest.TestCase):
    def test_only_collaborative_business_resources_are_versioned(self):
        expected = (
            "systemes", "digues", "troncons", "desordres", "mobilier",
            "equipements_mesure",
            "reseaux_techniques",
            "ouvrages_particuliers",
            "cheminements", "vegetation", "amenagements_hydrauliques",
            "prestations",
            "observations", "photos",
        )
        self.assertEqual(VERSIONED_TABLES, expected)
        self.assertEqual(tuple(RESOURCE_TABLES), (
            "systemes", "digues", "troncons", "desordres", "mobilier",
            "equipements-mesure", "reseaux-techniques", "ouvrages-particuliers",
            "cheminements", "vegetation", "amenagements-hydrauliques",
            "zones-stockage", "prestations",
            "observations", "photos", "troncons-lit", "elements-morphologiques-lit",
        ))
        # These three tables install their versions in the canonical DDL rather
        # than the shared additive VERSIONING_DDL helper.
        api_tables = expected[:11] + ("zones_stockage",) + expected[11:] + (
            "segments_hydrographiques", "elements_morphologiques_lit")
        self.assertEqual(tuple(RESOURCE_TABLES.values()), api_tables)
        self.assertNotIn("territoires_administratifs", VERSIONED_TABLES)
        self.assertNotIn("territoires-administratifs", RESOURCE_TABLES)

    def test_required_strong_version(self):
        self.assertEqual(parse_base_version('"17"'), 17)
        for value, code in [(None,428), ('*',422), ('W/"1"',422), ('"0"',422),
                            ('"1.5"',422), ('"' + '9' * 5000 + '"',422)]:
            with self.assertRaises(HTTPException) as error:
                parse_base_version(value)
            self.assertEqual(error.exception.status_code,code)


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class ResourceVersionsPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.postgres_safety import require_test_dsn

        cls.dsn=require_test_dsn("DIGUES_OCC_TEST_DSN")

    def create_disorder(self):
        identifier=uuid4()
        with psycopg.connect(self.dsn,autocommit=True) as connection:
            connection.execute(f"INSERT INTO public.desordres(id,designation,geometry,valid) VALUES (%s,'initial',{POINT_SQL},true)",(identifier,))
        return identifier

    def test_non_business_territory_has_no_version_column(self):
        with psycopg.connect(self.dsn) as connection:
            columns = connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'territoires_administratifs'"
            ).fetchall()
        self.assertNotIn(("version",), columns)

    def write(self, identifier, version, label, failing=False):
        with psycopg.connect(self.dsn,autocommit=True) as connection:
            def change():
                connection.execute("UPDATE public.desordres SET designation=%s WHERE id=%s",(label,identifier))
                if failing:
                    raise RuntimeError("secondary SQL failure inside the business transaction")
                return {"id":str(identifier)}
            return execute_mutation(connection,"desordres","update",change,
                                    identifier=identifier,if_match=f'"{version}"')

    def test_nominal_conflict_rollback_receipt_and_migration(self):
        identifier=self.create_disorder()
        response=self.write(identifier,1,"B")
        self.assertEqual(response["version"],2)
        self.assertEqual(response["mutation"]["versions"],{str(identifier):2})
        UUID(response["mutation"]["eventId"])
        with self.assertRaises(HTTPException) as conflict:
            self.write(identifier,1,"A")
        self.assertEqual(conflict.exception.status_code,409)
        with self.assertRaises(RuntimeError):
            self.write(identifier,2,"rolled back",failing=True)
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute("SELECT designation,version FROM public.desordres WHERE id=%s",(identifier,)).fetchone(),("B",2))

    def test_two_simultaneous_connections_only_one_writer_wins(self):
        identifier=self.create_disorder(); barrier=threading.Barrier(2)
        def writer(label):
            barrier.wait(timeout=5)
            try:
                self.write(identifier,1,label)
                return 200
            except HTTPException as conflict:
                return conflict.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(writer,["A","B"]))
        self.assertEqual(sorted(results),[200,409])

    def test_relation_only_sql_advances_aggregate_and_migration_preserves_it(self):
        from digues_app.target.versioning import VERSIONING_DDL
        identifier=self.create_disorder()
        with psycopg.connect(self.dsn,autocommit=True) as connection:
            systeme,digue,troncon=uuid4(),uuid4(),uuid4()
            connection.execute("INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'Relations',true)",(systeme,))
            connection.execute("INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) VALUES (%s,%s,'Relations',true)",(digue,systeme))
            connection.execute(f"INSERT INTO public.troncons(id,type_physique_id,digue_id,libelle,geometry,valid) VALUES (%s,'INDETERMINE',%s,'Relations',{LINE_SQL},true)",(troncon,digue))
            connection.execute("INSERT INTO public.desordres__troncons(desordre_id,troncon_id) VALUES (%s,%s)",(identifier,troncon))
            version=connection.execute("SELECT version FROM public.desordres WHERE id=%s",(identifier,)).fetchone()[0]
            self.assertGreater(version,1)
            for statement in VERSIONING_DDL:
                connection.execute(statement)
            self.assertEqual(connection.execute("SELECT version FROM public.desordres WHERE id=%s",(identifier,)).fetchone()[0],version)
        with self.assertRaises(HTTPException) as conflict:
            self.write(identifier,1,"obsolete before relation change")
        self.assertEqual(conflict.exception.status_code,409)

    def test_real_http_writes_require_version_and_return_409(self):
        from digues_webapp.app import create_app
        from digues_webapp.database import get_connection, get_write_connection
        app=create_app()
        def connection():
            with psycopg.connect(self.dsn,autocommit=True) as value:
                yield value
        app.dependency_overrides[get_connection]=connection
        app.dependency_overrides[get_write_connection]=connection
        with http_client(app) as client:
            created=client.post("/api/systemes-endiguement",json={"libelle":"Concurrent"})
            self.assertEqual(created.status_code,201,created.text)
            entity=created.json();identifier=entity["id"]
            self.assertEqual(entity["version"],1)
            url=f"/api/systemes-endiguement/{identifier}"
            self.assertEqual(client.get(url).json()["version"],1)
            self.assertEqual(client.put(url,json={"libelle":"A"}).status_code,428)
            winner=client.put(url,json={"libelle":"B"},headers={"If-Match":'"1"'})
            self.assertEqual(winner.status_code,200,winner.text)
            self.assertEqual(winner.json()["mutation"]["versions"][identifier],2)
            loser=client.put(url,json={"libelle":"A"},headers={"If-Match":'"1"'})
            self.assertEqual(loser.status_code,409,loser.text)
            self.assertNotIn("mutation",loser.json())
            self.assertEqual(client.get(url).json()["libelle"],"B")

    def test_all_current_resource_reads_and_geometry_writes_carry_versions(self):
        from digues_webapp.app import create_app
        from digues_webapp.database import get_connection, get_write_connection
        app=create_app()
        def connection():
            with psycopg.connect(self.dsn,autocommit=True) as value:
                yield value
        app.dependency_overrides[get_connection]=connection
        app.dependency_overrides[get_write_connection]=connection
        with http_client(app) as client:
            def create(path,payload):
                response=client.post(path,json=payload)
                self.assertEqual(response.status_code,201,response.text)
                result=response.json(); values=result.get("properties",result)
                self.assertEqual(result["mutation"]["versions"][values["id"]],values["version"])
                return values
            def update(path,values,payload):
                base=values["version"]
                response=client.put(path,json=payload,headers={"If-Match":f'"{base}"'})
                self.assertEqual(response.status_code,200,response.text)
                result=response.json();updated=result.get("properties",result)
                self.assertGreater(updated["version"],base)
                self.assertEqual(result["mutation"]["versions"][updated["id"]],updated["version"])
                self.assertEqual(client.put(path,json=payload,headers={"If-Match":f'"{base}"'}).status_code,409)
                return updated
            system=create("/api/systemes-endiguement",{"libelle":"OCC hierarchy"})
            digue=create("/api/digues",{"libelle":"Digue","systeme_endiguement_id":system["id"]})
            update(f'/api/digues/{digue["id"]}',digue,{"libelle":"Renamed"})
            line={"type":"LineString","coordinates":[[2,46],[2.02,46.02]]}
            troncon=create("/api/troncons",{"libelle":"Tronçon","digue_id":digue["id"],"geometry":line})
            update(f'/api/troncons/{troncon["id"]}',troncon,{"libelle":"Renamed"})
            systeme_reperage,borne_debut,borne_fin=uuid4(),uuid4(),uuid4()
            with psycopg.connect(self.dsn,autocommit=True) as connection:
                connection.execute("INSERT INTO public.systemes_reperage(id,troncon_id,libelle,valid) VALUES (%s,%s,'OCC',true)",(systeme_reperage,troncon["id"]))
                for borne,function,pr in [(borne_debut,"ST_StartPoint",0),(borne_fin,"ST_EndPoint",1000)]:
                    connection.execute(f"INSERT INTO public.bornes_reperage(id,libelle,geometry,valid) SELECT %s,'OCC',{function}(geometry),true FROM public.troncons WHERE id=%s",(borne,troncon["id"]))
                    connection.execute("INSERT INTO public.troncons_bornes VALUES (%s,%s)",(troncon["id"],borne))
                    connection.execute("INSERT INTO public.systemes_reperage__bornes(id,systeme_reperage_id,borne_id,valeur_pr,valid) VALUES (%s,%s,%s,%s,true)",(uuid4(),systeme_reperage,borne,pr))
                connection.execute("UPDATE public.troncons SET systeme_reperage_defaut_id=%s WHERE id=%s",(systeme_reperage,troncon["id"]))
            geometries=[{"type":"Point","coordinates":[2.01,46.01]},line,
                        {"type":"Polygon","coordinates":[[[2,46],[2.01,46],[2.01,46.01],[2,46]]]}]
            for geometry in geometries:
                desordre=create("/api/desordres",{"designation":"Concurrent","geometry":geometry,"troncon_ids":[troncon["id"]]})
                path=f'/api/desordres/{desordre["id"]}'
                self.assertEqual(client.get(path).json()["properties"]["version"],desordre["version"])
                if geometry["type"] in ("Point","LineString"):
                    reperage={"borne_debut_id":str(borne_debut),"distance_debut_m":0,"position_debut_relative":"SUR_BORNE"}
                    if geometry["type"]=="LineString":
                        reperage.update(borne_fin_id=str(borne_fin),distance_fin_m=0,position_fin_relative="SUR_BORNE")
                    desordre=update(path+"/reperage",desordre,reperage)
                desordre=update(path,desordre,{"commentaire":"Attributes","troncon_ids":[]})
                if geometry["type"]=="Point":
                    update(path,desordre,{"longitude":2.015,"latitude":46.015})
                else:
                    desordre=update(path+"/geometry",desordre,{"geometry":geometry})
                    if geometry["type"]=="LineString":
                        update(path+"/endpoints",desordre,{"crs":"EPSG:4326","debut":[2.01,46.01],"fin":[2.03,46.03]})
                # The OCC check precedes the repérage handler too.
                rejected=client.put(path+"/reperage",headers={"If-Match":'"1"'},json={
                    "borne_debut_id":str(uuid4()),"distance_debut_m":0,"position_debut_relative":"SUR_BORNE"})
                self.assertEqual(rejected.status_code,409,rejected.text)
            identifier=str(uuid4())
            with psycopg.connect(self.dsn,autocommit=True) as connection:
                connection.execute("INSERT INTO public.observations(id,desordre_id,valid) VALUES (%s,%s,true)",(identifier,desordre["id"]))
            path=f"/api/observations/{identifier}"
            observation=client.get(path).json()
            self.assertEqual(observation["version"],1)
            update(path,observation,{"suite":"OCC observation"})
