"""F7 : contrat natif et historique sur PostgreSQL/PostGIS réel."""
import os
import unittest
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from digues_app.target.database import PostgreSQLConfig, initialize_schema
from digues_webapp.app import create_app
from digues_webapp.database import get_connection, get_write_connection


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class PrestationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = os.environ.get("LEGACY_SINGLE_DATABASE_ENABLED")
        os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = "true"
        cls.dsn = os.environ["DIGUES_OCC_TEST_DSN"]
        initialize_schema(PostgreSQLConfig(dsn=cls.dsn), business_crs=3950)
        with psycopg.connect(cls.dsn, autocommit=True) as db:

            db.execute("INSERT INTO referentiel.types_prestation (id,designation,valid) VALUES "
                       "('T','Type',true),('OLD','Ancien',false)")
            cls.valid_type_ids = [r[0] for r in db.execute(
                "SELECT id FROM referentiel.types_prestation WHERE valid ORDER BY libelle,designation,id")]
            cls.troncons = [uuid4(), uuid4()]
            cls.desordres = [uuid4(), uuid4()]
            cls.ah = uuid4()
            cls.contact = uuid4()
            cls.inactive = uuid4()
            digue = uuid4()
            db.execute("INSERT INTO digues(id,libelle,valid) VALUES (%s,'Digue',true)", (digue,))
            for identifier in cls.troncons:
                db.execute("INSERT INTO troncons(id,digue_id,libelle,geometry,valid) "
                           "VALUES (%s,%s,'Tronçon',ST_GeomFromText('LINESTRING(0 0,10 0)',3950),true)", (identifier, digue))
            for identifier in cls.desordres:
                db.execute("INSERT INTO desordres(id,designation,geometry,valid) "
                           "VALUES (%s,'Désordre',ST_SetSRID(ST_Point(1,1),3950),true)", (identifier,))
            db.execute("INSERT INTO desordres(id,designation,geometry,valid) "
                       "VALUES (%s,'Inactif',ST_SetSRID(ST_Point(1,1),3950),false)", (cls.inactive,))
            db.execute("INSERT INTO amenagements_hydrauliques(id,designation,valid) "
                       "VALUES (%s,'AH',true)", (cls.ah,))
            db.execute("INSERT INTO contacts(id,nom,valid) "
                       "VALUES (%s,'Personne',true)", (cls.contact,))
        cls.app = create_app()

        def override():
            with psycopg.connect(cls.dsn, autocommit=True) as db:
                yield db

        cls.app.dependency_overrides[get_connection] = override
        cls.app.dependency_overrides[get_write_connection] = override

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop("LEGACY_SINGLE_DATABASE_ENABLED", None)
        else:
            os.environ["LEGACY_SINGLE_DATABASE_ENABLED"] = cls.previous

    def test_attribute_only_put_keeps_geometry_and_polymorphic_relations(self):
        with TestClient(self.app) as client:
            created = client.post('/api/prestations', json={
                'date_fin': '2026-10-09', 'designation': 'Base',
                'geometry': {'type': 'LineString', 'coordinates': [[2, 48], [2.1, 48.1]]},
                'troncon_ids': list(map(str, self.troncons)),
                'intervenant_ids': [str(self.contact)],
                'object_ids': {'desordres': list(map(str, self.desordres)),
                               'amenagements_hydrauliques': [str(self.ah)]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()['properties']['id']
            with psycopg.connect(self.dsn) as db:
                before = db.execute('SELECT ST_AsEWKB(geometry) FROM public.prestations WHERE id=%s',
                                    (identifier,)).fetchone()[0]
            result = client.put(f'/api/prestations/{identifier}', json={'commentaire': 'Base'},
                                headers={'If-Match': '"1"'})
            self.assertEqual(result.status_code, 200, result.text)
            properties = result.json()['properties']
            self.assertEqual(set(properties['troncon_ids']), set(map(str, self.troncons)))
            self.assertEqual(properties['intervenant_ids'], [str(self.contact)])
            self.assertEqual(set(properties['object_ids']['desordres']), set(map(str, self.desordres)))
            self.assertEqual(properties['object_ids']['amenagements_hydrauliques'], [str(self.ah)])
            with psycopg.connect(self.dsn) as db:
                after = db.execute('SELECT ST_AsEWKB(geometry) FROM public.prestations WHERE id=%s',
                                   (identifier,)).fetchone()[0]
            self.assertEqual(after, before)

    def test_object_edit_preserves_unexposed_historical_target(self):
        historical_target = uuid4()
        with psycopg.connect(self.dsn, autocommit=True) as db:
            db.execute("INSERT INTO public.ouvrages_telecom_energie "
                       "(id,valid) VALUES (%s,true)",
                       (historical_target,))
        with TestClient(self.app) as client:
            created = client.post('/api/prestations', json={
                'date_fin': '2026-10-09',
                'object_ids': {'desordres': [str(self.desordres[0])]},
            })
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()['properties']['id']
            with psycopg.connect(self.dsn, autocommit=True) as db:
                db.execute("INSERT INTO public.prestations_objets "
                           "(prestation_id,ouvrage_telecom_energie_id) VALUES (%s,%s)",
                           (identifier, historical_target))
            updated = client.put(f'/api/prestations/{identifier}', json={
                'object_ids': {'desordres': [str(self.desordres[1])]},
            }, headers={'If-Match': '"1"'})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()['properties']['object_ids']['desordres'],
                             [str(self.desordres[1])])
            with psycopg.connect(self.dsn) as db:
                self.assertEqual(db.execute(
                    'SELECT ouvrage_telecom_energie_id FROM public.prestations_objets '
                    'WHERE prestation_id=%s AND ouvrage_telecom_energie_id IS NOT NULL',
                    (identifier,),
                ).fetchone()[0], historical_target)

    def test_minimal(self):
        with TestClient(self.app) as client:
            response = client.post("/api/prestations", json={"date_fin": "2026-01-02"})
            self.assertEqual(response.status_code, 201, response.text)
            feature = response.json()
            # R6 deliberately has the same canonical DTO for native and migrated
            # objects; the retired source-origin marker must not reappear.
            self.assertNotIn("record_origin", feature["properties"])
            self.assertIsNone(feature["geometry"])
            identifier = feature["properties"]["id"]
            self.assertEqual(client.get("/api/prestations/"+identifier).status_code, 200)
            self.assertEqual(client.get("/api/prestations").status_code, 200)
            self.assertEqual(client.get("/api/prestations/relations/options").status_code, 200)
            observation_id = str(uuid4())
            created_observation = client.post("/api/observations", json={
                "id": observation_id, "parent": {"resource": "prestations", "id": identifier},
                "designation": "Contrôle natif",
            }, headers={"Operation-Id": str(uuid4())})
            self.assertEqual(created_observation.status_code, 201, created_observation.text)
            self.assertEqual(client.get("/api/prestations/"+identifier+"/observations")
                             .json()["observations"][0]["id"], observation_id)
            with psycopg.connect(self.dsn) as db:
                columns = {r[0] for r in db.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='prestations'")}
                self.assertFalse({'classe_source', 'nature_historique',
                                  'statut_resolution_type_prestation'} & columns)

    def test_create_replay_and_operation_collision(self):
        identifier, operation = str(uuid4()), str(uuid4())
        payload = {"id": identifier, "date_fin": "2026-01-02"}
        headers = {"Operation-Id": operation}
        with TestClient(self.app) as client:
            first = client.post("/api/prestations", json=payload, headers=headers)
            self.assertEqual(first.status_code, 201, first.text)
            again = client.post("/api/prestations", json=payload, headers=headers)
            self.assertEqual(first.json(), again.json())
            self.assertEqual(client.post("/api/prestations", json={**payload,
                "designation": "Autre"}, headers=headers).status_code, 409)
            self.assertEqual(client.post("/api/prestations", json={"date_fin": "2026-01-02"},
                                         headers={"Operation-Id": str(uuid4())}).status_code, 422)
            with psycopg.connect(self.dsn) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM prestations WHERE id=%s",
                                            (identifier,)).fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM technique.evenements_ressources "
                                            "WHERE resource='prestations' AND "
                                            "payload->'ids' @> jsonb_build_array(%s::text)",
                                            (identifier,)).fetchone()[0], 1)

    def test_geometry_links_version_and_idempotence(self):
        geometries = (None, {"type": "Point", "coordinates": [2, 48]},
                      {"type": "LineString", "coordinates": [[2, 48], [3, 49]]},
                      {"type": "Polygon", "coordinates": [[[2, 48], [3, 48],
                          [3, 49], [2, 48]]]})
        with TestClient(self.app) as client:
            refs = client.get("/api/referentiels/types-prestation")
            self.assertEqual(refs.status_code, 200, refs.text)
            self.assertEqual([r["id"] for r in refs.json()["references"]], self.valid_type_ids)
            ids = []
            for geometry in geometries:
                payload = {"date_fin": "2026-02-02", "type_prestation_id": "T",
                           "designation": "Action", "cout_global": 0,
                           "realisation_interne": False, "valid": False,
                           "troncon_ids": list(map(str, self.troncons)),
                           "object_ids": {"desordres": list(map(str, self.desordres)),
                                          "amenagements_hydrauliques": [str(self.ah)]},
                           "intervenant_ids": [str(self.contact)], "geometry": geometry}
                result = client.post("/api/prestations", json=payload)
                self.assertEqual(result.status_code, 201, result.text)
                feature = result.json()
                ids.append(feature["properties"]["id"])
                self.assertEqual(feature["properties"]["version"], 1)
                self.assertEqual(feature["properties"]["realisation_interne"], False)
                self.assertEqual(feature["geometry"]["type"] if geometry else None,
                                 geometry["type"] if geometry else None)
                self.assertEqual(set(feature["properties"]["troncon_ids"]),
                                 set(map(str, self.troncons)))
                self.assertEqual(len(feature["properties"]["object_ids"]["desordres"]), 2)
                self.assertEqual(feature["properties"]["intervenant_ids"], [str(self.contact)])
            endpoint = "/api/prestations/"+ids[0]
            headers = {"If-Match": '"1"', "Operation-Id": str(uuid4())}
            update = {"geometry": geometries[1], "troncon_ids": [str(self.troncons[1])],
                      "object_ids": {"desordres": [str(self.desordres[1])]},
                      "intervenant_ids": [], "type_prestation_id": None,
                      "commentaire": "", "valid": True}
            result = client.put(endpoint, json=update, headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["properties"]["version"], 2)
            self.assertEqual(result.json()["properties"]["troncon_ids"],
                             [str(self.troncons[1])])
            self.assertEqual(result.json()["properties"]["intervenant_ids"], [])
            self.assertEqual(result.json()["properties"]["object_ids"]["amenagements_hydrauliques"], [])
            self.assertEqual(result.json(), client.put(endpoint, json=update, headers=headers).json())
            self.assertEqual(client.put(endpoint, json={"valid": False},
                                        headers={"If-Match": '"1"'}).status_code, 409)
            self.assertEqual(client.put(endpoint, json={"valid": False}).status_code, 428)
            for version, geometry in ((2, geometries[2]), (3, geometries[3]),
                                      (4, geometries[1])):
                response = client.put(endpoint, json={"geometry": geometry},
                                      headers={"If-Match": f'"{version}"'})
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.put(endpoint, json={"geometry": None},
                                        headers={"If-Match": '"5"'}).status_code, 422)
            self.assertEqual(client.get("/api/prestations/"+str(uuid4())).status_code, 404)
            self.assertEqual(client.put("/api/prestations/"+str(uuid4()),
                                        json={"valid": True},
                                        headers={"If-Match": '"1"'}).status_code, 404)
        with psycopg.connect(self.dsn) as db:
            for identifier, geom in zip(ids, geometries):
                row = db.execute("SELECT ST_SRID(geometry),GeometryType(geometry) "
                                 "FROM prestations WHERE id=%s", (identifier,)).fetchone()
                self.assertEqual(row, (3950, "POINT") if identifier == ids[0] else
                                 ((None, None) if geom is None else (3950, geom["type"].upper())))
            # R6 forbids creation of this retired historical support entirely.
            self.assertIsNone(db.execute(
                "SELECT to_regclass('source_v2.appartenances_groupes_prestations')").fetchone()[0])
            self.assertEqual(db.execute("SELECT count(*) FROM public.localisations_prestations "
                                        "WHERE prestation_id=%s", (ids[0],)).fetchone()[0], 0)

    def test_validation_and_rollback(self):
        with TestClient(self.app) as client:
            base = {"date_fin": "2026-01-02"}
            for extra in ({"type_prestation_id": "OLD"}, {"type_prestation_id": "bad"},
                          {"source_class": "Prestation"}, {"historical_nature": "ORDINARY"},
                          {"geometry": {"type": "MultiPoint", "coordinates": [[2, 48]]}},
                          {"troncon_ids": [str(uuid4())]},
                          {"troncon_ids": [str(self.troncons[0])]*2},
                          {"object_ids": {"desordres": [str(self.inactive)]}},
                          {"object_ids": {"desordres": [str(self.desordres[0])]*2}},
                          {"object_ids": {"unknown": []}},
                          {"intervenant_ids": [str(uuid4())]},
                          {"cout_global": -1}):
                with self.subTest(extra=extra):
                    response = client.post("/api/prestations", json={**base, **extra})
                    self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(client.post("/api/prestations", json={}).status_code, 422)
            identifier = client.post("/api/prestations", json=base).json()["properties"]["id"]
            for payload in ({"date_fin": None}, {"type_prestation_id": "OLD"},
                            {"troncon_ids": [str(uuid4())]},
                            {"object_ids": {"desordres": [str(uuid4())]}}):
                self.assertEqual(client.put("/api/prestations/"+identifier, json=payload,
                                            headers={"If-Match": '"1"'}).status_code, 422)
            with psycopg.connect(self.dsn, autocommit=True) as db:
                db.execute("CREATE FUNCTION reject_f7_link() RETURNS trigger LANGUAGE plpgsql "
                           "AS $$ BEGIN RAISE EXCEPTION 'F7 rollback'; END $$")
                db.execute("CREATE TRIGGER reject_f7_link BEFORE INSERT ON "
                           "public.prestations_troncons FOR EACH ROW "
                           "EXECUTE FUNCTION reject_f7_link()")
                before = db.execute("SELECT count(*) FROM technique.evenements_ressources").fetchone()[0]
            operation = str(uuid4())
            try:
                failed = client.put("/api/prestations/"+identifier,
                                    json={"designation": "Rollback", "troncon_ids":
                                          [str(self.troncons[0])]},
                                    headers={"If-Match": '"1"', "Operation-Id": operation})
                self.assertEqual(failed.status_code, 422, failed.text)
                with psycopg.connect(self.dsn) as db:
                    self.assertEqual(db.execute("SELECT designation,version FROM prestations "
                                                "WHERE id=%s", (identifier,)).fetchone(), (None, 1))
                    self.assertEqual(db.execute("SELECT count(*) FROM technique.evenements_ressources").fetchone()[0], before)
                    self.assertEqual(db.execute("SELECT count(*) FROM technique.operations_ressources "
                                                "WHERE operation_id=%s", (operation,)).fetchone()[0], 0)
            finally:
                with psycopg.connect(self.dsn, autocommit=True) as db:
                    db.execute("DROP TRIGGER reject_f7_link ON public.prestations_troncons")
                    db.execute("DROP FUNCTION reject_f7_link()")
            with psycopg.connect(self.dsn, autocommit=True) as db:
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    db.execute("INSERT INTO public.prestations_troncons "
                               "(prestation_id,troncon_id) VALUES (%s,%s)",
                               (identifier, uuid4()))
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    db.execute("INSERT INTO public.prestations_objets "
                               "(prestation_id,desordre_id) VALUES (%s,%s)",
                               (identifier, uuid4()))
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    db.execute("INSERT INTO public.prestations_intervenants "
                               "(prestation_id,contact_id) VALUES (%s,%s)",
                               (identifier, uuid4()))
                with self.assertRaises(psycopg.errors.CheckViolation):
                    db.execute("INSERT INTO public.prestations_objets "
                               "(prestation_id,desordre_id,amenagement_hydraulique_id) "
                               "VALUES (%s,%s,%s)",
                               (identifier, self.desordres[0], self.ah))

    def test_historical_preservation_observation_photo(self):
        old_ids = [uuid4(), uuid4()]
        with psycopg.connect(self.dsn, autocommit=True) as db:
            for identifier, source, nature in zip(old_ids,
                    ("Prestation", "PrestationAmenagementHydraulique"),
                    ("ORDINARY", "AMENAGEMENT_HYDRAULIQUE")):
                db.execute("INSERT INTO prestations(id,classe_source,nature_historique,"
                           "statut_resolution_type_prestation,type_prestation_source_id,"
                           "type_prestation_id,champs_presents_source,champs_nuls_source) "
                           "VALUES (%s,%s,%s,'RESOLVED','OLD','OLD','{}','{}')",
                           (identifier, source, nature))
            db.execute("INSERT INTO source_v2.troncons_historiques_prestations(prestation_id,source_ordinal,"
                       "relation_source,troncon_source_id,troncon_source_uuid,troncon_id,"
                       "statut_resolution) VALUES (%s,0,'linearId',%s,%s,%s,'RESOLVED')",
                       (old_ids[0], str(self.troncons[0]), self.troncons[0], self.troncons[0]))
            db.execute("INSERT INTO source_v2.concordances_prestations_desordres(id,prestation_id,"
                       "desordre_source_class,desordre_id,prestation_declared,desordre_declared,"
                       "prestation_source_ordinals,desordre_source_ordinals,statut_concordance,"
                       "statut_resolution) VALUES (%s,%s,'Desordre',%s,true,false,'{0}','{}',"
                       "'PRESTATION_ONLY','RESOLVED')",
                       (uuid4(), old_ids[0], self.desordres[0]))
            db.execute("INSERT INTO source_v2.attributs_prestations(prestation_id,"
                       "foreign_parent_source_id) VALUES (%s,'ancienne-parent')", (old_ids[0],))
            db.execute("INSERT INTO source_v2.attributs_prestations_amenagement_hydraulique(prestation_id,"
                       "statut_resolution_amenagement_hydraulique,cote) "
                       "VALUES (%s,'NO_SOURCE_ID',12.5)", (old_ids[1],))
            db.execute("INSERT INTO public.localisations_prestations(prestation_id,"
                       "statut_resolution_troncon,statut_resolution_systeme_reperage,"
                       "statut_resolution_borne_debut,statut_resolution_borne_fin,"
                       "methode_debut,methode_fin,statut_resolution,diagnostic_codes,"
                       "seuil_concordance_forte_m,seuil_avertissement_m,"
                       "seuil_corroboration_m,corroboration_etendue_active) "
                       "VALUES (%s,'SOURCE_MISSING','SOURCE_MISSING','SOURCE_MISSING',"
                       "'SOURCE_MISSING','NON_RESOLU','NON_RESOLU','UNRESOLVED',"
                       "'{LINEAR_ID_BROKEN}',1,2,3,false)", (old_ids[0],))
            db.execute("SELECT public.promouvoir_liens_prestations_importees()")
        with TestClient(self.app) as client:
            for identifier in old_ids:
                endpoint = "/api/prestations/"+str(identifier)
                detail = client.get(endpoint)
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["properties"]["type_libelle"], "Ancien")
                if identifier == old_ids[0]:
                    self.assertEqual(detail.json()["properties"]["troncon_ids"],
                                     [str(self.troncons[0])])
                    # PRESTATION_ONLY reste dans P1, sans lien courant certain.
                    self.assertEqual(detail.json()["properties"]["object_ids"]["desordres"], [])
                self.assertEqual(client.put(endpoint, json={"type_prestation_id": "T"},
                                            headers={"If-Match": '"1"'}).status_code, 422)
                edited = client.put(endpoint, json={"designation": "Action source conservée",
                                                    "troncon_ids": [str(self.troncons[1])]},
                                    headers={"If-Match": '"1"'})
                self.assertEqual(edited.status_code, 200, edited.text)
                self.assertEqual(edited.json()["properties"]["historique"]["source_class"],
                                 "Prestation" if identifier == old_ids[0] else
                                 "PrestationAmenagementHydraulique")
                obs_id = str(uuid4())
                obs = client.post("/api/observations", json={"id": obs_id,
                    "parent": {"resource": "prestations", "id": str(identifier)},
                    "designation": "Visite"}, headers={"Operation-Id": str(uuid4())})
                self.assertEqual(obs.status_code, 201, obs.text)
                self.assertEqual(client.put("/api/observations/"+obs_id,
                    json={"designation": "Visite éditée"},
                    headers={"If-Match": '"1"', "Operation-Id": str(uuid4())}).status_code, 200)
                with psycopg.connect(self.dsn, autocommit=True) as db:
                    db.execute("INSERT INTO photos(id,observation_id,chemin_source,valid) "
                               "VALUES (%s,%s,'photo.jpg',true)", (uuid4(), obs_id))
                    self.assertEqual(db.execute("SELECT prestation_id,num_nonnulls("
                        "desordre_id,troncon_id,ouvrage_hydraulique_id,equipement_mesure_id,"
                        "cheminement_id,mobilier_id,reseau_technique_id,"
                        "amenagement_hydraulique_id,vegetation_id,ouvrage_particulier_id,"
                        "structure_amenagement_hydraulique_id,organe_protection_collective_id,"
                        "ouvrage_associe_amenagement_hydraulique_id,prestation_id) "
                        "FROM observations WHERE id=%s", (obs_id,)).fetchone(), (identifier, 1))
                self.assertEqual(client.get(endpoint+"/observations").json()
                                 ["observations"][0]["photo_count"], 1)
            with psycopg.connect(self.dsn) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM source_v2.troncons_historiques_prestations "
                                            "WHERE prestation_id=%s", (old_ids[0],)).fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM source_v2.concordances_prestations_desordres "
                                            "WHERE prestation_id=%s", (old_ids[0],)).fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM public.localisations_prestations "
                                            "WHERE prestation_id=%s", (old_ids[0],)).fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT diagnostic_codes FROM "
                    "public.localisations_prestations WHERE prestation_id=%s",
                    (old_ids[0],)).fetchone()[0], ["LINEAR_ID_BROKEN"])
                self.assertEqual(db.execute("SELECT foreign_parent_source_id FROM "
                    "source_v2.attributs_prestations WHERE prestation_id=%s",
                    (old_ids[0],)).fetchone()[0], "ancienne-parent")
                self.assertEqual(db.execute("SELECT cote FROM source_v2.attributs_prestations_amenagement_hydraulique "
                    "WHERE prestation_id=%s", (old_ids[1],)).fetchone()[0], 12.5)
