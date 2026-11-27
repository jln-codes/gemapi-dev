import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from digues_webapp.data_views import (
    DATA_VIEW_MAX_EXPORT_ROWS,
    DATA_VIEW_MAX_ROWS,
    HOME_VIEW_FIELDS,
    HOME_VIEW_ROOT,
    PILOT_ROOTS,
    VIEW_CHAIN,
    VIEW_FIELDS,
    VIEW_FIELDS_BY_KEY,
    VIEW_CLASSES,
    DataViewExportLimitError,
    DataViewValidationError,
    build_view_query,
    effective_granularity,
    execute_home_view,
    execute_view_export,
    execute_view_query,
    relation_path,
    view_catalog,
)
from tests.geometry_fixtures import LINE_SQL, POINT_SQL
from tests.postgres_safety import connect_test_database, require_test_dsn
from digues_webapp.models import (
    DATA_VIEW_MAX_FIELDS,
    DataViewFieldSelection,
    DataViewQueryRequest,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []
        self.current_query = ""
        self.current_params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.executions.append((query, params))
        self.current_query = query
        self.current_params = params

    def _filtered_rows(self):
        hidden = set(map(str, (self.current_params or ([],))[0] or []))
        return [
            row for row in self.rows
            if not hidden.intersection(
                str(entity.get("id"))
                for entity in (row[0] or [])
                if entity.get("class") == "troncons"
            )
        ]

    def fetchone(self):
        return (len(self._filtered_rows()),)

    def fetchall(self):
        rows = self._filtered_rows()
        if self.current_params and len(self.current_params) == 3:
            _hidden, limit, offset = self.current_params
            return rows[offset:offset + limit]
        return rows


class FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)

    def transaction(self):
        return FakeTransaction()

    def cursor(self):
        return self.cursor_instance


def selections(*keys):
    return [
        DataViewFieldSelection(table=key.split(".", 1)[0], field=key.split(".", 1)[1])
        for key in keys
    ]


class DataViewsTest(unittest.TestCase):
    EXPECTED_FIELDS = {
        "systemes": [
            "libelle", "designation", "commentaire", "population_protegee",
            "classement", "niveau_protection", "valid",
        ],
        "digues": ["libelle", "designation", "commentaire", "valid"],
        "troncons": [
            "libelle", "type_physique_id", "type_physique", "digue_id", "amenagement_hydraulique_id", "amenagement_hydraulique", "designation", "date_debut", "date_fin", "commentaire",
            "rive", "type", "typologie", "systeme_reperage", "valid",
        ],
        "desordres": [
            "designation", "type", "categorie", "commentaire", "lieu_dit",
            "cote", "position", "source", "date_debut", "date_fin", "valid",
        ],
        "observations": [
            "designation", "date", "urgence", "evolution", "suite",
            "suite_apporter", "nombre_desordres", "valid",
        ],
        "photos": [
            "libelle", "designation", "date", "commentaire", "cote",
            "orientation", "systeme_reperage", "borne_debut", "sens_debut",
            "distance_debut", "pr_debut", "borne_fin", "sens_fin",
            "distance_fin", "pr_fin", "valid",
        ],
        "mobilier": ["designation", "type", "commentaire", "date_debut", "troncon",
                     "digue", "systeme", "localisation", "valid"],
        "equipements_mesure": ["designation", "type", "commentaire", "date_debut",
                               "troncon", "digue", "systeme", "localisation", "valid"],
        "reseaux_techniques": ["designation", "type", "commentaire", "date_debut",
                               "troncon", "digue", "systeme", "localisation", "valid"],
        "ouvrages_particuliers": ["designation", "libelle", "commentaire", "date_debut",
                                   "date_fin", "type", "troncon",
                                   "digue", "systeme", "localisation", "valid"],
        "cheminements": ["designation", "type", "commentaire", "date_debut", "troncons",
                         "desordres", "localisation", "valid"],
        "vegetation": ["designation", "famille", "disposition", "invasif", "commentaire", "date_debut", "troncons",
                       "amenagements", "localisation", "valid"],
        "amenagements_hydrauliques": ["designation", "commentaire", "date_debut",
                                     "troncons", "zones", "valid"],
        "zones_stockage": ["designation", "type", "fonctionnement", "capacite",
                           "superficie", "profondeur", "commentaire", "ah",
                           "localisation", "valid"],
        "prestations": ["designation", "type", "commentaire", "date_debut", "date_fin",
                        "troncons", "intervenants", "objets", "localisation"],
        "troncons_lit": ["designation", "libelle", "commentaire", "date_debut", "date_fin", "valid", "digues"],
        "elements_morphologiques_lit": ["designation", "libelle", "commentaire", "date_debut", "date_fin", "valid", "type_morphologie", "troncon_lit", "longueur", "largeur", "rattache_berge", "vegetalise", "type_zone"],
    }

    @staticmethod
    def sql_for(root, *keys):
        return build_view_query(root, [VIEW_FIELDS_BY_KEY[key] for key in keys])

    def test_zone_root_is_accepted_by_the_http_request_contract(self):
        request = DataViewQueryRequest.model_validate({
            "root": "zones_stockage",
            "fields": [{"table": "zones_stockage", "field": "designation"}],
        })
        self.assertEqual(request.root, "zones_stockage")
        with self.assertRaises(ValidationError):
            DataViewQueryRequest.model_validate({
                "root": "zone_stockage_inconnue",
                "fields": [{"table": "zones_stockage", "field": "designation"}],
            })

    def test_catalog_declares_roots_relations_and_only_real_business_fields(self):
        catalog = view_catalog()
        self.assertEqual(catalog["default_root"], "desordres")
        self.assertEqual([item["id"] for item in catalog["roots"]], [*VIEW_CHAIN, *PILOT_ROOTS])
        self.assertEqual(
            [(item["parent"], item["child"]) for item in catalog["relations"]],
            list(zip(VIEW_CHAIN, VIEW_CHAIN[1:])),
        )
        self.assertEqual([table["id"] for table in catalog["tables"]], [*VIEW_CHAIN, *PILOT_ROOTS])
        fields = {
            table["id"]: [field["id"] for field in table["fields"]]
            for table in catalog["tables"]
        }
        self.assertEqual(fields, self.EXPECTED_FIELDS)
        self.assertNotIn("systemes.nom", str(catalog))
        self.assertNotIn("expression", str(catalog).casefold())
        self.assertNotIn("sql", str(catalog).casefold())
        identities = [
            field for table in catalog["tables"] for field in table["fields"]
            if field["id"] in {"designation", "libelle"}
            and table["id"] in {"systemes", "digues", "troncons", "desordres", "observations"}
        ]
        self.assertTrue(all(field["navigable"] for field in identities))
        unsupported_details = [
            field for table in catalog["tables"] for field in table["fields"]
            if field["id"] in {"designation", "libelle"}
            and table["id"] in {"photos"}
        ]
        self.assertTrue(all(not field["navigable"] for field in unsupported_details))

    def test_catalog_metadata_matches_closed_field_definitions(self):
        catalog_fields = {
            f"{table['id']}.{field['id']}": field
            for table in view_catalog()["tables"] for field in table["fields"]
        }
        self.assertEqual(set(catalog_fields), {field.key for field in VIEW_FIELDS})
        self.assertEqual(len(VIEW_FIELDS), len({field.key for field in VIEW_FIELDS}))
        self.assertTrue(all(
            len(table["fields"]) <= DATA_VIEW_MAX_FIELDS
            for table in view_catalog()["tables"]
        ))
        for field in VIEW_FIELDS:
            metadata = catalog_fields[field.key]
            self.assertEqual(metadata["source_class"], field.table)
            self.assertEqual(metadata["source_field"], field.field)
            if field.field.endswith("_id"):
                self.assertIn(field.key, {"troncons.type_physique_id", "troncons.digue_id", "troncons.amenagement_hydraulique_id"})
            self.assertNotIn("source_v2", metadata)
            self.assertFalse(field.field.endswith("_source_id"), field.key)

    def test_request_requires_an_explicit_supported_root(self):
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(fields=[
                {"table": "desordres", "field": "designation"},
            ])
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(root="equipements", fields=[
                {"table": "desordres", "field": "designation"},
            ])
        request = DataViewQueryRequest(root="troncons", fields=[
            {"table": "troncons", "field": "designation"},
        ])
        self.assertEqual(request.root, "troncons")
        for root in ("mobilier", "equipements_mesure", "reseaux_techniques",
                     "ouvrages_particuliers"):
            with self.subTest(root=root):
                parsed = DataViewQueryRequest(root=root, fields=[
                    {"table": root, "field": "designation"},
                ])
                self.assertEqual(parsed.root, root)

    def test_pilot_roots_have_isolated_bounded_query_plans(self):
        for root in PILOT_ROOTS[:4]:
            with self.subTest(root=root):
                catalog = next(item for item in view_catalog()["tables"] if item["id"] == root)
                self.assertEqual(catalog["compatible_roots"], [root])
                page, granularity = self.sql_for(root, f"{root}.designation", f"{root}.troncon")
                self.assertEqual(granularity, root)
                self.assertIn(f"FROM public.{root} AS ", page)
                self.assertIn("LEFT JOIN public.troncons AS t", page)
                self.assertIn("LIMIT %s OFFSET %s", page)
                self.assertNotIn("observations", page)
                self.assertNotIn("__troncons", page)
                if root == "ouvrages_particuliers":
                    self.assertNotIn("ON op.type_id", page)
                with self.assertRaises(DataViewValidationError):
                    self.sql_for(root, f"{root}.designation", "desordres.designation")

    def test_multi_relation_roots_use_aggregate_scope_without_multiplying_rows(self):
        links = {
            "cheminements": "public.cheminements__troncons",
            "vegetation": "public.vegetation__troncons",
            "prestations": "public.prestations_troncons",
        }
        for root, table in links.items():
            with self.subTest(root=root):
                query, granularity = self.sql_for(root, f"{root}.designation", f"{root}.troncons")
                self.assertEqual(granularity, root)
                self.assertIn(f"FROM public.{root} AS ", query)
                self.assertIn(f"FROM {table} AS link", query)
                self.assertIn("LEFT JOIN LATERAL", query)
                self.assertIn("array_agg(DISTINCT t.id)", query)
                self.assertIn("&& %s::uuid[]", query)
                self.assertLess(query.index("&& %s::uuid[]"), query.index("LIMIT %s"))
                with self.assertRaises(DataViewValidationError):
                    self.sql_for(root, f"{root}.designation", "desordres.designation")

        ah_query, _ = self.sql_for("amenagements_hydrauliques",
                                    "amenagements_hydrauliques.designation",
                                    "amenagements_hydrauliques.troncons")
        self.assertIn("FROM public.troncons tu WHERE tu.amenagement_hydraulique_id=a.id", ah_query)
        self.assertNotIn("amenagements_hydrauliques__troncons", ah_query)
        zone_query, _ = self.sql_for("zones_stockage", "zones_stockage.designation")
        self.assertIn("FROM public.zones_stockage z", zone_query)
        self.assertNotIn("zones_stockage__troncons", zone_query)

    def test_request_rejects_empty_duplicate_extra_and_excess_fields(self):
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(root="desordres", fields=[])
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(root="desordres", fields=[
                {"table": "desordres", "field": "designation"},
                {"table": "desordres", "field": "designation"},
            ])
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(root="desordres", fields=[{
                "table": "desordres", "field": "designation", "sql": "DROP TABLE x",
            }])
        arbitrary = [
            {"table": "catalogue", "field": f"field_{index:03d}"}
            for index in range(DATA_VIEW_MAX_FIELDS + 1)
        ]
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(root="desordres", fields=arbitrary)

    def test_request_bounds_page_and_patrimoine_payload(self):
        field = [{"table": "troncons", "field": "libelle"}]
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(root="troncons", fields=field, page_size=2_001)
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(root="troncons", fields=field, offset=-1)
        duplicate = "00000000-0000-0000-0000-000000000001"
        with self.assertRaises(ValidationError):
            DataViewQueryRequest(
                root="troncons",
                fields=field,
                hidden_troncon_ids=[duplicate, duplicate],
            )

    def test_relation_paths_are_explicit_and_bidirectional(self):
        self.assertEqual(
            relation_path("troncons", "photos"),
            ("troncons", "desordres", "observations", "photos"),
        )
        self.assertEqual(
            relation_path("observations", "digues"),
            ("observations", "desordres", "troncons", "digues"),
        )

    def test_each_root_starts_from_itself_without_a_selected_child(self):
        identity = {
            "systemes": "libelle", "digues": "libelle", "troncons": "libelle",
            "desordres": "designation", "observations": "designation",
            "photos": "libelle",
        }
        for root, field in identity.items():
            with self.subTest(root=root):
                query, granularity = self.sql_for(root, f"{root}.{field}")
                self.assertEqual(granularity, root)
                self.assertIn(f"FROM public.{root} AS", query)

    def test_system_digue_and_troncon_roots_do_not_depend_on_disorders(self):
        cases = (
            ("systemes", ("systemes.libelle",)),
            ("digues", ("systemes.libelle", "digues.libelle")),
            ("troncons", ("systemes.libelle", "digues.libelle", "troncons.libelle")),
        )
        for root, keys in cases:
            with self.subTest(root=root):
                query, _granularity = self.sql_for(root, *keys)
                self.assertNotIn("public.desordres", query)
                self.assertNotIn("link_desordres_troncons", query)

    def test_system_and_digue_rows_expose_their_troncon_scope_for_patrimoine(self):
        system_query, _ = self.sql_for("systemes", "systemes.libelle")
        digue_query, _ = self.sql_for("digues", "digues.libelle")
        for query in (system_query, digue_query):
            self.assertIn("AS scope_troncons", query)
            self.assertIn("'class', 'troncons'", query)
        self.assertIn("scope_digue.systeme_endiguement_id = s.id", system_query)
        self.assertIn("t.digue_id = di.id", digue_query)

    def test_deepest_selected_child_defines_granularity(self):
        selected = {"systemes", "digues", "troncons"}
        self.assertEqual(effective_granularity("troncons", selected), "troncons")
        self.assertEqual(
            effective_granularity("troncons", selected | {"desordres"}), "desordres"
        )
        self.assertEqual(
            effective_granularity(
                "troncons", selected | {"desordres", "observations"}
            ), "observations",
        )
        self.assertEqual(effective_granularity("troncons", set(VIEW_CHAIN)), "photos")

    def test_child_granularity_uses_real_rows_not_aggregated_children(self):
        cases = (
            ("desordres", ("troncons.libelle", "desordres.designation"), "d"),
            ("observations", (
                "troncons.libelle", "desordres.designation",
                "observations.designation",
            ), "o"),
            ("photos", (
                "troncons.libelle", "desordres.designation",
                "observations.designation", "photos.libelle",
            ), "p"),
        )
        for expected, keys, alias in cases:
            with self.subTest(granularity=expected):
                query, granularity = self.sql_for("troncons", *keys)
                self.assertEqual(granularity, expected)
                self.assertIn(f"FROM public.{expected} AS {alias}", query)
                self.assertNotRegex(
                    query,
                    rf"string_agg\s*\([^)]*\b{alias}\.(designation|libelle)",
                )

    def test_photo_rows_keep_exact_observation_and_disorder_provenance(self):
        entities_one = [
            {"class": "troncons", "id": "t1", "geometry": None, "attributes": {}},
            {"class": "desordres", "id": "d1", "geometry": None, "attributes": {}},
            {"class": "observations", "id": "o1", "geometry": None, "attributes": {}},
            {"class": "photos", "id": "p1", "geometry": None, "attributes": {}},
        ]
        entities_two = [
            {"class": "troncons", "id": "t1", "geometry": None, "attributes": {}},
            {"class": "desordres", "id": "d2", "geometry": None, "attributes": {}},
            {"class": "observations", "id": "o2", "geometry": None, "attributes": {}},
            {"class": "photos", "id": "p2", "geometry": None, "attributes": {}},
        ]
        connection = FakeConnection([
            (entities_one, "T1", "D1", "O1", "P1"),
            (entities_two, "T1", "D2", "O2", "P2"),
        ])
        result = execute_view_query(connection, "troncons", selections(
            "troncons.libelle", "desordres.designation",
            "observations.designation", "photos.libelle",
        ))
        self.assertEqual(result["granularity"], "photos")
        self.assertEqual(result["rows"][0]["values"], ["T1", "D1", "O1", "P1"])
        self.assertEqual(result["rows"][1]["values"], ["T1", "D2", "O2", "P2"])
        self.assertEqual(
            [[entity["id"] for entity in row["entities"][-3:]]
             for row in result["rows"]],
            [["d1", "o1", "p1"], ["d2", "o2", "p2"]],
        )

    def test_parent_values_repeat_on_each_child_row(self):
        connection = FakeConnection([
            ([], "T1", "D1", "P1"),
            ([], "T1", "D1", "P2"),
        ])
        result = execute_view_query(connection, "troncons", selections(
            "troncons.libelle", "desordres.designation", "photos.libelle",
        ))
        self.assertEqual(
            [row["values"][:2] for row in result["rows"]],
            [["T1", "D1"], ["T1", "D1"]],
        )

    def test_parallel_descendant_branches_are_rejected_with_business_labels(self):
        fields = selections(
            "desordres.designation", "equipements_mesure.designation"
        )
        connection = FakeConnection([])
        with self.assertRaisesRegex(
            DataViewValidationError, "Désordres.*Équipements de mesure"
        ):
            execute_view_query(connection, "troncons", fields)
        self.assertEqual(connection.cursor_instance.executions, [])

    def test_observation_roots_derive_troncons_for_all_real_parent_types(self):
        query, _granularity = self.sql_for(
            "observations", "troncons.libelle", "observations.designation"
        )
        for relation in (
            "o.troncon_id", "ouvrages_hydrauliques", "equipements_mesure",
            "public.cheminements__troncons", "mobilier", "reseaux_techniques",
            "public.vegetation__troncons",
        ):
            self.assertIn(relation, query)
        self.assertNotIn("amenagements_hydrauliques__troncons", query)

    def test_query_is_read_only_bounded_and_preserves_field_order(self):
        connection = FakeConnection([([], "Type A", "Désordre A")])
        result = execute_view_query(connection, "desordres", selections(
            "desordres.type", "desordres.designation",
        ))
        self.assertEqual(
            [column["key"] for column in result["columns"]],
            ["desordres.type", "desordres.designation"],
        )
        self.assertEqual(result["root"], "desordres")
        self.assertEqual(connection.cursor_instance.executions[0], (
            "SET TRANSACTION READ ONLY", None,
        ))
        count_query, count_params = connection.cursor_instance.executions[1]
        query, params = connection.cursor_instance.executions[2]
        self.assertEqual(count_params, ([],))
        self.assertEqual(params, ([], DATA_VIEW_MAX_ROWS, 0))
        self.assertIn("count(*)", count_query)
        self.assertIn("LIMIT %s OFFSET %s", query)
        for keyword in ("INSERT", "UPDATE", "DELETE"):
            self.assertNotIn(keyword, query.upper())

    def test_result_count_is_paginated_explicitly(self):
        connection = FakeConnection([
            ([], index) for index in range(DATA_VIEW_MAX_ROWS + 1)
        ])
        result = execute_view_query(
            connection, "systemes", selections("systemes.population_protegee")
        )
        self.assertEqual(len(result["rows"]), DATA_VIEW_MAX_ROWS)
        self.assertEqual(result["total"], DATA_VIEW_MAX_ROWS + 1)
        self.assertTrue(result["has_next"])
        self.assertFalse(result["has_previous"])
        self.assertTrue(result["truncated"])

        second = execute_view_query(
            connection, "systemes", selections("systemes.population_protegee"),
            offset=DATA_VIEW_MAX_ROWS,
        )
        self.assertEqual(len(second["rows"]), 1)
        self.assertFalse(second["has_next"])
        self.assertTrue(second["has_previous"])

    def test_patrimoine_filter_is_applied_before_count_and_pagination(self):
        hidden_rows = [
            ([{"class": "troncons", "id": "hidden"}], f"Masqué {index}")
            for index in range(DATA_VIEW_MAX_ROWS + 5)
        ]
        wanted = ([{"class": "troncons", "id": "visible"}], "Visible")
        connection = FakeConnection(hidden_rows + [wanted])

        result = execute_view_query(
            connection,
            "troncons",
            selections("troncons.libelle"),
            hidden_troncon_ids=["hidden"],
            page_size=10,
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["values"], ["Visible"])
        self.assertEqual(
            connection.cursor_instance.executions[-1][1],
            (["hidden"], 10, 0),
        )
        page_query = connection.cursor_instance.executions[-1][0]
        self.assertLess(page_query.index("&& %s::uuid[]"), page_query.index("LIMIT %s"))

    def test_all_roots_expose_their_troncon_scope_to_the_server_filter(self):
        identity = {
            "systemes": "libelle", "digues": "libelle", "troncons": "libelle",
            "desordres": "designation", "observations": "designation",
            "photos": "libelle",
        }
        for root, field in identity.items():
            with self.subTest(root=root):
                query, _ = self.sql_for(root, f"{root}.{field}")
                self.assertIn("troncon_ids", query)
                self.assertIn("&& %s::uuid[]", query)

    def test_multi_linked_disorder_is_hidden_when_any_troncon_is_hidden(self):
        entities = [
            {"class": "troncons", "id": "visible"},
            {"class": "troncons", "id": "hidden"},
            {"class": "desordres", "id": "d1"},
        ]
        result = execute_view_query(
            FakeConnection([(entities, "D1")]),
            "desordres",
            selections("desordres.designation"),
            hidden_troncon_ids=["hidden"],
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["rows"], [])

    def test_complete_export_is_not_limited_to_display_page(self):
        rows = [([], index) for index in range(DATA_VIEW_MAX_ROWS + 1)]
        result = execute_view_export(
            FakeConnection(rows), "systemes", selections("systemes.population_protegee")
        )
        self.assertEqual(result["row_count"], DATA_VIEW_MAX_ROWS + 1)
        self.assertEqual(result["total"], DATA_VIEW_MAX_ROWS + 1)
        self.assertLess(DATA_VIEW_MAX_ROWS, DATA_VIEW_MAX_EXPORT_ROWS)

    def test_complete_export_fails_explicitly_at_technical_server_limit(self):
        connection = FakeConnection([([], index) for index in range(3)])
        with patch("digues_webapp.data_views.DATA_VIEW_MAX_EXPORT_ROWS", 2):
            with self.assertRaisesRegex(
                DataViewExportLimitError, "3 lignes.*limite technique de 2"
            ):
                execute_view_export(
                    connection,
                    "systemes",
                    selections("systemes.population_protegee"),
                )
        self.assertEqual(len(connection.cursor_instance.executions), 2)

    def test_unknown_field_is_rejected_before_database_access(self):
        connection = FakeConnection([])
        with self.assertRaises(DataViewValidationError):
            execute_view_query(
                connection, "desordres", selections("desordres.password")
            )
        self.assertEqual(connection.cursor_instance.executions, [])

    def test_home_is_a_fixed_configuration_of_the_generic_engine(self):
        expected = {"rows": [], "columns": []}
        connection = object()
        with patch(
            "digues_webapp.data_views.execute_view_query", return_value=expected
        ) as execute:
            self.assertIs(execute_home_view(connection), expected)
        execute.assert_called_once_with(
            connection, HOME_VIEW_ROOT, list(HOME_VIEW_FIELDS), None, 0, DATA_VIEW_MAX_ROWS
        )
        self.assertEqual(HOME_VIEW_ROOT, "desordres")
        self.assertEqual(
            [f"{field.table}.{field.field}" for field in HOME_VIEW_FIELDS],
            [
                "systemes.libelle", "digues.libelle", "troncons.libelle",
                "desordres.designation",
            ],
        )


class DataViewsPostGISIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_test_dsn("DIGUES_OCC_TEST_DSN")
        try:
            cls.connection = connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=False)
            with cls.connection.cursor() as cursor:
                cursor.execute("SELECT PostGIS_Version()")
                cursor.fetchone()
        except Exception as exc:
            connection = getattr(cls, "connection", None)
            if connection is not None:
                connection.close()
            raise unittest.SkipTest(f"PostGIS de test indisponible : {exc}")

    @classmethod
    def tearDownClass(cls):
        connection = getattr(cls, "connection", None)
        if connection is not None:
            connection.rollback()
            connection.close()

    def test_all_roots_and_each_descending_granularity_execute(self):
        identities = {
            "systemes": "libelle", "digues": "libelle", "troncons": "libelle",
            "desordres": "designation", "observations": "designation",
            "photos": "libelle",
        }
        for root, field in identities.items():
            with self.subTest(root=root):
                result = execute_view_query(
                    self.connection, root, selections(f"{root}.{field}")
                )
                self.assertEqual(result["granularity"], root)
        for root in PILOT_ROOTS:
            with self.subTest(root=root):
                with self.connection.cursor() as cursor:
                    cursor.execute(f"SELECT count(*) FROM {VIEW_CLASSES[root].table}")
                    total = cursor.fetchone()[0]
                selected = [f"{root}.designation", f"{root}.troncon"] if root in PILOT_ROOTS[:4] else [
                    f"{root}.designation", f"{root}.troncons"]
                if root == "troncons_lit":
                    selected = [f"{root}.designation", f"{root}.digues"]
                elif root == "elements_morphologiques_lit":
                    selected = [f"{root}.designation", f"{root}.type_morphologie", f"{root}.troncon_lit"]
                elif root == "amenagements_hydrauliques":
                    selected = [f"{root}.designation", f"{root}.zones", f"{root}.troncons"]
                elif root == "zones_stockage":
                    selected = [f"{root}.designation", f"{root}.type", f"{root}.ah"]
                elif root not in {"ouvrages_particuliers", "vegetation"}:
                    selected.insert(1, f"{root}.type")
                result = execute_view_query(self.connection, root,
                    selections(*selected),
                    page_size=2)
                self.assertEqual(result["granularity"], root)
                self.assertEqual(result["total"], total)
                self.assertEqual(len(result["rows"]), min(total, 2))
        for keys, expected in (
            (("troncons.libelle", "desordres.designation"), "desordres"),
            (("troncons.libelle", "desordres.designation",
              "observations.designation"), "observations"),
            (("troncons.libelle", "desordres.designation",
              "observations.designation", "photos.libelle"), "photos"),
        ):
            with self.subTest(granularity=expected):
                result = execute_view_query(self.connection, "troncons", selections(*keys))
                self.assertEqual(result["granularity"], expected)

    def test_hydraulic_roots_read_direct_links_without_zone_troncon_relation(self):
        ah, zone, troncon = uuid4(), uuid4(), uuid4()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("INSERT INTO public.amenagements_hydrauliques(id,designation,valid) "
                               "VALUES (%s,'AH vue',true)", (ah,))
                cursor.execute("INSERT INTO public.zones_stockage "
                               "(id,designation,amenagement_hydraulique_id,valid,geometry) "
                               "VALUES (%s,'Zone vue',%s,true,ST_Transform(ST_GeomFromText("
                               "'POLYGON((2 48,2.001 48,2.001 48.001,2 48))',4326),3950))",
                               (zone, ah))
                cursor.execute("INSERT INTO public.troncons "
                               "(id,type_physique_id,designation,amenagement_hydraulique_id,valid) "
                               "VALUES (%s,'INDETERMINE','Tronçon vue',%s,false)", (troncon, ah))
            ah_result = execute_view_query(self.connection, "amenagements_hydrauliques",
                selections("amenagements_hydrauliques.designation", "amenagements_hydrauliques.zones",
                           "amenagements_hydrauliques.troncons"))
            ah_row = next(row for row in ah_result["rows"] if row["entities"][0]["id"] == str(ah))
            self.assertIn("Zone vue", ah_row["values"][1])
            self.assertIn("Tronçon vue", ah_row["values"][2])
            self.assertIsNone(ah_row["entities"][0]["geometry"])
            zone_result = execute_view_query(self.connection, "zones_stockage",
                selections("zones_stockage.designation", "zones_stockage.ah",
                           "zones_stockage.localisation"))
            zone_row = next(row for row in zone_result["rows"] if row["entities"][0]["id"] == str(zone))
            self.assertEqual(zone_row["values"][1:], ["AH vue", "Polygon"])
            self.assertEqual([entity["class"] for entity in zone_row["entities"]], ["zones_stockage"])
        finally:
            self.connection.rollback()

    def test_parent_roots_return_every_row_even_without_a_disorder(self):
        identity = {
            "systemes": "libelle", "digues": "libelle", "troncons": "libelle",
        }
        with self.connection.cursor() as cursor:
            for root, field in identity.items():
                with self.subTest(root=root):
                    cursor.execute(f"SELECT count(*) FROM public.{root}")
                    expected = min(cursor.fetchone()[0], DATA_VIEW_MAX_ROWS)
                    result = execute_view_query(
                        self.connection, root, selections(f"{root}.{field}")
                    )
                    self.assertEqual(result["row_count"], expected)

    def test_pilot_roots_paginate_filter_and_keep_one_row_per_object(self):
        systeme_id, digue_id, troncon_id = uuid4(), uuid4(), uuid4()
        baseline = {}
        inserted = {root: set() for root in PILOT_ROOTS[:4]}
        for root in inserted:
            keys = [f"{root}.designation", f"{root}.troncon"]
            if root != "ouvrages_particuliers":
                keys.insert(1, f"{root}.type")
            baseline[root] = execute_view_query(self.connection, root, selections(*keys))["total"]
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'Vue pilote',true)",
                    (systeme_id,),
                )
                cursor.execute(
                    "INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) "
                    "VALUES (%s,%s,'Digue pilote',true)", (digue_id, systeme_id),
                )
                cursor.execute(
                    "INSERT INTO public.troncons(id,type_physique_id,digue_id,libelle,geometry,valid) "
                    f"VALUES (%s,'INDETERMINE',%s,'Tronçon pilote',{LINE_SQL},true)", (troncon_id, digue_id),
                )
                for root in PILOT_ROOTS[:4]:
                    reference = {
                        "mobilier": "types_mobilier",
                        "equipements_mesure": "types_equipement_mesure",
                        "reseaux_techniques": "types_reseau_technique",
                    }.get(root)
                    if reference:
                        type_id = str(uuid4())
                        cursor.execute(
                            f"INSERT INTO referentiel.{reference} "
                            "(id,code,abrege,libelle,valid) VALUES (%s,%s,%s,'Type pilote',true)",
                            (type_id, type_id, type_id),
                        )
                    for index in range(3):
                        identifier = uuid4()
                        inserted[root].add(str(identifier))
                        if reference:
                            geometry_columns = ',geometry' if root in {'mobilier', 'equipements_mesure'} else ''
                            geometry_value = f',{POINT_SQL}' if geometry_columns else ''
                            cursor.execute(
                                f"INSERT INTO public.{root} "
                                f"(id,type_id,designation,troncon_id{geometry_columns},valid) "
                                f"VALUES (%s,%s,%s,%s{geometry_value},true)",
                                (identifier, type_id, f"{root} {index}", troncon_id),
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO public.ouvrages_particuliers "
                                f"(id,designation,troncon_id,geometry,valid) VALUES (%s,%s,%s,{POINT_SQL},true)",
                                (identifier, f"{root} {index}", troncon_id),
                            )
            for root in PILOT_ROOTS[:4]:
                keys = [f"{root}.designation", f"{root}.troncon"]
                if root != "ouvrages_particuliers":
                    keys.insert(1, f"{root}.type")
                fields = selections(*keys)
                first = execute_view_query(self.connection, root, fields, page_size=2)
                self.assertEqual(first["total"], baseline[root] + 3)
                rows = []
                for offset in range(0, first["total"], 2):
                    rows.extend(execute_view_query(self.connection, root, fields,
                                                   offset=offset, page_size=2)["rows"])
                identifiers = [row["entities"][0]["id"] for row in rows]
                self.assertEqual(len(identifiers), len(set(identifiers)))
                self.assertEqual(set(identifiers) & inserted[root], inserted[root])
                expected_tail = ["Type pilote", "Tronçon pilote"] if root != "ouvrages_particuliers" else ["Tronçon pilote"]
                self.assertTrue(all(row["values"][1:] == expected_tail
                                    for row in rows if row["entities"][0]["id"] in inserted[root]))
                hidden = execute_view_query(self.connection, root, fields,
                                            hidden_troncon_ids=[troncon_id])
                self.assertEqual(hidden["total"], baseline[root])
        finally:
            self.connection.rollback()

    def test_multi_relation_roots_keep_one_row_with_two_troncons(self):
        systeme, digue, troncons = uuid4(), uuid4(), [uuid4(), uuid4()]
        roots = {
            "cheminements": ("public.cheminements__troncons", "cheminement_id"),
            "vegetation": ("public.vegetation__troncons", "vegetation_id"),
            "prestations": ("public.prestations_troncons", "prestation_id"),
        }
        baseline = {}
        for root in roots:
            selected = selections(f"{root}.designation", f"{root}.troncons")
            baseline[root] = execute_view_query(self.connection, root, selected)["total"]
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'Multi',true)", (systeme,))
                cursor.execute("INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) "
                               "VALUES (%s,%s,'Multi',true)", (digue, systeme))
                for identifier in troncons:
                    cursor.execute("INSERT INTO public.troncons(id,type_physique_id,digue_id,libelle,geometry,valid) "
                                   f"VALUES (%s,'INDETERMINE',%s,'Multi',{LINE_SQL},true)", (identifier, digue))
                cursor.execute("SELECT id FROM referentiel.types_cheminement LIMIT 1")
                cheminement_type = cursor.fetchone()[0]
                identifiers = {root: uuid4() for root in roots}
                cursor.execute("INSERT INTO public.cheminements(id,type_cheminement_id,designation,geometry,valid) "
                               f"VALUES (%s,%s,'Multi',{LINE_SQL},true)", (identifiers["cheminements"], cheminement_type))
                cursor.execute("INSERT INTO public.vegetation(id,famille_vegetation,disposition,sous_type,designation,geometry,valid) "
                               f"VALUES (%s,'ARBRES','ISOLE','ARBRE_SUR_PIED','Multi',{POINT_SQL},true)",
                               (identifiers["vegetation"],))
                cursor.execute("INSERT INTO public.prestations(id,designation,date_fin,valid) "
                               "VALUES (%s,'Multi',DATE '2026-10-09',true)",
                               (identifiers["prestations"],))
                for root, (table, parent_key) in roots.items():
                    for troncon in troncons:
                        cursor.execute(f"INSERT INTO {table}({parent_key},troncon_id) VALUES (%s,%s)",
                                       (identifiers[root], troncon))
            for root in roots:
                with self.subTest(root=root):
                    selected = selections(f"{root}.designation", f"{root}.troncons")
                    result = execute_view_query(self.connection, root, selected)
                    self.assertEqual(result["total"], baseline[root] + 1)
                    matching = [row for row in result["rows"] if row["entities"][0]["id"] == str(identifiers[root])]
                    self.assertEqual(len(matching), 1)
                    self.assertEqual(len([entity for entity in matching[0]["entities"]
                                          if entity["class"] == "troncons"]), 2)
                    self.assertEqual(execute_view_query(self.connection, root, selected,
                        hidden_troncon_ids=[troncons[0]])["total"], baseline[root])
                    self.assertEqual(execute_view_query(self.connection, root, selected,
                        offset=result["total"], page_size=1)["rows"], [])
        finally:
            self.connection.rollback()

    def test_reseau_and_ouvrage_localisation_fields_use_the_stored_geometry(self):
        reseau_id, ouvrage_id = uuid4(), uuid4()
        type_id = str(uuid4())
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO referentiel.types_reseau_technique "
                    "(id,code,abrege,libelle,valid) VALUES (%s,%s,%s,'Vue localisation',true)",
                    (type_id, type_id, type_id),
                )
                cursor.execute(
                    "INSERT INTO public.reseaux_techniques(id,type_id,designation,geometry,valid) "
                    "VALUES (%s,%s,'Vue ligne',"
                    "ST_Transform(ST_GeomFromText('LINESTRING(2.1 48.2,2.2 48.3)',4326),3950),true)",
                    (reseau_id, type_id),
                )
                cursor.execute(
                    "INSERT INTO public.ouvrages_particuliers(id,designation,geometry,valid) "
                    "VALUES (%s,'Vue polygone',"
                    "ST_Transform(ST_GeomFromText('POLYGON((2.1 48.2,2.2 48.2,2.2 48.3,2.1 48.2))',4326),3950),true)",
                    (ouvrage_id,),
                )
            for root, identifier, expected in (
                ("reseaux_techniques", reseau_id, "LineString"),
                ("ouvrages_particuliers", ouvrage_id, "Polygon"),
            ):
                with self.subTest(root=root):
                    result = execute_view_query(self.connection, root,
                        selections(f"{root}.designation", f"{root}.localisation"))
                    row = next(row for row in result["rows"]
                               if str(row["entities"][0]["id"]) == str(identifier))
                    self.assertEqual(row["values"][1], expected)
        finally:
            self.connection.rollback()

    def test_patrimoine_filter_executes_for_all_six_roots(self):
        systeme_id, digue_id, hidden_id = uuid4(), uuid4(), uuid4()
        identity = {
            "systemes": "libelle", "digues": "libelle", "troncons": "libelle",
            "desordres": "designation", "observations": "designation",
            "photos": "libelle",
        }
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'Filtre',true)",
                    (systeme_id,),
                )
                cursor.execute(
                    "INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) "
                    "VALUES (%s,%s,'Filtre',true)", (digue_id, systeme_id),
                )
                cursor.execute(
                    "INSERT INTO public.troncons(id,type_physique_id,digue_id,libelle,geometry,valid) "
                    f"VALUES (%s,'INDETERMINE',%s,'Filtre',{LINE_SQL},true)", (hidden_id, digue_id),
                )
            for root, field in identity.items():
                with self.subTest(root=root):
                    fields = selections(f"{root}.{field}")
                    unfiltered = execute_view_query(self.connection, root, fields)
                    result = execute_view_query(
                        self.connection, root, fields, hidden_troncon_ids=[hidden_id],
                    )
                    if root in {"systemes", "digues", "troncons"}:
                        self.assertGreater(unfiltered["total"], result["total"])
                    self.assertFalse(any(
                        str(entity.get("id")) == str(hidden_id)
                        for item in result["rows"]
                        for entity in item["entities"]
                        if entity.get("class") == "troncons"
                    ))
        finally:
            self.connection.rollback()


if __name__ == "__main__":
    unittest.main()
