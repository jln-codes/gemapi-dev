"""Attribution on a fresh business schema, including technical writes and forgery.

Level B: disposable PostgreSQL/PostGIS; a failed update is rolled back in its
savepoint. This covers the SQL boundary, not a mocked CRUD serializer.
"""

import os
import unittest
from uuid import uuid4

import psycopg

from digues_webapp.resource_mutations import execute_mutation


@unittest.skipUnless(os.getenv('DIGUES_OCC_TEST_DSN'), 'PostGIS jetable requis')
class ContactAttributionPostgresTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ['DIGUES_OCC_TEST_DSN'])
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.first, self.second, self.system = uuid4(), uuid4(), uuid4()
        for identifier in (self.first, self.second):
            self.conn.execute('INSERT INTO public.contacts(id,nom,valid) VALUES (%s,%s,true)',
                              (identifier, str(identifier)))

    def _create(self):
        def write():
            self.conn.execute('INSERT INTO public.systemes(id,libelle,valid) '
                              "VALUES (%s,'Attribution',true)", (self.system,))
            return {'id': str(self.system)}
        return execute_mutation(self.conn, 'systemes', 'create', write)

    def _update(self, label, version):
        def write():
            self.conn.execute('UPDATE public.systemes SET libelle=%s WHERE id=%s',
                              (label, self.system))
            return {'id': str(self.system)}
        return execute_mutation(self.conn, 'systemes', 'update', write,
                                identifier=self.system, if_match=f'"{version}"')

    def _authors(self):
        return self.conn.execute('SELECT created_by,edited_by,version FROM public.systemes '
                                 'WHERE id=%s', (self.system,)).fetchone()

    def test_native_create_edit_and_technical_rewrite(self):
        self.conn.execute("SELECT set_config('digues.actor_checked','on',false)")
        self.conn.execute("SELECT set_config('digues.actor_contact_id',%s,false)", (str(self.first),))
        created = self._create()
        self.assertEqual(self._authors()[:2], (self.first, self.first))
        self.conn.execute("SELECT set_config('digues.actor_contact_id',%s,false)", (str(self.second),))
        edited = self._update('Modifié', created['version'])
        self.assertEqual(self._authors()[:2], (self.first, self.second))
        self.conn.execute("UPDATE public.systemes SET commentaire='recalcul technique' WHERE id=%s",
                          (self.system,))
        self.assertEqual(self._authors()[:2], (self.first, self.second))
        self.assertGreater(self._authors()[2], edited['version'])

    def test_no_association_stays_null_and_created_by_cannot_be_changed(self):
        self.conn.execute("SELECT set_config('digues.actor_checked','on',false)")
        self.conn.execute("SELECT set_config('digues.actor_contact_id','',false)")
        created = self._create()
        self.assertEqual(self._authors()[:2], (None, None))
        self.conn.execute("SELECT set_config('digues.actor_contact_id',%s,false)", (str(self.first),))
        self._update('Modifié', created['version'])
        self.assertEqual(self._authors()[:2], (None, self.first))
        with self.assertRaises(psycopg.errors.CheckViolation), self.conn.transaction():
            self.conn.execute('UPDATE public.systemes SET created_by=%s WHERE id=%s',
                              (self.second, self.system))
        self.assertEqual(self._authors()[:2], (None, self.first))

    def test_import_insert_is_not_attributed_to_initiator(self):
        self.conn.execute('INSERT INTO public.systemes(id,libelle,valid) '
                          "VALUES (%s,'Import',true)", (self.system,))
        self.assertEqual(self._authors()[:2], (None, None))
        self.conn.execute("UPDATE public.systemes SET commentaire='fichier transfere' WHERE id=%s",
                          (self.system,))
        self.assertEqual(self._authors()[:2], (None, None))

    def test_contact_deleted_after_association_check_leaves_null(self):
        self.conn.execute("SELECT set_config('digues.actor_checked','on',false)")
        self.conn.execute("SELECT set_config('digues.actor_contact_id',%s,false)",
                          (str(uuid4()),))
        self._create()
        self.assertEqual(self._authors()[:2], (None, None))


if __name__ == '__main__':
    unittest.main()
