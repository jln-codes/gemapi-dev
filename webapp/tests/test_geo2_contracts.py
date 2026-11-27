"""Contrats DTO GEO-2, sans base ni interface graphique."""

import unittest

from pydantic import ValidationError

from digues_webapp.models import (
    CheminementCreate, CheminementUpdate, DesordreCreate,
    EquipementMesureCreate, EquipementMesureUpdate, LineStringGeometry,
    MobilierCreate, MobilierUpdate, OuvrageParticulierCreate,
    OuvrageParticulierUpdate, PointGeometry, PolygonGeometry,
    PrestationCreate, TronconCreate, TronconUpdate, VegetationCreate,
    VegetationUpdate,
)


POINT = {"type": "Point", "coordinates": [2.1, 48.2]}
LINE = {"type": "LineString", "coordinates": [[2.1, 48.2], [2.2, 48.3]]}
POLYGON = {"type": "Polygon", "coordinates": [[[2, 48], [3, 48], [3, 49], [2, 48]]]}


class Geo2ContractTest(unittest.TestCase):
    def test_seven_creations_require_a_location(self):
        cases = (
            (TronconCreate, {"digue_id": "59f4fe94-d6f5-4e81-9e44-4695701a45af", "libelle": "T"}, LINE),
            (DesordreCreate, {}, POINT),
            (MobilierCreate, {"type_id": "M"}, POINT),
            (EquipementMesureCreate, {"type_id": "E"}, POINT),
            (OuvrageParticulierCreate, {}, POINT),
            (CheminementCreate, {"type_cheminement_id": "C"}, LINE),
            (VegetationCreate, {"famille_vegetation": "ARBRES", "disposition": "ISOLE",
                                "sous_type": "ARBRE_SUR_PIED"}, POINT),
        )
        for model, fields, geometry in cases:
            with self.subTest(model=model.__name__):
                for payload in (fields, {**fields, "geometry": None}):
                    with self.assertRaises(ValidationError):
                        model.model_validate(payload)
                self.assertEqual(model.model_validate({**fields, "geometry": geometry}).geometry.type,
                                 geometry["type"])

    def test_simple_two_dimensional_geometry_only(self):
        for geometry in (
            {"type": "Point", "coordinates": [2, 48, 12]},
            {"type": "MultiPoint", "coordinates": [[2, 48]]},
            {"type": "Point", "coordinates": [float("inf"), 48]},
        ):
            with self.subTest(geometry=geometry), self.assertRaises(ValidationError):
                PointGeometry.model_validate(geometry)
        for coordinates in ([[2, 48]], [[2, 48], [2, 48]], [[2, 48, 1], [3, 49, 2]]):
            with self.subTest(coordinates=coordinates), self.assertRaises(ValidationError):
                LineStringGeometry.model_validate({"type": "LineString", "coordinates": coordinates})
        with self.assertRaises(ValidationError):
            PolygonGeometry.model_validate({"type": "Polygon", "coordinates": [[[2, 48], [3, 48], [3, 49]]]})

    def test_each_family_rejects_wrong_empty_and_3d_shapes(self):
        cases = (
            (TronconCreate, {"digue_id": "59f4fe94-d6f5-4e81-9e44-4695701a45af", "libelle": "T"}, POINT),
            (DesordreCreate, {}, {"type": "MultiPoint", "coordinates": [[2, 48]]}),
            (MobilierCreate, {"type_id": "M"}, LINE),
            (EquipementMesureCreate, {"type_id": "E"}, LINE),
            (OuvrageParticulierCreate, {}, {"type": "MultiLineString", "coordinates": [[[2, 48], [3, 49]]]}),
            (CheminementCreate, {"type_cheminement_id": "C"}, POLYGON),
            (VegetationCreate, {"famille_vegetation": "ARBRES", "disposition": "ISOLE",
                                "sous_type": "ARBRE_SUR_PIED"},
             {"type": "MultiPolygon", "coordinates": []}),
        )
        for model, fields, wrong in cases:
            with self.subTest(model=model.__name__):
                for geometry in (
                    wrong,
                    {"type": "Point", "coordinates": []},
                    {"type": "LineString", "coordinates": []},
                    {"type": "Polygon", "coordinates": []},
                    {"type": "Point", "coordinates": [2, 48, 3]},
                ):
                    with self.assertRaises(ValidationError):
                        model.model_validate({**fields, "geometry": geometry})

    def test_cheminement_polygon_is_cac_only_and_put_omission_is_distinct_from_null(self):
        with self.assertRaises(ValidationError):
            CheminementCreate.model_validate({
                "type_cheminement_id": "C", "geometry": POLYGON,
            })
        self.assertEqual(CheminementCreate.model_validate({
            "type_cheminement_id": "CAC", "geometry": POLYGON,
        }).geometry.type, "Polygon")
        # Sans type dans le patch, le service valide le Polygon contre le type
        # courant relu sous verrou. Un couple final explicitement incompatible
        # reste refusé dès le DTO.
        self.assertEqual(
            CheminementUpdate.model_validate({"geometry": POLYGON}).geometry.type,
            "Polygon",
        )
        with self.assertRaises(ValidationError):
            CheminementUpdate.model_validate({
                "type_cheminement_id": "C", "geometry": POLYGON,
            })
        for model in (TronconUpdate, MobilierUpdate, EquipementMesureUpdate,
                      OuvrageParticulierUpdate, CheminementUpdate, VegetationUpdate):
            payload = {"libelle": "T"} if model is TronconUpdate else {"commentaire": "attribut"}
            self.assertNotIn("geometry", model.model_validate(payload).model_fields_set)
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model.model_validate({**payload, "geometry": None})

    def test_desordre_modes_and_prestation_facultative(self):
        for authority in (
            {"geometry": POINT}, {"geometry": LINE}, {"geometry": POLYGON},
            {"x": 700000, "y": 7000000},
            {"longitude": 2.1, "latitude": 48.2},
            {"line_endpoints": {"crs": "EPSG:4326", "debut": [2.1, 48.2], "fin": [2.2, 48.3]}},
        ):
            with self.subTest(authority=authority):
                DesordreCreate.model_validate(authority)
        self.assertIsNone(PrestationCreate(date_fin="2026-10-10").geometry)


if __name__ == "__main__":
    unittest.main()
