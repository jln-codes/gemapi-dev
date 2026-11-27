"""Génération des fiches et rapports PDF de digues-app.

Le modèle de données (``disorder_sheet``) et l'adaptation PostgreSQL
(``repository``) sont indépendants de tout moteur de rendu. Le renderer
(``pdf``) ne dépend que du modèle. Les dépendances lourdes (ReportLab,
matplotlib) ne sont chargées qu'à l'usage, afin de ne pas alourdir l'import du
paquet.
"""

from __future__ import annotations

from .disorder_sheet import (
    DisorderMapContext,
    DisorderObservation,
    DisorderPhoto,
    DisorderSheetData,
    build_disorder_sheet,
)

_LAZY = {
    "render_disorder_sheet_pdf": "pdf",
    "render_map_image": "map",
    "load_disorder_sheet": "repository",
    "DisorderSheetNotFound": "repository",
    "resolve_observer_name": "repository",
    "load_scope_node": "repository",
    "load_scope_disorder_rows": "repository",
    "CatalogDigue": "catalog",
    "CatalogEntry": "catalog",
    "CatalogScope": "catalog",
    "CatalogScopeNotFound": "catalog",
    "CatalogSysteme": "catalog",
    "CatalogTroncon": "catalog",
    "DisorderCatalog": "catalog",
    "DIGUE_SCOPE": "catalog",
    "NO_SYSTEM_LABEL": "catalog",
    "SYSTEME_SCOPE": "catalog",
    "TRONCON_SCOPE": "catalog",
    "load_patrimoine_catalog": "catalog",
    "merge_pdf_documents": "catalog",
    "render_catalog_pdf": "catalog",
}

__all__ = [
    "CatalogDigue",
    "CatalogEntry",
    "CatalogScope",
    "CatalogScopeNotFound",
    "CatalogSysteme",
    "CatalogTroncon",
    "DIGUE_SCOPE",
    "DisorderCatalog",
    "DisorderMapContext",
    "DisorderObservation",
    "DisorderPhoto",
    "DisorderSheetData",
    "DisorderSheetNotFound",
    "NO_SYSTEM_LABEL",
    "SYSTEME_SCOPE",
    "TRONCON_SCOPE",
    "build_disorder_sheet",
    "load_disorder_sheet",
    "load_patrimoine_catalog",
    "load_scope_disorder_rows",
    "load_scope_node",
    "merge_pdf_documents",
    "render_catalog_pdf",
    "render_disorder_sheet_pdf",
    "render_map_image",
    "resolve_observer_name",
]


def __getattr__(name: str):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f".{module_name}", __name__), name)
