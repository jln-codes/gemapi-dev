from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from scripts.weather import arome_precipitation_probe as probe


shared = probe.shared
RUN = datetime(2026, 10, 1, tzinfo=timezone.utc)


def coverage(run=RUN, period="P1D"):
    return shared.Coverage(f"{shared.PRODUCT}___{run:%Y-%m-%dT%H.%M.%SZ}_{period}", run, period)


def description(item, leads):
    times = tuple(item.run + timedelta(hours=value) for value in leads)
    return shared.CoverageDescription(item.coverage_id, times, "kg m-2", times[0], times[-1])


class AromeProbeTests(unittest.TestCase):
    def test_explicit_file_preserves_exported_arpege_and_distinct_arome_key(self):
        # A: source file + environment precedence, fake WCS; no provider access.
        with TemporaryDirectory(dir='audits') as folder:
            path = Path(folder)/'.env'
            path.write_text('ARPEGE_API_KEY=file-arpege\nAROME_API_KEY=file-arome\n')
            with patch.dict('os.environ', {'ARPEGE_API_KEY': 'exported-arpege'}, clear=True), \
                    patch.object(shared, 'WcsClient') as client, patch.object(probe, 'run_probe'), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(['--env-file', str(path), '--output-dir', str(Path(folder)/'out')]), 0)
                self.assertEqual(client.call_args_list[0].args[0], 'file-arome')
                self.assertEqual(client.call_args_list[1].args[0], 'exported-arpege')

    def test_dedicated_arome_key_preserves_arpege_key(self):
        with TemporaryDirectory() as directory:
            with patch.object(shared, "load_tool_environment"), patch.dict("os.environ", {"AROME_API_KEY": "arome-secret", shared.API_KEY_ENV: "arpege-secret"}, clear=True), patch.object(shared, "WcsClient") as clients, patch.object(probe, "run_probe"), redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(["--output-dir", str(Path(directory) / "evidence")]), 0)
            self.assertEqual(clients.call_args_list[0].args, ("arome-secret",))
            self.assertEqual(clients.call_args_list[1].args, ("arpege-secret",))

    def test_inventory_preserves_gaps_and_all_discrete_times(self):
        item = coverage()
        result = probe.time_inventory(item, description(item, [24, 25, 27, 42]))
        self.assertEqual(result["step_hours"], [1, 2, 15])
        self.assertEqual(result["lead_hours"], [24, 25, 27, 42])
        self.assertEqual(result["max_lead_hours"], 42)
        self.assertEqual(result["valid_times"][-1], "2026-10-02T18:00:00Z")

    def test_inventory_does_not_hide_unknown_period(self):
        known = coverage().coverage_id
        unknown = known.rsplit("_", 1)[0] + "_PT90M"
        xml = f'<Capabilities><CoverageId>{known}</CoverageId><CoverageId>{unknown}</CoverageId><CoverageId>TEMPERATURE</CoverageId></Capabilities>'.encode()
        self.assertEqual(probe.precipitation_ids(xml), sorted([known, unknown]))
        self.assertEqual([item.coverage_id for item in shared.parse_capabilities(xml)], [known])

    def test_j2_at_42h_is_within_tolerance_but_not_exact(self):
        item = coverage()
        steps = probe.timeline(item, description(item, range(24, 43)))
        self.assertTrue(steps[0]["exact_available"])
        self.assertFalse(steps[1]["exact_available"])
        self.assertEqual(steps[1]["selected_within_6h"], RUN + timedelta(hours=42))
        self.assertEqual(steps[1]["offset_hours"], -6)
        self.assertIsNone(steps[2]["selected_within_6h"])
        self.assertIsNone(steps[3]["selected_within_6h"])

    def test_j2_absent_when_horizon_is_41h(self):
        item = coverage()
        self.assertIsNone(probe.timeline(item, description(item, range(24, 42)))[1]["selected_within_6h"])

    def test_j2_exact_at_48h_and_all_nearby_times_reported(self):
        item = coverage()
        steps = probe.timeline(item, description(item, range(24, 52)))
        self.assertTrue(steps[1]["exact_available"])
        self.assertEqual(steps[1]["offset_hours"], 0)
        self.assertEqual(len(steps[1]["times_within_6h"]), 10)

    def test_common_timeline_uses_anchor_valid_times_not_model_leads(self):
        item = coverage(RUN + timedelta(hours=6))
        steps = probe.timeline(item, description(item, range(24, 43)), anchor=RUN)
        self.assertFalse(steps[0]["exact_available"])
        self.assertEqual(steps[0]["offset_hours"], 6)
        self.assertTrue(steps[1]["exact_available"])
        self.assertEqual(steps[1]["selected_within_6h"], RUN + timedelta(hours=48))

    def test_accumulation_prefers_after_24h_and_requires_contiguous_history(self):
        item = coverage()
        hourly = coverage(period="PT1H")
        daily = description(item, [24, 25, 26, 27])
        self.assertEqual(probe.accumulation_sample(item, daily, description(hourly, range(1, 28))), RUN + timedelta(hours=25))
        # Missing H+2 invalidates the 24-hour windows ending at H+24/H+25.
        self.assertEqual(probe.accumulation_sample(item, daily, description(hourly, range(3, 28))), RUN + timedelta(hours=26))
        with self.assertRaises(shared.ProbeError):
            probe.accumulation_sample(item, daily, description(hourly, [1, 24, 25, 26, 27]))

    def test_completeness_rejects_truncated_run_of_same_cycle(self):
        old = RUN - timedelta(days=1)
        items = [coverage(run, period) for run in (old, RUN) for period in shared.PERIODS]
        descriptions = {coverage(old).coverage_id: description(coverage(old), range(24, 52)), coverage().coverage_id: description(coverage(), range(24, 31))}
        self.assertEqual(probe.select_complete_run(items, descriptions)["P1D"].run, old)

    def test_completeness_does_not_require_48h_from_short_cycle(self):
        short = RUN + timedelta(hours=6)
        items = [coverage(run, period) for run in (RUN, short) for period in shared.PERIODS]
        descriptions = {coverage().coverage_id: description(coverage(), range(24, 52)), coverage(short).coverage_id: description(coverage(short), range(24, 43))}
        self.assertEqual(probe.select_complete_run(items, descriptions)["P1D"].run, short)

    def test_missing_period_or_missing_description_cannot_be_complete(self):
        for periods, descriptions in [(("P1D",), {coverage().coverage_id: description(coverage(), [24, 48])}), (shared.PERIODS, {})]:
            with self.subTest(periods=periods), self.assertRaises(shared.ProbeError):
                probe.select_complete_run([coverage(period=period) for period in periods], descriptions)

    def test_custom_endpoint_keeps_apikey_subsets_and_retry_contract(self):
        class Response:
            def __init__(self, status):
                self.status_code = status
                self.content = b"<xml/>"

            def iter_content(self, _size):
                yield b"TIFF"

        for status in (502, 503, 504):
            for operation in ("GetCapabilities", "DescribeCoverage", "GetCoverage"):
                with self.subTest(status=status, operation=operation):
                    session = Mock()
                    session.get.side_effect = [Response(status), Response(status), Response(200)]
                    sleeps = []
                    client = shared.WcsClient("fake-secret", endpoint=probe.WCS_ENDPOINT, session=session, sleep=sleeps.append)
                    with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as stdout:
                        if operation == "GetCapabilities":
                            client.get_capabilities()
                        elif operation == "DescribeCoverage":
                            client.describe_coverage("coverage")
                        else:
                            client.download_coverage("coverage", RUN, shared.DEFAULT_BBOX, Path(directory) / "raster.tif")
                    self.assertEqual(sleeps, [1, 2])
                    self.assertEqual(session.get.call_args.args[0], probe.WCS_ENDPOINT + "/" + operation)
                    self.assertEqual(session.get.call_args.kwargs["headers"], {"apikey": "fake-secret"})
                    self.assertNotIn("fake-secret", stdout.getvalue())

    def test_forbidden_is_not_retried_and_failure_report_survives(self):
        session = Mock()
        session.get.return_value.status_code = 403
        sleeps = []
        client = shared.WcsClient("fake-secret", endpoint=probe.WCS_ENDPOINT, session=session, sleep=sleeps.append)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            with patch.object(shared, "load_tool_environment"), patch.dict("os.environ", {shared.API_KEY_ENV: "fake-secret"}), patch.object(shared, "WcsClient", return_value=client), redirect_stdout(io.StringIO()):
                code = probe.main(["--output-dir", str(output)])
            report_text = (output / "report.json").read_text()
            report = json.loads(report_text)
        self.assertEqual(code, 1)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["rasters"], [])
        self.assertIn("HTTP 403", report["errors"][0]["error"])
        self.assertNotIn("fake-secret", report_text)


if __name__ == "__main__":
    unittest.main()
