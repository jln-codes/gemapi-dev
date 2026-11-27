/* Dérivé de map.js et du démarrage Carte/Base de Digues App. */
(function () {
  "use strict";

  const publishNetwork = () => window.dispatchEvent(new CustomEvent("digues-app:network-state", {
    detail: { status: navigator.onLine ? "online" : "offline" },
  }));
  addEventListener("online", publishNetwork);
  addEventListener("offline", publishNetwork);
  publishNetwork();

  const mapElement = document.querySelector("#map");
  if (!mapElement) return;

  const map = L.map(mapElement, { editable: true }).setView([46.8, 2.5], 6);
  map.attributionControl.setPrefix(false);
  LeafletLayerOrder.install(map);
  const panel = document.querySelector("#pwa-settings-panel");
  const open = document.querySelector("#open-technical-basemaps");
  const close = document.querySelector("#close-pwa-settings");
  function setPanel(visible) {
    panel.hidden = !visible;
    open.setAttribute("aria-expanded", String(visible));
    (visible ? close : open).focus();
  }
  open.addEventListener("click", () => setPanel(panel.hidden));
  close.addEventListener("click", () => setPanel(false));
  panel.addEventListener("keydown", event => {
    if (event.key === "Escape") setPanel(false);
  });

  const selector = BasemapSelector.create({
    map, L, basemapApi: Basemap, embedded: true,
    container: document.querySelector("#basemap-selector"),
  });
  void selector.start();

  const locate = document.querySelector("#locate-map");
  const status = document.querySelector("#user-position-status");
  const standalone = matchMedia("(display-mode: standalone)");
  const position = L.layerGroup().addTo(map);
  const tracking = PwaMapPosition.createController({
    geolocation: PwaGeolocation,
    settings: PwaMapSettings,
    onChange(state) {
      if (!state.active) position.clearLayers();
      locate.setAttribute("aria-pressed", String(state.active));
      status.textContent = state.error?.message ||
        ({ acquiring: "Recherche de position…", low_accuracy: "Précision insuffisante." }[state.phase] || "");
    },
    onPosition(fix, { first }) {
      const latLng = [fix.latitude, fix.longitude];
      const pane = LeafletLayerOrder.PANES.USER_POSITION.name;
      position.clearLayers();
      L.circle(latLng, { pane, radius: fix.accuracy, interactive: false }).addTo(position);
      L.marker(latLng, { pane, interactive: false, keyboard: false,
        icon: L.divIcon({ className: "user-position-marker", iconSize: [18, 18] }),
      }).addTo(position);
      if (first) map.setView(latLng, Math.max(map.getZoom(), 16));
    },
  });
  const updateLocate = () => {
    locate.hidden = !standalone.matches;
    if (locate.hidden) tracking.stop();
  };
  locate.setAttribute("aria-label", "Suivre ma position");
  locate.title = "Suivre ma position";
  locate.setAttribute("aria-pressed", "false");
  locate.addEventListener("click", () => {
    if (tracking.getState().active) tracking.stop();
    else tracking.start();
  });
  standalone.addEventListener("change", updateLocate);
  addEventListener("pagehide", tracking.stop);
  updateLocate();
})();
