"""Lecture des rattachements, avec PostgreSQL jetable explicitement choisi."""
import os
import json
import re
from pathlib import Path
import shutil
import subprocess
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import psycopg

from tests.geometry_fixtures import LINE_SQL, POINT_SQL
from digues_app.migration.media import OWNER_FIELDS
from digues_app.migration.canonical_media_writes import _PARENT_COLUMNS, _RECLASSIFIED_PARENT
from digues_app.target.schema import TABLE_DEFINITIONS
from digues_webapp.app import create_app
from digues_webapp.database import WebDatabaseError, get_connection, get_write_connection
from digues_webapp.data_views import execute_view_query
from digues_webapp.models import DataViewFieldSelection
from digues_webapp.offline_projection import read_pinned, read_scope
from digues_webapp.queries import (
    OBSERVATION_PARENTS, OBSERVATION_READ_ONLY_PARENTS,
    OBSERVATION_READ_PARENTS, OBSERVATION_PARENT_SQL_TABLES,
    OBSERVATION_DETAIL_SQL, fetch_observation,
)
from webapp.tests.test_resource_versions import http_client


class ObservationParentTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node non installé')
    def test_shared_form_accepts_exactly_the_backend_parent_families(self):
        script = """
          const assert = require('node:assert/strict');
          const form = require(process.argv[1]);
          for (const resource of JSON.parse(process.argv[2])) {
            const observation = {parent: {resource, id: 'parent-id', designation: 'Parent AH'}};
            assert.equal(form.parent(observation).resource, resource);
            assert.ok(form.parentText(observation).endsWith(' — Parent AH'));
            assert.ok(!form.parentText(observation).includes('undefined'));
            assert.equal(form.fields(observation).length, resource === 'desordres' ? 7 : 5);
          }
          assert.throws(() => form.parent({parent:{resource:'unknown',id:'id'}}));
        """
        result = subprocess.run(['node', '-e', script,
            str(Path(__file__).resolve().parents[1] / 'frontend/js/observation-form.js'),
            json.dumps([resource for _, resource, _ in OBSERVATION_READ_PARENTS])],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_mapping_covers_all_observation_foreign_keys(self):
        parent_check = re.search(
            r"num_nonnulls\(([^)]+)\) = 1",
            TABLE_DEFINITIONS["public.observations"],
        )
        self.assertIsNotNone(parent_check)
        ddl_parent_fields = tuple(field.strip() for field in parent_check.group(1).split(","))
        current_parent_fields = tuple(row[0] for row in OBSERVATION_READ_PARENTS)
        self.assertEqual(set(ddl_parent_fields), set(current_parent_fields))
        self.assertEqual(len(current_parent_fields),len(set(current_parent_fields)))
        self.assertEqual(_PARENT_COLUMNS, current_parent_fields)
        # Les champs historiques demeurent des entrées DB-3 distinctes des FK
        # courantes. Leur reclassement reste à qualifier après MOD-1M/MOD-1N.
        historical_only = set(OWNER_FIELDS) - set(current_parent_fields)
        self.assertEqual(historical_only, {
            "ouvrage_associe_amenagement_hydraulique_id", "hydraulic_measure_id",
            "berge_source_component_id", "lit_source_object_id",
        })
        self.assertEqual(set(_RECLASSIFIED_PARENT), historical_only - {"lit_source_object_id"})
        self.assertTrue(set(_RECLASSIFIED_PARENT.values()) <= set(current_parent_fields))
        self.assertEqual(len(current_parent_fields), len(OBSERVATION_PARENTS) + len(OBSERVATION_READ_ONLY_PARENTS))
        self.assertTrue({row[1] for row in OBSERVATION_PARENTS}.isdisjoint(
            {row[1] for row in OBSERVATION_READ_ONLY_PARENTS}
        ))
        from digues_webapp.models import ObservationParent
        for _, resource, labels in OBSERVATION_READ_PARENTS:
            ObservationParent(resource=resource, id=uuid4(), designation="Parent")
            table = OBSERVATION_PARENT_SQL_TABLES.get(resource, f"public.{resource}")
            self.assertIn(table, TABLE_DEFINITIONS)
            for label in labels:
                self.assertRegex(TABLE_DEFINITIONS[table], rf"(?m)^    {label} ")

    def test_new_historical_parents_are_readable_without_becoming_creation_parents(self):
        from pydantic import ValidationError
        from digues_webapp.models import ObservationParentInput

        for fk, resource, _ in OBSERVATION_READ_ONLY_PARENTS:
            with self.subTest(resource=resource):
                self.assertIn(f"o.{fk}", OBSERVATION_DETAIL_SQL)
                with self.assertRaises(ValidationError):
                    ObservationParentInput(resource=resource, id=uuid4())
                parent_id = str(uuid4())
                with patch("digues_webapp.queries._json_object", return_value={
                    "id": str(uuid4()), "photos": [],
                    "_parent_candidates": [{"resource": resource, "id": parent_id,
                                            "matched_id": parent_id, "designation": "Parent"}],
                }):
                    self.assertEqual(fetch_observation(None, uuid4())["parent"]["resource"], resource)

    def test_ouvrage_particulier_parent_is_resolved_by_read_api(self):
        parent_id = str(uuid4())
        observation_id = uuid4()
        with patch("digues_webapp.queries._json_object", return_value={
            "id": str(observation_id), "photos": [],
            "_parent_candidates": [{
                "resource": "ouvrages_particuliers", "id": parent_id,
                "matched_id": parent_id, "designation": "Ouvrage particulier",
            }],
        }):
            detail = fetch_observation(None, observation_id)
        self.assertEqual(detail["parent"], {
            "resource": "ouvrages_particuliers", "id": parent_id,
            "designation": "Ouvrage particulier",
        })

    def test_unified_structure_parent_exposes_only_activated_subtypes(self):
        from pydantic import ValidationError
        from digues_webapp.models import ObservationParent

        parent_id = str(uuid4())
        for subtype in ("CRETE", "TALUS", "TALUS_RISBERME"):
            with self.subTest(subtype=subtype), patch("digues_webapp.queries._json_object", return_value={
                "id": str(uuid4()), "photos": [], "_parent_candidates": [{
                    "resource": "structures_digue", "id": parent_id,
                    "matched_id": parent_id, "designation": "Structure",
                    "type_physique_id": subtype,
                }],
            }):
                self.assertEqual(fetch_observation(None, uuid4())["parent"], {
                    "resource": "structures_digue", "id": parent_id,
                    "designation": "Structure", "type_physique_id": subtype,
                })
        with self.assertRaises(ValidationError):
            ObservationParent(resource="structures_digue", id=parent_id,
                              designation="Structure", type_physique_id="DIGUE")

    def test_invalid_parent_is_rejected_without_silent_selection(self):
        identifier = str(uuid4())
        parent = dict(resource="troncons", id=identifier, matched_id=identifier, designation="Tronçon")
        for candidates in (None, [], [parent, parent], [{**parent, "matched_id": None}],
                           [{**parent, "resource": "unknown"}], [{**parent, "designation": ""}]):
            with self.subTest(candidates=candidates), patch(
                "digues_webapp.queries._json_object",
                return_value={"id": str(uuid4()),
                              "_parent_candidates": deepcopy(candidates)},
            ), self.assertRaises(WebDatabaseError):
                fetch_observation(None, uuid4())


@unittest.skipUnless(os.getenv("DIGUES_OCC_TEST_DSN"), "PostGIS jetable non configuré")
class ObservationDetailPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.postgres_safety import require_test_dsn
        cls.dsn = require_test_dsn("DIGUES_OCC_TEST_DSN")

    def setUp(self):
        self.connection = psycopg.connect(self.dsn, autocommit=True)
        self.addCleanup(self.connection.close)
        self.parents = {resource: uuid4() for _, resource, _ in OBSERVATION_PARENTS}
        self.observations = {resource: uuid4() for resource in self.parents}
        self.labels = {resource: f"Parent {resource}" for resource in self.parents}
        c = self.connection
        systeme, digue = uuid4(), uuid4()
        c.execute("INSERT INTO systemes(id,libelle,valid) VALUES (%s,'SE',true)", (systeme,))
        c.execute("INSERT INTO digues(id,systeme_endiguement_id,libelle,valid) VALUES (%s,%s,'Digue',true)", (digue, systeme))
        c.execute("INSERT INTO troncons(id,type_physique_id,digue_id,libelle,geometry,valid) VALUES (%s,'INDETERMINE',%s,%s,"
                  "ST_Transform(ST_GeomFromText('LINESTRING(2 46,2.01 46.01)',4326),3950),true)",
                  (self.parents['troncons'], digue, self.labels['troncons']))
        c.execute("INSERT INTO desordres(id,designation,geometry,valid) VALUES (%s,%s,"
                  "ST_Transform(ST_GeomFromText('POINT(2 46)',4326),3950),true)",
                  (self.parents['desordres'], self.labels['desordres']))
        c.execute("INSERT INTO public.desordres__troncons(desordre_id,troncon_id) VALUES (%s,%s)",
                  (self.parents['desordres'], self.parents['troncons']))
        c.execute("INSERT INTO amenagements_hydrauliques(id,designation,valid) "
                  "VALUES (%s,%s,true)",
                  (self.parents['amenagements_hydrauliques'], self.labels['amenagements_hydrauliques']))
        # A Zone is its own canonical Observation parent; the AH has no
        # geometry in R6. Build the explicitly requested Zone, not a fallback AH.
        c.execute("INSERT INTO zones_stockage(id,designation,geometry,valid) "
                  "VALUES (%s,%s,ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 0))',3950),true)",
                  (self.parents['zones_stockage'], self.labels['zones_stockage']))
        for resource, reference, type_field in (
            ('ouvrages_hydrauliques', 'referentiel.types_ouvrage_hydraulique', 'type_id'),
            ('equipements_mesure', 'referentiel.types_equipement_mesure', 'type_id'),
            ('mobilier', 'referentiel.types_mobilier', 'type_id'),
            ('reseaux_techniques', 'referentiel.types_reseau_technique', 'type_id'),
            ('cheminements', 'referentiel.types_cheminement', 'type_cheminement_id'),
        ):
            ref_id = str(uuid4())
            c.execute(f"INSERT INTO {reference}(id,code,abrege,libelle,valid) VALUES (%s,%s,%s,'Test',true)", (ref_id, ref_id, ref_id))
            geometry = LINE_SQL if resource == 'cheminements' else POINT_SQL
            geometry_columns = ',geometry' if resource in {'equipements_mesure', 'mobilier', 'cheminements'} else ''
            geometry_values = f',{geometry}' if geometry_columns else ''
            c.execute(f"INSERT INTO {resource}(id,{type_field},designation{geometry_columns},valid) "
                      f"VALUES (%s,%s,%s{geometry_values},true)",
                      (self.parents[resource], ref_id, self.labels[resource]))
        c.execute(f"INSERT INTO ouvrages_particuliers(id,designation,geometry,valid) VALUES (%s,%s,{POINT_SQL},true)",
                  (self.parents['ouvrages_particuliers'], self.labels['ouvrages_particuliers']))
        for resource in ('structures_amenagements_hydrauliques', 'organes_protection_collective'):
            table = OBSERVATION_PARENT_SQL_TABLES.get(resource, f'public.{resource}')
            c.execute(f"INSERT INTO {table}(id,amenagement_hydraulique_id,designation,valid) "
                      "VALUES (%s,%s,%s,true)",
                      (self.parents[resource], self.parents['amenagements_hydrauliques'],
                       self.labels[resource]))
        c.execute(f"INSERT INTO vegetation(id,famille_vegetation,disposition,sous_type,designation,geometry,valid) "
                  f"VALUES (%s,'ARBRES','ISOLE','ARBRE_SUR_PIED',%s,{POINT_SQL},true)",
                  (self.parents['vegetation'], self.labels['vegetation']))
        c.execute(
            "INSERT INTO prestations(id,date_fin,designation,valid) VALUES "
            "(%s,DATE '2026-01-01',%s,true)",
            (self.parents['prestations'], self.labels['prestations']),
        )
        for fk, resource, _ in OBSERVATION_PARENTS:
            c.execute(f"INSERT INTO observations(id,{fk},designation,valid) VALUES (%s,%s,%s,true)",
                      (self.observations[resource], self.parents[resource], f"Observation {resource}"))
        self.photo = uuid4()
        c.execute("INSERT INTO photos(id,observation_id,storage_key,valid) VALUES (%s,%s,%s,true)",
                  (self.photo, self.observations['desordres'], f"private/{self.photo}.jpg"))
        self.app = create_app()
        def database():
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                yield connection
        self.app.dependency_overrides[get_connection] = database
        self.app.dependency_overrides[get_write_connection] = database

    def test_http_reads_all_parents_and_preserves_photos_and_version(self):
        before = self.connection.execute("SELECT count(*) FROM technique.evenements_ressources").fetchone()[0]
        with http_client(self.app) as client:
            for resource, observation in self.observations.items():
                with self.subTest(resource=resource):
                    response = client.get(f"/api/observations/{observation}")
                    self.assertEqual(response.status_code, 200, response.text)
                    payload = response.json()
                    expected_parent = dict(resource=resource, id=str(self.parents[resource]),
                                           designation=self.labels[resource])
                    if resource == 'troncons':
                        expected_parent.update(type_physique_id='INDETERMINE',
                                               canonical_resource='troncons', relation_role='troncon_id')
                    self.assertEqual(payload['parent'], expected_parent)
                    self.assertEqual(payload['version'], 1)
                    self.assertNotIn('desordre_id', payload)
                    self.assertFalse(any(key.startswith('_') for key in payload))
                    for fk, _, _ in OBSERVATION_PARENTS[1:]:
                        self.assertNotIn(fk, payload)
                    if resource == 'desordres':
                        self.assertEqual([p['id'] for p in payload['photos']], [str(self.photo)])
                        self.assertIsNone(payload['photos'][0]['nom_fichier'])
                        self.assertFalse(payload['photos'][0]['content_available'])
                        self.assertEqual(self.connection.execute(
                            'SELECT storage_key FROM photos WHERE id=%s', (self.photo,)
                        ).fetchone()[0], f'private/{self.photo}.jpg')
                    else:
                        self.assertEqual(payload['photos'], [])
            self.assertEqual(client.get(f"/api/observations/{uuid4()}").status_code, 404)
            listing = client.get(f"/api/desordres/{self.parents['desordres']}/observations").json()
            self.assertEqual([o['id'] for o in listing['observations']], [str(self.observations['desordres'])])
            self.assertEqual(listing['observations'][0]['parent']['id'], str(self.parents['desordres']))
            self.assertEqual(listing['observations'][0]['photo_count'], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM technique.evenements_ressources").fetchone()[0], before)

    def test_unified_digue_structures_keep_read_only_parent_subtype_and_photos(self):
        # Contrat C3 : même FK typée Observation et même UUID canonique,
        # avec un type stable sans ouverture du parent dans le DTO de création.
        cases = (("CRETE", "CRETE"), ("TALUS", "TALUS"),
                 ("TALUS_RISBERME", "TALUS_RISBERME"))
        for extension_type, root_type in cases:
            with self.subTest(type=root_type):
                identifier, observation, photo = uuid4(), uuid4(), uuid4()
                with self.connection.transaction():
                    self.connection.execute(
                        "INSERT INTO public.troncons"
                        "(id,type_physique_id,designation,valid) VALUES (%s,%s,%s,false)",
                        (identifier, root_type, f"{root_type} conservé"),
                    )
                    self.connection.execute(
                        "UPDATE public.troncons SET num_couche=2 WHERE id=%s", (identifier,),
                    )
                    self.connection.execute(
                        "INSERT INTO public.observations(id,structure_digue_id,valid) "
                        "VALUES (%s,%s,true)", (observation, identifier),
                    )
                    self.connection.execute(
                        "INSERT INTO public.photos(id,observation_id,valid) VALUES (%s,%s,true)",
                        (photo, observation),
                    )
                with http_client(self.app) as client:
                    response = client.get(f"/api/observations/{observation}")
                self.assertEqual(response.status_code, 200, response.text)
                detail = response.json()
                self.assertEqual(detail["parent"], {
                    "resource": "structures_digue", "id": str(identifier),
                    "designation": f"{root_type} conservé", "type_physique_id": root_type,
                    "canonical_resource": "troncons", "relation_role": "structure_digue_id",
                })
                self.assertEqual([row["id"] for row in detail["photos"]], [str(photo)])
                self.assertEqual(self.connection.execute(
                    "SELECT num_couche,valid FROM public.troncons WHERE id=%s", (identifier,),
                ).fetchone(), (2, False))

    def test_position_trace_parent_keeps_photos_and_only_proven_berge_type(self):
        berge, pied, detached, first_observation, second_observation, photo = (uuid4() for _ in range(6))
        with self.connection.transaction():
            self.connection.execute(
                "INSERT INTO public.troncons"
                "(id,type_physique_id,designation,valid) VALUES (%s,'BERGE','Berge',false)",
                (berge,),
            )
            self.connection.execute(
                "INSERT INTO public.traces_positions_troncons"
                "(id,position_code,troncon_position_id,designation,valid) "
                "VALUES (%s,'PIED',%s,'Pied historique',false)",
                (pied, berge),
            )
            self.connection.execute(
                "INSERT INTO public.traces_positions_troncons"
                "(id,position_code,designation,valid) "
                "VALUES (%s,'PIED','Pied sans parent prouvé',false)",
                (detached,),
            )
            for observation, trace in ((first_observation, pied), (second_observation, detached)):
                self.connection.execute(
                    "INSERT INTO public.observations(id,trace_position_troncon_id,valid) "
                    "VALUES (%s,%s,true)", (observation, trace),
                )
            self.connection.execute(
                "INSERT INTO public.photos(id,observation_id,valid) VALUES (%s,%s,true)",
                (photo, first_observation),
            )
        with http_client(self.app) as client:
            first = client.get(f"/api/observations/{first_observation}")
            second = client.get(f"/api/observations/{second_observation}")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["parent"], {
            "resource": "traces_positions_troncons", "id": str(pied),
            "designation": "Pied historique", "type_physique_id": "BERGE",
        })
        self.assertEqual([item["id"] for item in first.json()["photos"]], [str(photo)])
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["parent"], {
            "resource": "traces_positions_troncons", "id": str(detached),
            "designation": "Pied sans parent prouvé",
        })

    def test_berge_parent_reads_each_root_designation_without_source_class(self):
        identifiers = (uuid4(), uuid4())
        observations = (uuid4(), uuid4())
        with self.connection.transaction():
            for identifier, observation, label in zip(
                identifiers, observations, ("Berge propre", "Trait propre"), strict=True,
            ):
                self.connection.execute(
                    "INSERT INTO public.troncons"
                    "(id,type_physique_id,designation,valid) VALUES (%s,'BERGE',%s,false)",
                    (identifier, label),
                )
                self.connection.execute(
                    "INSERT INTO public.observations(id,berge_id,valid) VALUES (%s,%s,true)",
                    (observation, identifier),
                )
        with http_client(self.app) as client:
            details = [client.get(f"/api/observations/{observation}") for observation in observations]
        for response, identifier, label in zip(
            details, identifiers, ("Berge propre", "Trait propre"), strict=True,
        ):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["parent"], {
                "resource": "berges", "id": str(identifier),
                "designation": label, "type_physique_id": "BERGE",
                "canonical_resource": "troncons", "relation_role": "berge_id",
            })
            self.assertNotIn("source_class", response.text)

    def test_label_fallback_and_priority(self):
        for resource in self.parents:
            with self.subTest(resource=resource):
                table = OBSERVATION_PARENT_SQL_TABLES.get(resource, f'public.{resource}')
                self.connection.execute(f"UPDATE {table} SET designation='   ' WHERE id=%s", (self.parents[resource],))
                expected = self.labels[resource] if resource == 'troncons' else str(self.parents[resource])
                self.assertEqual(fetch_observation(self.connection, self.observations[resource])['parent']['designation'], expected)
        for resource in ('troncons', 'cheminements'):
            self.connection.execute(f"UPDATE {resource} SET libelle='Libellé de repli' WHERE id=%s", (self.parents[resource],))
            self.assertEqual(fetch_observation(self.connection, self.observations[resource])['parent']['designation'], 'Libellé de repli')
            self.connection.execute(f"UPDATE {resource} SET designation=' Désignation prioritaire ' WHERE id=%s", (self.parents[resource],))
            self.assertEqual(fetch_observation(self.connection, self.observations[resource])['parent']['designation'], 'Désignation prioritaire')
        self.connection.execute("UPDATE mobilier SET designation=NULL WHERE id=%s", (self.parents['mobilier'],))
        self.assertEqual(fetch_observation(self.connection, self.observations['mobilier'])['parent']['designation'], str(self.parents['mobilier']))

    def test_inconsistent_database_result_returns_controlled_http_error(self):
        with http_client(self.app) as client, patch(
            'digues_webapp.queries._json_object',
            return_value={'id': str(self.observations['troncons']), '_parent_candidates': []},
        ):
            response = client.get(f"/api/observations/{self.observations['troncons']}")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {'detail': 'La base de données est temporairement indisponible.'})

    def test_put_retains_occ_idempotence_and_transversal_scope(self):
        observation = self.observations['desordres']
        with http_client(self.app) as client:
            url = f'/api/observations/{observation}'
            changes = {'suite': 'À suivre'}
            self.assertEqual(client.put(url, json=changes).status_code, 428)
            headers = {'If-Match': '"1"', 'Operation-Id': str(uuid4())}
            response = client.put(url, json=changes, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload['version'], 2)
            self.assertEqual(payload['parent']['resource'], 'desordres')
            self.assertEqual(payload['mutation']['versions'], {str(observation): 2})
            self.assertEqual(client.put(url, json=changes, headers=headers).json(), payload)
            self.assertEqual(client.put(url, json=changes, headers={'If-Match': '"1"'}).status_code, 409)
            self.assertEqual(client.get(url).json()['suite'], 'À suivre')
            for resource in self.parents.keys() - {'desordres'}:
                other = f'/api/observations/{self.observations[resource]}'
                response = client.put(other, json=changes, headers={'If-Match': '"1"'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(client.get(other).json()['version'], 2)
        count = self.connection.execute("SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s", (str(observation),)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_offline_scope_and_pinned_recovery_cover_disorder_and_trunk_only(self):
        snapshot = read_scope(self.connection, [self.parents['troncons']])
        observations = [r for r in snapshot['records'] if r['resource'] == 'observations']
        self.assertEqual({r['id'] for r in observations}, {str(self.observations[r]) for r in ('desordres', 'troncons')})
        self.assertEqual({r['payload']['parent']['resource'] for r in observations}, {'desordres', 'troncons'})
        self.assertTrue(all('photos' not in r['payload'] for r in observations))
        for resource in self.parents.keys() - {'desordres', 'troncons'}:
            self.assertEqual(read_pinned(self.connection, 'observations', self.observations[resource])['records'], [])
        self.assertTrue(read_pinned(self.connection, 'observations', self.observations['desordres'])['records'])

    def test_data_views_keep_observations_of_all_parents(self):
        fields = [DataViewFieldSelection(table='observations', field='designation')]
        view = execute_view_query(self.connection, 'observations', fields)
        identifiers = {entity['id'] for row in view['rows'] for entity in row['entities'] if entity['class'] == 'observations'}
        self.assertTrue(set(map(str, self.observations.values())) <= identifiers)
        # La racine Désordre reste délibérément limitée à ses enfants.
        view = execute_view_query(self.connection, 'desordres', fields)
        identifiers = {entity['id'] for row in view['rows'] for entity in row['entities'] if entity['class'] == 'observations'}
        self.assertIn(str(self.observations['desordres']), identifiers)
        self.assertTrue(set(map(str, (v for k, v in self.observations.items() if k != 'desordres'))).isdisjoint(identifiers))

    def test_sql_constraint_rejects_zero_or_two_parents(self):
        for values in ((None, None), (self.parents['desordres'], self.parents['troncons'])):
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.connection.execute("INSERT INTO observations(desordre_id,troncon_id,valid) VALUES (%s,%s,true)", values)

    def test_all_parent_foreign_keys_reject_missing_target(self):
        for fk, resource, _ in OBSERVATION_PARENTS:
            with self.subTest(resource=resource), self.assertRaises(psycopg.errors.ForeignKeyViolation):
                self.connection.execute(f"INSERT INTO observations({fk},valid) VALUES (%s,true)", (uuid4(),))

    def test_ah_parent_updates_keep_fk_and_photos(self):
        with http_client(self.app) as client:
            for fk, resource, _ in OBSERVATION_PARENTS[-3:]:
                identifier = self.observations[resource]
                photo = uuid4()
                self.connection.execute("INSERT INTO photos(id,observation_id,storage_key,valid) "
                                        "VALUES (%s,%s,%s,true)",
                                        (photo, identifier, f"synthetic/{photo}.png"))
                response = client.put(f'/api/observations/{identifier}', json={'suite': 'Suivi AH'},
                                      headers={'If-Match': '"1"', 'Operation-Id': str(uuid4())})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['parent']['resource'], resource)
                self.assertEqual(response.json()['photos'][0]['id'], str(photo))
                self.assertEqual(self.connection.execute(f'SELECT {fk},suite FROM observations WHERE id=%s',
                                                        (identifier,)).fetchone(), (self.parents[resource], 'Suivi AH'))

    def test_current_segment_parent_is_read_only(self):
        segment = uuid4()
        self.connection.execute(
            "INSERT INTO public.segments_hydrographiques(id,designation,geometry,valid) "
            f"VALUES (%s,'Segment de test',{LINE_SQL},true)", (segment,))
        with http_client(self.app) as client:
            for fk, resource, parent in (("segment_hydrographique_id",
                                          "segments_hydrographiques", segment),):
                with self.subTest(resource=resource):
                    observation = uuid4()
                    self.connection.execute(
                        f"INSERT INTO public.observations(id,{fk},valid) VALUES (%s,%s,true)",
                        (observation, parent))
                    url = f"/api/observations/{observation}"
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["parent"]["resource"], resource)
                    update = client.put(url, json={"suite": "Modification refusée"},
                                        headers={"If-Match": '"1"', "Operation-Id": str(uuid4())})
                    self.assertEqual(update.status_code, 422, update.text)
                    self.assertEqual(update.json()["detail"]["code"], "observation-parent-read-only")
                    self.assertEqual(client.get(url).json()["version"], 1)

    def test_ouvrage_particulier_fk_and_unique_parent_constraint(self):
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.connection.execute(
                "INSERT INTO observations(ouvrage_particulier_id,valid) VALUES (%s,true)",
                (uuid4(),),
            )
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute(
                "INSERT INTO observations(ouvrage_particulier_id,troncon_id,valid) "
                "VALUES (%s,%s,true)",
                (self.parents['ouvrages_particuliers'], self.parents['troncons']),
            )
