"""Adaptateur HTTP L1b des masques calculés par LIDAR_MASK_V1.

La connexion métier autorisée est fournie par la dépendance de routage. Aucun
identifiant de base ou paramètre SQL n'est accepté dans le corps de la demande.
"""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .lidar_masks import calculate_lidar_masks, MAX_ENSEMBLES

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class LidarEnsemble(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["SE", "AH"]
    id: UUID


class LidarMaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ensembles: list[LidarEnsemble] = Field(min_length=1, max_length=MAX_ENSEMBLES)


async def read_lidar_mask_request(request: Request) -> LidarMaskRequest:
    """Borne les octets avant de parser le JSON ou d'allouer les modèles."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Un corps JSON est requis.")
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_REQUEST_BYTES:
                raise HTTPException(413, "Demande trop volumineuse.")
        except ValueError:
            raise HTTPException(400, "Longueur de demande invalide.") from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "Demande trop volumineuse.")
        body.extend(chunk)
    try:
        return LidarMaskRequest.model_validate(json.loads(body))
    except (ValidationError, ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Sélection de masques invalide.") from None


def lidar_mask_response(connection, selection: LidarMaskRequest, radius_m=25) -> Response:
    """Une seule invocation L1a et donc un seul snapshot pour toute la sélection."""
    systeme_ids = [item.id for item in selection.ensembles if item.type == "SE"]
    amenagement_ids = [item.id for item in selection.ensembles if item.type == "AH"]
    result = calculate_lidar_masks(connection, systeme_ids=systeme_ids,
                                   amenagement_ids=amenagement_ids, radius_m=radius_m)
    if any(any(diagnostic["code"] == "ENSEMBLE_INTROUVABLE"
               for diagnostic in mask["diagnostics"]) for mask in result["masks"]):
        raise HTTPException(404, "Ensemble introuvable dans cette base.")
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > MAX_RESPONSE_BYTES:
        raise HTTPException(413, "Résultat de masques trop volumineux.")
    return Response(content=payload, media_type="application/json",
                    headers={"Cache-Control": "no-store"})
