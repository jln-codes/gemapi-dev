from datetime import date
import json
from threading import Lock
import unittest
from unittest.mock import patch

from digues_webapp.hydrometry import (
    HydrometryServiceUnavailableError,
    STRUCTURAL_CACHE_PAYLOAD_SCHEMA_VERSION,
    _structural_cache_key,
    _structural_identity,
    _acquire_discovery_lock,
    _write_persistent_structure,
    build_daily_discovery_selection,
    build_discovery_from_structure,
    build_structural_dataset,
    clear_hydrometry_caches,
    fetch_hydrometric_discovery,
    fetch_hydrometric_structure,
    serialize_structural_cache_payload,
    structural_completeness,
)


DAY_1 = date(2026, 10, 2)
DAY_2 = date(2026, 10, 3)


def territory(maximum=3):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"code": "T", "nom": "Territoire"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [1, 49], [maximum, 49], [maximum, 51], [1, 51], [1, 49],
        ]]},
    }]}


def station_row(code, site, longitude, *, in_service=True):
    return {
        "code_station": code,
        "code_site": site,
        "libelle_station": code,
        "en_service": in_service,
        "longitude_station": longitude,
        "latitude_station": 50,
    }


def site_row(code, longitude):
    return {
        "code_site": code,
        "libelle_site": code,
        "longitude_site": longitude,
        "latitude_site": 50,
    }


STATIONS = [
    station_row("A-H", "A", 0.8),
    station_row("B-H", "B", 0.6),
]
SITES = [site_row("A", 0.8), site_row("B", 0.6)]


def raw_edge():
    return {
        "type": "Feature",
        "properties": {
            "CdOH": "E",
            "CdNoeudDebut": "SOURCE",
            "CdNoeudFin": "TERRITORY",
            "SensEcoulementTH": "dans direction tronçon",
            "TopoOH": "Cours d'eau test",
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [[0.5, 50], [2, 50]],
        },
    }


def reference_sites(*, multistation=False):
    rows = list(STATIONS)
    if multistation:
        rows.append(station_row("A-Q", "A", 0.8))
    result = {}
    for code, longitude in (("A", 0.8), ("B", 0.6)):
        matching = [row for row in rows if row["code_site"] == code]
        result[code] = {
            "code_site": code,
            "name": code,
            "watercourse": {"name": "Cours d'eau test", "official_ids": []},
            "position": {
                "longitude": longitude,
                "latitude": 50,
                "crs": "EPSG:4326",
            },
            "municipality": {"code": None, "name": None},
            "inside_territory": False,
            "stations": [{
                "code_station": row["code_station"],
                "code_site": code,
                "name": row["code_station"],
                "watercourse_name": None,
                "status": "in_service",
                "in_service": True,
                "opened_at": None,
                "closed_at": None,
                "type": None,
                "producer_network_codes": [],
                "qualification_code": None,
                "altimetric_reference": {
                    "zero_altitude_native": None,
                    "system_code": None,
                    "valid_from": None,
                    "activated_at": None,
                },
                "position": {
                    "longitude": longitude,
                    "latitude": 50,
                    "crs": "EPSG:4326",
                },
                "recent_quantities": [],
            } for row in matching],
        }
    return result


def structural_payload(value=None, *, complete=True, multistation=False):
    value = value or territory()
    identity = _structural_identity(value)
    sites = reference_sites(multistation=multistation)
    # Le fingerprint est remplacé dans les tests persistants par celui produit
    # par build_structural_dataset ; ici il ne sert qu'aux tests de sélection.
    return {
        "kind": "hydrometric_structural_discovery",
        "generated_at": "2026-10-02T08:00:00Z",
        "identity": identity,
        "territory": {
            "id": "T",
            "label": "Territoire",
            "bbox": [1, 49, 3, 51],
            "fingerprint": identity["territory_fingerprint"],
        },
        "reference_fingerprint": "test-reference",
        "candidate_sites": list(sites.values()),
        "edges": [{
            "code": "E",
            "upstream_node": "SOURCE",
            "downstream_node": "TERRITORY",
            "length_m": 1000,
            "upstream_coordinate": [0.5, 50],
            "downstream_coordinate": [2, 50],
            "course_codes": [],
            "name": "Cours d'eau test",
            "nature": None,
            "status": None,
            "direction_status": "directed",
        }],
        "boundary_events": [{
            "identifier": "E:0:upstream",
            "edge_code": "E",
            "flow_fraction": 1 / 3,
            "direction": "upstream",
            "coordinate": [1, 50],
        }],
        "attachments": [
            {
                "site_code": "A", "edge_code": "E", "flow_fraction": 0.2,
                "distance_m": 0, "station_code": "A-H",
                "inside_territory": False,
            },
            {
                "site_code": "B", "edge_code": "E", "flow_fraction": 0.066,
                "distance_m": 0, "station_code": "B-H",
                "inside_territory": False,
            },
        ] + ([{
            "site_code": "A", "edge_code": "E", "flow_fraction": 0.2,
            "distance_m": 0, "station_code": "A-Q",
            "inside_territory": False,
        }] if multistation else []),
        "source_extract": {
            "bbox": [0.5, 48.5, 3.5, 51.5],
            "topage_feature_count": 1,
            "topage_edge_count": 1,
            "hubeau_station_count": 2,
            "hubeau_site_count": 2,
            "spatial_padding_degrees": 0.5,
            "spatial_padding_is_business_rule": False,
            "expansion_attempt": 0,
            "complete_for_traversed_branches": complete,
        },
        "diagnostics": {"boundary": [], "attachment": [], "reference": []},
        "provenance": {
            "reference": {"provider": "Hub'Eau Hydrométrie"},
            "network": {"provider": "BD TOPAGE", "version": "2026"},
        },
    }


def activity(*codes):
    return {
        code: {
            "H": {"source_time": "2026-10-02T06:00:00Z"}
            if code.endswith("H") else None,
            "Q": {"source_time": "2026-10-02T06:00:00Z"}
            if code.endswith("Q") else None,
        }
        for code in codes
    }


class Result:
    def __init__(self, row=None, rowcount=0):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class PersistentConnection:
    def __init__(self):
        self.discovery_rows = {}
        self.structural_rows = {}
        self.locks = {}
        self.guard = Lock()
        self.structural_reads = 0
        self.structural_writes = 0

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        key = params[0] if params else None
        if normalized.startswith("SELECT payload_schema_version"):
            if "cache_structure_hydrometrie" in normalized:
                self.structural_reads += 1
                return Result(self.structural_rows.get(key))
            return Result(self.discovery_rows.get(key))
        if normalized.startswith("SELECT pg_try_advisory_lock"):
            with self.guard:
                lock = self.locks.setdefault(key, Lock())
            return Result((lock.acquire(blocking=False),))
        if normalized.startswith("SELECT pg_advisory_unlock"):
            self.locks[key].release()
            return Result((True,))
        if normalized.startswith("INSERT INTO technique.cache_structure_hydrometrie"):
            completeness = params[9]
            existing = self.structural_rows.get(key)
            if existing is not None and existing[1] == "complete" and (
                completeness != "complete"
            ):
                return Result(rowcount=0)
            self.structural_writes += 1
            self.structural_rows[key] = (
                params[8], completeness, json.loads(params[10])
            )
            return Result(rowcount=1)
        if normalized.startswith("INSERT INTO technique.cache_decouverte_hydrometrie"):
            completeness = params[9]
            existing = self.discovery_rows.get(key)
            if existing is not None and existing[1] == "complete" and (
                completeness != "complete"
            ):
                return Result(rowcount=0)
            self.discovery_rows[key] = (
                params[8], completeness, json.loads(params[10])
            )
            return Result(rowcount=1)
        raise AssertionError(normalized)


class ReferenceClient:
    last_activity_errors = []

    def __init__(self, activities, station_snapshots=None):
        self.activities = list(activities)
        self.station_snapshots = station_snapshots or [STATIONS]
        self.activity_calls = 0
        self.reference_calls = 0
        self.requested_activity_codes = []

    def reference_stations(self, _bbox):
        index = min(self.reference_calls, len(self.station_snapshots) - 1)
        self.reference_calls += 1
        return list(self.station_snapshots[index])

    def reference_sites(self, _bbox):
        return list(SITES)

    def recent_activity(self, codes, **_kwargs):
        self.requested_activity_codes.append(list(codes))
        index = min(self.activity_calls, len(self.activities) - 1)
        self.activity_calls += 1
        return self.activities[index]


class FailingTopageClient:
    def features(self, _bbox):
        raise AssertionError("TOPAGE ne doit pas être rappelé")


class StructuralSelectionTest(unittest.TestCase):
    def setUp(self):
        clear_hydrometry_caches()

    def test_first_active_site_can_change_without_changing_structure(self):
        structural = structural_payload()
        sites = reference_sites()
        first, _ = build_discovery_from_structure(
            structural, sites, activity("A-H", "B-H"), DAY_1
        )
        second, _ = build_discovery_from_structure(
            structural, sites, activity("B-H"), DAY_2
        )
        self.assertEqual([site["code_site"] for site in first["sites"]], ["A"])
        self.assertEqual([site["code_site"] for site in second["sites"]], ["B"])

    def test_h_only_and_multistation_are_preserved_by_daily_selection(self):
        structural = structural_payload(multistation=True)
        sites = reference_sites(multistation=True)
        payload, _ = build_discovery_from_structure(
            structural, sites, activity("A-H", "A-Q"), DAY_1
        )
        selected = payload["sites"][0]
        self.assertEqual(selected["code_site"], "A")
        self.assertEqual(
            {station["code_station"] for station in selected["stations"]},
            {"A-H", "A-Q"},
        )
        self.assertEqual(
            {tuple(station["recent_quantities"]) for station in selected["stations"]},
            {("H",), ("Q",)},
        )

    def test_structural_build_calls_topage_once_and_keeps_all_candidates(self):
        calls = []
        unrelated = json.loads(json.dumps(raw_edge()))
        unrelated["properties"].update({
            "CdOH": "UNRELATED",
            "CdNoeudDebut": "U1",
            "CdNoeudFin": "U2",
        })
        unrelated["geometry"]["coordinates"] = [[1.5, 49.5], [2.5, 49.5]]

        class Topage:
            def features(self, bbox):
                calls.append(bbox)
                return [raw_edge(), unrelated]

        client = ReferenceClient(
            [activity("A-H")],
            station_snapshots=[[
                STATIONS[0],
                station_row("B-H", "B", 0.6, in_service=False),
            ]],
        )
        payload = build_structural_dataset(territory(), client, Topage())
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            {site["code_site"] for site in payload["candidate_sites"]},
            {"A", "B"},
        )
        self.assertNotIn("reference_date", payload["identity"])
        self.assertTrue(payload["boundary_events"])
        self.assertEqual(payload["source_extract"]["topage_edge_count"], 2)
        self.assertEqual(payload["source_extract"]["structural_edge_count"], 1)
        self.assertEqual([edge["code"] for edge in payload["edges"]], ["E"])


class StructuralPersistenceTest(unittest.TestCase):
    def setUp(self):
        clear_hydrometry_caches()
        self.territory = territory()
        self.connection = PersistentConnection()

    def persisted_structure(self):
        client = ReferenceClient([activity("A-H", "B-H")])

        class Topage:
            def features(self, _bbox):
                return [raw_edge()]

        payload = build_structural_dataset(self.territory, client, Topage())
        key = _structural_cache_key(self.territory)
        _write_persistent_structure(self.connection, key, payload)
        clear_hydrometry_caches()
        return payload

    def test_persistent_hit_skips_topage_after_memory_restart(self):
        expected = self.persisted_structure()
        result, _ = fetch_hydrometric_structure(
            self.territory,
            client=ReferenceClient([activity("A-H", "B-H")]),
            topage_client=FailingTopageClient(),
            persistence=self.connection,
        )
        self.assertEqual(result, expected)

    def test_persistent_hit_reuses_exact_membership_and_refreshes_status(self):
        self.persisted_structure()
        changed = [
            station_row("A-H", "A", 0.8, in_service=False),
            STATIONS[1],
        ]
        with patch(
            "digues_webapp.hydrometry.point_in_territory",
            side_effect=AssertionError("point-dans-polygone structurel recalculé"),
        ):
            _structural, sites = fetch_hydrometric_structure(
                self.territory,
                client=ReferenceClient(
                    [activity("B-H")], station_snapshots=[changed]
                ),
                topage_client=FailingTopageClient(),
                persistence=self.connection,
            )

        self.assertFalse(sites["A"]["inside_territory"])
        self.assertFalse(sites["A"]["stations"][0]["in_service"])
        self.assertFalse(sites["B"]["inside_territory"])

    def test_structure_is_reread_after_cross_process_lock(self):
        client = ReferenceClient([activity("A-H", "B-H")])

        class Topage:
            def features(self, _bbox):
                return [raw_edge()]

        expected = build_structural_dataset(self.territory, client, Topage())
        key = _structural_cache_key(self.territory)
        original_execute = self.connection.execute

        def execute(query, params=()):
            result = original_execute(query, params)
            if "pg_try_advisory_lock" in query and result.row == (True,):
                self.connection.structural_rows[key] = (
                    STRUCTURAL_CACHE_PAYLOAD_SCHEMA_VERSION,
                    "complete",
                    json.loads(serialize_structural_cache_payload(expected)),
                )
            return result

        self.connection.execute = execute
        with patch(
            "digues_webapp.hydrometry.build_structural_dataset",
            side_effect=AssertionError("second builder"),
        ) as build:
            result, _ = fetch_hydrometric_structure(
                self.territory,
                client=ReferenceClient([activity("A-H", "B-H")]),
                topage_client=FailingTopageClient(),
                persistence=self.connection,
            )
        self.assertEqual(result, expected)
        build.assert_not_called()
        self.assertGreaterEqual(self.connection.structural_reads, 2)

    def test_structural_key_invalidation_components(self):
        baseline = _structural_cache_key(self.territory)
        self.assertNotEqual(baseline, _structural_cache_key(territory(4)))
        with patch("digues_webapp.hydrometry.TOPAGE_VERSION", "2027"):
            self.assertNotEqual(baseline, _structural_cache_key(self.territory))
        with patch(
            "digues_webapp.hydrometry.TOPOLOGY_ALGORITHM_VERSION", "v-next"
        ):
            self.assertNotEqual(baseline, _structural_cache_key(self.territory))
        with patch("digues_webapp.hydrometry.ATTACHMENT_POLICY_ID", "v-next"):
            self.assertNotEqual(baseline, _structural_cache_key(self.territory))

    def test_station_position_change_rebuilds_attachment_structure(self):
        original = self.persisted_structure()
        changed = [station_row("A-H", "A", 0.81), STATIONS[1]]
        topage_calls = []

        class Topage:
            def features(self, bbox):
                topage_calls.append(bbox)
                return [raw_edge()]

        rebuilt, _ = fetch_hydrometric_structure(
            self.territory,
            client=ReferenceClient(
                [activity("A-H", "B-H")], station_snapshots=[changed]
            ),
            topage_client=Topage(),
            persistence=self.connection,
        )
        self.assertEqual(len(topage_calls), 1)
        self.assertNotEqual(
            original["reference_fingerprint"], rebuilt["reference_fingerprint"]
        )

    def test_structural_lock_timeout_never_builds_without_lock(self):
        key = _structural_cache_key(self.territory)
        lock_key = f"structural:{key}"
        owner_lock = Lock()
        owner_lock.acquire()
        self.connection.locks[lock_key] = owner_lock
        virtual_time = [0.0]

        def acquire(connection, cache_key):
            return _acquire_discovery_lock(
                connection,
                cache_key,
                timeout_seconds=2,
                clock=lambda: virtual_time[0],
                sleeper=lambda duration: virtual_time.__setitem__(
                    0, virtual_time[0] + duration
                ),
            )

        try:
            with (
                patch(
                    "digues_webapp.hydrometry._acquire_discovery_lock",
                    side_effect=acquire,
                ),
                patch(
                    "digues_webapp.hydrometry.build_structural_dataset",
                    side_effect=AssertionError("build sans verrou"),
                ) as build,
                self.assertRaises(HydrometryServiceUnavailableError),
            ):
                fetch_hydrometric_structure(
                    self.territory,
                    client=ReferenceClient([activity("A-H")]),
                    topage_client=FailingTopageClient(),
                    persistence=self.connection,
                )
        finally:
            owner_lock.release()
        build.assert_not_called()
        self.assertGreaterEqual(virtual_time[0], 2)

    def test_partial_structure_never_replaces_complete(self):
        complete = structural_payload(self.territory)
        partial = structural_payload(self.territory, complete=False)
        key = _structural_cache_key(self.territory)
        self.assertTrue(_write_persistent_structure(self.connection, key, complete))
        original = self.connection.structural_rows[key]
        self.assertFalse(_write_persistent_structure(self.connection, key, partial))
        self.assertEqual(self.connection.structural_rows[key], original)
        self.assertEqual(structural_completeness(partial), "partial")

    def test_structural_json_round_trip_is_standard_and_versioned(self):
        payload = structural_payload(self.territory)
        value = json.loads(serialize_structural_cache_payload(payload))
        self.assertEqual(
            value["schema_version"], STRUCTURAL_CACHE_PAYLOAD_SCHEMA_VERSION
        )
        self.assertNotIn("reference_date", value["identity"])
        self.assertNotIn("geometry", json.dumps(value["dataset"]["edges"]))

    def test_next_day_reuses_structure_and_reselects_without_topage(self):
        self.persisted_structure()
        client = ReferenceClient([
            activity("A-H", "B-H"),
            activity("A-H", "B-H"),
        ], station_snapshots=[
            STATIONS,
            [station_row("A-H", "A", 0.8, in_service=False), STATIONS[1]],
        ])
        with patch(
            "digues_webapp.hydrometry.fetch_territoire_administratif",
            return_value=self.territory,
        ):
            first = fetch_hydrometric_discovery(
                self.connection,
                client=client,
                topage_client=FailingTopageClient(),
                day_provider=lambda: DAY_1,
            )
            clear_hydrometry_caches()
            second = fetch_hydrometric_discovery(
                self.connection,
                client=client,
                topage_client=FailingTopageClient(),
                day_provider=lambda: DAY_2,
            )
        self.assertEqual([site["code_site"] for site in first["sites"]], ["A"])
        self.assertEqual([site["code_site"] for site in second["sites"]], ["B"])
        self.assertEqual(client.activity_calls, 2)
        self.assertEqual(client.requested_activity_codes[-1], ["B-H"])
        self.assertEqual(self.connection.structural_writes, 1)

    def test_same_day_restart_uses_daily_discovery_without_structure_read(self):
        self.persisted_structure()
        client = ReferenceClient([activity("A-H", "B-H")])
        with patch(
            "digues_webapp.hydrometry.fetch_territoire_administratif",
            return_value=self.territory,
        ):
            first = fetch_hydrometric_discovery(
                self.connection,
                client=client,
                topage_client=FailingTopageClient(),
                day_provider=lambda: DAY_1,
            )
            clear_hydrometry_caches()
            reads = self.connection.structural_reads
            second = fetch_hydrometric_discovery(
                self.connection,
                client=ReferenceClient([activity("B-H")]),
                topage_client=FailingTopageClient(),
                day_provider=lambda: DAY_1,
            )
        self.assertEqual(first, second)
        self.assertEqual(self.connection.structural_reads, reads)


if __name__ == "__main__":
    unittest.main()
