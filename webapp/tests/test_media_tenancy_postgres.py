"""Opt-in: four disposable local databases, synthetic Alpha/Beta images only.

Uses the lot-3 cluster guard (/tmp socket + matching PGDATA), never DATABASE_URL.
"""
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from contextlib import nullcontext
from digues_app.target import PostgreSQLConfig, initialize_schema
from digues_webapp import media_storage as media
from digues_webapp.database import open_read_connection, open_write_connection
from digues_webapp.desordre_pdf import BusinessPhotoLoader
from digues_webapp.media_target import resolve_media_maintenance_target
from digues_app.reports.disorder_sheet import DisorderPhoto
from tests.geometry_fixtures import LINE_SQL, POINT_SQL
from webapp.tests import test_business_postgres as business
from webapp.tests.test_media_tenancy import prepare, snapshot


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Cluster local jetable non configuré')
class MediaPostgresTest(unittest.TestCase):
    cleanup_databases = classmethod(business.BusinessPostgresTest.cleanup_databases.__func__)
    client = business.BusinessPostgresTest.client
    context = business.BusinessPostgresTest.context
    url = business.BusinessPostgresTest.url

    @classmethod
    def setUpClass(cls):
        business.BusinessPostgresTest.setUpClass.__func__(cls)
        cls.params = conninfo_to_dict(os.environ['DIGUES_MULTIBASE_TEST_ADMIN_DSN'])
        # Replace the isolated multibase fixture with the current canonical DDL.
        for index, name in enumerate(cls.dbnames):
            with psycopg.connect(**{**cls.params, 'dbname': name}, autocommit=True) as conn:
                conn.execute('DROP SCHEMA technique CASCADE')
                conn.execute('DROP TABLE public.systemes CASCADE')
                initialize_schema(
                    PostgreSQLConfig(), business_crs=3950,
                    connector=lambda **_: nullcontext(conn),
                )
                for suffix in ('rw', 'ro'):
                    role = sql.Identifier(f'{name}_{suffix}')
                    conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(role))
                    for schema in ('referentiel', 'technique'):
                        conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(
                            sql.Identifier(schema), role))
                conn.execute(sql.SQL('GRANT INSERT, UPDATE ON public.photos, technique.evenements_ressources, technique.operations_ressources TO {}').format(sql.Identifier(f'{name}_rw')))
                conn.execute(sql.SQL('GRANT UPDATE ON public.observations TO {}').format(sql.Identifier(f'{name}_rw')))
                conn.execute(sql.SQL('GRANT UPDATE (id) ON public.contacts TO {}').format(sql.Identifier(f'{name}_rw')))
                conn.execute(sql.SQL('GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(f'{name}_rw')))
                for schema in ('referentiel', 'technique'):
                    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}, {}').format(
                        sql.Identifier(schema), sql.Identifier(f'{name}_rw'), sql.Identifier(f'{name}_ro')))

    def setUp(self):
        business.BusinessPostgresTest.setUp(self)
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, MEDIA_BACKEND='filesystem', MEDIA_ROOT=self.folder))
        self.contexts = [self.context('admin_alpha', 0)[0], self.context('admin_beta', 2)[0]]
        self.stores = [media.get_media_storage(context=c) for c in self.contexts]
        self.photo, self.observation, self.disorder, self.key = uuid4(), uuid4(), uuid4(), uuid4()
        self.system, self.digue, self.trunk = uuid4(), uuid4(), uuid4()
        self.contents = []
        for position, index in enumerate((0, 2)):
            name = self.dbnames[index]
            out = io.BytesIO(); Image.new('RGB', (17, 11), ('blue', 'red')[position]).save(out, format='PNG')
            self.contents.append(out.getvalue())
            prepare(self.stores[position], out.getvalue(), key=self.key, photo=self.photo, observation=self.observation)
            with psycopg.connect(**{**self.params, 'dbname': name}) as conn:
                conn.execute("INSERT INTO systemes(id,libelle,valid) VALUES (%s,'Media test',true)", (self.system,))
                conn.execute("INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) VALUES (%s,%s,'Media test',true)", (self.digue,self.system))
                conn.execute(f"INSERT INTO troncons(id,type_physique_id,digue_id,libelle,geometry,valid) VALUES (%s,'INDETERMINE',%s,'Media test',{LINE_SQL},true)", (self.trunk,self.digue))
                conn.execute(f'INSERT INTO desordres(id,designation,geometry,valid) VALUES (%s,%s,{POINT_SQL},true)', (self.disorder, ('Alpha','Beta')[position]))
                conn.execute('INSERT INTO public.desordres__troncons(desordre_id,troncon_id) VALUES (%s,%s)', (self.disorder,self.trunk))
                conn.execute("INSERT INTO observations(id,desordre_id,designation,valid) VALUES (%s,%s,'Photos migrées',true)", (self.observation, self.disorder))
                conn.execute('INSERT INTO photos(id,observation_id,storage_key,valid) VALUES (%s,%s,%s,true)', (self.photo,self.observation,self.key))

    def test_http_same_uuid_key_correct_content_permissions_and_no_global_storage(self):
        for login, index, position in (('reader_alpha',0,0), ('admin_beta',2,1)):
            client = self.client(login)
            response = client.get(self.url(index, f'photos/{self.photo}/content'))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, self.contents[position])
            photo = client.get(self.url(index, f'photos/{self.photo}'))
            self.assertEqual(photo.status_code, 200, photo.text)
            self.assertEqual(photo.json()['content_url'], self.url(index, f'photos/{self.photo}/content'))
            observation = client.get(self.url(index, f'observations/{self.observation}'))
            self.assertEqual(observation.status_code, 200, observation.text)
            self.assertEqual(observation.json()['photos'][0]['content_url'], photo.json()['content_url'])
            other = 2 if index == 0 else 0
            with patch('digues_webapp.media_target.open_read_connection') as opened:
                self.assertEqual(client.get(self.url(other, f'photos/{self.photo}/content')).status_code, 403)
                opened.assert_not_called()
        client = self.client('superadmin')
        self.assertEqual(client.get(self.url(0, f'photos/{self.photo}/content')).status_code, 403)
        for path in (f'/media/{self.bases[0]}/objects/{self.key}/original',
                     f'/static/{self.bases[0]}/objects/{self.key}/original',
                     f'/api/photos/{self.photo}/content'):
            self.assertEqual(client.get(path).status_code, 404)
        beta_only = uuid4()
        with psycopg.connect(**{**self.params,'dbname':self.dbnames[2]}) as conn:
            conn.execute('INSERT INTO photos(id,observation_id,storage_key,valid) VALUES (%s,%s,%s,true)', (beta_only,self.observation,uuid4()))
        with patch('digues_webapp.photos.get_media_storage') as storage:
            self.assertEqual(self.client('reader_alpha').get(self.url(0, f'photos/{beta_only}/content')).status_code, 404)
            storage.assert_not_called()

    def test_upload_finalize_edit_localisation_and_foreign_reference_refused(self):
        import hashlib
        photo = uuid4(); digest = hashlib.sha256(self.contents[0]).hexdigest()
        alpha, beta = self.client('editor_alpha'), self.client('admin_beta')
        path = f'photo-uploads/{photo}/{digest}'
        params = {'observation_id':str(self.observation),'filename':'synthetic.png'}
        response = alpha.put(self.url(0,path), params=params, content=self.contents[0],
                             headers={**self.headers, 'Content-Type':'image/png'})
        self.assertEqual(response.status_code, 200, response.text)
        descriptor = response.json()
        payload = {key: descriptor[key] for key in ('id','observation_id','upload_ref','file_sha256','file_size')}
        contact = uuid4()
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[0]}) as conn:
            conn.execute("INSERT INTO contacts(id,nom,valid) VALUES (%s,'Photo synthétique',true)", (contact,))
        payload['photographe_id'] = str(contact)
        self.assertEqual(beta.get(self.url(2,path),params=params).status_code, 404)
        # Keep this request otherwise valid: foreign photographer rejection is
        # covered separately, so this assertion reaches foreign file lookup.
        beta_payload = {key: value for key, value in payload.items() if key != 'photographe_id'}
        denied = beta.post(self.url(2,'photos'), json=beta_payload, headers={**self.headers,'Operation-Id':str(uuid4())})
        self.assertEqual(denied.status_code,404,denied.text)
        self.assertIsNone(self.stores[1].prepared_for(photo))
        created = alpha.post(self.url(0,'photos'),json=payload,headers={**self.headers,'Operation-Id':str(uuid4())})
        self.assertEqual(created.status_code,201,created.text)
        self.assertEqual(created.json()['photographe_id'], str(contact))
        self.assertNotIn('photographe_source_id', created.json())
        self.assertEqual(alpha.get(created.json()['content_url']).content,self.contents[0])
        updated = alpha.put(self.url(0,f'photos/{photo}'),json={'designation':'Alpha edit'},
            headers={**self.headers,'If-Match':'"1"','Operation-Id':str(uuid4())})
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertEqual(updated.json()['content_url'],self.url(0,f'photos/{photo}/content'))
        self.assertEqual(alpha.get(self.url(0,f'photos/{photo}/localisation-options')).status_code,200)
        positioned = alpha.put(self.url(0,f'photos/{photo}/localisation'),json={'mode':'effacer'},
            headers={**self.headers,'If-Match':'"2"','Operation-Id':str(uuid4())})
        self.assertEqual(positioned.status_code,200,positioned.text)
        self.assertEqual(positioned.json()['content_url'],self.url(0,f'photos/{photo}/content'))
        for target in (self.url(0,f'photos/{photo}'), self.url(0,f'photos/{photo}/localisation')):
            self.assertEqual(self.client('reader_alpha').put(target,json={},headers=self.headers).status_code,403)
        self.assertEqual(self.client('reader_alpha').put(self.url(0,path),params=params,content=self.contents[0],headers=self.headers).status_code,403)

    def test_photographer_resolution_is_local_to_business_database(self):
        contact = uuid4()
        with psycopg.connect(**{**self.params, 'dbname': self.dbnames[0]}) as conn:
            conn.execute("INSERT INTO contacts(id,nom,valid) VALUES (%s,'Synthétique Alpha',true)", (contact,))
        for index, login in ((0, 'editor_alpha'), (2, 'admin_beta')):
            client = self.client(login)
            response = client.put(self.url(index, f'photos/{self.photo}'),
                json={'photographe_id': str(contact)},
                headers={**self.headers, 'If-Match': '"1"', 'Operation-Id': str(uuid4())})
            self.assertEqual(response.status_code, 200 if index == 0 else 422, response.text)
            if index == 0:
                self.assertEqual(response.json()['photographe_id'], str(contact))
            else:
                self.assertEqual(response.json()['detail']['code'], 'photo-photographer-invalid')
            with psycopg.connect(**{**self.params, 'dbname': self.dbnames[index]}) as conn:
                self.assertEqual(conn.execute('SELECT photographe_id,version FROM photos WHERE id=%s',
                    (self.photo,)).fetchone(), (contact, 2) if index == 0 else (None, 1))
            observation = client.get(self.url(index, f'observations/{self.observation}'))
            self.assertEqual(observation.json()['photos'][0]['photographe_id'], str(contact) if index == 0 else None)

    def test_cleanup_real_sql_symmetric_orphans_and_unavailable_connection(self):
        orphan = uuid4()
        for store in self.stores:
            prepare(store,self.contents[0],key=orphan)
        for position,index in enumerate((0,2)):
            target = resolve_media_maintenance_target(self.bases[index])
            before = snapshot(self.stores[1-position].root)
            store = self.stores[position]
            with open_write_connection(target) as conn:
                result = media.clean_prepared(conn,store,time.time()+86401,context=target)
            self.assertEqual(result['prepared_removed'],1)
            self.assertIsNone(store.inspect(orphan)); self.assertIsNotNone(store.inspect(self.key))
            self.assertEqual(snapshot(self.stores[1-position].root),before)
            # Even with zero expired bundles, DB failure prevents partial deletion.
            partial = store._dir('incoming') / 'abandoned.part'
            partial.write_bytes(b'test'); os.utime(partial,(0,0))
            before_self = snapshot(store.root)
            with self.assertRaises(psycopg.OperationalError):
                media.clean_prepared(conn,store,time.time()+86401,context=target)
            self.assertEqual(snapshot(store.root),before_self)
        with open_write_connection(self.contexts[1]) as conn, self.assertRaises(media.MediaScopeError):
            media.clean_prepared(conn,self.stores[0],context=self.contexts[0])

    def test_pdf_sheet_and_catalog_use_only_target_sql_and_bytes(self):
        for position,index,login in ((0,0,'reader_alpha'), (1,2,'admin_beta')):
            with open_read_connection(self.contexts[position]) as conn:
                loader = BusinessPhotoLoader(conn,self.contexts[position])
                self.assertEqual(loader(DisorderPhoto(str(self.photo),str(self.key))),self.contents[position])
                loader.check()
            seen=[]
            real_call=BusinessPhotoLoader.__call__
            def track(loader,photo):
                data=real_call(loader,photo); seen.append((loader.context.business_database_id,data)); return data
            client=self.client(login)
            with patch.object(BusinessPhotoLoader,'__call__',track):
                for path in (f'desordres/{self.disorder}/pdf',f'troncons/{self.trunk}/desordres/catalogue/pdf',
                             f'digues/{self.digue}/desordres/catalogue/pdf',f'systemes-endiguement/{self.system}/desordres/catalogue/pdf'):
                    result=client.get(self.url(index,path))
                    self.assertEqual(result.status_code,200,result.text[:200] if result.status_code != 200 else '')
                    self.assertTrue(result.content.startswith(b'%PDF-'))
            self.assertEqual(len(seen),4)
            self.assertTrue(all(base==self.bases[index] and data==self.contents[position] for base,data in seen))

    def test_pdf_identity_fault_is_not_swallowed_by_renderer(self):
        import json
        manifest=self.stores[0].root/'objects'/str(self.key)/'manifest.json'
        value=json.loads(manifest.read_text()); value['business_database_id']=str(self.bases[2])
        manifest.write_text(json.dumps(value))
        client=self.client('reader_alpha')
        for path in (f'photos/{self.photo}/content',f'photos/{self.photo}',
                     f'desordres/{self.disorder}/pdf',f'troncons/{self.trunk}/desordres/catalogue/pdf'):
            result=client.get(self.url(0,path))
            self.assertEqual(result.status_code,503,result.text)
