// Test-only HTTP peer for the real FastAPI/PostGIS distribution test.
import http from 'node:http';
import { loadCacheProducts } from './cache-products.mjs';
import { createCacheRegistry } from './cache-registry.mjs';
import { createLidarBlockCacheFactory, LIDAR_PRODUCT_ID } from './lidar-block-cache.mjs';
import { createLidarWmsAcquisition } from './lidar-wms.mjs';
import { createLidarDistribution } from './lidar-distribution.mjs';
import { syntheticLidarGeoTiff } from './lidar-fixture.mjs';
import { closeServer, createRequestHandler, listen } from './server.mjs';

const [cacheRoot, token] = process.argv.slice(2);
if (!cacheRoot || !token || token.length < 32) throw new Error('Test fixture arguments required');
const catalogue = await loadCacheProducts(new URL('../../webapp/backend/digues_webapp/cartographic_caches.json', import.meta.url));
const registry = createCacheRegistry(cacheRoot, catalogue, { freeBytes: async () => 1e12 });
let requests = 0;
const acquisition = createLidarWmsAcquisition(createLidarBlockCacheFactory(registry, catalogue), {
  fetchImpl: async (url) => {
    requests++;
    const params = new URL(url).searchParams;
    const [xmin, ymin, xmax, ymax] = params.get('BBOX').split(',').map(Number);
    const width = Number(params.get('WIDTH')), height = Number(params.get('HEIGHT'));
    const resolution = (xmax - xmin) / width;
    const level = Math.round(Math.log2(resolution / .5)), side = 512 * resolution;
    const ix = Math.floor(xmin / side), iy = Math.floor(ymin / side);
    const x0 = Math.round((xmin - ix * side) / resolution), y0 = Math.round((ymin - iy * side) / resolution);
    const block = { ix, iy, level, window:[x0,y0,x0+width,y0+height] };
    if (Math.abs((ymax-ymin)/height-resolution) > 1e-8) throw new Error('Non-square fixture pixels');
    const value = block.ix === 0 ? -9999 : 45.25;
    return new Response(syntheticLidarGeoTiff(block, { value }), {
      status: 200, headers: { 'Content-Type': 'image/geotiff' },
    });
  },
});
const distribution = createLidarDistribution(acquisition, catalogue.products.get(LIDAR_PRODUCT_ID));
const handler = createRequestHandler({}, { lidarDistribution: distribution,
  lidarInternalToken: token, logger: { error() {} } });
const server = http.createServer((req, res) => {
  if (req.url === '/test/counts') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ requests }));
    return;
  }
  return handler(req, res);
});
const address = await listen(server, { host: '127.0.0.1', port: 0 });
process.stdout.write(`READY ${address.port}\n`);
process.on('SIGTERM', () => { void closeServer(server).then(() => process.exit(0)); });
