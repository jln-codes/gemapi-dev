import assert from 'node:assert/strict';
import test from 'node:test';
import { cacheVersion, validateGeneration } from './cache-identity.mjs';
import { RasterCache } from './raster-cache.mjs';

test('cache version changes with style, metadata or operator generation', () => {
  const first = cacheVersion(Buffer.from('style a'), Buffer.from('metadata a'));
  assert.match(first, /^2026-09-[0-9a-f]{16}$/);
  assert.equal(first, cacheVersion(Buffer.from('style a'), Buffer.from('metadata a')));
  assert.notEqual(first, cacheVersion(Buffer.from('style b'), Buffer.from('metadata a')));
  assert.notEqual(first, cacheVersion(Buffer.from('style a'), Buffer.from('metadata b')));
  assert.notEqual(first, cacheVersion(Buffer.from('style a'), Buffer.from('metadata a'), '2026-10'));
  assert.throws(() => validateGeneration('../elsewhere'));
  assert.throws(() => new RasterCache('/tmp/example', '../elsewhere'));
});
