const { test, expect } = require('@playwright/test');
const { parameters, photoQualityPolicy } = require('./fixtures/photo-quality-policy');

async function loadModule(page) {
  await page.route('http://localhost/**', route => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><title>Photo preparation</title>',
  }));
  await page.goto('http://localhost/photo-preparation');
  await page.addScriptTag({ path: 'webapp/frontend/js/photo-file.js' });
}

test.beforeEach(async ({ page }) => loadModule(page));

test('les trois profils réduisent une grande photo dans l’ordre attendu', async ({ page }) => {
  const result = await page.evaluate(async ({ parameters, policies }) => {
    const canvas = document.createElement('canvas'); canvas.width = 3200; canvas.height = 2400;
    const context = canvas.getContext('2d');
    const gradient = context.createLinearGradient(0, 0, canvas.width, canvas.height);
    gradient.addColorStop(0, '#82b8dd'); gradient.addColorStop(0.45, '#d5c892'); gradient.addColorStop(1, '#315b2c');
    context.fillStyle = gradient; context.fillRect(0, 0, canvas.width, canvas.height);
    let seed = 1987;
    for (let i = 0; i < 1800; i++) {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      context.fillStyle = `rgba(${seed & 127},${(seed >>> 8) & 127},${(seed >>> 16) & 127},.28)`;
      context.fillRect(seed % canvas.width, (seed >>> 11) % canvas.height, 18 + seed % 75, 8 + seed % 31);
    }
    const source = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.96));
    const file = new File([source], 'terrain.jpg', { type: 'image/jpeg' });
    const rows = [];
    for (const policy of policies) {
      const media = await PhotoFile.prepare(file, policy);
      rows.push({ profile: policy.profile, width: media.width, height: media.height, size: media.size,
        mime: media.mimeType, policyVersion: media.policyVersion, hash: media.sha256 });
    }
    return { sourceSize: file.size, rows, configuredEdges: Object.values(parameters).map(value => value.max_long_edge_px) };
  }, { parameters, policies: ['economique', 'standard', 'haute_qualite'].map(profile => photoQualityPolicy(profile, 7)) });

  expect(result.configuredEdges).toEqual([1024, 1600, 2400]);
  expect(result.rows.map(row => [row.width, row.height])).toEqual([[1024, 768], [1600, 1200], [2400, 1800]]);
  expect(result.rows.map(row => row.size)).toEqual([...result.rows.map(row => row.size)].sort((a, b) => a - b));
  expect(result.rows.every(row => row.mime === 'image/jpeg' && row.policyVersion === 7 && /^[a-f0-9]{64}$/.test(row.hash))).toBe(true);
  expect(result.rows.at(-1).size).toBeLessThan(result.sourceSize);
});

test('limites 500 Kio, 1 Mio et 2 Mio : adaptation bornée et profils distincts', async ({ page }) => {
  const result = await page.evaluate(async ({ profiles, limits, parameters }) => {
    const policy = (profile, policyVersion, limit) => ({ contract_version: 2, parameters_version: 2,
      profile, policy_version: policyVersion, max_file_size_bytes: limit,
      effective_parameters: { ...parameters[profile], max_file_size_bytes: limit } });
    async function fixture(kind) {
      const canvas = document.createElement('canvas'); canvas.width = 3200; canvas.height = 2400;
      const context = canvas.getContext('2d');
      if (kind === 'simple') {
        const gradient = context.createLinearGradient(0, 0, 3200, 2400);
        gradient.addColorStop(0, '#8fc4e8'); gradient.addColorStop(1, '#486c35');
        context.fillStyle = gradient; context.fillRect(0, 0, 3200, 2400);
      } else if (kind === 'terrain') {
        const gradient = context.createLinearGradient(0, 0, 3200, 2400);
        gradient.addColorStop(0, '#82b8dd'); gradient.addColorStop(0.45, '#d5c892'); gradient.addColorStop(1, '#315b2c');
        context.fillStyle = gradient; context.fillRect(0, 0, 3200, 2400);
        let seed = 1987;
        for (let i = 0; i < 5000; i++) {
          seed = (seed * 1664525 + 1013904223) >>> 0;
          context.fillStyle = `rgba(${seed & 127},${(seed >>> 8) & 127},${(seed >>> 16) & 127},.35)`;
          context.fillRect(seed % 3200, (seed >>> 11) % 2400, 10 + seed % 55, 6 + seed % 27);
        }
      } else {
        const image = context.createImageData(3200, 2400); let seed = 73421;
        const random = () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return seed >>> 0; };
        for (let at = 0; at < image.data.length; at += 4) {
          image.data[at] = random() & 255; image.data[at + 1] = random() & 255;
          image.data[at + 2] = random() & 255; image.data[at + 3] = 255;
        }
        context.putImageData(image, 0, 0);
      }
      return new File([await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.96))], `${kind}.jpg`, { type: 'image/jpeg' });
    }
    const files = {};
    for (const kind of ['simple', 'terrain', 'detaillee']) files[kind] = await fixture(kind);
    const rows = [];
    for (const [kind, file] of Object.entries(files)) for (const limit of limits) for (const profile of profiles) {
      let media;
      try { media = await PhotoFile.prepare(file, policy(profile, 12, limit)); }
      catch (error) { throw new Error(`${kind}/${limit}/${profile}: ${error.message}`); }
      rows.push({ kind, sourceSize: file.size, limit, profile, width: media.width, height: media.height,
        size: media.size, quality: media.encoderQuality, ...media.adaptation });
    }
    let impossible = '';
    try { await PhotoFile.prepare(files.simple, policy('standard', 13, 100)); }
    catch (error) { impossible = error.message; }
    return { rows, impossible };
  }, { profiles: ['economique', 'standard', 'haute_qualite'],
    limits: [500 * 1024, 1024 * 1024, 2 * 1024 * 1024], parameters });

  expect(result.rows).toHaveLength(27);
  expect(result.rows.every(row => row.size <= row.limit && row.encodingAttempts >= 1)).toBe(true);
  for (const kind of ['simple', 'terrain', 'detaillee']) for (const limit of [500 * 1024, 1024 * 1024, 2 * 1024 * 1024]) {
    const rows = result.rows.filter(row => row.kind === kind && row.limit === limit);
    expect(rows.map(row => Math.max(row.width, row.height))).toEqual(
      [...rows.map(row => Math.max(row.width, row.height))].sort((a, b) => a - b),
    );
  }
  const resized = result.rows.filter(row => row.dimensionReductions > 0);
  expect(resized.length).toBeGreaterThan(0);
  expect(resized.every(row => row.quality >= 0.46 && row.qualityReductions > 0)).toBe(true);
  expect(result.impossible).toContain('taille maximale choisie');
});

test('petite image sans upscale, formats supportés, nom/MIME et SHA du fichier final', async ({ page }) => {
  const result = await page.evaluate(async policy => {
    const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 240;
    const context = canvas.getContext('2d'); context.fillStyle = '#4d774e'; context.fillRect(0, 0, 320, 240);
    const rows = [];
    for (const [mime, extension] of [['image/jpeg', 'jpeg'], ['image/png', 'png'], ['image/webp', 'webp']]) {
      const source = await new Promise(resolve => canvas.toBlob(resolve, mime, 0.9));
      const media = await PhotoFile.prepare(new File([source], `terrain.v1.${extension}`, { type: mime }), policy);
      const digest = await crypto.subtle.digest('SHA-256', await media.blob.arrayBuffer());
      const actual = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      rows.push({ width: media.width, height: media.height, mime: media.mimeType,
        blobMime: media.blob.type, name: media.originalFilename, hashMatches: actual === media.sha256 });
    }
    return rows;
  }, photoQualityPolicy('standard', 4));

  expect(result).toEqual([
    { width: 320, height: 240, mime: 'image/jpeg', blobMime: 'image/jpeg', name: 'terrain.v1.jpg', hashMatches: true },
    { width: 320, height: 240, mime: 'image/jpeg', blobMime: 'image/jpeg', name: 'terrain.v1.jpg', hashMatches: true },
    { width: 320, height: 240, mime: 'image/jpeg', blobMime: 'image/jpeg', name: 'terrain.v1.jpg', hashMatches: true },
  ]);
});

test('l’orientation EXIF est matérialisée dans les pixels encodés', async ({ page }) => {
  const result = await page.evaluate(async policy => {
    const canvas = document.createElement('canvas'); canvas.width = 40; canvas.height = 20;
    const context = canvas.getContext('2d'); context.fillStyle = '#c33'; context.fillRect(0, 0, 20, 20);
    context.fillStyle = '#36c'; context.fillRect(20, 0, 20, 20);
    const raw = new Uint8Array(await (await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9))).arrayBuffer());
    const exif = Uint8Array.from([0xff, 0xe1, 0, 34, 0x45, 0x78, 0x69, 0x66, 0, 0,
      0x4d, 0x4d, 0, 42, 0, 0, 0, 8, 0, 1, 0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, 6, 0, 0, 0, 0, 0, 0]);
    const bytes = new Uint8Array(raw.length + exif.length);
    bytes.set(raw.subarray(0, 2)); bytes.set(exif, 2); bytes.set(raw.subarray(2), 2 + exif.length);
    const media = await PhotoFile.prepare(new File([bytes], 'portrait.jpg', { type: 'image/jpeg' }), policy);
    return { width: media.width, height: media.height, orientation: PhotoFile.inspect(
      await media.blob.arrayBuffer(), media.originalFilename, media.mimeType).orientation };
  }, photoQualityPolicy());

  expect(result).toEqual({ width: 20, height: 40, orientation: 1 });
});

test('politique absente et fichier non supporté produisent des erreurs explicites', async ({ page }) => {
  const messages = await page.evaluate(async policy => {
    const canvas = document.createElement('canvas'); canvas.width = 2; canvas.height = 2;
    const png = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    const calls = [
      () => PhotoFile.prepare(new File([png], 'photo.png', { type: 'image/png' }), null),
      () => PhotoFile.prepare(new File(['GIF89a'], 'photo.gif', { type: 'image/gif' }), policy),
    ];
    const result = [];
    for (const call of calls) try { await call(); result.push('aucune erreur'); } catch (error) { result.push(error.message); }
    return result;
  }, photoQualityPolicy());

  expect(messages[0]).toContain('Politique de qualité photo indisponible');
  expect(messages[1]).toContain('JPEG, PNG ou WebP statique');
});
