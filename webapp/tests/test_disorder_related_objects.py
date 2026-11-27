"""Contrats unitaires de la consultation inverse Objet–Désordre."""

import unittest
from pathlib import Path
from uuid import UUID, uuid4

from digues_webapp.app import create_app
from digues_webapp.database import get_connection
from digues_webapp.models import DesordreAssociatedObjectsResponse
from digues_webapp.object_disorder_relations import (
    inverse_object_disorder_relations,
    registered_object_disorder_relations,
)
from digues_webapp.queries import (
    DesordreAssociatedObjectsCursorError,
    _decode_desordre_associated_objects_cursor,
    _encode_desordre_associated_objects_cursor,
    fetch_desordre_associated_objects,
)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.current = []
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters):
        self.executions.append((statement, parameters))
        self.current = [(1,)] if len(self.executions) == 1 else self.rows

    def fetchone(self):
        return self.current[0] if self.current else None

    def fetchall(self):
        return list(self.current)


class _Connection:
    def __init__(self, rows):
        self.database_cursor = _Cursor(rows)

    def cursor(self):
        return self.database_cursor


class DisorderRelatedObjectsTest(unittest.TestCase):
    def test_inverse_registry_is_closed_and_adds_only_troncon_to_write_families(self):
        inverse = inverse_object_disorder_relations()
        self.assertEqual([config.resource for config in inverse], [
            "troncons", "mobilier", "equipements-mesure", "reseaux-techniques",
            "ouvrages-particuliers", "cheminements", "vegetation",
            "amenagements-hydrauliques",
        ])
        self.assertNotIn("troncons", registered_object_disorder_relations())
        self.assertEqual(len({config.association_table for config in inverse}), 8)
        self.assertTrue(all(config.category_label for config in inverse))
        self.assertTrue(all(config.label_columns for config in inverse))

    def test_cursor_round_trip_and_rejects_malformed_values(self):
        identifier = uuid4()
        cursor = _encode_desordre_associated_objects_cursor(3, "libellé", identifier)
        self.assertEqual(
            _decode_desordre_associated_objects_cursor(cursor),
            (3, "libellé", identifier),
        )
        for invalid in (
            "", "***", _encode_desordre_associated_objects_cursor(8, "x", identifier),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(DesordreAssociatedObjectsCursorError):
                    _decode_desordre_associated_objects_cursor(invalid)

    def test_query_is_bounded_paginated_and_contains_no_geometry(self):
        first = UUID("00000000-0000-4000-8000-000000000101")
        second = UUID("00000000-0000-4000-8000-000000000102")
        rows = [
            (0, "troncons", "Tronçons", first, "Même libellé", True, "même libellé"),
            (0, "troncons", "Tronçons", second, "Même libellé", False, "même libellé"),
        ]
        connection = _Connection(rows)
        result = fetch_desordre_associated_objects(
            connection, uuid4(), limit=1,
        )
        self.assertEqual(result["items"], [{
            "resource": "troncons", "category": "Tronçons", "id": first,
            "label": "Même libellé", "valid": True,
        }])
        self.assertTrue(result["has_more"])
        self.assertIsNotNone(result["next_cursor"])
        self.assertNotIn("geometry", result["items"][0])
        query_parameters = connection.database_cursor.executions[1][1]
        self.assertEqual(len(query_parameters), 9)
        self.assertEqual(query_parameters[-1], 2)

    def test_response_contract_and_route_are_explicit(self):
        payload = DesordreAssociatedObjectsResponse.model_validate({
            "items": [{
                "resource": "mobilier",
                "category": "Mobilier",
                "id": uuid4(),
                "label": "Repère",
                "valid": False,
            }],
            "has_more": False,
            "next_cursor": None,
        })
        self.assertFalse(payload.items[0].valid)
        paths = {route.path for route in create_app().routes}
        self.assertIn("/api/desordres/{desordre_id}/objets-associes", paths)
        route = next(
            route for route in create_app().routes
            if route.path == "/api/desordres/{desordre_id}/objets-associes"
        )
        self.assertIn(get_connection, {
            dependency.call for dependency in route.dependant.dependencies
        })

    def test_shared_asset_is_loaded_and_precached_before_its_consumers(self):
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        asset = "/static/js/disorder-related-objects.js"
        map_page = (frontend / "map.html").read_text(encoding="utf-8")
        data_page = (frontend / "data.html").read_text(encoding="utf-8")
        worker = (frontend / "service-worker.js").read_text(encoding="utf-8")
        css = (frontend / "css" / "app.css").read_text(encoding="utf-8")
        self.assertLess(map_page.index(asset), map_page.index("/static/js/map.js"))
        self.assertLess(
            data_page.index(asset), data_page.index("/static/js/data-entity-detail.js")
        )
        self.assertIn(f'"{asset}"', worker)
        self.assertLess(
            css.index(".disorder-related-objects"),
            css.index("Responsive et container queries"),
        )


if __name__ == "__main__":
    unittest.main()
