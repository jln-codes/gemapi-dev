"""Initialisation des cinq ensembles végétation présents dès la création du schéma."""

from typing import Any

from digues_app.references.vegetation import initialize_vegetation_references


SCHEMA_REFERENCE_TABLES = (
    "referentiel.natures_vegetation",
    "referentiel.problematiques_vegetation",
    "referentiel.types_peuplement_vegetation",
    "referentiel.types_invasive_vegetation",
    "referentiel.especes_vegetation",
)


def seed_current_vegetation_taxonomy(cursor: Any) -> None:
    """Insère les valeurs C1 absentes et refuse les conflits structurels.

    Les libellés, codes, abréviations et validités personnalisés sont préservés.
    La transaction de création du schéma reste portée par l'appelant.
    """
    initialize_vegetation_references(cursor, tables=SCHEMA_REFERENCE_TABLES)
