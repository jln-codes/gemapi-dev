#!/usr/bin/env python3
"""Exporte l'import SQL des digues depuis le GeoPackage canonique courant.

Les géométries et signatures acceptées sont restaurées à l'identique, sans
qualification humaine inventée. L'import administratif est réservé aux tables
vides ; il suspend uniquement le calcul des repères, dans sa transaction.
"""

from pathlib import Path
import re
from uuid import UUID

from osgeo import ogr


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "gpkg" / "digues.gpkg"
SQL = ROOT / "data" / "digues.sql"


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    ogr.UseExceptions()
    source = ogr.Open(str(PACKAGE), 0)
    if source is None:
        raise RuntimeError("GeoPackage fictif des digues absent")
    layer = source.GetLayerByName("digues")
    if layer is None or layer.GetSpatialRef().GetAuthorityCode(None) != "3950":
        raise RuntimeError("Couche digues EPSG:3950 absente")

    rows = []
    parent_names = {}
    for feature in layer:
        repere_id = str(feature["id"])
        digue_id = str(feature["digue_id"])
        river_id = str(feature["riviere_id"])
        name = feature["digue_nom"]
        side = feature["rive"]
        start = float(feature["debut_fraction"])
        end = float(feature["fin_fraction"])
        geometry = feature.GetGeometryRef()
        signature = feature.GetField("geom_riviere_signature")
        if (str(UUID(repere_id)) != repere_id or str(UUID(digue_id)) != digue_id
                or str(UUID(river_id)) != river_id or not name
                or side not in {"G", "D"} or not 0 <= start < end <= 1
                or geometry is None or geometry.IsEmpty() or not geometry.IsValid()
                or geometry.GetGeometryName() != "LINESTRING" or geometry.Length() <= 0
                or not signature or not re.fullmatch(r"[0-9a-f]{32}", signature)):
            raise RuntimeError(f"Repère de digue fictif incompatible : {repere_id}")
        if digue_id in parent_names and parent_names[digue_id] != name:
            raise RuntimeError(f"Nom contradictoire pour la digue {digue_id}")
        parent_names[digue_id] = name
        rows.append((repere_id, digue_id, river_id, side, start, end,
                     geometry.ExportToWkb().hex(), signature))
    source = None
    if len(rows) != 3 or len(parent_names) != 3:
        raise RuntimeError("Les trois digues et trois repères fictifs sont requis")

    sql = [
        "-- Restauration administrative des trois instantanés fictifs ; aucune qualification inventée.",
        "BEGIN;",
        "SELECT pg_catalog.set_config('search_path', pg_catalog.format('pg_catalog,%I,%I', n.nspname, 'gemapi'), true) FROM pg_catalog.pg_extension AS e JOIN pg_catalog.pg_namespace AS n ON n.oid=e.extnamespace WHERE e.extname='postgis';",
        "LOCK TABLE gemapi.digues, gemapi.digues_reperes IN ACCESS EXCLUSIVE MODE;",
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM gemapi.digues) OR EXISTS(SELECT 1 FROM gemapi.digues_reperes) THEN RAISE EXCEPTION 'Import des digues réservé aux tables vides'; END IF; END $$;",
        "ALTER TABLE gemapi.digues_reperes DISABLE TRIGGER digues_reperes_prepare_trg;",
    ]
    written_parents = set()
    for repere_id, digue_id, river_id, side, start, end, wkb, signature in rows:
        if digue_id not in written_parents:
            sql.append(
                "INSERT INTO gemapi.digues(id,nom) VALUES "
                f"({quote(digue_id)},{quote(parent_names[digue_id])});"
            )
            written_parents.add(digue_id)
        sql.append(
            "INSERT INTO gemapi.digues_reperes"
            "(id,digue_id,riviere_id,rive,debut_fraction,fin_fraction,geom_reference,geom_riviere_signature) VALUES "
            f"({quote(repere_id)},{quote(digue_id)},{quote(river_id)},"
            f"{quote(side)},{start!r},{end!r},"
            f"ST_GeomFromWKB(decode('{wkb}','hex'),3950),{quote(signature)});"
        )
    sql.extend(["SET CONSTRAINTS ALL IMMEDIATE;",
                "ALTER TABLE gemapi.digues_reperes ENABLE TRIGGER digues_reperes_prepare_trg;", "COMMIT;", ""])
    SQL.write_text("\n".join(sql), encoding="utf-8")
    print(f"{len(rows)} repères fictifs exportés sans recalcul : {SQL.name}")


if __name__ == "__main__":
    main()
