from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import TestCase
from unittest.mock import MagicMock, patch

from psycopg.pq import TransactionStatus
from digues_webapp import sessions


class RegistryTest(TestCase):
    def setUp(self):
        self.now = 10
        self.store = sessions.Sessions(lambda: self.now)
        self.open = self.enterContext(patch.object(sessions.database, 'open_individual', side_effect=self.connect))
        self.enterContext(patch.object(sessions.database, 'check_admission'))
        self.addCleanup(self.store.clean, True)

    @staticmethod
    def connect(*_):
        conn = MagicMock()
        conn.closed = False
        conn.info.transaction_status = TransactionStatus.IDLE
        return conn, 'test'

    def login(self, name='alice', old=None):
        return self.store.login(name, 'unused', old)

    def test_rotation_and_isolation(self):
        a, old = self.login()
        b, other = self.login('bob')
        new, identity = self.login(old=a)
        self.assertNotIn(new, str(identity))
        self.assertNotEqual(old, identity)
        with self.assertRaises(sessions.NoSession), self.store.transaction(a):
            self.fail()
        with self.assertRaises(sessions.WrongContext), self.store.transaction(b, expected=identity['context']):
            self.fail()
        with self.store.transaction(b, expected=other['context']) as session:
            self.assertEqual(session.login, 'bob')

    def test_absolute_even_with_activity(self):
        self.store.absolute, self.store.idle = 10, 8
        token, _ = self.login()
        self.now += 6
        with self.store.transaction(token, activity=True):
            pass
        self.now += 4
        with self.assertRaises(sessions.NoSession), self.store.transaction(token):
            self.fail()

    def test_idle_not_extended_by_me(self):
        self.store.idle = 5
        token, _ = self.login()
        self.now += 4
        with self.store.transaction(token) as session:
            conn = session.connection
        self.now += 1
        self.store.clean()
        conn.close.assert_called_once()
        self.assertFalse(self.store.active)

    def test_capacity_and_reservations(self):
        self.store.limit, self.store.per_login = 2, 1
        with self.store.reservation('alice'):
            with self.assertRaises(sessions.Capacity):
                self.login()
            b, _ = self.login('bob')
            with self.assertRaises(sessions.Capacity):
                self.login('carol')
        self.assertEqual(self.open.call_count, 1)
        self.store.logout(b)
        self.login()

    def test_simultaneous_handshake(self):
        self.store.handshakes = 1
        started, finish = Event(), Event()
        def handshake(*args):
            started.set()
            if not finish.wait(3):
                raise AssertionError('handshake timeout')
            return self.connect()
        self.open.side_effect = handshake
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(self.login)
            self.assertTrue(started.wait(2))
            try:
                with self.assertRaises(sessions.Capacity):
                    self.login('bob')
            finally:
                finish.set()
            pending.result()

    def test_demo_limit_is_separate(self):
        self.store.demo_limit = 1
        self.store.login('public', 'unused', None, demo=True)
        with self.assertRaises(sessions.Capacity):
            self.store.login('public', 'unused', None, demo=True)
        self.login()
        self.login()

    def test_attempts_are_bounded(self):
        self.store.per_minute = 1
        self.store.attempt('ip', 'a')
        for pair in [('ip', 'b'), ('other', 'a')]:
            with self.assertRaises(sessions.RateLimited):
                self.store.attempt(*pair)
        self.now += 60
        self.store.attempt('ip', 'a')

    def test_non_idle_connection_discarded(self):
        token, _ = self.login()
        with self.store.transaction(token) as session:
            conn = session.connection
        conn.info.transaction_status = TransactionStatus.INERROR
        with self.assertRaises(sessions.database.DatabaseUnavailable), self.store.transaction(token):
            self.fail()
        self.assertFalse(self.store.active)
