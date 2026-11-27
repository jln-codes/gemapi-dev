import { readFile } from 'node:fs/promises';
import path from 'node:path';

// Read the runtime/cache subset of the same TOML catalogue validated by Python.
// Unsupported syntax in these fields fails closed; this is not a second registry.
function scalar(raw) {
  if (raw.startsWith('"') || raw.startsWith('[')) return JSON.parse(raw.replace(/,\s*\]/g, ']'));
  if (/^\d+$/.test(raw)) return Number(raw);
  if (raw === 'true' || raw === 'false') return raw === 'true';
  throw new Error(`Unsupported cache catalogue scalar: ${raw}`);
}

export function parseCachePolicies(text) {
  const result = { minFreeBytes: null, basemaps: new Map(), definitions: new Map() };
  let section = 'root';
  let current = null;
  const finish = () => {
    if (!current) return;
    const { cache } = current;
    if (!current.id || !cache.id || !cache.directory || cache.resources === undefined || cache.resource_max_age_seconds === undefined ||
        cache.http_max_age_seconds === undefined ||
        !cache.eviction || cache.retain_on_error === undefined ||
        current.id !== cache.id || result.basemaps.has(current.id)) {
      throw new Error('Incomplete or duplicate basemap cache policy');
    }
    result.basemaps.set(current.id, cache);
    result.definitions.set(current.id, current.definition);
  };
  const lines = text.split(/\r?\n/);
  for (let index = 0; index < lines.length; index++) {
    let line = lines[index].replace(/\s+#.*$/, '').trim();
    if (!line || line.startsWith('#')) continue;
    if (line === '[[basemaps]]') {
      finish();
      current = { cache: {}, definition: {} };
      section = 'basemap';
      continue;
    }
    if (/^\[.*\]$/.test(line)) {
      section = line === '[basemaps.cache]' ? 'cache' : 'other';
      continue;
    }
    while (/=\s*\[/.test(line) && (line.match(/\[/g)?.length || 0) >
        (line.match(/\]/g)?.length || 0)) {
      if (++index >= lines.length) throw new Error('Unclosed cache catalogue array');
      line += lines[index].replace(/\s+#.*$/, '').trim();
    }
    const match = /^([a-z_]+)\s*=\s*(.+)$/.exec(line);
    if (!match) {
      if (section === 'cache') throw new Error('Invalid cache catalogue entry');
      continue;
    }
    const [, name, value] = match;
    if (section === 'root' && name === 'min_free_bytes') result.minFreeBytes = scalar(value);
    if (section === 'basemap') {
      if (name === 'id') current.id = scalar(value);
      if (['id', 'display_name', 'enabled', 'adapter', 'public_tilejson_url', 'provider_endpoint',
        'provider_layer', 'provider_style', 'tile_matrix_set', 'wmts_matrix_limits',
        'wmts_publication', 'cache_generation', 'native_min_zoom', 'native_max_zoom',
        'attribution', 'tile_format'].includes(name)) current.definition[name] = scalar(value);
    }
    if (section === 'cache') current.cache[name] = scalar(value);
  }
  finish();
  if (!Number.isSafeInteger(result.minFreeBytes) || result.minFreeBytes < 1 ||
      result.basemaps.size === 0) throw new Error('Missing cache catalogue policy');
  const directories = [];
  for (const policy of result.basemaps.values()) {
    if (!/^[a-z][a-z0-9-]*(?:\/[a-z][a-z0-9-]*)*$/.test(policy.directory) ||
        typeof policy.resources !== 'boolean' ||
        !Number.isSafeInteger(policy.resource_max_age_seconds) ||
        policy.resource_max_age_seconds < 0 || policy.eviction !== 'lru' ||
        !Number.isSafeInteger(policy.tile_max_age_seconds ?? 0) ||
        (policy.tile_max_age_seconds ?? 0) < 0 ||
        !Number.isSafeInteger(policy.http_max_age_seconds) ||
        policy.http_max_age_seconds < 0 || policy.http_max_age_seconds > 31_536_000 ||
        typeof policy.retain_on_error !== 'boolean') {
      throw new Error('Invalid cache catalogue policy');
    }
    directories.push(policy.directory);
  }
  if (directories.some((a, i) => directories.some((b, j) => i !== j &&
      (a === b || a.startsWith(`${b}/`))))) throw new Error('Overlapping cache directories');
  return result;
}

export async function loadCachePolicies(filename) {
  return parseCachePolicies(await readFile(filename, 'utf8'));
}

export function basemapCacheRoot(cacheRoot, policy) {
  return path.join(cacheRoot, policy.directory);
}
