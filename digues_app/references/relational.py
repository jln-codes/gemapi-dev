"""Métadonnées sources des catalogues historiques, sans collecte ni SQL."""

from collections.abc import Mapping
from datetime import date
from typing import Any

from . import ReferenceContractError


RELATIONAL_REFERENCE_TABLES = frozenset({
    "referentiel.cotes", "referentiel.positions", "referentiel.sources_information",
    "referentiel.orientations_photo", "referentiel.rives",
    "referentiel.types_troncon", "referentiel.typologies_troncon",
})
SOURCE_FIELDS = frozenset({
    "libelle", "designation", "abrege", "dateMaj", "valid",
    "author", "lastUpdateAuthor",
})
CONSERVATION_COLUMNS = (
    "author_source_id", "last_update_author_source_id", "date_maj_source_text",
    "source_present_fields", "standard_catalog",
)

# Périmètre de conservation, les nomenclatures et valeurs restent définies par C1/C2.
CATALOG_METADATA_TABLES = frozenset({
    "referentiel.categories_desordre", "referentiel.types_desordre", "referentiel.urgences",
    "referentiel.suites_apporter", "referentiel.etats_ouvrage_accessoire_gc",
    "referentiel.manoeuvres_ouvrage", "referentiel.types_ouvrage_telecom_energie",
    "referentiel.types_ouvrage_voirie", "referentiel.securites",
    "referentiel.types_reseau_hydraulique", "referentiel.ecoulements",
    "referentiel.implantations", "referentiel.utilisations_conduite",
    "referentiel.types_rapport_etude",
    "source_v2.catalogue_destination_dependance", "source_v2.catalogue_types_ouvrage_voirie_dependance",
    "source_v2.catalogue_fonctions_structure", "source_v2.catalogue_natures_structure",
    "source_v2.catalogue_materiaux_structure", "source_v2.catalogue_largeurs_franc_bord",
    "source_v2.catalogue_regimes_lit", "source_v2.catalogue_natures_sol_berge",
    "source_v2.catalogue_profils", "source_v2.catalogue_evenements_hydrauliques",
    "source_v2.catalogue_seuil_lit", "source_v2.catalogue_regulation_lit",
})


def source_metadata(
    document: Mapping[str, Any], *, standard_catalog: bool,
    columns: tuple[str, ...] | None = None, additional_fields: tuple[str, ...] = (),
    date_prefix: bool = False,
) -> dict[str, Any]:
    """Capturer les clés et valeurs exactes, indépendamment des marqueurs source.

    Ne complète aucun champ absent et ne décide ni de l'admissibilité métier,
    ni de la fusion locale. La date typée garde son texte source, même vide.
    I/J/M projettent déjà les dix premiers caractères : conserver cette
    convention uniquement pour leurs catalogues, sans tronquer le texte source.
    """
    for field in ("author", "lastUpdateAuthor", "dateMaj", "designation", "abrege"):
        value = document.get(field)
        if value is not None and not isinstance(value, str):
            raise ReferenceContractError(f"{field}: texte source attendu")
    date_text = document.get("dateMaj")
    try:
        date_value = None if date_text in (None, "") else date.fromisoformat(
            date_text[:10] if date_prefix else date_text
        )
    except ValueError as exc:
        raise ReferenceContractError(f"dateMaj: date ISO invalide : {date_text!r}") from exc
    metadata = {
        "date_maj": date_value,
        "author_source_id": document.get("author"),
        "last_update_author_source_id": document.get("lastUpdateAuthor"),
        "date_maj_source_text": date_text,
        "source_present_fields": tuple(sorted((SOURCE_FIELDS | set(additional_fields)).intersection(document))),
        "standard_catalog": standard_catalog,
        "designation": document.get("designation"),
        "designation_source": document.get("designation"),
        "abrege_source": document.get("abrege"),
    }
    return metadata if columns is None else {column: metadata[column] for column in columns}
