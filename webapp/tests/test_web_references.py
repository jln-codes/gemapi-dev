import unittest
from unittest.mock import patch
from uuid import UUID

from pydantic import ValidationError

from digues_webapp.models import ObservationUpdate, PointDesordreUpdate, TronconUpdate
from digues_webapp.queries import (
    OBSERVATION_READ_PARENTS,
    ObservationNotFoundError,
    ObservationUpdateError,
    PointDesordreUpdateError,
    fetch_categories_desordre,
    fetch_reference_catalog,
    fetch_types_desordre,
    update_observation,
    update_point_desordre,
    update_troncon,
)


class Cursor:
    def __init__(self, rows=None, fetchone_results=None):
        self.rows = rows or []
        self.fetchone_results = iter(fetchone_results or [])
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return next(self.fetchone_results)


class Transaction:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Connection:
    def __init__(self, cursor):
        self.cursor_instance = cursor

    def cursor(self):
        return self.cursor_instance

    def transaction(self):
        return Transaction()


class WebReferenceTest(unittest.TestCase):
    desordre_id = UUID("00000000-0000-0000-0000-000000000001")
    desordre_row = (
        "POINT", "ancien-type", "ancienne-categorie", "ancien-cote",
        "ancienne-position", "ancienne-source",
    )

    def test_disorder_catalogs_expose_abrege_and_remain_sorted_by_label(self):
        cases = (
            (
                fetch_categories_desordre,
                "categories",
                {"categories": [{"id": "c1", "libelle": "Affaissement", "abrege": "AFF"}]},
            ),
            (
                fetch_types_desordre,
                "types",
                {"types": [{
                    "id": "t1", "libelle": "Fontis", "abrege": None,
                    "categorie_id": "c1",
                }]},
            ),
        )
        for fetcher, property_name, payload in cases:
            with self.subTest(property_name=property_name):
                cursor = Cursor(fetchone_results=[(payload,)])
                self.assertEqual(fetcher(Connection(cursor)), payload)
                query, params = cursor.executions[0]
                self.assertIn("'abrege'", query)
                self.assertIn("ORDER BY", query)
                self.assertRegex(query, r"ORDER BY [ct]\.libelle, [ct]\.id")
                self.assertIsNone(params)

    def test_catalog_query_is_whitelisted_valid_and_deterministic(self):
        cursor = Cursor(rows=[("rive-1", "Gauche", "RG", "G")])
        result = fetch_reference_catalog(Connection(cursor), "rives")
        self.assertEqual(result["references"][0]["id"], "rive-1")
        query, params = cursor.executions[0]
        self.assertIn("FROM referentiel.rives", query)
        self.assertIn("WHERE valid", query)
        self.assertIn("ORDER BY libelle, designation, id", query)
        self.assertIsNone(params)
        with self.assertRaises(KeyError):
            fetch_reference_catalog(Connection(Cursor()), "ref_rives; DROP TABLE x")

    def test_observation_update_distinguishes_nullable_reference_and_text(self):
        update = ObservationUpdate(suite_apporter_id=None, suite="  Complément  ")
        self.assertIsNone(update.suite_apporter_id)
        self.assertEqual(update.suite, "Complément")
        with self.assertRaises(ValidationError):
            ObservationUpdate()
        with self.assertRaises(ValidationError):
            ObservationUpdate(suite="x", observateur_source_id="interdit")

        identifier = UUID("00000000-0000-0000-0000-000000000001")
        cursor = Cursor(fetchone_results=[(identifier,) + (None,) * (len(OBSERVATION_READ_PARENTS) + 1), (identifier,)])
        expected = {"id": str(identifier), "suite": "Complément"}
        with patch("digues_webapp.queries.fetch_observation", return_value=expected):
            result = update_observation(
                Connection(cursor), identifier, ObservationUpdate(suite="  Complément  ")
            )
        self.assertEqual(result, expected)
        query, params = cursor.executions[1]
        self.assertIn("UPDATE public.observations", query)
        self.assertNotIn("desordre_id IS NOT NULL", query)
        self.assertEqual(params, ("Complément", identifier))

        clear_cursor = Cursor(fetchone_results=[(identifier,) + (None,) * (len(OBSERVATION_READ_PARENTS) + 1), (identifier,)])
        with patch("digues_webapp.queries.fetch_observation", return_value=expected):
            update_observation(
                Connection(clear_cursor), identifier,
                ObservationUpdate(suite_apporter_id=None),
            )
        self.assertEqual(
            clear_cursor.executions[1][1],
            (None, identifier),
        )

    def test_observation_update_rejects_unknown_reference_and_missing_object(self):
        identifier = UUID("00000000-0000-0000-0000-000000000001")
        unknown_cursor = Cursor(fetchone_results=[(identifier,) + (None,) * (len(OBSERVATION_READ_PARENTS) + 1), None])
        with self.assertRaises(ObservationUpdateError):
            update_observation(
                Connection(unknown_cursor), identifier,
                ObservationUpdate(suite_apporter_id="inconnue"),
            )
        self.assertIn("referentiel.suites_apporter", unknown_cursor.executions[1][0])
        self.assertEqual(unknown_cursor.executions[1][1], ("inconnue",))

        missing_cursor = Cursor(fetchone_results=[None])
        with self.assertRaises(ObservationNotFoundError):
            update_observation(
                Connection(missing_cursor), identifier, ObservationUpdate(suite=None),
            )

    def test_troncon_update_preserves_an_unchanged_inactive_reference(self):
        identifier = UUID("00000000-0000-0000-0000-000000000001")
        cursor = Cursor(fetchone_results=[("ancienne-rive", None, None), (identifier,)])
        expected = {"type": "Feature", "properties": {"id": str(identifier)}}
        update = TronconUpdate(
            libelle="Tronçon modifié",
            type_rive_id="ancienne-rive",
        )

        with patch("digues_webapp.queries.fetch_troncon", return_value=expected):
            result = update_troncon(Connection(cursor), identifier, update)

        self.assertEqual(result, expected)
        self.assertIn("FOR UPDATE", cursor.executions[0][0])
        self.assertFalse(any(
            "FROM referentiel.rives" in query for query, _params in cursor.executions
        ))

    def test_desordre_update_preserves_unchanged_inactive_category(self):
        expected = {"type": "Feature", "properties": {"id": str(self.desordre_id)}}
        for update in (
            PointDesordreUpdate(commentaire="Commentaire modifié"),
            PointDesordreUpdate(
                commentaire="Commentaire modifié",
                categorie_desordre_id="ancienne-categorie",
            ),
        ):
            with self.subTest(fields=update.model_fields_set):
                cursor = Cursor(fetchone_results=[self.desordre_row, (self.desordre_id,)])
                with patch("digues_webapp.queries.fetch_desordre", return_value=expected):
                    result = update_point_desordre(
                        Connection(cursor), self.desordre_id, update,
                    )
                self.assertEqual(result, expected)
                self.assertFalse(any(
                    "FROM referentiel.categories_desordre" in query
                    for query, _params in cursor.executions
                ))

    def test_desordre_update_validates_only_a_changed_category(self):
        inactive = Cursor(fetchone_results=[self.desordre_row, None])
        with self.assertRaisesRegex(PointDesordreUpdateError, "catégorie"):
            update_point_desordre(
                Connection(inactive), self.desordre_id,
                PointDesordreUpdate(categorie_desordre_id="autre-categorie-inactive"),
            )

        active_row = (
            "POINT", None, "ancienne-categorie", "ancien-cote",
            "ancienne-position", "ancienne-source",
        )
        active = Cursor(fetchone_results=[active_row, (1,)])
        expected = {"type": "Feature", "properties": {"id": str(self.desordre_id)}}
        with patch("digues_webapp.queries.fetch_desordre", return_value=expected):
            result = update_point_desordre(
                Connection(active), self.desordre_id,
                PointDesordreUpdate(categorie_desordre_id="nouvelle-categorie-active"),
            )
        self.assertEqual(result, expected)
        self.assertIn("AND valid", active.executions[1][0])

    def test_desordre_update_applies_the_same_rule_to_other_references(self):
        expected = {"type": "Feature", "properties": {"id": str(self.desordre_id)}}
        current_values = {
            "type_desordre_id": "ancien-type",
            "cote_id": "ancien-cote",
            "position_id": "ancienne-position",
            "source_id": "ancienne-source",
        }
        reference_tables = {
            "cote_id": "referentiel.cotes",
            "position_id": "referentiel.positions",
            "source_id": "referentiel.sources_information",
        }

        for field, current_value in current_values.items():
            with self.subTest(field=field, case="unchanged"):
                cursor = Cursor(fetchone_results=[self.desordre_row, (self.desordre_id,)])
                update = PointDesordreUpdate(
                    commentaire="Commentaire modifié", **{field: current_value},
                )
                with patch("digues_webapp.queries.fetch_desordre", return_value=expected):
                    update_point_desordre(Connection(cursor), self.desordre_id, update)
                self.assertFalse(any(
                    " AND valid" in query for query, _params in cursor.executions
                ))

            with self.subTest(field=field, case="changed-inactive"):
                cursor = Cursor(fetchone_results=[self.desordre_row, None])
                with self.assertRaises(PointDesordreUpdateError):
                    update_point_desordre(
                        Connection(cursor), self.desordre_id,
                        PointDesordreUpdate(**{field: "nouvelle-reference-inactive"}),
                    )
                expected_table = (
                    "referentiel.types_desordre" if field == "type_desordre_id"
                    else reference_tables[field]
                )
                self.assertIn(expected_table, cursor.executions[1][0])

            with self.subTest(field=field, case="changed-active"):
                fetches = [self.desordre_row, ("ancienne-categorie",)]
                if field == "type_desordre_id":
                    fetches.append((self.desordre_id,))
                cursor = Cursor(fetchone_results=fetches)
                with patch("digues_webapp.queries.fetch_desordre", return_value=expected):
                    result = update_point_desordre(
                        Connection(cursor), self.desordre_id,
                        PointDesordreUpdate(**{field: "nouvelle-reference-active"}),
                    )
                self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
