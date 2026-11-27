"""Private namespaces: synthetic files only, deliberately colliding identities."""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from digues_app.reports.disorder_sheet import DisorderPhoto
from digues_webapp import media_storage as media
from digues_webapp.desordre_pdf import BusinessPhotoLoader
from digues_webapp.media_target import MediaMaintenanceTarget
from webapp.tests.test_business_context import context
from PIL import Image


def picture():
    out = io.BytesIO()
    Image.new('RGB', (12, 8), 'blue').save(out, format='PNG')
    return out.getvalue()


def prepare(storage, data, *, key=None, photo=None, observation=None):
    photo, observation = photo or uuid4(), observation or uuid4()
    variant = media.validate_image(io.BytesIO(data), 'synthetic.png', 'image/png', hashlib.sha256(data).hexdigest())
    if key is None:
        return storage.prepare(io.BytesIO(data), photo, observation, 'synthetic.png', variant)
    with patch('digues_app.media_ingestion.uuid4', return_value=key):
        return storage.prepare(io.BytesIO(data), photo, observation, 'synthetic.png', variant)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class MediaTenancyTest(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, MEDIA_ROOT=self.folder, MEDIA_BACKEND='filesystem',
                                     LEGACY_SINGLE_DATABASE_ENABLED='false'))
        self.alpha, self.beta = context(), replace(context(), database_name='beta')
        self.a = media.get_media_storage(context=self.alpha)
        self.b = media.get_media_storage(context=self.beta)

    def test_same_photo_key_different_content_and_namespace(self):
        photo, observation, key = uuid4(), uuid4(), uuid4()
        a = prepare(self.a, picture(), key=key, photo=photo, observation=observation)
        # Different bytes but still a valid image, with exactly the same UUIDs.
        from PIL import Image
        out = io.BytesIO(); Image.new('RGB', (3, 4), 'red').save(out, format='PNG')
        prepare(self.b, out.getvalue(), key=key, photo=photo, observation=observation)
        self.assertEqual(self.a.root, Path(self.folder) / str(self.alpha.business_database_id))
        self.assertEqual(a['business_database_id'], str(self.alpha.business_database_id))
        self.assertEqual(a['media_namespace'], str(self.alpha.media_namespace))
        for store, content in ((self.a, picture()), (self.b, out.getvalue())):
            with store.open_variant(key) as stream:
                self.assertEqual(stream.read(), content)
        from digues_webapp.photos import check_prepared
        conn = Mock()
        with self.assertRaises(HTTPException) as denied:
            check_prepared(conn, self.b.inspect(key), photo, observation, a['variants']['original']['sha256'])
        self.assertEqual(denied.exception.status_code, 409)
        conn.execute.assert_not_called()
        before = snapshot(self.b.root)
        self.a.discard(key)
        self.assertIsNone(self.a.prepared_for(photo))
        self.assertEqual(snapshot(self.b.root), before)
        self.assertIsNotNone(self.b.prepared_for(photo))

    def test_foreign_preparation_binding_manifest_and_marker_refused(self):
        photo = uuid4()
        manifest = prepare(self.a, picture(), photo=photo)
        key = manifest['storage_key']
        self.assertIsNone(self.b.prepared_for(photo))
        self.assertIsNone(self.b.inspect(key))
        shutil.copytree(self.a.root / 'objects' / key, self.b.root / 'objects' / key)
        for operation in (lambda: self.b.inspect(key), lambda: self.b.open_variant(key),
                          lambda: list(self.b.candidates()), lambda: self.b.discard(key)):
            with self.assertRaises(media.MediaScopeError):
                operation()
        shutil.copyfile(self.a.root / 'bindings' / f'{photo}.json', self.b._dir('bindings') / f'{photo}.json')
        with self.assertRaises(media.MediaScopeError):
            self.b.prepared_for(photo)
        altered = replace(self.alpha, media_namespace=uuid4())
        with self.assertRaises(media.MediaScopeError):
            media.get_media_storage(context=altered).inspect(key)

    def test_no_legacy_fallback_or_adoption_and_legacy_format_unchanged(self):
        with self.assertRaises(HTTPException):
            media.get_media_storage()
        self.assertIsNone(media.get_media_storage(required=False))
        with self.assertRaises(TypeError):
            media.get_media_storage(context={'business_database_id': '../beta'})
        with patch.dict(os.environ, LEGACY_SINGLE_DATABASE_ENABLED='true'):
            legacy = media.get_media_storage()
            value = prepare(legacy, picture())
        self.assertEqual(value['schema_version'], 1)
        self.assertNotIn('business_database_id', value)
        self.assertIsNone(self.a.inspect(value['storage_key']))
        # A v1 bundle must not be silently upgraded/adopted by a tenant.
        shutil.copytree(legacy.root / 'objects' / value['storage_key'], self.a.root / 'objects' / value['storage_key'])
        with self.assertRaises(media.MediaScopeError):
            self.a.inspect(value['storage_key'])
        self.assertEqual(legacy.inspect(value['storage_key'])['schema_version'], 1)

    def test_legacy_cleanup_never_enumerates_business_namespaces(self):
        prepare(self.a, picture())
        before = snapshot(self.a.root)
        with patch.dict(os.environ, LEGACY_SINGLE_DATABASE_ENABLED='true'):
            legacy = media.get_media_storage()
            prepare(legacy, picture())
            conn = Mock(); conn.transaction.return_value = nullcontext()
            conn.execute.return_value.fetchone.return_value = None
            result = media.clean_prepared(conn, legacy, time.time() + 86401)
        self.assertEqual(result['prepared_removed'], 1)
        self.assertEqual(snapshot(self.a.root), before)

    def test_symlinks_at_root_parent_bundle_and_variant_are_refused(self):
        parent = Path(self.folder) / 'link'
        outside = Path(self.folder) / 'outside'; outside.mkdir()
        parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            media.FilesystemMediaStorage(parent / 'nested')
        value = prepare(self.a, picture())
        bundle = self.a.root / 'objects' / value['storage_key']
        original = bundle / 'original'
        original.unlink(); original.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):
            self.a.open_variant(value['storage_key'])
        original.unlink()
        shutil.rmtree(bundle); bundle.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.a.inspect(value['storage_key'])
        # Swapping a previously constructed namespace for a symlink also fails.
        shutil.rmtree(self.a.root)
        self.a.root.symlink_to(self.b.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.a.clean_incoming(time.time())

    def test_cleanup_requires_paired_target_and_usable_db_before_any_file_access(self):
        prepare(self.a, picture())
        partial = self.a._dir('incoming') / 'old.part'
        partial.write_bytes(b'partial'); os.utime(partial, (0, 0))
        before = snapshot(self.a.root)
        conn = Mock()
        for target in (None, self.beta):
            with self.assertRaises(media.MediaScopeError):
                media.clean_prepared(conn, self.a, context=target)
        conn.execute.return_value.fetchone.return_value = ('beta',)
        with self.assertRaises(media.MediaScopeError):
            media.clean_prepared(conn, self.a, context=self.alpha)
        conn.execute.side_effect = OSError('database unavailable')
        with self.assertRaises(OSError):
            media.clean_prepared(conn, self.a, context=self.alpha)
        self.assertEqual(snapshot(self.a.root), before)

    def test_cleanup_only_collects_one_namespace_and_keeps_references(self):
        referenced, orphan = uuid4(), uuid4()
        for store in (self.a, self.b):
            prepare(store, picture(), key=referenced)
            prepare(store, picture(), key=orphan)
            partial = store._dir('incoming') / 'abandoned.part'
            partial.write_bytes(b'abandoned'); os.utime(partial, (0, 0))
        for store, other, target in ((self.a, self.b, self.alpha), (self.b, self.a, self.beta)):
            before = snapshot(other.root)
            conn = Mock(); conn.transaction.return_value = nullcontext()
            def execute(query, params=()):
                result = (target.database_name,) if query == 'SELECT current_database()' else (
                    (1,) if params and params[0] == str(referenced) else None)
                return Mock(fetchone=Mock(return_value=result))
            conn.execute.side_effect = execute
            result = media.clean_prepared(conn, store, time.time() + 86401, context=target)
            self.assertEqual(result['prepared_removed'], 1)
            self.assertEqual(result['partials_removed'], 1)
            self.assertIsNotNone(store.inspect(referenced))
            self.assertIsNone(store.inspect(orphan))
            self.assertEqual(snapshot(other.root), before)

    def test_pdf_rechecks_sql_before_media_and_fails_closed_on_mismatch(self):
        photo, observation = uuid4(), uuid4()
        value = prepare(self.a, picture(), photo=photo, observation=observation)
        entry = DisorderPhoto(identifier=str(photo), storage_key=value['storage_key'])
        conn = Mock(); conn.execute.return_value.fetchone.return_value = None
        loader = BusinessPhotoLoader(conn, self.alpha)
        with patch('digues_webapp.desordre_pdf.get_media_storage') as storage:
            self.assertIsNone(loader(entry)); storage.assert_not_called()
        conn.execute.return_value.fetchone.return_value = (value['storage_key'], observation)
        self.assertEqual(loader(entry), picture()); loader.check()
        conn.execute.return_value.fetchone.return_value = (value['storage_key'], uuid4())
        with self.assertRaises(media.MediaScopeError):
            loader(entry)
        # Renderers may catch the exception, but the HTTP adapter must still fail.
        with self.assertRaises(HTTPException):
            loader.check()

    def test_cli_requires_explicit_target_and_never_opens_legacy_by_default(self):
        with patch('sys.argv', ['digues-webapp-clean-media']), patch('digues_webapp.database.open_write_connection') as opened:
            with self.assertRaises(SystemExit):
                media.main()
            opened.assert_not_called()
        with patch.dict(os.environ, LEGACY_SINGLE_DATABASE_ENABLED='true'), \
                patch('sys.argv', ['digues-webapp-clean-media', '--business-database-id', str(uuid4())]), \
                patch('digues_webapp.database.open_write_connection') as opened:
            with self.assertRaises(SystemExit):
                media.main()
            opened.assert_not_called()

    def test_cli_propagates_resolved_target_without_global_connection(self):
        from digues_webapp import media_target
        target = MediaMaintenanceTarget(self.alpha.business_database_id, self.alpha.media_namespace,
                                        self.alpha.database_name, self.alpha.cluster_ref,
                                        self.alpha.connection_profile_ref,
                                        self.alpha.schema_version, self.alpha.data_epoch)
        conn = Mock()
        with patch('sys.argv', ['digues-webapp-clean-media', '--business-database-id', str(target.business_database_id)]), \
                patch.object(media_target, 'resolve_media_maintenance_target', return_value=target) as resolve, \
                patch('digues_webapp.database.open_write_connection', return_value=nullcontext(conn)) as opened, \
                patch.object(media, 'clean_prepared', return_value={}) as clean, patch('builtins.print'):
            media.main()
            resolve.assert_called_once_with(target.business_database_id)
            opened.assert_called_once_with(target)
            args, kwargs = clean.call_args
            self.assertIs(args[0], conn)
            self.assertEqual(args[1].business_database_id, target.business_database_id)
            self.assertEqual(kwargs['context'], target)

    def test_concurrent_initialization_and_cleanup_recovery_remain_scoped(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            manifests = list(pool.map(lambda _: prepare(self.a, picture()), range(4)))
        self.assertEqual(len(list(self.a.candidates())), 4)
        self.assertEqual(json.loads((self.a.root / 'namespace.json').read_text())['business_database_id'],
                         str(self.alpha.business_database_id))
        manifest = manifests[0]
        prepare(self.b, picture(), key=manifest['storage_key'])
        before = snapshot(self.b.root)
        with patch.object(media.shutil, 'rmtree', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            self.a.discard(manifest['storage_key'])
        self.assertTrue(any(m.get('state') == 'deleting' for m in self.a.candidates()))
        self.a.discard(manifest['storage_key'])
        self.assertEqual(snapshot(self.b.root), before)
        self.assertEqual(len(list(self.a.candidates())), 3)
