"""B: ordinary public account, real sessions and tenant guards (~30 s).

Reuses the guarded business fixture, with private enrollment files per scenario.
No remote settings, real passwords, new role, or duplicated auth harness.
"""
import os
import io
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

from digues_app.control.auth import authenticate
from digues_app.deployment.public_demo import PublicDemoCredentials, configure_public_demo, promote_public_demo
from digues_app.provisioning.private_files import read_private, write_private
from digues_webapp.app import create_app
from digues_webapp import database, public_demo
from webapp.tests import test_business_postgres as business


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Cluster jetable non configuré')
class PublicDemoPostgresTest(unittest.TestCase):
    cleanup_databases = classmethod(business.BusinessPostgresTest.cleanup_databases.__func__)
    client = business.BusinessPostgresTest.client
    url = business.BusinessPostgresTest.url
    setUpClass = classmethod(business.BusinessPostgresTest.setUpClass.__func__)

    def setUp(self):
        business.BusinessPostgresTest.setUp(self)
        self.root = Path(self.tmp.name) / uuid4().hex
        self.root.mkdir(mode=0o700)
        for name in ('provisioning', 'runtime-config'):
            (self.root / name).mkdir(mode=0o700)
        self.public_file = self.root / 'runtime-config/public-demo.json'
        self.credentials = PublicDemoCredentials('demo_' + uuid4().hex[:8], 'public-fixture-only-123')
        self.actor = self.users['superadmin'].id
        self.enterContext(patch.dict(os.environ, PUBLIC_DEMO_CONFIG_FILE=str(self.public_file)))

    def enroll(self, **kwargs):
        return configure_public_demo(self.actor, self.bases[0], root=self.root,
                                     credentials=self.credentials, **kwargs)

    def test_enrollment_resume_api_permissions_and_fail_closed_configuration(self):
        self.assertIsNone(public_demo.frontend_public_demo())
        result = self.enroll()
        journal = read_private(self.root / 'provisioning/demo-public-install.json')
        self.assertEqual(journal['phase'], 'complete')
        self.assertEqual(self.enroll(), result)
        self.assertEqual(read_private(self.root / 'provisioning/demo-public-install.json'), journal)
        with TestClient(create_app(), base_url='https://testserver') as client:
            config = client.get('/api/config')
            self.assertEqual(config.headers['cache-control'], 'no-store')
            self.assertEqual(config.json()['public_demo'], result)
            response = client.post('/api/auth/login', json={
                'login': self.credentials.login, 'password': self.credentials.password},
                headers={**self.headers, 'X-Digues-Anonymous-Only': '1'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['is_superadmin'])
            self.assertEqual(response.json()['id'], result['user_id'])
            catalogue = client.get('/api/auth/databases').json()
            self.assertEqual([row['business_database_id'] for row in catalogue['databases']], [str(self.bases[0])])
            base = catalogue['databases'][0]
            self.assertEqual(base['role_code'], 'database_admin')
            self.assertTrue({'sql.read', 'ai.read', 'business.read', 'business.create', 'business.update', 'business.delete', 'members.manage', 'exports.read', 'offline.prepare', 'offline.write'} <= set(base['permissions']))
            response = client.get(self.url(0, f'systemes-endiguement/{self.object_id}'))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('Alpha main', response.text)
            for target, method, body in [
                (self.url(2, f'systemes-endiguement/{self.object_id}'), 'get', None),
                ('/api/admin/structures', 'get', None),
            ]:
                with self.subTest(target=target):
                    response = client.request(method, target, headers=self.headers,
                                              **({'json': body} if body else {}))
                    self.assertEqual(response.status_code, 403, response.text)
            response = client.post(self.url(0, 'sql/query'), headers=self.headers,
                                   json={'sql': 'SELECT libelle FROM public.systemes'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('Alpha main', response.text)
            created = client.post(self.url(0, 'systemes-endiguement'),
                                  json={'libelle': 'Objet public jetable'}, headers=self.headers)
            self.assertEqual(created.status_code, 201, created.text)
            identifier = created.json()['id']
            changed = client.put(self.url(0, f'systemes-endiguement/{identifier}'),
                                 json={'libelle': 'Objet public modifié'},
                                 headers={**self.headers, 'If-Match': '"1"'})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()['libelle'], 'Objet public modifié')
            self.assertEqual(client.get(self.url(0, 'memberships')).status_code, 200)
            # No DELETE operation exists yet; the standard permission is preserved.
            self.assertIn('business.delete', base['permissions'])
            user, token = None, None
            with psycopg.connect(self.control_dsn) as control:
                user, token = authenticate(control, self.credentials.login, self.credentials.password)
            from digues_webapp.business_context import resolve_business_context
            context = resolve_business_context(user, self.bases[0], 'ai.read', session_token=token)
            with database.open_ai_connection(context) as connection:
                self.assertEqual(connection.execute('SELECT libelle FROM public.systemes').fetchone()[0], 'Alpha main')

        original = read_private(self.public_file)
        for key, value in [('database_id', str(self.bases[2])), ('data_epoch', str(uuid4())),
                           ('user_id', str(self.actor))]:
            write_private(self.public_file, {**original, key: value})
            self.assertIsNone(public_demo.frontend_public_demo())
        write_private(self.public_file, original)
        with patch.dict(os.environ, PUBLIC_DEMO_CONFIG_FILE=''):
            self.assertIsNone(public_demo.frontend_public_demo())
        for statement, params in [
            ('UPDATE public.users SET is_superadmin=true WHERE id=%s', (UUID(result['user_id']),)),
            ("UPDATE public.users SET status='disabled' WHERE id=%s", (UUID(result['user_id']),)),
            ("UPDATE public.memberships SET role_id=(SELECT id FROM public.roles WHERE code='editor') WHERE user_id=%s", (UUID(result['user_id']),)),
        ]:
            with psycopg.connect(self.control_dsn) as control:
                control.execute(statement, params); control.commit()
                self.assertIsNone(public_demo.frontend_public_demo())
                # Restore only this isolated scenario's mutations.
                control.execute("UPDATE public.users SET is_superadmin=false,status='active' WHERE id=%s", (UUID(result['user_id']),))
                control.execute("UPDATE public.memberships SET role_id=(SELECT id FROM public.roles WHERE code='database_admin') WHERE user_id=%s", (UUID(result['user_id']),))

    def test_foreign_account_bad_password_identity_and_interrupted_publication(self):
        with self.assertRaisesRegex(ValueError, 'adoption refusée'):
            configure_public_demo(self.actor, self.bases[0], root=self.root,
                                  credentials=PublicDemoCredentials('reader_alpha', 'public-fixture-only-123'))
        self.assertFalse((self.root / 'provisioning/demo-public-install.json').exists())
        with self.assertRaisesRegex(ValueError, 'distinct'):
            configure_public_demo(self.actor, self.bases[0], root=self.root,
                                  credentials=PublicDemoCredentials(self.credentials.login, self.password))
        original_write = write_private
        def stop(path, data):
            if path == self.public_file:
                raise RuntimeError('publication interrompue')
            return original_write(path, data)
        with patch('digues_app.deployment.public_demo.write_private', side_effect=stop), \
             self.assertRaisesRegex(RuntimeError, 'publication interrompue'):
            self.enroll()
        pending = read_private(self.root / 'provisioning/demo-public-install.json')
        result = self.enroll()
        self.assertEqual(result['user_id'], pending['user_id'])
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            configure_public_demo(self.actor, self.bases[2], root=self.root, credentials=self.credentials)
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            configure_public_demo(self.actor, self.bases[0], root=self.root,
                                  credentials=PublicDemoCredentials(self.credentials.login, 'other-public-value'))

    def test_existing_installation_cli_rollback_and_membership_boundary(self):
        from digues_app.deployment import bootstrap
        write_private(self.root / 'runtime-config/control.json', {
            'CONTROL_DATABASE_URL': self.control_runtime_dsn, 'CONTROL_DATABASE_NAME': self.control_name})
        # A failure before commit leaves no partial account/membership, only intent.
        with patch('digues_app.deployment.public_demo.directory.change_membership',
                   side_effect=RuntimeError('transaction interrompue')), self.assertRaises(RuntimeError):
            self.enroll()
        aborted = read_private(self.root / 'provisioning/demo-public-install.json')['user_id']
        with psycopg.connect(self.control_dsn) as control:
            self.assertIsNone(control.execute('SELECT id FROM public.users WHERE id=%s', (UUID(aborted),)).fetchone())
        with patch.object(bootstrap, 'ROOT', self.root), \
             patch.object(bootstrap, 'admin_parameters', side_effect=AssertionError('No bootstrap secret')), \
             redirect_stdout(io.StringIO()):
            argv = ['configure-public-demo', '--actor-login', 'superadmin',
                    '--database-id', str(self.bases[0]), '--demo-login', self.credentials.login,
                    '--demo-password', self.credentials.password]
            self.assertEqual(bootstrap.main(argv), 0)
            state = read_private(self.root / 'provisioning/demo-public-install.json')
            self.assertNotEqual(state['user_id'], aborted)
            self.assertEqual(bootstrap.main(argv), 0)
        from digues_app.control import directory
        user_id = UUID(state['user_id'])
        with psycopg.connect(self.control_dsn) as control:
            directory.change_membership(control, self.actor, 'database', self.bases[2],
                                        user_id, role='reader', create=True)
        self.assertIsNone(public_demo.frontend_public_demo())
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            self.enroll()  # No repair or removal of an unexpected membership.
        with psycopg.connect(self.control_dsn) as control:
            control.execute('DELETE FROM public.memberships WHERE user_id=%s AND business_database_id=%s',
                            (user_id, self.bases[2]))
        with patch.dict(os.environ, ADMIN_ORIGIN='https://admin.testserver'), \
             TestClient(create_app(), base_url='https://admin.testserver') as admin:
            response = admin.post('/api/auth/login', json={
                'login': self.credentials.login, 'password': self.credentials.password},
                headers={'Origin': 'https://admin.testserver', 'X-Digues-CSRF': '1'})
            self.assertEqual(response.status_code, 200, response.text)
            response = admin.get('/api/admin/structures')
            self.assertEqual(response.status_code, 403, response.text)

    def legacy_enrollment(self):
        """B: emulate the previously installed membership; never a live target."""
        from digues_app.control import directory
        result = self.enroll()
        with psycopg.connect(self.control_dsn) as control:
            row = control.execute('SELECT updated_at FROM public.memberships WHERE user_id=%s',
                                  (UUID(result['user_id']),)).fetchone()
            directory.change_membership(control, self.actor, 'database', self.bases[0],
                                        UUID(result['user_id']), role='reader', expected_revision=row[0])
        return result

    def enrollment_snapshot(self):
        with psycopg.connect(self.control_dsn) as control:
            return (
                control.execute('SELECT * FROM public.users ORDER BY id').fetchall(),
                control.execute('SELECT * FROM public.local_credentials ORDER BY user_id').fetchall(),
                control.execute('SELECT * FROM public.memberships ORDER BY user_id,business_database_id').fetchall(),
                control.execute('SELECT * FROM public.admin_audit_log ORDER BY id').fetchall(),
                self.public_file.read_bytes(),
                (self.root / 'provisioning/demo-public-install.json').read_bytes(),
            )

    def test_promotion_directory_revision_audit_rollback_and_idempotent_cli(self):
        # B (~1 min including shared SQL setup): exact persisted identity, real
        # revision/audit boundary, and rollback. Only generated fixture is mutable.
        from digues_app.control import directory
        from digues_app.deployment import bootstrap
        result = self.legacy_enrollment()
        before = self.enrollment_snapshot()
        self.assertIsNone(public_demo.frontend_public_demo())  # reader is no longer public.
        change = directory.change_membership
        def fail_after_change(*args, **kwargs):
            change(*args, **kwargs)
            raise RuntimeError('rollback after audit')
        with patch('digues_app.deployment.public_demo.directory.change_membership', side_effect=fail_after_change), \
             self.assertRaisesRegex(RuntimeError, 'rollback after audit'):
            promote_public_demo(self.actor, self.bases[0], root=self.root)
        self.assertEqual(self.enrollment_snapshot(), before)
        write_private(self.root / 'runtime-config/control.json', {
            'CONTROL_DATABASE_URL': self.control_runtime_dsn, 'CONTROL_DATABASE_NAME': self.control_name})
        with patch.object(bootstrap, 'ROOT', self.root), \
             patch.object(bootstrap, 'admin_parameters', side_effect=AssertionError('No bootstrap secret')), \
             redirect_stdout(io.StringIO()):
            argv = ['promote-public-demo', '--actor-login', 'superadmin', '--database-id', str(self.bases[0])]
            self.assertEqual(bootstrap.main(argv), 0)
            after = self.enrollment_snapshot()
            self.assertEqual(bootstrap.main(argv), 0)
            self.assertEqual(self.enrollment_snapshot(), after)
        self.assertEqual(before[:2], after[:2])  # all users/passwords, including private admin
        self.assertEqual(before[4:], after[4:])  # published credentials/journal byte-identical
        with psycopg.connect(self.control_dsn) as control:
            self.assertEqual(control.execute('SELECT r.code FROM public.memberships m JOIN public.roles r '
                'ON r.id=m.role_id WHERE m.user_id=%s', (UUID(result['user_id']),)).fetchall(), [('database_admin',)])
            self.assertEqual(control.execute("SELECT count(*) FROM public.admin_audit_log WHERE target_id=%s "
                "AND action='database_membership.update' AND metadata->>'role'='database_admin'",
                (UUID(result['user_id']),)).fetchone(), (1,))
            unchanged = [row for row in after[2] if row[0] != UUID(result['user_id'])]
            self.assertEqual(unchanged, [row for row in before[2] if row[0] != UUID(result['user_id'])])
        self.assertEqual(self.enroll(), result)
        self.assertEqual(public_demo.frontend_public_demo(), result)

    def test_promotion_refuses_incompatible_identity_journal_roles_and_memberships(self):
        # Negative cases share only this scenario's owned enrollment, restored
        # explicitly after each mutation. Refusals must leave SQL/files untouched.
        result = self.legacy_enrollment()
        user_id = UUID(result['user_id'])
        original = read_private(self.public_file)
        journal_path = self.root / 'provisioning/demo-public-install.json'
        journal = read_private(journal_path)
        for key, value in [('user_id', str(self.actor)), ('data_epoch', str(uuid4())),
                           ('structure_id', str(uuid4())), ('database_id', str(self.bases[2])),
                           ('instance_id', str(uuid4())), ('login', 'unowned'), ('password', 'other-public-password')]:
            with self.subTest(config=key):
                write_private(self.public_file, {**original, key: value})
                before = self.enrollment_snapshot()
                with self.assertRaises(ValueError):
                    promote_public_demo(self.actor, self.bases[0], root=self.root)
                self.assertEqual(before, self.enrollment_snapshot())
                write_private(self.public_file, original)
        for key, value in [('user_id', str(self.actor)), ('phase', 'intent'),
                           ('actor_id', str(self.users['admin_alpha'].id)), ('password_sha256', 'invalid')]:
            with self.subTest(journal=key):
                write_private(journal_path, {**journal, key: value})
                with self.assertRaises(ValueError):
                    promote_public_demo(self.actor, self.bases[0], root=self.root)
                write_private(journal_path, journal)
        mutations = [
            ('UPDATE public.users SET is_superadmin=true WHERE id=%s', 'UPDATE public.users SET is_superadmin=false WHERE id=%s'),
            ("UPDATE public.users SET status='disabled' WHERE id=%s", "UPDATE public.users SET status='active' WHERE id=%s"),
            ("UPDATE public.memberships SET status='disabled' WHERE user_id=%s", "UPDATE public.memberships SET status='active' WHERE user_id=%s"),
            ("UPDATE public.memberships SET role_id=(SELECT id FROM public.roles WHERE code='editor') WHERE user_id=%s", "UPDATE public.memberships SET role_id=(SELECT id FROM public.roles WHERE code='reader') WHERE user_id=%s"),
        ]
        for change, restore in mutations:
            with self.subTest(change=change), psycopg.connect(self.control_dsn) as control:
                control.execute(change, (user_id,)); control.commit()
                before = self.enrollment_snapshot()
                with self.assertRaises(ValueError):
                    promote_public_demo(self.actor, self.bases[0], root=self.root)
                self.assertEqual(before, self.enrollment_snapshot())
                control.execute(restore, (user_id,))
        from digues_app.control import directory
        with psycopg.connect(self.control_dsn) as control:
            directory.change_membership(control, self.actor, 'database', self.bases[2], user_id, role='reader', create=True)
        before = self.enrollment_snapshot()
        with self.assertRaises(ValueError):
            promote_public_demo(self.actor, self.bases[0], root=self.root)
        self.assertEqual(before, self.enrollment_snapshot())
        with self.assertRaises(directory.DirectoryError):
            promote_public_demo(self.users['reader_alpha'].id, self.bases[0], root=self.root)
