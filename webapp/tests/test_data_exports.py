import csv
import io
import unittest
from unittest.mock import patch

from digues_webapp.app import app
from digues_webapp.data_views import DataViewExportLimitError
from digues_webapp.models import DataCsvExportRequest, DataGeoPackageExportRequest


class DataCsvExportTest(unittest.TestCase):
    def test_csv_neutralizes_formula_text_for_views_and_home_only_at_export(self):
        dangerous_and_regular_values = [
            "=1+1",
            "+1+1",
            "-1+1",
            "@SUM(A1:A2)",
            "   =1+1",
            "texte normal",
            "",
            None,
            "Été à Orléans",
            'lieu; "dit"\nligne suivante',
            42,
            -7,
        ]
        expected = [
            "'=1+1",
            "'+1+1",
            "'-1+1",
            "'@SUM(A1:A2)",
            "'   =1+1",
            "texte normal",
            "",
            "",
            "Été à Orléans",
            'lieu; "dit"\nligne suivante',
            "42",
            "-7",
        ]
        endpoint = next(
            route.endpoint for route in app.routes
            if route.path == "/api/data/export-csv"
        )
        for root in ("desordres", "troncons"):
            with self.subTest(root=root):
                request = DataCsvExportRequest(
                    root=root,
                    fields=[{"table": root, "field": (
                        "designation" if root == "desordres" else "libelle"
                    )}],
                )
                result = {
                    "root": root,
                    "granularity": root,
                    "columns": [{"label": "Valeur métier"}],
                    "rows": [{
                        "values": dangerous_and_regular_values,
                        "entities": [],
                    }],
                }
                with patch(
                    "digues_webapp.app.execute_view_export", return_value=result
                ):
                    response = endpoint(request, object())

                content = response.body.decode("utf-8")
                parsed = list(csv.reader(
                    io.StringIO(content.removeprefix("\ufeff")), delimiter=";"
                ))
                self.assertEqual(parsed[0], ["Valeur métier"])
                self.assertEqual(parsed[1], expected)
                self.assertEqual(
                    result["rows"][0]["values"], dangerous_and_regular_values
                )

    def test_csv_endpoint_replays_the_complete_filtered_view(self):
        request = DataCsvExportRequest(
            root="troncons",
            fields=[{"table": "troncons", "field": "libelle"}],
            hidden_troncon_ids=["00000000-0000-0000-0000-000000000001"],
        )
        endpoint = next(
            route.endpoint for route in app.routes
            if route.path == "/api/data/export-csv"
        )
        result = {
            "root": "troncons",
            "granularity": "troncons",
            "columns": [{"label": "Tronçons — Libellé"}],
            "rows": [{"values": [f"T{index}"], "entities": []}
                     for index in range(2_001)],
        }
        connection = object()
        with patch(
            "digues_webapp.app.execute_view_export", return_value=result
        ) as execute:
            response = endpoint(request, connection)

        execute.assert_called_once_with(
            connection,
            "troncons",
            request.fields,
            request.hidden_troncon_ids,
        )
        content = response.body.decode("utf-8")
        self.assertTrue(content.startswith("\ufeffTronçons — Libellé"))
        self.assertIn("T2000", content)
        self.assertEqual(response.media_type, "text/csv; charset=utf-8")
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="digues-donnees.csv"',
        )

    def test_export_limit_is_reported_explicitly_instead_of_truncating(self):
        request = DataCsvExportRequest(
            root="troncons",
            fields=[{"table": "troncons", "field": "libelle"}],
        )
        endpoint = next(
            route.endpoint for route in app.routes
            if route.path == "/api/data/export-csv"
        )
        with patch(
            "digues_webapp.app.execute_view_export",
            side_effect=DataViewExportLimitError("Résultat trop volumineux."),
        ):
            with self.assertRaises(Exception) as raised:
                endpoint(request, object())
        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(raised.exception.detail, "Résultat trop volumineux.")

    def test_geopackage_endpoint_accepts_a_complete_result_over_2000_rows(self):
        request = DataGeoPackageExportRequest(
            root="desordres",
            fields=[{"table": "desordres", "field": "designation"}],
            entity_classes=["desordres"],
        )
        endpoint = next(
            route.endpoint for route in app.routes
            if route.path == "/api/data/export-gpkg"
        )
        result = {
            "root": "desordres",
            "granularity": "desordres",
            "columns": [],
            "rows": [{"values": [], "entities": []} for _ in range(2_001)],
        }
        connection = object()
        with (
            patch("digues_webapp.app.execute_view_export", return_value=result),
            patch("digues_webapp.app.build_geopackage", return_value=b"gpkg") as build,
        ):
            response = endpoint(request, connection)
        build.assert_called_once_with(result, ["desordres"])
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
