"""UX-GEO-1, level A: packaged decisions -> bounded HTTP contract.

No mutable database or private input. Synthetic decisions exercise approved
forms without changing the real pending decisions. PostgreSQL behavior is
covered separately by test_geometry_capabilities_postgres.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from digues_webapp import geometry_capabilities as capabilities
from webapp.tests.test_equipement_mesure_postgres import http_client


RESOURCE = "ouvrages-particuliers"


def synthetic_model(*, shapes=("POINT", "LINESTRING", "POLYGON"), required=True,
                    status="DECIDE", active=True):
    return {
        "model_digest": "synthetic-model",
        "registry": {"targets": [{
            "id": "da:concept:ouvrage-particulier", "state": "active" if active else "deferred",
            "status": "DECIDE", "geometry": {
                "status": status, "target_shapes": list(shapes),
                "required_for_valid": required, "policy": "preserve_if_present",
            },
        }]},
    }


class GeometryCapabilitiesTest(unittest.TestCase):
    def test_packaged_pending_contract_is_explicit_compatibility(self):
        contract = capabilities.geometry_capabilities(RESOURCE)
        self.assertEqual(contract["status"], "compatibility")
        self.assertEqual(contract["allowedTypes"], ["Point", "LineString", "Polygon"])
        self.assertTrue(contract["required"])
        self.assertTrue(contract["create"])
        self.assertFalse(contract["remove"])
        self.assertIn("GEOMETRY_CONTRACT_NOT_APPROVED",
                      [item["code"] for item in contract["diagnostics"]])
        self.assertLess(len(json.dumps(contract)), 3000)
        self.assertNotIn("sirs-v2", json.dumps(contract).lower())

    def test_unapproved_vegetation_does_not_activate_an_editor(self):
        contract = capabilities.geometry_capabilities("vegetation")
        self.assertEqual(contract["status"], "unavailable")
        self.assertEqual(contract["allowedTypes"], [])
        self.assertIsNone(contract["required"])
        self.assertFalse(contract["create"])
        self.assertFalse(contract["edit"])

    def test_approved_forms_are_extracted_and_versioned(self):
        all_shapes = capabilities.build_geometry_capabilities(synthetic_model(), RESOURCE)
        point = capabilities.build_geometry_capabilities(synthetic_model(shapes=("POINT",)), RESOURCE)
        self.assertEqual(all_shapes["status"], "approved")
        self.assertEqual(point["allowedTypes"], ["Point"])
        self.assertNotEqual(point["contractVersion"], all_shapes["contractVersion"])
        self.assertEqual(point, capabilities.build_geometry_capabilities(
            synthetic_model(shapes=("POINT",)), RESOURCE))

    def test_absent_or_inactive_target_never_gets_compatibility(self):
        absent = synthetic_model()
        absent["registry"]["targets"] = []
        for model in (absent, synthetic_model(active=False)):
            contract = capabilities.build_geometry_capabilities(model, RESOURCE)
            self.assertEqual(contract["status"], "unavailable")
            self.assertFalse(contract["create"])
            self.assertEqual(contract["allowedTypes"], [])

    def test_unknown_family_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            capabilities.build_geometry_capabilities(synthetic_model(), "unknown")
        self.assertEqual(error.exception.status_code, 404)

    def test_incomplete_approved_decision_does_not_fall_back(self):
        for model in (synthetic_model(required="A_APPROUVER"),
                      synthetic_model(shapes=("A_APPROUVER",))):
            contract = capabilities.build_geometry_capabilities(model, RESOURCE)
            self.assertEqual(contract["status"], "unavailable")
            self.assertFalse(contract["create"])

    def test_optional_geometry_needs_backend_support(self):
        optional = synthetic_model(required=False)
        rejected = capabilities.build_geometry_capabilities(optional, RESOURCE)
        self.assertEqual(rejected["status"], "divergent")
        self.assertFalse(rejected["create"])
        accepted = capabilities.build_geometry_capabilities(optional, RESOURCE, backend={
            "allowedTypes": ["Point", "LineString", "Polygon"],
            "required": False, "remove": True,
        })
        self.assertEqual(accepted["status"], "approved")
        self.assertFalse(accepted["required"])
        self.assertTrue(accepted["remove"])

    def test_unsupported_approved_form_is_diagnosed_not_silently_truncated(self):
        contract = capabilities.build_geometry_capabilities(
            synthetic_model(shapes=("POINT", "MULTIPOINT")), RESOURCE)
        self.assertEqual(contract["status"], "divergent")
        self.assertFalse(contract["edit"])
        self.assertEqual(contract["allowedTypes"], [])
        self.assertIn("BACKEND_GEOMETRY_CONTRACT_DIVERGENCE",
                      [item["code"] for item in contract["diagnostics"]])

    def test_required_and_shape_validation_preserves_invalid_existing(self):
        with patch.object(capabilities, "load_geometry_model", return_value=synthetic_model(shapes=("POINT",))):
            capabilities.validate_geometry_write(RESOURCE, geometry_type="POINT", valid=True,
                                                   is_create=True, geometry_changed=True)
            for shape in (None, "Polygon", "MULTIPOINT"):
                with self.subTest(shape=shape), self.assertRaises(HTTPException) as error:
                    capabilities.validate_geometry_write(RESOURCE, geometry_type=shape,
                        valid=True, is_create=True, geometry_changed=True)
                self.assertEqual(error.exception.status_code, 422)
            for shape in (None, "MULTIPOINT", "Polygon"):
                capabilities.validate_geometry_write(RESOURCE, geometry_type=shape,
                    valid=False, is_create=False, geometry_changed=False)
                with self.assertRaises(HTTPException):
                    capabilities.validate_geometry_write(RESOURCE, geometry_type=shape,
                        valid=True, is_create=False, geometry_changed=False)
            with self.assertRaises(HTTPException):
                capabilities.validate_geometry_write(RESOURCE, geometry_type="Polygon",
                    valid=False, is_create=False, geometry_changed=True)

    def test_contract_version_mismatch_has_current_version(self):
        current = capabilities.geometry_capabilities(RESOURCE)["contractVersion"]
        capabilities.require_geometry_contract_version(RESOURCE, None)
        capabilities.require_geometry_contract_version(RESOURCE, current)
        with self.assertRaises(HTTPException) as error:
            capabilities.require_geometry_contract_version(RESOURCE, "stale")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.headers["Geometry-Contract-Version"], current)
        self.assertEqual(error.exception.detail["code"], "geometry-contract-changed")
        self.assertEqual(error.exception.detail["contractVersion"], current)

    def test_package_load_does_not_read_private_files(self):
        original = Path.open

        def guarded(path, *args, **kwargs):
            self.assertNotIn("private", path.parts)
            return original(path, *args, **kwargs)

        capabilities._load_geometry_model_cached.cache_clear()
        with patch.object(Path, "open", guarded):
            model = capabilities.load_geometry_model()
        self.assertIn("model_digest", model)

    def test_stale_model_is_explicitly_unavailable(self):
        with patch.object(capabilities, "load_geometry_model", side_effect=ValueError("stale")):
            with self.assertRaises(HTTPException) as error:
                capabilities.geometry_capabilities(RESOURCE)
        self.assertEqual(error.exception.status_code, 503)

    def test_cold_load_is_serialized_for_concurrent_callers(self):
        barrier = Barrier(4)
        model = {"model_digest": "concurrent-test"}
        capabilities._load_geometry_model_cached.cache_clear()
        self.addCleanup(capabilities._load_geometry_model_cached.cache_clear)

        def load():
            barrier.wait(timeout=10)
            return capabilities.load_geometry_model()

        with patch.object(capabilities, "load_model", return_value=model) as reader, \
             patch.object(capabilities, "check_model_freshness") as checker, \
             ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: load(), range(4)))
        self.assertTrue(all(result is model for result in results))
        reader.assert_called_once()
        checker.assert_called_once_with(model, check_source_hashes=False)

    def test_stale_package_prevents_application_construction(self):
        with patch("digues_webapp.app.load_geometry_model", side_effect=ValueError("stale")), \
             patch("digues_webapp.app.FastAPI") as factory:
            with self.assertRaisesRegex(ValueError, "stale"):
                create_app()
        factory.assert_not_called()


class GeometryCapabilitiesRouteTest(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", LEGACY_SINGLE_DATABASE_ENABLED="true")
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = create_app()
        self.app.dependency_overrides[get_connection] = lambda: object()
        self.app.dependency_overrides[get_write_connection] = lambda: object()
        self.client = self.enterContext(http_client(self.app))

    def test_minimal_http_contract_and_unknown_family(self):
        response = self.client.get("/api/geometry-capabilities/ouvrages-particuliers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["resource"], RESOURCE)
        self.assertEqual(self.client.get("/api/geometry-capabilities/unknown").status_code, 404)

    def test_contextual_route_uses_existing_read_permission_adapter(self):
        from digues_webapp.frontend_routes import POLICY
        self.assertEqual(POLICY[("GET", "/geometry-capabilities/{resource}")], "business.read")
        self.assertIn("/api/bases/{business_database_id}/geometry-capabilities/{resource}",
                      [route.path for route in self.app.routes])
        # Both modes remain exclusive; this is not a second public config API.
        self.assertEqual(self.client.get(
            f"/api/bases/{uuid4()}/geometry-capabilities/{RESOURCE}").status_code, 404)

    def test_new_mutation_rejects_stale_contract_inside_callback(self):
        def mutation(connection, resource, action, callback, **kwargs):
            return callback()
        with patch("digues_webapp.app.execute_mutation", side_effect=mutation), \
             patch("digues_webapp.app.create_ouvrage_particulier") as writer:
            response = self.client.post("/api/ouvrages-particuliers", json={
                "geometry": {"type": "Point", "coordinates": [2, 48]},
            }, headers={"Geometry-Contract-Version": "stale"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "geometry-contract-changed")
        self.assertEqual(response.json()["detail"]["contractVersion"],
                         response.headers["Geometry-Contract-Version"])
        writer.assert_not_called()

    def test_existing_receipt_does_not_revalidate_contract(self):
        receipt = {"type": "Feature", "geometry": None, "properties": {"id": str(uuid4())}}
        with patch("digues_webapp.app.execute_mutation", return_value=receipt), \
             patch("digues_webapp.app.require_geometry_contract_version") as check:
            response = self.client.put(f"/api/ouvrages-particuliers/{uuid4()}",
                json={"commentaire": "replay"}, headers={"Geometry-Contract-Version": "stale"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), receipt)
        check.assert_not_called()
