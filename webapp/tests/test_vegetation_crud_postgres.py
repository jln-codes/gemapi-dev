"""F5 Végétation sur PostgreSQL/PostGIS privé et jetable."""
import os
import unittest
from uuid import UUID, uuid4

import psycopg

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from tests.postgres_safety import require_test_dsn
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection
from fastapi.testclient import TestClient as http_client


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class VegetationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = require_test_dsn("DIGUES_OCC_TEST_DSN")
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        cls.troncon, cls.autre_troncon, cls.ah = uuid4(), uuid4(), uuid4()
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            for table, code in (
                ("referentiel.etats_sanitaires_vegetation", "E1"),
                ("referentiel.problematiques_vegetation", "P1"),
                ("referentiel.classes_hauteur_vegetation", "H1"),
                ("referentiel.classes_diametre_vegetation", "D1"),
                ("referentiel.especes_vegetation", "ES1"),
            ):
                conn.execute(
                    f"INSERT INTO {table}(id,code,abrege,libelle,valid) "
                    "VALUES(%s,%s,%s,%s,true) ON CONFLICT(id) DO NOTHING",
                    (code, code, code, code),
                )
            conn.execute(
                "INSERT INTO referentiel.problematiques_vegetation(id,code,abrege,libelle,valid) "
                "VALUES('P2','P2','P2','P2',true),"
                "('POLD','POLD','POLD','Ancienne problématique',false) "
                "ON CONFLICT(id) DO NOTHING"
            )
            systeme,digue=uuid4(),uuid4()
            conn.execute("INSERT INTO systemes(id,libelle,valid) VALUES(%s,'S',true)",(systeme,))
            conn.execute("INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) "
                         "VALUES(%s,%s,'D',true)",(digue,systeme))
            for identifier in (cls.troncon,cls.autre_troncon):
                conn.execute("INSERT INTO troncons(id,digue_id,type_physique_id,libelle,geometry,valid) "
                             "VALUES(%s,%s,'INDETERMINE','T',ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)",(identifier,digue))
            conn.execute("INSERT INTO amenagements_hydrauliques(id,valid) "
                         "VALUES(%s,true)",(cls.ah,))
        cls.app = create_app()

        def override():
            with psycopg.connect(cls.dsn, autocommit=True) as conn:
                yield conn

        cls.app.dependency_overrides[get_connection] = override
        cls.app.dependency_overrides[get_write_connection] = override

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous

    def test_attribute_only_put_keeps_geometry_and_relations(self):
        with http_client(self.app) as client:
            created = client.post('/api/vegetation', json={
                'famille_vegetation': 'ARBRES', 'disposition': 'ISOLE',
                'sous_type': 'ARBRE_SUR_PIED', 'designation': 'Base',
                'geometry': {'type': 'Point', 'coordinates': [2, 48]},
                'troncon_ids': [str(self.troncon), str(self.autre_troncon)],
                'amenagement_hydraulique_ids': [str(self.ah)],
                'problematique_ids': ['P1', 'P2'],
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()['properties']['id']
            with psycopg.connect(self.dsn) as db:
                before = db.execute('SELECT ST_AsEWKB(geometry) FROM public.vegetation WHERE id=%s',
                                    (identifier,)).fetchone()[0]
            result = client.put(f'/api/vegetation/{identifier}', json={'commentaire': 'Base'},
                                headers={'If-Match': '"1"'})
            self.assertEqual(result.status_code, 200, result.text)
            properties = result.json()['properties']
            self.assertEqual(set(properties['troncon_ids']), {str(self.troncon), str(self.autre_troncon)})
            self.assertEqual(properties['amenagement_hydraulique_ids'], [str(self.ah)])
            self.assertEqual(set(properties['problematique_ids']), {'P1', 'P2'})
            with psycopg.connect(self.dsn) as db:
                after = db.execute('SELECT ST_AsEWKB(geometry) FROM public.vegetation WHERE id=%s',
                                   (identifier,)).fetchone()[0]
            self.assertEqual(after, before)

    def test_geometry_crud_concurrency_and_rollback(self):
        geometries = (
            {"type": "Point", "coordinates": [2, 48]},
            {"type": "Point", "coordinates": [2.1, 48.2]},
            {"type": "LineString", "coordinates": [[2.1, 48.2], [2.2, 48.3]]},
            {"type": "Polygon", "coordinates": [[[2, 48], [3, 48], [3, 49], [2, 48]]]},
        )
        with http_client(self.app) as client:
            ids = []
            families = (
                ("ARBRES", "ISOLE", "ARBRE_SUR_PIED"),
                ("ARBRES", "ISOLE", "SOUCHE"),
                ("BUISSONS_ARBUSTES", "LIGNE", None),
                ("HERBACEES", "SURFACE", None),
            )
            for geometry, (family, disposition, subtype) in zip(geometries, families):
                payload = {"famille_vegetation": family, "disposition": disposition,
                           "valid": False, "geometry": geometry}
                if subtype is not None:
                    payload["sous_type"] = subtype
                response = client.post("/api/vegetation", json=payload)
                self.assertEqual(response.status_code, 201, response.text)
                feature = response.json()
                ids.append(feature["properties"]["id"])
                self.assertEqual(feature["geometry"]["type"] if geometry else None,
                                 geometry["type"] if geometry else None)
                self.assertFalse(feature["properties"]["valid"])
                self.assertEqual(feature["properties"]["version"], 1)
            endpoint = f"/api/vegetation/{ids[0]}"
            headers = {"If-Match": '"1"', "Operation-Id": str(uuid4())}
            patch = {"troncon_ids": [str(self.troncon),str(self.autre_troncon)],
                     "amenagement_hydraulique_ids": [str(self.ah)],
                     "problematique_ids": ["P1", "P2"],
                     "famille_vegetation": "BUISSONS_ARBUSTES", "disposition": "LIGNE",
                     "sous_type": None, "invasif": True,
                     "etat_sanitaire_id": "E1", "classe_hauteur_id": "H1",
                     "classe_diametre_id": "D1", "espece_id": "ES1",
                     "commentaire": "", "valid": True,
                     "geometry": geometries[2]}
            changed = client.put(endpoint, json=patch, headers=headers)
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()["properties"]["version"], 2)
            self.assertIsNone(changed.json()["properties"]["commentaire"])
            self.assertEqual(changed.json()["properties"]["famille_vegetation"], "BUISSONS_ARBUSTES")
            self.assertIsNone(changed.json()["properties"]["sous_type"])
            self.assertTrue(changed.json()["properties"]["invasif"])
            self.assertEqual(set(changed.json()["properties"]["troncon_ids"]),
                             {str(self.troncon),str(self.autre_troncon)})
            self.assertEqual(changed.json()["properties"]["amenagement_hydraulique_ids"],
                             [str(self.ah)])
            self.assertEqual(changed.json()["properties"]["problematique_ids"],
                             ["P1", "P2"])
            refused_relation = client.put(endpoint, json={
                "troncon_ids": [], "amenagement_hydraulique_ids": [str(uuid4())]},
                headers={"If-Match": '"2"'})
            self.assertEqual(refused_relation.status_code, 422, refused_relation.text)
            self.assertEqual(set(client.get(endpoint).json()["properties"]["troncon_ids"]),
                             {str(self.troncon),str(self.autre_troncon)})
            self.assertEqual(client.get(endpoint).json()["properties"]["version"], 2)
            self.assertEqual(client.put(endpoint, json=patch, headers=headers).json(), changed.json())
            self.assertEqual(client.put(endpoint, json={"valid": False},
                                        headers={"If-Match": '"1"'}).status_code, 409)
            self.assertEqual(client.put(endpoint, json={"valid": False}).status_code, 428)
            for version, geometry, family, subtype in (
                ("2", geometries[3], "HERBACEES", None),
                ("3", geometries[1], "ARBRES", "SOUCHE"),
                ("4", geometries[2], "BUISSONS_ARBUSTES", None),
            ):
                response = client.put(endpoint, json={"geometry": geometry,
                    "famille_vegetation": family, "sous_type": subtype},
                                      headers={"If-Match": f'"{version}"'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["geometry"]["type"], geometry["type"])
                self.assertEqual(response.json()["properties"]["famille_vegetation"], family)
                self.assertEqual(response.json()["properties"]["sous_type"], subtype)
            self.assertEqual(client.get(endpoint).json()["properties"]["version"], 5)
            self.assertEqual(client.put(endpoint, json={"geometry": None},
                                        headers={"If-Match": '"5"'}).status_code, 422)
            self.assertEqual(client.get(f"/api/vegetation/{uuid4()}").status_code, 404)
            self.assertEqual(client.put(f"/api/vegetation/{uuid4()}", json={"valid": True},
                                        headers={"If-Match": '"1"'}).status_code, 404)
            collection = client.get("/api/vegetation")
            self.assertEqual(collection.status_code, 200, collection.text)
            self.assertTrue(set(ids).issubset({f["properties"]["id"]
                                              for f in collection.json()["features"]}))
            bare = client.post("/api/vegetation", json={"famille_vegetation": "HERBACEES",
                "disposition": "ISOLE",
                "geometry": {"type": "Point", "coordinates": [2, 48]}})
            self.assertEqual(bare.status_code, 201, bare.text)
            bare_id = bare.json()["properties"]["id"]
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                conn.execute("""CREATE FUNCTION reject_f5_change() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN
                    IF NEW.commentaire = 'ROLLBACK' THEN
                        RAISE EXCEPTION 'F5 rollback'; END IF;
                    RETURN NEW; END $$""")
                conn.execute("CREATE TRIGGER reject_f5_change BEFORE UPDATE ON vegetation "
                             "FOR EACH ROW EXECUTE FUNCTION reject_f5_change()")
            try:
                refused = client.put(endpoint, json={"commentaire": "ROLLBACK"},
                                     headers={"If-Match": '"5"'})
                self.assertEqual(refused.status_code, 503, refused.text)
            finally:
                with psycopg.connect(self.dsn, autocommit=True) as conn:
                    conn.execute("DROP TRIGGER reject_f5_change ON vegetation")
                    conn.execute("DROP FUNCTION reject_f5_change()")
            self.assertEqual(client.get(endpoint).json()["properties"]["version"], 5)
            self.assertIsNone(client.get(endpoint).json()["properties"]["commentaire"])
            only_relations = client.put(endpoint, json={"troncon_ids": []},
                                        headers={"If-Match": '"5"'})
            self.assertEqual(only_relations.status_code, 200, only_relations.text)
            self.assertEqual(only_relations.json()["properties"]["version"], 6)
            self.assertEqual(only_relations.json()["properties"]["troncon_ids"], [])
            incompatible = client.put(endpoint, json={"famille_vegetation": "ARBRES"},
                                      headers={"If-Match": '"6"'})
            self.assertEqual(incompatible.status_code, 422, incompatible.text)
            self.assertEqual(client.get(endpoint).json()["properties"]["version"], 6)
        with psycopg.connect(self.dsn) as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT geometry FROM vegetation WHERE id=%s", (bare_id,)).fetchone()[0])
            for identifier, geometry_type in zip(
                ids, ("LINESTRING", "POINT", "LINESTRING", "POLYGON")
            ):
                self.assertEqual(conn.execute(
                    "SELECT ST_SRID(geometry),GeometryType(geometry) "
                    "FROM vegetation WHERE id=%s", (identifier,)).fetchone(),
                    (3950, geometry_type))

    def test_references_parent_validation_and_conditional_subtype(self):
        with http_client(self.app) as client:
            options = client.get("/api/vegetation/relations/options")
            self.assertEqual(options.status_code, 200, options.text)
            self.assertNotIn("parcelles",options.json())
            self.assertTrue({str(self.troncon), str(self.autre_troncon)}.issubset(
                {row["id"] for row in options.json()["troncons"]}
            ))
            for name in ("etats-sanitaires", "classes-hauteur",
                         "classes-diametre", "problematiques", "especes"):
                response = client.get(f"/api/referentiels/{name}-vegetation")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertGreaterEqual(len(response.json()["references"]), 1)
            minimal = {"famille_vegetation": "ARBRES", "disposition": "ISOLE",
                       "sous_type": "SOUCHE",
                       "geometry": {"type": "Point", "coordinates": [2, 48]}}
            for payload in (
                {}, {**minimal, "parcelle_gestion_id": str(uuid4())},
                {**minimal, "troncon_ids": [str(uuid4())]},
                {**minimal, "troncon_ids": [str(self.troncon)]*2},
                {**minimal, "amenagement_hydraulique_ids": [str(uuid4())]},
                {**minimal, "nature_id": "ARB"},
                {**minimal, "famille_vegetation": "INCONNUE"},
                {**minimal, "disposition": "INCONNUE"},
                {**minimal, "sous_type": None},
                {**minimal, "sous_type": "INCONNU"},
                {**minimal, "famille_vegetation": "HERBACEES"},
                {**minimal, "etat_sanitaire_id": "absent"},
                {**minimal, "classe_hauteur_id": "absent"},
                {**minimal, "classe_diametre_id": "absent"},
                {**minimal, "espece_id": "absent"},
                {**minimal, "problematique_ids": ["absent"]},
                {**minimal, "problematique_ids": ["POLD"]},
                {**minimal, "problematique_ids": ["P1", "P1"]},
                {**minimal, "plan_id": str(uuid4())},
                {**minimal, "geometry": {"type": "Polygon",
                 "coordinates": [[[2, 48], [3, 49], [3, 48], [2, 49], [2, 48]]]}}
            ):
                with self.subTest(payload=payload):
                    self.assertEqual(client.post("/api/vegetation", json=payload).status_code,
                                     422)
            historical = uuid4()
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                conn.execute("INSERT INTO vegetation(id,famille_vegetation,disposition,"
                             "sous_type,geometry,valid) "
                             "VALUES(%s,'ARBRES','ISOLE','SOUCHE',"
                             "ST_SetSRID(ST_Point(2,48),3950),true)",
                             (historical,))
            response = client.put(f"/api/vegetation/{historical}",
                                  json={"designation": "Historique"},
                                  headers={"If-Match": '"1"'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["properties"]["sous_type"], "SOUCHE")
        with psycopg.connect(self.dsn) as conn:
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO vegetation(famille_vegetation,disposition,"
                             "sous_type,geometry,valid) VALUES('ARBRES','ISOLE',NULL,"
                             "ST_SetSRID(ST_Point(2,48),3950),true)")
        with psycopg.connect(self.dsn) as conn:
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO vegetation(famille_vegetation,disposition,"
                             "sous_type,geometry,valid) VALUES('HERBACEES','ISOLE','SOUCHE',"
                             "ST_SetSRID(ST_Point(2,48),3950),true)")

    def test_observation_photo_parent_unique(self):
        with http_client(self.app) as client:
            vegetation = client.post("/api/vegetation", json={"famille_vegetation": "ARBRES",
                "disposition": "ISOLE", "sous_type": "SOUCHE",
                "geometry": {"type": "Point", "coordinates": [2, 48]}}).json()
            identifier = vegetation["properties"]["id"]
            observation_id = str(uuid4())
            observation = client.post("/api/observations", json={
                "id": observation_id,
                "parent": {"resource": "vegetation", "id": identifier},
                "designation": "Visite",
            }, headers={"Operation-Id": str(uuid4())})
            self.assertEqual(observation.status_code, 201, observation.text)
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                photo_id = uuid4()
                conn.execute("INSERT INTO photos(id,observation_id,storage_key,valid) "
                             "VALUES(%s,%s,'photo.jpg',true)", (photo_id, observation_id))
                self.assertEqual(conn.execute(
                    "SELECT vegetation_id FROM observations WHERE id=%s",
                    (observation_id,)).fetchone()[0], UUID(identifier))
                self.assertEqual(conn.execute(
                    "SELECT num_nonnulls(desordre_id,troncon_id,ouvrage_hydraulique_id,"
                    "equipement_mesure_id,cheminement_id,mobilier_id,reseau_technique_id,"
                    "amenagement_hydraulique_id,vegetation_id,ouvrage_particulier_id,"
                    "structure_amenagement_hydraulique_id,organe_protection_collective_id,"
                    "prestation_id,ouvrage_telecom_energie_id,ouvrage_voirie_id,"
                    "reseau_hydraulique_id,structure_digue_id,profil_travers_id,"
                    "objet_mesure_hydraulique_id,structure_protection_id,"
                    "segment_hydrographique_id,berge_id,composant_berge_id,element_morphologique_id) "
                    "FROM observations WHERE id=%s", (observation_id,)).fetchone()[0], 1)
                self.assertEqual(conn.execute(
                    "SELECT observation_id FROM photos WHERE id=%s",
                    (photo_id,)).fetchone()[0], UUID(observation_id))
            listed = client.get(f"/api/vegetation/{identifier}/observations")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["observations"][0]["photo_count"], 1)
