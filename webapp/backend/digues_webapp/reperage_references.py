"""Bounded offline reference graph and its canonical, server-owned revision.

The revision describes the actual inputs of the PostgreSQL referencing engine,
not the presentation JSON (whose coordinate precision may change).
"""
import hashlib
import json
from uuid import UUID

from fastapi import HTTPException

from .database import WebDatabaseError, business_crs_payload, read_business_crs

MAX_SYSTEMS = 200
MAX_REFERENCES = 2000
MAX_BYTES = 8 * 1024 * 1024


def _hex(value):
    return bytes(value).hex() if value is not None else None


def system_graph(
    connection,
    troncon_id: UUID,
    system_id: UUID,
    *,
    lock: bool = False,
    business_crs=None,
):
    business_crs = business_crs or read_business_crs(connection)
    suffix = " FOR SHARE" if lock else ""
    trunk = connection.execute(
        "SELECT id,version,valid,ST_AsEWKB(coalesce(geometry_reperage,geometry)),"
        "CASE WHEN GeometryType(coalesce(geometry_reperage,geometry))='LINESTRING' "
        "THEN ST_AsGeoJSON(coalesce(geometry_reperage,geometry)) ELSE NULL END,"
        "ST_SRID(coalesce(geometry_reperage,geometry)) "
        "FROM public.troncons WHERE id=%s" + suffix, (troncon_id,)
    ).fetchone()
    system = connection.execute(
        "SELECT id,troncon_id,libelle,valid FROM public.systemes_reperage "
        "WHERE id=%s AND troncon_id=%s" + suffix, (system_id, troncon_id)
    ).fetchone()
    if not trunk or not system:
        return None
    links = connection.execute(
        "SELECT l.id,l.borne_id,l.valeur_pr,l.valid,b.libelle,b.valid,"
        "ST_AsEWKB(b.geometry),ST_AsGeoJSON(b.geometry),ST_SRID(b.geometry) "
        "FROM public.systemes_reperage__bornes l "
        "JOIN public.bornes_reperage b ON b.id=l.borne_id "
        "WHERE l.systeme_reperage_id=%s ORDER BY l.borne_id,l.id" +
        (" FOR SHARE OF l,b" if lock else ""), (system_id,)
    ).fetchall()
    if len(links) > MAX_REFERENCES:
        raise HTTPException(422, {"code": "scope-too-large", "message": "Trop de bornes de repérage dans le périmètre."})
    if trunk[4] is not None and trunk[5] != business_crs.srid:
        raise WebDatabaseError(
            "La géométrie du tronçon de repérage ne correspond pas au CRS métier."
        )
    if any(row[7] is not None and row[8] != business_crs.srid for row in links):
        raise WebDatabaseError(
            "Une géométrie de borne ne correspond pas au CRS métier."
        )
    canonical = {
        "troncon": [str(trunk[0]), trunk[1], trunk[2], _hex(trunk[3])],
        "systeme": [str(system[0]), str(system[1]), system[3]],
        "links": [[str(row[0]), str(row[1]), str(row[2]), row[3], row[5], _hex(row[6])] for row in links],
    }
    revision = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()
    return {
        "id": str(system[0]), "troncon_id": str(troncon_id), "designation": system[2] or str(system[0]),
        "valid": system[3], "revision": revision,
        "crs": business_crs_payload(business_crs),
        "troncon_geometry": json.loads(trunk[4]) if trunk[4] else None,
        "bornes": [{"link_id": str(row[0]), "id": str(row[1]), "pr": str(row[2]),
                    "link_valid": row[3], "designation": row[4] or str(row[1]),
                    "valid": row[5], "geometry": json.loads(row[7]) if row[7] else None}
                   for row in links],
    }


def scope_graph(connection, troncon_ids, *, business_crs=None):
    business_crs = business_crs or read_business_crs(connection)
    ids = [row[0] for row in connection.execute(
        "SELECT id FROM public.systemes_reperage WHERE troncon_id=ANY(%s::uuid[]) "
        "ORDER BY troncon_id,id LIMIT %s", (troncon_ids, MAX_SYSTEMS + 1)
    ).fetchall()]
    if len(ids) > MAX_SYSTEMS:
        raise HTTPException(422, {"code": "scope-too-large", "message": "Trop de systèmes de repérage dans le périmètre."})
    by_system = connection.execute(
        "SELECT id,troncon_id FROM public.systemes_reperage WHERE id=ANY(%s::uuid[]) ORDER BY troncon_id,id",
        (ids,)
    ).fetchall() if ids else []
    systems = [
        system_graph(
            connection, trunk, system, business_crs=business_crs
        )
        for system, trunk in by_system
    ]
    if sum(len(item["bornes"]) for item in systems if item) > MAX_REFERENCES:
        raise HTTPException(422, {"code": "scope-too-large", "message": "Trop de bornes de repérage dans le périmètre."})
    result = {
        "schemaVersion": 2,
        "crs": business_crs_payload(business_crs),
        "systems": systems,
    }
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_BYTES:
        raise HTTPException(422, {"code": "scope-too-large", "message": "Catalogue de repérage trop volumineux."})
    return result


def check_revision(connection, troncon_id: UUID, system_id: UUID, expected: str):
    # Reference writes use root versions; table locks also protect direct SQL. SHARE table locks
    # also exclude a concurrent *insert* into the link table (row locks do not).
    # They are held until execute_mutation commits the Photo and receipt.
    connection.execute("LOCK TABLE public.troncons, public.systemes_reperage, "
                       "public.bornes_reperage, public.systemes_reperage__bornes IN SHARE MODE")
    try:
        if system_id is None:
            from .reperage import read_state
            current=read_state(connection,troncon_id)
            current={"revision":current["reference_revision"]}
        else:
            current = system_graph(connection, troncon_id, system_id, lock=True)
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        current = None  # The graph has outgrown the prepared bounded graph.
    if not current or current["revision"] != expected:
        raise HTTPException(409, {"code": "photo-references-changed",
                                  "message": "Le repérage préparé a changé. Conservez le brouillon et vérifiez les références avant de réessayer."})
