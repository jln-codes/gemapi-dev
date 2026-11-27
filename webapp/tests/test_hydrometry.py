from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

from digues_webapp.hydrometry import (
    BoundaryEvent,
    HydrometryClient,
    HydrometryServiceUnavailableError,
    SelectedSite,
    SiteAttachment,
    TopageEdge,
    assemble_hydrometric_dataset,
    boundary_events,
    build_active_sites,
    build_forecast_dataset,
    build_observation_dataset,
    clear_hydrometry_caches,
    dataset_sites_geojson,
    fetch_hydrometric_discovery,
    fetch_hydrometric_observations,
    parse_hubeau_series,
    resolve_site_series,
    resample_series,
    traverse_to_first_sites,
    _relation,
)


def edge(code, upstream, downstream, x1, x2, *, directed=True):
    return TopageEdge(
        code=code,
        upstream_node=upstream,
        downstream_node=downstream,
        length_m=1000,
        geometry={"type": "LineString", "coordinates": [[x1, 50], [x2, 50]]},
        direction_status="directed" if directed else "ambiguous",
    )


class TraversalTest(unittest.TestCase):
    def test_exact_boundary_crossing_is_classified_by_flow_direction(self):
        territory = {"type": "Polygon", "coordinates": [[
            [1, 49], [3, 49], [3, 51], [1, 51], [1, 49],
        ]]}
        edges = {
            "enter": edge("enter", "out", "in", 0, 2),
            "leave": edge("leave", "in", "out", 2, 4),
        }
        events, ambiguous = boundary_events(
            edges, territory, territory_bounds=(1, 49, 3, 51)
        )
        self.assertEqual(
            {(item.edge_code, item.direction) for item in events},
            {("enter", "upstream"), ("leave", "downstream")},
        )
        self.assertEqual(ambiguous, [])

    def test_first_upstream_station_stops_the_branch(self):
        edges = {"e": edge("e", "a", "b", 0, 1)}
        attachments = {"e": [
            SiteAttachment("first", "e", .4, 1),
            SiteAttachment("second", "e", .2, 1),
        ]}
        result = traverse_to_first_sites(
            BoundaryEvent("cross", "e", .8, "upstream", (.8, 50)),
            edges,
            attachments,
        )
        self.assertEqual([item.site_code for item in result.selected], ["first"])

    def test_first_downstream_station_stops_the_branch(self):
        edges = {"e": edge("e", "a", "b", 0, 1)}
        attachments = {"e": [
            SiteAttachment("first", "e", .6, 1),
            SiteAttachment("second", "e", .9, 1),
        ]}
        result = traverse_to_first_sites(
            BoundaryEvent("cross", "e", .2, "downstream", (.2, 50)),
            edges,
            attachments,
        )
        self.assertEqual([item.site_code for item in result.selected], ["first"])

    def test_bifurcation_keeps_first_site_on_each_branch(self):
        edges = {
            "root": edge("root", "n", "territory", 1, 2),
            "a": edge("a", "a0", "n", 0, 1),
            "b": edge("b", "b0", "n", 0, 1),
        }
        attachments = {
            "a": [SiteAttachment("site-a", "a", .5, 1)],
            "b": [SiteAttachment("site-b", "b", .5, 1)],
        }
        result = traverse_to_first_sites(
            BoundaryEvent("cross", "root", .5, "upstream", (1.5, 50)),
            edges,
            attachments,
        )
        self.assertEqual(
            {item.site_code for item in result.selected}, {"site-a", "site-b"}
        )

    def test_cycle_terminates_and_ambiguous_edge_is_not_forced(self):
        cyclic = {
            "root": edge("root", "n", "territory", 1, 2),
            "back": edge("back", "territory", "n", 2, 1),
        }
        result = traverse_to_first_sites(
            BoundaryEvent("cross", "root", .5, "upstream", (1.5, 50)),
            cyclic,
            {},
        )
        self.assertTrue(result.cycle_edges)
        ambiguous = {"e": edge("e", "a", "b", 0, 1, directed=False)}
        result = traverse_to_first_sites(
            BoundaryEvent("cross", "e", .5, "upstream", (.5, 50)),
            ambiguous,
            {},
        )
        self.assertEqual(result.ambiguous_edges, {"e"})

    def test_known_regulated_transition_is_conditional(self):
        edges = {
            "01T0000002005072266": edge(
                "01T0000002005072266", "01N0000002486819487", "territory", 1, 2
            ),
            "01T0000002287816425": edge(
                "01T0000002287816425", "source", "01N0000002486819487", 0, 1
            ),
        }
        attachments = {
            "01T0000002287816425": [
                SiteAttachment("conditional", "01T0000002287816425", .5, 1)
            ]
        }
        result = traverse_to_first_sites(
            BoundaryEvent("cross", "01T0000002005072266", .5, "upstream", (1.5, 50)),
            edges,
            attachments,
        )
        self.assertEqual(
            result.selected[0].conditional_connections[0]["connection_id"],
            "goeulzin-sensee-petite-sensee",
        )
        relation = _relation(
            event=BoundaryEvent("cross", "edge", .5, "upstream", (2, 50)),
            selected=SelectedSite(
                "conditional", 100, 2, "fingerprint",
                result.selected[0].conditional_connections,
            ),
            role="upstream",
            site_attachment=None,
            territory_id="T",
            fingerprint="territory-fingerprint",
            calculated_at="2026-10-02T08:00:00Z",
        )
        self.assertEqual(relation["selection"]["state"], "pending_review")
        self.assertEqual(
            relation["network_evidence"]["hydraulic_continuity"], "conditional"
        )


class ObservationContractTest(unittest.TestCase):
    def test_structural_reference_request_keeps_inactive_candidates(self):
        client = HydrometryClient()
        with patch.object(
            client,
            "_get_json",
            return_value={"count": 1, "data": [{"code_station": "S"}]},
        ) as request:
            self.assertEqual(
                client.reference_stations((1, 49, 3, 51)),
                [{"code_station": "S"}],
            )
        self.assertNotIn("en_service", request.call_args.kwargs["params"])

    def test_parser_converts_units_and_preserves_native_provenance(self):
        payload = {"data": [
            {
                "code_station": "S1", "grandeur_hydro": "H",
                "date_obs": "2026-10-02T06:00:00Z", "resultat_obs": 1428,
            },
        ]}
        result = parse_hubeau_series(payload, "H")[0]
        self.assertEqual(result["value"], 1.428)
        self.assertEqual(result["native_value"], 1428)
        self.assertEqual(result["native_unit"], "mm")
        self.assertEqual(result["station_code"], "S1")
        q = parse_hubeau_series({"data": [{
            "code_station": "S1", "grandeur_hydro": "Q",
            "date_obs": "2026-10-02T06:00:00Z", "resultat_obs": 7600,
        }]}, "Q")[0]
        self.assertEqual((q["value"], q["native_unit"]), (7.6, "l/s"))

    def test_active_stations_are_deduplicated_by_site(self):
        geometry = {"type": "Polygon", "coordinates": [[
            [1, 49], [3, 49], [3, 51], [1, 51], [1, 49],
        ]]}
        rows = [{
            "code_station": code, "code_site": "SITE", "en_service": True,
            "longitude_station": 2, "latitude_station": 50,
        } for code in ("S1", "S2")]
        activity = {
            "S1": {"H": {"source_time": "2026-10-02T06:00:00Z"}, "Q": None},
            "S2": {"H": None, "Q": {"source_time": "2026-10-02T06:00:00Z"}},
        }
        sites = build_active_sites(rows, [{
            "code_site": "SITE", "longitude_site": 2, "latitude_site": 50,
        }], activity, geometry)
        self.assertEqual(list(sites), ["SITE"])
        self.assertEqual(len(sites["SITE"]["stations"]), 2)

    def test_resampling_has_28_targets_and_inclusive_tolerance(self):
        values = [{
            "value": 1.0, "native_value": 1000, "native_unit": "mm",
            "unit": "m", "source_time": "2026-10-01T22:30:00Z",
            "station_code": "S1",
        }]
        samples = resample_series(values, date(2026, 10, 2))
        self.assertEqual(len(samples), 28)
        self.assertEqual(samples[-4]["measurement"]["station_code"], "S1")

    def test_multistation_policy_never_uses_recency(self):
        series = [
            {"station_code": "old", "measurements": [{}]},
            {"station_code": "new", "measurements": [{}]},
        ]
        self.assertEqual(resolve_site_series(series)["status"], "multiple_unresolved")
        self.assertIsNone(resolve_site_series(series)["selected_station_code"])
        self.assertEqual(
            resolve_site_series(series, official_station_code="old")["status"],
            "selected_official",
        )
        self.assertEqual(
            resolve_site_series(series[:1])["status"], "selected_single_active"
        )
        self.assertEqual(resolve_site_series([])["status"], "unavailable")

    def test_unresolved_series_has_no_site_level_value(self):
        discovery = {
            "identity": {"territory_fingerprint": "fp"},
            "sites": [{
                "code_site": "SITE", "stations": [
                    {"code_station": "S1"}, {"code_station": "S2"},
                ],
            }],
        }

        class Client:
            def observation_series(self, station_code, quantity, **_kwargs):
                if quantity == "Q":
                    return []
                return [{
                    "value": 1 if station_code == "S1" else 2,
                    "unit": "m", "native_value": 1000, "native_unit": "mm",
                    "source_time": "2026-10-02T06:00:00Z",
                    "station_code": station_code,
                }]

        result = build_observation_dataset(
            discovery,
            Client(),
            date(2026, 10, 2),
            now=datetime(2026, 10, 2, 8, tzinfo=timezone.utc),
            workers=1,
        )["sites"]["SITE"]
        self.assertEqual(
            result["series_resolution"]["height"]["status"],
            "multiple_unresolved",
        )
        self.assertIsNone(result["latest"]["height"])
        self.assertTrue(all(
            sample["height_m"] is None
            for sample in result["samples_7d"]["samples"]
        ))

    def test_one_quantity_and_one_station_failure_stay_partial(self):
        discovery = {"identity": {"territory_fingerprint": "fp"}, "sites": [
            {"code_site": "A", "stations": [{"code_station": "A1"}]},
            {"code_site": "B", "stations": [{"code_station": "B1"}]},
        ]}

        class Client:
            def observation_series(self, station_code, quantity, **_kwargs):
                if station_code == "A1" and quantity == "H":
                    raise HydrometryServiceUnavailableError("indisponible")
                return [{
                    "value": 1, "unit": "m" if quantity == "H" else "m3/s",
                    "native_value": 1000,
                    "native_unit": "mm" if quantity == "H" else "l/s",
                    "source_time": "2026-10-02T06:00:00Z",
                    "station_code": station_code,
                }]

        result = build_observation_dataset(
            discovery, Client(), date(2026, 10, 2),
            now=datetime(2026, 10, 2, 8, tzinfo=timezone.utc), workers=2,
        )
        self.assertEqual(
            result["sites"]["A"]["series_resolution"]["height"]["status"],
            "unavailable",
        )
        self.assertEqual(
            result["sites"]["A"]["series_resolution"]["discharge"]["status"],
            "selected_single_active",
        )
        self.assertEqual(
            result["sites"]["B"]["series_resolution"]["height"]["status"],
            "selected_single_active",
        )

    def test_geojson_exposes_site_once_and_unresolved_state(self):
        discovery = {
            "identity": {}, "territory": {}, "sites": [{
                "code_site": "SITE", "name": "Site", "watercourse": {"name": "River"},
                "position": {"longitude": 2, "latitude": 50}, "stations": [],
                "territory_relations": [{
                    "territory": {"id": "T", "fingerprint": "fingerprint"},
                    "role": "inside",
                    "selection": {
                        "method": "inside_exact_polygon", "state": "selected",
                    },
                    "topology_status": "not_applicable",
                    "hydraulic_continuity": "not_assessed",
                    "proof_summary": {
                        "count": 1,
                        "pending_review_count": 0,
                        "conditional_connection_ids": [],
                    },
                    "proofs": [{"selection_state": "selected"}],
                }],
            }],
        }
        observations = {
            "cache_date": "2026-10-02", "generated_at": "2026-10-02T08:00:00Z",
            "sites": {"SITE": {
                "latest": {"height": None, "discharge": None},
                "series_resolution": {
                    "height": {"status": "multiple_unresolved"},
                    "discharge": {"status": "unavailable"},
                },
            }},
            "errors": [],
        }
        forecasts = {
            "generated_at": "2026-10-02T08:00:00Z",
            "sites": {"SITE": None}, "errors": [],
        }
        dataset = assemble_hydrometric_dataset(discovery, observations, forecasts)
        geojson = dataset_sites_geojson(dataset)
        self.assertEqual(len(geojson["features"]), 1)
        self.assertEqual(
            geojson["features"][0]["properties"]["observations"]
            ["series_resolution"]["height"]["status"],
            "multiple_unresolved",
        )
        self.assertEqual(geojson["features"][0]["properties"]["name"], "Site")
        self.assertNotIn("code_hydro", geojson["features"][0]["properties"])
        self.assertNotIn("stations", dataset)
        relation = geojson["features"][0]["properties"]["territory_relations"][0]
        self.assertEqual(relation["role"], "inside")
        self.assertEqual(relation["proof_count"], 1)
        self.assertNotIn("proofs", relation)

    def test_vigicrues_forecast_is_optional_and_matches_station_code(self):
        discovery = {"sites": [
            {"code_site": "A", "stations": [{"code_station": "A01"}]},
            {"code_site": "B", "stations": [{"code_station": "B01"}]},
        ]}

        class Client:
            def forecast_catalog(self, quantity):
                return {"A01"} if quantity == "H" else set()

            def forecast(self, code, quantity):
                self.request = (code, quantity)
                return {"points": [], "source": "Vigicrues"}

        client = Client()
        result = build_forecast_dataset(discovery, client)
        self.assertEqual(client.request, ("A01", "H"))
        self.assertIsNotNone(result["sites"]["A"])
        self.assertIsNone(result["sites"]["B"])


class CacheSeparationTest(unittest.TestCase):
    def setUp(self):
        clear_hydrometry_caches()

    @staticmethod
    def territory(maximum=3):
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"code": "T"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [1, 49], [maximum, 49], [maximum, 51], [1, 51], [1, 49],
            ]]},
        }]}

    def test_discovery_is_reused_and_invalidated_by_territory(self):
        first = self.territory()
        second = self.territory(4)
        payload = {"identity": {}, "sites": []}
        with (
            patch(
                "digues_webapp.hydrometry.fetch_territoire_administratif",
                side_effect=[first, first, second],
            ),
            patch(
                "digues_webapp.hydrometry.build_daily_discovery_selection",
                return_value=payload,
            ) as build,
        ):
            self.assertIs(fetch_hydrometric_discovery(object()), payload)
            self.assertIs(fetch_hydrometric_discovery(object()), payload)
            self.assertIs(fetch_hydrometric_discovery(object()), payload)
        self.assertEqual(build.call_count, 2)

    def test_observation_day_refresh_does_not_rebuild_discovery(self):
        discovery = {"identity": {"territory_fingerprint": "fp"}, "sites": []}
        payload = {"kind": "hydrometric_observations"}
        with patch(
            "digues_webapp.hydrometry.build_observation_dataset",
            return_value=payload,
        ) as build:
            fetch_hydrometric_observations(discovery, day_provider=lambda: date(2026, 10, 2))
            fetch_hydrometric_observations(discovery, day_provider=lambda: date(2026, 10, 2))
            fetch_hydrometric_observations(discovery, day_provider=lambda: date(2026, 10, 3))
        self.assertEqual(build.call_count, 2)


if __name__ == "__main__":
    unittest.main()
