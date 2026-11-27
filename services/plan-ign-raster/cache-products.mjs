import { readFile } from 'node:fs/promises';

const ID = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const DIRECTORY = /^[a-z][a-z0-9-]*(?:\/[a-z][a-z0-9-]*)*$/;
const FIELDS = new Set(['id', 'display_name', 'scope', 'policy', 'directory', 'resources', 'resource_max_age_seconds', 'tile_max_age_seconds',
  'eviction', 'retain_on_error', 'http_max_age_seconds', 'artifact']);
const REQUIRED = [...FIELDS].filter((key) => key !== 'artifact');
const MAX = Number.MAX_SAFE_INTEGER;
const integer = (value, min, max = MAX) => Number.isSafeInteger(value) && value >= min && value <= max;

export function parseCacheProducts(document) {
  if (!document || typeof document !== 'object' || Array.isArray(document) ||
      Object.keys(document).some((key) => !['contract_version', 'min_free_bytes', 'capacity_bytes', 'products'].includes(key)) ||
      document.contract_version !== 1 || (!integer(document.min_free_bytes, 1) || !integer(document.capacity_bytes, 1)) ||
      !Array.isArray(document.products) || !document.products.length) {
    throw new Error('Invalid cartographic cache registry');
  }
  const products = new Map();
  const directories = [];
  for (const product of document.products) {
    if (!product || typeof product !== 'object' || Array.isArray(product) ||
        Object.keys(product).some((key) => !FIELDS.has(key)) ||
        !REQUIRED.every((key) => Object.hasOwn(product, key)) ||
        !ID.test(product.id) || products.has(product.id) ||
        typeof product.display_name !== 'string' || !product.display_name.trim() ||
        product.scope !== 'global' ||
        !['persistent-reconstructible', 'planned-reconstructible'].includes(product.policy) ||
        !DIRECTORY.test(product.directory) ||
        typeof product.resources !== 'boolean' ||
        !integer(product.resource_max_age_seconds, 0) ||
        !integer(product.tile_max_age_seconds, 0) ||
        product.eviction !== 'lru' || typeof product.retain_on_error !== 'boolean' ||
        !integer(product.http_max_age_seconds, 0, 31_536_000) ||
        (product.artifact !== undefined && (!product.artifact ||
          typeof product.artifact !== 'object' || Array.isArray(product.artifact) ||
          Object.keys(product.artifact).sort().join(',') !== 'format,generation,grid_id' ||
          !ID.test(product.artifact.format) || !ID.test(product.artifact.generation) ||
          !ID.test(product.artifact.grid_id))) ||
        (product.tile_max_age_seconds > 0 &&
          product.http_max_age_seconds > product.tile_max_age_seconds)) {
      throw new Error('Invalid cartographic cache product');
    }
    directories.push(product.directory);
    products.set(product.id, Object.freeze({ ...product,
      ...(product.artifact && { artifact: Object.freeze({ ...product.artifact }) }) }));
  }
  if (directories.some((a, i) => directories.some((b, j) => i !== j &&
      (a === b || a.startsWith(`${b}/`))))) {
    throw new Error('Overlapping cartographic cache directories');
  }
  return { minFreeBytes: document.min_free_bytes, capacityBytes: document.capacity_bytes, products };
}

export async function loadCacheProducts(filename) {
  return parseCacheProducts(JSON.parse(await readFile(filename, 'utf8')));
}

// The older basemap file still carries its cache declaration for backend
// validation. Refuse drift until that declaration is removed in a coordinated
// contract change; the technical registry is the storage authority.
export function assertBasemapProductCompatibility(basemaps, cacheProducts, legacyMinFreeBytes) {
  if (legacyMinFreeBytes !== cacheProducts.minFreeBytes) {
    throw new Error('Basemap cache disk reserve differs from cartographic product registry');
  }
  for (const [id, oldPolicy] of basemaps) {
    const product = cacheProducts.products.get(id);
    if (!product || product.scope !== 'global' || Object.entries(oldPolicy).some(
      ([key, value]) => product[key] !== value)) {
      throw new Error(`Basemap cache policy differs from cartographic product ${id}`);
    }
  }
}
