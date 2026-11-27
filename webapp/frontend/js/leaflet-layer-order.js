/* Canonical Leaflet pane order shared by Carte and its PWA runtime. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LeafletLayerOrder = api;
})(globalThis, function () {
  "use strict";

  const PANES = Object.freeze({
    BASEMAP: Object.freeze({ name: "tilePane", zIndex: 200, pointerEvents: "auto", builtIn: true }),
    LIDAR: Object.freeze({ name: "lidarRelief", zIndex: 325, pointerEvents: "none" }),
    WEATHER: Object.freeze({ name: "weather-precipitations", zIndex: 340, pointerEvents: "auto" }),
    HYDROMETRY: Object.freeze({ name: "environment-stations", zIndex: 345, pointerEvents: "auto" }),
    TERRITORY_MASK: Object.freeze({ name: "territory-mask", zIndex: 350, pointerEvents: "none" }),
    TERRITORY_OUTLINE: Object.freeze({ name: "territory-outline", zIndex: 360, pointerEvents: "none" }),
    BUSINESS_VECTORS: Object.freeze({ name: "overlayPane", zIndex: 400, pointerEvents: "auto", builtIn: true }),
    BUSINESS_MARKERS: Object.freeze({ name: "markerPane", zIndex: 600, pointerEvents: "auto", builtIn: true }),
    USER_POSITION: Object.freeze({ name: "user-position", zIndex: 650, pointerEvents: "none" }),
    MAP_CANDIDATE: Object.freeze({ name: "map-candidate", zIndex: 660, pointerEvents: "none" }),
    POPUPS: Object.freeze({ name: "popupPane", zIndex: 700, pointerEvents: "auto", builtIn: true }),
  });

  function install(map) {
    if (!map || typeof map.getPane !== "function" || typeof map.createPane !== "function") {
      throw new TypeError("Une carte Leaflet est requise pour installer l'ordre des couches.");
    }
    for (const definition of Object.values(PANES)) {
      let pane = map.getPane(definition.name);
      if (!pane) {
        if (definition.builtIn) {
          throw new Error(`Pane Leaflet standard absent : ${definition.name}.`);
        }
        pane = map.createPane(definition.name);
      }
      pane.style.zIndex = String(definition.zIndex);
      pane.style.pointerEvents = definition.pointerEvents;
    }
    return PANES;
  }

  return { PANES, install };
});
