import sharp from 'sharp';
import { blockGeometry, lidarGrid } from './lidar-grid.mjs';

export const MAX_GEOTIFF_BYTES = 8 * 1024 * 1024;
const MAX_DECODED_BYTES = 4 * 1024 * 1024; // Sharp expands a gray input to RGB float.
const TYPE_SIZE = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8 };

function tiffTags(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 8 || bytes.length > MAX_GEOTIFF_BYTES) {
    throw new Error('Invalid GeoTIFF byte length');
  }
  const order = bytes.toString('ascii', 0, 2);
  if (!['II', 'MM'].includes(order)) throw new Error('Invalid TIFF byte order');
  const le = order === 'II';
  const u16 = (at) => le ? bytes.readUInt16LE(at) : bytes.readUInt16BE(at);
  const u32 = (at) => le ? bytes.readUInt32LE(at) : bytes.readUInt32BE(at);
  const f64 = (at) => le ? bytes.readDoubleLE(at) : bytes.readDoubleBE(at);
  if (u16(2) !== 42) throw new Error('Unsupported TIFF header');
  const ifd = u32(4);
  if (ifd < 8 || ifd + 2 > bytes.length) throw new Error('Invalid TIFF IFD');
  const count = u16(ifd);
  if (count > 256 || ifd + 2 + count * 12 + 4 > bytes.length) throw new Error('Oversized TIFF IFD');
  const tags = new Map();
  for (let index = 0; index < count; index++) {
    const at = ifd + 2 + index * 12;
    const tag = u16(at), type = u16(at + 2), items = u32(at + 4);
    const length = items * TYPE_SIZE[type];
    if (!TYPE_SIZE[type] || !Number.isSafeInteger(length) || length > MAX_GEOTIFF_BYTES ||
        tags.has(tag)) throw new Error('Invalid TIFF tag');
    const start = length <= 4 ? at + 8 : u32(at + 8);
    if (start < 0 || start + length > bytes.length) throw new Error('TIFF tag outside file');
    tags.set(tag, { type, items, start });
  }
  if (u32(ifd + 2 + count * 12) !== 0) throw new Error('Multi-page TIFF is unsupported');
  const values = (tag, allowed, wanted = null) => {
    const item = tags.get(tag);
    if (!item || !allowed.includes(item.type) || item.items > 256 ||
        (wanted != null && item.items !== wanted)) {
      throw new Error(`Invalid GeoTIFF tag ${tag}`);
    }
    return Array.from({ length: item.items }, (_, index) => {
      const at = item.start + index * TYPE_SIZE[item.type];
      return item.type === 3 ? u16(at) : item.type === 4 ? u32(at) : f64(at);
    });
  };
  const ascii = (tag) => {
    const item = tags.get(tag);
    if (!item || item.type !== 2 || item.items < 2 || item.items > 64) {
      throw new Error(`Invalid GeoTIFF tag ${tag}`);
    }
    return bytes.toString('ascii', item.start, item.start + item.items).replace(/\0+$/, '').trim();
  };
  return { tags, values, ascii };
}

export async function validateLidarGeoTiff(bytes, block, grid = lidarGrid(block?.level)) {
  const { bbox: expected, width, height } = blockGeometry(block, grid);
  const { tags, values, ascii } = tiffTags(bytes);
  if (values(256, [3, 4], 1)[0] !== width ||
      values(257, [3, 4], 1)[0] !== height ||
      values(258, [3], 1)[0] !== 32 ||
      values(277, [3], 1)[0] !== 1 ||
      values(339, [3], 1)[0] !== 3 ||
      ![1, 5, 8, 32946].includes(values(259, [3], 1)[0]) ||
      (tags.has(284) && values(284, [3], 1)[0] !== 1) || tags.has(34264)) {
    throw new Error('Unsupported LiDAR TIFF raster layout');
  }
  const keys = values(34735, [3]);
  const keyEnd = keys[3] * 4 + 4;
  if (keys.length < 4 || keys[0] !== 1 || keyEnd > keys.length ||
      (keyEnd !== keys.length && (keys.length - keyEnd !== 4 ||
        keys.slice(keyEnd).some((value) => value !== 0)))) {
    throw new Error('Invalid GeoTIFF key directory');
  }
  const geo = new Map();
  for (let i = 4; i < keyEnd; i += 4) {
    if (geo.has(keys[i])) throw new Error('Duplicate GeoTIFF key');
    geo.set(keys[i], keys.slice(i, i + 4));
  }
  const inline = (key) => {
    const entry = geo.get(key);
    return entry?.[1] === 0 && entry[2] === 1 ? entry[3] : null;
  };
  // IGN WMS currently emits 32767 with EPSG:2154 in GTCitationGeoKey.
  const citationKey = geo.get(1026);
  const citation = citationKey?.[1] === 34737 && citationKey[2] === 10 && citationKey[3] === 0
    ? (() => {
      const item = tags.get(34737);
      return item?.type === 2 && item.items >= 10
        ? bytes.toString('ascii', item.start, item.start + 10) : null;
    })() : null;
  if (inline(1024) !== 1 || inline(1025) !== 1 ||
      !(inline(3072) === 2154 || (inline(3072) === 32767 && citation === 'EPSG:2154|'))) {
    throw new Error('GeoTIFF must be projected PixelIsArea EPSG:2154');
  }
  const scale = values(33550, [12], 3), tie = values(33922, [12], 6);
  const originX = tie[3] - tie[0] * scale[0];
  const originY = tie[4] + tie[1] * scale[1];
  const near = (a, b) => Number.isFinite(a) && Math.abs(a - b) <= 1e-7;
  if (!near(scale[0], grid.resolutionM) || !near(scale[1], grid.resolutionM) ||
      !near(scale[2], 0) || !near(tie[2], 0) || !near(tie[5], 0) ||
      !near(originX, expected[0]) || !near(originY, expected[3])) {
    throw new Error('GeoTIFF grid alignment differs from block');
  }
  const nodataText = ascii(42113);
  if (!/^(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|nan)$/i.test(nodataText)) {
    throw new Error('Invalid GeoTIFF NoData');
  }
  const nodata = Number(nodataText);
  if (!Number.isFinite(nodata) && !Number.isNaN(nodata)) throw new Error('Invalid GeoTIFF NoData');
  const image = sharp(bytes, { failOn: 'error', limitInputPixels: grid.pixels ** 2 });
  const meta = await image.metadata();
  if (meta.format !== 'tiff' || meta.width !== width || meta.height !== height ||
      meta.channels !== 1 || meta.bitsPerSample !== 32 || meta.depth !== 'float' || meta.pages !== 1) {
    throw new Error('Decoded GeoTIFF metadata differs from TIFF tags');
  }
  const decoded = await image.raw({ depth: 'float' }).toBuffer({ resolveWithObject: true });
  if (decoded.info.width !== width || decoded.info.height !== height ||
      decoded.data.length > MAX_DECODED_BYTES || decoded.data.length !==
      width * height * decoded.info.channels * 4) {
    throw new Error('Decoded GeoTIFF exceeds pixel limit');
  }
  let validPixels = 0;
  for (let at = 0; at < decoded.data.length; at += 4) {
    const value = decoded.data.readFloatLE(at);
    if (!Number.isFinite(value) && !(Number.isNaN(nodata) && Number.isNaN(value))) {
      throw new Error('Incoherent LiDAR sample');
    }
    if (at % (decoded.info.channels * 4) === 0 &&
        !(Number.isNaN(nodata) ? Number.isNaN(value) : value === nodata)) validPixels++;
  }
  return { bbox: expected, width, height,
    crs: grid.crs, nodata: Number.isNaN(nodata) ? 'NaN' : nodata, bytes: bytes.length,
    validPixels, hasElevation: validPixels > 0 };
}
