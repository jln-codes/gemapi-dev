"""Regression proofs for I-01 (V2 labels) and I-02 (resumable media cleanup)."""
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

import psycopg

from digues_webapp import media_storage
from digues_webapp.data_views import execute_view_query, execute_view_export
from digues_webapp.geopackage import build_geopackage
from digues_webapp.models import DataViewFieldSelection
from digues_webapp.photos import PhotoCreate, create_photo
from webapp.tests import test_photos as photo_fixtures
from webapp.tests import test_resource_versions as version_fixtures
from webapp.tests.test_photos import picture
from webapp.tests.test_resource_versions import http_client


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'PostGIS jetable non configuré')
class PhotoAuditFixesPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        version_fixtures.ResourceVersionsPostgresTest.setUpClass()
        cls.dsn = os.environ['DIGUES_OCC_TEST_DSN']

    setUp = photo_fixtures.PhotoPostgresTest.setUp
    prepare = photo_fixtures.PhotoPostgresTest.prepare

    def test_v2_base_entity_gpkg_and_photo_detail_agree(self):
        from osgeo import ogr
        selections = [DataViewFieldSelection(table='photos', field='sens_' + end)
                      for end in ('debut', 'fin')]
        cases = [(True, 12, 'Amont', 'AVANT_BORNE'),
                 (False, 12, 'Aval', 'APRES_BORNE'),
                 (True, 0, 'Sur la borne', 'SUR_BORNE'),
                 (False, 0, 'Sur la borne', 'SUR_BORNE'),
                 (None, 0, 'Sur la borne', 'SUR_BORNE'),
                 (None, 12, None, None), (None, None, None, None)]
        expected = {}
        for flag, distance, label, relative in cases:
            identifier = str(uuid4())
            self.connection.execute(
                "INSERT INTO photos(id,observation_id,chemin_source,valid,geometry,"
                "borne_debut_aval,borne_debut_distance,borne_fin_aval,borne_fin_distance) "
                "VALUES (%s,%s,'historique.jpg',true,ST_SetSRID(ST_Point(1700000,8200000),3950),%s,%s,%s,%s)",
                (identifier, self.observations['desordres'], flag, distance, flag, distance))
            expected[identifier] = (label, relative)

        def assert_rows(result):
            found = set()
            for row in result['rows']:
                for entity in row['entities']:
                    if entity['class'] == 'photos' and entity['id'] in expected:
                        found.add(entity['id'])
                        label = expected[entity['id']][0]
                        self.assertEqual(row['values'], [label, label])
                        for end in ('debut', 'fin'):
                            self.assertEqual(entity['attributes']['sens_' + end], label)
            return found

        found, offset = set(), 0
        while True:
            result = execute_view_query(self.connection, 'photos', selections, offset=offset)
            found.update(assert_rows(result))
            if not result['has_next']:
                break
            offset += result['page_size']
        self.assertEqual(found, set(expected))
        exported = execute_view_export(self.connection, 'photos', selections)
        self.assertEqual(assert_rows(exported), set(expected))
        with tempfile.NamedTemporaryFile(suffix='.gpkg') as file:
            file.write(build_geopackage(exported, ['photos'])); file.flush()
            dataset = ogr.Open(file.name)
            layer = dataset.GetLayerByName('photos_points')
            found = set()
            for feature in layer:
                identifier = feature.GetField('id_objet')
                if identifier in expected:
                    found.add(identifier)
                    for end in ('debut', 'fin'):
                        self.assertEqual(feature.GetField('sens_' + end), expected[identifier][0])
            self.assertEqual(found, set(expected))
            found = set()
            for feature in dataset.GetLayerByName('vue_resultats'):
                for identifier in json.loads(feature.GetField('photos_ids') or '[]'):
                    if identifier in expected:
                        found.add(identifier)
                        for end in ('debut', 'fin'):
                            self.assertEqual(feature.GetField('photos_sens_' + end), expected[identifier][0])
            self.assertEqual(found, set(expected))
            dataset = None
        with http_client(self.app) as client:
            for identifier, (_, relative) in expected.items():
                response = client.get('/api/photos/' + identifier)
                self.assertEqual(response.status_code, 200, response.text)
                photo = response.json()
                self.assertEqual(photo['version'], 1)  # All these reads are inert.
                for end in ('debut', 'fin'):
                    self.assertEqual(photo['localisation']['position_' + end + '_relative'], relative)

    def test_cleanup_resumes_every_interruption_boundary(self):
        stages = ('before_intent', 'before_quarantine', 'after_quarantine',
                  'after_manifest', 'after_original', 'before_rmdir', 'after_rmdir')
        real_replace, real_rename = os.replace, os.rename
        real_rmtree = media_storage.shutil.rmtree
        for stage in stages:
            with self.subTest(stage=stage):
                payload = self.prepare()
                key = payload['upload_ref']
                bundle = Path(self.folder.name, 'objects', key)
                trash = Path(self.folder.name, 'trash', key)
                intent = Path(self.folder.name, 'deleting', key + '.json')
                def replace(source, destination):
                    if stage == 'before_intent' and Path(destination) == intent:
                        raise OSError('interrupted before intent publication')
                    return real_replace(source, destination)
                def rename(source, destination):
                    if Path(source) == bundle and stage == 'before_quarantine':
                        raise OSError('interrupted before quarantine')
                    result = real_rename(source, destination)
                    if Path(source) == bundle and stage == 'after_quarantine':
                        raise OSError('interrupted after quarantine')
                    return result
                def remove(path, *args, **kwargs):
                    if Path(path) == trash:
                        if stage == 'after_manifest':
                            (trash / 'manifest.json').unlink()
                        elif stage == 'after_original':
                            (trash / 'original').unlink()
                        elif stage == 'before_rmdir':
                            for child in trash.iterdir():
                                child.unlink()
                        elif stage == 'after_rmdir':
                            real_rmtree(path, *args, **kwargs)
                        else:
                            return real_rmtree(path, *args, **kwargs)
                        raise OSError('interrupted during removal')
                    return real_rmtree(path, *args, **kwargs)
                with (patch.object(media_storage.os, 'replace', side_effect=replace),
                      patch.object(media_storage.os, 'rename', side_effect=rename),
                      patch.object(media_storage.shutil, 'rmtree', side_effect=remove),
                      self.assertRaises(OSError)):
                    media_storage.clean_prepared(self.connection, self.storage, time.time() + 86401)
                candidates = list(self.storage.candidates())
                self.assertEqual([m['storage_key'] for m in candidates], [key])
                self.assertEqual(candidates[0]['owner']['photo_id'], payload['id'])
                result = media_storage.clean_prepared(self.connection, self.storage, time.time() + 86401)
                self.assertEqual(result['prepared_removed'], 1)
                self.assertFalse(bundle.exists()); self.assertFalse(trash.exists()); self.assertFalse(intent.exists())
                self.assertIsNone(self.storage.prepared_for(payload['id']))
                self.assertEqual(media_storage.clean_prepared(self.connection, self.storage)['prepared_removed'], 0)

    def test_two_collectors_serialize_and_delete_only_once(self):
        payload = self.prepare()
        barrier = Barrier(2)
        def collect(_):
            storage = media_storage.FilesystemMediaStorage(self.folder.name)
            candidates = list(storage.candidates())
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                with patch.object(storage, 'candidates', return_value=iter(candidates)):
                    barrier.wait(timeout=5)
                    return media_storage.clean_prepared(connection, storage, time.time() + 86401)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(collect, range(2)))
        self.assertEqual(sum(r['prepared_removed'] for r in results), 1)
        self.assertEqual(list(self.storage.candidates()), [])
        self.assertIsNone(self.storage.inspect(payload['upload_ref']))

    def test_finalization_lock_and_sql_reference_protect_original(self):
        payload = self.prepare()
        # Keep the real creation transaction uncommitted while cleanup requests its lock.
        waiting = Event()
        real_lock = media_storage.lock_photo
        def attempted_lock(connection, identifier):
            waiting.set()
            return real_lock(connection, identifier)
        def collect():
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                return media_storage.clean_prepared(connection, self.storage, time.time() + 86401)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connection.transaction():
                create_photo(self.connection, PhotoCreate(**payload), str(uuid4()))
                with patch.object(media_storage, 'lock_photo', side_effect=attempted_lock):
                    future = pool.submit(collect)
                    self.assertTrue(waiting.wait(5))
                    self.assertFalse(future.done())
            self.assertEqual(future.result(timeout=5)['prepared_removed'], 0)
        # A later pass also protects a Photo consumed just before cleanup.
        self.assertEqual(collect()['prepared_removed'], 0)
        with self.storage.open_variant(payload['upload_ref']) as stream:
            self.assertEqual(stream.read(), picture())

    def test_retry_rechecks_sql_even_with_deletion_intent(self):
        payload = self.prepare()
        # Crash after durable intent, before moving the original.
        with patch.object(media_storage.os, 'rename', side_effect=OSError('crash')), self.assertRaises(OSError):
            media_storage.clean_prepared(self.connection, self.storage, time.time() + 86401)
        # The original is still available: a valid finalization may win before retry.
        create_photo(self.connection, PhotoCreate(**payload), str(uuid4()))
        self.assertEqual(media_storage.clean_prepared(self.connection, self.storage, time.time() + 86401)['prepared_removed'], 0)
        with self.storage.open_variant(payload['upload_ref']) as stream:
            self.assertEqual(stream.read(), picture())

    def test_cleanup_winning_lock_prevents_finalization_of_removed_media(self):
        from fastapi import HTTPException
        from digues_webapp import photos
        payload = self.prepare()
        quarantining, release, finalizing = Event(), Event(), Event()
        real_rename, real_lock = os.rename, photos.lock_photo
        def pause_rename(source, destination):
            quarantining.set()
            if not release.wait(5):
                raise TimeoutError('test did not release cleanup')
            return real_rename(source, destination)
        def attempted_lock(connection, identifier):
            finalizing.set()
            return real_lock(connection, identifier)
        def collect():
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                return media_storage.clean_prepared(connection, self.storage, time.time() + 86401)
        def finalize():
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                return create_photo(connection, PhotoCreate(**payload), str(uuid4()))
        with ThreadPoolExecutor(max_workers=2) as pool:
            with (patch.object(media_storage.os, 'rename', side_effect=pause_rename),
                  patch.object(photos, 'lock_photo', side_effect=attempted_lock)):
                cleanup = pool.submit(collect)
                try:
                    self.assertTrue(quarantining.wait(5))
                    creation = pool.submit(finalize)
                    self.assertTrue(finalizing.wait(5))
                    self.assertFalse(creation.done())
                finally:
                    release.set()
                self.assertEqual(cleanup.result(timeout=5)['prepared_removed'], 1)
                with self.assertRaises(HTTPException) as error:
                    creation.result(timeout=5)
                self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM photos WHERE id=%s', (payload['id'],)).fetchone()[0], 0)

    def test_quarantined_original_survives_sql_failure_and_later_is_reconciled(self):
        payload = self.prepare()
        key = payload['upload_ref']
        # Stop before rmtree, leaving the full quarantined original and its intent.
        with patch.object(media_storage.shutil, 'rmtree', side_effect=OSError('crash')), self.assertRaises(OSError):
            media_storage.clean_prepared(self.connection, self.storage, time.time() + 86401)
        trash = Path(self.folder.name, 'trash', key)
        with patch.object(self.connection, 'execute', side_effect=psycopg.OperationalError('unavailable')), self.assertRaises(psycopg.OperationalError):
            media_storage.clean_prepared(self.connection, self.storage)
        self.assertEqual((trash / 'original').read_bytes(), picture())
        self.assertEqual(media_storage.clean_prepared(self.connection, self.storage)['prepared_removed'], 1)

    def test_corruption_without_deletion_intent_never_authorizes_removal(self):
        payload = self.prepare()
        bundle = Path(self.folder.name, 'objects', payload['upload_ref'])
        (bundle / 'manifest.json').unlink()
        for _ in range(2):
            with self.assertLogs(media_storage.LOGGER, level='WARNING'):
                result = media_storage.clean_prepared(self.connection, self.storage, time.time() + 86401)
            self.assertEqual(result['prepared_removed'], 0)
            self.assertEqual((bundle / 'original').read_bytes(), picture())
        # A malformed intent is also insufficient, even alongside that directory.
        intent = Path(self.folder.name, 'deleting', payload['upload_ref'] + '.json')
        intent.write_text(json.dumps({'storage_key': payload['upload_ref']}))
        with self.assertLogs(media_storage.LOGGER, level='WARNING'):
            self.assertEqual(media_storage.clean_prepared(self.connection, self.storage, time.time() + 86401)['prepared_removed'], 0)
        self.assertTrue((bundle / 'original').exists())
