import json
import shutil
import subprocess
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class EntityNavigationTest(unittest.TestCase):
    def test_navigation_starts_after_all_business_editors_have_registered(self):
        bootstrap = (FRONTEND / 'js/business-bootstrap.js').read_text()
        map_script = (FRONTEND / 'js/map.js').read_text()
        self.assertNotIn('void MapEntityNavigation.start();', map_script)
        self.assertGreater(bootstrap.index('root.MapEntityNavigation?.start()'),
                           bootstrap.index('placeholder.replaceWith(script)'))

    def test_url_selection_uses_canonical_category_without_changing_technical_resources(self):
        script = (FRONTEND / 'js/map.js').read_text()
        source = 'function setMapUrlSelection' + script.split('function setMapUrlSelection', 1)[1].split('function focusMapEntity', 1)[0]
        program = "const assert=require('node:assert/strict');\n" + (
            f"const EntityNavigation=require({json.dumps(str(FRONTEND / 'js/entity-navigation.js'))});\n"
            "let selectedMapUrlTarget; function syncMapViewportUrl(){}\n") + source + """
const id='00000000-0000-4000-8000-000000000001';
for (const [resource,category] of [['amenagements-hydrauliques','amenagements_hydrauliques'],
  ['reseaux-techniques','reseaux_techniques'],['equipements-mesure','equipements_mesure'],
  ['ouvrages-particuliers','ouvrages_particuliers'],['vegetation','vegetation'],['prestations','prestations']]) {
  setMapUrlSelection(resource,id); assert.equal(selectedMapUrlTarget.entityClass,category);
}
"""
        subprocess.run(['node', '-e', program], check=True, timeout=30, capture_output=True, text=True)

    def test_unsupported_incoming_category_releases_url_guard_without_opening_an_editor(self):
        script = (FRONTEND / 'js/map.js').read_text()
        source = ('async function openInitialMapEntity' +
                  script.split('async function openInitialMapEntity', 1)[1]
                  .split('window.MapEntityNavigation', 1)[0])
        program = """
const assert = require('node:assert/strict');
let mapEntityNavigationStarted=true, mapEntityNavigationPending=false,
    mapEntityNavigationComplete=false, initialEntityTargetError=null, mapEntityNavigationStatus;
const initialEntityTarget={entityClass:'vegetation',id:'00000000-0000-4000-8000-000000000001'};
const mapEntityEditors=new Map();
let synchronized=0, reported=0;
function syncMapViewportUrl(){synchronized++;}
function beginAppStatus(scope,level,message){assert.equal(level,'error');reported++;}
""" + source + """
(async()=>{
  assert.equal(await openInitialMapEntity(),false);
  assert.equal(mapEntityNavigationComplete,true);
  assert.equal(mapEntityNavigationPending,false);
  assert.equal(synchronized,1); assert.equal(reported,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        subprocess.run(['node', '-e', program], check=True, timeout=30, capture_output=True, text=True)

    def test_map_reads_incoming_coordinates_independently_of_url_mode(self):
        script = (FRONTEND / "js" / "map.js").read_text(encoding="utf-8")
        source = ("function mapViewportFromUrl"
                  + script.split("function mapViewportFromUrl", 1)[1]
                    .split("const initialMapViewport", 1)[0])
        program = source + """
const assert = require('node:assert/strict');
assert.deepEqual(mapViewportFromUrl('?base=cabbalr1&lat=43.62&lon=4.71&zoom=16'),
  {latitude:43.62,longitude:4.71,zoom:16});
assert.equal(mapViewportFromUrl('?lat=95&lon=4.71&zoom=16'),null);
"""
        subprocess.run(["node", "-e", program], check=True, timeout=30, capture_output=True, text=True)

    def test_automatic_address_modes_do_not_restrict_incoming_links(self):
        module = FRONTEND / "js" / "entity-navigation.js"
        program = f"""
const assert = require('node:assert/strict');
const N = require({json.dumps(str(module))});
const id = '00000000-0000-4000-8000-000000000701';
const href = '/carte?base=cabbalr1&lat=43.62&lon=4.71&zoom=16&entity=vegetation&id='+id;
const position = {{lat:43.62,lon:4.71,zoom:16}};
const selected = {{entityClass:'vegetation',id}};
assert.equal(N.fromSearch(href.split('?')[1]).id,id);
assert.equal(N.automaticUrl(href,{{view:'map',mode:'minimal',position,selected}}),'/carte?base=cabbalr1');
assert.equal(N.automaticUrl(href,{{view:'map',mode:'position',position,selected}}),
  '/carte?base=cabbalr1&lat=43.620000&lon=4.710000&zoom=16');
assert.equal(N.automaticUrl(href,{{view:'map',mode:'object',position,selected}}),
  '/carte?base=cabbalr1&lat=43.620000&lon=4.710000&zoom=16&entity=vegetation&id='+id);
assert.equal(N.automaticUrl(href,{{view:'map',mode:'object',selected}}),
  '/carte?base=cabbalr1&entity=vegetation&id='+id);
assert.equal(N.automaticUrl('/base?base=cabbalr1&lat=2&entity=vegetation&id='+id,
  {{view:'data',mode:'object',position,selected}}),'/base?base=cabbalr1&entity=vegetation&id='+id);
assert.equal(N.automaticUrl(href,{{view:'map',mode:'object',position,selected:{{entityClass:'desordres',id}}}}),
  '/carte?base=cabbalr1&lat=43.620000&lon=4.710000&zoom=16');
assert.equal(N.automaticUrl('/base?base=cabbalr1&entity=vegetation',{{view:'data',mode:'minimal'}}),
  '/base?base=cabbalr1');
"""
        subprocess.run(["node", "-e", program], check=True, timeout=30, capture_output=True, text=True)

    def test_explicit_category_contract_and_business_base(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND / "js" / "entity-navigation.js"
        program = f"""
const Navigation = require({json.dumps(str(module))});
const id = '00000000-0000-4000-8000-000000000701';
const runtime = {{pageUrl(path) {{ return path + '?base=cabbalr1'; }}}};
const urls = Navigation.ENTITY_CLASSES.map(entityClass => Navigation.mapUrl(entityClass, id, {{runtime, origin:'https://example.test'}}));
const parsed = Navigation.fromSearch('?base=x&entity=vegetation&id=' + id);
const errors = [];
for (const search of ['?entity=vegetation', '?entity=unknown&id=' + id, '?entity=vegetation&id=no']) {{
  try {{ Navigation.fromSearch(search); }} catch (error) {{ errors.push(error.message); }}
}}
process.stdout.write(JSON.stringify({{classes:Navigation.ENTITY_CLASSES, urls, parsed, errors}}));
"""
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(len(result["classes"]), 14)
        self.assertEqual(len(set(result["classes"])), 14)
        self.assertIn("systemes", result["classes"])
        self.assertIn("digues", result["classes"])
        self.assertIn("zones_stockage", result["classes"])
        self.assertIn("troncons", result["classes"])
        self.assertIn("troncons_lit", result["classes"])
        self.assertIn("elements_morphologiques_lit", result["classes"])
        self.assertEqual(result["parsed"], {
            "entityClass": "vegetation",
            "id": "00000000-0000-4000-8000-000000000701",
        })
        for entity_class, url in zip(result["classes"], result["urls"]):
            self.assertEqual(
                url,
                "/carte?base=cabbalr1"
                f"&entity={entity_class}&id=00000000-0000-4000-8000-000000000701",
            )
        self.assertEqual(len(result["errors"]), 3)

    def test_entry_points_and_precache_load_the_shared_contract(self):
        data = (FRONTEND / "data.html").read_text(encoding="utf-8")
        map_page = (FRONTEND / "map.html").read_text(encoding="utf-8")
        worker = (FRONTEND / "service-worker.js").read_text(encoding="utf-8")
        resource = '/static/js/entity-navigation.js'
        self.assertIn(resource, data)
        self.assertIn(resource, map_page)
        self.assertIn(f'"{resource}"', worker)
        # Shared View now owns the tabs/observations DOM, mounted in this host.
        # C5's real browser test exercises their controls after canonical loading.
        self.assertIn('id="data-entity-detail"', data)
        # The map action is also created by DataEntityDetail.createHost; its
        # actual navigation is exercised in test_troncons_c5_browser_postgres.
        self.assertIn('/static/js/data-entity-detail.js', data)
        for name in ('entity-form-view.js', 'troncon-form.js', 'troncon-reperage.js'):
            self.assertIn(name, data)
            self.assertIn(name, map_page)
            self.assertIn(f'"/static/js/{name}"', worker)


if __name__ == "__main__":
    unittest.main()
