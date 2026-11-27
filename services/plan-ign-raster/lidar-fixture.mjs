// Test-only synthetic, uncompressed, single-strip GeoTIFF. No IGN data.
import { blockGeometry } from './lidar-grid.mjs';

export function syntheticLidarGeoTiff(block, { value = 42.5, srid = 2154,
  resolution = blockGeometry(block).resolutionM, xOffset = 0, nodata = '-9999', ignCitation = false } = {}) {
  const { bbox: bounds, width, height } = blockGeometry(block), pixels = width * height;
  const scale = Buffer.alloc(24);
  scale.writeDoubleLE(resolution, 0); scale.writeDoubleLE(resolution, 8);
  const tie = Buffer.alloc(48);
  tie.writeDoubleLE(bounds[0] + xOffset, 24); tie.writeDoubleLE(bounds[3], 32);
  const geoValues = ignCitation
    ? [1, 1, 0, 4, 1024, 0, 1, 1, 1025, 0, 1, 1,
      1026, 34737, 10, 0, 3072, 0, 1, 32767]
    : [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, srid];
  const geo = Buffer.alloc(geoValues.length * 2);
  geoValues
    .forEach((number, index) => geo.writeUInt16LE(number, index * 2));
  const noData = Buffer.from(`${nodata}\0`, 'ascii');
  const entries = [
    [256, 4, 1, width], [257, 4, 1, height], [258, 3, 1, 32],
    [259, 3, 1, 1], [262, 3, 1, 1], [273, 4, 1, null],
    [277, 3, 1, 1], [278, 4, 1, height], [279, 4, 1, pixels * 4],
    [284, 3, 1, 1], [339, 3, 1, 3], [33550, 12, 3, scale],
    [33922, 12, 6, tie], [34735, 3, geoValues.length, geo],
    [42113, 2, noData.length, noData],
  ].sort((a, b) => a[0] - b[0]);
  if (ignCitation) entries.push([34737, 2, 11, Buffer.from(`EPSG:${srid}|\0`, 'ascii')]);
  entries.sort((a, b) => a[0] - b[0]);
  const ifdEnd = 8 + 2 + entries.length * 12 + 4;
  let next = ifdEnd;
  const attachments = [];
  for (const entry of entries) {
    if (!Buffer.isBuffer(entry[3]) || entry[3].length <= 4) continue;
    next = (next + 7) & ~7;
    attachments.push([next, entry[3]]);
    entry[3] = next;
    next += attachments.at(-1)[1].length;
  }
  next = (next + 7) & ~7;
  entries.find((entry) => entry[0] === 273)[3] = next;
  const bytes = Buffer.alloc(next + pixels * 4);
  bytes.write('II', 0, 'ascii'); bytes.writeUInt16LE(42, 2); bytes.writeUInt32LE(8, 4);
  bytes.writeUInt16LE(entries.length, 8);
  entries.forEach(([tag, type, count, datum], index) => {
    const at = 10 + index * 12;
    bytes.writeUInt16LE(tag, at); bytes.writeUInt16LE(type, at + 2);
    bytes.writeUInt32LE(count, at + 4);
    if (Buffer.isBuffer(datum)) datum.copy(bytes, at + 8);
    else bytes.writeUInt32LE(datum, at + 8);
  });
  for (const [at, data] of attachments) data.copy(bytes, at);
  for (let index = 0; index < pixels; index++) bytes.writeFloatLE(value, next + index * 4);
  return bytes;
}

export function fixtureTagOffset(bytes, tag) {
  const count = bytes.readUInt16LE(8);
  for (let index = 0; index < count; index++) {
    const at = 10 + index * 12;
    if (bytes.readUInt16LE(at) === tag) return at;
  }
  throw new Error(`Missing fixture tag ${tag}`);
}
