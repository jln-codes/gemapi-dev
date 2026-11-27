"""Same guarded disposable PostgreSQL cluster as the business routing suite."""
import unittest
from uuid import uuid4
from unittest.mock import patch

from webapp.tests import test_business_postgres as business
from digues_webapp import database


class BrowserIdentityPostgresTest(business.BusinessPostgresTest):
    def test_discovery_lists_only_explicit_memberships_and_no_connection_secrets(self):
        expected = {'reader_alpha': [0], 'editor_alpha': [0, 1], 'admin_alpha': [0, 1],
                    'admin_beta': [2], 'external_reader': [1, 2], 'superadmin': []}
        instance = None
        for login, indexes in expected.items():
            client = self.client(login)
            with patch.object(database, '_connection') as opened:
                response = client.get('/api/auth/databases')
                self.assertEqual(response.status_code, 200, response.text)
                opened.assert_not_called()
            value = response.json()
            self.assertEqual(set(value), {'instance_id', 'user_id', 'expires_at', 'databases'})
            self.assertEqual({b['business_database_id'] for b in value['databases']}, {str(self.bases[i]) for i in indexes})
            if instance is not None:
                self.assertEqual(instance, value['instance_id'])
            instance = value['instance_id']
            for row in value['databases']:
                self.assertEqual(set(row), {'business_database_id', 'structure_id', 'structure_name', 'display_name', 'slug', 'status', 'data_epoch', 'role_code', 'permissions'})
                if login in ('admin_alpha', 'admin_beta'):
                    self.assertEqual(row['role_code'], 'database_admin')
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_catalogue_supports_exact_authorized_slug_resolution_to_uuid(self):
        catalogue = self.client('external_reader').get('/api/auth/databases').json()
        by_slug = {row['slug']: row['business_database_id'] for row in catalogue['databases']}
        self.assertEqual(len(by_slug), len(catalogue['databases']))
        self.assertEqual(by_slug['base_1'], str(self.bases[1]))
        self.assertEqual(by_slug['base_2'], str(self.bases[2]))
        self.assertNotIn('base_0', by_slug)  # Base existante, mais étrangère à cette session.
        self.assertNotIn('BASE_1', by_slug)  # La résolution future est exacte, sans normalisation implicite.

        reader_catalogue = self.client('reader_alpha').get('/api/auth/databases').json()
        reader_by_slug = {
            row['slug']: row['business_database_id'] for row in reader_catalogue['databases']
        }
        self.assertEqual(reader_by_slug, {'base_0': str(self.bases[0])})

    def test_previous_user_or_epoch_cannot_replay_under_new_session(self):
        client = self.client('editor_alpha')
        catalog = client.get('/api/auth/databases').json()
        base = next(b for b in catalog['databases'] if b['business_database_id'] == str(self.bases[0]))
        query = {'context_user_id': catalog['user_id'], 'context_instance_id': catalog['instance_id'], 'context_data_epoch': base['data_epoch']}
        with patch.object(database, '_connection') as opened:
            for field in query:
                response = client.get(self.url(0, f'systemes-endiguement/{self.object_id}'), params={**query, field: str(uuid4())})
                self.assertEqual(response.status_code, 409, response.text)
            other = self.client('admin_alpha')  # Both users may access Alpha, still never exchange intentions.
            self.assertEqual(other.get(self.url(0, f'systemes-endiguement/{self.object_id}'), params=query).status_code, 409)
            opened.assert_not_called()
        response = client.get(self.url(0, f'systemes-endiguement/{self.object_id}'), params=query)
        self.assertEqual(response.status_code, 200, response.text)
