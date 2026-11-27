"""Bounded spatial proofs for the master model, never a SQL write path.

Inputs are 2D GeoJSON in an explicit common CRS. Exact line equality compares
point sets with rational arithmetic; it does not simplify or rewrite geometry.
Relative projection is a logical calculation in CRS units, not an authoritative
PR. Bornage plans reuse the Core contract and leave its execution unqualified.
"""

from copy import deepcopy
from fractions import Fraction
from math import hypot, isfinite

from .geometry_contracts import geometry_approved


# This names the existing Core interface. It does not make a logical target UUID
# a row of public.troncons, create a system, or execute a database operation.
CORE_REPERAGE_INTERFACE = {
    "module": "digues_app.target.reperage",
    "projection_function": "public.xy_vers_reperage",
    "projection_inputs": ["p_troncon_id", "p_systeme_reperage_id", "p_point_xy"],
    "reverse_functions": ["public.borne_offset_vers_xy", "public.pr_vers_xy"],
    "execution": "NOT_EXECUTED",
    "missing_integrations": ["TARGET_OWNER_BINDING", "BORNAGE_INITIALIZER",
                             "TRANSACTION_AND_GENERAL_HISTORY"],
}


class SpatialContractError(ValueError):
    def __init__(self, code):
        self.code = "INV-GEO." + code
        super().__init__(self.code)


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SpatialContractError("DIMENSIONS_UNQUALIFIED")
    try:
        if any(type(v) not in (int, float) or not isfinite(v) for v in value):
            raise SpatialContractError("COORDINATES_INVALID")
    except OverflowError:
        raise SpatialContractError("COORDINATES_INVALID") from None
    return tuple(Fraction(v) for v in value)


def _line(geometry):
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise SpatialContractError("LINESTRING_REQUIRED")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        raise SpatialContractError("GEOMETRY_EMPTY")
    points = [_point(value) for value in coordinates]
    if all(point == points[0] for point in points):
        raise SpatialContractError("GEOMETRY_DEGENERATE")
    return points


def _crs(geometry, explicit):
    embedded = geometry.get("crs") if isinstance(geometry, dict) else None
    if explicit is not None and embedded is not None and explicit != embedded:
        raise SpatialContractError("CRS_CONFLICT")
    return explicit if explicit is not None else embedded


def _known_crs(value):
    if type(value) is int:
        return value > 0
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().upper() not in {"UNKNOWN", "UNDEFINED", "INCONNU"}
    if isinstance(value, dict):
        return (value.get("type") == "name" and isinstance(value.get("properties"), dict)
                and isinstance(value["properties"].get("name"), str)
                and _known_crs(value["properties"]["name"]))
    return False


def _same_crs(left, right, left_crs, right_crs):
    left_crs, right_crs = _crs(left, left_crs), _crs(right, right_crs)
    if left_crs is None or right_crs is None or left_crs == "" or right_crs == "":
        raise SpatialContractError("CRS_MISSING")
    if not _known_crs(left_crs) or not _known_crs(right_crs):
        raise SpatialContractError("CRS_UNQUALIFIED")
    if left_crs != right_crs:
        raise SpatialContractError("CRS_MISMATCH")
    return left_crs


def _segments(points):
    return [(start, end) for start, end in zip(points, points[1:]) if start != end]


def _cross(first, last, point):
    return (last[0] - first[0]) * (point[1] - first[1]) - (last[1] - first[1]) * (point[0] - first[0])


def _on_segment(point, first, last):
    return (_cross(first, last, point) == 0 and
            all(min(first[i], last[i]) <= point[i] <= max(first[i], last[i]) for i in (0, 1)))


def _intersection(first, second):
    a, b = first
    c, d = second
    sides = (_cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b))
    if all(side == 0 for side in sides):
        axis = 0 if a[0] != b[0] else 1
        left = max(min(a[axis], b[axis]), min(c[axis], d[axis]))
        right = min(max(a[axis], b[axis]), max(c[axis], d[axis]))
        return "overlap" if left < right else "touch" if left == right else None
    if sides[0] * sides[1] < 0 and sides[2] * sides[3] < 0:
        return "cross"
    if any((_on_segment(c, a, b), _on_segment(d, a, b),
            _on_segment(a, c, d), _on_segment(b, c, d))):
        return "touch"
    return None


def _ring(coordinates):
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 4:
        raise SpatialContractError("POLYGON_RING_INVALID")
    points = [_point(value) for value in coordinates]
    if points[0] != points[-1]:
        raise SpatialContractError("POLYGON_RING_OPEN")
    segments = _segments(points)
    if len(segments) < 3 or sum(a[0] * b[1] - b[0] * a[1] for a, b in segments) == 0:
        raise SpatialContractError("GEOMETRY_DEGENERATE")
    for i, first in enumerate(segments):
        for j in range(i + 1, len(segments)):
            relation = _intersection(first, segments[j])
            adjacent = j == i + 1 or (i == 0 and j == len(segments) - 1)
            if relation and (not adjacent or relation != "touch"):
                raise SpatialContractError("POLYGON_RING_SELF_INTERSECTION")
    return segments


def _inside(point, segments):
    inside = False
    for first, last in segments:
        if _on_segment(point, first, last):
            return 0
        if (first[1] > point[1]) != (last[1] > point[1]):
            crossing = first[0] + (point[1] - first[1]) * (last[0] - first[0]) / (last[1] - first[1])
            if crossing > point[0]:
                inside = not inside
    return 1 if inside else -1


def _polygon(geometry):
    """Prove simple, separated rings exactly; touching rings remain blocked.

    No ring orientation, simplification, repair or coordinate normalization is
    imposed. Point contacts between rings need a fuller topology interpretation
    and are deliberately unqualified, rather than falsely declared invalid.
    """
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        raise SpatialContractError("GEOMETRY_EMPTY")
    rings = [_ring(ring) for ring in coordinates]
    for index, first in enumerate(rings):
        for second in rings[index + 1:]:
            relations = {_intersection(a, b) for a in first for b in second}
            if relations & {"cross", "overlap"}:
                raise SpatialContractError("POLYGON_RINGS_INTERSECTION")
            if "touch" in relations:
                raise SpatialContractError("POLYGON_TOUCH_TOPOLOGY_UNQUALIFIED")
    for hole in rings[1:]:
        if _inside(hole[0][0], rings[0]) != 1:
            raise SpatialContractError("POLYGON_HOLE_OUTSIDE")
    for index, first in enumerate(rings[1:]):
        for second in rings[index + 2:]:
            if _inside(first[0][0], second) == 1 or _inside(second[0][0], first) == 1:
                raise SpatialContractError("POLYGON_HOLES_NESTED")


def qualify_geometry(geometry, contract, *, crs=None, valid=True):
    """Check the approved geometry facet, never the whole object's validity.

    A compatible result proves 2D coordinates, explicit CRS identity, expected
    shape and the bounded topology below. It certifies neither a CRS definition,
    PostGIS storage, imported occurrences nor an object's other obligations.
    The supplied geometry is never rewritten, including incompatible history.
    """
    result = {"status": "UNQUALIFIED", "compatible": None, "shape": None, "crs": None,
              "checks": [], "diagnostics": [],
              "qualification": "GEOMETRY_CONTRACT_ONLY_NOT_IMPORT_CERTIFIED"}
    try:
        if not isinstance(contract, dict) or not geometry_approved(contract):
            raise SpatialContractError("TARGET_CONTRACT_UNQUALIFIED")
        result["checks"].append("APPROVED_TARGET_CONTRACT")
        if valid is not None and type(valid) is not bool:
            raise SpatialContractError("VALIDITY_CLAIM_UNQUALIFIED")
        if geometry is None:
            if contract["required_for_valid"] and valid is True:
                raise SpatialContractError("GEOMETRY_MISSING")
            if contract["required_for_valid"] and valid is None:
                result["checks"].append("SOURCE_VALIDITY_UNQUALIFIED")
                return result
            result.update(status="COMPATIBLE", compatible=True)
            result["checks"].append("GEOMETRY_ABSENCE_ALLOWED_FOR_INVALID" if contract["required_for_valid"]
                                    else "OPTIONAL_GEOMETRY_ABSENT")
            return result
        if not isinstance(geometry, dict) or not isinstance(geometry.get("type"), str):
            raise SpatialContractError("GEOMETRY_ENCODING_UNQUALIFIED")
        result["shape"] = geometry["type"]
        expected = contract["target_shapes"]
        if "GEOMETRY" not in expected and geometry["type"].upper() not in expected:
            raise SpatialContractError("GEOMETRY_UNEXPECTED")
        result["checks"].append("TARGET_SHAPE")
        if geometry["type"] == "Point":
            _point(geometry.get("coordinates"))
        elif geometry["type"] == "LineString":
            _line(geometry)
        elif geometry["type"] == "Polygon":
            _polygon(geometry)
        else:
            raise SpatialContractError("GEOMETRY_TOPOLOGY_UNQUALIFIED")
        result["checks"].extend(["FINITE_2D_COORDINATES", "NONDEGENERATE_GEOMETRY"])
        result["crs"] = deepcopy(_same_crs(geometry, geometry, crs, crs))
        result["checks"].append("EXPLICIT_CRS_IDENTITY")
        if geometry["type"] == "Polygon":
            result["checks"].append("SIMPLE_RINGS_SEPARATED_HOLES")
        result.update(status="COMPATIBLE", compatible=True)
    except SpatialContractError as error:
        code = error.code.removeprefix("INV-GEO.")
        unqualified = "UNQUALIFIED" in code or code.startswith("CRS_")
        result.update(status="UNQUALIFIED" if unqualified else "INCOMPATIBLE",
                      compatible=None if unqualified else False)
        result["diagnostics"].append({"code": error.code, "blocking": True})
    return result


def _covered(segment, others):
    """A segment is covered by the exact union of collinear closed segments."""
    start, end = segment
    dx, dy = end[0] - start[0], end[1] - start[1]
    axis = 0 if dx else 1
    intervals = []
    for first, last in others:
        if any(dx * (p[1] - start[1]) != dy * (p[0] - start[0]) for p in (first, last)):
            continue
        lo, hi = sorted((p[axis] - start[axis]) / (end[axis] - start[axis]) for p in (first, last))
        lo, hi = max(Fraction(0), lo), min(Fraction(1), hi)
        if lo <= hi:
            intervals.append((lo, hi))
    reached = Fraction(0)
    for lo, hi in sorted(intervals):
        if lo > reached:
            return False
        reached = max(reached, hi)
        if reached == 1:
            return True
    return False


def exact_spatial_equal(left, right, *, left_crs=None, right_crs=None):
    """Exact Point/LineString equality; unsupported comparisons fail closed.

    NULL equals NULL only. Reversed lines, subdivision and retracing can describe
    the same set of points; proximity and different CRS never establish equality.
    """
    if left is None or right is None:
        return left is right
    _same_crs(left, right, left_crs, right_crs)
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise SpatialContractError("GEOMETRY_ENCODING_UNQUALIFIED")
    if left.get("type") != right.get("type"):
        return False
    if left.get("type") == "Point":
        return _point(left.get("coordinates")) == _point(right.get("coordinates"))
    if left.get("type") != "LineString":
        raise SpatialContractError("GEOMETRY_EQUALITY_UNQUALIFIED")
    first, second = _segments(_line(left)), _segments(_line(right))
    return all(_covered(s, second) for s in first) and all(_covered(s, first) for s in second)


def _locate(point, line):
    segments = _segments(line)
    try:
        lengths = [hypot(float(b[0] - a[0]), float(b[1] - a[1])) for a, b in segments]
    except OverflowError:
        raise SpatialContractError("PROJECTION_NUMERIC_RANGE_UNQUALIFIED") from None
    length = sum(lengths)
    if not isfinite(length):
        raise SpatialContractError("PROJECTION_NUMERIC_RANGE_UNQUALIFIED")
    if length <= 0:
        raise SpatialContractError("GEOMETRY_DEGENERATE")
    candidates, offset = [], 0.0
    for (start, end), segment_length in zip(segments, lengths):
        dx, dy = end[0] - start[0], end[1] - start[1]
        fraction = max(Fraction(0), min(Fraction(1),
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)))
        projected = (start[0] + fraction * dx, start[1] + fraction * dy)
        distance = sum((point[i] - projected[i]) ** 2 for i in (0, 1))
        candidates.append((distance, projected, offset + float(fraction) * segment_length))
        offset += segment_length
    nearest = min(row[0] for row in candidates)
    solutions = {(point, distance) for squared, point, distance in candidates if squared == nearest}
    if len(solutions) != 1:
        raise SpatialContractError("PROJECTION_AMBIGUOUS")
    projected, abscissa = next(iter(solutions))
    return {"coordinates": [float(v) for v in projected], "fraction": abscissa / length,
            "abscissa": abscissa}


def update_relative_projection(state, owner_geometry, *, source_crs=None, owner_crs=None):
    """Recalculate one explicitly associated object's derived projection.

    Failure clears the current derived value. Source geometry and associations
    are copied unchanged. Valid Polygon pairs have no projection by contract;
    this expected absence is distinct from malformed or unqualified geometry.
    No nearest-owner search, invented metric CRS, system of repérage or PR occurs.
    """
    result = deepcopy(state)
    result.update(projection=None, diagnostics=[], qualification="LOGICAL_PROJECTION_NOT_CERTIFIED")
    source = state.get("geometry")
    try:
        if not isinstance(source, dict) or not isinstance(owner_geometry, dict):
            raise SpatialContractError("PROJECTION_GEOMETRY_MISSING")
        source_type, owner_type = source.get("type"), owner_geometry.get("type")
        if (source_type == "Polygon" and owner_type == "LineString"
                or owner_type == "Polygon" and source_type in {"Point", "LineString", "Polygon"}):
            _same_crs(source, owner_geometry, source_crs, owner_crs)
            for geometry in (source, owner_geometry):
                if geometry["type"] == "Point":
                    _point(geometry.get("coordinates"))
                elif geometry["type"] == "LineString":
                    _line(geometry)
                else:
                    _polygon(geometry)
            result["diagnostics"].append({"code": "INV-GEO.PROJECTION_NOT_APPLICABLE",
                                          "blocking": False, "current_projection_cleared": True})
            return result
        if owner_geometry.get("type") != "LineString" or source.get("type") not in {"Point", "LineString"}:
            raise SpatialContractError("PROJECTION_FORBIDDEN_SHAPES")
        crs = _same_crs(source, owner_geometry, source_crs, owner_crs)
        line = _line(owner_geometry)
        if source["type"] == "Point":
            first, last = _point(source.get("coordinates")), None
        else:
            points = _line(source)
            first, last = points[0], points[-1]
        result["projection"] = {"start": _locate(first, line),
                                "end": _locate(last, line) if last is not None else None,
                                "crs": deepcopy(crs), "distance_unit": "CRS_UNIT"}
    except SpatialContractError as error:
        result["diagnostics"].append({"code": error.code, "blocking": True,
                                      "current_projection_cleared": True})
    return result


def plan_bornage(previous_geometry, new_geometry, active_bornage, dependent_projections, *, historical=False, new_crs=None):
    """Plan Core bornage transitions without inventing bornes or SQL bindings.

    An initialization request is mandatory for a new line, with its exact
    endpoints. Runtime creation of identities and the Core binding remain an
    explicit obligation. General history owns old values; this is no archive.
    """
    result = {"action": "UNCHANGED", "active_bornage": deepcopy(active_bornage),
              "dependent_projections": deepcopy(dependent_projections),
              "initial_bounds": [], "core_operation": None,
              "history_policy": "general", "diagnostics": [], "obligations": [],
              "core_interface": deepcopy(CORE_REPERAGE_INTERFACE),
              "qualification": "LOGICAL_PLAN_NOT_EXECUTED"}
    old_type = previous_geometry.get("type") if isinstance(previous_geometry, dict) else None
    new_type = new_geometry.get("type") if isinstance(new_geometry, dict) else None
    if new_type == "Polygon":
        result.update(action="REMOVE_ACTIVE_BORNAGE", active_bornage=None,
                      dependent_projections=[None for _ in dependent_projections], core_operation="remove_active_bornage")
        result["obligations"] = ["CORE_BORNAGE_EXECUTION_UNQUALIFIED"]
    elif new_type == "LineString":
        if historical or old_type == "LineString":
            result["action"] = "PRESERVE_HISTORICAL_BORNAGE" if historical else "PRESERVE_CORE_BORNAGE"
        else:
            result.update(action="INITIALIZE_CORE_BORNAGE", active_bornage=None,
                          dependent_projections=[None for _ in dependent_projections],
                          core_operation="initialize_bornage")
            result["obligations"] = ["CORE_BORNAGE_EXECUTION_UNQUALIFIED"]
            try:
                _line(new_geometry)
                crs = _same_crs(new_geometry, new_geometry, new_crs, new_crs)
                result["initial_bounds"] = [
                    {"role": role, "geometry": {"type": "Point", "coordinates": deepcopy(coordinate)},
                     "crs": deepcopy(crs)}
                    for role, coordinate in (("start", new_geometry["coordinates"][0]),
                                             ("end", new_geometry["coordinates"][-1]))]
            except SpatialContractError as error:
                result["initial_bounds"] = []
                result["diagnostics"].append({"code": error.code})
    else:
        result["diagnostics"].append({"code": "INV-GEO.BORNAGE_GEOMETRY_UNQUALIFIED"})
    return result
