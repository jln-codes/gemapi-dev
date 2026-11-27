import inspect
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from digues_webapp import auth, database, business_context as bc
from digues_webapp.app import create_app
from digues_webapp.frontend_routes import POLICY
from digues_app.control.auth import UserIdentity
from webapp.tests.test_business_context import context


LEVEL_A_ROUTE_CONTRACT = {
    ('GET', '/mobilier'): 'business.read',
    ('POST', '/mobilier'): 'business.create',
    ('GET', '/mobilier/{mobilier_id}'): 'business.read',
    ('PUT', '/mobilier/{mobilier_id}'): 'business.update',
    ('GET', '/mobilier/{mobilier_id}/observations'): 'business.read',
    ('GET', '/equipements-mesure'): 'business.read',
    ('POST', '/equipements-mesure'): 'business.create',
    ('GET', '/equipements-mesure/{identifier}'): 'business.read',
    ('PUT', '/equipements-mesure/{identifier}'): 'business.update',
    ('GET', '/equipements-mesure/{identifier}/observations'): 'business.read',
    ('GET', '/ouvrages-particuliers'): 'business.read',
    ('POST', '/ouvrages-particuliers'): 'business.create',
    ('GET', '/ouvrages-particuliers/{identifier}'): 'business.read',
    ('PUT', '/ouvrages-particuliers/{identifier}'): 'business.update',
    ('GET', '/ouvrages-particuliers/{identifier}/observations'): 'business.read',
    ('GET', '/reseaux-techniques'): 'business.read',
    ('POST', '/reseaux-techniques'): 'business.create',
    ('GET', '/reseaux-techniques/{identifier}'): 'business.read',
    ('PUT', '/reseaux-techniques/{identifier}'): 'business.update',
    ('GET', '/reseaux-techniques/{identifier}/observations'): 'business.read',
    ('GET', '/cheminements'): 'business.read',
    ('GET', '/desordres/search'): 'business.read',
    ('GET', '/desordres/selection'): 'business.read',
    ('GET', '/desordres/{desordre_id}/objets-associes'): 'business.read',
    ('POST', '/cheminements'): 'business.create',
    ('GET', '/cheminements/{identifier}'): 'business.read',
    ('PUT', '/cheminements/{identifier}'): 'business.update',
    ('GET', '/cheminements/{identifier}/observations'): 'business.read',
    ('GET', '/vegetation'): 'business.read',
    ('GET', '/vegetation/relations/options'): 'business.read',
    ('POST', '/vegetation'): 'business.create',
    ('GET', '/vegetation/{identifier}'): 'business.read',
    ('PUT', '/vegetation/{identifier}'): 'business.update',
    ('GET', '/vegetation/{identifier}/observations'): 'business.read',
    ('GET', '/amenagements-hydrauliques'): 'business.read',
    ('GET', '/amenagements-hydrauliques/relations/options'): 'business.read',
    ('POST', '/amenagements-hydrauliques'): 'business.create',
    ('GET', '/amenagements-hydrauliques/{identifier}'): 'business.read',
    ('PUT', '/amenagements-hydrauliques/{identifier}'): 'business.update',
    ('GET', '/amenagements-hydrauliques/{identifier}/observations'): 'business.read',
    ('GET', '/zones-stockage'): 'business.read',
    ('GET', '/zones-stockage/relations/options'): 'business.read',
    ('POST', '/zones-stockage'): 'business.create',
    ('GET', '/zones-stockage/{identifier}'): 'business.read',
    ('PUT', '/zones-stockage/{identifier}'): 'business.update',
    ('GET', '/zones-stockage/{identifier}/observations'): 'business.read',
    ('GET', '/prestations'): 'business.read',
    ('GET', '/prestations/relations/options'): 'business.read',
    ('POST', '/prestations'): 'business.create',
    ('GET', '/prestations/{identifier}'): 'business.read',
    ('PUT', '/prestations/{identifier}'): 'business.update',
    ('GET', '/prestations/{identifier}/observations'): 'business.read',
}


class BrowserContextTest(unittest.TestCase):
    def test_url_content_preference_is_scoped_to_installation_and_user(self):
        root = Path(__file__).resolve().parents[1] / 'frontend' / 'js'
        program = r'''
const assert = require('node:assert/strict');
const {createContext, createRuntime} = require(ROOT + '/business-context.js');
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const values = new Map();
const storage = {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
const catalog = (instance,user,base,slug) => ({instance_id:id(instance),user_id:id(user),databases:[
  {business_database_id:id(base),slug,data_epoch:id(50),status:'active',permissions:['business.read']} ]});
const runtime = (instance,user,base,slug) => createRuntime({
  context:createContext(catalog(instance,user,base,slug),id(base)),origin:'https://digues.test',fetchImpl:fetch,storage});
const a=runtime(1,2,3,'cabbalr1');
assert.equal(a.urlMode(),'position');
a.setUrlMode('minimal');
assert.equal(a.urlMode(),'minimal');
assert.equal(runtime(1,2,4,'cabbalr2').urlMode(),'minimal');
assert.equal(runtime(1,5,3,'cabbalr1').urlMode(),'position');
assert.equal(runtime(6,2,3,'cabbalr1').urlMode(),'position');
a.setUrlMode('object');
assert.equal(a.urlMode(),'object');
assert.throws(()=>a.setUrlMode('unknown'));
'''.replace('ROOT', json.dumps(str(root)))
        subprocess.run(['node', '-e', program], check=True, timeout=30, capture_output=True, text=True)

    def test_navigation_selection_resolves_only_exact_authorized_slug_or_transitional_uuid(self):
        root = Path(__file__).resolve().parents[1] / 'frontend' / 'js'
        program = r'''
const assert = require('node:assert/strict');
const {resolveDatabaseId, createContext, createRuntime} = require(ROOT + '/business-context.js');
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const base = (n, slug, status = 'active', permissions = ['business.read']) =>
  ({business_database_id:id(n), slug, data_epoch:id(90+n), status, permissions});
const catalog = {instance_id:id(1), user_id:id(2), databases:[
  base(11,'cabbalr1'), base(12,'cabbalr2'), base(13,'symadrem1'), base(14,'blocked','inactive'), base(15,'unreadable','active',[]),
]};
for (const [slug, uuid] of [['cabbalr1',id(11)], ['cabbalr2',id(12)], ['symadrem1',id(13)], [id(11),id(11)]]) {
  assert.equal(resolveDatabaseId(catalog,slug),uuid);
  const context = createContext(catalog,resolveDatabaseId(catalog,slug));
  assert.equal(context.business_database_id,uuid);
  assert.equal(context.slug,catalog.databases.find(base=>base.business_database_id===uuid).slug);
  const runtime = createRuntime({context, origin:'https://digues.test', fetchImpl:fetch});
  assert.equal(runtime.pageUrl('/carte'),`/carte?base=${context.slug}`);
  assert.equal(runtime.pageUrl('/base?entity=vegetation&id='+id(80)),`/base?entity=vegetation&id=${id(80)}&base=${context.slug}`);
  assert.equal(runtime.databaseName,`digues-app-field-projection:${id(1)}:${id(2)}:${uuid}:${id(90+Number(uuid.slice(-12)))}`);
}
for (const selection of ['foreign-base','Cabbalr1','', 'with space','école', 'a'.repeat(64),id(99)])
  assert.throws(()=>resolveDatabaseId(catalog,selection));
for (const selection of ['blocked','unreadable'])
  assert.throws(()=>createContext(catalog,resolveDatabaseId(catalog,selection)));
assert.throws(()=>createContext({...catalog,databases:[base(16,undefined)]},id(16)));
assert.notEqual(resolveDatabaseId(catalog,'cabbalr1'),resolveDatabaseId(catalog,'cabbalr2'));
'''.replace('ROOT', json.dumps(str(root)))
        subprocess.run(['node', '-e', program], check=True, timeout=30, capture_output=True, text=True)

    def test_business_global_producers_precede_their_consumers(self):
        frontend = Path(__file__).resolve().parents[1] / 'frontend'

        def business_scripts(page_name):
            page = (frontend / page_name).read_text(encoding='utf-8')
            return [
                part.split('"', 1)[0]
                for part in page.split('data-business-src="')[1:]
            ]

        for page_name, terminal in (('map.html', '/static/js/map.js'),
                                    ('data.html', '/static/js/data-view.js')):
            scripts = business_scripts(page_name)
            with self.subTest(page=page_name):
                self.assertLess(scripts.index('/static/js/inter-page-bus.js'),
                                scripts.index('/static/js/interface-coordination.js'))
                self.assertLess(scripts.index('/static/js/inter-page-bus.js'),
                                scripts.index('/static/js/resource-mutation.js'))
                self.assertLess(scripts.index('/static/js/local-projection.js'),
                                scripts.index('/static/js/offline-consultation.js'))
                self.assertLess(scripts.index('/static/js/offline-consultation.js'),
                                scripts.index('/static/js/resource-server-transport.js'))
                self.assertLess(scripts.index('/static/js/resource-server-transport.js'),
                                scripts.index('/static/js/offline-operations.js'))
                self.assertLess(scripts.index('/static/js/offline-operations.js'),
                                scripts.index(terminal))

        map_scripts = business_scripts('map.html')
        self.assertLess(map_scripts.index('/static/js/entity-editor-shell.js'),
                        map_scripts.index('/static/js/map.js'))
        bootstrap = (frontend / 'js' / 'business-bootstrap.js').read_text(encoding='utf-8')
        self.assertLess(bootstrap.index('root.BusinessRuntime = root.BusinessContext.createRuntime'),
                        bootstrap.index('document.querySelectorAll("script[data-business-src]")'))

    def test_frontend_adapters_never_keep_legacy_connection_dependencies(self):
        app = create_app()
        def dependencies(node):
            yield node.call
            for child in node.dependencies:
                yield from dependencies(child)
        found = set()
        for route in app.routes:
            if not getattr(route, 'path', '').startswith('/api/bases/'):
                continue
            calls = set(dependencies(route.dependant))
            self.assertNotIn(database.get_connection, calls)
            self.assertNotIn(database.get_write_connection, calls)
            for method in route.methods:
                found.add((method, route.path.removeprefix('/api/bases/{business_database_id}')))
        self.assertTrue(set(POLICY) <= found)
        self.assertNotIn(('GET', '/vigicrues/stations'), POLICY)
        self.assertIn(('GET', '/hydrometrie/sites'), POLICY)

    def test_level_a_business_route_contract_is_independent_from_policy(self):
        self.assertEqual(len(LEVEL_A_ROUTE_CONTRACT), 52)
        app = create_app()

        def dependencies(node):
            yield node.call
            for child in node.dependencies:
                yield from dependencies(child)

        prefix = '/api/bases/{business_database_id}'
        found = {}
        route_positions = {}
        for position, route in enumerate(app.routes):
            path = getattr(route, 'path', '')
            if not path.startswith(prefix):
                continue
            suffix = path.removeprefix(prefix)
            calls = tuple(dependencies(route.dependant))
            for method in route.methods:
                key = (method, suffix)
                route_positions[key] = position
                if key not in LEVEL_A_ROUTE_CONTRACT:
                    continue
                permissions = {
                    inspect.getclosurevars(call).nonlocals.get('permission')
                    for call in calls
                    if inspect.isfunction(call)
                    and 'permission' in inspect.getclosurevars(call).nonlocals
                }
                found[key] = (calls, permissions)

        self.assertEqual(set(found), set(LEVEL_A_ROUTE_CONTRACT))
        for key, expected_permission in LEVEL_A_ROUTE_CONTRACT.items():
            with self.subTest(method=key[0], path=key[1]):
                calls, permissions = found[key]
                self.assertNotIn(database.get_connection, calls)
                self.assertNotIn(database.get_write_connection, calls)
                self.assertEqual(permissions, {expected_permission})

        for static_path, dynamic_path in (
            ('/desordres/search', '/desordres/{desordre_id}'),
            ('/desordres/selection', '/desordres/{desordre_id}'),
            ('/vegetation/relations/options', '/vegetation/{identifier}'),
            ('/amenagements-hydrauliques/relations/options', '/amenagements-hydrauliques/{identifier}'),
            ('/zones-stockage/relations/options', '/zones-stockage/{identifier}'),
            ('/prestations/relations/options', '/prestations/{identifier}'),
        ):
            self.assertLess(route_positions[('GET', static_path)],
                            route_positions[('GET', dynamic_path)])

    def test_expected_context_is_checked_before_business_connection(self):
        with patch.dict('os.environ', LEGACY_SINGLE_DATABASE_ENABLED='false'):
            app = create_app()
        target = context()
        app.dependency_overrides[auth.get_auth_settings] = lambda: auth.AuthSettings('https://testserver', True)
        app.dependency_overrides[auth.require_authenticated_user] = lambda: MagicMock(id=target.user_id)
        with patch.object(bc, 'resolve_business_context', return_value=target), \
                patch.object(bc, 'open_control_connection') as control, \
                patch.object(database, 'open_read_connection') as opened, \
                TestClient(app, base_url='https://testserver') as client:
            instance = uuid4()
            control.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (instance,)
            for values in ({'context_user_id': str(uuid4())},
                           {'context_user_id': str(target.user_id), 'context_instance_id': str(instance), 'context_data_epoch': str(uuid4())}):
                response = client.get(f'/api/bases/{target.business_database_id}/troncons/options', params=values)
                self.assertEqual(response.status_code, 409, response.text)
            opened.assert_not_called()

    def test_runtime_namespaces_urls_references_late_responses_and_stop(self):
        root = Path(__file__).resolve().parents[1] / 'frontend' / 'js'
        program = r'''
const assert = require('node:assert/strict');
const {createContext, createRuntime} = require(ROOT + '/business-context.js');
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const catalog = {instance_id:id(1), user_id:id(2), databases:[{business_database_id:id(3),slug:'cabbalr1',data_epoch:id(4),status:'active',permissions:['business.read']}]};
const context = createContext(catalog,id(3));
assert(Object.isFrozen(context)); assert(Object.isFrozen(context.permissions));
assert.throws(()=>createContext(catalog,id(9)));
const calls=[];
const a=createRuntime({context,origin:'https://digues.test',fetchImpl:async(...args)=>{calls.push(args);return new Response('{}');}});
assert.equal(a.databaseName,`digues-app-field-projection:${id(1)}:${id(2)}:${id(3)}:${id(4)}`);
assert.equal(a.coordinationKey('offline-replay'),`digues-app:offline-replay:${id(1)}:${id(2)}:${id(3)}:${id(4)}`);
const b=createRuntime({context:createContext({...catalog,user_id:id(9)},id(3)),origin:'https://digues.test',fetchImpl:fetch});
assert.notEqual(a.databaseName,b.databaseName);
assert.equal(a.pageUrl('/carte'),'/carte?base=cabbalr1');
assert.equal(a.pageUrl('/carte?lat=45.1&lon=2.2&zoom=12'),'/carte?lat=45.1&lon=2.2&zoom=12&base=cabbalr1');
assert.equal(a.pageUrl('/base'),'/base?base=cabbalr1');
assert.throws(()=>a.apiUrl(`/api/bases/${id(8)}/photos/1`));
assert.equal(a.apiUrl('/api/auth/me'),'/api/auth/me');
(async()=>{
 await a.fetch('/api/observations',{method:'POST',body:'{}'});
 assert(calls[0][0].startsWith(`/api/bases/${id(3)}/observations?`));
 assert.equal(calls[0][1].headers.get('X-Digues-CSRF'),'1');
 assert.equal(new URL(calls[0][0],'https://digues.test').searchParams.get('context_user_id'),id(2));
 const References=require(ROOT + '/reference-data.js');
 globalThis.BusinessRuntime=a;
 assert.equal((await References.load('rives',async()=>({references:[{id:'r',libelle:'Alpha'}]})))[0].libelle,'Alpha');
 globalThis.BusinessRuntime=b;
 assert.equal((await References.load('rives',async()=>({references:[{id:'r',libelle:'Beta'}]})))[0].libelle,'Beta');
 let finish;
 const slow=createRuntime({context,origin:'https://digues.test',fetchImpl:()=>new Promise(resolve=>finish=resolve)});
 const pending=slow.fetch('/api/troncons'); slow.stop(); finish(new Response('{}'));
 await assert.rejects(pending,{name:'AbortError'});
 assert.throws(()=>slow.apiUrl('/api/troncons'),{name:'AbortError'});
 const legacy=createRuntime({legacy:true,origin:'http://localhost',fetchImpl:fetch});
 assert.equal(legacy.databaseName,'digues-app-field-projection');
 assert.equal(legacy.apiUrl('/api/troncons'),'/api/troncons');
 assert.throws(()=>legacy.apiUrl(`/api/bases/${id(3)}/troncons`));
 assert.throws(()=>createRuntime({legacy:true,context}));
 let revision='initial';
 const fenced=createRuntime({context,origin:'https://digues.test',fetchImpl:fetch,isCurrent:()=>revision==='initial'});
 revision='logout';
 assert.equal(fenced.isActive(),false);
 assert.throws(()=>fenced.apiUrl('/api/troncons'),{name:'AbortError'});
})();
'''.replace('ROOT', json.dumps(str(root)))
        subprocess.run(['node', '-e', program], check=True, timeout=30, capture_output=True, text=True)

    def test_runtime_reports_network_observations_without_changing_fetch_contract(self):
        root = Path(__file__).resolve().parents[1] / 'frontend' / 'js'
        program = r'''
const assert = require('node:assert/strict');
const {createContext, createRuntime} = require(ROOT + '/business-context.js');
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const catalog = {instance_id:id(1), user_id:id(2), databases:[{
  business_database_id:id(3), slug:'cabbalr1', data_epoch:id(4), status:'active', permissions:['business.read'],
}]};
const context = createContext(catalog,id(3));
let next, nativeOptions, unauthorized = 0;
const fetchImpl = async (_url, options) => {
  nativeOptions = options;
  if (next instanceof Error) throw next;
  if (typeof next === 'function') return next(options);
  return new Response('{}', { status: next });
};
const runtime = createRuntime({ context, origin:'https://digues.test', fetchImpl,
  onUnauthorized: () => unauthorized++ });
const starts = [], results = [];
const detach = runtime.setConnectivityObserver({
  start(metadata) { starts.push(metadata); return { index: starts.length }; },
  result(handle, observation) { results.push({ handle, observation }); },
});
(async()=>{
  for (const status of [200, 403, 404, 422, 500, 502]) {
    next = status;
    const response = await runtime.fetch('/api/troncons');
    assert.equal(response.status, status);
    assert.equal(results.at(-1).observation.outcome, 'response');
    assert.equal(results.at(-1).observation.status, status);
    assert.equal(results.at(-1).observation.contextKey, context.key);
    assert.equal(results.at(-1).observation.generation, 1);
    assert.equal(results.at(-1).observation.scope, 'business');
    assert.equal(typeof results.at(-1).observation.startedAt, 'number');
  }
  next = 401;
  await assert.rejects(runtime.fetch('/api/troncons'), { name: 'AbortError' });
  assert.equal(unauthorized, 1);
  assert.equal(results.at(-1).observation.status, 401);

  const beforeSuppressed = starts.length;
  next = 200;
  assert.equal((await runtime.fetch('/api/resource-events/head', { connectivityObservation: false })).status, 200);
  assert.equal(starts.length, beforeSuppressed);
  assert.equal('connectivityObservation' in nativeOptions, false);

  for (const [error, outcome] of [
    [new TypeError('Failed to fetch'), 'transport-error'],
    [new DOMException('Timed out', 'TimeoutError'), 'timeout'],
    [new DOMException('Cancelled', 'AbortError'), 'abort'],
  ]) {
    next = error;
    await assert.rejects(runtime.fetch('/api/troncons'), value => value === error);
    assert.equal(results.at(-1).observation.outcome, outcome);
  }

  next = 200;
  await runtime.fetch('https://tiles.example.test/tile');
  assert.equal(results.at(-1).observation.scope, 'peripheral');
  detach();
  const detachedCount = starts.length;
  await runtime.fetch('/api/troncons');
  assert.equal(starts.length, detachedCount);

  let finish;
  const slowResults = [];
  const slow = createRuntime({ context, origin:'https://digues.test',
    fetchImpl: () => new Promise(resolve => { finish = resolve; }) });
  slow.setConnectivityObserver({ start: metadata => metadata,
    result: (_handle, observation) => slowResults.push(observation) });
  const pending = slow.fetch('/api/troncons');
  slow.stop(); finish(new Response('{}'));
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(slowResults.at(-1).outcome, 'abort');
  assert.equal(slowResults.at(-1).contextKey, context.key);

  const resilient = createRuntime({ context, origin:'https://digues.test',
    fetchImpl: async () => new Response('{}') });
  resilient.setConnectivityObserver({ start() { throw new Error('diagnostic'); },
    result() { throw new Error('diagnostic'); } });
  assert.equal((await resilient.fetch('/api/troncons')).status, 200);
})();
'''.replace('ROOT', json.dumps(str(root)))
        subprocess.run(['node', '-e', program], check=True, timeout=30,
                       capture_output=True, text=True)
