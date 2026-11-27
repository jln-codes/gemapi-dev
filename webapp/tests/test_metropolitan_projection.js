// A, <1s, no mutable service: independent EPSG 9802 control points.
// Guards the shared drawing definitions, axis order, signs and closed catalogue.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const proj4 = require('../frontend/vendor/proj4/proj4.js');
const { projectedCRS } = require('../frontend/js/entity-geometry-draft.js');
const P = require('../frontend/js/local-projection.js');
const fixture = JSON.parse(fs.readFileSync(path.resolve(__dirname,
  '../../tests/fixtures/metropolitan-crs-controls.json'), 'utf8'));

test('ten exact Lambert definitions agree with independent off-meridian controls in both directions', () => {
  assert.deepEqual(Object.keys(projectedCRS).map(Number), [2154,3942,3943,3944,3945,3946,3947,3948,3949,3950]);
  for (const { srid, lonlat, xy } of fixture.controls) {
    P.validateBusinessCRS({ authority: 'EPSG', srid, code: `EPSG:${srid}` });
    const actual = proj4('EPSG:4326', projectedCRS[srid], lonlat);
    actual.forEach((value, i) => assert(Math.abs(value - xy[i]) < .001, `${srid}: axis ${i}`));
    const reverse = proj4(projectedCRS[srid], 'EPSG:4326', xy);
    reverse.forEach((value, i) => assert(Math.abs(value - lonlat[i]) < 1e-8, `${srid}: reverse ${i}`));
  }
  for (const srid of [3941,3951,4326,3857,5490,9794,9842]) assert.equal(projectedCRS[srid], undefined);
});
