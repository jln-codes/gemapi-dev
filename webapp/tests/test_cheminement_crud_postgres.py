"""F4 Cheminement sur PostgreSQL/PostGIS privé et jetable."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest
from uuid import UUID, uuid4
import psycopg
from psycopg import sql
from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.queries import OBSERVATION_READ_PARENTS
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from digues_webapp.object_disorder_relations import (
    object_disorder_relation,
    read_object_disorder_ids,
)
from webapp.tests.test_equipement_mesure_postgres import http_client

@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'PostGIS jetable non configuré')
class CheminementPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = os.environ.get('LEGACY_SINGLE_DATABASE_ENABLED')
        os.environ['LEGACY_SINGLE_DATABASE_ENABLED']='true'
        cls.dsn=os.environ['DIGUES_OCC_TEST_DSN']
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.systeme,cls.digue=uuid4(),uuid4()
        cls.troncons=[uuid4(),uuid4()]
        cls.desordres=[uuid4(),uuid4()]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:


            conn.execute("INSERT INTO referentiel.types_cheminement(id,code,abrege,libelle,valid) VALUES('CT1','CT1','CT1','Passage',true)")
            if conn.execute("SELECT valid FROM referentiel.types_cheminement WHERE id='CAC'").fetchone() != (True,):
                raise AssertionError('Type C1 CAC requis par la fixture absent')
            conn.execute("INSERT INTO referentiel.types_cheminement(id,code,abrege,libelle,valid) VALUES('OLD','OLD','OLD','Historique',false)")
            cls.valid_type_ids = [r[0] for r in conn.execute(
                "SELECT id FROM referentiel.types_cheminement WHERE valid ORDER BY libelle,id")]
            conn.execute("INSERT INTO systemes(id,libelle,valid) VALUES(%s,'F4',true)",(cls.systeme,))
            conn.execute("INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) VALUES(%s,%s,'F4',true)",(cls.digue,cls.systeme))
            for t in cls.troncons:
                conn.execute("INSERT INTO troncons(id,digue_id,type_physique_id,libelle,geometry,valid) "
                             "VALUES(%s,%s,'INDETERMINE','F4',ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)",(t,cls.digue))
            for index, d in enumerate(cls.desordres):
                conn.execute(
                    "INSERT INTO desordres(id,designation,valid,geometry) "
                    "VALUES(%s,'F4',%s,ST_SetSRID(ST_Point(1,1),3950))",
                    (d, index != 0),
                )
        cls.app=create_app()
        def override():
            with psycopg.connect(cls.dsn,autocommit=True) as conn: yield conn
        cls.app.dependency_overrides[get_connection]=override
        cls.app.dependency_overrides[get_write_connection]=override

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None: os.environ.pop('LEGACY_SINGLE_DATABASE_ENABLED',None)
        else: os.environ['LEGACY_SINGLE_DATABASE_ENABLED']=cls.previous

    def _disorder_links(self, identifier):
        with psycopg.connect(self.dsn) as connection:
            return {
                row[1]: row[0]
                for row in connection.execute(
                    "SELECT id,desordre_id FROM public.cheminements__desordres "
                    "WHERE cheminement_id=%s ORDER BY desordre_id",
                    (identifier,),
                ).fetchall()
            }

    def _source_v2_counts(self):
        with psycopg.connect(self.dsn) as connection:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='source_v2' "
                "ORDER BY tablename"
            ).fetchall()
            return {
                table: connection.execute(sql.SQL(
                    "SELECT count(*) FROM source_v2.{}"
                ).format(sql.Identifier(table))).fetchone()[0]
                for (table,) in tables
            }

    def _common_disorder_ids(self, identifier):
        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                return read_object_disorder_ids(
                    cursor, object_disorder_relation("cheminements"), identifier,
                )

    def test_disorder_links_are_differential_idempotent_and_keep_source_v2(self):
        added = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO desordres(id,designation,valid,geometry) "
                "VALUES(%s,'Ajout différentiel',false,ST_SetSRID(ST_Point(1,1),3950))",
                (added,),
            )
        source_before = self._source_v2_counts()
        with http_client(self.app) as client:
            created = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CT1',
                'desordre_ids': list(map(str, self.desordres)),
                'geometry': {'type': 'LineString', 'coordinates': [[2,48],[2.1,48.1]]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = UUID(created.json()['properties']['id'])
            initial = self._disorder_links(identifier)
            self.assertEqual(set(initial), set(self.desordres))
            self.assertEqual(set(self._common_disorder_ids(identifier)),
                             set(self.desordres))

            protected = self.desordres[1]
            function_name = f"protect_f5e_{uuid4().hex}"
            trigger_name = f"protect_f5e_{uuid4().hex}"
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                connection.execute(sql.SQL(
                    "CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN IF OLD.desordre_id = {}::uuid THEN "
                    "RAISE EXCEPTION 'unchanged relation deleted'; END IF; "
                    "RETURN OLD; END $$"
                ).format(sql.Identifier(function_name), sql.Literal(str(protected))))
                connection.execute(sql.SQL(
                    "CREATE TRIGGER {} BEFORE DELETE ON "
                    "public.cheminements__desordres FOR EACH ROW "
                    "EXECUTE FUNCTION public.{}()"
                ).format(sql.Identifier(trigger_name), sql.Identifier(function_name)))

            first_operation = str(uuid4())
            try:
                changed = client.put(
                    f'/api/cheminements/{identifier}',
                    json={'desordre_ids': [str(protected), str(added)]},
                    headers={'If-Match': '"1"', 'Operation-Id': first_operation},
                )
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(changed.json()['properties']['version'], 2)
                changed_links = self._disorder_links(identifier)
                self.assertEqual(set(changed_links), {protected, added})
                self.assertEqual(set(self._common_disorder_ids(identifier)),
                                 {protected, added})
                self.assertEqual(changed_links[protected], initial[protected])
                self.assertNotIn(self.desordres[0], changed_links)

                replay = client.put(
                    f'/api/cheminements/{identifier}',
                    json={'desordre_ids': [str(protected), str(added)]},
                    headers={'If-Match': '"1"', 'Operation-Id': first_operation},
                )
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json(), changed.json())

                identical_operation = str(uuid4())
                identical = client.put(
                    f'/api/cheminements/{identifier}',
                    json={'desordre_ids': [str(added), str(protected)]},
                    headers={'If-Match': '"2"', 'Operation-Id': identical_operation},
                )
                self.assertEqual(identical.status_code, 200, identical.text)
                self.assertEqual(identical.json()['properties']['version'], 3)
                self.assertEqual(self._disorder_links(identifier), changed_links)
            finally:
                with psycopg.connect(self.dsn, autocommit=True) as connection:
                    connection.execute(sql.SQL(
                        "DROP TRIGGER {} ON public.cheminements__desordres"
                    ).format(sql.Identifier(trigger_name)))
                    connection.execute(sql.SQL(
                        "DROP FUNCTION public.{}()"
                    ).format(sql.Identifier(function_name)))

            attribute_only = client.put(
                f'/api/cheminements/{identifier}',
                json={'commentaire': 'Attribut seul'},
                headers={'If-Match': '"3"', 'Operation-Id': str(uuid4())},
            )
            self.assertEqual(attribute_only.status_code, 200, attribute_only.text)
            self.assertEqual(self._disorder_links(identifier), changed_links)

        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.operations_ressources "
                "WHERE operation_id=%s", (first_operation,),
            ).fetchone()[0], 1)
            events = connection.execute(
                "SELECT event_id FROM technique.evenements_ressources "
                "WHERE resource='cheminements' AND "
                "payload->'ids' @> jsonb_build_array(%s::text)",
                (identifier,),
            ).fetchall()
            self.assertEqual(len(events), 4)
        self.assertEqual(self._source_v2_counts(), source_before)

    def test_disorder_apply_failure_rolls_back_parent_links_event_and_receipt(self):
        rejected = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO desordres(id,designation,valid,geometry) "
                "VALUES(%s,'Refus transactionnel',true,ST_SetSRID(ST_Point(1,1),3950))", (rejected,),
            )
        with http_client(self.app) as client:
            created = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CT1',
                'desordre_ids': [str(self.desordres[0])],
                'geometry': {'type': 'LineString', 'coordinates': [[2,48],[2.1,48.1]]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = UUID(created.json()['properties']['id'])
            before_links = self._disorder_links(identifier)
            operation = str(uuid4())
            function_name = f"reject_f5e_{uuid4().hex}"
            trigger_name = f"reject_f5e_{uuid4().hex}"
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                events_before = connection.execute(
                    "SELECT count(*) FROM technique.evenements_ressources"
                ).fetchone()[0]
                connection.execute(sql.SQL(
                    "CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN IF NEW.desordre_id = {}::uuid THEN "
                    "RAISE EXCEPTION 'F5E rollback'; END IF; RETURN NEW; END $$"
                ).format(sql.Identifier(function_name), sql.Literal(str(rejected))))
                connection.execute(sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON "
                    "public.cheminements__desordres FOR EACH ROW "
                    "EXECUTE FUNCTION public.{}()"
                ).format(sql.Identifier(trigger_name), sql.Identifier(function_name)))
            try:
                refused = client.put(
                    f'/api/cheminements/{identifier}',
                    json={'commentaire': 'Ne doit pas persister',
                          'desordre_ids': [str(rejected)]},
                    headers={'If-Match': '"1"', 'Operation-Id': operation},
                )
                self.assertEqual(refused.status_code, 503, refused.text)
            finally:
                with psycopg.connect(self.dsn, autocommit=True) as connection:
                    connection.execute(sql.SQL(
                        "DROP TRIGGER {} ON public.cheminements__desordres"
                    ).format(sql.Identifier(trigger_name)))
                    connection.execute(sql.SQL(
                        "DROP FUNCTION public.{}()"
                    ).format(sql.Identifier(function_name)))

            for invalid in (
                {'desordre_ids': [str(uuid4())]},
                {'desordre_ids': None},
                {'desordre_ids': [str(self.desordres[0])] * 2},
            ):
                response = client.put(
                    f'/api/cheminements/{identifier}', json=invalid,
                    headers={'If-Match': '"1"', 'Operation-Id': str(uuid4())},
                )
                self.assertEqual(response.status_code, 422, response.text)

        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute(
                "SELECT commentaire,version FROM public.cheminements WHERE id=%s",
                (identifier,),
            ).fetchone(), (None, 1))
            self.assertEqual(self._disorder_links(identifier), before_links)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.evenements_ressources"
            ).fetchone()[0], events_before)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.operations_ressources "
                "WHERE operation_id=%s", (operation,),
            ).fetchone()[0], 0)

    def test_two_same_version_mutations_serialize_on_parent(self):
        with http_client(self.app) as client:
            created = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CT1',
                'desordre_ids': [str(self.desordres[0])],
                'geometry': {'type': 'LineString', 'coordinates': [[2,48],[2.1,48.1]]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = UUID(created.json()['properties']['id'])
            barrier = Barrier(2)

            def mutate(desordre_id):
                barrier.wait(timeout=5)
                return client.put(
                    f'/api/cheminements/{identifier}',
                    json={'desordre_ids': [str(desordre_id)]},
                    headers={'If-Match': '"1"', 'Operation-Id': str(uuid4())},
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(mutate, self.desordres))
            self.assertEqual(sorted(response.status_code for response in responses),
                             [200, 409])
            winner = next(response for response in responses if response.status_code == 200)
            self.assertEqual(winner.json()['properties']['version'], 2)
            final = client.get(f'/api/cheminements/{identifier}')
            self.assertEqual(final.json()['properties']['version'], 2)

        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.operations_ressources "
                "WHERE resource='cheminements' AND resource_id=%s", (identifier,),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM technique.evenements_ressources "
                "WHERE resource='cheminements' AND "
                "payload->'ids' @> jsonb_build_array(%s::text)", (identifier,),
            ).fetchone()[0], 2)

    def test_hundreds_of_disorder_links_use_the_same_differential_contract(self):
        disorder_ids = [uuid4() for _ in range(305)]
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO desordres(id,designation,valid,geometry) "
                    "VALUES(%s,%s,%s,ST_SetSRID(ST_Point(1,1),3950))",
                    [(identifier, f'Volume {index}', index % 2 == 0)
                     for index, identifier in enumerate(disorder_ids)],
                )
        with http_client(self.app) as client:
            created = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CT1',
                'desordre_ids': list(map(str, disorder_ids[:300])),
                'geometry': {'type': 'LineString', 'coordinates': [[2,48],[2.1,48.1]]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = UUID(created.json()['properties']['id'])
            before = self._disorder_links(identifier)
            requested = disorder_ids[5:295] + disorder_ids[300:305]
            changed = client.put(
                f'/api/cheminements/{identifier}',
                json={'desordre_ids': list(map(str, requested))},
                headers={'If-Match': '"1"', 'Operation-Id': str(uuid4())},
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            after = self._disorder_links(identifier)
            self.assertEqual(set(after), set(requested))
            for disorder_id in disorder_ids[5:295]:
                self.assertEqual(after[disorder_id], before[disorder_id])

    def test_attribute_only_put_keeps_geometry_and_both_relations(self):
        with http_client(self.app) as client:
            created = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CT1', 'designation': 'Base',
                'geometry': {'type': 'LineString', 'coordinates': [[2, 48], [2.1, 48.1]]},
                'troncon_ids': list(map(str, self.troncons)),
                'desordre_ids': list(map(str, self.desordres)),
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()['properties']['id']
            with psycopg.connect(self.dsn) as db:
                before = db.execute('SELECT ST_AsEWKB(geometry) FROM public.cheminements WHERE id=%s',
                                    (identifier,)).fetchone()[0]
            result = client.put(f'/api/cheminements/{identifier}', json={'commentaire': 'Base'},
                                headers={'If-Match': '"1"'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['properties']['troncon_ids'], list(map(str, sorted(self.troncons))))
            self.assertEqual(result.json()['properties']['desordre_ids'], list(map(str, sorted(self.desordres))))
            with psycopg.connect(self.dsn) as db:
                after = db.execute('SELECT ST_AsEWKB(geometry) FROM public.cheminements WHERE id=%s',
                                   (identifier,)).fetchone()[0]
            self.assertEqual(after, before)

    def test_polygon_is_reserved_to_cac_and_type_change_rolls_back(self):
        line = {'type': 'LineString', 'coordinates': [[2.1, 48.2], [2.2, 48.3]]}
        polygon = {'type': 'Polygon', 'coordinates': [
            [[2, 48], [3, 48], [3, 49], [2, 48]],
        ]}
        invalid_polygon = {'type': 'Polygon', 'coordinates': [
            [[2, 48], [3, 49], [3, 48], [2, 49], [2, 48]],
        ]}
        with http_client(self.app) as client:
            refused = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CT1', 'geometry': polygon,
            })
            self.assertEqual(refused.status_code, 422, refused.text)
            self.assertIn("CAC", refused.text)

            created = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CAC', 'designation': 'Accès surfacique',
                'geometry': polygon,
                'desordre_ids': list(map(str, self.desordres)),
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()['properties']['id']
            self.assertEqual(created.json()['geometry']['type'], 'Polygon')

            attribute_only = client.put(
                f'/api/cheminements/{identifier}',
                json={'commentaire': 'Polygone historique conservé'},
                headers={'If-Match': '"1"', 'Operation-Id': str(uuid4())},
            )
            self.assertEqual(attribute_only.status_code, 200, attribute_only.text)
            self.assertEqual(attribute_only.json()['geometry']['type'], 'Polygon')

            refused_change = client.put(
                f'/api/cheminements/{identifier}',
                json={'type_cheminement_id': 'CT1', 'desordre_ids': []},
                headers={'If-Match': '"2"', 'Operation-Id': str(uuid4())},
            )
            self.assertEqual(refused_change.status_code, 422, refused_change.text)
            self.assertIn("CAC", refused_change.text)
            current = client.get(f'/api/cheminements/{identifier}')
            self.assertEqual(current.status_code, 200, current.text)
            self.assertEqual(current.json()['properties']['version'], 2)
            self.assertEqual(current.json()['properties']['type_cheminement_id'], 'CAC')
            self.assertEqual(
                set(current.json()['properties']['desordre_ids']),
                set(map(str, self.desordres)),
            )
            self.assertEqual(current.json()['geometry']['type'], 'Polygon')

            compatible_change = client.put(
                f'/api/cheminements/{identifier}',
                json={'type_cheminement_id': 'CT1', 'geometry': line,
                      'desordre_ids': []},
                headers={'If-Match': '"2"', 'Operation-Id': str(uuid4())},
            )
            self.assertEqual(compatible_change.status_code, 200, compatible_change.text)
            self.assertEqual(compatible_change.json()['geometry']['type'], 'LineString')
            self.assertEqual(compatible_change.json()['properties']['desordre_ids'], [])

            malformed = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CAC', 'geometry': invalid_polygon,
            })
            self.assertEqual(malformed.status_code, 422, malformed.text)
            self.assertNotIn('ck_', malformed.text)
            multi = client.post('/api/cheminements', json={
                'type_cheminement_id': 'CAC',
                'geometry': {'type': 'MultiPolygon', 'coordinates': [polygon['coordinates']]},
            })
            self.assertEqual(multi.status_code, 422, multi.text)

    def test_create_update_relations_geometry_and_rollback(self):
        point={'type':'Point','coordinates':[2.1,48.2]}
        line={'type':'LineString','coordinates':[[2.1,48.2],[2.2,48.3]]}
        polygon={'type':'Polygon','coordinates':[[[2,48],[3,48],[3,49],[2,48]]]}
        with http_client(self.app) as client:
            for geom in (None, point, polygon):
                body={'type_cheminement_id':'CT1','designation':'Voie','valid':False,
                      'troncon_ids':[str(v) for v in self.troncons],
                      'desordre_ids':[str(v) for v in self.desordres], 'geometry':geom}
                created=client.post('/api/cheminements',json=body)
                self.assertEqual(created.status_code,422,created.text)
            body={'type_cheminement_id':'CT1','designation':'Voie','valid':False,
                  'troncon_ids':[str(v) for v in self.troncons],
                  'desordre_ids':[str(v) for v in self.desordres], 'geometry':line}
            created=client.post('/api/cheminements',json=body)
            self.assertEqual(created.status_code,201,created.text)
            row=created.json(); identifier=row['properties']['id']
            self.assertEqual(row['geometry']['type'],'LineString')
            self.assertEqual(len(row['properties']['troncon_ids']),2)
            self.assertEqual(len(row['properties']['desordre_ids']),2)
            endpoint=f'/api/cheminements/{identifier}'
            headers={'If-Match':'"1"','Operation-Id':str(uuid4())}
            change={'troncon_ids':[str(self.troncons[1])], 'desordre_ids':[],
                    'geometry':line,'largeur':0,'commentaire':''}
            updated=client.put(endpoint,json=change,headers=headers)
            self.assertEqual(updated.status_code,200,updated.text)
            self.assertEqual(updated.json()['properties']['version'],2)
            self.assertEqual(updated.json()['properties']['largeur'],0)
            self.assertEqual(len(updated.json()['properties']['desordre_ids']),0)
            collection=client.get('/api/cheminements')
            self.assertEqual(collection.status_code,200,collection.text)
            self.assertTrue(any(item['properties']['id']==identifier for item in collection.json()['features']))
            self.assertEqual(client.put(endpoint,json=change,headers=headers).status_code,200)
            self.assertEqual(client.put(endpoint,json={'valid':True},headers={'If-Match':'"1"'}).status_code,409)
            self.assertEqual(client.put(endpoint,json={'commentaire':'ROLLBACK','troncon_ids':[str(uuid4())]},headers={'If-Match':'"2"'}).status_code,422)
            self.assertEqual(client.get(endpoint).json()['properties']['commentaire'],None)
            self.assertEqual(client.get(endpoint).json()['properties']['version'],2)
            relation_only=client.put(endpoint,json={'troncon_ids':[]},headers={'If-Match':'"2"'})
            self.assertEqual(relation_only.status_code,200,relation_only.text)
            self.assertEqual(relation_only.json()['properties']['version'],3)
            self.assertEqual(relation_only.json()['properties']['troncon_ids'],[])
            self.assertEqual(client.put(endpoint,json={'desordre_ids':[str(uuid4())]},headers={'If-Match':'"3"'}).status_code,422)
            # Échec du second INSERT après UPDATE parent et premier lien :
            # le rollback doit restaurer version, attribut et liste entière.
            with psycopg.connect(self.dsn,autocommit=True) as conn:
                conn.execute(f"""CREATE FUNCTION public.reject_f4_link() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN
                    IF NEW.troncon_id = '{self.troncons[1]}'::uuid THEN
                        RAISE EXCEPTION 'F4 rollback'; END IF;
                    RETURN NEW; END $$""")
                conn.execute("CREATE TRIGGER reject_f4_link BEFORE INSERT ON "
                             "public.cheminements__troncons FOR EACH ROW "
                             "EXECUTE FUNCTION public.reject_f4_link()")
            try:
                refused=client.put(endpoint,json={
                    'commentaire':'Ne doit pas persister',
                    'troncon_ids':[str(value) for value in self.troncons],
                },headers={'If-Match':'"3"'})
                self.assertEqual(refused.status_code,503,refused.text)
            finally:
                with psycopg.connect(self.dsn,autocommit=True) as conn:
                    conn.execute('DROP TRIGGER reject_f4_link ON public.cheminements__troncons')
                    conn.execute('DROP FUNCTION public.reject_f4_link()')
            state=client.get(endpoint).json()['properties']
            self.assertEqual((state['version'],state['commentaire'],state['troncon_ids']),
                             (3,None,[]))
            self.assertEqual(client.post('/api/cheminements',json={'type_cheminement_id':'OLD'}).status_code,422)
            self.assertEqual(client.post('/api/cheminements',json={'type_cheminement_id':'CT1','troncon_ids':[str(uuid4())]}).status_code,422)
            self.assertEqual(client.post('/api/cheminements',json={
                'type_cheminement_id':'CT1', 'geometry':{
                    'type':'Polygon','coordinates':[[[2,48],[3,49],[3,48],[2,49],[2,48]]],
                },
            }).status_code,422)
            self.assertEqual(client.get(f'/api/cheminements/{uuid4()}').status_code,404)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT ST_SRID(geometry),GeometryType(geometry) FROM cheminements WHERE id=%s',(identifier,)).fetchone(),(3950,'LINESTRING'))
            self.assertEqual(conn.execute('SELECT count(*) FROM public.cheminements__troncons WHERE cheminement_id=%s',(identifier,)).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM public.cheminements__desordres WHERE cheminement_id=%s',(identifier,)).fetchone()[0],0)

    def test_reference_and_historical_inactive(self):
        old=uuid4()
        with psycopg.connect(self.dsn,autocommit=True) as conn:
            conn.execute("INSERT INTO cheminements(id,type_cheminement_id,designation,geometry,valid) "
                         "VALUES(%s,'OLD','V2',ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)",(old,))
        with http_client(self.app) as client:
            catalog=client.get('/api/referentiels/types-cheminement')
            self.assertEqual(catalog.status_code,200,catalog.text)
            self.assertEqual([x['id'] for x in catalog.json()['references']],self.valid_type_ids)
            self.assertEqual(client.get(f'/api/cheminements/{old}').json()['properties']['type_cheminement_id'],'OLD')
            edited=client.put(f'/api/cheminements/{old}',json={'designation':'V2 édité'},headers={'If-Match':'"1"'})
            self.assertEqual(edited.status_code,200,edited.text)
            self.assertEqual(edited.json()['properties']['type_cheminement_id'],'OLD')
            self.assertEqual(client.put(f'/api/cheminements/{old}',json={'type_cheminement_id':'OLD'},headers={'If-Match':'"2"'}).status_code,200)

    def test_observation_photo_parent_unique(self):
        with http_client(self.app) as client:
            obj=client.post('/api/cheminements',json={'type_cheminement_id':'CT1',
                 'geometry':{'type':'LineString','coordinates':[[2,48],[2.1,48.1]]}}).json()
            identifier=obj['properties']['id']; obsid=str(uuid4())
            obs=client.post('/api/observations',headers={'Operation-Id':str(uuid4())},json={
                'id':obsid,'parent':{'resource':'cheminements','id':identifier},'designation':'Visite'})
            self.assertEqual(obs.status_code,201,obs.text)
            with psycopg.connect(self.dsn,autocommit=True) as conn:
                pid=uuid4()
                conn.execute("INSERT INTO photos(id,observation_id,storage_key,valid) VALUES(%s,%s,'chemin.jpg',true)",(pid,obsid))
                self.assertEqual(conn.execute('SELECT cheminement_id FROM observations WHERE id=%s',(obsid,)).fetchone()[0],UUID(identifier))
                self.assertEqual(conn.execute(
                    f"SELECT num_nonnulls({','.join(c for c, _, _ in OBSERVATION_READ_PARENTS)}) "
                    'FROM observations WHERE id=%s',(obsid,),
                ).fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT observation_id FROM photos WHERE id=%s',(pid,)).fetchone()[0],UUID(obsid))
            listed=client.get(f'/api/cheminements/{identifier}/observations')
            self.assertEqual(listed.json()['observations'][0]['photo_count'],1)
