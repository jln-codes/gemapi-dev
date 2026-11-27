from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

import requests

from digues_webapp import weather


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def capabilities_xml(*coverage_ids):
    entries = "".join(
        f"<CoverageSummary><CoverageId>{coverage_id}</CoverageId></CoverageSummary>"
        for coverage_id in coverage_ids
    )
    return f"<Capabilities>{entries}</Capabilities>".encode("utf-8")


def description_xml(coverage_id, *seconds):
    coefficients = " ".join(str(value) for value in seconds)
    return f"""
    <CoverageDescriptions>
      <CoverageDescription>
        <CoverageId>{coverage_id}</CoverageId>
        <GeneralGridAxis>
          <gridAxesSpanned>time</gridAxesSpanned>
          <coefficients>{coefficients}</coefficients>
        </GeneralGridAxis>
      </CoverageDescription>
    </CoverageDescriptions>
    """.encode("utf-8")


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.cursor_instance = FakeCursor(row)

    def cursor(self):
        return self.cursor_instance


class FakeResponse:
    def __init__(self, status_code, content=b"ok"):
        self.status_code = status_code
        self.content = content

    def iter_content(self, _size):
        yield self.content


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeWeatherClient:
    def __init__(self, run, *, description_seconds=None):
        self.run = run
        self.coverage_id = (
            f"{weather.PRODUCT}___{run.strftime('%Y-%m-%dT%H.%M.%SZ')}_P1D"
        )
        self.capabilities_calls = 0
        self.describe_calls = 0
        self.download_calls = []
        self.description_seconds = description_seconds or (
            24 * 3600,
            48 * 3600,
            72 * 3600,
            96 * 3600,
        )

    def get_capabilities(self):
        self.capabilities_calls += 1
        older = self.run - timedelta(hours=6)
        return capabilities_xml(
            f"{weather.PRODUCT}___{older.strftime('%Y-%m-%dT%H.%M.%SZ')}_P1D",
            self.coverage_id,
        )

    def describe_coverage(self, coverage_id):
        self.describe_calls += 1
        return description_xml(coverage_id, *self.description_seconds)

    def download_coverage(self, coverage_id, valid_time, bbox, destination):
        self.download_calls.append((coverage_id, valid_time, bbox.as_tuple()))
        create_test_tiff(destination, [[2.5]], nodata=None)


class MultiRunWeatherClient:
    def __init__(self, runs):
        self.runs = list(runs)
        self.describe_calls = []

    def _coverage_id(self, run):
        return f"{weather.PRODUCT}___{run.strftime('%Y-%m-%dT%H.%M.%SZ')}_P1D"

    def get_capabilities(self):
        return capabilities_xml(
            *(self._coverage_id(run) for run, _seconds in self.runs)
        )

    def describe_coverage(self, coverage_id):
        self.describe_calls.append(coverage_id)
        for run, seconds in self.runs:
            if coverage_id == self._coverage_id(run):
                return description_xml(coverage_id, *seconds)
        raise AssertionError(f"coverage inattendu: {coverage_id}")


class ExactTimeWeatherClient:
    def __init__(self, runs, *, fail_downloads=False):
        self.runs = list(runs)
        self.fail_downloads = fail_downloads
        self.capabilities_calls = 0
        self.describe_calls = []
        self.download_calls = []

    @staticmethod
    def coverage_id(run):
        return f"{weather.PRODUCT}___{run.strftime('%Y-%m-%dT%H.%M.%SZ')}_P1D"

    def get_capabilities(self):
        self.capabilities_calls += 1
        return capabilities_xml(*(self.coverage_id(run) for run, _times in self.runs))

    def describe_coverage(self, coverage_id):
        self.describe_calls.append(coverage_id)
        for run, times in self.runs:
            if coverage_id == self.coverage_id(run):
                return description_xml(
                    coverage_id,
                    *((value - run).total_seconds() for value in times),
                )
        raise AssertionError(f"coverage inattendu: {coverage_id}")

    def download_coverage(self, coverage_id, valid_time, bbox, destination):
        self.download_calls.append((coverage_id, valid_time, bbox.as_tuple()))
        if self.fail_downloads:
            raise weather.WeatherServiceUnavailableError("AROME indisponible")
        create_test_tiff(destination, [[3.5]], nodata=None)


class ImmediateExecutor:
    def submit(self, function, *args):
        function(*args)


class DeferredExecutor:
    def __init__(self):
        self.tasks = []

    def submit(self, function, *args):
        self.tasks.append((function, args))

    def run_all(self):
        while self.tasks:
            function, args = self.tasks.pop(0)
            function(*args)


def create_test_tiff(path: Path, values, *, nodata=None):
    try:
        from osgeo import gdal, osr
    except Exception as exc:
        raise unittest.SkipTest(f"GDAL indisponible pour le test raster: {exc}")

    height = len(values)
    width = len(values[0])
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), width, height, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((2.0, 0.1, 0.0, 51.0, 0.0, -0.1))
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(4326)
    dataset.SetProjection(spatial_ref.ExportToWkt())
    band = dataset.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    for y, row in enumerate(values):
        for x, value in enumerate(row):
            band.WriteRaster(x, y, 1, 1, struct.pack("=f", float(value)))
    dataset.FlushCache()
    dataset = None


class WeatherServiceTest(unittest.TestCase):
    def setUp(self):
        self.executor_patch = patch.object(
            weather,
            "_preload_executor",
            ImmediateExecutor(),
        )
        self.executor_patch.start()

    def tearDown(self):
        weather.clear_weather_cache()
        self.executor_patch.stop()

    def test_missing_territory_prevents_meteofrance_call(self):
        client = FakeWeatherClient(utc("2026-09-05T18:00:00Z"))
        with self.assertRaises(weather.TerritoryRequiredError):
            weather.fetch_precipitations(FakeConnection((None, None, None, None)), client=client)
        self.assertEqual(client.capabilities_calls, 0)
        self.assertEqual(client.download_calls, [])

    def test_missing_api_key_is_clean_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(weather.WeatherConfigurationError) as captured:
                weather.load_meteofrance_api_key()
        self.assertNotIn("ARPEGE_API_KEY", str(captured.exception))

    def test_arpege_environment_keeps_apikey_header_and_arome_separate(self):
        with patch.dict(os.environ, {"ARPEGE_API_KEY": "environment-key",
                                   "AROME_API_KEY": "arome-key"}, clear=True):
            key = weather.load_meteofrance_api_key()
            self.assertEqual(key, "environment-key")
            session = FakeSession([FakeResponse(200, b"<Capabilities/>")])
            weather.MeteoFranceWcsClient(key, session=session).get_capabilities()
            self.assertEqual(session.calls[0][1]["headers"], {"apikey": key})

    def test_arome_key_is_loaded_independently_from_arpege(self):
        with patch.dict(os.environ, {"ARPEGE_API_KEY": "arpege-key",
                                   "AROME_API_KEY": "arome-key"}, clear=True):
            self.assertEqual(weather.load_meteofrance_api_key(), "arpege-key")
            self.assertEqual(weather.load_arome_api_key(), "arome-key")

    def test_backend_never_reads_configuration_files(self):
        # The retired file sources are deliberately absent from this contract:
        # an unconfigured service must fail closed without opening any file.
        with patch.dict(os.environ, {}, clear=True), \
                patch("builtins.open", side_effect=AssertionError("No file input")) as opened:
            self.assertIsNone(weather.load_arome_api_key())
            with self.assertRaises(weather.WeatherConfigurationError):
                weather.load_meteofrance_api_key()
            opened.assert_not_called()

    def test_selects_latest_p1d_run(self):
        run_a = "2026-09-05T12.00.00Z"
        run_b = "2026-09-05T18.00.00Z"
        coverages = weather.parse_capabilities(
            capabilities_xml(
                f"{weather.PRODUCT}___{run_a}_P1D",
                f"{weather.PRODUCT}___{run_b}_PT1H",
                f"{weather.PRODUCT}___{run_b}_P1D",
            )
        )
        selected = weather.select_latest_p1d_coverage(coverages)
        self.assertEqual(selected.run, utc("2026-09-05T18:00:00Z"))
        self.assertEqual(selected.period, "P1D")

    def test_selects_latest_complete_daily_run_when_latest_is_complete(self):
        latest = utc("2026-09-06T12:00:00Z")
        older = utc("2026-09-06T00:00:00Z")
        client = MultiRunWeatherClient(
            [
                (older, (24 * 3600, 48 * 3600, 72 * 3600, 96 * 3600)),
                (latest, (24 * 3600, 48 * 3600, 72 * 3600, 96 * 3600)),
            ]
        )

        session = weather._discover_daily_session(client, date(2026, 9, 6))

        self.assertEqual(session.coverage.run, latest)
        self.assertEqual(client.describe_calls, [client._coverage_id(latest)])
        self.assertEqual([step.label for step in session.steps], ["J+1", "J+2", "J+3", "J+4"])
        self.assertEqual([step.lead_hours for step in session.steps], [24, 48, 72, 96])

    def test_selects_previous_complete_daily_run_when_latest_is_incomplete(self):
        latest = utc("2026-09-06T12:00:00Z")
        older = utc("2026-09-06T00:00:00Z")
        client = MultiRunWeatherClient(
            [
                (older, (24 * 3600, 48 * 3600, 72 * 3600, 96 * 3600)),
                (latest, (24 * 3600,)),
            ]
        )

        session = weather._discover_daily_session(client, date(2026, 9, 6))

        self.assertEqual(session.coverage.run, older)
        self.assertEqual(
            client.describe_calls,
            [client._coverage_id(latest), client._coverage_id(older)],
        )

    def test_selects_first_complete_daily_run_after_multiple_incomplete_runs(self):
        latest = utc("2026-09-06T18:00:00Z")
        middle = utc("2026-09-06T12:00:00Z")
        complete = utc("2026-09-06T00:00:00Z")
        older = utc("2026-09-05T18:00:00Z")
        client = MultiRunWeatherClient(
            [
                (older, (24 * 3600, 48 * 3600, 72 * 3600, 96 * 3600)),
                (complete, (24 * 3600, 48 * 3600, 72 * 3600, 96 * 3600)),
                (middle, (24 * 3600, 48 * 3600)),
                (latest, (24 * 3600,)),
            ]
        )

        session = weather._discover_daily_session(client, date(2026, 9, 6))

        self.assertEqual(session.coverage.run, complete)
        self.assertEqual(
            client.describe_calls,
            [
                client._coverage_id(latest),
                client._coverage_id(middle),
                client._coverage_id(complete),
            ],
        )

    def test_mixed_session_prefers_exact_arome_per_step_and_keeps_arpege_horizons(self):
        arpege_run = utc("2026-09-06T00:00:00Z")
        arpege = FakeWeatherClient(arpege_run)
        targets = [arpege_run + timedelta(hours=value) for value in (24, 48, 72, 96)]
        arome_run = utc("2026-09-05T18:00:00Z")
        arome = ExactTimeWeatherClient([(arome_run, targets[:2])])

        result = weather.fetch_precipitation_forecast_steps(
            FakeConnection((2.45, 50.4, 2.75, 50.6)),
            client=arpege,
            arome_client=arome,
            day_provider=lambda: date(2026, 9, 6),
        )

        self.assertEqual(result["model"], "MIXED")
        self.assertIsNone(result["run"])
        self.assertEqual(
            [step["model"] for step in result["forecast_steps"]],
            ["AROME", "AROME", "ARPEGE", "ARPEGE"],
        )
        self.assertTrue(all(step["run"] for step in result["forecast_steps"]))
        self.assertEqual(len(arome.download_calls), 2)
        self.assertEqual(len(arpege.download_calls), 2)
        grid = weather.fetch_precipitations(
            FakeConnection((2.45, 50.4, 2.75, 50.6)),
            valid_time=targets[0],
            client=arpege,
            arome_client=arome,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(grid["model"], result["forecast_steps"][0]["model"])
        self.assertEqual(grid["run"], result["forecast_steps"][0]["run"])
        self.assertEqual(grid["valid_time"], result["forecast_steps"][0]["valid_time"])

    def test_missing_arome_key_preserves_four_arpege_steps(self):
        arpege = FakeWeatherClient(utc("2026-09-06T00:00:00Z"))
        with patch.dict(os.environ, {"ARPEGE_API_KEY": "arpege"}, clear=True):
            clients = weather._make_clients(arpege, None)
        self.assertIsNone(clients.arome)
        result = weather.fetch_precipitation_forecast_steps(
            FakeConnection((2.45, 50.4, 2.75, 50.6)),
            client=arpege,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(
            [step["model"] for step in result["forecast_steps"]],
            ["ARPEGE"] * 4,
        )

    def test_arome_partial_exact_coverage_falls_back_per_missing_target(self):
        run = utc("2026-09-06T00:00:00Z")
        arpege = FakeWeatherClient(run)
        arome = ExactTimeWeatherClient([(run, (run + timedelta(hours=24),))])
        result = weather.fetch_precipitation_forecast_steps(
            FakeConnection((2.45, 50.4, 2.75, 50.6)),
            client=arpege,
            arome_client=arome,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(
            [step["model"] for step in result["forecast_steps"]],
            ["AROME", "ARPEGE", "ARPEGE", "ARPEGE"],
        )
        self.assertEqual(result["model"], "MIXED")
        self.assertIsNone(result["run"])

    def test_exact_arome_selection_uses_latest_compatible_run_without_approximation(self):
        canonical_run = utc("2026-09-06T00:00:00Z")
        target = canonical_run + timedelta(hours=24)
        newest = utc("2026-09-06T06:00:00Z")
        compatible = utc("2026-09-06T00:00:00Z")
        older = utc("2026-09-05T18:00:00Z")
        client = ExactTimeWeatherClient([
            (older, (target,)),
            (compatible, (target,)),
            (newest, (target + timedelta(hours=1),)),
        ])
        canonical = weather.ForecastStep(
            0, "J+1", target, 24, "ARPEGE", canonical_run, "arpege"
        )
        selected = weather.select_exact_arome_steps(client, (canonical,))
        self.assertEqual(selected[target].run, compatible)
        self.assertEqual(selected[target].valid_time, target)
        self.assertEqual(
            client.describe_calls,
            [client.coverage_id(newest), client.coverage_id(compatible)],
        )

        no_exact = ExactTimeWeatherClient([(newest, (target + timedelta(hours=1),))])
        self.assertEqual(weather.select_exact_arome_steps(no_exact, (canonical,)), {})

    def test_arome_download_failure_uses_arpege_and_keeps_distinct_cache_keys(self):
        run = utc("2026-09-06T00:00:00Z")
        target = run + timedelta(hours=24)
        arpege = FakeWeatherClient(run)
        arome = ExactTimeWeatherClient([(run, (target,))], fail_downloads=True)
        connection = FakeConnection((2.45, 50.4, 2.75, 50.6))
        grid = weather.fetch_precipitations(
            connection,
            valid_time=target,
            client=arpege,
            arome_client=arome,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(grid["model"], "ARPEGE")
        self.assertEqual(grid["run"], weather._format_utc(run))
        self.assertEqual(len(arome.download_calls), 1)
        self.assertEqual(len(arpege.download_calls), 4)
        keys = list(weather._weather_cache)
        target_keys = [key for key in keys if weather._format_utc(target) in key]
        self.assertEqual({key[5] for key in target_keys}, {"AROME", "ARPEGE"})

    def test_arome_client_retries_502_then_succeeds_on_arome_endpoint(self):
        arpege_run = utc("2026-09-06T00:00:00Z")
        target = arpege_run + timedelta(hours=24)
        arome_run = utc("2026-09-05T18:00:00Z")
        coverage_id = ExactTimeWeatherClient.coverage_id(arome_run)
        with tempfile.TemporaryDirectory() as directory:
            tiff_path = Path(directory) / "arome.tif"
            create_test_tiff(tiff_path, [[4.5]], nodata=None)
            tiff = tiff_path.read_bytes()
        session = FakeSession([
            FakeResponse(200, capabilities_xml(coverage_id)),
            FakeResponse(
                200,
                description_xml(
                    coverage_id,
                    int((target - arome_run).total_seconds()),
                ),
            ),
            FakeResponse(502),
            FakeResponse(200, tiff),
        ])
        sleeps = []
        client = weather.MeteoFranceWcsClient(
            "arome-secret",
            session=session,
            sleep=sleeps.append,
            endpoint=weather.AROME_WCS_ENDPOINT,
            model=weather.AROME_MODEL,
        )
        grid = weather.fetch_precipitations(
            FakeConnection((2.45, 50.4, 2.75, 50.6)),
            valid_time=target,
            client=FakeWeatherClient(arpege_run),
            arome_client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(grid["model"], "AROME")
        self.assertEqual(grid["valid_time"], weather._format_utc(target))
        self.assertEqual(sleeps, [1])
        self.assertTrue(all(call[0].startswith(weather.AROME_WCS_ENDPOINT) for call in session.calls))

    def test_no_complete_daily_run_raises_data_unavailable(self):
        latest = utc("2026-09-06T12:00:00Z")
        older = utc("2026-09-06T00:00:00Z")
        client = MultiRunWeatherClient(
            [
                (older, (24 * 3600, 48 * 3600, 72 * 3600)),
                (latest, (24 * 3600,)),
            ]
        )

        with self.assertRaises(weather.WeatherDataUnavailableError) as captured:
            weather._discover_daily_session(client, date(2026, 9, 6))

        self.assertIn("4 échéances quotidiennes", str(captured.exception))
        self.assertEqual(
            client.describe_calls,
            [client._coverage_id(latest), client._coverage_id(older)],
        )

    def test_selects_first_valid_time_at_or_after_h24(self):
        run = utc("2026-09-05T18:00:00Z")
        description = weather.CoverageDescription(
            coverage_id="coverage",
            times=(
                run + timedelta(hours=12),
                run + timedelta(hours=23, minutes=59),
                run + timedelta(hours=24),
                run + timedelta(hours=25),
            ),
        )
        self.assertEqual(weather.select_p1d_valid_time(description, run), run + timedelta(hours=24))

    def test_selects_daily_steps_near_targets_and_omits_missing_j4(self):
        run = utc("2026-09-05T18:00:00Z")
        description = weather.CoverageDescription(
            coverage_id="coverage",
            times=(
                run + timedelta(hours=23),
                run + timedelta(hours=49),
                run + timedelta(hours=72),
                run + timedelta(hours=80),
            ),
        )
        steps = weather.select_daily_forecast_steps(description, run)
        self.assertEqual([step.label for step in steps], ["J+1", "J+2", "J+3"])
        self.assertEqual([step.lead_hours for step in steps], [23, 49, 72])
        self.assertEqual([step.index for step in steps], [0, 1, 2])

    def test_raster_to_cells_preserves_values_as_mm_and_nodata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weather.tif"
            create_test_tiff(path, [[1.25, -9999.0]], nodata=-9999.0)
            cells, bbox, resolution = weather.raster_to_cells(path)
        self.assertEqual(len(cells), 2)
        self.assertAlmostEqual(cells[0]["value_mm"], 1.25)
        self.assertIsNone(cells[1]["value_mm"])
        self.assertEqual(cells[0]["bounds"], [[50.9, 2.0], [51.0, 2.1]])
        self.assertEqual(bbox, (2.0, 50.9, 2.2, 51.0))
        self.assertEqual(resolution, (0.1, 0.1))

    def test_http_retries_retryable_statuses_only(self):
        session = FakeSession([
            FakeResponse(502, b"bad gateway"),
            FakeResponse(200, b"<Capabilities />"),
        ])
        sleeps = []
        client = weather.MeteoFranceWcsClient("secret-value", session=session, sleep=sleeps.append)
        self.assertEqual(client.get_capabilities(), b"<Capabilities />")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(sleeps, [1])
        for _url, kwargs in session.calls:
            self.assertEqual(kwargs["headers"], {"apikey": "secret-value"})

    def test_http_retries_stop_after_three_transient_failures_without_secret(self):
        session = FakeSession([
            FakeResponse(502),
            FakeResponse(503),
            FakeResponse(504),
        ])
        client = weather.MeteoFranceWcsClient("secret-value", session=session, sleep=lambda _delay: None)
        with self.assertRaises(weather.WeatherServiceUnavailableError) as captured:
            client.get_capabilities()
        self.assertEqual(len(session.calls), 3)
        self.assertNotIn("secret-value", str(captured.exception))
        self.assertIn("temporairement indisponible", str(captured.exception))

    def test_http_refusals_are_classified_without_retry_or_secret(self):
        cases = (
            (400, "requête fournisseur invalide", "Météo-France a refusé la demande."),
            (401, "authentification rejetée", "Météo-France a refusé la demande."),
            (403, "ressource non autorisée", "Météo-France a refusé la demande."),
            (404, "ressource absente", "Météo-France a refusé la demande."),
            (429, "limitation ou quota fournisseur", "Météo-France limite temporairement les requêtes."),
        )
        for status, reason, public_message in cases:
            with self.subTest(status=status):
                session = FakeSession([FakeResponse(status, b"private provider body")])
                client = weather.MeteoFranceWcsClient(
                    "secret-value",
                    session=session,
                    sleep=lambda _delay: None,
                )
                with self.assertLogs(weather.LOGGER, level="WARNING") as logs, \
                     self.assertRaises(weather.WeatherServiceUnavailableError) as captured:
                    client.get_capabilities()
                error = captured.exception
                diagnostics = "\n".join(logs.output)
                self.assertEqual(len(session.calls), 1)
                self.assertEqual(str(error), public_message)
                self.assertEqual(error.upstream_status, status)
                self.assertEqual(error.operation, "GetCapabilities")
                self.assertEqual(error.model, "ARPEGE")
                self.assertIn(f"HTTP {status}", diagnostics)
                self.assertIn(reason, diagnostics)
                self.assertIn("modèle=ARPEGE", diagnostics)
                self.assertIn("opération=GetCapabilities", diagnostics)
                self.assertNotIn("secret-value", diagnostics)
                self.assertNotIn("private provider body", diagnostics)
                self.assertNotIn("secret-value", str(error))

    def test_other_5xx_is_classified_without_retry(self):
        session = FakeSession([FakeResponse(500, b"private provider body")])
        client = weather.MeteoFranceWcsClient("secret-value", session=session)
        with self.assertLogs(weather.LOGGER, level="WARNING") as logs, \
             self.assertRaises(weather.WeatherServiceUnavailableError) as captured:
            client.get_capabilities()
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(captured.exception.upstream_status, 500)
        self.assertIn("indisponibilité fournisseur", "\n".join(logs.output))
        self.assertNotIn("private provider body", "\n".join(logs.output))

    def test_http_timeout_has_a_short_user_message(self):
        session = FakeSession([requests.Timeout("private timeout detail")])
        client = weather.MeteoFranceWcsClient("secret-value", session=session)
        with self.assertRaises(weather.WeatherServiceUnavailableError) as captured:
            client.get_capabilities()
        self.assertEqual(
            str(captured.exception),
            "Météo-France n’a pas répondu à temps.",
        )
        self.assertNotIn("private", str(captured.exception))

    def test_first_access_discovers_run_and_second_access_same_day_reuses_session(self):
        run = utc("2026-09-05T18:00:00Z")
        client = FakeWeatherClient(run)
        connection = FakeConnection((2.45, 50.4, 2.75, 50.6))
        first = weather.fetch_precipitation_forecast_steps(
            connection,
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        second = weather.fetch_precipitation_forecast_steps(
            connection,
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(first, second)
        self.assertEqual(client.capabilities_calls, 1)
        self.assertEqual(client.describe_calls, 1)
        self.assertEqual(len(client.download_calls), 4)
        self.assertEqual(
            [step["label"] for step in first["forecast_steps"]],
            ["J+1", "J+2", "J+3", "J+4"],
        )
        self.assertTrue(
            all(step["status"] == "ready" for step in first["forecast_steps"])
        )

    def test_new_day_rediscovers_session_and_misses_grid_cache(self):
        run = utc("2026-09-05T18:00:00Z")
        client = FakeWeatherClient(run)
        connection = FakeConnection((2.45, 50.4, 2.75, 50.6))
        weather.fetch_precipitation_forecast_steps(
            connection,
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        weather.fetch_precipitation_forecast_steps(
            connection,
            client=client,
            day_provider=lambda: date(2026, 9, 7),
        )
        self.assertEqual(client.capabilities_calls, 2)
        self.assertEqual(client.describe_calls, 2)
        self.assertEqual(len(client.download_calls), 8)

    def test_valid_time_must_belong_to_daily_session(self):
        run = utc("2026-09-05T18:00:00Z")
        client = FakeWeatherClient(run)
        connection = FakeConnection((2.45, 50.4, 2.75, 50.6))
        selected = run + timedelta(hours=48)
        payload = weather.fetch_precipitations(
            connection,
            valid_time=selected,
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(payload["valid_time"], selected.strftime("%Y-%m-%dT%H:%M:%SZ"))
        with self.assertRaises(weather.InvalidWeatherStepError):
            weather.fetch_precipitations(
                connection,
                valid_time=run + timedelta(hours=30),
                client=client,
                day_provider=lambda: date(2026, 9, 6),
            )

    def test_cache_hit_avoids_second_download_for_same_bbox_and_valid_time(self):
        run = utc("2026-09-05T18:00:00Z")
        client = FakeWeatherClient(run)
        connection = FakeConnection((2.45, 50.4, 2.75, 50.6))
        valid_time = run + timedelta(hours=24)
        first = weather.fetch_precipitations(
            connection,
            valid_time=valid_time,
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        second = weather.fetch_precipitations(
            connection,
            valid_time=valid_time,
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(first, second)
        self.assertEqual(len(client.download_calls), 4)

    def test_cache_miss_when_bbox_changes(self):
        run = utc("2026-09-05T18:00:00Z")
        client = FakeWeatherClient(run)
        weather.fetch_precipitations(
            FakeConnection((2.45, 50.4, 2.75, 50.6)),
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        weather.fetch_precipitations(
            FakeConnection((2.46, 50.4, 2.75, 50.6)),
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(len(client.download_calls), 8)

    def test_same_preload_is_scheduled_once_and_cache_contains_no_secret(self):
        deferred = DeferredExecutor()
        self.executor_patch.stop()
        self.executor_patch = patch.object(weather, "_preload_executor", deferred)
        self.executor_patch.start()
        run = utc("2026-09-05T18:00:00Z")
        client = FakeWeatherClient(run)
        connection = FakeConnection((2.45, 50.4, 2.75, 50.6))

        forecast = weather.fetch_precipitation_forecast_steps(
            connection,
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        weather.fetch_precipitation_forecast_steps(
            connection,
            client=client,
            day_provider=lambda: date(2026, 9, 6),
        )
        self.assertEqual(len(deferred.tasks), 1)
        self.assertTrue(
            all(step["status"] == "loading" for step in forecast["forecast_steps"])
        )
        deferred.run_all()
        self.assertEqual(len(client.download_calls), 4)
        self.assertNotIn("secret", repr(weather._weather_cache))
        self.assertNotIn("secret", repr(weather._weather_session))


if __name__ == "__main__":
    unittest.main()
