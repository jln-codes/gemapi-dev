"""Requêtes GeoJSON en lecture seule pour la carte expérimentale."""

from __future__ import annotations

import base64
import json
import logging
import math
import psycopg
from psycopg import sql
from fastapi import HTTPException
from typing import Any
from uuid import UUID

from digues_app.crs import BusinessCRS, BusinessCRSError, qualified_business_crs

from .database import WebDatabaseError, read_business_crs
from .object_disorder_relations import (
    RELATION_UNSET,
    ObjectDisorderRelationConfig,
    ObjectDisorderRelationConflictError,
    ObjectDisorderRelationPlan,
    ObjectDisorderRelationValidationError,
    apply_object_disorder_update,
    inverse_object_disorder_relations,
    object_disorder_relation,
    plan_object_disorder_update,
    read_object_disorder_ids,
)
from .models import (
    PrestationCreate,
    PrestationUpdate,
    AmenagementHydrauliqueCreate,
    AmenagementHydrauliqueUpdate,
    ZoneStockageCreate,
    ZoneStockageUpdate,
    VegetationCreate,
    VegetationUpdate,
    CheminementCreate,
    CheminementUpdate,
    cheminement_geometry_types,
    DesordreCreate,
    DigueCreate,
    DigueUpdate,
    EquipementMesureCreate,
    EquipementMesureUpdate,
    LineEndpoints,
    DesordreGeometryUpdate,
    MobilierCreate,
    MobilierUpdate,
    ObservationCreate,
    ObservationParent,
    ObservationUpdate,
    OuvrageParticulierCreate,
    OuvrageParticulierUpdate,
    PointDesordreUpdate,
    PointReperageUpdate,
    BornageGeometryQuery,
    ReseauTechniqueCreate,
    ReseauTechniqueUpdate,
    SystemeEndiguementCreate,
    SystemeEndiguementUpdate,
    TronconCreate,
    TronconUpdate,
)


LOGGER = logging.getLogger(__name__)

MOBILIER_DISORDER_RELATION = object_disorder_relation("mobilier")
EQUIPEMENT_MESURE_DISORDER_RELATION = object_disorder_relation(
    "equipements-mesure"
)
RESEAU_TECHNIQUE_DISORDER_RELATION = object_disorder_relation(
    "reseaux-techniques"
)
VEGETATION_DISORDER_RELATION = object_disorder_relation("vegetation")
AMENAGEMENT_HYDRAULIQUE_DISORDER_RELATION = object_disorder_relation(
    "amenagements-hydrauliques"
)

DESORDRE_SEARCH_DEFAULT_LIMIT = 50
DESORDRE_SEARCH_MAX_LIMIT = 100
DESORDRE_SELECTION_MAX_IDS = 100
DESORDRE_ASSOCIATED_OBJECTS_DEFAULT_LIMIT = 50
DESORDRE_ASSOCIATED_OBJECTS_MAX_LIMIT = 100


class DesordreSearchCursorError(ValueError):
    """Le curseur opaque ne correspond pas au contrat de recherche."""


class DesordreAssociatedObjectsCursorError(ValueError):
    """Le curseur opaque ne correspond pas à la projection inverse."""


def _plan_resource_disorders(
    cursor: Any,
    config: ObjectDisorderRelationConfig,
    identifier: UUID,
    requested: object,
    error_type: type[ValueError],
) -> ObjectDisorderRelationPlan | None:
    """Traduit le contrat partagé dans l'erreur métier de la ressource."""

    try:
        return plan_object_disorder_update(
            cursor, config, identifier, requested,
        )
    except ObjectDisorderRelationConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "disorder-already-associated",
                "message": str(exc),
            },
        ) from exc
    except ObjectDisorderRelationValidationError as exc:
        raise error_type(str(exc)) from exc


def _add_disorder_ids(
    feature: dict[str, Any],
    cursor: Any,
    config: ObjectDisorderRelationConfig,
    identifier: UUID,
) -> dict[str, Any]:
    """Enrichit seulement le détail, sans alourdir les collections GeoJSON."""

    feature["properties"]["desordre_ids"] = [
        str(value) for value in read_object_disorder_ids(
            cursor, config, identifier,
        )
    ]
    return feature


def _validated_lonlat(longitude: float, latitude: float) -> None:
    if (not math.isfinite(longitude) or not math.isfinite(latitude)
            or not -180 <= longitude <= 180 or not -90 <= latitude <= 90):
        raise HTTPException(status_code=422, detail="Longitude/latitude EPSG:4326 invalides.")


def _validated_required_geometry(
    cursor: Any, geometry: Any, business_srid: int,
    allowed_types: set[str], error_type: type[ValueError], label: str,
) -> str:
    """Applique au candidat transformé le même prédicat que le DDL GEO-1."""
    if geometry is None:
        raise error_type(f"La géométrie du {label} est obligatoire.")
    value = json.dumps(geometry.model_dump(mode="json"), allow_nan=False)
    cursor.execute(
        "SELECT GeometryType(candidate.g), "
        "public.geometrie_metier_simple_valide(candidate.g) "
        "FROM (SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), "
        "%s) AS g) AS candidate",
        (value, business_srid),
    )
    row = cursor.fetchone()
    if row is None or row[0] not in allowed_types or row[1] is not True:
        raise error_type(
            f"La géométrie du {label} doit être valide, non vide, "
            "non dégénérée et de type autorisé."
        )
    return value


def _geometry_integrity_error(
    exc: Exception, error_type: type[ValueError], label: str,
) -> ValueError | None:
    """Traduit uniquement les refus GEO-1 connus, jamais une panne SQL inconnue."""
    diagnostic = getattr(exc, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    column = getattr(diagnostic, "column_name", None)
    if constraint in {"ck_geometrie_metier_valide", "ck_type_geometrie"} or (
        getattr(exc, "sqlstate", None) == "23502" and column == "geometry"
    ):
        return error_type(
            f"La géométrie du {label} est obligatoire, valide, non vide "
            "et de type autorisé."
        )
    if label == "Végétation" and getattr(exc, "sqlstate", None) == "23514":
        primary = getattr(diagnostic, "message_primary", "") or ""
        if primary.startswith("Géométrie ") and "incompatible avec la nature de végétation" in primary:
            return error_type("La géométrie est incompatible avec la nature de Végétation.")
    if label == "Désordre" and getattr(exc, "sqlstate", None) == "P0001":
        primary = getattr(diagnostic, "message_primary", "") or ""
        if primary.startswith("Repérage de début invalide"):
            return error_type("Le bornage de début ne permet pas de localiser le Désordre.")
        if primary.startswith("Repérage de fin invalide"):
            return error_type("Le bornage de fin ne permet pas de localiser le Désordre.")
        if primary == "Le début et la fin ne peuvent pas définir une ligne nulle.":
            return error_type("Le bornage produit une ligne de longueur nulle.")
    return None


def _raise_unexpected_sql_error(exc: Exception) -> None:
    if isinstance(exc, psycopg.Error):
        raise WebDatabaseError("La mutation SQL a échoué.") from exc


def _endpoint_source_srid(crs_code: str, business_crs: BusinessCRS) -> int:
    """Valide le CRS explicite d'extrémités avant de construire leur géométrie."""

    if crs_code == "EPSG:4326":
        return 4326
    try:
        source_crs = qualified_business_crs(crs_code)
    except BusinessCRSError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if source_crs != business_crs:
        raise HTTPException(
            status_code=422,
            detail=(
                f"CRS projeté incompatible : {source_crs.code} fourni, "
                f"{business_crs.code} attendu."
            ),
        )
    return source_crs.srid


def transform_position(connection: Any, longitude: float, latitude: float) -> dict[str, float]:
    """Calcul PostGIS en lecture seule ; aucune géométrie métier n'est écrite."""
    _validated_lonlat(longitude, latitude)
    business_crs = read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT ST_X(p), ST_Y(p) FROM (
                SELECT ST_Transform(ST_SetSRID(ST_Point(%s, %s), 4326), %s) AS p
            ) AS transformed""", (longitude, latitude, business_crs.srid))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError(
            f"Transformation vers {business_crs.code} impossible."
        ) from exc
    if not row or not all(value is not None and math.isfinite(float(value)) for value in row):
        raise WebDatabaseError(
            f"Résultat de transformation vers {business_crs.code} invalide."
        )
    return {"x": float(row[0]), "y": float(row[1])}


def position_vers_reperage(connection: Any, troncon_id: UUID,
                           systeme_reperage_id: UUID, longitude: float,
                           latitude: float) -> dict[str, Any]:
    """Appelle le moteur historique sur le point GPS transformé par PostGIS."""
    _validated_lonlat(longitude, latitude)
    business_crs = read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT to_jsonb(r) - 'point_source' - 'point_projete',
                ST_AsGeoJSON(r.point_projete)::jsonb AS point_projete
                FROM public.xy_vers_reperage(
                    %s, %s, ST_Transform(ST_SetSRID(ST_Point(%s, %s), 4326), %s)
                ) AS r""", (
                    troncon_id, systeme_reperage_id, longitude, latitude,
                    business_crs.srid,
                ))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Calcul du repérage GPS impossible.") from exc
    if not row or not isinstance(row[0], dict):
        raise WebDatabaseError("Résultat de repérage GPS invalide.")
    result = dict(row[0])
    if result.get("statut") != "OK":
        raise HTTPException(status_code=422, detail=(
            "Repérage indisponible pour ce tronçon et ce système "
            f"({result.get('statut') or 'STATUT_INCONNU'})."))
    if (not result.get("borne_id") or result.get("position_relative") not in
            {"AVANT_BORNE", "SUR_BORNE", "APRES_BORNE"}
            or not all(result.get(key) is not None and math.isfinite(float(result[key]))
                       for key in ("distance_borne_m", "distance_axe_m"))
            or float(result["distance_borne_m"]) < 0
            or float(result["distance_axe_m"]) < 0
            or not isinstance(row[1], dict)):
        raise WebDatabaseError("Résultat de repérage GPS incomplet.")
    result["point_projete"] = row[1]
    return result


TYPES_DESORDRE_SQL = """
    SELECT jsonb_build_object(
        'types', coalesce(jsonb_agg(
            jsonb_build_object(
                'id', t.id,
                'libelle', t.libelle,
                'abrege', t.abrege,
                'categorie_id', t.categorie_id,
                'valid', t.valid
            ) ORDER BY t.libelle, t.id
        ), '[]'::jsonb)
    )
    FROM referentiel.types_desordre AS t
    WHERE t.valid
"""


CATEGORIES_DESORDRE_SQL = """
    SELECT jsonb_build_object(
        'categories', coalesce(jsonb_agg(
            jsonb_build_object(
                'id', c.id,
                'libelle', c.libelle,
                'abrege', c.abrege,
                'valid', c.valid
            ) ORDER BY c.libelle, c.id
        ), '[]'::jsonb)
    )
    FROM referentiel.categories_desordre AS c
    WHERE c.valid
"""


REFERENCE_CATALOG_TABLES = {
    "domanialites-lit": "referentiel.domanialites_lit",
    "regimes-ecoulement-lit": "referentiel.regimes_ecoulement_lit",
    "occupations-riveraines-lit": "referentiel.occupations_riveraines_lit",
    "types-zone-atterrissement-lit": "referentiel.types_zone_atterrissement_lit",
    "rives": "referentiel.rives",
    "types-troncon": "referentiel.types_troncon",
    "typologies-troncon": "referentiel.typologies_troncon",
    "cotes": "referentiel.cotes",
    "positions": "referentiel.positions",
    "sources": "referentiel.sources_information",
    "suites-apporter": "referentiel.suites_apporter",
    "urgences": "referentiel.urgences",
    "orientations-photo": "referentiel.orientations_photo",
    "types-mobilier": "referentiel.types_mobilier",
    "types-equipement-mesure": "referentiel.types_equipement_mesure",
    "types-reseau-technique": "referentiel.types_reseau_technique",
    "types-cheminement": "referentiel.types_cheminement",
    "materiaux-cheminement": "referentiel.materiaux_cheminement",
    "revetements-cheminement": "referentiel.revetements_cheminement",
    "usages-cheminement": "referentiel.usages_cheminement",
    "types-voie": "referentiel.types_voie",
    "securites": "referentiel.securites",
    "orientations-ouvrage": "referentiel.orientations_ouvrage",
    "types-amenagement-hydraulique": "referentiel.types_amenagement_hydraulique",
    "fonctionnements-amenagement-hydraulique": "referentiel.fonctionnements_amenagement_hydraulique",
    "types-prestation": "referentiel.types_prestation",
    "types-ouvrage-particulier": "referentiel.types_ouvrage_particulier",
    "problematiques-vegetation": "referentiel.problematiques_vegetation",
    "etats-sanitaires-vegetation": "referentiel.etats_sanitaires_vegetation",
    "classes-hauteur-vegetation": "referentiel.classes_hauteur_vegetation",
    "classes-diametre-vegetation": "referentiel.classes_diametre_vegetation",
    "especes-vegetation": "referentiel.especes_vegetation",
}

EDITABLE_REFERENCE_TABLES = {
    "type_rive_id": ("referentiel.rives", "La rive"),
    "type_troncon_id": ("referentiel.types_troncon", "Le type de tronçon"),
    "typologie_troncon_id": (
        "referentiel.typologies_troncon", "La typologie de tronçon",
    ),
    "cote_id": ("referentiel.cotes", "Le côté"),
    "position_id": ("referentiel.positions", "La position"),
    "source_id": ("referentiel.sources_information", "La source"),
    "suite_apporter_id": ("referentiel.suites_apporter", "La suite à apporter"),
    "urgence_id": ("referentiel.urgences", "L'urgence"),
    "mobilier_type_id": ("referentiel.types_mobilier", "Le type de Mobilier"),
    "equipement_mesure_type_id": (
        "referentiel.types_equipement_mesure", "Le type d'Équipement de mesure",
    ),
    "reseau_technique_type_id": (
        "referentiel.types_reseau_technique", "Le type de Réseau technique",
    ),
    "cheminement_type_id": ("referentiel.types_cheminement", "Le type de Cheminement"),
    "cheminement_materiau_id": ("referentiel.materiaux_cheminement", "Le matériau"),
    "cheminement_revetement_id": ("referentiel.revetements_cheminement", "Le revêtement"),
    "cheminement_usage_id": ("referentiel.usages_cheminement", "L’usage"),
    "cheminement_type_voie_id": ("referentiel.types_voie", "Le type de voie"),
    "cheminement_securite_id": ("referentiel.securites", "La sécurité"),
    "cheminement_orientation_id": ("referentiel.orientations_ouvrage", "L’orientation"),
    "cheminement_revetement_haut_id": ("referentiel.revetements_cheminement", "Le revêtement haut"),
    "cheminement_revetement_bas_id": ("referentiel.revetements_cheminement", "Le revêtement bas"),
    "amenagement_hydraulique_type_id": ("referentiel.types_amenagement_hydraulique", "Le type d’aménagement hydraulique"),
    "prestation_type_id": ("referentiel.types_prestation", "Le type de prestation"),
    "ouvrage_particulier_type_id": (
        "referentiel.types_ouvrage_particulier", "Le type d’ouvrage particulier"),
    "vegetation_etat_sanitaire_id": ("referentiel.etats_sanitaires_vegetation", "L'état sanitaire"),
    "vegetation_classe_hauteur_id": ("referentiel.classes_hauteur_vegetation", "La classe de hauteur"),
    "vegetation_classe_diametre_id": ("referentiel.classes_diametre_vegetation", "La classe de diamètre"),
    "vegetation_type_cote_id": ("referentiel.cotes", "Le côté"),
    "vegetation_type_position_id": ("referentiel.positions", "La position"),
    "vegetation_espece_id": ("referentiel.especes_vegetation", "L’espèce"),
}


TRONCONS_OPTIONS_SQL = """
    SELECT jsonb_build_object(
        'troncons', coalesce(jsonb_agg(
            jsonb_build_object(
                'id', t.id,
                'libelle', coalesce(t.libelle,t.designation),
                'version',t.version,'type_physique_id',t.type_physique_id,
                'digue_id', t.digue_id,
                'digue_libelle', d.libelle,
                'valid', t.valid
            ) ORDER BY d.libelle, t.libelle, t.id
        ), '[]'::jsonb)
    )
    FROM public.troncons AS t
    LEFT JOIN public.digues AS d ON d.id = t.digue_id
"""


MOBILIER_GEOJSON_SQL = """
    SELECT jsonb_build_object(
        'type', 'FeatureCollection',
        'features', coalesce(jsonb_agg(feature ORDER BY sort_label, sort_id),
                             '[]'::jsonb)
    )
    FROM (
        SELECT coalesce(m.designation, '') AS sort_label,
               m.id::text AS sort_id,
               jsonb_build_object(
                   'type', 'Feature',
                   'id', m.id,
                   'geometry', CASE WHEN m.geometry IS NULL THEN NULL ELSE
                       ST_AsGeoJSON(ST_Transform(m.geometry, 4326))::jsonb END,
                   'properties', jsonb_build_object(
                       'id', m.id,
                       'version', m.version,
                       'type_id', m.type_id,
                       'type_libelle', type_mobilier.libelle,
                       'designation', m.designation,
                       'commentaire', m.commentaire,
                       'date_debut', m.date_debut,
                       'troncon_id', m.troncon_id,
                       'troncon_libelle', troncon.libelle,
                       'valid', m.valid
                   )
               ) AS feature
        FROM public.mobilier AS m
        JOIN referentiel.types_mobilier AS type_mobilier
          ON type_mobilier.id = m.type_id
        LEFT JOIN public.troncons AS troncon ON troncon.id = m.troncon_id
    ) AS mobilier_features
"""


MOBILIER_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'type', 'Feature',
        'id', m.id,
        'geometry', CASE WHEN m.geometry IS NULL THEN NULL ELSE
            ST_AsGeoJSON(ST_Transform(m.geometry, 4326))::jsonb END,
        'properties', jsonb_build_object(
            'id', m.id,
            'version', m.version,
            'type_id', m.type_id,
            'type_libelle', type_mobilier.libelle,
            'designation', m.designation,
            'commentaire', m.commentaire,
            'date_debut', m.date_debut,
            'troncon_id', m.troncon_id,
            'troncon_libelle', troncon.libelle,
            'valid', m.valid
        )
    )
    FROM public.mobilier AS m
    JOIN referentiel.types_mobilier AS type_mobilier
      ON type_mobilier.id = m.type_id
    LEFT JOIN public.troncons AS troncon ON troncon.id = m.troncon_id
    WHERE m.id = %s
"""


EQUIPEMENT_MESURE_GEOJSON_SQL = """
    SELECT jsonb_build_object(
        'type', 'FeatureCollection',
        'features', coalesce(jsonb_agg(feature ORDER BY sort_label, sort_id),
                             '[]'::jsonb)
    )
    FROM (
        SELECT coalesce(e.designation, '') AS sort_label,
               e.id::text AS sort_id,
               jsonb_build_object(
                   'type', 'Feature', 'id', e.id,
                   'geometry', CASE WHEN e.geometry IS NULL THEN NULL ELSE
                       ST_AsGeoJSON(ST_Transform(e.geometry, 4326))::jsonb END,
                   'properties', jsonb_build_object(
                       'id', e.id, 'version', e.version,
                       'type_id', e.type_id, 'type_libelle', type_e.libelle,
                       'designation', e.designation, 'commentaire', e.commentaire,
                       'date_debut', e.date_debut, 'troncon_id', e.troncon_id,
                       'troncon_libelle', troncon.libelle, 'valid', e.valid, 'invalidity_causes',to_jsonb(array_remove(ARRAY[
                CASE WHEN e.geometry IS NULL THEN 'MISSING_GEOMETRY' END,
                CASE WHEN e.geometry IS NOT NULL AND NOT public.geometrie_metier_simple_valide(e.geometry) THEN 'INVALID_GEOMETRY' END,
                CASE WHEN GeometryType(e.geometry)<>'POINT' THEN 'UNEXPECTED_GEOMETRY' END
            ],NULL))
                   )
               ) AS feature
        FROM public.equipements_mesure AS e
        JOIN referentiel.types_equipement_mesure AS type_e ON type_e.id = e.type_id
        LEFT JOIN public.troncons AS troncon ON troncon.id = e.troncon_id
    ) AS equipement_features
"""


EQUIPEMENT_MESURE_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'type', 'Feature', 'id', e.id,
        'geometry', CASE WHEN e.geometry IS NULL THEN NULL ELSE
            ST_AsGeoJSON(ST_Transform(e.geometry, 4326))::jsonb END,
        'properties', jsonb_build_object(
            'id', e.id, 'version', e.version,
            'type_id', e.type_id, 'type_libelle', type_e.libelle,
            'designation', e.designation, 'commentaire', e.commentaire,
            'date_debut', e.date_debut, 'troncon_id', e.troncon_id,
            'troncon_libelle', troncon.libelle, 'valid', e.valid, 'invalidity_causes',to_jsonb(array_remove(ARRAY[
                CASE WHEN e.geometry IS NULL THEN 'MISSING_GEOMETRY' END,
                CASE WHEN e.geometry IS NOT NULL AND NOT public.geometrie_metier_simple_valide(e.geometry) THEN 'INVALID_GEOMETRY' END,
                CASE WHEN GeometryType(e.geometry)<>'POINT' THEN 'UNEXPECTED_GEOMETRY' END
            ],NULL))
        )
    )
    FROM public.equipements_mesure AS e
    JOIN referentiel.types_equipement_mesure AS type_e ON type_e.id = e.type_id
    LEFT JOIN public.troncons AS troncon ON troncon.id = e.troncon_id
    WHERE e.id = %s
"""


RESEAU_TECHNIQUE_GEOJSON_SQL = """
    SELECT jsonb_build_object(
        'type', 'FeatureCollection',
        'features', coalesce(jsonb_agg(feature ORDER BY sort_label, sort_id),
                             '[]'::jsonb)
    )
    FROM (
        SELECT coalesce(r.designation, '') AS sort_label, r.id::text AS sort_id,
               jsonb_build_object(
                   'type', 'Feature', 'id', r.id,
                   'geometry', CASE WHEN r.geometry IS NULL THEN NULL ELSE
                       ST_AsGeoJSON(ST_Transform(r.geometry, 4326))::jsonb END,
                   'properties', jsonb_build_object(
                       'id', r.id, 'version', r.version,
                       'type_id', r.type_id, 'type_libelle', type_r.libelle,
                       'designation', r.designation, 'commentaire', r.commentaire,
                       'date_debut', r.date_debut, 'troncon_id', r.troncon_id,
                       'source_id', r.source_id, 'securite_id', r.securite_id,
                       'troncon_libelle', troncon.libelle, 'valid', r.valid
                   )
               ) AS feature
        FROM public.reseaux_techniques AS r
        JOIN referentiel.types_reseau_technique AS type_r ON type_r.id = r.type_id
        LEFT JOIN public.troncons AS troncon ON troncon.id = r.troncon_id
    ) AS reseau_features
"""


RESEAU_TECHNIQUE_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'type', 'Feature', 'id', r.id,
        'geometry', CASE WHEN r.geometry IS NULL THEN NULL ELSE
            ST_AsGeoJSON(ST_Transform(r.geometry, 4326))::jsonb END,
        'properties', jsonb_build_object(
            'id', r.id, 'version', r.version,
            'type_id', r.type_id, 'type_libelle', type_r.libelle,
            'designation', r.designation, 'commentaire', r.commentaire,
            'date_debut', r.date_debut, 'troncon_id', r.troncon_id,
                       'source_id', r.source_id, 'securite_id', r.securite_id,
            'troncon_libelle', troncon.libelle, 'valid', r.valid
        )
    )
    FROM public.reseaux_techniques AS r
    JOIN referentiel.types_reseau_technique AS type_r ON type_r.id = r.type_id
    LEFT JOIN public.troncons AS troncon ON troncon.id = r.troncon_id
    WHERE r.id = %s
"""


OUVRAGE_PARTICULIER_COLLECTION_SQL = """
    SELECT jsonb_build_object(
        'type', 'FeatureCollection',
        'features', coalesce(jsonb_agg(feature ORDER BY sort_label, sort_id), '[]'::jsonb)
    ) FROM (
        SELECT coalesce(o.designation, o.libelle, '') AS sort_label,
               o.id::text AS sort_id,
               jsonb_build_object(
                   'type', 'Feature', 'id', o.id,
                   'geometry', CASE WHEN o.geometry IS NULL THEN NULL ELSE
                       ST_AsGeoJSON(ST_Transform(o.geometry, 4326))::jsonb END,
                   'properties', jsonb_build_object(
                       'id', o.id, 'version', o.version,
                       'type_id', o.type_id, 'type_libelle', typ.libelle,
                       'designation', o.designation, 'libelle', o.libelle,
                       'commentaire', o.commentaire, 'date_debut', o.date_debut,
                       'date_fin', o.date_fin, 'troncon_id', o.troncon_id,
                       'cote_id', o.cote_id, 'position_id', o.position_id, 'source_id', o.source_id,
                       'troncon_libelle', t.libelle, 'valid', o.valid, 'invalidity_causes',to_jsonb(array_remove(ARRAY[
                CASE WHEN o.geometry IS NULL THEN 'MISSING_GEOMETRY' END,
                CASE WHEN o.geometry IS NOT NULL AND NOT public.geometrie_metier_simple_valide(o.geometry) THEN 'INVALID_GEOMETRY' END
            ],NULL))
                   )
               ) AS feature
        FROM public.ouvrages_particuliers AS o
        LEFT JOIN referentiel.types_ouvrage_particulier AS typ ON typ.id = o.type_id
        LEFT JOIN public.troncons AS t ON t.id = o.troncon_id
    ) AS ouvrage_features
"""


OUVRAGE_PARTICULIER_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'type', 'Feature', 'id', o.id,
        'geometry', CASE WHEN o.geometry IS NULL THEN NULL ELSE
            ST_AsGeoJSON(ST_Transform(o.geometry, 4326))::jsonb END,
        'properties', jsonb_build_object(
            'id', o.id, 'version', o.version,
            'type_id', o.type_id, 'type_libelle', typ.libelle,
            'designation', o.designation, 'libelle', o.libelle,
            'commentaire', o.commentaire, 'date_debut', o.date_debut,
            'date_fin', o.date_fin, 'troncon_id', o.troncon_id,
            'cote_id', o.cote_id, 'position_id', o.position_id, 'source_id', o.source_id,
                       'troncon_libelle', t.libelle, 'valid', o.valid, 'invalidity_causes',to_jsonb(array_remove(ARRAY[
                CASE WHEN o.geometry IS NULL THEN 'MISSING_GEOMETRY' END,
                CASE WHEN o.geometry IS NOT NULL AND NOT public.geometrie_metier_simple_valide(o.geometry) THEN 'INVALID_GEOMETRY' END
            ],NULL))
        )
    )
    FROM public.ouvrages_particuliers AS o
    LEFT JOIN referentiel.types_ouvrage_particulier AS typ ON typ.id = o.type_id
    LEFT JOIN public.troncons AS t ON t.id = o.troncon_id
    WHERE o.id = %s
"""


OUVRAGE_PARTICULIER_OBSERVATIONS_SQL = """
    SELECT jsonb_build_object(
        'parent', jsonb_build_object(
            'resource', 'ouvrages_particuliers', 'id', o.id,
            'designation', coalesce(
                nullif(btrim(o.designation), ''),
                o.id::text
            )
        ),
        'observations', coalesce((
            SELECT jsonb_agg(item ORDER BY sort_date DESC NULLS LAST, sort_id)
            FROM (
                SELECT obs.date_observation AS sort_date, obs.id::text AS sort_id,
                       jsonb_build_object(
                           'id', obs.id,
                           'parent', jsonb_build_object(
                               'resource', 'ouvrages_particuliers', 'id', o.id,
                               'designation', coalesce(
                                   nullif(btrim(o.designation), ''),
                                   o.id::text
                               )
                           ),
                           'designation', obs.designation,
                           'date', obs.date_observation, 'valid', obs.valid,
                           'photo_count', (SELECT count(*) FROM public.photos p
                                           WHERE p.observation_id = obs.id)
                       ) AS item
                FROM public.observations obs WHERE obs.ouvrage_particulier_id = o.id
            ) AS ordered_observations
        ), '[]'::jsonb)
    ) FROM public.ouvrages_particuliers o WHERE o.id = %s
"""


CHEMINEMENT_PROPERTIES_SQL = """
    'id', c.id, 'version', c.version,
    'type_cheminement_id', c.type_cheminement_id,
    'type_libelle', typ.libelle,
    'designation', c.designation, 'libelle', c.libelle,
    'commentaire', c.commentaire, 'date_debut', c.date_debut,
    'date_fin', c.date_fin, 'largeur', c.largeur,
    'dimension_horizontale', c.dimension_horizontale,
    'dimension_verticale', c.dimension_verticale,
    'numero_secteur', c.numero_secteur, 'valid', c.valid,
    'materiau_id', c.materiau_id, 'revetement_id', c.revetement_id,
    'usage_id', c.usage_id, 'statut_acces', c.statut_acces,
    'type_voie_id', c.type_voie_id, 'position_id', c.position_id,
    'cote_id', c.cote_id, 'securite_id', c.securite_id,
    'orientation_id', c.orientation_id,
    'position_haut_id', c.position_haut_id,
    'position_bas_id', c.position_bas_id,
    'revetement_haut_id', c.revetement_haut_id,
    'revetement_bas_id', c.revetement_bas_id, 'source_id', c.source_id,
    'troncon_ids', (SELECT coalesce(jsonb_agg(lt.troncon_id ORDER BY lt.troncon_id), '[]'::jsonb)
                    FROM public.cheminements__troncons lt WHERE lt.cheminement_id=c.id),
    'desordre_ids', (SELECT coalesce(jsonb_agg(ld.desordre_id ORDER BY ld.desordre_id), '[]'::jsonb)
                     FROM public.cheminements__desordres ld WHERE ld.cheminement_id=c.id)
"""

CHEMINEMENT_FEATURE_SQL = """
    jsonb_build_object(
        'type', 'Feature', 'id', c.id,
        'geometry', CASE WHEN c.geometry IS NULL THEN NULL ELSE
            ST_AsGeoJSON(ST_Transform(c.geometry,4326))::jsonb END,
        'properties', jsonb_build_object(
""" + CHEMINEMENT_PROPERTIES_SQL + """
        )
    )
"""

CHEMINEMENT_COLLECTION_SQL = """
    SELECT jsonb_build_object('type','FeatureCollection',
        'features',coalesce(jsonb_agg(feature ORDER BY sort_label,sort_id),'[]'::jsonb))
    FROM (SELECT coalesce(c.designation,c.libelle,'') sort_label,
                 c.id::text sort_id,
""" + CHEMINEMENT_FEATURE_SQL + """ feature
          FROM public.cheminements c LEFT JOIN referentiel.types_cheminement typ
            ON typ.id=c.type_cheminement_id) items
"""

CHEMINEMENT_DETAIL_SQL = "SELECT " + CHEMINEMENT_FEATURE_SQL + """
    FROM public.cheminements c LEFT JOIN referentiel.types_cheminement typ
      ON typ.id=c.type_cheminement_id WHERE c.id=%s
"""

CHEMINEMENT_OBSERVATIONS_SQL = OUVRAGE_PARTICULIER_OBSERVATIONS_SQL.replace(
    'ouvrage_particulier_id', 'cheminement_id'
).replace('ouvrages_particuliers', 'cheminements')

PRESTATION_OBJECT_FAMILIES = {
    "desordres": "desordre_id",
    "ouvrages_hydrauliques": "ouvrage_hydraulique_id",
    "ouvrages_particuliers": "ouvrage_particulier_id",
    "mobilier": "mobilier_id",
    "equipements_mesure": "equipement_mesure_id",
    "reseaux_techniques": "reseau_technique_id",
    "cheminements": "cheminement_id",
    "vegetation": "vegetation_id",
    "amenagements_hydrauliques": "amenagement_hydraulique_id",
}
PRESTATION_FIELDS = (
    "designation", "libelle", "commentaire", "date_debut", "date_fin",
    "cout_global", "cout_metre", "realisation_interne", "valid",
)
PRESTATION_FEATURE_SQL = """
    jsonb_build_object(
        'type','Feature','id',p.id,
        'geometry',CASE WHEN p.geometry IS NULL THEN NULL ELSE
            ST_AsGeoJSON(ST_Transform(p.geometry,4326))::jsonb END,
        'properties',jsonb_build_object(
            'id',p.id,'version',p.version,
            'type_prestation_id',p.type_prestation_id,
            'type_libelle',coalesce(typ.libelle,typ.designation),
            'designation',p.designation,'libelle',p.libelle,
            'commentaire',p.commentaire,'date_debut',p.date_debut,
            'date_fin',p.date_fin,'cout_global',p.cout_global,
            'cout_metre',p.cout_metre,'realisation_interne',p.realisation_interne,
            'valid',p.valid,
            'troncon_ids',(SELECT coalesce(jsonb_agg(n.troncon_id ORDER BY n.troncon_id),'[]'::jsonb)
                FROM public.prestations_troncons n WHERE n.prestation_id=p.id),
            'intervenant_ids',(SELECT coalesce(jsonb_agg(n.contact_id ORDER BY n.contact_id),'[]'::jsonb)
                FROM public.prestations_intervenants n WHERE n.prestation_id=p.id),
            'object_ids',jsonb_build_object(
                'desordres',(SELECT coalesce(jsonb_agg(n.desordre_id ORDER BY n.desordre_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.desordre_id IS NOT NULL),
                'ouvrages_hydrauliques',(SELECT coalesce(jsonb_agg(n.ouvrage_hydraulique_id ORDER BY n.ouvrage_hydraulique_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.ouvrage_hydraulique_id IS NOT NULL),
                'ouvrages_particuliers',(SELECT coalesce(jsonb_agg(n.ouvrage_particulier_id ORDER BY n.ouvrage_particulier_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.ouvrage_particulier_id IS NOT NULL),
                'mobilier',(SELECT coalesce(jsonb_agg(n.mobilier_id ORDER BY n.mobilier_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.mobilier_id IS NOT NULL),
                'equipements_mesure',(SELECT coalesce(jsonb_agg(n.equipement_mesure_id ORDER BY n.equipement_mesure_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.equipement_mesure_id IS NOT NULL),
                'reseaux_techniques',(SELECT coalesce(jsonb_agg(n.reseau_technique_id ORDER BY n.reseau_technique_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.reseau_technique_id IS NOT NULL),
                'cheminements',(SELECT coalesce(jsonb_agg(n.cheminement_id ORDER BY n.cheminement_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.cheminement_id IS NOT NULL),
                'vegetation',(SELECT coalesce(jsonb_agg(n.vegetation_id ORDER BY n.vegetation_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.vegetation_id IS NOT NULL),
                'amenagements_hydrauliques',(SELECT coalesce(jsonb_agg(n.amenagement_hydraulique_id ORDER BY n.amenagement_hydraulique_id),'[]'::jsonb)
                    FROM public.prestations_objets n WHERE n.prestation_id=p.id AND n.amenagement_hydraulique_id IS NOT NULL)
            )
        )
    )
"""
PRESTATION_FROM_SQL = """
    FROM public.prestations p
    LEFT JOIN referentiel.types_prestation typ ON typ.id=p.type_prestation_id
"""
PRESTATION_COLLECTION_SQL = """
    SELECT jsonb_build_object('type','FeatureCollection',
        'features',coalesce(jsonb_agg(feature ORDER BY sort_label,sort_id),'[]'::jsonb))
    FROM (SELECT coalesce(p.designation,p.libelle,'') sort_label,p.id::text sort_id,
""" + PRESTATION_FEATURE_SQL + " feature " + PRESTATION_FROM_SQL + ") items"
PRESTATION_DETAIL_SQL = "SELECT " + PRESTATION_FEATURE_SQL + PRESTATION_FROM_SQL + " WHERE p.id=%s"
PRESTATION_OBSERVATIONS_SQL = OUVRAGE_PARTICULIER_OBSERVATIONS_SQL.replace(
    'ouvrage_particulier_id','prestation_id'
).replace('ouvrages_particuliers','prestations')


TRONCON_SUMMARY_SQL = """jsonb_build_object(
    'id',tu.id,'version',tu.version,'libelle',tu.libelle,'resource','troncons',
    'type_physique_id',tu.type_physique_id,
    'type_physique_libelle',utyp.libelle,
    'designation',tu.designation,
    'valid',tu.valid,
    'digue_id',tu.digue_id,
    'amenagement_hydraulique_id',tu.amenagement_hydraulique_id
)"""


AMENAGEMENT_HYDRAULIQUE_FEATURE_SQL = """
    jsonb_build_object(
        'type','Feature','id',a.id,
        'geometry',NULL,
        'properties',jsonb_build_object(
            'id',a.id,'version',a.version,'designation',a.designation,
            'libelle',a.libelle,'commentaire',a.commentaire,
            'date_debut',a.date_debut,'date_fin',a.date_fin,'valid',a.valid,
            'troncon_ids',(SELECT coalesce(jsonb_agg(t.id ORDER BY t.id),'[]'::jsonb)
                FROM public.troncons t WHERE t.amenagement_hydraulique_id=a.id),
            'zone_ids',(SELECT coalesce(jsonb_agg(z.id ORDER BY z.id),'[]'::jsonb)
                FROM public.zones_stockage z WHERE z.amenagement_hydraulique_id=a.id)
        )
    )
"""
AMENAGEMENT_HYDRAULIQUE_FROM_SQL = """
    FROM public.amenagements_hydrauliques a
"""
AMENAGEMENT_HYDRAULIQUE_COLLECTION_SQL = """
    SELECT jsonb_build_object('type','FeatureCollection',
        'features',coalesce(jsonb_agg(feature ORDER BY sort_label,sort_id),'[]'::jsonb))
    FROM (SELECT coalesce(a.designation,a.libelle,'') sort_label,a.id::text sort_id,
""" + AMENAGEMENT_HYDRAULIQUE_FEATURE_SQL + " feature " + AMENAGEMENT_HYDRAULIQUE_FROM_SQL + ") items"
AMENAGEMENT_HYDRAULIQUE_DETAIL_SQL = (
    "SELECT jsonb_set(jsonb_set(" + AMENAGEMENT_HYDRAULIQUE_FEATURE_SQL
    + ", '{properties,troncons}', (SELECT coalesce(jsonb_agg("
    + TRONCON_SUMMARY_SQL
    + " ORDER BY tu.type_physique_id,tu.designation NULLS LAST,tu.id),"
    + "'[]'::jsonb) FROM public.troncons tu "
    + "JOIN referentiel.types_troncon_unifie utyp ON utyp.id=tu.type_physique_id "
    + "WHERE tu.amenagement_hydraulique_id=a.id)),"
    + "'{properties,zones_stockage}', (SELECT coalesce(jsonb_agg(jsonb_build_object("
    + "'id',z.id,'designation',z.designation,'libelle',z.libelle,'valid',z.valid) "
    + "ORDER BY z.designation NULLS LAST,z.id),'[]'::jsonb) "
    + "FROM public.zones_stockage z WHERE z.amenagement_hydraulique_id=a.id))"
    + AMENAGEMENT_HYDRAULIQUE_FROM_SQL + " WHERE a.id=%s"
)
AMENAGEMENT_HYDRAULIQUE_OBSERVATIONS_SQL = OUVRAGE_PARTICULIER_OBSERVATIONS_SQL.replace(
    'ouvrage_particulier_id','amenagement_hydraulique_id'
).replace('ouvrages_particuliers','amenagements_hydrauliques')


ZONE_STOCKAGE_FEATURE_SQL = """
    jsonb_build_object(
        'type','Feature','id',z.id,
        'geometry',CASE WHEN z.valid IS TRUE AND z.geometry IS NOT NULL AND ST_SRID(z.geometry)>0
            THEN ST_AsGeoJSON(ST_Transform(z.geometry,4326))::jsonb ELSE NULL END,
        'properties',jsonb_build_object(
            'id',z.id,'version',z.version,'type_id',z.type_id,
            'type_libelle',typ.libelle,'designation',z.designation,
            'libelle',z.libelle,'commentaire',z.commentaire,
            'date_debut',z.date_debut,'date_fin',z.date_fin,'valid',z.valid,
            'capacite_stockage',z.capacite_stockage,'profondeur_moyenne',z.profondeur_moyenne,
            'superficie',z.superficie,'collectivite_competente_id',z.collectivite_competente_id,
            'collectivite_competente_libelle',coalesce(org.designation,org.nom),
            'fonctionnement_id',z.fonctionnement_id,'fonctionnement_libelle',fon.libelle,
            'amenagement_hydraulique_id',z.amenagement_hydraulique_id,
            'amenagement_hydraulique_libelle',coalesce(a.designation,a.libelle),
            'geometry_status',CASE WHEN z.geometry IS NULL THEN 'ABSENTE'
                WHEN ST_SRID(z.geometry)<=0 THEN 'SRID_INCONNU'
                WHEN GeometryType(z.geometry)<>'POLYGON' OR ST_IsEmpty(z.geometry)
                    OR NOT ST_IsValid(z.geometry) THEN 'HISTORIQUE_NON_CONFORME'
                ELSE 'CONFORME' END,
            'reseau_hydraulique_ids',(SELECT coalesce(jsonb_agg(l.reseau_hydraulique_id ORDER BY l.reseau_hydraulique_id),'[]'::jsonb)
                FROM public.reseaux_hydrauliques__zones_stockage l WHERE l.zone_stockage_id=z.id),
            'desordre_ids',(SELECT coalesce(jsonb_agg(l.desordre_id ORDER BY l.desordre_id),'[]'::jsonb)
                FROM public.zones_stockage__desordres l WHERE l.zone_stockage_id=z.id),
            'prestation_ids',(SELECT coalesce(jsonb_agg(l.prestation_id ORDER BY l.prestation_id),'[]'::jsonb)
                FROM public.prestations_objets l WHERE l.zone_stockage_id=z.id),
            'vegetation_ids',(SELECT coalesce(jsonb_agg(l.vegetation_id ORDER BY l.vegetation_id),'[]'::jsonb)
                FROM public.vegetation__zones_stockage l WHERE l.zone_stockage_id=z.id),
            'gestionnaire_ids',(SELECT coalesce(jsonb_agg(l.organisme_id ORDER BY l.organisme_id),'[]'::jsonb)
                FROM public.zones_stockage__gestionnaires l WHERE l.zone_stockage_id=z.id),
            'organes',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',o.id,'designation',o.designation)
                ORDER BY o.designation NULLS LAST,o.id),'[]'::jsonb)
                FROM public.organes_protection_collective o WHERE o.zone_stockage_id=z.id),
            'ouvrages_hydrauliques',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',o.id,'designation',o.designation)
                ORDER BY o.designation NULLS LAST,o.id),'[]'::jsonb)
                FROM public.ouvrages_hydrauliques o WHERE o.zone_stockage_id=z.id),
            'structures',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',s.id,'designation',s.designation)
                ORDER BY s.designation NULLS LAST,s.id),'[]'::jsonb)
                FROM public.amenagements_hydrauliques__structures s WHERE s.zone_stockage_id=z.id)
        )
    )
"""
ZONE_STOCKAGE_FROM_SQL = """
    FROM public.zones_stockage z
    LEFT JOIN referentiel.types_amenagement_hydraulique typ ON typ.id=z.type_id
    LEFT JOIN referentiel.fonctionnements_amenagement_hydraulique fon ON fon.id=z.fonctionnement_id
    LEFT JOIN public.amenagements_hydrauliques a ON a.id=z.amenagement_hydraulique_id
    LEFT JOIN public.organismes org ON org.id=z.collectivite_competente_id
"""
ZONE_STOCKAGE_COLLECTION_SQL = """
    SELECT jsonb_build_object('type','FeatureCollection',
        'features',coalesce(jsonb_agg(feature ORDER BY sort_label,sort_id),'[]'::jsonb))
    FROM (SELECT coalesce(z.designation,z.libelle,'') sort_label,z.id::text sort_id,
""" + ZONE_STOCKAGE_FEATURE_SQL + " feature " + ZONE_STOCKAGE_FROM_SQL + ") items"
ZONE_STOCKAGE_DETAIL_SQL = "SELECT " + ZONE_STOCKAGE_FEATURE_SQL + ZONE_STOCKAGE_FROM_SQL + " WHERE z.id=%s"
ZONE_STOCKAGE_OBSERVATIONS_SQL = OUVRAGE_PARTICULIER_OBSERVATIONS_SQL.replace(
    'ouvrage_particulier_id','zone_stockage_id'
).replace('ouvrages_particuliers','zones_stockage')


VEGETATION_FEATURE_SQL = """
    jsonb_build_object(
        'type','Feature','id',v.id,
        'geometry',CASE WHEN v.geometry IS NULL THEN NULL ELSE
            ST_AsGeoJSON(ST_Transform(v.geometry,4326))::jsonb END,
        'properties',jsonb_build_object(
            'id',v.id,'version',v.version,
             'famille_vegetation',v.famille_vegetation,
             'disposition',v.disposition,'sous_type',v.sous_type,
             'invasif',v.invasif,
             'invalidity_causes',to_jsonb(array_remove(ARRAY[
                 CASE WHEN v.geometry IS NULL THEN 'MISSING_GEOMETRY' END,
                 CASE WHEN v.geometry IS NOT NULL AND NOT public.geometrie_metier_simple_valide(v.geometry) THEN 'INVALID_GEOMETRY' END,
                 CASE WHEN v.famille_vegetation IS NULL OR v.disposition IS NULL THEN 'UNDETERMINED_CLASSIFICATION' END
             ],NULL)),
             'problematique_ids',(SELECT coalesce(jsonb_agg(
                 lp.problematique_id ORDER BY lp.problematique_id),'[]'::jsonb)
                 FROM public.problematiques_vegetation lp
                 WHERE lp.vegetation_id=v.id),
             'problematiques',(SELECT coalesce(jsonb_agg(jsonb_build_object(
                 'id',lp.problematique_id,'libelle',rp.libelle)
                 ORDER BY rp.libelle,lp.problematique_id),'[]'::jsonb)
                 FROM public.problematiques_vegetation lp
                 JOIN referentiel.problematiques_vegetation rp
                   ON rp.id=lp.problematique_id
                 WHERE lp.vegetation_id=v.id),
            'designation',v.designation,'commentaire',v.commentaire,
            'date_debut',v.date_debut,'date_fin',v.date_fin,'valid',v.valid,
            'contact_eau',v.contact_eau,'densite',v.densite,
            'hauteur_m',v.hauteur_m,'diametre_cm',v.diametre_cm,
            'type_cote_id',v.type_cote_id,'type_cote_libelle',cote.libelle,
            'type_position_id',v.type_position_id,'type_position_libelle',position.libelle,
            'etat_sanitaire_id',v.etat_sanitaire_id,'etat_sanitaire_libelle',etat.libelle,
            'classe_hauteur_id',v.classe_hauteur_id,'classe_hauteur_libelle',hauteur.libelle,
             'classe_diametre_id',v.classe_diametre_id,'classe_diametre_libelle',diametre.libelle,
             'espece_id',v.espece_id,'espece_libelle',espece.libelle,
             'troncon_ids',(SELECT coalesce(jsonb_agg(lt.troncon_id ORDER BY lt.troncon_id),
                 '[]'::jsonb) FROM public.vegetation__troncons lt
                 WHERE lt.vegetation_id=v.id),
             'plan_gestion_ids',(SELECT coalesce(jsonb_agg(lp.plan_id ORDER BY lp.plan_id),
                 '[]'::jsonb) FROM public.plans_gestion__vegetation lp
                 WHERE lp.vegetation_id=v.id),
             'amenagement_hydraulique_ids',(SELECT coalesce(
                 jsonb_agg(la.amenagement_hydraulique_id
                     ORDER BY la.amenagement_hydraulique_id),'[]'::jsonb)
                 FROM public.vegetation__amenagements_hydrauliques la
                 WHERE la.vegetation_id=v.id)
        )
    )
"""
VEGETATION_FROM_SQL = """
    FROM public.vegetation v
    LEFT JOIN referentiel.cotes cote ON cote.id=v.type_cote_id
    LEFT JOIN referentiel.positions position ON position.id=v.type_position_id
    LEFT JOIN referentiel.etats_sanitaires_vegetation etat ON etat.id=v.etat_sanitaire_id
    LEFT JOIN referentiel.classes_hauteur_vegetation hauteur ON hauteur.id=v.classe_hauteur_id
    LEFT JOIN referentiel.classes_diametre_vegetation diametre ON diametre.id=v.classe_diametre_id
    LEFT JOIN referentiel.especes_vegetation espece ON espece.id=v.espece_id
"""
VEGETATION_COLLECTION_SQL = """
    SELECT jsonb_build_object('type','FeatureCollection',
        'features',coalesce(jsonb_agg(feature ORDER BY sort_label,sort_id),'[]'::jsonb))
    FROM (SELECT coalesce(v.designation,'') sort_label,v.id::text sort_id,
""" + VEGETATION_FEATURE_SQL + " feature " + VEGETATION_FROM_SQL + ") items"
VEGETATION_DETAIL_SQL = "SELECT " + VEGETATION_FEATURE_SQL + VEGETATION_FROM_SQL + " WHERE v.id=%s"
VEGETATION_RELATIONS_OPTIONS_SQL = """
    SELECT jsonb_build_object(
        'troncons',(SELECT coalesce(jsonb_agg(jsonb_build_object(
            'id',t.id,'libelle',t.libelle,'valid',t.valid)
            ORDER BY t.libelle NULLS LAST,t.id),'[]'::jsonb)
            FROM public.troncons t),
        'amenagements_hydrauliques',(SELECT coalesce(jsonb_agg(jsonb_build_object(
            'id',a.id,'libelle',coalesce(a.designation,a.libelle),
            'valid',a.valid)
            ORDER BY a.designation NULLS LAST,a.id),'[]'::jsonb)
            FROM public.amenagements_hydrauliques a))
"""
VEGETATION_OBSERVATIONS_SQL = OUVRAGE_PARTICULIER_OBSERVATIONS_SQL.replace(
    'ouvrage_particulier_id','vegetation_id'
).replace('ouvrages_particuliers','vegetation')


TRONCON_REPERAGE_OPTIONS_SQL = """
    SELECT jsonb_build_object(
        'troncon_id', t.id,
        'troncon_libelle', t.libelle,
        'systeme_reperage_id', sr.id,
        'systeme_reperage_libelle', sr.libelle,
        'bornes', coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'id', b.borne_id,
                'libelle', b.libelle,
                'libelle_affichage', b.libelle_affichage,
                'role_spatial', b.role_spatial,
                'valeur_pr', b.valeur_pr
            ) ORDER BY b.valeur_pr, b.borne_id)
            FROM public.bornes_systemes_reperage_lecture AS b
            WHERE b.systeme_reperage_id = sr.id AND b.valid
        ), '[]'::jsonb)
    )
    FROM public.troncons AS t
    LEFT JOIN public.systemes_reperage AS sr
      ON sr.id = t.systeme_reperage_defaut_id AND sr.troncon_id=t.id AND sr.valid
    WHERE t.id = %s AND t.valid
"""


from .troncons import COLLECTION_SQL as TRONCONS_GEOJSON_SQL


# Navigation AH sans géométries : les FK actuelles font seules autorité.
# Une ligne public.troncons est nécessaire pour annoncer son éditeur applicatif.
PATRIMOINE_AH_SQL = """
    SELECT coalesce(jsonb_agg(ah ORDER BY sort_label, sort_id), '[]'::jsonb)
    FROM (
        SELECT coalesce(a.libelle, a.designation, '') AS sort_label, a.id AS sort_id,
            jsonb_build_object(
                'id', a.id, 'libelle', a.libelle, 'designation', a.designation,
                'valid', a.valid,
                'zones_stockage', coalesce((
                    SELECT jsonb_agg(jsonb_build_object(
                        'id', z.id, 'libelle', z.libelle, 'designation', z.designation,
                        'valid', z.valid, 'amenagement_hydraulique_id', z.amenagement_hydraulique_id
                    ) ORDER BY coalesce(z.libelle, z.designation, ''), z.id)
                    FROM public.zones_stockage z WHERE z.amenagement_hydraulique_id=a.id
                ), '[]'::jsonb),
                'troncons', coalesce((
                    SELECT jsonb_agg(jsonb_build_object(
                        'id', tu.id, 'version', tu.version, 'libelle', tu.libelle, 'designation', tu.designation,
                        'commentaire', tu.commentaire, 'date_debut', tu.date_debut,
                        'date_fin', tu.date_fin, 'valid', tu.valid,
                        'type_physique_id', tu.type_physique_id,
                        'type_physique_libelle', typ.libelle,
                        'digue_id', tu.digue_id, 'amenagement_hydraulique_id', tu.amenagement_hydraulique_id,
                        'resource', 'troncons'
                    ) ORDER BY tu.type_physique_id, coalesce(tu.libelle, tu.designation, ''), tu.id)
                    FROM public.troncons tu
                    JOIN referentiel.types_troncon_unifie typ ON typ.id=tu.type_physique_id
                    WHERE tu.amenagement_hydraulique_id=a.id
                ), '[]'::jsonb)
            ) AS ah
        FROM public.amenagements_hydrauliques a
    ) AS amenagements
"""


SYSTEMES_ENDIGUEMENT_SQL = """
    SELECT jsonb_build_object(
        'systemes', coalesce(
            jsonb_agg(systeme ORDER BY sort_label, sort_id),
            '[]'::jsonb
        ),
        'amenagements_hydrauliques', (""" + PATRIMOINE_AH_SQL + """),
        'troncons_sans_parent', coalesce((SELECT jsonb_agg(jsonb_build_object(
            'id',t.id,'libelle',t.libelle,'designation',t.designation,'valid',t.valid,
            'version',t.version,'type_physique_id',t.type_physique_id,
            'type_physique_libelle',typ.libelle) ORDER BY coalesce(t.libelle,t.designation),t.id)
            FROM public.troncons t JOIN referentiel.types_troncon_unifie typ ON typ.id=t.type_physique_id
            WHERE t.digue_id IS NULL AND t.amenagement_hydraulique_id IS NULL),'[]'::jsonb)
    )
    FROM (
        SELECT
            coalesce(s.libelle, '') AS sort_label,
            s.id::text AS sort_id,
            jsonb_build_object(
                'id', s.id,
                'libelle', s.libelle,
                'commentaire', s.commentaire,
                'designation', s.designation,
                'population_protegee', s.population_protegee,
                'classement', s.classement,
                'niveau_protection', s.niveau_protection,
                'valid', s.valid,
                'digues', coalesce((
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'id', d.id,
                            'systeme_endiguement_id', d.systeme_endiguement_id,
                            'libelle', d.libelle,
                            'commentaire', d.commentaire,
                            'designation', d.designation,
                            'valid', d.valid,
                            'troncons', coalesce((
                                SELECT jsonb_agg(
                                    jsonb_build_object(
                                        'id', t.id,'version',t.version,'type_physique_id',t.type_physique_id,
                                        'amenagement_hydraulique_id',t.amenagement_hydraulique_id,
                                        'type_physique_libelle',(SELECT libelle FROM referentiel.types_troncon_unifie WHERE id=t.type_physique_id),
                                        'digue_id', t.digue_id,
                                        'systeme_reperage_defaut_id',
                                            t.systeme_reperage_defaut_id,
                                        'libelle', t.libelle,
                                        'date_debut', t.date_debut,
                                        'date_fin', t.date_fin,
                                        'commentaire', t.commentaire,
                                        'designation', t.designation,
                                        'type_rive_id', t.type_rive_id,
                                        'type_rive_libelle', rive.libelle,
                                        'type_troncon_id', t.type_troncon_id,
                                        'type_troncon_libelle', type_troncon.libelle,
                                        'typologie_troncon_id', t.typologie_troncon_id,
                                        'typologie_troncon_libelle', typologie.libelle,
                                        'valid', t.valid
                                    )
                                    ORDER BY t.libelle, t.id
                                )
                                FROM public.troncons AS t
                                LEFT JOIN referentiel.rives AS rive
                                    ON rive.id = t.type_rive_id
                                LEFT JOIN referentiel.types_troncon AS type_troncon
                                    ON type_troncon.id = t.type_troncon_id
                                LEFT JOIN referentiel.typologies_troncon AS typologie
                                    ON typologie.id = t.typologie_troncon_id
                                WHERE t.digue_id = d.id
                            ), '[]'::jsonb)
                        )
                        ORDER BY d.libelle, d.id
                    )
                    FROM public.digues AS d
                    WHERE d.systeme_endiguement_id = s.id
                ), '[]'::jsonb)
            ) AS systeme
        FROM public.systemes AS s
    ) AS systemes
"""


SYSTEME_ENDIGUEMENT_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'id', s.id,
        'version', s.version,
        'libelle', s.libelle,
        'commentaire', s.commentaire,
        'designation', s.designation,
        'population_protegee', s.population_protegee,
        'classement', s.classement,
        'niveau_protection', s.niveau_protection,
        'valid', s.valid,
        'digues', '[]'::jsonb
    )
    FROM public.systemes AS s
    WHERE s.id = %s
"""


DIGUE_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'id', d.id,
        'version', d.version,
        'systeme_endiguement_id', d.systeme_endiguement_id,
        'systeme_endiguement_libelle', s.libelle,
        'libelle', d.libelle,
        'commentaire', d.commentaire,
        'designation', d.designation,
        'valid', d.valid,
        'troncons',(SELECT coalesce(jsonb_agg(
            """ + TRONCON_SUMMARY_SQL + """
            ORDER BY tu.type_physique_id,tu.designation NULLS LAST,tu.id),'[]'::jsonb)
            FROM public.troncons tu
            JOIN referentiel.types_troncon_unifie utyp ON utyp.id=tu.type_physique_id
            WHERE tu.digue_id=d.id),
        'troncons_lit',(SELECT coalesce(jsonb_agg(jsonb_build_object(
            'id', tl.id, 'libelle', tl.libelle, 'designation', tl.designation,
            'valid', tl.valid) ORDER BY tl.id),'[]'::jsonb)
            FROM public.segments_hydrographiques__digues association
            JOIN public.segments_hydrographiques tl ON tl.id=association.segment_id
            WHERE association.digue_id=d.id)
    )
    FROM public.digues AS d
    JOIN public.systemes AS s ON s.id = d.systeme_endiguement_id
    WHERE d.id = %s
"""


TRONCON_LIT_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'id', tl.id,
        'version', tl.version,
        'libelle', tl.libelle,
        'designation', tl.designation,
        'commentaire', tl.commentaire,
        'date_debut', tl.date_debut,
        'date_fin', tl.date_fin,
        'valid', tl.valid,
        'created_by', tl.created_by, 'edited_by', tl.edited_by,
        'amenagement_id', tl.amenagement_id,
        'geometry_ewkt', ST_AsEWKT(tl.geometry,17),
        'geometry_srid', ST_SRID(tl.geometry),
        'digue_ids',(SELECT coalesce(jsonb_agg(association.digue_id ORDER BY association.digue_id),'[]'::jsonb)
            FROM public.segments_hydrographiques__digues association WHERE association.segment_id=tl.id),
        'digues',(SELECT coalesce(jsonb_agg(jsonb_build_object(
            'id', d.id, 'libelle', d.libelle, 'designation', d.designation,
            'valid', d.valid) ORDER BY d.id),'[]'::jsonb)
            FROM public.segments_hydrographiques__digues association
            JOIN public.digues d ON d.id=association.digue_id
            WHERE association.segment_id=tl.id)
    )
    FROM public.segments_hydrographiques tl
    WHERE tl.id=%s
"""


# Fixed L02 tables and columns; no SQL identifier comes from a request.
_LIT_COMPONENT_VALUES = {
    "elements_morphologiques_lit": (
        ("version", "c.version"),
        ("type_morphologie", "c.type_morphologie"), ("libelle", "c.libelle"),
        ("longueur", "c.longueur::text"), ("largeur", "c.largeur::text"),
        ("rattache_berge", "c.rattache_berge"), ("vegetalise", "c.vegetalise"),
        ("type_zone_id", "c.type_zone_id"),
    ),
    "largeurs_lit": (("largeur_moyenne", "c.largeur_moyenne::text"),),
    "pentes_lit": (("pente_moyenne", "c.pente_moyenne::text"),),
    "domanialites_lit": (("type_id", "c.type_id"),),
    "regimes_ecoulement_lit": (("regime_id", "c.regime_id"),),
    "occupations_riveraines_lit": (("type_occupation_id", "c.type_occupation_id"),
                                   ("rive_id", "c.rive_id")),
}
_LIT_COMPONENT_COMMON_FIELDS = (
    ("id", "c.id"), ("segment_id", "c.segment_id"),
    ("designation", "c.designation"), ("commentaire", "c.commentaire"),
    ("date_debut", "c.date_debut"), ("date_fin", "c.date_fin"),
    ("date_maj", "c.date_maj"), ("valid", "c.valid"),
    ("created_by", "c.created_by"), ("edited_by", "c.edited_by"),
    ("borne_debut_id", "c.borne_debut_id"), ("borne_fin_id", "c.borne_fin_id"),
    ("borne_debut_distance", "c.borne_debut_distance::text"),
    ("borne_fin_distance", "c.borne_fin_distance::text"),
    ("borne_debut_aval", "c.borne_debut_aval"), ("borne_fin_aval", "c.borne_fin_aval"),
    ("pr_debut", "c.pr_debut::text"), ("pr_fin", "c.pr_fin::text"),
    ("systeme_rep_id", "c.systeme_rep_id"),
)
TRONCON_LIT_COMPONENTS_SQL = (
    "WITH parent AS (SELECT %s::uuid AS id) SELECT jsonb_build_object("
    + ",".join(
        f"'{table}', (SELECT coalesce(jsonb_agg(jsonb_build_object("
        + ",".join(f"'{field}',{expression}" for field, expression in (*_LIT_COMPONENT_COMMON_FIELDS, *values))
        + f") ORDER BY c.id),'[]'::jsonb) FROM public.{table} c WHERE c.segment_id=parent.id)"
        for table, values in _LIT_COMPONENT_VALUES.items()
    )
    + ") FROM parent"
)

TRONCON_LIT_FULL_DETAIL_SQL = (
    "WITH lit_properties(value) AS (" + TRONCON_LIT_DETAIL_SQL + ") "
    "SELECT value || (" + TRONCON_LIT_COMPONENTS_SQL.replace(
        "SELECT %s::uuid AS id", "SELECT (lit_properties.value->>'id')::uuid AS id")
    + ") FROM lit_properties"
)


MORPHOLOGY_DETAIL_SQL = (
    "SELECT jsonb_build_object("
    + ",".join(f"'{field}',{expression}" for field, expression in (
        *_LIT_COMPONENT_COMMON_FIELDS, *_LIT_COMPONENT_VALUES["elements_morphologiques_lit"],
        ("geometry_ewkt", "ST_AsEWKT(c.geometry,17)"), ("geometry_srid", "ST_SRID(c.geometry)"),
        ("position_debut_ewkt", "ST_AsEWKT(c.position_debut,17)"),
        ("position_fin_ewkt", "ST_AsEWKT(c.position_fin,17)"),
    ))
    + ") FROM public.elements_morphologiques_lit c WHERE c.id=%s"
)


from .troncons import DETAIL_SQL as TRONCON_DETAIL_SQL


DESORDRE_LATEST_OBSERVATION_PROPERTIES_SQL = """
                    'dernier_niveau_urgence_id', derniere_observation.urgence_id,
                    'dernier_niveau_urgence_libelle', derniere_observation.urgence_libelle,
                    'derniere_observation_id', derniere_observation.id,
                    'derniere_observation_date', derniere_observation.date_observation,
"""


def _desordre_latest_observation_join(desordre_alias: str) -> str:
    """Shared virtual contract for the latest observation of a disorder.

    UUID text ordering is only the existing deterministic tie-breaker; it does
    not carry business chronology. ``valid`` is deliberately not a predicate:
    in the historical model it is an approval state, not deletion semantics.
    """

    if desordre_alias not in {"d", "p"}:
        raise ValueError("Alias SQL Désordre non pris en charge.")
    return f"""
        LEFT JOIN LATERAL (
            SELECT
                o.id,
                o.date_observation,
                o.urgence_id,
                urgence.libelle AS urgence_libelle
            FROM public.observations AS o
            LEFT JOIN referentiel.urgences AS urgence ON urgence.id = o.urgence_id
            WHERE o.desordre_id = {desordre_alias}.id
            ORDER BY o.date_observation DESC NULLS LAST, o.id::text
            LIMIT 1
        ) AS derniere_observation ON true
    """


DESORDRES_GEOJSON_SQL = """
    SELECT jsonb_build_object(
        'type', 'FeatureCollection',
        'features', coalesce(
            jsonb_agg(feature ORDER BY sort_label, sort_id),
            '[]'::jsonb
        )
    )
    FROM (
        SELECT
            coalesce(d.designation, '') AS sort_label,
            d.id::text AS sort_id,
            jsonb_build_object(
                'type', 'Feature',
                'id', d.id,
                'geometry', ST_AsGeoJSON(
                    ST_Transform(d.geometry, 4326)
                )::jsonb,
                'properties', jsonb_build_object(
                    'id', d.id,
                    'version', d.version,
""" + DESORDRE_LATEST_OBSERVATION_PROPERTIES_SQL + """
                    'designation', d.designation,
                    'type_desordre_id', d.type_desordre_id,
                    'categorie_desordre_id', d.categorie_desordre_id,
                    'categorie_desordre_libelle', categorie.libelle,
                    'type_desordre_libelle', td.libelle,
                    'cote_id', d.cote_id,
                    'cote_libelle', cote.libelle,
                    'position_id', d.position_id,
                    'position_libelle', position.libelle,
                    'source_id', d.source_id,
                    'source_libelle', source.libelle,
                    'commentaire', d.commentaire,
                    'lieu_dit', d.lieu_dit,
                    'date_debut', d.date_debut,
                    'date_fin', d.date_fin,
                    'valid', d.valid,
                    'troncon_ids', coalesce((
                        SELECT jsonb_agg(lien.troncon_id ORDER BY lien.troncon_id)
                        FROM public.desordres__troncons AS lien
                        WHERE lien.desordre_id = d.id
                    ), '[]'::jsonb),
                    'type_geometrie', GeometryType(d.geometry)
                )
            ) AS feature
        FROM public.desordres AS d
        LEFT JOIN referentiel.types_desordre AS td
            ON td.id = d.type_desordre_id
        LEFT JOIN referentiel.categories_desordre AS categorie
            ON categorie.id = d.categorie_desordre_id
        LEFT JOIN referentiel.cotes AS cote ON cote.id = d.cote_id
        LEFT JOIN referentiel.positions AS position ON position.id = d.position_id
        LEFT JOIN referentiel.sources_information AS source ON source.id = d.source_id
""" + _desordre_latest_observation_join("d") + """
        WHERE d.geometry IS NOT NULL
          AND GeometryType(d.geometry) IN ('POINT', 'LINESTRING', 'POLYGON')
    ) AS features
"""


_POINT_DESORDRE_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'type', 'Feature',
        'id', p.id,
        'geometry', ST_AsGeoJSON(
            ST_Transform(p.geometry, 4326)
        )::jsonb,
        'properties', jsonb_build_object(
            'id', p.id,
            'version', base.version,
""" + DESORDRE_LATEST_OBSERVATION_PROPERTIES_SQL + """
            'designation', p.designation,
            'type_desordre_id', p.type_desordre_id,
            'categorie_desordre_id', base.categorie_desordre_id,
            'categorie_desordre_libelle', categorie.libelle,
            'type_desordre_libelle', td.libelle,
            'cote_id', base.cote_id,
            'cote_libelle', cote.libelle,
            'position_id', base.position_id,
            'position_libelle', position.libelle,
            'source_id', base.source_id,
            'source_libelle', source.libelle,
            'commentaire', p.commentaire,
            'lieu_dit', base.lieu_dit,
            'date_debut', p.date_debut,
            'date_fin', p.date_fin,
            'x', p.coord_x,
            'y', p.coord_y,
            'longitude', p.longitude_4326,
            'latitude', p.latitude_4326,
            'troncon_ids', coalesce((
                SELECT jsonb_agg(lien.troncon_id ORDER BY lien.troncon_id)
                FROM public.desordres__troncons AS lien
                WHERE lien.desordre_id = p.id
            ), '[]'::jsonb),
            'reperage', jsonb_build_object(
                'nombre_troncons', liens.nombre_troncons,
                'disponible', liens.nombre_troncons = 1
                    AND sr.id IS NOT NULL,
                'motif_indisponibilite', CASE
                    WHEN liens.nombre_troncons = 0
                        THEN 'Aucun tronçon associé.'
                    WHEN liens.nombre_troncons > 1
                        THEN 'Plusieurs tronçons associés.'
                    WHEN sr.id IS NULL
                        THEN 'Aucun système de repérage disponible.'
                    ELSE NULL
                END,
                'troncon_id', t.id,
                'troncon_libelle', t.libelle,
                'systeme_reperage_id', sr.id,
                'systeme_reperage_libelle', sr.libelle,
                'borne_debut_id', localisation.borne_debut_id,
                'borne_debut_libelle', borne.libelle,
                'distance_debut_m', localisation.distance_debut_m,
                'position_debut_relative',
                    localisation.position_debut_relative,
                'pr_debut', localisation.pr_debut,
                'bornes', coalesce((
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'id', disponible.borne_id,
                            'libelle', disponible.libelle,
                            'libelle_affichage', disponible.libelle_affichage,
                            'role_spatial', disponible.role_spatial,
                            'valeur_pr', disponible.valeur_pr
                        )
                        ORDER BY disponible.valeur_pr, disponible.borne_id
                    )
                    FROM public.bornes_systemes_reperage_lecture AS disponible
                    WHERE disponible.systeme_reperage_id = sr.id
                      AND disponible.valid
                ), '[]'::jsonb)
            ),
            'valid', p.valid
        )
    )
    FROM public.desordres_points_saisie AS p
    JOIN public.desordres AS base ON base.id = p.id
    LEFT JOIN referentiel.types_desordre AS td
        ON td.id = p.type_desordre_id
    LEFT JOIN referentiel.categories_desordre AS categorie
        ON categorie.id = base.categorie_desordre_id
    LEFT JOIN referentiel.cotes AS cote ON cote.id = base.cote_id
    LEFT JOIN referentiel.positions AS position ON position.id = base.position_id
    LEFT JOIN referentiel.sources_information AS source ON source.id = base.source_id
""" + _desordre_latest_observation_join("p") + """
    LEFT JOIN LATERAL (
        SELECT count(*)::integer AS nombre_troncons,
            min(lien.troncon_id::text)::uuid AS troncon_id
        FROM public.desordres__troncons AS lien
        WHERE lien.desordre_id = p.id
    ) AS liens ON true
    LEFT JOIN public.troncons AS t
        ON t.id = liens.troncon_id AND liens.nombre_troncons = 1
    LEFT JOIN public.localisations_desordres AS localisation
        ON localisation.desordre_id = p.id
       AND localisation.troncon_id = t.id
    LEFT JOIN public.systemes_reperage AS sr
        ON sr.id = coalesce(
            localisation.systeme_reperage_id,
            t.systeme_reperage_defaut_id
        )
       AND sr.troncon_id = t.id
    LEFT JOIN public.bornes_reperage AS borne
        ON borne.id = localisation.borne_debut_id
"""
POINT_DESORDRE_SQL = _POINT_DESORDRE_DETAIL_SQL + " WHERE p.id = %s"
POINT_DESORDRES_BATCH_SQL = _POINT_DESORDRE_DETAIL_SQL + " WHERE p.id = ANY(%s::uuid[])"


DESORDRE_GEOMETRY_TYPE_SQL = """
    SELECT GeometryType(d.geometry)
    FROM public.desordres AS d
    WHERE d.id = %s
"""


_LINE_DESORDRE_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'type', 'Feature',
        'id', d.id,
        'geometry', ST_AsGeoJSON(ST_Transform(d.geometry, 4326))::jsonb,
        'properties', jsonb_build_object(
            'id', d.id,
            'version', d.version,
""" + DESORDRE_LATEST_OBSERVATION_PROPERTIES_SQL + """
            'designation', d.designation,
            'type_desordre_id', d.type_desordre_id,
            'categorie_desordre_id', d.categorie_desordre_id,
            'categorie_desordre_libelle', categorie.libelle,
            'type_desordre_libelle', td.libelle,
            'cote_id', d.cote_id,
            'cote_libelle', cote.libelle,
            'position_id', d.position_id,
            'position_libelle', position.libelle,
            'source_id', d.source_id,
            'source_libelle', source.libelle,
            'commentaire', d.commentaire,
            'lieu_dit', d.lieu_dit,
            'date_debut', d.date_debut,
            'date_fin', d.date_fin,
            'valid', d.valid,
            'type_geometrie', GeometryType(d.geometry),
            'nombre_sommets', ST_NPoints(d.geometry),
            'debut_x', ST_X(ST_StartPoint(d.geometry)),
            'debut_y', ST_Y(ST_StartPoint(d.geometry)),
            'fin_x', ST_X(ST_EndPoint(d.geometry)),
            'fin_y', ST_Y(ST_EndPoint(d.geometry)),
            'debut_longitude', ST_X(ST_Transform(
                ST_StartPoint(d.geometry), 4326
            )),
            'debut_latitude', ST_Y(ST_Transform(
                ST_StartPoint(d.geometry), 4326
            )),
            'fin_longitude', ST_X(ST_Transform(
                ST_EndPoint(d.geometry), 4326
            )),
            'fin_latitude', ST_Y(ST_Transform(
                ST_EndPoint(d.geometry), 4326
            )),
            'troncon_ids', coalesce((
                SELECT jsonb_agg(lien.troncon_id ORDER BY lien.troncon_id)
                FROM public.desordres__troncons AS lien
                WHERE lien.desordre_id = d.id
            ), '[]'::jsonb),
            'reperage', jsonb_build_object(
                'nombre_troncons', liens.nombre_troncons,
                'disponible', liens.nombre_troncons = 1
                    AND localisation.id IS NOT NULL,
                'motif_indisponibilite', CASE
                    WHEN liens.nombre_troncons = 0 THEN 'Aucun tronçon associé.'
                    WHEN liens.nombre_troncons > 1
                        THEN 'Plusieurs tronçons associés.'
                    WHEN localisation.id IS NULL
                        THEN 'Aucun système de repérage disponible.'
                    ELSE NULL
                END,
                'troncon_id', localisation.troncon_id,
                'troncon_libelle', localisation.troncon_libelle,
                'systeme_reperage_id', localisation.systeme_reperage_id,
                'systeme_reperage_libelle',
                    localisation.systeme_reperage_libelle,
                'borne_debut_id', localisation.borne_debut_id,
                'borne_debut_libelle', localisation.borne_debut_libelle,
                'distance_debut_m', localisation.distance_debut_m,
                'position_debut_relative',
                    localisation.position_debut_relative,
                'pr_debut', localisation.pr_debut,
                'borne_fin_id', localisation.borne_fin_id,
                'borne_fin_libelle', localisation.borne_fin_libelle,
                'distance_fin_m', localisation.distance_fin_m,
                'position_fin_relative', localisation.position_fin_relative,
                'pr_fin', localisation.pr_fin,
                'bornes', coalesce((
                    SELECT jsonb_agg(jsonb_build_object(
                        'id', disponible.borne_id,
                        'libelle', disponible.libelle,
                        'libelle_affichage', disponible.libelle_affichage,
                        'role_spatial', disponible.role_spatial,
                        'valeur_pr', disponible.valeur_pr
                    ) ORDER BY disponible.valeur_pr, disponible.borne_id)
                    FROM public.bornes_systemes_reperage_lecture AS disponible
                    WHERE disponible.systeme_reperage_id =
                        localisation.systeme_reperage_id
                      AND disponible.valid
                ), '[]'::jsonb)
            )
        )
    )
    FROM public.desordres AS d
    LEFT JOIN referentiel.types_desordre AS td
        ON td.id = d.type_desordre_id
    LEFT JOIN referentiel.categories_desordre AS categorie
        ON categorie.id = d.categorie_desordre_id
    LEFT JOIN referentiel.cotes AS cote ON cote.id = d.cote_id
    LEFT JOIN referentiel.positions AS position ON position.id = d.position_id
    LEFT JOIN referentiel.sources_information AS source ON source.id = d.source_id
""" + _desordre_latest_observation_join("d") + """
    LEFT JOIN LATERAL (
        SELECT count(*)::integer AS nombre_troncons
        FROM public.desordres__troncons AS lien
        WHERE lien.desordre_id = d.id
    ) AS liens ON true
    LEFT JOIN public.localisations_desordres_lecture AS localisation
        ON localisation.desordre_id = d.id
    WHERE GeometryType(d.geometry) = 'LINESTRING'
"""
LINE_DESORDRE_SQL = _LINE_DESORDRE_DETAIL_SQL + " AND d.id = %s"
LINE_DESORDRES_BATCH_SQL = _LINE_DESORDRE_DETAIL_SQL + " AND d.id = ANY(%s::uuid[])"


_POLYGON_DESORDRE_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'type', 'Feature',
        'id', d.id,
        'geometry', ST_AsGeoJSON(ST_Transform(d.geometry, 4326))::jsonb,
        'properties', jsonb_build_object(
            'id', d.id,
            'version', d.version,
""" + DESORDRE_LATEST_OBSERVATION_PROPERTIES_SQL + """
            'designation', d.designation,
            'type_desordre_id', d.type_desordre_id,
            'categorie_desordre_id', d.categorie_desordre_id,
            'categorie_desordre_libelle', categorie.libelle,
            'type_desordre_libelle', td.libelle,
            'cote_id', d.cote_id,
            'cote_libelle', cote.libelle,
            'position_id', d.position_id,
            'position_libelle', position.libelle,
            'source_id', d.source_id,
            'source_libelle', source.libelle,
            'commentaire', d.commentaire,
            'lieu_dit', d.lieu_dit,
            'date_debut', d.date_debut,
            'date_fin', d.date_fin,
            'valid', d.valid,
            'type_geometrie', GeometryType(d.geometry),
            'nombre_sommets', ST_NPoints(d.geometry),
            'nombre_troncons', (
                SELECT count(*)::integer
                FROM public.desordres__troncons AS lien
                WHERE lien.desordre_id = d.id
            ),
            'troncon_ids', coalesce((
                SELECT jsonb_agg(lien.troncon_id ORDER BY lien.troncon_id)
                FROM public.desordres__troncons AS lien
                WHERE lien.desordre_id = d.id
            ), '[]'::jsonb),
            'x', ST_X(ST_PointOnSurface(d.geometry)),
            'y', ST_Y(ST_PointOnSurface(d.geometry)),
            'longitude', ST_X(ST_Transform(
                ST_PointOnSurface(d.geometry), 4326
            )),
            'latitude', ST_Y(ST_Transform(
                ST_PointOnSurface(d.geometry), 4326
            )),
            'reperage', jsonb_build_object(
                'disponible', false,
                'motif_indisponibilite',
                    'Le repérage polygonal est uniquement informatif.'
            )
        )
    )
    FROM public.desordres AS d
    LEFT JOIN referentiel.types_desordre AS td
        ON td.id = d.type_desordre_id
    LEFT JOIN referentiel.categories_desordre AS categorie
        ON categorie.id = d.categorie_desordre_id
    LEFT JOIN referentiel.cotes AS cote ON cote.id = d.cote_id
    LEFT JOIN referentiel.positions AS position ON position.id = d.position_id
    LEFT JOIN referentiel.sources_information AS source ON source.id = d.source_id
""" + _desordre_latest_observation_join("d") + """
    WHERE GeometryType(d.geometry) = 'POLYGON'
"""
POLYGON_DESORDRE_SQL = _POLYGON_DESORDRE_DETAIL_SQL + " AND d.id = %s"
POLYGON_DESORDRES_BATCH_SQL = _POLYGON_DESORDRE_DETAIL_SQL + " AND d.id = ANY(%s::uuid[])"


_UNLOCATED_DESORDRE_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'type', 'Feature',
        'id', d.id,
        'geometry', NULL,
        'properties', jsonb_build_object(
            'id', d.id,
            'version', d.version,
""" + DESORDRE_LATEST_OBSERVATION_PROPERTIES_SQL + """
            'designation', d.designation,
            'commentaire', d.commentaire,
            'lieu_dit', d.lieu_dit,
            'date_debut', d.date_debut,
            'date_fin', d.date_fin,
            'valid', d.valid,
            'type_desordre_id', d.type_desordre_id,
            'type_desordre_libelle', td.libelle,
            'categorie_desordre_id', d.categorie_desordre_id,
            'categorie_desordre_libelle', categorie.libelle,
            'cote_id', d.cote_id,
            'cote_libelle', cote.libelle,
            'position_id', d.position_id,
            'position_libelle', position.libelle,
            'source_id', d.source_id,
            'source_libelle', source.libelle,
            'type_geometrie', NULL,
            'troncon_ids', coalesce((
                SELECT jsonb_agg(lien.troncon_id ORDER BY lien.troncon_id)
                FROM public.desordres__troncons AS lien
                WHERE lien.desordre_id = d.id
            ), '[]'::jsonb),
            'zones_stockage', coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', z.id, 'designation', z.designation, 'libelle', z.libelle
                ) ORDER BY z.id)
                FROM public.zones_stockage__desordres AS lien
                JOIN public.zones_stockage AS z ON z.id = lien.zone_stockage_id
                WHERE lien.desordre_id = d.id
            ), '[]'::jsonb),
            'reperage', jsonb_build_object(
                'disponible', false,
                'motif_indisponibilite', 'Géométrie non renseignée.'
            )
        )
    )
    FROM public.desordres AS d
    LEFT JOIN referentiel.types_desordre AS td ON td.id = d.type_desordre_id
    LEFT JOIN referentiel.categories_desordre AS categorie
        ON categorie.id = d.categorie_desordre_id
    LEFT JOIN referentiel.cotes AS cote ON cote.id = d.cote_id
    LEFT JOIN referentiel.positions AS position ON position.id = d.position_id
    LEFT JOIN referentiel.sources_information AS source ON source.id = d.source_id
""" + _desordre_latest_observation_join("d") + """
    WHERE d.geometry IS NULL
"""
UNLOCATED_DESORDRE_SQL = _UNLOCATED_DESORDRE_DETAIL_SQL + " AND d.id = %s"
# Share the third bounded read with polygons; unit readers keep strict filters.
POLYGON_AND_UNLOCATED_DESORDRES_BATCH_SQL = """
    WITH requested_disorders AS (
        SELECT id FROM public.desordres WHERE id = ANY(%s::uuid[])
    )
""" + _POLYGON_DESORDRE_DETAIL_SQL + """
    AND d.id IN (SELECT id FROM requested_disorders)
    UNION ALL
""" + _UNLOCATED_DESORDRE_DETAIL_SQL + """
    AND d.id IN (SELECT id FROM requested_disorders)
"""


DESORDRE_OBSERVATIONS_SQL = """
    SELECT jsonb_build_object(
        'parent', jsonb_build_object(
            'resource', 'desordres', 'id', d.id,
            'designation', coalesce(nullif(btrim(d.designation), ''), d.id::text)
        ),
        'observations', coalesce((
            SELECT jsonb_agg(observation ORDER BY sort_date DESC NULLS LAST, sort_id)
            FROM (
                SELECT
                    o.date_observation AS sort_date,
                    o.id::text AS sort_id,
                    jsonb_build_object(
                        'id', o.id,
                        'parent', jsonb_build_object(
                            'resource', 'desordres', 'id', d.id,
                            'designation', coalesce(nullif(btrim(d.designation), ''), d.id::text)
                        ),
                        'urgence_id', o.urgence_id,
                        'urgence_libelle', u.libelle,
                        'designation', o.designation,
                        'date', o.date_observation,
                        'evolution', o.evolution,
                        'valid', o.valid,
                        'photo_count', (
                            SELECT count(*)
                            FROM public.photos AS p
                            WHERE p.observation_id = o.id
                        )
                    ) AS observation
                FROM public.observations AS o
                LEFT JOIN referentiel.urgences AS u ON u.id = o.urgence_id
                WHERE o.desordre_id = d.id
            ) AS ordered_observations
        ), '[]'::jsonb)
    )
    FROM public.desordres AS d
    WHERE d.id = %s
"""


# Même enveloppe Observation, sur le parent Tronçon déjà pris en charge au CREATE.
TRONCON_OBSERVATIONS_SQL = (DESORDRE_OBSERVATIONS_SQL
    .replace("'desordres'", "'troncons'")
    .replace("public.desordres AS d", "public.troncons AS d")
    .replace("o.desordre_id = d.id", "(o.troncon_id = d.id OR o.berge_id = d.id OR o.structure_digue_id = d.id)")
    .replace("nullif(btrim(d.designation), ''), d.id::text",
             "nullif(btrim(d.designation), ''), nullif(btrim(d.libelle), ''), d.id::text"))


MOBILIER_OBSERVATIONS_SQL = """
    SELECT jsonb_build_object(
        'parent', jsonb_build_object(
            'resource', 'mobilier', 'id', m.id,
            'designation', coalesce(
                nullif(btrim(m.designation), ''), m.id::text
            )
        ),
        'observations', coalesce((
            SELECT jsonb_agg(observation ORDER BY sort_date DESC NULLS LAST, sort_id)
            FROM (
                SELECT o.date_observation AS sort_date,
                       o.id::text AS sort_id,
                       jsonb_build_object(
                           'id', o.id,
                           'parent', jsonb_build_object(
                               'resource', 'mobilier', 'id', m.id,
                               'designation', coalesce(
                                   nullif(btrim(m.designation), ''), m.id::text
                               )
                           ),
                           'designation', o.designation,
                           'date', o.date_observation,
                           'evolution', o.evolution,
                           'valid', o.valid,
                           'photo_count', (
                               SELECT count(*) FROM public.photos AS p
                               WHERE p.observation_id = o.id
                           )
                       ) AS observation
                FROM public.observations AS o
                WHERE o.mobilier_id = m.id
            ) AS ordered_observations
        ), '[]'::jsonb)
    )
    FROM public.mobilier AS m
    WHERE m.id = %s
"""


EQUIPEMENT_MESURE_OBSERVATIONS_SQL = """
    SELECT jsonb_build_object(
        'parent', jsonb_build_object(
            'resource', 'equipements_mesure', 'id', e.id,
            'designation', coalesce(
                nullif(btrim(e.designation), ''), e.id::text
            )
        ),
        'observations', coalesce((
            SELECT jsonb_agg(observation ORDER BY sort_date DESC NULLS LAST, sort_id)
            FROM (
                SELECT o.date_observation AS sort_date, o.id::text AS sort_id,
                       jsonb_build_object(
                           'id', o.id,
                           'parent', jsonb_build_object(
                               'resource', 'equipements_mesure', 'id', e.id,
                               'designation', coalesce(
                                   nullif(btrim(e.designation), ''), e.id::text
                               )
                           ),
                           'designation', o.designation, 'date', o.date_observation,
                           'evolution', o.evolution, 'valid', o.valid,
                           'photo_count', (
                               SELECT count(*) FROM public.photos AS p
                               WHERE p.observation_id = o.id
                           )
                       ) AS observation
                FROM public.observations AS o
                WHERE o.equipement_mesure_id = e.id
            ) AS ordered_observations
        ), '[]'::jsonb)
    )
    FROM public.equipements_mesure AS e
    WHERE e.id = %s
"""


RESEAU_TECHNIQUE_OBSERVATIONS_SQL = """
    SELECT jsonb_build_object(
        'parent', jsonb_build_object(
            'resource', 'reseaux_techniques', 'id', r.id,
            'designation', coalesce(
                nullif(btrim(r.designation), ''), r.id::text
            )
        ),
        'observations', coalesce((
            SELECT jsonb_agg(observation ORDER BY sort_date DESC NULLS LAST, sort_id)
            FROM (
                SELECT o.date_observation AS sort_date, o.id::text AS sort_id,
                       jsonb_build_object(
                           'id', o.id,
                           'parent', jsonb_build_object(
                               'resource', 'reseaux_techniques', 'id', r.id,
                               'designation', coalesce(
                                   nullif(btrim(r.designation), ''), r.id::text
                               )
                           ),
                           'designation', o.designation, 'date', o.date_observation,
                           'evolution', o.evolution, 'valid', o.valid,
                           'photo_count', (
                               SELECT count(*) FROM public.photos AS p
                               WHERE p.observation_id = o.id
                           )
                       ) AS observation
                FROM public.observations AS o
                WHERE o.reseau_technique_id = r.id
            ) AS ordered_observations
        ), '[]'::jsonb)
    )
    FROM public.reseaux_techniques AS r
    WHERE r.id = %s
"""


# Identifiants SQL exclusivement internes : aucune table/colonne fournie par le client.
OBSERVATION_PARENTS = (
    ("desordre_id", "desordres", ("designation",)),
    ("troncon_id", "troncons", ("designation", "libelle")),
    ("ouvrage_hydraulique_id", "ouvrages_hydrauliques", ("designation",)),
    ("equipement_mesure_id", "equipements_mesure", ("designation",)),
    ("cheminement_id", "cheminements", ("designation", "libelle")),
    ("mobilier_id", "mobilier", ("designation",)),
    ("reseau_technique_id", "reseaux_techniques", ("designation",)),
    ("amenagement_hydraulique_id", "amenagements_hydrauliques", ("designation",)),
    ("zone_stockage_id", "zones_stockage", ("designation", "libelle")),
    ("vegetation_id", "vegetation", ("designation",)),
    ("ouvrage_particulier_id", "ouvrages_particuliers", ("designation", "libelle")),
    ("structure_amenagement_hydraulique_id", "structures_amenagements_hydrauliques", ("designation", "libelle")),
    ("organe_protection_collective_id", "organes_protection_collective", ("designation", "libelle")),
    ("prestation_id", "prestations", ("designation", "libelle")),
)

# Ces familles restent consultables, notamment pour leurs Photos directes.
OBSERVATION_READ_ONLY_PARENTS = (
    ("ouvrage_telecom_energie_id", "ouvrages_telecom_energie", ("designation", "libelle")),
    ("ouvrage_voirie_id", "ouvrages_voirie", ("designation", "libelle")),
    ("reseau_hydraulique_id", "reseaux_hydrauliques", ("designation", "libelle")),
    ("structure_digue_id", "structures_digue", ("designation",)),
    ("profil_travers_id", "profils_travers", ("designation", "libelle")),
    ("objet_mesure_hydraulique_id", "objets_mesures_hydrauliques", ("designation",)),
    ("structure_protection_id", "structures_protection", ("designation", "libelle")),
    ("segment_hydrographique_id", "segments_hydrographiques", ("designation", "libelle")),
    ("berge_id", "berges", ("designation", "libelle")),
    ("composant_berge_id", "composants_berges", ("designation",)),
    ("element_morphologique_id", "elements_morphologiques_lit", ("designation", "libelle")),
    ("trace_position_troncon_id", "traces_positions_troncons", ("designation",)),
)
OBSERVATION_READ_PARENTS = OBSERVATION_PARENTS + OBSERVATION_READ_ONLY_PARENTS

OBSERVATION_PARENT_SQL_TABLES = {
    "structures_amenagements_hydrauliques": "public.amenagements_hydrauliques__structures",
    "structures_digue": "public.troncons", "berges": "public.troncons",
}

def _observation_parent_label(resource: str, field: str) -> str:
    return f"p.{field}"


def _observation_parent_sql(fk, resource, labels):
    table=OBSERVATION_PARENT_SQL_TABLES.get(resource,f'public.{resource}')
    predicate = (" AND p.type_physique_id='BERGE'" if resource=='berges' else
        " AND p.est_structure" if resource=='structures_digue' else "")
    physical = resource in {'troncons','structures_digue','berges'}
    kind = 'p.type_physique_id' if physical else 'tu.type_physique_id' if resource=='traces_positions_troncons' else 'NULL::text'
    canonical = "'troncons'" if physical else "NULL::text"
    role = f"'{fk}'" if physical else "NULL::text"
    label = ','.join(f"nullif(btrim(p.{field}), '')" for field in labels)
    return (f"SELECT '{resource}' AS resource,binding.id,p.id AS matched_id,"
        f"coalesce({label},binding.id::text) AS designation,{kind} AS type_physique_id,"
        f"{canonical} AS canonical_resource,{role} AS relation_role "
        f"FROM (VALUES (o.{fk})) AS binding(id) LEFT JOIN {table} p ON p.id=binding.id{predicate} "
        + ("LEFT JOIN public.troncons tu ON tu.id=p.troncon_position_id " if resource=='traces_positions_troncons' else "")
        + "WHERE binding.id IS NOT NULL")


OBSERVATION_PARENT_CANDIDATES_SQL = " UNION ALL ".join(
    _observation_parent_sql(fk,resource,labels) for fk,resource,labels in OBSERVATION_READ_PARENTS)


OBSERVATION_DETAIL_SQL = """
    SELECT jsonb_build_object(
        'id', o.id,
        'version', o.version,
        '_parent_candidates', (
            SELECT coalesce(jsonb_agg(to_jsonb(candidate)), '[]'::jsonb)
            FROM (""" + OBSERVATION_PARENT_CANDIDATES_SQL + """) AS candidate
        ),
        'urgence_id', o.urgence_id,
        'urgence_libelle', u.libelle,
        'designation', o.designation,
        'date', o.date_observation,
        'evolution', o.evolution,
        'suite_apporter_id', o.suite_apporter_id,
        'suite_apporter_libelle', suite_apporter.libelle,
        'suite', o.suite,
        'nombre_desordres', o.nombre_desordres,
        'valid', o.valid,
        'photos', coalesce((
            SELECT jsonb_agg(photo ORDER BY sort_date DESC NULLS LAST, sort_id)
            FROM (
                SELECT
                    p.date_photo AS sort_date,
                    p.id::text AS sort_id,
                    jsonb_build_object(
                        'id', p.id,
                        'observation_id', p.observation_id,
                        'version', p.version,
                        '_storage_key', p.storage_key,
                        'photographe_id', p.photographe_id,
                        'designation', p.designation,
                        'libelle', p.libelle,
                        'commentaire', p.commentaire,
                        'cote_id', p.cote_id,
                        'cote_libelle', cote.libelle,
                        'orientation_photo_id', p.orientation_photo_id,
                        'orientation_photo_libelle', orientation.libelle,
                        'date', p.date_photo,
                        'valid', p.valid,
                        'nom_fichier', NULL,
                        'content_available', false
                    ) AS photo
                FROM public.photos AS p
                LEFT JOIN referentiel.cotes AS cote ON cote.id = p.cote_id
                LEFT JOIN referentiel.orientations_photo AS orientation
                    ON orientation.id = p.orientation_photo_id
                WHERE p.observation_id = o.id
            ) AS ordered_photos
        ), '[]'::jsonb)
    )
    FROM public.observations AS o
    LEFT JOIN referentiel.urgences AS u ON u.id = o.urgence_id
    LEFT JOIN referentiel.suites_apporter AS suite_apporter
        ON suite_apporter.id = o.suite_apporter_id
    WHERE o.id = %s
"""


POINT_UPDATE_COLUMNS = {
    "designation": "designation",
    "type_desordre_id": "type_desordre_id",
    "categorie_desordre_id": "categorie_desordre_id",
    "commentaire": "commentaire",
    "lieu_dit": "lieu_dit",
    "date_debut": "date_debut",
    "date_fin": "date_fin",
    "valid": "valid",
    "x": "coord_x",
    "y": "coord_y",
    "longitude": "longitude_4326",
    "latitude": "latitude_4326",
}


class PointDesordreNotFoundError(LookupError):
    """Le désordre demandé n'existe pas ou n'est pas ponctuel."""


class PointDesordreUpdateError(ValueError):
    """Le trigger PostgreSQL a refusé la modification ponctuelle."""


class ObservationNotFoundError(LookupError):
    """Le désordre ou l'observation demandé n'existe pas."""


class ObservationUpdateError(ValueError):
    """La modification limitée d'une observation a été refusée."""


class PointReperageUnavailableError(ValueError):
    """Le désordre Point ne possède pas un contexte de repérage éditable."""


class PointReperageUpdateError(ValueError):
    """Le trigger PostgreSQL a refusé la modification du repérage."""


class DesordreNotFoundError(LookupError):
    """Le désordre cartographique demandé n'existe pas."""


class LineDesordreNotFoundError(LookupError):
    """Le désordre demandé n'existe pas ou n'est pas linéaire."""


class LineDesordreUpdateError(ValueError):
    """PostgreSQL a refusé la géométrie linéaire proposée."""


class HeritageCreationError(ValueError):
    """La création patrimoniale a été refusée sans objet partiel."""


class HeritageNotFoundError(LookupError):
    """L'objet patrimonial demandé n'existe pas."""


class DesordreCreationError(ValueError):
    """La création du désordre a été refusée sans objet partiel."""


class MobilierNotFoundError(LookupError):
    """Le Mobilier demandé n'existe pas."""


class MobilierValidationError(ValueError):
    """La création ou la modification du Mobilier a été refusée."""


class EquipementMesureNotFoundError(LookupError):
    """L'Équipement de mesure demandé n'existe pas."""


class EquipementMesureValidationError(ValueError):
    """La mutation de l'Équipement de mesure a été refusée."""


class ReseauTechniqueNotFoundError(LookupError):
    """Le Réseau technique demandé n'existe pas."""


class ReseauTechniqueValidationError(ValueError):
    """La mutation du Réseau technique a été refusée."""


class OuvrageParticulierNotFoundError(LookupError):
    """L'ouvrage particulier demandé n'existe pas."""


class OuvrageParticulierValidationError(ValueError):
    """La mutation de l'ouvrage particulier a été refusée."""


class CheminementNotFoundError(LookupError):
    """Le Cheminement demandé n'existe pas."""


class CheminementValidationError(ValueError):
    """Le Cheminement ou ses relations explicites sont invalides."""


class PrestationNotFoundError(LookupError):
    """Prestation introuvable."""


class PrestationValidationError(ValueError):
    """Contrat métier de prestation ou liens natifs invalides."""


class AmenagementHydrauliqueNotFoundError(LookupError):
    """Aménagement hydraulique introuvable."""


class AmenagementHydrauliqueValidationError(ValueError):
    """Payload AH ou relation explicite invalide."""


class ZoneStockageNotFoundError(LookupError):
    """Zone absente de la base métier autorisée."""


class ZoneStockageValidationError(ValueError):
    """Géométrie ou relation de Zone invalide."""


class VegetationNotFoundError(LookupError):
    """La Végétation demandée n'existe pas."""


class VegetationValidationError(ValueError):
    """Une référence ou une géométrie Végétation est invalide."""


def _feature_collection(connection: Any, query: str) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture cartographique PostgreSQL impossible.") from exc

    result = row[0] if row else None
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or result.get("type") != "FeatureCollection":
        raise WebDatabaseError("Réponse GeoJSON PostgreSQL invalide.")
    result.setdefault("features", [])
    return result


def fetch_troncons(connection: Any) -> dict[str, Any]:
    """Retourne les tronçons transformés en EPSG:4326 par PostGIS."""

    return _feature_collection(connection, TRONCONS_GEOJSON_SQL)


def fetch_systemes_endiguement(connection: Any) -> dict[str, Any]:
    """Retourne les hiérarchies SE/Digues et AH, sans géométries."""

    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(SYSTEMES_ENDIGUEMENT_SQL)
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture du patrimoine PostgreSQL impossible.") from exc
    result = row[0] if row else None
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or not isinstance(result.get("systemes"), list):
        raise WebDatabaseError("Réponse hiérarchique PostgreSQL invalide.")
    return result


def fetch_types_desordre(connection: Any) -> dict[str, Any]:
    """Retourne les types de désordre nécessaires au formulaire de création."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(TYPES_DESORDRE_SQL)
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture des types de désordre impossible.") from exc
    result = row[0] if row else None
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or not isinstance(result.get("types"), list):
        raise WebDatabaseError("Réponse du référentiel des désordres invalide.")
    return result


def fetch_categories_desordre(connection: Any) -> dict[str, Any]:
    """Retourne les catégories de désordre déjà présentes en PostgreSQL."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(CATEGORIES_DESORDRE_SQL)
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError(
            "Lecture des catégories de désordre impossible."
        ) from exc
    result = row[0] if row else None
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or not isinstance(
        result.get("categories"), list
    ):
        raise WebDatabaseError("Réponse des catégories de désordre invalide.")
    return result


def fetch_reference_catalog(connection: Any, catalog: str) -> dict[str, Any]:
    """Retourne un catalogue SIRS autorisé, valide et trié."""

    table = REFERENCE_CATALOG_TABLES.get(catalog)
    if table is None:
        raise KeyError(catalog)
    # Le nom de table vient exclusivement de la liste fermée ci-dessus.
    labels = {
        "urgences": "NULL::text AS designation, NULL::text AS abrege",
        "types-mobilier": "NULL::text AS designation, abrege",
        "types-equipement-mesure": "NULL::text AS designation, abrege",
        "types-reseau-technique": "NULL::text AS designation, abrege",
        "types-cheminement": "NULL::text AS designation, abrege",
        "types-amenagement-hydraulique": "NULL::text AS designation, abrege",
        "fonctionnements-amenagement-hydraulique": "designation, NULL::text AS abrege",
        "types-prestation": "designation, NULL::text AS abrege",
        "types-ouvrage-particulier": "designation, NULL::text AS abrege",
        "types-zone-atterrissement-lit": "designation, NULL::text AS abrege",
        "materiaux-cheminement": "designation, NULL::text AS abrege",
        "revetements-cheminement": "designation, NULL::text AS abrege",
        "usages-cheminement": "designation, NULL::text AS abrege",
        "types-voie": "designation, NULL::text AS abrege",
        "orientations-ouvrage": "designation, NULL::text AS abrege",
        "problematiques-vegetation": "NULL::text AS designation, abrege",
        "etats-sanitaires-vegetation": "NULL::text AS designation, abrege",
        "classes-hauteur-vegetation": "NULL::text AS designation, abrege",
        "classes-diametre-vegetation": "NULL::text AS designation, abrege",
        "especes-vegetation": "NULL::text AS designation, abrege",
    }.get(catalog, "designation, abrege")
    query = f"""
        SELECT id, libelle, {labels}
        FROM {table}
        WHERE valid
        ORDER BY libelle, designation, id
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    except Exception as exc:
        raise WebDatabaseError(
            "Lecture du référentiel PostgreSQL impossible."
        ) from exc
    return {
        "references": [
            {
                "id": str(identifier),
                "libelle": libelle,
                "designation": designation,
                "abrege": abrege,
            }
            for row in rows
            for identifier, libelle, designation, abrege in [row[:4]]
        ]
    }


def fetch_troncon_options(connection: Any) -> dict[str, Any]:
    """Retourne tous les tronçons, y compris ceux hors arbre des systèmes."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(TRONCONS_OPTIONS_SQL)
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture des tronçons disponibles impossible.") from exc
    result = row[0] if row else None
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or not isinstance(result.get("troncons"), list):
        raise WebDatabaseError("Réponse des tronçons disponibles invalide.")
    # C5: selectors for the existing canonical editor, scoped by this connection.
    # These are business reference lists, never an expansion of SQL/AI rights.
    from .troncons import CATALOGS
    with connection.cursor() as cursor:
        result['references'] = {}
        for field, table in CATALOGS.items():
            cursor.execute(f'SELECT id,libelle,valid FROM referentiel.{table} ORDER BY libelle,id')
            result['references'][field] = [dict(id=str(i),libelle=l,valid=v) for i,l,v in cursor.fetchall()]
        for key, table in (('digues','digues'),('amenagements','amenagements_hydrauliques'),
                           ('segments','segments_hydrographiques')):
            cursor.execute(f'SELECT id,coalesce(libelle,designation,id::text),valid FROM public.{table} ORDER BY 2,id')
            result[key] = [dict(id=str(i),libelle=l,valid=v) for i,l,v in cursor.fetchall()]
        cursor.execute("SELECT id,est_support_classique,type_physique_id='BERGE' FROM public.troncons")
        roles = {str(i):(classic,berge) for i,classic,berge in cursor.fetchall()}
        for row in result['troncons']:
            row['est_support_classique'],row['est_support_berge'] = roles[row['id']]
    return result


def fetch_troncon_reperage_options(
    connection: Any, troncon_id: UUID
) -> dict[str, Any]:
    """Retourne le système par défaut et ses bornes actives."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(TRONCON_REPERAGE_OPTIONS_SQL, (troncon_id,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture du repérage du tronçon impossible.") from exc
    if not row:
        raise DesordreCreationError("Tronçon absent ou invalide.")
    result = row[0]
    if not isinstance(result, dict):
        raise WebDatabaseError("Réponse de repérage du tronçon invalide.")
    return result


def geometry_from_bornage(connection: Any, troncon_id: UUID, command: BornageGeometryQuery) -> dict[str, Any]:
    """Réutilise le moteur SQL de bornage sans créer d'objet ni de repérage."""
    try:
        options = fetch_troncon_reperage_options(connection, troncon_id)
    except DesordreCreationError as exc:
        raise HTTPException(422, str(exc)) from exc
    if str(command.systeme_reperage_id) != options.get("systeme_reperage_id"):
        raise HTTPException(422, "Le système de repérage du tronçon a changé ou est indisponible.")
    bornes = {str(borne["id"]) for borne in options["bornes"]}
    positions = []
    abscissas = []
    for end in ("debut", "fin"):
        borne = getattr(command, f"borne_{end}_id")
        if borne is None:
            continue
        if str(borne) not in bornes:
            raise HTTPException(422, "La borne ne fait pas partie des références actives du tronçon.")
        distance = getattr(command, f"distance_{end}_m")
        position = getattr(command, f"position_{end}_relative")
        offset = -distance if position == "AVANT_BORNE" else distance
        row = connection.execute(
            "SELECT statut, ST_AsGeoJSON(ST_Transform(point_xy,4326))::jsonb, abscisse_m "
            "FROM public.borne_offset_vers_xy(%s,%s,%s,%s)",
            (troncon_id, command.systeme_reperage_id, borne, offset),
        ).fetchone()
        if not row or row[0] != "OK" or not row[1]:
            raise HTTPException(422, "Ce bornage ne permet pas de calculer une position sur le tronçon.")
        positions.append(row[1]["coordinates"])
        abscissas.append(row[2])
    if len(positions) == 2 and positions[0] == positions[1]:
        raise HTTPException(422, "Le début et la fin doivent définir une ligne non nulle.")
    geometry = ({"type": "Point", "coordinates": positions[0]} if len(positions) == 1
                else {"type": "LineString", "coordinates": positions})
    if len(positions) == 2:
        geometry=connection.execute("WITH axis AS (SELECT ST_Force2D(coalesce(geometry_reperage,geometry)) g FROM public.troncons WHERE id=%s), "
            "part AS (SELECT ST_LineSubstring(g,least(%s,%s)/ST_Length(g),greatest(%s,%s)/ST_Length(g)) g FROM axis) "
            "SELECT ST_AsGeoJSON(ST_Transform(CASE WHEN %s>%s THEN ST_Reverse(g) ELSE g END,4326))::jsonb FROM part",
            (troncon_id,*abscissas,*abscissas,*abscissas)).fetchone()[0]
    return {"troncon_id": str(troncon_id), "systeme_reperage_id": str(command.systeme_reperage_id),
            "geometry": geometry}


def _fetch_created_object(
    connection: Any,
    query: str,
    identifier: UUID,
    *,
    error_message: str,
) -> dict[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (identifier,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError(error_message) from exc
    if not row:
        raise WebDatabaseError("L'objet créé n'a pas pu être relu.")
    result = row[0]
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise WebDatabaseError("Réponse PostgreSQL invalide après création.")
    return result


def fetch_systeme_endiguement(
    connection: Any, systeme_id: UUID
) -> dict[str, Any]:
    return _fetch_created_object(
        connection,
        SYSTEME_ENDIGUEMENT_DETAIL_SQL,
        systeme_id,
        error_message="Relecture du système d'endiguement impossible.",
    )


def fetch_digue(connection: Any, digue_id: UUID) -> dict[str, Any]:
    return _fetch_created_object(
        connection,
        DIGUE_DETAIL_SQL,
        digue_id,
        error_message="Relecture de la digue impossible.",
    )


def fetch_troncon(connection: Any, troncon_id: UUID) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(TRONCON_DETAIL_SQL,(troncon_id,))
            row=cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture du Tronçon impossible.") from exc
    if row is None:raise HeritageNotFoundError("Tronçon introuvable dans cette base.")
    result=json.loads(row[0]) if isinstance(row[0],str) else row[0]
    if not isinstance(result,dict) or result.get('type')!='Feature':
        raise WebDatabaseError("Réponse GeoJSON du Tronçon invalide.")
    return result


def _lit_properties(connection, query, identifier):
    with connection.cursor() as cursor:
        cursor.execute(query, (identifier,))
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(404, "Objet Lit introuvable dans cette base.")
    return row[0]


def fetch_element_morphologique(connection: Any, identifier: UUID) -> dict[str, Any]:
    """GeoJSON de consultation et géométrie native conservée séparément."""
    with connection.transaction():
        properties = _lit_properties(connection, MORPHOLOGY_DETAIL_SQL, identifier)
        return _lit_feature(connection, "elements_morphologiques_lit", properties)


def fetch_troncon_lit(connection: Any, troncon_lit_id: UUID) -> dict[str, Any]:
    """Lit l'objet autonome et toutes ses associations explicites aux Digues.

    Cette lecture ne publie pas sa géométrie dans GEO-STABLE-1.
    """
    with connection.transaction():
        properties = _lit_properties(connection, TRONCON_LIT_FULL_DETAIL_SQL, troncon_lit_id)
        return _lit_feature(connection, "segments_hydrographiques", properties)


def _lit_feature(connection: Any, table: str, properties: dict) -> dict:
    """Une projection de lecture ne réécrit jamais la géométrie source.

    Le savepoint isole les erreurs de projection d'une géométrie historique :
    sa fiche et les autres objets restent disponibles, même avec un SRID nul.
    Les deux noms de tables proviennent exclusivement des lecteurs Lit.
    """
    if table not in {"segments_hydrographiques", "elements_morphologiques_lit"}:
        raise ValueError("Support Lit inconnu")
    geometry, diagnostics = None, []
    identifier = properties["id"]
    # Project the value read in the same SQL snapshot as the attributes/version,
    # not a second mutable read of the object between two concurrent commits.
    native_geometry = properties.get("geometry_ewkt")
    with connection.cursor() as cursor:
        cursor.execute("SELECT ST_SRID(geometry),GeometryType(geometry),"
                       "ST_IsEmpty(geometry),ST_IsValid(geometry) "
                       "FROM (SELECT ST_GeomFromEWKT(%s) AS geometry) source", (native_geometry,))
        srid, shape, empty, topology = cursor.fetchone()
    from .geometry_capabilities import geometry_capabilities
    resource = "troncons-lit" if table == "segments_hydrographiques" else "elements-morphologiques-lit"
    allowed = {value.upper() for value in geometry_capabilities(resource)["allowedTypes"]}
    if shape is None:
        diagnostics.append("GEOMETRIE_ABSENTE")
    else:
        if shape not in allowed:
            diagnostics.append("FORME_HORS_CONTRAT")
        if srid != read_business_crs(connection).srid:
            diagnostics.append("SRID_HORS_CONTRAT")
        if not topology:
            diagnostics.append("TOPOLOGIE_INVALIDE")
        if not srid or empty:
            diagnostics.append("GEOMETRIE_INEXPLOITABLE")
        else:
            try:
                with connection.transaction(), connection.cursor() as cursor:
                    cursor.execute(
                        "WITH projected AS (SELECT ST_Transform(ST_GeomFromEWKT(%s),4326) AS g) "
                        "SELECT ST_AsGeoJSON(g,15,0)::jsonb,"
                        "ST_XMin(g),ST_YMin(g),ST_XMax(g),ST_YMax(g) FROM projected",
                        (native_geometry,),
                    )
                    row = cursor.fetchone()
                    if row[0] is not None and all(value is not None and math.isfinite(value) for value in row[1:]):
                        geometry = row[0]
                    else:
                        diagnostics.append("GEOMETRIE_INEXPLOITABLE")
            except (psycopg.DataError, psycopg.InternalError):
                diagnostics.append("GEOMETRIE_INEXPLOITABLE")
    properties["geometry_diagnostics"] = diagnostics
    return {"type": "Feature", "id": identifier, "geometry": geometry, "properties": properties}


def fetch_troncon_lit_collection(connection: Any) -> dict:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(TRONCON_LIT_DETAIL_SQL.replace("WHERE tl.id=%s", "ORDER BY tl.id"))
        properties = [row[0] for row in cursor.fetchall()]
        return {"type": "FeatureCollection", "features": [
            _lit_feature(connection, "segments_hydrographiques", item) for item in properties]}


def fetch_element_morphologique_collection(connection: Any) -> dict:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(MORPHOLOGY_DETAIL_SQL.replace("WHERE c.id=%s", "ORDER BY c.id"))
        properties = [row[0] for row in cursor.fetchall()]
        return {"type": "FeatureCollection", "features": [
            _lit_feature(connection, "elements_morphologiques_lit", item) for item in properties]}


def fetch_lit_options(connection: Any) -> dict:
    result = {}
    with connection.cursor() as cursor:
        for key, table in (("digues", "digues"), ("troncons_lit", "segments_hydrographiques")):
            cursor.execute("SELECT id,libelle,designation,valid FROM public." + table +
                           " ORDER BY coalesce(libelle,designation,''),id")
            result[key] = [{"id": str(row[0]), "libelle": row[1],
                            "designation": row[2], "valid": row[3]} for row in cursor.fetchall()]
    return result


_LIT_CHARACTERISTIC_TABLES = {
    "largeurs": ("largeurs_lit", {"largeur_moyenne"}, {}),
    "pentes": ("pentes_lit", {"pente_moyenne"}, {}),
    "domanialites": ("domanialites_lit", {"type_id"}, {"type_id": "referentiel.domanialites_lit"}),
    "regimes-ecoulement": ("regimes_ecoulement_lit", {"regime_id"}, {"regime_id": "referentiel.regimes_ecoulement_lit"}),
    "occupations-riveraines": ("occupations_riveraines_lit", {"type_occupation_id", "rive_id"},
                              {"type_occupation_id": "referentiel.occupations_riveraines_lit", "rive_id": "referentiel.rives"}),
}
_LIT_DESCRIPTION_FIELDS = {"designation", "commentaire", "date_debut", "date_fin", "valid", "pr_debut", "pr_fin"}


def _lit_reference(cursor, table, identifier, previous=None):
    if identifier is None or identifier == previous:
        return
    active = " AND valid" if table.startswith("referentiel.") else ""
    cursor.execute(f"SELECT 1 FROM {table} WHERE id=%s{active} FOR KEY SHARE", (identifier,))
    if cursor.fetchone() is None:
        raise HTTPException(422, "La référence choisie est absente de cette base.")


def _lit_write_error(exc):
    if isinstance(exc, psycopg.errors.UniqueViolation):
        raise HTTPException(409, "Une occurrence portant cet UUID existe déjà.") from exc
    if isinstance(exc, (psycopg.IntegrityError, psycopg.DataError)):
        raise HTTPException(422, "La modification ne satisfait pas les références ou le contrat de validité Lit.") from exc
    _raise_unexpected_sql_error(exc)
    raise exc


def _lit_geometry_for_write(cursor, resource, geometry, valid, srid):
    from .geometry_capabilities import validate_geometry_write
    validate_geometry_write(resource, geometry_type=geometry.type if geometry else None,
                            valid=valid, is_create=True, geometry_changed=True)
    if geometry is None:
        return None
    allowed = {"LINESTRING", "POLYGON"} if resource == "troncons-lit" else {"POINT", "LINESTRING", "POLYGON"}
    try:
        return _validated_required_geometry(cursor, geometry, srid, allowed, ValueError, "Lit")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _lit_existing_geometry_valid(cursor, table, identifier, valid, srid, allowed):
    if not valid:
        return
    from .geometry_capabilities import validate_geometry_write
    cursor.execute(f"SELECT GeometryType(geometry) FROM public.{table} WHERE id=%s", (identifier,))
    geometry_type = cursor.fetchone()[0]
    resource = "troncons-lit" if table == "segments_hydrographiques" else "elements-morphologiques-lit"
    validate_geometry_write(resource, geometry_type=geometry_type, valid=valid,
                            is_create=False, geometry_changed=False)
    cursor.execute("SELECT geometry IS NOT NULL AND ST_SRID(geometry)=%s "
                   "AND GeometryType(geometry)=ANY(%s::text[]) "
                   "AND public.geometrie_metier_simple_valide(geometry) "
                   f"FROM public.{table} WHERE id=%s", (srid, sorted(allowed), identifier))
    if cursor.fetchone()[0] is not True:
        raise HTTPException(422, "La géométrie conservée doit être corrigée avant de rendre l'objet valide.")


def _replace_lit_digues(cursor, identifier, requested):
    cursor.execute("SELECT digue_id FROM public.segments_hydrographiques__digues "
                   "WHERE segment_id=%s ORDER BY digue_id", (identifier,))
    current = {row[0] for row in cursor.fetchall()}
    requested = set(requested)
    for digue in sorted(requested - current):
        _lit_reference(cursor, "public.digues", digue)
    for digue in sorted(current - requested):
        cursor.execute("SELECT 1 FROM public.segments_hydrographiques__ouvrages "
                       "WHERE segment_id=%s AND digue_id=%s LIMIT 1", (identifier, digue))
        if cursor.fetchone() is not None:
            raise HTTPException(422, "Cette association Digue porte un détail d'ouvrage à conserver et ne peut pas être retirée ici.")
        cursor.execute("DELETE FROM public.segments_hydrographiques__digues "
                       "WHERE segment_id=%s AND digue_id=%s", (identifier, digue))
    for digue in sorted(requested - current):
        cursor.execute("INSERT INTO public.segments_hydrographiques__digues(segment_id,digue_id) "
                       "VALUES(%s,%s)", (identifier, digue))


def create_troncon_lit(connection: Any, creation) -> dict:
    srid = read_business_crs(connection).srid
    try:
        with connection.transaction(), connection.cursor() as cursor:
            geometry = _lit_geometry_for_write(cursor, "troncons-lit", creation.geometry, creation.valid, srid)
            cursor.execute("INSERT INTO public.segments_hydrographiques "
                           "(id,designation,libelle,commentaire,date_debut,date_fin,valid,geometry) "
                           "VALUES(%s,%s,%s,%s,%s,%s,%s,"
                           "ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s))",
                           (creation.id, creation.designation, creation.libelle, creation.commentaire,
                            creation.date_debut, creation.date_fin, creation.valid, geometry, srid))
            _replace_lit_digues(cursor, creation.id, creation.digue_ids)
            return fetch_troncon_lit(connection, creation.id)
    except psycopg.Error as exc:
        _lit_write_error(exc)


def update_troncon_lit(connection: Any, identifier: UUID, update) -> dict:
    srid = read_business_crs(connection).srid
    values = update.model_dump(exclude_unset=True, exclude={"geometry", "digue_ids"})
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("SELECT valid FROM public.segments_hydrographiques WHERE id=%s FOR UPDATE", (identifier,))
            current = cursor.fetchone()
            if current is None:
                raise HTTPException(404, "Tronçon de lit introuvable.")
            valid = values.get("valid", current[0])
            assignments = [f"{field}=%s" for field in values]
            params = list(values.values())
            if "geometry" in update.model_fields_set:
                geometry = _lit_geometry_for_write(cursor, "troncons-lit", update.geometry, valid, srid)
                assignments.append("geometry=ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s)")
                params.extend((geometry, srid))
            else:
                _lit_existing_geometry_valid(cursor, "segments_hydrographiques", identifier, valid, srid, {"LINESTRING", "POLYGON"})
            if "digue_ids" in update.model_fields_set:
                _replace_lit_digues(cursor, identifier, update.digue_ids)
            cursor.execute("UPDATE public.segments_hydrographiques SET " +
                           (",".join(assignments) or "version=version") + " WHERE id=%s", (*params, identifier))
            return fetch_troncon_lit(connection, identifier)
    except psycopg.Error as exc:
        _lit_write_error(exc)


def create_element_morphologique(connection: Any, creation) -> dict:
    srid = read_business_crs(connection).srid
    values = creation.model_dump(exclude={"geometry"})
    try:
        with connection.transaction(), connection.cursor() as cursor:
            _lit_reference(cursor, "public.segments_hydrographiques", creation.segment_id)
            _lit_reference(cursor, "referentiel.types_zone_atterrissement_lit", creation.type_zone_id)
            geometry = _lit_geometry_for_write(cursor, "elements-morphologiques-lit", creation.geometry, creation.valid, srid)
            cursor.execute("INSERT INTO public.elements_morphologiques_lit (" + ",".join(values) + ",geometry) VALUES (" +
                           ",".join(["%s"] * len(values)) + ",ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s))",
                           (*values.values(), geometry, srid))
            return fetch_element_morphologique(connection, creation.id)
    except psycopg.Error as exc:
        _lit_write_error(exc)


def update_element_morphologique(connection: Any, identifier: UUID, update) -> dict:
    srid = read_business_crs(connection).srid
    values = update.model_dump(exclude_unset=True, exclude={"geometry"})
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("SELECT valid,segment_id,type_zone_id FROM public.elements_morphologiques_lit "
                           "WHERE id=%s FOR UPDATE", (identifier,))
            current = cursor.fetchone()
            if current is None:
                raise HTTPException(404, "Élément morphologique introuvable.")
            if "segment_id" in values:
                _lit_reference(cursor, "public.segments_hydrographiques", values["segment_id"], current[1])
            if "type_zone_id" in values:
                _lit_reference(cursor, "referentiel.types_zone_atterrissement_lit", values["type_zone_id"], current[2])
            valid = values.get("valid", current[0])
            assignments = [f"{field}=%s" for field in values]
            params = list(values.values())
            if "geometry" in update.model_fields_set:
                geometry = _lit_geometry_for_write(cursor, "elements-morphologiques-lit", update.geometry, valid, srid)
                assignments.append("geometry=ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s)")
                params.extend((geometry, srid))
            else:
                _lit_existing_geometry_valid(cursor, "elements_morphologiques_lit", identifier, valid, srid, {"POINT", "LINESTRING", "POLYGON"})
            cursor.execute("UPDATE public.elements_morphologiques_lit SET " +
                           (",".join(assignments) or "version=version") + " WHERE id=%s", (*params, identifier))
            return fetch_element_morphologique(connection, identifier)
    except psycopg.Error as exc:
        _lit_write_error(exc)


def _write_lit_characteristic(connection, parent_id, family, value, identifier=None):
    spec = _LIT_CHARACTERISTIC_TABLES.get(family)
    if spec is None:
        raise HTTPException(404, "Famille de caractéristiques inconnue.")
    table, specific, references = spec
    supplied = value.model_dump(exclude_unset=True)
    if set(supplied) - (_LIT_DESCRIPTION_FIELDS | specific | ({"id"} if identifier is None else set())):
        raise HTTPException(422, "Attribut incompatible avec cette famille.")
    creating = identifier is None
    identifier = value.id if creating else identifier
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("SELECT id FROM public.segments_hydrographiques WHERE id=%s FOR UPDATE", (parent_id,))
            if cursor.fetchone() is None:
                raise HTTPException(404, "Tronçon de lit introuvable.")
            previous = {}
            if not creating:
                cursor.execute(f"SELECT to_jsonb(c) FROM public.{table} c WHERE id=%s AND segment_id=%s FOR UPDATE", (identifier, parent_id))
                row = cursor.fetchone()
                if row is None:
                    raise HTTPException(404, "Caractéristique absente de ce Tronçon de lit.")
                previous = row[0]
            values = supplied if not creating else {
                field: item for field, item in value.model_dump().items()
                if field in _LIT_DESCRIPTION_FIELDS | specific | {"id"}}
            for field, reference_table in references.items():
                if field in values:
                    _lit_reference(cursor, reference_table, values[field], previous.get(field))
            if creating:
                values["segment_id"] = parent_id
                cursor.execute(f"INSERT INTO public.{table}(" + ",".join(values) + ") VALUES(" +
                               ",".join(["%s"] * len(values)) + ")", tuple(values.values()))
            else:
                cursor.execute(f"UPDATE public.{table} SET " + ",".join(f"{field}=%s" for field in values) +
                               " WHERE id=%s AND segment_id=%s", (*values.values(), identifier, parent_id))
            cursor.execute("UPDATE public.segments_hydrographiques SET version=version WHERE id=%s", (parent_id,))
            return fetch_troncon_lit(connection, parent_id)
    except psycopg.Error as exc:
        _lit_write_error(exc)


def create_lit_characteristic(connection, parent_id, family, creation):
    return _write_lit_characteristic(connection, parent_id, family, creation)


def update_lit_characteristic(connection, parent_id, family, identifier, update):
    return _write_lit_characteristic(connection, parent_id, family, update, identifier)


def _creation_error(exc: Exception, fallback: str) -> HeritageCreationError:
    LOGGER.exception("Création d’un objet patrimonial impossible")
    return HeritageCreationError(fallback)


def _validate_reference_values(
    cursor: Any,
    values: dict[str, str | None],
    error_type: type[ValueError],
) -> None:
    """Valide des FK éditables contre une liste fermée de tables."""

    for field, value in values.items():
        if value is None:
            continue
        table, label = EDITABLE_REFERENCE_TABLES[field]
        # Le nom de table vient exclusivement de EDITABLE_REFERENCE_TABLES.
        cursor.execute(
            f"SELECT 1 FROM {table} WHERE id = %s AND valid",  # noqa: S608
            (value,),
        )
        if cursor.fetchone() is None:
            raise error_type(f"{label} est absente ou invalide.")


def create_systeme_endiguement(
    connection: Any, creation: SystemeEndiguementCreate
) -> dict[str, Any]:
    """Insère puis relit un système dans une transaction unique."""

    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.systemes "
                    "(libelle, commentaire, designation, population_protegee, "
                    "classement, niveau_protection, valid) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        creation.libelle, creation.commentaire,
                        creation.designation, creation.population_protegee,
                        creation.classement, creation.niveau_protection,
                        creation.valid,
                    ),
                )
                systeme_id = cursor.fetchone()[0]
            return fetch_systeme_endiguement(connection, systeme_id)
    except (WebDatabaseError, HeritageCreationError):
        raise
    except Exception as exc:
        raise _creation_error(
            exc, "La création du système d’endiguement a échoué."
        ) from exc


def create_digue(connection: Any, creation: DigueCreate) -> dict[str, Any]:
    """Crée une digue seulement sous un système parent actif."""

    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM public.systemes WHERE id = %s AND valid",
                    (creation.systeme_endiguement_id,),
                )
                if cursor.fetchone() is None:
                    raise HeritageCreationError(
                        "Le système d'endiguement parent est absent ou invalide."
                    )
                cursor.execute(
                    "INSERT INTO public.digues "
                    "(systeme_endiguement_id, libelle, commentaire, "
                    "designation, valid) VALUES (%s, %s, %s, %s, %s) "
                    "RETURNING id",
                    (
                        creation.systeme_endiguement_id,
                        creation.libelle,
                        creation.commentaire,
                        creation.designation,
                        creation.valid,
                    ),
                )
                digue_id = cursor.fetchone()[0]
            return fetch_digue(connection, digue_id)
    except (WebDatabaseError, HeritageCreationError):
        raise
    except Exception as exc:
        raise _creation_error(exc, "La création de la digue a échoué.") from exc


def create_troncon(connection: Any, creation: TronconCreate) -> dict[str, Any]:
    from .troncons import write
    return write(connection,creation,fetch=fetch_troncon)


def update_systeme_endiguement(
    connection: Any, systeme_id: UUID, update: SystemeEndiguementUpdate
) -> dict[str, Any]:
    """Met à jour les champs métier éditables d'un système."""

    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.systemes SET libelle = %s, commentaire = %s, "
                    "designation = %s, population_protegee = %s, classement = %s, "
                    "niveau_protection = %s, valid = %s WHERE id = %s RETURNING id",
                    (
                        update.libelle, update.commentaire, update.designation,
                        update.population_protegee, update.classement,
                        update.niveau_protection, update.valid, systeme_id,
                    ),
                )
                if cursor.fetchone() is None:
                    raise HeritageNotFoundError(
                        "Le système d'endiguement est introuvable."
                    )
            return fetch_systeme_endiguement(connection, systeme_id)
    except (WebDatabaseError, HeritageNotFoundError):
        raise
    except Exception as exc:
        raise _creation_error(exc, "La modification du système a échoué.") from exc


def update_digue(
    connection: Any, digue_id: UUID, update: DigueUpdate
) -> dict[str, Any]:
    """Met à jour les champs métier éditables d'une digue."""

    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.digues SET libelle = %s, commentaire = %s, "
                    "designation = %s, valid = %s WHERE id = %s RETURNING id",
                    (
                        update.libelle, update.commentaire, update.designation,
                        update.valid, digue_id,
                    ),
                )
                if cursor.fetchone() is None:
                    raise HeritageNotFoundError("La digue est introuvable.")
            return fetch_digue(connection, digue_id)
    except (WebDatabaseError, HeritageNotFoundError):
        raise
    except Exception as exc:
        raise _creation_error(exc, "La modification de la digue a échoué.") from exc


def update_troncon(connection: Any, troncon_id: UUID, update: TronconUpdate) -> dict[str, Any]:
    from .troncons import write
    return write(connection,update,identifier=troncon_id,fetch=fetch_troncon)


def _validate_desordre_references(cursor: Any, creation: DesordreCreate) -> None:
    _validate_reference_values(cursor, {
        "cote_id": creation.cote_id,
        "position_id": creation.position_id,
        "source_id": creation.source_id,
    }, DesordreCreationError)
    if creation.type_desordre_id is not None:
        cursor.execute(
            "SELECT categorie_id FROM referentiel.types_desordre "
            "WHERE id = %s AND valid",
            (creation.type_desordre_id,),
        )
        type_row = cursor.fetchone()
        if type_row is None:
            raise DesordreCreationError(
                "Le type de désordre est absent ou invalide."
            )
        if (
            creation.categorie_desordre_id is not None
            and type_row[0] != creation.categorie_desordre_id
        ):
            raise DesordreCreationError(
                "Le type de désordre n'appartient pas à la catégorie choisie."
            )
    if creation.categorie_desordre_id is not None:
        cursor.execute(
            "SELECT 1 FROM referentiel.categories_desordre "
            "WHERE id = %s AND valid",
            (creation.categorie_desordre_id,),
        )
        if cursor.fetchone() is None:
            raise DesordreCreationError(
                "La catégorie de désordre est absente ou invalide."
            )
    if creation.troncon_ids:
        cursor.execute(
            "SELECT id FROM public.troncons "
            "WHERE id = ANY(%s::uuid[]) AND valid FOR SHARE",
            (creation.troncon_ids,),
        )
        existing = {row[0] for row in cursor.fetchall()}
        missing = set(creation.troncon_ids) - existing
        if missing:
            raise DesordreCreationError(
                "Au moins un tronçon associé est absent ou invalide."
            )


def _validate_desordre_geometry(
    cursor: Any, creation: DesordreCreate, business_srid: int
) -> str | None:
    if creation.geometry is None:
        return None
    return _validated_required_geometry(
        cursor, creation.geometry, business_srid,
        {"POINT", "LINESTRING", "POLYGON"},
        DesordreCreationError, "Désordre",
    )


def _insert_desordre(
    cursor: Any, creation: DesordreCreate, business_srid: int
) -> UUID:
    common_columns = (
        "type_desordre_id, designation, commentaire, date_debut, date_fin, valid"
    )
    common_values = (
        creation.type_desordre_id,
        creation.designation,
        creation.commentaire,
        creation.date_debut,
        creation.date_fin,
        creation.valid,
    )
    geometry_json = _validate_desordre_geometry(cursor, creation, business_srid)
    if geometry_json is not None:
        target = (
            "public.desordres_points_saisie"
            if creation.geometry.type == "Point"
            else "public.desordres"
        )
        cursor.execute(
            f"INSERT INTO {target} ({common_columns}, geometry) "
            "VALUES (%s, %s, %s, %s, %s, %s, "
            f"ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), {business_srid})) "
            "RETURNING id",
            (*common_values, geometry_json),
        )
    elif creation.line_endpoints is not None:
        debut_x, debut_y = creation.line_endpoints.debut
        fin_x, fin_y = creation.line_endpoints.fin
        business_crs = qualified_business_crs(business_srid)
        source_srid = _endpoint_source_srid(
            creation.line_endpoints.crs, business_crs
        )
        point_sql = f"ST_SetSRID(ST_Point(%s, %s), {business_srid})"
        if source_srid == 4326:
            point_sql = (
                "ST_Transform(ST_SetSRID(ST_Point(%s, %s), 4326), "
                f"{business_srid})"
            )
        cursor.execute(
            "INSERT INTO public.desordres "
            f"({common_columns}, geometry) "
            "VALUES (%s, %s, %s, %s, %s, %s, "
            f"ST_MakeLine({point_sql}, {point_sql})) RETURNING id",
            (*common_values, debut_x, debut_y, fin_x, fin_y),
        )
    elif creation.reperage is not None:
        troncon_id = creation.troncon_ids[0]
        cursor.execute(
            "SELECT systeme_reperage_defaut_id FROM public.troncons "
            "WHERE id = %s",
            (troncon_id,),
        )
        systeme_id = cursor.fetchone()[0]
        if systeme_id is None:
            raise DesordreCreationError(
                "Le tronçon ne possède aucun système de repérage par défaut."
            )
        start_offset = (
            -creation.reperage.distance_debut_m
            if creation.reperage.position_debut_relative == "AVANT_BORNE"
            else 0.0 if creation.reperage.position_debut_relative == "SUR_BORNE"
            else creation.reperage.distance_debut_m
        )
        if creation.geometry_type == "Point":
            cursor.execute(
                "INSERT INTO public.desordres "
                f"({common_columns}, geometry) "
                "SELECT %s, %s, %s, %s, %s, %s, conversion.point_xy "
                "FROM public.borne_offset_vers_xy(%s, %s, %s, %s) "
                "AS conversion WHERE conversion.statut = 'OK' RETURNING id",
                (
                    *common_values, troncon_id, systeme_id,
                    creation.reperage.borne_debut_id, start_offset,
                ),
            )
        else:
            end_offset = (
                -creation.reperage.distance_fin_m
                if creation.reperage.position_fin_relative == "AVANT_BORNE"
                else 0.0 if creation.reperage.position_fin_relative == "SUR_BORNE"
                else creation.reperage.distance_fin_m
            )
            cursor.execute(
                "INSERT INTO public.desordres "
                f"({common_columns}, geometry) "
                "SELECT %s, %s, %s, %s, %s, %s, "
                "ST_MakeLine(debut.point_xy, fin.point_xy) "
                "FROM public.borne_offset_vers_xy(%s, %s, %s, %s) AS debut "
                "CROSS JOIN public.borne_offset_vers_xy(%s, %s, %s, %s) AS fin "
                "WHERE debut.statut = 'OK' AND fin.statut = 'OK' RETURNING id",
                (
                    *common_values,
                    troncon_id, systeme_id,
                    creation.reperage.borne_debut_id, start_offset,
                    troncon_id, systeme_id,
                    creation.reperage.borne_fin_id, end_offset,
                ),
            )
    elif creation.x is not None:
        cursor.execute(
            "INSERT INTO public.desordres_points_saisie "
            f"({common_columns}, coord_x, coord_y) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (*common_values, creation.x, creation.y),
        )
    else:
        cursor.execute(
            "INSERT INTO public.desordres_points_saisie "
            f"({common_columns}, geometry) "
            "VALUES (%s, %s, %s, %s, %s, %s, "
            f"ST_Transform(ST_SetSRID(ST_Point(%s, %s), 4326), {business_srid})) "
            "RETURNING id",
            (*common_values, creation.longitude, creation.latitude),
        )
    row = cursor.fetchone()
    if row is None:
        raise DesordreCreationError("La géométrie proposée est invalide.")
    # La vue de saisie ponctuelle ne porte pas encore ces colonnes.
    # Leur écriture directe conserve le schéma métier sans l'altérer.
    cursor.execute(
        "UPDATE public.desordres SET categorie_desordre_id = %s, lieu_dit = %s, "
        "cote_id = %s, position_id = %s, source_id = %s "
        "WHERE id = %s",
        (
            creation.categorie_desordre_id, creation.lieu_dit,
            creation.cote_id, creation.position_id, creation.source_id, row[0],
        ),
    )
    return row[0]


def create_desordre(connection: Any, creation: DesordreCreate) -> dict[str, Any]:
    """Crée géométrie et liens, puis relit l'état produit par PostGIS."""

    business_srid = read_business_crs(connection).srid
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                _validate_desordre_references(cursor, creation)
                desordre_id = _insert_desordre(cursor, creation, business_srid)
                if creation.troncon_ids:
                    cursor.execute(
                        "INSERT INTO public.desordres__troncons "
                        "(desordre_id, troncon_id) "
                        "SELECT %s, troncon_id FROM unnest(%s::uuid[]) "
                        "AS selected(troncon_id)",
                        (desordre_id, creation.troncon_ids),
                    )
                if creation.reperage is not None:
                    cursor.execute(
                        "SELECT systeme_reperage_defaut_id "
                        "FROM public.troncons WHERE id = %s",
                        (creation.troncon_ids[0],),
                    )
                    systeme_id = cursor.fetchone()[0]
                    cursor.execute(
                        "INSERT INTO public.localisations_desordres ("
                        "desordre_id, troncon_id, systeme_reperage_id, "
                        "borne_debut_id, distance_debut_m, "
                        "position_debut_relative, borne_fin_id, "
                        "distance_fin_m, position_fin_relative, valid) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true) "
                        "ON CONFLICT (desordre_id) DO UPDATE SET "
                        "borne_debut_id = EXCLUDED.borne_debut_id, "
                        "distance_debut_m = EXCLUDED.distance_debut_m, "
                        "position_debut_relative = EXCLUDED.position_debut_relative, "
                        "borne_fin_id = EXCLUDED.borne_fin_id, "
                        "distance_fin_m = EXCLUDED.distance_fin_m, "
                        "position_fin_relative = EXCLUDED.position_fin_relative",
                        (
                            desordre_id, creation.troncon_ids[0], systeme_id,
                            creation.reperage.borne_debut_id,
                            creation.reperage.distance_debut_m,
                            creation.reperage.position_debut_relative,
                            creation.reperage.borne_fin_id,
                            creation.reperage.distance_fin_m,
                            creation.reperage.position_fin_relative,
                        ),
                    )
            return fetch_desordre(connection, desordre_id)
    except (HTTPException, DesordreCreationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, DesordreCreationError, "Désordre")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Création du désordre impossible")
        raise DesordreCreationError(
            "La création du désordre a échoué."
        ) from exc


def _validate_mobilier_references(
    cursor: Any,
    *,
    type_id: str | None = None,
    troncon_id: UUID | None = None,
    validate_type: bool = True,
    validate_troncon: bool = True,
) -> None:
    if validate_type:
        _validate_reference_values(
            cursor,
            {"mobilier_type_id": None if type_id is None else str(type_id)},
            MobilierValidationError,
        )
    if validate_troncon and troncon_id is not None:
        cursor.execute(
            "SELECT 1 FROM public.troncons WHERE id = %s AND valid FOR SHARE",
            (troncon_id,),
        )
        if cursor.fetchone() is None:
            raise MobilierValidationError(
                "Le tronçon associé est absent ou invalide."
            )


def _validated_mobilier_geometry(cursor: Any, geometry: Any, business_srid: int) -> str:
    return _validated_required_geometry(
        cursor, geometry, business_srid, {"POINT"},
        MobilierValidationError, "Mobilier",
    )


def create_mobilier(
    connection: Any, creation: MobilierCreate
) -> dict[str, Any]:
    """Crée un Mobilier puis relit son état autoritaire."""

    business_srid = read_business_crs(connection).srid
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                _validate_mobilier_references(
                    cursor,
                    type_id=creation.type_id,
                    troncon_id=creation.troncon_id,
                )
                geometry_json = _validated_mobilier_geometry(
                    cursor, creation.geometry, business_srid,
                )
                cursor.execute(
                    "INSERT INTO public.mobilier "
                    "(type_id, designation, commentaire, date_debut, geometry, "
                    "troncon_id, valid) VALUES (%s, %s, %s, %s, "
                    "ST_Transform("
                    f"ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), {business_srid}), "
                    "%s, %s) RETURNING id",
                    (
                        str(creation.type_id), creation.designation,
                        creation.commentaire, creation.date_debut,
                        geometry_json, creation.troncon_id,
                        creation.valid,
                    ),
                )
                mobilier_id = cursor.fetchone()[0]
                disorder_plan = _plan_resource_disorders(
                    cursor, MOBILIER_DISORDER_RELATION, mobilier_id,
                    creation.desordre_ids, MobilierValidationError,
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_mobilier(connection, mobilier_id)
    except (MobilierValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, MobilierValidationError, "Mobilier")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Création du Mobilier impossible")
        raise MobilierValidationError(
            "La création du Mobilier a échoué."
        ) from exc


def update_mobilier(
    connection: Any, mobilier_id: UUID, update: MobilierUpdate
) -> dict[str, Any]:
    """Modifie les champs F2 ; une géométrie absente n'efface jamais le Point."""

    values = update.model_dump(exclude_unset=True)
    geometry = update.geometry if "geometry" in update.model_fields_set else None
    requested_disorders = (
        update.desordre_ids
        if "desordre_ids" in update.model_fields_set else RELATION_UNSET
    )
    for key in ("geometry", "desordre_ids"):
        values.pop(key, None)
    business_srid = (
        read_business_crs(connection).srid if geometry is not None else None
    )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT type_id, troncon_id FROM public.mobilier "
                    "WHERE id = %s FOR UPDATE",
                    (mobilier_id,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise MobilierNotFoundError("Mobilier introuvable.")
                _validate_mobilier_references(
                    cursor,
                    type_id=values.get("type_id"),
                    troncon_id=values.get("troncon_id"),
                    validate_type=(
                        "type_id" in values
                        and str(values["type_id"]) != str(current[0])
                    ),
                    validate_troncon=(
                        "troncon_id" in values
                        and values["troncon_id"] != current[1]
                    ),
                )
                if "type_id" in values:
                    values["type_id"] = str(values["type_id"])
                disorder_plan = _plan_resource_disorders(
                    cursor, MOBILIER_DISORDER_RELATION, mobilier_id,
                    requested_disorders, MobilierValidationError,
                )
                assignments = [f"{field} = %s" for field in values]
                parameters = list(values.values())
                if geometry is not None:
                    geometry_json = _validated_mobilier_geometry(cursor, geometry, business_srid)
                    assignments.append(
                        "geometry = ST_Transform(ST_SetSRID("
                        f"ST_GeomFromGeoJSON(%s), 4326), {business_srid})"
                    )
                    parameters.append(geometry_json)
                if not assignments:
                    assignments.append("version = version")
                cursor.execute(
                    "UPDATE public.mobilier SET " + ", ".join(assignments)
                    + " WHERE id = %s RETURNING id",
                    (*parameters, mobilier_id),
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_mobilier(connection, mobilier_id)
    except (MobilierNotFoundError, MobilierValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, MobilierValidationError, "Mobilier")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Modification du Mobilier impossible")
        raise MobilierValidationError(
            "La modification du Mobilier a échoué."
        ) from exc


def _validate_equipement_mesure_references(
    cursor: Any, *, type_id: str | None, troncon_id: UUID | None,
    validate_type: bool = True, validate_troncon: bool = True,
) -> None:
    if validate_type:
        _validate_reference_values(
            cursor, {"equipement_mesure_type_id": type_id},
            EquipementMesureValidationError,
        )
    if validate_troncon and troncon_id is not None:
        cursor.execute(
            "SELECT 1 FROM public.troncons WHERE id = %s AND valid FOR SHARE",
            (troncon_id,),
        )
        if cursor.fetchone() is None:
            raise EquipementMesureValidationError(
                "Le tronçon associé est absent ou invalide."
            )


def _validated_equipement_mesure_geometry(
    cursor: Any, geometry: Any, business_srid: int,
) -> str:
    return _validated_required_geometry(
        cursor, geometry, business_srid, {"POINT"},
        EquipementMesureValidationError, "Équipement de mesure",
    )


def create_equipement_mesure(
    connection: Any, creation: EquipementMesureCreate
) -> dict[str, Any]:
    """Crée un Équipement de mesure puis relit son état autoritaire."""

    business_srid = read_business_crs(connection).srid
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                _validate_equipement_mesure_references(
                    cursor, type_id=creation.type_id,
                    troncon_id=creation.troncon_id,
                )
                geometry_json = _validated_equipement_mesure_geometry(
                    cursor, creation.geometry, business_srid,
                )
                cursor.execute(
                    "INSERT INTO public.equipements_mesure "
                    "(type_id, designation, commentaire, date_debut, geometry, "
                    "troncon_id, valid) VALUES (%s, %s, %s, %s, "
                    "ST_Transform("
                    f"ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), {business_srid}), "
                    "%s, %s) RETURNING id",
                    (
                        creation.type_id, creation.designation, creation.commentaire,
                        creation.date_debut, geometry_json,
                        creation.troncon_id, creation.valid,
                    ),
                )
                identifier = cursor.fetchone()[0]
                disorder_plan = _plan_resource_disorders(
                    cursor, EQUIPEMENT_MESURE_DISORDER_RELATION, identifier,
                    creation.desordre_ids, EquipementMesureValidationError,
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_equipement_mesure(connection, identifier)
    except (EquipementMesureValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, EquipementMesureValidationError, "Équipement de mesure")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Création de l'Équipement de mesure impossible")
        raise EquipementMesureValidationError(
            "La création de l'Équipement de mesure a échoué."
        ) from exc


def update_equipement_mesure(
    connection: Any, identifier: UUID, update: EquipementMesureUpdate
) -> dict[str, Any]:
    """Modifie les champs F3 sans suppression implicite du Point."""

    values = update.model_dump(exclude_unset=True)
    geometry = update.geometry if "geometry" in update.model_fields_set else None
    requested_disorders = (
        update.desordre_ids
        if "desordre_ids" in update.model_fields_set else RELATION_UNSET
    )
    for key in ("geometry", "desordre_ids"):
        values.pop(key, None)
    business_srid = (
        read_business_crs(connection).srid if geometry is not None else None
    )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT type_id, troncon_id FROM public.equipements_mesure "
                    "WHERE id = %s FOR UPDATE", (identifier,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise EquipementMesureNotFoundError(
                        "Équipement de mesure introuvable."
                    )
                _validate_equipement_mesure_references(
                    cursor, type_id=values.get("type_id"),
                    troncon_id=values.get("troncon_id"),
                    validate_type=(
                        "type_id" in values and values["type_id"] != current[0]
                    ),
                    validate_troncon=(
                        "troncon_id" in values
                        and values["troncon_id"] != current[1]
                    ),
                )
                disorder_plan = _plan_resource_disorders(
                    cursor, EQUIPEMENT_MESURE_DISORDER_RELATION, identifier,
                    requested_disorders, EquipementMesureValidationError,
                )
                assignments = [f"{field} = %s" for field in values]
                parameters = list(values.values())
                if geometry is not None:
                    geometry_json = _validated_equipement_mesure_geometry(
                        cursor, geometry, business_srid,
                    )
                    assignments.append(
                        "geometry = ST_Transform(ST_SetSRID("
                        f"ST_GeomFromGeoJSON(%s), 4326), {business_srid})"
                    )
                    parameters.append(geometry_json)
                if not assignments:
                    assignments.append("version = version")
                cursor.execute(
                    "UPDATE public.equipements_mesure SET " + ", ".join(assignments)
                    + " WHERE id = %s RETURNING id", (*parameters, identifier),
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_equipement_mesure(connection, identifier)
    except (
        EquipementMesureNotFoundError, EquipementMesureValidationError,
        WebDatabaseError,
    ):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, EquipementMesureValidationError, "Équipement de mesure")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Modification de l'Équipement de mesure impossible")
        raise EquipementMesureValidationError(
            "La modification de l'Équipement de mesure a échoué."
        ) from exc


def _validate_reseau_technique_references(
    cursor: Any, *, type_id: str | None, troncon_id: UUID | None,
    validate_type: bool = True, validate_troncon: bool = True,
) -> None:
    if validate_type:
        _validate_reference_values(
            cursor, {"reseau_technique_type_id": type_id},
            ReseauTechniqueValidationError,
        )
    if validate_troncon and troncon_id is not None:
        cursor.execute(
            "SELECT 1 FROM public.troncons WHERE id=%s AND valid FOR SHARE",
            (troncon_id,),
        )
        if cursor.fetchone() is None:
            raise ReseauTechniqueValidationError(
                "Le tronçon associé est absent ou invalide."
            )


def _validated_reseau_technique_geometry(cursor: Any, geometry: Any) -> str:
    geometry_json = json.dumps(geometry.model_dump(mode="json"), allow_nan=False)
    cursor.execute(
        "SELECT GeometryType(g), ST_IsValid(g), NOT ST_IsEmpty(g) FROM ("
        "SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) AS g) AS candidate",
        (geometry_json,),
    )
    row = cursor.fetchone()
    if (row is None or row[0] not in {"POINT", "LINESTRING"}
            or not row[1] or not row[2]):
        raise ReseauTechniqueValidationError(
            "La géométrie doit être un Point ou une LineString valide."
        )
    return geometry_json


def create_reseau_technique(
    connection: Any, creation: ReseauTechniqueCreate
) -> dict[str, Any]:
    """Crée un Réseau technique puis relit son état autoritaire."""

    business_srid = read_business_crs(connection).srid
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                _validate_reseau_technique_references(
                    cursor, type_id=creation.type_id,
                    troncon_id=creation.troncon_id,
                )
                geometry_json = (
                    _validated_reseau_technique_geometry(cursor, creation.geometry)
                    if creation.geometry is not None else None
                )
                cursor.execute(
                    "INSERT INTO public.reseaux_techniques "
                    "(type_id,designation,commentaire,date_debut,geometry,"
                    "troncon_id,valid) VALUES(%s,%s,%s,%s,CASE WHEN %s::text "
                    "IS NULL THEN NULL ELSE ST_Transform(ST_SetSRID("
                    f"ST_GeomFromGeoJSON(%s),4326),{business_srid}) END,%s,%s) RETURNING id",
                    (
                        creation.type_id, creation.designation, creation.commentaire,
                        creation.date_debut, geometry_json, geometry_json,
                        creation.troncon_id, creation.valid,
                    ),
                )
                identifier = cursor.fetchone()[0]
                disorder_plan = _plan_resource_disorders(
                    cursor, RESEAU_TECHNIQUE_DISORDER_RELATION, identifier,
                    creation.desordre_ids, ReseauTechniqueValidationError,
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_reseau_technique(connection, identifier)
    except (ReseauTechniqueValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        LOGGER.exception("Création du Réseau technique impossible")
        raise ReseauTechniqueValidationError(
            "La création du Réseau technique a échoué."
        ) from exc


def update_reseau_technique(
    connection: Any, identifier: UUID, update: ReseauTechniqueUpdate
) -> dict[str, Any]:
    """Modifie attributs et géométrie Point/LineString du Réseau."""

    values = update.model_dump(exclude_unset=True)
    geometry = update.geometry if "geometry" in update.model_fields_set else None
    requested_disorders = (
        update.desordre_ids
        if "desordre_ids" in update.model_fields_set else RELATION_UNSET
    )
    for key in ("geometry", "desordre_ids"):
        values.pop(key, None)
    business_srid = (
        read_business_crs(connection).srid if geometry is not None else None
    )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT type_id,troncon_id FROM public.reseaux_techniques "
                    "WHERE id=%s FOR UPDATE", (identifier,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise ReseauTechniqueNotFoundError(
                        "Réseau technique introuvable."
                    )
                _validate_reseau_technique_references(
                    cursor, type_id=values.get("type_id"),
                    troncon_id=values.get("troncon_id"),
                    validate_type=(
                        "type_id" in values and values["type_id"] != current[0]
                    ),
                    validate_troncon=(
                        "troncon_id" in values
                        and values["troncon_id"] != current[1]
                    ),
                )
                disorder_plan = _plan_resource_disorders(
                    cursor, RESEAU_TECHNIQUE_DISORDER_RELATION, identifier,
                    requested_disorders, ReseauTechniqueValidationError,
                )
                assignments = [f"{field}=%s" for field in values]
                parameters = list(values.values())
                if geometry is not None:
                    geometry_json = _validated_reseau_technique_geometry(
                        cursor, geometry
                    )
                    assignments.append(
                        "geometry=ST_Transform(ST_SetSRID("
                        f"ST_GeomFromGeoJSON(%s),4326),{business_srid})"
                    )
                    parameters.append(geometry_json)
                if not assignments:
                    assignments.append("version=version")
                cursor.execute(
                    "UPDATE public.reseaux_techniques SET " + ",".join(assignments)
                    + " WHERE id=%s RETURNING id", (*parameters, identifier),
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_reseau_technique(connection, identifier)
    except (
        ReseauTechniqueNotFoundError, ReseauTechniqueValidationError,
        WebDatabaseError,
    ):
        raise
    except Exception as exc:
        LOGGER.exception("Modification du Réseau technique impossible")
        raise ReseauTechniqueValidationError(
            "La modification du Réseau technique a échoué."
        ) from exc


def _validate_ouvrage_troncon(cursor: Any, troncon_id: UUID | None) -> None:
    if troncon_id is None:
        return
    cursor.execute(
        "SELECT 1 FROM public.troncons WHERE id=%s AND valid FOR SHARE",
        (troncon_id,),
    )
    if cursor.fetchone() is None:
        raise OuvrageParticulierValidationError(
            "Le tronçon associé est absent ou invalide."
        )


def _validated_ouvrage_geometry(cursor: Any, geometry: Any, business_srid: int) -> str:
    return _validated_required_geometry(
        cursor, geometry, business_srid, {"POINT", "LINESTRING", "POLYGON"},
        OuvrageParticulierValidationError, "Ouvrage particulier",
    )


def _validated_optional_simple_geometry(cursor: Any, geometry: Any) -> str:
    """Contrat existant des familles facultatives, indépendant de GEO-1."""
    value = json.dumps(geometry.model_dump(mode="json"), allow_nan=False)
    cursor.execute(
        "SELECT GeometryType(g), ST_IsValid(g), NOT ST_IsEmpty(g) FROM ("
        "SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) AS g) AS candidate",
        (value,),
    )
    row = cursor.fetchone()
    if row is None or row[0] not in {"POINT", "LINESTRING", "POLYGON"} or not row[1] or not row[2]:
        raise ValueError("La géométrie doit être un Point, une LineString ou un Polygon valide.")
    return value


OUVRAGE_PARTICULIER_DISORDER_RELATION = object_disorder_relation(
    "ouvrages-particuliers"
)


def _plan_ouvrage_particulier_disorders(
    cursor: Any, identifier: UUID, requested: object = RELATION_UNSET,
) -> ObjectDisorderRelationPlan | None:
    try:
        return plan_object_disorder_update(
            cursor, OUVRAGE_PARTICULIER_DISORDER_RELATION, identifier, requested,
        )
    except ObjectDisorderRelationValidationError as exc:
        raise OuvrageParticulierValidationError(str(exc)) from exc


def create_ouvrage_particulier(
    connection: Any, creation: OuvrageParticulierCreate
) -> dict[str, Any]:
    """Crée un ouvrage particulier métier, puis le relit."""
    from .geometry_capabilities import validate_geometry_write

    validate_geometry_write(
        "ouvrages-particuliers", geometry_type=creation.geometry.type,
        valid=creation.valid, is_create=True, geometry_changed=True,
    )
    business_srid = read_business_crs(connection).srid
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                _validate_ouvrage_troncon(cursor, creation.troncon_id)
                _validate_reference_values(
                    cursor, {"ouvrage_particulier_type_id": creation.type_id},
                    OuvrageParticulierValidationError,
                )
                geometry = _validated_ouvrage_geometry(
                    cursor, creation.geometry, business_srid,
                )
                cursor.execute(
                    "INSERT INTO public.ouvrages_particuliers "
                    "(type_id,designation,libelle,commentaire,date_debut,date_fin,"
                    "troncon_id,geometry,valid) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,ST_Transform("
                    f"ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),{business_srid}),"
                    "%s) RETURNING id",
                    (creation.type_id, creation.designation, creation.libelle, creation.commentaire,
                     creation.date_debut, creation.date_fin, creation.troncon_id,
                     geometry, creation.valid),
                )
                identifier = cursor.fetchone()[0]
                disorder_plan = _plan_ouvrage_particulier_disorders(
                    cursor, identifier, creation.desordre_ids,
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_ouvrage_particulier(connection, identifier)
    except (OuvrageParticulierValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, OuvrageParticulierValidationError, "Ouvrage particulier")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Création de l'ouvrage particulier impossible")
        raise OuvrageParticulierValidationError(
            "La création de l'ouvrage particulier a échoué."
        ) from exc


def update_ouvrage_particulier(
    connection: Any, identifier: UUID, update: OuvrageParticulierUpdate
) -> dict[str, Any]:
    from .geometry_capabilities import validate_geometry_write

    values = update.model_dump(exclude_unset=True)
    geometry = update.geometry if "geometry" in update.model_fields_set else None
    requested_disorders = (
        update.desordre_ids
        if "desordre_ids" in update.model_fields_set else RELATION_UNSET
    )
    for key in ("desordre_ids", "geometry"):
        values.pop(key, None)
    business_srid = (
        read_business_crs(connection).srid if geometry is not None else None
    )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT troncon_id,GeometryType(geometry),valid,"
                    "public.geometrie_metier_simple_valide(geometry),type_id "
                    "FROM public.ouvrages_particuliers "
                    "WHERE id=%s FOR UPDATE", (identifier,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise OuvrageParticulierNotFoundError(
                        "Ouvrage particulier introuvable."
                    )
                validate_geometry_write(
                    "ouvrages-particuliers",
                    geometry_type=geometry.type if geometry is not None else current[1],
                    valid=values.get("valid", current[2]), is_create=False,
                    geometry_changed="geometry" in update.model_fields_set,
                )
                if (geometry is None and values.get("valid", current[2])
                        and current[3] is not True):
                    raise OuvrageParticulierValidationError(
                        "Une géométrie absente, vide ou spatialement invalide "
                        "ne permet pas de valider l'Ouvrage particulier."
                    )
                if "troncon_id" in values and values["troncon_id"] != current[0]:
                    _validate_ouvrage_troncon(cursor, values["troncon_id"])
                if "type_id" in values and values["type_id"] != current[4]:
                    _validate_reference_values(
                        cursor, {"ouvrage_particulier_type_id": values["type_id"]},
                        OuvrageParticulierValidationError,
                    )
                disorder_plan = _plan_ouvrage_particulier_disorders(
                    cursor, identifier, requested_disorders,
                )
                assignments = [f"{field}=%s" for field in values]
                parameters = list(values.values())
                if geometry is not None:
                    geometry_json = _validated_ouvrage_geometry(cursor, geometry, business_srid)
                    assignments.append(
                        "geometry=ST_Transform(ST_SetSRID("
                        f"ST_GeomFromGeoJSON(%s),4326),{business_srid})"
                    )
                    parameters.append(geometry_json)
                if not assignments:
                    assignments.append("version=version")
                cursor.execute(
                    "UPDATE public.ouvrages_particuliers SET " + ",".join(assignments)
                    + " WHERE id=%s RETURNING id", (*parameters, identifier),
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_ouvrage_particulier(connection, identifier)
    except (HTTPException, OuvrageParticulierNotFoundError, OuvrageParticulierValidationError,
            WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, OuvrageParticulierValidationError, "Ouvrage particulier")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Modification de l'ouvrage particulier impossible")
        raise OuvrageParticulierValidationError(
            "La modification de l'ouvrage particulier a échoué."
        ) from exc


CHEMINEMENT_EDITABLE_FIELDS = (
    "type_cheminement_id", "designation", "libelle", "commentaire",
    "date_debut", "date_fin", "largeur", "dimension_horizontale",
    "dimension_verticale", "numero_secteur", "materiau_id", "revetement_id",
    "usage_id", "statut_acces", "type_voie_id", "position_id", "cote_id",
    "securite_id", "orientation_id", "position_haut_id", "position_bas_id",
    "revetement_haut_id", "revetement_bas_id", "source_id", "valid",
)

CHEMINEMENT_REFERENCE_FIELDS = {
    "materiau_id": "cheminement_materiau_id",
    "revetement_id": "cheminement_revetement_id",
    "usage_id": "cheminement_usage_id",
    "type_voie_id": "cheminement_type_voie_id",
    "position_id": "position_id",
    "cote_id": "cote_id",
    "securite_id": "cheminement_securite_id",
    "orientation_id": "cheminement_orientation_id",
    "position_haut_id": "position_id",
    "position_bas_id": "position_id",
    "revetement_haut_id": "cheminement_revetement_haut_id",
    "revetement_bas_id": "cheminement_revetement_bas_id",
    "source_id": "source_id",
}


def _validate_cheminement_references(cursor: Any, values: dict[str, Any]) -> None:
    for field, reference_field in CHEMINEMENT_REFERENCE_FIELDS.items():
        if field in values:
            _validate_reference_values(
                cursor, {reference_field: values[field]}, CheminementValidationError,
            )

CHEMINEMENT_DISORDER_RELATION = object_disorder_relation("cheminements")


def _validate_cheminement_type(cursor: Any, type_id: str) -> None:
    _validate_reference_values(
        cursor, {"cheminement_type_id": type_id}, CheminementValidationError,
    )


def _validated_cheminement_geometry(
    cursor: Any, geometry: Any, business_srid: int, type_id: str,
) -> str:
    return _validated_required_geometry(
        cursor, geometry, business_srid,
        {value.upper() for value in cheminement_geometry_types(type_id)},
        CheminementValidationError, "Cheminement",
    )


def _validate_cheminement_geometry_type(type_id: str, geometry_type: str) -> None:
    allowed_types = {
        value.upper() for value in cheminement_geometry_types(type_id)
    }
    if geometry_type.upper() not in allowed_types:
        raise CheminementValidationError(
            "La géométrie Polygon est réservée au type "
            "Chemin d'accès technique (CAC)."
        )


def _validate_cheminement_troncons(cursor: Any, identifiers: list[UUID]) -> None:
    for identifier in identifiers:
        cursor.execute(
            "SELECT 1 FROM public.troncons WHERE id=%s AND valid FOR SHARE",
            (identifier,),
        )
        if cursor.fetchone() is None:
            raise CheminementValidationError(
                f"Référence troncons absente ou invalide : {identifier}."
            )


def _replace_cheminement_troncons(
    cursor: Any, cheminement_id: UUID, identifiers: list[UUID],
) -> None:
    cursor.execute(
        "DELETE FROM public.cheminements__troncons WHERE cheminement_id=%s",
        (cheminement_id,),
    )
    for identifier in identifiers:
        cursor.execute(
            "INSERT INTO public.cheminements__troncons "
            "(cheminement_id,troncon_id) VALUES (%s,%s)",
            (cheminement_id, identifier),
        )


def _plan_cheminement_disorders(
    cursor: Any, cheminement_id: UUID, requested: object = RELATION_UNSET,
) -> ObjectDisorderRelationPlan | None:
    try:
        return plan_object_disorder_update(
            cursor, CHEMINEMENT_DISORDER_RELATION, cheminement_id, requested,
        )
    except ObjectDisorderRelationValidationError as exc:
        raise CheminementValidationError(str(exc)) from exc


def create_cheminement(connection: Any, creation: CheminementCreate) -> dict[str, Any]:
    business_srid = read_business_crs(connection).srid
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                _validate_cheminement_type(cursor, creation.type_cheminement_id)
                _validate_cheminement_troncons(cursor, creation.troncon_ids)
                geometry = _validated_cheminement_geometry(
                    cursor, creation.geometry, business_srid,
                    creation.type_cheminement_id,
                )
                scalar = creation.model_dump(exclude={"troncon_ids", "desordre_ids", "geometry"})
                _validate_cheminement_references(cursor, scalar)
                fields = tuple(CHEMINEMENT_EDITABLE_FIELDS)
                cursor.execute(
                    "INSERT INTO public.cheminements (" + ",".join(fields) + ",geometry) "
                    "VALUES (" + ",".join(["%s"] * len(fields)) + ",ST_Transform("
                    f"ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),{business_srid})) RETURNING id",
                    (*[scalar[name] for name in fields], geometry),
                )
                identifier = cursor.fetchone()[0]
                _replace_cheminement_troncons(
                    cursor, identifier, creation.troncon_ids,
                )
                disorder_plan = _plan_cheminement_disorders(
                    cursor, identifier, creation.desordre_ids,
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_cheminement(connection, identifier)
    except (CheminementValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, CheminementValidationError, "Cheminement")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Création du Cheminement impossible")
        raise CheminementValidationError("La création du Cheminement a échoué.") from exc


def update_cheminement(
    connection: Any, identifier: UUID, update: CheminementUpdate,
) -> dict[str, Any]:
    values = update.model_dump(exclude_unset=True)
    geometry = update.geometry if "geometry" in update.model_fields_set else None
    requested_disorders = (
        update.desordre_ids
        if "desordre_ids" in update.model_fields_set else RELATION_UNSET
    )
    for key in ("troncon_ids", "desordre_ids", "geometry"):
        values.pop(key, None)
    business_srid = (
        read_business_crs(connection).srid if geometry is not None else None
    )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT type_cheminement_id, GeometryType(geometry), to_jsonb(cheminements) "
                    "FROM public.cheminements WHERE id=%s FOR UPDATE",
                    (identifier,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise CheminementNotFoundError("Cheminement introuvable.")
                if "type_cheminement_id" in values and values["type_cheminement_id"] != row[0]:
                    _validate_cheminement_type(cursor, values["type_cheminement_id"])
                _validate_cheminement_references(cursor, {
                    field: value for field, value in values.items()
                    if value != row[2].get(field)
                })
                final_type_id = values.get("type_cheminement_id", row[0])
                if geometry is None:
                    _validate_cheminement_geometry_type(final_type_id, row[1])
                if update.troncon_ids is not None:
                    _validate_cheminement_troncons(cursor, update.troncon_ids)
                disorder_plan = _plan_cheminement_disorders(
                    cursor, identifier, requested_disorders,
                )
                assignments = [f"{field}=%s" for field in CHEMINEMENT_EDITABLE_FIELDS if field in values]
                parameters = [values[field] for field in CHEMINEMENT_EDITABLE_FIELDS if field in values]
                if geometry is not None:
                    geojson = _validated_cheminement_geometry(
                        cursor, geometry, business_srid, final_type_id,
                    )
                    assignments.append(
                        "geometry=ST_Transform(ST_SetSRID("
                        f"ST_GeomFromGeoJSON(%s),4326),{business_srid})"
                    )
                    parameters.append(geojson)
                # Also advance the aggregate version for a relation-only replacement.
                if not assignments:
                    assignments.append("version=version")
                cursor.execute(
                    "UPDATE public.cheminements SET " + ",".join(assignments)
                    + " WHERE id=%s", (*parameters, identifier),
                )
                if update.troncon_ids is not None:
                    _replace_cheminement_troncons(
                        cursor, identifier, update.troncon_ids,
                    )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_cheminement(connection, identifier)
    except (CheminementNotFoundError, CheminementValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, CheminementValidationError, "Cheminement")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Modification du Cheminement impossible")
        raise CheminementValidationError("La modification du Cheminement a échoué.") from exc




def _validate_prestation_type(cursor: Any, type_id: str) -> None:
    cursor.execute(
        "SELECT 1 FROM referentiel.types_prestation WHERE id=%s AND valid IS TRUE FOR SHARE",
        (type_id,),
    )
    if cursor.fetchone() is None:
        raise PrestationValidationError("Le type de prestation est absent ou inactif.")


def _prestation_geometry(cursor: Any, geometry: Any) -> str:
    try:
        return _validated_optional_simple_geometry(cursor, geometry)
    except ValueError as exc:
        raise PrestationValidationError(str(exc)) from exc


def _prestation_existing_links(
    cursor: Any, identifier: UUID, table: str, column: str,
) -> set[UUID]:
    # Couples table/colonne internes exclusivement ; aucune interpolation de l'API.
    allowed = {
        ("public.prestations_troncons", "troncon_id"),
        ("public.prestations_intervenants", "contact_id"),
        *{("public.prestations_objets", name) for name in PRESTATION_OBJECT_FAMILIES.values()},
    }
    if (table, column) not in allowed:
        raise ValueError((table, column))
    cursor.execute(
        f"SELECT {column} FROM {table} WHERE prestation_id=%s "
        f"AND {column} IS NOT NULL", (identifier,),
    )
    return {row[0] for row in cursor.fetchall()}


def _validate_prestation_ids(
    cursor: Any, table: str, identifiers: list[UUID], existing: set[UUID],
) -> None:
    if table not in {*PRESTATION_OBJECT_FAMILIES, "troncons", "contacts"}:
        raise ValueError(table)
    for identifier in identifiers:
        if identifier in existing:
            continue
        cursor.execute(
            f"SELECT 1 FROM public.{table} WHERE id=%s AND valid IS TRUE FOR SHARE",
            (identifier,),
        )
        if cursor.fetchone() is None:
            raise PrestationValidationError(
                f"Relation {table} absente ou inactive : {identifier}."
            )


def _validate_prestation_links(
    cursor: Any, identifier: UUID | None,
    troncon_ids: list[UUID] | None,
    object_ids: dict[str, list[UUID]] | None,
    intervenant_ids: list[UUID] | None,
) -> None:
    if troncon_ids is not None:
        existing = (_prestation_existing_links(
            cursor, identifier, "public.prestations_troncons", "troncon_id"
        ) if identifier else set())
        _validate_prestation_ids(cursor, "troncons", troncon_ids, existing)
    if object_ids is not None:
        for family, ids in object_ids.items():
            column = PRESTATION_OBJECT_FAMILIES[family]
            existing = (_prestation_existing_links(
                cursor, identifier, "public.prestations_objets", column
            ) if identifier else set())
            _validate_prestation_ids(cursor, family, ids, existing)
    if intervenant_ids is not None:
        existing = (_prestation_existing_links(
            cursor, identifier, "public.prestations_intervenants", "contact_id"
        ) if identifier else set())
        _validate_prestation_ids(cursor, "contacts", intervenant_ids, existing)


def _replace_prestation_links(
    cursor: Any, identifier: UUID,
    troncon_ids: list[UUID] | None,
    object_ids: dict[str, list[UUID]] | None,
    intervenant_ids: list[UUID] | None,
) -> None:
    if troncon_ids is not None:
        cursor.execute(
            "DELETE FROM public.prestations_troncons WHERE prestation_id=%s",
            (identifier,),
        )
        for troncon_id in troncon_ids:
            cursor.execute(
                "INSERT INTO public.prestations_troncons "
                "(prestation_id,troncon_id) VALUES (%s,%s)",
                (identifier, troncon_id),
            )
    if object_ids is not None:
        # Les familles historiques hors DTO restent liées à la Prestation.
        editable_targets = " OR ".join(
            f"{column} IS NOT NULL" for column in PRESTATION_OBJECT_FAMILIES.values()
        )
        cursor.execute(
            "DELETE FROM public.prestations_objets WHERE prestation_id=%s AND ("
            + editable_targets + ")",
            (identifier,),
        )
        for family, identifiers in object_ids.items():
            column = PRESTATION_OBJECT_FAMILIES[family]
            for target_id in identifiers:
                cursor.execute(
                    f"INSERT INTO public.prestations_objets (prestation_id,{column}) "
                    "VALUES (%s,%s)", (identifier, target_id),
                )
    if intervenant_ids is not None:
        cursor.execute(
            "DELETE FROM public.prestations_intervenants WHERE prestation_id=%s",
            (identifier,),
        )
        for contact_id in intervenant_ids:
            cursor.execute(
                "INSERT INTO public.prestations_intervenants "
                "(prestation_id,contact_id) VALUES (%s,%s)",
                (identifier, contact_id),
            )


def create_prestation(connection: Any, creation: PrestationCreate) -> dict[str, Any]:
    business_srid = read_business_crs(connection).srid
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                if creation.type_prestation_id is not None:
                    _validate_prestation_type(cursor, creation.type_prestation_id)
                _validate_prestation_links(
                    cursor, None, creation.troncon_ids,
                    creation.object_ids, creation.intervenant_ids,
                )
                geometry = (_prestation_geometry(cursor, creation.geometry)
                            if creation.geometry is not None else None)
                values = creation.model_dump(exclude={
                    "troncon_ids", "object_ids", "intervenant_ids", "geometry",
                    "type_prestation_id", "id",
                })
                cursor.execute(
                    "INSERT INTO public.prestations (id,type_prestation_id,"
                    + ",".join(PRESTATION_FIELDS) + ",geometry) VALUES "
                    "(coalesce(%s,gen_random_uuid()),%s,"
                    + ",".join(["%s"] * len(PRESTATION_FIELDS))
                    + ",CASE WHEN %s::text IS NULL THEN NULL ELSE "
                    "ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),"
                    f"{business_srid}) END) "
                    "RETURNING id",
                    (creation.id, creation.type_prestation_id,
                     *[values[field] for field in PRESTATION_FIELDS],
                     geometry, geometry),
                )
                identifier = cursor.fetchone()[0]
                _replace_prestation_links(
                    cursor, identifier, creation.troncon_ids,
                    creation.object_ids, creation.intervenant_ids,
                )
            return fetch_prestation(connection, identifier)
    except (PrestationValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        LOGGER.exception("Création de la prestation impossible")
        raise PrestationValidationError("La création de la prestation a échoué.") from exc


def update_prestation(
    connection: Any, identifier: UUID, update: PrestationUpdate,
) -> dict[str, Any]:
    values = update.model_dump(exclude_unset=True, exclude={
        "troncon_ids", "object_ids", "intervenant_ids", "geometry",
        "type_prestation_id",
    })
    business_srid = (
        read_business_crs(connection).srid
        if update.geometry is not None
        else None
    )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT type_prestation_id,date_fin "
                    "FROM public.prestations WHERE id=%s FOR UPDATE",
                    (identifier,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise PrestationNotFoundError("Prestation introuvable.")
                if "type_prestation_id" in update.model_fields_set:
                    if update.type_prestation_id is not None and update.type_prestation_id != row[0]:
                        _validate_prestation_type(cursor, update.type_prestation_id)
                    values["type_prestation_id"] = update.type_prestation_id
                if "date_fin" in values and values["date_fin"] is None:
                    raise PrestationValidationError(
                        "La date de fin d'une action réalisée est obligatoire."
                    )
                _validate_prestation_links(
                    cursor, identifier, update.troncon_ids,
                    update.object_ids, update.intervenant_ids,
                )
                assignments = [f"{field}=%s" for field in (*PRESTATION_FIELDS, "type_prestation_id")
                               if field in values]
                parameters = [values[field] for field in (*PRESTATION_FIELDS, "type_prestation_id")
                              if field in values]
                if update.geometry is not None:
                    geometry = _prestation_geometry(cursor, update.geometry)
                    assignments.append(
                        "geometry=ST_Transform(ST_SetSRID("
                        f"ST_GeomFromGeoJSON(%s),4326),{business_srid})"
                    )
                    parameters.append(geometry)
                if not assignments:
                    assignments.append("version=version")
                cursor.execute(
                    "UPDATE public.prestations SET " + ",".join(assignments)
                    + " WHERE id=%s", (*parameters, identifier),
                )
                _replace_prestation_links(
                    cursor, identifier, update.troncon_ids,
                    update.object_ids, update.intervenant_ids,
                )
            return fetch_prestation(connection, identifier)
    except (PrestationNotFoundError, PrestationValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        LOGGER.exception("Modification de la prestation impossible")
        raise PrestationValidationError("La modification de la prestation a échoué.") from exc


AMENAGEMENT_HYDRAULIQUE_FIELDS = (
    "designation", "libelle", "commentaire",
    "date_debut", "date_fin", "valid",
)


def _replace_amenagement_hydraulique_troncons(cursor, identifier, troncon_ids, versions):
    """Compare each changed child's editor version, under ordered row locks."""
    from .troncons import version_conflict
    cursor.execute("SELECT id FROM public.troncons WHERE amenagement_hydraulique_id=%s", (identifier,))
    previous={r[0] for r in cursor.fetchall()}; requested=set(troncon_ids)
    cursor.execute("SELECT id,version,amenagement_hydraulique_id FROM public.troncons "
                   "WHERE id=ANY(%s::uuid[]) ORDER BY id FOR UPDATE", (list(previous|requested),))
    rows={r[0]:r for r in cursor.fetchall()}
    if requested-rows.keys():
        raise AmenagementHydrauliqueValidationError("Un Tronçon choisi est absent de cette base.")
    changed=previous ^ requested
    for key in sorted(changed,key=str):
        row=rows[key]
        if key in requested and row[2] is not None and row[2]!=identifier:
            raise HTTPException(409,{'code':'troncon-already-attached','resource':'troncons','id':str(key),
                'currentVersion':row[1],'message':'Ce Tronçon appartient déjà à un autre AH.'})
        expected=versions.get(key)
        if expected is None:
            raise HTTPException(428,{'code':'troncon-version-required','resource':'troncons','id':str(key),
                'message':'La version de départ de chaque Tronçon dont le rattachement change est obligatoire.'})
        if row[1]!=expected:version_conflict(key,expected,row[1])
    result={}
    for key in sorted(changed,key=str):
        cursor.execute("UPDATE public.troncons SET amenagement_hydraulique_id=%s WHERE id=%s RETURNING version",
                       (identifier if key in requested else None,key))
        result[str(key)]=cursor.fetchone()[0]
    return result


def _replace_amenagement_hydraulique_zones(cursor: Any, identifier: UUID,
                                           zone_ids: list[UUID]) -> None:
    cursor.execute("SELECT id FROM public.zones_stockage WHERE amenagement_hydraulique_id=%s FOR UPDATE", (identifier,))
    previous = {row[0] for row in cursor.fetchall()}
    requested = set(zone_ids)
    for zone_id in sorted(previous - requested):
        cursor.execute("UPDATE public.zones_stockage SET amenagement_hydraulique_id=NULL WHERE id=%s", (zone_id,))
    for zone_id in sorted(requested - previous):
        cursor.execute("UPDATE public.zones_stockage SET amenagement_hydraulique_id=%s "
                       "WHERE id=%s AND amenagement_hydraulique_id IS NULL RETURNING id",
                       (identifier, zone_id))
        if cursor.fetchone() is None:
            raise AmenagementHydrauliqueValidationError(
                f"Zone absente ou déjà rattachée à un autre AH : {zone_id}."
            )


def create_amenagement_hydraulique(
    connection: Any, creation: AmenagementHydrauliqueCreate,
) -> dict[str, Any]:
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                values = creation.model_dump(exclude={"troncon_ids", "troncon_versions", "zone_ids", "desordre_ids"})
                cursor.execute(
                    "INSERT INTO public.amenagements_hydrauliques ("
                    + ",".join(AMENAGEMENT_HYDRAULIQUE_FIELDS) + ") VALUES ("
                    + ",".join(["%s"] * len(AMENAGEMENT_HYDRAULIQUE_FIELDS))
                    + ") RETURNING id",
                    tuple(values[field] for field in AMENAGEMENT_HYDRAULIQUE_FIELDS),
                )
                identifier = cursor.fetchone()[0]
                changed_troncons = _replace_amenagement_hydraulique_troncons(
                    cursor, identifier, creation.troncon_ids, creation.troncon_versions,
                )
                _replace_amenagement_hydraulique_zones(cursor, identifier, creation.zone_ids)
                disorder_plan = _plan_resource_disorders(
                    cursor, AMENAGEMENT_HYDRAULIQUE_DISORDER_RELATION,
                    identifier, creation.desordre_ids,
                    AmenagementHydrauliqueValidationError,
                )
                apply_object_disorder_update(cursor, disorder_plan)
            result=fetch_amenagement_hydraulique(connection, identifier)
            if changed_troncons:result['_related_mutations']={'troncons':changed_troncons}
            return result
    except (HTTPException, AmenagementHydrauliqueValidationError, WebDatabaseError):
        raise
    except ObjectDisorderRelationConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "disorder-already-associated", "message": str(exc)},
        ) from exc
    except Exception as exc:
        LOGGER.exception("Création de l'aménagement hydraulique impossible")
        raise AmenagementHydrauliqueValidationError(
            "La création de l'aménagement hydraulique a échoué."
        ) from exc


def update_amenagement_hydraulique(
    connection: Any, identifier: UUID, update: AmenagementHydrauliqueUpdate,
) -> dict[str, Any]:
    values = update.model_dump(
        exclude_unset=True,
        exclude={"troncon_ids", "troncon_versions", "zone_ids", "desordre_ids"},
    )
    requested_disorders = (
        update.desordre_ids
        if "desordre_ids" in update.model_fields_set else RELATION_UNSET
    )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM public.amenagements_hydrauliques "
                    "WHERE id=%s FOR UPDATE", (identifier,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise AmenagementHydrauliqueNotFoundError(
                        "Aménagement hydraulique introuvable."
                    )
                changed_troncons = {}
                disorder_plan = _plan_resource_disorders(
                    cursor, AMENAGEMENT_HYDRAULIQUE_DISORDER_RELATION,
                    identifier, requested_disorders,
                    AmenagementHydrauliqueValidationError,
                )
                assignments = [f"{field}=%s" for field in AMENAGEMENT_HYDRAULIQUE_FIELDS
                               if field in values]
                parameters = [values[field] for field in AMENAGEMENT_HYDRAULIQUE_FIELDS
                              if field in values]
                if not assignments:
                    assignments.append("version=version")
                cursor.execute(
                    "UPDATE public.amenagements_hydrauliques SET "
                    + ",".join(assignments) + " WHERE id=%s",
                    (*parameters, identifier),
                )
                if update.troncon_ids is not None:
                    changed_troncons = _replace_amenagement_hydraulique_troncons(
                        cursor, identifier, update.troncon_ids, update.troncon_versions,
                    )
                if update.zone_ids is not None:
                    _replace_amenagement_hydraulique_zones(cursor, identifier, update.zone_ids)
                apply_object_disorder_update(cursor, disorder_plan)
            result=fetch_amenagement_hydraulique(connection, identifier)
            if changed_troncons:result['_related_mutations']={'troncons':changed_troncons}
            return result
    except (HTTPException, AmenagementHydrauliqueNotFoundError,
            AmenagementHydrauliqueValidationError, WebDatabaseError):
        raise
    except ObjectDisorderRelationConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "disorder-already-associated", "message": str(exc)},
        ) from exc
    except Exception as exc:
        LOGGER.exception("Modification de l'aménagement hydraulique impossible")
        raise AmenagementHydrauliqueValidationError(
            "La modification de l'aménagement hydraulique a échoué."
        ) from exc


ZONE_STOCKAGE_FIELDS = (
    "type_id", "designation", "libelle", "commentaire", "date_debut", "date_fin",
    "capacite_stockage", "profondeur_moyenne", "superficie",
    "collectivite_competente_id", "fonctionnement_id", "amenagement_hydraulique_id", "valid",
)


def _validate_zone_references(
    cursor: Any, values: dict[str, Any], *, previous: dict[str, Any] | None = None,
) -> None:
    for field, table in (
        ("type_id", "referentiel.types_amenagement_hydraulique"),
        ("fonctionnement_id", "referentiel.fonctionnements_amenagement_hydraulique"),
    ):
        value = values.get(field)
        if value is not None and (previous is None or value != previous.get(field)):
            cursor.execute(f"SELECT 1 FROM {table} WHERE id=%s AND valid FOR SHARE", (value,))
            if cursor.fetchone() is None:
                raise ZoneStockageValidationError(f"{field} absent ou invalide.")
    for field, table in (
        ("collectivite_competente_id", "public.organismes"),
        ("amenagement_hydraulique_id", "public.amenagements_hydrauliques"),
    ):
        value = values.get(field)
        if value is not None:
            cursor.execute(f"SELECT 1 FROM {table} WHERE id=%s FOR KEY SHARE", (value,))
            if cursor.fetchone() is None:
                raise ZoneStockageValidationError(f"{field} absent de cette base.")


def _replace_zone_reseaux(cursor: Any, identifier: UUID, ids: list[UUID]) -> None:
    for reseau_id in ids:
        cursor.execute("SELECT 1 FROM public.reseaux_hydrauliques WHERE id=%s FOR KEY SHARE", (reseau_id,))
        if cursor.fetchone() is None:
            raise ZoneStockageValidationError(f"Réseau hydraulique absent : {reseau_id}.")
    cursor.execute("DELETE FROM public.reseaux_hydrauliques__zones_stockage WHERE zone_stockage_id=%s", (identifier,))
    for reseau_id in ids:
        cursor.execute("INSERT INTO public.reseaux_hydrauliques__zones_stockage "
                       "(reseau_hydraulique_id,zone_stockage_id) VALUES (%s,%s)",
                       (reseau_id, identifier))


def create_zone_stockage(connection: Any, creation: ZoneStockageCreate) -> dict[str, Any]:
    business_srid = read_business_crs(connection).srid
    if creation.valid is not True:
        raise ZoneStockageValidationError("Une Zone native doit être valide.")
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                values = creation.model_dump(exclude={"geometry", "reseau_hydraulique_ids"})
                _validate_zone_references(cursor, values)
                geometry = _validated_required_geometry(cursor, creation.geometry, business_srid,
                    {"POLYGON"}, ZoneStockageValidationError, "Zone de stockage")
                cursor.execute("INSERT INTO public.zones_stockage ("
                    + ",".join(ZONE_STOCKAGE_FIELDS) + ",geometry) VALUES ("
                    + ",".join(["%s"] * len(ZONE_STOCKAGE_FIELDS))
                    + f",ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),{business_srid})) RETURNING id",
                    (*[values[field] for field in ZONE_STOCKAGE_FIELDS], geometry))
                identifier = cursor.fetchone()[0]
                _replace_zone_reseaux(cursor, identifier, creation.reseau_hydraulique_ids)
            return fetch_zone_stockage(connection, identifier)
    except (ZoneStockageValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        LOGGER.exception("Création de Zone impossible")
        raise ZoneStockageValidationError("La création de la Zone a échoué.") from exc


def update_zone_stockage(connection: Any, identifier: UUID,
                         update: ZoneStockageUpdate) -> dict[str, Any]:
    values = update.model_dump(exclude_unset=True, exclude={"geometry", "reseau_hydraulique_ids"})
    business_srid = read_business_crs(connection).srid if update.geometry is not None or values.get("valid") is True else None
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SELECT id,geometry IS NOT NULL AND GeometryType(geometry)='POLYGON' "
                               "AND NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry) "
                               "AND ST_SRID(geometry)=%s,type_id,fonctionnement_id FROM public.zones_stockage "
                               "WHERE id=%s FOR UPDATE", (business_srid or 0, identifier))
                existing = cursor.fetchone()
                if existing is None:
                    raise ZoneStockageNotFoundError("Zone de stockage introuvable.")
                if values.get("valid") is True and update.geometry is None and existing[1] is not True:
                    raise ZoneStockageValidationError(
                        "Corrigez explicitement la géométrie historique avant de qualifier la Zone valide.")
                _validate_zone_references(cursor, values, previous={
                    "type_id": existing[2], "fonctionnement_id": existing[3],
                })
                assignments = [f"{field}=%s" for field in ZONE_STOCKAGE_FIELDS if field in values]
                parameters = [values[field] for field in ZONE_STOCKAGE_FIELDS if field in values]
                if update.geometry is not None:
                    geometry = _validated_required_geometry(cursor, update.geometry, business_srid,
                        {"POLYGON"}, ZoneStockageValidationError, "Zone de stockage")
                    assignments.append(f"geometry=ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),{business_srid})")
                    parameters.append(geometry)
                if not assignments:
                    assignments.append("version=version")
                cursor.execute("UPDATE public.zones_stockage SET " + ",".join(assignments)
                               + " WHERE id=%s", (*parameters, identifier))
                if update.reseau_hydraulique_ids is not None:
                    _replace_zone_reseaux(cursor, identifier, update.reseau_hydraulique_ids)
            return fetch_zone_stockage(connection, identifier)
    except (ZoneStockageNotFoundError, ZoneStockageValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        LOGGER.exception("Modification de Zone impossible")
        raise ZoneStockageValidationError("La modification de la Zone a échoué.") from exc


VEGETATION_EDITABLE_FIELDS = (
    "famille_vegetation", "disposition", "sous_type", "invasif",
    "designation", "commentaire", "date_debut", "date_fin",
    "contact_eau", "densite", "hauteur_m", "diametre_cm",
    "type_cote_id", "type_position_id", "etat_sanitaire_id",
    "classe_hauteur_id", "classe_diametre_id", "espece_id", "valid",
)
VEGETATION_RELATIONS = {
    "problematique_ids": (
        "public.problematiques_vegetation", "problematique_id",
        "referentiel.problematiques_vegetation",
    ),
    "troncon_ids": ("public.vegetation__troncons", "troncon_id", "public.troncons"),
    "amenagement_hydraulique_ids": (
        "public.vegetation__amenagements_hydrauliques",
        "amenagement_hydraulique_id", "public.amenagements_hydrauliques",
    ),
}
VEGETATION_REFERENCES = {
    "type_cote_id": "vegetation_type_cote_id",
    "type_position_id": "vegetation_type_position_id",
    "etat_sanitaire_id": "vegetation_etat_sanitaire_id",
    "classe_hauteur_id": "vegetation_classe_hauteur_id",
    "classe_diametre_id": "vegetation_classe_diametre_id",
    "espece_id": "vegetation_espece_id",
}


def _replace_vegetation_relations(
    cursor: Any, identifier: UUID, field: str, identifiers: list[UUID],
) -> None:
    # Tables et colonnes sont exclusivement issues de VEGETATION_RELATIONS.
    table, column, target = VEGETATION_RELATIONS[field]
    for related_id in identifiers:
        cursor.execute(
            f"SELECT 1 FROM {target} WHERE id=%s AND valid FOR SHARE",
            (related_id,),
        )
        if cursor.fetchone() is None:
            raise VegetationValidationError(
                f"Relation {field} absente ou invalide : {related_id}."
            )
    cursor.execute(f"DELETE FROM {table} WHERE vegetation_id=%s", (identifier,))
    for related_id in identifiers:
        cursor.execute(
            f"INSERT INTO {table} (vegetation_id,{column}) VALUES (%s,%s)",
            (identifier, related_id),
        )


def _validate_vegetation_references(cursor: Any, values: dict[str, Any]) -> None:
    _validate_reference_values(
        cursor, {VEGETATION_REFERENCES[key]: value for key,value in values.items()
                 if key in VEGETATION_REFERENCES and value is not None},
        VegetationValidationError,
    )


def _validated_vegetation_geometry(cursor: Any, geometry: Any, business_srid: int) -> str:
    return _validated_required_geometry(
        cursor, geometry, business_srid, {"POINT", "LINESTRING", "POLYGON"},
        VegetationValidationError, "Végétation",
    )


def _validate_vegetation_taxonomy(family: str, sub_type: str | None) -> None:
    if (family == "ARBRES") != (sub_type is not None):
        raise VegetationValidationError(
            "Le sous-type est obligatoire pour un Arbre et interdit ailleurs."
        )


def create_vegetation(connection: Any, creation: VegetationCreate) -> dict[str, Any]:
    business_srid = read_business_crs(connection).srid
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                values = creation.model_dump(
                    exclude={"geometry", "desordre_ids", *VEGETATION_RELATIONS}
                )
                _validate_vegetation_references(cursor, values)
                _validate_vegetation_taxonomy(
                    creation.famille_vegetation, creation.sous_type,
                )
                geometry = _validated_vegetation_geometry(
                    cursor, creation.geometry, business_srid,
                )
                fields = VEGETATION_EDITABLE_FIELDS
                cursor.execute(
                    "INSERT INTO public.vegetation (" + ",".join(fields) + ",geometry) "
                    "VALUES (" + ",".join(["%s"] * len(fields)) + ",ST_Transform("
                    f"ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),{business_srid})) RETURNING id",
                    (*[values[key] for key in fields], geometry),
                )
                identifier = cursor.fetchone()[0]
                for field in VEGETATION_RELATIONS:
                    _replace_vegetation_relations(
                        cursor, identifier, field, getattr(creation, field),
                    )
                disorder_plan = _plan_resource_disorders(
                    cursor, VEGETATION_DISORDER_RELATION, identifier,
                    creation.desordre_ids, VegetationValidationError,
                )
                apply_object_disorder_update(cursor, disorder_plan)
            return fetch_vegetation(connection, identifier)
    except (VegetationValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, VegetationValidationError, "Végétation")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Création de la Végétation impossible")
        raise VegetationValidationError("La création de la Végétation a échoué.") from exc


def update_vegetation(
    connection: Any, identifier: UUID, update: VegetationUpdate,
) -> dict[str, Any]:
    values = update.model_dump(exclude_unset=True)
    geometry = update.geometry if "geometry" in update.model_fields_set else None
    requested_disorders = (
        update.desordre_ids
        if "desordre_ids" in update.model_fields_set else RELATION_UNSET
    )
    for key in ("geometry", "desordre_ids"):
        values.pop(key, None)
    relations = {field: values.pop(field) for field in VEGETATION_RELATIONS if field in values}
    business_srid = (
        read_business_crs(connection).srid if geometry is not None else None
    )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT famille_vegetation,sous_type,etat_sanitaire_id,"
                    "classe_hauteur_id,classe_diametre_id,espece_id,"
                    "type_cote_id,type_position_id,disposition,valid FROM public.vegetation "
                    "WHERE id=%s FOR UPDATE", (identifier,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise VegetationNotFoundError("Végétation introuvable.")
                existing = dict(zip((
                    "famille_vegetation", "sous_type", "etat_sanitaire_id",
                    "classe_hauteur_id", "classe_diametre_id", "espece_id",
                    "type_cote_id", "type_position_id", "disposition", "valid",
                ), current))
                _validate_vegetation_references(cursor, {
                    key: value for key,value in values.items()
                    if key in VEGETATION_REFERENCES and value != existing[key]
                })
                final_valid = values.get("valid", existing["valid"])
                final_family = values.get("famille_vegetation", existing["famille_vegetation"])
                final_disposition = values.get("disposition", existing["disposition"])
                if final_valid and (final_family is None or final_disposition is None):
                    raise VegetationValidationError(
                        "Pour valider la Végétation, renseignez sa famille et sa disposition."
                    )
                # An invalid historical row may contain NULL classifications.
                # Descriptive patches preserve them; qualification changes must
                # still satisfy the complete final taxonomy.
                taxonomy_changed = any(field in values and values[field] != existing[field]
                                       for field in ("famille_vegetation", "sous_type"))
                if final_valid or taxonomy_changed:
                    _validate_vegetation_taxonomy(final_family, values.get("sous_type", existing["sous_type"]))
                disorder_plan = _plan_resource_disorders(
                    cursor, VEGETATION_DISORDER_RELATION, identifier,
                    requested_disorders, VegetationValidationError,
                )
                assignments = [f"{key}=%s" for key in VEGETATION_EDITABLE_FIELDS if key in values]
                params = [values[key] for key in VEGETATION_EDITABLE_FIELDS if key in values]
                if geometry is not None:
                    geojson = _validated_vegetation_geometry(cursor, geometry, business_srid)
                    assignments.append(
                        "geometry=ST_Transform(ST_SetSRID("
                        f"ST_GeomFromGeoJSON(%s),4326),{business_srid})"
                    )
                    params.append(geojson)
                if assignments:
                    cursor.execute(
                        "UPDATE public.vegetation SET " + ",".join(assignments)
                        + " WHERE id=%s", (*params, identifier),
                    )
                for field, identifiers in relations.items():
                    _replace_vegetation_relations(cursor, identifier, field, identifiers)
                apply_object_disorder_update(cursor, disorder_plan)
                if (relations or disorder_plan is not None) and not assignments:
                    cursor.execute("UPDATE public.vegetation SET version=version WHERE id=%s",
                                   (identifier,))
            return fetch_vegetation(connection, identifier)
    except (VegetationNotFoundError, VegetationValidationError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, VegetationValidationError, "Végétation")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Modification de la Végétation impossible")
        raise VegetationValidationError("La modification de la Végétation a échoué.") from exc


def fetch_desordres(connection: Any) -> dict[str, Any]:
    """Retourne les désordres Point, LineString et Polygon en EPSG:4326."""

    return _feature_collection(connection, DESORDRES_GEOJSON_SQL)


def _desordre_search_label_sql(alias: str = "d") -> str:
    """Libellé borné partagé par la page et son curseur stable."""

    if alias != "d":
        raise ValueError("Alias de recherche Désordre non pris en charge.")
    return """
        left(coalesce(
            nullif(btrim(d.designation), ''),
            CASE WHEN nullif(btrim(d.lieu_dit), '') IS NOT NULL
                 THEN 'Désordre — ' || btrim(d.lieu_dit) END,
            nullif(btrim(typ.libelle), ''),
            'Désordre ' || d.id::text
        ), 500)
    """


def _encode_desordre_search_cursor(sort_label: str, identifier: UUID) -> str:
    raw = json.dumps(
        [sort_label, str(identifier)], ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_desordre_search_cursor(value: str | None) -> tuple[str, UUID] | None:
    if value is None:
        return None
    if not value or len(value) > 2_048:
        raise DesordreSearchCursorError("Curseur de recherche invalide.")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.b64decode(
            padded, altchars=b"-_", validate=True,
        ).decode("utf-8"))
        if (not isinstance(decoded, list) or len(decoded) != 2
                or not isinstance(decoded[0], str) or len(decoded[0]) > 500):
            raise ValueError
        return decoded[0], UUID(decoded[1])
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise DesordreSearchCursorError("Curseur de recherche invalide.") from exc


def _desordre_search_item(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "label": row[1],
        "designation": row[2],
        "lieu_dit": row[3],
        "type_label": row[4],
        "category_label": row[5],
        "valid": row[6],
    }


def search_desordres(
    connection: Any,
    *,
    query: str = "",
    limit: int = DESORDRE_SEARCH_DEFAULT_LIMIT,
    cursor: str | None = None,
    troncon_id: UUID | None = None,
) -> dict[str, Any]:
    """Recherche déterministe sans géométrie et sans filtrage implicite de ``valid``."""

    if not 1 <= limit <= DESORDRE_SEARCH_MAX_LIMIT:
        raise ValueError("La limite de recherche est invalide.")
    normalized_query = query.strip()
    after = _decode_desordre_search_cursor(cursor)
    label_sql = _desordre_search_label_sql()
    conditions: list[str] = []
    parameters: list[Any] = []
    if normalized_query:
        escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append("""(
            d.designation ILIKE %s ESCAPE '\\' OR
            d.commentaire ILIKE %s ESCAPE '\\' OR
            d.lieu_dit ILIKE %s ESCAPE '\\' OR
            typ.libelle ILIKE %s ESCAPE '\\' OR
            categorie.libelle ILIKE %s ESCAPE '\\' OR
            d.id::text ILIKE %s ESCAPE '\\'
        )""")
        parameters.extend([pattern] * 6)
    if troncon_id is not None:
        conditions.append("""EXISTS (
            SELECT 1 FROM public.desordres__troncons lien_filtre
            WHERE lien_filtre.desordre_id=d.id AND lien_filtre.troncon_id=%s
        )""")
        parameters.append(troncon_id)
    where_sql = " AND ".join(conditions) if conditions else "true"
    cursor_sql = ""
    if after is not None:
        cursor_sql = "WHERE (sort_label, id) > (%s COLLATE \"C\", %s)"
        parameters.extend(after)
    parameters.append(limit + 1)
    statement = f"""
        WITH candidates AS (
            SELECT d.id, {label_sql} AS label,
                   lower({label_sql}) COLLATE "C" AS sort_label,
                   d.designation, d.lieu_dit,
                   typ.libelle AS type_label,
                   categorie.libelle AS category_label,
                   d.valid
            FROM public.desordres d
            LEFT JOIN referentiel.types_desordre typ ON typ.id=d.type_desordre_id
            LEFT JOIN referentiel.categories_desordre categorie
                ON categorie.id=d.categorie_desordre_id
            WHERE {where_sql}
        )
        SELECT id,label,designation,lieu_dit,type_label,category_label,valid,
               sort_label
        FROM candidates
        {cursor_sql}
        ORDER BY sort_label, id
        LIMIT %s
    """
    try:
        with connection.cursor() as database_cursor:
            database_cursor.execute(statement, parameters)
            rows = database_cursor.fetchall()
    except Exception as exc:
        raise WebDatabaseError("Recherche des Désordres impossible.") from exc
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        _encode_desordre_search_cursor(page[-1][7], page[-1][0])
        if has_more and page else None
    )
    return {
        "items": [_desordre_search_item(row) for row in page],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def fetch_desordre_selection(
    connection: Any, identifiers: list[UUID],
) -> dict[str, Any]:
    """Résout un lot borné dans la base active, en conservant l'ordre demandé."""

    if len(identifiers) > DESORDRE_SELECTION_MAX_IDS:
        raise ValueError("La sélection est limitée à 100 identifiants par requête.")
    ordered = list(dict.fromkeys(identifiers))
    if not ordered:
        return {"items": [], "missing_ids": []}
    label_sql = _desordre_search_label_sql()
    statement = f"""
        SELECT d.id, {label_sql} AS label,
               d.designation, d.lieu_dit,
               typ.libelle AS type_label,
               categorie.libelle AS category_label,
               d.valid
        FROM public.desordres d
        LEFT JOIN referentiel.types_desordre typ ON typ.id=d.type_desordre_id
        LEFT JOIN referentiel.categories_desordre categorie
            ON categorie.id=d.categorie_desordre_id
        WHERE d.id=ANY(%s::uuid[])
    """
    try:
        with connection.cursor() as database_cursor:
            database_cursor.execute(statement, (ordered,))
            by_id = {row[0]: _desordre_search_item(row) for row in database_cursor.fetchall()}
    except Exception as exc:
        raise WebDatabaseError("Lecture de la sélection de Désordres impossible.") from exc
    return {
        "items": [by_id[identifier] for identifier in ordered if identifier in by_id],
        "missing_ids": [identifier for identifier in ordered if identifier not in by_id],
    }


def _encode_desordre_associated_objects_cursor(
    category_order: int, sort_label: str, identifier: UUID,
) -> str:
    raw = json.dumps(
        [category_order, sort_label, str(identifier)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_desordre_associated_objects_cursor(
    value: str | None,
) -> tuple[int, str, UUID] | None:
    if value is None:
        return None
    if not value or len(value) > 2_048:
        raise DesordreAssociatedObjectsCursorError(
            "Curseur de consultation inverse invalide."
        )
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.b64decode(
            padded, altchars=b"-_", validate=True,
        ).decode("utf-8"))
        if (not isinstance(decoded, list) or len(decoded) != 3
                or not isinstance(decoded[0], int)
                or not 0 <= decoded[0] < len(inverse_object_disorder_relations())
                or not isinstance(decoded[1], str)
                or len(decoded[1]) > 500):
            raise ValueError
        return decoded[0], decoded[1], UUID(decoded[2])
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise DesordreAssociatedObjectsCursorError(
            "Curseur de consultation inverse invalide."
        ) from exc


def _associated_object_label(
    config: ObjectDisorderRelationConfig,
) -> sql.Composed:
    candidates = [
        sql.SQL("nullif(btrim(parent.{}), '')").format(sql.Identifier(column))
        for column in config.label_columns
    ]
    candidates.append(sql.SQL("{} || ' ' || parent.{}::text").format(
        sql.Literal(config.category_label),
        sql.Identifier(config.parent_id_column),
    ))
    return sql.SQL("left(coalesce({}), 500)").format(
        sql.SQL(", ").join(candidates),
    )


def _desordre_associated_objects_statement(
    cursor_filter: sql.Composable,
) -> sql.Composed:
    branches: list[sql.Composed] = []
    for category_order, config in enumerate(inverse_object_disorder_relations()):
        branches.append(sql.SQL("""
            SELECT {category_order}::integer AS category_order,
                   {resource}::text AS resource,
                   {category}::text AS category,
                   parent.{parent_id} AS id,
                   {label} AS label,
                   parent.valid
            FROM {association} association
            JOIN {parent} parent
              ON parent.{parent_id}=association.{association_parent}
            WHERE association.{association_disorder}=%s
        """).format(
            category_order=sql.Literal(category_order),
            resource=sql.Literal(config.resource.replace("-", "_")),
            category=sql.Literal(config.category_label),
            parent_id=sql.Identifier(config.parent_id_column),
            label=_associated_object_label(config),
            association=sql.Identifier("public", config.association_table),
            parent=sql.Identifier("public", config.parent_table),
            association_parent=sql.Identifier(config.association_parent_column),
            association_disorder=sql.Identifier(config.association_disorder_column),
        ))
    return sql.SQL("""
        WITH associated AS ({branches}), ranked AS (
            SELECT category_order, resource, category, id, label, valid,
                   lower(label) COLLATE "C" AS sort_label
            FROM associated
        )
        SELECT category_order, resource, category, id, label, valid, sort_label
        FROM ranked
        {cursor_filter}
        ORDER BY category_order, sort_label, id
        LIMIT %s
    """).format(
        branches=sql.SQL(" UNION ALL ").join(branches),
        cursor_filter=cursor_filter,
    )


def fetch_desordre_associated_objects(
    connection: Any,
    desordre_id: UUID,
    *,
    limit: int = DESORDRE_ASSOCIATED_OBJECTS_DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Lit une page SQL bornée des objets publics associés au Désordre."""

    if not 1 <= limit <= DESORDRE_ASSOCIATED_OBJECTS_MAX_LIMIT:
        raise ValueError("La limite de consultation inverse est invalide.")
    after = _decode_desordre_associated_objects_cursor(cursor)
    cursor_filter = sql.SQL("")
    parameters: list[Any] = [
        desordre_id for _config in inverse_object_disorder_relations()
    ]
    if after is not None:
        cursor_filter = sql.SQL(
            "WHERE (category_order, sort_label, id) > (%s, %s COLLATE \"C\", %s)"
        )
        parameters.extend(after)
    parameters.append(limit + 1)
    statement = _desordre_associated_objects_statement(cursor_filter)
    try:
        with connection.cursor() as database_cursor:
            database_cursor.execute(
                "SELECT 1 FROM public.desordres WHERE id=%s", (desordre_id,),
            )
            if database_cursor.fetchone() is None:
                raise DesordreNotFoundError("Désordre introuvable.")
            database_cursor.execute(statement, parameters)
            rows = database_cursor.fetchall()
    except DesordreNotFoundError:
        raise
    except Exception as exc:
        raise WebDatabaseError(
            "Lecture des objets associés au Désordre impossible."
        ) from exc
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        _encode_desordre_associated_objects_cursor(
            page[-1][0], page[-1][6], page[-1][3],
        ) if has_more and page else None
    )
    return {
        "items": [{
            "resource": row[1],
            "category": row[2],
            "id": row[3],
            "label": row[4],
            "valid": row[5],
        } for row in page],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def fetch_mobilier_collection(connection: Any) -> dict[str, Any]:
    """Retourne les Mobilier, y compris ceux sans géométrie."""

    return _feature_collection(connection, MOBILIER_GEOJSON_SQL)


def fetch_mobilier(connection: Any, mobilier_id: UUID) -> dict[str, Any]:
    """Relit un Mobilier et son Point éventuel en EPSG:4326."""

    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(MOBILIER_DETAIL_SQL, (mobilier_id,))
            row = cursor.fetchone()
            if row:
                feature = _add_disorder_ids(
                    row[0], cursor, MOBILIER_DISORDER_RELATION, mobilier_id,
                )
    except Exception as exc:
        raise WebDatabaseError("Lecture du Mobilier impossible.") from exc
    if not row:
        raise MobilierNotFoundError("Mobilier introuvable.")
    return _desordre_feature(feature)


def fetch_equipement_mesure_collection(connection: Any) -> dict[str, Any]:
    """Retourne les Équipements, y compris ceux sans Point."""

    return _feature_collection(connection, EQUIPEMENT_MESURE_GEOJSON_SQL)


def fetch_equipement_mesure(
    connection: Any, identifier: UUID
) -> dict[str, Any]:
    """Relit un Équipement de mesure et son Point éventuel."""

    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(EQUIPEMENT_MESURE_DETAIL_SQL, (identifier,))
            row = cursor.fetchone()
            if row:
                feature = _add_disorder_ids(
                    row[0], cursor, EQUIPEMENT_MESURE_DISORDER_RELATION,
                    identifier,
                )
    except Exception as exc:
        raise WebDatabaseError(
            "Lecture de l'Équipement de mesure impossible."
        ) from exc
    if not row:
        raise EquipementMesureNotFoundError("Équipement de mesure introuvable.")
    return _desordre_feature(feature)


def fetch_reseau_technique_collection(connection: Any) -> dict[str, Any]:
    """Retourne les Réseaux, y compris ceux sans géométrie."""

    return _feature_collection(connection, RESEAU_TECHNIQUE_GEOJSON_SQL)


def fetch_reseau_technique(
    connection: Any, identifier: UUID
) -> dict[str, Any]:
    """Relit un Réseau technique en GeoJSON EPSG:4326."""

    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(RESEAU_TECHNIQUE_DETAIL_SQL, (identifier,))
            row = cursor.fetchone()
            if row:
                feature = _add_disorder_ids(
                    row[0], cursor, RESEAU_TECHNIQUE_DISORDER_RELATION,
                    identifier,
                )
    except Exception as exc:
        raise WebDatabaseError("Lecture du Réseau technique impossible.") from exc
    if not row:
        raise ReseauTechniqueNotFoundError("Réseau technique introuvable.")
    return _desordre_feature(feature)


def fetch_ouvrage_particulier_collection(connection: Any) -> dict[str, Any]:
    return _feature_collection(connection, OUVRAGE_PARTICULIER_COLLECTION_SQL)


def fetch_cheminement_collection(connection: Any) -> dict[str, Any]:
    return _feature_collection(connection, CHEMINEMENT_COLLECTION_SQL)




def fetch_prestation_collection(connection: Any) -> dict[str, Any]:
    return _feature_collection(connection, PRESTATION_COLLECTION_SQL)


def fetch_prestation(connection: Any, identifier: UUID) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(PRESTATION_DETAIL_SQL, (identifier,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture de la prestation impossible.") from exc
    if not row:
        raise PrestationNotFoundError("Prestation introuvable.")
    return _desordre_feature(row[0])


def fetch_prestation_options(connection: Any) -> dict[str, Any]:
    result = {}
    try:
        with connection.cursor() as cursor:
            for table in (*PRESTATION_OBJECT_FAMILIES, "troncons", "contacts"):
                if table == "contacts":
                    label = "coalesce(nullif(btrim(designation),''),nullif(btrim(concat_ws(' ',prenom,nom)),''),id::text)"
                elif table == "troncons":
                    label = "coalesce(nullif(btrim(libelle),''),id::text)"
                else:
                    label = "coalesce(nullif(btrim(designation),''),id::text)"
                # Table names are closed by PRESTATION_OBJECT_FAMILIES.
                cursor.execute(
                    f"SELECT id,{label} AS label,valid FROM public.{table} "
                    "ORDER BY label,id"
                )
                result[table] = [
                    {"id": str(row[0]), "libelle": row[1], "valid": row[2]}
                    for row in cursor.fetchall()
                ]
    except Exception as exc:
        raise WebDatabaseError("Options des relations Prestations indisponibles.") from exc
    return result


def fetch_prestation_observations(
    connection: Any, identifier: UUID,
) -> dict[str, Any]:
    return _json_object(
        connection, PRESTATION_OBSERVATIONS_SQL, identifier,
        error_message="Lecture des observations de prestation impossible.",
        not_found_message="Prestation introuvable.",
    )


def fetch_amenagement_hydraulique_collection(connection: Any) -> dict[str, Any]:
    return _feature_collection(connection, AMENAGEMENT_HYDRAULIQUE_COLLECTION_SQL)


def fetch_amenagement_hydraulique(connection: Any, identifier: UUID) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(AMENAGEMENT_HYDRAULIQUE_DETAIL_SQL, (identifier,))
            row = cursor.fetchone()
            if row:
                feature = _add_disorder_ids(
                    row[0], cursor, AMENAGEMENT_HYDRAULIQUE_DISORDER_RELATION,
                    identifier,
                )
    except Exception as exc:
        raise WebDatabaseError("Lecture de l'aménagement hydraulique impossible.") from exc
    if not row:
        raise AmenagementHydrauliqueNotFoundError("Aménagement hydraulique introuvable.")
    return _desordre_feature(feature)


def fetch_amenagement_hydraulique_observations(
    connection: Any, identifier: UUID,
) -> dict[str, Any]:
    return _json_object(
        connection, AMENAGEMENT_HYDRAULIQUE_OBSERVATIONS_SQL, identifier,
        error_message="Lecture des observations AH impossible.",
        not_found_message="Aménagement hydraulique introuvable.",
    )


def fetch_amenagement_hydraulique_relation_options(connection: Any) -> dict[str, Any]:
    """Choix réglementaires issus des FK directes du modèle courant."""
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT t.id,coalesce(t.designation,t.id::text),"
                           "d.libelle,t.valid,t.version,t.type_physique_id,t.amenagement_hydraulique_id FROM public.troncons t "
                           "LEFT JOIN public.digues d ON d.id=t.digue_id ORDER BY 2,t.id")
            troncons = cursor.fetchall()
            cursor.execute("SELECT id,coalesce(designation,libelle,id::text),valid "
                           "FROM public.zones_stockage ORDER BY 2,id")
            zones = cursor.fetchall()
    except Exception as exc:
        raise WebDatabaseError("Lecture des relations AH impossible.") from exc
    return {"troncons": [{"id": str(i), "libelle": label, "digue_libelle": digue,
                          "valid": valid,"version":version,"type_physique_id":physical,"amenagement_hydraulique_id":parent} for i, label, digue, valid, version, physical, parent in troncons],
            "zones": [{"id": str(i), "libelle": label, "valid": valid}
                      for i, label, valid in zones]}


def fetch_zone_stockage_collection(connection: Any) -> dict[str, Any]:
    collection = _feature_collection(connection, ZONE_STOCKAGE_COLLECTION_SQL)
    for feature in collection["features"]:
        _hydrate_historical_zone_geometry(connection, feature)
    return collection


def _hydrate_historical_zone_geometry(connection: Any, feature: dict[str, Any]) -> None:
    """Isole une transformation PostGIS historique défectueuse à sa Zone."""
    properties = feature["properties"]
    if properties["valid"] is not False or properties["geometry_status"] in {"ABSENTE", "SRID_INCONNU"}:
        return
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SELECT ST_AsGeoJSON(ST_Transform(geometry,4326))::jsonb "
                               "FROM public.zones_stockage WHERE id=%s", (properties["id"],))
                row = cursor.fetchone()
                feature["geometry"] = row[0] if row else None
    except (psycopg.DataError, psycopg.InternalError):
        properties["geometry_status"] = "TRANSFORMATION_IMPOSSIBLE"


def fetch_zone_stockage_relation_options(connection: Any) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,coalesce(designation,libelle,id::text) "
                           "FROM public.amenagements_hydrauliques ORDER BY 2,id")
            amenagements = cursor.fetchall()
            cursor.execute("SELECT id,coalesce(designation,libelle,id::text) "
                           "FROM public.reseaux_hydrauliques ORDER BY 2,id")
            reseaux = cursor.fetchall()
            cursor.execute("SELECT id,coalesce(designation,nom,id::text) "
                           "FROM public.organismes ORDER BY 2,id")
            organismes = cursor.fetchall()
    except Exception as exc:
        raise WebDatabaseError("Lecture des relations de Zone impossible.") from exc
    return {"amenagements": [{"id": str(i), "libelle": label} for i, label in amenagements],
            "reseaux": [{"id": str(i), "libelle": label} for i, label in reseaux],
            "organismes": [{"id": str(i), "libelle": label} for i, label in organismes]}


def fetch_zone_stockage(connection: Any, identifier: UUID) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(ZONE_STOCKAGE_DETAIL_SQL, (identifier,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture de la Zone impossible.") from exc
    if not row:
        raise ZoneStockageNotFoundError("Zone de stockage introuvable.")
    feature = _desordre_feature(row[0])
    _hydrate_historical_zone_geometry(connection, feature)
    return feature


def fetch_zone_stockage_observations(connection: Any, identifier: UUID) -> dict[str, Any]:
    return _json_object(
        connection, ZONE_STOCKAGE_OBSERVATIONS_SQL, identifier,
        error_message="Lecture des observations de Zone impossible.",
        not_found_message="Zone de stockage introuvable.",
    )


def fetch_vegetation_collection(connection: Any) -> dict[str, Any]:
    return _feature_collection(connection, VEGETATION_COLLECTION_SQL)


def fetch_vegetation(connection: Any, identifier: UUID) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(VEGETATION_DETAIL_SQL, (identifier,))
            row = cursor.fetchone()
            if row:
                feature = _add_disorder_ids(
                    row[0], cursor, VEGETATION_DISORDER_RELATION, identifier,
                )
    except Exception as exc:
        raise WebDatabaseError("Lecture de la Végétation impossible.") from exc
    if not row:
        raise VegetationNotFoundError("Végétation introuvable.")
    return _desordre_feature(feature)


def fetch_vegetation_relations_options(connection: Any) -> dict[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(VEGETATION_RELATIONS_OPTIONS_SQL)
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Liste des relations de Végétation impossible.") from exc
    return row[0]


def fetch_vegetation_observations(connection: Any, identifier: UUID) -> dict[str, Any]:
    return _json_object(
        connection, VEGETATION_OBSERVATIONS_SQL, identifier,
        error_message="Lecture des observations de Végétation impossible.",
        not_found_message="Végétation introuvable.",
    )


def fetch_cheminement(connection: Any, identifier: UUID) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(CHEMINEMENT_DETAIL_SQL, (identifier,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture du Cheminement impossible.") from exc
    if not row:
        raise CheminementNotFoundError("Cheminement introuvable.")
    return _desordre_feature(row[0])


def fetch_cheminement_observations(connection: Any, identifier: UUID) -> dict[str, Any]:
    return _json_object(
        connection, CHEMINEMENT_OBSERVATIONS_SQL, identifier,
        error_message="Lecture des observations du Cheminement impossible.",
        not_found_message="Cheminement introuvable.",
    )


def fetch_ouvrage_particulier(
    connection: Any, identifier: UUID
) -> dict[str, Any]:
    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(OUVRAGE_PARTICULIER_DETAIL_SQL, (identifier,))
            row = cursor.fetchone()
            if row:
                feature = _desordre_feature(row[0])
                feature["properties"]["desordre_ids"] = [
                    str(disorder_id) for disorder_id in read_object_disorder_ids(
                        cursor, OUVRAGE_PARTICULIER_DISORDER_RELATION, identifier,
                    )
                ]
    except Exception as exc:
        raise WebDatabaseError("Lecture de l'ouvrage particulier impossible.") from exc
    if not row:
        raise OuvrageParticulierNotFoundError("Ouvrage particulier introuvable.")
    return feature


def fetch_ouvrage_particulier_observations(
    connection: Any, identifier: UUID
) -> dict[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(OUVRAGE_PARTICULIER_OBSERVATIONS_SQL, (identifier,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture des observations impossible.") from exc
    if not row:
        raise OuvrageParticulierNotFoundError("Ouvrage particulier introuvable.")
    return row[0]


def _desordre_feature(value: Any) -> dict[str, Any]:
    """Decode the authoritative SQL projection, shared by detail and batch reads."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or value.get("type") != "Feature":
        raise WebDatabaseError("Réponse GeoJSON PostgreSQL invalide.")
    return value


def fetch_desordres_batch(connection: Any, identifiers: list[UUID]) -> list[dict[str, Any]]:
    """Bounded details, three SQL reads regardless of count, on the given connection.

    The caller owns the snapshot transaction. No connection, cache, or weaker
    offline serialization is introduced. Missing/unsupported IDs fail the whole
    read, just like fetch_desordre; duplicates are read once, in input order.
    """
    if len(identifiers) > 2000:
        raise ValueError("Lecture de désordres limitée à 2000 identifiants.")
    ids = list(dict.fromkeys(UUID(str(value)) for value in identifiers))
    if not ids:
        return []
    read_business_crs(connection)
    features = {}
    try:
        with connection.cursor() as cursor:
            for statement in (POINT_DESORDRES_BATCH_SQL, LINE_DESORDRES_BATCH_SQL,
                              POLYGON_AND_UNLOCATED_DESORDRES_BATCH_SQL):
                cursor.execute(statement, (ids,))
                for row in cursor.fetchall():
                    feature = _desordre_feature(row[0])
                    features[UUID(feature["properties"]["id"])] = feature
    except Exception as exc:
        raise WebDatabaseError("Lecture des désordres impossible.") from exc
    if any(identifier not in features for identifier in ids):
        raise DesordreNotFoundError("Désordre introuvable ou type non pris en charge.")
    return [features[identifier] for identifier in ids]


def fetch_point_desordre(connection: Any, desordre_id: UUID) -> dict[str, Any]:
    """Relit un Point et toutes ses coordonnées dérivées depuis PostgreSQL."""

    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(POINT_DESORDRE_SQL, (desordre_id,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture du désordre Point impossible.") from exc
    if not row:
        raise PointDesordreNotFoundError("Désordre Point introuvable.")
    return _desordre_feature(row[0])


def fetch_line_desordre(connection: Any, desordre_id: UUID) -> dict[str, Any]:
    """Relit une LineString complète en EPSG:4326 depuis PostgreSQL."""

    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(LINE_DESORDRE_SQL, (desordre_id,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture du désordre LineString impossible.") from exc
    if not row:
        raise LineDesordreNotFoundError("Désordre LineString introuvable.")
    return _desordre_feature(row[0])


def fetch_polygon_desordre(connection: Any, desordre_id: UUID) -> dict[str, Any]:
    """Relit un Polygon et son point représentatif calculé par PostGIS."""

    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(POLYGON_DESORDRE_SQL, (desordre_id,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture du désordre Polygon impossible.") from exc
    if not row:
        raise DesordreNotFoundError("Désordre Polygon introuvable.")
    return _desordre_feature(row[0])


def fetch_desordre(connection: Any, desordre_id: UUID) -> dict[str, Any]:
    """Distribue la lecture détaillée selon la géométrie réelle du désordre."""

    read_business_crs(connection)
    try:
        with connection.cursor() as cursor:
            cursor.execute(DESORDRE_GEOMETRY_TYPE_SQL, (desordre_id,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError("Lecture du type géométrique impossible.") from exc
    if not row:
        raise DesordreNotFoundError("Désordre introuvable.")
    if row[0] is None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(UNLOCATED_DESORDRE_SQL, (desordre_id,))
                detail = cursor.fetchone()
        except Exception as exc:
            raise WebDatabaseError("Lecture du désordre sans géométrie impossible.") from exc
        if not detail:
            raise DesordreNotFoundError("Désordre sans géométrie introuvable.")
        return _desordre_feature(detail[0])
    if row[0] == "POINT":
        return fetch_point_desordre(connection, desordre_id)
    if row[0] == "LINESTRING":
        return fetch_line_desordre(connection, desordre_id)
    if row[0] == "POLYGON":
        return fetch_polygon_desordre(connection, desordre_id)
    raise DesordreNotFoundError("Type de désordre non pris en charge.")


def _json_object(
    connection: Any,
    query: str,
    identifier: UUID,
    *,
    error_message: str,
    not_found_message: str,
) -> dict[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (identifier,))
            row = cursor.fetchone()
    except Exception as exc:
        raise WebDatabaseError(error_message) from exc
    if not row:
        raise ObservationNotFoundError(not_found_message)
    result = row[0]
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise WebDatabaseError("Réponse PostgreSQL invalide.")
    return result


def fetch_desordre_observations(
    connection: Any, desordre_id: UUID
) -> dict[str, Any]:
    """Retourne les observations directement rattachées à un désordre."""

    return _json_object(
        connection,
        DESORDRE_OBSERVATIONS_SQL,
        desordre_id,
        error_message="Lecture des observations du désordre impossible.",
        not_found_message="Désordre introuvable.",
    )


def fetch_troncon_observations(connection: Any, troncon_id: UUID) -> dict[str, Any]:
    """Liste les Observations rattachées directement au Tronçon existant."""
    return _json_object(
        connection, TRONCON_OBSERVATIONS_SQL, troncon_id,
        error_message="Lecture des observations du Tronçon impossible.",
        not_found_message="Tronçon introuvable.",
    )


def fetch_mobilier_observations(
    connection: Any, mobilier_id: UUID
) -> dict[str, Any]:
    """Retourne les Observations génériques rattachées au Mobilier."""

    return _json_object(
        connection,
        MOBILIER_OBSERVATIONS_SQL,
        mobilier_id,
        error_message="Lecture des observations du Mobilier impossible.",
        not_found_message="Mobilier introuvable.",
    )


def fetch_equipement_mesure_observations(
    connection: Any, identifier: UUID
) -> dict[str, Any]:
    """Retourne les Observations rattachées à l'Équipement de mesure."""

    return _json_object(
        connection, EQUIPEMENT_MESURE_OBSERVATIONS_SQL, identifier,
        error_message="Lecture des observations de l'Équipement impossible.",
        not_found_message="Équipement de mesure introuvable.",
    )


def fetch_reseau_technique_observations(
    connection: Any, identifier: UUID
) -> dict[str, Any]:
    """Retourne les Observations rattachées au Réseau technique."""

    return _json_object(
        connection, RESEAU_TECHNIQUE_OBSERVATIONS_SQL, identifier,
        error_message="Lecture des observations du Réseau impossible.",
        not_found_message="Réseau technique introuvable.",
    )


def fetch_observation(connection: Any, observation_id: UUID, *, context=None, include_photos=True) -> dict[str, Any]:
    """Retourne une observation, son parent direct et ses photos enfants."""

    result = _json_object(
        connection,
        OBSERVATION_DETAIL_SQL,
        observation_id,
        error_message="Lecture de l'observation impossible.",
        not_found_message="Observation introuvable.",
    )
    candidates = result.pop("_parent_candidates", None)
    try:
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ValueError("Une observation doit avoir exactement un parent.")
        candidate = dict(candidates[0])
        if candidate.pop("matched_id", None) != candidate.get("id"):
            raise ValueError("Parent référencé introuvable.")
        parent = ObservationParent.model_validate(candidate).model_dump(mode="json", exclude_none=True)
    except (ValueError, TypeError) as exc:
        raise WebDatabaseError("Rattachement de l'observation incohérent.") from exc
    result["parent"] = parent
    if not include_photos:
        result.pop("photos", None)
        return result
    from .photos import enrich_photo
    photos = result.get("photos", [])
    for photo in photos:
        enrich_photo(photo, context=context)
    return result


def create_observation(connection: Any, creation: ObservationCreate, *, context=None) -> dict[str, Any]:
    """Une FK directe, UUID client ; appelé dans la transaction de mutation."""
    fk = next(fk for fk, resource, _ in OBSERVATION_PARENTS
              if resource == creation.parent.resource)
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                # Verrou compatible avec la FK, conservé jusqu'au commit : le
                # parent ne peut disparaître entre sa validation et l'insertion.
                cursor.execute(
                    f"SELECT id FROM public.{creation.parent.resource} WHERE id=%s FOR KEY SHARE",
                    (creation.parent.id,),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(422, {"code": "observation-parent-not-found",
                                             "message": "Le parent de l'observation n'existe pas."})
                _validate_reference_values(cursor, {
                    "suite_apporter_id": creation.suite_apporter_id,
                    "urgence_id": creation.urgence_id,
                }, ObservationUpdateError)
                cursor.execute(
                    f"INSERT INTO public.observations (id, {fk}, designation, date_observation, evolution, "
                    "suite_apporter_id, suite, urgence_id, nombre_desordres, valid) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (id) DO NOTHING RETURNING id",
                    (creation.id, creation.parent.id, creation.designation, creation.date,
                     creation.evolution, creation.suite_apporter_id, creation.suite,
                     creation.urgence_id, creation.nombre_desordres, creation.valid),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(409, {"code": "observation-id-collision",
                                             "message": "Cet UUID Observation est déjà utilisé."})
            return fetch_observation(connection, creation.id, context=context)
    except ObservationUpdateError as exc:
        raise HTTPException(422, {"code": "observation-reference-invalid", "message": str(exc)}) from exc
    except (HTTPException, WebDatabaseError):
        raise
    except Exception as exc:
        raise WebDatabaseError("Création de l'observation impossible.") from exc


def update_observation(
    connection: Any,
    observation_id: UUID,
    update: ObservationUpdate,
    *, context=None,
) -> dict[str, Any]:
    """Édition partielle transversale, sans changement de rattachement."""

    values = update.model_dump(exclude_unset=True)
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                parent_columns = [fk for fk, _, _ in OBSERVATION_READ_PARENTS]
                cursor.execute(
                    "SELECT " + ", ".join(parent_columns)
                    + ", suite_apporter_id, urgence_id FROM public.observations "
                    "WHERE id = %s FOR UPDATE", (observation_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ObservationNotFoundError("Observation introuvable.")
                parents = [resource for index, (_, resource, _) in enumerate(OBSERVATION_READ_PARENTS)
                           if row[index] is not None]
                if len(parents) != 1:
                    raise WebDatabaseError("Rattachement de l'observation incohérent.")
                if parents[0] in {resource for _, resource, _ in OBSERVATION_READ_ONLY_PARENTS}:
                    raise HTTPException(422, {"code": "observation-parent-read-only",
                                             "message": "Cette observation historique est en lecture seule."})
                if parents[0] != "desordres" and {"urgence_id", "nombre_desordres"} & values.keys():
                    raise HTTPException(422, "Urgence et nombre de désordres sont réservés aux Observations de Désordre.")
                previous = dict(zip(("suite_apporter_id", "urgence_id"), row[-2:]))
                _validate_reference_values(cursor, {
                    key: value for key, value in values.items()
                    if key in previous and value != previous[key]
                }, ObservationUpdateError)
                # Noms issus exclusivement du modèle extra=forbid ; aucune FK parente.
                cursor.execute(
                    "UPDATE public.observations SET "
                    + ", ".join(f"{'date_observation' if key == 'date' else key} = %s" for key in values)
                    + " WHERE id = %s RETURNING id",
                    (*values.values(), observation_id),
                )
                if cursor.fetchone() is None:
                    raise ObservationNotFoundError(
                        "Observation introuvable."
                    )
            return fetch_observation(connection, observation_id, context=context)
    except (HTTPException, ObservationNotFoundError, ObservationUpdateError, WebDatabaseError):
        raise
    except Exception as exc:
        raise WebDatabaseError("Modification de l'observation impossible.") from exc


def update_point_desordre(
    connection: Any,
    desordre_id: UUID,
    update: PointDesordreUpdate,
) -> dict[str, Any]:
    """Met à jour attributs, liens et opération spatiale dans une seule transaction."""

    values = update.model_dump(exclude_unset=True)
    troncon_ids = values.pop("troncon_ids", None)
    # Les opérations spécialisées conservent leurs calculs et validations.
    # Leur transaction imbriquée reste sous celle des attributs et du reçu.
    spatial = {name: values.pop(name) for name in ("geometry", "line_endpoints", "reperage")
               if name in values}
    coordinate_fields = {"x", "y", "longitude", "latitude"}
    if coordinate_fields & values.keys():
        read_business_crs(connection)

    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT GeometryType(geometry), type_desordre_id, "
                    "categorie_desordre_id, cote_id, position_id, source_id "
                    "FROM public.desordres "
                    "WHERE id = %s FOR UPDATE",
                    (desordre_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise DesordreNotFoundError("Désordre introuvable.")
                (
                    geometry_type,
                    current_type_id,
                    current_category_id,
                    current_cote_id,
                    current_position_id,
                    current_source_id,
                ) = row
                if coordinate_fields & values.keys() and geometry_type != "POINT":
                    raise PointDesordreUpdateError(
                        "Les coordonnées numériques directes sont réservées au Point."
                    )
                current_references = {
                    "type_desordre_id": current_type_id,
                    "categorie_desordre_id": current_category_id,
                    "cote_id": current_cote_id,
                    "position_id": current_position_id,
                    "source_id": current_source_id,
                }
                changed_references = {
                    field: values[field]
                    for field in current_references
                    if field in values and values[field] != current_references[field]
                }
                _validate_reference_values(
                    cursor,
                    {
                        field: changed_references[field]
                        for field in ("cote_id", "position_id", "source_id")
                        if field in changed_references
                    },
                    PointDesordreUpdateError,
                )
                type_changed = "type_desordre_id" in changed_references
                category_changed = "categorie_desordre_id" in changed_references
                changed_type_row = None
                if type_changed and values["type_desordre_id"] is not None:
                    cursor.execute(
                        "SELECT categorie_id FROM referentiel.types_desordre "
                        "WHERE id = %s AND valid",
                        (values["type_desordre_id"],),
                    )
                    changed_type_row = cursor.fetchone()
                    if changed_type_row is None:
                        raise PointDesordreUpdateError(
                            "Le type de désordre est absent ou invalide."
                        )
                effective_category_id = values.get(
                    "categorie_desordre_id", current_category_id
                )
                effective_type_id = values.get("type_desordre_id", current_type_id)
                if category_changed and effective_category_id is not None:
                    cursor.execute(
                        "SELECT 1 FROM referentiel.categories_desordre "
                        "WHERE id = %s AND valid",
                        (effective_category_id,),
                    )
                    if cursor.fetchone() is None:
                        raise PointDesordreUpdateError(
                            "La catégorie de désordre est absente ou invalide."
                        )
                if (
                    (type_changed or category_changed)
                    and effective_type_id is not None
                    and effective_category_id is not None
                ):
                    effective_type = changed_type_row
                    if not type_changed:
                        cursor.execute(
                            "SELECT categorie_id FROM referentiel.types_desordre "
                            "WHERE id = %s",
                            (effective_type_id,),
                        )
                        effective_type = cursor.fetchone()
                    if effective_type is None or effective_type[0] != effective_category_id:
                        raise PointDesordreUpdateError(
                            "Le type de désordre n'appartient pas à la catégorie choisie."
                        )
                base_values = {
                    field: values.pop(field)
                    for field in (
                        "categorie_desordre_id", "lieu_dit", "cote_id",
                        "position_id", "source_id",
                    )
                    if field in values
                }
                if base_values:
                    assignments = [f"{field} = %s" for field in base_values]
                    cursor.execute(
                        "UPDATE public.desordres SET " + ", ".join(assignments)
                        + " WHERE id = %s",
                        [*base_values.values(), desordre_id],
                    )
                if values:
                    assignments = [
                        f"{POINT_UPDATE_COLUMNS[field]} = %s" for field in values
                    ]
                    parameters = [values[field] for field in values] + [desordre_id]
                    target = (
                        "public.desordres_points_saisie"
                        if geometry_type == "POINT"
                        else "public.desordres"
                    )
                    cursor.execute(
                        f"UPDATE {target} SET " + ", ".join(assignments)
                        + " WHERE id = %s RETURNING id",
                        parameters,
                    )
                    cursor.fetchone()
                if troncon_ids is not None:
                    if geometry_type == "POINT" and len(troncon_ids) > 1:
                        raise PointDesordreUpdateError(
                            "Un désordre Point accepte au plus un tronçon."
                        )
                    if troncon_ids:
                        cursor.execute(
                            "SELECT id FROM public.troncons "
                            "WHERE id = ANY(%s::uuid[]) AND valid FOR SHARE",
                            (troncon_ids,),
                        )
                        if {item[0] for item in cursor.fetchall()} != set(troncon_ids):
                            raise PointDesordreUpdateError(
                                "Au moins un tronçon est absent ou invalide."
                            )
                    cursor.execute(
                        "SELECT troncon_id FROM public.desordres__troncons "
                        "WHERE desordre_id = %s FOR UPDATE",
                        (desordre_id,),
                    )
                    current_troncon_ids = {item[0] for item in cursor.fetchall()}
                    requested_troncon_ids = set(troncon_ids)
                    if current_troncon_ids != requested_troncon_ids:
                        # La localisation porte une FK restrictive vers le lien :
                        # elle doit disparaître avant le lien qu'elle référence.
                        cursor.execute(
                            "DELETE FROM public.localisations_desordres "
                            "WHERE desordre_id = %s",
                            (desordre_id,),
                        )
                        cursor.execute(
                            "DELETE FROM public.desordres__troncons "
                            "WHERE desordre_id = %s",
                            (desordre_id,),
                        )
                        if troncon_ids:
                            cursor.execute(
                                "INSERT INTO public.desordres__troncons "
                                "(desordre_id, troncon_id) "
                                "SELECT %s, troncon_id FROM unnest(%s::uuid[]) "
                                "AS selected(troncon_id)",
                                (desordre_id, troncon_ids),
                            )
            if "geometry" in spatial:
                update_line_desordre_geometry(connection, desordre_id,
                    DesordreGeometryUpdate(geometry=update.geometry))
            elif "line_endpoints" in spatial:
                update_line_desordre_endpoints(connection, desordre_id, update.line_endpoints)
            elif "reperage" in spatial:
                update_point_reperage(connection, desordre_id, update.reperage)
            return fetch_desordre(connection, desordre_id)
    except (PointDesordreNotFoundError, DesordreNotFoundError,
            PointDesordreUpdateError, LineDesordreNotFoundError,
            LineDesordreUpdateError, PointReperageUnavailableError,
            PointReperageUpdateError):
        raise
    except WebDatabaseError:
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, PointDesordreUpdateError, "Désordre")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Mise à jour du désordre impossible")
        raise PointDesordreUpdateError(
            "La mise à jour du désordre a échoué."
        ) from exc


def update_point_reperage(
    connection: Any,
    desordre_id: UUID,
    update: PointReperageUpdate,
) -> dict[str, Any]:
    """Écrit le bornage enfant, puis relit géométrie et valeurs dérivées."""

    read_business_crs(connection)
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT GeometryType(geometry) FROM public.desordres "
                    "WHERE id = %s FOR UPDATE",
                    (desordre_id,),
                )
                row = cursor.fetchone()
                if row is None or row[0] not in ("POINT", "LINESTRING"):
                    raise PointDesordreNotFoundError("Désordre repérable introuvable.")
                geometry_type = row[0]
                has_end = update.borne_fin_id is not None
                if geometry_type == "POINT" and has_end:
                    raise PointReperageUpdateError(
                        "Un Point ne possède pas de bornage de fin."
                    )
                if geometry_type == "LINESTRING" and not has_end:
                    raise PointReperageUpdateError(
                        "Une LineString exige un bornage de fin complet."
                    )
                cursor.execute(
                    "SELECT count(*)::integer, "
                    "min(l.troncon_id::text)::uuid "
                    "FROM public.desordres__troncons AS l "
                    "WHERE l.desordre_id = %s",
                    (desordre_id,),
                )
                nombre_troncons, troncon_id = cursor.fetchone()
                if nombre_troncons != 1:
                    raise PointReperageUnavailableError(
                        "Le repérage exige exactement un tronçon associé "
                        "au désordre."
                    )
                cursor.execute(
                    "SELECT coalesce(l.systeme_reperage_id, "
                    "t.systeme_reperage_defaut_id) "
                    "FROM public.troncons AS t "
                    "LEFT JOIN public.localisations_desordres AS l "
                    "ON l.desordre_id = %s AND l.troncon_id = t.id "
                    "WHERE t.id = %s",
                    (desordre_id, troncon_id),
                )
                systeme_row = cursor.fetchone()
                systeme_id = systeme_row[0] if systeme_row else None
                if systeme_id is None:
                    raise PointReperageUnavailableError(
                        "Aucun système de repérage n'est disponible pour "
                        "le tronçon associé."
                    )
                cursor.execute(
                    "SELECT 1 "
                    "FROM public.systemes_reperage__bornes "
                    "WHERE systeme_reperage_id = %s AND borne_id = %s",
                    (systeme_id, update.borne_debut_id),
                )
                if cursor.fetchone() is None:
                    raise PointReperageUpdateError(
                        "La borne sélectionnée n'appartient pas au système "
                        "de repérage du tronçon associé."
                    )
                if update.borne_fin_id is not None:
                    cursor.execute(
                        "SELECT 1 FROM public.systemes_reperage__bornes "
                        "WHERE systeme_reperage_id = %s AND borne_id = %s",
                        (systeme_id, update.borne_fin_id),
                    )
                    if cursor.fetchone() is None:
                        raise PointReperageUpdateError(
                            "La borne de fin n'appartient pas au système "
                            "de repérage du tronçon associé."
                        )
                cursor.execute(
                    "UPDATE public.localisations_desordres SET "
                    "troncon_id = %s, systeme_reperage_id = %s, "
                    "borne_debut_id = %s, distance_debut_m = %s, "
                    "position_debut_relative = %s, borne_fin_id = %s, "
                    "distance_fin_m = %s, position_fin_relative = %s "
                    "WHERE desordre_id = %s RETURNING id",
                    (
                        troncon_id,
                        systeme_id,
                        update.borne_debut_id,
                        update.distance_debut_m,
                        update.position_debut_relative,
                        update.borne_fin_id,
                        update.distance_fin_m,
                        update.position_fin_relative,
                        desordre_id,
                    ),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        "INSERT INTO public.localisations_desordres ("
                        "desordre_id, troncon_id, systeme_reperage_id, "
                        "borne_debut_id, distance_debut_m, "
                        "position_debut_relative, borne_fin_id, "
                        "distance_fin_m, position_fin_relative, valid) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true) "
                        "RETURNING id",
                        (
                            desordre_id,
                            troncon_id,
                            systeme_id,
                            update.borne_debut_id,
                            update.distance_debut_m,
                            update.position_debut_relative,
                            update.borne_fin_id,
                            update.distance_fin_m,
                            update.position_fin_relative,
                        ),
                    )
                    cursor.fetchone()
            return fetch_desordre(connection, desordre_id)
    except (
        PointDesordreNotFoundError,
        PointReperageUnavailableError,
        PointReperageUpdateError,
        WebDatabaseError,
    ):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, PointReperageUpdateError, "Désordre")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Mise à jour du repérage impossible")
        raise PointReperageUpdateError(
            "La mise à jour du repérage a échoué."
        ) from exc


def update_line_desordre_geometry(
    connection: Any,
    desordre_id: UUID,
    update: DesordreGeometryUpdate,
) -> dict[str, Any]:
    """Transforme le GeoJSON 4326 dans le CRS métier, puis relit l'objet."""

    business_srid = read_business_crs(connection).srid
    geometry_json = json.dumps(update.geometry.model_dump(), allow_nan=False)
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                _validated_required_geometry(
                    cursor, update.geometry, business_srid,
                    {"LINESTRING", "POLYGON"},
                    LineDesordreUpdateError, "Désordre",
                )
                cursor.execute(
                    "UPDATE public.desordres SET geometry = ST_Transform("
                    "ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), "
                    f"{business_srid}) "
                    "WHERE id = %s AND GeometryType(geometry) = "
                    "GeometryType(ST_GeomFromGeoJSON(%s)) "
                    "RETURNING id",
                    (geometry_json, desordre_id, geometry_json),
                )
                if cursor.fetchone() is None:
                    raise LineDesordreNotFoundError(
                        "Désordre introuvable ou type géométrique incompatible."
                    )
            return fetch_desordre(connection, desordre_id)
    except (
        LineDesordreNotFoundError,
        LineDesordreUpdateError,
        WebDatabaseError,
    ):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, LineDesordreUpdateError, "Désordre")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Mise à jour de la géométrie impossible")
        raise LineDesordreUpdateError(
            "La mise à jour de la géométrie a échoué."
        ) from exc


def update_line_desordre_endpoints(
    connection: Any,
    desordre_id: UUID,
    endpoints: LineEndpoints,
) -> dict[str, Any]:
    """Remplace uniquement début/fin et conserve les sommets intermédiaires."""

    business_crs = read_business_crs(connection)
    source_srid = _endpoint_source_srid(endpoints.crs, business_crs)
    debut_x, debut_y = endpoints.debut
    fin_x, fin_y = endpoints.fin
    point_sql = f"ST_SetSRID(ST_Point(%s, %s), {business_crs.srid})"
    if source_srid == 4326:
        point_sql = (
            "ST_Transform(ST_SetSRID(ST_Point(%s, %s), 4326), "
            f"{business_crs.srid})"
        )
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.desordres SET geometry = ST_SetPoint("
                    f"ST_SetPoint(geometry, 0, {point_sql}), "
                    f"ST_NPoints(geometry) - 1, {point_sql}) "
                    "WHERE id = %s AND GeometryType(geometry) = 'LINESTRING' "
                    "AND ST_NPoints(geometry) >= 2 RETURNING id",
                    (debut_x, debut_y, fin_x, fin_y, desordre_id),
                )
                if cursor.fetchone() is None:
                    raise LineDesordreNotFoundError(
                        "Désordre LineString introuvable."
                    )
            return fetch_line_desordre(connection, desordre_id)
    except (LineDesordreNotFoundError, WebDatabaseError):
        raise
    except Exception as exc:
        geometry_error = _geometry_integrity_error(exc, LineDesordreUpdateError, "Désordre")
        if geometry_error is not None:
            raise geometry_error from exc
        _raise_unexpected_sql_error(exc)
        LOGGER.exception("Mise à jour des extrémités impossible")
        raise LineDesordreUpdateError(
            "La mise à jour des extrémités a échoué."
        ) from exc
