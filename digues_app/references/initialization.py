"""Projection PostgreSQL autonome des 36 référentiels déclarés par C1/C2.

Ce module ne dépend ni du paquet de migration ni des catalogues historiques.
Les écritures sont conservatives : les lignes absentes sont créées, les
personnalisations existantes sont préservées et les écarts structurels sont
refusés. Le curseur appartient à la transaction de l'appelant.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from . import ReferenceContractError, ReferenceContracts, load_contracts
from .relational import RELATIONAL_REFERENCE_TABLES, CATALOG_METADATA_TABLES, source_metadata
from .vegetation import (
    REFERENCE_TABLES as VEGETATION_TABLES,
    initialize_vegetation_references,
)


REFERENCE_TABLES = (
    "referentiel.categories_desordre",
    "referentiel.types_desordre",
    "referentiel.urgences",
    "referentiel.types_amenagement_hydraulique",
    "referentiel.types_ouvrage_hydraulique",
    "referentiel.types_equipement_mesure",
    "referentiel.types_cheminement",
    "referentiel.types_mobilier",
    "referentiel.types_reseau_technique",
    *VEGETATION_TABLES,
    "referentiel.cotes",
    "referentiel.positions",
    "referentiel.sources_information",
    "referentiel.suites_apporter",
    "referentiel.orientations_photo",
    "referentiel.rives",
    "referentiel.types_troncon",
    "referentiel.typologies_troncon",
    "referentiel.etats_ouvrage_accessoire_gc",
    "referentiel.manoeuvres_ouvrage",
    "referentiel.securites",
    "referentiel.types_ouvrage_telecom_energie",
    "referentiel.types_ouvrage_voirie",
    "referentiel.types_reseau_hydraulique",
    "referentiel.ecoulements",
    "referentiel.implantations",
    "referentiel.utilisations_conduite",
    "referentiel.types_rapport_etude",
    "referentiel.types_prestation",
)

PRESTATION_TABLE = "referentiel.types_prestation"
PRESTATION_TRACKED_FIELDS = frozenset({
    "_id", "@class", "_rev", "libelle", "designation", "valid",
    "dateMaj", "author", "lastUpdateAuthor",
})


@dataclass(frozen=True)
class ReferenceTableSpec:
    table: str
    columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    structural_columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    def dictionaries(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(zip(self.columns, row, strict=True)) for row in self.rows)


def _date(value: Any, context: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ReferenceContractError(f"{context}: date textuelle attendue")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ReferenceContractError(f"{context}: date ISO invalide : {value!r}") from exc


def _projection_by_table(c2: Mapping[str, Any]) -> dict[str, list[tuple[str, Mapping[str, Any]]]]:
    result: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for source_class, source in c2["classes"].items():
        for projection in source["projections"]:
            table = projection["table"]
            if table.startswith("referentiel."):
                result.setdefault(table, []).append((source_class, projection))
    return result


def _prestation_row(value: Mapping[str, Any]) -> dict[str, Any]:
    document = {"_id": value["id"], "@class": "fr.sirs.core.model.RefPrestation", **value}
    document.pop("id")
    present = tuple(sorted(field for field in PRESTATION_TRACKED_FIELDS if field in document))
    nulls = tuple(sorted(field for field in present if document[field] is None))
    date_text = document.get("dateMaj")
    return {
        "id": value["id"],
        "classe_source": "RefPrestation",
        "libelle": value.get("libelle"),
        "designation": value.get("designation"),
        "valid": value.get("valid"),
        "metadata_consistent": None,
        "standard_present": True,
        "standard_libelle": value.get("libelle"),
        "standard_designation": value.get("designation"),
        "standard_valid": value.get("valid"),
        "standard_date_maj": _date(date_text, f"{value['id']}.dateMaj"),
        "standard_date_maj_source_text": date_text,
        "standard_author": value.get("author"),
        "standard_last_update_author": value.get("lastUpdateAuthor"),
        "standard_present_fields": present,
        "standard_null_fields": nulls,
        "local_present": False,
        "local_libelle": None,
        "local_designation": None,
        "local_valid": None,
        "local_date_maj": None,
        "local_date_maj_source_text": None,
        "local_author": None,
        "local_last_update_author": None,
        "local_present_fields": (),
        "local_null_fields": (),
    }


def reference_table_specs(
    contracts: ReferenceContracts | None = None,
) -> Mapping[str, ReferenceTableSpec]:
    """Construit et valide la projection SQL complète de C1/C2."""
    selected = contracts or load_contracts()
    c1, c2 = selected.c1, selected.c2
    declared = {
        table: definition
        for table, definition in c2["tables"].items()
        if table.startswith("referentiel.")
    }
    if set(declared) != set(REFERENCE_TABLES) or len(REFERENCE_TABLES) != 36:
        raise ReferenceContractError("C1/C2 : les 36 tables referentiel ne sont pas couvertes")
    projections = _projection_by_table(c2)
    result: dict[str, ReferenceTableSpec] = {}
    for table in REFERENCE_TABLES:
        definition = declared[table]
        columns = tuple(definition["columns"])
        keys = tuple(definition["key_columns"])
        rows: list[dict[str, Any]] = []
        nomenclatures = tuple(definition["nomenclatures"])
        if all(c1["nomenclatures"][name]["kind"] == "canonical" for name in nomenclatures):
            if len(nomenclatures) != 1:
                raise ReferenceContractError(f"{table}: projection canonique ambiguë")
            rows = selected.canonical_values(table)
        elif table == PRESTATION_TABLE:
            rows = [_prestation_row(value) for value in selected.values(nomenclatures[0])]
        else:
            projected = projections.get(table, [])
            if len(projected) != len(nomenclatures):
                raise ReferenceContractError(f"{table}: projection historique incomplète")
            for source_class, projection in projected:
                source = c2["classes"][source_class]
                name = source["nomenclature"]
                if name not in nomenclatures:
                    raise ReferenceContractError(f"{table}: nomenclature étrangère {name}")
                for value in selected.values(name):
                    row = {
                        target: value.get(origin)
                        for target, origin in projection["fields"].items()
                    }
                    row.update(projection["constants"])
                    if "date_maj" in columns:
                        row["date_maj"] = _date(row.get("date_maj"), f"{value['id']}.dateMaj")
                    if table in RELATIONAL_REFERENCE_TABLES | CATALOG_METADATA_TABLES:
                        row.update(source_metadata(value, standard_catalog=True, additional_fields=("categorieId",)))
                    if "classe_source" in columns:
                        row["classe_source"] = source_class
                    if "standard_catalog" in columns:
                        row["standard_catalog"] = True
                    rows.append(row)
        if table == "referentiel.types_reseau_hydraulique":
            # Valeur native cible ; aucune équivalence avec un type V2 n'est inférée.
            rows.append({
                "nature": "OUVERT", "id": "FOSSE", "abrege": "FOS",
                "libelle": "Fossé", "valid": True, "standard_catalog": False,
            })
        tuples = tuple(tuple(row.get(column) for column in columns) for row in rows)
        key_indexes = tuple(columns.index(column) for column in keys)
        row_keys = [tuple(row[index] for index in key_indexes) for row in tuples]
        if len(row_keys) != len(set(row_keys)):
            raise ReferenceContractError(f"{table}: collision de clé dans la projection C1")
        structural = tuple(column for column in ("categorie_id", "nature", "classe_source")
                           if column in columns and column not in keys)
        result[table] = ReferenceTableSpec(
            table=table,
            columns=columns,
            key_columns=keys,
            structural_columns=structural,
            rows=tuples,
        )
    if sum(len(spec.rows) for spec in result.values()) != 409:
        raise ReferenceContractError("C1/C2 et valeur Fossé : 409 lignes referentiel attendues")
    return result


def _normalized(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _sql_value(column: str, value: Any) -> Any:
    if column.endswith(("_present_fields", "_null_fields")) and isinstance(value, tuple):
        return list(value)
    return value


def _read_existing(cursor: Any, spec: ReferenceTableSpec) -> dict[tuple[Any, ...], tuple[Any, ...]]:
    ids = [row[spec.columns.index("id")] for row in spec.rows]
    cursor.execute(
        f"SELECT {', '.join(spec.columns)} FROM {spec.table} "
        "WHERE id = ANY(%s::text[]) FOR UPDATE",
        (ids,),
    )
    key_indexes = tuple(spec.columns.index(column) for column in spec.key_columns)
    return {
        tuple(row[index] for index in key_indexes): tuple(_normalized(value) for value in row)
        for row in cursor.fetchall()
    }


def _merge_prestation_standard(
    expected: Mapping[str, Any], existing: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(expected)
    local_columns = tuple(column for column in expected if column.startswith("local_"))
    for column in local_columns:
        merged[column] = _normalized(existing[column])
    if not existing["local_present"]:
        return merged
    comparisons = (
        ("standard_libelle", "local_libelle"),
        ("standard_designation", "local_designation"),
        ("standard_valid", "local_valid"),
        ("standard_date_maj_source_text", "local_date_maj_source_text"),
        ("standard_author", "local_author"),
        ("standard_last_update_author", "local_last_update_author"),
    )
    merged["metadata_consistent"] = all(expected[left] == existing[right]
                                         for left, right in comparisons)
    for common, standard, local in (
        ("libelle", "standard_libelle", "local_libelle"),
        ("designation", "standard_designation", "local_designation"),
        ("valid", "standard_valid", "local_valid"),
    ):
        merged[common] = expected[standard] if expected[standard] == existing[local] else None
    return merged


def _initialize_prestations(cursor: Any, spec: ReferenceTableSpec) -> tuple[int, int, int, int]:
    expected_by_id = {row[0]: data for row, data in zip(spec.rows, spec.dictionaries(), strict=True)}
    existing_rows = _read_existing(cursor, spec)
    existing_by_id = {
        key[0]: dict(zip(spec.columns, row, strict=True))
        for key, row in existing_rows.items()
    }
    inserts: list[tuple[Any, ...]] = []
    updates: list[tuple[Any, ...]] = []
    desired: dict[str, tuple[Any, ...]] = {}
    unchanged = preserved = 0
    for identifier, expected in expected_by_id.items():
        existing = existing_by_id.get(identifier)
        if existing is None:
            expected_tuple = tuple(_normalized(expected[column]) for column in spec.columns)
            inserts.append(expected_tuple)
            desired[identifier] = expected_tuple
            continue
        if existing["classe_source"] != "RefPrestation":
            raise ReferenceContractError(f"{spec.table}.{identifier}: classe source incompatible")
        merged = _merge_prestation_standard(expected, existing)
        merged_tuple = tuple(_normalized(merged[column]) for column in spec.columns)
        desired[identifier] = merged_tuple
        existing_tuple = tuple(_normalized(existing[column]) for column in spec.columns)
        if existing["standard_present"]:
            if existing_tuple != merged_tuple:
                raise ReferenceContractError(
                    f"{spec.table}.{identifier}: branche standard ou projection commune différente de C1"
                )
            unchanged += 1
        else:
            updates.append(merged_tuple)
            preserved += int(bool(existing["local_present"]))
    if inserts:
        cursor.executemany(
            f"INSERT INTO {spec.table} ({', '.join(spec.columns)}) VALUES "
            f"({', '.join('%s' for _ in spec.columns)}) ON CONFLICT (id) DO NOTHING",
            [tuple(_sql_value(column, value) for column, value in zip(spec.columns, row, strict=True))
             for row in inserts],
        )
    if updates:
        assignments = ", ".join(f"{column} = %s" for column in spec.columns[1:])
        cursor.executemany(
            f"UPDATE {spec.table} SET {assignments} WHERE id = %s",
            [
                tuple(_sql_value(column, value)
                      for column, value in zip(spec.columns[1:], row[1:], strict=True))
                + (row[0],)
                for row in updates
            ],
        )
    final = _read_existing(cursor, spec)
    if len(final) != len(spec.rows):
        raise ReferenceContractError(f"{spec.table}: initialisation incomplète")
    for identifier, row in desired.items():
        if final.get((identifier,)) != row:
            raise ReferenceContractError(
                f"{spec.table}.{identifier}: conflit concurrent ou projection incohérente"
            )
    return len(inserts), len(updates), unchanged, preserved


def _initialize_regular(cursor: Any, spec: ReferenceTableSpec) -> tuple[int, int, int, int]:
    key_indexes = tuple(spec.columns.index(column) for column in spec.key_columns)
    structural_indexes = tuple(spec.columns.index(column) for column in spec.structural_columns)
    expected = {
        tuple(row[index] for index in key_indexes): tuple(_normalized(value) for value in row)
        for row in spec.rows
    }
    existing = _read_existing(cursor, spec)

    def structural_conflict(actual: tuple[Any, ...], standard: tuple[Any, ...]) -> bool:
        for index in structural_indexes:
            if actual[index] == standard[index]:
                continue
            # Un document local peut omettre sa catégorie (Ecore 0..1), même
            # sous un ID standard. Préserver ce choix attesté sans le compléter.
            # Les catégories différentes renseignées et les classes/natures
            # restent soumises aux gardes structurelles existantes.
            if (
                spec.table == "referentiel.types_desordre"
                and spec.columns[index] == "categorie_id"
                and actual[index] is None
                and actual[spec.columns.index("standard_catalog")] is False
                and actual[spec.columns.index("source_present_fields")] is not None
            ):
                continue
            return True
        return False

    for key in expected.keys() & existing.keys():
        if structural_conflict(existing[key], expected[key]):
            raise ReferenceContractError(f"{spec.table}.{key}: relation ou classe source différente de C1")
    missing = [row for key, row in expected.items() if key not in existing]
    if missing:
        conflict = ", ".join(spec.key_columns)
        cursor.executemany(
            f"INSERT INTO {spec.table} ({', '.join(spec.columns)}) VALUES "
            f"({', '.join('%s' for _ in spec.columns)}) "
            f"ON CONFLICT ({conflict}) DO NOTHING",
            [tuple(_sql_value(column, value) for column, value in zip(spec.columns, row, strict=True))
             for row in missing],
        )
    final = _read_existing(cursor, spec)
    if expected.keys() - final.keys():
        raise ReferenceContractError(f"{spec.table}: initialisation incomplète")
    for key in expected:
        if structural_conflict(final[key], expected[key]):
            raise ReferenceContractError(f"{spec.table}.{key}: conflit concurrent structurel")
    unchanged = sum(final[key] == row for key, row in expected.items()) - len(missing)
    preserved = len(expected) - len(missing) - unchanged
    return len(missing), 0, unchanged, preserved


def initialize_reference_tables(
    cursor: Any,
    *,
    contracts: ReferenceContracts | None = None,
    tables: Sequence[str] = REFERENCE_TABLES,
) -> dict[str, tuple[int, int, int, int]]:
    """Initialise les tables demandées depuis C1 dans la transaction courante."""
    if len(set(tables)) != len(tables) or set(tables) - set(REFERENCE_TABLES):
        raise ReferenceContractError("Tables de référentiel inconnues ou dupliquées")
    specs = reference_table_specs(contracts)
    result: dict[str, tuple[int, int, int, int]] = {}
    vegetation = [table for table in tables if table in VEGETATION_TABLES]
    for table in tables:
        if table in VEGETATION_TABLES:
            continue
        spec = specs[table]
        result[table] = (
            _initialize_prestations(cursor, spec)
            if table == PRESTATION_TABLE
            else _initialize_regular(cursor, spec)
        )
    if vegetation:
        result.update(initialize_vegetation_references(cursor, tables=vegetation))
    return {table: result[table] for table in tables}


def validate_reference_tables(
    cursor: Any,
    *,
    contracts: ReferenceContracts | None = None,
    exact: bool = True,
) -> None:
    """Vérifie la présence et, pour une base neuve, toutes les propriétés C1."""
    for spec in reference_table_specs(contracts).values():
        expected = {
            tuple(row[spec.columns.index(key)] for key in spec.key_columns):
            tuple(_normalized(value) for value in row)
            for row in spec.rows
        }
        actual = _read_existing(cursor, spec)
        if expected.keys() - actual.keys():
            raise ReferenceContractError(f"{spec.table}: valeurs C1 manquantes")
        if exact:
            different = [key for key, row in expected.items() if actual[key] != row]
            if different:
                raise ReferenceContractError(
                    f"{spec.table}: propriétés C1 différentes pour {different[0]}"
                )


__all__ = [
    "REFERENCE_TABLES", "PRESTATION_TABLE", "ReferenceTableSpec",
    "initialize_reference_tables", "reference_table_specs",
    "validate_reference_tables",
]
