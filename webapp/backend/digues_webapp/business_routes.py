"""Migration explicite d'un sous-ensemble d'API ; pas de routage global implicite."""

import os
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, Request
from psycopg import sql
from fastapi.responses import JSONResponse, StreamingResponse
from digues_app.control.auth import UserIdentity
from starlette.datastructures import MutableHeaders
from starlette.concurrency import run_in_threadpool

from . import queries
from .auth import get_auth_settings, require_authenticated_user
from .business_context import (
    BusinessContext, business_connection_dependency, get_business_read_connection,
    get_business_create_connection, get_business_update_connection,
    get_business_sql_connection, require_business_permission, revalidated_factory,
)
from .models import AiChatRequest, AiChatResponse, InteractiveSqlRequest, SystemeEndiguementCreate, SystemeEndiguementUpdate
from .models import ObservationCreate, ObservationUpdate
from .interactive_sql import execute_interactive_query
from .resource_mutations import execute_mutation
from .resource_journal import mutation_origin, parse_cursor, read_head, read_page, stream_events, HEADERS
from .offline_projection import read_scope, read_pinned
from .patrimoine_geometries import read_patrimoine_geometries
from .lidar_masks_http import LidarMaskRequest, read_lidar_mask_request, lidar_mask_response
from .lidar_distribution import (
    LidarPlanRequest, LidarReadRequest, read_lidar_distribution_request,
    lidar_plan_response, lidar_block_response,
)
from .schema_context import get_ai_schema_context
from .ai import chat_with_mistral


def require_object(connection, table, identifier):
    # Only a server-selected table; an object in another database is absent here.
    if connection.execute(sql.SQL('SELECT 1 FROM public.{} WHERE id=%s').format(
            sql.Identifier(table)), (identifier,)).fetchone() is None:
        raise HTTPException(404, 'Objet introuvable dans cette base.')


class BusinessRoutingMiddleware:
    """Deux modes exclusifs. Aucun repli vers la démo sur une route contextualisée."""

    def __init__(self, app, *, legacy=False):
        self.app, self.legacy = app, legacy

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope['path']
        contextual = path.startswith('/api/bases/')
        # Identity/control have their own guards and are not legacy business APIs.
        outside_business = path.startswith(('/api/auth/', '/api/admin/', '/api/help/')) or path in {
            '/api/config', '/api/health', '/api/basemaps',
        }
        if path.startswith('/api/') and not outside_business and (contextual == self.legacy):
            return await JSONResponse({'detail': 'Route indisponible dans ce mode serveur.'}, 404)(scope, receive, send)

        async def guarded_send(message):
            if contextual and message['type'] == 'http.response.start':
                MutableHeaders(scope=message)['Cache-Control'] = 'no-store'
            await send(message)
        await self.app(scope, receive, guarded_send)


def register_routes(application):
    value = os.getenv('LEGACY_SINGLE_DATABASE_ENABLED', 'false')
    if value not in {'true', 'false'}:
        raise ValueError('LEGACY_SINGLE_DATABASE_ENABLED doit valoir true ou false.')
    application.add_middleware(BusinessRoutingMiddleware, legacy=value == 'true')

    @application.get('/api/health')
    def health():
        return {'status': 'ok'}

    router = application

    from .media_target import media_route_dependencies
    observation_context, observation_connection = media_route_dependencies(True, 'business.read')

    @router.get('/api/bases/{business_database_id}/basemaps')
    def basemap_catalogue(context=Depends(require_business_permission('business.read'))):
        from .basemap_catalog import basemap_catalog_payload
        return JSONResponse(basemap_catalog_payload(), headers={'Cache-Control': 'no-store'})

    @router.get('/api/bases/{business_database_id}/lidar/policy')
    def lidar_policy(context=Depends(require_business_permission('business.read'))):
        from .lidar_distribution import current_policy
        return current_policy()

    @router.get('/api/bases/{business_database_id}/observations/{observation_id}')
    def observation(observation_id: UUID, connection=Depends(observation_connection),
                    context=Depends(observation_context)):
        require_object(connection, 'observations', observation_id)
        return queries.fetch_observation(connection, observation_id, context=context)

    @router.get('/api/bases/{business_database_id}/systemes-endiguement')
    def systems(connection: Any = Depends(get_business_read_connection)):
        return queries.fetch_systemes_endiguement(connection)

    @router.get('/api/bases/{business_database_id}/systemes-endiguement/{systeme_id}')
    def system(systeme_id: UUID, connection: Any = Depends(get_business_read_connection)):
        require_object(connection, 'systemes', systeme_id)
        return queries.fetch_systeme_endiguement(connection, systeme_id)

    @router.get('/api/bases/{business_database_id}/digues/{digue_id}')
    def digue(digue_id: UUID, connection: Any = Depends(get_business_read_connection)):
        require_object(connection, 'digues', digue_id)
        return queries.fetch_digue(connection, digue_id)

    @router.get('/api/bases/{business_database_id}/troncons')
    def trunks(connection: Any = Depends(get_business_read_connection)):
        return queries.fetch_troncons(connection)

    @router.get('/api/bases/{business_database_id}/patrimoine/geometries/v1')
    def patrimoine_geometries(
        crs: str = Query(..., description='CRS projeté qualifié, ex. EPSG:2154'),
        digue_id: UUID | None = None,
        amenagement_hydraulique_id: UUID | None = None,
        connection: Any = Depends(get_business_read_connection),
    ):
        return read_patrimoine_geometries(
            connection, digue_id=digue_id,
            amenagement_hydraulique_id=amenagement_hydraulique_id, crs=crs,
        )

    @router.post('/api/bases/{business_database_id}/lidar/masks/v1', openapi_extra={
        'requestBody': {'required': True, 'content': {
            'application/json': {'schema': LidarMaskRequest.model_json_schema()}}},
    })
    async def lidar_masks(request: Request,
                          connection: Any = Depends(get_business_read_connection)):
        selection = await read_lidar_mask_request(request)
        from .lidar_distribution import current_policy
        policy = await run_in_threadpool(current_policy)
        return await run_in_threadpool(lidar_mask_response, connection, selection, policy['buffer_m'])

    @router.post('/api/bases/{business_database_id}/lidar/blocks/v1/plan', openapi_extra={
        'requestBody': {'required': True, 'content': {
            'application/json': {'schema': LidarPlanRequest.model_json_schema()}}},
    })
    async def lidar_blocks_plan(request: Request,
                                context: BusinessContext = Depends(require_business_permission('business.read')),
                                user: UserIdentity = Depends(require_authenticated_user),
                                settings=Depends(get_auth_settings)):
        selection = await read_lidar_distribution_request(request, LidarPlanRequest)
        return await lidar_plan_response(request, context, user, settings, selection)

    @router.post('/api/bases/{business_database_id}/lidar/blocks/v1/read', openapi_extra={
        'requestBody': {'required': True, 'content': {
            'application/json': {'schema': LidarReadRequest.model_json_schema()}}},
    })
    async def lidar_blocks_read(request: Request,
                                context: BusinessContext = Depends(require_business_permission('business.read')),
                                user: UserIdentity = Depends(require_authenticated_user),
                                settings=Depends(get_auth_settings)):
        selection = await read_lidar_distribution_request(request, LidarReadRequest)
        return await lidar_block_response(request, context, user, settings, selection)

    @router.get('/api/bases/{business_database_id}/troncons/{troncon_id}')
    def trunk(troncon_id: UUID, connection: Any = Depends(get_business_read_connection)):
        require_object(connection, 'troncons', troncon_id)
        return queries.fetch_troncon(connection, troncon_id)

    @router.post('/api/bases/{business_database_id}/systemes-endiguement', status_code=201)
    def create_system(creation: SystemeEndiguementCreate, origin: dict = Depends(mutation_origin),
                      connection: Any = Depends(get_business_create_connection)):
        return execute_mutation(connection, 'systemes', 'create',
            lambda: queries.create_systeme_endiguement(connection, creation), origin=origin)

    @router.put('/api/bases/{business_database_id}/systemes-endiguement/{systeme_id}')
    def update_system(systeme_id: UUID, update: SystemeEndiguementUpdate,
                      origin: dict = Depends(mutation_origin),
                      if_match: str | None = Header(default=None, alias='If-Match'),
                      connection: Any = Depends(get_business_update_connection)):
        return execute_mutation(connection, 'systemes', 'update',
            lambda: queries.update_systeme_endiguement(connection, systeme_id, update),
            identifier=systeme_id, if_match=if_match, origin=origin)

    @router.post('/api/bases/{business_database_id}/sql/query')
    def sql_query(body: InteractiveSqlRequest, connection: Any = Depends(get_business_sql_connection)):
        return execute_interactive_query(connection, body.sql)

    def service_factory(request, context, user, permission, *, sql=False, ai=False):
        settings = get_auth_settings(request)
        return revalidated_factory(context, user, request.cookies.get(settings.cookie_name, ''), permission, sql=sql, ai=ai)

    @router.post('/api/bases/{business_database_id}/ai/chat')
    def ai_chat(body: AiChatRequest, request: Request,
                context: BusinessContext = Depends(require_business_permission('ai.read')),
                user=Depends(require_authenticated_user)):
        factory, validate = service_factory(request, context, user, 'ai.read', ai=True)
        # Even a cache hit or an answer without tool calls requires a working,
        # restricted readonly profile. No cached metadata bypasses this check.
        with factory():
            pass
        schema = get_ai_schema_context(connection_factory=factory, cache_key=context.schema_cache_key)
        result = chat_with_mistral([message.model_dump() for message in body.recent_messages], schema,
            conversation_summary=body.conversation_summary, current_message=body.message, connection_factory=factory)
        validate()  # Do not return a long-running answer after revocation.
        return AiChatResponse(answer=result.answer, conversation_summary=result.conversation_summary,
            recent_messages=list(result.recent_messages),
            executed_queries=[{'sql': sql} for sql in result.executed_queries],
            consulted_sources=[{'title': s.title, 'path': s.path, 'heading': s.heading} for s in result.consulted_sources],
            external_sources=[{key: getattr(s, key) for key in (
                'title', 'organization', 'url', 'source_id', 'category', 'document_date')}
                for s in result.external_sources]).model_dump()

    @router.get('/api/bases/{business_database_id}/resource-events/head')
    def head(request: Request, context: BusinessContext = Depends(require_business_permission('business.read')),
             user=Depends(require_authenticated_user)):
        factory, _ = service_factory(request, context, user, 'business.read')
        return read_head(connection_factory=factory)

    @router.get('/api/bases/{business_database_id}/resource-events')
    def page(request: Request, after: str = '0', limit: int = Query(default=100, ge=1, le=500),
             context: BusinessContext = Depends(require_business_permission('business.read')),
             user=Depends(require_authenticated_user)):
        factory, _ = service_factory(request, context, user, 'business.read')
        return read_page(parse_cursor(after), limit, connection_factory=factory)

    @router.get('/api/bases/{business_database_id}/resource-events/stream')
    def stream(request: Request, after: str = '0', last_event_id: str | None = Header(default=None),
               context: BusinessContext = Depends(require_business_permission('business.read')),
               user=Depends(require_authenticated_user)):
        factory, _ = service_factory(request, context, user, 'business.read')
        cursor = parse_cursor(last_event_id if last_event_id is not None else after)
        read_head(connection_factory=factory)  # Preflight before HTTP headers.
        return StreamingResponse(stream_events(request, cursor, connection_factory=factory),
                                 media_type='text/event-stream', headers=HEADERS)

    offline_connection = business_connection_dependency('offline.prepare', offline_prepare=True)

    @router.get('/api/bases/{business_database_id}/offline-scope')
    def offline_scope(troncon_id: list[UUID] = Query(), connection: Any = Depends(offline_connection)):
        return read_scope(connection, troncon_id)

    @router.get('/api/bases/{business_database_id}/offline-scope/pinned/{resource}/{identifier}')
    def pinned(resource: str, identifier: UUID, connection: Any = Depends(offline_connection)):
        return read_pinned(connection, resource, identifier)

    create_context, create_connection = media_route_dependencies(True, 'business.create', write=True)
    update_context, update_connection = media_route_dependencies(True, 'business.update', write=True)

    @router.post('/api/bases/{business_database_id}/observations', status_code=201)
    def create_observation(creation: ObservationCreate, operation_id: str = Header(alias='Operation-Id'),
                           origin: dict = Depends(mutation_origin),
                           connection=Depends(create_connection), context=Depends(create_context)):
        return execute_mutation(connection, 'observations', 'create',
            lambda: queries.create_observation(connection, creation, context=context),
            identifier=creation.id, origin=origin, operation_id=operation_id,
            changes=creation.model_dump(mode='json'))

    @router.put('/api/bases/{business_database_id}/observations/{observation_id}')
    def update_observation(observation_id: UUID, update: ObservationUpdate,
                           operation_id: str | None = Header(default=None, alias='Operation-Id'),
                           if_match: str | None = Header(default=None, alias='If-Match'),
                           origin: dict = Depends(mutation_origin),
                           connection=Depends(update_connection), context=Depends(update_context)):
        return execute_mutation(connection, 'observations', 'update',
            lambda: queries.update_observation(connection, observation_id, update, context=context),
            identifier=observation_id, if_match=if_match, origin=origin,
            operation_id=operation_id, changes=update.model_dump(mode='json', exclude_unset=True))
