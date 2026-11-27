// Synthetic public fixtures only. No copy/read of an IGN tile or existing cache.
export const I = '10000000-0000-4000-8000-000000000001';
export const S = '20000000-0000-4000-8000-000000000001';
export const TARGETS = Object.freeze(Object.fromEntries(['A', 'B'].map((name, n) =>
  [name, Object.freeze({ name, instance: I, structure: S,
    base: `30000000-0000-4000-8000-00000000000${n + 1}`, generation: 'synthetic-v1' })])));

function variable(value) {
  const out = []; let n = BigInt(value);
  do { let b = Number(n & 127n); n >>= 7n; if (n) b |= 128; out.push(b); } while (n);
  return Buffer.from(out);
}
const field = (n, wire, bytes) => Buffer.concat([variable((n << 3) | wire), bytes]);
const number = (n, value) => field(n, 0, variable(value));
const bytes = (n, value) => { const b = Buffer.from(value); return field(n, 2, Buffer.concat([variable(b.length), b])); };
const packed = (n, values) => bytes(n, Buffer.concat(values.map(variable)));
const zig = n => n < 0 ? -2 * n - 1 : 2 * n;

export function tile(target, z, x, y) {
  const value = s => bytes(1, s);
  const polygon = Buffer.concat([number(1, 1), packed(2, [0, 0, 2, 2]), number(3, 3),
    packed(4, [9, zig(512), zig(512), 26, zig(3072), 0, 0, zig(3072), zig(-3072), 0, 15])]);
  // Location-sensitive marker: prevents equal-looking output for distinct views.
  const px = 1400 + x % 3 * 400, py = 1400 + y % 3 * 400;
  const point = Buffer.concat([number(1, 2), packed(2, [0, 0, 1, 1, 2, 2]), number(3, 1),
    packed(4, [9, zig(px), zig(py)])]);
  const layer = Buffer.concat([bytes(1, 'fixture'), bytes(2, polygon), bytes(2, point),
    ...['context', 'label', 'tile'].map(k => bytes(3, k)),
    ...[target.name, 'A', `${z}/${x}/${y}`].map(v => bytes(4, value(v))),
    number(5, 4096), number(15, 2)]);
  return bytes(3, layer);
}

export function glyphs() {
  const width = 8, height = 12, pad = 3, bitmap = Buffer.alloc((width + pad * 2) * (height + pad * 2));
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const ink = x < 2 || x >= width - 2 || y >= 5 && y <= 6;
    bitmap[(y + pad) * (width + pad * 2) + x + pad] = ink ? 240 : 20;
  }
  const glyph = Buffer.concat([number(1, 65), bytes(2, bitmap), number(3, width), number(4, height),
    number(5, zig(0)), number(6, zig(12)), number(7, 10)]);
  return bytes(1, Buffer.concat([bytes(1, 'Audit Sans'), bytes(2, '0-255'), bytes(3, glyph)]));
}

export function styleFor(target, contextual = true, commonStyle = null, metadata = null) {
  const id = contextual ? `fixture-${target.base}` : 'fixture-global';
  const prefix = contextual
    ? `synthetic-mvt://${target.instance}/${target.structure}/${target.base}/${target.generation}`
    : 'synthetic-mvt://global';
  if (commonStyle) {
    // Consume the actual shared JSON, changing only the internal source identity.
    const style = structuredClone(commonStyle);
    const original = Object.values(style.sources)[0];
    style.sources = { [id]: { ...original, tiles: [`${prefix}/{z}/{x}/{y}.pbf`],
      minzoom: metadata.minzoom, maxzoom: metadata.maxzoom } };
    for (const layer of style.layers) if (layer.source) layer.source = id;
    return style;
  }
  return {
    version: 8, transition: { duration: 0, delay: 0 },
    glyphs: 'synthetic-assets://shared/fonts/{fontstack}/{range}.pbf',
    sprite: 'synthetic-assets://shared/sprite',
    sources: { [id]: { type: 'vector', tiles: [`${prefix}/{z}/{x}/{y}.pbf`],
      minzoom: 0, maxzoom: 18, scheme: 'xyz' } },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#ffffff' } },
      { id: 'areas', type: 'fill', source: id, 'source-layer': 'fixture', filter: ['==', '$type', 'Polygon'],
        paint: { 'fill-color': ['match', ['get', 'context'], 'A', '#e63946', 'B', '#2463eb', '#ff00ff'], 'fill-opacity': 1 } },
      { id: 'markers', type: 'symbol', source: id, 'source-layer': 'fixture', filter: ['==', '$type', 'Point'],
        layout: { 'icon-image': 'green-dot', 'icon-allow-overlap': true, 'icon-ignore-placement': true,
          'text-field': 'A', 'text-font': ['Audit Sans'], 'text-size': 18,
          'text-offset': [0, 1.5], 'text-allow-overlap': true, 'text-ignore-placement': true },
        paint: { 'text-color': '#111111' } },
    ],
  };
}
