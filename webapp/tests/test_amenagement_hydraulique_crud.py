"""Contrats DTO et HTTP AH réglementaire / Zone de stockage.

Niveau A : formes d'écriture, cardinalités et routes sans base. La validité
spatiale et les FK sont exercées dans test_amenagement_hydraulique_crud_postgres.
"""
import unittest
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from digues_webapp.app import create_app
from digues_webapp.models import (
    AmenagementHydrauliqueCreate, AmenagementHydrauliqueUpdate,
    ZoneStockageCreate, ZoneStockageUpdate,
)


FRONT = Path(__file__).resolve().parents[1] / "frontend"
POLYGON = {"type": "Polygon", "coordinates": [[
    [2, 48], [2.001, 48], [2.001, 48.001], [2, 48],
]]}


class AmenagementHydrauliqueContractTest(unittest.TestCase):
    def test_regulatory_ah_has_no_geometry_or_historical_storage_fields(self):
        self.assertEqual(AmenagementHydrauliqueCreate().zone_ids, [])
        self.assertEqual(AmenagementHydrauliqueCreate().troncon_ids, [])
        for payload in ({"geometry": None}, {"geometry": POLYGON},
                        {"type_id": "A"}, {"capacite_stockage": 20},
                        {"zone_ids": [str(uuid4())] * 2}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                AmenagementHydrauliqueCreate.model_validate(payload)
        for payload in ({}, {"geometry": None}, {"zone_ids": None},
                        {"troncon_ids": None}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                AmenagementHydrauliqueUpdate.model_validate(payload)
        self.assertIsNone(AmenagementHydrauliqueUpdate(valid=None).valid)

    def test_zone_native_polygon_and_partial_historical_update(self):
        zone = ZoneStockageCreate(geometry=POLYGON, valid=True)
        self.assertIsNone(zone.amenagement_hydraulique_id)
        self.assertEqual(zone.reseau_hydraulique_ids, [])
        for payload in ({}, {"geometry": {"type": "Point", "coordinates": [2, 48]}},
                        {"geometry": None}, {"geometry": POLYGON,
                        "reseau_hydraulique_ids": [str(uuid4())] * 2}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ZoneStockageCreate.model_validate(payload)
        self.assertEqual(ZoneStockageUpdate(commentaire="mise à jour").commentaire,
                         "mise à jour")
        for payload in ({}, {"geometry": None}, {"reseau_hydraulique_ids": None},
                        {"geometry": {"type": "LineString", "coordinates": [[2, 48], [3, 49]]}}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ZoneStockageUpdate.model_validate(payload)

    def test_routes_and_editor_boundaries(self):
        paths = {(r.path, method) for r in create_app().routes
                 if hasattr(r, "methods") for method in r.methods}
        for resource in ("amenagements-hydrauliques", "zones-stockage"):
            for suffix, method in (("", "GET"), ("", "POST"),
                                   ("/{identifier}", "GET"), ("/{identifier}", "PUT"),
                                   ("/{identifier}/observations", "GET")):
                path = f"/api/{resource}{suffix}"
                self.assertIn((path, method), paths)
                contextual = "/api/bases/{business_database_id}" + path.removeprefix("/api")
                self.assertIn((contextual, method), paths)
        self.assertIn(("/api/amenagements-hydrauliques/relations/options", "GET"), paths)
        self.assertIn(("/api/bases/{business_database_id}/amenagements-hydrauliques/relations/options", "GET"), paths)
        ah_editor = (FRONT / "js/amenagement-hydraulique-editor.js").read_text()
        zone_editor = (FRONT / "js/zone-stockage-editor.js").read_text()
        self.assertNotIn("geometryFromVertices", ah_editor)
        self.assertIn('["Tronçons associés", "troncons", p.troncons || []]', ah_editor)
        self.assertIn('MapEntityNavigation?.open(resource, row.id)', ah_editor)
        self.assertNotIn('troncons_unifies', ah_editor)
        self.assertIn('geometryTypes: ["Polygon"]', zone_editor)
        for name in ("amenagement-hydraulique-editor.js", "zone-stockage-editor.js"):
            self.assertIn(name, (FRONT / "map.html").read_text())
            self.assertIn(name, (FRONT / "service-worker.js").read_text())
