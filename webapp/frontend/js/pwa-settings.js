(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PwaMapSettings = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const STORAGE_KEY = "digues:pwa-map-settings:v1";
  const VERSION = 1;
  const ANTI_DUPLICATE_METERS = 1.5;
  const LIMITS = Object.freeze({
    distanceMeters: Object.freeze({ min: 2, max: 100 }),
    intervalSeconds: Object.freeze({ min: 1, max: 300 }),
    maxAccuracyMeters: Object.freeze({ min: 5, max: 100 }),
    radiusKm: Object.freeze({ min: 0.5, max: 50 }),
  });
  const DEFAULT_SETTINGS = Object.freeze({
    version: VERSION,
    showUserPosition: false,
    showWazeButton: true,
    tracking: Object.freeze({
      distanceMeters: 5,
      intervalSeconds: 5,
      maxAccuracyMeters: 25,
      smartPolygonCorrection: true,
    }),
    proximity: Object.freeze({ enabled: false, radiusKm: 5 }),
  });

  function boundedNumber(value, name, fallback) {
    if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
    return Math.min(LIMITS[name].max, Math.max(LIMITS[name].min, value));
  }

  function normalizeSettings(value) {
    const tracking = value?.tracking;
    const proximity = value?.proximity;
    return {
      version: VERSION,
      showUserPosition: typeof value?.showUserPosition === "boolean"
        ? value.showUserPosition : DEFAULT_SETTINGS.showUserPosition,
      showWazeButton: typeof value?.showWazeButton === "boolean"
        ? value.showWazeButton : DEFAULT_SETTINGS.showWazeButton,
      tracking: {
        distanceMeters: boundedNumber(
          tracking?.distanceMeters, "distanceMeters", DEFAULT_SETTINGS.tracking.distanceMeters,
        ),
        intervalSeconds: boundedNumber(
          tracking?.intervalSeconds, "intervalSeconds", DEFAULT_SETTINGS.tracking.intervalSeconds,
        ),
        maxAccuracyMeters: boundedNumber(
          tracking?.maxAccuracyMeters, "maxAccuracyMeters",
          DEFAULT_SETTINGS.tracking.maxAccuracyMeters,
        ),
        smartPolygonCorrection: typeof tracking?.smartPolygonCorrection === "boolean"
          ? tracking.smartPolygonCorrection : DEFAULT_SETTINGS.tracking.smartPolygonCorrection,
      },
      proximity: {
        enabled: typeof proximity?.enabled === "boolean"
          ? proximity.enabled : DEFAULT_SETTINGS.proximity.enabled,
        radiusKm: boundedNumber(
          proximity?.radiusKm, "radiusKm", DEFAULT_SETTINGS.proximity.radiusKm,
        ),
      },
    };
  }

  function localStorageTarget() {
    try {
      return root.localStorage ?? null;
    } catch (_error) {
      return null;
    }
  }

  function loadSettings(storage = localStorageTarget()) {
    try {
      const value = JSON.parse(storage?.getItem(STORAGE_KEY) ?? "null");
      return value?.version === VERSION
        ? normalizeSettings(value) : normalizeSettings(null);
    } catch (_error) {
      return normalizeSettings(null);
    }
  }

  function saveSettings(settings, storage = localStorageTarget()) {
    const normalized = normalizeSettings(settings);
    if (!storage) throw new Error("Stockage local indisponible.");
    storage.setItem(STORAGE_KEY, JSON.stringify(normalized));
    return normalized;
  }

  function resetSettings(storage = localStorageTarget()) {
    if (!storage) throw new Error("Stockage local indisponible.");
    storage.removeItem(STORAGE_KEY);
    return normalizeSettings(null);
  }

  function validCoordinate(position) {
    return Number.isFinite(position?.longitude) && position.longitude >= -180
      && position.longitude <= 180 && Number.isFinite(position?.latitude)
      && position.latitude >= -90 && position.latitude <= 90;
  }

  function validPosition(position) {
    return validCoordinate(position)
      && Number.isFinite(position?.timestamp) && position.timestamp >= 0;
  }

  function haversineMeters(first, second) {
    if (!validCoordinate(first) || !validCoordinate(second)) return NaN;
    const radians = Math.PI / 180;
    const latitudeDifference = (second.latitude - first.latitude) * radians;
    const longitudeDifference = (second.longitude - first.longitude) * radians;
    const latitudeA = first.latitude * radians;
    const latitudeB = second.latitude * radians;
    const chord = Math.sin(latitudeDifference / 2) ** 2
      + Math.cos(latitudeA) * Math.cos(latitudeB)
      * Math.sin(longitudeDifference / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.min(1, Math.sqrt(chord)));
  }

  function shouldAcceptPosition(position, lastAccepted, settings) {
    const { tracking } = normalizeSettings(settings);
    if (!validPosition(position) || !Number.isFinite(position.accuracy)
        || position.accuracy < 0 || position.accuracy > tracking.maxAccuracyMeters) {
      return false;
    }
    if (lastAccepted == null) return true;
    if (!validPosition(lastAccepted) || position.timestamp <= lastAccepted.timestamp) return false;
    const distance = haversineMeters(lastAccepted, position);
    if (distance >= tracking.distanceMeters) return true;
    return position.timestamp - lastAccepted.timestamp >= tracking.intervalSeconds * 1000
      && distance > ANTI_DUPLICATE_METERS;
  }

  return {
    STORAGE_KEY, DEFAULT_SETTINGS, LIMITS, ANTI_DUPLICATE_METERS,
    normalizeSettings, loadSettings, saveSettings, resetSettings,
    haversineMeters, shouldAcceptPosition,
  };
});
