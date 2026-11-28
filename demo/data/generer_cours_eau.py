#!/usr/bin/env python3
"""Rebuild fictional hydro SQL imports from the current demo GeoPackage.

``demo/gpkg/cours_eau.gpkg`` is the sole hydrographic source. This script
never changes it and never reads a real-data directory.
"""

from pathlib import Path
from uuid import UUID

from osgeo import ogr


ROOT = Path(__file__).resolve().parents[1]
GPKG = ROOT / "gpkg" / "cours_eau.gpkg"
SQL = ROOT / "data" / "11_cours_eau.sql"
LINK_TARGETS = (
    "56604300-336a-5db6-a654-624c3d49b9be",
    "72a200c8-f10a-51c4-b728-925594da209a",
    "237e9e64-756e-590d-a920-fe12d00c3763",
    "d41e0b39-cd80-526c-87d8-d4cadb98d685",
    "fe00e78e-569c-5d4b-8d46-c8c03147c31d",
    "746637dd-3706-5b78-ae35-9b76fee85d51",
)


def quote(value):
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def current_rows():
    ogr.UseExceptions()
    source = ogr.Open(str(GPKG), 0)
    if source is None:
        raise RuntimeError("GeoPackage fictif actuel manquant")
    layer = source.GetLayerByName("cours_eau")
    if layer is None or layer.GetSpatialRef().GetAuthorityCode(None) != "3950":
        raise RuntimeError("Couche cours_eau EPSG:3950 absente")
    rows = []
    for feature in layer:
        uid = feature.GetField("id")
        name = feature.GetField("nom")
        kind = feature.GetField("type_ecoulement")
        valid = feature.GetField("valide")
        direction = feature.GetField("direction")
        provenance = feature.GetField("source")
        geometry = feature.GetGeometryRef()
        if (not name or not name.strip() or str(UUID(uid)) != uid or not kind or provenance not in
                {"INVENTAIRE_RIVIERES", "RELEVE_FOSSES", "SYNTHESE_CARTOGRAPHIQUE"}
                or valid not in (0, 1)
                or geometry is None or ogr.GT_Flatten(geometry.GetGeometryType()) != ogr.wkbMultiLineString
                or geometry.IsEmpty() or not geometry.IsValid() or geometry.Length() <= 0):
            raise RuntimeError(f"Objet hydrographique fictif incompatible : {uid}")
        if feature.GetField("section") != geometry.GetGeometryCount() or bool(valid) != (direction == 1 and geometry.GetGeometryCount() == 1):
            raise RuntimeError(f"Qualification incohérente : {uid}")
        rows.append((uid, name, kind, direction, provenance, geometry.ExportToWkb().hex()))
    source = None
    if len(rows) != 58 or len({row[0] for row in rows}) != 58:
        raise RuntimeError("Les 58 UUID hydrographiques fictifs sont requis")
    if not set(LINK_TARGETS) <= {row[0] for row in rows}:
        raise RuntimeError("UUID liés incohérents")
    return rows


def main():
    rows = current_rows()
    sql = ["-- MOD-HYDRO-01 : cours d'eau fictifs ; UUID historiques conservés.",
           "BEGIN;",
           "SELECT pg_catalog.set_config('search_path', pg_catalog.format('pg_catalog,%I,%I', n.nspname, 'gemapi'), true) FROM pg_catalog.pg_extension AS e JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace WHERE e.extname='postgis';"]
    for uid, name, kind, direction, provenance, wkb in rows:
        sql.append("INSERT INTO gemapi.cours_eau (id,nom,type_ecoulement,direction,source,geom) VALUES ("
                   + ",".join((quote(uid), quote(name), quote(kind), "NULL" if direction is None else "TRUE" if direction else "FALSE", quote(provenance),
                               "ST_GeomFromWKB(decode('" + wkb + "','hex'),3950)")) + ");")
    sql.extend(["COMMIT;", ""])

    SQL.write_text("\n".join(sql), encoding="utf-8")
    print(f"{len(rows)} objets fictifs : {SQL.name}")


if __name__ == "__main__":
    main()
