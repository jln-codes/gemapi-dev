(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }

  root.AppHeader = api;
  const header = root.document?.querySelector(".app-header[data-interface]");
  if (!header) return;
  const interfaceType = header.dataset.interface;
  api.render(header, interfaceType);
  const switcher = header.querySelector('.view-switcher');
  if (root.BusinessRuntime && switcher) switcher.href = root.BusinessRuntime.pageUrl(switcher.getAttribute('href'));
  root.AppHeaderController = api.createController({
    header,
    interfaceType,
    windowTarget: root,
    documentTarget: root.document,
  });
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const INTERFACES = new Set(["map", "data", "admin"]);
  const NETWORK_STATUSES = new Set(["unknown", "checking", "online", "offline"]);
  const DATA_MIN_WIDTH = 700;
  const CONFIG = Object.freeze({
    admin: Object.freeze({ status: "Administration · en ligne uniquement" }),
    map: Object.freeze({
      switchHref: "/base",
      switchTarget: "data",
      switchLabel: "Base de données",
      status: "Chargement des données…",
    }),
    data: Object.freeze({
      switchHref: "/carte",
      switchTarget: "map",
      switchLabel: "Cartographie",
      status: "Connexion au backend…",
    }),
  });

  function capabilities({ interfaceType, width, standalone }) {
    if (!INTERFACES.has(interfaceType)) throw new TypeError("Interface inconnue");
    const canAccessData = interfaceType !== "admin" && !standalone && Number(width) >= DATA_MIN_WIDTH;
    return Object.freeze({
      canAccessData,
      canShowInterfaceSwitch: canAccessData,
      canUseInterInterfacePreferences: canAccessData,
      canUseDesktopMapTools: interfaceType === "map" && !standalone,
      canShowExternalMapLayers: interfaceType === "map" && !standalone,
      canUseFieldMapTools: interfaceType === "map",
    });
  }

  function isStandalone(windowTarget) {
    return Boolean(
      windowTarget?.navigator?.standalone
      || windowTarget?.matchMedia?.("(display-mode: standalone)")?.matches,
    );
  }

  function normalizeNetworkState(state) {
    if (NETWORK_STATUSES.has(state?.status)) return state.status;
    if (typeof state?.offline === "boolean") return state.offline ? "offline" : "online";
    return "unknown";
  }

  function networkStateLabel(status) {
    return ({ unknown: "État du serveur non déterminé", checking: "Vérification de l’accès au serveur",
      online: "Serveur accessible", offline: "Serveur inaccessible — mode local" })[status]
      || "État du serveur non déterminé";
  }

  function networkStateIcon(status, standalone) {
    const icon = standalone ? "wifi" : "network";
    return status === "offline" ? `${icon}-off` : icon;
  }

  function mapCommands() {
    return `
      <div class="create-menu">
        <button id="toggle-create-menu" type="button" class="menu-button"
          aria-controls="create-menu-list" aria-expanded="false">Créer</button>
        <div id="create-menu-list" class="create-menu-list" role="menu" hidden>
          <button type="button" role="menuitem" data-create-type="systeme">Système d'endiguement</button>
          <button type="button" role="menuitem" data-create-type="digue">Digue</button>
          <button type="button" role="menuitem" data-create-type="troncon">Tronçon</button>
          <button type="button" role="menuitem" data-create-type="desordre">Désordre</button>
          <button type="button" role="menuitem" data-create-type="mobilier">Mobilier</button>
          <button type="button" role="menuitem" data-create-type="equipement-mesure">Équipement de mesure</button>
          <button type="button" role="menuitem" data-create-type="reseau-technique">Réseau technique</button>
          <button type="button" role="menuitem" data-create-type="ouvrage-particulier">Ouvrage particulier</button>
          <button type="button" role="menuitem" data-create-type="troncons-lit">Tronçon de lit</button>
          <button type="button" role="menuitem" data-create-type="elements-morphologiques-lit">Élément morphologique du lit</button>
          <button type="button" role="menuitem" data-create-type="cheminement">Cheminement</button>
          <button type="button" role="menuitem" data-create-type="vegetation">Végétation</button>
          <button type="button" role="menuitem" data-create-type="amenagement-hydraulique">AH réglementaire</button>
          <button type="button" role="menuitem" data-create-type="zone-stockage">Zone de stockage</button>
          <button type="button" role="menuitem" data-create-type="prestation">Prestation</button>
        </div>
      </div>
      <div class="tools-menu">
        <button id="toggle-tools-menu" type="button" class="menu-button"
          aria-controls="tools-menu-list" aria-expanded="false">Outils</button>
        <div id="tools-menu-list" class="tools-menu-list" role="menu" hidden>
          <button type="button" role="menuitem" data-tool-action="territoire-administratif"
            data-capability="desktop-map-tools">Territoire administratif…</button>
          <button type="button" role="menuitem" data-tool-action="print-map"
            data-capability="desktop-map-tools">Imprimer la carte…</button>
          <button type="button" role="menuitem" data-tool-action="offline-consultation"
            data-capability="field-map-tools" aria-haspopup="dialog">Consultation hors ligne…</button>
          <button type="button" role="menuitem" data-tool-action="offline-basemaps"
            aria-haspopup="dialog">Préparer les fonds hors ligne…</button>
          <button type="button" role="menuitem"
            data-tool-action="disorder-catalogue">Catalogue des désordres…</button>
        </div>
      </div>
      <button id="toggle-pwa-settings" type="button" class="menu-button"
        aria-controls="pwa-settings-panel" aria-expanded="false" hidden>Paramètres</button>`;
  }

  function dataCommands() {
    return `
      <button id="show-statistics" class="menu-button" type="button"
        aria-controls="data-statistics-view" aria-expanded="true">Statistiques</button>
      <button id="toggle-views" class="menu-button" type="button"
        aria-controls="data-view-builder-workspace" aria-expanded="false">Vues</button>
      <button id="toggle-queries" class="menu-button" type="button"
        aria-controls="queries-view" aria-expanded="false">Requêtes SQL</button>
      <div class="tools-menu">
        <button id="toggle-tools-menu" type="button" class="menu-button"
          aria-controls="tools-menu-list" aria-expanded="false">Outils</button>
        <div id="tools-menu-list" class="tools-menu-list" role="menu" hidden>
          <button type="button" role="menuitem" data-tool-action="offline-consultation"
            aria-haspopup="dialog">Synchronisation locale</button>
          <button type="button" role="menuitem"
            data-tool-action="disorder-catalogue">Catalogue des désordres…</button>
        </div>
      </div>`;
  }

  function settings() {
    return `
      <details class="settings-menu">
        <summary class="menu-button settings-menu-toggle">Paramètres</summary>
        <div class="settings-menu-list">
          <label data-capability="inter-interface-preferences">
            <input id="sync-heritage-selection" type="checkbox">
            Synchroniser les sélections patrimoine
          </label>
          <label data-capability="inter-interface-preferences">
            <input id="open-interface-in-new-tab" type="checkbox">
            Ouvrir Carte/Base dans un nouvel onglet
          </label>
          <label for="url-content-mode">Contenu de l'adresse
            <select id="url-content-mode">
              <option value="minimal">Minimal</option>
              <option value="position">Position cartographique</option>
              <option value="object">Objet sélectionné</option>
            </select>
          </label>
          <span id="url-content-status" role="status"></span>
        </div>
      </details>`;
  }

  function interfaceIcon(name) {
    const paths = name === "map"
      ? '<path d="m3 6 6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14"/>'
      : '<rect x="3" y="4" width="3" height="3" rx=".5"/><rect x="3" y="10.5" width="3" height="3" rx=".5"/><rect x="3" y="17" width="3" height="3" rx=".5"/><path d="M10 5.5h11M10 12h11M10 18.5h11"/>';
    return `<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
      stroke-linejoin="round">${paths}</svg>`;
  }

  function interfaceToggle(interfaceType, config) {
    const map = interfaceType === "map"
      ? `<button type="button" class="view-toggle-segment" aria-label="Carte"
          aria-current="page" title="Carte — page active">${interfaceIcon("map")}</button>`
      : `<a class="view-toggle-segment view-switcher" href="${config.switchHref}"
          data-interface-target="map" aria-label="Carte" title="Ouvrir ${config.switchLabel}">${interfaceIcon("map")}</a>`;
    const data = interfaceType === "data"
      ? `<button type="button" class="view-toggle-segment" aria-label="Base"
          aria-current="page" title="Base — page active">${interfaceIcon("data")}</button>`
      : `<a class="view-toggle-segment view-switcher" href="${config.switchHref}"
          data-interface-target="data" aria-label="Base" title="Ouvrir ${config.switchLabel}">${interfaceIcon("data")}</a>`;
    return `<div class="view-toggle" role="group" aria-label="Vue Carte ou Base">${map}${data}</div>`;
  }

  function mountNotifications(header) {
    if (!header.parentElement || !header.querySelector) return;
    const status = header.querySelector("#status");
    if (!status) return;
    const documentTarget = header.ownerDocument;
    const notifications = documentTarget.createElement("div");
    notifications.className = "header-notifications";
    const panel = documentTarget.createElement("div");
    panel.className = "backend-status-panel";
    const close = documentTarget.createElement("button");
    close.type = "button";
    close.className = "backend-status-close icon-button";
    close.setAttribute("aria-label", "Fermer le message");
    close.textContent = "×";
    panel.append(status, close);
    notifications.append(panel);
    header.after(notifications);

    const signature = () => `${status.textContent}\u0000${status.classList.contains("error")}`;
    let lastSignature = signature();
    const MutationObserverClass = documentTarget.defaultView?.MutationObserver;
    if (MutationObserverClass) {
      new MutationObserverClass(() => {
        const current = signature();
        if (current === lastSignature) return;
        lastSignature = current;
        panel.hidden = !status.textContent.trim();
      }).observe(status, { attributes: true, attributeFilter: ["class"], childList: true, characterData: true, subtree: true });
    }
    close.addEventListener("click", () => {
      panel.hidden = true;
      const navigation = header.querySelector(".main-navigation");
      if (navigation) {
        navigation.tabIndex = -1;
        navigation.focus({ preventScroll: true });
      }
    });
  }

  function render(header, interfaceType) {
    const config = CONFIG[interfaceType];
    if (!config) throw new TypeError("Interface inconnue");
    if (interfaceType === "admin") {
      header.innerHTML = `
        <div class="app-brand"><strong>digues.app</strong><span>administration · online-only</span></div>
        <div class="header-navigation"><div class="app-navigation-viewport">
          <nav class="main-navigation" aria-label="Administration">
            <a class="menu-button" data-admin-route href="/">Paramètres</a>
            <a class="menu-button" data-admin-route href="/structures">Structures</a>
            <a class="menu-button" data-admin-route href="/bases">Bases</a>
            <a class="menu-button" data-admin-route href="/users">Utilisateurs</a>
            <a class="menu-button" data-admin-route href="/jobs">Jobs</a>
            <a class="menu-button" data-admin-route href="/diagnostics">Santé</a>
          </nav></div></div>
        <span id="identity"></span><button id="logout" type="button" class="menu-button">Se déconnecter</button>`;
      return header;
    }
    const commands = interfaceType === "map" ? mapCommands() : dataCommands();
    header.innerHTML = `
      <div class="app-brand">
        <strong>digues.app</strong>
        <span>version expérimentale</span>
      </div>
      <div class="header-navigation">
        <div class="app-navigation-viewport">
          <nav class="main-navigation" aria-label="Navigation ${interfaceType === "map" ? "principale" : "Données"}">
            ${commands}
            ${settings()}
            <button id="toggle-ai" type="button" class="menu-button"
              aria-controls="ai-panel" aria-expanded="false">Assistant IA</button>
            <button id="toggle-help" type="button" class="menu-button"
              aria-controls="help-modal" aria-expanded="false" aria-haspopup="dialog">Aide</button>
          </nav>
        </div>
      </div>
      <button id="toggle-heritage" class="heritage-tab" type="button"
        aria-controls="heritage-panel" aria-expanded="false" aria-label="Ouvrir le patrimoine"
        title="Ouvrir le patrimoine"><svg class="heritage-tab-shape" viewBox="0 0 24 150"
          preserveAspectRatio="none" aria-hidden="true"><path d="M0 0 19 9 Q24 11 24 15 V135 Q24 139 19 141 L0 150 Z"/></svg>
        <span aria-hidden="true">${[..."PATRIMOINE"].map(letter => `<span>${letter}</span>`).join("")}</span></button>
      <div class="header-actions" role="group" aria-label="Choix de vue, compte et état du serveur">
        ${interfaceToggle(interfaceType, config)}
        <button id="open-business-session" class="header-action" type="button" aria-label="Compte et base, contrôle à venir" aria-disabled="true" title="Compte et base : contrôle à venir">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.657 3.582 3 8 3s8 -1.343 8 -3V6"/><path d="M4 12v6c0 1.657 3.582 3 8 3s8 -1.343 8 -3v-6"/></svg>
        </button>
        <span id="network-status" class="header-action header-network-status" role="img" aria-label="État du serveur en cours de vérification" title="État du serveur en cours de vérification" aria-live="polite" aria-atomic="true">
          <svg data-network-icon="network" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M6 9a6 6 0 1 0 12 0a6 6 0 0 0 -12 0"/><path d="M12 3c1.333 .333 2 2.333 2 6s-.667 5.667 -2 6"/><path d="M12 3c-1.333 .333 -2 2.333 -2 6s.667 5.667 2 6"/><path d="M6 9h12"/><path d="M3 20h7"/><path d="M14 20h7"/><path d="M10 20a2 2 0 1 0 4 0a2 2 0 0 0 -4 0"/><path d="M12 15v3"/></svg>
          <svg data-network-icon="network-off" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" hidden><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M6.528 6.536a6 6 0 0 0 7.942 7.933m2.247 -1.76a6 6 0 0 0 -8.427 -8.425"/><path d="M12 3c1.333 .333 2 2.333 2 6c0 .337 -.006 .66 -.017 .968m-.55 3.473c-.333 .884 -.81 1.403 -1.433 1.559"/><path d="M12 3c-.936 .234 -1.544 1.29 -1.822 3.167m-.16 3.838c.116 3.029 .776 4.695 1.982 4.995"/><path d="M6 9h3m4 0h5"/><path d="M3 20h7"/><path d="M14 20h7"/><path d="M10 20a2 2 0 1 0 4 0a2 2 0 0 0 -4 0"/><path d="M12 15v3"/><path d="M3 3l18 18"/></svg>
          <svg data-network-icon="wifi" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" hidden><path d="M12 18l.01 0"/><path d="M9.172 15.172a4 4 0 0 1 5.656 0"/><path d="M6.343 12.343a8 8 0 0 1 11.314 0"/><path d="M3.515 9.515c4.686 -4.687 12.284 -4.687 17 0"/></svg>
          <svg data-network-icon="wifi-off" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" hidden><path d="M12 18l.01 0"/><path d="M9.172 15.172a4 4 0 0 1 5.656 0"/><path d="M6.343 12.343a7.963 7.963 0 0 1 3.864 -2.14m4.163 .155a7.965 7.965 0 0 1 3.287 2"/><path d="M3.515 9.515a12 12 0 0 1 3.544 -2.455m3.101 -.92a12 12 0 0 1 10.325 3.374"/><path d="M3 3l18 18"/></svg>
        </span>
      </div>
      <p id="status" role="status">${config.status}</p>`;
    mountNotifications(header);
    return header;
  }

  function positionMenu({ button, menu, header, windowTarget, documentTarget }) {
    if (!button || !menu || !header) return;
    const compactPortrait = windowTarget.matchMedia(
      "(orientation: portrait) and (max-width: 1024px)",
    ).matches;
    if (menu.hidden) {
      menu.style.removeProperty("--compact-menu-left");
      menu.style.removeProperty("--compact-menu-top");
      return;
    }
    const headerBottom = Math.max(0, header.getBoundingClientRect().bottom);
    menu.style.setProperty("--compact-menu-top", `${headerBottom}px`);
    if (compactPortrait) {
      menu.style.removeProperty("--compact-menu-left");
      return;
    }
    const margin = 8;
    const anchorRect = button.getBoundingClientRect();
    const menuWidth = menu.getBoundingClientRect().width;
    const viewportWidth = documentTarget.documentElement.clientWidth || windowTarget.innerWidth;
    const maximumLeft = Math.max(margin, viewportWidth - menuWidth - margin);
    const left = Math.min(Math.max(margin, anchorRect.left), maximumLeft);
    menu.style.setProperty("--compact-menu-left", `${left}px`);
  }

  function createController({ header, interfaceType, windowTarget, documentTarget }) {
    if (!header || !INTERFACES.has(interfaceType)) throw new TypeError("Header invalide");
    // Administration has no business switch, field tools, PWA or data redirect.
    if (interfaceType === "admin") {
      return { capabilities: () => capabilities({ interfaceType, width: 0, standalone: false }) };
    }
    const switchToggle = header.querySelector(".view-toggle");
    const interInterfacePreferences = [
      ...header.querySelectorAll('[data-capability="inter-interface-preferences"]'),
    ];
    const desktopTools = [...header.querySelectorAll('[data-capability="desktop-map-tools"]')];
    const externalMapLayers = [
      ...documentTarget.querySelectorAll('[data-capability="external-map-layers"]'),
    ];
    const fieldTools = [...header.querySelectorAll('[data-capability="field-map-tools"]')];
    const settingsMenu = header.querySelector(".settings-menu");
    const settingsToggle = header.querySelector(".settings-menu-toggle");
    const settingsList = header.querySelector(".settings-menu-list");
    const urlModeSelect = header.querySelector("#url-content-mode");
    const urlModeStatus = header.querySelector("#url-content-status");
    const runtime = windowTarget.BusinessRuntime;
    // <details> dispatches toggle asynchronously. Publish the real header edge
    // independently of opening a menu. The same measured edge anchors the
    // notification overlay without putting messages in the navigation flow.
    function publishHeaderEdge() {
      const edge = `${Math.max(0, header.getBoundingClientRect().bottom)}px`;
      header.style.setProperty("--compact-menu-top", edge);
      header.parentElement?.style.setProperty("--app-header-bottom", edge);
      positionSettings();
    }
    urlModeSelect.closest("label").hidden = Boolean(runtime?.legacy);
    urlModeStatus.hidden = Boolean(runtime?.legacy);
    urlModeSelect.value = runtime?.urlMode?.() || "position";
    urlModeSelect.addEventListener("change", () => {
      try {
        runtime.setUrlMode(urlModeSelect.value);
        urlModeStatus.textContent = "";
      } catch (_) {
        urlModeSelect.value = runtime?.urlMode?.() || "position";
        urlModeStatus.textContent = "Préférence non enregistrée : stockage local indisponible.";
      }
    });
    windowTarget.addEventListener("storage", event => {
      if (event.key === runtime?.urlModeKey) urlModeSelect.value = runtime.urlMode();
    });
    const viewport = header.querySelector(".app-navigation-viewport");
    const networkButton = header.querySelector("#network-status");
    const standaloneQuery = windowTarget.matchMedia("(display-mode: standalone)");
    let redirected = false;
    let current = null;
    let networkStatus = "unknown";

    function showNetworkState(state) {
      if (!networkButton) return;
      const status = normalizeNetworkState(state);
      networkStatus = status;
      const label = networkStateLabel(status);
      networkButton.setAttribute("aria-label", label);
      networkButton.title = label;
      networkButton.dataset.networkStatus = status;
      showNetworkIcon();
    }
    function showNetworkIcon() {
      if (!networkButton) return;
      const selected = networkStateIcon(networkStatus, header.dataset.runtime === "standalone");
      networkButton.querySelectorAll("[data-network-icon]").forEach(svg => {
        svg.toggleAttribute("hidden", svg.dataset.networkIcon !== selected);
      });
    }
    windowTarget.addEventListener("digues-app:network-state", event => showNetworkState(event.detail));
    showNetworkState(windowTarget.OfflineRuntime?.getNetworkState?.() || { status: "unknown", offline: null });

    function applyCapabilities() {
      const standalone = isStandalone(windowTarget);
      current = capabilities({
        interfaceType,
        width: documentTarget.documentElement.clientWidth || windowTarget.innerWidth,
        standalone,
      });
      header.dataset.runtime = standalone ? "standalone" : "web";
      showNetworkIcon();
      header.dataset.canAccessData = String(current.canAccessData);
      switchToggle.hidden = !current.canShowInterfaceSwitch;
      interInterfacePreferences.forEach((item) => {
        item.hidden = !current.canUseInterInterfacePreferences;
      });
      desktopTools.forEach((item) => { item.hidden = !current.canUseDesktopMapTools; });
      externalMapLayers.forEach((item) => { item.hidden = !current.canShowExternalMapLayers; });
      fieldTools.forEach((item) => { item.hidden = !current.canUseFieldMapTools; });
      if (interfaceType === "data" && !current.canAccessData && !redirected) {
        redirected = true;
        windowTarget.location.replace(windowTarget.BusinessRuntime?.pageUrl("/carte") || "/carte");
      }
      documentTarget.dispatchEvent(new windowTarget.CustomEvent("digues-app:header-capabilities", {
        detail: current,
      }));
      return current;
    }

    function positionSettings() {
      if (!settingsMenu.open) {
        settingsList.style.removeProperty("--compact-menu-left");
        settingsList.style.removeProperty("--compact-menu-top");
        return;
      }
      positionMenu({
        button: settingsToggle,
        menu: settingsList,
        header,
        windowTarget,
        documentTarget,
      });
    }

    settingsMenu.addEventListener("toggle", positionSettings);
    viewport.addEventListener("scroll", positionSettings, { passive: true });
    windowTarget.addEventListener("resize", applyCapabilities);
    windowTarget.addEventListener("resize", positionSettings);
    windowTarget.addEventListener("orientationchange", positionSettings);
    standaloneQuery.addEventListener?.("change", applyCapabilities);
    if (windowTarget.ResizeObserver) new windowTarget.ResizeObserver(publishHeaderEdge).observe(header);
    publishHeaderEdge();
    applyCapabilities();
    return { applyCapabilities, capabilities: () => current, positionSettings };
  }

  return {
    DATA_MIN_WIDTH,
    capabilities,
    createController,
    isStandalone,
    networkStateLabel,
    networkStateIcon,
    normalizeNetworkState,
    positionMenu,
    render,
  };
});
