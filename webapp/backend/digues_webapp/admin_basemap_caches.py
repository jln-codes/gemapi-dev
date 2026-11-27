"""Superadmin-only cache observations; the renderer remains the sole cache owner."""

from __future__ import annotations

import asyncio
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request as UrlRequest, build_opener

from fastapi import Depends, HTTPException


def renderer_call(method: str, route: str, actor: str, body: dict | None = None) -> dict:
    base = os.getenv("BASEMAP_ADMIN_URL", "").rstrip("/")
    token = os.getenv("BASEMAP_ADMIN_TOKEN", "")
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme == "http" and parsed.hostname in {"basemap", "127.0.0.1"}
                 and parsed.port and not parsed.username and not parsed.password
                 and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "Administration du renderer non configurée.")
    if len(token) < 32:
        raise HTTPException(503, "Secret d'administration cartographique absent.")
    request = UrlRequest(base + route, method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "X-Admin-Actor": actor,
                 "Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=8) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 409:
            raise HTTPException(409, "Réglages modifiés entre-temps ; rechargez la page.") from None
        if error.code == 400:
            raise HTTPException(422, "Réglages ou identifiant de cache invalides.") from None
        raise HTTPException(503, "Administration cartographique indisponible.") from None
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(503, "Renderer cartographique indisponible.") from None


def register_routes(app, *, require_operator):
    read = [Depends(require_operator)]

    @app.get('/api/admin/basemap-caches', dependencies=read)
    async def get_basemap_caches(user=Depends(require_operator)):
        status = await asyncio.to_thread(renderer_call, 'GET', '/internal/cache-admin', str(user.id))
        return {'status': status}
