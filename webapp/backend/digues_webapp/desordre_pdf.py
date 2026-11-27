"""Fiche Désordre PDF : ``GET /api/desordres/{id}/pdf``.

La génération elle-même appartient à ``digues_app.reports`` ; ce module ne fait
que l'adaptation FastAPI : connexion en lecture seule, résolution de la photo
par ``MediaStorage``, réponse HTTP. Le PDF est produit en mémoire et n'est pas
conservé sur le serveur.

Les imports lourds (ReportLab, matplotlib) sont différés à l'appel afin de ne
pas les charger au démarrage de l'application.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.responses import Response

from .database import get_connection
from .media_storage import get_media_storage, MediaScopeError


LOGGER = logging.getLogger(__name__)


class BusinessPhotoLoader:
    """Per-report SQL + media target, captured explicitly (including in threads).

    The generic renderers tolerate missing photos. Identity/SQL errors MUST NOT
    disappear into that fallback: check() is mandatory before returning the PDF.
    """
    def __init__(self, connection, context):
        self.connection, self.context = connection, context
        self.error = None

    def __call__(self, photo):
        if not photo.storage_key or not photo.identifier:
            return None
        try:
            row = self.connection.execute(
                "SELECT storage_key,observation_id FROM public.photos WHERE id=%s",
                (photo.identifier,)).fetchone()
            if not row or not row[0]:
                return None
            if str(row[0]) != str(photo.storage_key):
                raise MediaScopeError("Report Photo identity changed")
            storage = get_media_storage(required=False, context=self.context)
            manifest = storage.inspect(row[0]) if storage else None
            if manifest is None:
                return None
            if (manifest['owner']['photo_id'] != str(photo.identifier)
                    or manifest['owner']['observation_id'] != str(row[1])):
                raise MediaScopeError("Report Photo owner mismatch")
            with storage.open_variant(row[0], 'original') as stream:
                return stream.read()
        except FileNotFoundError:
            return None
        except Exception as exc:
            self.error = exc
            raise

    def check(self):
        if self.error is not None:
            raise HTTPException(503, 'Lecture des médias du rapport non confirmée.') from self.error


def load_photo_bytes(photo) -> bytes | None:
    """Octets de la photo, obtenus exclusivement via ``MediaStorage``.

    Aucun chemin interne du backend de stockage n'est lu. Un média absent ou
    illisible produit le rendu historique d'une photo absente, sans erreur.
    Partagé par la fiche individuelle et le catalogue.
    """

    if not photo.storage_key:
        return None
    try:
        storage = get_media_storage(required=False)
        if storage is None:
            return None
        stream = storage.open_variant(photo.storage_key, "original")
    except Exception:
        LOGGER.warning("Photo indisponible pour la fiche PDF : %s", photo.identifier)
        return None
    try:
        return stream.read()
    finally:
        try:
            stream.close()
        except Exception:  # pragma: no cover - fermeture défensive
            pass


def render_situation_map(context) -> bytes | None:
    """Carte de situation, ou ``None`` si elle ne peut pas être produite.

    Une carte impossible ne doit pas empêcher la production de la fiche : le
    rendu est alors celui de l'implémentation historique lorsqu'aucune carte
    n'était disponible (titre de section seul). Partagé par la fiche
    individuelle et le catalogue.
    """

    from digues_app.reports.map import render_map_image

    try:
        return render_map_image(context)
    except Exception:
        LOGGER.exception("Carte de situation indisponible ; fiche générée sans carte.")
        return None


def register_routes(application) -> None:
    from .database import get_connection, get_write_connection
    connection_dependency = get_connection
    @application.get("/api/desordres/{desordre_id}/pdf")
    def desordre_pdf(
        desordre_id: UUID,
        connection: Any = Depends(connection_dependency),
    ) -> Response:
        from digues_app.reports.naming import content_disposition, disorder_pdf_filename
        from digues_app.reports.pdf import render_disorder_sheet_pdf
        from digues_app.reports.repository import (
            DisorderSheetNotFound,
            load_disorder_sheet,
        )

        try:
            sheet = load_disorder_sheet(connection, desordre_id)
        except DisorderSheetNotFound as exc:
            raise HTTPException(status_code=404, detail="Désordre introuvable.") from exc

        loader = BusinessPhotoLoader(connection, None)
        content = render_disorder_sheet_pdf(
            sheet,
            photo_loader=loader,
            map_renderer=render_situation_map,
        )
        loader.check()
        filename = disorder_pdf_filename(sheet.section_label, sheet.designation)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": content_disposition(filename)},
        )
