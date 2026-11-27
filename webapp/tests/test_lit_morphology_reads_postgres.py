"""L02-B HTTP + PostgreSQL, niveau B, < 2 min avec bases/ACL jetables.

Le lecteur conserve EWKT/SRID natifs, y compris sans parent ou invalide.
La seconde recette utilise authentification, rôles et trois bases réelles ;
aucune dépendance d'autorisation n'est simulée pour qualifier l'isolation.
"""
from contextlib import nullcontext
import os
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

from digues_app.target import PostgreSQLConfig, initialize_schema
from digues_webapp.database import read_business_crs
from webapp.tests import test_business_postgres as business
from webapp.tests.test_resource_versions import http_client
from webapp.tests import test_troncon_lit_reads_postgres as lit_reads


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'PostGIS jetable non configuré')
class MorphologyReadsPostgresTest(unittest.TestCase):
    setUp = lit_reads.TronconLitReadsPostgresTest.setUp

    def test_detail_without_parent_exact_geometry_invalid_rows_and_parent_list(self):
        srid=read_business_crs(self.connection).srid
        parent,other=uuid4(),uuid4()
        for identifier in (parent,other):
            self.connection.execute("INSERT INTO public.segments_hydrographiques(id,geometry,valid) "
                "VALUES(%s,ST_GeomFromText('LINESTRING(0 0,1 1)',%s),true)",(identifier,srid))
        identifiers=sorted(uuid4() for _ in range(4))
        parents=(None,parent,parent,other)
        shapes=('POINT(1.123456789012345 2)','LINESTRING(1 2,3 4)',
                'MULTIPOINT((1 2),(3 4))','POLYGON((0 0,2 0,2 2,0 0))')
        kinds=('ILE','BANC','INDETERMINE','ATTERISSEMENT')
        for index,identifier in enumerate(identifiers):
            self.connection.execute("INSERT INTO public.elements_morphologiques_lit"
                "(id,type_morphologie,segment_id,geometry,longueur,rattache_berge,valid) "
                "VALUES(%s,%s,%s,ST_GeomFromText(%s,%s),0,false,%s)",
                (identifier,kinds[index],parents[index],shapes[index],srid,index!=2))
        with http_client(self.app) as client:
            for index,identifier in enumerate(identifiers):
                response=client.get(f'/api/elements-morphologiques-lit/{identifier}')
                self.assertEqual(response.status_code,200,response.text)
                item=response.json()["properties"]
                self.assertEqual(item['id'],str(identifier))
                self.assertEqual(item['segment_id'],str(parents[index]) if parents[index] else None)
                self.assertEqual(item['type_morphologie'],kinds[index])
                self.assertEqual(item['geometry_srid'],srid)
                self.assertEqual(item['longueur'],'0')
                self.assertFalse(item['rattache_berge'])
                self.assertEqual(item['valid'],index!=2)
                self.assertEqual(self.connection.execute('SELECT ST_AsEWKB(ST_GeomFromEWKT(%s))='
                    'ST_AsEWKB(geometry) FROM public.elements_morphologiques_lit WHERE id=%s',
                    (item['geometry_ewkt'],identifier)).fetchone(),(True,))
                self.assertNotIn('source_class',item)
                self.assertNotIn('troncon_id',item)
            linked=client.get(f'/api/troncons-lit/{parent}').json()['properties']['elements_morphologiques_lit']
            self.assertEqual([row['id'] for row in linked],[str(identifiers[1]),str(identifiers[2])])
            self.assertEqual(client.get(f'/api/elements-morphologiques-lit/{uuid4()}').status_code,404)
            self.assertEqual(client.post(f'/api/elements-morphologiques-lit/{identifiers[0]}',json={}).status_code,405)


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Cluster jetable non configuré')
class MorphologyTenancyPostgresTest(unittest.TestCase):
    cleanup_databases=classmethod(business.BusinessPostgresTest.cleanup_databases.__func__)
    client=business.BusinessPostgresTest.client
    url=business.BusinessPostgresTest.url
    setUp=business.BusinessPostgresTest.setUp

    @classmethod
    def setUpClass(cls):
        business.BusinessPostgresTest.setUpClass.__func__(cls)
        # Replace only generated fixture bases with the actual current DDL.
        for index,name in enumerate(cls.dbnames):
            with psycopg.connect(**{**cls.admin_params,'dbname':name},autocommit=True) as connection:
                connection.execute('DROP SCHEMA technique CASCADE')
                connection.execute('DROP TABLE public.systemes CASCADE')
                initialize_schema(PostgreSQLConfig(),business_crs=(3950,3950,2154)[index],
                                  connector=lambda **_:nullcontext(connection))
                for suffix in ('rw','ro'):
                    role=sql.Identifier(f'{name}_{suffix}')
                    for schema in ('public','referentiel','technique'):
                        connection.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),role))
                        connection.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(schema),role))

    def test_same_uuid_selected_database_authorization_and_no_fallback(self):
        identifier,beta_only=uuid4(),uuid4()
        for index in (0,2):
            with psycopg.connect(**{**self.admin_params,'dbname':self.dbnames[index]}) as connection:
                srid=read_business_crs(connection).srid
                connection.execute("INSERT INTO public.elements_morphologiques_lit"
                    "(id,type_morphologie,designation,geometry,valid) "
                    "VALUES(%s,%s,%s,ST_GeomFromText(%s,%s),true)",
                    (identifier,'ILE' if index==0 else 'BANC','Alpha' if index==0 else 'Beta',
                     f'POINT({index+1} {index+2})',srid))
                if index==2:
                    connection.execute("INSERT INTO public.elements_morphologiques_lit"
                        "(id,type_morphologie,valid) VALUES(%s,'INDETERMINE',false)",(beta_only,))
        alpha,beta=self.client('reader_alpha'),self.client('admin_beta')
        for client,index,label,kind,srid in ((alpha,0,'Alpha','ILE',3950),(beta,2,'Beta','BANC',2154)):
            response=client.get(self.url(index,f'elements-morphologiques-lit/{identifier}'))
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.headers['cache-control'],'no-store')
            item=response.json()["properties"]
            self.assertEqual((item['designation'],item['type_morphologie'],item['geometry_srid']),
                             (label,kind,srid))
            self.assertIsNone(item['segment_id'])
        self.assertEqual(alpha.get(self.url(2,f'elements-morphologiques-lit/{identifier}')).status_code,403)
        self.assertEqual(beta.get(self.url(0,f'elements-morphologiques-lit/{identifier}')).status_code,403)
        self.assertEqual(alpha.get(self.url(0,f'elements-morphologiques-lit/{beta_only}')).status_code,404)
        self.assertEqual(alpha.get(f'/api/elements-morphologiques-lit/{identifier}').status_code,404)
