import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from digues_webapp.app import app, create_app
from digues_webapp.basemap_catalog import (
    BasemapCatalogError,
    load_basemap_catalog,
    parse_basemap_catalog,
)


def route_endpoint(path: str):
    return next(route.endpoint for route in app.routes if route.path == path)


def definition(identifier: str, order: int, **changes):
    data = {
        "id": identifier,
        "display_name": identifier,
        "order": order,
        "enabled": True,
        "default": False,
        "adapter": "plan_ign_raster",
        "public_tilejson_url": f"/basemap/{identifier}.json",
        "native_min_zoom": 0,
        "native_max_zoom": 18,
        "display_max_zoom": 19,
        "attribution": "© test",
        "tile_format": "webp",
        "capabilities": {
            "browser_print": True,
            "server_pdf": False,
        },
        "offline": {
            "required": True,
            "status": "planned",
            "retention": "review-required",
            "min_zoom": 0,
            "max_zoom": 18,
        },
        "cache": {
            "id": identifier,
            "policy": "persistent-reconstructible",
            "http_max_age_seconds": 3600,
            "directory": identifier,
            "resources": False,
            "resource_max_age_seconds": 0,
            "eviction": "lru",
            "retain_on_error": True,
        },
    }
    data.update(changes)
    return data


class BasemapCatalogTest(unittest.TestCase):
    def test_catalogue_is_public_and_independent_of_browser_selection(self):
        with TestClient(create_app()) as client:
            response = client.get('/api/basemaps?base=autre')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(response.json(), client.get('/api/basemaps').json())
            self.assertEqual([row['id'] for row in response.json()['basemaps']],
                             ['ign-epure', 'ign-plan', 'ign-ortho'])
            self.assertEqual(client.get('/api/desordres').status_code, 403)
            self.assertEqual(client.get('/api/bases/autre/desordres').status_code, 404)

    def test_default_catalog_declares_three_independent_basemaps(self):
        catalog = load_basemap_catalog()

        self.assertEqual(
            [item.id for item in catalog.basemaps],
            ["ign-epure", "ign-plan", "ign-ortho"],
        )
        self.assertEqual(
            [item.cache.id for item in catalog.basemaps],
            ["ign-epure", "ign-plan", "ign-ortho"],
        )
        self.assertEqual(catalog.resolved_default().id, "ign-epure")
        self.assertEqual([item.cache.directory for item in catalog.basemaps],
                         ["ign/epure", "ign/plan", "ign/ortho"])
        epure = catalog.basemaps[0]
        self.assertEqual((epure.native_max_zoom, epure.display_max_zoom), (18, 19))
        self.assertEqual(catalog.basemaps[1].adapter, "wmts_raster")
        self.assertEqual(catalog.basemaps[2].provider_layer, "HR.ORTHOIMAGERY.ORTHOPHOTOS")
        self.assertEqual((catalog.basemaps[2].native_min_zoom,
                          catalog.basemaps[2].tile_matrix_set), (6, "PM_6_19"))
        self.assertTrue(all(item.offline.required for item in catalog.basemaps))
        self.assertTrue(all(item.offline.status == "planned" for item in catalog.basemaps))

    def test_public_contract_only_announces_operational_adapter(self):
        payload = load_basemap_catalog().public_payload()

        self.assertEqual(payload["contract_version"], 1)
        self.assertEqual(payload["default_basemap_id"], "ign-epure")
        self.assertEqual([item["id"] for item in payload["basemaps"]],
                         ["ign-epure", "ign-plan", "ign-ortho"])
        self.assertEqual([item["delivery"]["url"] for item in payload["basemaps"]],
                         ["/basemap/plan-ign.json", "/basemap/ign-plan.json", "/basemap/ign-ortho.json"])
        self.assertTrue(all(item["offline"]["status"] == "planned" and
                            item["offline"]["retention"] == "review-required"
                            for item in payload["basemaps"]))
        published = payload["basemaps"][0]
        self.assertEqual(published["delivery"], {
            "adapter": "tilejson",
            "url": "/basemap/plan-ign.json",
        })
        self.assertEqual(published["zoom"], {
            "native_min": 0,
            "native_max": 18,
            "display_max": 19,
        })
        self.assertEqual(payload["offline"], {
            "contract_version": 1,
            "required_before_cartography_completion": True,
            "payload_store": "cache-storage",
            "metadata_store": "indexeddb",
            "package_unit": "basemap-territory-generation",
            "local_delivery": "service-worker-xyz",
        })
        self.assertEqual(published["offline"], {
            "required": True,
            "status": "planned",
            "retention": "review-required",
            "min_zoom": 0,
            "max_zoom": 18,
        })
        serialized = json.dumps(payload)
        self.assertNotIn("provider_endpoint", serialized)
        self.assertNotIn("provider_layer", serialized)
        self.assertNotIn("cache", published)
        self.assertNotIn("http_max_age_seconds", serialized)
        self.assertNotIn("data.geopf.fr", serialized)

    def test_default_falls_back_to_first_enabled_entry(self):
        catalog = parse_basemap_catalog({
            "contract_version": 1,
            "min_free_bytes": 1024,
            "basemaps": [
                definition("disabled-default", 10, enabled=False, default=True),
                definition("second", 30),
                definition("first", 20),
            ],
        })

        self.assertEqual(catalog.resolved_default().id, "first")
        self.assertEqual(catalog.public_payload()["default_basemap_id"], "first")

    def test_invalid_contracts_are_rejected(self):
        invalid = [
            [definition("same", 10), definition("same", 20)],
            [definition("first", 10), definition("second", 10)],
            [definition("first", 10), definition("second", 20, cache=definition("first", 1)["cache"])],
            [definition("first", 10, native_max_zoom=18, display_max_zoom=17)],
            [definition("first", 10, native_max_zoom=18, display_max_zoom=20)],
            [definition("first", 10, public_tilejson_url="javascript:alert(1)")],
            [definition("first", 10, default=True), definition("second", 20, default=True)],
            [definition("first", 10, cache={**definition("first", 10)["cache"],
                                             "tile_max_age_seconds": -1})],
            [definition("first", 10, cache={**definition("first", 10)["cache"],
                                             "resources": "invalid"})],
            [definition("first", 10, cache={**definition("first", 10)["cache"],
                                             "directory": "../outside"})],
            [definition("first", 10), definition("second", 20, cache={
                **definition("second", 20)["cache"], "directory": "first/child"})],
            [definition("first", 10, offline={
                "required": True,
                "status": "available",
                "retention": "review-required",
                "min_zoom": 0,
                "max_zoom": 18,
            })],
            [definition("first", 10, offline={
                "required": True,
                "status": "planned",
                "retention": "authorized",
                "min_zoom": 0,
                "max_zoom": 19,
            })],
            [definition("first", 10, offline={
                "required": False,
                "status": "planned",
                "retention": "authorized",
                "min_zoom": 0,
                "max_zoom": 18,
            })],
        ]
        for basemaps in invalid:
            with self.subTest(basemaps=basemaps), self.assertRaises(BasemapCatalogError):
                parse_basemap_catalog({"contract_version": 1, "min_free_bytes": 1024,
                                       "basemaps": basemaps})

    def test_enabled_wmts_requires_a_public_tilejson_delivery(self):
        item = definition("wmts", 10)
        item.update({
            "adapter": "wmts_raster",
            "tile_format": "png",
            "public_tilejson_url": None,
            "provider_endpoint": "https://provider.example.test/wmts",
            "provider_layer": "LAYER",
            "provider_style": "normal",
            "tile_matrix_set": "PM",
            "wmts_matrix_limits": [[z, 0, 2**z - 1, 0, 2**z - 1]
                                   for z in range(19)],
            "wmts_publication": "review-required",
            "cache_generation": "test",
            "cache": {**item["cache"], "tile_max_age_seconds": 3600},
        })
        with self.assertRaises(BasemapCatalogError):
            parse_basemap_catalog({"contract_version": 1, "min_free_bytes": 1024,
                                   "basemaps": [item]})

        item["public_tilejson_url"] = "/basemap/wmts.json"
        with self.assertRaises(BasemapCatalogError):
            parse_basemap_catalog({"contract_version": 1, "min_free_bytes": 1024,
                                   "basemaps": [item]})

        item["enabled"] = False
        catalog = parse_basemap_catalog({"contract_version": 1, "min_free_bytes": 1024,
                                         "basemaps": [item]})
        self.assertEqual(catalog.available(), ())
        self.assertIsNone(catalog.public_payload()["default_basemap_id"])

        item["enabled"] = True
        item["wmts_publication"] = "authorized"
        catalog = parse_basemap_catalog({"contract_version": 1, "min_free_bytes": 1024,
                                         "basemaps": [item]})
        self.assertEqual(catalog.public_payload()["basemaps"][0]["id"], "wmts")
        self.assertNotIn("provider_layer", catalog.public_payload()["basemaps"][0])

        for changed in (
            {"wmts_matrix_limits": item["wmts_matrix_limits"][:-1]},
            {"public_tilejson_url": "/basemap/other.json"},
            {"cache": {**item["cache"], "tile_max_age_seconds": 900}},
            {"provider_endpoint": "https://provider.example.test/wmts?LAYER=other"},
        ):
            with self.subTest(changed=changed), self.assertRaises(BasemapCatalogError):
                parse_basemap_catalog({"contract_version": 1, "min_free_bytes": 1024,
                                       "basemaps": [{**item, **changed}]})

    def test_discovery_endpoint_is_public_and_not_cached(self):
        response = route_endpoint("/api/basemaps")()
        payload = json.loads(response.body)

        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(payload["default_basemap_id"], "ign-epure")
        self.assertEqual(payload["basemaps"][0]["delivery"]["adapter"], "tilejson")

    def test_catalog_file_can_be_selected_by_server_configuration(self):
        with patch.dict("os.environ", {"BASEMAP_CATALOG_FILE": "/does/not/exist"}):
            with self.assertRaises(BasemapCatalogError):
                load_basemap_catalog()


if __name__ == "__main__":
    unittest.main()
