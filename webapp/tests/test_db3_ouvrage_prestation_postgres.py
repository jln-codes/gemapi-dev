"""Contrat DB-3 : CRUD métier Ouvrage particulier et Prestation sur DB-2.

Niveau B, transaction annulée après chaque cas dans une base PostGIS jetable.
Les références inconnues sont refusées et aucun champ de provenance n'est écrit.
"""
import os
import unittest
from datetime import date
from uuid import uuid4

import psycopg

from digues_webapp.models import (
    OuvrageParticulierCreate, OuvrageParticulierUpdate,
    PrestationCreate, PrestationUpdate,
)
from digues_webapp.queries import (
    OuvrageParticulierValidationError, PrestationValidationError,
    create_ouvrage_particulier, create_prestation,
    fetch_ouvrage_particulier_collection, fetch_prestation_collection,
    update_ouvrage_particulier, update_prestation,
)


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable requis")
class CanonicalBusinessPostgresTest(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(os.environ["DIGUES_OCC_TEST_DSN"])
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)

    def test_ouvrage_particulier_uses_canonical_type(self):
        self.connection.execute(
            "INSERT INTO referentiel.types_ouvrage_particulier(id,libelle) "
            "VALUES ('VANNE','Vanne'),('PASSAGE','Passage')"
        )
        geometry = {"type": "Point", "coordinates": [2.0, 46.0]}
        with self.assertRaises(OuvrageParticulierValidationError):
            create_ouvrage_particulier(self.connection, OuvrageParticulierCreate(
                type_id="INCONNU", geometry=geometry,
            ))
        created = create_ouvrage_particulier(self.connection, OuvrageParticulierCreate(
            type_id="VANNE", designation="Ouvrage test", geometry=geometry,
        ))
        identifier = created["properties"]["id"]
        self.assertEqual(created["properties"]["type_libelle"], "Vanne")
        changed = update_ouvrage_particulier(
            self.connection, identifier, OuvrageParticulierUpdate(type_id="PASSAGE")
        )
        self.assertEqual(changed["properties"]["type_libelle"], "Passage")
        self.assertEqual(
            fetch_ouvrage_particulier_collection(self.connection)["features"][0]["properties"]["type_id"],
            "PASSAGE",
        )

    def test_prestation_uses_business_relations_without_history_tables(self):
        self.connection.execute(
            "INSERT INTO referentiel.types_prestation(id,libelle,valid) "
            "VALUES ('ENTRETIEN','Entretien',true)"
        )
        identifier = uuid4()
        created = create_prestation(self.connection, PrestationCreate(
            id=identifier, type_prestation_id="ENTRETIEN", designation="Débroussaillage",
            date_fin=date(2026, 10, 17),
        ))
        self.assertEqual(created["properties"]["type_prestation_id"], "ENTRETIEN")
        self.assertNotIn("historique", created["properties"])
        self.assertNotIn("record_origin", created["properties"])
        changed = update_prestation(
            self.connection, identifier, PrestationUpdate(commentaire="Terminé")
        )
        self.assertEqual(changed["properties"]["commentaire"], "Terminé")
        self.assertEqual(len(fetch_prestation_collection(self.connection)["features"]), 1)
        with self.assertRaises(PrestationValidationError):
            update_prestation(self.connection, identifier, PrestationUpdate(date_fin=None))
