"""Lecture v1 des emprises patrimoniales sur une connexion métier autorisée.

Les noms SQL restent confinés ici. Le résultat ne dépend ni des extensions des
Tronçons ni des preuves techniques de leur import.
"""

from __future__ import annotations

import math
from uuid import UUID

import psycopg
from digues_app.crs import qualified_business_crs
from fastapi import HTTPException
from psycopg import sql

from .database import business_crs_payload, read_business_crs


_GEOMETRY_TABLES = {
    "troncon": sql.Identifier("public", "troncons"),
    "zone_stockage": sql.Identifier("public", "zones_stockage"),
}


def read_lidar_mask_sources(connection, *, systeme_ids: tuple[UUID, ...],
                            amenagement_ids: tuple[UUID, ...]) -> dict:
    """Lit les liens et formes canoniques pour LIDAR_MASK_V1, dans le snapshot appelant.

    La relation SE est exclusivement systemes <- digues <- troncons ;
    la relation AH est la FK directe des Tronçons et des Zones.
    Aucune association inverse ni extension historique n'est suivie ici.
    """
    existing_systemes = {row[0] for row in connection.execute(
        "SELECT id FROM public.systemes WHERE id=ANY(%s::uuid[])",
        (list(systeme_ids),)).fetchall()}
    existing_amenagements = {row[0] for row in connection.execute(
        "SELECT id FROM public.amenagements_hydrauliques WHERE id=ANY(%s::uuid[])",
        (list(amenagement_ids),)).fetchall()}
    rows = connection.execute("""
        SELECT 'SE', d.systeme_endiguement_id, 'TRONCON', t.id,
               t.type_physique_id, t.valid, ST_AsEWKB(t.geometry)
        FROM public.troncons t
        JOIN public.digues d ON d.id=t.digue_id
        WHERE d.systeme_endiguement_id=ANY(%s::uuid[])
        UNION ALL
        SELECT 'AH', t.amenagement_hydraulique_id, 'TRONCON', t.id,
               t.type_physique_id, t.valid, ST_AsEWKB(t.geometry)
        FROM public.troncons t
        WHERE t.amenagement_hydraulique_id=ANY(%s::uuid[])
        UNION ALL
        SELECT 'AH', z.amenagement_hydraulique_id, 'ZONE_STOCKAGE', z.id,
               NULL::text, z.valid, ST_AsEWKB(z.geometry)
        FROM public.zones_stockage z
        WHERE z.amenagement_hydraulique_id=ANY(%s::uuid[])
        ORDER BY 1,2,3,4
    """, (list(systeme_ids), list(amenagement_ids), list(amenagement_ids))).fetchall()
    return {"systemes": existing_systemes, "amenagements": existing_amenagements,
            "rows": rows}


def _geometry(connection, kind: str, identifier: UUID, requested_srid: int):
    """Isole un échec PostGIS à l'objet, afin de garder le reste de la sélection."""
    table = _GEOMETRY_TABLES[kind]
    source_srid = None
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("SELECT ST_SRID(geometry) FROM {} WHERE id=%s")
                               .format(table), (identifier,))
                source_srid = cursor.fetchone()[0]
                if source_srid <= 0:
                    return source_srid, None, None, ["SRID_INCONNU"]
                cursor.execute(sql.SQL("SELECT ST_IsEmpty(geometry), ST_IsValid(geometry) "
                                       "FROM {} WHERE id=%s").format(table), (identifier,))
                empty, topology_valid = cursor.fetchone()
                if empty:
                    return source_srid, None, None, ["GEOMETRIE_VIDE"]
                cursor.execute(sql.SQL("""
                    WITH transformed AS (
                        SELECT CASE WHEN ST_SRID(geometry)=%s THEN geometry
                                    ELSE ST_Transform(geometry,%s) END AS g
                        FROM {} WHERE id=%s
                    )
                    SELECT ST_AsGeoJSON(g,15,0)::jsonb,
                           ST_XMin(g),ST_YMin(g),ST_XMax(g),ST_YMax(g)
                    FROM transformed
                """).format(table), (requested_srid, requested_srid, identifier))
                geojson, xmin, ymin, xmax, ymax = cursor.fetchone()
                bbox = [float(xmin), float(ymin), float(xmax), float(ymax)]
                if geojson is None or not all(map(math.isfinite, bbox)):
                    return source_srid, None, None, ["GEOMETRIE_INEXPLOITABLE"]
                diagnostics = [] if topology_valid else ["TOPOLOGIE_INVALIDE"]
                return source_srid, geojson, bbox, diagnostics
    except (psycopg.DataError, psycopg.InternalError):
        # La transaction imbriquée revient à son savepoint. Aucun détail SQL
        # ni coordonnée sensible n'est exposé dans le diagnostic public.
        return source_srid, None, None, ["GEOMETRIE_INEXPLOITABLE"]


def read_patrimoine_geometries(connection, *, digue_id: UUID | None,
                               amenagement_hydraulique_id: UUID | None,
                               crs: str) -> dict:
    """Projette une sélection explicite, sans déduction spatiale de parent.

    Sans filtre parent, le périmètre couvre toute la base sélectionnée. Sinon,
    il comprend l'union des Tronçons rattachés aux parents demandés. Les AH
    demandés et ceux rattachés à ces Tronçons sont retournés. Les Zones liées
    ne créent aucun lien direct avec les Tronçons.
    """
    whole_base = digue_id is None and amenagement_hydraulique_id is None
    try:
        requested = qualified_business_crs(crs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    with connection.transaction():
        stored_crs = read_business_crs(connection)
        if digue_id is not None and connection.execute(
                "SELECT 1 FROM public.digues WHERE id=%s", (digue_id,)).fetchone() is None:
            raise HTTPException(404, "Digue introuvable dans cette base.")
        if amenagement_hydraulique_id is not None and connection.execute(
                "SELECT 1 FROM public.amenagements_hydrauliques WHERE id=%s",
                (amenagement_hydraulique_id,)).fetchone() is None:
            raise HTTPException(404, "Aménagement hydraulique introuvable dans cette base.")

        troncon_rows = connection.execute("""
            SELECT id,type_physique_id,digue_id,amenagement_hydraulique_id,
                   valid,geometry IS NOT NULL
            FROM public.troncons
            WHERE %s::boolean OR digue_id=%s OR amenagement_hydraulique_id=%s
            ORDER BY id
        """, (whole_base, digue_id, amenagement_hydraulique_id)).fetchall()
        ah_ids = {row[3] for row in troncon_rows if row[3] is not None}
        if amenagement_hydraulique_id is not None:
            ah_ids.add(amenagement_hydraulique_id)
        ah_rows = connection.execute("""
            SELECT id,valid
            FROM public.amenagements_hydrauliques
            WHERE %s::boolean OR id=ANY(%s::uuid[])
            ORDER BY id
        """, (whole_base, list(ah_ids))).fetchall()
        zone_rows = connection.execute("""
            SELECT id,amenagement_hydraulique_id,valid,geometry IS NOT NULL
            FROM public.zones_stockage
            WHERE %s::boolean OR amenagement_hydraulique_id=ANY(%s::uuid[])
            ORDER BY id
        """, (whole_base, list(ah_ids))).fetchall()

        extent = None
        diagnostics = []

        def projected(kind, identifier, has_geometry):
            nonlocal extent
            if not has_geometry:
                source_srid, geometry, bbox, issues = None, None, None, ["GEOMETRIE_ABSENTE"]
            else:
                source_srid, geometry, bbox, issues = _geometry(
                    connection, kind, identifier, requested.srid)
            if bbox is not None:
                extent = bbox[:] if extent is None else [
                    min(extent[0], bbox[0]), min(extent[1], bbox[1]),
                    max(extent[2], bbox[2]), max(extent[3], bbox[3]),
                ]
            diagnostics.extend({"resource": kind, "id": str(identifier), "code": code}
                               for code in issues)
            return {"geometry": geometry, "source_srid": source_srid,
                    "geometry_srid": requested.srid if geometry is not None else None,
                    "bbox": bbox}

        troncons = [{"id": str(identifier), "type": subtype,
                     "digue_id": str(digue) if digue else None,
                     "amenagement_hydraulique_id": str(ah) if ah else None,
                     "valid": valid,
                     **projected("troncon", identifier, has_geometry)}
                    for identifier, subtype, digue, ah, valid, has_geometry in troncon_rows]
        amenagements = [{"id": str(identifier), "valid": valid,
                         "zone_ids": [str(zone_id) for zone_id, ah, *_ in zone_rows if ah == identifier],
                         "troncon_ids": [str(troncon_id) for troncon_id, _, _, ah, _, _ in troncon_rows
                                         if ah == identifier]}
                        for identifier, valid in ah_rows]
        zones = [{"id": str(identifier),
                  "amenagement_hydraulique_id": str(ah) if ah else None,
                  "valid": valid,
                  **projected("zone_stockage", identifier, has_geometry)}
                 for identifier, ah, valid, has_geometry in zone_rows]
        return {"version": 1, "crs": business_crs_payload(requested),
                "stored_crs": business_crs_payload(stored_crs),
                "selection": {"scope": "base" if whole_base else "parents",
                              "digue_id": str(digue_id) if digue_id else None,
                              "amenagement_hydraulique_id": (
                                  str(amenagement_hydraulique_id)
                                  if amenagement_hydraulique_id else None)},
                "troncons": troncons, "amenagements_hydrauliques": amenagements,
                "zones_stockage": zones,
                "bbox": extent, "diagnostics": diagnostics}
