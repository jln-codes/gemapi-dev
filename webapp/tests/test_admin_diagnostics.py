"""Observation tests: synthetic files and injected connections, never app DSNs."""
import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import requests

from digues_app.control.auth import UserIdentity
from digues_webapp import admin_diagnostics as diag, auth, service_observations as observations
from digues_webapp.app import create_app
from digues_webapp.media_storage import FilesystemMediaStorage


def target():
    identifier = uuid4()
    return dict(id=identifier, display_name="Alpha", structure="Structure Alpha", status="active",
        schema_version=diag.BUSINESS_SCHEMA_VERSION, data_epoch=uuid4(), media_namespace=identifier,
        database_name="private_db", cluster_ref="private_cluster", connection_profile_ref="private_profile")


class DiagnosticsCollectorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runner = diag.ProbeRunner()
        self.addCleanup(self.runner.executor.shutdown)
        self.enterContext(patch.object(diag, "runner", self.runner))
        self.enterContext(patch("psycopg.connect", side_effect=AssertionError("No external PostgreSQL")))
        self.enterContext(patch.object(diag, 'active_external_checks', return_value={name: diag.result('unknown', 'synthetic', active_probe=True) for name in diag.external_probes.PROBES}))
        self.record = target()
        self.control = self.enterContext(patch.object(diag, "control_probe", return_value=diag.result("ok", "synthetic",
            _targets=[self.record], _has_more=True, _namespaces=[self.record], _namespaces_complete=True)))
        self.probes = {name: self.enterContext(patch.object(diag, name, return_value=diag.result("ok", "synthetic")))
                       for name in ("provisioning_probe", "business_probe", "media_probe", "basemap_probe", "caches_probe")}

    async def test_sections_are_safe_and_explicitly_projected(self):
        data = await diag.collect("test", "build", limit=1)
        self.assertEqual(data["control_plane"]["status"], "ok")
        self.assertEqual(data["business_databases"]["next_after"], str(self.record["id"]))
        self.assertEqual(data["business_databases"]["items"][0]["display_name"], "Alpha")
        serialized = json.dumps(data, default=str)
        for forbidden in ("private_db", "private_cluster", "private_profile", "_targets", "media_namespace", "password"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(data["backups"]["status"], "not_configured")
        self.assertIsNone(data["application"]["available_version"])

    async def test_disk_summary_is_projected_once_without_losing_media_checks(self):
        self.probes['media_probe'].return_value = diag.result('degraded', 'Namespaces contrôlés',
            issues=[{'code': 'namespace_invalid'}], disk={'filesystem_id': 'fastapi-device:42',
            'total_bytes': 1000, 'used_bytes': 800, 'free_bytes': 150,
            'reserved_bytes': 50, 'status': 'ok'})
        data = await diag.collect('test', 'build')
        self.assertNotIn('disk', data['media'])
        self.assertEqual(data['media']['issues'], [{'code': 'namespace_invalid'}])
        self.assertEqual(data['storage']['filesystem']['available_bytes'], 150)
        self.assertEqual(data['storage']['categories'], [])
        self.assertEqual(data['storage']['measured_at'], data['media']['checked_at'])

    async def test_control_failure_preserves_independent_checks(self):
        self.control.side_effect = RuntimeError("postgresql://secret@remote/traceback")
        data = await diag.collect("test", "build")
        self.assertEqual(data["control_plane"]["status"], "error")
        self.assertEqual(data["application"]["status"], "ok")
        self.assertEqual(data["basemap"]["status"], "ok")
        self.assertEqual(data["media"]["status"], "ok")
        self.probes["business_probe"].assert_not_called()
        self.assertNotIn("postgresql://secret", json.dumps(data, default=str))

    async def test_host_snapshot_is_projected_without_inventory_or_losing_media_health(self):
        # Level B: real JSON publication -> diagnostics projection; isolated files,
        # injected control/extent maps. API authorization is exercised below.
        from digues_app.storage_host import collect, publish, space
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]/'audits') as folder:
            root=Path(folder);data_dir=root/'files';data_dir.mkdir()
            (data_dir/'data').write_bytes(b'fixture')
            installation=str(uuid4());output=root/'snapshot.json'
            host=collect(dict(schema_version=1,installation_id=installation,filesystem_path=str(root),
                roots=[dict(path=str(data_dir),category='system')]),mapper=lambda fd:[(4096,8192)])
            publish(output,host)
            self.control.return_value['_instance_id']=installation
            self.probes['media_probe'].return_value=diag.result('degraded','Namespaces contrôlés',
                issues=[{'code':'namespace_invalid'}],disk=dict(filesystem_id='local-fixture',
                    filesystem_fsid=space(root)['fsid'],total_bytes=1000,used_bytes=800,free_bytes=150,reserved_bytes=50,status='ok'))
            with patch.dict(os.environ,{'STORAGE_HOST_SNAPSHOT_FILE':str(output),'STORAGE_HOST_FILESYSTEM_ID':''}),patch('digues_app.storage_host.collect',side_effect=AssertionError('No inventory from HTTP')):
                observed=await diag.collect('test','build')
            self.assertEqual(observed['storage']['snapshot_state'],'ready')
            self.assertEqual(observed['storage']['categories'][0]['category'],'system')
            self.assertEqual(observed['media']['issues'],[{'code':'namespace_invalid'}])
            self.assertNotIn('filesystem_fsid',json.dumps(observed,default=str))
            self.assertNotIn(str(root),json.dumps(observed,default=str))
            with patch.dict(os.environ,{'STORAGE_HOST_SNAPSHOT_FILE':str(root/'absent.json')}):
                degraded=await diag.collect('test','build')
            self.assertEqual(degraded['storage']['snapshot_state'],'absent')
            self.assertEqual(degraded['storage']['categories'],[])
            self.probes['media_probe'].assert_called_once()

    async def test_one_database_unavailable_does_not_hide_other_sections(self):
        self.probes["business_probe"].side_effect = OSError("private path password")
        data = await diag.collect("test", "build")
        self.assertEqual(data["business_databases"]["items"][0]["status"], "error")
        self.assertEqual(data["business_databases"]["status"], "degraded")
        self.assertEqual(data["control_plane"]["status"], "ok")

    async def test_non_active_database_is_not_probed(self):
        self.record.update(status='disabled')
        data = await diag.collect('test', 'build')
        item = data['business_databases']['items'][0]
        self.assertEqual(item['status'], 'unknown')
        self.assertEqual(item['expected_schema_version'], diag.BUSINESS_SCHEMA_VERSION)
        self.probes['business_probe'].assert_not_called()
        self.record.update(status='active')
        await diag.collect('test', 'build')
        self.assertEqual(self.probes['business_probe'].call_count, 1)

    async def test_media_and_basemap_fail_independently(self):
        self.probes["media_probe"].side_effect = PermissionError("/private/credentials")
        self.probes["basemap_probe"].side_effect = requests.Timeout("secret token")
        data = await diag.collect("test", "build")
        self.assertEqual(data["media"]["status"], "error")
        self.assertEqual(data["basemap"]["status"], "error")
        self.assertEqual(data["control_plane"]["status"], "ok")
        self.assertNotIn("secret token", json.dumps(data, default=str))

    async def test_global_deadline_and_single_flight_prevent_unbounded_work(self):
        release = Event()
        entered = Event()
        def slow_probe():
            entered.set()
            release.wait(5)
            return diag.result("ok", "released")
        calls = Mock(side_effect=slow_probe)
        try:
            # Establish an in-flight probe before measuring the HTTP deadline.
            # Under load the event loop may otherwise start all 20 callers only
            # after their deadline, correctly submitting no probe at all.
            self.runner.submit("slow", calls)
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            started = time.monotonic()
            results = await asyncio.gather(*(self.runner.run("slow", calls, started + .05) for _ in range(20)))
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(all(r["code"] == "timeout" for r in results))
            calls.assert_called_once()
            # Bounded capacity, no unbounded executor queue even after timeouts.
            for n in range(3):
                self.runner.submit(n, calls)
            busy, future = self.runner.submit("fifth", calls)
            self.assertEqual(busy["code"], "busy")
            self.assertIsNone(future)
        finally:
            release.set()

    async def test_collector_deadline_returns_other_sections(self):
        release = Event()
        self.probes["basemap_probe"].side_effect = lambda: (release.wait(5), diag.result("ok", "late"))[1]
        run = self.runner.run
        async def controlled_probe(key, operation, deadline):
            # These injected probes do no I/O. Keep their completion independent
            # of thread scheduling; only the deliberately blocked probe exercises
            # the real deadline. ProbeRunner concurrency is tested separately.
            if key == "basemap":
                return await run(key, operation, deadline)
            return operation()
        try:
            with patch.object(diag, "DEADLINE_SECONDS", .05), patch.object(self.runner, "run", side_effect=controlled_probe):
                started = time.monotonic()
                data = await diag.collect("test", "build")
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(data["basemap"]["code"], "timeout")
            self.assertEqual(data["control_plane"]["status"], "ok")
        finally:
            release.set()

    async def test_refresh_reuses_probes(self):
        await diag.collect("test", "build")
        await diag.collect("test", "build")
        self.control.assert_called_once()
        self.probes["basemap_probe"].assert_called_once()


class DiagnosticsHTTPTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1", "AUTH_ORIGIN": "https://business.test",
            "ADMIN_ORIGIN": "https://admin.test", "ADMIN_ALLOW_INSECURE_LOCALHOST": "false"}))
        self.app = create_app()
        self.user = UserIdentity(uuid4(), "operator", "Operator", "active", True)
        self.app.dependency_overrides[auth.get_current_user] = lambda: self.user
        self.client = self.enterContext(TestClient(self.app, base_url="https://admin.test", follow_redirects=False))
        self.collect = self.enterContext(patch.object(diag, "collect", return_value={"contract_version": 1}))
        self.enterContext(patch("psycopg.connect", side_effect=AssertionError("No PostgreSQL")))

    def test_host_session_superadmin_no_store(self):
        response = self.client.get("/api/admin/diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.collect.reset_mock()
        self.assertEqual(self.client.get("https://business.test/api/admin/diagnostics").status_code, 403)
        self.user = replace(self.user, is_superadmin=False)
        self.assertEqual(self.client.get("/api/admin/diagnostics").status_code, 403)
        self.user = None
        self.assertEqual(self.client.get("/api/admin/diagnostics").status_code, 401)
        self.collect.assert_not_called()

    def test_pagination_is_validated(self):
        for query in ("limit=6", "limit=0", "after=arbitrary"):
            self.assertEqual(self.client.get("/api/admin/diagnostics?" + query).status_code, 422)
        identifier = uuid4()
        self.assertEqual(self.client.get(f"/api/admin/diagnostics?limit=2&after={identifier}").status_code, 200)
        self.assertEqual(self.collect.call_args.kwargs, {"after": identifier, "limit": 2})

    def test_clean_routes_share_guarded_document_and_cannot_bypass(self):
        for path in ("/", "/bases", "/jobs", "/diagnostics"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('data-interface="admin"', response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
        for path in ("/diagnostics", "/jobs", "/bases", "/admin-shell"):
            self.assertEqual(self.client.get("https://business.test" + path).status_code, 403)
        self.assertEqual(self.client.get("/admin-shell").status_code, 403)
        self.user = None
        self.assertEqual(self.client.get("/diagnostics").headers["location"], "/login?next=/diagnostics")

    def test_identity_control_outage_never_bypasses_session_to_run_probes(self):
        from digues_app.control.database import ControlDatabaseError
        self.app.dependency_overrides.clear()
        self.client.cookies.set("__Host-digues_session", "synthetic-token")
        with patch.object(auth, "open_control_connection", side_effect=ControlDatabaseError("Contrôle indisponible.")):
            response = self.client.get("/api/admin/diagnostics")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.collect.assert_not_called()


class PassiveProbeTest(unittest.TestCase):
    def test_metadata_never_echoes_arbitrary_environment_or_revision(self):
        with patch.dict(os.environ, {"DIGUES_ENVIRONMENT": "private-environment-secret", "DIGUES_BUILD_REVISION": "private-build-secret"}):
            value = diag.application_probe("test", "build")
        self.assertEqual(value["environment"], "unknown")
        self.assertIsNone(value["revision"])
        self.assertNotIn("secret", str(value))

    def test_cache_inventory_is_passive_bounded_and_does_not_return_territory(self):
        from digues_webapp import weather, hydrometry
        entries = {(weather._cache_date().isoformat(), f"private-territory-{n}"):
                   weather.GridCacheEntry(status="error", error=RuntimeError("private-error")) for n in range(501)}
        with patch.object(weather, "_weather_cache", entries), patch.object(hydrometry, "_discovery_cache", {}), \
                patch.object(weather, "_make_clients", side_effect=AssertionError("No provider call")):
            value = diag.caches_probe()
        self.assertEqual(value["items"][0]["sampled"], 500)
        self.assertEqual(value["items"][0]["entries"], 501)
        self.assertEqual(value["items"][0]["status"], "degraded")
        self.assertEqual(value["items"][1]["status"], "unknown")
        self.assertNotIn("private-", str(value))

    def test_missing_media_root_is_not_created(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "not-created"
            with patch.dict(os.environ, {"MEDIA_ROOT": str(root), "MEDIA_BACKEND": "filesystem"}):
                with self.assertRaises(OSError):
                    diag.media_probe({})
            self.assertFalse(root.exists())

    def test_normal_provider_calls_record_success_and_timeout_without_new_probes(self):
        from digues_webapp.weather import MeteoFranceWcsClient
        from digues_webapp.vigicrues import VigicruesClient, HUBEAU_STATIONS_URL
        with patch.object(observations, "_observations", {}):
            session = Mock(); session.get.side_effect = requests.Timeout("SECRET_SENTINEL")
            with self.assertRaises(Exception):
                MeteoFranceWcsClient("SECRET_SENTINEL", session=session).get_capabilities()
            self.assertEqual(observations.snapshot()["meteofrance"]["last_error_code"], "timeout")
            response = Mock(url=HUBEAU_STATIONS_URL, status_code=200)
            response.iter_content.return_value = [b'{"data":[]}']
            session.get.side_effect = None; session.get.return_value = response
            VigicruesClient(session=session)._get_json(HUBEAU_STATIONS_URL, allowed_urls={HUBEAU_STATIONS_URL})
            self.assertEqual(observations.snapshot()["hubeau"]["status"], "ok")
            self.assertNotIn("SECRET_SENTINEL", str(observations.snapshot()))

    def test_database_uses_only_server_readonly_profile_and_no_business_tables(self):
        record = target()
        conn = Mock()
        conn.cursor.return_value = nullcontext(Mock())
        conn.execute.return_value.fetchone.side_effect = [(record["database_name"], "on"), (1234,), (None,)]
        with patch.object(diag, "resolve_profile", return_value={}) as profile, \
                patch.object(diag, "technical_connection", return_value=nullcontext(conn)), \
                patch.object(diag, "validate_sql_role_security") as validate:
            value = diag.business_probe(record)
        validate.assert_called_once()
        self.assertEqual(value["size_bytes"], 1234)
        self.assertIsNone(value["observed_schema_version"])
        self.assertEqual(value['status'], 'degraded')
        self.assertEqual(profile.call_args.kwargs['kind'], 'sql_readonly')
        for call in conn.execute.call_args_list:
            self.assertNotIn("public.", call.args[0])
        conn.execute.return_value.fetchone.side_effect = [("other", "on")]
        with patch.object(diag, "resolve_profile", return_value={}), patch.object(diag, "technical_connection", return_value=nullcontext(conn)):
            with self.assertRaises(ValueError):
                diag.business_probe(record)

    def test_current_business_metadata_is_checked_against_identity_and_catalogue(self):
        record = target()
        record['schema_version'] = diag.BUSINESS_SCHEMA_VERSION
        conn = Mock()
        conn.cursor.return_value = nullcontext(Mock())
        for identity, expected in ((record['id'], 'ok'), (uuid4(), 'degraded')):
            conn.execute.return_value.fetchone.side_effect = [(record['database_name'], 'on'), (1234,)]
            with patch.object(diag, 'resolve_profile', return_value={}), \
                    patch.object(diag, 'technical_connection', return_value=nullcontext(conn)), \
                    patch.object(diag, 'validate_sql_role_security'), \
                    patch.object(diag, 'read_business_metadata', return_value=(identity, diag.BUSINESS_SCHEMA_REVISION, diag.BUSINESS_SCHEMA_VERSION, None)):
                self.assertEqual(diag.business_probe(record)['status'], expected)

        conn.execute.return_value.fetchone.side_effect = [
            (record['database_name'], 'on'),
            (1234,),
        ]
        with patch.object(diag, 'resolve_profile', return_value={}), \
                patch.object(diag, 'technical_connection', return_value=nullcontext(conn)), \
                patch.object(diag, 'validate_sql_role_security'), \
                patch.object(
                    diag,
                    'read_business_metadata',
                    return_value=(record['id'], 1, 'sirs-1', None),
                ):
            value = diag.business_probe(record)
        self.assertEqual(value['status'], 'degraded')
        self.assertEqual(value['schema_check'], 'outdated_or_inconsistent')

    def test_connection_bounds_and_readonly_are_forced_without_app_fallback(self):
        with patch("psycopg.connect") as connect:
            with diag.technical_connection({"dbname": "explicit", "options": "unsafe", "connect_timeout": 60}):
                pass
        args = connect.call_args.kwargs
        self.assertEqual(args["dbname"], "explicit")
        self.assertEqual(args["connect_timeout"], 2)
        self.assertIn("default_transaction_read_only=on", args["options"])
        self.assertIn("statement_timeout=750ms", args["options"])

    def test_media_is_bounded_passive_and_detects_missing_orphan_old_and_symlink(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"MEDIA_ROOT": temporary, "MEDIA_BACKEND": "filesystem"}):
            a, b = target(), target()
            storage = FilesystemMediaStorage(Path(temporary) / str(a["id"]), business_database_id=a["id"], media_namespace=a["media_namespace"])
            storage.prepare_namespace()
            orphan = Path(temporary) / str(uuid4()); orphan.mkdir()
            incoming = storage.root / "incoming" / "old"; incoming.touch(); os.utime(incoming, (1, 1))
            before = sorted(str(p.relative_to(temporary)) for p in Path(temporary).rglob("*"))
            catalogue = {"_namespaces": [a, b], "_targets": [a, b], "_namespaces_complete": True}
            data = diag.media_probe(catalogue)
            codes = {i["code"] for i in data["issues"]}
            self.assertIn("namespace_without_catalogue", codes)
            self.assertIn("active_namespace_missing_or_unsafe", codes)
            self.assertTrue(any(i["old_entries"] for i in data["staging"]))
            self.assertEqual(before, sorted(str(p.relative_to(temporary)) for p in Path(temporary).rglob("*")))
            self.assertNotIn(temporary, json.dumps(data))
            (Path(temporary) / str(b["id"])).symlink_to(storage.root, target_is_directory=True)
            self.assertIn("namespace_invalid", {i["code"] for i in diag.media_probe(catalogue)["issues"]})
            with patch.object(diag, "MEDIA_SCAN_LIMIT", 1):
                self.assertTrue(diag.media_probe(catalogue)["truncated"])

    def test_unknown_external_service_and_timeout_are_not_ok_and_never_echo_error(self):
        with patch.object(observations, "_observations", {}):
            self.assertEqual(observations.snapshot()["hubeau"]["status"], "unknown")
            with self.assertRaises(requests.Timeout), observations.observe("meteofrance"):
                raise requests.Timeout("secret apikey /private/worker.json")
            value = observations.snapshot()["meteofrance"]
            self.assertEqual(value["status"], "error")
            self.assertEqual(value["last_error_code"], "timeout")
            self.assertNotIn("secret", json.dumps(value))
            with observations.observe("hubeau"):
                pass
            self.assertEqual(observations.snapshot()["hubeau"]["status"], "ok")

    def test_basemap_is_internal_bounded_no_redirect_or_tile_request(self):
        responses = []
        for body in (b"ok\n", b'{"tilejson":"3.0.0","tiles":["https://do-not-follow.invalid/secret"]}'):
            response = Mock(status_code=200, headers={"X-Process-RSS-MiB": "40"}, raw=BytesIO(body))
            responses.append(nullcontext(response))
        session = Mock(); session.get.side_effect = responses
        with patch.dict(os.environ, {"BASEMAP_RASTER_URL": "http://internal.test:8788"}), \
                patch.object(diag.requests, "Session", return_value=nullcontext(session)):
            data = diag.basemap_probe()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(session.trust_env)
        self.assertEqual([c.args[0] for c in session.get.call_args_list],
            ["http://internal.test:8788/health", "http://internal.test:8788/basemap/plan-ign.json"])
        for call in session.get.call_args_list:
            self.assertEqual(call.kwargs["timeout"], (1, 1))
            self.assertFalse(call.kwargs["allow_redirects"])
        self.assertNotIn("secret", str(data))
