import json
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from digues_webapp.app import app
from digues_webapp.geopackage import DataGeoPackageError, build_geopackage
from digues_webapp.models import DataGeoPackageExportRequest


class DataGeoPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from osgeo import ogr
        except ImportError as exc:
            raise unittest.SkipTest("GDAL/OGR indisponible") from exc
        cls.ogr = ogr

    @staticmethod
    def entity(entity_class, identifier, geometry, **attributes):
        return {
            "class": entity_class, "id": identifier,
            "geometry": geometry, "attributes": attributes,
        }

    @staticmethod
    def result(rows, columns=None, root="desordres", granularity="desordres"):
        return {
            "root": root,
            "granularity": granularity,
            "columns": columns or [{
                "key": "desordres.designation", "label": "Désignation",
                "source_class": "desordres", "source_field": "designation",
            }],
            "rows": rows,
        }

    def inspect(self, content, include_features=False):
        self.assertEqual(content[:16], b"SQLite format 3\x00")
        with tempfile.NamedTemporaryFile(suffix=".gpkg") as handle:
            handle.write(content)
            handle.flush()
            dataset = self.ogr.Open(handle.name)
            self.assertIsNotNone(dataset)
            result = {}
            for index in range(dataset.GetLayerCount()):
                layer = dataset.GetLayerByIndex(index)
                definition = layer.GetLayerDefn()
                fields = [definition.GetFieldDefn(i).GetName()
                          for i in range(definition.GetFieldCount())]
                item = {
                    "count": layer.GetFeatureCount(),
                    "type": layer.GetGeomType(),
                    "fields": fields,
                }
                if include_features:
                    item["features"] = [
                        {field: feature.GetField(field) for field in fields}
                        for feature in layer
                    ]
                result[layer.GetName()] = item
            return result

    def build(self, rows, columns=None, entity_classes=None, **metadata):
        return build_geopackage(
            self.result(rows, columns, **metadata),
            entity_classes or ["desordres"],
        )

    def test_exports_each_geometry_family_and_exact_view_rows(self):
        fixtures = {
            "Point": ([2.35, 48.86], "desordres_points", self.ogr.wkbMultiPoint),
            "LineString": ([[2.3, 48.8], [2.4, 48.9]], "desordres_lignes", self.ogr.wkbMultiLineString),
            "Polygon": ([[[2.3, 48.8], [2.4, 48.8], [2.4, 48.9], [2.3, 48.8]]], "desordres_polygones", self.ogr.wkbMultiPolygon),
        }
        for geometry_type, (coordinates, layer_name, layer_type) in fixtures.items():
            with self.subTest(geometry_type=geometry_type):
                entity = self.entity(
                    "desordres", geometry_type,
                    {"type": geometry_type, "coordinates": coordinates},
                    designation=geometry_type,
                )
                layers = self.inspect(self.build([
                    {"values": [geometry_type], "entities": [entity]},
                ]))
                self.assertEqual(set(layers), {layer_name, "vue_resultats"})
                self.assertEqual(layers[layer_name]["type"], layer_type)
                self.assertEqual(layers["vue_resultats"]["count"], 1)

    def test_entities_are_reduced_to_requested_classes_and_deduplicated(self):
        troncon = self.entity(
            "troncons", "t1",
            {"type": "LineString", "coordinates": [[2, 48], [3, 49]]},
            designation="T1",
        )
        point = self.entity(
            "desordres", "d1", {"type": "Point", "coordinates": [2, 48]},
            designation="D1",
        )
        columns = [{
            "key": "troncons.designation", "label": "Tronçon",
            "source_class": "troncons", "source_field": "designation",
        }]
        layers = self.inspect(self.build([
            {"values": ["T1"], "entities": [troncon, point]},
            {"values": ["T1"], "entities": [troncon, point]},
        ], columns, ["troncons"]))
        self.assertEqual(set(layers), {"troncons_lignes", "vue_resultats"})
        self.assertEqual(layers["troncons_lignes"]["count"], 1)
        self.assertEqual(layers["vue_resultats"]["count"], 2)

    def test_more_than_40_entities_and_24_attributes_are_legitimate(self):
        attributes = {f"field_{index}": index for index in range(30)}
        entities = [
            self.entity(
                "photos", f"p{index}",
                {"type": "Point", "coordinates": [2 + index / 1000, 48]},
                **attributes,
            )
            for index in range(41)
        ]
        columns = [{
            "key": "photos.field_0", "label": "Champ",
            "source_class": "photos", "source_field": "field_0",
        }]
        layers = self.inspect(self.build(
            [{"values": [0], "entities": entities}], columns, ["photos"],
            root="photos", granularity="photos",
        ))
        self.assertEqual(layers["photos_points"]["count"], 41)

    def test_photo_observation_disorder_associations_are_preserved_per_row(self):
        columns = [
            {"key": "desordres.designation", "label": "Désordre", "source_class": "desordres", "source_field": "designation"},
            {"key": "observations.designation", "label": "Observation", "source_class": "observations", "source_field": "designation"},
            {"key": "photos.libelle", "label": "Photo", "source_class": "photos", "source_field": "libelle"},
        ]
        rows = []
        for disorder, observation, photo in (("d1", "o1", "p1"), ("d2", "o2", "p2")):
            rows.append({
                "values": [disorder, observation, photo],
                "entities": [
                    self.entity("desordres", disorder, None, designation=disorder),
                    self.entity("observations", observation, None, designation=observation),
                    self.entity("photos", photo, {"type": "Point", "coordinates": [2, 48]}, libelle=photo),
                ],
            })
        layers = self.inspect(self.build(
            rows, columns, ["photos"], root="troncons", granularity="photos"
        ), include_features=True)
        exported = layers["vue_resultats"]["features"]
        self.assertEqual([json.loads(row["desordres_ids"]) for row in exported], [["d1"], ["d2"]])
        self.assertEqual([json.loads(row["observations_ids"]) for row in exported], [["o1"], ["o2"]])
        self.assertEqual([json.loads(row["photos_ids"]) for row in exported], [["p1"], ["p2"]])
        self.assertTrue(all(row["racine"] == "troncons" for row in exported))
        self.assertTrue(all(row["granularite"] == "photos" for row in exported))

    def test_null_or_invalid_only_result_is_rejected_cleanly(self):
        rows = [{"values": ["Sans géométrie"], "entities": [
            self.entity("desordres", "d1", None, designation="Sans géométrie"),
        ]}]
        with self.assertRaisesRegex(DataGeoPackageError, "aucune géométrie exploitable"):
            self.build(rows)

    def test_definition_contract_no_longer_accepts_browser_rows_or_columns(self):
        request = DataGeoPackageExportRequest(
            root="photos",
            fields=[{"table": "photos", "field": "date"}],
            entity_classes=["photos"],
        )
        for legacy_field in ("rows", "columns"):
            with self.subTest(legacy_field=legacy_field):
                payload = request.model_dump(mode="json")
                payload[legacy_field] = []
                with self.assertRaises(ValidationError):
                    DataGeoPackageExportRequest(**payload)

    def test_http_endpoint_replays_complete_view_and_returns_named_download(self):
        request = DataGeoPackageExportRequest(
            root="desordres",
            fields=[{"table": "desordres", "field": "designation"}],
            entity_classes=["desordres"],
        )
        endpoint = next(route.endpoint for route in app.routes
                        if route.path == "/api/data/export-gpkg")
        complete_result = self.result([])
        connection = object()
        with (
            patch("digues_webapp.app.execute_view_export", return_value=complete_result) as execute,
            patch("digues_webapp.app.build_geopackage", return_value=b"gpkg") as build,
        ):
            response = endpoint(request, connection)
        execute.assert_called_once_with(
            connection, "desordres", request.fields, request.hidden_troncon_ids
        )
        build.assert_called_once_with(complete_result, ["desordres"])
        self.assertEqual(response.body, b"gpkg")
        self.assertEqual(response.media_type, "application/geopackage+sqlite3")
        self.assertEqual(response.headers["content-disposition"],
                         'attachment; filename="digues-donnees.gpkg"')


if __name__ == "__main__":
    unittest.main()
