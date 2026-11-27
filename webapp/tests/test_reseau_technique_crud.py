"""Contrats F3 Réseau technique sans dépendance PostgreSQL."""

from pathlib import Path
import shutil
import subprocess
import unittest

from pydantic import ValidationError

from digues_webapp.app import create_app
from digues_webapp.models import ReseauTechniqueCreate, ReseauTechniqueUpdate


PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]


class ReseauTechniqueModelTest(unittest.TestCase):
    def test_minimal_complete_empty_false_and_null(self):
        minimal = ReseauTechniqueCreate(type_id=" ELE ")
        self.assertEqual(minimal.type_id, "ELE")
        self.assertIsNone(minimal.geometry)
        complete = ReseauTechniqueCreate(
            type_id="EAU", designation="  ", commentaire=" test ", valid=False,
            geometry={"type": "LineString", "coordinates": [[2, 48], [2.1, 48.1]]},
        )
        self.assertIsNone(complete.designation)
        self.assertEqual(complete.commentaire, "test")
        self.assertFalse(complete.valid)

    def test_point_and_linestring_are_the_only_supported_geometries(self):
        point = ReseauTechniqueCreate(
            type_id="ELE", geometry={"type": "Point", "coordinates": [2, 48]},
        )
        self.assertEqual(point.geometry.type, "Point")
        line = ReseauTechniqueCreate(
            type_id="ELE",
            geometry={"type": "LineString", "coordinates": [[2, 48], [3, 49]]},
        )
        self.assertEqual(line.geometry.type, "LineString")
        for geometry in (
            {"type": "Polygon", "coordinates": [[[2, 48], [3, 48], [3, 49], [2, 48]]]},
            {"type": "LineString", "coordinates": [[2, 48]]},
            {"type": "Point", "coordinates": [200, 48]},
        ):
            with self.subTest(geometry=geometry), self.assertRaises(ValidationError):
                ReseauTechniqueCreate(type_id="ELE", geometry=geometry)

    def test_invalid_fields_and_update_contract(self):
        for payload in (
            {"type_id": " "},
            {"type_id": "ELE", "source_class": "ReseauTechnique"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ReseauTechniqueCreate.model_validate(payload)
        with self.assertRaises(ValidationError):
            ReseauTechniqueUpdate()
        for payload in ({"type_id": None}, {"valid": None}, {"geometry": None}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ReseauTechniqueUpdate.model_validate(payload)


class ReseauTechniqueFrontendContractTest(unittest.TestCase):
    def test_legacy_and_business_routes(self):
        routes = {(route.path, method) for route in create_app().routes
                  if hasattr(route, "methods") for method in route.methods}
        for path, method in (
            ("/api/reseaux-techniques", "GET"),
            ("/api/reseaux-techniques", "POST"),
            ("/api/reseaux-techniques/{identifier}", "GET"),
            ("/api/reseaux-techniques/{identifier}", "PUT"),
            ("/api/reseaux-techniques/{identifier}/observations", "GET"),
        ):
            self.assertIn((path, method), routes)
            contextual = "/api/bases/{business_database_id}" + path.removeprefix("/api")
            self.assertIn((contextual, method), routes)

    def test_uses_common_shell_relation_and_domain_geometry_policy(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const editor = fs.readFileSync("webapp/frontend/js/reseau-technique-editor.js", "utf8");
const shared = fs.readFileSync("webapp/frontend/js/mobilier-form.js", "utf8");
const map = fs.readFileSync("webapp/frontend/js/map.js", "utf8");
const html = fs.readFileSync("webapp/frontend/map.html", "utf8");
assert.match(editor, /entityEditor\.create/);
assert.match(editor, /businessForm\.fill/);
assert.match(shared, /relationSelector\.fill/);
assert.match(editor, /shell\.beginCreate/);
assert.match(editor, /shell\.beginEdit/);
assert.match(editor, /shell\.beginSave/);
assert.match(editor, /shell\.hasDraft/);
assert.match(editor, /"Point"/);
assert.match(editor, /"LineString"/);
assert.match(editor, /resource: "reseaux_techniques"/);
assert.doesNotMatch(editor, /Polygon|OfflineOperationsRuntime|enqueueCreate|IndexedDB/);
assert.match(map, /ReseauTechniqueEditor\.create/);
assert.match(html, /reseau-technique-editor\.js/);
'''
        subprocess.run([node, "-e", program], cwd=PROJECT_DIRECTORY, check=True)

    def test_geometry_serialization_is_policy_free_and_stable(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const editor = require("./webapp/frontend/js/reseau-technique-editor.js");
const positions = [{lng: 2, lat: 48}, {lng: 2.1, lat: 48.1}];
assert.deepEqual(editor.geometryFromLatLngs("Point", positions), {
  type: "Point", coordinates: [2, 48],
});
assert.deepEqual(editor.geometryFromLatLngs("LineString", positions), {
  type: "LineString", coordinates: [[2, 48], [2.1, 48.1]],
});
'''
        subprocess.run([node, "-e", program], cwd=PROJECT_DIRECTORY, check=True)


if __name__ == "__main__":
    unittest.main()
