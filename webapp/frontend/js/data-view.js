(function initializeDataView() {
  "use strict";

  const appRoot = document.querySelector("main");
  const workspace = document.querySelector("#app-workspace");
  const statisticsView = document.querySelector("#data-statistics-view");
  const statisticsButton = document.querySelector("#show-statistics");
  const detailView = document.querySelector("#data-entity-detail");
  const viewsButton = document.querySelector("#toggle-views");
  const status = document.querySelector("#status");
  const queriesView = document.querySelector("#queries-view");
  const queriesButton = document.querySelector("#toggle-queries");
  const queriesCloseButton = document.querySelector("#close-queries");
  const toolsButton = document.querySelector("#toggle-tools-menu");
  const toolsMenuList = document.querySelector("#tools-menu-list");
  const header = document.querySelector(".app-header");
  const navigationViewport = header.querySelector(".app-navigation-viewport");
  const baseSummary = document.querySelector("#base-summary");
  const baseSummaryCrs = document.querySelector("#base-summary-crs");
  const heritageSelectionSyncCheckbox = document.querySelector(
    "#sync-heritage-selection",
  );
  let showUuid = false;
  // TopNavigation manages side panels; this mode owns the central workspace.
  let centralMode = "statistics";
  let selectedUrlTarget = null;
  let selectedHeritageObject = null;
  const hiddenTronconIds = new Set();
  let heritageSelectionSyncController = null;

  function syncDataUrl() {
    const url = EntityNavigation.automaticUrl(window.location.href, {
      view: "data", mode: window.BusinessRuntime?.urlMode?.() || "position", selected: selectedUrlTarget,
    });
    if (url !== window.location.pathname + window.location.search + window.location.hash) {
      window.history.replaceState(window.history.state, "", url);
    }
  }
  window.addEventListener("digues-app:url-mode-changed", syncDataUrl);
  window.addEventListener("storage", event => {
    if (event.key === window.BusinessRuntime?.urlModeKey) syncDataUrl();
  });

  const viewExportController = DataGeoPackage.createController({
    button: document.querySelector("#data-view-export-gpkg"),
    status: document.querySelector("#data-view-export-status"),
    hideWhenUnavailable: true,
  });
  const viewCsvExportController = DataGeoPackage.createCsvController({
    button: document.querySelector("#data-view-export-csv"),
    status: document.querySelector("#data-view-export-status"),
  });

  function setViewDataset(dataset) {
    viewExportController.setDataset(dataset);
    viewCsvExportController.setDataset(dataset);
  }

  function openEntity(entityClass, entityId, entities = []) {
    const identifier = String(entityId);
    appRoot.dispatchEvent(new CustomEvent("digues-app:open-entity", {
      detail: {
        entityClass, entityId: identifier, entities,
        source: getCentralSource(),
      },
    }));
  }

  const viewBuilderController = DataViewBuilder.createController({
    hiddenTronconIds,
    onOpenEntity: openEntity,
    onDatasetChange: setViewDataset,
  });
  const detailController = BaseDetailController.createController({
    eventTarget: appRoot,
    getSource: getCentralSource,
    onShow({ entityClass, entityId }) {
      appRoot.dataset.selectedEntityClass = entityClass;
      appRoot.dataset.selectedEntityId = entityId;
      showCentralMode("detail");
    },
    onReady({ entityClass, entityId }) {
      try { selectedUrlTarget = EntityNavigation.target(entityClass, entityId); }
      catch (_) { selectedUrlTarget = null; }
      syncDataUrl();
    },
    onClose({ source }) {
      selectedUrlTarget = null;
      syncDataUrl();
      delete appRoot.dataset.selectedEntityClass;
      delete appRoot.dataset.selectedEntityId;
      showCentralMode(source);
    },
    onMutationConfirmed(mutation) {
      return ResourceMutationController.publishMutation(mutation, { applyLocally: true });
    },
  });

  const heritageController = HeritagePanel.createController({
    getShowUuid: () => showUuid,
    hiddenTronconIds,
    async onSelection(selection, { origin = "user" } = {}) {
      if (window.BusinessRuntime && (!BusinessRuntime.isActive() || !BusinessRuntime.can("business.read"))) return false;
      if (origin !== "restore") {
        if (!await detailController.open(selection.resource, selection.item.id)) return false;
        navigation.close("heritage");
      }
      selectedHeritageObject = selection;
      return true;
    },
    onVisibilityChange(nextHiddenTronconIds, selection, context) {
      viewBuilderController.setHiddenTronconIds(nextHiddenTronconIds);
      heritageSelectionSyncController?.selectionChanged(selection, context);
    },
  });
  window.OfflineRuntime?.attachHeritage(heritageController);
  heritageSelectionSyncController = HeritageSelectionSync.createController({
    bus: InterPageBus,
    checkbox: heritageSelectionSyncCheckbox,
    heritageController,
  });

  DataResourceInvalidators.register({
    mutationController: ResourceMutationController,
    heritageController,
    viewController: viewBuilderController,
    detailController,
  });
  const aiController = AiPanel.createController();

  function setQueriesOpen(open) {
    if (open) showCentralMode("sql");
    else if (centralMode === "sql") showCentralMode("statistics");
  }

  function positionToolsMenu() {
    AppHeader.positionMenu({
      button: toolsButton,
      menu: toolsMenuList,
      header,
      windowTarget: window,
      documentTarget: document,
    });
  }

  function setToolsOpen(open) {
    toolsMenuList.hidden = !open;
    toolsButton.setAttribute("aria-expanded", String(open));
    positionToolsMenu();
  }

  navigationViewport.addEventListener("scroll", positionToolsMenu, { passive: true });
  window.addEventListener("resize", positionToolsMenu);
  window.addEventListener("orientationchange", positionToolsMenu);

  function getCentralSource() {
    // Nested entity navigation retains the original return destination.
    return centralMode === "detail" ? detailController.getSource() : centralMode;
  }

  function syncCentralContent() {
    appRoot.dataset.centralView = centralMode;
    statisticsView.hidden = centralMode !== "statistics";
    detailView.hidden = centralMode !== "detail";
    queriesView.hidden = centralMode !== "sql";
    viewBuilderController.setOpen(centralMode === "views");
    statisticsButton.setAttribute("aria-expanded", String(centralMode === "statistics"));
    viewsButton.setAttribute("aria-expanded", String(centralMode === "views"));
    queriesButton.setAttribute("aria-expanded", String(centralMode === "sql"));
    appRoot.classList.toggle("queries-open", centralMode === "sql");
  }

  function showCentralMode(mode) {
    centralMode = mode;
    // SQL remains a TopNavigation entry for its compatibility with IA/panels.
    // Its boolean is an adapter, never an independent central-content choice.
    if (mode === "sql") navigation.open("queries");
    else navigation.close("queries");
    syncCentralContent();
  }

  function activateCentralMode(mode) {
    if (centralMode === "detail" && !detailController.close()) return false;
    showCentralMode(mode);
    return true;
  }

  const navigation = TopNavigation.createController({
    additionalCompatiblePairs: [["heritage", "ai"]],
    entries: {
      heritage: {
        panel: heritageController.panel,
        toggleButton: heritageController.toggleButton,
        closeButton: heritageController.closeButton,
        initialOpen: false,
        setOpen: heritageController.setOpen,
      },
      queries: {
        panel: queriesView,
        toggleButton: queriesButton,
        closeButton: queriesCloseButton,
        initialOpen: false,
        canOpen: () => centralMode !== "detail" || detailController.close(),
        setOpen: setQueriesOpen,
      },
      tools: {
        toggleButton: toolsButton,
        initialOpen: !toolsMenuList.hidden,
        setOpen: setToolsOpen,
      },
      ai: {
        panel: aiController.panel,
        toggleButton: aiController.toggleButton,
        closeButton: aiController.closeButton,
        initialOpen: false,
        setOpen: aiController.setOpen,
      },
    },
    onChange(navigationState, mode) {
      appRoot.dataset.layout = mode;
      for (const name of ["heritage", "ai"]) {
        appRoot.classList.toggle(`${name}-open`, Boolean(navigationState[name]));
      }
      syncCentralContent();
    },
  });

  const catalogueController = window.DisorderReports?.createCatalogueController({
    button: document.querySelector('[data-tool-action="disorder-catalogue"]'),
    status,
    getScope: () => selectedHeritageObject,
    onInvoke: () => navigation.close("tools"),
  });

  statisticsButton.addEventListener("click", () => activateCentralMode("statistics"));
  viewsButton.addEventListener("click", () => activateCentralMode(
    centralMode === "views" ? "statistics" : "views",
  ));

  function syncLayout() {
    navigation.setLayout(workspace.clientWidth, window.innerHeight);
  }
  syncLayout();
  new ResizeObserver(syncLayout).observe(workspace);
  window.addEventListener("resize", syncLayout);

  const initialData = Promise.resolve(window.ResourceEventTransport?.bootstrap).then(
    () => heritageController.load(),
  );
  window.ResourceEventTransport?.start(initialData);

  try {
    const requested = EntityNavigation.fromSearch(window.location.search);
    if (requested) openEntity(requested.entityClass, requested.id);
  } catch (_) { /* Unknown or malformed deep links never select an object. */ }

  window.BusinessRuntime.fetch("/api/base-summary")
    .then((response) => {
      if (!response.ok) throw new Error("Synthèse indisponible");
      return response.json();
    })
    .then((summary) => {
      if (typeof summary?.crs?.label !== "string" || !summary.crs.label.trim()) {
        throw new Error("CRS actif absent");
      }
      baseSummaryCrs.textContent = summary.crs.label;
      baseSummary.hidden = false;
    })
    .catch(() => {
      baseSummary.hidden = true;
    });

  fetch("/api/config")
    .then((response) => {
      if (!response.ok) throw new Error("Backend indisponible");
      return response.json();
    })
    .then((config) => {
      showUuid = Boolean(config.show_uuid);
      document.body.classList.toggle("show-uuid", showUuid);
      status.textContent = "Backend disponible";
      status.classList.remove("error");
    })
    .catch(() => {
      status.textContent = "Backend indisponible";
      status.classList.add("error");
    });
}());
