"""Projection, provenance, precedence, aspect ratio and degenerate extents."""

import sys
import unittest
from datetime import date
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qgis.core import (QgsApplication, QgsGeometry, QgsLayoutItemMap, QgsLayoutPoint, QgsLayoutSize,
                       QgsPrintLayout, QgsProject, QgsRectangle)
from gemapi_app.report_maps import (MAP_CRS, MAP_WIDTH_MM, MAP_HEIGHT_MM,
                                        MIN_REPORT_MAP_SCALE_DENOMINATOR,
                                        apply_minimum_report_scale, background,
                                        fit_extent, location, location_layers)
from gemapi_app.report_pdf import _map
from gemapi_app.report_synthetic_portrait import group_layers_and_extent


class ReportMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        cls.owns_app = cls.app is None
        if cls.owns_app:
            cls.app = QgsApplication([], True)
            cls.app.initQgis()

    @classmethod
    def tearDownClass(cls):
        if cls.owns_app:
            cls.app.exitQgis()

    def test_own_precedence_point_line_polygon_and_long_axis(self):
        for wkt in ("POINT(700000 6600000)",
                    "LINESTRING(700000 6600000,700001 6615000)",
                    "LINESTRING(700000 6600000,715000 6600001)",
                    "POLYGON((700000 6600000,700100 6600000,700100 6600100,700000 6600000))"):
            record = {"own_wkt": wkt, "reference_geometries": [{
                "famille": "ah_stockage", "objet_id": "one", "nom": "Autre",
                "direct": True, "wkt": "POINT(100000 100000)"}]}
            layers, extent, text = location_layers(record)
            self.assertEqual(len(layers), 1)
            self.assertEqual(text, "Géométrie propre")
            self.assertAlmostEqual(extent.width() / extent.height(),
                                   MAP_WIDTH_MM / MAP_HEIGHT_MM, places=5)
            self.assertEqual(len(location(record)), 1)

    def test_child_fallback_for_all_three_geometry_types(self):
        for wkt in ("POINT(1700000 9300000)",
                    "LINESTRING(1700000 9300000,1700100 9300000)",
                    "POLYGON((1700000 9300000,1700100 9300000,1700100 9300100,1700000 9300000))"):
            with self.subTest(wkt=wkt):
                record = {"own_wkt": None, "quantite": 7,
                          "unite": "arbres", "reference_geometries": [{
                              "famille": "vegetation", "objet_id": "zone", "nom": "Zone fictive",
                              "direct": True, "wkt": wkt}]}
                before = dict(record)
                self.assertEqual(location(record)[0][0], wkt)
                self.assertEqual(location(record)[0][1], "reference")
                layers, extent, provenance = location_layers(record)
                self.assertEqual(len(layers), 1)
                self.assertIsNotNone(extent)
                self.assertEqual(layers[0].wkbType(), QgsGeometry.fromWkt(wkt).wkbType())
                self.assertIn("Zone fictive", provenance)
                grouped_layers, grouped_extent = group_layers_and_extent([record])
                self.assertEqual(sum(layer.featureCount() for layer in grouped_layers),1)
                self.assertIsNotNone(grouped_extent)
                self.assertEqual(grouped_layers[0].name(),"Patrimoine de référence")
                self.assertEqual(record, before)
        # Own geometry takes precedence even if the child geometry is unavailable.
        self.assertEqual(location({"own_wkt":"POINT(0 0)","reference_geometries":[{
            "famille":"vegetation","objet_id":"zone","wkt":None}]})[0][:2], ("POINT(0 0)", "propre"))

    def test_offline_basemap_falls_back(self):
        with patch("gemapi_app.report_maps.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(background("epure"))
            self.assertIsNone(background("ortho"))

    def test_multiple_heritage_paths_and_empty(self):
        references = [
            {"famille": "ah_stockage", "objet_id": "one", "nom": "ZEC A", "direct": True,
             "wkt": "POINT(700000 6600000)"},
            {"famille": "ah_stockage", "objet_id": "one", "nom": "ZEC A", "direct": False,
             "wkt": "POINT(700000 6600000)"},
            {"famille": "cours_eau", "objet_id": "two", "nom": "Fossé B",
             "direct": True, "wkt": "LINESTRING(700100 6600000,700200 6600000)"},
        ]
        layers, extent, text = location_layers({"reference_geometries": references})
        self.assertEqual(len(layers), 2)
        self.assertIn("ZEC A", text)
        self.assertIn("Fossé B", text)
        self.assertTrue(extent.width() > 200)
        self.assertEqual(location_layers({})[1], None)

    def test_synthetic_group_uses_direct_heritage_when_own_geometry_is_absent(self):
        records = [
            {"id": "without", "own_wkt": None, "reference_geometries": [{
                "famille": "ah_stockage", "objet_id": "storage", "nom": "Stockage",
                "direct": True,
                "wkt": "POLYGON((700000 6600000,700100 6600000,700100 6600100,700000 6600000))"}]},
            {"id": "with", "own_wkt": "POINT(700200 6600100)", "reference_geometries": []},
        ]
        layers, extent = group_layers_and_extent(records)
        self.assertEqual(sum(layer.featureCount() for layer in layers), 2)
        self.assertTrue(any(layer.name() == "Patrimoine de référence" for layer in layers))
        self.assertTrue(extent.width() > 200)

    def test_pdf_map_scale_floor_preserves_center_geometry_and_frame(self):
        x, y = 1699932, 9226858
        cases = (
            ("point", f"POINT({x} {y})", True),
            ("short line", f"LINESTRING({x} {y},{x + 100} {y})", True),
            ("one hectare", f"POLYGON(({x} {y},{x + 100} {y},"
             f"{x + 100} {y + 100},{x} {y + 100},{x} {y}))", True),
            ("long line", f"LINESTRING({x} {y},{x + 3000} {y})", False),
            ("multipart", f"MULTIPOINT(({x} {y}),({x + 3000} {y + 300}))", False),
        )
        for name, wkt, must_expand in cases:
            with self.subTest(name=name):
                record = {"own_wkt": wkt}
                _, initial_extent, _ = location_layers(record)
                layout = QgsPrintLayout(QgsProject.instance())
                layout.initializeDefaults()
                initial = QgsLayoutItemMap(layout)
                layout.addLayoutItem(initial)
                initial.attemptMove(QgsLayoutPoint(10, 44))
                initial.attemptResize(QgsLayoutSize(MAP_WIDTH_MM, MAP_HEIGHT_MM))
                initial.setCrs(MAP_CRS)
                initial.zoomToExtent(initial_extent)
                initial_scale = initial.scale()
                items, retained = [], []
                _map(layout, record, 44, 0, 0, [], None, "epure", retained,
                     date(2026, 1, 1), items)
                self.assertEqual(len(items), 1)
                item = items[0]
                self.assertGreaterEqual(item.scale(), MIN_REPORT_MAP_SCALE_DENOMINATOR)
                self.assertAlmostEqual(item.sizeWithUnits().width(), MAP_WIDTH_MM)
                self.assertAlmostEqual(item.sizeWithUnits().height(), MAP_HEIGHT_MM)
                self.assertAlmostEqual(item.extent().center().x(), initial_extent.center().x(), places=5)
                self.assertAlmostEqual(item.extent().center().y(), initial_extent.center().y(), places=5)
                for layer in retained:
                    for feature in layer.getFeatures():
                        bounds = feature.geometry().boundingBox()
                        self.assertTrue(item.extent().contains(bounds), (name, bounds, item.extent()))
                if must_expand:
                    self.assertLess(initial_scale, MIN_REPORT_MAP_SCALE_DENOMINATOR)
                    self.assertAlmostEqual(item.scale(), MIN_REPORT_MAP_SCALE_DENOMINATOR, places=3)
                    self.assertGreater(item.extent().width(), initial_extent.width())
                else:
                    self.assertGreaterEqual(initial_scale, MIN_REPORT_MAP_SCALE_DENOMINATOR)
                    self.assertAlmostEqual(item.scale(), initial_scale, places=3)
                    self.assertAlmostEqual(item.extent().width(), initial_extent.width(), places=5)

    def test_portrait_map_frames_at_7500_and_extended_geometry(self):
        self.assertEqual(MIN_REPORT_MAP_SCALE_DENOMINATOR, 7500)
        for height, expected_height in ((73, 547.5), (92, 690)):
            for width, expanded in ((100, True), (3000, False)):
                with self.subTest(height=height, width=width):
                    layout = QgsPrintLayout(QgsProject.instance())
                    layout.initializeDefaults()
                    item = QgsLayoutItemMap(layout)
                    layout.addLayoutItem(item)
                    item.attemptMove(QgsLayoutPoint(10, 100))
                    item.attemptResize(QgsLayoutSize(190, height))
                    item.setCrs(MAP_CRS)
                    extent = fit_extent(
                        QgsRectangle(700000, 6600000, 700000 + width, 6600100),
                        190 / height)
                    item.zoomToExtent(extent)
                    initial_scale = item.scale()
                    center = item.extent().center()
                    apply_minimum_report_scale(item)
                    self.assertGreaterEqual(item.scale(), 7500)
                    self.assertAlmostEqual(item.extent().center().x(), center.x(), places=4)
                    self.assertAlmostEqual(item.extent().center().y(), center.y(), places=4)
                    self.assertTrue(item.extent().contains(extent))
                    self.assertAlmostEqual(item.sizeWithUnits().width(), 190)
                    self.assertAlmostEqual(item.sizeWithUnits().height(), height)
                    if expanded:
                        self.assertLess(initial_scale, 7500)
                        self.assertAlmostEqual(item.scale(), 7500, places=3)
                        self.assertAlmostEqual(item.extent().width(), 1425, places=3)
                        self.assertAlmostEqual(item.extent().height(), expected_height, places=3)
                    else:
                        self.assertGreaterEqual(initial_scale, 7500)
                        self.assertAlmostEqual(item.scale(), initial_scale, places=3)

    def test_pdf_map_without_geometry_stays_absent(self):
        layout = QgsPrintLayout(QgsProject.instance())
        layout.initializeDefaults()
        items = []
        _map(layout, {}, 44, 0, 0, [], None, "epure", [], date(2026, 1, 1), items)
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
