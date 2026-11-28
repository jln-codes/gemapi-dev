"""Pure-Python safety and presentation tests for report queries."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app.report_data import (
    REPORT_FILTERS, ROW_FIELDS, build_management_query, build_report_query, group_name,
    grouped_entries, measure_lines, filter_option_queries,
    normalized_filters, own_totals, prepare_rows, report_warnings,
)


ID1 = "11111111-1111-1111-1111-111111111111"
ID2 = "22222222-2222-2222-2222-222222222222"


class ReportDataTests(unittest.TestCase):
    def test_zone_localisation_and_commune_use_effective_geometry(self):
        query = build_report_query({"communes":[ID1]}, include_geometry=True)
        self.assertIn("gemapi.prestations_carte carto WHERE carto.id=p.id", query)
        self.assertIn("ST_AsText(p.geom)",query)
        self.assertIn("p.geom IS NOT NULL",query)
        self.assertIn("gemapi.prestations_carte",build_management_query({"communes":[ID1]}))
        point={"has_geometry":False,"reference_measures":None,"reference_geometries":[{
            "famille":"vegetation","direct":True,"wkt":"POINT(0 0)"}],"quantite":7,"unite":"arbres"}
        self.assertIn("Géométrie ponctuelle du patrimoine",measure_lines(point))
        self.assertIn("Quantité déclarée : 7 arbres",measure_lines(point))

    def test_filters_and_select_only(self):
        filters = {"years": [2023, 2024], "ah_stockage": [ID1, ID2],
                   "cours_eau": [ID1], "completion": "incomplete"}
        query = build_report_query(filters)
        self.assertTrue(query.startswith("WITH relations AS"))
        self.assertNotIn("INSERT INTO", query)
        self.assertNotIn("UPDATE ", query.upper())
        self.assertIn("s.code IN ('PLANIFIE','EN_COURS','ANNULE')", query)
        self.assertNotIn("p.statut,", query)
        self.assertIn("JOIN gemapi.statuts_prestations s", query)
        self.assertIn("EXTRACT(YEAR FROM p.date_debut)", query)
        self.assertIn("AND chosen_r.famille=E'ah_stockage'", query)
        self.assertIn("AND chosen_r.famille='cours_eau'", query)
        self.assertIn("OR", query)
        self.assertIn("make_date(2024", query)
        geo_query = build_report_query({}, include_geometry=True)
        self.assertIn("ST_AsText(p.geom)", geo_query)
        self.assertIn("ST_AsText(r.geom)", geo_query)
        self.assertIn("FROM relations WHERE prestation_id=p.id", geo_query)
        self.assertIn("false AS direct", query)
        self.assertIn("s.cours_eau_id=p.cours_eau_id", query)
        self.assertIn("s.etat_reference='CONFORME'", query)
        self.assertIn("s.cours_eau_qualifie IS TRUE", query)
        self.assertIn("ST_Intersects(p.geom,s.geom_reference)", query)
        self.assertNotIn("ST_DWithin", query)
        indirect = query.split("NULL::geometry AS geom, false AS direct")[1].split(") SELECT p.id")[0]
        self.assertNotIn("ST_GeometryType", indirect)
        self.assertNotIn("ST_Dimension", indirect)
        self.assertIn("o.id=l.ah_section_id", build_management_query({"ah": [ID1]}))
        self.assertNotIn("ST_Intersects(p.geom,s.geom_reference)", build_management_query({"ah": [ID1]}))
        self.assertIn("count(DISTINCT p.id)", build_report_query({}, count_only=True))
        self.assertNotIn("INSERT INTO", geo_query)

    def test_ah_scope_overlap_and_duplicate_totals(self):
        record = dict(id=ID1, own_length=20, own_area=None, parents=[
            {"kind": "cours_eau", "id": ID1, "nom": "Rivière", "direct": True},
            {"kind": "ah", "id": ID1, "nom": "AH A", "direct": False},
            {"kind": "ah", "id": ID2, "nom": "AH B", "direct": False}])
        self.assertEqual(own_totals([record, record]), (20, 0))
        self.assertEqual(len(grouped_entries([record, record], ())), 1)
        self.assertEqual(len(grouped_entries([record, record], ("ah",))), 2)
        self.assertTrue(any("groupes AH" in w for w in report_warnings([record], {"groups": ["ah"]})))
        record["parents"][-1]["in_scope"] = False
        self.assertEqual(len(grouped_entries([record], ("ah",))), 1)
        self.assertFalse(any("groupes AH" in w for w in report_warnings([record], {"groups": ["ah"]})))

    def test_business_labels_hide_keys_without_merging_homonyms(self):
        from gemapi_app.report_data import (group_label, group_values,
            direct_attachment_label, ah_recognition_lines)
        for criterion in ("ah", "cours_eau", "digues", "site_terrestre"):
            for identifier in (ID1,ID2):
                self.assertEqual(group_label(criterion,f"Même nom [{identifier}]"),"Même nom")
        self.assertEqual(direct_attachment_label({"objects":"cours_eau : La Clarence"}),
                         "La Clarence (cours d’eau)")
        rows=[dict(id=i,parents=[dict(kind="ah",id=i,nom="Même AH",direct=False,
               section_ids=[ID1])]) for i in (ID1,ID2)]
        self.assertNotEqual(group_values(rows[0],"ah"),group_values(rows[1],"ah"))
        self.assertEqual(len(grouped_entries(rows,("ah",))),2)
        displayed=" ".join(ah_recognition_lines(rows[0]))
        self.assertIn("Même AH",displayed)
        self.assertIn("reconnaissance indirecte",displayed)
        self.assertNotIn(ID1,displayed)

    def test_rejects_invalid_filters(self):
        for filters in ({"ah_stockage": ["not-uuid"]}, {"years": [0]},
                        {"statuses": ["not-uuid"]},
                        {"providers": ["not-uuid"]},
                        {"groups": ("type", "type")},
                        {"groups": ("year", "type", "status", "commune")},
                        {"groups": ("unknown",)}, {"completion": "done"},
                        {"unexpected": [ID1]}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                normalized_filters(filters)

    def test_management_uses_shared_rules_and_literal_search(self):
        unfiltered = build_management_query({})
        self.assertNotIn("WITH relations AS", unfiltered)
        self.assertIn("DISTINCT ON (p.id)", unfiltered)
        query = build_management_query(
            {"years": [2027], "ah_stockage": [ID1, ID2], "statuses": [ID1],
             "communes": [ID2]}, "L'étang")
        self.assertIn("WITH relations AS", query)
        self.assertIn("AND chosen_r.famille=E'ah_stockage'", query)
        self.assertIn("selected_c.id IN", query)
        self.assertIn("p.statut_id IN", query)
        self.assertIn("E'L''étang'", query)
        self.assertIn("s.code = 'TERMINE'", query)
        self.assertNotIn("UPDATE ", query.upper())
        with self.assertRaises(ValueError):
            build_management_query({}, "x" * 201)

    def test_management_ah_children_are_alternatives_only_for_management(self):
        filters = {"ah_stockage": [ID1], "ah_ouvrage": [ID2], "years": [2027]}
        query = build_management_query(filters)
        self.assertIn("chosen_r.famille=E'ah_stockage'", query)
        self.assertIn("OR (chosen_r.famille=E'ah_ouvrage'", query)
        self.assertIn("EXTRACT(YEAR FROM p.date_debut)", query)
        self.assertIn("DISTINCT ON (p.id)", query)
        report_query = build_report_query(filters)
        self.assertNotIn("OR (chosen_r.famille=E'ah_ouvrage'", report_query)

    def test_provider_filter_is_optional_multiselect_and_combines_with_status(self):
        self.assertNotIn("providers", filter_option_queries())
        self.assertIn("providers", filter_option_queries(include_providers=True))
        baseline = build_management_query({})
        self.assertNotIn("p.prestataire_id IN", baseline)
        query = build_management_query({"providers": [ID2, ID1, ID1],
                                        "statuses": [ID1]})
        self.assertIn("p.prestataire_id IN (E'11111111-1111-1111-1111-111111111111'::uuid, "
                      "E'22222222-2222-2222-2222-222222222222'::uuid)", query)
        self.assertIn(" AND p.prestataire_id IN", query)
        self.assertIn("p.statut_id IN", query)

    def test_current_patrimonial_families_and_parent_deduplication(self):
        self.assertEqual(set(REPORT_FILTERS) &
                         {"ah_stockage", "ah_ouvrage", "vegetation"}, set())
        self.assertTrue({"cours_eau", "digues", "ah", "site_terrestre"}
                        <= set(REPORT_FILTERS))
        options = filter_option_queries(include_statuses=True, include_providers=True)
        for family in ("ah_stockage", "ah_ouvrage", "cours_eau", "digues", "vegetation"):
            self.assertIn(family, options)
            self.assertIn(f"gemapi.{family}", options[family])
            self.assertIn("gemapi.prestations l",
                          build_report_query({family: [ID1]}))
        for parent, parent_kind in (("ah", "ah"), ("site_terrestre", "site_terrestre")):
            query = build_report_query({parent: [ID1]}, count_only=True)
            self.assertIn(f"chosen_r.parent_kind=E'{parent_kind}'", query)
            self.assertIn("count(DISTINCT p.id)", query)
        digue = build_report_query({"digues": [ID1]}, count_only=True)
        self.assertIn("chosen_r.famille=E'digues'", digue)
        self.assertIn("JOIN gemapi.digues_reperes dr", digue)
        self.assertNotIn("ST_DWithin", digue)
        self.assertNotIn("type_ecoulement", options["cours_eau"])
        self.assertNotIn("IS NOT NULL", options["cours_eau"])
        self.assertEqual(options["cours_eau"], "SELECT c.id::text,c.nom FROM gemapi.cours_eau c ORDER BY 2,1")
        self.assertNotIn("fosses", options)
        record = {"id": ID1, "date_debut": "2027-01-01", "type": "Travaux",
                  "own_length": 12.0, "own_area": None,
                  "parents": [{"kind": "ah", "id": ID2, "nom": "AH A"},
                              {"kind": "ah", "id": ID2, "nom": "AH A"}]}
        entries = grouped_entries([record], ("ah",))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0][0][2], 1)

    def test_deduplication_and_measure_sources(self):
        row = [None] * len(ROW_FIELDS)
        values = {"id": ID1, "intitule": "Prestation ponctuelle", "type": "Type",
                  "technician": "Tech", "provider": "Prestataire",
                  "has_geometry": True, "reference_measures": "Cours d'eau : 5 m; ZEC : 20 m²",
                  "reference_count": 2, "communes": "A; B"}
        for key, value in values.items():
            row[ROW_FIELDS.index(key)] = value
        records = prepare_rows([row, row])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["reference_geometries"], [])
        self.assertEqual(own_totals(records), (0, 0))
        self.assertIn("Géométrie ponctuelle", measure_lines(records[0])[0])
        self.assertEqual(group_name(records[0], "commune"), "Plusieurs communes")
        self.assertEqual(group_name(records[0], "type"), "Type")
        self.assertEqual(group_name(records[0], "technician"), "Tech")
        self.assertEqual(group_name(records[0], "none"), "Toutes les prestations")
        self.assertFalse(any("AMB-01" in item for item in report_warnings(records, {})))
        self.assertTrue(any("Envisagée" in item for item in
                            report_warnings(records, {}, ("Envisagée",))))

    def test_declared_quantities_and_cartographed_measures_are_distinct(self):
        cases = (
            ({"has_geometry": True, "quantite": 10, "unite": "arbres"},
             ["Géométrie ponctuelle", "Quantité déclarée : 10 arbres"]),
            ({"own_length": 209.4, "quantite": 4, "unite": "interventions"},
             ["Longueur cartographiée : 209,4 m", "Quantité déclarée : 4 interventions"]),
            ({"own_length": 209.4, "quantite": 10, "unite": "m"},
             ["Longueur cartographiée : 209,4 m", "Quantité déclarée : 10 m"]),
            ({"own_area": 375.2, "quantite": 2, "unite": "ha"},
             ["Surface cartographiée : 375,2 m²", "Quantité déclarée : 2 ha"]),
            ({"own_length": 209.4}, ["Longueur cartographiée : 209,4 m"]),
            ({"site_child_area": 1250, "quantite": 7, "unite": "arbres"},
             ["Surface du patrimoine (géométrie parente) : 1 250,0 m²",
              "Quantité déclarée : 7 arbres"]),
            ({"site_child_length": 209.45, "quantite": 2, "unite": "ha"},
             ["Longueur du patrimoine (géométrie parente) : 209,4 m",
              "Quantité déclarée : 2 ha"]),
            ({"has_geometry": False, "reference_geometries": [
                {"famille": "vegetation", "direct": True, "wkt": "MULTIPOINT((0 0))"}]},
             ["Géométrie ponctuelle du patrimoine"]),
            ({"own_area": 375.2, "site_child_area": 1250},
             ["Surface cartographiée : 375,2 m²",
              "Surface du patrimoine (géométrie parente) : 1 250,0 m²"]),
        )
        for record, expected in cases:
            with self.subTest(record=record):
                self.assertEqual(measure_lines(record), expected)
        overlapping = [dict(own_length=209.4), dict(own_length=209.4)]
        self.assertEqual(own_totals(overlapping), (418.8, 0))
        query = build_report_query({})
        self.assertIn("p.origine_mesures='Patrimoine de rattachement' THEN p.longueur_m", query)
        self.assertIn("p.origine_mesures='Patrimoine de rattachement' THEN p.surface_m2", query)
        self.assertIn("JOIN gemapi.vegetation o ON o.id = l.vegetation_id", query)

    def test_hierarchies_count_distinct_uuids_and_keep_start_year(self):
        first = dict(id=ID1, date_debut="2025-12-15", date_fin="2027-02-01",
                     type="Entretien", technician="Alice", provider="Entreprise A",
                     statut="Terminé", communes="A; B", own_length=10, own_area=None,
                     has_geometry=True, reference_count=0)
        second = dict(id=ID2, date_debut="2026-05-01", date_fin="2026-06-01",
                      type="Entretien", technician="Bob", provider="Entreprise B",
                      statut="Planifié", communes="B", own_length=20, own_area=None,
                      has_geometry=True, reference_count=0)
        records = [first, second]
        self.assertEqual(len(grouped_entries(records, ())), 2)
        one = grouped_entries(records, ("commune",))
        self.assertEqual(len(one), 3)
        self.assertEqual({path[0][1] for path, _ in one}, {"A", "B"})
        self.assertEqual(next(path[0][2:] for path, record in one
                              if path[0][1] == "B"), (2, 30, 0))
        two = grouped_entries(records, ("year", "commune"))
        self.assertEqual(len(two), 3)
        self.assertEqual({path[0][1] for path, record in two if record["id"] == ID1},
                         {"2025"})
        three = grouped_entries(records, ("type", "commune", "status"))
        self.assertEqual(len(three), 3)
        self.assertNotEqual([node[0] for node in three[0][0]],
                            [node[0] for node in two[0][0]])
        self.assertEqual({record["id"] for _, record in three}, {ID1, ID2})
        self.assertEqual(own_totals(records), (30, 0))
        self.assertTrue(any("total général" in warning for warning in
                            report_warnings(records, {"groups": ("commune",)})))


if __name__ == "__main__":
    unittest.main()
