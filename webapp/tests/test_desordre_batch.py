"""Bounded batch contract without a configured database."""
import json
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from digues_webapp import queries
from digues_webapp.database import WebDatabaseError


class DesordreBatchTest(unittest.TestCase):
    def test_crs_validation_then_three_parameterized_queries_and_input_order(self):
        ids = [uuid4() for _ in range(2000)]
        features = [{"type": "Feature", "properties": {"id": str(i)}} for i in ids]
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[(3950,)], [(json.dumps(f),) for f in reversed(features)], [], []]
        self.assertEqual(queries.fetch_desordres_batch(connection, ids), features)
        self.assertEqual(cursor.execute.call_count, 4)
        self.assertIn('technique.metadonnees_base', cursor.execute.call_args_list[0].args[0])
        for call in cursor.execute.call_args_list[1:]:
            self.assertIn("ANY(%s::uuid[])", call.args[0])
            self.assertEqual(call.args[1], (ids,))
            self.assertNotIn(str(ids[0]), call.args[0])
        connection.transaction.assert_not_called()

    def test_empty_excessive_invalid_ids_do_not_query(self):
        connection = MagicMock()
        self.assertEqual(queries.fetch_desordres_batch(connection, []), [])
        for ids in ([uuid4()] * 2001, ["not-a-uuid"]):
            with self.assertRaises(ValueError):
                queries.fetch_desordres_batch(connection, ids)
        connection.cursor.assert_not_called()

    def test_missing_or_unsupported_id_is_not_silently_omitted(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [[(3950,)], [], [], []]
        with self.assertRaises(queries.DesordreNotFoundError):
            queries.fetch_desordres_batch(connection, [uuid4()])

    def test_database_error_is_expurgated(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("private DSN")
        with self.assertRaises(WebDatabaseError) as error:
            queries.fetch_desordres_batch(connection, [uuid4()])
        self.assertNotIn("private", str(error.exception))
