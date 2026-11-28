"""Session cache scope and invalidation, without a running PostgreSQL server."""

import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app import session_data  # noqa: E402


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self):
        for callback in self.callbacks:
            callback()


class Project:
    def __init__(self):
        self.cleared = Signal()
        self.readProject = Signal()
        self.fileNameChanged = Signal()


class Connection:
    def __init__(self, role):
        self.role = role

    def executeSql(self, query):
        assert query == "SELECT current_user::text, session_user::text"
        return [[self.role, self.role]]


def main():
    def layer(schema, table, service="gemapi_demo"):
        item = Mock()
        item.isValid.return_value = True
        item.providerType.return_value = "postgres"
        item.source.return_value = f"""service='{service}' table="{schema}"."{table}" """
        return item

    project_layers = Mock()
    anchor = layer("gemapi", "prestations_fiches")
    project_layers.mapLayers.return_value = {"a": anchor, "b": layer("gemapi", "mobilier")}
    assert "gemapi_demo" in session_data._connection_info(project_layers)
    project_layers.mapLayers.return_value["b"] = layer("autre", "mobilier")
    with TestCase().assertRaisesRegex(ValueError, "schéma gemapi"):
        session_data._connection_info(project_layers)
    project_layers.mapLayers.return_value["b"] = layer("gemapi", "mobilier", "autre")
    with TestCase().assertRaisesRegex(ValueError, "Plusieurs connexions"):
        session_data._connection_info(project_layers)

    source = ["host=one user=alice"]
    role = ["alice"]
    opened = []
    original_info = session_data._connection_info
    original_open = session_data._open
    original_enabled = session_data.CACHE_ENABLED
    session_data._connection_info = lambda _project: source[0]
    session_data._open = lambda info: opened.append(info) or Connection(role[0])
    session_data.CACHE_ENABLED = True
    project = Project()
    cache = session_data.SessionData(project)
    loads = []

    def load():
        loads.append(True)
        return (len(loads),)

    try:
        assert cache.get("reference.providers", load) == (1,)
        assert cache.get("reference.providers", load) == (1,)
        assert len(loads) == len(opened) == 1
        assert cache._identity == ("alice", "alice")
        cache.get("filters.years", load)
        cache.invalidate_prestations()
        assert cache.get("reference.providers", load) == (1,)
        assert cache.get("filters.years", load) == (3,)
        source[0] = "host=two user=bob"
        role[0] = "bob"
        assert cache.get("reference.providers", load) == (4,)
        assert cache._identity == ("bob", "bob")
        assert len(opened) == 2 and cache.scope_generation >= 2
        cache.clear("manual")
        assert cache.get("reference.providers", load) == (5,)
        project.readProject.emit()
        assert cache.get("reference.providers", load) == (6,)
        session_data.CACHE_ENABLED = False
        assert cache.get("reference.providers", load) == (7,)
        assert cache.get("reference.providers", load) == (8,)
        cache.close()
        assert not project.cleared.callbacks
        assert not project.readProject.callbacks
        assert not project.fileNameChanged.callbacks
    finally:
        session_data._connection_info = original_info
        session_data._open = original_open
        session_data.CACHE_ENABLED = original_enabled
    print("Cache session : réutilisation, invalidation, rôles et désactivation OK")


if __name__ == "__main__":
    main()
