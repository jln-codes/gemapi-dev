"""``/api/.../desordres/catalogue`` : contrat HTTP du catalogue patrimonial.

Les tests n'ont besoin ni de PostgreSQL ni de réseau : le cœur du catalogue est
remplacé, la connexion est une dépendance factice. Ils vérifient le contrat par
nœud Patrimoine (système, digue, tronçon), le PDF unique et les cas vides ou
inconnus.
"""

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI

from digues_app.reports.catalog import (
    CatalogDigue,
    CatalogEntry,
    CatalogScope,
    CatalogScopeNotFound,
    CatalogSysteme,
    CatalogTroncon,
    DisorderCatalog,
)
from digues_app.reports.disorder_sheet import DisorderSheetData
from digues_app.reports.pdf import render_disorder_sheet_pdf
from digues_webapp.database import get_connection
from digues_webapp.desordre_catalog import WARNING_HEADER, register_routes
from webapp.tests import test_resource_versions as fixtures


def pdf_bytes() -> bytes:
    sheet = DisorderSheetData(
        designation="D-1",
        section_label="TR-01",
        disorder_type="-",
        comment="-",
        observations=(),
        photo=None,
        latitude="-",
        longitude="-",
        side="-",
        position="-",
        map_context=None,
    )
    return render_disorder_sheet_pdf(sheet)


def make_catalog(
    *,
    scope_kind="systeme",
    scope_label="SE Carbone",
    scope_identifier=None,
    entry_count=2,
    systeme_id="4a526cb4-3dc0-5b63-84c4-18f3facfbffa",
    systeme_label="SE Carbone",
    warnings=(),
) -> DisorderCatalog:
    entries = tuple(
        CatalogEntry(str(uuid4()), f"D-{index}") for index in range(entry_count)
    )
    troncons = (CatalogTroncon(str(uuid4()), "TR-01", entries),)
    digues = (CatalogDigue(str(uuid4()), "Digue A", troncons),)
    systemes = (
        CatalogSysteme(
            identifier=systeme_id,
            label=systeme_label if systeme_id else "Sans système d'endiguement",
            digues=digues,
        ),
    ) if entry_count else ()
    return DisorderCatalog(
        scope=CatalogScope(
            kind=scope_kind,
            identifier=scope_identifier or systeme_id or str(uuid4()),
            label=scope_label,
        ),
        systemes=systemes,
        warnings=tuple(warnings),
    )


class DisorderCatalogRouteTest(unittest.TestCase):
    def build_client(self, loaded):
        application = FastAPI()
        register_routes(application)
        application.dependency_overrides[get_connection] = lambda: object()
        patches = [
            patch(
                "digues_app.reports.catalog.load_patrimoine_catalog",
                side_effect=lambda _connection, _kind, _identifier: loaded,
            ),
            patch(
                "digues_app.reports.catalog.render_catalog_pdf",
                return_value=pdf_bytes(),
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return fixtures.http_client(application)

    def test_system_scope_returns_the_hierarchical_json(self):
        with self.build_client(make_catalog(entry_count=3)) as client:
            response = client.get(
                f"/api/systemes-endiguement/{uuid4()}/desordres/catalogue"
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["scope"]["kind"], "systeme")
        self.assertEqual(body["scope"]["label"], "SE Carbone")
        self.assertEqual(body["entry_count"], 3)
        self.assertEqual(body["digue_count"], 1)
        self.assertEqual(body["troncon_count"], 1)
        systeme = body["systemes"][0]
        self.assertEqual(systeme["label"], "SE Carbone")
        self.assertEqual(systeme["digues"][0]["label"], "Digue A")
        troncon = systeme["digues"][0]["troncons"][0]
        self.assertEqual(troncon["label"], "TR-01")
        self.assertEqual(len(troncon["entries"]), 3)

    def test_system_scope_returns_a_direct_pdf(self):
        with self.build_client(make_catalog(warnings=["un avertissement"])) as client:
            response = client.get(
                f"/api/systemes-endiguement/{uuid4()}/desordres/catalogue/pdf"
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))
        disposition = response.headers["content-disposition"]
        self.assertIn("attachment;", disposition)
        self.assertIn("Desordres_SE Carbone.pdf", disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        self.assertEqual(response.headers[WARNING_HEADER], "1")

    def test_digue_scope_uses_the_historical_dike_prefix(self):
        with self.build_client(
            make_catalog(scope_kind="digue", scope_label="Digue A")
        ) as client:
            response = client.get(f"/api/digues/{uuid4()}/desordres/catalogue/pdf")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            "Desordres_digue_Digue A.pdf",
            response.headers["content-disposition"],
        )

    def test_troncon_scope_uses_the_troncon_prefix(self):
        with self.build_client(
            make_catalog(scope_kind="troncon", scope_label="TR-01")
        ) as client:
            response = client.get(f"/api/troncons/{uuid4()}/desordres/catalogue/pdf")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            "Desordres_troncon_TR-01.pdf",
            response.headers["content-disposition"],
        )

    def test_unknown_scope_returns_404(self):
        application = FastAPI()
        register_routes(application)
        application.dependency_overrides[get_connection] = lambda: object()
        with patch(
            "digues_app.reports.catalog.load_patrimoine_catalog",
            side_effect=CatalogScopeNotFound("absent"),
        ):
            with fixtures.http_client(application) as client:
                response = client.get(
                    f"/api/systemes-endiguement/{uuid4()}/desordres/catalogue"
                )
                pdf_response = client.get(
                    f"/api/systemes-endiguement/{uuid4()}/desordres/catalogue/pdf"
                )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(pdf_response.status_code, 404)

    def test_empty_scope_is_json_200_but_pdf_404(self):
        with self.build_client(make_catalog(entry_count=0)) as client:
            json_response = client.get(
                f"/api/systemes-endiguement/{uuid4()}/desordres/catalogue"
            )
            pdf_response = client.get(
                f"/api/systemes-endiguement/{uuid4()}/desordres/catalogue/pdf"
            )

        self.assertEqual(json_response.status_code, 200, json_response.text)
        self.assertEqual(json_response.json()["entry_count"], 0)
        self.assertEqual(json_response.json()["systemes"], [])
        self.assertEqual(pdf_response.status_code, 404)

    def test_digue_without_system_serialises_the_synthetic_root(self):
        with self.build_client(
            make_catalog(
                scope_kind="digue",
                scope_label="Digue seule",
                systeme_id=None,
                entry_count=1,
            )
        ) as client:
            response = client.get(f"/api/digues/{uuid4()}/desordres/catalogue")

        self.assertEqual(response.status_code, 200, response.text)
        systeme = response.json()["systemes"][0]
        self.assertIsNone(systeme["id"])
        self.assertEqual(systeme["label"], "Sans système d'endiguement")

    def test_routes_are_registered_and_legacy_routes_are_gone(self):
        from digues_webapp.app import create_app

        paths = {route.path for route in create_app().routes}
        for path in (
            "/api/systemes-endiguement/{systeme_id}/desordres/catalogue",
            "/api/systemes-endiguement/{systeme_id}/desordres/catalogue/pdf",
            "/api/digues/{digue_id}/desordres/catalogue",
            "/api/digues/{digue_id}/desordres/catalogue/pdf",
            "/api/troncons/{troncon_id}/desordres/catalogue",
            "/api/troncons/{troncon_id}/desordres/catalogue/pdf",
        ):
            self.assertIn(path, paths)
        self.assertNotIn("/api/desordres/catalogue", paths)
        self.assertNotIn("/api/desordres/catalogue/pdf", paths)


if __name__ == "__main__":
    unittest.main()
