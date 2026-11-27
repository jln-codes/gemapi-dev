from copy import deepcopy
import unittest

from digues_webapp.hydrometry import (
    HydrometryDataError,
    _finalize_territory_relations,
    normalize_territory_relations,
)


def raw_relation(
    *,
    role="upstream",
    crossing="cross-a",
    path="path-a",
    state="selected",
    topology="directed",
    continuity="supported",
    connections=None,
):
    return {
        "territory": {"id": "T", "fingerprint": "territory-fingerprint"},
        "role": role,
        "selection": {
            "method": (
                "inside_exact_polygon"
                if role == "inside"
                else f"first_active_{role}"
            ),
            "state": state,
            "activity_policy_id": "activity/v1",
            "calculated_at": "2026-10-03T00:00:00Z",
        },
        "network_evidence": {
            "source": "BD TOPAGE",
            "dataset_version": "2026",
            "discovery_algorithm_version": "discovery/v1",
            "direction": None if role == "inside" else role,
            "topology_status": topology,
            "hydraulic_continuity": continuity,
            "crossing": None if role == "inside" else {
                "crossing_event_id": crossing,
                "reference_edge_id": f"edge-{crossing}",
            },
            "site_attachment": None if role == "inside" else {
                "reference_edge_id": "attachment-edge",
                "method": "spatial_with_explicit_tolerance",
                "distance_m": 3.0,
                "station_code": "S1",
            },
            "traversed_edge_count": 4 if role != "inside" else 0,
            "hydrographic_distance_m": 1200.0 if role != "inside" else 0.0,
            "path_fingerprint": None if role == "inside" else path,
            "conditional_connections": list(connections or []),
        },
    }


def site():
    return {
        "code_site": "SITE",
        "inside_territory": False,
        "stations": [{"code_station": "S1"}],
    }


class TerritoryRelationNormalizationTest(unittest.TestCase):
    def test_two_proofs_for_same_site_role_make_one_relation(self):
        relations = normalize_territory_relations("SITE", [
            raw_relation(crossing="cross-a", path="path-a"),
            raw_relation(crossing="cross-b", path="path-b"),
        ])

        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0]["proof_summary"]["count"], 2)
        self.assertEqual(
            {proof["path_fingerprint"] for proof in relations[0]["proofs"]},
            {"path-a", "path-b"},
        )

    def test_two_roles_for_same_site_remain_two_relations(self):
        relations = normalize_territory_relations("E3611210", [
            raw_relation(role="upstream"),
            raw_relation(role="downstream", crossing="cross-b", path="path-b"),
        ])
        self.assertEqual(
            {relation["role"] for relation in relations},
            {"upstream", "downstream"},
        )

    def test_supported_proof_establishes_relation_despite_conditional_proof(self):
        connection = {"connection_id": "regulated"}
        relations = normalize_territory_relations("SITE", [
            raw_relation(continuity="supported"),
            raw_relation(
                crossing="cross-b",
                path="path-b",
                state="pending_review",
                continuity="conditional",
                connections=[connection],
            ),
        ])
        relation = relations[0]
        self.assertEqual(relation["selection"]["state"], "selected")
        self.assertEqual(relation["hydraulic_continuity"], "supported")
        self.assertEqual(relation["proof_summary"]["pending_review_count"], 1)
        self.assertEqual(
            relation["proof_summary"]["conditional_connection_ids"],
            ["regulated"],
        )

    def test_supported_proof_establishes_relation_despite_pending_proof(self):
        pending = raw_relation(
            crossing="cross-b", path="path-b", state="pending_review",
            topology="ambiguous", continuity="unknown",
        )
        relation = normalize_territory_relations(
            "SITE", [raw_relation(continuity="supported"), pending]
        )[0]
        self.assertEqual(relation["selection"]["state"], "selected")
        self.assertEqual(relation["topology_status"], "directed")

    def test_only_pending_proofs_leave_relation_pending(self):
        relation = normalize_territory_relations("SITE", [raw_relation(
            state="pending_review",
            continuity="conditional",
            connections=[{"connection_id": "regulated"}],
        )])[0]
        self.assertEqual(relation["selection"]["state"], "pending_review")
        self.assertEqual(relation["hydraulic_continuity"], "conditional")

    def test_conditional_and_unknown_proofs_aggregate_to_unknown(self):
        relation = normalize_territory_relations("SITE", [
            raw_relation(
                state="pending_review",
                continuity="conditional",
                connections=[{"connection_id": "regulated"}],
            ),
            raw_relation(
                crossing="cross-b",
                path="path-b",
                state="pending_review",
                continuity="unknown",
            ),
        ])[0]
        self.assertEqual(relation["selection"]["state"], "pending_review")
        self.assertEqual(relation["hydraulic_continuity"], "unknown")

    def test_multiple_conditional_connections_are_preserved(self):
        connections = [
            {"connection_id": "regulated-a", "fact": "A"},
            {"connection_id": "regulated-b", "fact": "B"},
        ]
        relation = normalize_territory_relations("SITE", [raw_relation(
            state="pending_review",
            continuity="conditional",
            connections=connections,
        )])[0]
        self.assertEqual(
            relation["proofs"][0]["conditional_connection_ids"],
            ["regulated-a", "regulated-b"],
        )
        self.assertEqual(relation["conditional_connections"], connections)

    def test_conflicting_qualifications_are_not_silently_overwritten(self):
        first = raw_relation(connections=[{
            "connection_id": "regulated", "fact": "A",
        }])
        second = raw_relation(
            crossing="cross-b",
            path="path-b",
            connections=[{"connection_id": "regulated", "fact": "B"}],
        )
        with self.assertRaises(HydrometryDataError):
            normalize_territory_relations("SITE", [first, second])

    def test_inside_duplicates_collapse_without_topage_crossing(self):
        inside = raw_relation(
            role="inside", topology="not_applicable", continuity="not_assessed"
        )
        relation = normalize_territory_relations(
            "SITE", [inside, deepcopy(inside)]
        )[0]
        self.assertEqual(relation["proof_summary"]["count"], 1)
        self.assertIsNone(relation["proofs"][0]["crossing"])
        self.assertEqual(
            relation["provenance"]["source"], "territory_exact_geometry"
        )

    def test_finalize_preserves_mixed_review_without_marking_relation_pending(self):
        selected = raw_relation()
        pending = raw_relation(
            crossing="cross-b",
            path="path-b",
            state="pending_review",
            continuity="conditional",
            connections=[{"connection_id": "regulated"}],
        )
        candidates, selected_sites, reviews, roles, counts = (
            _finalize_territory_relations(
                {"SITE": site()}, {"SITE": [selected, pending]}
            )
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(selected_sites), 1)
        self.assertEqual(roles["upstream"], {"SITE"})
        self.assertEqual(reviews[0]["scope"], "proofs")
        self.assertEqual(counts["pending_review_proofs"], 1)
        self.assertEqual(counts["pending_review_relations"], 0)
        self.assertEqual(counts["mixed_review_relations"], 1)

    def test_pending_only_relation_keeps_full_proofs_outside_selected_sites(self):
        pending = raw_relation(
            state="pending_review",
            continuity="conditional",
            connections=[{"connection_id": "regulated"}],
        )
        _candidates, selected_sites, reviews, _roles, counts = (
            _finalize_territory_relations(
                {"SITE": site()}, {"SITE": [pending]}
            )
        )
        self.assertEqual(selected_sites, [])
        self.assertEqual(reviews[0]["scope"], "relation")
        self.assertEqual(len(reviews[0]["relation"]["proofs"]), 1)
        self.assertEqual(counts["territory_relations"], 0)
        self.assertEqual(counts["candidate_territory_relations"], 1)


if __name__ == "__main__":
    unittest.main()
