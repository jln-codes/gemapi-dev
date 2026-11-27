const assert = require('node:assert/strict');
const test = require('node:test');

class Element {
  addEventListener(name, handler) { this[name] = handler; }
  setAttribute(name,value) { this[name]=value; }
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.dataset = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  add(child) { this.children.push(child); }
}

function payload(ids) {
 const policy={tile_max_age_seconds:86400,resource_max_age_seconds:0,eviction:'lru'};
 const counters={sizeBytes:0,fileCount:0,hits:0,misses:0,readErrors:0,writeErrors:0,evictions:0,noSpace:0};
 return {status:{schema_version:3,observed_at:new Date().toISOString(),
  global:{capacity_bytes:22*1024**3,reserve_bytes:3*1024**3},
  products:ids.map(id=>({id,name:`Fond ${id}`,scope:'global',sizeBytes:0,fileCount:0,
   tiles:counters,resources:null,settings:policy}))}};
}

function mount(api) {
  const elements = Object.fromEntries(['#cache-status', '#cache-overview', '#cache-global-form',
    '#cache-funds', '#cache-refresh'].map((id) => [id, new Element('div')]));
  global.document = { getElementById: id => elements['#'+id], querySelector: (selector) => elements[selector],
    createElement: (tag) => new Element(tag) };
  global.window = { confirm: () => false };
  global.Option = class { constructor(text, value) { this.text = text; this.value = value; } };
  delete require.cache[require.resolve('../backend/digues_webapp/admin_assets/basemap-caches.js')];
  require('../backend/digues_webapp/admin_assets/basemap-caches.js');
  const controller = window.AdminBasemapCaches({ api });
  return { elements, controller };
}
async function render(ids) {
  const mounted = mount(async () => payload(ids));
  await mounted.controller.enter();
  return mounted;
}

test('admin cache description renders one table for any catalogue size and no quota controls', async () => {
 for(const ids of [['ign-epure'],['ign-epure','ign-plan','ign-ortho','ign-lidar-hd-mnt']]) {
  const {elements,controller}=await render(ids);
  const table=elements['#cache-funds'].children[0].children[0];
  assert.equal(table.tag,'table'); assert.equal(table.children[1].children.length,ids.length);
  assert.equal(elements['#cache-global-form'].children.length,0);
  controller.leave();
 }
});

// A, milliseconds: component-owned cancellation and generations must prevent a
// late response (including a transport ignoring AbortSignal) from repainting a
// new view or enabling its refresh button. No HTTP/security boundary is mocked.
test('cache lifecycle aborts, coalesces refresh and ignores stale responses on reentry', async () => {
  const requests = [];
  const { elements, controller } = mount((url, {signal}) => new Promise((resolve, reject) => {
    requests.push({url, signal, resolve, reject});
  }));
  const refresh = elements['#cache-refresh'];
  const first = controller.enter();
  await refresh.click();
  assert.equal(requests.length, 1);
  assert.equal(refresh.disabled, true);
  controller.leave();
  assert.equal(requests[0].signal.aborted, true);
  assert.equal(elements['#cache-funds'].children.length, 0);
  const second = controller.enter();
  assert.equal(requests.length, 2);
  requests[0].resolve(payload(['old'])); await first;
  assert.equal(refresh.disabled, true);
  assert.equal(elements['#cache-funds'].children.length, 0);
  requests[1].resolve(payload(['new'])); await second;
  assert.equal(refresh.disabled, false);
  const body = elements['#cache-funds'].children[0].children[0].children[1];
  assert.equal(body.children[0].children[0].textContent, 'Fond new');
  const third = refresh.click();
  requests[2].reject(new Error('Service indisponible')); await third;
  assert.match(elements['#cache-status'].textContent, /Mesures indisponibles/);
  assert.equal(refresh.disabled, false);
  controller.leave();
  assert.equal(elements['#cache-overview'].children.length, 0);
  assert.equal(elements['#cache-status'].textContent, '');
});
