// Isolated DOM and read responses for controller tests; never a security or offline proof.
const Builder = require('../frontend/js/data-view-builder.js');
const Default = require('../frontend/js/data-view-default.js');
class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.hidden = false; this.value = ''; this.textContent = ''; this.scrollTop = 0;
    this.scrollHeight = 1000; this.clientHeight = 400; this.attributes = {};
    this.style = {setProperty() {}};
  }
  append(...children) {
    this.children.push(...children);
    if (this.tag === 'select' && !this.value && children[0]) this.value = children[0].value;
  }
  replaceChildren(...children) { this.children = []; this.value = ''; this.append(...children); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getBoundingClientRect() { return {height: 144}; }
}
function createHarness({fetchOverride = null, paginate = false, hiddenTronconIds = new Set()} = {}) {
  const selectors = ['data-view-builder-workspace', 'data-view-root', 'data-view-table',
    'data-view-field', 'data-view-add-field', 'data-view-field-list', 'data-view-resizer',
    'data-view-result-table', 'data-view-result-head', 'data-view-result-body',
    'data-view-result-empty', 'data-view-result-status', 'data-view-export-bar'];
  const elements = Object.fromEntries(selectors.map(id => [id,
    new Element(['data-view-root', 'data-view-table', 'data-view-field'].includes(id) ? 'select' : 'div')]));
  elements.builder = new Element(); elements.scroll = new Element();
  elements['data-view-builder-workspace'].hidden = true;
  const rootElement = {ownerDocument: {createElement: tag => new Element(tag)},
    querySelector: selector => selector === '.data-view-builder' ? elements.builder
      : selector === '.data-view-result-scroll' ? elements.scroll : elements[selector.slice(1)] || null};
  let catalog = Default.catalog();
  catalog.local_only = false;
  catalog.tables.at(-1).fields.push({id: 'type', label: 'Type', type: 'text',
    source_class: 'desordres', source_field: 'type', navigable: false});
  const requests = [], datasets = [], opened = [];
  const controller = Builder.createController({rootElement, hiddenTronconIds,
    onDatasetChange: dataset => datasets.push(dataset),
    onOpenEntity: (...args) => opened.push(args),
    fetchJson: async (url, options = {}) => {
      requests.push({url, ...(options.body ? JSON.parse(options.body) : {})});
      if (fetchOverride) {
        const response = await fetchOverride(url, options);
        if (response !== undefined) return response;
      }
      if (url.endsWith('view-catalog')) return catalog;
      const query = JSON.parse(options.body);
      const entities = Default.definition().fields.map(({table, field}) => ({class: table,
        id: table + '-1', attributes: {[field]: table + ' A'}}));
      return {root: query.root, granularity: query.root,
        columns: query.fields.map(({table, field}) => {
          const metadata = catalog.tables.find(item => item.id === table).fields.find(item => item.id === field);
          return {key: `${table}.${field}`, label: metadata.label, source_class: table,
            source_field: field, navigable: metadata.navigable};
        }),
        rows: [{entities, values: query.fields.map(({field}) => field === 'type' ? null : 'label')}],
        offset: query.offset, total: paginate ? 2 : 1, has_next: paginate && query.offset === 0};
    }});
  const click = id => elements[id].listeners.click();
  const remove = index => elements['data-view-field-list'].children[index].children[0].listeners.click();
  const queries = () => requests.filter(item => item.url.endsWith('view-query'));
  const select = (table, field) => {
    elements['data-view-table'].value = table;
    elements['data-view-table'].listeners.change();
    elements['data-view-field'].value = field; click('data-view-add-field');
  };
  return {controller, elements, requests, datasets, opened, click, remove, queries, select,
    setCatalog: value => {catalog = value;}};
}
module.exports = {Builder, Default, createHarness, settle: () => new Promise(resolve => setImmediate(resolve))};
