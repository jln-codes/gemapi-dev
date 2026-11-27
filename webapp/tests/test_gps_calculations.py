"""Contrat des calculs GPS de GEO-4 : uniquement des lectures PostGIS."""
import math
import unittest
from uuid import uuid4

from fastapi import HTTPException

from digues_app.crs import qualified_business_crs
from digues_webapp.app import app
from digues_webapp.queries import (
    _endpoint_source_srid,
    position_vers_reperage,
    transform_position,
)


class Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql, params=None):
        assert sql.lstrip().startswith("SELECT ")
        assert "INSERT " not in sql and "UPDATE " not in sql and "DELETE " not in sql
        self.connection.statements.append((sql, params))

    def fetchone(self):
        return self.connection.result

    def fetchall(self):
        return [(self.connection.srid,)]


class Connection:
    def __init__(self, result, srid=3950):
        self.result = result
        self.srid = srid
        self.statements = []

    def cursor(self):
        return Cursor(self)


class GpsCalculationsTest(unittest.TestCase):
    def test_transform_valid_invalid_and_read_only(self):
        connection = Connection((1700000, 8200000))
        self.assertEqual(transform_position(connection, 2, 46),
                         {"x": 1700000.0, "y": 8200000.0})
        self.assertIn("metadonnees_base", connection.statements[0][0])
        self.assertIn("ST_Transform", connection.statements[1][0])
        self.assertEqual(connection.statements[1][1], (2, 46, 3950))
        for longitude, latitude in [(181, 46), (2, -91), (math.nan, 46), (2, math.inf)]:
            with self.subTest(longitude=longitude, latitude=latitude), self.assertRaises(HTTPException) as caught:
                transform_position(connection, longitude, latitude)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(len(connection.statements), 2)

    def test_transform_reads_each_database_crs_without_contamination(self):
        first = Connection((1700000, 8200000), srid=3950)
        second = Connection((700000, 6600000), srid=2154)
        self.assertEqual(transform_position(first, 2, 46)["x"], 1700000)
        self.assertEqual(transform_position(second, 2, 46)["x"], 700000)
        self.assertEqual(transform_position(first, 2, 46)["x"], 1700000)
        self.assertEqual(first.statements[1][1][-1], 3950)
        self.assertEqual(second.statements[1][1][-1], 2154)
        self.assertEqual(first.statements[3][1][-1], 3950)

    def test_projected_endpoint_srid_must_match_selected_database(self):
        for srid in (3950, 2154):
            business_crs = qualified_business_crs(srid)
            self.assertEqual(
                _endpoint_source_srid(f"EPSG:{srid}", business_crs), srid
            )
            self.assertEqual(_endpoint_source_srid("EPSG:4326", business_crs), 4326)
            wrong = 2154 if srid == 3950 else 3950
            with self.assertRaises(HTTPException) as caught:
                _endpoint_source_srid(f"EPSG:{wrong}", business_crs)
            self.assertEqual(caught.exception.status_code, 422)

    def test_reperage_result_conventions_distance_and_errors(self):
        troncon, system, borne = uuid4(), uuid4(), uuid4()
        for sense, distance in [("AVANT_BORNE", 12), ("SUR_BORNE", 0), ("APRES_BORNE", 12)]:
            result = {"statut": "OK", "borne_id": str(borne), "distance_borne_m": distance,
                      "position_relative": sense, "distance_axe_m": 7.4, "pr": 100}
            connection = Connection((result, {"type": "Point", "coordinates": [1700000, 8200000]}))
            output = position_vers_reperage(connection, troncon, system, 2, 46)
            self.assertEqual(output["position_relative"], sense)
            self.assertEqual(output["distance_axe_m"], 7.4)
            self.assertEqual(output["point_projete"]["coordinates"], [1700000, 8200000])
            self.assertIn("xy_vers_reperage", connection.statements[1][0])
            self.assertEqual(
                connection.statements[1][1], (troncon, system, 2, 46, 3950)
            )
        for result in [({"statut": "REFERENCE_ABSENTE"}, None),
                       ({"statut": "SYSTEME_INCOMPLET"}, None)]:
            with self.assertRaises(HTTPException) as caught:
                position_vers_reperage(Connection(result), troncon, system, 2, 46)
            self.assertEqual(caught.exception.status_code, 422)

    def test_get_routes_have_no_mutation_or_extra_side_effect(self):
        troncon, system, borne = uuid4(), uuid4(), uuid4()
        connection = Connection((1700000, 8200000))
        routes = {route.path: route for route in app.routes if route.path in {
            '/api/coordinates/transform',
            '/api/troncons/{troncon_id}/position-reperage'}}
        self.assertEqual({method for route in routes.values() for method in route.methods}, {'GET'})
        self.assertEqual(routes['/api/coordinates/transform'].endpoint(
            2, 46, connection), {'x': 1700000.0, 'y': 8200000.0})
        self.assertNotIn('/api/coordinates/transform-3950', {
            route.path for route in app.routes
        })
        connection.result = ({"statut": "OK", "borne_id": str(borne),
            "distance_borne_m": 0, "position_relative": "SUR_BORNE",
            "distance_axe_m": 7.4},
            {"type": "Point", "coordinates": [1700000, 8200000]})
        output = routes['/api/troncons/{troncon_id}/position-reperage'].endpoint(
            troncon, system, 2, 46, connection)
        self.assertEqual(output['distance_axe_m'], 7.4)
        connection.result = ({"statut": "REFERENCE_ABSENTE"}, None)
        with self.assertRaises(HTTPException) as caught:
            routes['/api/troncons/{troncon_id}/position-reperage'].endpoint(
                uuid4(), system, 2, 46, connection)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(len(connection.statements), 6)
