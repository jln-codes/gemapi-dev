from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from osgeo import gdal, osr
import requests


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "weather"
    / "arpege_precipitation_probe.py"
)
SPEC = importlib.util.spec_from_file_location("arpege_precipitation_probe", SCRIPT_PATH)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def _write_constant_raster(
    path: Path,
    value: float,
    *,
    width: int = 2,
    height: int = 2,
    geotransform: tuple[float, float, float, float, float, float] = (
        2.4,
        0.1,
        0.0,
        50.7,
        0.0,
        -0.1,
    ),
) -> None:
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(path), width, height, 1, gdal.GDT_Float64)
    dataset.SetGeoTransform(geotransform)
    spatial_reference = osr.SpatialReference()
    spatial_reference.ImportFromEPSG(4326)
    dataset.SetProjection(spatial_reference.ExportToWkt())
    band = dataset.GetRasterBand(1)
    band.Fill(value)
    band.SetMetadataItem("GRIB_ELEMENT", "RPRATE")
    band.SetMetadataItem("GRIB_UNIT", "[kg/(m^2*s)]")
    band.SetMetadataItem("GRIB_REF_TIME", "2026-09-05T18:00:00Z")
    band.SetMetadataItem("GRIB_VALID_TIME", "2026-09-05T23:00:00Z")
    band.SetMetadataItem("GRIB_FORECAST_SECONDS", "18000")
    band.SetMetadataItem("GRIB_PDS_TEMPLATE_ASSEMBLED_VALUES_0", "18000")
    band.SetMetadataItem("GRIB_PDS_TEMPLATE_ASSEMBLED_VALUES_1", "1")
    dataset = None


class SyntheticArpegeClient:
    def __init__(
        self,
        *,
        run: datetime,
        times: list[datetime],
        rainfall: dict[datetime, float],
        size: tuple[int, int] = (2, 2),
    ) -> None:
        self.run = run
        self.times = times
        self.rainfall = rainfall
        self.size = size
        self.calls: list[tuple[str, datetime]] = []

    @staticmethod
    def _coverage_id(period: str, run: datetime) -> str:
        return (
            f"{probe.PRODUCT}___"
            f"{run.strftime('%Y-%m-%dT%H.%M.%SZ')}_{period}"
        )

    def get_capabilities(self) -> bytes:
        coverages = "".join(
            f"<wcs:CoverageId>{self._coverage_id(period, self.run)}</wcs:CoverageId>"
            for period in probe.PERIODS
        )
        return (
            '<wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0">'
            f"{coverages}"
            "</wcs:Capabilities>"
        ).encode()

    def describe_coverage(self, coverage_id: str) -> bytes:
        coefficients = " ".join(
            str(int((value - self.run).total_seconds()))
            for value in self.times
        )
        return f'''<wcs:CoverageDescriptions
            xmlns:wcs="http://www.opengis.net/wcs/2.0"
            xmlns:gml="http://www.opengis.net/gml/3.2"
            xmlns:gmlrgrid="http://www.opengis.net/gml/3.3/rgrid"
            xmlns:swe="http://www.opengis.net/swe/2.0">
          <wcs:CoverageDescription>
            <gml:beginPosition>{self.times[0].strftime("%Y-%m-%dT%H:%M:%SZ")}</gml:beginPosition>
            <gml:endPosition>{self.times[-1].strftime("%Y-%m-%dT%H:%M:%SZ")}</gml:endPosition>
            <wcs:CoverageId>{coverage_id}</wcs:CoverageId>
            <gmlrgrid:GeneralGridAxis>
              <gmlrgrid:coefficients>{coefficients}</gmlrgrid:coefficients>
              <gmlrgrid:gridAxesSpanned>time</gmlrgrid:gridAxesSpanned>
            </gmlrgrid:GeneralGridAxis>
            <swe:uom code="kg m-2"/>
          </wcs:CoverageDescription>
        </wcs:CoverageDescriptions>'''.encode()

    def download_coverage(
        self,
        coverage_id: str,
        valid_time: datetime,
        _bbox: tuple[float, float, float, float],
        destination: Path,
    ) -> None:
        period = coverage_id.rsplit("_", 1)[-1]
        self.calls.append((period, valid_time))
        windows = {
            "PT1H": 1,
            "PT3H": 3,
            "PT6H": 6,
            "P1D": 24,
        }
        hours = windows[period]
        total = sum(
            self.rainfall.get(valid_time - timedelta(hours=offset), 0.0)
            for offset in range(hours)
        )
        _write_constant_raster(destination, total)


class ArpegePrecipitationProbeTest(unittest.TestCase):
    def test_explicit_env_file_supplies_the_standalone_probe_without_http(self):
        # A: synthetic file, no server; same explicit input as the CLI.
        with TemporaryDirectory(dir='audits') as folder:
            path = Path(folder)/'.env'
            path.write_text('ARPEGE_API_KEY=synthetic-file-key\n')
            with patch.dict(os.environ, {}, clear=True), patch.object(probe, 'WcsClient') as client, \
                    patch.object(probe, 'run_probe'):
                self.assertEqual(probe.main(['--env-file', str(path)]), 0)
                client.assert_called_once_with('synthetic-file-key', verbose=False)

    def _synthetic_client(self, rainfall: dict[int, float], hours: int) -> SyntheticArpegeClient:
        run = datetime(2026, 9, 5, 18, tzinfo=timezone.utc)
        times = [run + timedelta(hours=offset) for offset in range(hours + 1)]
        schedule = {run + timedelta(hours=offset): rainfall.get(offset, 0.0) for offset in range(hours + 1)}
        return SyntheticArpegeClient(run=run, times=times, rainfall=schedule)

    def test_missing_api_key_exits_cleanly(self):
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(probe, "load_tool_environment"),
            patch("sys.stderr", stderr),
        ):
            result = probe.main([])

        self.assertEqual(result, 2)
        self.assertIn(probe.API_KEY_ENV, stderr.getvalue())

    def test_http_error_does_not_expose_api_key(self):
        class Session:
            def get(self, *_args, **_kwargs):
                response = requests.Response()
                response.status_code = 401
                response.url = "https://example.invalid"
                response.raise_for_status()

        secret = "must-not-appear"
        client = probe.WcsClient(secret, session=Session())

        with self.assertRaisesRegex(probe.ProbeError, r"HTTP 401") as caught:
            client.get_capabilities()

        self.assertNotIn(secret, str(caught.exception))

    def test_getcapabilities_retries_502_then_succeeds(self):
        class Response:
            def __init__(self, status_code, content=b""):
                self.status_code = status_code
                self.content = content

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return Response(502)
                return Response(200, b"<ok/>")

        sleeps = []
        client = probe.WcsClient("secret-test", session=Session(), sleep=sleeps.append)

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            payload = client.get_capabilities()

        self.assertEqual(payload, b"<ok/>")
        self.assertEqual(client._session.calls, 2)
        self.assertEqual(sleeps, [1])
        self.assertIn("GetCapabilities : tentative 1/3, HTTP 502", stdout.getvalue())
        self.assertIn("GetCapabilities : tentative 2/3, HTTP 200", stdout.getvalue())

    def test_getcapabilities_three_502_failures_report_temporary_unavailability(self):
        class Response:
            def __init__(self, status_code):
                self.status_code = status_code
                self.content = b""

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response(502)

        sleeps = []
        client = probe.WcsClient("secret-test", session=Session(), sleep=sleeps.append)

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaisesRegex(probe.ProbeError, r"temporairement indisponible"):
                client.get_capabilities()

        self.assertEqual(client._session.calls, 3)
        self.assertEqual(sleeps, [1, 2])
        self.assertIn("GetCapabilities : tentative 3/3, HTTP 502", stdout.getvalue())

    def test_getcapabilities_401_does_not_retry(self):
        class Response:
            def __init__(self):
                self.status_code = 401
                self.content = b""

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response()

        sleeps = []
        client = probe.WcsClient("secret-test", session=Session(), sleep=sleeps.append)

        with self.assertRaisesRegex(probe.ProbeError, r"HTTP 401"):
            client.get_capabilities()

        self.assertEqual(client._session.calls, 1)
        self.assertEqual(sleeps, [])

    def test_selects_latest_run_containing_every_required_period(self):
        identifiers = []
        for run in ("2026-09-04T18.00.00Z", "2026-09-05T00.00.00Z"):
            for period in probe.PERIODS:
                if run == "2026-09-05T00.00.00Z" and period == "PT6H":
                    continue
                identifiers.append(
                    f"<wcs:CoverageId>{probe.PRODUCT}___{run}_{period}</wcs:CoverageId>"
                )
        payload = (
            '<wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0">'
            + "".join(identifiers)
            + "</wcs:Capabilities>"
        ).encode()

        selected = probe.select_latest_complete_run(probe.parse_capabilities(payload))

        self.assertEqual(set(selected), set(probe.PERIODS))
        self.assertEqual(
            next(iter(selected.values())).run,
            datetime(2026, 9, 4, 18, tzinfo=timezone.utc),
        )

    def test_description_uses_discrete_time_coefficients_and_common_time(self):
        run = datetime(2026, 9, 5, tzinfo=timezone.utc)

        def description(identifier, coefficients):
            return f'''<wcs:CoverageDescriptions
                xmlns:wcs="http://www.opengis.net/wcs/2.0"
                xmlns:gml="http://www.opengis.net/gml/3.2"
                xmlns:gmlrgrid="http://www.opengis.net/gml/3.3/rgrid"
                xmlns:swe="http://www.opengis.net/swe/2.0">
              <wcs:CoverageDescription>
                <gml:beginPosition>2026-09-05T01:00:00Z</gml:beginPosition>
                <gml:endPosition>2026-09-06T06:00:00Z</gml:endPosition>
                <wcs:CoverageId>{identifier}</wcs:CoverageId>
                <gmlrgrid:GeneralGridAxis>
                  <gmlrgrid:coefficients>{coefficients}</gmlrgrid:coefficients>
                  <gmlrgrid:gridAxesSpanned>time</gmlrgrid:gridAxesSpanned>
                </gmlrgrid:GeneralGridAxis>
                <swe:uom code="kg m-2"/>
              </wcs:CoverageDescription>
            </wcs:CoverageDescriptions>'''.encode()

        first = probe.parse_description(description("first", "3600 86400 90000"), run)
        second = probe.parse_description(description("second", "10800 86400 90000"), run)

        self.assertEqual(first.unit, "kg m-2")
        self.assertEqual(
            probe.select_common_time([first, second]),
            datetime(2026, 9, 6, tzinfo=timezone.utc),
        )

    def test_describecoverage_retries_503_then_succeeds(self):
        class Response:
            def __init__(self, status_code, content=b""):
                self.status_code = status_code
                self.content = content

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return Response(503)
                return Response(
                    200,
                    b'''<wcs:CoverageDescriptions xmlns:wcs="http://www.opengis.net/wcs/2.0">
                    <wcs:CoverageDescription>
                      <wcs:CoverageId>coverage</wcs:CoverageId>
                    </wcs:CoverageDescription>
                    </wcs:CoverageDescriptions>''',
                )

        sleeps = []
        client = probe.WcsClient("secret-test", session=Session(), sleep=sleeps.append)

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            payload = client.describe_coverage("coverage")

        self.assertIn(b"CoverageDescriptions", payload)
        self.assertEqual(client._session.calls, 2)
        self.assertEqual(sleeps, [1])
        self.assertIn("DescribeCoverage coverage : tentative 1/3, HTTP 503", stdout.getvalue())
        self.assertIn("DescribeCoverage coverage : tentative 2/3, HTTP 200", stdout.getvalue())

    def test_describecoverage_404_does_not_retry(self):
        class Response:
            def __init__(self):
                self.status_code = 404
                self.content = b""

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response()

        sleeps = []
        client = probe.WcsClient("secret-test", session=Session(), sleep=sleeps.append)

        with self.assertRaisesRegex(probe.ProbeError, r"HTTP 404"):
            client.describe_coverage("coverage")

        self.assertEqual(client._session.calls, 1)
        self.assertEqual(sleeps, [])

    def test_getcoverage_uses_repeated_unquoted_subsets(self):
        class Response:
            status_code = 200
            content = b""

            def raise_for_status(self):
                return None

            def iter_content(self, _size):
                yield b"TIFF"

        class Session:
            def get(self, url, **kwargs):
                self.url = url
                self.kwargs = kwargs
                return Response()

        session = Session()
        client = probe.WcsClient("secret-test", session=session)
        valid_time = datetime(2026, 9, 6, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            destination = Path(directory) / "sample.tif"
            client.download_coverage(
                "coverage", valid_time, probe.DEFAULT_BBOX, destination
            )

        subsets = [
            value for name, value in session.kwargs["params"] if name == "subset"
        ]
        self.assertEqual(subsets[-1], "time(2026-09-06T00:00:00Z)")
        self.assertNotIn('"', subsets[-1])
        self.assertEqual(session.kwargs["headers"], {"apikey": "secret-test"})

    def test_getcoverage_retries_502_then_succeeds(self):
        class Response:
            def __init__(self, status_code):
                self.status_code = status_code

            def iter_content(self, _size):
                yield b"TIFF"

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response(502 if self.calls == 1 else 200)

        sleeps = []
        session = Session()
        client = probe.WcsClient("secret-test", session=session, sleep=sleeps.append)

        stdout = io.StringIO()
        with TemporaryDirectory() as directory, redirect_stdout(stdout):
            client.download_coverage(
                "coverage-502-then-ok",
                datetime(2026, 9, 6, tzinfo=timezone.utc),
                probe.DEFAULT_BBOX,
                Path(directory) / "sample.tif",
            )

        self.assertEqual(session.calls, 2)
        self.assertEqual(sleeps, [1])
        self.assertIn("tentative 1/3, HTTP 502", stdout.getvalue())
        self.assertIn("tentative 2/3, HTTP 200", stdout.getvalue())

    def test_getcoverage_three_502_failures_report_product(self):
        class Response:
            status_code = 502

            def iter_content(self, _size):
                yield b""

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response()

        sleeps = []
        session = Session()
        client = probe.WcsClient("secret-test", session=session, sleep=sleeps.append)

        stdout = io.StringIO()
        with TemporaryDirectory() as directory, redirect_stdout(stdout):
            with self.assertRaisesRegex(
                probe.ProbeError, r"coverage-always-502.*HTTP 502"
            ):
                client.download_coverage(
                    "coverage-always-502",
                    datetime(2026, 9, 6, tzinfo=timezone.utc),
                    probe.DEFAULT_BBOX,
                    Path(directory) / "sample.tif",
                )

        self.assertEqual(session.calls, 3)
        self.assertEqual(sleeps, [1, 2])
        self.assertIn("tentative 3/3, HTTP 502", stdout.getvalue())

    def test_getcoverage_400_does_not_retry(self):
        class Response:
            status_code = 400

            def iter_content(self, _size):
                yield b""

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response()

        session = Session()
        client = probe.WcsClient("secret-test", session=session, sleep=lambda _delay: None)

        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(probe.ProbeError, r"HTTP 400"):
                client.download_coverage(
                    "coverage-bad-request",
                    datetime(2026, 9, 6, tzinfo=timezone.utc),
                    probe.DEFAULT_BBOX,
                    Path(directory) / "sample.tif",
                )

        self.assertEqual(session.calls, 1)

    def test_getcoverage_messages_do_not_expose_api_key(self):
        class Response:
            status_code = 502

            def iter_content(self, _size):
                yield b""

        class Session:
            def get(self, *_args, **_kwargs):
                return Response()

        secret = "must-not-appear"
        client = probe.WcsClient(
            secret,
            session=Session(),
            verbose=True,
            sleep=lambda _delay: None,
        )

        stdout = io.StringIO()
        with TemporaryDirectory() as directory, redirect_stdout(stdout):
            with self.assertRaises(probe.ProbeError) as caught:
                client.download_coverage(
                    "coverage-secret-check",
                    datetime(2026, 9, 6, tzinfo=timezone.utc),
                    probe.DEFAULT_BBOX,
                    Path(directory) / "sample.tif",
                )

        self.assertNotIn(secret, stdout.getvalue())
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn("coverageid=coverage-secret-check", stdout.getvalue())

    def test_analyzes_single_band_raster_without_numpy(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.tif"
            driver = gdal.GetDriverByName("GTiff")
            dataset = driver.Create(str(path), 3, 3, 1, gdal.GDT_Float64)
            dataset.SetGeoTransform((2.4, 0.1, 0, 50.7, 0, -0.1))
            spatial_reference = osr.SpatialReference()
            spatial_reference.ImportFromEPSG(4326)
            dataset.SetProjection(spatial_reference.ExportToWkt())
            band = dataset.GetRasterBand(1)
            band.Fill(2.5)
            band.SetMetadataItem("GRIB_ELEMENT", "RPRATE")
            band.SetMetadataItem("GRIB_UNIT", "[kg/(m^2*s)]")
            dataset = None

            summary = probe.analyze_raster(path, "PT1H", "coverage", "kg m-2")

        self.assertEqual((summary.width, summary.height), (3, 3))
        self.assertEqual(summary.crs, "EPSG:4326")
        self.assertAlmostEqual(summary.center, 2.5)
        self.assertAlmostEqual(summary.mean, 2.5)
        self.assertEqual(summary.data_type, "Float64")
        self.assertEqual(summary.band_tags["default"]["GRIB_ELEMENT"], "RPRATE")

    def test_rain_search_finds_first_wet_sample_after_dry_ones(self):
        client = self._synthetic_client({5: 0.0, 6: 0.5}, hours=6)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            report = probe.run_probe(
                client,
                probe.DEFAULT_BBOX,
                find_rain=True,
                rain_threshold=0.001,
                max_search_samples=10,
                comparison_tolerance=0.001,
            )

        self.assertEqual(report["rain_search"]["samples_checked"], 2)
        self.assertEqual(report["rain_search"]["selected_valid_time"], "2026-09-06T00:00:00Z")
        self.assertEqual(report["rain_search"]["pt1h_max"], 0.5)
        self.assertTrue(report["comparisons"]["pt3h_vs_sum_pt1h"]["compatible"])
        self.assertTrue(report["comparisons"]["pt6h_vs_sum_pt1h"]["compatible"])
        self.assertEqual(
            report["comparisons"]["p1d_vs_sum_pt1h"]["mismatch_reason"],
            "Option --verify-p1d-sum désactivée.",
        )
        self.assertEqual(
            sum(1 for period, _ in client.calls if period == "PT1H"),
            6,
        )

    def test_rain_search_stops_after_max_samples_without_rain(self):
        client = self._synthetic_client({}, hours=7)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            report = probe.run_probe(
                client,
                probe.DEFAULT_BBOX,
                find_rain=True,
                rain_threshold=0.001,
                max_search_samples=2,
                comparison_tolerance=0.001,
            )

        self.assertEqual(report["rain_search"]["samples_checked"], 2)
        self.assertIsNone(report["rain_search"]["selected_valid_time"])
        self.assertEqual(report["products"], {})
        self.assertEqual(report["comparisons"], {})
        self.assertIn("Aucune échéance pluvieuse", stdout.getvalue())

    def test_sum_comparisons_accept_rounding_tolerance_and_reject_misalignment(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            target_path = base / "target.tif"
            source_a_path = base / "source-a.tif"
            source_b_path = base / "source-b.tif"
            source_c_path = base / "source-c.tif"
            misaligned_path = base / "misaligned.tif"
            _write_constant_raster(target_path, 3.0004)
            _write_constant_raster(source_a_path, 1.0)
            _write_constant_raster(source_b_path, 1.0)
            _write_constant_raster(source_c_path, 1.0)
            _write_constant_raster(
                misaligned_path,
                1.0,
                width=3,
                height=3,
            )

            target = probe.capture_raster(target_path, "PT3H", "coverage", "kg m-2")
            source_a = probe.capture_raster(source_a_path, "PT1H", "coverage", "kg m-2")
            source_b = probe.capture_raster(source_b_path, "PT1H", "coverage", "kg m-2")
            source_c = probe.capture_raster(source_c_path, "PT1H", "coverage", "kg m-2")
            misaligned = probe.capture_raster(
                misaligned_path, "PT1H", "coverage", "kg m-2"
            )

        tolerant = probe._compare_sum(target, [source_a, source_b, source_c], 0.001)
        strict = probe._compare_sum(target, [source_a, source_b, source_c], 0.0001)
        rejected = probe._compare_sum(target, [source_a, source_b, misaligned], 0.001)

        self.assertTrue(tolerant.compatible)
        self.assertFalse(strict.compatible)
        self.assertAlmostEqual(tolerant.max_absolute_error, 0.0004, places=7)
        self.assertAlmostEqual(tolerant.mean_absolute_error, 0.0004, places=7)
        self.assertEqual(rejected.compatible, None)
        self.assertIn("alignés", rejected.mismatch_reason or "")

    def test_verify_p1d_sum_is_off_by_default(self):
        client = self._synthetic_client({5: 0.0, 6: 0.5}, hours=6)
        with redirect_stdout(io.StringIO()):
            report = probe.run_probe(
                client,
                probe.DEFAULT_BBOX,
                find_rain=True,
                rain_threshold=0.001,
                max_search_samples=10,
                comparison_tolerance=0.001,
            )

        self.assertEqual(
            report["comparisons"]["p1d_vs_sum_pt1h"]["mismatch_reason"],
            "Option --verify-p1d-sum désactivée.",
        )
        self.assertEqual(
            sum(1 for period, _ in client.calls if period == "PT1H"),
            6,
        )
        self.assertNotIn(("PT1H", client.run), client.calls)


if __name__ == "__main__":
    unittest.main()
