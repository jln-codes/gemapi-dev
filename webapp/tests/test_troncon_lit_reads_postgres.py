"""L01 : lecture des associations explicites Digue–Tronçon de lit.

Niveau B, groupe API/PostGIS jetable, coût < 10 s hors préparation du runner.
Chaque scénario utilise ses UUID propres ; les écritures sont annulées après
lecture sur la même connexion. Les objets invalides restent lisibles et aucune
proximité, homonymie ni appartenance à un système ne crée une association.
La sécurité multi-base reste exercée par les tests dédiés de tenancy.
"""

import os
from decimal import Decimal
import unittest
from unittest.mock import patch
from uuid import uuid4

from digues_webapp.app import create_app
from digues_webapp.database import get_connection, read_business_crs
from tests.postgres_safety import connect_test_database
from webapp.tests.test_resource_versions import http_client


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class TronconLitReadsPostgresTest(unittest.TestCase):
    def setUp(self):
        self.connection = connect_test_database("DIGUES_OCC_TEST_DSN")
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        with patch.dict(os.environ, {"LEGACY_SINGLE_DATABASE_ENABLED": "true"}):
            self.app = create_app()

        def connection():
            yield self.connection

        self.app.dependency_overrides[get_connection] = connection

    def test_five_occurrence_lists_preserve_identity_precision_and_parent_scope(self):
        # L02: two equal-valued occurrences remain distinct; another segment's
        # occurrence must never appear. Invalid rows remain readable as such.
        segment, other_segment, author, borne, system = (uuid4() for _ in range(5))
        srid = read_business_crs(self.connection).srid
        for identifier in (segment,other_segment):
            self.connection.execute(
                "INSERT INTO public.segments_hydrographiques(id,geometry,valid) "
                "VALUES(%s,ST_GeomFromText('LINESTRING(0 0,1 1)',%s),true)", (identifier,srid))
        self.connection.execute("INSERT INTO public.contacts(id,nom,valid) VALUES(%s,'Auteur L02',true)", (author,))
        self.connection.execute("INSERT INTO public.bornes_reperage(id,libelle,valid) VALUES(%s,'Borne L02',true)", (borne,))
        self.connection.execute("INSERT INTO public.segments_hydrographiques__systemes_reperage(id,segment_id,valid) VALUES(%s,%s,true)", (system,segment))
        precision = Decimal('12.345678901234567890123456789')
        cases = (
            ('largeurs_lit','largeur_moyenne',precision),
            ('pentes_lit','pente_moyenne',Decimal('0')),
            ('domanialites_lit','type_id','RefDomanialiteLit:2'),
            ('regimes_ecoulement_lit','regime_id','RefRegimeLit:99'),
            ('occupations_riveraines_lit','type_occupation_id','RefOccupationLit:99'),
        )
        ids = {}
        for table,field,value in cases:
            ids[table] = sorted((uuid4(),uuid4()))
            for index,identifier in enumerate(ids[table]):
                self.connection.execute(
                    f"INSERT INTO public.{table}(id,segment_id,{field},valid,designation,date_debut,date_fin,date_maj,"
                    "created_by,edited_by,borne_debut_id,borne_debut_distance,borne_debut_aval,pr_debut,systeme_rep_id) "
                    "VALUES(%s,%s,%s,%s,'Même valeur','2024-01-02','2025-02-03','2025-03-04',%s,%s,%s,%s,false,0,%s)",
                    (identifier,segment,value,index == 0,author,author,borne,precision,system))
            self.connection.execute(f"INSERT INTO public.{table}(id,segment_id,{field},valid) VALUES(%s,%s,%s,true)",
                                    (uuid4(),other_segment,value))
        with http_client(self.app) as client:
            response = client.get(f'/api/troncons-lit/{segment}')
            self.assertEqual(response.status_code,200,response.text)
            feature = response.json()
            self.assertEqual(feature["type"], "Feature")
            item = feature["properties"]
        self.assertNotIn('zones_atterrissement_lit', item)
        for table,field,value in cases:
            rows = item[table]
            self.assertEqual([r['id'] for r in rows], [str(identifier) for identifier in ids[table]])
            self.assertEqual([r['valid'] for r in rows], [True,False])
            for row in rows:
                self.assertEqual(row['segment_id'],str(segment))
                self.assertEqual(row[field],str(value) if isinstance(value,Decimal) else value)
                self.assertEqual(row['date_debut'],'2024-01-02')
                self.assertEqual(row['date_fin'],'2025-02-03')
                self.assertEqual(row['date_maj'],'2025-03-04')
                self.assertEqual(row['created_by'],str(author))
                self.assertEqual(row['edited_by'],str(author))
                self.assertEqual(row['borne_debut_id'],str(borne))
                self.assertEqual(row['borne_debut_distance'],str(precision))
                self.assertFalse(row['borne_debut_aval'])
                self.assertEqual(row['pr_debut'],'0')
                self.assertEqual(row['systeme_rep_id'],str(system))
                self.assertIsNone(row['borne_fin_id'])
                self.assertIsNone(row['borne_fin_aval'])
                self.assertNotIn('source_class',row)
                self.assertFalse(any('geometry' in key or 'ewkt' in key for key in row))

    def test_exact_plurality_in_both_directions_and_empty_associations(self):
        systeme = uuid4()
        digues = sorted(uuid4() for _ in range(3))
        segments = sorted(uuid4() for _ in range(3))
        srid = read_business_crs(self.connection).srid
        self.connection.execute(
            "INSERT INTO public.systemes(id,libelle,valid) VALUES(%s,'Système L01',true)",
            (systeme,),
        )
        for index, identifier in enumerate(digues):
            self.connection.execute(
                "INSERT INTO public.digues(id,systeme_endiguement_id,libelle,valid) "
                "VALUES(%s,%s,'Digue homonyme',%s)", (identifier, systeme, index != 1),
            )
        for index, identifier in enumerate(segments):
            self.connection.execute(
                "INSERT INTO public.segments_hydrographiques(id,libelle,geometry,valid) "
                "VALUES(%s,'Lit homonyme',ST_GeomFromText('LINESTRING(0 0,1 1)',%s),%s)",
                (identifier, srid, index != 1),
            )
        links = ((segments[0], digues[0]), (segments[0], digues[1]),
                 (segments[1], digues[0]))
        for segment, digue in links:
            self.connection.execute(
                "INSERT INTO public.segments_hydrographiques__digues(segment_id,digue_id) "
                "VALUES(%s,%s)", (segment, digue),
            )

        with http_client(self.app) as client:
            for segment in segments:
                response = client.get(f"/api/troncons-lit/{segment}")
                self.assertEqual(response.status_code, 200, response.text)
                feature = response.json()
                self.assertEqual(feature["geometry"]["type"], "LineString")
                item = feature["properties"]
                self.assertEqual(item["id"], str(segment))
                self.assertEqual([d["id"] for d in item["digues"]],
                                 [str(d) for s, d in links if s == segment])
                self.assertNotIn("digue_id", item)
                self.assertNotIn("geometry", item)
                self.assertNotIn("source_class", item)
                if segment == segments[0]:
                    self.assertEqual([d["valid"] for d in item["digues"]], [True, False])
            for digue in digues:
                response = client.get(f"/api/digues/{digue}")
                self.assertEqual(response.status_code, 200, response.text)
                item = response.json()
                self.assertEqual([s["id"] for s in item["troncons_lit"]],
                                 [str(s) for s, d in links if d == digue])
                self.assertEqual(item["troncons_unifies"], [])
                if digue == digues[0]:
                    self.assertEqual([s["valid"] for s in item["troncons_lit"]], [True, False])
            missing = client.get(f"/api/troncons-lit/{uuid4()}")
            self.assertEqual(missing.status_code, 404, missing.text)
