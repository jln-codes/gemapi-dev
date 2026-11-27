"""Persistance du territoire administratif singleton."""

from __future__ import annotations

import json
import logging
from typing import Any

from .database import WebDatabaseError, read_business_crs


LOGGER = logging.getLogger(__name__)


class TerritoireConflictError(ValueError):
    """Un territoire existe déjà et son remplacement n'a pas été demandé."""


class TerritoirePersistenceError(ValueError):
    """PostgreSQL a refusé la géométrie du territoire administratif."""


def territoire_geojson_sql(srid: int) -> str:
    return f"""
    SELECT jsonb_build_object(
        'type', 'FeatureCollection',
        'features', coalesce(jsonb_agg(
            jsonb_build_object(
                'type', 'Feature',
                'id', t.id,
                'geometry', ST_AsGeoJSON(ST_Transform(t.geometry, 4326))::jsonb,
                'properties', jsonb_build_object(
                    'id', t.id,
                    'libelle', t.libelle,
                    'srid', ST_SRID(t.geometry)
                )
            ) ORDER BY t.id
        ), '[]'::jsonb)
    )
    FROM technique.territoires_administratifs AS t
    WHERE ST_SRID(t.geometry) = {srid}
"""


def territoire_validate_wkb_sql(srid: int) -> str:
    return f"""
    SELECT
        GeometryType(candidate.geometry),
        ST_IsValid(candidate.geometry),
        NOT ST_IsEmpty(candidate.geometry),
        ST_SRID(candidate.geometry)
    FROM (
        SELECT ST_SetSRID(ST_GeomFromWKB(%s), {srid}) AS geometry
    ) AS candidate
"""


def territoire_insert_sql(srid: int) -> str:
    return f"""
    INSERT INTO technique.territoires_administratifs (id, libelle, geometry)
    VALUES (
        1,
        %s,
        ST_SetSRID(ST_GeomFromWKB(%s), {srid})
            ::geometry(Polygon, {srid})
    )
    ON CONFLICT (id) DO NOTHING
    RETURNING id
"""


def territoire_upsert_sql(srid: int) -> str:
    return f"""
    INSERT INTO technique.territoires_administratifs (id, libelle, geometry)
    VALUES (
        1,
        %s,
        ST_SetSRID(ST_GeomFromWKB(%s), {srid})
            ::geometry(Polygon, {srid})
    )
    ON CONFLICT (id) DO UPDATE SET
        libelle = EXCLUDED.libelle,
        geometry = EXCLUDED.geometry
    RETURNING id
"""


def fetch_territoire_administratif(connection: Any) -> dict[str, Any]:
    """Retourne le territoire courant en GeoJSON EPSG:4326, ou une collection vide."""

    try:
        business_crs = read_business_crs(connection)
        with connection.cursor() as cursor:
            cursor.execute(territoire_geojson_sql(business_crs.srid))
            row = cursor.fetchone()
    except WebDatabaseError:
        raise
    except Exception as exc:
        raise WebDatabaseError(
            "Lecture du territoire administratif impossible."
        ) from exc
    result = row[0] if row else None
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or result.get("type") != "FeatureCollection":
        raise WebDatabaseError("Réponse GeoJSON du territoire invalide.")
    result.setdefault("features", [])
    return result


def replace_territoire_administratif(
    connection: Any,
    *,
    libelle: str,
    wkb: bytes,
    target_srid: int,
    replace: bool,
) -> dict[str, Any]:
    """Insère ou remplace transactionnellement le singleton id=1."""

    normalized_libelle = libelle.strip()
    if not normalized_libelle:
        raise TerritoirePersistenceError("Le libellé du territoire est obligatoire.")
    try:
        with connection.transaction():
            business_crs = read_business_crs(connection)
            if target_srid != business_crs.srid:
                raise TerritoirePersistenceError(
                    "Le CRS cible de la géométrie importée ne correspond pas "
                    "au CRS métier de la base."
                )
            with connection.cursor() as cursor:
                cursor.execute(
                    territoire_validate_wkb_sql(business_crs.srid), (wkb,)
                )
                row = cursor.fetchone()
                if row is None:
                    raise TerritoirePersistenceError(
                        "La géométrie du territoire est invalide."
                    )
                geometry_type, is_valid, is_not_empty, srid = row
                if (
                    geometry_type != "POLYGON"
                    or not is_valid
                    or not is_not_empty
                    or srid != business_crs.srid
                ):
                    raise TerritoirePersistenceError(
                        "La géométrie du territoire est invalide."
                    )

                cursor.execute(
                    territoire_upsert_sql(business_crs.srid)
                    if replace
                    else territoire_insert_sql(business_crs.srid),
                    (normalized_libelle, wkb),
                )
                if cursor.fetchone() is None:
                    raise TerritoireConflictError(
                        "Un territoire administratif existe déjà ; "
                        "demander explicitement son remplacement."
                    )
            return fetch_territoire_administratif(connection)
    except (TerritoireConflictError, TerritoirePersistenceError, WebDatabaseError):
        raise
    except Exception as exc:
        LOGGER.exception("Enregistrement du territoire administratif impossible")
        raise TerritoirePersistenceError(
            "L’enregistrement du territoire a échoué."
        ) from exc
