"""Catalogue et planificateur relationnel fermé des vues Données."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .database import WebDatabaseError
from .models import DATA_VIEW_MAX_PAGE_SIZE, DataViewFieldSelection
from .readonly_sql import serialize_sql_value
from .troncons import GEOMETRY as TRONCON_GEOMETRY, GEOJSON_SUPPORTED


DATA_VIEW_MAX_ROWS = DATA_VIEW_MAX_PAGE_SIZE
DATA_VIEW_MAX_EXPORT_ROWS = 50_000


class DataViewValidationError(ValueError):
    """Sélection absente du catalogue ou chemin relationnel ambigu."""


class DataViewExportLimitError(ValueError):
    """Le résultat complet dépasse la protection technique d'un export."""


@dataclass(frozen=True)
class ViewClass:
    id: str
    label: str
    table: str
    alias: str


@dataclass(frozen=True)
class ViewRelation:
    parent: str
    child: str
    cardinality: str
    through: str | None = None


@dataclass(frozen=True)
class ViewField:
    table: str
    field: str
    label: str
    data_type: str
    expression: str
    navigable: bool = False

    @property
    def key(self) -> str:
        return f"{self.table}.{self.field}"


# Branche canonique des champs sélectionnables. Les autres parents possibles
# d'une Observation servent au calcul Patrimoine, sans devenir artificiellement
# des maillons de cette branche.
VIEW_CHAIN = (
    "systemes", "digues", "troncons", "desordres", "observations", "photos",
)
PILOT_ROOTS = (
    "mobilier", "equipements_mesure", "reseaux_techniques", "ouvrages_particuliers",
    "cheminements", "vegetation", "amenagements_hydrauliques", "zones_stockage", "prestations",
    "troncons_lit", "elements_morphologiques_lit",
)
VIEW_CLASSES = {
    "systemes": ViewClass(
        "systemes", "Systèmes d’endiguement", "public.systemes", "s",
    ),
    "digues": ViewClass("digues", "Digues", "public.digues", "di"),
    "troncons": ViewClass("troncons", "Tronçons", "public.troncons", "t"),
    "desordres": ViewClass(
        "desordres", "Désordres", "public.desordres", "d",
    ),
    "observations": ViewClass(
        "observations", "Observations", "public.observations", "o",
    ),
    "photos": ViewClass("photos", "Photos", "public.photos", "p"),
    "mobilier": ViewClass("mobilier", "Mobilier", "public.mobilier", "m"),
    "equipements_mesure": ViewClass(
        "equipements_mesure", "Équipements de mesure", "public.equipements_mesure", "e",
    ),
    "reseaux_techniques": ViewClass(
        "reseaux_techniques", "Réseaux techniques", "public.reseaux_techniques", "r",
    ),
    "ouvrages_particuliers": ViewClass(
        "ouvrages_particuliers", "Ouvrages particuliers", "public.ouvrages_particuliers", "op",
    ),
    "cheminements": ViewClass("cheminements", "Cheminements", "public.cheminements", "c"),
    "vegetation": ViewClass("vegetation", "Végétation", "public.vegetation", "v"),
    "amenagements_hydrauliques": ViewClass(
        "amenagements_hydrauliques", "AH réglementaires",
        "public.amenagements_hydrauliques", "a",
    ),
    "zones_stockage": ViewClass("zones_stockage", "Zones de stockage", "public.zones_stockage", "z"),
    "prestations": ViewClass("prestations", "Prestations", "public.prestations", "pr"),
}
VIEW_CLASSES.update({
    "troncons_lit": ViewClass("troncons_lit", "Tronçons de lit", "public.segments_hydrographiques", "lit"),
    "elements_morphologiques_lit": ViewClass("elements_morphologiques_lit", "Éléments morphologiques du lit", "public.elements_morphologiques_lit", "morph"),
})
VIEW_RELATIONS = (
    ViewRelation("systemes", "digues", "one-to-many"),
    ViewRelation("digues", "troncons", "one-to-many"),
    ViewRelation(
        "troncons", "desordres", "many-to-many", "public.desordres__troncons",
    ),
    ViewRelation("desordres", "observations", "one-to-many"),
    ViewRelation("observations", "photos", "one-to-many"),
)
ROOT_LABELS = {class_id: VIEW_CLASSES[class_id].label for class_id in (*VIEW_CHAIN, *PILOT_ROOTS)}
RELATIONS_BY_PARENT = {
    class_id: tuple(
        relation for relation in VIEW_RELATIONS if relation.parent == class_id
    )
    for class_id in VIEW_CLASSES
}
RELATIONS_BY_CHILD = {
    class_id: tuple(
        relation for relation in VIEW_RELATIONS if relation.child == class_id
    )
    for class_id in VIEW_CLASSES
}

# Branches réellement présentes autour de Tronçon. Le catalogue initial expose
# la branche Désordres ; ce contrôle empêche déjà un futur ajout de produire un
# produit cartésien avec les branches parallèles du schéma.
TRONCON_DESCENDANT_BRANCH = {
    "desordres": "desordres",
    "observations": "desordres",
    "photos": "desordres",
    "ouvrages_hydrauliques": "ouvrages_hydrauliques",
    "equipements_mesure": "equipements_mesure",
    "cheminements": "cheminements",
    "mobilier": "mobilier",
    "reseaux_techniques": "reseaux_techniques",
    "amenagements_hydrauliques": "amenagements_hydrauliques",
    "parcelles_gestion_vegetation": "vegetation",
    "vegetation": "vegetation",
}
BRANCH_LABELS = {
    "desordres": "Désordres",
    "ouvrages_hydrauliques": "Ouvrages hydrauliques",
    "equipements_mesure": "Équipements de mesure",
    "cheminements": "Cheminements",
    "mobilier": "Mobilier",
    "reseaux_techniques": "Réseaux techniques",
    "amenagements_hydrauliques": "AH réglementaires",
    "zones_stockage": "Zones de stockage",
    "vegetation": "Végétation",
}


def _field(
    table: str,
    field: str,
    label: str,
    expression: str,
    data_type: str = "text",
    navigable: bool = False,
) -> ViewField:
    return ViewField(table, field, label, data_type, expression, navigable)


VIEW_FIELDS = (
    _field("systemes", "libelle", "Libellé", "s.libelle", navigable=True),
    _field(
        "systemes", "designation", "Désignation", "s.designation", navigable=True,
    ),
    _field("systemes", "commentaire", "Commentaire", "s.commentaire"),
    _field(
        "systemes", "population_protegee", "Population protégée",
        "s.population_protegee::text",
    ),
    _field("systemes", "classement", "Classement", "s.classement"),
    _field(
        "systemes", "niveau_protection", "Niveau de protection",
        "s.niveau_protection",
    ),
    _field(
        "systemes", "valid", "Valide",
        "CASE WHEN s.valid THEN 'Oui' ELSE 'Non' END",
    ),
    _field("digues", "libelle", "Libellé", "di.libelle", navigable=True),
    _field(
        "digues", "designation", "Désignation", "di.designation", navigable=True,
    ),
    _field("digues", "commentaire", "Commentaire", "di.commentaire"),
    _field(
        "digues", "valid", "Valide",
        "CASE WHEN di.valid THEN 'Oui' ELSE 'Non' END",
    ),
    _field("troncons", "libelle", "Libellé", "t.libelle", navigable=True),
    _field("troncons", "type_physique_id", "Code du sous-type physique", "t.type_physique_id"),
    _field("troncons", "type_physique", "Sous-type physique", "type_physique.libelle"),
    _field("troncons", "digue_id", "UUID Digue", "t.digue_id"),
    _field("troncons", "amenagement_hydraulique_id", "UUID Aménagement hydraulique", "t.amenagement_hydraulique_id"),
    _field("troncons", "amenagement_hydraulique", "Aménagement hydraulique", "coalesce(ah.libelle, ah.designation)"),
    _field(
        "troncons", "designation", "Désignation", "t.designation", navigable=True,
    ),
    _field("troncons", "date_debut", "Date de début", "t.date_debut"),
    _field("troncons", "date_fin", "Date de fin", "t.date_fin"),
    _field("troncons", "commentaire", "Commentaire", "t.commentaire"),
    _field("troncons", "rive", "Rive", "rive.libelle"),
    _field("troncons", "type", "Type de tronçon", "type_troncon.libelle"),
    _field(
        "troncons", "typologie", "Typologie de tronçon", "typologie.libelle",
    ),
    _field(
        "troncons", "systeme_reperage", "Système de repérage par défaut",
        "reperage.libelle",
    ),
    _field(
        "troncons", "valid", "Valide",
        "CASE WHEN t.valid THEN 'Oui' ELSE 'Non' END",
    ),
    _field(
        "desordres", "designation", "Désignation", "d.designation",
        navigable=True,
    ),
    _field("desordres", "type", "Type", "td.libelle"),
    _field("desordres", "categorie", "Catégorie", "categorie.libelle"),
    _field("desordres", "commentaire", "Commentaire", "d.commentaire"),
    _field("desordres", "lieu_dit", "Lieu-dit", "d.lieu_dit"),
    _field("desordres", "cote", "Côté", "cote.libelle"),
    _field("desordres", "position", "Position", "position.libelle"),
    _field("desordres", "source", "Source", "source.libelle"),
    _field("desordres", "date_debut", "Date de début", "d.date_debut", "date"),
    _field("desordres", "date_fin", "Date de fin", "d.date_fin", "date"),
    _field("desordres", "valid", "Valide", "d.valid", "boolean"),
    _field(
        "observations", "designation", "Désignation", "o.designation", navigable=True,
    ),
    _field("observations", "date", "Date", "o.date_observation"),
    _field("observations", "urgence", "Urgence", "urgence.libelle"),
    _field("observations", "evolution", "Évolution", "o.evolution"),
    _field(
        "observations", "suite", "Suite à apporter (complément)", "o.suite",
    ),
    _field(
        "observations", "suite_apporter", "Suite à apporter",
        "suite_apporter.libelle",
    ),
    _field(
        "observations", "nombre_desordres", "Nombre de désordres",
        "o.nombre_desordres::text",
    ),
    _field(
        "observations", "valid", "Valide",
        "CASE WHEN o.valid THEN 'Oui' ELSE 'Non' END",
    ),
    _field("photos", "libelle", "Libellé", "p.libelle"),
    _field(
        "photos", "designation", "Désignation", "p.designation",
    ),
    _field("photos", "date", "Date", "p.date_photo"),
    _field("photos", "commentaire", "Commentaire", "p.commentaire"),
    _field("photos", "cote", "Côté", "photo_cote.libelle"),
    _field("photos", "orientation", "Orientation", "orientation.libelle"),
    _field(
        "photos", "systeme_reperage", "Système de repérage",
        "photo_reperage.libelle",
    ),
    _field("photos", "borne_debut", "Borne de début", "photo_borne_debut.libelle"),
    _field(
        "photos", "sens_debut", "Sens depuis la borne de début",
        "CASE WHEN p.borne_debut_distance=0 THEN 'Sur la borne' "
        "WHEN p.borne_debut_aval THEN 'Amont' "
        "WHEN NOT p.borne_debut_aval THEN 'Aval' END",
    ),
    _field(
        "photos", "distance_debut", "Distance depuis la borne de début (m)",
        "p.borne_debut_distance::text",
    ),
    _field("photos", "pr_debut", "PR de début", "p.pr_debut::text"),
    _field("photos", "borne_fin", "Borne de fin", "photo_borne_fin.libelle"),
    _field(
        "photos", "sens_fin", "Sens depuis la borne de fin",
        "CASE WHEN p.borne_fin_distance=0 THEN 'Sur la borne' "
        "WHEN p.borne_fin_aval THEN 'Amont' "
        "WHEN NOT p.borne_fin_aval THEN 'Aval' END",
    ),
    _field(
        "photos", "distance_fin", "Distance depuis la borne de fin (m)",
        "p.borne_fin_distance::text",
    ),
    _field("photos", "pr_fin", "PR de fin", "p.pr_fin::text"),
    _field(
        "photos", "valid", "Valide",
        "CASE WHEN p.valid THEN 'Oui' ELSE 'Non' END",
    ),
    _field("mobilier", "designation", "Désignation", "m.designation", navigable=True),
    _field("mobilier", "type", "Type", "mt.libelle"),
    _field("mobilier", "commentaire", "Commentaire", "m.commentaire"),
    _field("mobilier", "date_debut", "Date de début", "m.date_debut", "date"),
    _field("mobilier", "troncon", "Tronçon", "t.libelle"),
    _field("mobilier", "digue", "Digue", "di.libelle"),
    _field("mobilier", "systeme", "Système d’endiguement", "s.libelle"),
    _field("mobilier", "localisation", "Localisation", "CASE WHEN m.geometry IS NULL THEN 'Non localisé' ELSE 'Point' END"),
    _field("mobilier", "valid", "Valide", "CASE WHEN m.valid THEN 'Oui' ELSE 'Non' END"),
    _field("equipements_mesure", "designation", "Désignation", "e.designation", navigable=True),
    _field("equipements_mesure", "type", "Type", "et.libelle"),
    _field("equipements_mesure", "commentaire", "Commentaire", "e.commentaire"),
    _field("equipements_mesure", "date_debut", "Date de début", "e.date_debut", "date"),
    _field("equipements_mesure", "troncon", "Tronçon", "t.libelle"),
    _field("equipements_mesure", "digue", "Digue", "di.libelle"),
    _field("equipements_mesure", "systeme", "Système d’endiguement", "s.libelle"),
    _field("equipements_mesure", "localisation", "Localisation", "CASE WHEN e.geometry IS NULL THEN 'Non localisé' ELSE 'Point' END"),
    _field("equipements_mesure", "valid", "Valide", "CASE WHEN e.valid THEN 'Oui' ELSE 'Non' END"),
    _field("reseaux_techniques", "designation", "Désignation", "r.designation", navigable=True),
    _field("reseaux_techniques", "type", "Type", "rt.libelle"),
    _field("reseaux_techniques", "commentaire", "Commentaire", "r.commentaire"),
    _field("reseaux_techniques", "date_debut", "Date de début", "r.date_debut", "date"),
    _field("reseaux_techniques", "troncon", "Tronçon", "t.libelle"),
    _field("reseaux_techniques", "digue", "Digue", "di.libelle"),
    _field("reseaux_techniques", "systeme", "Système d’endiguement", "s.libelle"),
    _field("reseaux_techniques", "localisation", "Localisation", "CASE WHEN r.geometry IS NULL THEN 'Non localisé' ELSE replace(ST_GeometryType(r.geometry), 'ST_', '') END"),
    _field("reseaux_techniques", "valid", "Valide", "CASE WHEN r.valid THEN 'Oui' ELSE 'Non' END"),
    _field("ouvrages_particuliers", "designation", "Désignation", "op.designation", navigable=True),
    _field("ouvrages_particuliers", "libelle", "Libellé", "op.libelle", navigable=True),
    _field("ouvrages_particuliers", "commentaire", "Commentaire", "op.commentaire"),
    _field("ouvrages_particuliers", "date_debut", "Date de début", "op.date_debut", "date"),
    _field("ouvrages_particuliers", "date_fin", "Date de fin", "op.date_fin", "date"),
    _field("ouvrages_particuliers", "type", "Type", "opt.libelle"),
    _field("ouvrages_particuliers", "troncon", "Tronçon", "t.libelle"),
    _field("ouvrages_particuliers", "digue", "Digue", "di.libelle"),
    _field("ouvrages_particuliers", "systeme", "Système d’endiguement", "s.libelle"),
    _field("ouvrages_particuliers", "localisation", "Localisation", "CASE WHEN op.geometry IS NULL THEN 'Non localisé' ELSE replace(ST_GeometryType(op.geometry), 'ST_', '') END"),
    _field("ouvrages_particuliers", "valid", "Valide", "CASE WHEN op.valid THEN 'Oui' ELSE 'Non' END"),
    _field("cheminements", "designation", "Désignation", "c.designation", navigable=True),
    _field("cheminements", "type", "Type", "rt.libelle"),
    _field("cheminements", "commentaire", "Commentaire", "c.commentaire"),
    _field("cheminements", "date_debut", "Date de début", "c.date_debut", "date"),
    _field("cheminements", "troncons", "Tronçons", "links.troncon_labels"),
    _field("cheminements", "desordres", "Désordres", "relations.desordre_labels"),
    _field("cheminements", "localisation", "Localisation", "CASE WHEN c.geometry IS NULL THEN 'Non localisé' ELSE replace(ST_GeometryType(c.geometry), 'ST_', '') END"),
    _field("cheminements", "valid", "Valide", "CASE WHEN c.valid THEN 'Oui' ELSE 'Non' END"),
    _field("vegetation", "designation", "Désignation", "v.designation", navigable=True),
    _field("vegetation", "famille", "Famille", "v.famille_vegetation"),
    _field("vegetation", "disposition", "Disposition", "v.disposition"),
    _field("vegetation", "invasif", "Invasif", "CASE WHEN v.invasif THEN 'Oui' ELSE 'Non' END"),
    _field("vegetation", "commentaire", "Commentaire", "v.commentaire"),
    _field("vegetation", "date_debut", "Date de début", "v.date_debut", "date"),
    _field("vegetation", "troncons", "Tronçons", "links.troncon_labels"),
    _field("vegetation", "amenagements", "Aménagements hydrauliques", "relations.amenagement_labels"),
    _field("vegetation", "localisation", "Localisation", "CASE WHEN v.geometry IS NULL THEN 'Non localisé' ELSE replace(ST_GeometryType(v.geometry), 'ST_', '') END"),
    _field("vegetation", "valid", "Valide", "CASE WHEN v.valid THEN 'Oui' ELSE 'Non' END"),
    _field("amenagements_hydrauliques", "designation", "Désignation", "a.designation", navigable=True),
    _field("amenagements_hydrauliques", "commentaire", "Commentaire", "a.commentaire"),
    _field("amenagements_hydrauliques", "date_debut", "Date de début", "a.date_debut", "date"),
    _field("amenagements_hydrauliques", "troncons", "Tronçons", "links.troncon_labels"),
    _field("amenagements_hydrauliques", "zones", "Zones", "links.zone_labels"),
    _field("amenagements_hydrauliques", "valid", "Valide", "CASE WHEN a.valid THEN 'Oui' WHEN NOT a.valid THEN 'Non' ELSE 'Non renseigné' END"),
    _field("zones_stockage", "designation", "Désignation", "z.designation", navigable=True),
    _field("zones_stockage", "type", "Type", "rt.libelle"),
    _field("zones_stockage", "fonctionnement", "Fonctionnement", "fon.libelle"),
    _field("zones_stockage", "capacite", "Capacité", "z.capacite_stockage", "number"),
    _field("zones_stockage", "superficie", "Superficie", "z.superficie", "number"),
    _field("zones_stockage", "profondeur", "Profondeur moyenne", "z.profondeur_moyenne", "number"),
    _field("zones_stockage", "commentaire", "Commentaire", "z.commentaire"),
    _field("zones_stockage", "ah", "AH réglementaire", "a.designation"),
    _field("zones_stockage", "localisation", "Localisation", "CASE WHEN z.geometry IS NULL THEN 'Non localisé' ELSE replace(ST_GeometryType(z.geometry), 'ST_', '') END"),
    _field("zones_stockage", "valid", "Valide", "CASE WHEN z.valid THEN 'Oui' ELSE 'Qualification incomplète' END"),
    _field("prestations", "designation", "Désignation", "pr.designation", navigable=True),
    _field("prestations", "type", "Type", "rt.designation"),
    _field("prestations", "commentaire", "Commentaire", "pr.commentaire"),
    _field("prestations", "date_debut", "Date de début", "pr.date_debut", "date"),
    _field("prestations", "date_fin", "Date de fin", "pr.date_fin", "date"),
    _field("prestations", "troncons", "Tronçons", "links.troncon_labels"),
    _field("prestations", "intervenants", "Intervenants", "relations.intervenant_labels"),
    _field("prestations", "objets", "Objets concernés", "relations.object_count::text"),
    _field("prestations", "localisation", "Localisation", "CASE WHEN pr.geometry IS NULL THEN 'Non localisé' ELSE replace(ST_GeometryType(pr.geometry), 'ST_', '') END"),
)
VIEW_FIELDS += tuple(
    _field(root, name, label, f"{alias}.{name}", kind, navigable=name in {"designation", "libelle"})
    for root, alias in (("troncons_lit", "lit"), ("elements_morphologiques_lit", "morph"))
    for name, label, kind in (
        ("designation", "Désignation", "text"), ("libelle", "Libellé", "text"),
        ("commentaire", "Commentaire", "text"), ("date_debut", "Date de début", "date"),
        ("date_fin", "Date de fin", "date"), ("valid", "Valide", "boolean"),
    )
) + (
    _field("troncons_lit", "digues", "Digues associées", "links.digue_labels"),
    _field("elements_morphologiques_lit", "type_morphologie", "Sous-type", "morph.type_morphologie"),
    _field("elements_morphologiques_lit", "troncon_lit", "Tronçon de lit", "coalesce(parent.designation,parent.libelle,parent.id::text)"),
    _field("elements_morphologiques_lit", "longueur", "Longueur", "morph.longueur::text"),
    _field("elements_morphologiques_lit", "largeur", "Largeur", "morph.largeur::text"),
    _field("elements_morphologiques_lit", "rattache_berge", "Rattaché à la berge", "morph.rattache_berge", "boolean"),
    _field("elements_morphologiques_lit", "vegetalise", "Végétalisé", "morph.vegetalise", "boolean"),
    _field("elements_morphologiques_lit", "type_zone", "Type d’atterrissement", "zone.libelle"),
)
VIEW_FIELDS_BY_KEY = {field.key: field for field in VIEW_FIELDS}
TABLE_LABELS = {class_id: item.label for class_id, item in VIEW_CLASSES.items()}


REFERENCE_JOINS = {
    "troncons": """
        LEFT JOIN referentiel.types_troncon_unifie AS type_physique ON type_physique.id = t.type_physique_id
        LEFT JOIN public.amenagements_hydrauliques AS ah ON ah.id = t.amenagement_hydraulique_id
        LEFT JOIN public.systemes_reperage AS reperage
            ON reperage.id = t.systeme_reperage_defaut_id
        LEFT JOIN referentiel.rives AS rive ON rive.id = t.type_rive_id
        LEFT JOIN referentiel.types_troncon AS type_troncon
            ON type_troncon.id = t.type_troncon_id
        LEFT JOIN referentiel.typologies_troncon AS typologie
            ON typologie.id = t.typologie_troncon_id
    """,
    "desordres": """
        LEFT JOIN referentiel.types_desordre AS td ON td.id = d.type_desordre_id
        LEFT JOIN referentiel.categories_desordre AS categorie
            ON categorie.id = d.categorie_desordre_id
        LEFT JOIN referentiel.cotes AS cote ON cote.id = d.cote_id
        LEFT JOIN referentiel.positions AS position ON position.id = d.position_id
        LEFT JOIN referentiel.sources_information AS source ON source.id = d.source_id
    """,
    "observations": """
        LEFT JOIN referentiel.urgences AS urgence ON urgence.id = o.urgence_id
        LEFT JOIN referentiel.suites_apporter AS suite_apporter
            ON suite_apporter.id = o.suite_apporter_id
    """,
    "photos": """
        LEFT JOIN referentiel.cotes AS photo_cote ON photo_cote.id = p.cote_id
        LEFT JOIN referentiel.orientations_photo AS orientation
            ON orientation.id = p.orientation_photo_id
        LEFT JOIN public.systemes_reperage AS photo_reperage
            ON photo_reperage.id = p.systeme_reperage_id
        LEFT JOIN public.bornes_reperage AS photo_borne_debut
            ON photo_borne_debut.id = p.borne_debut_id
        LEFT JOIN public.bornes_reperage AS photo_borne_fin
            ON photo_borne_fin.id = p.borne_fin_id
    """,
}


def view_catalog() -> dict[str, Any]:
    """Retourne les racines, classes, relations et champs autorisés."""

    return {
        "default_root": "desordres",
        "default_view": {
            "root": HOME_VIEW_ROOT,
            "fields": [field.model_dump() for field in HOME_VIEW_FIELDS],
        },
        "roots": [
            {"id": class_id, "label": ROOT_LABELS[class_id]}
            for class_id in (*VIEW_CHAIN, *PILOT_ROOTS)
        ],
        "relations": [
            {
                "parent": relation.parent,
                "child": relation.child,
                "cardinality": relation.cardinality,
                "through": relation.through,
            }
            for relation in VIEW_RELATIONS
        ],
        "tables": [
            {
                "id": class_id,
                "label": VIEW_CLASSES[class_id].label,
                "compatible_roots": list(VIEW_CHAIN) if class_id in VIEW_CHAIN else [class_id],
                "fields": [
                    {
                        "id": field.field,
                        "label": field.label,
                        "type": field.data_type,
                        "source_class": field.table,
                        "source_field": field.field,
                        "navigable": field.navigable,
                    }
                    for field in VIEW_FIELDS if field.table == class_id
                ],
            }
            for class_id in (*VIEW_CHAIN, *PILOT_ROOTS)
        ],
    }


def _selected_fields(selections: list[DataViewFieldSelection]) -> list[ViewField]:
    fields = []
    for selection in selections:
        key = f"{selection.table}.{selection.field}"
        field = VIEW_FIELDS_BY_KEY.get(key)
        if field is None:
            raise DataViewValidationError(f"Champ de vue non autorisé : {key}.")
        fields.append(field)
    return fields


def validate_branch_selection(root: str, selected_classes: set[str]) -> None:
    """Refuse les branches indépendantes qui multiplieraient les lignes."""

    if root in PILOT_ROOTS:
        if selected_classes - {root}:
            raise DataViewValidationError("La vue pilote accepte uniquement les champs de sa catégorie.")
        return
    root_index = VIEW_CHAIN.index(root)
    if root_index > VIEW_CHAIN.index("troncons"):
        return
    branches = {
        TRONCON_DESCENDANT_BRANCH[class_id]
        for class_id in selected_classes
        if class_id in TRONCON_DESCENDANT_BRANCH
    }
    if len(branches) <= 1:
        return
    labels = ", ".join(BRANCH_LABELS[branch] for branch in sorted(branches))
    raise DataViewValidationError(
        "La vue sélectionne plusieurs branches enfants indépendantes depuis "
        f"Tronçons : {labels}. Choisissez une seule branche descendante principale."
    )


def relation_path(root: str, target: str) -> tuple[str, ...]:
    """Retourne le chemin le plus court dans le graphe métier bidirectionnel."""

    if root not in VIEW_CLASSES or target not in VIEW_CLASSES:
        raise DataViewValidationError("Classe de vue non prise en charge.")
    pending = [(root, (root,))]
    visited = {root}
    while pending:
        current, path = pending.pop(0)
        if current == target:
            return path
        neighbours = [
            relation.child for relation in RELATIONS_BY_PARENT[current]
        ] + [
            relation.parent for relation in RELATIONS_BY_CHILD[current]
        ]
        for neighbour in neighbours:
            if neighbour not in visited:
                visited.add(neighbour)
                pending.append((neighbour, path + (neighbour,)))
    raise DataViewValidationError(
        f"Aucun chemin relationnel entre {root} et {target}."
    )


def descendant_depths(root: str) -> dict[str, int]:
    """Calcule les profondeurs enfant depuis une racine dans le graphe orienté."""

    if root not in VIEW_CLASSES:
        raise DataViewValidationError(f"Objet principal non autorisé : {root}.")
    depths = {root: 0}
    pending = [root]
    while pending:
        current = pending.pop(0)
        for relation in RELATIONS_BY_PARENT[current]:
            if relation.child not in depths:
                depths[relation.child] = depths[current] + 1
                pending.append(relation.child)
    return depths


def effective_granularity(root: str, selected_classes: set[str]) -> str:
    """Le dernier descendant sélectionné porte une ligne du résultat."""

    depths = descendant_depths(root)
    descendants = [
        (depths[class_id], class_id)
        for class_id in selected_classes if class_id in depths
    ]
    return max(descendants, default=(0, root))[1]


def _entity_sql(class_id: str) -> str:
    expressions = {
        "systemes": """jsonb_build_object(
            'class', 'systemes', 'id', s.id,
            'geometry', ST_AsGeoJSON(ST_Transform(s.zone_protegee, 4326))::jsonb,
            'attributes', jsonb_build_object(
                'libelle', s.libelle, 'designation', s.designation,
                'commentaire', s.commentaire,
                'population_protegee', s.population_protegee,
                'classement', s.classement,
                'niveau_protection', s.niveau_protection, 'valid', s.valid))""",
        "digues": """jsonb_build_object(
            'class', 'digues', 'id', di.id, 'geometry', null,
            'attributes', jsonb_build_object(
                'libelle', di.libelle, 'designation', di.designation,
                'commentaire', di.commentaire, 'valid', di.valid))""",
        "troncons": f"""jsonb_build_object(
            'class', 'troncons', 'id', t.id,
            'geometry', {TRONCON_GEOMETRY},
            'attributes', jsonb_build_object(
                'type_physique_id', t.type_physique_id, 'type_physique', type_physique.libelle,
                'digue_id', t.digue_id, 'amenagement_hydraulique_id', t.amenagement_hydraulique_id,
                'amenagement_hydraulique', coalesce(ah.libelle, ah.designation),
                'geometry_type', GeometryType(t.geometry), 'geometry_srid', ST_SRID(t.geometry),
                'geometry_dimensions', ST_NDims(t.geometry), 'geometry_geojson_supportee', {GEOJSON_SUPPORTED},
                'geometry_vide', ST_IsEmpty(t.geometry),
                'libelle', t.libelle, 'designation', t.designation,
                'date_debut', t.date_debut, 'date_fin', t.date_fin,
                'commentaire', t.commentaire, 'rive', rive.libelle,
                'type', type_troncon.libelle, 'typologie', typologie.libelle,
                'systeme_reperage', reperage.libelle, 'valid', t.valid))""",
        "desordres": """jsonb_build_object(
            'class', 'desordres', 'id', d.id,
            'geometry', ST_AsGeoJSON(ST_Transform(d.geometry, 4326))::jsonb,
            'attributes', jsonb_build_object(
                'designation', d.designation, 'type', td.libelle,
                'categorie', categorie.libelle, 'commentaire', d.commentaire,
                'lieu_dit', d.lieu_dit, 'cote', cote.libelle,
                'position', position.libelle, 'source', source.libelle,
                'date_debut', d.date_debut, 'date_fin', d.date_fin,
                'valid', d.valid))""",
        "observations": """jsonb_build_object(
            'class', 'observations', 'id', o.id, 'geometry', null,
            'attributes', jsonb_build_object(
                'designation', o.designation, 'date', o.date_observation,
                'urgence', urgence.libelle, 'evolution', o.evolution,
                'suite', o.suite, 'suite_apporter', suite_apporter.libelle,
                'nombre_desordres', o.nombre_desordres, 'valid', o.valid))""",
        "photos": """jsonb_build_object(
            'class', 'photos', 'id', p.id,
            'geometry', ST_AsGeoJSON(ST_Transform(p.geometry, 4326))::jsonb,
            'attributes', jsonb_build_object(
                'libelle', p.libelle, 'designation', p.designation,
                'date', p.date_photo, 'commentaire', p.commentaire,
                'cote', photo_cote.libelle, 'orientation', orientation.libelle,
                'systeme_reperage', photo_reperage.libelle,
                'borne_debut', photo_borne_debut.libelle,
                'sens_debut', CASE WHEN p.borne_debut_distance=0 THEN 'Sur la borne'
                    WHEN p.borne_debut_aval THEN 'Amont'
                    WHEN NOT p.borne_debut_aval THEN 'Aval' END,
                'distance_debut', p.borne_debut_distance,
                'pr_debut', p.pr_debut, 'borne_fin', photo_borne_fin.libelle,
                'sens_fin', CASE WHEN p.borne_fin_distance=0 THEN 'Sur la borne'
                    WHEN p.borne_fin_aval THEN 'Amont'
                    WHEN NOT p.borne_fin_aval THEN 'Aval' END,
                'distance_fin', p.borne_fin_distance,
                'pr_fin', p.pr_fin, 'valid', p.valid))""",
    }
    return expressions[class_id]


def _optional_entity_array(class_id: str) -> str:
    alias = VIEW_CLASSES[class_id].alias
    return (
        f"CASE WHEN {alias}.id IS NULL THEN '[]'::jsonb ELSE "
        f"jsonb_build_array({_entity_sql(class_id)}) END"
    )


def _related_troncon_ids_sql(observation_available: bool) -> str:
    disorder_link = """
        SELECT relation.troncon_id
        FROM public.desordres__troncons AS relation
        WHERE relation.desordre_id = d.id
    """
    if not observation_available:
        return disorder_link
    return disorder_link + """
        UNION SELECT o.troncon_id WHERE o.troncon_id IS NOT NULL
        UNION
        SELECT ouvrage.troncon_id
        FROM public.ouvrages_hydrauliques AS ouvrage
        WHERE ouvrage.id = o.ouvrage_hydraulique_id
          AND ouvrage.troncon_id IS NOT NULL
        UNION
        SELECT equipement.troncon_id
        FROM public.equipements_mesure AS equipement
        WHERE equipement.id = o.equipement_mesure_id
          AND equipement.troncon_id IS NOT NULL
        UNION
        SELECT lien.troncon_id
        FROM public.cheminements__troncons AS lien
        WHERE lien.cheminement_id = o.cheminement_id
        UNION
        SELECT mobilier.troncon_id
        FROM public.mobilier AS mobilier
        WHERE mobilier.id = o.mobilier_id AND mobilier.troncon_id IS NOT NULL
        UNION
        SELECT reseau.troncon_id
        FROM public.reseaux_techniques AS reseau
        WHERE reseau.id = o.reseau_technique_id
          AND reseau.troncon_id IS NOT NULL
        UNION
        SELECT ouvrage.troncon_id
        FROM public.ouvrages_particuliers AS ouvrage
        WHERE ouvrage.id = o.ouvrage_particulier_id
          AND ouvrage.troncon_id IS NOT NULL
        UNION
        SELECT lien.troncon_id
        FROM public.vegetation__troncons AS lien
        WHERE lien.vegetation_id = o.vegetation_id
    """


def _heritage_lateral(
    fields: list[ViewField], observation_available: bool,
) -> tuple[str, dict[str, str]]:
    heritage_fields = [
        field for field in fields
        if VIEW_CHAIN.index(field.table) <= VIEW_CHAIN.index("troncons")
    ]
    aliases = {
        field.key: f"heritage.field_{index}"
        for index, field in enumerate(heritage_fields)
    }
    projections = []
    for index, field in enumerate(heritage_fields):
        value = f"({field.expression})::text"
        projections.append(
            f"string_agg(DISTINCT {value}, ' · ' ORDER BY {value}) AS field_{index}"
        )
    extra = ",\n                " + ",\n                ".join(projections) if projections else ""
    sql = f"""
        LEFT JOIN LATERAL (
            SELECT
                coalesce(jsonb_agg(DISTINCT {_entity_sql('troncons')})
                    FILTER (WHERE t.id IS NOT NULL), '[]'::jsonb)
                || coalesce(jsonb_agg(DISTINCT {_entity_sql('digues')})
                    FILTER (WHERE di.id IS NOT NULL), '[]'::jsonb)
                || coalesce(jsonb_agg(DISTINCT {_entity_sql('systemes')})
                    FILTER (WHERE s.id IS NOT NULL), '[]'::jsonb) AS entities,
                count(DISTINCT t.id) AS troncon_count,
                count(DISTINCT di.id) AS digue_count,
                count(DISTINCT s.id) AS systeme_count,
                coalesce(array_agg(DISTINCT t.id)
                    FILTER (WHERE t.id IS NOT NULL), ARRAY[]::uuid[]) AS troncon_ids
                {extra}
            FROM ({_related_troncon_ids_sql(observation_available)}) AS related
            JOIN public.troncons AS t
                ON t.id = related.troncon_id
            LEFT JOIN public.digues AS di ON di.id = t.digue_id
            LEFT JOIN public.systemes AS s ON s.id = di.systeme_endiguement_id
            {REFERENCE_JOINS['troncons']}
        ) AS heritage ON true
    """
    return sql, aliases


def _scope_troncons_lateral(granularity: str) -> str:
    """Expose les tronçons couverts par une ligne SE ou Digue pour Patrimoine."""

    if granularity == "systemes":
        relation = """
            JOIN public.digues AS scope_digue ON scope_digue.id = t.digue_id
            WHERE scope_digue.systeme_endiguement_id = s.id
        """
    elif granularity == "digues":
        relation = "WHERE t.digue_id = di.id"
    else:
        raise ValueError("La portée tronçon ne concerne que les SE et les digues.")
    return f"""
        LEFT JOIN LATERAL (
            SELECT coalesce(
                jsonb_agg({_entity_sql('troncons')}
                    ORDER BY coalesce(t.designation, t.libelle, ''), t.id),
                '[]'::jsonb
            ) AS entities,
            coalesce(array_agg(t.id ORDER BY t.id), ARRAY[]::uuid[]) AS troncon_ids
            FROM public.troncons AS t
            {REFERENCE_JOINS['troncons']}
            {relation}
        ) AS scope_troncons ON true
    """


RELATED_ROOT_LINKS = {
    "cheminements": ("public.cheminements__troncons", "cheminement_id"),
    "vegetation": ("public.vegetation__troncons", "vegetation_id"),
    "prestations": ("public.prestations_troncons", "prestation_id"),
}
RELATED_ROOT_TYPES = {
    "cheminements": ("referentiel.types_cheminement", "type_cheminement_id", "libelle"),
    "prestations": ("referentiel.types_prestation", "type_prestation_id", "designation"),
}


def _build_related_rows_query(root: str, fields: list[ViewField]) -> tuple[str, str]:
    """Une ligne par racine N:N ; Patrimoine couvre ses seuls tronçons explicites."""

    source = VIEW_CLASSES[root]
    link_table, link_parent = RELATED_ROOT_LINKS[root]
    if root == "vegetation":
        type_expression = "v.famille_vegetation"
        type_join = ""
        sort_expression = "coalesce(v.designation, v.famille_vegetation, '')"
    else:
        type_table, type_fk, type_label = RELATED_ROOT_TYPES[root]
        type_expression = f"rt.{type_label}"
        type_join = f"LEFT JOIN {type_table} AS rt ON rt.id = {source.alias}.{type_fk}"
        sort_expression = (f"coalesce({source.alias}.designation, "
                           f"{source.alias}.libelle, {type_expression}, '')")
    extra_relations = {
        "cheminements": """(SELECT string_agg(DISTINCT coalesce(d.designation, d.id::text), ' · ')
            FROM public.cheminements__desordres cd
            JOIN public.desordres d ON d.id = cd.desordre_id
            WHERE cd.cheminement_id = c.id) AS desordre_labels""",
        "vegetation": """(SELECT string_agg(DISTINCT coalesce(a.designation, a.libelle, a.id::text), ' · ')
            FROM public.vegetation__amenagements_hydrauliques va
            JOIN public.amenagements_hydrauliques a ON a.id = va.amenagement_hydraulique_id
            WHERE va.vegetation_id = v.id) AS amenagement_labels""",
        "amenagements_hydrauliques": "NULL::text AS unused_label",
        "prestations": """(SELECT string_agg(DISTINCT coalesce(contact.designation,
                concat_ws(' ', contact.prenom, contact.nom), contact.id::text), ' · ')
            FROM public.prestations_intervenants pi
            JOIN public.contacts contact ON contact.id = pi.contact_id
            WHERE pi.prestation_id = pr.id) AS intervenant_labels,
            (SELECT count(*) FROM public.prestations_objets po
             WHERE po.prestation_id = pr.id) AS object_count""",
    }
    values = ",\n            ".join(
        f"{field.expression} AS value_{index}" for index, field in enumerate(fields)
    )
    query = f"""
        SELECT
            jsonb_build_array(jsonb_build_object(
                'class', '{root}', 'id', {source.alias}.id,
                'geometry', CASE WHEN {source.alias}.geometry IS NULL THEN NULL
                    ELSE ST_AsGeoJSON(ST_Transform({source.alias}.geometry, 4326))::jsonb END,
                'attributes', jsonb_build_object(
                    'designation', {source.alias}.designation,
                    'commentaire', {source.alias}.commentaire,
                    'date_debut', {source.alias}.date_debut,
                    'type', {type_expression}, 'valid', {source.alias}.valid)))
            || links.entities AS entities,
            links.troncon_ids AS troncon_ids,
            {sort_expression} AS sort_value,
            {source.alias}.id AS sort_id,
            {values}
        FROM {source.table} AS {source.alias}
        {type_join}
        LEFT JOIN LATERAL (
            SELECT
                coalesce(array_agg(DISTINCT t.id)
                    FILTER (WHERE t.id IS NOT NULL), ARRAY[]::uuid[]) AS troncon_ids,
                string_agg(DISTINCT t.libelle, ' · ') AS troncon_labels,
                coalesce(jsonb_agg(DISTINCT {_entity_sql('troncons')})
                    FILTER (WHERE t.id IS NOT NULL), '[]'::jsonb)
                || coalesce(jsonb_agg(DISTINCT {_entity_sql('digues')})
                    FILTER (WHERE di.id IS NOT NULL), '[]'::jsonb)
                || coalesce(jsonb_agg(DISTINCT {_entity_sql('systemes')})
                    FILTER (WHERE s.id IS NOT NULL), '[]'::jsonb) AS entities
            FROM {link_table} AS link
            JOIN public.troncons AS t ON t.id = link.troncon_id
            LEFT JOIN public.digues AS di ON di.id = t.digue_id
            LEFT JOIN public.systemes AS s ON s.id = di.systeme_endiguement_id
            {REFERENCE_JOINS['troncons']}
            WHERE link.{link_parent} = {source.alias}.id
        ) AS links ON true
        LEFT JOIN LATERAL (SELECT {extra_relations[root]}) AS relations ON true
    """
    return query, root


def _build_lit_rows_query(root: str, fields: list[ViewField]) -> tuple[str, str]:
    """Autonomous objects; historical geometry is projected by detail/Carte only."""
    source = VIEW_CLASSES[root]
    alias = source.alias
    joins = """
        LEFT JOIN LATERAL (
            SELECT string_agg(coalesce(d.designation,d.libelle,d.id::text), ', ' ORDER BY d.id) AS digue_labels
            FROM public.segments_hydrographiques__digues AS link
            JOIN public.digues AS d ON d.id=link.digue_id
            WHERE link.segment_id=lit.id
        ) AS links ON true
    """ if root == "troncons_lit" else """
        LEFT JOIN public.segments_hydrographiques AS parent ON parent.id=morph.segment_id
        LEFT JOIN referentiel.types_zone_atterrissement_lit AS zone ON zone.id=morph.type_zone_id
    """
    values = ", ".join(f"{field.expression} AS value_{index}" for index, field in enumerate(fields))
    extra = "'digues', links.digue_labels" if root == "troncons_lit" else (
        "'type_morphologie', morph.type_morphologie, 'segment_id', morph.segment_id"
    )
    return f"""
        SELECT jsonb_build_array(jsonb_build_object(
            'class', '{root}', 'id', {alias}.id, 'geometry', NULL,
            'attributes', jsonb_build_object('designation', {alias}.designation,
                'libelle', {alias}.libelle, 'commentaire', {alias}.commentaire,
                'valid', {alias}.valid, {extra}))) AS entities,
            ARRAY[]::uuid[] AS troncon_ids,
            coalesce({alias}.designation,{alias}.libelle,'') AS sort_value,
            {alias}.id AS sort_id, {values}
        FROM {source.table} AS {alias} {joins}
    """, root


def _build_hydraulic_rows_query(root: str, fields: list[ViewField]) -> tuple[str, str]:
    """Racines AH/Zone autonomes ; aucune relation Zone-Tronçon déduite."""
    source = VIEW_CLASSES[root]
    alias = source.alias
    values = ", ".join(f"{field.expression} AS value_{index}" for index, field in enumerate(fields))
    if root == "amenagements_hydrauliques":
        geometry = "NULL"
        joins = """
            LEFT JOIN LATERAL (SELECT
                (SELECT string_agg(coalesce(tu.designation,tu.id::text), ' · ' ORDER BY tu.id)
                 FROM public.troncons tu WHERE tu.amenagement_hydraulique_id=a.id)
                    AS troncon_labels,
                (SELECT string_agg(coalesce(z.designation,z.libelle,z.id::text), ' · ' ORDER BY z.id)
                 FROM public.zones_stockage z WHERE z.amenagement_hydraulique_id=a.id)
                    AS zone_labels) links ON true
        """
    else:
        geometry = ("CASE WHEN z.valid IS TRUE AND z.geometry IS NOT NULL AND ST_SRID(z.geometry)>0 "
                    "THEN ST_AsGeoJSON(ST_Transform(z.geometry,4326))::jsonb ELSE NULL END")
        joins = """
            LEFT JOIN referentiel.types_amenagement_hydraulique rt ON rt.id=z.type_id
            LEFT JOIN referentiel.fonctionnements_amenagement_hydraulique fon ON fon.id=z.fonctionnement_id
            LEFT JOIN public.amenagements_hydrauliques a ON a.id=z.amenagement_hydraulique_id
        """
    return f"""
        SELECT jsonb_build_array(jsonb_build_object(
            'class','{root}','id',{alias}.id,'geometry',{geometry},
            'attributes',jsonb_build_object('designation',{alias}.designation,
                'libelle',{alias}.libelle,'commentaire',{alias}.commentaire,
                'valid',{alias}.valid))) AS entities,
            ARRAY[]::uuid[] AS troncon_ids,
            coalesce({alias}.designation,{alias}.libelle,'') AS sort_value,
            {alias}.id AS sort_id, {values}
        FROM {source.table} {alias} {joins}
    """, root


def _build_pilot_rows_query(root: str, fields: list[ViewField]) -> tuple[str, str]:
    """Une ligne par objet, avec ses seuls rattachements plusieurs-vers-un."""

    if root in {"troncons_lit", "elements_morphologiques_lit"}:
        return _build_lit_rows_query(root, fields)
    if root in {"amenagements_hydrauliques", "zones_stockage"}:
        return _build_hydraulic_rows_query(root, fields)
    if root in RELATED_ROOT_LINKS:
        return _build_related_rows_query(root, fields)
    source = VIEW_CLASSES[root]
    type_sources = {
        "mobilier": ("referentiel.types_mobilier", "mt"),
        "equipements_mesure": ("referentiel.types_equipement_mesure", "et"),
        "reseaux_techniques": ("referentiel.types_reseau_technique", "rt"),
        "ouvrages_particuliers": ("referentiel.types_ouvrage_particulier", "opt"),
    }
    type_source = type_sources[root]
    join_kind = "LEFT JOIN" if root == "ouvrages_particuliers" else "JOIN"
    type_join = (f"{join_kind} {type_source[0]} AS {type_source[1]} "
                 f"ON {type_source[1]}.id = {source.alias}.type_id") if type_source else ""
    type_label = f"{type_source[1]}.libelle" if type_source else None
    type_attribute = f"'type', {type_label}," if type_label else ""
    sort_label = f"coalesce({source.alias}.designation, {type_label}, '')" if type_source else (
        f"coalesce({source.alias}.designation, {source.alias}.libelle, '')"
    )
    extra_attributes = (f", 'libelle', {source.alias}.libelle"
                        if root == "ouvrages_particuliers" else "")
    values = ",\n            ".join(
        f"{field.expression} AS value_{index}" for index, field in enumerate(fields)
    )
    query = f"""
        SELECT
            jsonb_build_array(jsonb_build_object(
                'class', '{root}', 'id', {source.alias}.id,
                'geometry', CASE WHEN {source.alias}.geometry IS NULL THEN NULL
                    ELSE ST_AsGeoJSON(ST_Transform({source.alias}.geometry, 4326))::jsonb END,
                'attributes', jsonb_build_object(
                    'designation', {source.alias}.designation,
                    {type_attribute}
                    'commentaire', {source.alias}.commentaire,
                    'date_debut', {source.alias}.date_debut,
                    'valid', {source.alias}.valid{extra_attributes})))
            || {_optional_entity_array('troncons')}
            || {_optional_entity_array('digues')}
            || {_optional_entity_array('systemes')} AS entities,
            CASE WHEN t.id IS NULL THEN ARRAY[]::uuid[] ELSE ARRAY[t.id]::uuid[] END AS troncon_ids,
            {sort_label} AS sort_value,
            {source.alias}.id AS sort_id,
            {values}
        FROM {source.table} AS {source.alias}
        {type_join}
        LEFT JOIN public.troncons AS t ON t.id = {source.alias}.troncon_id
        LEFT JOIN public.digues AS di ON di.id = t.digue_id
        LEFT JOIN public.systemes AS s ON s.id = di.systeme_endiguement_id
        {REFERENCE_JOINS['troncons']}
    """
    return query, root


def _build_view_rows_query(root: str, fields: list[ViewField]) -> tuple[str, str]:
    """Construit les lignes métier non filtrées et non paginées."""

    if root not in VIEW_CLASSES:
        raise DataViewValidationError(f"Objet principal non autorisé : {root}.")
    selected_classes = {field.table for field in fields}
    validate_branch_selection(root, selected_classes)
    if root in PILOT_ROOTS:
        return _build_pilot_rows_query(root, fields)
    if not selected_classes <= set(VIEW_CLASSES):
        raise DataViewValidationError("Le chemin relationnel demandé n’est pas pris en charge.")
    for class_id in selected_classes:
        relation_path(root, class_id)

    granularity = effective_granularity(root, selected_classes)
    granularity_index = VIEW_CHAIN.index(granularity)
    root_index = VIEW_CHAIN.index(root)
    troncon_index = VIEW_CHAIN.index("troncons")
    disorder_index = VIEW_CHAIN.index("desordres")
    joins: list[str] = []
    where: list[str] = []
    expression_by_key = {field.key: field.expression for field in fields}

    if granularity_index <= troncon_index:
        base = VIEW_CLASSES[granularity]
        from_sql = f"FROM {base.table} AS {base.alias}"
        if granularity == "digues":
            joins.append(
                "LEFT JOIN public.systemes AS s ON s.id = di.systeme_endiguement_id"
            )
        elif granularity == "troncons":
            joins.extend((
                "LEFT JOIN public.digues AS di ON di.id = t.digue_id",
                "LEFT JOIN public.systemes AS s ON s.id = di.systeme_endiguement_id",
                REFERENCE_JOINS["troncons"],
            ))
        if root == "systemes" and granularity != "systemes":
            where.append("s.id IS NOT NULL")
        lineage = VIEW_CHAIN[:granularity_index + 1]
        provenance = " || ".join(
            _optional_entity_array(class_id) for class_id in lineage
        )
        if granularity in {"systemes", "digues"}:
            joins.append(_scope_troncons_lateral(granularity))
            provenance += " || scope_troncons.entities"
            troncon_ids = "scope_troncons.troncon_ids"
        else:
            troncon_ids = "ARRAY[t.id]::uuid[]"
    else:
        base = VIEW_CLASSES[granularity]
        from_sql = f"FROM {base.table} AS {base.alias}"
        if granularity == "photos":
            joins.append("JOIN public.observations AS o ON o.id = p.observation_id")
        if granularity_index >= VIEW_CHAIN.index("observations"):
            disorder_join = "JOIN" if root_index <= disorder_index else "LEFT JOIN"
            joins.append(
                f"{disorder_join} public.desordres AS d ON d.id = o.desordre_id"
            )
            joins.append(REFERENCE_JOINS["observations"])
        joins.append(REFERENCE_JOINS["desordres"])
        if granularity == "photos":
            joins.append(REFERENCE_JOINS["photos"])
        heritage_sql, heritage_aliases = _heritage_lateral(
            fields,
            granularity_index >= VIEW_CHAIN.index("observations"),
        )
        joins.append(heritage_sql)
        expression_by_key.update(heritage_aliases)
        direct_lineage = ["desordres"]
        if granularity_index >= VIEW_CHAIN.index("observations"):
            direct_lineage.append("observations")
        if granularity == "photos":
            direct_lineage.append("photos")
        provenance = "heritage.entities || " + " || ".join(
            _optional_entity_array(class_id) for class_id in direct_lineage
        )
        troncon_ids = "heritage.troncon_ids"
        root_count = {
            "systemes": "heritage.systeme_count > 0",
            "digues": "heritage.digue_count > 0",
            "troncons": "heritage.troncon_count > 0",
        }.get(root)
        if root_count:
            where.append(root_count)

    expressions = ",\n            ".join(
        f"{expression_by_key[field.key]} AS value_{index}"
        for index, field in enumerate(fields)
    )
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    sort_expression = {
        "systemes": "coalesce(s.designation, s.libelle, '')",
        "digues": "coalesce(di.designation, di.libelle, '')",
        "troncons": "coalesce(t.designation, t.libelle, '')",
        "desordres": "coalesce(d.designation, '')",
        "observations": "coalesce(o.designation, '')",
        "photos": "coalesce(p.designation, p.libelle, '')",
    }[granularity]
    query = f"""
        SELECT
            {provenance} AS entities,
            coalesce({troncon_ids}, ARRAY[]::uuid[]) AS troncon_ids,
            {sort_expression} AS sort_value,
            {base.alias}.id AS sort_id,
            {expressions}
        {from_sql}
        {' '.join(joins)}
        {where_sql}
    """
    return query, granularity


def _patrimoine_filter_sql() -> str:
    return "NOT (view_rows.troncon_ids && %s::uuid[])"


def build_view_query(root: str, fields: list[ViewField]) -> tuple[str, str]:
    """Construit la page SQL, filtrée avant son LIMIT/OFFSET."""

    rows_query, granularity = _build_view_rows_query(root, fields)
    value_columns = ", ".join(f"value_{index}" for index in range(len(fields)))
    query = f"""
        WITH view_rows AS ({rows_query})
        SELECT entities, {value_columns}
        FROM view_rows
        WHERE {_patrimoine_filter_sql()}
        ORDER BY sort_value NULLS LAST, sort_id
        LIMIT %s OFFSET %s
    """
    return query, granularity


def _view_count_query(root: str, fields: list[ViewField]) -> tuple[str, str]:
    rows_query, granularity = _build_view_rows_query(root, fields)
    return f"""
        WITH view_rows AS ({rows_query})
        SELECT count(*)
        FROM view_rows
        WHERE {_patrimoine_filter_sql()}
    """, granularity


def _columns(fields: list[ViewField]) -> list[dict[str, Any]]:
    return [
        {
            "key": field.key,
            "label": f"{TABLE_LABELS[field.table]} — {field.label}",
            "type": field.data_type,
            "source_class": field.table,
            "source_field": field.field,
            "navigable": field.navigable,
        }
        for field in fields
    ]


def _serialized_rows(raw_rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [
        {
            "entities": raw_row[0] or [],
            "values": [serialize_sql_value(value) for value in raw_row[1:]],
        }
        for raw_row in raw_rows
    ]


def execute_view_query(
    connection: Any,
    root: str,
    selections: list[DataViewFieldSelection],
    hidden_troncon_ids: list[Any] | None = None,
    offset: int = 0,
    page_size: int = DATA_VIEW_MAX_ROWS,
) -> dict[str, Any]:
    """Exécute une projection contrôlée selon une racine explicite."""

    if root not in VIEW_CLASSES:
        raise DataViewValidationError(f"Objet principal non autorisé : {root}.")
    validate_branch_selection(root, {selection.table for selection in selections})
    fields = _selected_fields(selections)
    if offset < 0 or not 1 <= page_size <= DATA_VIEW_MAX_ROWS:
        raise DataViewValidationError("Pagination de vue invalide.")
    query, granularity = build_view_query(root, fields)
    count_query, _ = _view_count_query(root, fields)
    hidden_ids = [str(identifier) for identifier in (hidden_troncon_ids or [])]
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(count_query, (hidden_ids,))
                count_row = cursor.fetchone()
                total = int(count_row[0]) if count_row else 0
                cursor.execute(query, (hidden_ids, page_size, offset))
                raw_rows = cursor.fetchall()
    except Exception as exc:
        raise WebDatabaseError("Construction de la vue impossible.") from exc

    rows = _serialized_rows(raw_rows)
    return {
        "root": root,
        "granularity": granularity,
        "columns": _columns(fields),
        "rows": rows,
        "row_count": len(rows),
        "total": total,
        "offset": offset,
        "page_size": page_size,
        "has_previous": offset > 0,
        "has_next": offset + len(rows) < total,
        "truncated": offset > 0 or offset + len(rows) < total,
    }


def execute_view_export(
    connection: Any,
    root: str,
    selections: list[DataViewFieldSelection],
    hidden_troncon_ids: list[Any] | None = None,
    *, include_native_troncons: bool = False,
) -> dict[str, Any]:
    """Rejoue côté serveur tout le résultat filtré d'un export Data."""

    fields = _selected_fields(selections)
    validate_branch_selection(root, {field.table for field in fields})
    query, granularity = build_view_query(root, fields)
    count_query, _ = _view_count_query(root, fields)
    hidden_ids = [str(identifier) for identifier in (hidden_troncon_ids or [])]
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cursor.execute(count_query, (hidden_ids,))
                count_row = cursor.fetchone()
                total = int(count_row[0]) if count_row else 0
                if total > DATA_VIEW_MAX_EXPORT_ROWS:
                    raise DataViewExportLimitError(
                        "L’export contient "
                        f"{total} lignes, au-delà de la limite technique de "
                        f"{DATA_VIEW_MAX_EXPORT_ROWS}. Réduisez le périmètre Patrimoine."
                    )
                cursor.execute(query, (hidden_ids, DATA_VIEW_MAX_EXPORT_ROWS + 1, 0))
                raw_rows = cursor.fetchall()
                rows = _serialized_rows(raw_rows)
                if include_native_troncons:
                    from .geopackage import attach_native_troncons
                    attach_native_troncons(cursor, rows)
    except DataViewExportLimitError:
        raise
    except Exception as exc:
        raise WebDatabaseError("Construction de l’export de vue impossible.") from exc
    return {
        "root": root,
        "granularity": granularity,
        "columns": _columns(fields),
        "rows": rows,
        "row_count": len(raw_rows),
        "total": total,
    }


HOME_VIEW_ROOT = "desordres"
HOME_VIEW_FIELDS = (
    DataViewFieldSelection(table="systemes", field="libelle"),
    DataViewFieldSelection(table="digues", field="libelle"),
    DataViewFieldSelection(table="troncons", field="libelle"),
    DataViewFieldSelection(table="desordres", field="designation"),
)


def execute_home_view(
    connection: Any,
    hidden_troncon_ids: list[Any] | None = None,
    offset: int = 0,
    page_size: int = DATA_VIEW_MAX_ROWS,
) -> dict[str, Any]:
    """Configuration interne fixe de l'accueil Données."""

    return execute_view_query(
        connection,
        HOME_VIEW_ROOT,
        list(HOME_VIEW_FIELDS),
        hidden_troncon_ids,
        offset,
        page_size,
    )
