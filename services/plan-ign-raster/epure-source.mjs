import { STYLE_URL, METADATA_URL } from './source.mjs';
function templatePattern(template, fields) {
  let pattern = template.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const field of fields) pattern = pattern.replace(`\\{${field}\\}`, '([^/?#]+)');
  return new RegExp(`^${pattern}$`);
}

// Match the actual validated templates, never classify by extension (.pbf also
// means glyphs; .json also means sprites). Unknown resources fail closed.
export function epureResourceClassifier(style, metadata) {
  const mvt = templatePattern(metadata.tiles[0], ['z', 'x', 'y']);
  const glyph = templatePattern(style.glyphs, ['fontstack', 'range']);
  return value => {
    const url = new URL(value);
    if (url.username || url.password || url.search || url.hash) throw new Error('Unexpected source resource');
    if (value === STYLE_URL || value === METADATA_URL) return 'json';
    const tile = mvt.exec(value);
    if (tile) {
      const xyz = tile.slice(1).map(Number);
      if (tile.slice(1).some(v => !/^\d+$/.test(v)) ||
          xyz.some(v => !Number.isSafeInteger(v)) || xyz[0] < metadata.minzoom ||
          xyz[0] > metadata.maxzoom || xyz[1] >= 2 ** xyz[0] || xyz[2] >= 2 ** xyz[0]) {
        throw new Error('Invalid source tile');
      }
      return 'mvt';
    }
    if (glyph.test(value)) return 'glyph';
    if (['.json', '.png', '@2x.json', '@2x.png'].some(s => value === style.sprite + s)) return 'sprite';
    throw new Error('Unknown source resource');
  };
}


// Native source revisions recover failed warm acquisitions; disk identities stay
// geographic and global. Only the serialized Native owner changes this state.
export function createEpureSource({ generation, version, style, metadata, resources }) {
  if (Object.keys(style.sources).join(',') !== 'plan_ign') throw new Error('Unexpected geographic source');
  const classify = epureResourceClassifier(style, metadata);
  let revision = 0, current = null;
  const pending = new Set(), failures = [];
  function prepared() {
    if (current) return current;
    const prefix = `digues-mvt://${generation}/${version}/r${revision}`;
    const id = `plan_ign_r${revision}`;
    const sourceStyle = structuredClone(style);
    sourceStyle.sources = { [id]: { ...sourceStyle.sources.plan_ign,
      tiles: [`${prefix}/{z}/{x}/{y}.pbf`] } };
    for (const layer of sourceStyle.layers) if (layer.source === 'plan_ign') layer.source = id;
    current = { prefix, style: sourceStyle };
    return current;
  }
  function get(value) {
    const task = (async () => {
      if (value.startsWith('digues-mvt://')) {
        const source = prepared();
        const match = /^(digues-mvt:\/\/[^?#]+)\/(\d+)\/(\d+)\/(\d+)\.pbf$/.exec(value);
        if (!match || match[1] !== source.prefix) throw new Error('Unknown Native source revision');
        value = metadata.tiles[0].replace('{z}', match[2]).replace('{x}', match[3]).replace('{y}', match[4]);
        if (classify(value) !== 'mvt') throw new Error('Invalid MVT');
      } else classify(value);
      return resources.get(value);
    })();
    pending.add(task);
    void task.catch(error => failures.push(error));
    void task.finally(() => pending.delete(task)).catch(() => {});
    return task;
  }
  return { prepared, get, invalidate() { revision++; current = null; },
    async drain() {
      while (pending.size) await Promise.allSettled([...pending]);
      const error = failures[0]; failures.length = 0;
      if (error) throw error;
    } };
}
