"""Volatile ownership of physical PostgreSQL connections in one HTTP worker."""
from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
import os
import secrets
import threading
import time
from uuid import uuid4

import psycopg
from psycopg.pq import TransactionStatus

from . import session_database as database


class Capacity(Exception):
    pass


class RateLimited(Exception):
    pass


class NoSession(Exception):
    pass


class WrongContext(Exception):
    pass


def setting(name, default, minimum=1):
    value = int(os.getenv("AUTH_" + name, default))
    if value < minimum:
        raise ValueError(f"Configuration AUTH_{name} invalide")
    return value


@dataclass
class Session:
    login: str
    connection: object = field(repr=False)
    schema: str
    created: float
    touched: float
    context: str = field(default_factory=lambda: str(uuid4()))
    lock: object = field(default_factory=threading.Lock)
    revoked: bool = False

    def public(self):
        return {"login": self.login, "context": self.context}


def digest(token):
    return sha256(token.encode()).digest() if token and len(token) == 43 else None


class Sessions:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.limit = setting("MAX_SESSIONS", 20)
        reserve = setting("TECHNICAL_RESERVE", 1, 0)
        self.limit = min(self.limit, setting("CONNECTION_BUDGET", self.limit + reserve) - reserve)
        if self.limit < 1:
            raise ValueError("Budget PostgreSQL insuffisant")
        self.per_login = setting("PER_ACCOUNT", 3)
        self.demo_limit = setting("DEMO_PER_ACCOUNT", self.per_login)
        self.handshakes = setting("MAX_HANDSHAKES", 3)
        self.idle = setting("IDLE_SECONDS", 7200)
        self.absolute = setting("ABSOLUTE_SECONDS", 43200)
        self.sql_idle_seconds = min(self.idle, self.absolute)
        self.per_minute = setting("ATTEMPTS_PER_MINUTE", 10)
        self.global_rate = setting("GLOBAL_ATTEMPTS_PER_MINUTE", 60)
        self.lock = threading.Lock()
        self.active = {}
        self.pending = Counter()
        self.attempts = deque()
        self.stopped = False

    def attempt(self, address, login):
        now = self.clock()
        with self.lock:
            while self.attempts and self.attempts[0][0] <= now - 60:
                self.attempts.popleft()
            if (len(self.attempts) >= self.global_rate or
                    sum(ip == address or name == login for _, ip, name in self.attempts) >= self.per_minute):
                raise RateLimited
            self.attempts.append((now, address, login))

    @contextmanager
    def reservation(self, login, demo=False):
        with self.lock:
            pending = self.pending.total()
            occupied = sum(s.login == login for s in self.active.values())
            if (self.stopped or pending >= self.handshakes or len(self.active) + pending >= self.limit
                    or occupied + self.pending[login] >= (self.demo_limit if demo else self.per_login)):
                raise Capacity
            self.pending[login] += 1
        try:
            yield
        finally:
            with self.lock:
                self.pending[login] -= 1
                if not self.pending[login]:
                    del self.pending[login]

    def login(self, login, password, previous, demo=False):
        with self.reservation(login, demo):
            conn, schema = database.open_individual(login, password, self.sql_idle_seconds)
            try:
                self.logout(previous)
                token = secrets.token_urlsafe(32)
                now = self.clock()
                session = Session(login, conn, schema, now, now)
                with self.lock:
                    if self.stopped:
                        raise Capacity
                    self.active[digest(token)] = session
            except BaseException:
                conn.close()
                raise
        return token, session.public()

    def expired(self, session):
        now = self.clock()
        return now - session.created >= self.absolute or now - session.touched >= self.idle

    def _close(self, key, session):
        # Caller has the session lock. Count the slot until the socket is closed.
        session.revoked = True
        session.connection.close()
        with self.lock:
            self.active.pop(key, None)

    def logout(self, token, expected=None):
        key = digest(token)
        with self.lock:
            session = self.active.get(key)
            if not session:
                return
            if expected is not None and expected != session.context:
                raise WrongContext
            session.revoked = True
        with session.lock:
            self._close(key, session)

    @contextmanager
    def transaction(self, token, *, expected=None, activity=False):
        key = digest(token)
        with self.lock:
            session = self.active.get(key)
        if not session:
            raise NoSession
        if not session.lock.acquire(timeout=2):
            raise Capacity
        try:
            if session.revoked or self.expired(session):
                self._close(key, session)
                raise NoSession
            if expected is not None and expected != session.context:
                raise WrongContext
            conn = session.connection
            try:
                if conn.info.transaction_status != TransactionStatus.IDLE:
                    raise database.DatabaseUnavailable
                with conn.transaction():
                    database.check_admission(conn, session.login, session.schema)
                    yield session
                    if session.revoked or self.expired(session):
                        raise NoSession
                if activity:
                    session.touched = self.clock()
            except (database.AccessDenied, NoSession):
                self._close(key, session)
                raise
            except (database.DatabaseUnavailable, psycopg.OperationalError, psycopg.InterfaceError):
                self._close(key, session)
                raise database.DatabaseUnavailable from None
            finally:
                if not conn.closed and conn.info.transaction_status != TransactionStatus.IDLE:
                    self._close(key, session)
                    raise database.DatabaseUnavailable
        finally:
            session.lock.release()

    def clean(self, shutdown=False):
        with self.lock:
            if shutdown:
                self.stopped = True
            entries = list(self.active.items())
        for key, session in entries:
            if shutdown or self.expired(session):
                with session.lock:
                    if shutdown or self.expired(session):
                        self._close(key, session)
