"""Conservative longitudinal candidates for watercourses, levees and AH sections.

The service has no widget dependency. It never changes a stored association.
Distances and sampling are in metres in EPSG:3950. A watercourse is hidden
only when every covered piece is provably nearer a levee section than any
unprotected section. Inconclusive cases keep the watercourse visible.
"""

from dataclasses import dataclass
from math import ceil, hypot, isfinite
from uuid import UUID

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle, QgsWkbTypes

from .record import SITE_CHILDREN, TARGETS, LINKS, literal


@dataclass(frozen=True)
class ProjectionSettings:
    sample_step_m: float = 25.0
    boundary_tolerance_m: float = 1.0
    max_samples: int = 5000
    refinement_levels: int = 3

    def __post_init__(self):
        if (self.sample_step_m <= 0 or self.boundary_tolerance_m < 0 or
                self.max_samples < 1 or self.refinement_levels < 0):
            raise ValueError("Paramètres de projection invalides.")


def candidate_query(wkt, radius_m, watercourse_id=None):
    """Prefilter selectable modes, then read complete intervals of nearby courses.

    A distance hit identifies a course, never a priority. All admissible
    intervals of that course are needed to construct its uncovered complement.
    No project layer, displayed name or provider subset restricts this query.
    """
    radius_m = float(radius_m)
    if not isfinite(radius_m) or radius_m < 0:
        raise ValueError("Rayon de recherche invalide.")
    target = f"ST_GeomFromText({literal(str(wkt))},3950)"
    branches = [
        ("cours_eau", "gemapi.cours_eau o", "o.geom",
         "",
         "ST_AsText(o.geom)", "NULL::text", "NULL::float8", "NULL::float8", "NULL::boolean"),
        ("digues", "gemapi.digues_reperes o JOIN gemapi.cours_eau r ON r.id=o.riviere_id",
         "r.geom", "AND gemapi.cours_eau_reference_admissible(r.geom,r.direction,r.valide) "
         "AND md5(encode(ST_AsEWKB(r.geom),'hex'))=o.geom_riviere_signature "
         "AND ST_Equals(o.geom_reference,ST_LineSubstring(ST_GeometryN(r.geom,1),o.debut_fraction,o.fin_fraction))",
         "ST_AsText(o.geom_reference)", "o.riviere_id::text",
         "o.debut_fraction", "o.fin_fraction",
         "md5(encode(ST_AsEWKB(r.geom),'hex'))=o.geom_riviere_signature"),
        ("ah_sections", "gemapi.ah_sections_carte o JOIN gemapi.cours_eau r ON r.id=o.cours_eau_id",
         "r.geom", "AND o.etat_reference='CONFORME' AND o.cours_eau_qualifie IS TRUE "
         "AND o.signature_conforme IS TRUE "
         "AND ST_Equals(o.geom_reference,ST_LineSubstring(ST_GeometryN(r.geom,1),o.debut_fraction,o.fin_fraction))",
         "ST_AsText(o.geom_reference)", "o.cours_eau_id::text", "o.debut_fraction", "o.fin_fraction",
         "o.signature_conforme"),
        ("ah_stockage", "gemapi.ah_stockage o", "o.geom", "", "NULL::text",
         "NULL::text", "NULL::float8", "NULL::float8", "NULL::boolean"),
        ("ah_ouvrage", "gemapi.ah_ouvrage o", "o.geom", "", "NULL::text",
         "NULL::text", "NULL::float8", "NULL::float8", "NULL::boolean"),
        *((key, f"gemapi.{key} o", f"o.{TARGETS[key].geometry}",
           "AND o.site_terrestre_id IS NOT NULL", "NULL::text", "NULL::text", "NULL::float8",
           "NULL::float8", "NULL::boolean") for key in SITE_CHILDREN),
        ("sites_terrestres", "gemapi.sites_terrestres o", "o.geom", "", "NULL::text",
         "NULL::text", "NULL::float8", "NULL::float8", "NULL::boolean"),
    ]
    sql = []
    for family, source, geom, extra, geom_text, river_id, start, end, signature in branches:
        if watercourse_id is not None:
            if family not in ("cours_eau", "digues", "ah_sections"):
                continue
            river_column = "o.id" if family == "cours_eau" else "r.id"
            extra += f" AND {river_column}={literal(str(UUID(str(watercourse_id))), '::uuid')}"
        object_id = "o.digue_id" if family == "digues" else "o.id"
        sql.append(
            f"SELECT {literal(family)} AS famille,{object_id}::text AS objet_id,"
            f"{geom_text} AS geom_wkt,{river_id} AS riviere_id,{start} AS debut_fraction,"
            f"{end} AS fin_fraction,{signature} AS signature_ok "
            f"FROM {source} WHERE ST_DWithin({geom},{target},{radius_m:.9f}) {extra}")
    return " UNION ALL ".join(sql)


def resolve_candidates(geometry, rows):
    """Return admissible identities and per-course longitudinal resolutions.

    Missing courses or malformed sections never establish a substitution.
    Distinct UUIDs are retained even when their parent or geometry is identical.
    """
    allowed = {family: set() for family in LINKS}
    rivers, priorities = {}, {}
    for family, uid, wkt, river_id, start, end, signature in rows:
        family, uid = str(family), str(uid)
        if family == "cours_eau":
            rivers[uid] = str(wkt)
        elif family in ("digues", "ah_sections"):
            if signature is True or str(signature).lower() in ("1", "t", "true"):
                priorities.setdefault(str(river_id), []).append(
                    ((family, uid), float(start), float(end), True))
        else:
            allowed[family].add(uid)
    resolutions = {}
    for uid, wkt in rivers.items():
        visible, identities, ambiguous = resolve_watercourse(geometry, wkt, priorities.get(uid, ()))
        if visible:
            allowed["cours_eau"].add(uid)
        for family, identity in identities:
            allowed[family].add(identity)
        resolutions[uid] = visible, identities, ambiguous
    return allowed, resolutions


def _line_slice(line, start, end):
    length = line.length()
    begin, finish = start * length, end * length
    points = [line.interpolate(begin).asPoint()]
    travelled = 0.0
    vertices = line.asPolyline()
    for first, second in zip(vertices, vertices[1:]):
        travelled += hypot(second.x() - first.x(), second.y() - first.y())
        if begin < travelled < finish:
            points.append(QgsPointXY(second))
    points.append(line.interpolate(finish).asPoint())
    return QgsGeometry.fromPolylineXY(points)


def projected_fraction(line, point):
    """Fraction of the closest reference-line point, or None if undefined."""
    length = line.length()
    distance = line.lineLocatePoint(point)
    if length <= 0 or not isfinite(distance) or distance < 0:
        return None
    return min(1.0, max(0.0, distance / length))


def _samples(geometry, settings):
    """Return (sample point, covering radius), or None when proof is too costly."""
    kind = QgsWkbTypes.flatType(geometry.wkbType())
    if kind in (QgsWkbTypes.Point, QgsWkbTypes.MultiPoint):
        points = geometry.asMultiPoint() if kind == QgsWkbTypes.MultiPoint else [geometry.asPoint()]
        if len(points) > settings.max_samples:
            return None
        return [(QgsGeometry.fromPointXY(point), 0.0) for point in points]
    if kind in (QgsWkbTypes.LineString, QgsWkbTypes.MultiLineString):
        parts = geometry.asMultiPolyline() if kind == QgsWkbTypes.MultiLineString else [geometry.asPolyline()]
        result = []
        for part in parts:
            for first, second in zip(part, part[1:]):
                length = hypot(second.x() - first.x(), second.y() - first.y())
                count = max(1, ceil(length / settings.sample_step_m))
                if len(result) + count > settings.max_samples:
                    return None
                for index in range(count):
                    fraction = (index + 0.5) / count
                    point = QgsPointXY(first.x() + fraction * (second.x() - first.x()),
                                       first.y() + fraction * (second.y() - first.y()))
                    result.append((QgsGeometry.fromPointXY(point), length / count / 2))
        return result
    if kind in (QgsWkbTypes.Polygon, QgsWkbTypes.MultiPolygon):
        bounds = geometry.boundingBox()
        width, height = bounds.width(), bounds.height()
        columns = max(1, ceil(width / settings.sample_step_m))
        rows = max(1, ceil(height / settings.sample_step_m))
        if columns * rows > settings.max_samples:
            return None
        dx, dy = width / columns, height / rows
        radius = hypot(dx, dy) / 2
        result = []
        for column in range(columns):
            for row in range(rows):
                left = bounds.xMinimum() + column * dx
                bottom = bounds.yMinimum() + row * dy
                cell = QgsGeometry.fromRect(QgsRectangle(left, bottom, left + dx, bottom + dy))
                if geometry.intersects(cell):
                    result.append((QgsGeometry.fromPointXY(QgsPointXY(left + dx / 2, bottom + dy / 2)),
                                   radius))
        return result
    return None


def _sections(river_wkt, levees):
    """Build priority sections and the uncovered complement of a reference line."""
    river = QgsGeometry.fromWkt(river_wkt)
    if (river.isNull() or not river.isGeosValid() or
            QgsWkbTypes.flatType(river.wkbType()) != QgsWkbTypes.MultiLineString or
            len(river.asMultiPolyline()) != 1 or not all(row[3] for row in levees)):
        return None
    line = QgsGeometry.fromPolylineXY(river.asMultiPolyline()[0])
    if line.length() <= 0:
        return None
    intervals = []
    for uid, start, end, _ in levees:
        if not (0 <= start < end <= 1):
            return None
        intervals.append((start, end, uid, _line_slice(line, start, end)))
    covered = []
    for start, end, _, _ in sorted(intervals, key=lambda row: (row[0], row[1])):
        if covered and start <= covered[-1][1]:
            covered[-1] = (covered[-1][0], max(covered[-1][1], end))
        else:
            covered.append((start, end))
    gaps = []
    current = 0.0
    for start, end in covered:
        if start > current:
            gaps.append(_line_slice(line, current, start))
        current = max(current, end)
    if current < 1:
        gaps.append(_line_slice(line, current, 1))
    return line, intervals, gaps


def resolve_watercourse(geometry, river_wkt, levees, settings=ProjectionSettings()):
    """Return (visible river, levee UUIDs, ambiguous).

    `levees` contains (identity, start_fraction, end_fraction, signature_ok).
    Identity may be a UUID or (family, UUID). Levees and AH sections have
    exactly the same priority; overlapping identities remain ambiguous.
    Every sample covers a known region of the prestation geometry. The
    2*radius margin follows from the 1-Lipschitz property of distance to a
    fixed geometry: it prevents excluding an object between samples.
    """
    levees = tuple(levees)
    ids = {row[0] for row in levees}
    if not levees:
        return True, set(), False
    if geometry.isNull() or geometry.isEmpty() or not geometry.isGeosValid():
        return True, ids, True
    sections = _sections(river_wkt, levees)
    if sections is None:
        return True, ids, True
    line, intervals, gaps = sections
    result = True, ids, True
    for level in range(settings.refinement_levels + 1):
        step = settings.sample_step_m / 2 ** level
        finer = ProjectionSettings(step, settings.boundary_tolerance_m,
                                   settings.max_samples, 0)
        samples = _samples(geometry, finer)
        if samples is None or not samples:
            return result
        visible_river = False
        visible_levees = set()
        ambiguous = False
        for point, radius in samples:
            distances = [("digues", uid, point.distance(section)) for _, _, uid, section in intervals]
            distances.extend(("cours_eau", None, point.distance(gap)) for gap in gaps)
            if any(not isfinite(distance) or distance < 0 for _, _, distance in distances):
                return True, ids, True
            nearest = min(row[2] for row in distances)
            possible = [(family, uid) for family, uid, distance in distances
                        if distance <= nearest + 2 * radius + settings.boundary_tolerance_m]
            if radius == 0:
                fraction = projected_fraction(line, point)
                if fraction is None:
                    return True, ids, True
                # The explicit longitudinal projection is a second, additive
                # check. At a meander the nearest point can be non-unique;
                # the distance comparison above retains every plausible side.
                possible.extend(("digues", uid) for start, end, uid, _ in intervals
                                if start <= fraction <= end)
                if not any(start < fraction < end for start, end, _, _ in intervals):
                    possible.append(("cours_eau", None))
            visible_river |= any(family == "cours_eau" for family, _ in possible)
            visible_levees.update(uid for family, uid in possible if family == "digues")
            ambiguous |= len(set(possible)) > 1
        result = visible_river, visible_levees, ambiguous
        if not (visible_river and visible_levees):
            break
    return result
