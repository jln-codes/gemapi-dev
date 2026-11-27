"""Adaptateurs explicites des handlers Carte/Base encore mono-base.

Les handlers métier sont réutilisés, pas appelés via HTTP. La signature publiée
remplace exclusivement la dépendance de connexion ; aucune surcharge globale
FastAPI, DSN globale ou ContextVar. Toute nouvelle route exige une entrée revue.
"""

from functools import wraps
from inspect import signature, iscoroutinefunction

from fastapi import Depends
from fastapi.routing import APIRoute

from .business_context import business_connection_dependency, require_business_permission


LEVEL_A_IDENTIFIERS = {
    'mobilier': 'mobilier_id',
    'equipements-mesure': 'identifier',
    'ouvrages-particuliers': 'identifier',
    'reseaux-techniques': 'identifier',
    'cheminements': 'identifier',
    'vegetation': 'identifier',
    'amenagements-hydrauliques': 'identifier',
    'zones-stockage': 'identifier',
    'prestations': 'identifier',
}
LEVEL_A_READ = {
    '/vegetation/relations/options',
    '/amenagements-hydrauliques/relations/options',
    '/prestations/relations/options',
    '/zones-stockage/relations/options',
} | {
    f'/{resource}' for resource in LEVEL_A_IDENTIFIERS
} | {
    f'/{resource}/{{{identifier}}}'
    for resource, identifier in LEVEL_A_IDENTIFIERS.items()
} | {
    f'/{resource}/{{{identifier}}}/observations'
    for resource, identifier in LEVEL_A_IDENTIFIERS.items()
}

READ = {
    '/troncons-lit', '/troncons-lit/options', '/troncons-lit/{troncon_lit_id}',
    '/elements-morphologiques-lit', '/elements-morphologiques-lit/{element_id}',
    '/geometry-capabilities/{resource}',
    '/base-summary', '/territoire-administratif', '/hydrometrie/sites',
    '/meteo/precipitations',
    '/meteo/precipitations/echances', '/referentiels/types-desordre',
    '/referentiels/categories-desordre', '/referentiels/{catalog}',
    '/troncons/options', '/troncons/{troncon_id}/reperage-options',
    '/troncons/{troncon_id}/geometrie-bornage',
    '/troncons/{troncon_id}/observations',
    '/coordinates/transform', '/troncons/{troncon_id}/position-reperage',
    '/desordres', '/desordres/search', '/desordres/selection',
    '/desordres/{desordre_id}/objets-associes',
    '/desordres/{desordre_id}', '/desordres/{desordre_id}/observations',
    '/data/desordres', '/data/view-catalog',
} | LEVEL_A_READ
POLICY = {('GET', path): 'business.read' for path in READ} | {
    ('POST', '/troncons-lit'): 'business.create',
    ('PUT', '/troncons-lit/{troncon_lit_id}'): 'business.update',
    ('POST', '/elements-morphologiques-lit'): 'business.create',
    ('PUT', '/elements-morphologiques-lit/{element_id}'): 'business.update',
    ('POST', '/troncons-lit/{troncon_lit_id}/caracteristiques/{famille}'): 'business.create',
    ('PUT', '/troncons-lit/{troncon_lit_id}/caracteristiques/{famille}/{component_id}'): 'business.update',
    ('POST', '/data/desordres/query'): 'business.read',
    ('POST', '/data/view-query'): 'business.read',
    ('POST', '/data/export-gpkg'): 'exports.read',
    ('POST', '/data/export-csv'): 'exports.read',
    ('GET', '/sql/presets'): 'sql.read',
    ('POST', '/territoire-administratif/import'): 'business.update',
    **{('POST', '/' + path): 'business.create' for path in ('digues', 'troncons', 'desordres')},
    **{('POST', '/' + resource): 'business.create' for resource in LEVEL_A_IDENTIFIERS},
    **{('PUT', path): 'business.update' for path in (
        '/digues/{digue_id}', '/troncons/{troncon_id}', '/desordres/{desordre_id}',
        '/desordres/{desordre_id}/reperage', '/desordres/{desordre_id}/geometry',
        '/desordres/{desordre_id}/endpoints')},
    **{('PUT', f'/{resource}/{{{identifier}}}'): 'business.update'
       for resource, identifier in LEVEL_A_IDENTIFIERS.items()},
}


def register_frontend_routes(application):
    from .database import get_connection, get_write_connection

    for route in list(application.routes):
        if not isinstance(route, APIRoute) or not route.path.startswith('/api/'):
            continue
        suffix = route.path.removeprefix('/api')
        for method in route.methods:
            permission = POLICY.get((method, suffix))
            if permission is None:
                continue
            endpoint = route.endpoint

            def adapt(handler):
                if iscoroutinefunction(handler):
                    @wraps(handler)
                    async def adapted(**kwargs):
                        return await handler(**kwargs)
                else:
                    @wraps(handler)
                    def adapted(**kwargs):
                        return handler(**kwargs)
                return adapted

            adapted = adapt(endpoint)
            parameters = []
            handler_signature = signature(endpoint, eval_str=True)
            for param in handler_signature.parameters.values():
                dependency = getattr(param.default, 'dependency', None)
                if dependency in (get_connection, get_write_connection):
                    param = param.replace(default=Depends(business_connection_dependency(
                        permission, write=dependency is get_write_connection)))
                parameters.append(param)
            adapted.__signature__ = handler_signature.replace(parameters=parameters)
            application.add_api_route(
                '/api/bases/{business_database_id}' + suffix, adapted, methods=[method],
                dependencies=[Depends(require_business_permission(permission))],
                status_code=route.status_code, response_class=route.response_class,
                response_model=route.response_model, name='business_' + route.name,
            )
    # Static /troncons/options must precede the existing UUID detail route.
    options = next(r for r in application.routes if getattr(r, 'path', '') ==
                   '/api/bases/{business_database_id}/troncons/options')
    application.routes.remove(options)
    application.routes.insert(0, options)
