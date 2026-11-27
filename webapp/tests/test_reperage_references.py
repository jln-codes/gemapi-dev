"""Canonical offline reference revision without a live database."""
import unittest
from uuid import uuid4
from fastapi import HTTPException

from digues_webapp.reperage_references import system_graph, check_revision


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.trunk, self.system, self.borne, self.link = [uuid4() for _ in range(4)]
        self.pr = 125
        self.axis = b"axis-3950"
        self.locked = False
        self._cursor_rows = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def fetchall(self):
        return self._cursor_rows

    def execute(self, sql, params=None):
        if "technique.metadonnees_base" in sql:
            self._cursor_rows = [(3950,)]
            return self
        if sql.startswith("LOCK TABLE"):
            self.locked = True
            return Result([])
        if "FROM public.troncons" in sql:
            return Result([(self.trunk, 3, True, self.axis, '{"type":"LineString","coordinates":[[2,46],[3,47]]}', 3950)])
        if "FROM public.systemes_reperage__bornes" in sql:
            return Result([(self.link, self.borne, self.pr, True, "B-1", True,
                            b"borne-3950", '{"type":"Point","coordinates":[1700000,8200000]}', 3950)])
        if "FROM public.systemes_reperage WHERE" in sql:
            return Result([(self.system, self.trunk, "Repérage", True)])
        raise AssertionError(sql)


class ReperageReferencesTest(unittest.TestCase):
    def test_canonical_revision_changes_for_actual_calibration_inputs(self):
        connection = Connection()
        first = system_graph(connection, connection.trunk, connection.system)
        self.assertEqual(first["bornes"][0]["pr"], "125")
        self.assertEqual(first["crs"]["code"], "EPSG:3950")
        self.assertEqual(first["bornes"][0]["geometry"]["type"], "Point")
        self.assertEqual(first["troncon_geometry"]["type"], "LineString")
        self.assertEqual(first["revision"], system_graph(connection, connection.trunk, connection.system)["revision"])
        connection.pr = 126
        self.assertNotEqual(first["revision"], system_graph(connection, connection.trunk, connection.system)["revision"])
        connection.pr = 125
        connection.axis = b"moved-axis"
        self.assertNotEqual(first["revision"], system_graph(connection, connection.trunk, connection.system)["revision"])

    def test_revision_guard_locks_before_read_and_rejects_change(self):
        connection = Connection()
        revision = system_graph(connection, connection.trunk, connection.system)["revision"]
        check_revision(connection, connection.trunk, connection.system, revision)
        self.assertTrue(connection.locked)
        connection.pr += 1
        with self.assertRaises(HTTPException) as caught:
            check_revision(connection, connection.trunk, connection.system, revision)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "photo-references-changed")


if __name__ == "__main__":
    unittest.main()
