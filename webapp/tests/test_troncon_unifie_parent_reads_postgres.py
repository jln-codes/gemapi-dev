"""Lecture API des liens exacts Digue/AH vers les Tronçons unifiés.

Niveau B, PostGIS jetable. Les parents absents et la relation AH vers les
anciens tronçons vérifient qu'aucun rattachement indirect n'est inventé.
"""

import os
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.app import create_app
from digues_webapp.database import get_connection
from tests.postgres_safety import connect_test_database


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class TronconUnifieParentReadsPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.app = create_app()

        def override():
            with connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=True) as db:
                yield db

        cls.app.dependency_overrides[get_connection] = override

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous

    def test_details_use_only_explicit_parent_fks_and_share_one_summary(self):
        systeme, digue, other_digue = uuid4(), uuid4(), uuid4()
        amenagement, other_amenagement = uuid4(), uuid4()
        old_troncon = uuid4()
        rows = [
            (uuid4(), "BERGE", digue, None, "Berge", True),
            (uuid4(), "CRETE", digue, None, "Crête", True),
            (uuid4(), "TALUS", digue, amenagement, "Talus", False),
            (uuid4(), "TALUS_RISBERME", other_digue, None, "Talus de risberme", True),
            (uuid4(), "RISBERME", None, amenagement, "Risberme", True),
            (uuid4(), "FRANC_BORD", None, None, "Franc-bord", False),
            (uuid4(), "INDETERMINE", None, amenagement, "Tronçon non qualifié", False),
            (uuid4(), "FRANC_BORD", digue, None, "Franc-bord lié", True),
        ]
        with connect_test_database("DIGUES_OCC_TEST_DSN", autocommit=True) as db:
            with db.transaction():
                db.execute("INSERT INTO public.systemes(id,libelle,valid) VALUES(%s,'Système',true)",
                           (systeme,))
                db.execute("INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) "
                           "VALUES(%s,%s,'Digue',true),(%s,%s,'Autre digue',true)",
                           (digue, systeme, other_digue, systeme))
                db.execute("INSERT INTO public.amenagements_hydrauliques(id,designation,valid) "
                           "VALUES(%s,'AH',true),(%s,'Autre AH',true)",
                           (amenagement, other_amenagement))
                db.execute("INSERT INTO public.troncons(id,type_physique_id,digue_id,libelle,valid) "
                           "VALUES(%s,'INDETERMINE',%s,'Ancien tronçon',true)", (old_troncon, digue))
                db.execute("INSERT INTO public.amenagements_hydrauliques__troncons"
                           "(amenagement_hydraulique_id,troncon_id) VALUES(%s,%s)",
                           (amenagement, old_troncon))
                for identifier, type_code, digue_id, ah_id, designation, valid in rows:
                    srid = 0 if type_code == "TALUS" else 3950
                    db.execute("INSERT INTO public.troncons"
                               "(id,type_physique_id,digue_id,amenagement_hydraulique_id,"
                               "designation,geometry,valid) "
                               "VALUES(%s,%s,%s,%s,%s,ST_GeomFromText('LINESTRING(0 0,1 1)',%s),%s)",
                               (identifier, type_code, digue_id, ah_id, designation, srid, valid))

        with TestClient(self.app) as client:
            digue_response = client.get(f"/api/digues/{digue}")
            ah_response = client.get(f"/api/amenagements-hydrauliques/{amenagement}")
            other_digue_response = client.get(f"/api/digues/{other_digue}")
            other_ah_response = client.get(f"/api/amenagements-hydrauliques/{other_amenagement}")
        for response in (digue_response, ah_response, other_digue_response, other_ah_response):
            self.assertEqual(response.status_code, 200, response.text)
        digue_items = digue_response.json()["troncons"]
        ah_properties = ah_response.json()["properties"]
        ah_items = ah_properties["troncons"]
        self.assertEqual({item["id"] for item in digue_items},
                         {str(rows[index][0]) for index in (0, 1, 2, 7)} | {str(old_troncon)})
        # B6 : public.troncons porte une racine unifiée de même identité,
        # créée transactionnellement, et garde son rattachement explicite Digue.
        reperage_root = next(item for item in digue_items if item["id"] == str(old_troncon))
        self.assertEqual(reperage_root["type_physique_id"], "INDETERMINE")
        self.assertEqual(reperage_root["digue_id"], str(digue))
        self.assertIsNone(reperage_root["amenagement_hydraulique_id"])
        self.assertEqual({item["id"] for item in ah_items},
                         {str(rows[index][0]) for index in (2, 4, 6)})
        self.assertEqual(next(item for item in digue_items if item["id"] == str(rows[2][0])),
                         next(item for item in ah_items if item["id"] == str(rows[2][0])))
        for item in digue_items + ah_items:
            self.assertEqual(set(item), {"id", "version", "libelle", "resource", "type_physique_id", "designation",
                                         "type_physique_libelle", "valid", "digue_id",
                                         "amenagement_hydraulique_id"})
            self.assertNotIn("source_class", item)
        self.assertEqual(next(item for item in ah_items if item["id"] == str(rows[6][0]))
                         ["type_physique_libelle"], "Indéterminé")
        self.assertEqual(ah_properties["troncon_ids"], sorted(
            [str(rows[index][0]) for index in (2, 4, 6)]))
        self.assertEqual(ah_properties["zone_ids"], [])
        self.assertEqual(ah_properties["zones_stockage"], [])
        self.assertEqual(other_digue_response.json()["troncons"], [
            {"id": str(rows[3][0]), "version": 1, "libelle": None, "resource": "troncons", "type_physique_id": "TALUS_RISBERME",
             "type_physique_libelle": "Talus de risberme",
             "designation": "Talus de risberme", "valid": True,
             "digue_id": str(other_digue), "amenagement_hydraulique_id": None},
        ])
        self.assertEqual(other_ah_response.json()["properties"]["troncons"], [])
