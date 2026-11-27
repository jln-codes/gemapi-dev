// Explicit metadata fixture shared by mocked business editors.
const { mockGeometryCapabilities } = require('./mock-geometry-capabilities');
function businessCRS(srid = 3950) {
  const name = srid === 2154 ? 'RGF93 v1 / Lambert-93' : `RGF93 v1 / CC${srid - 3900}`;
  return { authority: 'EPSG', srid, code: `EPSG:${srid}`, name, label: `${name} — EPSG:${srid}` };
}
async function mockBusinessCRS(route, srid = 3950) {
  // Ces suites interceptent explicitement les métadonnées applicatives ; les
  // scénarios connectés à une vraie base n'installent pas cette fixture.
  if (await mockGeometryCapabilities(route)) return true;
  if (new URL(route.request().url()).pathname !== '/api/base-summary') return false;
  await route.fulfill({ json: { crs: businessCRS(srid) } });
  return true;
}
module.exports = { businessCRS, mockBusinessCRS };
