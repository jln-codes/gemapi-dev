import { mkdir, lstat } from 'node:fs/promises';
import path from 'node:path';
import { assertCacheTree, cacheStats } from './cache-registry.mjs';

// Descriptive facade. Legacy quota overrides are left untouched and not loaded.
export class CacheSettings {
  constructor({ cacheRoot, catalogue, registry }) {
    Object.assign(this, { cacheRoot, catalogue, registry });
  }
  async load() {
    // Never silently relax a private guard left by the old quota console.
    // An operator must review capacity/reserve in the new registry and archive
    // the obsolete file explicitly; no legacy policy is parsed or migrated.
    const legacy = await lstat(path.join(this.cacheRoot, 'cache-settings.json'))
      .catch(error => error.code === 'ENOENT' ? null : Promise.reject(error));
    if (legacy) throw new Error('Legacy cache settings require operator review of global capacity and reserve');
    await mkdir(this.cacheRoot, { recursive: true });
    for (const entry of this.registry.values()) await assertCacheTree(this.cacheRoot, entry.root);
  }
  async snapshot() {
    const products = (await cacheStats(this.registry)).products.map(entry => {
      const definition = this.catalogue.products.get(entry.id);
      return { ...entry, name: definition.display_name, scope: 'global',
        settings: { tile_max_age_seconds: definition.tile_max_age_seconds,
          resource_max_age_seconds: definition.resource_max_age_seconds,
          retain_on_error: definition.retain_on_error, eviction: 'lru' } };
    });
    return { schema_version: 3, observed_at: new Date().toISOString(),
      global: { capacity_bytes: this.catalogue.capacityBytes, reserve_bytes: this.catalogue.minFreeBytes }, products };
  }
  async storageSnapshot() {
    const budget = { remaining: 10000, deadline: Date.now() + 1000,
      step() { if (--this.remaining < 0 || Date.now() > this.deadline) throw new Error('Observation bound reached'); } };
    const scopes = [];
    let complete = true;
    for (const entry of this.registry.values()) {
      const value = { product: entry.id, kind: entry.kind, target: entry.target, categories: {} };
      for (const [name, store] of [['tiles', entry.tiles], ['resources', entry.resources]]) {
        if (!store) continue;
        try { value.categories[name] = { status: 'measured', ...await store.observe(budget) }; }
        catch { value.categories[name] = { status: 'unavailable', logical_bytes: null, physical_bytes: null }; complete = false; }
      }
      scopes.push(value);
    }
    return { contract_version: 1, observed_at: new Date().toISOString(), complete, scopes };
  }

}
