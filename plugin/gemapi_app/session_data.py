"""Per-QGIS-session lookup data, scoped to one PostgreSQL connection and role."""

import hashlib
import os
from time import perf_counter

from qgis.core import QgsDataSourceUri, QgsProject, QgsProviderRegistry

from .perf import ENABLED as PERF_ENABLED, connect, cache_event, profiled
from .record import TARGETS


CACHE_ENABLED = os.environ.get("SIG_PRESTATIONS_CACHE", "1").strip().lower() not in (
    "0", "false", "no")


def _connection_info(project):
    anchors = {"prestations", "prestations_fiches", "prestations_carte"}
    has_anchor = False
    infos = set()
    for layer in project.mapLayers().values():
        if not layer.isValid() or layer.providerType() != "postgres":
            continue
        uri = QgsDataSourceUri(layer.source())
        if uri.schema() != "gemapi":
            if uri.table() in anchors or uri.table() in TARGETS:
                raise ValueError(f"La couche {uri.table()} doit utiliser le schéma gemapi.")
            continue
        if uri.table() in anchors:
            has_anchor = True
        infos.add(uri.connectionInfo(False))
    if len(infos) > 1:
        raise ValueError("Plusieurs connexions PostgreSQL de prestations sont présentes dans le projet.")
    if not has_anchor:
        raise ValueError("Chargez gemapi.prestations ou gemapi.prestations_fiches depuis PostgreSQL.")
    return infos.pop()


def connection_login(project):
    """Return the configured PostgreSQL login from the active prestations connection."""
    return QgsDataSourceUri(_connection_info(project)).username().strip()


def _open(connection_info):
    metadata = QgsProviderRegistry.instance().providerMetadata("postgres")
    if metadata is None:
        raise ValueError("Le fournisseur PostgreSQL de QGIS est indisponible.")
    return connect(metadata, connection_info)


@profiled("manager.connection")
def connection_from_project():
    return _open(_connection_info(QgsProject.instance()))


class SessionData:
    """Keep only read-only UI options; never cache prestation rows or write checks."""

    def __init__(self, project=None):
        self.project = project or QgsProject.instance()
        self._connection = None
        self._source_digest = None
        self._identity = None
        self._values = {}
        self.scope_generation = 0
        self.prestations_generation = 0
        for signal in (self.project.cleared, self.project.readProject,
                       self.project.fileNameChanged):
            signal.connect(self._project_changed)

    def _project_changed(self, *_):
        self.clear("project")

    def connection(self):
        info = _connection_info(self.project)
        digest = hashlib.sha256(info.encode("utf-8")).digest()
        if self._connection is not None and digest == self._source_digest:
            return self._connection
        self.clear("connection")
        connection = _open(info)
        identity = connection.executeSql("SELECT current_user::text, session_user::text")
        if len(identity) != 1 or len(identity[0]) != 2:
            raise ValueError("Identité PostgreSQL indisponible.")
        self._connection = connection
        self._source_digest = digest
        self._identity = tuple(str(value) for value in identity[0])
        return connection

    def get(self, key, loader, avoided_queries=1):
        self.connection()  # Detect a changed project or connection before every hit.
        started = perf_counter() if PERF_ENABLED else None
        if CACHE_ENABLED and key in self._values:
            cache_event("hit", key, started, avoided_queries)
            return self._values[key]
        value = loader()
        if CACHE_ENABLED:
            self._values[key] = value
        cache_event("miss", key, started, 0)
        return value

    def invalidate_prestations(self):
        self.prestations_generation += 1
        self._values.pop("filters.years", None)
        cache_event("invalidate", "prestations")

    def invalidate_family(self, table):
        keys = [f"family.layer.{table}", f"family.options.{table}"]
        if table == "cours_eau":
            keys.extend(("family.hydro_options", "family.hydro_catalogue"))
        for key in keys:
            self._values.pop(key, None)
        cache_event("invalidate", "family")

    def clear(self, reason="manual"):
        self._values.clear()
        self._connection = None
        self._source_digest = None
        self._identity = None
        self.scope_generation += 1
        self.prestations_generation += 1
        cache_event("invalidate", reason)

    def close(self):
        for signal in (self.project.cleared, self.project.readProject,
                       self.project.fileNameChanged):
            signal.disconnect(self._project_changed)
        self.clear("unload")
