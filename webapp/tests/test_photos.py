"""Connected media, real PostgreSQL transactions and controlled binary content."""
import datetime
import hashlib
import io
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

from digues_webapp.media_storage import FilesystemMediaStorage, validate_image, clean_prepared, PREPARED_TTL
from digues_webapp.photos import PhotoCreate, PhotoMetadata, create_photo, validate_references
from digues_webapp.resource_mutations import execute_mutation
from webapp.tests import test_observation_detail as parents
from webapp.tests import test_resource_versions as fixtures


def picture(format='PNG'):
    stream = io.BytesIO()
    Image.new('RGB', (12, 8), 'blue').save(stream, format=format)
    return stream.getvalue()


class MediaStorageTest(unittest.TestCase):
    def test_photographer_is_a_business_contact_reference(self):
        contact = uuid4()
        connection = Mock()
        validate_references(connection, {'libelle': 'Légende'})
        connection.execute.assert_not_called()
        connection.execute.return_value.fetchone.return_value = (1,)
        validate_references(connection, {'photographe_id': contact})
        connection.execute.assert_called_once_with(
            "SELECT 1 FROM public.contacts WHERE id=%s AND valid FOR KEY SHARE", (contact,)
        )
        with self.assertRaises(ValidationError):
            PhotoMetadata(photographe_source_id=str(contact))

    def test_valid_formats_and_strict_actual_content(self):
        for fmt, name, mime in [('PNG','a.png','image/png'), ('JPEG','a.jpeg','image/jpeg'), ('WEBP','a.webp','image/webp')]:
            data = picture(fmt)
            variant = validate_image(io.BytesIO(data), name, mime, hashlib.sha256(data).hexdigest())
            self.assertEqual((variant['width'], variant['height'], variant['size']), (12,8,len(data)))
        for data, name, mime, code, reason in [(b'', 'x.png','image/png',422,'file-empty'),
                (b'not image','x.png','image/png',415,'file-invalid'),
                (picture(),'../a.png','image/png',422,'file-name-invalid'),
                (picture(),'a.jpg','image/png',415,'file-type-mismatch'),
                (picture(),'a.png','image/jpeg',415,'file-type-mismatch'),
                (picture('GIF'),'a.gif','image/gif',415,'file-type-forbidden'),
                (picture()[:-15],'a.png','image/png',415,'file-invalid')]:
            with self.subTest(name=name, code=code), self.assertRaises(HTTPException) as error:
                validate_image(io.BytesIO(data), name, mime, hashlib.sha256(data).hexdigest())
            self.assertEqual(error.exception.status_code, code)
            self.assertEqual(error.exception.detail['code'], reason)
        with self.assertRaises(HTTPException) as error:
            validate_image(io.BytesIO(picture()),'x.png','image/png','0'*64)
        self.assertEqual((error.exception.status_code, error.exception.detail['code']),
                         (422, 'file-hash-mismatch'))

    def test_partials_and_symlinks_and_immutable_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = FilesystemMediaStorage(folder)
            data = picture(); variant = validate_image(io.BytesIO(data),'x.png','image/png',hashlib.sha256(data).hexdigest())
            photo, observation = uuid4(), uuid4()
            first = storage.prepare(io.BytesIO(data), photo, observation, 'x.png', variant)
            self.assertNotEqual(first['storage_key'], str(photo))
            self.assertEqual(storage.prepare(io.BytesIO(data),photo,observation,'x.png',variant),first)
            with self.assertRaises(HTTPException):
                storage.prepare(io.BytesIO(data),photo,uuid4(),'x.png',variant)
            with storage.incoming() as stream:
                os.utime(stream.name if isinstance(stream.name,str) else next(Path(folder,'incoming').glob('*.part')), (0,0))
                self.assertEqual(storage.clean_incoming(time.time()),0)
            partial = Path(folder,'incoming','abandoned.part'); partial.write_bytes(b'partial'); os.utime(partial,(0,0))
            self.assertEqual(storage.clean_incoming(time.time()),1)
            bundle = Path(folder,'objects',first['storage_key'])
            (bundle/'original').unlink(); (bundle/'original').symlink_to('/etc/passwd')
            with self.assertRaises(OSError): storage.open_variant(first['storage_key'])

    def test_upload_limits_and_storage_interruption_cleanup(self):
        data = picture()
        for constant, limit in (("ABSOLUTE_MEDIA_LIMIT_BYTES", 2), ("MAX_PIXELS", 2)):
            with patch("digues_app.media_ingestion." + constant, limit), self.assertRaises(HTTPException) as error:
                validate_image(io.BytesIO(data), "x.png", "image/png", hashlib.sha256(data).hexdigest())
            self.assertEqual(error.exception.status_code, 413)
        with tempfile.TemporaryDirectory() as folder:
            storage = FilesystemMediaStorage(folder)
            with self.assertRaises(RuntimeError):
                with storage.incoming() as stream:
                    stream.write(b"interrupted")
                    raise RuntimeError("disconnect")
            self.assertEqual(list(Path(folder, "incoming").iterdir()), [])
            variant = validate_image(io.BytesIO(data), "x.png", "image/png", hashlib.sha256(data).hexdigest())
            with patch("digues_app.media_ingestion.shutil.copyfileobj", side_effect=OSError("disk full")), self.assertRaises(OSError):
                storage.prepare(io.BytesIO(data), uuid4(), uuid4(), "x.png", variant)
            self.assertEqual(list(Path(folder, "incoming").iterdir()), [])
            self.assertEqual(list(storage.candidates()), [])
        from digues_webapp import media_storage
        public = Path(media_storage.__file__).resolve().parents[2] / "frontend" / "uploads"
        with self.assertRaises(OSError): FilesystemMediaStorage(public)

    def test_prepared_blob_above_current_default_but_below_absolute_limit_is_accepted(self):
        stream = io.BytesIO()
        Image.frombytes('RGB', (800, 800), os.urandom(800 * 800 * 3)).save(stream, format='PNG')
        data = stream.getvalue()
        self.assertGreater(len(data), 1024 * 1024)
        self.assertLess(len(data), 20 * 1024 * 1024)
        variant = validate_image(
            io.BytesIO(data), 'ancienne-photo.png', 'image/png', hashlib.sha256(data).hexdigest(),
        )
        self.assertEqual(variant['size'], len(data))

    def test_write_contract_forbids_business_bypasses(self):
        payload = dict(id=str(uuid4()), observation_id=str(uuid4()), upload_ref=str(uuid4()),file_sha256='a'*64,file_size=12)
        for change in ({'id':'bad'}, {'observation_id':'bad'}, {'chemin_source':'/tmp/a'}, {'storage_key':str(uuid4())},
                       {'version':9}, {'file_size':True}, {'file_size':0}, {'file_sha256':'bad'}):
            with self.assertRaises(ValidationError): PhotoCreate(**(payload|change))
        self.assertEqual(PhotoCreate(**(payload | {'file_size': 20 * 1024 * 1024})).file_size,
                         20 * 1024 * 1024)
        with self.assertRaises(ValidationError):
            PhotoCreate(**(payload | {'file_size': 20 * 1024 * 1024 + 1}))
        for field in ('id','observation_id','chemin_source','storage_key','geometry','position','upload_ref'):
            with self.assertRaises(ValidationError): PhotoMetadata(**{field:str(uuid4())})


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'PostGIS jetable non configuré')
class PhotoPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ResourceVersionsPostgresTest.setUpClass()
        cls.dsn = os.environ['DIGUES_OCC_TEST_DSN']

    def setUp(self):
        parents.ObservationDetailPostgresTest.setUp(self)
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.env = patch.dict(os.environ, MEDIA_BACKEND='filesystem', MEDIA_ROOT=self.folder.name)
        self.env.start(); self.addCleanup(self.env.stop)
        self.storage = FilesystemMediaStorage(self.folder.name)

    def prepare(self, family='desordres', **metadata):
        identifier = uuid4(); data = picture(); digest = hashlib.sha256(data).hexdigest()
        variant = validate_image(io.BytesIO(data), 'terrain.png','image/png',digest)
        manifest = self.storage.prepare(io.BytesIO(data),identifier,self.observations[family],'terrain.png',variant)
        return dict(id=str(identifier),observation_id=str(self.observations[family]),upload_ref=manifest['storage_key'],
                    file_sha256=digest,file_size=len(data),**metadata)

    def counts(self, identifier):
        return tuple(self.connection.execute(sql,(identifier,)).fetchone()[0] for sql in (
            'SELECT count(*) FROM photos WHERE id=%s',
            "SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s",
            'SELECT count(*) FROM technique.operations_ressources WHERE resource_id=%s'))

    def test_http_upload_finalize_and_content_all_parents(self):
        with fixtures.http_client(self.app) as client:
            for family in self.observations:
                identifier = str(uuid4()); data=picture(); digest=hashlib.sha256(data).hexdigest()
                upload=f'/api/photo-uploads/{identifier}/{digest}'
                params={'observation_id':str(self.observations[family]),'filename':'terrain.png'}
                prepared=client.put(upload,params=params,data=data,headers={'Content-Type':'image/png'})
                self.assertEqual(prepared.status_code,200,prepared.text)
                descriptor=prepared.json()
                self.assertEqual(self.counts(identifier),(0,0,0))
                self.assertEqual(client.get(upload,params=params).json(),descriptor)
                retry=client.put(upload,params=params,data=data,headers={'Content-Type':'image/png'})
                self.assertEqual(retry.json(),descriptor)
                payload={key:descriptor[key] for key in ('id','observation_id','upload_ref','file_sha256','file_size')}
                response=client.post('/api/photos',json=payload,headers={'Operation-Id':str(uuid4())})
                self.assertEqual(response.status_code,201,response.text)
                photo=response.json()
                self.assertEqual((photo['id'],photo['observation_id'],photo['version']),(identifier,params['observation_id'],1))
                self.assertEqual(photo['date'],datetime.date.today().isoformat())
                self.assertTrue(photo['content_available'])
                self.assertNotIn('storage_key',photo); self.assertNotIn('chemin_source',photo)
                self.assertEqual(photo['mutation']['action'],'create'); self.assertEqual(photo['mutation']['resource'],'photos')
                self.assertEqual(photo['mutation']['versions'],{identifier:1})
                content=client.get(photo['content_url'])
                self.assertEqual(content.content,data)
                self.assertEqual(content.headers['Content-Type'],'image/png')
                self.assertEqual(content.headers['X-Content-Type-Options'],'nosniff')
                self.assertEqual(client.get(photo['content_url']+'?variant=thumbnail').status_code,404)
                observation=client.get('/api/observations/'+params['observation_id']).json()
                self.assertTrue(next(p for p in observation['photos'] if p['id']==identifier)['content_available'])
                self.assertEqual(self.counts(identifier),(1,1,1))
                self.assertEqual(self.connection.execute('SELECT storage_key FROM photos WHERE id=%s',(identifier,)).fetchone(),(payload['upload_ref'],))

    def test_idempotence_lost_response_collisions_and_multiple_photos(self):
        payload=self.prepare(); operation=str(uuid4())
        first=create_photo(self.connection,PhotoCreate(**payload),operation)
        # Simulated response lost after commit; no storage reads needed to replay receipt.
        with patch('digues_webapp.photos.get_media_storage',side_effect=OSError('offline storage')):
            self.assertEqual(create_photo(self.connection,PhotoCreate(**payload),operation),first)
            # upload_ref is transport only, including when the first reply was lost.
            self.assertEqual(create_photo(self.connection,PhotoCreate(**(payload | {'upload_ref': str(uuid4())})),operation),first)
        with self.assertRaises(HTTPException) as collision:
            create_photo(self.connection,PhotoCreate(**(payload|{'designation':'different'})),operation)
        self.assertEqual(collision.exception.detail['code'],'operation-id-collision')
        with self.assertRaises(HTTPException) as collision:
            create_photo(self.connection,PhotoCreate(**payload),str(uuid4()))
        self.assertEqual(collision.exception.detail['code'],'photo-id-collision')
        self.assertEqual(self.counts(payload['id']),(1,1,1))
        second=create_photo(self.connection,PhotoCreate(**self.prepare()),str(uuid4()))
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(first['observation_id'],second['observation_id'])

    def test_expired_upload_renews_without_collector_and_preserves_consumed(self):
        data = picture(); digest = hashlib.sha256(data).hexdigest(); identifier = str(uuid4())
        params = {'observation_id': str(self.observations['desordres']), 'filename': 'terrain.png'}
        url = f'/api/photo-uploads/{identifier}/{digest}'
        with fixtures.http_client(self.app) as client:
            with patch('digues_webapp.media_storage.time.time', return_value=time.time() - 90000):
                first = client.put(url, params=params, data=data, headers={'Content-Type':'image/png'}).json()
            self.assertEqual(client.get(url, params=params).status_code, 410)
            wrong_parent = client.put(url, params=params | {'observation_id': str(self.observations['troncons'])},
                                      data=data, headers={'Content-Type':'image/png'})
            self.assertEqual(wrong_parent.status_code, 409)
            changed = picture('JPEG')
            wrong_image = client.put(f'/api/photo-uploads/{identifier}/{hashlib.sha256(changed).hexdigest()}',
                                    params=params | {'filename': 'terrain.jpg'}, data=changed, headers={'Content-Type':'image/jpeg'})
            self.assertEqual(wrong_image.status_code, 409)
            self.assertIsNotNone(self.storage.inspect(first['upload_ref']))
            renewed = client.put(url, params=params, data=data, headers={'Content-Type':'image/png'})
            self.assertEqual(renewed.status_code, 200, renewed.text)
            descriptor = renewed.json()
            self.assertNotEqual(first['upload_ref'], descriptor['upload_ref'])
            self.assertIsNone(self.storage.inspect(first['upload_ref']))
            payload = {key: descriptor[key] for key in ('id','observation_id','upload_ref','file_sha256','file_size')}
            operation = str(uuid4())
            created = client.post('/api/photos', json=payload, headers={'Operation-Id':operation})
            self.assertEqual(created.status_code, 201, created.text)
            with patch('digues_webapp.photos.time.time', return_value=time.time() + 90000):
                same = client.put(url, params=params, data=data, headers={'Content-Type':'image/png'})
            self.assertEqual(same.json()['upload_ref'], descriptor['upload_ref'])
            self.assertEqual(client.post('/api/photos', json=payload | {'upload_ref': first['upload_ref']},
                                        headers={'Operation-Id':operation}).json(), created.json())
            self.assertEqual(self.counts(identifier), (1,1,1))

    def test_media_cannot_be_consumed_by_another_photo_or_observation(self):
        payload=self.prepare()
        for change in ({'id':str(uuid4())},{'observation_id':str(self.observations['troncons'])}):
            with self.assertRaises(HTTPException) as error:
                create_photo(self.connection,PhotoCreate(**(payload|change)),str(uuid4()))
            self.assertEqual(error.exception.status_code,409)
        create_photo(self.connection,PhotoCreate(**payload),str(uuid4()))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.connection.execute('INSERT INTO photos(observation_id,storage_key,valid) VALUES (%s,%s,true)',
                                    (self.observations['desordres'],payload['upload_ref']))

    def test_sql_failure_cleans_file_and_storage_failure_creates_no_row(self):
        payload=self.prepare()
        with patch('digues_webapp.resource_mutations.append_event',side_effect=RuntimeError('SQL failure')):
            with self.assertRaises(RuntimeError): create_photo(self.connection,PhotoCreate(**payload),str(uuid4()))
        self.assertEqual(self.counts(payload['id']),(0,0,0))
        self.assertIsNone(self.storage.inspect(payload['upload_ref']))
        payload=self.prepare()
        with patch('digues_webapp.media_storage.FilesystemMediaStorage.inspect',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): create_photo(self.connection,PhotoCreate(**payload),str(uuid4()))
        self.assertEqual(self.counts(payload['id']),(0,0,0))

    def test_exception_after_commit_never_deletes_original(self):
        payload=self.prepare(); operation=str(uuid4())
        def commit_then_fail(*args,**kwargs):
            execute_mutation(*args,**kwargs)
            raise RuntimeError('response lost')
        with patch('digues_webapp.photos.execute_mutation',side_effect=commit_then_fail):
            with self.assertRaises(RuntimeError): create_photo(self.connection,PhotoCreate(**payload),operation)
        self.assertIsNotNone(self.storage.inspect(payload['upload_ref']))
        self.assertEqual(create_photo(self.connection,PhotoCreate(**payload),operation)['id'],payload['id'])
        self.assertEqual(self.counts(payload['id']),(1,1,1))

    def test_unavailable_connection_never_guesses_commit_outcome_or_deletes_original(self):
        # Level B: real SQL and storage, fresh UUID/media per outcome. The
        # caller cannot reconcile after connection loss, whether SQL committed
        # or rolled back. No source-column fixture or general Photo rewrite.
        for committed in (False, True):
            with self.subTest(committed=committed):
                payload = self.prepare()
                operation = str(uuid4())

                def lose_connection(connection, *args, **kwargs):
                    try:
                        if committed:
                            execute_mutation(connection, *args, **kwargs)
                        else:
                            with patch('digues_webapp.resource_mutations.append_event',
                                       side_effect=RuntimeError('SQL failure')):
                                execute_mutation(connection, *args, **kwargs)
                    finally:
                        connection.close()
                    raise RuntimeError('COMMIT response unavailable')

                with psycopg.connect(self.dsn, autocommit=True) as connection:
                    with patch('digues_webapp.photos.execute_mutation', side_effect=lose_connection):
                        with self.assertLogs('digues_webapp.photos', level='WARNING'):
                            with self.assertRaises(RuntimeError):
                                create_photo(connection, PhotoCreate(**payload), operation)
                self.assertIsNotNone(self.storage.inspect(payload['upload_ref']))
                self.assertEqual(self.counts(payload['id']), (1, 1, 1) if committed else (0, 0, 0))
                if committed:
                    self.assertEqual(create_photo(self.connection, PhotoCreate(**payload), operation)['id'],
                                     payload['id'])
                    self.assertEqual(self.counts(payload['id']), (1, 1, 1))

    def test_expiry_cleanup_preserves_finalized_original_forever(self):
        abandoned=self.prepare(); kept=self.prepare()
        create_photo(self.connection,PhotoCreate(**kept),str(uuid4()))
        with patch('digues_webapp.photos.time.time',return_value=time.time()+PREPARED_TTL+1):
            with self.assertRaises(HTTPException) as error:
                create_photo(self.connection,PhotoCreate(**abandoned),str(uuid4()))
            self.assertEqual(error.exception.status_code,410)
        result=clean_prepared(self.connection,self.storage,time.time()+PREPARED_TTL+1)
        self.assertEqual(result['prepared_removed'],1)
        self.assertIsNone(self.storage.inspect(abandoned['upload_ref']))
        self.assertIsNotNone(self.storage.inspect(kept['upload_ref']))
        self.assertEqual(clean_prepared(self.connection,self.storage,time.time()+PREPARED_TTL*365)['prepared_removed'],0)

    def test_co2_create_update_triplet_occ_and_retry(self):
        # Current metadata has one typed Contact FK. Historical source strings
        # are migration evidence, never an editable second photographer field.
        first, second, missing = uuid4(), uuid4(), uuid4()
        for contact in (first, second):
            self.connection.execute("INSERT INTO contacts(id,nom,valid) VALUES (%s,'Fictif',true)", (contact,))

        def assert_contact(photo, contact):
            self.assertEqual(self.connection.execute(
                'SELECT photographe_id FROM photos WHERE id=%s', (photo['id'],)).fetchone(), (contact,))
            self.assertEqual(photo['photographe_id'], str(contact) if contact else None)
            self.assertNotIn('photographe_source_id', photo)
            self.assertNotIn('photographe_resolution_status', photo)

        with fixtures.http_client(self.app) as client:
            for contact in (None, first):
                payload = self.prepare(photographe_id=str(contact) if contact else None)
                headers = {'Operation-Id': str(uuid4())}
                response = client.post('/api/photos', json=payload, headers=headers)
                self.assertEqual(response.status_code, 201, response.text)
                assert_contact(response.json(), contact)
                self.assertEqual(client.post('/api/photos', json=payload, headers=headers).json(), response.json())
            photo = response.json()
            url = '/api/photos/' + photo['id']
            for contact in (second, None, first):
                headers = {'If-Match': f'"{photo["version"]}"', 'Operation-Id': str(uuid4())}
                payload = {'photographe_id': str(contact) if contact else None}
                updated = client.put(url, json=payload, headers=headers)
                self.assertEqual(updated.status_code, 200, updated.text)
                photo = updated.json()
                assert_contact(photo, contact)
                with patch('digues_webapp.photos.validate_references', side_effect=AssertionError('must replay receipt')):
                    self.assertEqual(client.put(url, json=payload, headers=headers).json(), photo)
                conflict = client.put(url, json={'photographe_id': str(second)},
                                      headers=headers | {'Operation-Id': str(uuid4())})
                self.assertEqual(conflict.status_code, 409)
                assert_contact(photo, contact)
            counts = self.counts(photo['id'])
            for payload in ({'photographe_id': str(missing)}, {'photographe_id': ''},
                            {'photographe_source_id': 'historical-contact'},
                            {'photographe_resolution_status': 'RESOLVED'}):
                rejected = client.put(url, json=payload, headers={
                    'If-Match': f'"{photo["version"]}"', 'Operation-Id': str(uuid4())})
                self.assertEqual(rejected.status_code, 422, rejected.text)
                assert_contact(photo, first)
                self.assertEqual(self.counts(photo['id']), counts)
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                self.connection.execute('UPDATE photos SET photographe_id=%s WHERE id=%s', (missing, self.photo))

    def test_native_photographer_evidence_survives_current_metadata_edits(self):
        # Real native job/report, not source columns or a nominal receipt.
        from tests.historical_projection_evidence import NativeHistoricalImport
        from tests.test_migration_core import source_fixture
        from tests.postgres_safety import connect_test_database
        from digues_webapp.database import get_connection, get_write_connection
        from digues_app.migration.native_report import iter_comparisons
        from digues_app.migration.source_inventory import canonical_json
        from digues_webapp.sirs_report_export import read_authenticated_bilan
        documents = source_fixture()
        contact = uuid4()
        documents.setdefault('Contact', []).append({'_id':contact.hex, 'nom':'Photographe historique', 'valid':True})
        observation = documents['Desordre'][0]['observations'][0]
        states = ({}, {'photographeId':None}, {'photographeId':''},
                  {'photographeId':'historical-contact'}, {'photographeId':contact.hex})
        photos = [{'id':uuid4().hex, 'chemin':'private/historical-image.jpg', 'geometry':None,
                   'valid':False, **state} for state in states]
        observation['photos'] = photos
        job = NativeHistoricalImport(self, documents)
        report_bytes = (job.directory/'bilan-exemple.json').read_bytes()
        verified, historical, _ = read_authenticated_bilan(job.context, job.marker)
        self.assertEqual(verified['provenance']['assurance'],'authenticated_historical_execution')
        self.assertEqual(historical['comparisons'],job.report['comparisons'])
        def database():
            with connect_test_database('DIGUES_DIRECT_TEST_DSN', autocommit=True) as connection:
                yield connection
        self.app.dependency_overrides[get_connection] = database
        self.app.dependency_overrides[get_write_connection] = database
        originals = {UUID(row.historical_id):row.document_json for row in job.reader.original.occurrences
                     if row.source_class.rsplit('.',1)[-1] in {'Photo','PhotoDependance'} and row.historical_id}
        comparisons = tuple(iter_comparisons(job.report))
        with fixtures.http_client(self.app) as client:
            for index, original in enumerate(photos):
                key = UUID(original['id'])
                self.assertEqual(originals[key], canonical_json(original))
                photo = client.get('/api/photos/'+str(key)).json()
                expected = str(contact) if index==4 else None
                self.assertEqual(photo['photographe_id'],expected)
                self.assertFalse(photo['content_available'])
                self.assertEqual(client.get('/api/photos/'+str(key)+'/content').status_code,404)
                # The mono-base repository fixture has no control-plane media
                # transfer job. Do not invent a pending transfer or file name.
                self.assertEqual(photo['content_state'],'unassociated')
                for obsolete in ('chemin_source','photographe_source_id','photographe_resolution_status'):
                    self.assertNotIn(obsolete,photo)
                self.assertEqual(job.connection.execute('SELECT photographe_id,storage_key FROM photos WHERE id=%s',(key,)).fetchone(),
                                 (contact if index==4 else None,None))
                headers={'If-Match':f'"{photo["version"]}"','Operation-Id':str(uuid4())}
                response=client.put('/api/photos/'+str(key),json={'libelle':'État courant'},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['photographe_id'],expected)
                self.assertEqual(client.put('/api/photos/'+str(key),json={'libelle':'État courant'},headers=headers).json(),response.json())
        self.assertEqual((job.directory/'bilan-exemple.json').read_bytes(), report_bytes)
        _, unchanged, _ = read_authenticated_bilan(job.context, job.marker)
        self.assertEqual(unchanged,historical)
        self.assertFalse(any(row.status=='MISMATCH' for row in comparisons))
        self.assertTrue(any(row.field=='chemin' and row.operator=='SOURCE_METADATA' for row in comparisons))
        self.assertTrue(any(row.field=='photographeId' and row.operator=='MEDIA_REFERENCE' for row in comparisons))

    def test_http_metadata_occ_immutable_parent_legacy_and_references(self):
        # Metadata-only Photo: an old source path is not a managed storage key.
        # Actual historical provenance is qualified by the native test above.
        self.connection.execute('UPDATE photos SET storage_key=NULL WHERE id=%s',(self.photo,))
        ref=str(uuid4()); cote=str(uuid4())
        self.connection.execute("INSERT INTO referentiel.orientations_photo(id,designation,libelle,valid) VALUES (%s,'Face','Face',true)",(ref,))
        self.connection.execute("INSERT INTO referentiel.cotes(id,designation,libelle,valid) VALUES (%s,'Gauche','Gauche',true)",(cote,))
        payload=self.prepare(date=None,libelle='L',designation='D',orientation_photo_id=ref,cote_id=cote,photographe_id=None)
        with fixtures.http_client(self.app) as client:
            response=client.post('/api/photos',json=payload,headers={'Operation-Id':str(uuid4())})
            self.assertEqual(response.status_code,201,response.text); photo=response.json()
            self.assertIsNone(photo['date']); self.assertEqual(photo['cote_libelle'],'Gauche')
            self.assertEqual(photo['orientation_photo_libelle'],'Face')
            url='/api/photos/'+payload['id']; headers={'Operation-Id':str(uuid4()),'If-Match':'"1"'}
            updated=client.put(url,json={'designation':'Changed'},headers=headers)
            self.assertEqual(updated.status_code,200,updated.text); self.assertEqual(updated.json()['version'],2)
            self.assertEqual(updated.json()['mutation']['action'],'update')
            self.assertEqual(client.put(url,json={'designation':'Changed'},headers=headers).json(),updated.json())
            self.assertEqual(client.put(url,json={'designation':'Conflict'},headers=headers|{'Operation-Id':str(uuid4())}).status_code,409)
            for change in ({'observation_id':str(uuid4())},{'chemin_source':'x'},{'storage_key':str(uuid4())},{'orientation_photo_id':'absent'}):
                self.assertEqual(client.put(url,json=change,headers={'If-Match':'"2"','Operation-Id':str(uuid4())}).status_code,422)
            self.assertEqual(client.put(url,json={},headers={'Operation-Id':str(uuid4())}).status_code,428)
            legacy=client.get(f'/api/photos/{self.photo}').json()
            self.assertFalse(legacy['content_available']); self.assertIsNone(legacy['nom_fichier'])
            self.assertEqual(client.get(f'/api/photos/{self.photo}/content').status_code,404)
            self.assertEqual(client.get(f'/api/photos/{uuid4()}/content').status_code,404)
            self.assertEqual(client.get(f'/api/photos/{uuid4()}').status_code,404)

    def test_upload_errors_and_missing_file_or_observation(self):
        with fixtures.http_client(self.app) as client:
            for data, filename, mime, expected in [(b'','a.png','image/png',422),(b'abc','a.png','image/png',415),
                    (picture(),'../a.png','image/png',422),(picture(),'a.png','image/jpeg',415)]:
                url=f'/api/photo-uploads/{uuid4()}/{hashlib.sha256(data).hexdigest()}'
                response=client.put(url,params={'observation_id':str(self.observations['desordres']),'filename':filename},data=data,headers={'Content-Type':mime})
                self.assertEqual(response.status_code,expected,response.text)
            self.assertEqual(list(Path(self.folder.name,'incoming').iterdir()),[])
            payload=self.prepare()
            for change,expected in [({'observation_id':str(uuid4())},422),({'upload_ref':str(uuid4())},404),({'id':'bad'},422),({'chemin_source':'a'},422)]:
                response=client.post('/api/photos',json=payload|change,headers={'Operation-Id':str(uuid4())})
                self.assertEqual(response.status_code,expected,response.text)
            self.assertEqual(client.post('/api/photos',json={'id':str(uuid4()),'observation_id':payload['observation_id']},headers={'Operation-Id':str(uuid4())}).status_code,422)

    def test_concurrent_retry_produces_one_event_and_one_file(self):
        payload=self.prepare(); operation=str(uuid4()); barrier=Barrier(2)
        def writer(_):
            with psycopg.connect(self.dsn,autocommit=True) as connection:
                barrier.wait(timeout=5)
                return create_photo(connection,PhotoCreate(**payload),operation)
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(writer,range(2)))
        self.assertEqual(results[0],results[1]); self.assertEqual(self.counts(payload['id']),(1,1,1))
        self.assertEqual(len(list(Path(self.folder.name,'objects').iterdir())),1)

    def test_metadata_update_preserves_existing_georeferencing(self):
        self.connection.execute("UPDATE photos SET borne_debut_distance=7.5, pr_debut=11, "
                                "position_debut=ST_GeomFromText('POINT(2 3)',3950), commentaire='historique' WHERE id=%s", (self.photo,))
        geometry_sql = ("SELECT ST_AsEWKB(geometry),ST_AsEWKB(position_debut),ST_AsEWKB(position_fin),"
                        "systeme_reperage_id,borne_debut_id,borne_fin_id,borne_debut_aval,borne_fin_aval,"
                        "borne_debut_distance,borne_fin_distance,pr_debut,pr_fin,commentaire,storage_key "
                        "FROM photos WHERE id=%s")
        before = self.connection.execute(geometry_sql, (self.photo,)).fetchone()
        with fixtures.http_client(self.app) as client:
            response=client.put(f"/api/photos/{self.photo}",json={"libelle":"Légende"},headers={"If-Match":'"2"',"Operation-Id":str(uuid4())})
            self.assertEqual(response.status_code,200,response.text)
        row=self.connection.execute("SELECT borne_debut_distance,pr_debut,ST_AsText(position_debut),commentaire,storage_key FROM photos WHERE id=%s",(self.photo,)).fetchone()
        self.assertEqual(row,(7.5,11,'POINT(2 3)','historique',f'private/{self.photo}.jpg'))
        self.assertEqual(self.connection.execute(geometry_sql, (self.photo,)).fetchone(), before)


    def test_storage_schema_nullability_and_reference_constraint(self):
        columns=dict(self.connection.execute("SELECT column_name,is_nullable FROM information_schema.columns WHERE table_name='photos'").fetchall())
        self.assertEqual(columns['storage_key'],'YES'); self.assertEqual(columns['version'],'NO')
        self.assertTrue({'chemin_source','photographe_source_id','statut_resolution_photographe'}.isdisjoint(columns))
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute("INSERT INTO photos(observation_id,storage_key,valid) VALUES (%s,'',true)",(self.observations['desordres'],))
        self.assertEqual(self.connection.execute('SELECT storage_key,version FROM photos WHERE id=%s',(self.photo,)).fetchone(),(f'private/{self.photo}.jpg',1))
