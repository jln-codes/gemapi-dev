from contextlib import contextmanager
import unittest
from unittest.mock import call, patch

from digues_webapp.environment_tools import (
    EnvironmentToolError,
    get_hydrometric_data,
    get_hydrometric_sites,
    get_precipitation_forecast,
)


DATASET = {
    "cache_date": "2026-09-07",
    "generated_at": "2026-09-07T09:00:00Z",
    "territoire": {"id": "territory-1"},
    "provenance": {
        "observations": "Hub'Eau Hydrométrie",
        "forecasts": "Vigicrues",
    },
    "sites": [
        {
            "code_site": "E3666010",
            "name": "Béthune",
            "watercourse": {"name": "La Lawe"},
            "position": {"longitude": 2.64, "latitude": 50.53},
            "stations": [{"code_station": "E366601001"}],
            "territory_relations": [{
                "territory": {"id": "T", "fingerprint": "territory-fingerprint"},
                "role": "upstream",
                "selection": {
                    "method": "first_active_upstream",
                    "state": "selected",
                },
                "topology_status": "directed",
                "hydraulic_continuity": "supported",
                "proof_summary": {
                    "count": 3,
                    "pending_review_count": 1,
                    "conditional_connection_ids": ["regulated"],
                },
                "proofs": [],
            }],
            "observations": {
                "latest": {
                    "height": {
                        "value": 1.42,
                        "source_time": "2026-09-07T05:50:00Z",
                        "station_code": "E366601001",
                    },
                    "discharge": {
                        "value": 7.6,
                        "source_time": "2026-09-07T06:40:00Z",
                        "station_code": "E366601001",
                    },
                },
                "series_resolution": {
                    "height": {"status": "selected_single_active",
                               "selected_station_code": "E366601001"},
                    "discharge": {"status": "selected_single_active",
                                  "selected_station_code": "E366601001"},
                },
                "samples_7d": {
                    "samples": [
                        {
                            "target_time": "2026-09-05T00:00:00Z",
                            "height_m": 1.1,
                            "height_source_time": "2026-09-04T23:55:00Z",
                            "discharge_m3_s": 6.8,
                            "discharge_source_time": "2026-09-05T00:20:00Z",
                        },
                        {
                            "target_time": "2026-09-06T06:00:00Z",
                            "height_m": 1.3,
                            "height_source_time": "2026-09-06T05:50:00Z",
                            "discharge_m3_s": None,
                            "discharge_source_time": None,
                        },
                        {
                            "target_time": "2026-09-07T06:00:00Z",
                            "height_m": 1.42,
                            "height_source_time": "2026-09-07T05:50:00Z",
                            "discharge_m3_s": 7.6,
                            "discharge_source_time": "2026-09-07T06:40:00Z",
                        },
                    ],
                },
            },
            "forecasts": {
                "height": {
                    "unit": "m",
                    "produced_at": "2026-09-07T08:00:00Z",
                    "points": [{
                        "forecast_time": "2026-09-07T12:00:00Z",
                        "minimum": 1.3,
                        "mean": 1.5,
                        "maximum": 1.7,
                    }],
                },
                "discharge": None,
            },
        },
        {
            "code_site": "E3666020",
            "name": "Bruay",
            "watercourse": {"name": "La Lawe"},
            "position": {"longitude": 2.55, "latitude": 50.48},
            "stations": [{"code_station": "E366602001"}],
            "observations": {
                "latest": {"height": None, "discharge": None},
                "series_resolution": {
                    "height": {"status": "unavailable",
                               "selected_station_code": None},
                    "discharge": {"status": "unavailable",
                                  "selected_station_code": None},
                },
                "samples_7d": {"samples": []},
            },
            "forecasts": {"height": None, "discharge": None},
        },
    ],
}

class EnvironmentToolsTest(unittest.TestCase):
    def cached_vigicrues(self):
        return (
            patch("digues_webapp.environment_tools.open_ai_connection"),
            patch(
                "digues_webapp.environment_tools.fetch_hydrometric_territorial_dataset",
                return_value=DATASET,
            ),
        )

    def test_lists_and_searches_cached_territory_stations(self):
        connection, fetch = self.cached_vigicrues()
        with connection as open_connection, fetch as fetch_dataset:
            listed = get_hydrometric_sites()
            result = get_hydrometric_sites(query="béthune")

        self.assertEqual(listed["count"], 2)
        self.assertEqual(result["source"], "Hub'Eau Hydrométrie + BD TOPAGE")
        self.assertEqual(result["cache_date"], "2026-09-07")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["sites"][0]["name"], "Béthune")
        self.assertEqual(result["sites"][0]["river"], "La Lawe")
        self.assertEqual(result["sites"][0]["latest_height_m"], 1.42)
        self.assertEqual(result["sites"][0]["code_site"], "E3666010")
        self.assertNotIn("stations", result)
        relation = result["sites"][0]["territory_relations"][0]
        self.assertEqual(relation["role"], "upstream")
        self.assertEqual(relation["proof_count"], 3)
        self.assertEqual(relation["pending_review_proof_count"], 1)
        self.assertTrue(relation["has_conditional_connections"])
        self.assertEqual(
            fetch_dataset.call_args_list,
            [
                call(open_connection.return_value.__enter__.return_value),
                call(open_connection.return_value.__enter__.return_value),
            ],
        )

    def test_hydrometry_uses_controlled_runtime_connection_for_persistent_cache(self):
        read_connection = object()
        cache_connection = object()

        @contextmanager
        def factory():
            yield read_connection

        @contextmanager
        def cache_factory():
            yield cache_connection

        factory.hydrometry_cache_factory = cache_factory
        with patch(
            "digues_webapp.environment_tools.fetch_hydrometric_territorial_dataset",
            return_value=DATASET,
        ) as fetch_dataset:
            result = get_hydrometric_sites(connection_factory=factory)

        self.assertEqual(result["count"], 2)
        fetch_dataset.assert_called_once_with(
            read_connection,
            persistent_connection=cache_connection,
        )

    def test_observations_filter_period_and_keep_separate_h_q_times(self):
        connection, fetch = self.cached_vigicrues()
        with connection, fetch:
            result = get_hydrometric_data(
                station="E366601001",
                kind="observation",
                variable="both",
                start_time="2026-09-06T00:00:00Z",
                end_time="2026-09-07T23:59:59Z",
            )

        height, discharge = result["series"]
        self.assertEqual(result["source"], "Hub'Eau Hydrométrie")
        self.assertEqual((height["variable"], height["unit"]), ("height", "m"))
        self.assertEqual(
            (discharge["variable"], discharge["unit"]),
            ("discharge", "m3/s"),
        )
        self.assertEqual(len(height["points"]), 2)
        self.assertEqual(result["period"]["start_time"], "2026-09-06T00:00:00Z")
        self.assertEqual(
            height["points"][-1]["source_time"], "2026-09-07T05:50:00Z"
        )
        self.assertEqual(
            discharge["points"][-1]["source_time"], "2026-09-07T06:40:00Z"
        )
        self.assertTrue(result["data_available"])
        self.assertEqual(
            result["site"]["territory_relations"][0]["proof_count"], 3
        )

    def test_forecast_and_station_without_forecast_are_explicit(self):
        connection, fetch = self.cached_vigicrues()
        with connection, fetch:
            available = get_hydrometric_data(
                station="Béthune", kind="forecast", variable="both"
            )
            absent = get_hydrometric_data(
                station="Bruay", kind="forecast", variable="Q"
            )

        self.assertTrue(available["series"][0]["data_available"])
        self.assertEqual(available["source"], "Vigicrues")
        self.assertFalse(available["series"][1]["data_available"])
        self.assertEqual(
            available["series"][0]["points"][0]["forecast_time"],
            "2026-09-07T12:00:00Z",
        )
        self.assertFalse(absent["data_available"])
        self.assertEqual(absent["series"][0]["points"], [])

    def test_unknown_station_and_period_without_data_are_explicit(self):
        connection, fetch = self.cached_vigicrues()
        with connection, fetch:
            with self.assertRaisesRegex(EnvironmentToolError, "inconnue"):
                get_hydrometric_data(station="Absente", kind="observation")
            result = get_hydrometric_data(
                station="Béthune",
                kind="observation",
                start_time="2025-01-01T00:00:00Z",
                end_time="2025-01-02T00:00:00Z",
            )
        self.assertFalse(result["data_available"])
        self.assertTrue(all(series["points"] == [] for series in result["series"]))

    def test_precipitation_lists_steps_and_selects_point_and_time(self):
        steps = {
            "model": "MIXED",
            "unit": "mm",
            "accumulation_hours": 24,
            "run": None,
            "cache_date": "2026-09-07",
            "forecast_steps": [
                {
                    "valid_time": "2026-09-08T00:00:00Z",
                    "status": "ready",
                    "model": "AROME",
                    "run": "2026-09-07T00:00:00Z",
                }
            ],
        }
        grid = {
            **steps,
            "model": "AROME",
            "run": "2026-09-07T00:00:00Z",
            "valid_time": "2026-09-08T00:00:00Z",
            "cells": [
                {"bounds": [[50.0, 2.0], [51.0, 3.0]], "value_mm": 12.4}
            ],
        }
        with (
            patch("digues_webapp.environment_tools.open_ai_connection") as connection,
            patch(
                "digues_webapp.environment_tools.fetch_precipitation_forecast_steps",
                return_value=steps,
            ) as fetch_steps,
            patch(
                "digues_webapp.environment_tools.fetch_precipitations",
                return_value=grid,
            ) as fetch_grid,
        ):
            available = get_precipitation_forecast()
            result = get_precipitation_forecast(
                valid_time="2026-09-08T00:00:00Z",
                longitude=2.64,
                latitude=50.53,
            )

        self.assertEqual(available["forecast_steps"], steps["forecast_steps"])
        self.assertEqual(result["value"], 12.4)
        self.assertEqual(result["unit"], "mm")
        self.assertEqual(result["model"], "AROME")
        self.assertEqual(result["run"], "2026-09-07T00:00:00Z")
        self.assertEqual(available["model"], "MIXED")
        self.assertEqual(available["source"], "Météo-France")
        self.assertTrue(result["data_available"])
        read_connection = connection.return_value.__enter__.return_value
        self.assertEqual(
            fetch_steps.call_args_list,
            [call(read_connection), call(read_connection)],
        )
        fetch_grid.assert_called_once_with(
            read_connection,
            valid_time="2026-09-08T00:00:00Z",
        )

    def test_precipitation_absence_and_parameters_are_validated(self):
        grid = {
            "model": "ARPEGE",
            "unit": "mm",
            "valid_time": "2026-09-08T00:00:00Z",
            "cells": [
                {"bounds": [[50.0, 2.0], [51.0, 3.0]], "value_mm": None}
            ],
        }
        with (
            patch("digues_webapp.environment_tools.open_ai_connection"),
            patch(
                "digues_webapp.environment_tools.fetch_precipitation_forecast_steps",
                return_value={
                    "forecast_steps": [{
                        "valid_time": "2026-09-08T00:00:00Z",
                        "status": "ready",
                    }],
                },
            ),
            patch(
                "digues_webapp.environment_tools.fetch_precipitations",
                return_value=grid,
            ),
        ):
            result = get_precipitation_forecast(
                valid_time="2026-09-08T00:00:00Z",
                longitude=2.64,
                latitude=50.53,
            )
        self.assertIsNone(result["value"])
        self.assertFalse(result["data_available"])

        with self.assertRaises(EnvironmentToolError):
            get_precipitation_forecast(longitude=2.64)
        with self.assertRaises(EnvironmentToolError):
            get_precipitation_forecast(latitude=91, longitude=2.64)
        with self.assertRaises(EnvironmentToolError):
            get_precipitation_forecast(latitude=50.53, longitude="2.64")
        with self.assertRaises(EnvironmentToolError):
            get_precipitation_forecast(valid_time="demain")


if __name__ == "__main__":
    unittest.main()
