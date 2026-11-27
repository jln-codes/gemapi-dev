"""Végétation DB-3 sur le DDL canonique : attributs et liens métier directs.

Niveau B sur cluster jetable. La transaction du scénario est annulée au nettoyage.
"""
import os
import unittest
from uuid import uuid4

import psycopg

from digues_webapp.data_views import VIEW_FIELDS_BY_KEY, build_view_query
from digues_webapp.models import VegetationCreate, VegetationUpdate
from digues_webapp.queries import (
    VegetationValidationError, create_vegetation, fetch_vegetation_collection,
    update_vegetation,
)


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable requis")
class CanonicalVegetationPostgresTest(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(os.environ["DIGUES_OCC_TEST_DSN"])
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)

    def test_tree_stump_invasivity_and_direct_links(self):
        system, digue, troncon, amenagement = (uuid4() for _ in range(4))
        self.connection.execute(
            "INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'SE',true)", (system,)
        )
        self.connection.execute(
            "INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) "
            "VALUES (%s,%s,'Digue',true)", (digue, system)
        )
        self.connection.execute(
            "INSERT INTO public.troncons(id,digue_id,libelle,valid) "
            "VALUES (%s,%s,'Tronçon',true)", (troncon, digue)
        )
        self.connection.execute(
            "INSERT INTO public.amenagements_hydrauliques(id,designation,valid) "
            "VALUES (%s,'AH',true)", (amenagement,)
        )
        created = create_vegetation(self.connection, VegetationCreate(
            famille_vegetation="ARBRES", disposition="ISOLE", sous_type="SOUCHE",
            invasif=True, hauteur_m=1.5, designation="Souche test",
            troncon_ids=[troncon], amenagement_hydraulique_ids=[amenagement],
            geometry={"type": "Point", "coordinates": [2.0, 46.0]},
        ))
        properties = created["properties"]
        self.assertEqual(properties["famille_vegetation"], "ARBRES")
        self.assertEqual(properties["sous_type"], "SOUCHE")
        self.assertTrue(properties["invasif"])
        self.assertEqual(properties["troncon_ids"], [str(troncon)])
        self.assertEqual(properties["amenagement_hydraulique_ids"], [str(amenagement)])
        self.assertEqual(len(fetch_vegetation_collection(self.connection)["features"]), 1)
        view_sql, _ = build_view_query(
            "vegetation", [VIEW_FIELDS_BY_KEY["vegetation.famille"]]
        )
        view_rows = self.connection.execute(view_sql, ([], 10, 0)).fetchall()
        self.assertEqual(view_rows[0][-1], "ARBRES")
        with self.assertRaises(VegetationValidationError):
            update_vegetation(self.connection, properties["id"], VegetationUpdate(
                famille_vegetation="HERBACEES"
            ))
        changed = update_vegetation(self.connection, properties["id"], VegetationUpdate(
            famille_vegetation="HERBACEES", sous_type=None, invasif=False,
        ))
        self.assertEqual(changed["properties"]["famille_vegetation"], "HERBACEES")
        self.assertIsNone(changed["properties"]["sous_type"])
