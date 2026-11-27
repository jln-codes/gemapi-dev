"""Initialisation des catalogues métier du schéma neuf DB-2.

Les contrats C1/C2 restent des entrées de conversion. Seules les valeurs
métier sont projetées dans les tables permanentes de ``referentiel``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
import re

from digues_app.target.schema import REFERENCE_TABLES, TABLE_DEFINITIONS
from . import ReferenceContractError, ReferenceContracts, load_contracts
from .initialization import reference_table_specs as historical_reference_specs
from .measurement_types import PARTICULIER_TO_MEASUREMENT


# Chaque catalogue cible est alimenté par une nomenclature utile. Les anciens
# catalogues regroupés sont éclatés ; leurs IDs restent des clés de référentiel.
CATALOG_NOMENCLATURES: Mapping[str, tuple[str, ...]] = {
    "types_ouvrage_particulier": ("ouvrage_particulier",),
    "fonctions_structure": ("fonction",),
    "natures_structure": ("nature",),
    "materiaux_structure": ("materiau",),
    "largeurs_franc_bord": ("largeur_franc_bord",),
    "natures_sol_berge": ("nature_sol_berge",),
    "types_evenement_hydraulique": ("evenement_hydraulique",),
    "frequences_evenement_hydraulique": ("frequence_evenement_hydraulique",),
    "orientations_vent": ("orientation_vent",),
    "references_hauteur": ("reference_hauteur",),
    "origines_profil_long": ("origine_profil_long",),
    "origines_profil_travers": ("origine_profil_travers",),
    "positions_profil_long_digue": ("position_profil_long_sur_digue",),
    "systemes_releve": ("systeme_releve_profil",),
    "types_profil_travers": ("type_profil_travers",),
    "types_fermeture": ("fermeture_plage_depot_lit",),
    "types_ouverture": ("ouverture_plage_depot_lit",),
    "types_ouvrage_fermeture": ("element_associe_fermeture_plage_depot_lit",),
    "types_ouvrage_depot": ("element_associe_plage_depot_lit",),
    "fonctions_seuil_lit": ("fonction_seuil_lit",),
    "positions_seuil_lit": ("position_axe_seuil_lit",),
    "geometries_crete_seuil_lit": ("geometrie_crete_seuil_lit",),
    "profils_coursier_seuil_lit": ("profil_coursier_seuil_lit",),
    "types_inspection_seuil_lit": ("type_inspection_seuil_lit",),
    "positions_structure_seuil_lit": ("position_structure_seuil_lit",),
    "types_traitement_vegetation": ("traitement_vegetation",),
    "sous_types_traitement_vegetation": ("sous_traitement_vegetation",),
    "frequences_traitement_vegetation": ("frequence_traitement_vegetation",),
    "types_zone_vegetation": ("type_arbre_vegetation", "type_peuplement_vegetation", "type_invasive_vegetation"),
    "materiaux_cheminement": ("materiau",),
    "revetements_cheminement": ("revetement",),
    "usages_cheminement": ("usage_voie",),
    "types_voie": ("voie_digue",),
    "orientations_ouvrage": ("orientation_ouvrage",),
    "types_organe_protection_collective": ("type_organe_protection_collective",),
    "fonctionnements_amenagement_hydraulique": ("fonctionnement_a_h",),
    "etats_organe_protection_collective": ("etat",),
    "fonctions_deversoir": ("fonction_deversoir",),
    "raccordements_digue": ("raccordement_digue",),
    "moyens_manipulation_batardeaux": ("moyen_manip_batardeaux",),
    "natures_batardeaux": ("nature_batardeaux",),
    "types_seuil_batardable": ("seuil",),
    "types_glissiere": ("type_glissiere",),
    "types_barrage_lit": ("type_barrage_lit",),
    "types_ouvrage_freineur_lit": ("type_ouvrage_freineur_lit",),
    "domanialites_lit": ("domanialite_lit",),
    "regimes_ecoulement_lit": ("regime_lit",),
    "occupations_riveraines_lit": ("occupation_lit",),
    "types_zone_atterrissement_lit": ("zone_atterrissement_lit",),
}

STRUCTURE_TYPES = (
    ("CRETE", "Crête"), ("FONDATION", "Fondation"),
    ("PIED_DIGUE", "Pied de digue"), ("TALUS", "Talus"),
    ("TALUS_RISBERME", "Talus de risberme"),
    ("RISBERME", "Risberme"),
    ("FRANC_BORD", "Franc-bord"),
)
TRONCON_TYPES = (
    # Nomenclature commune de public.troncons : aucun type n'exige une extension
    # ou un système de repérage ; les codes historiques restent inchangés.
    ("BERGE", "Berge"), ("TALUS", "Talus"),
    ("TALUS_RISBERME", "Talus de risberme"), ("RISBERME", "Risberme"),
    ("CRETE", "Crête"), ("FRANC_BORD", "Franc-bord"),
    ("INDETERMINE", "Indéterminé"),
)
TRONCON_SIDES = (
    ("TERRE", "Terre"), ("EAU", "Rivière–Mer–Étang"),
    ("INDETERMINE", "Indéterminé"),
)
VEGETATION_TYPES = {
    "familles_vegetation": (
        ("HERBACEES", "Herbacées"), ("BUISSONS_ARBUSTES", "Buissons et arbustes"),
        ("ARBRES", "Arbres"),
    ),
    "dispositions_vegetation": (
        ("ISOLE", "Isolé"), ("LIGNE", "Ligne"), ("SURFACE", "Surface"),
    ),
    "sous_types_vegetation": (
        ("ARBRE_SUR_PIED", "Arbre sur pied"), ("SOUCHE", "Souche"),
    ),
}


def _columns(table: str) -> tuple[str, ...]:
    definition = TABLE_DEFINITIONS[table]
    return tuple(re.findall(r"(?m)^    ([a-z_][a-z_0-9]*)\s+(?:text|boolean|date|integer|uuid)", definition))


def canonical_reference_rows(
    contracts: ReferenceContracts | None = None,
) -> dict[str, tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]]:
    """Projette C1 vers les seules colonnes persistantes des catalogues cibles."""
    selected = contracts or load_contracts()
    old = historical_reference_specs(selected)
    result: dict[str, tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]] = {}
    for table in REFERENCE_TABLES:
        target_columns = _columns(table)
        if table in old:
            spec = old[table]
            columns = tuple(col for col in spec.columns if col in target_columns)
            data = [dict(zip(spec.columns, row, strict=True)) for row in spec.rows]
            if table == "referentiel.types_ouvrage_voirie":
                data.extend({
                    "id": row["id"], "designation": row.get("designation"),
                    "abrege": row.get("abrege"), "libelle": row.get("libelle"),
                    "valid": row.get("valid"),
                } for row in selected.values("ouvrage_voirie_dependance"))
            if table == "referentiel.types_ouvrage_hydraulique":
                data.extend((
                    {"id": "PRISE_D_EAU", "code": "prise_d_eau", "abrege": "PDE", "libelle": "Prise d'eau", "valid": True},
                    {"id": "OUVRAGE_HYDRAULIQUE", "code": "ouvrage_hydraulique", "abrege": "OHY", "libelle": "Ouvrage hydraulique", "valid": True},
                ))
            if table == "referentiel.problematiques_vegetation":
                data = [row for row in data if row["id"] in {"DAN", "GEN"}]
            rows = tuple(tuple(value.get(col) for col in columns) for value in data)
        else:
            columns = tuple(col for col in ("id", "code", "abrege", "libelle", "designation", "valid", "date_maj") if col in target_columns)
            name = table.split(".", 1)[1]
            native = {"types_structure_digue": STRUCTURE_TYPES,
                      "types_troncon_unifie": TRONCON_TYPES,
                      "cotes_troncon_unifie": TRONCON_SIDES, **VEGETATION_TYPES}
            if name in native:
                values = native[name]
                data = [{"id": code, "code": code, "libelle": label, "valid": True} for code, label in values]
            else:
                nomenclatures = CATALOG_NOMENCLATURES.get(name)
                if not nomenclatures:
                    raise ReferenceContractError(f"{table}: source canonique inconnue")
                data = [row for nomenclature in nomenclatures for row in selected.values(nomenclature)]
            rows = tuple(tuple({
                "id": row["id"], "code": row.get("code") or row.get("abrege"),
                "abrege": row.get("abrege"),
                "libelle": row.get("libelle"), "designation": row.get("designation"),
                "valid": row.get("valid", True),
                "date_maj": (date.fromisoformat(row["dateMaj"])
                             if "date_maj" in columns and row.get("dateMaj") is not None else None),
            }.get(col) for col in columns) for row in data)
            # Des catalogues V2 distincts peuvent partager un abrégé ; le code
            # n'est alors pas une clé métier unique de ce catalogue fusionné.
            code_index = columns.index("code") if "code" in columns else None
            if code_index is not None and len({row[code_index] for row in rows if row[code_index] is not None}) != len([row for row in rows if row[code_index] is not None]):
                rows = tuple(tuple(None if i == code_index else value for i, value in enumerate(row)) for row in rows)
        id_index = columns.index("id")
        if table == "referentiel.types_ouvrage_particulier":
            rows = tuple(row for row in rows if row[id_index] not in PARTICULIER_TO_MEASUREMENT)
        ids = [row[id_index] for row in rows]
        if len(ids) != len(set(ids)):
            raise ReferenceContractError(f"{table}: identifiants métier dupliqués")
        result[table] = columns, rows
    return result


def initialize_canonical_references(cursor: Any, *, contracts: ReferenceContracts | None = None) -> dict[str, int]:
    """Insère les valeurs absentes ; refuse les divergences de catalogues gérés."""
    result = {}
    for table, (columns, rows) in canonical_reference_rows(contracts).items():
        if not rows:
            result[table] = 0
            continue
        placeholders = ", ".join("%s" for _ in columns)
        before = cursor.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        present_ids={r[0] for r in cursor.execute(f'SELECT id FROM {table}').fetchall()}
        missing=tuple(r for r in rows if r[columns.index('id')] not in present_ids)
        if missing:cursor.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
            missing,
        )
        actual = cursor.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE id = ANY(%s::text[])",
                                ([row[columns.index('id')] for row in rows],)).fetchall()
        normalized = {tuple(row) for row in actual}
        if normalized != set(rows):
            raise ReferenceContractError(f"{table}: conflit avec le catalogue canonique")
        result[table] = cursor.execute(f"SELECT count(*) FROM {table}").fetchone()[0] - before
    return result


def validate_canonical_references(cursor: Any, *, contracts: ReferenceContracts | None = None) -> None:
    """Vérifie les valeurs complètes des catalogues métier d'une base neuve."""
    for table, (columns, rows) in canonical_reference_rows(contracts).items():
        actual = cursor.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
        if set(actual) != set(rows) or len(actual) != len(rows):
            raise ReferenceContractError(f"{table}: catalogue canonique divergent")
