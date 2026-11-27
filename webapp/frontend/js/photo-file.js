/* One preparation pipeline for connected and offline user photos. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PhotoFile = api;
})(globalThis, function () {
  'use strict';
  const MAX_BYTES = 20 * 1024 * 1024, MAX_PIXELS = 25000000;
  const invalid = message => { throw new Error(message); };
  const hex = digest => Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  function jpegOrientation(bytes, view, at, size) {
    const data = at + 2, end = at + size;
    if (data + 14 > end || String.fromCharCode(...bytes.subarray(data, data + 6)) !== 'Exif\x00\x00') return 1;
    const tiff = data + 6, marker = String.fromCharCode(bytes[tiff], bytes[tiff + 1]);
    if (!['II', 'MM'].includes(marker)) return 1;
    const little = marker === 'II', u16 = offset => view.getUint16(offset, little), u32 = offset => view.getUint32(offset, little);
    if (u16(tiff + 2) !== 42) return 1;
    const directory = tiff + u32(tiff + 4);
    if (directory + 2 > end) return 1;
    const count = u16(directory);
    for (let index = 0; index < count; index++) {
      const entry = directory + 2 + index * 12;
      if (entry + 12 > end) return 1;
      if (u16(entry) === 0x0112 && u16(entry + 2) === 3 && u32(entry + 4) === 1) {
        const orientation = u16(entry + 8);
        return orientation >= 1 && orientation <= 8 ? orientation : 1;
      }
    }
    return 1;
  }
  function inspect(buffer, filename, declared = '') {
    const bytes = new Uint8Array(buffer), view = new DataView(buffer);
    if (!bytes.length) invalid('Le fichier est vide.');
    if (bytes.length > MAX_BYTES) invalid('Le fichier dépasse la limite de 20 Mio.');
    if (!filename || [...filename].length > 200 || /[/\\:\x00-\x1f\x7f]/.test(filename) || ['.', '..'].includes(filename)) invalid('Nom de fichier invalide.');
    const ascii = (at, n) => String.fromCharCode(...bytes.subarray(at, at + n));
    let mimeType, extensions, width, height, orientation = 1;
    if (ascii(0, 8) === '\x89PNG\r\n\x1a\n') {
      mimeType = 'image/png'; extensions = ['png'];
      let imageData = false, frameControl = false;
      for (let at = 8; at + 12 <= bytes.length;) {
        const size = view.getUint32(at), type = ascii(at + 4, 4), end = at + 12 + size;
        if (end > bytes.length) invalid('Image PNG incomplète.');
        if (type === 'IHDR' && size === 13) { width = view.getUint32(at + 8); height = view.getUint32(at + 12); }
        if (type === 'acTL' && (size !== 8 || view.getUint32(at + 8) !== 1)) invalid('Choisissez une image statique.');
        if (type === 'fcTL') { if (imageData || frameControl) invalid('Choisissez une image statique.'); frameControl = true; }
        if (type === 'IDAT') imageData = true;
        at = end;
      }
    } else if (ascii(0, 4) === 'RIFF' && ascii(8, 4) === 'WEBP' && bytes.length >= 20) {
      mimeType = 'image/webp'; extensions = ['webp'];
      if (view.getUint32(4, true) + 8 > bytes.length) invalid('Image WebP incomplète.');
      const uint24 = at => bytes[at] + bytes[at + 1] * 256 + bytes[at + 2] * 65536;
      for (let at = 12; at + 8 <= bytes.length;) {
        const type = ascii(at, 4), size = view.getUint32(at + 4, true), data = at + 8;
        if (data + size > bytes.length) invalid('Image WebP incomplète.');
        if (type === 'ANIM' || type === 'ANMF') invalid('Choisissez une image statique.');
        if (type === 'VP8X' && size >= 10) {
          if (bytes[data] & 2) invalid('Choisissez une image statique.');
          width = uint24(data + 4) + 1; height = uint24(data + 7) + 1;
        } else if (type === 'VP8 ' && size >= 10 && !width) {
          width = view.getUint16(data + 6, true) & 0x3fff; height = view.getUint16(data + 8, true) & 0x3fff;
        } else if (type === 'VP8L' && size >= 5 && bytes[data] === 0x2f && !width) {
          const bits = view.getUint32(data + 1, true);
          width = (bits & 0x3fff) + 1; height = ((bits >>> 14) & 0x3fff) + 1;
        }
        at = data + size + (size % 2);
      }
    } else if (bytes[0] === 0xff && bytes[1] === 0xd8) {
      mimeType = 'image/jpeg'; extensions = ['jpg', 'jpeg'];
      for (let at = 2; at + 3 < bytes.length;) {
        if (bytes[at++] !== 0xff) invalid('Image JPEG invalide.');
        while (bytes[at] === 0xff) at++;
        const marker = bytes[at++];
        if (marker === 0xda || marker === 0xd9) break;
        if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
        const size = view.getUint16(at);
        if (size < 2 || at + size > bytes.length) invalid('Image JPEG incomplète.');
        if (marker === 0xe1) orientation = jpegOrientation(bytes, view, at, size);
        if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker) && size >= 8) {
          height = view.getUint16(at + 3); width = view.getUint16(at + 5);
        }
        at += size;
      }
    } else invalid('Choisissez une image JPEG, PNG ou WebP statique.');
    if (!width || !height || width * height > MAX_PIXELS || Math.max(width, height) > 20000) invalid('Dimensions invalides : 25 millions de pixels et 20 000 pixels par côté maximum.');
    if (!extensions.includes(filename.split('.').pop().toLowerCase()) || ![mimeType, '', 'application/octet-stream'].includes(declared)) invalid('Le type ou l’extension ne correspond pas au contenu de l’image.');
    return { mimeType, width, height, size: bytes.length, originalFilename: filename, orientation };
  }
  function policyParameters(policy) {
    const value = policy?.effective_parameters;
    if (policy?.contract_version !== 2 || ![1, 2].includes(policy?.parameters_version)
        || !Number.isSafeInteger(policy.policy_version) || policy.policy_version < 1
        || !['economique', 'standard', 'haute_qualite'].includes(policy.profile)
        || !value || !Number.isSafeInteger(value.max_long_edge_px)
        || value.max_long_edge_px < 320 || value.max_long_edge_px > 10000
        || value.output_mime_type !== 'image/jpeg'
        || typeof value.encoder_quality !== 'number' || value.encoder_quality < 0.1 || value.encoder_quality > 1) {
      invalid('Politique de qualité photo indisponible ou incompatible. Une première connexion est nécessaire avant d’ajouter une photo hors ligne.');
    }
    // Parameters v1 described the previous contract: nominal encoding followed
    // only by the absolute 20 MiB guard. Keeping it readable lets an already
    // cached policy remain usable until the instance is reached again.
    if (policy.parameters_version === 1) return { ...value, max_file_size_bytes: MAX_BYTES,
      min_long_edge_px: value.max_long_edge_px, min_encoder_quality: value.encoder_quality,
      encoder_quality_step: 0.08, dimension_scale_step: 0.8, adaptive: false };
    if (!Number.isSafeInteger(value.max_file_size_bytes) || value.max_file_size_bytes <= 0
        || value.max_file_size_bytes > MAX_BYTES
        || policy.max_file_size_bytes !== undefined && policy.max_file_size_bytes !== value.max_file_size_bytes
        || !Number.isSafeInteger(value.min_long_edge_px) || value.min_long_edge_px < 320
        || value.min_long_edge_px > value.max_long_edge_px
        || typeof value.min_encoder_quality !== 'number' || value.min_encoder_quality < 0.1
        || value.min_encoder_quality > value.encoder_quality
        || typeof value.encoder_quality_step !== 'number' || value.encoder_quality_step < 0.01
        || value.encoder_quality_step > 0.25
        || typeof value.dimension_scale_step !== 'number' || value.dimension_scale_step < 0.5
        || value.dimension_scale_step >= 1) {
      invalid('Politique de qualité photo indisponible ou incompatible. Une première connexion est nécessaire avant d’ajouter une photo hors ligne.');
    }
    return { ...value, adaptive: true };
  }
  function normalizePolicy(policy) {
    const value = policyParameters(policy);
    const effective = { max_long_edge_px: value.max_long_edge_px,
      output_mime_type: value.output_mime_type, encoder_quality: value.encoder_quality };
    if (policy.parameters_version === 2) Object.assign(effective, {
      min_long_edge_px: value.min_long_edge_px,
      min_encoder_quality: value.min_encoder_quality,
      encoder_quality_step: value.encoder_quality_step,
      dimension_scale_step: value.dimension_scale_step,
      max_file_size_bytes: value.max_file_size_bytes,
    });
    const normalized = { contract_version: policy.contract_version, parameters_version: policy.parameters_version,
      profile: policy.profile, policy_version: policy.policy_version,
      effective_parameters: Object.freeze(effective) };
    if (policy.parameters_version === 2) normalized.max_file_size_bytes = value.max_file_size_bytes;
    return Object.freeze(normalized);
  }
  function outputName(name) {
    const base = name.replace(/\.[^.]*$/, '').trim() || 'photo';
    return `${base.slice(0, 196)}.jpg`;
  }
  async function decode(file) {
    if (globalThis.createImageBitmap) {
      try {
        const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
        return { image: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() };
      } catch (_) { /* HTMLImageElement remains a broadly supported fallback. */ }
    }
    const url = URL.createObjectURL(file);
    try {
      const image = new Image(); image.src = url; await image.decode();
      return { image, width: image.naturalWidth, height: image.naturalHeight, close: () => URL.revokeObjectURL(url) };
    } catch (_) {
      URL.revokeObjectURL(url);
      invalid('Cette image ne peut pas être décodée sur cet appareil. Choisissez un fichier JPEG, PNG ou WebP compatible.');
    }
  }
  async function prepare(file, policy) {
    if (!(file instanceof Blob) || !file.size) invalid('Sélectionnez une image non vide.');
    if (file.size > MAX_BYTES) invalid('Le fichier dépasse la limite de 20 Mio.');
    const parameters = policyParameters(policy);
    let sourceBuffer = await file.arrayBuffer();
    inspect(sourceBuffer, file.name, file.type);
    sourceBuffer = null;
    const decoded = await decode(file);
    let canvas;
    try {
      if (!decoded.width || !decoded.height || decoded.width * decoded.height > MAX_PIXELS) {
        invalid('Dimensions décodées invalides : 25 millions de pixels maximum.');
      }
      const scale = Math.min(1, parameters.max_long_edge_px / Math.max(decoded.width, decoded.height));
      let width = Math.max(1, Math.round(decoded.width * scale));
      let height = Math.max(1, Math.round(decoded.height * scale));
      canvas = document.createElement('canvas');
      const draw = () => {
        canvas.width = width; canvas.height = height;
        const context = canvas.getContext('2d', { alpha: false });
        if (!context) invalid('La préparation de l’image n’est pas disponible sur cet appareil.');
        context.fillStyle = '#fff'; context.fillRect(0, 0, width, height);
        context.drawImage(decoded.image, 0, 0, width, height);
      };
      const encode = quality => new Promise(resolve => canvas.toBlob(
        resolve, parameters.output_mime_type, quality,
      ));
      draw();
      let quality = parameters.encoder_quality;
      let blob = await encode(quality);
      let encodingAttempts = 1, qualityReductions = 0, dimensionReductions = 0;
      if (!blob) invalid('L’encodage JPEG a échoué sur cet appareil.');
      while (parameters.adaptive && blob.size > parameters.max_file_size_bytes
          && quality > parameters.min_encoder_quality) {
        const next = Math.max(parameters.min_encoder_quality,
          Math.round((quality - parameters.encoder_quality_step) * 100) / 100);
        if (next === quality) break;
        quality = next; blob = await encode(quality); encodingAttempts++; qualityReductions++;
        if (!blob) invalid('L’encodage JPEG a échoué sur cet appareil.');
      }
      while (parameters.adaptive && blob.size > parameters.max_file_size_bytes
          && Math.max(width, height) > parameters.min_long_edge_px) {
        const currentLongEdge = Math.max(width, height);
        const nextLongEdge = Math.max(parameters.min_long_edge_px,
          Math.floor(currentLongEdge * parameters.dimension_scale_step));
        if (nextLongEdge >= currentLongEdge) break;
        const resize = nextLongEdge / currentLongEdge;
        width = Math.max(1, Math.round(width * resize));
        height = Math.max(1, Math.round(height * resize));
        draw(); dimensionReductions++;
        // A smaller raster may fit at a better JPEG quality. Restart from the
        // profile's nominal quality, then apply the same bounded quality ladder.
        quality = parameters.encoder_quality;
        blob = await encode(quality); encodingAttempts++;
        if (!blob) invalid('L’encodage JPEG a échoué sur cet appareil.');
        while (blob.size > parameters.max_file_size_bytes
            && quality > parameters.min_encoder_quality) {
          const next = Math.max(parameters.min_encoder_quality,
            Math.round((quality - parameters.encoder_quality_step) * 100) / 100);
          if (next === quality) break;
          quality = next; blob = await encode(quality); encodingAttempts++; qualityReductions++;
          if (!blob) invalid('L’encodage JPEG a échoué sur cet appareil.');
        }
      }
      if (blob.size > parameters.max_file_size_bytes) {
        invalid('Cette photo ne peut pas respecter la taille maximale choisie sans dépasser les limites de qualité autorisées.');
      }
      if (blob.size > MAX_BYTES) invalid('L’image préparée dépasse encore la limite absolue de 20 Mio.');
      const filename = outputName(file.name || 'photo');
      let finalBuffer = await blob.arrayBuffer();
      const finalMetadata = inspect(finalBuffer, filename, parameters.output_mime_type);
      if (finalMetadata.width !== width || finalMetadata.height !== height) invalid('Dimensions de l’image préparée incohérentes.');
      const sha256 = hex(await crypto.subtle.digest('SHA-256', finalBuffer));
      finalBuffer = null;
      const prepared = typeof File === 'function'
        ? new File([blob], filename, { type: parameters.output_mime_type, lastModified: Date.now() })
        : blob.slice(0, blob.size, parameters.output_mime_type);
      return { ...finalMetadata, originalFilename: filename, sourceFilename: file.name,
        sha256, blob: prepared, validationVersion: 1, policyVersion: policy.policy_version,
        policyContractVersion: policy.contract_version, policyParametersVersion: policy.parameters_version,
        profile: policy.profile, encoderQuality: quality,
        adaptation: Object.freeze({ encodingAttempts, qualityReductions, dimensionReductions }) };
    } catch (error) {
      if (error?.message) throw error;
      invalid('La préparation de cette image a échoué.');
    } finally {
      decoded.close();
      if (canvas) { canvas.width = 1; canvas.height = 1; }
    }
  }
  async function persistence() {
    try {
      const storage = navigator.storage;
      const persistent = await storage?.persisted?.() || await storage?.persist?.() || false;
      return { persistent, ...await storage?.estimate?.() };
    } catch (_) { return { persistent: false }; }
  }
  return { prepare, inspect, persistence, normalizePolicy, MAX_BYTES, MAX_PIXELS };
});
