"""Champs et référentiels Cheminement DB-2 sur PostGIS jetable (niveau B)."""

import os
import unittest
from uuid import uuid4

import psycopg

from digues_webapp.models import CheminementCreate, CheminementUpdate
from digues_webapp.queries import (
    CheminementValidationError,
    create_cheminement,
    fetch_cheminement_collection,
    fetch_reference_catalog,
    update_cheminement,
)
from tests.postgres_safety import require_test_dsn


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable requis")
class CanonicalCheminementPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = require_test_dsn("DIGUES_OCC_TEST_DSN")

    def setUp(self):
        self.connection = psycopg.connect(self.dsn)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.connection.execute(
            "INSERT INTO referentiel.types_cheminement"
            "(id,code,abrege,libelle,valid) VALUES('DB3-CHE','DB3-CHE','DB3CHE','Chemin',true)"
        )
        for table in (
            "materiaux_cheminement", "revetements_cheminement",
            "usages_cheminement", "types_voie", "orientations_ouvrage",
        ):
            self.connection.execute(
                f"INSERT INTO referentiel.{table}(id,code,libelle,valid) "
                "VALUES (%s,%s,'Valeur DB-3',true)",
                (f"DB3-{table}", f"DB3-{table}"),
            )
        for table in ("positions", "cotes", "securites", "sources_information"):
            self.connection.execute(
                f"INSERT INTO referentiel.{table}(id,libelle,valid) "
                "VALUES (%s,'Valeur DB-3',true)", (f"DB3-{table}",),
            )

    def test_create_update_and_reject_unknown_reference(self):
        values = {
            "materiau_id": "DB3-materiaux_cheminement",
            "revetement_id": "DB3-revetements_cheminement",
            "usage_id": "DB3-usages_cheminement",
            "statut_acces": "PUBLIC",
            "type_voie_id": "DB3-types_voie",
            "position_id": "DB3-positions",
            "cote_id": "DB3-cotes",
            "securite_id": "DB3-securites",
            "orientation_id": "DB3-orientations_ouvrage",
            "position_haut_id": "DB3-positions",
            "position_bas_id": "DB3-positions",
            "revetement_haut_id": "DB3-revetements_cheminement",
            "revetement_bas_id": "DB3-revetements_cheminement",
            "source_id": "DB3-sources_information",
        }
        created = create_cheminement(self.connection, CheminementCreate(
            type_cheminement_id="DB3-CHE", designation="Chemin DB-3",
            geometry={"type": "LineString", "coordinates": [[2, 46], [2.1, 46.1]]},
            **values,
        ))
        identifier = created["properties"]["id"]
        for field, value in values.items():
            self.assertEqual(created["properties"][field], value)
        self.assertIn(
            "DB3-materiaux_cheminement",
            {row["id"] for row in fetch_reference_catalog(
                self.connection, "materiaux-cheminement"
            )["references"]},
        )
        changed = update_cheminement(self.connection, identifier, CheminementUpdate(
            statut_acces="PRIVE", revetement_bas_id=None,
        ))
        self.assertEqual(changed["properties"]["statut_acces"], "PRIVE")
        self.assertIsNone(changed["properties"]["revetement_bas_id"])
        with self.assertRaises(CheminementValidationError):
            update_cheminement(self.connection, identifier, CheminementUpdate(
                usage_id="type-inconnu",
            ))
        self.assertEqual(
            self.connection.execute(
                "SELECT statut_acces,usage_id FROM public.cheminements WHERE id=%s",
                (identifier,),
            ).fetchone(),
            ("PRIVE", values["usage_id"]),
        )

    def test_untyped_existing_row_is_visible_in_collection(self):
        identifier = uuid4()
        self.connection.execute(
            "INSERT INTO public.cheminements(id,designation,valid) VALUES (%s,'Sans type',false)",
            (identifier,),
        )
        features = fetch_cheminement_collection(self.connection)["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["designation"], "Sans type")
