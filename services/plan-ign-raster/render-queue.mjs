import { performance } from 'node:perf_hooks';
import { metatileGeometry } from './geometry.mjs';

export const DEFAULT_RENDER_TIMEOUT_MS = 60_000;

export function createRenderQueue(map, { timeoutMs = DEFAULT_RENDER_TIMEOUT_MS,
  logger = console, prepare = null, validate = null } = {}) {
  let active = false;
  let released = false;
  let fatalError = null;
  let releasePromise;
  let lastRenderMs = 0;
  let renderCount = 0;
  const queue = [];
  let idleWaiters = [];

  function finishActive() {
    active = false;
    for (const resolve of idleWaiters) resolve();
    idleWaiters = [];
    pump();
  }

  function pump() {
    if (active || released || fatalError || queue.length === 0) return;
    active = true;
    const item = queue.shift();
    const started = performance.now();
    let responseSettled = false;
    let callbackFinished = false;
    const timer = setTimeout(() => {
      fatalError = new Error(`MapLibre Native render timed out after ${timeoutMs} ms`);
      responseSettled = true;
      logger.error?.(`ERROR ${fatalError.message}; renderer is unhealthy`);
      item.reject(fatalError);
      for (const pending of queue.splice(0)) pending.reject(fatalError);
    }, timeoutMs);
    const failBeforeRender = (error) => {
      clearTimeout(timer);
      if (error.fatalRenderer) {
        fatalError = error;
        for (const pending of queue.splice(0)) pending.reject(error);
      }
      if (!responseSettled) { responseSettled = true; item.reject(error); }
      finishActive();
    };
    const assertUsable = () => {
      if (fatalError) throw fatalError;
      if (released) throw new Error('MapLibre Native renderer is closed');
    };
    const start = () => { try {
      assertUsable();
      logger.log(`RENDER meta=${item.meta.z}/${item.meta.originX}/${item.meta.originY}`);
      map.render(metatileGeometry(item.meta).viewport, (error, bytes) => {
        if (callbackFinished) return;
        callbackFinished = true;
        clearTimeout(timer);
        lastRenderMs = Math.round(performance.now() - started);
        renderCount++;
        logger.log(`${responseSettled ? 'RENDER_LATE' : 'RENDER_DONE'} meta=${item.meta.z}/${item.meta.originX}/${item.meta.originY} ms=${lastRenderMs} rssMiB=${Math.round(process.memoryUsage().rss / 1048576)}`);
        const deliver = () => {
          if (!responseSettled) {
            responseSettled = true;
            if (error) item.reject(error);
            else item.resolve(Buffer.from(bytes));
          }
          finishActive();
        };
        if (validate) {
          Promise.resolve().then(() => validate(item.context, error, bytes, item.meta))
            .then(deliver, failure => {
              if (!responseSettled) { responseSettled = true; item.reject(failure); }
              finishActive();
            });
        } else deliver();
      });
    } catch (error) {
      if (callbackFinished) return;
      callbackFinished = true;
      clearTimeout(timer);
      if (!responseSettled) item.reject(error);
      finishActive();
    } };
    if (prepare) {
      Promise.resolve().then(() => prepare(item.context, assertUsable))
        .then(start, failBeforeRender);
    } else start();
  }

  return {
    stats() { return { lastRenderMs, renderCount }; },
    isHealthy() { return !fatalError && !released; },
    render(meta, context = null) {
      if (released) return Promise.reject(new Error('MapLibre Native renderer is closed'));
      if (fatalError) return Promise.reject(fatalError);
      return new Promise((resolve, reject) => {
        queue.push({ meta, context, resolve, reject });
        pump();
      });
    },
    release() {
      if (releasePromise) return releasePromise;
      released = true;
      for (const item of queue.splice(0)) item.reject(new Error('Renderer shutting down'));
      releasePromise = (async () => {
        if (active) {
          await new Promise((resolve) => {
            const done = () => { clearTimeout(timer); resolve(); };
            idleWaiters.push(done);
            const timer = setTimeout(() => {
              idleWaiters = idleWaiters.filter((waiter) => waiter !== done);
              resolve();
            }, Math.min(timeoutMs, 15_000));
          });
        }
        if (active) {
          logger.error?.('ERROR Native render still active at shutdown; process restart required');
          throw new Error('Native render still active; refusing unsafe Map release');
        }
        map.release();
      })();
      return releasePromise;
    },
  };
}
