import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import Mock, patch

import requests

from digues_webapp.vigicrues import (
    HUBEAU_OBSERVATIONS_URL,
    HUBEAU_STATIONS_URL,
    VIGICRUES_STATIONS_REDIRECT_URL,
    VigicruesClient,
    VigicruesDataError,
    VigicruesServiceUnavailableError,
    VigicruesTerritoryRequiredError,
    build_territorial_dataset,
    clear_vigicrues_cache,
    fetch_vigicrues_stations,
    fetch_vigicrues_territorial_dataset,
    observation_targets,
    parse_forecast,
    parse_hubeau_observation_series,
    parse_vigicrues_stations,
    resample_observations,
    stations_geojson,
    territory_bbox,
)

try:
    from digues_webapp.app import FRONTEND_DIRECTORY, app
except ModuleNotFoundError as exc:
    if exc.name != "fastapi":
        raise
    FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"
    app = None


TERRITORY = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [1.0, 45.0],
                [3.0, 45.0],
                [3.0, 47.0],
                [1.0, 47.0],
                [1.0, 45.0],
            ]],
        },
        "properties": {"libelle": "Territoire de test"},
    }],
}


def vigicrues_payload():
    return {
        "count": 3,
        "ListEntVigiCru": [
            {
                "CdEntVigiCru": "A123456701",
                "TypEntVigiCru": "8",
                "LbEntVigiCru": "Station intérieure",
                "LbCoursEau": "La Rivière",
            },
            {
                "CdEntVigiCru": "B123456701",
                "TypEntVigiCru": "8",
                "LbEntVigiCru": "Station extérieure",
                "LbCoursEau": "Le Fleuve",
            },
            {
                "CdEntVigiCru": "C123456701",
                "TypEntVigiCru": "8",
                "LbEntVigiCru": "Station sans coordonnées",
            },
        ],
    }


def hydrometry_payload():
    return {
        "count": 3,
        "data": [
            {
                "code_station": "A123456701",
                "libelle_station": "Libellé hydrométrique",
                "longitude_station": 2.0,
                "latitude_station": 46.0,
            },
            {
                "code_station": "B123456701",
                "longitude_station": 4.0,
                "latitude_station": 46.0,
            },
            {
                "code_station": "C123456701",
                "longitude_station": None,
                "latitude_station": "invalide",
            },
        ],
    }


def observation_payload(quantity, observations):
    return {
        "count": len(observations),
        "data": [
            {
                "code_station": "A123456701",
                "grandeur_hydro": quantity,
                "date_obs": source_time,
                "resultat_obs": value,
            }
            for source_time, value in observations
        ],
    }


def forecast_payload(quantity="H"):
    return {
        "Simul": {
            "CdEntVigiCru": "A123456701",
            "TypEntVigiCru": "7",
            "GrdSimul": quantity,
            "DtProdSimul": "2026-09-07T08:00:00+02:00",
            "Prevs": [{
                "DtPrev": "2026-09-07T12:00:00+02:00",
                "ResMinPrev": 1.1,
                "ResMoyPrev": 1.2,
                "ResMaxPrev": 1.3,
            }],
        }
    }


class FakeResponse:
    def __init__(self, payload, url, status_code=200):
        self.body = json.dumps(payload).encode("utf-8")
        self.url = url
        self.status_code = status_code
        self.closed = False

    def iter_content(self, chunk_size):
        del chunk_size
        return [self.body]

    def close(self):
        self.closed = True


class VigicruesParsingTest(unittest.TestCase):
    def test_parses_real_field_names_and_preserves_future_identifiers(self):
        stations = parse_vigicrues_stations(
            vigicrues_payload(), hydrometry_payload()
        )
        self.assertEqual(len(stations), 2)
        self.assertEqual(stations[0], {
            "id": "A123456701",
            "code_vigicrues": "A123456701",
            "code_hydro": "A123456701",
            "nom": "Station intérieure",
            "cours_eau": "La Rivière",
            "troncon": None,
            "territoire": None,
            "longitude": 2.0,
            "latitude": 46.0,
        })

    def test_empty_invalid_and_missing_coordinates(self):
        self.assertEqual(
            parse_vigicrues_stations(
                {"ListEntVigiCru": []}, {"data": []}
            ),
            [],
        )
        with self.assertRaises(VigicruesDataError):
            parse_vigicrues_stations({}, {"data": []})
        stations = parse_vigicrues_stations(
            {"ListEntVigiCru": vigicrues_payload()["ListEntVigiCru"][2:]},
            {"data": hydrometry_payload()["data"][2:]},
        )
        self.assertEqual(stations, [])

    def test_bbox_and_exact_polygon_filter_keep_inside_only(self):
        stations = parse_vigicrues_stations(
            vigicrues_payload(), hydrometry_payload()
        )
        self.assertEqual(territory_bbox(TERRITORY), (1.0, 45.0, 3.0, 47.0))
        result = stations_geojson(stations, TERRITORY)
        self.assertEqual(len(result["features"]), 1)
        feature = result["features"][0]
        self.assertEqual(feature["id"], "A123456701")
        self.assertEqual(feature["geometry"]["coordinates"], [2.0, 46.0])
        self.assertEqual(feature["properties"]["longitude"], 2.0)
        self.assertEqual(feature["properties"]["latitude"], 46.0)

    def test_missing_territory_is_explicit(self):
        with self.assertRaises(VigicruesTerritoryRequiredError):
            territory_bbox({"type": "FeatureCollection", "features": []})


class VigicruesObservationTest(unittest.TestCase):
    def test_parses_hubeau_height_and_converts_mm_to_metres(self):
        observations = parse_hubeau_observation_series(
            observation_payload("H", [("2026-09-07T05:50:00Z", 1428)]),
            "H",
        )
        self.assertEqual(observations, [{
            "source_time": "2026-09-07T05:50:00Z",
            "value": 1.428,
            "native_value": 1428.0,
            "native_unit": "mm",
        }])

    def test_parses_hubeau_discharge_and_converts_litres_to_cubic_metres(self):
        observations = parse_hubeau_observation_series(
            observation_payload("Q", [("2026-09-07T06:40:00Z", 7600)]),
            "Q",
        )
        self.assertEqual(observations, [{
            "source_time": "2026-09-07T06:40:00Z",
            "value": 7.6,
            "native_value": 7600.0,
            "native_unit": "l/s",
        }])

    def test_selects_nearest_measure_without_interpolation(self):
        height = parse_hubeau_observation_series(observation_payload("H", [
            ("2026-09-07T04:40:00+00:00", 1100),
            ("2026-09-07T05:50:00+00:00", 1428),
            ("2026-09-07T06:20:00+00:00", 1500),
        ]), "H")
        sample = resample_observations(height, [], date(2026, 9, 7))[25]
        self.assertEqual(sample["target_time"], "2026-09-07T06:00:00Z")
        self.assertEqual(sample["height_m"], 1.428)
        self.assertEqual(
            sample["height_source_time"], "2026-09-07T05:50:00Z"
        )

    def test_tolerance_is_inclusive_and_outside_value_becomes_null(self):
        height = [
            {"source_time": "2026-09-07T04:30:00Z", "value": 1.0},
        ]
        at_limit = resample_observations(
            height, [], date(2026, 9, 7)
        )[25]
        self.assertEqual(at_limit["height_m"], 1.0)
        outside = resample_observations(
            [{"source_time": "2026-09-07T04:29:59Z", "value": 2.0}],
            [],
            date(2026, 9, 7),
        )[25]
        self.assertIsNone(outside["height_m"])
        self.assertIsNone(outside["height_source_time"])

    def test_height_and_discharge_keep_independent_source_times(self):
        samples = resample_observations(
            [{"source_time": "2026-09-07T05:50:00Z", "value": 1.428}],
            [{"source_time": "2026-09-07T06:40:00Z", "value": 7.6}],
            date(2026, 9, 7),
        )
        sample = samples[25]
        self.assertEqual(sample["height_source_time"], "2026-09-07T05:50:00Z")
        self.assertEqual(
            sample["discharge_source_time"], "2026-09-07T06:40:00Z"
        )
        self.assertEqual(sample["discharge_m3_s"], 7.6)

    def test_grid_contains_four_targets_per_day_for_seven_days(self):
        targets = observation_targets(date(2026, 9, 7))
        self.assertEqual(len(targets), 28)
        self.assertEqual(targets[0].isoformat(), "2026-09-01T00:00:00+00:00")
        self.assertEqual(targets[-1].isoformat(), "2026-09-07T18:00:00+00:00")
        self.assertEqual(
            {target.hour for target in targets},
            {0, 6, 12, 18},
        )

    def test_forecast_preserves_production_time_range_and_unit(self):
        forecast = parse_forecast(forecast_payload())
        self.assertEqual(forecast["kind"], "forecast")
        self.assertEqual(forecast["quantity"], "height")
        self.assertEqual(forecast["unit"], "m")
        self.assertEqual(forecast["produced_at"], "2026-09-07T06:00:00Z")
        self.assertEqual(forecast["points"][0]["forecast_time"], "2026-09-07T10:00:00Z")


class VigicruesClientTest(unittest.TestCase):
    def test_fetches_vigicrues_and_hubeau_with_territory_bbox(self):
        session = Mock()
        session.get.side_effect = [
            FakeResponse(vigicrues_payload(), VIGICRUES_STATIONS_REDIRECT_URL),
            FakeResponse(hydrometry_payload(), HUBEAU_STATIONS_URL, 206),
        ]
        stations = VigicruesClient(session=session).stations(
            (1.0, 45.0, 3.0, 47.0)
        )
        self.assertEqual(len(stations), 2)
        self.assertEqual(session.get.call_count, 2)
        hubeau_call = session.get.call_args_list[1]
        self.assertEqual(
            hubeau_call.kwargs["params"]["bbox"],
            "1.0000000,45.0000000,3.0000000,47.0000000",
        )

    def test_network_error_is_normalized(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("private detail")
        with self.assertRaisesRegex(
            VigicruesServiceUnavailableError,
            "temporairement indisponible",
        ) as raised:
            VigicruesClient(session=session).stations((1.0, 45.0, 3.0, 47.0))
        self.assertNotIn("private detail", str(raised.exception))

    def test_streaming_timeout_is_normalized_and_response_is_closed(self):
        response = FakeResponse(
            vigicrues_payload(), VIGICRUES_STATIONS_REDIRECT_URL
        )
        response.iter_content = Mock(side_effect=requests.Timeout("private detail"))
        session = Mock()
        session.get.return_value = response
        with self.assertRaisesRegex(
            VigicruesServiceUnavailableError,
            "pas répondu à temps",
        ) as raised:
            VigicruesClient(session=session).stations((1.0, 45.0, 3.0, 47.0))
        self.assertNotIn("private detail", str(raised.exception))
        self.assertTrue(response.closed)

    def test_observation_and_forecast_calls_use_official_parameters(self):
        session = Mock()
        session.get.side_effect = [
            FakeResponse(
                observation_payload(
                    "H", [("2026-09-07T05:50:00Z", 1428)]
                ),
                HUBEAU_OBSERVATIONS_URL,
            ),
            FakeResponse(
                {
                    "ListEntVigiCru": [{
                        "CdEntVigiCru": "A123456701",
                        "TypEntVigiCru": "7",
                    }]
                },
                "https://www.vigicrues.gouv.fr/services/v1.1/prevision.json",
            ),
            FakeResponse(
                forecast_payload(),
                "https://www.vigicrues.gouv.fr/services/v1.1/prevision.json",
            ),
        ]
        client = VigicruesClient(session=session)
        self.assertEqual(client.observations(
            "A123456701",
            "H",
            start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 7, 9, tzinfo=timezone.utc),
        )[0]["value"], 1.428)
        self.assertEqual(client.forecast_catalog("H"), {"A123456701"})
        self.assertEqual(client.forecast("A123456701", "H")["unit"], "m")
        observation_params = session.get.call_args_list[0].kwargs["params"]
        self.assertEqual(observation_params, {
            "code_entite": "A123456701",
            "grandeur_hydro": "H",
            "date_debut_obs": "2026-09-01T00:00:00Z",
            "date_fin_obs": "2026-09-07T09:00:00Z",
            "sort": "asc",
            "size": 20_000,
            "format": "json",
        })
        forecast_params = session.get.call_args_list[2].kwargs["params"]
        self.assertEqual(forecast_params, {
            "CdEntVigiCru": "A123456701",
            "TypEntVigiCru": "7",
            "GrdSimul": "H",
            "FormatDate": "iso",
        })


class FakeConnection:
    pass


class FakeVigicruesClient:
    def __init__(
        self,
        *,
        fail_discharge=False,
        fail_height=False,
        fail_station_codes=None,
        forecast_codes=None,
    ):
        self.fail_discharge = fail_discharge
        self.fail_height = fail_height
        self.fail_station_codes = set(fail_station_codes or [])
        self.forecast_codes = set(forecast_codes or [])
        self.station_calls = 0
        self.observation_calls = []

    def stations(self, bbox):
        self.bbox = bbox
        self.station_calls += 1
        return parse_vigicrues_stations(
            vigicrues_payload(), hydrometry_payload()
        )

    def observations(self, code_hydro, quantity, *, start_time, end_time):
        self.observation_calls.append(
            (code_hydro, quantity, start_time, end_time)
        )
        if (
            code_hydro in self.fail_station_codes
            or quantity == "Q" and self.fail_discharge
            or quantity == "H" and self.fail_height
        ):
            raise VigicruesServiceUnavailableError("private detail")
        source_time = (
            "2026-09-07T05:50:00Z"
            if quantity == "H"
            else "2026-09-07T06:40:00Z"
        )
        value = 1.428 if quantity == "H" else 7.6
        return [{"source_time": source_time, "value": value}]

    def forecast_catalog(self, quantity):
        del quantity
        return self.forecast_codes

    def forecast(self, code_vigicrues, quantity):
        del code_vigicrues
        return parse_forecast(forecast_payload(quantity))


class VigicruesServiceTest(unittest.TestCase):
    def setUp(self):
        clear_vigicrues_cache()

    def test_service_uses_database_territory_and_returns_geojson(self):
        client = FakeVigicruesClient()
        with patch(
            "digues_webapp.vigicrues.fetch_territoire_administratif",
            return_value=TERRITORY,
        ) as fetch_territory:
            result = fetch_vigicrues_stations(
                FakeConnection(), client=client, day_provider=lambda: date(2026, 9, 7)
            )
        fetch_territory.assert_called_once()
        self.assertEqual(client.bbox, (1.0, 45.0, 3.0, 47.0))
        self.assertEqual(result["type"], "FeatureCollection")
        self.assertEqual(len(result["features"]), 1)
        properties = result["features"][0]["properties"]
        self.assertEqual(properties["latest_height_m"], 1.428)
        self.assertEqual(
            properties["latest_height_source_time"],
            "2026-09-07T05:50:00Z",
        )
        self.assertEqual(properties["latest_discharge_m3_s"], 7.6)
        self.assertEqual(
            properties["latest_discharge_source_time"],
            "2026-09-07T06:40:00Z",
        )

    def test_daily_cache_reuses_dataset_then_rolls_to_next_day(self):
        client = FakeVigicruesClient()
        with patch(
            "digues_webapp.vigicrues.fetch_territoire_administratif",
            return_value=TERRITORY,
        ):
            first = fetch_vigicrues_territorial_dataset(
                FakeConnection(), client=client,
                day_provider=lambda: date(2026, 9, 7),
            )
            second = fetch_vigicrues_territorial_dataset(
                FakeConnection(), client=client,
                day_provider=lambda: date(2026, 9, 7),
            )
            third = fetch_vigicrues_territorial_dataset(
                FakeConnection(), client=client,
                day_provider=lambda: date(2026, 9, 8),
            )
        self.assertIs(first, second)
        self.assertIsNot(first, third)
        self.assertEqual(client.station_calls, 2)

    def test_concurrent_first_access_builds_dataset_once(self):
        started = Event()
        release = Event()

        class SlowClient(FakeVigicruesClient):
            def stations(self, bbox):
                started.set()
                release.wait(timeout=2)
                return super().stations(bbox)

        client = SlowClient()
        with patch(
            "digues_webapp.vigicrues.fetch_territoire_administratif",
            return_value=TERRITORY,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(
                    fetch_vigicrues_territorial_dataset,
                    FakeConnection(),
                    client=client,
                    day_provider=lambda: date(2026, 9, 7),
                )
                self.assertTrue(started.wait(timeout=1))
                second = executor.submit(
                    fetch_vigicrues_territorial_dataset,
                    FakeConnection(),
                    client=client,
                    day_provider=lambda: date(2026, 9, 7),
                )
                release.set()
                self.assertIs(first.result(timeout=3), second.result(timeout=3))
        self.assertEqual(client.station_calls, 1)

    def test_changed_territory_has_distinct_cache_identity(self):
        changed = json.loads(json.dumps(TERRITORY))
        changed["features"][0]["geometry"]["coordinates"][0][1][0] = 3.1
        client = FakeVigicruesClient()
        with patch(
            "digues_webapp.vigicrues.fetch_territoire_administratif",
            side_effect=[TERRITORY, changed],
        ):
            first = fetch_vigicrues_territorial_dataset(
                FakeConnection(), client=client,
                day_provider=lambda: date(2026, 9, 7),
            )
            second = fetch_vigicrues_territorial_dataset(
                FakeConnection(), client=client,
                day_provider=lambda: date(2026, 9, 7),
            )
        self.assertNotEqual(
            first["territoire"]["fingerprint"],
            second["territoire"]["fingerprint"],
        )
        self.assertEqual(client.station_calls, 2)

    def test_station_without_discharge_or_forecast_remains_usable(self):
        dataset = build_territorial_dataset(
            TERRITORY,
            FakeVigicruesClient(fail_discharge=True),
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 9, tzinfo=timezone.utc),
            observation_workers=1,
        )
        station = dataset["stations"][0]
        self.assertEqual(station["observations"]["latest"]["height_m"], 1.428)
        self.assertIsNone(
            station["observations"]["latest"]["discharge_m3_s"]
        )
        self.assertIsNone(station["forecasts"]["height"])
        self.assertIsNone(station["forecasts"]["discharge"])
        self.assertEqual(station["errors"], [{
            "kind": "observation",
            "quantity": "discharge",
            "error": "unavailable",
        }])

    def test_station_without_height_remains_usable_with_discharge(self):
        dataset = build_territorial_dataset(
            TERRITORY,
            FakeVigicruesClient(fail_height=True),
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 9, tzinfo=timezone.utc),
            observation_workers=1,
        )
        station = dataset["stations"][0]
        self.assertIsNone(station["observations"]["latest"]["height_m"])
        self.assertEqual(
            station["observations"]["latest"]["discharge_m3_s"], 7.6
        )
        self.assertEqual(station["errors"], [{
            "kind": "observation",
            "quantity": "height",
            "error": "unavailable",
        }])

    def test_one_station_observation_failure_does_not_remove_other_station(self):
        class TwoStationClient(FakeVigicruesClient):
            def stations(self, bbox):
                stations = super().stations(bbox)
                second = {
                    **stations[0],
                    "id": "D123456701",
                    "code_hydro": "D123456701",
                    "code_vigicrues": "D123456701",
                    "nom": "Deuxième station",
                    "longitude": 2.2,
                }
                return [stations[0], second]

        dataset = build_territorial_dataset(
            TERRITORY,
            TwoStationClient(fail_station_codes={"A123456701"}),
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 9, tzinfo=timezone.utc),
            observation_workers=2,
        )
        by_code = {station["code_hydro"]: station for station in dataset["stations"]}
        self.assertIsNone(
            by_code["A123456701"]["observations"]["latest"]["height_m"]
        )
        self.assertEqual(
            by_code["D123456701"]["observations"]["latest"]["height_m"],
            1.428,
        )

    def test_dataset_provenance_separates_hubeau_observations_and_forecasts(self):
        client = FakeVigicruesClient(forecast_codes={"A123456701"})
        dataset = build_territorial_dataset(
            TERRITORY,
            client,
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 9, tzinfo=timezone.utc),
            observation_workers=1,
        )
        self.assertEqual(dataset["provenance"]["observations"], "Hub'Eau Hydrométrie")
        self.assertEqual(dataset["provenance"]["forecasts"], "Vigicrues")
        self.assertEqual(dataset["provenance"]["observation_native_units"], {
            "height": "mm",
            "discharge": "l/s",
        })
        station = dataset["stations"][0]
        self.assertEqual(station["observations"]["source"], "Hub'Eau Hydrométrie")
        self.assertEqual(station["forecasts"]["height"]["source"], "Vigicrues")
        self.assertEqual(
            {call[2] for call in client.observation_calls},
            {datetime(2026, 8, 31, 22, 30, tzinfo=timezone.utc)},
        )
        self.assertEqual(
            {call[3] for call in client.observation_calls},
            {datetime(2026, 9, 7, 9, tzinfo=timezone.utc)},
        )

    def test_available_forecast_is_separate_from_observations(self):
        dataset = build_territorial_dataset(
            TERRITORY,
            FakeVigicruesClient(forecast_codes={"A123456701"}),
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 9, tzinfo=timezone.utc),
            observation_workers=1,
        )
        station = dataset["stations"][0]
        self.assertEqual(station["forecasts"]["height"]["kind"], "forecast")
        self.assertEqual(
            station["observations"]["kind"], "observation"
        )

    def test_forecast_failure_does_not_remove_hubeau_observations(self):
        class ForecastFailureClient(FakeVigicruesClient):
            def forecast_catalog(self, quantity):
                if quantity == "H":
                    raise VigicruesServiceUnavailableError("private detail")
                return super().forecast_catalog(quantity)

        dataset = build_territorial_dataset(
            TERRITORY,
            ForecastFailureClient(),
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 9, tzinfo=timezone.utc),
            observation_workers=1,
        )
        station = dataset["stations"][0]
        self.assertEqual(station["observations"]["latest"]["height_m"], 1.428)
        self.assertEqual(station["observations"]["latest"]["discharge_m3_s"], 7.6)
        self.assertEqual(dataset["errors"], [{
            "kind": "forecast_catalog",
            "quantity": "height",
            "error": "unavailable",
        }])


@unittest.skipIf(app is None, "FastAPI indisponible dans l'environnement de test")
class VigicruesEndpointTest(unittest.TestCase):
    def test_neutral_endpoint_is_the_canonical_route(self):
        route = next(
            route for route in app.routes
            if route.path == "/api/hydrometrie/sites"
        )
        collection = {"type": "FeatureCollection", "features": []}
        with patch(
            "digues_webapp.app.fetch_hydrometric_sites",
            return_value=collection,
        ) as fetch_sites:
            self.assertEqual(route.endpoint(FakeConnection()), collection)
        fetch_sites.assert_called_once()
        self.assertEqual(route.methods, {"GET"})
        self.assertFalse(route.deprecated)

    def test_obsolete_application_route_is_absent(self):
        self.assertNotIn(
            "/api/vigicrues/stations",
            {route.path for route in app.routes},
        )

    def test_frontend_layer_is_distinct_and_loaded_only_on_demand(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(
            encoding="utf-8"
        )
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-layer-toggle="vigicrues"', page)
        self.assertIn("Stations hydrométriques", page)
        self.assertIn("function toggleVigicruesLayer(input)", script)
        self.assertIn('fetchGeoJSON("/api/hydrometrie/sites")', script)
        self.assertIn("configureVigicruesStation", script)
        self.assertIn('series_resolution?.height?.status === "multiple_unresolved"', script)
        self.assertIn("Plusieurs séries H", script)
        self.assertIn('fields.push(["Hauteur", "height"])', script)
        self.assertIn('fields.push(["Débit", "discharge"])', script)
        self.assertIn('fields.push(["Mesure hauteur", "height_time"])', script)
        self.assertIn('fields.push(["Mesure débit", "discharge_time"])', script)
        popup = script.split("function configureVigicruesStation", 1)[1].split(
            "function errorDetail", 1
        )[0]
        self.assertNotIn("Code Hydro", popup)
        self.assertNotIn("Code Vigicrues", popup)
        startup = script.split("async function loadMapData()", 1)[1].split(
            "loadMapData();", 1
        )[0]
        self.assertNotIn("/api/vigicrues/stations", startup)
        self.assertIn(".vigicrues-station-marker", css)
        self.assertIn("#176b87", css)


if __name__ == "__main__":
    unittest.main()
