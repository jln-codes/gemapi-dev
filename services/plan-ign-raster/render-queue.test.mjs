import assert from 'node:assert/strict';
import test from 'node:test';
import { metatileForTile } from './geometry.mjs';
import { createRenderQueue } from './render-queue.mjs';

const firstMeta = metatileForTile(17, 66308, 45632);
const secondMeta = metatileForTile(17, 66312, 45632);
const silentLogger = { log() {} };

test('one Native render is active even for different metatiles', async () => {
  const callbacks = [];
  const map = {
    render(_options, callback) { callbacks.push(callback); },
    release() {},
  };
  const queue = createRenderQueue(map, { logger: silentLogger });
  const first = queue.render(firstMeta);
  const second = queue.render(secondMeta);
  assert.equal(callbacks.length, 1);
  callbacks[0](null, Buffer.from('first'));
  assert.deepEqual(await first, Buffer.from('first'));
  assert.equal(callbacks.length, 2);
  callbacks[1](null, Buffer.from('second'));
  assert.deepEqual(await second, Buffer.from('second'));
  await queue.release();
});

test('a timed-out Native callback makes the renderer fatal without overlapping renders', async () => {
  const callbacks = [];
  let released = 0;
  const map = {
    render(_options, callback) { callbacks.push(callback); },
    release() { released++; },
  };
  const queue = createRenderQueue(map, { timeoutMs: 10, logger: silentLogger });
  const first = queue.render(firstMeta);
  const second = queue.render(secondMeta);
  await Promise.all([
    assert.rejects(first, /timed out/),
    assert.rejects(second, /timed out/),
  ]);
  assert.equal(queue.isHealthy(), false);
  assert.equal(callbacks.length, 1);
  callbacks[0](null, Buffer.from('late'));
  callbacks[0](null, Buffer.from('duplicate late callback'));
  assert.equal(callbacks.length, 1);
  await assert.rejects(queue.render(secondMeta), /timed out/);
  await queue.release();
  assert.equal(released, 1);
});

test('shutdown rejects queued renders and releases the Native map', async () => {
  const callbacks = [];
  let released = 0;
  const map = {
    render(_options, callback) { callbacks.push(callback); },
    release() { released++; },
  };
  const queue = createRenderQueue(map, { logger: silentLogger });
  const first = queue.render(firstMeta);
  const second = queue.render(secondMeta);
  const closing = queue.release();
  await assert.rejects(second, /shutting down/);
  await assert.rejects(queue.render(secondMeta), /closed/);
  assert.equal(released, 0);
  callbacks[0](null, Buffer.from('first'));
  assert.deepEqual(await first, Buffer.from('first'));
  await closing;
  assert.equal(released, 1);
});

test('shutdown refuses to release an active Native map whose callback never returns', async () => {
  let released = 0;
  const errors = [];
  const map = { render() {}, release() { released++; } };
  const queue = createRenderQueue(map, { timeoutMs: 10,
    logger: { log() {}, error: (message) => errors.push(message) } });
  await assert.rejects(queue.render(firstMeta), /timed out/);
  await assert.rejects(queue.release(), /refusing unsafe Map release/);
  assert.equal(released, 0);
  assert(errors.some((message) => /still active at shutdown/.test(message)));
});
