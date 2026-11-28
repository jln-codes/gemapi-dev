#!/usr/bin/env python3
"""Audit spatial reproductible des prestations fictives du schéma courant.

La connexion PostgreSQL est lue depuis les variables libpq (PGHOST, PGPORT,
PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE). La session est forcée en lecture
seule. Le script ne modifie ni la base ni les GeoPackages canoniques.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg2
from osgeo import ogr, osr


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
from install import render, trusted_path

INVENTORY_SQL = r"""
WITH references_patrimoniales AS (
    SELECT p.id AS prestation_id, 'cours_eau'::text AS famille,
           c.id AS patrimoine_id, c.nom AS patrimoine_nom,
           NULL::uuid AS parent_id, NULL::text AS parent_nom, c.geom
      FROM gemapi.prestations p JOIN gemapi.cours_eau c ON c.id=p.cours_eau_id
    UNION ALL
    SELECT p.id, 'digue', d.id, d.nom, NULL::uuid, NULL::text, r.geom_reference
      FROM gemapi.prestations p
      JOIN gemapi.digues d ON d.id=p.digue_id
      JOIN gemapi.digues_reperes r ON r.digue_id=d.id
    UNION ALL
    SELECT p.id, 'ah_sections', h.id, c.nom, a.id, a.nom, h.geom_reference
      FROM gemapi.prestations p JOIN gemapi.ah_sections h ON h.id=p.ah_section_id
      JOIN gemapi.cours_eau c ON c.id=h.cours_eau_id JOIN gemapi.ah a ON a.id=h.ah_id
    UNION ALL
    SELECT p.id, 'ah_stockage', s.id, s.nom, a.id, a.nom, s.geom
      FROM gemapi.prestations p
      JOIN gemapi.ah_stockage s ON s.id=p.ah_stockage_id
      JOIN gemapi.ah a ON a.id=s.ah_id
    UNION ALL
    SELECT p.id, 'ah_ouvrage', o.id, o.nom, a.id, a.nom, o.geom
      FROM gemapi.prestations p
      JOIN gemapi.ah_ouvrage o ON o.id=p.ah_ouvrage_id
      JOIN gemapi.ah a ON a.id=o.ah_id
    UNION ALL
    SELECT p.id, 'site_terrestre', s.id, s.nom, NULL::uuid, NULL::text, s.geom
      FROM gemapi.prestations p
      JOIN gemapi.sites_terrestres s ON s.id=p.site_terrestre_id
    UNION ALL
    SELECT p.id, 'troncon', z.id, z.designation, d.id, d.nom, z.geometry FROM gemapi.prestations p JOIN gemapi.troncons z ON z.id=p.troncon_id
    JOIN gemapi.digues d ON d.id=z.digue_id
    UNION ALL
    SELECT p.id, 'mobilier', z.id, z.designation, coalesce(z.site_terrestre_id,z.troncon_id,z.ah_ouvrage_id,z.ah_stockage_id,z.ah_section_id), s.nom, z.geometry
    FROM gemapi.prestations p JOIN gemapi.mobilier z ON z.id=p.mobilier_id LEFT JOIN gemapi.sites_terrestres s ON s.id=z.site_terrestre_id
    UNION ALL
    SELECT p.id, 'cheminement', z.id, z.designation, coalesce(z.site_terrestre_id,z.troncon_id,z.ah_ouvrage_id,z.ah_stockage_id,z.ah_section_id), s.nom, z.geometry
    FROM gemapi.prestations p JOIN gemapi.cheminements z ON z.id=p.cheminement_id LEFT JOIN gemapi.sites_terrestres s ON s.id=z.site_terrestre_id
    UNION ALL
    SELECT p.id, 'vegetation', z.id, z.designation, coalesce(z.site_terrestre_id,z.troncon_id,z.ah_ouvrage_id,z.ah_stockage_id,z.ah_section_id), s.nom, z.geometry
    FROM gemapi.prestations p JOIN gemapi.vegetation z ON z.id=p.vegetation_id LEFT JOIN gemapi.sites_terrestres s ON s.id=z.site_terrestre_id
),
candidats AS (
    SELECT 'cours_eau'::text famille, id patrimoine_id, nom patrimoine_nom,
           NULL::uuid parent_id, NULL::text parent_nom, geom FROM gemapi.cours_eau
    UNION ALL
    SELECT 'digue', d.id, d.nom, NULL::uuid, NULL::text, r.geom_reference
      FROM gemapi.digues d JOIN gemapi.digues_reperes r ON r.digue_id=d.id
    UNION ALL
    SELECT 'ah_sections', h.id, c.nom, a.id, a.nom, h.geom_reference
      FROM gemapi.ah_sections_carte h JOIN gemapi.cours_eau c ON c.id=h.cours_eau_id
      JOIN gemapi.ah a ON a.id=h.ah_id
      WHERE h.etat_reference='CONFORME' AND h.cours_eau_qualifie IS TRUE
    UNION ALL
    SELECT 'ah_stockage', s.id, s.nom, a.id, a.nom, s.geom
      FROM gemapi.ah_stockage s JOIN gemapi.ah a ON a.id=s.ah_id
    UNION ALL
    SELECT 'ah_ouvrage', o.id, o.nom, a.id, a.nom, o.geom
      FROM gemapi.ah_ouvrage o JOIN gemapi.ah a ON a.id=o.ah_id
    UNION ALL
    SELECT 'site_terrestre', id, nom, NULL::uuid, NULL::text, geom
      FROM gemapi.sites_terrestres
    UNION ALL
    SELECT 'troncon', z.id, z.designation, d.id, d.nom, z.geometry FROM gemapi.troncons z
    JOIN gemapi.digues d ON d.id=z.digue_id
    UNION ALL
    SELECT 'mobilier', z.id, z.designation, coalesce(z.site_terrestre_id,z.troncon_id,z.ah_ouvrage_id,z.ah_stockage_id,z.ah_section_id), s.nom, z.geometry
    FROM gemapi.mobilier z LEFT JOIN gemapi.sites_terrestres s ON s.id=z.site_terrestre_id
    UNION ALL
    SELECT 'cheminement', z.id, z.designation, coalesce(z.site_terrestre_id,z.troncon_id,z.ah_ouvrage_id,z.ah_stockage_id,z.ah_section_id), s.nom, z.geometry
    FROM gemapi.cheminements z LEFT JOIN gemapi.sites_terrestres s ON s.id=z.site_terrestre_id
    UNION ALL
    SELECT 'vegetation', z.id, z.designation, coalesce(z.site_terrestre_id,z.troncon_id,z.ah_ouvrage_id,z.ah_stockage_id,z.ah_section_id), s.nom, z.geometry
    FROM gemapi.vegetation z LEFT JOIN gemapi.sites_terrestres s ON s.id=z.site_terrestre_id
),
base AS (
    SELECT p.*, tp.libelle AS type_prestation, r.famille,
           r.patrimoine_id, r.patrimoine_nom, r.parent_id, r.parent_nom,
           r.geom AS patrimoine_geom
      FROM gemapi.prestations p
      JOIN gemapi.types_prestations tp ON tp.id=p.type_prestation_id
      LEFT JOIN references_patrimoniales r ON r.prestation_id=p.id
)
SELECT b.origine_mesures, ST_GeometryType(carte.geom) AS type_effectif, b.id, b.intitule, b.type_prestation, b.famille,
       b.patrimoine_id, b.patrimoine_nom, b.parent_id, b.parent_nom,
       CASE WHEN b.geom IS NULL THEN NULL ELSE ST_GeometryType(b.geom) END AS type_geometrie,
       b.geom IS NOT NULL AS geometrie_presente,
       CASE WHEN b.geom IS NULL THEN NULL ELSE ST_XMin(ST_Envelope(b.geom)) END AS xmin,
       CASE WHEN b.geom IS NULL THEN NULL ELSE ST_YMin(ST_Envelope(b.geom)) END AS ymin,
       CASE WHEN b.geom IS NULL THEN NULL ELSE ST_XMax(ST_Envelope(b.geom)) END AS xmax,
       CASE WHEN b.geom IS NULL THEN NULL ELSE ST_YMax(ST_Envelope(b.geom)) END AS ymax,
       CASE WHEN b.geom IS NULL OR b.patrimoine_geom IS NULL THEN NULL
            ELSE ST_Distance(b.geom,b.patrimoine_geom) END AS distance_patrimoine_m,
       CASE WHEN b.geom IS NULL OR b.patrimoine_geom IS NULL THEN NULL
            ELSE ST_Intersects(b.geom,b.patrimoine_geom) END AS intersecte_patrimoine,
       CASE WHEN b.geom IS NULL THEN 0 ELSE
            (SELECT count(*) FROM candidats c WHERE ST_DWithin(b.geom,c.geom,25)) END
            AS patrimoines_dans_25m,
       CASE WHEN b.geom IS NULL THEN 0 ELSE
            (SELECT count(*) FROM candidats c WHERE ST_DWithin(b.geom,c.geom,50)) END
            AS patrimoines_dans_50m,
       alt.famille AS alternative_famille, alt.patrimoine_id AS alternative_id,
       alt.patrimoine_nom AS alternative_nom, alt.parent_nom AS alternative_parent_nom,
       alt.distance_m AS alternative_distance_m,
       CASE WHEN b.geom IS NULL THEN 0 ELSE
            count(*) OVER (PARTITION BY encode(ST_AsEWKB(b.geom),'hex')) END
            AS geometries_strictement_identiques,
       ST_AsBinary(b.geom) AS prestation_wkb,
       CASE WHEN b.geom IS NULL OR b.patrimoine_geom IS NULL THEN NULL
            ELSE ST_AsBinary(ST_ShortestLine(b.geom,b.patrimoine_geom)) END AS liaison_wkb,
       alt.liaison_wkb AS alternative_liaison_wkb
  FROM base b JOIN gemapi.prestations_carte carte ON carte.id=b.id
  LEFT JOIN LATERAL (
      SELECT c.famille, c.patrimoine_id, c.patrimoine_nom, c.parent_nom,
             ST_Distance(b.geom,c.geom) AS distance_m,
             ST_AsBinary(ST_ShortestLine(b.geom,c.geom)) AS liaison_wkb
        FROM candidats c
       WHERE b.geom IS NOT NULL
         AND (c.famille,c.patrimoine_id) IS DISTINCT FROM (b.famille,b.patrimoine_id)
       ORDER BY ST_Distance(b.geom,c.geom), c.famille, c.patrimoine_id
       LIMIT 1
  ) alt ON true
 ORDER BY b.id;
"""


CLUSTERS_SQL = r"""
WITH rayons(rayon_m) AS (VALUES (5.0),(10.0),(25.0),(50.0)),
affectations AS (
    SELECT r.rayon_m, p.id,
           CASE WHEN p.cours_eau_id IS NOT NULL THEN 'cours_eau'
                WHEN p.digue_id IS NOT NULL THEN 'digue'
                WHEN p.ah_section_id IS NOT NULL THEN 'ah_sections'
                WHEN p.ah_stockage_id IS NOT NULL THEN 'ah_stockage'
                WHEN p.ah_ouvrage_id IS NOT NULL THEN 'ah_ouvrage'
                WHEN p.site_terrestre_id IS NOT NULL THEN 'site_terrestre'
                WHEN p.troncon_id IS NOT NULL THEN 'troncon'
                WHEN p.mobilier_id IS NOT NULL THEN 'mobilier'
                WHEN p.cheminement_id IS NOT NULL THEN 'cheminement'
                WHEN p.vegetation_id IS NOT NULL THEN 'vegetation' END AS famille,
           coalesce(p.cours_eau_id,p.digue_id,p.ah_stockage_id,p.ah_ouvrage_id,
                    p.site_terrestre_id,p.troncon_id,p.mobilier_id,p.cheminement_id,p.vegetation_id,p.ah_section_id) AS patrimoine_id,
           p.geom,
           ST_ClusterDBSCAN(p.geom, eps := r.rayon_m, minpoints := 2)
               OVER (PARTITION BY r.rayon_m) AS cluster_id
      FROM rayons r CROSS JOIN gemapi.prestations p
     WHERE p.geom IS NOT NULL
)
SELECT rayon_m, cluster_id, count(*) AS nombre_prestations,
       count(DISTINCT patrimoine_id) AS patrimoines_distincts,
       count(DISTINCT famille) AS familles_distinctes,
       count(*)-count(DISTINCT encode(ST_AsEWKB(geom),'hex')) AS doublons_geometrie_excedentaires,
       array_agg(id::text ORDER BY id) AS prestation_ids,
       array_agg(DISTINCT famille ORDER BY famille) AS familles,
       ST_AsBinary(ST_Buffer(ST_ConvexHull(ST_Collect(geom)),greatest(rayon_m/4.0,1.0))) AS cluster_wkb
  FROM affectations
 WHERE cluster_id IS NOT NULL
 GROUP BY rayon_m,cluster_id
 ORDER BY rayon_m,cluster_id;
"""


def bytes_value(value):
    return bytes(value) if value is not None else None


def severity(row):
    """Return (level, label, reason) using diagnostic, non-regulatory bands."""
    distance = row["distance_patrimoine_m"]
    family = row["famille"]
    if not row["geometrie_presente"]:
        if family in {"ah_stockage", "ah_ouvrage", "vegetation", "mobilier", "cheminement"}:
            return 0, "sans_geometrie_autorisee", "Géométrie propre facultative pour ce rattachement"
        return 4, "critique", "Géométrie obligatoire absente"
    if distance is None:
        return 4, "critique", "Patrimoine déclaré ou géométrie de référence introuvable"
    alternative = row["alternative_distance_m"]
    alternative_nette = (alternative is not None and distance > 50
                          and alternative + 25 < distance
                          and alternative < distance * 0.5)
    if distance > 500:
        return 4, "critique", "Distance au patrimoine supérieure à 500 m"
    if distance > 100 or alternative_nette:
        reason = "Distance au patrimoine supérieure à 100 m"
        if alternative_nette:
            reason += "; un autre patrimoine est nettement plus proche"
        return 3, "forte", reason
    if distance > 50:
        return 2, "moderee", "Distance au patrimoine comprise entre 50 et 100 m"
    if distance > 25:
        return 1, "vigilance", "Distance au patrimoine comprise entre 25 et 50 m"
    return 0, "coherente", "Distance au patrimoine inférieure ou égale à 25 m"


def create_layer(ds, name, geometry_type, fields):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3950)
    layer = ds.CreateLayer(name, srs, geometry_type)
    for field_name, field_type in fields:
        definition = ogr.FieldDefn(field_name, field_type)
        layer.CreateField(definition)
    return layer


def set_feature(layer, values, geometry_wkb):
    feature = ogr.Feature(layer.GetLayerDefn())
    for key, value in values.items():
        if value is not None:
            if isinstance(value, Decimal):
                value = float(value)
            feature.SetField(key, value)
    if geometry_wkb:
        feature.SetGeometry(ogr.CreateGeometryFromWkb(bytes_value(geometry_wkb)))
    if layer.CreateFeature(feature) != ogr.OGRERR_NONE:
        raise RuntimeError(f"Écriture impossible dans {layer.GetName()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("audits"))
    parser.add_argument("--schema", default="gemapi")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ogr.UseExceptions()

    connection = psycopg2.connect("")
    connection.set_session(readonly=True, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout='5min'")
            cursor.execute(trusted_path(args.schema))
            cursor.execute(render(INVENTORY_SQL, args.schema))
            columns = [item.name for item in cursor.description]
            inventory = [dict(zip(columns, row)) for row in cursor.fetchall()]
            cursor.execute(render(CLUSTERS_SQL, args.schema))
            cluster_columns = [item.name for item in cursor.description]
            clusters = [dict(zip(cluster_columns, row)) for row in cursor.fetchall()]
        connection.rollback()
    finally:
        connection.close()

    if len(inventory) != 100 or len({str(row["id"]) for row in inventory}) != 100:
        raise RuntimeError("Le jeu de démonstration doit contenir exactement 100 prestations")

    by_id = {str(row["id"]): row for row in inventory}
    anomalies = []
    for row in inventory:
        level, label, reason = severity(row)
        row["niveau_gravite"] = level
        row["gravite"] = label
        row["motif"] = reason
        row["alternative_nettement_plus_proche"] = bool(
            row["alternative_distance_m"] is not None
            and row["distance_patrimoine_m"] is not None
            and row["distance_patrimoine_m"] > 50
            and row["alternative_distance_m"] + 25 < row["distance_patrimoine_m"]
            and row["alternative_distance_m"] < row["distance_patrimoine_m"] * 0.5)
        row["ambiguite_25m"] = row["geometrie_presente"] and row["patrimoines_dans_25m"] > 1
        if level > 0 or row["ambiguite_25m"]:
            anomalies.append(row)

    anomaly_fields = [
        "id", "intitule", "type_prestation", "famille", "patrimoine_id",
        "patrimoine_nom", "parent_id", "parent_nom", "type_geometrie",
        "origine_mesures", "type_effectif", "geometrie_presente", "distance_patrimoine_m", "intersecte_patrimoine",
        "patrimoines_dans_25m", "patrimoines_dans_50m",
        "alternative_famille", "alternative_id", "alternative_nom",
        "alternative_parent_nom", "alternative_distance_m",
        "alternative_nettement_plus_proche", "ambiguite_25m",
        "geometries_strictement_identiques", "gravite", "niveau_gravite", "motif",
    ]
    anomaly_path = args.output_dir / "prestations_spatial_anomalies.csv"
    with anomaly_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, anomaly_fields)
        writer.writeheader()
        for row in anomalies:
            writer.writerow({key: row.get(key) for key in anomaly_fields})

    cluster_fields = [
        "rayon_m", "cluster_id", "nombre_prestations", "patrimoines_distincts",
        "familles_distinctes", "doublons_geometrie_excedentaires", "distance_max_m",
        "distance_moyenne_m", "familles", "prestation_ids", "cluster_suspect",
    ]
    for cluster in clusters:
        ids = [str(value) for value in cluster["prestation_ids"]]
        distances = [float(by_id[value]["distance_patrimoine_m"])
                     for value in ids if by_id[value]["distance_patrimoine_m"] is not None]
        cluster["distance_max_m"] = max(distances) if distances else None
        cluster["distance_moyenne_m"] = sum(distances) / len(distances) if distances else None
        cluster["familles"] = ";".join(cluster["familles"])
        cluster["prestation_ids"] = ";".join(ids)
        cluster["cluster_suspect"] = bool(
            cluster["nombre_prestations"] >= 5
            or cluster["patrimoines_distincts"] >= 3
            or cluster["doublons_geometrie_excedentaires"] > 0)
    cluster_path = args.output_dir / "prestations_spatial_clusters.csv"
    with cluster_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, cluster_fields)
        writer.writeheader()
        for row in clusters:
            writer.writerow({key: row.get(key) for key in cluster_fields})

    gpkg_path = args.output_dir / "prestations_spatial_diagnostic.gpkg"
    if gpkg_path.exists():
        gpkg_path.unlink()
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(gpkg_path))
    suspect_layer = create_layer(ds, "prestations_suspectes", ogr.wkbUnknown, [
        ("uuid", ogr.OFTString), ("famille", ogr.OFTString),
        ("patrimoine", ogr.OFTString), ("distance_m", ogr.OFTReal),
        ("gravite", ogr.OFTString), ("motif", ogr.OFTString),
    ])
    link_layer = create_layer(ds, "liaisons_patrimoine", ogr.wkbLineString, [
        ("uuid", ogr.OFTString), ("patrimoine_id", ogr.OFTString),
        ("distance_m", ogr.OFTReal), ("gravite", ogr.OFTString),
    ])
    alternative_layer = create_layer(ds, "patrimoines_alternatifs", ogr.wkbLineString, [
        ("uuid", ogr.OFTString), ("alternative_id", ogr.OFTString),
        ("alternative", ogr.OFTString), ("distance_m", ogr.OFTReal),
    ])
    cluster_layer = create_layer(ds, "clusters_suspects", ogr.wkbPolygon, [
        ("rayon_m", ogr.OFTReal), ("cluster_id", ogr.OFTInteger),
        ("nombre", ogr.OFTInteger), ("patrimoines", ogr.OFTInteger),
        ("familles", ogr.OFTString), ("uuids", ogr.OFTString),
    ])
    for row in anomalies:
        if row["prestation_wkb"]:
            set_feature(suspect_layer, {
                "uuid": str(row["id"]), "famille": row["famille"],
                "patrimoine": row["patrimoine_nom"],
                "distance_m": row["distance_patrimoine_m"], "gravite": row["gravite"],
                "motif": row["motif"],
            }, row["prestation_wkb"])
        if (row["niveau_gravite"] > 0 and row["distance_patrimoine_m"]
                and row["liaison_wkb"]):
            set_feature(link_layer, {
                "uuid": str(row["id"]), "patrimoine_id": str(row["patrimoine_id"]),
                "distance_m": row["distance_patrimoine_m"], "gravite": row["gravite"],
            }, row["liaison_wkb"])
        if row["alternative_nettement_plus_proche"] and row["alternative_liaison_wkb"]:
            set_feature(alternative_layer, {
                "uuid": str(row["id"]), "alternative_id": str(row["alternative_id"]),
                "alternative": row["alternative_nom"],
                "distance_m": row["alternative_distance_m"],
            }, row["alternative_liaison_wkb"])
    for row in clusters:
        if row["cluster_suspect"]:
            set_feature(cluster_layer, {
                "rayon_m": row["rayon_m"], "cluster_id": row["cluster_id"],
                "nombre": row["nombre_prestations"],
                "patrimoines": row["patrimoines_distincts"],
                "familles": row["familles"], "uuids": row["prestation_ids"],
            }, row["cluster_wkb"])
    ds = None

    distance_bands = Counter()
    for row in inventory:
        value = row["distance_patrimoine_m"]
        if value is None:
            distance_bands["sans_geometrie"] += 1
        elif value <= 10:
            distance_bands["0-10"] += 1
        elif value <= 25:
            distance_bands["10-25"] += 1
        elif value <= 50:
            distance_bands["25-50"] += 1
        elif value <= 100:
            distance_bands["50-100"] += 1
        elif value <= 500:
            distance_bands["100-500"] += 1
        else:
            distance_bands[">500"] += 1
    cluster_stats = {}
    all_suspect_members = set()
    for radius in (5.0, 10.0, 25.0, 50.0):
        selected = [row for row in clusters if float(row["rayon_m"]) == radius]
        members = set()
        suspect_members = set()
        for row in selected:
            row_members = set(row["prestation_ids"].split(";"))
            members.update(row_members)
            if row["cluster_suspect"]:
                suspect_members.update(row_members)
                all_suspect_members.update(row_members)
        cluster_stats[str(int(radius))] = {
            "nombre_clusters": len(selected),
            "taille_maximale": max((row["nombre_prestations"] for row in selected), default=0),
            "prestations_membres": len(members),
            "prestations_clusters_suspects": len(suspect_members),
        }
    summary = {
        "prestations": len(inventory),
        "origines_mesures": dict(Counter(row["origine_mesures"] for row in inventory)),
        "types_effectifs": dict(Counter(row["type_effectif"] or "NULL" for row in inventory)),
        "geometries_presentes": sum(row["geometrie_presente"] for row in inventory),
        "types_geometrie": dict(Counter(row["type_geometrie"] or "NULL" for row in inventory)),
        "familles": dict(Counter(row["famille"] for row in inventory)),
        "tranches_distance": dict(distance_bands),
        "coherentes_25m": sum(row["geometrie_presente"] and row["distance_patrimoine_m"] is not None
                              and row["distance_patrimoine_m"] <= 25 for row in inventory),
        "sans_geometrie_autorisee": sum(row["gravite"] == "sans_geometrie_autorisee" for row in inventory),
        "incoherences_fortes": sum(row["niveau_gravite"] >= 3 for row in inventory),
        "alternatives_nettement_plus_proches": sum(row["alternative_nettement_plus_proche"] for row in inventory),
        "ambiguite_25m": sum(row["ambiguite_25m"] for row in inventory),
        "geometries_dupliquees": sum(row["geometries_strictement_identiques"] > 1 for row in inventory),
        "clusters": len(clusters),
        "clusters_suspects": sum(row["cluster_suspect"] for row in clusters),
        "prestations_dans_clusters_suspects": len(all_suspect_members),
        "clusters_par_rayon": cluster_stats,
        "anomalies_csv": len(anomalies),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
