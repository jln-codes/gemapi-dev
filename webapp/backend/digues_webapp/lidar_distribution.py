"""L2c : accès métier aux blocs Float32, sans exposition du cache ni d'IGN."""

from __future__ import annotations

import asyncio
import json
import math
import os
from typing import Literal
from urllib.parse import urlsplit

import httpx
import psycopg
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from .database import open_read_connection
from .lidar_masks import calculate_lidar_masks, MAX_ENSEMBLES
from .lidar_masks_http import LidarEnsemble

MAX_REQUEST_BYTES = 64 * 1024
MAX_INTERNAL_BYTES = 2 * 1024 * 1024
MAX_TIFF_BYTES = 8 * 1024 * 1024
_WORK_SLOTS = asyncio.Semaphore(4)


class LidarView(BaseModel):
    model_config = ConfigDict(extra='forbid')
    crs: Literal['EPSG:2154']
    bbox: tuple[float, float, float, float]
    level: int = Field(default=0, ge=0, le=30, strict=True)

    @model_validator(mode='after')
    def bounded(self):
        x0, y0, x1, y1 = self.bbox
        if (not all(math.isfinite(value) for value in self.bbox) or
                x0 >= x1 or y0 >= y1 or
                any(abs(value) > 1e12 for value in self.bbox)):
            raise ValueError('Emprise LiDAR invalide ou trop vaste.')
        return self


class LidarBlockRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[0-9a-f]{64}$')
    ix: int
    iy: int


class LidarPlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ensembles: list[LidarEnsemble] = Field(min_length=1, max_length=MAX_ENSEMBLES)
    vue: LidarView
    policy_version: int = Field(strict=True, ge=1, le=9007199254740991)


class LidarReadRequest(LidarPlanRequest):
    bloc: LidarBlockRef


async def read_lidar_distribution_request(request: Request, model):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'Un corps JSON est requis.')
    declared = request.headers.get('content-length')
    if declared is not None:
        try:
            if int(declared) > MAX_REQUEST_BYTES:
                raise HTTPException(413, 'Demande trop volumineuse.')
        except ValueError:
            raise HTTPException(400, 'Longueur de demande invalide.') from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, 'Demande trop volumineuse.')
        body.extend(chunk)
    try:
        return model.model_validate(json.loads(body))
    except (ValidationError, ValueError, UnicodeDecodeError):
        raise HTTPException(422, 'Demande LiDAR invalide.') from None


def _authorized_masks(selection, radius_m):
    with open_read_connection() as connection:
        result = calculate_lidar_masks(
            connection, radius_m=radius_m,
            systeme_ids=[row.id for row in selection.ensembles if row.type == 'SE'],
            amenagement_ids=[row.id for row in selection.ensembles if row.type == 'AH'],
        )
        if any(any(item['code'] == 'ENSEMBLE_INTROUVABLE' for item in mask['diagnostics'])
               for mask in result['masks']):
            raise HTTPException(404, 'Ensemble introuvable dans cette base.')
        ready = [json.dumps(mask['geometry'], separators=(',', ':'))
                 for mask in result['masks'] if mask['status'] == 'READY']
        if ready:
            try:
                shape, valid, empty, kind = connection.execute('''
                    WITH parts AS (
                        SELECT ST_SetSRID(ST_GeomFromGeoJSON(value), 2154) AS geom
                        FROM unnest(%s::text[]) AS value
                    ), dissolved AS (
                        SELECT ST_Multi(ST_UnaryUnion(ST_Collect(geom))) AS geom FROM parts
                    )
                    SELECT ST_AsGeoJSON(geom)::jsonb, ST_IsValid(geom), ST_IsEmpty(geom),
                           GeometryType(geom) FROM dissolved
                ''', (ready,)).fetchone()
            except psycopg.Error as exc:
                raise HTTPException(503, 'Union des masques LiDAR indisponible.') from exc
            if not valid or empty or kind != 'MULTIPOLYGON' or not isinstance(shape, dict):
                raise HTTPException(503, 'Union des masques LiDAR indisponible.')
            coverage = {'status': 'READY', 'geometry': shape}
        else:
            coverage = {'status': 'EMPTY', 'geometry': None}
    return result, {'version': result['version'], 'crs': result['crs'],
                    'masks': [coverage]}


def _check_policy(current_policy, version):
    if current_policy()['policy_version'] != version:
        raise HTTPException(409, 'Buffer LiDAR modifié ; recalculer le plan.')


def _internal_settings():
    base = os.getenv('BASEMAP_RASTER_URL', '').rstrip('/')
    token = os.getenv('LIDAR_INTERNAL_TOKEN', '')
    parsed = urlsplit(base)
    if (len(token) < 32 or parsed.scheme not in {'http', 'https'} or not parsed.hostname or
            parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise HTTPException(503, 'Distribution LiDAR indisponible.')
    return base, token


async def _internal_call(path, body, *, max_bytes, client):
    base, token = _internal_settings()
    encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > MAX_INTERNAL_BYTES:
        raise HTTPException(413, 'Résultat de masques trop volumineux.')
    try:
        if client is None:
            raise HTTPException(503, 'Transport LiDAR indisponible.')
        upstream = await client.send(httpx.Request('POST', base + path, content=encoded,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}), stream=True)
        try:
            chunks, size = [], 0
            async for chunk in upstream.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(502, 'Réponse LiDAR trop volumineuse.')
                chunks.append(chunk)
            return upstream.status_code, upstream.headers, b''.join(chunks)
        finally:
            await upstream.aclose()
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise HTTPException(503, 'Distribution LiDAR indisponible.') from exc


async def _call_while_connected(request, path, body, *, max_bytes):
    task = asyncio.create_task(_internal_call(path, body, max_bytes=max_bytes, client=getattr(request.app.state, 'lidar_http_client', None)))
    try:
        while not task.done():
            if await request.is_disconnected():
                task.cancel()  # Closing the internal HTTP stream aborts the Node subscriber.
                raise HTTPException(408, 'Demande LiDAR annulée.')
            await asyncio.wait({task}, timeout=0.2)
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _prepared(request, selection, current_policy):
    _internal_settings()  # The product remains disabled before any spatial work.
    try:
        await asyncio.wait_for(_WORK_SLOTS.acquire(), timeout=0.05)
    except TimeoutError:
        raise HTTPException(503, 'Distribution LiDAR occupée.') from None
    try:
        policy = await run_in_threadpool(current_policy)
        if selection.policy_version != policy['policy_version']:
            raise HTTPException(409, 'Buffer LiDAR modifié ; recalculer le plan.')
        masks, coverage = await run_in_threadpool(_authorized_masks, selection, policy['buffer_m'])
        if await request.is_disconnected():
            raise HTTPException(408, 'Demande LiDAR annulée.')
        return masks, coverage, policy
    except BaseException:
        _WORK_SLOTS.release()
        raise


async def lidar_plan_response(request, selection, *, current_policy, revalidate_access):
    masks, coverage, policy = await _prepared(request, selection, current_policy)
    try:
        code, _headers, payload = await _call_while_connected(request, '/internal/lidar/v1/plan',
            {'masks': coverage, 'view': selection.vue.model_dump()},
            max_bytes=256 * 1024)
        if code != 200:
            raise HTTPException(413 if code == 413 else 503, 'Planification LiDAR indisponible.')
        plan = json.loads(payload)
        await run_in_threadpool(revalidate_access)
        await run_in_threadpool(_check_policy, current_policy, policy['policy_version'])
        plan['buffer_m'] = policy['buffer_m']
        plan['policy_version'] = policy['policy_version']
        plan['ensembles'] = [{'type': mask['type'], 'id': mask['id'],
                             'status': mask['status'], 'diagnostics': mask['diagnostics']}
                            for mask in masks['masks']]
        encoded = json.dumps(plan, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > 256 * 1024:
            raise HTTPException(413, 'Plan LiDAR trop volumineux.')
        return Response(content=encoded, media_type='application/json',
                        headers={'Cache-Control': 'no-store'})
    finally:
        _WORK_SLOTS.release()


async def lidar_block_response(request, selection, *, current_policy, revalidate_access):
    masks, coverage, policy = await _prepared(request, selection, current_policy)
    try:
        code, headers, payload = await _call_while_connected(request, '/internal/lidar/v1/block',
            {'masks': coverage, 'view': selection.vue.model_dump(),
             'block': selection.bloc.model_dump()}, max_bytes=MAX_TIFF_BYTES)
        await run_in_threadpool(revalidate_access)
        await run_in_threadpool(_check_policy, current_policy, policy['policy_version'])
        if code == 404:
            raise HTTPException(404, 'Bloc absent du plan courant.')
        if code == 204 and headers.get('x-lidar-status') == 'NO_COVERAGE':
            return Response(status_code=204, headers={'Cache-Control': 'no-store',
                                                     'X-Lidar-Status': 'NO_COVERAGE'})
        if (code != 200 or headers.get('content-type', '').split(';', 1)[0] != 'image/geotiff'
                or headers.get('x-lidar-status') not in {'DATA', 'NO_DATA'}):
            raise HTTPException(504 if code == 504 else 503, 'Bloc LiDAR indisponible.')
        return Response(content=payload, media_type='image/geotiff', headers={
            'Cache-Control': 'no-store', 'X-Lidar-Status': headers['x-lidar-status'],
            'X-Cache': headers.get('x-cache', 'UNKNOWN')})
    finally:
        _WORK_SLOTS.release()
