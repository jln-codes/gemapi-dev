"""Contrat DB-3 Photo : lecture et édition sur le DDL canonique jetable.

Niveau B, une base PostgreSQL/PostGIS neuve par runner, aucune ressource externe.
Le cas négatif vérifie qu'un photographe absent ne devient pas une référence.
"""
import os
import unittest
from uuid import uuid4

import psycopg
from fastapi import HTTPException

from digues_webapp.photo_localisation import options, read_localisation
from digues_webapp.photos import PhotoMetadata, read_photo, update_photo
from digues_webapp.queries import fetch_observation


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable requis")
class CanonicalPhotoPostgresTest(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(os.environ["DIGUES_OCC_TEST_DSN"])
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.system, self.digue, self.troncon = uuid4(), uuid4(), uuid4()
        self.vegetation, self.observation, self.photo = uuid4(), uuid4(), uuid4()
        self.connection.execute(
            "INSERT INTO public.systemes(id,libelle,valid) VALUES (%s,'Test Photo',true)",
            (self.system,),
        )
        self.connection.execute(
            "INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) "
            "VALUES (%s,%s,'Digue test',true)",
            (self.digue, self.system),
        )
        self.connection.execute(
            "INSERT INTO public.troncons(id,digue_id,libelle,valid) "
            "VALUES (%s,%s,'Tronçon test',true)",
            (self.troncon, self.digue),
        )
        self.connection.execute(
            "INSERT INTO public.vegetation(id,famille_vegetation,disposition,valid) "
            "VALUES (%s,'ARBRES','ISOLE',true)", (self.vegetation,),
        )
        self.connection.execute(
            "INSERT INTO public.vegetation__troncons(vegetation_id,troncon_id) VALUES (%s,%s)",
            (self.vegetation, self.troncon),
        )
        self.connection.execute(
            "INSERT INTO public.observations(id,vegetation_id,valid) VALUES (%s,%s,true)",
            (self.observation, self.vegetation),
        )
        self.connection.execute(
            "INSERT INTO public.photos(id,observation_id,storage_key,valid) "
            "VALUES (%s,%s,%s,true)", (self.photo, self.observation, str(uuid4())),
        )

    def test_photo_and_observation_read_use_only_business_parents(self):
        photo = read_photo(self.connection, self.photo)
        self.assertEqual(photo["id"], str(self.photo))
        self.assertIsNone(photo["photographe_id"])
        self.assertIsNone(photo["nom_fichier"])
        self.assertIsNone(photo["localisation"]["systeme_reperage_id"])
        detail = fetch_observation(self.connection, self.observation)
        self.assertEqual(detail["parent"]["resource"], "vegetation")
        self.assertEqual(detail["photos"][0]["id"], str(self.photo))
        self.assertEqual(
            [item["id"] for item in options(self.connection, self.photo, None)["context_troncons"]],
            [str(self.troncon)],
        )

    def test_photographer_is_a_valid_contact_fk(self):
        contact = uuid4()
        with self.assertRaises(HTTPException) as refused:
            update_photo(self.connection, self.photo, PhotoMetadata(photographe_id=contact))
        self.assertEqual(refused.exception.status_code, 422)
        self.connection.execute(
            "INSERT INTO public.contacts(id,nom,valid) VALUES (%s,'Photographe',true)",
            (contact,),
        )
        updated = update_photo(
            self.connection, self.photo, PhotoMetadata(photographe_id=contact)
        )
        self.assertEqual(updated["photographe_id"], str(contact))
        self.assertEqual(read_localisation(self.connection, self.photo)["warnings"], [])

    def test_historical_photo_can_wait_for_binary_without_a_source_column(self):
        self.connection.execute(
            "UPDATE public.photos SET storage_key=NULL WHERE id=%s", (self.photo,)
        )
        photo = read_photo(self.connection, self.photo)
        self.assertFalse(photo["content_available"])
        self.assertIsNone(photo["content_url"])
        columns = {
            row[0] for row in self.connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='photos'"
            )
        }
        self.assertNotIn("chemin_source", columns)
        self.assertNotIn("champs_presents_source", columns)
