"""Les huit référentiels végétation de C1 et leur initialisation conservative.

Les contrats sont chargés à la première utilisation. Aucun import de migration,
aucune définition de valeur parallèle et aucun accès aux sources historiques.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import Any

from . import ReferenceContractError, load_contracts


REFERENCE_TABLES = (
    "referentiel.natures_vegetation",
    "referentiel.problematiques_vegetation",
    "referentiel.etats_sanitaires_vegetation",
    "referentiel.classes_hauteur_vegetation",
    "referentiel.classes_diametre_vegetation",
    "referentiel.types_peuplement_vegetation",
    "referentiel.types_invasive_vegetation",
    "referentiel.especes_vegetation",
)
COMMON_COLUMNS = ("id", "code", "abrege", "libelle", "valid")
NATURE_COLUMNS = (
    *COMMON_COLUMNS, "native_selectable", "compatible_point",
    "compatible_linestring", "compatible_polygon",
)


@dataclass(frozen=True)
class VegetationReferenceRow:
    id: str
    code: str
    abrege: str
    libelle: str
    valid: bool = True


@cache
def reference_records() -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    """Instantané immuable des valeurs et drapeaux de C1."""
    contracts = load_contracts()
    return MappingProxyType({
        table: tuple(MappingProxyType(row) for row in contracts.canonical_values(table))
        for table in REFERENCE_TABLES
    })


@cache
def reference_rows() -> Mapping[str, tuple[VegetationReferenceRow, ...]]:
    """Projection commune utilisée par les préparateurs et la démonstration."""
    return MappingProxyType({
        table: tuple(VegetationReferenceRow(**{key: row[key] for key in COMMON_COLUMNS})
                     for row in records)
        for table, records in reference_records().items()
    })


@cache
def native_geometry_compatibility() -> Mapping[str, frozenset[str]]:
    """Compatibilités des natures natives, périmètre actuel du diagnostic d'import.

    IND et INV restent exclus de ce diagnostic. Leurs drapeaux complets sont
    conservés dans reference_records() et écrits lors de l'initialisation.
    """
    return MappingProxyType({
        row["id"]: frozenset(kind for kind in ("point", "linestring", "polygon")
                             if row["compatible_" + kind])
        for row in reference_records()["referentiel.natures_vegetation"]
        if row["native_selectable"]
    })


def initialize_vegetation_references(
    cursor: Any, *, tables: Sequence[str] = REFERENCE_TABLES,
) -> dict[str, tuple[int, int, int, int]]:
    """Insère les valeurs absentes ; préserve les champs personnalisés existants.

    Retourne par table (créées, mises à jour, identiques, personnalisées).
    Un conflit de drapeaux structurels est une erreur explicite. Le curseur doit
    appartenir à la transaction de l'appelant, qui assure son rollback en erreur.
    """
    if len(set(tables)) != len(tables) or set(tables) - set(REFERENCE_TABLES):
        raise ReferenceContractError("Tables végétation inconnues ou dupliquées")
    result = {}
    for table in tables:
        columns = NATURE_COLUMNS if table == "referentiel.natures_vegetation" else COMMON_COLUMNS
        standards = {
            row["id"]: tuple(row[key] for key in columns)
            for row in reference_records()[table]
        }
        selected_ids = list(standards)
        if table == "referentiel.problematiques_vegetation":
            # Ancienne valeur déjà désactivée par le seed V7, jamais recréée.
            selected_ids.append("SOU")

        def read_existing() -> dict[str, tuple]:
            cursor.execute(
                f"SELECT {', '.join(columns)} FROM {table} "
                "WHERE id = ANY(%s::text[]) FOR UPDATE",
                (selected_ids,),
            )
            rows = {row[0]: tuple(row) for row in cursor.fetchall()}
            if table == "referentiel.natures_vegetation":
                for identifier in rows.keys() & standards.keys():
                    if rows[identifier][len(COMMON_COLUMNS):] != standards[identifier][len(COMMON_COLUMNS):]:
                        raise ReferenceContractError(
                            f"{table}.{identifier}: compatibilités géométriques ou sélection native "
                            "différentes de C1 ; ligne existante préservée"
                        )
            if table == "referentiel.problematiques_vegetation" and "SOU" in rows and rows["SOU"][-1]:
                raise ReferenceContractError(
                    f"{table}.SOU: ancienne problématique encore active ; "
                    "désactivation à décider explicitement, ligne préservée"
                )
            return rows

        existing = read_existing()
        missing = [row for identifier, row in standards.items() if identifier not in existing]
        created = 0
        if missing:
            cursor.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('%s' for _ in columns)}) ON CONFLICT (id) DO NOTHING",
                missing,
            )
            # psycopg compte les insertions effectives, y compris en cas de conflit.
            created = cursor.rowcount
            existing = read_existing()
        if standards.keys() - existing.keys():
            raise ReferenceContractError(f"{table}: initialisation incomplète")
        unchanged = sum(existing[key] == row for key, row in standards.items()) - created
        result[table] = (created, 0, unchanged, len(standards) - created - unchanged)
    return result


CANONICAL_VEGETATION_TABLES = (
    "referentiel.familles_vegetation", "referentiel.dispositions_vegetation",
    "referentiel.sous_types_vegetation", "referentiel.problematiques_vegetation",
    "referentiel.etats_sanitaires_vegetation", "referentiel.classes_hauteur_vegetation",
    "referentiel.classes_diametre_vegetation", "referentiel.especes_vegetation",
    "referentiel.types_traitement_vegetation", "referentiel.sous_types_traitement_vegetation",
    "referentiel.frequences_traitement_vegetation", "referentiel.types_zone_vegetation",
)


def initialize_canonical_vegetation_references(cursor: Any) -> None:
    """Reuse the canonical catalog projection; never resurrect historical tables.

    No local CouchDB value extends a validated catalog. Existing custom metadata
    is preserved, while missing approved IDs are inserted from the same C1/C2
    projection used to initialize a fresh target.
    """
    from .canonical import canonical_reference_rows
    catalogs = canonical_reference_rows()
    for table in CANONICAL_VEGETATION_TABLES:
        columns, rows = catalogs[table]
        if not rows:
            continue
        cursor.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES "
                           f"({','.join('%s' for _ in columns)}) ON CONFLICT (id) DO NOTHING", rows)
        cursor.execute(f"SELECT id FROM {table} WHERE id=ANY(%s::text[])",
                       ([row[columns.index('id')] for row in rows],))
        if {row[0] for row in cursor.fetchall()} != {row[columns.index('id')] for row in rows}:
            raise ReferenceContractError(f"{table}: canonical initialization incomplete")
