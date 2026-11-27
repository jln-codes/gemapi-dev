/* Local GPS geometry preparation. PostGIS remains the final authority. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PwaGeometry = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const MAX_POINTS = 10000;
  const same = (a, b) => a[0] === b[0] && a[1] === b[1];
  const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const bits = new DataView(new ArrayBuffer(8));
  function parts(number) {
    bits.setFloat64(0, number);
    const high = bits.getUint32(0), low = bits.getUint32(4);
    const exponent = (high >>> 20) & 2047;
    let mantissa = (BigInt(high & 0xfffff) << 32n) | BigInt(low);
    if (exponent) mantissa |= 1n << 52n;
    if (high >>> 31) mantissa = -mantissa;
    return [mantissa, exponent ? exponent - 1075 : -1074];
  }
  function integers(numbers) {
    const values = numbers.map(parts);
    const exponent = Math.min(...values.map(value => value[1]));
    return values.map(([mantissa, power]) => mantissa << BigInt(power - exponent));
  }
  // Fast determinant with an exact dyadic fallback near floating point error.
  // No distance tolerance or minimum business area is introduced here.
  function orientation(a, b, c) {
    const left = (b[0] - a[0]) * (c[1] - a[1]);
    const right = (b[1] - a[1]) * (c[0] - a[0]);
    const determinant = left - right;
    if (Math.abs(determinant) > (Math.abs(left) + Math.abs(right)) * 4 * Number.EPSILON) {
      return Math.sign(determinant);
    }
    const [ax, ay, bx, by, cx, cy] = integers([...a, ...b, ...c]);
    const exact = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    return exact < 0n ? -1 : exact > 0n ? 1 : 0;
  }
  function between(a, b, p) {
    return p[0] >= Math.min(a[0], b[0]) && p[0] <= Math.max(a[0], b[0])
      && p[1] >= Math.min(a[1], b[1]) && p[1] <= Math.max(a[1], b[1]);
  }
  function intersects(a, b, c, d) {
    const ac = orientation(a, b, c), ad = orientation(a, b, d);
    const ca = orientation(c, d, a), cb = orientation(c, d, b);
    return ac * ad < 0 && ca * cb < 0 || ac === 0 && between(a, b, c)
      || ad === 0 && between(a, b, d) || ca === 0 && between(c, d, a)
      || cb === 0 && between(c, d, b);
  }
  function coordinatesValid(coordinates) {
    return Array.isArray(coordinates) && coordinates.length === 2
      && coordinates.every(Number.isFinite)
      && Math.abs(coordinates[0]) <= 180 && Math.abs(coordinates[1]) <= 90;
  }
  function useful(coordinates) {
    return coordinates.filter((point, index) => index === 0 || !same(point, coordinates[index - 1]));
  }
  function nonzeroArea(ring) {
    const origin = ring[0];
    let area = 0, compensation = 0, magnitude = 0;
    for (let index = 1; index < ring.length - 1; index += 1) {
      const a = ring[index], b = ring[index + 1];
      const value = (a[0] - origin[0]) * (b[1] - origin[1])
        - (a[1] - origin[1]) * (b[0] - origin[0]);
      magnitude += Math.abs(value);
      const adjusted = value - compensation, next = area + adjusted;
      compensation = next - area - adjusted; area = next;
    }
    if (Math.abs(area) > magnitude * ring.length * Number.EPSILON * 4) return true;
    const values = integers(ring.flat());
    let exact = 0n;
    for (let index = 0; index < ring.length - 1; index += 1) {
      exact += values[index * 2] * values[index * 2 + 3] - values[index * 2 + 1] * values[index * 2 + 2];
    }
    return exact !== 0n;
  }
  function inside(point, ring) {
    let winding = 0;
    for (let index = 0; index < ring.length - 1; index += 1) {
      const a = ring[index], b = ring[index + 1];
      if (a[1] <= point[1] && b[1] > point[1] && orientation(a, b, point) > 0) winding++;
      if (a[1] > point[1] && b[1] <= point[1] && orientation(a, b, point) < 0) winding--;
    }
    return winding !== 0;
  }
  function validate(geometry, allowedTypes = ["LineString", "Polygon"]) {
    if (!geometry || !allowedTypes.includes(geometry.type) || !["LineString", "Polygon"].includes(geometry.type)) {
      throw new Error("Ce type de géométrie n’est pas autorisé pour ce relevé.");
    }
    const polygon = geometry.type === "Polygon";
    const rings = polygon ? geometry.coordinates : [geometry.coordinates];
    if (!Array.isArray(rings) || !rings.length) throw new Error("Le relevé est vide.");
    let count = 0;
    const cleaned = rings.map(ring => {
      if (!Array.isArray(ring) || !ring.every(coordinatesValid)) throw new Error("Coordonnées longitude/latitude invalides.");
      count += ring.length;
      if (count > MAX_POINTS + rings.length) throw new Error("Le relevé dépasse la limite de sommets.");
      if (polygon && (ring.length < 4 || !same(ring[0], ring.at(-1)))) throw new Error("Le contour doit être fermé et comporter trois sommets utiles.");
      const result = useful(ring);
      if (result.length < (polygon ? 4 : 2)) throw new Error(polygon
        ? "Trois positions distinctes sont nécessaires." : "Deux positions distinctes sont nécessaires.");
      return result;
    });
    if (!polygon) return geometry;
    const edges = [];
    cleaned.forEach((ring, ringId) => {
      for (let index = 0; index < ring.length - 1; index += 1) {
        const a = ring[index], b = ring[index + 1], c = ring[(index + 2) % (ring.length - 1)];
        if (orientation(a, b, c) === 0 && !between(a, c, b)) throw new Error("Le contour comporte un segment en aller-retour.");
        edges.push({ a, b, ringId, index, size: ring.length - 1,
          minX: Math.min(a[0], b[0]), maxX: Math.max(a[0], b[0]),
          minY: Math.min(a[1], b[1]), maxY: Math.max(a[1], b[1]) });
      }
    });
    edges.sort((a, b) => a.minX - b.minX);
    // Bounding-box sweep avoids comparing disjoint portions of long tracks.
    for (let i = 0; i < edges.length; i++) {
      const a = edges[i];
      for (let j = i + 1; j < edges.length && edges[j].minX <= a.maxX; j++) {
        const b = edges[j];
        if (a.maxY < b.minY || b.maxY < a.minY) continue;
        const gap = Math.abs(a.index - b.index);
        if (a.ringId === b.ringId && (gap === 1 || gap === a.size - 1)) continue;
        if (intersects(a.a, a.b, b.a, b.b)) throw new Error("Le contour se croise ou se touche : ajustez les sommets.");
      }
    }
    cleaned.forEach((ring, index) => {
      if (!nonzeroArea(ring)) throw new Error("Le polygone est sans surface.");
      if (index && (!inside(ring[0], cleaned[0]) || cleaned.some((other, j) => j > 0 && j !== index && inside(ring[0], other)))) {
        throw new Error("Les anneaux du polygone se recouvrent ou sont hors du contour.");
      }
    });
    return geometry;
  }
  function fromPoints(type, points) {
    if (!["LineString", "Polygon"].includes(type)) throw new Error("Type de tracé GPS non pris en charge.");
    let coordinates = useful(points.map(point => [point.longitude, point.latitude]));
    if (type === "Polygon" && coordinates.length > 1 && same(coordinates[0], coordinates.at(-1))) coordinates = coordinates.slice(0, -1);
    if (coordinates.length < (type === "Polygon" ? 3 : 2)) throw new Error(type === "Polygon"
      ? "Trois positions distinctes sont nécessaires." : "Deux positions distinctes sont nécessaires.");
    return { type, coordinates: type === "Polygon" ? [[...coordinates, coordinates[0].slice()]] : coordinates };
  }
  function geometryFromPoints(type, points) { return validate(fromPoints(type, points)); }

  function proposePolygonCorrection(points, settings) {
    if (points.length < 4) return null;
    let remaining = 150000, attempts = 0, candidateVertices = 0;
    const distance = (a, b) => {
      if (--remaining < 0) throw new Error("budget");
      return settings.haversineMeters(a, b);
    };
    const tolerance = (a, b) => Math.min(15, Math.max(5, a.accuracy || 0, b.accuracy || 0));
    const close = candidate => {
      if (candidate.length < 4) return candidate;
      const first = candidate[0], last = candidate.at(-1), previous = candidate.at(-2);
      const gap = distance(first, last);
      return gap > 0 && gap <= tolerance(first, last) && gap < distance(previous, last) / 2 ? candidate.slice(0, -1) : candidate;
    };
    const candidates = new Map();
    function add(candidate, reason, refine = true) {
      if (++attempts > 64) throw new Error("budget");
      candidateVertices += candidate.length;
      if (candidateVertices > 4000) throw new Error("budget");
      const selected = refine ? close(candidate) : candidate;
      try {
        const geometry = geometryFromPoints("Polygon", selected);
        candidates.set(JSON.stringify(geometry.coordinates), { geometry, reason });
      } catch (_) { /* A rejected candidate never replaces raw points. */ }
    }
    try {
      const closed = close(points);
      if (closed !== points) add(closed, "Fermeture proche du départ", false);
      const coordinates = points.map(p => [p.longitude, p.latitude]);
      for (let i = 0; i < points.length - 3; i++) {
        for (let j = i + 2; j <= points.length - 2; j++) {
          // Every possible local correction has diameter <= 30 m. Once this
          // contiguous window leaves that radius, no later end can qualify.
          if (distance(points[i], points[j]) > 30) break;
          const crossing = intersects(coordinates[i], coordinates[i + 1], coordinates[j], coordinates[j + 1]);
          // Extend beyond 12 only with an actual crossing/exact return, not
          // merely a nearby trace which could be a real small terrain detail.
          const returnsNear = j - i >= 3 && (j - i <= 12 || same(coordinates[i], coordinates[j]))
            && distance(points[i], points[j]) <= tolerance(points[i], points[j]);
          if (!crossing && !returnsNear) continue;
          const local = points.slice(i, j + (returnsNear ? 1 : 2));
          const limit = 2 * tolerance(points[i], points[j + 1]);
          if (local.some((a, index) => local.slice(index + 1).some(b => distance(a, b) > limit))) continue;
          add([...points.slice(0, i + 1), ...points.slice(j + 1)], crossing ? "Petite boucle croisée" : "Petite boucle GPS");
          if (candidates.size > 1) return { ambiguous: true };
        }
      }
    } catch (_) {
      // Incomplete search must never select the first candidate arbitrarily.
      return { limited: true };
    }
    return candidates.values().next().value || null;
  }
  function prepare(type, points, settings) {
    const rawGeometry = fromPoints(type, points);
    let validationError = null;
    try { validate(rawGeometry); } catch (error) { validationError = error.message; }
    const proposal = type === "Polygon" && settings.loadSettings().tracking.smartPolygonCorrection
      ? proposePolygonCorrection(points, settings) : null;
    const correctedGeometry = proposal?.geometry || null;
    return { rawGeometry, correctedGeometry, geometry: clone(correctedGeometry || rawGeometry),
      correction: proposal?.reason || null, choice: correctedGeometry ? "corrected" : "raw",
      validationError: correctedGeometry ? null : validationError,
      correctionWarning: proposal?.ambiguous ? "Plusieurs corrections possibles : ajustez le tracé brut."
        : proposal?.limited ? "Correction automatique limitée : vérifiez les sommets." : null };
  }
  return { clone, validate, fromPoints, geometryFromPoints, proposePolygonCorrection, prepare };
});
