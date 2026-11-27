"""``GET /api/desordres/{id}/pdf`` : contrat HTTP de la fiche Désordre.

Les tests n'ont besoin ni de PostgreSQL ni de réseau : la connexion est
remplacée par une dépendance factice, la carte est remplacée par une image
synthétique et le stockage média est injecté. Ils vérifient le contrat HTTP et
l'obligation de passer par ``MediaStorage`` pour lire une photo.
"""

import io
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from PIL import Image

from digues_app.reports.disorder_sheet import (
    DisorderMapContext,
    DisorderObservation,
    DisorderPhoto,
    build_disorder_sheet,
    DisorderSheetData,
)
from digues_app.reports.repository import DisorderSheetNotFound
from digues_webapp.database import get_connection
from digues_webapp.desordre_pdf import register_routes
from webapp.tests import test_resource_versions as fixtures


def image_bytes(size=(40, 30), fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", size, (90, 140, 200)).save(stream, format=fmt)
    return stream.getvalue()


def make_sheet(photo=None, with_map=True):
    return build_disorder_sheet(
        {
            "designation": "Fissure — secteur 01",
            "commentaire": "Commentaire",
            "type_desordre_id": "RefTypeDesordre:2",
            "cote_id": "RefCote:1",
            "position_id": "RefPosition:8",
            "latitude": 50.5,
            "longitude": 2.5,
        },
        {"libelle": "Tronçon 01"},
        observer_name=lambda _author: "-",
        disorder_types={"2": "Fissure"},
        sides={"1": "Rivière"},
        positions={"8": "Crête"},
        observations=({"date": "2026-04-01", "author": None},),
        photos=[photo] if photo else [],
        map_context=(
            DisorderMapContext(section_line=((0.0, 0.0), (100.0, 100.0)))
            if with_map
            else None
        ),
    )


class FakeStorage:
    """Stockage média minimal, conforme au protocole ``MediaStorage``."""

    def __init__(self, content):
        self.content = content
        self.calls = []

    def open_variant(self, storage_key, variant="original"):
        self.calls.append((storage_key, variant))
        if self.content is None:
            raise FileNotFoundError(storage_key)
        return io.BytesIO(self.content)


class DisorderPdfRouteTest(unittest.TestCase):
    def build_client(self, sheet, storage=None):
        application = FastAPI()
        register_routes(application)
        application.dependency_overrides[get_connection] = lambda: object()
        patches = [
            patch(
                "digues_app.reports.repository.load_disorder_sheet",
                return_value=sheet,
            ),
            patch(
                "digues_app.reports.map.render_map_image",
                return_value=image_bytes((120, 90)),
            ),
        ]
        if storage is not None:
            patches.append(
                patch("digues_webapp.desordre_pdf.get_media_storage", return_value=storage)
            )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return fixtures.http_client(application)

    def test_returns_a_pdf_with_a_coherent_filename(self):
        disorder_id = uuid4()
        with self.build_client(make_sheet()) as client:
            response = client.get(f"/api/desordres/{disorder_id}/pdf")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", response.content[-2048:])
        disposition = response.headers["content-disposition"]
        self.assertIn("attachment;", disposition)
        self.assertIn("desordre_Tron", disposition)
        self.assertIn("filename*=UTF-8''", disposition)

    def test_unknown_disorder_returns_404(self):
        disorder_id = uuid4()
        application = FastAPI()
        register_routes(application)
        application.dependency_overrides[get_connection] = lambda: object()
        with patch(
            "digues_app.reports.repository.load_disorder_sheet",
            side_effect=DisorderSheetNotFound(str(disorder_id)),
        ):
            with fixtures.http_client(application) as client:
                response = client.get(f"/api/desordres/{disorder_id}/pdf")

        self.assertEqual(response.status_code, 404)

    def test_photo_content_is_read_through_media_storage(self):
        storage = FakeStorage(image_bytes((450, 337), "JPEG"))
        photo = DisorderPhoto(identifier="photo-1", storage_key="cle-technique")
        disorder_id = uuid4()

        with self.build_client(make_sheet(photo=photo), storage=storage) as client:
            response = client.get(f"/api/desordres/{disorder_id}/pdf")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(storage.calls, [("cle-technique", "original")])

    def test_photo_without_storage_key_is_not_requested(self):
        storage = FakeStorage(image_bytes())
        photo = DisorderPhoto(identifier="photo-1", storage_key=None)
        disorder_id = uuid4()

        with self.build_client(make_sheet(photo=photo), storage=storage) as client:
            response = client.get(f"/api/desordres/{disorder_id}/pdf")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(storage.calls, [])

    def test_unavailable_media_does_not_break_the_fiche(self):
        storage = FakeStorage(None)
        photo = DisorderPhoto(identifier="photo-1", storage_key="cle-manquante")
        disorder_id = uuid4()

        with self.build_client(make_sheet(photo=photo), storage=storage) as client:
            response = client.get(f"/api/desordres/{disorder_id}/pdf")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_fiche_is_produced_without_map(self):
        disorder_id = uuid4()
        with self.build_client(make_sheet(with_map=False)) as client:
            response = client.get(f"/api/desordres/{disorder_id}/pdf")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_route_is_registered_on_the_application(self):
        from digues_webapp.app import create_app

        paths = {route.path for route in create_app().routes}
        self.assertIn("/api/desordres/{desordre_id}/pdf", paths)


if __name__ == "__main__":
    unittest.main()
