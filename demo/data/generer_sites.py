#!/usr/bin/env python3
"""Exporte les sites et enfants fictifs depuis leur GeoPackage courant."""

from pathlib import Path

from osgeo import ogr


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "gpkg" / "sites_terrestres.gpkg"
SQL = ROOT / "data" / "10_sites_terrestres.sql"
SHAPES = {"POINT", "MULTIPOINT", "LINESTRING", "MULTILINESTRING", "POLYGON", "MULTIPOLYGON"}
TABLES = (
    ("gemapi.sites_terrestres", ("id", "nom"), "geom", {"MULTIPOLYGON"}),
    ("gemapi.mobilier", ("id", "designation", "type_id", "site_terrestre_id", "commentaire"), "geometry", SHAPES),
    ("gemapi.cheminements", ("id", "designation", "type_cheminement_id", "site_terrestre_id", "commentaire"), "geometry", SHAPES),
    ("gemapi.vegetation", ("id", "designation", "famille_vegetation", "site_terrestre_id", "commentaire"), "geometry", SHAPES),
)


def literal(value):
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def main():
    ogr.UseExceptions()
    source = ogr.Open(str(PACKAGE), 0)
    statements = [
        "-- Sites terrestres fictifs canoniques courants.",
        "BEGIN;",
        "SELECT pg_catalog.set_config('search_path', pg_catalog.format('pg_catalog,%I,%I', "
        "n.nspname, 'gemapi'), true) FROM pg_catalog.pg_extension e JOIN "
        "pg_catalog.pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='postgis';",
    ]
    for table, fields, geom_col, types in TABLES:
        layer = source.GetLayerByName(table.split(".")[1])
        if layer.GetSpatialRef().GetAuthorityCode(None) != "3950":
            raise ValueError(f"{table} doit utiliser EPSG:3950")
        rows = []
        for feature in layer:
            geometry = feature.GetGeometryRef()
            if (geometry is None or geometry.IsEmpty() or not geometry.IsValid()
                    or geometry.GetGeometryName() not in types
                    or geometry.Is3D() or geometry.IsMeasured()):
                raise ValueError(f"Géométrie incompatible pour {table}/{feature['id']}")
            values = [literal(feature[name]) for name in fields]
            wkb = geometry.ExportToWkb(ogr.wkbNDR).hex()
            values.append(f"ST_GeomFromWKB(decode('{wkb}','hex'),3950)")
            rows.append((feature["id"], f"INSERT INTO {table} "
                         f"({','.join((*fields, geom_col))}) VALUES ({','.join(values)});"))
        statements.extend(statement for _, statement in sorted(rows))
    SQL.write_text("\n".join([*statements, "COMMIT;", ""]), encoding="utf-8")


if __name__ == "__main__":
    main()
