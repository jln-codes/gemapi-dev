"""Contrats F3 Équipement de mesure sans dépendance PostgreSQL."""

from pathlib import Path
import shutil
import subprocess
import unittest

from pydantic import ValidationError

from digues_webapp.app import create_app
from digues_webapp.models import EquipementMesureCreate, EquipementMesureUpdate


PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]


class EquipementMesureModelTest(unittest.TestCase):
    def test_minimal_complete_empty_false_and_null(self):
        minimal = EquipementMesureCreate(type_id=" PIE ", geometry={"type": "Point", "coordinates": [2, 48]})
        self.assertEqual(minimal.type_id, "PIE")
        self.assertEqual(minimal.geometry.type, "Point")
        complete = EquipementMesureCreate(
            type_id="ECH", designation="  ", commentaire=" test ",
            valid=False,
            geometry={"type": "Point", "coordinates": [2.1, 48.2]},
        )
        self.assertIsNone(complete.designation)
        self.assertEqual(complete.commentaire, "test")
        self.assertFalse(complete.valid)

    def test_invalid_fields_and_non_point_are_rejected(self):
        for payload in (
            {"type_id": " "},
            {"type_id": "PIE", "source_class": "OuvrageParticulier"},
            {"type_id": "PIE", "geometry": {
                "type": "LineString", "coordinates": [[2, 48], [3, 49]],
            }},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                EquipementMesureCreate.model_validate(payload)

    def test_update_contract(self):
        with self.assertRaises(ValidationError):
            EquipementMesureUpdate()
        for payload in ({"type_id": None}, {"valid": None}, {"geometry": None}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                EquipementMesureUpdate.model_validate(payload)


class EquipementMesureFrontendContractTest(unittest.TestCase):
    def test_legacy_and_business_routes(self):
        routes = {(route.path, method) for route in create_app().routes
                  if hasattr(route, "methods") for method in route.methods}
        for path, method in (
            ("/api/equipements-mesure", "GET"),
            ("/api/equipements-mesure", "POST"),
            ("/api/equipements-mesure/{identifier}", "GET"),
            ("/api/equipements-mesure/{identifier}", "PUT"),
            ("/api/equipements-mesure/{identifier}/observations", "GET"),
        ):
            self.assertIn((path, method), routes)
            contextual = "/api/bases/{business_database_id}" + path.removeprefix("/api")
            self.assertIn((contextual, method), routes)

    def test_reuses_point_editor_shell_relations_and_stays_online_only(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const editor = fs.readFileSync("webapp/frontend/js/mobilier-editor.js", "utf8");
const form = fs.readFileSync("webapp/frontend/js/mobilier-form.js", "utf8");
const map = fs.readFileSync("webapp/frontend/js/map.js", "utf8");
assert.match(editor, /entityEditor\.create/);
assert.match(editor, /entityEditor\.requireGeometry/);
assert.match(editor, /allowedTypes: \["Point"\]/);
assert.match(form, /relationSelector\.fill/);
assert.match(editor, /session\.begin\("create"/);
assert.match(editor, /shell\.beginEdit/);
assert.match(editor, /session\.save/);
assert.match(editor, /shell\.hasDraft/);
assert.match(map, /MobilierForm\.profile\("equipements_mesure"\)/);
assert.match(form, /resource: "equipements-mesure"/);
assert.match(form, /observationResource: "equipements_mesure"/);
assert.match(form, /referenceName: "typesEquipementMesure"/);
assert.doesNotMatch(editor, /OfflineOperationsRuntime|enqueueCreate|IndexedDB/);
'''
        subprocess.run(
            [node, "-e", program], cwd=PROJECT_DIRECTORY, check=True,
        )


if __name__ == "__main__":
    unittest.main()
