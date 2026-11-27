"""Catalogue PDF par périmètre Patrimoine.

La génération appartient à ``digues_app.reports.catalog`` ; ce module ne fait
que l'adaptation FastAPI : connexion, résolution du périmètre patrimonial,
réponse JSON ou PDF.

Un catalogue correspond à un nœud du patrimoine sélectionné dans le panneau
Patrimoine (système d'endiguement, digue ou tronçon). Le backend résout lui-même
la hiérarchie système → digues → tronçons → désordres : le frontend ne fournit
jamais une liste d'UUID de désordres.

Présentation strictement fidèle à l'historique : le catalogue est la
concaténation des fiches, dans l'ordre hiérarchique, sans page de titre, sans
séparateur, sans sommaire et sans signet.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.responses import Response

from .database import get_connection
from .desordre_pdf import load_photo_bytes, render_situation_map, BusinessPhotoLoader


LOGGER = logging.getLogger(__name__)

WARNING_HEADER = "X-Digues-Catalogue-Avertissements"


def _load_catalog(connection: Any, scope_kind: str, identifier: UUID) -> Any:
    from digues_app.reports.catalog import CatalogScopeNotFound, load_patrimoine_catalog

    try:
        return load_patrimoine_catalog(connection, scope_kind, identifier)
    except CatalogScopeNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Périmètre patrimonial introuvable.",
        ) from exc


def _serialize(catalog: Any) -> dict[str, Any]:
    return {
        "scope": {
            "kind": catalog.scope.kind,
            "id": catalog.scope.identifier,
            "label": catalog.scope.label,
        },
        "entry_count": catalog.entry_count,
        "digue_count": catalog.digue_count,
        "troncon_count": catalog.troncon_count,
        "systemes": [
            {
                "id": systeme.identifier,
                "label": systeme.label,
                "digues": [
                    {
                        "id": digue.identifier,
                        "label": digue.label,
                        "troncons": [
                            {
                                "id": troncon.identifier,
                                "label": troncon.label,
                                "entries": [
                                    {
                                        "id": entry.disorder_id,
                                        "designation": entry.designation,
                                    }
                                    for entry in troncon.entries
                                ],
                            }
                            for troncon in digue.troncons
                        ],
                    }
                    for digue in systeme.digues
                ],
            }
            for systeme in catalog.systemes
        ],
        "warnings": list(catalog.warnings),
    }


def _catalog_pdf(connection: Any, catalog: Any, *, context=None) -> Response:
    from digues_app.reports.catalog import render_catalog_pdf
    from digues_app.reports.naming import content_disposition, disorder_catalog_filename

    if catalog.entry_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Aucun désordre dans ce périmètre patrimonial.",
        )

    loader = BusinessPhotoLoader(connection, context) if context else load_photo_bytes
    content = render_catalog_pdf(
        connection,
        catalog,
        photo_loader=loader,
        map_renderer=render_situation_map,
    )
    if context:
        loader.check()
    filename = disorder_catalog_filename(catalog.scope.kind, catalog.scope.label)
    headers = {
        "Content-Disposition": content_disposition(
            filename, fallback="catalogue-desordres.pdf"
        ),
        WARNING_HEADER: str(len(catalog.warnings)),
    }
    return Response(content=content, media_type="application/pdf", headers=headers)


def register_routes(application, *, contextual=False) -> None:
    from .media_target import media_route_dependencies
    _, read_connection = media_route_dependencies(contextual, 'business.read')
    export_context, export_connection = media_route_dependencies(contextual, 'exports.read')
    prefix = '/api/bases/{business_database_id}' if contextual else '/api'
    @application.get(prefix + "/amenagements-hydrauliques/{amenagement_id}/desordres/catalogue")
    def amenagement_catalogue(
        amenagement_id: UUID,
        connection: Any = Depends(read_connection),
    ) -> dict[str, Any]:
        return _serialize(_load_catalog(connection, "amenagement_hydraulique", amenagement_id))

    @application.get(prefix + "/amenagements-hydrauliques/{amenagement_id}/desordres/catalogue/pdf")
    def amenagement_catalogue_pdf(
        amenagement_id: UUID,
        connection: Any = Depends(export_connection),
        context=Depends(export_context),
    ) -> Response:
        return _catalog_pdf(connection, _load_catalog(connection, "amenagement_hydraulique", amenagement_id), context=context)

    @application.get(prefix + "/systemes-endiguement/{systeme_id}/desordres/catalogue")
    def systeme_catalogue(
        systeme_id: UUID,
        connection: Any = Depends(read_connection),
    ) -> dict[str, Any]:
        return _serialize(_load_catalog(connection, "systeme", systeme_id))

    @application.get(prefix + "/systemes-endiguement/{systeme_id}/desordres/catalogue/pdf")
    def systeme_catalogue_pdf(
        systeme_id: UUID,
        connection: Any = Depends(export_connection),
        context=Depends(export_context),
    ) -> Response:
        return _catalog_pdf(connection, _load_catalog(connection, "systeme", systeme_id), context=context)

    @application.get(prefix + "/digues/{digue_id}/desordres/catalogue")
    def digue_catalogue(
        digue_id: UUID,
        connection: Any = Depends(read_connection),
    ) -> dict[str, Any]:
        return _serialize(_load_catalog(connection, "digue", digue_id))

    @application.get(prefix + "/digues/{digue_id}/desordres/catalogue/pdf")
    def digue_catalogue_pdf(
        digue_id: UUID,
        connection: Any = Depends(export_connection),
        context=Depends(export_context),
    ) -> Response:
        return _catalog_pdf(connection, _load_catalog(connection, "digue", digue_id), context=context)

    @application.get(prefix + "/troncons/{troncon_id}/desordres/catalogue")
    def troncon_catalogue(
        troncon_id: UUID,
        connection: Any = Depends(read_connection),
    ) -> dict[str, Any]:
        return _serialize(_load_catalog(connection, "troncon", troncon_id))

    @application.get(prefix + "/troncons/{troncon_id}/desordres/catalogue/pdf")
    def troncon_catalogue_pdf(
        troncon_id: UUID,
        connection: Any = Depends(export_connection),
        context=Depends(export_context),
    ) -> Response:
        return _catalog_pdf(connection, _load_catalog(connection, "troncon", troncon_id), context=context)
