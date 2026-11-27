"""Photos binaires synthétiques, sans base métier."""
import hashlib
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

from digues_webapp.media_storage import FilesystemMediaStorage, validate_image
from digues_webapp.photos import PhotoCreate, PhotoMetadata, validate_references


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
            private_sample = Path(folder, 'synthetic-private.txt')
            private_sample.write_bytes(b'private sample')
            (bundle/'original').unlink()
            (bundle/'original').symlink_to(private_sample)
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
