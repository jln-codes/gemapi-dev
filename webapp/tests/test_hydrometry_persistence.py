from datetime import date
import json
from threading import Lock, Thread
import time
import unittest
from unittest.mock import patch

from digues_webapp.hydrometry import (
    DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION,
    HydrometryServiceUnavailableError,
    _acquire_discovery_lock,
    _discovery_cache_key,
    _discovery_identity,
    _write_persistent_discovery,
    clear_hydrometry_caches,
    deserialize_discovery_cache_payload,
    discovery_payload_size_bytes,
    fetch_hydrometric_discovery,
    serialize_discovery_cache_payload,
)


REFERENCE_DATE = date(2026, 10, 2)


def territory(maximum=3):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"code": "T", "nom": "Territoire"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [1, 49], [maximum, 49], [maximum, 51], [1, 51], [1, 49],
        ]]},
    }]}


def discovery_payload(value, *, complete=True, activity_errors=None, rich=False):
    identity = _discovery_identity(value, REFERENCE_DATE)
    proof = {
        "selection_state": "selected",
        "direction": "upstream",
        "topology_status": "directed",
        "hydraulic_continuity": "not_assessed",
        "crossing": {"crossing_event_id": "crossing:test"},
        "site_attachment": {"reference_edge_id": "edge:test"},
        "traversed_edge_count": 2,
        "hydrographic_distance_m": 100.0,
        "path_fingerprint": "path:test",
        "conditional_connection_ids": [],
    }
    relation = {
        "territory": {
            "id": "T",
            "fingerprint": identity["territory_fingerprint"],
        },
        "role": "upstream",
        "selection": {
            "state": "selected",
            "method": "first_active_upstream",
        },
        "topology_status": "directed",
        "hydraulic_continuity": "not_assessed",
        "proof_summary": {
            "count": 1,
            "selected_count": 1,
            "pending_review_count": 0,
            "conditional_count": 0,
            "conditional_connection_ids": [],
        },
        "proofs": [proof],
        "conditional_connections": [],
        "provenance": {"source": "BD TOPAGE", "dataset_version": "2026"},
    }
    site = {
        "code_site": "SITE",
        "name": "Site test",
        "stations": [{
            "code_station": "S-H",
            "available_quantities": ["H"],
            "activity": {"H": {"source_time": "2026-10-02T06:00:00Z"}},
        }],
        "territory_relations": [relation],
    }
    pending = []
    if rich:
        site["stations"].append({
            "code_station": "S-Q",
            "available_quantities": ["Q"],
            "activity": {"Q": {"source_time": "2026-10-02T06:05:00Z"}},
        })
        site["series_resolution"] = {
            "height": {"status": "selected_single_active"},
            "discharge": {"status": "multiple_unresolved"},
        }
        conditional = {
            "connection_id": "regulated",
            "hydraulic_continuity": "conditional",
        }
        relation["proofs"][0]["hydraulic_continuity"] = "conditional"
        relation["hydraulic_continuity"] = "conditional"
        relation["proofs"][0]["conditional_connection_ids"] = ["regulated"]
        relation["conditional_connections"] = [conditional]
        relation["proof_summary"]["conditional_count"] = 1
        relation["proof_summary"]["conditional_connection_ids"] = ["regulated"]
        pending.append({
            "scope": "proofs",
            "code_site": "SITE",
            "reason": "preuve supplémentaire à examiner",
            "pending_proofs": [],
        })
    return {
        "kind": "hydrometric_discovery",
        "generated_at": "2026-10-02T08:00:00Z",
        "identity": identity,
        "territory": {"id": "T", "fingerprint": identity["territory_fingerprint"]},
        "source_extract": {"complete_for_traversed_branches": complete},
        "counts": {"selected_sites": 1, "pending_review": len(pending)},
        "sites": [site],
        "pending_review": pending,
        "diagnostics": {"activity": activity_errors or []},
        "provenance": {"network": {"provider": "BD TOPAGE", "version": "2026"}},
    }


class Result:
    def __init__(self, row=None, rowcount=0):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class PersistentConnection:
    def __init__(self):
        self.rows = {}
        self.locks = {}
        self.guard = Lock()
        self.read_count = 0
        self.write_count = 0
        self.lock_count = 0
        self.read_error = False
        self.write_error = False

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        key = params[0] if params else None
        if normalized.startswith("SELECT payload_schema_version"):
            self.read_count += 1
            if self.read_error:
                raise RuntimeError("read unavailable")
            return Result(self.rows.get(key))
        if normalized.startswith("SELECT pg_try_advisory_lock"):
            with self.guard:
                lock = self.locks.setdefault(key, Lock())
            acquired = lock.acquire(blocking=False)
            if acquired:
                self.lock_count += 1
            return Result((acquired,))
        if normalized.startswith("SELECT pg_advisory_unlock"):
            self.locks[key].release()
            return Result((True,))
        if normalized.startswith("INSERT INTO technique.cache_decouverte_hydrometrie"):
            if self.write_error:
                raise RuntimeError("write unavailable")
            completeness = params[9]
            existing = self.rows.get(key)
            if (
                existing is not None
                and existing[0] == params[8]
                and existing[1] == "complete"
                and completeness != "complete"
            ):
                return Result(rowcount=0)
            self.write_count += 1
            self.rows[key] = (params[8], completeness, json.loads(params[10]))
            return Result(rowcount=1)
        raise AssertionError(normalized)


class DiscoveryPersistenceTest(unittest.TestCase):
    def setUp(self):
        clear_hydrometry_caches()
        self.territory = territory()
        self.connection = PersistentConnection()

    def fetch(self, builder):
        with (
            patch(
                "digues_webapp.hydrometry.fetch_territoire_administratif",
                return_value=self.territory,
            ),
            patch(
                "digues_webapp.hydrometry.build_daily_discovery_selection",
                side_effect=builder,
            ) as build,
        ):
            payload = fetch_hydrometric_discovery(
                self.connection,
                day_provider=lambda: REFERENCE_DATE,
            )
        return payload, build

    def test_memory_hit_does_not_read_postgresql_again(self):
        payload = discovery_payload(self.territory)
        key = _discovery_cache_key(self.territory, REFERENCE_DATE)
        _write_persistent_discovery(self.connection, key, payload)
        first, build = self.fetch(lambda *_args, **_kwargs: self.fail("rebuild"))
        reads = self.connection.read_count
        second, _ = self.fetch(lambda *_args, **_kwargs: self.fail("rebuild"))
        self.assertEqual(first, second)
        self.assertEqual(self.connection.read_count, reads)
        build.assert_not_called()

    def test_persistent_hit_survives_memory_restart_without_rebuild(self):
        payload = discovery_payload(self.territory)
        key = _discovery_cache_key(self.territory, REFERENCE_DATE)
        _write_persistent_discovery(self.connection, key, payload)
        clear_hydrometry_caches()
        result, build = self.fetch(lambda *_args, **_kwargs: self.fail("rebuild"))
        self.assertEqual(result, payload)
        build.assert_not_called()

    def test_miss_rebuilds_and_writes_complete_payload(self):
        expected = discovery_payload(self.territory)
        result, build = self.fetch(lambda *_args, **_kwargs: expected)
        self.assertEqual(result, expected)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(self.connection.write_count, 1)

    def test_cache_is_reread_after_cross_process_lock(self):
        expected = discovery_payload(self.territory)
        key = _discovery_cache_key(self.territory, REFERENCE_DATE)
        original_execute = self.connection.execute

        def execute(query, params=()):
            result = original_execute(query, params)
            if "pg_try_advisory_lock" in query and result.row == (True,):
                self.connection.rows[key] = (
                    DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION,
                    "complete",
                    json.loads(serialize_discovery_cache_payload(expected)),
                )
            return result

        self.connection.execute = execute
        result, build = self.fetch(lambda *_args, **_kwargs: self.fail("rebuild"))
        self.assertEqual(result, expected)
        build.assert_not_called()
        self.assertGreaterEqual(self.connection.read_count, 2)

    def test_lock_guard_expiry_never_starts_a_concurrent_rebuild(self):
        key = _discovery_cache_key(self.territory, REFERENCE_DATE)
        owner_lock = Lock()
        owner_lock.acquire()
        self.connection.locks[key] = owner_lock
        virtual_time = [0.0]
        lock_wait_guard = 2.0
        builder_duration = 10.0

        def clock():
            return virtual_time[0]

        def sleeper(duration):
            virtual_time[0] += duration

        def acquire(connection, cache_key):
            return _acquire_discovery_lock(
                connection,
                cache_key,
                timeout_seconds=lock_wait_guard,
                clock=clock,
                sleeper=sleeper,
            )

        try:
            with (
                patch(
                    "digues_webapp.hydrometry._acquire_discovery_lock",
                    side_effect=acquire,
                ),
                patch(
                    "digues_webapp.hydrometry.fetch_territoire_administratif",
                    return_value=self.territory,
                ),
                patch(
                    "digues_webapp.hydrometry.build_daily_discovery_selection",
                    side_effect=AssertionError("rebuild concurrent"),
                ) as build,
                self.assertRaises(HydrometryServiceUnavailableError),
            ):
                fetch_hydrometric_discovery(
                    self.connection,
                    day_provider=lambda: REFERENCE_DATE,
                )
        finally:
            owner_lock.release()

        self.assertGreaterEqual(virtual_time[0], lock_wait_guard)
        self.assertLess(virtual_time[0], builder_duration)
        build.assert_not_called()
        self.assertEqual(self.connection.write_count, 0)

    def test_identity_components_invalidate_the_cache(self):
        scenarios = [
            ("territory", lambda: setattr(self, "territory", territory(4))),
            (
                "topage",
                lambda stack: stack.enter_context(
                    patch("digues_webapp.hydrometry.TOPAGE_VERSION", "2027")
                ),
            ),
            (
                "algorithm",
                lambda stack: stack.enter_context(patch(
                    "digues_webapp.hydrometry.DISCOVERY_ALGORITHM_VERSION", "v-next"
                )),
            ),
            (
                "topology_algorithm",
                lambda stack: stack.enter_context(patch(
                    "digues_webapp.hydrometry.TOPOLOGY_ALGORITHM_VERSION",
                    "topology-next",
                )),
            ),
            (
                "attachment_policy",
                lambda stack: stack.enter_context(patch(
                    "digues_webapp.hydrometry.ATTACHMENT_POLICY_ID",
                    "attachment-next",
                )),
            ),
            (
                "policy",
                lambda stack: stack.enter_context(patch(
                    "digues_webapp.hydrometry.ACTIVITY_POLICY_ID", "policy-next"
                )),
            ),
        ]
        from contextlib import ExitStack

        for label, change in scenarios:
            with self.subTest(label=label):
                self.setUp()
                original = discovery_payload(self.territory)
                key = _discovery_cache_key(self.territory, REFERENCE_DATE)
                _write_persistent_discovery(self.connection, key, original)
                clear_hydrometry_caches()
                with ExitStack() as stack:
                    if label == "territory":
                        change()
                    else:
                        change(stack)
                    rebuilt = discovery_payload(self.territory)
                    result, build = self.fetch(lambda *_args, **_kwargs: rebuilt)
                self.assertEqual(result, rebuilt)
                self.assertEqual(build.call_count, 1)

        self.setUp()
        old = discovery_payload(self.territory)
        _write_persistent_discovery(
            self.connection,
            _discovery_cache_key(self.territory, REFERENCE_DATE),
            old,
        )
        clear_hydrometry_caches()
        new_date = date(2026, 10, 3)
        new_payload = discovery_payload(self.territory)
        new_payload["identity"] = _discovery_identity(self.territory, new_date)
        with (
            patch("digues_webapp.hydrometry.fetch_territoire_administratif", return_value=self.territory),
            patch(
                "digues_webapp.hydrometry.build_daily_discovery_selection",
                return_value=new_payload,
            ) as build,
        ):
            fetch_hydrometric_discovery(
                self.connection, day_provider=lambda: new_date
            )
        self.assertEqual(build.call_count, 1)

    def test_unknown_or_invalid_payload_rebuilds(self):
        key = _discovery_cache_key(self.territory, REFERENCE_DATE)
        valid = discovery_payload(self.territory)
        for stored in (
            (1, "complete", {"schema_version": 1}),
            (999, "complete", json.loads(serialize_discovery_cache_payload(valid))),
            (DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION, "complete", "{invalid"),
        ):
            with self.subTest(stored=stored[0:2]):
                clear_hydrometry_caches()
                self.connection.rows[key] = stored
                rebuilt, build = self.fetch(lambda *_args, **_kwargs: valid)
                self.assertEqual(rebuilt, valid)
                self.assertEqual(build.call_count, 1)

    def test_previous_complete_schema_is_replaced_by_current_payload(self):
        key = _discovery_cache_key(self.territory, REFERENCE_DATE)
        self.connection.rows[key] = (1, "complete", {"schema_version": 1})
        valid = discovery_payload(self.territory)

        rebuilt, build = self.fetch(lambda *_args, **_kwargs: valid)

        self.assertEqual(rebuilt, valid)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(
            self.connection.rows[key][0], DISCOVERY_CACHE_PAYLOAD_SCHEMA_VERSION
        )

    def test_partial_or_degraded_payload_never_replaces_complete(self):
        key = _discovery_cache_key(self.territory, REFERENCE_DATE)
        complete = discovery_payload(self.territory)
        _write_persistent_discovery(self.connection, key, complete)
        original = self.connection.rows[key]
        self.assertFalse(_write_persistent_discovery(
            self.connection,
            key,
            discovery_payload(self.territory, complete=False),
        ))
        self.assertFalse(_write_persistent_discovery(
            self.connection,
            key,
            discovery_payload(self.territory, activity_errors=[{"quantity": "H"}]),
        ))
        self.assertEqual(self.connection.rows[key], original)

    def test_partial_payload_is_reused_as_explicitly_partial_when_no_complete_exists(self):
        partial = discovery_payload(self.territory, complete=False)
        result, build = self.fetch(lambda *_args, **_kwargs: partial)
        self.assertEqual(result, partial)
        self.assertEqual(build.call_count, 1)
        key = _discovery_cache_key(self.territory, REFERENCE_DATE)
        self.assertEqual(self.connection.rows[key][1], "partial")

        clear_hydrometry_caches()
        result, rebuild = self.fetch(
            lambda *_args, **_kwargs: self.fail("rebuild")
        )
        self.assertEqual(result, partial)
        self.assertFalse(
            result["source_extract"]["complete_for_traversed_branches"]
        )
        rebuild.assert_not_called()

    def test_read_or_write_error_keeps_rebuilt_result_usable(self):
        expected = discovery_payload(self.territory)
        self.connection.read_error = True
        result, _ = self.fetch(lambda *_args, **_kwargs: expected)
        self.assertEqual(result, expected)
        clear_hydrometry_caches()
        self.connection.read_error = False
        self.connection.write_error = True
        result, _ = self.fetch(lambda *_args, **_kwargs: expected)
        self.assertEqual(result, expected)

    def test_concurrent_requests_have_one_effective_builder(self):
        calls = []

        def builder(*_args, **_kwargs):
            calls.append(1)
            time.sleep(0.03)
            return discovery_payload(self.territory)

        results = []

        def request():
            results.append(fetch_hydrometric_discovery(
                self.connection,
                day_provider=lambda: REFERENCE_DATE,
            ))

        with (
            patch(
                "digues_webapp.hydrometry.fetch_territoire_administratif",
                return_value=self.territory,
            ),
            patch(
                "digues_webapp.hydrometry.build_daily_discovery_selection",
                side_effect=builder,
            ) as build,
        ):
            threads = [Thread(target=request) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(self.connection.lock_count, 1)

    def test_round_trip_preserves_extended_discovery_contract(self):
        payload = discovery_payload(self.territory, rich=True)
        serialized = serialize_discovery_cache_payload(payload)
        restored = deserialize_discovery_cache_payload(
            serialized, payload["identity"]
        )
        self.assertEqual(restored, payload)
        self.assertEqual(discovery_payload_size_bytes(payload), len(serialized.encode()))
        for dynamic_field in ('"latest"', '"samples_7d"', '"forecasts"'):
            self.assertNotIn(dynamic_field, serialized)


if __name__ == "__main__":
    unittest.main()
