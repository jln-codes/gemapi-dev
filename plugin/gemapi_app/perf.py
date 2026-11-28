"""Opt-in, local QGIS timing trace; no SQL text or business values are logged."""

import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter


ENABLED = os.environ.get("SIG_PRESTATIONS_PERF", "").strip().lower() in ("1", "true", "yes")
_current = ContextVar("gemapi_app_perf", default=None)


def _log(message):
    if os.environ.get("SIG_PRESTATIONS_PERF_WORKER") == "1":
        import json
        print(json.dumps({"perf": message}, ensure_ascii=False), flush=True)
        return
    from qgis.core import QgsMessageLog, Qgis
    QgsMessageLog.logMessage(message, "SIG-PERF", Qgis.Info)


@contextmanager
def measure(name):
    if not ENABLED:
        yield
        return
    parent = _current.get()
    span = {"name": name, "queries": 0, "sql_ms": 0.0, "connections": 0}
    token = _current.set(span)
    start = perf_counter()
    try:
        yield
    finally:
        elapsed = (perf_counter() - start) * 1000
        _current.reset(token)
        if parent is not None:
            for key in ("queries", "sql_ms", "connections"):
                parent[key] += span[key]
        _log(f"{name} total_ms={elapsed:.2f} sql_client_ms={span['sql_ms']:.2f} "
             f"queries={span['queries']} connections={span['connections']}")


def profiled(name):
    def decorate(function):
        if not ENABLED:
            return function
        @wraps(function)
        def wrapped(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


class TimedConnection:
    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def executeSql(self, sql):
        start = perf_counter()
        try:
            rows = self._connection.executeSql(sql)
        except Exception:
            _query_result(sql, start, None, failed=True)
            raise
        _query_result(sql, start, len(rows))
        return rows


def _query_result(sql, start, rows, failed=False):
    elapsed = (perf_counter() - start) * 1000
    span = _current.get()
    if span is not None:
        span["queries"] += 1
        span["sql_ms"] += elapsed
    # A fingerprint groups identical statements without exposing SQL or parameters.
    fingerprint = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12]
    operation = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else "EMPTY"
    _log(f"sql operation={operation} fingerprint={fingerprint} "
         f"client_ms={elapsed:.2f} rows={rows if rows is not None else '-'} "
         f"failed={int(failed)}")


def cache_event(action, key, started=None, avoided_queries=0):
    if ENABLED:
        elapsed = (perf_counter() - started) * 1000 if started is not None else 0.0
        _log(f"cache action={action} key={key} total_ms={elapsed:.2f} "
             f"avoided_queries={avoided_queries}")


def connect(metadata, connection_info):
    if not ENABLED:
        return metadata.createConnection(connection_info, {})
    start = perf_counter()
    connection = None
    try:
        connection = metadata.createConnection(connection_info, {})
    finally:
        elapsed = (perf_counter() - start) * 1000
        span = _current.get()
        if span is not None:
            span["connections"] += int(connection is not None)
        _log(f"connection open_ms={elapsed:.2f} success={int(connection is not None)}")
    return TimedConnection(connection)
