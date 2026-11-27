"""Contrat unitaire du socle fermé Objet–Désordre."""

import unittest
from uuid import UUID, uuid4

from pydantic import ValidationError

from digues_app.target.schema import (
    EXPECTED_TABLES,
    IMPORT_PROOF_TRACKED_TABLES,
    MIGRATION_TABLES,
)
from digues_webapp.models import (
    AmenagementHydrauliqueCreate,
    AmenagementHydrauliqueUpdate,
    EquipementMesureCreate,
    EquipementMesureUpdate,
    MobilierCreate,
    MobilierUpdate,
    ReseauTechniqueCreate,
    ReseauTechniqueUpdate,
    VegetationCreate,
    VegetationUpdate,
)

from digues_webapp.object_disorder_relations import (
    RELATION_UNSET,
    ObjectDisorderRelationConfig,
    ObjectDisorderRelationConfigurationError,
    ObjectDisorderRelationValidationError,
    ensure_registered_object_disorder_relation,
    normalize_object_disorder_request,
    object_disorder_relation,
    object_disorder_relation_difference,
    registered_object_disorder_relations,
)


class ObjectDisorderRelationsTest(unittest.TestCase):
    def test_5g_tables_are_canonical_but_not_implicit_sirs_migration_targets(self):
        tables = {
            "public.amenagements_hydrauliques__desordres",
            "public.equipements_mesure__desordres",
            "public.mobilier__desordres",
            "public.reseaux_techniques__desordres",
            "public.vegetation__desordres",
        }
        self.assertTrue(tables <= set(EXPECTED_TABLES))
        self.assertTrue(tables <= set(IMPORT_PROOF_TRACKED_TABLES))
        self.assertTrue(tables.isdisjoint(MIGRATION_TABLES))

    def test_registry_is_closed_and_rejects_forged_sql_configuration(self):
        self.assertEqual(registered_object_disorder_relations(), (
            "amenagements-hydrauliques", "cheminements",
            "equipements-mesure", "mobilier", "ouvrages-particuliers",
            "reseaux-techniques", "vegetation",
        ))
        registered = object_disorder_relation("cheminements")
        self.assertIs(ensure_registered_object_disorder_relation(registered), registered)

        with self.assertRaises(ObjectDisorderRelationConfigurationError):
            object_disorder_relation("public.desordres; DROP SCHEMA public")

        forged = ObjectDisorderRelationConfig(
            resource=registered.resource,
            parent_table=registered.parent_table,
            parent_id_column=registered.parent_id_column,
            association_table=registered.association_table,
            association_parent_column=registered.association_parent_column,
            association_disorder_column=registered.association_disorder_column,
        )
        with self.assertRaises(ObjectDisorderRelationConfigurationError):
            ensure_registered_object_disorder_relation(forged)

        amenagement = object_disorder_relation("amenagements-hydrauliques")
        self.assertEqual(
            amenagement.association_table,
            "amenagements_hydrauliques__desordres",
        )
        self.assertTrue(amenagement.exclusive_disorder)

    def test_request_distinguishes_absent_null_empty_and_duplicates(self):
        first = UUID("00000000-0000-0000-0000-000000000002")
        second = UUID("00000000-0000-0000-0000-000000000001")

        self.assertIsNone(normalize_object_disorder_request(RELATION_UNSET))
        self.assertEqual(normalize_object_disorder_request([]), ())
        self.assertEqual(
            normalize_object_disorder_request([first, second]), (first, second),
        )
        for invalid in (None, [first, first], [str(first)], "not-a-list"):
            with self.subTest(invalid=invalid), self.assertRaises(
                ObjectDisorderRelationValidationError
            ):
                normalize_object_disorder_request(invalid)

    def test_difference_is_deterministic_and_preserves_unchanged_ids(self):
        one = UUID("00000000-0000-0000-0000-000000000001")
        two = UUID("00000000-0000-0000-0000-000000000002")
        three = UUID("00000000-0000-0000-0000-000000000003")
        four = UUID("00000000-0000-0000-0000-000000000004")

        difference = object_disorder_relation_difference(
            [three, one, two], [four, two, three],
        )
        self.assertEqual(difference.current, (one, two, three))
        self.assertEqual(difference.requested, (two, three, four))
        self.assertEqual(difference.additions, (four,))
        self.assertEqual(difference.removals, (one,))
        self.assertEqual(difference.unchanged, (two, three))
        self.assertTrue(difference.changed)

        identical = object_disorder_relation_difference([three, two], [two, three])
        self.assertFalse(identical.changed)
        self.assertEqual(identical.unchanged, (two, three))

    def test_five_new_dtos_share_absent_empty_null_and_duplicate_semantics(self):
        disorder = uuid4()
        point = {"type": "Point", "coordinates": [2.1, 48.2]}
        create_cases = (
            (MobilierCreate, {"type_id": "M", "geometry": point}),
            (EquipementMesureCreate, {"type_id": "E", "geometry": point}),
            (ReseauTechniqueCreate, {"type_id": "R"}),
            (VegetationCreate, {"famille_vegetation": "ARBRES", "disposition": "ISOLE",
                                "sous_type": "ARBRE_SUR_PIED", "geometry": point}),
            (AmenagementHydrauliqueCreate, {}),
        )
        update_cases = (
            MobilierUpdate, EquipementMesureUpdate, ReseauTechniqueUpdate,
            VegetationUpdate, AmenagementHydrauliqueUpdate,
        )
        for model, base in create_cases:
            with self.subTest(create=model.__name__):
                self.assertEqual(model.model_validate(base).desordre_ids, [])
                self.assertEqual(
                    model.model_validate({**base, "desordre_ids": [disorder]}).desordre_ids,
                    [disorder],
                )
                for invalid in (None, [disorder, disorder]):
                    with self.assertRaises(ValidationError):
                        model.model_validate({**base, "desordre_ids": invalid})
        for model in update_cases:
            with self.subTest(update=model.__name__):
                omitted = model.model_validate({"commentaire": "inchangé"})
                self.assertNotIn("desordre_ids", omitted.model_fields_set)
                self.assertEqual(
                    model.model_validate({"desordre_ids": []}).desordre_ids, [],
                )
                for invalid in (None, [disorder, disorder]):
                    with self.assertRaises(ValidationError):
                        model.model_validate({"desordre_ids": invalid})


if __name__ == "__main__":
    unittest.main()
