"""Conservative, temporary display groups for the cours_eau selector.

Only exact, unambiguous endpoint connections join distinct UUIDs. Names are
kept verbatim for grouping; folded names are used solely for search and sort.
The input geometries and database rows are never changed.
"""

from collections import defaultdict
from dataclasses import dataclass
import unicodedata

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle, QgsSpatialIndex



def search_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(char for char in text if not unicodedata.combining(char))
                    .casefold().split())


def _field(feature, name):
    value = feature[name]
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return None
    return str(value)


@dataclass(frozen=True)
class WatercourseOption:
    ids: tuple[str, ...]
    fids: tuple[int, ...]
    label: str
    search: str


def build_options(features):
    """Build UI groups in O(n + indexed junction checks), once per load.

    Each MultiLineString remains one graph vertex even if its own parts are
    disjoint. This is a display proposal, not a hydrographic identity rule.
    """
    rows = []
    endpoints = defaultdict(list)
    index = QgsSpatialIndex()
    for feature in features:
        uid = _field(feature, "id")
        if not uid:
            raise ValueError("Cours d'eau sans UUID dans la couche QGIS.")
        geom = QgsGeometry(feature.geometry())
        fid = feature.id()
        name = _field(feature, "nom")
        if not name or not name.strip():
            raise ValueError("Nom de cours d’eau obligatoire : mettre à jour les données avant utilisation.")
        kind = _field(feature, "type_ecoulement")
        row = (uid, fid, name, kind, geom)
        rows.append(row)
        if geom.isNull() or geom.isEmpty():
            continue
        index.addFeature(feature)
        parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
        for part in parts:
            if len(part) >= 2:
                for point in (part[0], part[-1]):
                    endpoints[(point.x(), point.y())].append(fid)

    by_fid = {row[1]: row for row in rows}
    parent = {row[0]: row[0] for row in rows}

    def root(uid):
        while parent[uid] != uid:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    def join(a, b):
        a, b = root(a), root(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    pair_junctions = defaultdict(int)
    for incident_fids in endpoints.values():
        if len(incident_fids) == 2 and incident_fids[0] != incident_fids[1]:
            pair_junctions[tuple(sorted(incident_fids))] += 1

    for (x, y), incident_fids in endpoints.items():
        # A branching endpoint, duplicate part or ring is ambiguous.
        if len(incident_fids) != 2 or incident_fids[0] == incident_fids[1]:
            continue
        if pair_junctions[tuple(sorted(incident_fids))] != 1:
            # Shared ends on both sides can represent a geometric duplicate.
            continue
        first, second = (by_fid[fid] for fid in incident_fids)
        if first[2] != second[2]:
            continue
        point = QgsGeometry.fromPointXY(QgsPointXY(x, y))
        nearby = index.intersects(QgsRectangle(x, y, x, y))
        touching = {by_fid[fid][0] for fid in nearby
                    if by_fid[fid][4].intersects(point)}
        if touching != {first[0], second[0]}:
            continue
        join(first[0], second[0])

    components = defaultdict(list)
    for row in rows:
        key = (row[2], root(row[0]))
        components[key].append(row)

    same_name = defaultdict(list)
    for (name, _), members in components.items():
        if name:
            same_name[name].append(members)
    sector = {}
    for name, groups in same_name.items():
        groups.sort(key=lambda group: (
            min(row[4].boundingBox().xMinimum() for row in group),
            min(row[4].boundingBox().yMinimum() for row in group),
            min(row[0] for row in group)))
        if len(groups) > 1:
            for number, group in enumerate(groups, 1):
                sector[tuple(sorted(row[0] for row in group))] = (number, len(groups))

    options = []
    for (name, _), members in components.items():
        members.sort(key=lambda row: row[0])
        ids = tuple(row[0] for row in members)
        fids = tuple(row[1] for row in members)
        label = name.strip()
        if len(ids) > 1:
            label += f" ({len(ids)} objets)"
        if ids in sector:
            number, total = sector[ids]
            label += f" — secteur {number}/{total}"
        options.append(WatercourseOption(ids, fids, label,
                                         " ".join(search_key(term) for term in (name, label))))
    options.sort(key=lambda option: (search_key(option.label), option.ids))
    return options
