"""CRUD Lit level A: DTO edit boundary, exact numeric values, model capabilities."""
import unittest
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError

from digues_webapp.models import (
    TronconLitCreate, TronconLitUpdate, ElementMorphologiqueLitCreate,
    ElementMorphologiqueLitUpdate, LitCharacteristicCreate, LitCharacteristicUpdate,
)
from digues_webapp.geometry_capabilities import backend_geometry_capabilities, build_geometry_capabilities
from digues_app.master_model.engine import DEFAULT_MODEL_PATH, load_model


class LitCrudContractTest(unittest.TestCase):
    def test_patch_omission_is_distinct_from_clear_and_null(self):
        update=TronconLitUpdate(designation="Après")
        self.assertNotIn("geometry",update.model_fields_set)
        self.assertNotIn("digue_ids",update.model_fields_set)
        self.assertEqual(TronconLitUpdate(digue_ids=[]).digue_ids,[])
        for field in ("valid","geometry","digue_ids"):
            with self.assertRaises(ValidationError):
                TronconLitUpdate(**{field:None})
        self.assertIsNone(ElementMorphologiqueLitUpdate(segment_id=None).segment_id)
        with self.assertRaises(ValidationError):
            ElementMorphologiqueLitUpdate(type_morphologie=None)
        with self.assertRaises(ValidationError):
            TronconLitUpdate()

    def test_business_identity_and_specific_fields_never_leak_provenance(self):
        for factory in (TronconLitCreate,ElementMorphologiqueLitCreate,LitCharacteristicCreate):
            args={"id":uuid4(),"valid":False}
            if factory is ElementMorphologiqueLitCreate:
                args["type_morphologie"]="INDETERMINE"
            for forbidden in ("source_class","legacy_type","origine_v2","date_maj","created_by"):
                with self.subTest(factory=factory.__name__,field=forbidden),self.assertRaises(ValidationError):
                    factory(**args,**{forbidden:"source"})
        with self.assertRaises(ValidationError):
            LitCharacteristicCreate(id=uuid4(),geometry={"type":"Point","coordinates":[2,48]})
        with self.assertRaises(ValidationError):
            LitCharacteristicUpdate(segment_id=uuid4())
        with self.assertRaises(ValidationError):
            TronconLitCreate(id=uuid4(),digue_ids=[(identifier:=uuid4()),identifier])

    def test_characteristic_family_and_decimal_precision(self):
        item=LitCharacteristicCreate(id=uuid4(),largeur_moyenne="0.000000000000000001",pr_debut="0")
        item.validate_family("largeurs")
        self.assertEqual(item.largeur_moyenne,Decimal("0.000000000000000001"))
        self.assertEqual(item.pr_debut,Decimal(0))
        with self.assertRaises(ValueError):
            item.validate_family("pentes")
        with self.assertRaises(ValueError):
            LitCharacteristicCreate(id=uuid4(),regime_id=None).validate_family("largeurs")
        for number in (True,"NaN","Infinity"):
            with self.assertRaises(ValidationError):
                LitCharacteristicCreate(id=uuid4(),largeur_moyenne=number)
        morph=ElementMorphologiqueLitCreate(id=uuid4(),type_morphologie="ILE",longueur="0",rattache_berge=False,vegetalise=None)
        self.assertEqual(morph.longueur,0)
        self.assertFalse(morph.rattache_berge)
        self.assertIsNone(morph.vegetalise)

    def test_real_model_enables_only_approved_lit_shapes(self):
        model=load_model(DEFAULT_MODEL_PATH)
        for resource,shapes in (("troncons-lit",["LineString","Polygon"]),
                                ("elements-morphologiques-lit",["Point","LineString","Polygon"])):
            contract=build_geometry_capabilities(model,resource)
            self.assertEqual(contract["status"],"approved")
            self.assertEqual(contract["allowedTypes"],shapes)
            self.assertTrue(contract["requiredForValid"])
            self.assertTrue(contract["allowMissingWhenInvalid"])
            self.assertFalse(contract["remove"])
            self.assertFalse(backend_geometry_capabilities(resource)["remove"])
