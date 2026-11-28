#!/usr/bin/env python3
"""Exporte les prestations actuelles sans recalculer leurs géométries."""

from pathlib import Path

from osgeo import ogr

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "gpkg" / "prestations.gpkg"
SQL = ROOT / "data" / "15_prestations.sql"
PHOTO_FIELDS = ("photo_1", "photo_1_commentaire", "photo_2", "photo_2_commentaire")


TARGETS = ("cours_eau_id", "digue_id", "ah_stockage_id", "ah_ouvrage_id", "ah_section_id",
           "site_terrestre_id", "troncon_id", "mobilier_id", "cheminement_id", "vegetation_id")
OWN = {"cours_eau_id", "digue_id", "troncon_id", "site_terrestre_id", "ah_section_id"}


def quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    """Export the current demo exactly, preserving manually edited geometries.

    No seed geometry, translation or historic topological transform is replayed.
    """
    ogr.UseExceptions()
    source = ogr.Open(str(PACKAGE), 0)
    if source is None:
        raise FileNotFoundError(PACKAGE)
    rows = []
    fields = ("id", "intitule", "type_prestation_id", "prestataire_id",
              "technicien_id", "date_debut", "date_fin", "quantite", "unite",
              "description", "position", "rive", *PHOTO_FIELDS,
              *TARGETS)
    for layer in source:
        if layer.GetSpatialRef().GetAuthorityCode(None) != "3950":
            raise ValueError(f"SRID incorrect pour {layer.GetName()}")
        for feature in layer:
            values = [feature.GetField(name) for name in fields]
            code = feature.GetField("statut")
            geometry = feature.GetGeometryRef()
            targets = [key for key in TARGETS if feature[key]]
            if len(targets) != 1 or (targets[0] in OWN and geometry is None):
                raise ValueError(f"Cible ou géométrie propre incorrecte pour {feature['id']}")
            if geometry is not None and (geometry.IsEmpty() or not geometry.IsValid()
                    or geometry.Is3D() or geometry.IsMeasured() or geometry.GetGeometryName() not in
                    {"POINT","MULTIPOINT","LINESTRING","MULTILINESTRING","POLYGON","MULTIPOLYGON"}):
                raise ValueError(f"Géométrie incompatible pour {feature['id']}")
            required = ("id", "type_prestation_id", "prestataire_id", "technicien_id", "date_debut", "date_fin")
            if not all(feature[key] for key in required) or not code:
                raise ValueError(f"Attribut obligatoire absent pour {feature['id']}")
            if feature.GetField("site_terrestre_id") and (
                    geometry is None or geometry.GetGeometryName() not in
                    ("MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON")):
                raise ValueError(f"La prestation {feature.GetField('id')} sur site doit être multipartie")
            geom = (f"ST_GeomFromWKB(decode('{geometry.ExportToWkb().hex()}','hex'),3950)"
                    if geometry is not None else "NULL")
            literals = [quoted(value) if value is not None else "NULL" for value in values]
            literals += [f"(SELECT id FROM gemapi.statuts_prestations WHERE code={quoted(code)})", geom]
            rows.append((feature.GetField("id"), f"INSERT INTO gemapi.prestations ({','.join((*fields, 'statut_id', 'geom'))}) VALUES ({','.join(literals)});"))
    if len(rows) != 100 or len({uid for uid, _ in rows}) != 100:
        raise ValueError("100 UUID de démonstration distincts requis")
    header = ["-- Prestations courantes, coordonnées exactes conservées depuis le GeoPackage.", "BEGIN;",
              "SELECT pg_catalog.set_config('search_path', pg_catalog.format('pg_catalog,%I,%I', n.nspname, 'gemapi'), true) FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='postgis';"]
    SQL.write_text("\n".join([*header, *(statement for _, statement in sorted(rows)), "COMMIT;", ""]), encoding="utf-8")
    print("100 prestations exportées sans recalcul des géométries ni attributs")

if __name__ == "__main__":
    main()
