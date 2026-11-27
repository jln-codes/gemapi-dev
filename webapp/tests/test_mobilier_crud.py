"""Contrats F2 Mobilier sans dépendance PostgreSQL."""

from pathlib import Path
import shutil
import subprocess
import unittest
from uuid import uuid4

from pydantic import ValidationError

from digues_webapp.app import create_app
from digues_webapp.models import MobilierCreate, MobilierUpdate


PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]


class MobilierModelTest(unittest.TestCase):
    def test_minimal_full_empty_false_and_null_contract(self):
        type_id = " MRE "
        troncon_id = uuid4()
        minimal = MobilierCreate(type_id=type_id, geometry={"type": "Point", "coordinates": [2, 48]})
        self.assertEqual(minimal.type_id, "MRE")
        self.assertEqual(minimal.geometry.type, "Point")
        self.assertTrue(minimal.valid)

        complete = MobilierCreate(
            type_id=type_id,
            troncon_id=troncon_id,
            designation="  ",
            commentaire=" commentaire ",
            valid=False,
            geometry={"type": "Point", "coordinates": [2.1, 48.2]},
        )
        self.assertIsNone(complete.designation)
        self.assertEqual(complete.commentaire, "commentaire")
        self.assertFalse(complete.valid)
        self.assertEqual(complete.geometry.type, "Point")

    def test_blank_type_extra_and_non_point_are_rejected(self):
        for payload in (
            {"type_id": "   "},
            {"type_id": str(uuid4()), "source_class": "Mobilier"},
            {
                "type_id": str(uuid4()),
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[2.1, 48.2], [2.2, 48.3]],
                },
            },
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                MobilierCreate.model_validate(payload)

    def test_update_requires_a_field_and_never_interprets_null_as_point_removal(self):
        with self.assertRaises(ValidationError):
            MobilierUpdate()
        for payload in ({"type_id": None}, {"valid": None}, {"geometry": None}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                MobilierUpdate.model_validate(payload)
        self.assertFalse(MobilierUpdate(valid=False).valid)


class MobilierFrontendContractTest(unittest.TestCase):
    def test_common_composition_and_session_contracts(self):
        """Level A: discover the shared JS contracts through the normal Python group."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        subprocess.run(
            [node, "--test", "webapp/tests/test_entity_form_view.js",
             "webapp/tests/test_entity_form_session.js"],
            cwd=PROJECT_DIRECTORY, check=True,
        )

    def test_legacy_and_business_routes(self):
        routes = {(route.path, method) for route in create_app().routes
                  if hasattr(route, "methods") for method in route.methods}
        for path, method in (
            ("/api/mobilier", "GET"),
            ("/api/mobilier", "POST"),
            ("/api/mobilier/{mobilier_id}", "GET"),
            ("/api/mobilier/{mobilier_id}", "PUT"),
            ("/api/mobilier/{mobilier_id}/observations", "GET"),
        ):
            self.assertIn((path, method), routes)
            contextual = "/api/bases/{business_database_id}" + path.removeprefix("/api")
            self.assertIn((contextual, method), routes)

    def test_point_serialization_and_f1_consumption(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        subprocess.run(
            [node, "-e", r'''
const assert = require("node:assert/strict");
const Mobilier = require("./webapp/frontend/js/mobilier-editor.js");
assert.deepEqual(Mobilier.geometryFromLatLng({lat: 48.2, lng: 2.1}), {
  type: "Point", coordinates: [2.1, 48.2],
});
const source = require("node:fs").readFileSync(
  "./webapp/frontend/js/mobilier-editor.js", "utf8",
);
const shared = require("node:fs").readFileSync(
  "./webapp/frontend/js/mobilier-form.js", "utf8",
);
assert.match(source, /entityEditor\.create/);
assert.match(source, /MobilierForm\.fill/);
assert.match(source, /MobilierForm\.payload/);
assert.match(source, /entityEditor\.requireGeometry/);
assert.match(source, /allowedTypes: \["Point"\]/);
assert.match(shared, /relationSelector\.fill/);
assert.match(shared, /typesMobilier/);
assert.match(source, /If-Match|concurrency\.headers/);
assert.match(source, /session\.begin\("create"/);
assert.match(source, /shell\.beginEdit/);
assert.match(source, /session\.save/);
assert.match(source, /formSessionFactory\.create/);
assert.doesNotMatch(source, /innerHTML|createElement\("(?:header|nav|form)"\)/);
assert.match(source, /shell\.hasDraft/);
assert.match(source, /ObservationParentList\.load/);
assert.match(source, /formViewFactory\.create/);
assert.match(source, /MobilierForm\.composition\(settings\)/);
assert.doesNotMatch(source, /createElement\("dialog"\)/);
assert.doesNotMatch(source, /OfflineOperationsRuntime|enqueueCreate|IndexedDB/);
'''],
            cwd=PROJECT_DIRECTORY,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
