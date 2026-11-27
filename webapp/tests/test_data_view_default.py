"""STAT-2, niveau A, groupe webapp : état isolé par scénario, aucun SQL/stockage.

Contrats du preset, champs invalides, cycle visible/masqué, pagination et liens.
Les fixtures API/DOM sont des doubles ; la preuve offline est le scénario réel
offline-projection.spec.js avec transport coupé et IndexedDB. Coût ~5 secondes.
Les assertions utiles de DataDisorders sont transférées ici et au test de scroll
existant du constructeur ; le masquage des colonnes constantes est abandonné.
"""
import json
import subprocess
import unittest


class DefaultViewTest(unittest.TestCase):
    def scenario(self, source):
        program = """
const assert = require('node:assert/strict');
const {Builder, Default, createHarness, settle} = require('./webapp/tests/view-builder-harness.js');
(async () => { __SCENARIO__ })().catch(error => {console.error(error); process.exitCode=1;});
""".replace("__SCENARIO__", source)
        result = subprocess.run(['node', '-e', program], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_default_metadata_matches_server_and_rejects_changed_definitions(self):
        from digues_webapp.data_views import view_catalog, HOME_VIEW_FIELDS, _columns, _selected_fields
        output = subprocess.check_output(['node', '-e', """
const D=require('./webapp/frontend/js/data-view-default.js');
process.stdout.write(JSON.stringify({catalog:D.catalog(),columns:D.columns()}));
"""], text=True)
        local = json.loads(output)
        server = view_catalog()
        self.assertEqual(local['catalog']['default_view'], server['default_view'])
        self.assertEqual(local['columns'], _columns(_selected_fields(list(HOME_VIEW_FIELDS))))
        for table in local['catalog']['tables']:
            authoritative = next(item for item in server['tables'] if item['id'] == table['id'])
            self.assertEqual(table['label'], authoritative['label'])
            self.assertIn(table['fields'][0], authoritative['fields'])
        self.scenario(r'''
const preset = Default.definition();
assert.equal(Default.matches(preset), true);
assert.equal(Default.matches({...preset, hidden_troncon_ids:['t9'],offset:1,page_size:1}), true);
assert.equal(Default.matches({...preset,root:'systemes'}), false);
assert.equal(Default.matches({...preset,fields:preset.fields.slice(1)}), false);
assert.equal(Default.matches({...preset,fields:[...preset.fields].reverse()}), false);
const catalog = Default.catalog();
catalog.default_view.fields[0].field = 'absent';
assert.throws(() => Builder.normalizeCatalog(catalog), /absente du catalogue/);
''')

    def test_first_open_loads_one_ordered_preset_with_disorder_root_and_links(self):
        self.scenario(r'''
const h = createHarness();
assert.equal(h.requests.length,0);
await h.controller.setHiddenTronconIds(new Set(['t9']));
assert.equal(h.requests.length,0);
await h.controller.setOpen(true); await settle();
assert.equal(h.queries().length,1);
assert.deepEqual(h.queries()[0], {url:'/api/data/view-query',...Default.definition(),
  hidden_troncon_ids:['t9'],offset:0,page_size:500});
assert.equal(h.elements['data-view-field-list'].children.length,4);
assert.equal(h.elements['data-view-result-head'].children.length,4); // no conditional hierarchy hiding
const row=h.elements['data-view-result-body'].children[0];
row.children[3].children[0].listeners.click();
assert.equal(h.opened[0][0],'desordres'); assert.equal(h.opened[0][1],'desordres-1');
assert.equal(h.elements['data-view-export-bar'].hidden,false);
assert.deepEqual(h.datasets.at(-1).fields,Default.definition().fields);
assert.ok(h.datasets.at(-1).entityClasses.includes('desordres'));
h.controller.setOpen(true); await settle(); assert.equal(h.queries().length,1);
''')

    def test_customization_and_intentionally_empty_selection_survive_reopening(self):
        self.scenario(r'''
const h=createHarness(); await h.controller.setOpen(true); await settle();
h.select('desordres','type'); await settle();
assert.equal(h.queries().at(-1).fields.length,5);
assert.equal(h.elements['data-view-result-body'].children[0].children[4].textContent,'—');
h.remove(0); await settle();
const fields=h.datasets.at(-1).fields, count=h.queries().length;
h.controller.setOpen(false); await h.controller.setOpen(true); await settle();
assert.equal(h.queries().length,count); assert.deepEqual(h.datasets.at(-1).fields,fields);
while(h.elements['data-view-field-list'].children.length){h.remove(0);await settle();}
assert.equal(h.elements['data-view-result-table'].hidden,true);
assert.equal(h.elements['data-view-export-bar'].hidden,true);
assert.match(h.elements['data-view-result-empty'].textContent,/Ajoutez un champ/);
const emptyCount=h.queries().length;
h.controller.setOpen(false); await h.controller.setOpen(true); await settle();
assert.equal(h.elements['data-view-field-list'].children.length,0);
assert.equal(h.queries().length,emptyCount);
h.select('troncons','libelle'); await settle(); assert.equal(h.queries().at(-1).root,'troncons');
''')

    def test_hidden_invalidation_and_heritage_changes_defer_one_refresh(self):
        self.scenario(r'''
const h=createHarness(); await h.controller.setOpen(true); await settle();
for(const resource of ['systemes','digues','troncons','desordres']) assert.equal(h.controller.dependsOn(resource),true);
assert.equal(await h.controller.invalidate({resource:'photos'}),false);
h.controller.setOpen(false); const count=h.queries().length;
await h.controller.invalidate({resource:'troncons'});
await h.controller.invalidate({resource:'desordres'});
await h.controller.setHiddenTronconIds(new Set(['t9']));
assert.equal(h.queries().length,count);
await h.controller.setOpen(true); await settle();
assert.equal(h.queries().length,count+1);
assert.deepEqual(h.queries().at(-1).hidden_troncon_ids,['t9']);
await h.controller.setHiddenTronconIds(new Set(['t9'])); assert.equal(h.queries().length,count+1);
await h.controller.setHiddenTronconIds(new Set()); assert.equal(h.queries().length,count+2);
h.remove(3); await settle(); assert.equal(h.queries().at(-1).root,'systemes');
''')

    def test_scroll_is_deduplicated_and_hidden_refresh_preserves_loaded_rows(self):
        self.scenario(r'''
let release;
const h=createHarness({paginate:true,fetchOverride:async(url,options)=>{
  if(options.body && JSON.parse(options.body).offset===1) await new Promise(resolve=>{release=resolve;});
}});
await h.controller.setOpen(true); await settle();
h.elements.scroll.scrollTop=500;
h.elements.scroll.listeners.scroll(); h.elements.scroll.listeners.scroll(); await settle();
assert.equal(h.queries().length,2);
release(); await settle();
assert.equal(h.elements['data-view-result-body'].children.length,2);
h.elements.scroll.listeners.scroll(); await settle(); assert.equal(h.queries().length,2);
h.controller.setOpen(false); await h.controller.invalidate({resource:'desordres'});
const reopening=h.controller.setOpen(true); await settle();
assert.equal(h.queries().at(-1).offset,1); release(); await reopening;
assert.equal(h.elements['data-view-result-body'].children.length,2);
assert.equal(h.elements.scroll.scrollTop,500);
''')

    def test_heritage_can_mutate_its_own_set_before_notifying_the_builder(self):
        self.scenario(r'''
const hidden=new Set(),h=createHarness({hiddenTronconIds:hidden});
await h.controller.setOpen(true);await settle();
hidden.add('t9');await h.controller.setHiddenTronconIds(hidden);
assert.equal(h.queries().length,2);
assert.deepEqual(h.queries().at(-1).hidden_troncon_ids,['t9']);
hidden.clear();await h.controller.setHiddenTronconIds(hidden);
assert.equal(h.queries().length,3);
assert.deepEqual(h.queries().at(-1).hidden_troncon_ids,[]);
''')

    def test_closed_during_catalog_load_queries_only_after_reopening(self):
        self.scenario(r'''
let release;
const h=createHarness({fetchOverride:async url=>{
  if(url.endsWith('view-catalog'))await new Promise(resolve=>{release=resolve;});
}});
const opening=h.controller.setOpen(true); h.controller.setOpen(false);
release(); await opening; await settle(); assert.equal(h.queries().length,0);
await h.controller.setOpen(true); await settle(); assert.equal(h.queries().length,1);
assert.equal(h.requests.filter(item=>item.url.endsWith('view-catalog')).length,1);
''')

    def test_error_retains_selection_and_retry_does_not_reapply_preset(self):
        self.scenario(r'''
let fail=false;
const h=createHarness({fetchOverride:async(url,options)=>{
  if(options.body && fail)throw new Error('Serveur indisponible');
}});
await h.controller.setOpen(true); await settle();
fail=true; h.remove(0); await settle();
assert.match(h.elements['data-view-result-empty'].textContent,/Serveur indisponible/);
assert.equal(h.elements['data-view-field-list'].children.length,3);
assert.equal(h.elements['data-view-result-table'].hidden,true);
fail=false; h.controller.setOpen(false); await h.controller.setOpen(true); await settle();
assert.equal(h.queries().at(-1).fields.length,3);
''')

    def test_reconnection_upgrades_local_catalog_without_overwriting_custom_fields(self):
        self.scenario(r'''
const handlers=new Map();globalThis.addEventListener=(name,handler)=>handlers.set(name,handler);
const h=createHarness();h.setCatalog(Default.catalog());
await h.controller.setOpen(true);await settle(); h.remove(0);await settle();
const fields=h.datasets.at(-1).fields;
h.controller.setOpen(false);
const server=Default.catalog();server.local_only=false;
server.tables.at(-1).fields.push({id:'type',label:'Type'});h.setCatalog(server);
handlers.get('digues-app:backend-reconnected')();
const count=h.requests.length;
await h.controller.setOpen(true);await settle();
assert.equal(h.requests.length,count+2);
assert.deepEqual(h.datasets.at(-1).fields,fields);
h.select('desordres','type');await settle();assert.equal(h.queries().at(-1).fields.length,4);
''')


if __name__ == '__main__':
    unittest.main()
