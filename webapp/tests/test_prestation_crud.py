"""F7 : DTO, routes, relations natives et frontière de provenance."""
import unittest
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from digues_webapp.app import create_app
from digues_webapp.models import PrestationCreate, PrestationUpdate, ObservationCreate


FRONT = Path(__file__).resolve().parents[1] / "frontend"


class PrestationContractTest(unittest.TestCase):
    def test_native_completed_action_and_target_fields(self):
        minimal = PrestationCreate(date_fin="2026-09-29")
        self.assertIsNone(minimal.type_prestation_id)
        self.assertEqual(minimal.troncon_ids, [])
        self.assertEqual(minimal.object_ids, {})
        self.assertIsNone(minimal.geometry)
        self.assertEqual(PrestationCreate(id=str(uuid4()), date_fin="2026-09-29")
                         .id.__class__.__name__, "UUID")
        values = {
            "date_fin": "2026-09-29", "date_debut": "2026-09-01",
            "type_prestation_id": " T ", "designation": " ",
            "libelle": " Entretien ", "commentaire": "", "cout_global": 0,
            "cout_metre": 10.5, "realisation_interne": False, "valid": False,
            "troncon_ids": [str(uuid4()), str(uuid4())],
            "object_ids": {"desordres": [str(uuid4())],
                           "vegetation": [str(uuid4())]},
            "intervenant_ids": [str(uuid4())],
        }
        for geometry in (
            None, {"type": "Point", "coordinates": [2, 48]},
            {"type": "LineString", "coordinates": [[2, 48], [3, 49]]},
            {"type": "Polygon", "coordinates": [[[2, 48], [3, 48], [3, 49], [2, 48]]]},
        ):
            with self.subTest(geometry=geometry):
                value = PrestationCreate.model_validate(values | {"geometry": geometry})
                self.assertEqual(value.type_prestation_id, "T")
                self.assertIsNone(value.designation)
                self.assertIsNone(value.commentaire)
                self.assertFalse(value.realisation_interne)
                self.assertFalse(value.valid)
                self.assertEqual(value.cout_global, 0)
        for addition in (
            {"date_fin": None}, {"date_fin": ""},
            {"troncon_ids": ["bad"]},
            {"id": "bad"},
            {"troncon_ids": [str(uuid4())] * 2},
            {"object_ids": {"unknown": [str(uuid4())]}},
            {"object_ids": {"desordres": [str(uuid4())] * 2}},
            {"intervenant_ids": [str(uuid4())] * 2},
            {"cout_global": -1}, {"cout_metre": "nan"},
            {"geometry": {"type": "MultiPoint", "coordinates": [[2, 48]]}},
            {"source_class": "Prestation"}, {"historical_nature": "ORDINARY"},
            {"source_present_fields": []}, {"linear_source_id": "t1"},
            {"statut": "REALISE"}, {"global_prestation_id": str(uuid4())},
        ):
            with self.subTest(addition=addition), self.assertRaises(ValidationError):
                PrestationCreate.model_validate(values | addition)
        for patch in ({}, {"geometry": None}, {"troncon_ids": None},
                      {"object_ids": None}, {"intervenant_ids": None},
                      {"source_class": "Prestation"}, {"object_ids": {"desordres": ["bad"]}}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                PrestationUpdate.model_validate(patch)
        self.assertIsNone(PrestationUpdate(type_prestation_id=" ").type_prestation_id)
        self.assertFalse(PrestationUpdate(realisation_interne=False).realisation_interne)

    def test_observation_parent_and_route_contract(self):
        parent = str(uuid4())
        observation = ObservationCreate.model_validate({
            "id": str(uuid4()), "parent": {"resource": "prestations", "id": parent}})
        self.assertEqual(observation.parent.resource, "prestations")
        paths = {(route.path, method) for route in create_app().routes
                 if hasattr(route, "methods") for method in route.methods}
        for path, method in (
            ("/api/prestations", "GET"), ("/api/prestations", "POST"),
            ("/api/prestations/relations/options", "GET"),
            ("/api/prestations/{identifier}", "GET"),
            ("/api/prestations/{identifier}", "PUT"),
            ("/api/prestations/{identifier}/observations", "GET"),
        ):
            self.assertIn((path, method), paths)
            contextual = "/api/bases/{business_database_id}" + path.removeprefix("/api")
            self.assertIn((contextual, method), paths)
        editor = (FRONT / "js/prestation-editor.js").read_text()
        form = (FRONT / "js/prestation-form.js").read_text()
        for part in ("entityEditor.create(", "concurrency.headers(",
                     '"Operation-Id"', 'resource: "prestations"', "objectFamilies"):
            self.assertIn(part, editor)
        for part in ("relationSelector.fill(", 'referenceData.load("typesPrestation"',
                     "OBJECT_FAMILIES", "intervenant_ids", "type_prestation_id"):
            self.assertIn(part, form)
        self.assertNotIn("record_origin", form)
        self.assertNotIn("historique", form)
        self.assertIn("prestation-editor.js", (FRONT / "map.html").read_text())
        self.assertIn("prestation-editor.js", (FRONT / "service-worker.js").read_text())
