"""Contrat F4 du CRUD Ouvrage particulier générique."""

from pathlib import Path
import json
import shutil
import subprocess
import unittest
from uuid import uuid4

from pydantic import ValidationError

from digues_webapp.app import create_app
from digues_webapp.geometry_capabilities import geometry_capabilities
from digues_webapp.models import (
    ObservationCreate, OuvrageParticulierCreate, OuvrageParticulierUpdate,
)


ROOT = Path(__file__).resolve().parents[2]


class OuvrageParticulierModelTest(unittest.TestCase):
    def test_minimal_complete_and_historical_fields_forbidden(self):
        minimal = OuvrageParticulierCreate(geometry={"type": "Point", "coordinates": [2, 48]})
        self.assertEqual(minimal.geometry.type, "Point")
        full = OuvrageParticulierCreate(
            designation="  ", libelle=" Ouvrage ", valid=False,
            geometry={"type": "Point", "coordinates": [2, 48]},
        )
        self.assertIsNone(full.designation)
        self.assertEqual(full.libelle, "Ouvrage")
        self.assertFalse(full.valid)
        for field, value in (
            ("source_class", "OuvrageParticulier"),
            ("type_source_id", "RefOuvrageParticulier:9"),
            ("type_id", "PIE"),
            ("source_id", "legacy"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                OuvrageParticulierCreate.model_validate({field: value})

    def test_point_line_polygon_and_invalid_geometry(self):
        for geometry in (
            {"type": "Point", "coordinates": [2, 48]},
            {"type": "LineString", "coordinates": [[2, 48], [3, 49]]},
            {"type": "Polygon", "coordinates": [[[2, 48], [3, 48],
                                                    [3, 49], [2, 48]]]},
        ):
            with self.subTest(type=geometry["type"]):
                self.assertEqual(
                    OuvrageParticulierCreate(geometry=geometry).geometry.type,
                    geometry["type"],
                )
        for geometry in (
            {"type": "MultiPoint", "coordinates": [[2, 48]]},
            {"type": "LineString", "coordinates": [[2, 48]]},
            {"type": "Polygon", "coordinates": [[[2, 48], [3, 48], [3, 49]]]},
        ):
            with self.subTest(geometry=geometry), self.assertRaises(ValidationError):
                OuvrageParticulierCreate(geometry=geometry)

    def test_update_and_observation_parent(self):
        disorder = uuid4()
        self.assertEqual(
            OuvrageParticulierCreate(desordre_ids=[disorder], geometry={"type": "Point", "coordinates": [2, 48]}).desordre_ids,
            [disorder],
        )
        self.assertEqual(
            OuvrageParticulierUpdate(desordre_ids=[]).desordre_ids, [],
        )
        for payload in ({}, {"valid": None}, {"geometry": None},
                        {"desordre_ids": None},
                        {"desordre_ids": [str(disorder), str(disorder)]},
                        {"type_source_id": "RefOuvrageParticulier:10"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                OuvrageParticulierUpdate.model_validate(payload)
        parent = uuid4()
        observation = ObservationCreate.model_validate({
            "id": str(uuid4()),
            "parent": {"resource": "ouvrages_particuliers", "id": str(parent)},
        })
        self.assertEqual(observation.parent.id, parent)


class OuvrageParticulierFrontendTest(unittest.TestCase):
    def test_legacy_and_business_routes(self):
        routes = {(route.path, method) for route in create_app().routes
                  if hasattr(route, "methods") for method in route.methods}
        for path, method in (
            ("/api/ouvrages-particuliers", "GET"),
            ("/api/ouvrages-particuliers", "POST"),
            ("/api/ouvrages-particuliers/{identifier}", "GET"),
            ("/api/ouvrages-particuliers/{identifier}", "PUT"),
            ("/api/ouvrages-particuliers/{identifier}/observations", "GET"),
        ):
            self.assertIn((path, method), routes)
            contextual = "/api/bases/{business_database_id}" + path.removeprefix("/api")
            self.assertIn((contextual, method), routes)

    def test_geometry_serialization_and_f1_usage(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        script = r'''
const assert = require("node:assert/strict");
const editor = require("./webapp/frontend/js/ouvrage-particulier-editor.js");
const draft = require("./webapp/frontend/js/entity-geometry-draft.js");
const shell = require("./webapp/frontend/js/entity-editor-shell.js");
const positions = [{lng:2,lat:48},{lng:3,lat:48},{lng:3,lat:49}];
assert.deepEqual(editor.geometryFromVertices("Point", positions),
  {type:"Point",coordinates:[2,48]});
assert.deepEqual(editor.geometryFromVertices("LineString", positions),
  {type:"LineString",coordinates:[[2,48],[3,48],[3,49]]});
assert.deepEqual(editor.geometryFromVertices("Polygon", positions),
  {type:"Polygon",coordinates:[[[2,48],[3,48],[3,49],[2,48]]]});
const contract = draft.readContract(JSON.parse(process.argv[1]), "ouvrages-particuliers");
assert.deepEqual(contract.allowedTypes, ["Point", "LineString", "Polygon"]);
assert.equal(contract.required, true);
assert.equal(contract.remove, false);
assert.equal(contract.status, "compatibility");
assert.throws(() => shell.requireGeometry({mode:"create", allowedTypes:contract.allowedTypes}), /obligatoire/i);
assert.throws(() => shell.requireGeometry({mode:"edit", currentGeometry:{type:"MultiPoint"}, allowedTypes:contract.allowedTypes}), /doit être de type/i);
'''
        subprocess.run([node, "-e", script,
                        json.dumps(geometry_capabilities("ouvrages-particuliers"))], cwd=ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
