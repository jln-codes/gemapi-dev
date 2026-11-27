"""Génération de GeoPackages depuis un résultat complet du moteur de vues."""

from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from .troncons import FIELDS as TRONCON_FIELDS, json_object


def attach_native_troncons(cursor: Any, rows: list[dict[str, Any]]) -> None:
    """Enrich only server-selected canonical identities in the export snapshot.

    An explicit business-field allowlist excludes attribution and migration
    evidence. EWKB keeps SRID/Z/M and curves exactly; it is never a made-up
    cartographic representation. This data is not sent by the normal view API.
    """
    entities = [entity for row in rows for entity in row.get("entities", [])
                if entity.get("class") == "troncons"]
    identifiers = sorted({str(entity["id"]) for entity in entities})
    if not identifiers:
        return
    attributes = json_object([(field, "t." + field) for field in TRONCON_FIELDS])
    cursor.execute("SELECT t.id::text, " + attributes + ", "
                   "encode(ST_AsEWKB(t.geometry), 'hex'), "
                   "encode(ST_AsEWKB(t.geometry_reperage), 'hex') "
                   "FROM public.troncons t WHERE t.id = ANY(%s::uuid[])", (identifiers,))
    native = {identifier: (values, geometry, axis) for identifier, values, geometry, axis in cursor.fetchall()}
    if set(native) != set(identifiers):
        raise DataGeoPackageError("Un tronçon de l’export n’est plus accessible.")
    for entity in entities:
        values, geometry, axis = native[str(entity["id"])]
        entity["_native"] = {"attributes": values, "geometry_ewkb": geometry,
                             "geometry_reperage_ewkb": axis}

class DataGeoPackageError(ValueError):
    """Le résultat affiché ne peut pas produire de couche GeoPackage."""


LAYER_SUFFIXES = {
    "point": "points",
    "line": "lignes",
    "polygon": "polygones",
}


def _field_name(value: str, used: set[str]) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", normalized).strip("_").lower()
    base = (base or "champ")[:55]
    candidate = base
    suffix = 2
    while candidate in used or candidate == "id_objet":
        candidate = f"{base[:51]}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _display_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _normalized_geometry(ogr: Any, value: dict[str, Any] | None) -> tuple[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        geometry = ogr.CreateGeometryFromJson(json.dumps(value))
    except Exception:
        return None
    if geometry is None or geometry.IsEmpty() or not geometry.IsValid():
        return None

    geometry_type = ogr.GT_Flatten(geometry.GetGeometryType())
    definitions = (
        ("point", ogr.wkbPoint, ogr.wkbMultiPoint),
        ("line", ogr.wkbLineString, ogr.wkbMultiLineString),
        ("polygon", ogr.wkbPolygon, ogr.wkbMultiPolygon),
    )
    for family, single_type, multi_type in definitions:
        if geometry_type == single_type:
            multi = ogr.Geometry(multi_type)
            multi.AddGeometry(geometry)
            return family, multi
        if geometry_type == multi_type:
            return family, geometry
    return None


def build_geopackage(
    result: dict[str, Any], entity_classes: list[str]
) -> bytes:
    """Construit les couches géographiques et la table exacte des lignes de vue."""

    try:
        from osgeo import ogr, osr
    except ImportError as exc:  # pragma: no cover - dépend du runtime système.
        raise DataGeoPackageError("Export GeoPackage indisponible sur ce serveur.") from exc
    ogr.UseExceptions()
    osr.UseExceptions()

    columns = result.get("columns") or []
    rows = result.get("rows") or []
    included_classes = set(entity_classes)
    entities: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        for entity in row.get("entities") or []:
            entity_class = entity.get("class")
            entity_id = str(entity.get("id") or "")
            if entity_class not in included_classes or not entity_id:
                continue
            key = (entity_class, entity_id)
            stored = entities.setdefault(key, {
                "attributes": {}, "geometry": None, "family": None,
                "native": entity.get("_native"),
            })
            stored["attributes"].update({
                name: value for name, value in (entity.get("attributes") or {}).items()
                if value is not None
            })
            normalized = _normalized_geometry(ogr, entity.get("geometry"))
            if normalized is not None and stored["geometry"] is None:
                stored["family"], stored["geometry"] = normalized
    geographic_entities = [
        (entity_class, entity_id, stored)
        for (entity_class, entity_id), stored in entities.items()
        if stored["geometry"] is not None
    ]
    native_entities = [(kind, identifier, value) for (kind, identifier), value in entities.items()
                       if value.get("native")]
    if not geographic_entities and not any(value["native"].get("geometry_ewkb") for _, _, value in native_entities):
        raise DataGeoPackageError("Le résultat affiché ne contient aucune géométrie exploitable.")

    columns_by_class = {
        entity_class: [
            column for column in columns
            if column.get("source_class") == entity_class
        ]
        for entity_class in included_classes
    }
    spatial_reference = osr.SpatialReference()
    spatial_reference.ImportFromEPSG(4326)
    spatial_reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    driver = ogr.GetDriverByName("GPKG")
    if driver is None:  # pragma: no cover - installation GDAL incomplète.
        raise DataGeoPackageError("Pilote GeoPackage indisponible sur ce serveur.")

    with tempfile.TemporaryDirectory(prefix="digues-app-data-gpkg-") as directory:
        path = Path(directory) / "digues-donnees.gpkg"
        dataset = driver.CreateDataSource(str(path))
        if dataset is None:  # pragma: no cover - erreur native GDAL.
            raise DataGeoPackageError("Création du GeoPackage impossible.")
        layers = {}
        try:
            if native_entities:
                inventory = dataset.CreateLayer("troncons_inventaire", geom_type=ogr.wkbNone)
                for name in ("id_objet", "attributs", "qualifications_geometrie", "geometry_ewkb", "geometry_reperage_ewkb"):
                    inventory.CreateField(ogr.FieldDefn(name, ogr.OFTString))
                for _kind, identifier, stored in native_entities:
                    feature = ogr.Feature(inventory.GetLayerDefn())
                    feature.SetField("id_objet", identifier)
                    feature.SetField("attributs", json.dumps(stored["native"]["attributes"], ensure_ascii=False))
                    feature.SetField("qualifications_geometrie", json.dumps({
                        key: value for key, value in stored["attributes"].items() if key.startswith("geometry_")
                    }, ensure_ascii=False))
                    for name in ("geometry_ewkb", "geometry_reperage_ewkb"):
                        if stored["native"].get(name) is not None:
                            feature.SetField(name, stored["native"][name])
                    if inventory.CreateFeature(feature) != 0:
                        raise DataGeoPackageError("Écriture de l’inventaire des tronçons impossible.")
                    feature = None
                inventory = None
            ogr_geometry_types = {
                "point": ogr.wkbMultiPoint,
                "line": ogr.wkbMultiLineString,
                "polygon": ogr.wkbMultiPolygon,
            }
            layer_keys = sorted({
                (entity_class, stored["family"])
                for entity_class, _entity_id, stored in geographic_entities
            })
            for entity_class, family in layer_keys:
                layer_name = f"{entity_class}_{LAYER_SUFFIXES[family]}"
                family_entities = [
                    (entity_id, stored)
                    for row_class, entity_id, stored in geographic_entities
                    if row_class == entity_class and stored["family"] == family
                ]
                layer = dataset.CreateLayer(
                    layer_name, spatial_reference, ogr_geometry_types[family],
                )
                if layer is None:
                    raise DataGeoPackageError(f"Création de la couche {layer_name} impossible.")
                layer.CreateField(ogr.FieldDefn("id_objet", ogr.OFTString))
                used_names: set[str] = set()
                field_definitions = [
                    (column, _field_name(column["source_field"], used_names))
                    for column in columns_by_class.get(entity_class, [])
                ]
                for _column, field_name in field_definitions:
                    layer.CreateField(ogr.FieldDefn(field_name, ogr.OFTString))
                layers[layer_name] = layer
                for entity_id, stored in family_entities:
                    feature = ogr.Feature(layer.GetLayerDefn())
                    feature.SetField("id_objet", entity_id)
                    for column, field_name in field_definitions:
                        value = stored["attributes"].get(column["source_field"])
                        display_value = _display_value(value)
                        if display_value is not None:
                            feature.SetField(field_name, display_value)
                    feature.SetGeometry(stored["geometry"])
                    if layer.CreateFeature(feature) != 0:
                        raise DataGeoPackageError("Écriture d’un objet dans le GeoPackage impossible.")
                    feature = None

            # Cette table non spatiale conserve une ligne par granularité de la
            # vue. Elle maintient notamment les associations exactes
            # Photo -> Observation -> Désordre, sans agréger les descendants.
            row_layer = dataset.CreateLayer("vue_resultats", geom_type=ogr.wkbNone)
            if row_layer is None:
                raise DataGeoPackageError("Création de la table des résultats impossible.")
            row_layer.CreateField(ogr.FieldDefn("ligne", ogr.OFTInteger64))
            row_layer.CreateField(ogr.FieldDefn("racine", ogr.OFTString))
            row_layer.CreateField(ogr.FieldDefn("granularite", ogr.OFTString))
            row_field_names: set[str] = {"ligne", "racine", "granularite"}
            row_fields = [
                (column, _field_name(column.get("key") or column.get("label") or "champ", row_field_names))
                for column in columns
            ]
            for _column, field_name in row_fields:
                row_layer.CreateField(ogr.FieldDefn(field_name, ogr.OFTString))
            identity_fields = {}
            for entity_class in (
                "systemes", "digues", "troncons", "desordres", "observations", "photos"
            ):
                field_name = _field_name(f"{entity_class}_ids", row_field_names)
                identity_fields[entity_class] = field_name
                row_layer.CreateField(ogr.FieldDefn(field_name, ogr.OFTString))
            for row_index, row in enumerate(rows, start=1):
                feature = ogr.Feature(row_layer.GetLayerDefn())
                feature.SetField("ligne", row_index)
                feature.SetField("racine", str(result.get("root") or ""))
                feature.SetField("granularite", str(result.get("granularity") or ""))
                for value, (_column, field_name) in zip(
                    row.get("values") or [], row_fields, strict=False
                ):
                    display_value = _display_value(value)
                    if display_value is not None:
                        feature.SetField(field_name, display_value)
                identities = {entity_class: [] for entity_class in identity_fields}
                for entity in row.get("entities") or []:
                    entity_class = entity.get("class")
                    identifier = entity.get("id")
                    if entity_class in identities and identifier is not None:
                        value = str(identifier)
                        if value not in identities[entity_class]:
                            identities[entity_class].append(value)
                for entity_class, identifiers in identities.items():
                    if identifiers:
                        feature.SetField(
                            identity_fields[entity_class],
                            json.dumps(identifiers, ensure_ascii=False, separators=(",", ":")),
                        )
                if row_layer.CreateFeature(feature) != 0:
                    raise DataGeoPackageError("Écriture d’une ligne de vue impossible.")
                feature = None
            layers["vue_resultats"] = row_layer
        finally:
            layers.clear()
            dataset = None
        return path.read_bytes()
