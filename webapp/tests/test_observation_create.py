"""POST Observation : parents autorisés et reçus transactionnels."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import get_args
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from pydantic import ValidationError

from digues_webapp.models import ObservationCreate, ObservationCreateParentResource
from digues_webapp.offline_projection import read_scope
from digues_webapp.queries import create_observation
from digues_webapp.resource_mutations import execute_mutation
from webapp.tests import test_observation_detail as parents
from webapp.tests import test_resource_versions as fixtures


class ObservationCreateModelTest(unittest.TestCase):
    def test_contract_forbids_extra_fields_and_invalid_business_values(self):
        payload = {'id': str(uuid4()), 'parent': {'resource': 'troncons', 'id': str(uuid4())}}
        for changes in ({'id': 'not-uuid'}, {'id': 123}, {'id': None}, {'parent': None},
                        {'parent': {'resource': 'systemes', 'id': str(uuid4())}},
                        {'parent': {'resource': 'troncons', 'id': 'bad'}},
                        {'desordre_id': str(uuid4())}, {'version': 4}, {'nombre_desordres': -1},
                        {'nombre_desordres': 1.2}, {'nombre_desordres': True}, {'valid': 'true'},
                        {'urgence_id': 'urgente'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ObservationCreate(**(payload | changes))
        value = ObservationCreate(**(payload | {'designation': ' Test ', 'suite': '  '}))
        self.assertEqual(value.designation, 'Test')
        self.assertIsNone(value.suite)
        self.assertTrue(value.valid)
        self.assertIsNone(value.date)
        self.assertEqual(ObservationCreate(**(payload | {
            'parent': {'resource': 'ouvrages_particuliers', 'id': str(uuid4())}
        })).parent.resource, 'ouvrages_particuliers')


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'PostGIS jetable non configuré')
class ObservationCreatePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ResourceVersionsPostgresTest.setUpClass()
        cls.dsn = os.environ['DIGUES_OCC_TEST_DSN']

    def setUp(self):
        parents.ObservationDetailPostgresTest.setUp(self)

    def payload(self, resource='desordres'):
        return {'id': str(uuid4()), 'parent': {'resource': resource, 'id': str(self.parents[resource])},
                'designation': 'Constat terrain', 'date': '2026-09-17', 'evolution': 'Stable',
                'suite': 'À suivre', 'nombre_desordres': 2, 'valid': True}

    def write(self, payload, operation, connection=None):
        creation = ObservationCreate(**payload)
        connection = connection or self.connection
        return execute_mutation(connection, 'observations', 'create',
            lambda: create_observation(connection, creation), identifier=creation.id,
            operation_id=operation, changes=creation.model_dump(mode='json'))

    def counts(self, identifier):
        c = self.connection
        return (c.execute('SELECT count(*) FROM observations WHERE id=%s', (identifier,)).fetchone()[0],
                c.execute("SELECT count(*) FROM technique.evenements_ressources WHERE payload->'ids' ? %s", (identifier,)).fetchone()[0],
                c.execute('SELECT count(*) FROM technique.operations_ressources WHERE resource_id=%s', (identifier,)).fetchone()[0])

    def test_http_creation_for_existing_families_uuid_version_parent_and_event(self):
        with fixtures.http_client(self.app) as client:
            for resource in get_args(ObservationCreateParentResource):
                with self.subTest(resource=resource):
                    payload = self.payload(resource)
                    response = client.post('/api/observations', json=payload, headers={
                        'Operation-Id': str(uuid4()), 'X-Digues-App-Client-Id': 'test-client',
                        'X-Digues-App-Instance-Id': 'test-page',
                    })
                    self.assertEqual(response.status_code, 201, response.text)
                    created = response.json()
                    self.assertEqual(created['id'], payload['id'])
                    self.assertEqual(created['version'], 1)
                    self.assertEqual(created['parent'], payload['parent'] | {'designation': self.labels[resource]})
                    self.assertEqual(created['mutation']['action'], 'create')
                    self.assertEqual(created['mutation']['resource'], 'observations')
                    self.assertEqual(created['mutation']['versions'], {payload['id']: 1})
                    self.assertEqual(created['mutation']['originClientId'], 'test-client')
                    self.assertEqual(created['mutation']['originInstanceId'], 'test-page')
                    self.assertEqual(created['photos'], [])
                    read = client.get('/api/observations/' + payload['id']).json()
                    self.assertEqual(read, {k: v for k, v in created.items() if k != 'mutation'})
                    self.assertEqual(self.counts(payload['id']), (1, 1, 1))
            for resource in self.parents.keys() - set(get_args(ObservationCreateParentResource)):
                response = client.post('/api/observations', json=self.payload(resource),
                                       headers={'Operation-Id': str(uuid4())})
                self.assertEqual(response.status_code, 422, response.text)

    def test_troncon_observation_list_matches_exact_parent_and_missing_parent(self):
        # Niveau B : SQL/HTTP réels, parents uniques par setUp ; aucun faux
        # parent ni archivage. Le lien Désordre–Tronçon ne vaut pas parent direct.
        with fixtures.http_client(self.app) as client:
            target = '/api/troncons/' + str(self.parents['troncons']) + '/observations'
            before = client.get(target)
            self.assertEqual(before.status_code, 200, before.text)
            self.assertEqual([row['id'] for row in before.json()['observations']], [str(self.observations['troncons'])])
            expected = self.write(self.payload('troncons'), str(uuid4()))
            self.write(self.payload('desordres'), str(uuid4()))
            response = client.get(target)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['parent'], expected['parent'])
            rows = {row['id']: row for row in response.json()['observations']}
            self.assertEqual(set(rows), {str(self.observations['troncons']), expected['id']})
            self.assertEqual(rows[expected['id']]['designation'], expected['designation'])
            self.assertEqual(client.get('/api/troncons/' + str(uuid4()) + '/observations').status_code, 404)
            self.assertEqual(client.get('/api/troncons/invalide/observations').status_code, 422)

    def test_http_idempotent_retry_and_collisions(self):
        payload = self.payload()
        headers = {'Operation-Id': str(uuid4())}
        with fixtures.http_client(self.app) as client:
            first = client.post('/api/observations', json=payload, headers=headers)
            self.assertEqual(first.status_code, 201, first.text)
            # Champs par défaut explicites et ordre des clés ne changent pas l'intention.
            retry = client.post('/api/observations', json=payload | {'urgence_id': None, 'suite_apporter_id': None}, headers=headers)
            self.assertEqual(retry.status_code, 201)
            self.assertEqual(retry.json(), first.json())
            changed = client.post('/api/observations', json=payload | {'suite': 'Différent'}, headers=headers)
            self.assertEqual(changed.status_code, 409)
            self.assertEqual(changed.json()['detail']['code'], 'operation-id-collision')
            duplicate = client.post('/api/observations', json=payload, headers={'Operation-Id': str(uuid4())})
            self.assertEqual(duplicate.status_code, 409)
            self.assertEqual(duplicate.json()['detail']['code'], 'observation-id-collision')
            # Une édition ultérieure ne transforme pas le retry de création en update.
            client.put('/api/observations/' + payload['id'], json={'suite': 'Modifié'}, headers={'If-Match': '"1"'})
            self.assertEqual(client.post('/api/observations', json=payload, headers=headers).json(), first.json())
            self.assertEqual(client.get('/api/observations/' + payload['id']).json()['version'], 2)
        self.assertEqual(self.counts(payload['id']), (1, 2, 1))

    def test_http_validation_missing_parent_unknown_resource_uuid_and_operation(self):
        original = self.payload()
        with fixtures.http_client(self.app) as client:
            for changes, code in (
                ({'parent': {'resource': 'desordres', 'id': str(uuid4())}}, 'observation-parent-not-found'),
                ({'parent': {'resource': 'photos', 'id': str(uuid4())}}, None),
                ({'parent': None}, None), ({'id': 'bad'}, None),
                ({'parent': {'resource': 'troncons', 'id': str(self.parents['troncons'])}, 'urgence_id': 'u'}, None),
                ({'suite_apporter_id': 'absente'}, None), ({'urgence_id': 'absente'}, None),
                ({'desordre_id': str(uuid4())}, None),
            ):
                response = client.post('/api/observations', json=original | changes, headers={'Operation-Id': str(uuid4())})
                self.assertEqual(response.status_code, 422, response.text)
                if code:
                    self.assertEqual(response.json()['detail']['code'], code)
            for headers in ({}, {'Operation-Id': 'not-uuid'}):
                self.assertEqual(client.post('/api/observations', json=original, headers=headers).status_code, 422)
        self.assertEqual(self.counts(original['id']), (0, 0, 0))

    def test_valid_references_and_default_version(self):
        urgency, suite = str(uuid4()), str(uuid4())
        self.connection.execute("INSERT INTO referentiel.urgences(id,libelle,valid) VALUES (%s,'Urgent',true)", (urgency,))
        self.connection.execute("INSERT INTO referentiel.suites_apporter(id,designation,libelle,valid) VALUES (%s,'Travaux','Travaux',true)", (suite,))
        payload = self.payload() | {'urgence_id': urgency, 'suite_apporter_id': suite}
        created = self.write(payload, str(uuid4()))
        self.assertEqual((created['urgence_libelle'], created['suite_apporter_libelle']), ('Urgent', 'Travaux'))
        self.assertEqual(created['version'], 1)
        with fixtures.http_client(self.app) as client:
            response = client.get('/api/referentiels/urgences')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(urgency, [row['id'] for row in response.json()['references']])
        # Aucun lien n'est inventé et le statut du parent n'est pas une nouvelle ACL.
        self.connection.execute('UPDATE desordres SET valid=false WHERE id=%s', (self.parents['desordres'],))
        self.assertEqual(self.write(self.payload(), str(uuid4()))['version'], 1)

    def test_concurrent_identical_retry_only_one_write(self):
        payload, operation, barrier = self.payload(), str(uuid4()), Barrier(2)
        def writer(_):
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                barrier.wait(timeout=5)
                return self.write(payload, operation, connection)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(writer, range(2)))
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(self.counts(payload['id']), (1, 1, 1))

    def test_creation_updates_projection_for_disorder_and_trunk(self):
        disorder = self.write(self.payload(), str(uuid4()))
        segment = self.write(self.payload('troncons'), str(uuid4()))
        snapshot = read_scope(self.connection, [self.parents['troncons']])
        observations = {row['id']: row for row in snapshot['records'] if row['resource'] == 'observations'}
        self.assertEqual(observations[disorder['id']]['payload']['version'], 1)
        self.assertEqual(observations[disorder['id']]['payload']['parent'], disorder['parent'])
        self.assertNotIn('photos', observations[disorder['id']]['payload'])
        self.assertEqual(observations[segment['id']]['payload']['parent'], segment['parent'])
        self.assertEqual(observations[segment['id']]['serverVersion'], 1)
        self.assertNotIn('photos', observations[segment['id']]['payload'])

    def test_parent_deleted_after_scope_preparation_refuses_creation_without_event(self):
        parent = uuid4()
        self.connection.execute("INSERT INTO troncons(id,digue_id,libelle,geometry,valid) "
                                "SELECT %s,digue_id,'Parent temporaire',geometry,true FROM troncons WHERE id=%s",
                                (parent, self.parents['troncons']))
        snapshot = read_scope(self.connection, [parent])
        self.assertTrue(any(r['resource'] == 'troncons' and r['id'] == str(parent) for r in snapshot['records']))
        payload = self.payload('troncons') | {'parent': {'resource': 'troncons', 'id': str(parent)}}
        self.connection.execute("DELETE FROM troncons WHERE id=%s", (parent,))
        with self.assertRaises(HTTPException) as error:
            self.write(payload, str(uuid4()))
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(error.exception.detail['code'], 'observation-parent-not-found')
        self.assertEqual(self.counts(payload['id']), (0, 0, 0))

    def test_concurrent_distinct_operations_same_uuid_one_conflict(self):
        payload, barrier = self.payload(), Barrier(2)
        def writer(_):
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                barrier.wait(timeout=5)
                try:
                    self.write(payload, str(uuid4()), connection)
                    return 201
                except HTTPException as error:
                    return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(writer, range(2)))
        self.assertEqual(sorted(responses), [201, 409])
        self.assertEqual(self.counts(payload['id']), (1, 1, 1))

    def test_rollback_before_journal_and_before_receipt_leaves_nothing(self):
        for target in ('append_event', 'Jsonb'):
            payload, operation = self.payload(), str(uuid4())
            with patch('digues_webapp.resource_mutations.' + target, side_effect=RuntimeError('interruption')):
                with self.assertRaises(RuntimeError):
                    self.write(payload, operation)
            self.assertEqual(self.counts(payload['id']), (0, 0, 0))
            self.assertEqual(self.write(payload, operation)['version'], 1)
            self.assertEqual(self.counts(payload['id']), (1, 1, 1))
