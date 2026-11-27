"""LIDAR_MASK_V1 : masques PostGIS à la demande sur une base métier autorisée.

Ce module ne publie aucune route et ne stocke aucun masque. Le fournisseur de
connexion doit résoudre l'autorisation et la base avant l'appel.
"""

from __future__ import annotations

import json
import math
from time import perf_counter_ns
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from .database import read_business_crs
from .patrimoine_geometries import read_lidar_mask_sources

VERSION = "LIDAR_MASK_V1"
OUTPUT_SRID = 2154
RADIUS_M = 25.0
BUFFER_OPTIONS = "quad_segs=8"
MAX_ENSEMBLES = 1024  # Defensive cardinality, paired with the 64 KiB HTTP budget.
_SHAPES = frozenset({"POINT", "LINESTRING", "POLYGON", "MULTIPOINT",
                     "MULTILINESTRING", "MULTIPOLYGON"})
_BLOCKING = frozenset({"SRID_INCONNU", "TYPE_INACCEPTE", "TOPOLOGIE_INVALIDE",
                       "TRANSFORMATION_IMPOSSIBLE", "CALCUL_INEXPLOITABLE",
                       "ENSEMBLE_INTROUVABLE"})


def _selection(systeme_ids, amenagement_ids):
    def normalize(values):
        values = tuple(values)
        if any(not isinstance(value, UUID) for value in values):
            raise ValueError("Les identifiants d'ensemble doivent être des UUID.")
        return tuple(sorted(set(values), key=str))

    systemes, amenagements = normalize(systeme_ids), normalize(amenagement_ids)
    if not 1 <= len(systemes) + len(amenagements) <= MAX_ENSEMBLES:
        raise ValueError(f"Sélection limitée à 1–{MAX_ENSEMBLES} ensembles distincts.")
    return systemes, amenagements


def _project_source(connection, ewkb, phase_counters=None):
    """Qualifie puis transforme une seule forme ; un échec reste isolé par savepoint."""
    if ewkb is None:
        return None, None, "GEOMETRIE_ABSENTE"
    srid = None
    try:
        with connection.transaction():
            srid, shape, empty, valid = connection.execute("""
                WITH g AS (SELECT ST_GeomFromEWKB(%s::bytea) AS geom)
                SELECT ST_SRID(geom), GeometryType(geom), ST_IsEmpty(geom),
                       ST_IsValid(geom) FROM g
            """, (bytes(ewkb),)).fetchone()
            if srid <= 0:
                return srid, None, "SRID_INCONNU"
            if empty:
                return srid, None, "GEOMETRIE_VIDE"
            if shape not in _SHAPES:
                return srid, None, "TYPE_INACCEPTE"
            if not valid:
                return srid, None, "TOPOLOGIE_INVALIDE"
            transform_started = perf_counter_ns() if phase_counters is not None else None
            try:
                projected, projected_valid, projected_empty, bounds = connection.execute("""
                    WITH g AS MATERIALIZED (
                        SELECT CASE WHEN ST_SRID(geom)=%s THEN geom
                                    ELSE ST_Transform(geom,%s) END AS geom
                        FROM (SELECT ST_GeomFromEWKB(%s::bytea) AS geom) source
                    )
                    SELECT ST_AsEWKB(geom), ST_IsValid(geom), ST_IsEmpty(geom),
                           ARRAY[ST_XMin(geom), ST_YMin(geom),
                                 ST_XMax(geom), ST_YMax(geom)] FROM g
                """, (OUTPUT_SRID, OUTPUT_SRID, bytes(ewkb))).fetchone()
            finally:
                if phase_counters is not None:
                    phase_counters["transform_ns"] += perf_counter_ns()-transform_started
            if (not projected_valid or projected_empty or projected is None
                    or not all(math.isfinite(value) for value in bounds)):
                return srid, None, "TRANSFORMATION_IMPOSSIBLE"
            return srid, bytes(projected), None
    except (psycopg.DataError, psycopg.InternalError):
        return srid, None, "TRANSFORMATION_IMPOSSIBLE"


def _union_buffers(connection, projected, radius_m):
    """Agrège dans l'ordre canonique, sans réparation ni réduction à l'enveloppe."""
    try:
        with connection.transaction():
            geometry, area, bounds, valid, shape, empty = connection.execute("""
                WITH inputs AS MATERIALIZED (
                    SELECT ordinal, ST_Buffer(ST_GeomFromEWKB(ewkb), %s, %s) AS geom
                    FROM unnest(%s::bytea[]) WITH ORDINALITY AS rows(ewkb, ordinal)
                ), merged AS MATERIALIZED (
                    SELECT ST_UnaryUnion(ST_Collect(geom ORDER BY ordinal)) AS geom
                    FROM inputs
                ), polygon AS MATERIALIZED (
                    SELECT CASE WHEN GeometryType(geom) IN ('POLYGON','MULTIPOLYGON')
                                THEN ST_Multi(geom) END AS geom FROM merged
                )
                SELECT ST_AsGeoJSON(geom,15,0)::jsonb, ST_Area(geom),
                       ARRAY[ST_XMin(geom),ST_YMin(geom),ST_XMax(geom),ST_YMax(geom)],
                       ST_IsValid(geom), GeometryType(geom), ST_IsEmpty(geom)
                FROM polygon
            """, (radius_m, BUFFER_OPTIONS, projected)).fetchone()
    except (psycopg.DataError, psycopg.InternalError):
        return None
    if (geometry is None or not valid or empty or shape != "MULTIPOLYGON"
            or area is None or not math.isfinite(area) or area <= 0
            or bounds is None or not all(math.isfinite(value) for value in bounds)):
        return None
    return geometry, float(area), [float(value) for value in bounds]


def calculate_lidar_masks(connection, *, systeme_ids=(), amenagement_ids=(),
                          timings_out: dict | None = None, radius_m: float = RADIUS_M) -> dict:
    """Calcule les masques SE/AH dans un seul snapshot métier borné.

`connection` est une connexion métier autorisée, inactive et dédiée à l'appel.
La fonction ouvre REPEATABLE READ READ ONLY avant toute requête métier. Les
temps facultatifs sont écrits dans `timings_out`, jamais dans le résultat
déterministe. Aucun ensemble inexistant ni forme anomalique ne donne de masque
partiel. Les géométries sources ne sont jamais modifiées.
    """
    from digues_app.control.lidar_policy import validate_buffer
    validate_buffer(radius_m)
    systemes, amenagements = _selection(systeme_ids, amenagement_ids)
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("Le calcul requiert une connexion hors transaction.")
    started = perf_counter_ns()
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        stored_crs = read_business_crs(connection)
        source = read_lidar_mask_sources(connection, systeme_ids=systemes,
                                         amenagement_ids=amenagements)
        read_done = perf_counter_ns()
        by_mask = {(kind, identifier): {} for kind, identifiers in
                   (("SE", systemes), ("AH", amenagements)) for identifier in identifiers}
        unique = {}
        for kind, target_id, resource, identifier, subtype, valid, ewkb in source["rows"]:
            key = (resource, identifier)
            unique.setdefault(key, (subtype, valid, ewkb))
            by_mask[(kind, target_id)][key] = None
        phase_counters = {"transform_ns": 0} if timings_out is not None else None
        projected = {key: _project_source(connection, value[2], phase_counters)
                     for key, value in sorted(unique.items(), key=lambda pair:
                                              (pair[0][0], str(pair[0][1])))}
        transform_done = perf_counter_ns()
        masks = []
        for (kind, identifier), members in by_mask.items():
            exists = identifier in (source["systemes"] if kind == "SE"
                                    else source["amenagements"])
            diagnostics = []
            contributors = []
            if not exists:
                diagnostics.append({"code": "ENSEMBLE_INTROUVABLE", "resource": kind,
                                    "id": str(identifier)})
            usable = []
            for key in sorted(members, key=lambda item: (item[0], str(item[1]))):
                resource, contributor_id = key
                subtype, valid, _ = unique[key]
                srid, shape, issue = projected[key]
                state = "USED" if issue is None else issue
                contributors.append({"resource": resource, "id": str(contributor_id),
                                     "subtype": subtype, "valid": valid,
                                     "source_srid": srid, "state": state})
                if issue is not None:
                    diagnostics.append({"code": issue, "resource": resource,
                                        "id": str(contributor_id)})
                else:
                    usable.append(shape)
            status = "ANOMALY" if any(item["code"] in _BLOCKING for item in diagnostics) else "EMPTY"
            geometry = area = bbox = None
            if status != "ANOMALY" and usable:
                result = _union_buffers(connection, usable, radius_m)
                if result is None:
                    status = "ANOMALY"
                    diagnostics.append({"code": "CALCUL_INEXPLOITABLE", "resource": kind,
                                        "id": str(identifier)})
                else:
                    status = "READY"
                    geometry, area, bbox = result
            masks.append({"type": kind, "id": str(identifier), "status": status,
                          "geometry": geometry, "area_m2": area, "bbox": bbox,
                          "contributors": contributors, "diagnostics": diagnostics})
        union_done = perf_counter_ns()
    if timings_out is not None:
        timings_out.clear()
        timings_out.update({
            "read_ms": (read_done-started)/1_000_000,
            "qualification_ms": (transform_done-read_done-phase_counters["transform_ns"])/1_000_000,
            "transform_ms": phase_counters["transform_ns"]/1_000_000,
            "buffer_union_ms": (union_done-transform_done)/1_000_000,
            "total_ms": (perf_counter_ns()-started)/1_000_000,
            "unique_contributors": len(unique),
            "result_bytes": sum(len(json.dumps(mask["geometry"], sort_keys=True).encode())
                                for mask in masks if mask["geometry"] is not None),
        })
    return {"version": VERSION, "crs": "EPSG:2154", "radius_m": radius_m,
            "buffer_options": BUFFER_OPTIONS, "stored_crs": stored_crs.code,
            "masks": masks}
