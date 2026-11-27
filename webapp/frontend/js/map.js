const MAP_PANES = window.LeafletLayerOrder.PANES;
const TERRITORY_MASK_PANE = MAP_PANES.TERRITORY_MASK.name;
const TERRITORY_OUTLINE_PANE = MAP_PANES.TERRITORY_OUTLINE.name;
const WEATHER_PANE = MAP_PANES.WEATHER.name;
const HYDROMETRY_PANE = MAP_PANES.HYDROMETRY.name;
const TRONCON_STYLE = Object.freeze({
  ...MapLayerControls.categoryStyle("troncons").path,
  opacity: 0.85,
});
let basemap = window.Basemap.fallback;
let basemapSelector = null;
let offlineBasemapUi = null;
const WEATHER_LEGEND_CLASSES = Object.freeze([
  ["0 mm", "transparent"],
  [">0–1 mm", "#d7ecfb"],
  ["1–5 mm", "#96cdec"],
  ["5–10 mm", "#4b9fd8"],
  ["10–20 mm", "#2377b7"],
  ["20–40 mm", "#15508a"],
  [">40 mm", "#6f2c8f"],
]);
const mapElement = document.querySelector("#map");
mapElement.style.visibility = "hidden";
const map = L.map("map", {
  zoomControl: false,
  editable: true,
  maxBoundsViscosity: 1,
  // Larger jumps would shrink the current SVG mask beyond its viewport.
  zoomAnimationThreshold: 1,
}).setView([46.8, 2.5], 6);
map.attributionControl.setPrefix(false);
window.LeafletLayerOrder.install(map);
// Keep one renderer for territory replacements; Leaflet scales its SVG viewport
// during zoom animation before recomputing the projected mask.
const territoryMaskRenderer = L.svg({ pane: TERRITORY_MASK_PANE, padding: 1.1 });
const statusElement = document.querySelector("#status");
const heritageToggleButton = document.querySelector("#toggle-heritage");
const aiToggleButton = document.querySelector("#toggle-ai");
const createMenuButton = document.querySelector("#toggle-create-menu");
const createMenuList = document.querySelector("#create-menu-list");
const mapCreateActions = (() => {
  const actions = new Map();
  return {
    register(type, action) {
      if (!type || typeof action !== "function") {
        throw new Error("Action de création cartographique invalide.");
      }
      actions.set(type, action);
    },
    run(type) {
      const action = actions.get(type);
      if (!action) return false;
      Promise.resolve(action()).catch((error) => {
        console.error(`Ouverture de la création ${type} impossible`, error);
      });
      return true;
    },
  };
})();
window.MapCreateActions = mapCreateActions;
const toolsMenuButton = document.querySelector("#toggle-tools-menu");
const toolsMenuList = document.querySelector("#tools-menu-list");
const appHeader = document.querySelector(".app-header");
const headerNavigation = document.querySelector(".header-navigation");
const appNavigationViewport = document.querySelector(".app-navigation-viewport");
const heritageCloseButton = document.querySelector("#close-heritage");
const heritagePanel = document.querySelector("#heritage-panel");
const primaryArea = document.querySelector("#primary-area");
const appWorkspace = document.querySelector("#app-workspace");
const appRoot = document.querySelector("main");
const aiPanel = document.querySelector("#ai-panel");
const aiCloseButton = document.querySelector("#close-ai");
const pwaSettingsToggleButton = document.querySelector("#toggle-pwa-settings");
const pwaSettingsPanel = document.querySelector("#pwa-settings-panel");
const pwaSettingsCloseButton = document.querySelector("#close-pwa-settings");
const pwaSettingsForm = document.querySelector("#pwa-settings-form");
const pwaSettingsMessage = document.querySelector("#pwa-settings-message");
const pwaDistanceMeters = document.querySelector("#pwa-distance-meters");
const pwaIntervalSeconds = document.querySelector("#pwa-interval-seconds");
const pwaMaxAccuracyMeters = document.querySelector("#pwa-max-accuracy-meters");
const pwaSmartPolygonCorrection = document.querySelector("#pwa-smart-polygon-correction");
const pwaShowUserPosition = document.querySelector("#pwa-show-user-position");
const pwaShowWazeButton = document.querySelector("#pwa-show-waze-button");
const pwaProximityEnabled = document.querySelector("#pwa-proximity-enabled");
const pwaRadiusKm = document.querySelector("#pwa-radius-km");
const pwaSettingsResetButton = document.querySelector("#reset-pwa-settings");
const legacySettingsMenu = document.querySelector(".settings-menu");
const mapLegend = document.querySelector("#map-legend");
const weatherLegend = document.querySelector("#weather-legend");
const weatherLegendToggle = document.querySelector("#toggle-weather-legend");
const weatherTimeline = document.querySelector("#weather-timeline");
const weatherTimelineLabels = document.querySelector("#weather-timeline-labels");
const weatherTimelineRange = document.querySelector("#weather-timeline-range");
const weatherTimelineProvenance = document.querySelector("#weather-timeline-provenance");
const directionsButton = document.querySelector("#open-directions");
const locateMapButton = document.querySelector("#locate-map");
const userPositionStatus = document.querySelector("#user-position-status");
let userPositionMessage = "";
let mapLocateMessage = "";
let mapLocateRequest = null;
const layerToggleInputs = document.querySelectorAll("[data-layer-toggle]");
const heritageTree = document.querySelector("#heritage-tree");
const heritageSelectionSyncCheckbox = document.querySelector(
  "#sync-heritage-selection",
);
const editorPanel = document.querySelector("#editor-panel");
const editorMapInteractionBanner = document.querySelector("#editor-map-interaction");
const editorMapInteractionStatus = document.querySelector("#editor-map-interaction-status");
const finishEditorMapInteractionButton = document.querySelector(
  "#finish-editor-map-interaction",
);
const cancelEditorMapInteractionButton = document.querySelector(
  "#cancel-editor-map-interaction",
);
const gpsPointLonlatButton = document.querySelector("#gps-point-lonlat");
const gpsPointXyButton = document.querySelector("#gps-point-xy");
const gpsLineStartButton = document.querySelector("#gps-line-start");
const gpsLineEndButton = document.querySelector("#gps-line-end");
const gpsBornageStartButton = document.querySelector("#gps-bornage-start");
const gpsBornageEndButton = document.querySelector("#gps-bornage-end");
const gpsBornageStartDistance = document.querySelector("#gps-bornage-start-distance");
const gpsBornageEndDistance = document.querySelector("#gps-bornage-end-distance");
const gpsMapCreateButton = document.querySelector("#gps-map-create");
const gpsMapEditButton = document.querySelector("#gps-map-edit");
const gpsCandidatePanel = document.querySelector("#gps-position-candidate");
const gpsCandidateDefaultParent = gpsCandidatePanel.parentElement;
const gpsCandidateStatus = document.querySelector("#gps-candidate-status");
const gpsCandidateDetails = document.querySelector("#gps-candidate-details");
const gpsCandidateLongitude = document.querySelector("#gps-candidate-longitude");
const gpsCandidateLatitude = document.querySelector("#gps-candidate-latitude");
const gpsCandidateAccuracy = document.querySelector("#gps-candidate-accuracy");
const gpsCandidateAcceptButton = document.querySelector("#gps-candidate-accept");
const gpsCandidateRetryButton = document.querySelector("#gps-candidate-retry");
const gpsCandidateCancelButton = document.querySelector("#gps-candidate-cancel");
const heritageObjectForm = document.querySelector("#heritage-object-editor");
const heritageObjectIdField = document.querySelector("#heritage-object-id-field");
const heritageObjectId = document.querySelector("#heritage-object-id");
const heritageParentField = document.querySelector("#heritage-parent-field");
const heritageParentLabel = document.querySelector("#heritage-parent-label");
const heritageParent = document.querySelector("#heritage-parent");
const heritageObjectLabel = document.querySelector("#heritage-object-label");
const heritageObjectDesignation = document.querySelector("#heritage-object-designation");
const heritageObjectCommentaire = document.querySelector("#heritage-object-commentaire");
const heritageObjectPopulation = document.querySelector("#heritage-object-population");
const heritageObjectClassement = document.querySelector("#heritage-object-classement");
const heritageObjectNiveau = document.querySelector("#heritage-object-niveau");
const heritageObjectDateDebut = document.querySelector("#heritage-object-date-debut");
const heritageObjectDateFin = document.querySelector("#heritage-object-date-fin");
const heritageObjectTypeRive = document.querySelector("#heritage-object-type-rive");
const heritageObjectTypeTroncon = document.querySelector("#heritage-object-type-troncon");
const heritageObjectTypologieTroncon = document.querySelector(
  "#heritage-object-typologie-troncon",
);
const heritageObjectValid = document.querySelector("#heritage-object-valid");
const heritageCreateMessage = document.querySelector("#heritage-create-message");
const heritageCreateActions = document.querySelector("#heritage-create-actions");
const cancelCreateButton = document.querySelector("#cancel-create");
const submitCreateButton = document.querySelector("#submit-create");
const tronconCreateGeometry = document.querySelector("#troncon-create-geometry");
const startTronconDrawButton = document.querySelector("#start-troncon-draw");
const tronconDrawStatus = document.querySelector("#troncon-draw-status");
const tronconDrawActions = document.querySelector("#troncon-draw-actions");
const cancelTronconDrawButton = document.querySelector("#cancel-troncon-draw");
const desordreEditorForm = document.querySelector("#desordre-editor");
const desordreAssociatedObjects = document.querySelector("#desordre-associated-objects");
const desordreCreateIdField = document.querySelector("#desordre-create-id-field");
const desordreCreateId = document.querySelector("#desordre-create-id");
const desordreCreateDesignation = document.querySelector("#desordre-create-designation");
const desordreCreateTypeReference = document.querySelector("#desordre-create-type-reference");
const desordreCreateCategorieReference = document.querySelector("#desordre-create-categorie-reference");
const desordreCreateCote = document.querySelector("#desordre-create-cote");
const desordreCreatePosition = document.querySelector("#desordre-create-position");
const desordreCreateSource = document.querySelector("#desordre-create-source");
const desordreCreateCommentaire = document.querySelector("#desordre-create-commentaire");
const desordreCreateLieuDit = document.querySelector("#desordre-create-lieu-dit");
const desordreCreateDateDebut = document.querySelector("#desordre-create-date-debut");
const desordreCreateDateFin = document.querySelector("#desordre-create-date-fin");
const desordreCreateValid = document.querySelector("#desordre-create-valid");
const desordreCreateTroncons = document.querySelector("#desordre-create-troncons");
const desordreCreateGeometryType = document.querySelector("#desordre-create-geometry-type");
const desordreXyChoice = document.querySelector("#desordre-xy-choice");
const desordreLonlatChoice = document.querySelector("#desordre-lonlat-choice");
const desordreCreateXy = document.querySelector("#desordre-create-xy");
const desordreCreateLonlat = document.querySelector("#desordre-create-lonlat");
const desordreCreateX = document.querySelector("#desordre-create-x");
const desordreCreateY = document.querySelector("#desordre-create-y");
const desordreCreateLongitude = document.querySelector("#desordre-create-longitude");
const desordreCreateLatitude = document.querySelector("#desordre-create-latitude");
const desordreCreateGeometry = document.querySelector("#desordre-create-geometry");
const startDesordreDrawButton = document.querySelector("#start-desordre-draw");
const desordreDrawStatus = document.querySelector("#desordre-draw-status");
const desordreDrawActions = document.querySelector("#desordre-draw-actions");
const cancelDesordreDrawButton = document.querySelector("#cancel-desordre-draw");
const validateDesordreDrawButton = document.querySelector("#validate-desordre-draw");
const desordreCreateMessage = document.querySelector("#desordre-create-message");
const desordreCreateActions = document.querySelector("#desordre-create-actions");
const desordrePdfButton = document.querySelector("#desordre-pdf");
const desordrePdfStatus = document.querySelector("#desordre-pdf-status");
const cancelDesordreCreateButton = document.querySelector("#cancel-desordre-create");
const submitDesordreCreateButton = document.querySelector("#submit-desordre-create");
const desordreModeSelector = document.querySelector("#desordre-mode-selector");
const desordreCreateLineCoordinates = document.querySelector("#desordre-create-line-coordinates");
const desordreCreateLineCrs = document.querySelector("#desordre-create-line-crs");
const desordreCreateLineStart1 = document.querySelector("#desordre-create-line-start-1");
const desordreCreateLineStart2 = document.querySelector("#desordre-create-line-start-2");
const desordreCreateLineEnd1 = document.querySelector("#desordre-create-line-end-1");
const desordreCreateLineEnd2 = document.querySelector("#desordre-create-line-end-2");
const desordreCreateBornage = document.querySelector("#desordre-create-bornage");
const desordreCreateBornageEnd = document.querySelector("#desordre-create-bornage-end");
const desordreCreateBorneStart = document.querySelector("#desordre-create-borne-start");
const desordreCreateDistanceStart = document.querySelector("#desordre-create-distance-start");
const desordreCreateSenseStart = document.querySelector("#desordre-create-sense-start");
const desordreCreateBorneEnd = document.querySelector("#desordre-create-borne-end");
const desordreCreateDistanceEnd = document.querySelector("#desordre-create-distance-end");
const desordreCreateSenseEnd = document.querySelector("#desordre-create-sense-end");
const polygonRepresentativePoint = document.querySelector("#polygon-representative-point");
const polygonRepresentativeX = document.querySelector("#polygon-representative-x");
const polygonRepresentativeY = document.querySelector("#polygon-representative-y");
const polygonRepresentativeLongitude = document.querySelector("#polygon-representative-longitude");
const polygonRepresentativeLatitude = document.querySelector("#polygon-representative-latitude");
const editorTabs = document.querySelector("#disorder-tabs");
const businessEditorContent = document.querySelector("#business-editor-content");
const editorMessage = desordreCreateMessage;
const saveButton = submitDesordreCreateButton;
const cancelEditButton = cancelDesordreCreateButton;
const closeEditorButton = document.querySelector("#close-editor");
const startMapPositionButton = document.querySelector("#start-map-position");
const mapPositionActions = document.querySelector("#map-position-actions");
const mapPositionStatus = document.querySelector("#map-position-status");
const validateMapPositionButton = document.querySelector("#validate-map-position");
const cancelMapPositionButton = document.querySelector("#cancel-map-position");
const pointMapEditor = document.querySelector("#point-map-editor");
const lineEditorMessage = desordreCreateMessage;
const startLineEditButton = document.querySelector("#start-line-edit");
const lineGeometryActions = document.querySelector("#line-geometry-actions");
const lineGeometryStatus = document.querySelector("#line-geometry-status");
const validateLineEditButton = document.querySelector("#validate-line-edit");
const cancelLineEditButton = document.querySelector("#cancel-line-edit");
const pointEditTroncon = desordreCreateTroncons;
const bornageFields = desordreCreateBornage;
const generalTabButton = document.querySelector("#general-tab-button");
const observationsTabButton = document.querySelector("#observations-tab-button");
const generalTab = document.querySelector("#general-tab");
const observationsTab = document.querySelector("#observations-tab");
const observationsListView = document.querySelector("#observations-list-view");
const observationsList = document.querySelector("#observations-list");
const observationsMessage = document.querySelector("#observations-message");
const observationsCount = document.querySelector("#observations-count");
const observationDetailView = document.querySelector("#observation-detail-view");
const backToObservationsButton = document.querySelector("#back-to-observations");
const observationPhotos = document.querySelector("#observation-photos");
const photosCount = document.querySelector("#photos-count");
const photosStorageNote = document.querySelector("#photos-storage-note");
const observationSuiteForm = document.querySelector("#observation-suite-form");
let observationControls = new Map();
const observationSuiteMessage = document.querySelector("#observation-suite-message");
const photoLightbox = document.querySelector("#photo-lightbox");
const lightboxImage = document.querySelector("#lightbox-image");
const lightboxUnavailable = document.querySelector("#lightbox-unavailable");
const lightboxTitle = document.querySelector("#lightbox-title");
const lightboxCaption = document.querySelector("#lightbox-caption");
const closeLightboxButton = document.querySelector("#close-lightbox");
const previousPhotoButton = document.querySelector("#previous-photo");
const nextPhotoButton = document.querySelector("#next-photo");
const TERRITORY_VIEW_PADDING_RATIO = 0.08;
const TERRITORY_VIEW_MAX_ZOOM = 17;
const territoireModal = document.querySelector("#territoire-modal");
const territoireForm = document.querySelector("#territoire-form");
const closeTerritoireModalButton = document.querySelector("#close-territoire-modal");
const cancelTerritoireModalButton = document.querySelector("#cancel-territoire-modal");
const territoireCurrentState = document.querySelector("#territoire-current-state");
const territoireLibelleInput = document.querySelector("#territoire-libelle");
const territoireFileInput = document.querySelector("#territoire-file");
const territoireLayerInput = document.querySelector("#territoire-layer");
const territoireMessage = document.querySelector("#territoire-message");
const submitTerritoireButton = document.querySelector("#submit-territoire");
const mapPrintModal = document.querySelector("#map-print-modal");
const mapPrintForm = document.querySelector("#map-print-form");
const closeMapPrintModalButton = document.querySelector("#close-map-print-modal");
const cancelMapPrintModalButton = document.querySelector("#cancel-map-print-modal");
const submitMapPrintButton = document.querySelector("#submit-map-print");
const mapPrintMessage = document.querySelector("#map-print-message");
const disorderFields = {
  id: desordreCreateId,
  designation: desordreCreateDesignation,
  type: desordreCreateTypeReference,
  categorie: desordreCreateCategorieReference,
  cote: desordreCreateCote,
  position: desordreCreatePosition,
  source: desordreCreateSource,
  commentaire: desordreCreateCommentaire,
  lieuDit: desordreCreateLieuDit,
  dateDebut: desordreCreateDateDebut,
  dateFin: desordreCreateDateFin,
  valid: desordreCreateValid,
  x: desordreCreateX,
  y: desordreCreateY,
  longitude: desordreCreateLongitude,
  latitude: desordreCreateLatitude,
  geometryType: desordreCreateGeometryType,
};
const reperageFields = {
  borne: desordreCreateBorneStart,
  distance: desordreCreateDistanceStart,
  sens: desordreCreateSenseStart,
};
const lineEditTroncons = desordreCreateTroncons;
const existingGeometryEditFormControls = Object.freeze([
  disorderFields.designation,
  disorderFields.type,
  disorderFields.categorie,
  disorderFields.cote,
  disorderFields.position,
  disorderFields.source,
  disorderFields.commentaire,
  disorderFields.lieuDit,
  disorderFields.dateDebut,
  disorderFields.dateFin,
  disorderFields.valid,
  lineEditTroncons,
]);
const lineMapEditor = document.querySelector("#line-map-editor");
const lineCoordinateEditor = desordreCreateLineCoordinates;
const lineEndpointsCrs = desordreCreateLineCrs;
const lineStart1 = desordreCreateLineStart1;
const lineStart2 = desordreCreateLineStart2;
const lineEnd1 = desordreCreateLineEnd1;
const lineEnd2 = desordreCreateLineEnd2;
const lineBorneStart = desordreCreateBorneStart;
const lineDistanceStart = desordreCreateDistanceStart;
const lineSenseStart = desordreCreateSenseStart;
const lineBorneEnd = desordreCreateBorneEnd;
const lineDistanceEnd = desordreCreateDistanceEnd;
const lineSenseEnd = desordreCreateSenseEnd;
const pointBornageWarning = document.querySelector("#point-bornage-warning");
const lineBornageWarning = document.querySelector("#line-bornage-warning");

let activePointLayer = null;
let lastServerFeature = null;
let initialFormValues = null;
let requestedDesordreId = null;
let graphicEditActive = false;
let pointPlacementFromGps = false;
let provisionalLatLng = null;
let graphicRequestInFlight = false;
let heritageData = { systemes: [] };
let selectedHeritageObject = null;
const hiddenTronconIds = new Set();
let heritagePanelController = null;
let heritageSelectionSyncController = null;
let aiPanelController = null;
let tronconsGeoJsonLayer = null;
// Includes physical geometries that cannot be projected to GeoJSON. This
// inventory is per business page, never a shared basemap/LiDAR cache.
const tronconPrintInventory = new Map();
let highlightedTronconLayer = null;
let observationsLoadedFor = null;
let observationsListDirty = true;
let observationsLoadSequence = 0;
let currentObservationPhotos = [];
let currentObservation = null;

document.querySelector("#create-desordre-observation").addEventListener("click", () => {
  const values = lastServerFeature?.properties;
  if (editorState.objectType !== "desordre" || editorState.mode !== "edit" || !values?.id
      || observationSuiteSubmitting || creationRequestInFlight) return;
  ObservationCreate.open({ resource: "desordres", id: values.id, designation: values.designation });
});
document.querySelector("#create-troncon-observation").addEventListener("click", () => {
  if (selectedHeritageObject?.resource !== "troncons" || creationRequestInFlight) return;
  const values = selectedHeritageObject.item;
  ObservationCreate.open({ resource: "troncons", id: values.id,
    designation: values.designation || values.libelle });
});
let observationSuiteSubmitting = false;
let currentPhotoIndex = -1;
let currentReperage = null;
let activeLineLayer = null;
let activePolygonLayer = null;
let polygonEditActive = false;
let selectedLineLayer = null;
let lineEditActive = false;
let lineRequestInFlight = false;
let editorMapInteraction = null;
let gpsTronconTracking = null;
let gpsDesordreTracking = null;
let gpsDesordreContext = null;
let gpsModeControls = null;
let editorMapPresentationSuspended = false;
let mapEditorStale = false;
let observationConflict = false;
let currentTopNavigationState = {};
let currentTopNavigationLayout = "split-docked";
let initialLineReperageValues = null;
let desordresGeoJsonLayer = null;
let desordrePointLayer = null;
let desordreLineLayer = null;
let desordrePolygonLayer = null;
let territoireContourLayer = null;
let territoireMaskLayer = null;
let territoireRequestSequence = 0;
let vigicruesStationLayer = null;
let vigicruesRequestVersion = 0;
let precipitationLayer = null;
let weatherForecastSession = null;
let precipitationDataByValidTime = new Map();
let weatherFrontendCacheDate = null;
let weatherTerritoryCacheKey = null;
let selectedWeatherStepIndex = 0;
let precipitationRequestPending = false;
let precipitationRequestController = null;
let precipitationRequestVersion = 0;
let showUuid = false;
let businessCRS = null;
let businessCRSLoading = null;
let territoireAdministratifGeoJSON = { type: "FeatureCollection", features: [] };
let territoireImportPending = false;
let mapPrintPending = false;
let mapPrintAbortController = null;
let mapPrintSourceBounds = null;
let mapViewportReady = false;
let historicalViewportBounds = null;
let initialMapViewportApplied = false;
const APP_STATUS_PRIORITY = Object.freeze({ info: 1, loading: 2, error: 3 });
const appStatusBySource = new Map();
let appBaseStatus = "Chargement des données…";
let appStatusOrder = 0;

function renderAppStatus() {
  const activeStatus = Array.from(appStatusBySource.values()).sort((left, right) => (
    APP_STATUS_PRIORITY[right.state] - APP_STATUS_PRIORITY[left.state]
    || right.order - left.order
  ))[0];
  statusElement.textContent = activeStatus?.message || appBaseStatus;
  if (activeStatus?.state === "error") {
    statusElement.classList.add("error");
  } else {
    statusElement.classList.remove("error");
  }
}

function setAppBaseStatus(message) {
  appBaseStatus = message;
  renderAppStatus();
}

function beginAppStatus(source, state, message) {
  const token = Symbol(source);
  appStatusBySource.set(source, {
    message,
    order: ++appStatusOrder,
    state,
    token,
  });
  renderAppStatus();
  return token;
}

function appStatusIsCurrent(source, token) {
  return appStatusBySource.get(source)?.token === token;
}

function updateAppStatus(source, token, state, message) {
  if (!appStatusIsCurrent(source, token)) return false;
  appStatusBySource.set(source, {
    message,
    order: ++appStatusOrder,
    state,
    token,
  });
  renderAppStatus();
  return true;
}

function clearAppStatus(source, token = null) {
  if (token && !appStatusIsCurrent(source, token)) return false;
  const removed = appStatusBySource.delete(source);
  if (removed) renderAppStatus();
  return removed;
}

const startupStatusToken = beginAppStatus(
  "startup",
  "loading",
  "Chargement des données…",
);
const entityEditor = EntityEditorShell.create({ mode: "edit" });
const editorState = entityEditor.state;

function syncDesordrePdfAction() {
  const available = editorState.objectType === "desordre"
    && editorState.mode === "edit"
    && Boolean(editorState.objectId);
  if (desordrePdfButton) desordrePdfButton.hidden = !available;
  if (!available && desordrePdfStatus) desordrePdfStatus.textContent = "";
}

function setDisorderEditorState(mode, geometryType, objectId = null, data = null) {
  if (!mapFeatureStore.hasDeferred("desordres", objectId)) mapEditorStale = false;
  entityEditor.setContext({
    mode,
    objectType: "desordre",
    geometryType,
    objectId,
    data,
  });
  syncDesordrePdfAction();
}

function mapViewportFromUrl(search = window.location.search) {
  const parameters = new URLSearchParams(search);
  const latitudeText = parameters.get("lat");
  const longitudeText = parameters.get("lon");
  const zoomText = parameters.get("zoom");
  const latitude = Number(latitudeText);
  const longitude = Number(longitudeText);
  const zoom = Number(zoomText);
  if (
    !parameters.has("lat")
    || !parameters.has("lon")
    || !parameters.has("zoom")
    || !latitudeText?.trim()
    || !longitudeText?.trim()
    || !zoomText?.trim()
    || !Number.isFinite(latitude)
    || !Number.isFinite(longitude)
    || !Number.isFinite(zoom)
    || latitude < -90
    || latitude > 90
    || longitude < -180
    || longitude > 180
    || zoom < 0
  ) {
    return null;
  }
  return { latitude, longitude, zoom };
}

const initialMapViewport = mapViewportFromUrl();
let initialEntityTarget = null;
let initialEntityTargetError = null;
try {
  initialEntityTarget = EntityNavigation.fromSearch(window.location.search);
} catch (error) {
  initialEntityTargetError = error;
}
const mapEntityEditors = new Map();
let mapEntityNavigationStarted = false;
let mapEntityNavigationPending = false;
let mapEntityNavigationComplete = false;
let mapEntityNavigationStatus = null;
let selectedMapUrlTarget = null;

function setMapUrlSelection(entityClass, id) {
  // Panel resources follow HTTP names; visible links use the closed Base catalogue.
  const urlClass = {
    "equipements-mesure": "equipements_mesure",
    "reseaux-techniques": "reseaux_techniques",
    "ouvrages-particuliers": "ouvrages_particuliers",
    "troncons-lit": "troncons_lit",
    "elements-morphologiques-lit": "elements_morphologiques_lit",
    "amenagements-hydrauliques": "amenagements_hydrauliques",
    "zones-stockage": "zones_stockage",
  }[entityClass] || entityClass;
  try { selectedMapUrlTarget = urlClass && id ? EntityNavigation.target(urlClass, id) : null; }
  catch (_) { selectedMapUrlTarget = null; }
  syncMapViewportUrl();
}

function focusMapEntity(feature) {
  if (!feature?.geometry) return false;
  const bounds = L.geoJSON(feature).getBounds();
  if (!bounds.isValid()) return false;
  map.fitBounds(bounds, { padding: [40, 40], maxZoom: 18 });
  return true;
}

async function openInitialMapEntity() {
  if (!mapEntityNavigationStarted || mapEntityNavigationPending || mapEntityNavigationComplete) return false;
  if (initialEntityTargetError) {
    mapEntityNavigationComplete = true;
    mapEntityNavigationStatus = beginAppStatus(
      "entity-navigation", "error", initialEntityTargetError.message,
    );
    return false;
  }
  if (!initialEntityTarget) return false;
  const editor = mapEntityEditors.get(initialEntityTarget.entityClass);
  if (!editor) {
    mapEntityNavigationComplete = true;
    mapEntityNavigationStatus = beginAppStatus(
      "entity-navigation", "error", "Cette catégorie ne dispose pas d’une ouverture par lien dans Carte.",
    );
    syncMapViewportUrl();
    return false;
  }
  mapEntityNavigationPending = true;
  const runtime = window.BusinessRuntime;
  mapEntityNavigationStatus = beginAppStatus(
    "entity-navigation", "loading", "Ouverture de l’objet demandé…",
  );
  try {
    await mapDataReadyPromise;
    if (runtime !== window.BusinessRuntime || !runtime?.isActive()) return false;
    const feature = await editor.openEdit(initialEntityTarget.id);
    if (runtime !== window.BusinessRuntime || !runtime?.isActive()) return false;
    if (!feature) throw new Error("Objet introuvable, inaccessible ou ouverture annulée.");
    const localized = focusMapEntity(feature);
    updateAppStatus(
      "entity-navigation",
      mapEntityNavigationStatus,
      "info",
      localized ? "Objet ouvert et localisé sur la carte."
        : "Objet ouvert, mais non localisé.",
    );
    mapEntityNavigationComplete = true;
    setMapUrlSelection(initialEntityTarget.entityClass, initialEntityTarget.id);
    return true;
  } catch (error) {
    if (runtime === window.BusinessRuntime && runtime?.isActive()) {
      updateAppStatus(
        "entity-navigation", mapEntityNavigationStatus, "error",
        `Navigation vers l’objet impossible : ${error.message}`,
      );
      mapEntityNavigationComplete = true;
      syncMapViewportUrl();
    }
    return false;
  } finally {
    mapEntityNavigationPending = false;
  }
}

window.MapEntityNavigation = Object.freeze({
  canOpen(entityClass, id) {
    try {
      const target = EntityNavigation.target(entityClass, id);
      return mapEntityEditors.has(target.entityClass) && Boolean(window.BusinessRuntime?.isActive())
        && window.BusinessRuntime.can("business.read");
    } catch (_) { return false; }
  },
  register(entityClass, editor) {
    if (!EntityNavigation.ENTITY_CLASSES.includes(entityClass)
        || !editor || typeof editor.openEdit !== "function") {
      throw new TypeError("Éditeur de navigation Carte invalide.");
    }
    mapEntityEditors.set(entityClass, editor);
    void openInitialMapEntity();
  },
  start() {
    mapEntityNavigationStarted = true;
    return openInitialMapEntity();
  },
  async open(entityClass, id) {
    const target = EntityNavigation.target(entityClass, id);
    const editor = mapEntityEditors.get(target.entityClass);
    if (!editor) throw new Error("Cette catégorie n’est pas disponible sur Carte.");
    const runtime = window.BusinessRuntime;
    await mapDataReadyPromise;
    if (runtime !== window.BusinessRuntime || !runtime?.isActive()) return false;
    const feature = await editor.openEdit(target.id);
    if (runtime !== window.BusinessRuntime || !runtime?.isActive()) return false;
    // Une ouverture refusée (brouillon, permission ou lecture) est déjà traitée
    // par l'éditeur ; l'annulation utilisateur n'est pas une erreur JavaScript.
    if (!feature) return false;
    focusMapEntity(feature);
    setMapUrlSelection(target.entityClass, target.id);
    return true;
  },
});

const disorderRelatedObjectsController = DisorderRelatedObjects.createController({
  container: desordreAssociatedObjects,
  fetchJson,
  isCurrent: (identifier) => editorState.objectType === "desordre"
    && editorState.mode === "edit" && String(editorState.objectId) === String(identifier)
    && !editorPanel.hidden,
  onNavigate: (item) => MapEntityNavigation.open(item.resource, item.id),
  isRepresented: item => MapFormViews.disorder.representsRelation(item),
});
MapFormViews.disorder.form.addEventListener("change", () => disorderRelatedObjectsController.refresh());

function loadDisorderRelatedObjects(feature) {
  const identifier = feature?.properties?.id;
  if (!identifier || editorState.mode !== "edit") {
    disorderRelatedObjectsController.clear();
    return false;
  }
  void disorderRelatedObjectsController.open(identifier);
  return true;
}

function restoreInitialMapViewport() {
  if (initialMapViewportApplied) {
    return false;
  }
  initialMapViewportApplied = true;
  if (!initialMapViewport) {
    return false;
  }
  map.setView(
    [initialMapViewport.latitude, initialMapViewport.longitude],
    initialMapViewport.zoom,
    { animate: false },
  );
  return true;
}

function syncMapViewportUrl() {
  if (!mapViewportReady || initialEntityTarget && !mapEntityNavigationComplete) {
    return;
  }
  const center = map.getCenter();
  const zoom = map.getZoom();
  const url = EntityNavigation.automaticUrl(window.location.href, {
    view: "map", mode: window.BusinessRuntime?.urlMode?.() || "position",
    position: { lat: center?.lat, lon: center?.lng, zoom }, selected: selectedMapUrlTarget,
  });
  if (url !== window.location.pathname + window.location.search + window.location.hash) {
    window.history.replaceState(window.history.state, "", url);
  }
}

window.addEventListener("digues-app:url-mode-changed", syncMapViewportUrl);
window.addEventListener("storage", event => {
  if (event.key === window.BusinessRuntime?.urlModeKey) syncMapViewportUrl();
});

function openDirections() {
  const center = map.getCenter();

  if (!Number.isFinite(center?.lat) || !Number.isFinite(center?.lng)) {
    return;
  }

  const url = `https://waze.com/ul?ll=${center.lat},${center.lng}&navigate=yes`;
  window.open(url, "_blank", "noopener,noreferrer");
}

function renderUserPositionStatus() {
  userPositionStatus.textContent = [userPositionMessage, mapLocateMessage]
    .filter(Boolean).join(" · ");
}

function syncWazeButtonVisibility() {
  directionsButton.hidden = !PwaMapSettings.loadSettings().showWazeButton;
}

function locateMapOnce() {
  if (mapLocateRequest || !PwaGeolocation.isStandalone()) return;
  const request = PwaGeolocation.requestCurrentPosition();
  mapLocateRequest = request;
  locateMapButton.disabled = true;
  mapLocateMessage = "Recherche de position…";
  renderUserPositionStatus();
  request.promise.then((position) => {
    if (mapLocateRequest !== request) return;
    const maxZoom = map.getMaxZoom();
    const targetZoom = Number.isFinite(maxZoom) ? Math.min(19, maxZoom) : 19;
    map.setView([position.latitude, position.longitude],
      Math.max(map.getZoom(), targetZoom), { animate: false });
    mapLocateMessage = "Carte centrée sur votre position.";
    renderUserPositionStatus();
  }, (error) => {
    if (mapLocateRequest !== request) return;
    const messages = {
      PERMISSION_DENIED: "Position refusée. Autorisez la localisation.",
      TIMEOUT: "Délai GPS dépassé. Réessayez.",
      POSITION_UNAVAILABLE: "Position GPS indisponible. Réessayez.",
    };
    mapLocateMessage = messages[error?.code] || error?.message || "Position GPS indisponible.";
    renderUserPositionStatus();
  }).finally(() => {
    if (mapLocateRequest !== request) return;
    mapLocateRequest = null;
    locateMapButton.disabled = false;
  });
}

map.on("moveend", syncMapViewportUrl);
directionsButton?.addEventListener("click", openDirections);
locateMapButton?.addEventListener("click", locateMapOnce);
syncWazeButtonVisibility();
window.addEventListener("storage", (event) => {
  if (event.key === PwaMapSettings.STORAGE_KEY) syncWazeButtonVisibility();
});
let provisionalTronconLayer = null;
let cancelledTronconGeometry = null;
let provisionalDesordreLayer = null;
let cancelledDesordreGeometry = null;
let desordreTypes = [];
let desordreTypesLoadingPromise = null;
let desordreCategories = [];
let desordreCategoriesLoadingPromise = null;
let desordreCotes = [];
let desordrePositions = [];
let desordreSources = [];
let desordreTronconOptions = [];
let desordreTronconOptionsLoaded = false;
let desordreTronconsLoadingPromise = null;
let desordreTronconOptionsVersion = 0;
const reperageOptionsByTroncon = new Map();
const reperageLoadingByTroncon = new Map();
const reperageOptionsVersionByTroncon = new Map();
let reperageOptionsGlobalVersion = 0;
let previousDesordreGeometryType = "Point";
let creationRequestInFlight = false;
let creationReperageRequestVersion = 0;
let creationReperageAvailable = false;
let lastAcceptedCreationTronconIds = [];
const tronconLayersById = new Map();
const desordreLayersById = new Map();
const mapFeatureStore = MapFeatureStore.createRegistry();

const heritageCreationTypes = {
  systeme: {
    title: "Système d'endiguement",
    endpoint: "/api/systemes-endiguement",
    kind: "Système d'endiguement",
    resource: "systemes",
  },
  digue: {
    title: "Digue",
    endpoint: "/api/digues",
    kind: "Digue",
    resource: "digues",
  },
  troncon: {
    title: "Tronçon",
    endpoint: "/api/troncons",
    kind: "Tronçon",
    resource: "troncons",
  },
  desordre: {
    title: "Désordre",
    endpoint: "/api/desordres",
    resource: "desordres",
  },
};

function createDesordrePointIcon(feature = null) {
  const variables = DisorderUrgencyStyle.pointVariables(feature);
  const style = Object.entries(variables)
    .map(([name, value]) => `${name}:${value}`)
    .join(";");
  return L.divIcon({
    className: "desordre-point-marker",
    html: `<span style="${style}"></span>`,
    iconAnchor: [9, 9],
    iconSize: [18, 18],
  });
}

const pointIcon = createDesordrePointIcon();

const vigicruesStationIcon = L.divIcon({
  className: "vigicrues-station-marker",
  html: `
    <span>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path
          d="M16 19.875c0 .621-.512 1.125-1.143 1.125h-5.714a1.134 1.134 0 0 1-1.143-1.125v-15.875a1 1 0 0 1 1-1h5.857c.631 0 1.143.504 1.143 1.125l0 15.75M16 9h-2M16 6h-3M16 12h-3M16 18h-3M16 15h-2"
        />
      </svg>
    </span>
  `,
  iconAnchor: [12, 12],
  iconSize: [24, 24],
});

function createDesordrePointMarker(latlng, feature = null) {
  return L.marker(latlng, {
    draggable: false,
    icon: createDesordrePointIcon(feature),
  });
}

function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function businessLabel(item, fallback = "Sans libellé") {
  return item?.libelle || (showUuid ? item?.id : null) || fallback;
}

async function loadFrontendConfig() {
  let config;
  try {
    config = await fetchJson("/api/config");
    if (!config || typeof config !== "object" || Array.isArray(config)) {
      throw new Error("Configuration frontend invalide.");
    }
  } catch (error) {
    console.warn("Configuration frontend indisponible ; fond OpenStreetMap utilisé.", error);
    config = { show_uuid: false }; // Presentation default, not an offline business record.
  }
  showUuid = Boolean(config.show_uuid);
  document.body.classList.toggle("show-uuid", showUuid);
  try {
    offlineBasemapUi = window.OfflineBasemapUi.create({
      map, basemapApi: window.Basemap, getTerritory: () => territoireAdministratifGeoJSON.features[0] || null,
    });
  } catch (error) {
    console.warn("Stockage cartographique local indisponible ; les fonds en ligne restent accessibles.", error);
    offlineBasemapUi = null;
  }
  basemapSelector = window.BasemapSelector.create({
    map,
    L,
    container: document.querySelector("#basemap-selector"),
    embedded: true,
    basemapApi: window.Basemap,
    offlineSource: offlineBasemapUi,
    onChange: (selected) => {
      if (mapPrintPending && (basemap?.id !== selected?.id || basemap?.url !== selected?.url)) {
        mapPrintAbortController?.abort();
      }
      basemap = selected;
    },
  });
  offlineBasemapUi?.attachSelector(basemapSelector);
  offlineBasemapUi?.start();
  await basemapSelector.start(config.basemap_tilejson_url);
}

function inputText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function optionalPayloadValue(value) {
  if (value === null || value === undefined) return null;
  const normalized = String(value).trim();
  return normalized === "" ? null : normalized;
}

function nullableNonNegativeInteger(value) {
  if (value === null || value === undefined || String(value).trim() === "") {
    return null;
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error("La population protégée doit être un entier positif ou nul.");
  }
  return parsed;
}

function coordinate(value, precision) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(precision) : "";
}

function popupContent(properties, popupFields) {
  const list = document.createElement("dl");
  list.className = "popup-fields";
  popupFields.forEach(([label, key]) => {
    if (key === "id" && !showUuid) {
      return;
    }
    const term = document.createElement("dt");
    const value = document.createElement("dd");
    term.textContent = label;
    value.textContent = text(properties[key]);
    list.append(term, value);
  });
  return list;
}

function configureVigicruesStation(feature, layer) {
  const properties = feature.properties || {};
  const observations = properties.observations || {};
  const height = observations.latest?.height;
  const discharge = observations.latest?.discharge;
  const hasHeight = Number.isFinite(height?.value);
  const hasDischarge = Number.isFinite(discharge?.value);
  const heightTime = hasHeight ? height.source_time : null;
  const dischargeTime = hasDischarge ? discharge.source_time : null;
  const popup = document.createElement("div");
  const title = document.createElement("strong");
  const stationName = text(properties.name, "Site sans nom");
  const watercourse = properties.watercourse?.name;
  title.textContent = watercourse
    ? `${stationName} — ${watercourse}`
    : stationName;
  popup.append(title);

  const values = {};
  const fields = [];
  if (observations.series_resolution?.height?.status === "multiple_unresolved") {
    values.height = "Plusieurs séries H (aucune série de référence)";
    fields.push(["Hauteur", "height"]);
  }
  if (observations.series_resolution?.discharge?.status === "multiple_unresolved") {
    values.discharge = "Plusieurs séries Q (aucune série de référence)";
    fields.push(["Débit", "discharge"]);
  }
  if (hasHeight && observations.series_resolution?.height?.status !== "multiple_unresolved") {
    values.height = `${height.value} m`;
    fields.push(["Hauteur", "height"]);
  }
  if (hasDischarge && observations.series_resolution?.discharge?.status !== "multiple_unresolved") {
    values.discharge = `${discharge.value} m³/s`;
    fields.push(["Débit", "discharge"]);
  }
  if (heightTime && dischargeTime && heightTime === dischargeTime) {
    values.measure_time = formatWeatherDate(heightTime);
    fields.push(["Mesure", "measure_time"]);
  } else {
    if (heightTime) {
      values.height_time = formatWeatherDate(heightTime);
      fields.push(["Mesure hauteur", "height_time"]);
    }
    if (dischargeTime) {
      values.discharge_time = formatWeatherDate(dischargeTime);
      fields.push(["Mesure débit", "discharge_time"]);
    }
  }
  if (fields.length) {
    popup.append(popupContent(values, fields));
  }
  layer.bindPopup(popup);
}

function errorDetail(body, fallback) {
  if (typeof body?.detail === "string" && body.detail.trim()) {
    return body.detail.trim();
  }
  if (typeof body?.detail?.message === "string" && body.detail.message.trim()) {
    return body.detail.message.trim();
  }
  if (Array.isArray(body?.detail)) {
    const messages = body.detail
      .map((item) => item?.msg)
      .filter((message) => typeof message === "string" && message.trim());
    if (messages.length) {
      return messages.join(" ");
    }
  }
  return fallback;
}

function httpErrorFallback(status) {
  if (status === 404) return "La ressource demandée est introuvable.";
  if (status === 408 || status === 504) return "Le serveur n’a pas répondu à temps.";
  if (status >= 500) return "Le serveur est temporairement indisponible.";
  return "La demande a été refusée.";
}

async function fetchJson(url, options = {}) {
  await window.ResourceEventTransport?.bootstrap;
  const originHeaders = ["POST", "PUT", "PATCH", "DELETE"].includes(options.method)
    ? await ResourceMutationController.requestHeaders() : {};
  const response = await (window.OfflineRuntime?.fetch || globalThis.BusinessRuntime?.fetch || fetch)(url, {
    ...options,
    headers: {
      Accept: "application/geo+json",
      ...originHeaders,
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = httpErrorFallback(response.status);
    try {
      detail = errorDetail(await response.json(), detail);
    } catch (_error) {
      // Le message générique reste affiché si le corps n'est pas du JSON.
    }
    const error = new Error(response.status === 409 ? ResourceConcurrency.CONFLICT_MESSAGE : detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function fetchGeoJSON(url) {
  const data = await fetchJson(url);
  if (data.type !== "FeatureCollection" || !Array.isArray(data.features)) {
    throw new Error("La réponse cartographique est invalide.");
  }
  return data;
}

function publishConfirmedMutation(
  resource,
  action,
  ids,
  aspects = undefined,
  response = null,
  applyLocally = false,
) {
  const descriptor = { resource, action, ids, ...response?.mutation };
  if (aspects !== undefined && !response?.mutation) descriptor.aspects = aspects;
  try {
    const publication = applyLocally
      ? ResourceMutationController.publishMutation(descriptor, { applyLocally: true })
      : ResourceMutationController.publishMutation(descriptor);
    return publication.catch((error) => {
      console.error("Publication de mutation impossible", error);
      return null;
    });
  } catch (error) {
    // Une erreur de coordination ne remet pas en cause une écriture déjà
    // confirmée par PostgreSQL ; les consommateurs locaux restent utilisables.
    console.error("Publication de mutation impossible", error);
    return null;
  }
}

function featureIdentifier(feature) {
  const identifier = feature?.properties?.id ?? feature?.id;
  return identifier == null || String(identifier).trim() === ""
    ? null : String(identifier);
}

function publishConfirmedDesordreUpdate(feature, aspects = undefined) {
  const identifier = featureIdentifier(feature)
    || featureIdentifier(lastServerFeature);
  return publishConfirmedMutation(
    "desordres", "update", identifier ? [identifier] : null, aspects, feature,
  );
}

async function writeDesordre(url, options) {
  const generation=legacyEditorGeneration, runtime=window.BusinessRuntime;
  entityEditor.beginSave({ url, method: options.method });
  syncLegacyEditorPresentation();
  try {
    const feature = await fetchJson(url, {
      ...options,
      headers: { ...options.headers, ...ResourceConcurrency.headers(lastServerFeature?.properties?.version) },
    });
    if(generation === legacyEditorGeneration && runtime === window.BusinessRuntime)
      entityEditor.confirm(feature, { url, method: options.method });
    return feature;
  } catch (error) {
    if(generation !== legacyEditorGeneration || runtime !== window.BusinessRuntime) throw error;
    entityEditor.fail(error, {
      conflict: error.status === 409 ? true : editorState.conflict,
    });
    if (error.status === 409) {
      markMapEditorStale();
      // Keep one authoritative reread pending until the draft is explicitly abandoned.
      mapFeatureStore.invalidate({ resource: "desordres", action: "update",
        ids: [String(lastServerFeature.properties.id)] }).catch(console.error);
    }
    throw error;
  } finally {
    syncLegacyEditorPresentation();
  }
}

function setTerritoireAdministratifState(collection) {
  if (collection.type !== "FeatureCollection" || !Array.isArray(collection.features)) {
    throw new Error("La réponse cartographique du territoire est invalide.");
  }
  if (collection.features.length > 1) {
    throw new Error("La réponse cartographique du territoire est ambiguë.");
  }
  territoireAdministratifGeoJSON = {
    type: "FeatureCollection",
    features: collection.features.slice(0, 1),
  };
  const signature = collection.gpsTerritoryUnknown ? undefined
    : JSON.stringify(territoireAdministratifGeoJSON.features[0]?.geometry || null);
  if (window.DiguesGpsTerritoryKey !== signature) {
    window.DiguesGpsTerritoryKey = signature;
    window.dispatchEvent(new Event("digues-app:gps-territory-changed"));
  }
}

function currentTerritoireFeature() {
  return territoireAdministratifGeoJSON.features[0] || null;
}

async function loadTerritoireAdministratif(generation = ++territoireRequestSequence) {
  const collection = await fetchGeoJSON("/api/territoire-administratif");
  if (generation !== territoireRequestSequence) return false;
  setTerritoireAdministratifState(collection);
  return true;
}

function territoryBoundsFromFeature(feature) {
  if (!feature) {
    return null;
  }
  const layer = L.geoJSON(feature);
  const bounds = layer.getBounds();
  return bounds.isValid() ? bounds : null;
}

function territoryMaskGeoJSON(feature) {
  const coordinates = feature?.geometry?.coordinates;
  const outerRing = coordinates?.[0];
  if (!Array.isArray(outerRing) || outerRing.length < 4) {
    return null;
  }
  const worldRing = [
    [-180, -90],
    [180, -90],
    [180, 90],
    [-180, 90],
    [-180, -90],
  ];
  const polygons = [
    [worldRing, outerRing],
  ];
  coordinates.slice(1).forEach((hole) => {
    if (Array.isArray(hole) && hole.length >= 4) {
      polygons.push([hole]);
    }
  });
  return {
    type: "Feature",
    geometry: {
      type: "MultiPolygon",
      coordinates: polygons,
    },
    properties: {},
  };
}

function clearTerritoireLayers() {
  if (territoireMaskLayer) {
    map.removeLayer(territoireMaskLayer);
    territoireMaskLayer = null;
  }
  if (territoireContourLayer) {
    map.removeLayer(territoireContourLayer);
    territoireContourLayer = null;
  }
}

function renderTerritoireLayers() {
  clearTerritoireLayers();
  const feature = currentTerritoireFeature();
  if (!feature) {
    return null;
  }

  const maskFeature = territoryMaskGeoJSON(feature);
  if (maskFeature) {
    territoireMaskLayer = L.geoJSON(maskFeature, {
      interactive: false,
      pane: TERRITORY_MASK_PANE,
      renderer: territoryMaskRenderer,
      style: {
        color: "#c6cfd6",
        fillColor: "#eff3f6",
        fillOpacity: 1,
        opacity: 0,
        stroke: false,
      },
    }).addTo(map);
  }

  territoireContourLayer = L.geoJSON(feature, {
    interactive: false,
    pane: TERRITORY_OUTLINE_PANE,
    style: {
      color: "#385f80",
      fillColor: "#ffffff",
      fillOpacity: 0.04,
      opacity: 0.95,
      weight: 2,
    },
  }).addTo(map);

  return territoireContourLayer.getBounds();
}

function applyViewportBounds(bounds) {
  if (!bounds?.isValid()) {
    return false;
  }
  const paddedBounds = bounds.pad(TERRITORY_VIEW_PADDING_RATIO);
  map.fitBounds(paddedBounds, {
    maxZoom: TERRITORY_VIEW_MAX_ZOOM,
    animate: false,
  });
  map.setMaxBounds(paddedBounds);
  map.setMinZoom(map.getZoom());
  return true;
}

function updateTerritoireSpatialConstraint(bounds) {
  if (!bounds?.isValid()) return false;
  const paddedBounds = bounds.pad(TERRITORY_VIEW_PADDING_RATIO);
  const center = map.getCenter();
  const zoom = map.getZoom();
  const canPreserveViewport = paddedBounds.contains(center);
  map.setMaxBounds(paddedBounds);
  const territoryFitZoom = map.getBoundsZoom(paddedBounds, false);
  if (Number.isFinite(territoryFitZoom)) {
    map.setMinZoom(Math.min(zoom, territoryFitZoom));
  }
  if (canPreserveViewport) {
    map.setView(center, zoom, { animate: false });
  }
  return canPreserveViewport;
}

function revealMapAfterInitialViewport() {
  map.invalidateSize({ pan: false, animate: false });
  mapElement.style.visibility = "";
  mapViewportReady = true;
  if (pendingUserPositionCenter) {
    map.setView(pendingUserPositionCenter, Math.max(map.getZoom(), 16), { animate: false });
    pendingUserPositionCenter = null;
  }
  focusPendingMapCandidate();
  syncMapViewportUrl();
}

function applyTerritoireCartography(bounds) {
  const territoryBounds = renderTerritoireLayers();
  const referenceBounds = territoryBounds || bounds;
  if (referenceBounds?.isValid()) {
    applyViewportBounds(referenceBounds);
  }
  restoreInitialMapViewport();
  revealMapAfterInitialViewport();
  return referenceBounds;
}

async function reloadTerritoireContext() {
  const generation = ++territoireRequestSequence;
  if (!await loadTerritoireAdministratif(generation)) return false;
  const territoryBounds = renderTerritoireLayers();
  updateTerritoireSpatialConstraint(territoryBounds);
  invalidatePrecipitationCache();
  clearVigicruesLayer();
  const vigicruesToggle = document.querySelector('[data-layer-toggle="vigicrues"]');
  if (vigicruesToggle) vigicruesToggle.checked = false;
  invalidateVigicruesRequest(vigicruesToggle);
  return currentTerritoireFeature();
}

function precipitationColor(value) {
  if (value === null || value === undefined || value <= 0) return "transparent";
  if (value < 1) return "#d7ecfb";
  if (value < 5) return "#96cdec";
  if (value < 10) return "#4b9fd8";
  if (value < 20) return "#2377b7";
  if (value < 40) return "#15508a";
  return "#6f2c8f";
}

function precipitationLabel(value) {
  if (value === null || value === undefined) return "NoData";
  return `${Number(value).toFixed(1)} mm`;
}

function formatWeatherDate(value) {
  if (!value) return "non précisée";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("fr-FR", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: "UTC",
    });
}

function clearPrecipitationLayer() {
  if (precipitationLayer) {
    map.removeLayer(precipitationLayer);
    precipitationLayer = null;
  }
}

function clearWeatherLegend() {
  if (!weatherLegend) return;
  weatherLegend.hidden = true;
  weatherLegend.replaceChildren();
  if (weatherLegendToggle) {
    weatherLegendToggle.hidden = true;
    weatherLegendToggle.setAttribute("aria-expanded", "false");
    weatherLegendToggle.setAttribute(
      "aria-label",
      "Déplier la légende des précipitations",
    );
  }
}

function setWeatherLegendExpanded(expanded) {
  if (!weatherLegend || !weatherLegendToggle) return;
  const isExpanded = !weatherLegendToggle.hidden && Boolean(expanded);
  weatherLegend.hidden = !isExpanded;
  weatherLegendToggle.setAttribute("aria-expanded", String(isExpanded));
  weatherLegendToggle.setAttribute(
    "aria-label",
    `${isExpanded ? "Replier" : "Déplier"} la légende des précipitations`,
  );
}

function toggleWeatherLegend() {
  setWeatherLegendExpanded(
    weatherLegendToggle?.getAttribute("aria-expanded") !== "true",
  );
}

function renderWeatherLegend() {
  if (!weatherLegend) return;
  const wasAvailable = weatherLegendToggle && !weatherLegendToggle.hidden;
  const wasExpanded = weatherLegendToggle?.getAttribute("aria-expanded") === "true";
  weatherLegend.replaceChildren();
  WEATHER_LEGEND_CLASSES.forEach(([label, color]) => {
    const row = document.createElement("div");
    row.className = "weather-legend-row";
    const swatch = document.createElement("i");
    swatch.className = "weather-legend-swatch";
    swatch.style.background = color;
    const textNode = document.createElement("span");
    textNode.textContent = label;
    row.append(swatch, textNode);
    weatherLegend.append(row);
  });
  if (weatherLegendToggle) weatherLegendToggle.hidden = false;
  setWeatherLegendExpanded(wasAvailable && wasExpanded);
}

function renderPrecipitationLayer(data) {
  clearPrecipitationLayer();
  if (!Array.isArray(data?.cells)) {
    throw new Error("Réponse météo invalide.");
  }
  precipitationLayer = L.layerGroup();
  data.cells.forEach((cell) => {
    const bounds = cell.bounds;
    if (!Array.isArray(bounds) || bounds.length !== 2) {
      return;
    }
    const value = cell.value_mm;
    const rectangle = L.rectangle(bounds, {
      pane: WEATHER_PANE,
      interactive: true,
      className: "weather-precipitation-cell",
      stroke: false,
      fillColor: precipitationColor(value),
      fillOpacity: value && value > 0 ? 0.48 : 0,
    });
    rectangle.bindTooltip(
      `Précipitations 24 h : ${precipitationLabel(value)}<br>Échéance : ${formatWeatherDate(data.valid_time)} UTC`
    );
    rectangle.addTo(precipitationLayer);
  });
  precipitationLayer.addTo(map);
  renderWeatherLegend();
}

function setPrecipitationLoading(loading) {
  precipitationRequestPending = loading;
  if (weatherTimeline) {
    weatherTimeline.classList[loading ? "add" : "remove"]("loading");
    weatherTimeline.setAttribute("aria-busy", String(loading));
  }
}

function clearWeatherTimeline() {
  if (!weatherTimeline) return;
  weatherTimeline.hidden = true;
  weatherTimelineLabels.replaceChildren();
  if (weatherTimelineProvenance) weatherTimelineProvenance.textContent = "";
  weatherTimelineRange.min = "0";
  weatherTimelineRange.max = "0";
  weatherTimelineRange.value = "0";
}

function clearVigicruesLayer() {
  if (typeof vigicruesStationLayer !== "undefined" && vigicruesStationLayer) {
    map.removeLayer(vigicruesStationLayer);
    vigicruesStationLayer = null;
  }
}

function invalidateVigicruesRequest(input = null) {
  vigicruesRequestVersion += 1;
  if (input) input.disabled = false;
  clearAppStatus("vigicrues");
}

async function toggleVigicruesLayer(input) {
  if (!input.checked) {
    if (vigicruesStationLayer) {
      map.removeLayer(vigicruesStationLayer);
    }
    invalidateVigicruesRequest(input);
    return;
  }
  if (!currentTerritoireFeature()) {
    input.checked = false;
    invalidateVigicruesRequest(input);
    beginAppStatus(
      "vigicrues",
      "error",
      "Importez d'abord un territoire administratif pour afficher les stations hydrométriques.",
    );
    return;
  }
  if (vigicruesStationLayer) {
    vigicruesStationLayer.addTo(map);
    clearAppStatus("vigicrues");
    return;
  }
  const requestVersion = ++vigicruesRequestVersion;
  input.disabled = true;
  const statusToken = beginAppStatus(
    "vigicrues",
    "loading",
    "Chargement des stations hydrométriques…",
  );
  try {
    const stations = await fetchGeoJSON("/api/hydrometrie/sites");
    if (
      !input.checked
      || requestVersion !== vigicruesRequestVersion
      || !appStatusIsCurrent("vigicrues", statusToken)
    ) return;
    vigicruesStationLayer = L.geoJSON(stations, {
      pointToLayer(_feature, latlng) {
        return L.marker(latlng, { icon: vigicruesStationIcon, pane: HYDROMETRY_PANE });
      },
      onEachFeature: configureVigicruesStation,
    }).addTo(map);
    clearAppStatus("vigicrues", statusToken);
  } catch (error) {
    if (
      requestVersion !== vigicruesRequestVersion
      || !appStatusIsCurrent("vigicrues", statusToken)
    ) return;
    input.checked = false;
    clearVigicruesLayer();
    updateAppStatus(
      "vigicrues",
      statusToken,
      "error",
      error.message,
    );
  } finally {
    if (requestVersion === vigicruesRequestVersion) {
      input.disabled = false;
    }
  }
}

function renderWeatherTimeline() {
  const steps = weatherForecastSession?.forecast_steps;
  if (!weatherTimeline || !Array.isArray(steps) || !steps.length) {
    clearWeatherTimeline();
    return;
  }
  weatherTimelineLabels.replaceChildren();
  steps.forEach((step) => {
    const label = document.createElement("span");
    label.textContent = step.label;
    label.dataset.status = step.status || "pending";
    weatherTimelineLabels.append(label);
  });
  weatherTimelineRange.min = "0";
  weatherTimelineRange.max = String(steps.length - 1);
  weatherTimelineRange.step = "1";
  weatherTimelineRange.value = String(selectedWeatherStepIndex);
  weatherTimelineRange.disabled = steps.length < 2;
  const selected = steps[selectedWeatherStepIndex];
  if (weatherTimelineProvenance) {
    weatherTimelineProvenance.textContent = selected?.model
      ? `${selected.model} · cumul 24 h`
      : "Cumul 24 h";
  }
  weatherTimeline.hidden = false;
}

function currentCivilDateKey(now = new Date()) {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentWeatherTerritoryKey() {
  const feature = currentTerritoireFeature();
  return feature ? JSON.stringify(feature) : null;
}

function cancelPrecipitationRequest() {
  precipitationRequestVersion += 1;
  if (precipitationRequestController) {
    precipitationRequestController.abort();
    precipitationRequestController = null;
  }
  setPrecipitationLoading(false);
}

function resetWeatherFrontendCache({ uncheck = false } = {}) {
  cancelPrecipitationRequest();
  clearAppStatus("weather");
  weatherForecastSession = null;
  precipitationDataByValidTime = new Map();
  weatherFrontendCacheDate = null;
  weatherTerritoryCacheKey = null;
  selectedWeatherStepIndex = 0;
  clearPrecipitationLayer();
  clearWeatherLegend();
  clearWeatherTimeline();
  const input = document.querySelector('[data-layer-toggle="precipitations"]');
  if (uncheck && input) {
    input.checked = false;
  }
}

function invalidatePrecipitationCache() {
  resetWeatherFrontendCache({ uncheck: true });
}

function prepareWeatherFrontendContext() {
  const context = {
    cacheDate: currentCivilDateKey(),
    territoryKey: currentWeatherTerritoryKey(),
  };
  if (
    weatherFrontendCacheDate !== context.cacheDate
    || weatherTerritoryCacheKey !== context.territoryKey
  ) {
    resetWeatherFrontendCache();
    weatherFrontendCacheDate = context.cacheDate;
    weatherTerritoryCacheKey = context.territoryKey;
  }
  return context;
}

function weatherRequestIsCurrent(input, requestVersion, context, validTime = null) {
  if (!input.checked || requestVersion !== precipitationRequestVersion) {
    return false;
  }
  if (
    weatherFrontendCacheDate !== context.cacheDate
    || weatherTerritoryCacheKey !== context.territoryKey
  ) {
    return false;
  }
  if (validTime) {
    const selected = weatherForecastSession?.forecast_steps?.[selectedWeatherStepIndex];
    return selected?.valid_time === validTime;
  }
  return true;
}

function validateWeatherForecastSession(data) {
  if (!Array.isArray(data?.forecast_steps) || !data.forecast_steps.length) {
    throw new Error("Réponse des échéances météo invalide.");
  }
  data.forecast_steps.forEach((step, index) => {
    if (
      step?.index !== index
      || typeof step.label !== "string"
      || typeof step.valid_time !== "string"
      || typeof step.model !== "string"
      || typeof step.run !== "string"
    ) {
      throw new Error("Réponse des échéances météo invalide.");
    }
  });
  return data;
}

function applyWeatherGridProvenance(step, data) {
  if (!step || !data) return;
  if (typeof data.model === "string") step.model = data.model;
  if (typeof data.run === "string") step.run = data.run;
  if (typeof data.lead_hours === "number") step.lead_hours = data.lead_hours;
  renderWeatherTimeline();
}

function beginPrecipitationRequest() {
  if (precipitationRequestController) {
    precipitationRequestController.abort();
  }
  precipitationRequestController = new AbortController();
  precipitationRequestVersion += 1;
  setPrecipitationLoading(true);
  return {
    controller: precipitationRequestController,
    version: precipitationRequestVersion,
  };
}

function finishPrecipitationRequest(requestVersion) {
  if (requestVersion !== precipitationRequestVersion) return;
  precipitationRequestController = null;
  setPrecipitationLoading(false);
}

async function loadWeatherForecastSession(input, context) {
  if (weatherForecastSession) return true;
  const request = beginPrecipitationRequest();
  try {
    const response = await fetchJson(
      "/api/meteo/precipitations/echances",
      { signal: request.controller.signal },
    );
    if (!weatherRequestIsCurrent(input, request.version, context)) {
      return false;
    }
    const data = validateWeatherForecastSession(response);
    weatherForecastSession = data;
    selectedWeatherStepIndex = 0;
    renderWeatherTimeline();
    return true;
  } finally {
    finishPrecipitationRequest(request.version);
  }
}

async function selectWeatherForecastIndex(index, input, context, statusToken) {
  const steps = weatherForecastSession?.forecast_steps || [];
  const boundedIndex = Math.max(0, Math.min(Number(index), steps.length - 1));
  const step = steps[boundedIndex];
  if (!step) {
    throw new Error("Échéance météo indisponible.");
  }
  selectedWeatherStepIndex = boundedIndex;
  renderWeatherTimeline();

  const cached = precipitationDataByValidTime.get(step.valid_time);
  if (cached) {
    cancelPrecipitationRequest();
    if (input.checked && appStatusIsCurrent("weather", statusToken)) {
      applyWeatherGridProvenance(step, cached);
      renderPrecipitationLayer(cached);
      clearAppStatus("weather", statusToken);
    }
    return cached;
  }

  const request = beginPrecipitationRequest();
  try {
    const data = await fetchJson(
      `/api/meteo/precipitations?valid_time=${encodeURIComponent(step.valid_time)}`,
      { signal: request.controller.signal },
    );
    if (!weatherRequestIsCurrent(
      input,
      request.version,
      context,
      step.valid_time,
    ) || !appStatusIsCurrent("weather", statusToken)) {
      return null;
    }
    if (!Array.isArray(data?.cells) || data.valid_time !== step.valid_time) {
      throw new Error("Réponse météo invalide.");
    }
    precipitationDataByValidTime.set(step.valid_time, data);
    applyWeatherGridProvenance(step, data);
    renderPrecipitationLayer(data);
    clearAppStatus("weather", statusToken);
    return data;
  } finally {
    finishPrecipitationRequest(request.version);
  }
}

async function togglePrecipitationLayer(input) {
  if (!input.checked) {
    cancelPrecipitationRequest();
    clearAppStatus("weather");
    clearPrecipitationLayer();
    clearWeatherLegend();
    clearWeatherTimeline();
    return;
  }
  if (!currentTerritoireFeature()) {
    input.checked = false;
    beginAppStatus(
      "weather",
      "error",
      "Importez d'abord un territoire administratif pour afficher la météo.",
    );
    return;
  }

  const context = prepareWeatherFrontendContext();
  setPrecipitationLoading(true);
  const statusToken = beginAppStatus(
    "weather",
    "loading",
    "Chargement des précipitations prévues…",
  );
  try {
    if (!await loadWeatherForecastSession(input, context)) {
      return;
    }
    renderWeatherTimeline();
    await selectWeatherForecastIndex(
      selectedWeatherStepIndex,
      input,
      context,
      statusToken,
    );
  } catch (error) {
    if (
      error?.name === "AbortError"
      || !appStatusIsCurrent("weather", statusToken)
    ) {
      return;
    }
    input.checked = false;
    clearPrecipitationLayer();
    clearWeatherLegend();
    clearWeatherTimeline();
    updateAppStatus(
      "weather",
      statusToken,
      "error",
      error.message,
    );
  } finally {
    if (!precipitationRequestPending) {
      setPrecipitationLoading(false);
    }
  }
}

function setToolsMenuOpen(open) {
  toolsMenuList.hidden = !open;
  toolsMenuButton.setAttribute("aria-expanded", String(open));
  positionCompactLandscapeMenu(toolsMenuButton, toolsMenuList);
}

function renderTerritoireModal({ keepMessage = false } = {}) {
  const feature = currentTerritoireFeature();
  territoireCurrentState.textContent = feature
    ? `Territoire actuel : ${text(feature.properties?.libelle, "Sans libellé")}`
    : "Aucun territoire configuré";
  territoireLibelleInput.value = feature
    ? inputText(feature.properties?.libelle)
    : territoireLibelleInput.value;
  submitTerritoireButton.textContent = feature ? "Remplacer" : "Importer";
  if (!keepMessage) {
    territoireMessage.textContent = "";
    territoireMessage.classList.remove("error");
  }
}

function openTerritoireModal() {
  renderTerritoireModal();
  territoireModal.hidden = false;
  territoireLibelleInput.focus();
}

function closeTerritoireModal() {
  if (territoireImportPending) {
    return;
  }
  territoireModal.hidden = true;
  territoireForm.reset();
  territoireMessage.textContent = "";
  territoireMessage.classList.remove("error");
}

function openMapPrintModal() {
  const bounds = map.getBounds();
  mapPrintSourceBounds = bounds?.isValid?.()
    ? L.latLngBounds(bounds.getSouthWest(), bounds.getNorthEast())
    : null;
  mapPrintMessage.textContent = "";
  mapPrintMessage.classList.remove("error");
  mapPrintModal.hidden = false;
  submitMapPrintButton.focus();
}

function closeMapPrintModal() {
  if (mapPrintPending) return;
  mapPrintModal.hidden = true;
  mapPrintForm.reset();
  mapPrintMessage.textContent = "";
  mapPrintMessage.classList.remove("error");
  mapPrintSourceBounds = null;
}

function setMapPrintPending(pending) {
  mapPrintPending = pending;
  Array.from(mapPrintForm.elements).forEach((element) => {
    element.disabled = pending;
  });
  mapPrintModal.setAttribute("aria-busy", String(pending));
}

function currentMapPrintState() {
  const serialize = (layer) => window.MapPrint.serializeVisibleLayerGroup(map, layer);
  const weatherStep = weatherForecastSession?.forecast_steps?.[selectedWeatherStepIndex];
  return {
    territoryMask: serialize(territoireMaskLayer),
    territoryOutline: serialize(territoireContourLayer),
    troncons: serialize(tronconsGeoJsonLayer),
    tronconsNonRepresentables: [...tronconPrintInventory.values()].filter(feature =>
      !hiddenTronconIds.has(String(feature.properties.id)) && !feature.geometry
      && feature.properties.geometry_type).map(feature => ({
        id: feature.properties.id, type: feature.properties.geometry_type,
      })),
    disorderPoints: serialize(desordrePointLayer),
    disorderLines: serialize(desordreLineLayer),
    disorderPolygons: serialize(desordrePolygonLayer),
    weather: serialize(precipitationLayer),
    weatherLegend: WEATHER_LEGEND_CLASSES.map(([label, color]) => ({ label, color })),
    weatherValidTimeLabel: weatherStep?.valid_time
      ? `Échéance : ${formatWeatherDate(weatherStep.valid_time)} UTC`
      : null,
  };
}

async function submitMapPrint() {
  if (!mapPrintSourceBounds?.isValid?.()) {
    throw new Error("La carte ne possède pas d'emprise exportable.");
  }
  const selected = (name) => mapPrintForm.querySelector(`[name="${name}"]:checked`);
  const options = {
    format: selected("print-format")?.value,
    orientation: selected("print-orientation")?.value,
    output: selected("print-output")?.value,
    legend: Boolean(selected("print-legend")),
    scale: Boolean(selected("print-scale")),
    north: Boolean(selected("print-north")),
  };
  setMapPrintPending(true);
  mapPrintAbortController = new AbortController();
  mapPrintMessage.textContent = "Génération de la carte…";
  mapPrintMessage.classList.remove("error");
  try {
    const selectedBasemap = basemap;
    if (!selectedBasemap) throw new Error("Aucun fond cartographique disponible pour l’impression.");
    if (selectedBasemap.capabilities?.browser_print === false) {
      throw new Error("Le fond actif ne permet pas l’impression navigateur.");
    }
    await window.MapPrint.exportMap({
      L,
      attribution: selectedBasemap.options.attribution,
      options,
      sourceBounds: mapPrintSourceBounds,
      state: currentMapPrintState(),
      tileUrl: selectedBasemap.url,
      tileOptions: selectedBasemap.options,
      tileFormat: selectedBasemap.entry?.tile_format,
      signal: mapPrintAbortController.signal,
    });
    setMapPrintPending(false);
    closeMapPrintModal();
  } finally {
    setMapPrintPending(false);
    mapPrintAbortController = null;
  }
}

function territoireContentType(file) {
  const name = file?.name || "";
  const suffix = name.toLowerCase().split(".").pop();
  if (suffix === "zip") return "application/zip";
  if (suffix === "gpkg") return "application/geopackage+sqlite3";
  throw new Error("Sélectionnez un fichier .gpkg ou .zip.");
}

function setTerritoireImportPending(pending) {
  territoireImportPending = pending;
  territoireLibelleInput.disabled = pending;
  territoireFileInput.disabled = pending;
  territoireLayerInput.disabled = pending;
  submitTerritoireButton.disabled = pending;
  cancelTerritoireModalButton.disabled = pending;
  closeTerritoireModalButton.disabled = pending;
}

async function submitTerritoireImport() {
  const libelle = territoireLibelleInput.value.trim();
  const file = territoireFileInput.files[0];
  if (!libelle) {
    throw new Error("Le libellé est obligatoire.");
  }
  if (!file) {
    throw new Error("Sélectionnez un fichier GeoPackage ou Shapefile ZIP.");
  }
  const contentType = territoireContentType(file);
  const replacing = Boolean(currentTerritoireFeature());
  if (replacing && !window.confirm(
    "Remplacer le territoire administratif actuel ? L'ancien contour ne sera pas conservé."
  )) {
    return;
  }

  const parameters = new URLSearchParams({
    libelle,
    replace: replacing ? "true" : "false",
  });
  const layer = territoireLayerInput.value.trim();
  if (layer) {
    parameters.set("layer", layer);
  }

  setTerritoireImportPending(true);
  territoireMessage.textContent = replacing ? "Remplacement en cours…" : "Import en cours…";
  territoireMessage.classList.remove("error");
  let collection;
  try {
    collection = await fetchJson(
      `/api/territoire-administratif/import?${parameters.toString()}`,
      {
        method: "POST",
        headers: {
          "X-Filename": file.name,
          "Content-Type": contentType,
        },
        body: file,
      },
    );
  } finally {
    setTerritoireImportPending(false);
  }
  const territoryId = featureIdentifier(collection?.features?.[0]);
  // Signal technique same-origin : il actualise map-context dans les autres
  // onglets, sans version métier ni reçu destiné à un journal serveur futur.
  publishConfirmedMutation(
    "territoires-administratifs",
    replacing ? "update" : "create",
    territoryId ? [territoryId] : null, undefined, collection,
  );
  try {
    territoireRequestSequence += 1;
    setTerritoireAdministratifState(collection);
    applyTerritoireCartography(historicalViewportBounds);
    invalidatePrecipitationCache();
    clearVigicruesLayer();
    const vigicruesToggle = document.querySelector(
      '[data-layer-toggle="vigicrues"]',
    );
    if (vigicruesToggle) vigicruesToggle.checked = false;
    invalidateVigicruesRequest(vigicruesToggle);
    territoireFileInput.value = "";
    closeTerritoireModal();
    return collection;
  } catch (refreshError) {
    console.error("Actualisation locale du territoire impossible", refreshError);
    territoireMessage.textContent =
      "Territoire enregistré. L’actualisation locale est incomplète.";
    territoireMessage.classList.add("error");
    return collection;
  }
}

function appendDefinition(list, label, value) {
  if (!showUuid && label === "Identifiant") return;
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = text(value);
  if (label === "Identifiant") {
    term.classList.add("technical-identifier");
    description.classList.add("technical-identifier");
  }
  list.append(term, description);
}

function closePhotoLightbox() {
  photoLightbox.hidden = true;
  lightboxImage.removeAttribute("src");
  lightboxImage.hidden = true;
  currentPhotoIndex = -1;
}

function showPhotoInLightbox(index) {
  const photo = currentObservationPhotos[index];
  if (!photo) {
    return;
  }
  currentPhotoIndex = index;
  lightboxTitle.textContent = text(
    photo.libelle || photo.designation,
    photo.nom_fichier || "Photo",
  );
  lightboxCaption.textContent = [photo.date, photo.commentaire, photo.nom_fichier]
    .concat([photo.cote_libelle, photo.orientation_photo_libelle])
    .filter(Boolean)
    .join(" — ");
  if (photo.content_available && photo.content_url) {
    lightboxImage.src = window.BusinessRuntime?.apiUrl(photo.content_url) || photo.content_url;
    lightboxImage.alt = lightboxTitle.textContent;
    lightboxImage.hidden = false;
    lightboxUnavailable.hidden = true;
  } else {
    lightboxImage.removeAttribute("src");
    lightboxImage.hidden = true;
    lightboxUnavailable.textContent = window.PhotoMedia?.contentNote(photo) || "Photo sans fichier associé.";
    lightboxUnavailable.hidden = false;
  }
  previousPhotoButton.disabled = currentObservationPhotos.length < 2;
  nextPhotoButton.disabled = currentObservationPhotos.length < 2;
  photoLightbox.hidden = false;
  closeLightboxButton.focus();
}

function navigatePhoto(offset) {
  if (currentObservationPhotos.length < 2) {
    return;
  }
  const nextIndex = (
    currentPhotoIndex + offset + currentObservationPhotos.length
  ) % currentObservationPhotos.length;
  showPhotoInLightbox(nextIndex);
}

function renderPhotoMetadata(photos) {
  currentObservationPhotos = photos;
  observationPhotos.replaceChildren();
  photosCount.textContent = `${photos.length} photo(s)`;
  photosStorageNote.textContent = photos.some((photo) => photo.content_available)
    ? "Images disponibles ; les originaux sont conservés."
    : "Métadonnées disponibles ; les fichiers source ne sont pas matérialisés dans la base cible.";
  if (photos.length === 0) {
    const empty = document.createElement("p");
    empty.className = "field-help";
    empty.textContent = "Aucune photo pour cette observation.";
    observationPhotos.append(empty);
  } else photos.forEach((photo, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "photo-card";
    const preview = document.createElement("span");
    preview.className = "photo-placeholder";
    if (photo.content_available && photo.content_url) {
      const image = document.createElement("img");
      const contentUrl = photo.variants?.thumbnail?.content_url || photo.content_url;
      image.src = window.BusinessRuntime?.apiUrl(contentUrl) || contentUrl;
      image.alt = photo.libelle || photo.designation || "Photo";
      image.loading = "lazy"; preview.append(image);
    } else preview.textContent = "Photo";
    const label = document.createElement("strong");
    label.textContent = text(
      photo.libelle || photo.designation,
      photo.nom_fichier || "Photo",
    );
    const metadata = document.createElement("span");
    metadata.textContent = [
      photo.date || photo.nom_fichier,
      photo.cote_libelle,
      photo.orientation_photo_libelle,
      photo.commentaire,
      window.PhotoMedia?.contentNote(photo),
    ].filter(Boolean).join(" — ") || "Sans métadonnée";
    button.append(preview, label, metadata);
    button.addEventListener("click", () => showPhotoInLightbox(index));
    observationPhotos.append(button);
  });
  if (currentObservation && window.PhotoOffline) void window.PhotoOffline.mount(observationPhotos, currentObservation,
    localCount => { if (currentObservation?.id === observationPhotos.querySelector('[data-local-photos]')?.dataset.localPhotos)
      photosCount.textContent = `${photos.length + localCount} photo(s)`; }).catch(error => {
    photosStorageNote.textContent = 'Photos locales indisponibles : ' + error.message;
  });
}

let observationEditGeneration = 0;
let observationReadGeneration = 0;
let observationStale = false;
observationSuiteForm.addEventListener("input", () => { observationEditGeneration += 1; });
observationSuiteForm.addEventListener("change", () => { observationEditGeneration += 1; });

function observationParent(observation) {
  return ObservationForm.parent(observation);
}

function observationParentText(parent) {
  return ObservationForm.parentText({ parent });
}

async function openObservation(observationId, { parent = null, onReturn = null,
  isParentCurrent = null, propagateError = false } = {}) {
  const expectedParent = parent || currentObservation?.parent || {
    resource: "desordres", id: lastServerFeature?.properties?.id,
  };
  if (!expectedParent?.id) return false;
  if (expectedParent.resource !== "desordres") {
    return ObservationDetail.open({ id: observationId, parent: expectedParent, fetchJson,
      onReturn, isParentCurrent });
  }
  const readGeneration = ++observationReadGeneration;
  const editGeneration = observationEditGeneration;
  const expectedParentId = String(expectedParent.id);
  const runtime = window.BusinessRuntime;
  const stillSelected = () => readGeneration === observationReadGeneration
    && runtime === window.BusinessRuntime && (!runtime || runtime.isActive())
    && editorState.objectType === "desordre" && editorState.mode === "edit"
    && String(lastServerFeature?.properties.id || "") === expectedParentId;
  if (!stillSelected()) return false;
  observationsMessage.textContent = "Chargement de l’observation…";
  try {
    const observation = await fetchJson(
      `/api/observations/${encodeURIComponent(observationId)}`,
    );
    const parent = observationParent(observation);
    if (String(observation.id) !== String(observationId)
        || parent.resource !== expectedParent.resource
        || String(parent.id) !== expectedParentId) {
      throw new Error("L’observation ne correspond pas au désordre sélectionné.");
    }
    if (observation.version === null && observation.localStatus) {
      if (!stillSelected()) return false;
      await window.OfflineRuntime.showDetail("observations", observationId);
      return true;
    }
    if (window.OfflineRuntime?.isOffline()) {
      if (!stillSelected()) return false;
      await window.OfflineRuntime.showDetail("observations", observationId);
      return true;
    }
    const observationFields=ObservationForm.fields(observation);
    const catalogs=new Map(await Promise.all(observationFields.filter(f=>f[2]==='select').map(async f=>
      [f[0],await ReferenceData.load(f[4],fetchJson)])));
    if (!stillSelected()) return false;
    if (editGeneration !== observationEditGeneration) {
      observationStale = true;
      observationSuiteMessage.textContent =
        "Cette observation a été modifiée ailleurs. Votre saisie est conservée.";
      observationsMessage.textContent = "";
      return false;
    }
    currentObservation = observation;
    observationConflict = false;
    observationStale = false;
    const view=MapFormViews.observation;
    observationControls=new Map();
    for(const [key,label,kind,,catalog,labelKey] of observationFields){
      const control=view.controls.get(key);
      if(kind==='select') ReferenceData.fillSelect(control,catalogs.get(key),observation[key],`Sans ${label.toLowerCase()}`,observation[labelKey]);
      else control.value=observation[key]??'';
      observationControls.set(key,control);
    }
    view.controls.get('parent_label').value=ObservationForm.parentText(observation);
    view.sync({mode:'edit',objectId:observation.id});
    view.setSubject({ objectName: observation.designation });
    observationSuiteMessage.textContent = "";
    observationSuiteMessage.classList.remove("error");
    renderPhotoMetadata(Array.isArray(observation.photos) ? observation.photos : []);
    observationsListView.hidden = true;
    observationDetailView.hidden = false;
    observationsMessage.textContent = "";
    return true;
  } catch (error) {
    if (!stillSelected()) return false;
    console.error("Lecture de l’observation impossible", error);
    observationsMessage.textContent = error.message;
    if (propagateError) throw error;
    return false;
  }
}

observationSuiteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentObservation?.id || observationSuiteSubmitting) return;
  observationSuiteSubmitting = true;
  // Keep the submitted draft immutable until its authoritative reread finishes.
  // Restore previous disabled states on every exit, including offline and errors.
  const controls = [
    ...observationSuiteForm.querySelectorAll("input, select, textarea, button"),
    backToObservationsButton, generalTabButton, observationsTabButton, closeEditorButton,
    MapFormViews.observation.close, editorCloseTarget(),
  ];
  const disabledStates = controls.map(control => control.disabled);
  MapFormViews.observation.sync({mode:"edit",objectId:currentObservation.id,pending:true});
  controls.forEach(control => { control.disabled = true; });
  try {
    observationSuiteMessage.textContent = "Enregistrement…";
    observationSuiteMessage.classList.remove("error");
    let updated;
    try {
      const changes = ObservationForm.changes(observationControls, currentObservation);
      if (!Object.keys(changes).length) {
        observationSuiteMessage.textContent = "Aucune modification.";
        return;
      }
      const changed = changes;
      if (window.OfflineOperationsRuntime?.supported("observations", changed)
          && (await window.OfflineRuntime?.inspect())?.records.some(row => row.resource === "observations" && row.id === String(currentObservation.id))) {
        const result = await window.OfflineOperationsRuntime.submit({ resource: "observations", resourceId: String(currentObservation.id),
          baseVersion: currentObservation.version, changes: changed });
        observationConflict = result.status === "conflict";
        if (result.status === "synced") {
          const loaded = await openObservation(currentObservation.id);
          observationSuiteMessage.textContent = loaded ? "Observation relue depuis PostgreSQL." : "Observation enregistrée. L’actualisation locale est incomplète.";
        } else observationSuiteMessage.textContent = `Enregistré localement — ${window.OfflineOperations.statusLabel(result.status)}. Intention conservée dans Outils → Consultation hors ligne.`;
        return;
      }
      updated = await fetchJson(
        `/api/observations/${encodeURIComponent(currentObservation.id)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", ...ResourceConcurrency.headers(currentObservation.version) },
          body: JSON.stringify(changes),
        },
      );
    } catch (error) {
      if (error.status === 409) observationConflict = true;
      observationSuiteMessage.textContent = error.message;
      observationSuiteMessage.classList.add("error");
      return;
    }
    const observationId = updated?.id ?? currentObservation.id;
    publishConfirmedMutation("observations", "update", [String(observationId)], undefined, updated, true);
    try {
      currentObservation = updated;
      if (!await openObservation(updated.id)) {
        throw new Error("La relecture autoritaire de l’observation a échoué.");
      }
      observationSuiteMessage.textContent = "Observation relue depuis PostgreSQL.";
      observationSuiteMessage.classList.remove("error");
    } catch (refreshError) {
      console.error("Actualisation locale de l’observation impossible", refreshError);
      observationSuiteMessage.textContent =
        "Observation enregistrée. L’actualisation locale est incomplète.";
      observationSuiteMessage.classList.add("error");
    }
  } finally {
    observationSuiteSubmitting = false;
    controls.forEach((control, index) => { control.disabled = disabledStates[index]; });
    MapFormViews.observation.sync({mode:"edit",objectId:currentObservation?.id,pending:false,
      conflict:observationConflict,dirty:Boolean(currentObservation && Object.keys(ObservationForm.changes(observationControls,currentObservation)).length)});
  }
});

function renderObservations(data) {
  const observations = Array.isArray(data.observations) ? data.observations : [];
  observationsList.replaceChildren();
  observationsCount.textContent = `${observations.length} observation(s)`;
  if (observations.length === 0) {
    observationsMessage.textContent = "Aucune observation pour ce désordre.";
    return;
  }
  observationsMessage.textContent = "";
  observations.forEach((observation) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "observation-row";
    const heading = document.createElement("span");
    heading.className = "observation-row-heading";
    heading.textContent = [observation.date, observation.urgence_libelle]
      .filter(Boolean)
      .join(" — ") || "Observation sans date";
    const designation = document.createElement("strong");
    designation.textContent = text(observation.designation, "Sans désignation");
    const evolution = document.createElement("span");
    evolution.textContent = text(observation.evolution, "").slice(0, 120);
    const photoCount = document.createElement("small");
    photoCount.textContent = `${observation.photo_count || 0} photo(s)`;
    button.append(heading, designation, evolution, photoCount);
    button.addEventListener("click", () => openObservation(observation.id, { parent: data.parent }));
    observationsList.append(button);
  });
}

async function loadObservations({ force = false, propagateError = false } = {}) {
  const desordreId = lastServerFeature?.properties.id;
  const identifier = String(desordreId || "");
  if (!identifier || (!force && !observationsListDirty
      && observationsLoadedFor === identifier)) {
    return false;
  }
  const generation = ++observationsLoadSequence;
  observationsMessage.textContent = "Chargement des observations…";
  observationsList.replaceChildren();
  try {
    const data = await fetchJson(
      `/api/desordres/${encodeURIComponent(desordreId)}/observations`,
    );
    if (generation !== observationsLoadSequence
        || String(lastServerFeature?.properties.id || "") !== identifier) return false;
    if (data.parent?.resource !== "desordres"
        || String(data.parent.id) !== String(desordreId)) {
      throw new Error("Réponse d’observations incohérente.");
    }
    renderObservations(data);
    observationsLoadedFor = identifier;
    observationsListDirty = false;
    return true;
  } catch (error) {
    if (generation !== observationsLoadSequence) return false;
    console.error("Lecture des observations impossible", error);
    observationsMessage.textContent = error.message;
    observationsListDirty = true;
    if (propagateError) throw error;
    return false;
  }
}

function showEditorTab(name) {
  if(!canSelectEditorTab(name))return;
  if (name === "observations" && (observationsTabButton.hidden || observationsTabButton.disabled)) return;
  const showGeneral = name === "general";
  if (!showGeneral) cancelGpsCandidate();
  MapFormViews.disorder.show(name,false);
  generalTab.hidden = !showGeneral;
  observationsTab.hidden = showGeneral;
  generalTabButton.classList.toggle("active", showGeneral);
  observationsTabButton.classList.toggle("active", !showGeneral);
  generalTabButton.setAttribute("aria-selected", String(showGeneral));
  observationsTabButton.setAttribute("aria-selected", String(!showGeneral));
  if (!showGeneral) {
    observationsListView.hidden = false;
    observationDetailView.hidden = true;
    closePhotoLightbox();
    loadObservations();
  }
  syncDesordrePdfAction();
}


function clearHighlightedTroncon() {
  highlightedTronconLayer?.setZIndexOffset?.(0);
  if (highlightedTronconLayer && tronconsGeoJsonLayer) {
    tronconsGeoJsonLayer.resetStyle(highlightedTronconLayer);
  }
  highlightedTronconLayer = null;
}

function highlightTroncon(tronconId) {
  clearHighlightedTroncon();
  const layer = tronconLayersById.get(String(tronconId));
  if (!layer) {
    return null;
  }
  layer.setStyle?.({ color: "#1769aa", opacity: 1, weight: 7 });
  layer.setZIndexOffset?.(1000);
  layer.bringToFront?.();
  highlightedTronconLayer = layer;
  return layer;
}

function descendantTronconIds(kind, item) {
  return HeritagePanel.descendantTronconIds(kind, item);
}


function applyIndividualTronconVisibility() {
  if (!tronconsGeoJsonLayer) {
    return;
  }
  tronconLayersById.forEach((layer, tronconId) => {
    const shouldBeVisible = !hiddenTronconIds.has(tronconId);
    const isVisible = tronconsGeoJsonLayer.hasLayer(layer);
    if (shouldBeVisible && !isVisible) {
      tronconsGeoJsonLayer.addLayer(layer);
    } else if (!shouldBeVisible && isVisible) {
      tronconsGeoJsonLayer.removeLayer(layer);
    }
  });
}

function desordreLayerGroup(layer) {
  const geometryType = layer.feature?.geometry?.type;
  if (geometryType === "Point") return desordrePointLayer;
  if (geometryType === "LineString") return desordreLineLayer;
  if (geometryType === "Polygon") return desordrePolygonLayer;
  return null;
}

function desordreBelongsToHeritageSelection(layer) {
  const tronconIds = Array.isArray(layer.feature?.properties?.troncon_ids)
    ? layer.feature.properties.troncon_ids.map(String)
    : [];
  return !tronconIds.some((id) => hiddenTronconIds.has(id));
}

function updateAppBaseStatusFromHeritageSelection() {
  if (!tronconsGeoJsonLayer || !desordresGeoJsonLayer) return false;
  const tronconCount = Array.from(tronconLayersById.keys())
    .filter((id) => !hiddenTronconIds.has(id)).length;
  const desordreCount = Array.from(desordreLayersById.values())
    .filter(desordreBelongsToHeritageSelection).length;
  const tronconLabel = tronconCount === 1 ? "tronçon" : "tronçons";
  const desordreLabel = desordreCount === 1 ? "désordre" : "désordres";
  setAppBaseStatus(
    `${tronconCount} ${tronconLabel} · ${desordreCount} ${desordreLabel}`,
  );
  return true;
}

function applyIndividualDesordreVisibility() {
  desordreLayersById.forEach((layer) => {
    const group = desordreLayerGroup(layer);
    if (!group) return;
    const shouldBeVisible = desordreBelongsToHeritageSelection(layer);
    const isVisible = group.hasLayer(layer);
    if (shouldBeVisible && !isVisible) {
      group.addLayer(layer);
    } else if (!shouldBeVisible && isVisible) {
      group.removeLayer(layer);
    }
  });
  updateAppBaseStatusFromHeritageSelection();
}


function tronconBounds(layer) {
  const bounds=layer?.getBounds?.();
  if(bounds?.isValid?.())return bounds;
  const point=layer?.getLatLng?.();
  return point ? L.latLngBounds([point,point]) : null;
}

function heritageObjectBounds(kind, item) {
  const bounds = L.latLngBounds();
  descendantTronconIds(kind, item).forEach((tronconId) => {
    const layerBounds = tronconBounds(tronconLayersById.get(tronconId));
    if (layerBounds?.isValid?.()) {
      bounds.extend(layerBounds);
    }
  });
  return bounds;
}

function zoomToHeritageObject(kind, item) {
  const bounds = heritageObjectBounds(kind, item);
  if (!bounds.isValid()) {
    return false;
  }
  map.fitBounds(bounds, { padding: [40, 40], maxZoom: 18 });
  return true;
}

function renderHeritageTree(data) {
  heritagePanelController.render(data);
}

async function loadHeritageTree() {
  await window.ResourceEventTransport?.bootstrap;
  return heritagePanelController.load();
}

function updateHeritageVisibilityControls() {
  heritagePanelController.updateControls();
}


function setHeritagePanelOpen(open) {
  heritagePanelController.setOpen(open);
  mapLegend.hidden = open;
}

function refreshMapSize() {
  if (!mapElement.hidden) {
    window.requestAnimationFrame(() => map.invalidateSize());
  }
}

function setAiPanelOpen(open) {
  aiPanelController.setOpen(open);
}

function fillPwaSettingsForm(settings) {
  pwaDistanceMeters.value = settings.tracking.distanceMeters;
  pwaIntervalSeconds.value = settings.tracking.intervalSeconds;
  pwaMaxAccuracyMeters.value = settings.tracking.maxAccuracyMeters;
  pwaSmartPolygonCorrection.checked = settings.tracking.smartPolygonCorrection;
  pwaShowUserPosition.checked = settings.showUserPosition;
  pwaShowWazeButton.checked = settings.showWazeButton;
  pwaProximityEnabled.checked = settings.proximity.enabled;
  pwaRadiusKm.value = settings.proximity.radiusKm;
}

// Cette couche ne participe à aucun brouillon ni à aucune couche métier.
// Le marqueur Leaflet standard est dans markerPane (600) ; la télémétrie est à 650.
// Le candidat reste au-dessus des deux pendant l'inspection, puis disparaît.
const mapCandidateLayer = L.layerGroup().addTo(map);
let mapCandidateFocusVersion = 0;
let pendingMapCandidateBounds = null;

function clearMapCandidate() {
  mapCandidateFocusVersion += 1;
  pendingMapCandidateBounds = null;
  mapCandidateLayer.clearLayers();
}

function focusPendingMapCandidate() {
  if (!mapViewportReady || !pendingMapCandidateBounds) return;
  const version = mapCandidateFocusVersion;
  window.requestAnimationFrame(() => {
    if (version !== mapCandidateFocusVersion || !pendingMapCandidateBounds) return;
    map.invalidateSize({ pan: false, animate: false });
    map.fitBounds(pendingMapCandidateBounds, {
      padding: [48, 48], maxZoom: 17, animate: false,
    });
    pendingMapCandidateBounds = null;
  });
}

function showPointMapCandidate(candidate, { center = true } = {}) {
  clearMapCandidate();
  const latLng = L.latLng(candidate.latitude, candidate.longitude);
  const accuracy = L.circle(latLng, {
    pane: MAP_PANES.MAP_CANDIDATE.name, radius: candidate.accuracy,
    color: "#274f43", fillColor: "#5f8278", fillOpacity: 0.16,
    weight: 2, dashArray: "4 4", interactive: false,
  }).addTo(mapCandidateLayer);
  L.marker(latLng, {
    pane: MAP_PANES.MAP_CANDIDATE.name, interactive: false, keyboard: false,
    icon: L.divIcon({ className: "map-candidate-marker", iconSize: [24, 24],
      iconAnchor: [12, 12] }),
  }).addTo(mapCandidateLayer);
  if (center) {
    pendingMapCandidateBounds = accuracy.getBounds();
    focusPendingMapCandidate();
  }
}
let userPositionLayer = null;
let userPositionMarker = null;
let userPositionAccuracy = null;
let pendingUserPositionCenter = null;

function removeUserPositionLayer() {
  pendingUserPositionCenter = null;
  if (userPositionLayer) map.removeLayer(userPositionLayer);
  userPositionLayer = null;
  userPositionMarker = null;
  userPositionAccuracy = null;
}

function renderUserPosition(position, { first }) {
  const latLng = L.latLng(position.latitude, position.longitude);
  if (!userPositionLayer) {
    userPositionLayer = L.layerGroup().addTo(map);
    userPositionAccuracy = L.circle(latLng, {
      pane: MAP_PANES.USER_POSITION.name, radius: position.accuracy,
      color: "#3f685e", fillColor: "#5f8278", fillOpacity: 0.12,
      weight: 1, interactive: false,
    }).addTo(userPositionLayer);
    userPositionMarker = L.marker(latLng, {
      pane: MAP_PANES.USER_POSITION.name, interactive: false, keyboard: false,
      icon: L.divIcon({ className: "user-position-marker", iconSize: [18, 18],
        iconAnchor: [9, 9] }),
    }).addTo(userPositionLayer);
  } else {
    userPositionAccuracy.setLatLng(latLng).setRadius(position.accuracy);
    userPositionMarker.setLatLng(latLng);
  }
  // Seul le premier fix recentre ; les suivants laissent la carte explorée en place.
  if (first) {
    if (mapViewportReady) map.setView(latLng, Math.max(map.getZoom(), 16), { animate: false });
    else pendingUserPositionCenter = latLng;
  }
}

const userPositionMessages = Object.freeze({
  PERMISSION_DENIED: "Accès à la position refusé. Autorisez-le dans les réglages de l’appareil.",
  UNSUPPORTED: "Géolocalisation indisponible sur cet appareil.",
  POSITION_UNAVAILABLE: "Position momentanément indisponible.",
  TIMEOUT: "Recherche de position expirée. Le suivi reste activé.",
  INVALID_POSITION: "Position GPS invalide. Le suivi attend une nouvelle position.",
});

const userPositionController = PwaMapPosition.createController({
  geolocation: PwaGeolocation,
  settings: PwaMapSettings,
  onChange(state) {
    if (!state.active) removeUserPositionLayer();
    userPositionMessage = state.phase === "acquiring"
      ? "Recherche de position…"
      : state.phase === "low_accuracy"
        ? "Précision insuffisante. Dernière position valide conservée."
        : state.phase === "error"
          ? userPositionMessages[state.error?.code] || "Position indisponible."
          : "";
    renderUserPositionStatus();
  },
  onPosition: renderUserPosition,
  onPermanentError(error) {
    try {
      PwaMapSettings.saveSettings({ ...PwaMapSettings.loadSettings(), showUserPosition: false });
    } catch (_error) {
      // L'échec de stockage ne doit pas relancer le watch après un refus.
    }
    pwaShowUserPosition.checked = false;
    if (!pwaSettingsPanel.hidden) {
      pwaSettingsMessage.textContent = userPositionMessages[error.code];
    }
  },
});

function applyUserPositionPreference() {
  const enabled = PwaGeolocation.isStandalone()
    && PwaMapSettings.loadSettings().showUserPosition;
  if (enabled) userPositionController.start();
  else userPositionController.stop();
  if (!PwaGeolocation.isStandalone()) {
    userPositionMessage = "";
    mapLocateMessage = "";
    renderUserPositionStatus();
  }
}

function setPwaSettingsPanelOpen(open) {
  if (open) {
    fillPwaSettingsForm(PwaMapSettings.loadSettings());
    pwaSettingsMessage.textContent = "";
  }
  pwaSettingsPanel.hidden = !open;
  pwaSettingsToggleButton.setAttribute("aria-expanded", String(open));
  if (open) pwaDistanceMeters.focus();
}

function setEditorPanelVisible(open) {
  // Fermer visuellement conserve les brouillons et les validations métier.
  if (!open) {
    legacyEditorGeneration += 1;
    legacyEditorLoading = false;
    cancelGpsCandidate();
    finishEditorMapInteraction({ restoreFocus: false });
  }
  if (open && !activeBusinessEditor) syncLegacyEditorPresentation();
  topNavigation[open ? "open" : "close"]("editor");
}

let activeBusinessEditor = null;
function editorCloseTarget() { return activeBusinessEditor?.formView?.close || (editorState.objectType === "desordre" ? MapFormViews.disorder.close : MapFormViews.heritage.close); }
const businessEditorPanel = {
  isActive(editor) { return activeBusinessEditor === editor; },
  bindCoordinates(view, operations) {
    EntityGeometryDraft.bindCoordinates({
      view, getCRS: () => businessCRS, loadCRS: loadBusinessCRS, project: window.proj4, request: fetchJson, ...operations,
    });
  },
  connect({ element, shell, label, onObservations, isDirty,
    hasActiveDraft, onDiscard, formView = null, canLeave = null, syncState = null, isSaving = () => false }) {
    const view = formView;
    if (!view) throw new Error("EntityFormView est obligatoire pour cet éditeur.");
    let loading = false;
    const owner = {
      element,
      formView,
      get selection() { return shell.state.mode === "edit" && shell.state.objectId
        ? { entityClass: shell.state.objectType, id: shell.state.objectId } : null; },
      close: () => session.close(),
      discard: () => session.discard(),
    };
    const session = {
      isActive: () => this.isActive(owner),
      showTab: (name) => view.show(name),
      sync: () => formView && syncState ? syncState()
        : view.sync({ ...shell.state, dirty: !loading && isDirty(), pending: shell.state.pending || isSaving(), loading }),
      setLoading(value) { loading = Boolean(value); session.sync(); },
      update: (options) => this.update(owner, options),
      discard: () => {
        onDiscard();
        shell.cancel();
        loading = false;
        view.show("general");
        this.close(owner);
        return true;
      },
      close: () => {
        if (canLeave) return canLeave() ? session.discard() : false;
        if (shell.state.pending) return false;
        const dirty = !loading && isDirty();
        shell.markDirty(dirty);
        const draft = shell.state.mode === "create"
          ? dirty || hasActiveDraft()
          : shell.hasDraft({ active: hasActiveDraft() });
        if (draft && !window.confirm(`Abandonner les modifications de ${label} ?`)) return false;
        return session.discard();
      },
      prepare: (id = null) => {
        if (session.isActive()) {
          if (id && shell.state.mode === "edit" && String(shell.state.objectId) === String(id)) {
            this.activate(owner);
            session.sync();
            return false;
          }
          if (!id && shell.state.mode === "create") {
            this.activate(owner);
            return false;
          }
          if (!session.close()) {
            return false;
          }
        }
        return this.activate(owner);
      },
    };
    if (!syncState) {
      const reflect=()=>session.sync();
      element.addEventListener("input",reflect);element.addEventListener("change",reflect);
    }
    session.sync();
    return session;
  },
  activate(editor) {
    if (activeBusinessEditor && activeBusinessEditor !== editor
        && !activeBusinessEditor.close()) {
      setEditorPanelVisible(true);
      return false;
    }
    if (!activeBusinessEditor && (!editorPanel.hidden || legacyEditorHasDraft())) {
      closeEditorButton.click();
      if (!editorPanel.hidden || legacyEditorHasDraft()) { setEditorPanelVisible(true); return false; }
    }
    legacyEditorGeneration += 1;
    legacyEditorLoading = false;
    activeBusinessEditor = editor;
    document.querySelector("#heritage-create-host").hidden = true;
    document.querySelector("#disorder-editor-host").hidden = true;
    setMapUrlSelection(null, null);
    businessEditorContent.replaceChildren(editor.element);
    businessEditorContent.hidden = false;
    editorTabs.hidden = true;
    generalTab.hidden = true;
    observationsTab.hidden = true;
    setEditorPanelVisible(true);
    (editor.formView?.close || closeEditorButton).focus();
    return true;
  },
  update(editor, { objectName } = {}) {
    if (activeBusinessEditor !== editor) return;
    if (objectName !== undefined) editor.formView?.setSubject({ objectName });
    if (editor.selection) setMapUrlSelection(editor.selection.entityClass, editor.selection.id);
  },
  close(editor) {
    if (activeBusinessEditor !== editor) return;
    activeBusinessEditor = null;

    setMapUrlSelection(null, null);
    businessEditorContent.hidden = true;
    setEditorPanelVisible(false);
  },
  beginPointPlacement(cancel, returnFocusTarget) {
    beginEditorMapInteraction({ kind: "business-point", instruction: "Choisissez le Point sur la carte.",
      cancel, returnFocusTarget });
  },
  finishPointPlacement() { finishEditorMapInteraction(); },
  beginGeometryDrawing({ instruction, cancel, finish, returnFocusTarget }) {
    beginEditorMapInteraction({ kind: "business-geometry", instruction, cancel,
      finishAction: finish, finishLabel: "Terminer", returnFocusTarget });
  },
  updateGeometryDrawing(instruction, finishAvailable) {
    if (editorMapInteraction?.kind !== "business-geometry") return;
    updateEditorMapInteractionInstruction(instruction);
    setEditorMapInteractionFinishAvailable(finishAvailable);
  },
  finishGeometryDrawing() {
    if (editorMapInteraction?.kind === "business-geometry") finishEditorMapInteraction();
  },
  beginGpsTracking(cancel) {
    beginEditorMapInteraction({ kind: "business-gps", instruction: "Relevé GPS en cours.",
      cancel, returnFocusTarget: editorCloseTarget });
  },
  finishGpsTracking() {
    if (editorMapInteraction?.kind === "business-gps") finishEditorMapInteraction();
  },
};

let disorderSaveIntent = null;
let disorderEditAcquisition = null;
let disorderNumericBasis = null;
let desordreSpatialDraft = null;
let desordreAcquisitionGeometry = null;
let legacyDrawingAcquisition = null;
let creationGeometryBeforeDrawing = null;
const geometryTypeBlockedMessage = "Annulez le dessin ou effacez les coordonnées avant de changer de type géométrique.";
function clearResolvedGeometryTypeWarning() {
  if (!provisionalDesordreLayer && !desordreDraftHasNumericCoordinates()
      && desordreCreateMessage.textContent === geometryTypeBlockedMessage) {
    desordreCreateMessage.textContent = "";
    desordreCreateMessage.classList.remove("error");
  }
}
let legacyEditorGeneration = 0;
let legacyEditorLoading = false;
let heritageCreationBaseline = null;
function captureLegacyCreationForm() {
  const form = editorState.objectType === "desordre" ? desordreEditorForm : heritageObjectForm;
  // The original creation snapshot did not include the mode-specific controls.
  // Their generated names must not make a reload of the bornage options dirty.
  const localGeometryFields = editorState.objectType === "desordre"
    ? MapFormViews.disorder.geometry.component.querySelectorAll("[name]")
    : MapFormViews.heritage.geometry.block.querySelectorAll("[name]");
  const internalNames = new Set(Array.from(localGeometryFields, control => control.name));
  return JSON.stringify([...new FormData(form)].filter(([name]) => !internalNames.has(name)));
}
function legacyEditorHasDraft() {
  if ((heritageObjectForm.hidden && desordreEditorForm.hidden) || activeBusinessEditor || legacyEditorLoading) return false;
  if (editorState.mode === "create") return Boolean(provisionalTronconLayer || provisionalDesordreLayer
    || cancelledTronconGeometry || cancelledDesordreGeometry
    || (editorState.objectType === "troncon" && MapFormViews.heritage.geometry.coordinateEntry.hasDraft?.())
    || (heritageCreationBaseline !== null && heritageCreationBaseline !== captureLegacyCreationForm()));
  return editorState.objectType === "desordre" ? desordreEditorHasDraft() : entityEditor.hasDraft();
}
function canLeaveLegacyEditor() {
  if (creationRequestInFlight || lineRequestInFlight || graphicRequestInFlight || editorState.pending || editorState.uncertain) return false;
  return !legacyEditorHasDraft() || window.confirm("Abandonner les modifications de l'objet ouvert ?");
}
function syncLegacyEditorPresentation() {
  if (activeBusinessEditor) return;
  const disorder = editorState.objectType === "desordre";
  const heritage = ["systeme", "digue", "troncon"].includes(editorState.objectType);
  const supportsObservations = disorder || editorState.objectType === "troncon";
  const exists = editorState.mode === "edit" && Boolean(editorState.objectId)
    && (!disorder || String(lastServerFeature?.properties.id) === String(editorState.objectId));
  const pending = creationRequestInFlight || lineRequestInFlight || graphicRequestInFlight || editorState.pending;
  const unavailable = pending || legacyEditorLoading;
  const view=disorder ? MapFormViews.disorder : MapFormViews.heritage;
  document.querySelector('#disorder-editor-host').hidden=!disorder || desordreEditorForm.hidden;
  document.querySelector('#heritage-create-host').hidden=!heritage || heritageObjectForm.hidden;
  view.sync({...editorState,pending,loading:legacyEditorLoading,dirty:legacyEditorHasDraft(),objectId:exists?editorState.objectId:null});
  if (heritage) view.setSubject({ family: heritageCreationTypes[editorState.objectType].resource });
  else if (disorder) view.setSubject({ objectName: exists ? lastServerFeature.properties.designation : "" });
  if(heritage && view.observationsTab)view.observationsTab.hidden=editorState.objectType!=="troncon";
  view.general.hidden=disorder && generalTabButton.getAttribute('aria-selected')==='false';
  editorTabs.hidden = false;
  observationsTabButton.hidden = !supportsObservations;
  observationsTabButton.disabled = !supportsObservations || !exists || unavailable
    || lineEditActive || graphicEditActive || polygonEditActive;
  heritageCreateActions.hidden = !heritage;
  heritageCreateMessage.hidden = !heritage;
  desordreCreateActions.hidden = !disorder;
  desordreCreateMessage.hidden = !disorder;
  desordrePdfButton.parentElement.hidden = !disorder;
  desordrePdfStatus.hidden = !disorder;
  const can = permission => !window.BusinessRuntime || BusinessRuntime.can(permission);
  const writable = can(editorState.mode === "create" ? "business.create" : "business.update");
  if (heritage) {
    submitCreateButton.hidden = !writable;
    heritageObjectForm.querySelector("fieldset").disabled = unavailable || !writable;
  }
  if (disorder) {
    let reading = syncLegacyEditorPresentation.spatialReading;
    if (!reading) {
      const host = document.createElement("section");
      host.dataset.spatialRead = ""; view.geometry.block.append(host);
      reading = syncLegacyEditorPresentation.spatialReading = { host };
    }
    reading.host.hidden = !exists;
    const feature = exists ? lastServerFeature : null, runtime = window.BusinessRuntime;
    if (reading.feature !== feature || reading.runtime !== runtime) {
      Object.assign(reading, { feature, runtime });
      if (feature) EntityGeometryDraft.renderRead({ container: reading.host,
        geometry: feature.geometry, values: feature.properties, request: fetchJson,
        isCurrent: () => reading.feature === feature && lastServerFeature === feature
          && runtime === window.BusinessRuntime && runtime?.isActive?.() !== false });
    }
    submitDesordreCreateButton.hidden = !writable;
    desordreEditorForm.querySelector("fieldset").disabled = unavailable || !writable || editorState.uncertain || editorState.forbidden || editorState.conflict;
    submitDesordreCreateButton.disabled = unavailable || editorState.forbidden || editorState.conflict || graphicEditActive || lineEditActive || polygonEditActive || Boolean(legacyDrawingAcquisition);
    document.querySelector("#create-desordre-observation").hidden = !exists || !can("business.create");
  }
}

function leaveBusinessEditor() {
  if (activeBusinessEditor) {
    if (activeBusinessEditor.close()) return true;
    setEditorPanelVisible(true);
    return false;
  }
  if (!editorPanel.hidden || legacyEditorHasDraft()) {
    closeEditorButton.click();
    if (!editorPanel.hidden || legacyEditorHasDraft()) { setEditorPanelVisible(true); return false; }
  }
  return true;
}

const heritageAttributeEditor = MapHeritageEditor.create({
  map, L,
  panel: businessEditorPanel,
  onSaved(entity, payload) {
    if (entity.entityClass === "troncons") upsertTronconOnMap(payload);
  },
  onMutationConfirmed: mutation => ResourceMutationController.publishMutation(mutation, { applyLocally: true }),
});

async function openHeritageEdit(resource, id) {
  if (editorMapInteractionBlocksObjectOpen()) return null;
  try { return await heritageAttributeEditor.openEdit(resource, id); }
  catch (error) {
    beginAppStatus("general", "error", error.message);
    return null;
  }
}


function shouldSuspendEditorMapInteraction(interaction, state, mode) {
  return Boolean(interaction && state.editor && mode === "single-tool");
}

function inspectingGpsPoint() {
  return editorMapInteraction?.phase === "inspection-point";
}

function setEditorMapNavigationLocked(locked) {
  headerNavigation.inert = locked;
}

function editorMapInteractionMapCovered(state, mode, editorSuspended) {
  const editorCoversMap = state.editor && !editorSuspended;
  return (mode === "single-tool" && (state.heritage || editorCoversMap)) ||
    (state.heritage && editorCoversMap &&
      heritagePanel.getBoundingClientRect().right >=
        editorPanel.getBoundingClientRect().left);
}

function syncEditorMapInteractionPresentation(
  state = currentTopNavigationState,
  mode = currentTopNavigationLayout,
  { focusTarget = null } = {},
) {
  currentTopNavigationState = { ...state };
  currentTopNavigationLayout = mode;
  const suspend = shouldSuspendEditorMapInteraction(
    editorMapInteraction,
    state,
    mode,
  );
  const wasSuspended = editorMapPresentationSuspended;
  const manualDrawing = editorMapInteraction && !["business-gps", "gps-troncon", "gps-desordre"].includes(editorMapInteraction.kind)
    && !inspectingGpsPoint();
  const gpsTracking = ["business-gps", "gps-troncon", "gps-desordre"].includes(editorMapInteraction?.kind);
  const showBanner = !gpsTracking && (suspend || Boolean(editorMapInteraction?.phase) || manualDrawing);
  const focusGps = () => document.querySelector(".gps-geometry-tracking--floating")?.focus({ preventScroll: true });

  if (suspend && !wasSuspended) {
    editorMapInteractionBanner.hidden = !showBanner;
    if (gpsTracking) focusGps();
    else if (!gpsCandidatePanel.contains(document.activeElement)) editorMapInteractionBanner.focus();
    appRoot.classList.add("editor-map-mode");
    editorPanel.inert = true;
    editorPanel.setAttribute("aria-hidden", "true");
  } else if (!suspend && wasSuspended) {
    appRoot.classList.remove("editor-map-mode");
    editorPanel.inert = false;
    editorPanel.removeAttribute("aria-hidden");
    if (gpsTracking) focusGps();
    else if (!(inspectingGpsPoint() && gpsCandidatePanel.contains(document.activeElement))) {
      const requestedTarget = focusTarget || editorMapInteraction?.returnFocusTarget;
      const target = typeof requestedTarget === "function"
        ? requestedTarget()
        : requestedTarget;
      const availableTarget = target && !target.hidden && !target.disabled
        ? target
        : editorCloseTarget();
      availableTarget.focus();
    }
    editorMapInteractionBanner.hidden = !showBanner;
  } else {
    appRoot.classList.toggle("editor-map-mode", suspend);
    editorMapInteractionBanner.hidden = !showBanner;
    editorPanel.inert = suspend;
    if (suspend) {
      editorPanel.setAttribute("aria-hidden", "true");
    } else {
      editorPanel.removeAttribute("aria-hidden");
    }
  }

  editorMapPresentationSuspended = suspend;
  const mapCovered = editorMapInteractionMapCovered(state, mode, suspend);
  mapElement.inert = Boolean(mapCovered);
  return mapCovered;
}

function updateEditorMapInteractionInstruction(instruction) {
  if (!editorMapInteraction) {
    return;
  }
  editorMapInteraction.instruction = instruction;
  editorMapInteractionStatus.textContent = instruction;
}

function setEditorMapInteractionFinishAvailable(available) {
  if (!editorMapInteraction) {
    return;
  }
  editorMapInteraction.finishAvailable = available;
  finishEditorMapInteractionButton.hidden = inspectingGpsPoint() || !available;
}

function setEditorMapInteractionPending(pending) {
  if (!editorMapInteraction) {
    return;
  }
  finishEditorMapInteractionButton.disabled = pending;
  cancelEditorMapInteractionButton.disabled = pending;
}

function captureExistingGeometryEditFormSnapshot() {
  return existingGeometryEditFormControls.map((control) => {
    if (["checkbox", "radio"].includes(control.type)) {
      return { control, checked: control.checked, kind: "checked" };
    }
    if (control.tagName === "SELECT") {
      return {
        control,
        kind: control.multiple ? "multiple" : "value",
        options: Array.from(control.selectedOptions).map((option) => ({
          label: option.textContent,
          value: option.value,
        })),
        value: control.value,
      };
    }
    return { control, kind: "value", value: control.value };
  });
}

function restoreExistingGeometryEditFormSnapshot(snapshot) {
  if (!snapshot) {
    return;
  }
  snapshot.forEach(({ control, checked, kind, options = [], value }) => {
    if (kind === "checked") {
      control.checked = checked;
      return;
    }
    if (control.tagName === "SELECT") {
      options.forEach((selectedOption) => {
        if (!Array.from(control.options).some(
          (option) => option.value === selectedOption.value,
        )) {
          const option = control.ownerDocument.createElement("option");
          option.value = selectedOption.value;
          option.textContent = selectedOption.label;
          control.append(option);
        }
      });
    }
    if (kind === "multiple") {
      const selectedValues = new Set(options.map((option) => option.value));
      Array.from(control.options).forEach((option) => {
        option.selected = selectedValues.has(option.value);
      });
      return;
    }
    control.value = value;
  });
}

function refreshEditorMapInteractionFormSnapshot() {
  if (!editorMapInteraction?.formSnapshot) {
    return;
  }
  editorMapInteraction.formSnapshot = captureExistingGeometryEditFormSnapshot();
}

function restoreEditorMapInteractionFormSnapshot() {
  restoreExistingGeometryEditFormSnapshot(editorMapInteraction?.formSnapshot);
}

function editorMapInteractionBlocksObjectOpen() {
  return Boolean(editorMapInteraction);
}

function beginEditorMapInteraction({
  kind,
  instruction,
  cancel,
  phase = null,
  replaceGpsInspection = false,
  finishAction = null,
  finishLabel = "Terminer",
  formSnapshot = null,
  returnFocusTarget,
  cancelFocusTarget = returnFocusTarget,
  finishAvailable = false,
}) {
  if (editorMapInteraction) {
    if (!(replaceGpsInspection && inspectingGpsPoint())) {
      finishEditorMapInteraction({ restoreFocus: false });
    }
  }
  // Réaffirmer l'ouverture conserve l'éditeur comme entrée la plus récente
  // lors d'une rotation vers le mode single-tool.
  topNavigation.open("editor");
  editorMapInteraction = {
    cancel,
    cancelFocusTarget,
    finishAction,
    finishAvailable,
    formSnapshot,
    instruction,
    kind,
    phase,
    returnFocusTarget,
  };
  editorMapInteractionStatus.textContent = instruction;
  finishEditorMapInteractionButton.textContent = finishLabel;
  finishEditorMapInteractionButton.hidden = phase === "inspection-point" || !finishAvailable;
  finishEditorMapInteractionButton.disabled = false;
  cancelEditorMapInteractionButton.hidden = phase === "inspection-point";
  cancelEditorMapInteractionButton.disabled = false;
  cancelEditorMapInteractionButton.textContent = ["business-gps","gps-troncon","gps-desordre"].includes(kind)
    ? "Annuler" : "Annuler le dessin";
  setEditorMapNavigationLocked(true);
  syncEditorMapInteractionPresentation();
  syncGpsControls();
  gpsTronconTracking?.refresh();
  gpsDesordreTracking?.refresh();
  syncLegacyEditorPresentation();
}

function finishEditorMapInteraction({ restoreFocus = true, focusTarget = null } = {}) {
  if (!editorMapInteraction) {
    return;
  }
  const target = focusTarget || editorMapInteraction.returnFocusTarget;
  editorMapInteraction = null;
  finishEditorMapInteractionButton.disabled = false;
  cancelEditorMapInteractionButton.disabled = false;
  setEditorMapNavigationLocked(false);
  syncEditorMapInteractionPresentation(
    currentTopNavigationState,
    currentTopNavigationLayout,
    { focusTarget: restoreFocus ? target : null },
  );
  cancelGpsCandidate();
  syncGpsControls();
  gpsTronconTracking?.refresh();
  gpsDesordreTracking?.refresh();
  queuePendingMapEntityRefresh();
}

function cancelEditorMapInteraction() {
  entityEditor.setConflict(false);
  if (!editorMapInteraction) {
    return;
  }
  const interaction = editorMapInteraction;
  try {
    interaction.cancel();
  } finally {
    if (editorMapInteraction === interaction) {
      finishEditorMapInteraction({ focusTarget: interaction.cancelFocusTarget });
    }
  }
}

finishEditorMapInteractionButton.addEventListener("click", () => {
  if (typeof editorMapInteraction?.finishAction === "function") {
    editorMapInteraction.finishAction();
    return;
  }
  finishEditorMapInteraction();
});

cancelEditorMapInteractionButton.addEventListener("click", () => {
  cancelEditorMapInteraction();
});

const gpsCandidateMessages = Object.freeze({
  PERMISSION_DENIED: "Accès à la position refusé. Autorisez-le puis réessayez.",
  POSITION_UNAVAILABLE: "Position indisponible. Réessayez lorsque le signal revient.",
  TIMEOUT: "L’acquisition a expiré. Réessayez.",
  UNSUPPORTED: "La géolocalisation est indisponible sur cet appareil.",
  NOT_STANDALONE: "La capture GPS est réservée à la PWA installée.",
  INVALID_POSITION: "La position reçue est invalide. Réessayez.",
  LOW_ACCURACY: "Précision insuffisante pour le seuil réglé. Réessayez.",
  STALE_POSITION: "La position reçue est trop ancienne. Réessayez.",
});
let gpsCandidateOrigin = null;

function isMapPointGpsTarget(target) {
  return target === "map-create-point" || target === "map-edit-point";
}

function gpsTargetAvailable(target) {
  if (!PwaGeolocation.isStandalone() || !currentTopNavigationState.editor
      || editorState.objectType !== "desordre" || desordreEditorForm.hidden
      || generalTab.hidden) return false;
  const geometryType = editorState.geometryType || desordreCreateGeometryType.value;
  const mode = selectedDesordreMode();
  if (target === "point-lonlat") return geometryType === "Point" && mode === "lonlat";
  if (target === "point-xy") return geometryType === "Point" && mode === "xy";
  if (["line-start", "line-end", "line-start-xy", "line-end-xy"].includes(target)) {
    const xy = target.endsWith("-xy");
    return geometryType === "LineString" && mode === (xy ? "xy" : "lonlat")
      && desordreCreateLineCrs.value === (xy ? (businessCRS?.code || "") : "EPSG:4326");
  }
  if (["point-reperage", "line-start-reperage", "line-end-reperage"].includes(target)) {
    return mode === "bornage" && (target === "point-reperage"
      ? geometryType === "Point" : geometryType === "LineString");
  }
  if (target === "map-create-point") {
    return editorState.mode === "create" && geometryType === "Point" && mode === "map"
      && (!editorMapInteraction || inspectingGpsPoint());
  }
  if (target === "map-edit-point") {
    return editorState.mode === "edit" && geometryType === "Point" && mode === "map"
      && (!editorMapInteraction || inspectingGpsPoint());
  }
  return false;
}

function syncGpsControls() {
  gpsPointLonlatButton.hidden = !gpsTargetAvailable("point-lonlat");
  gpsPointXyButton.hidden = !gpsTargetAvailable("point-xy");
  gpsLineStartButton.hidden = !(
    gpsTargetAvailable("line-start") || gpsTargetAvailable("line-start-xy"));
  gpsLineEndButton.hidden = !(
    gpsTargetAvailable("line-end") || gpsTargetAvailable("line-end-xy"));
  gpsBornageStartButton.hidden = !(
    gpsTargetAvailable("point-reperage") || gpsTargetAvailable("line-start-reperage"));
  gpsBornageStartButton.querySelector("span").textContent =
    editorState.geometryType === "LineString" || desordreCreateGeometryType.value === "LineString"
      ? "GPS début" : "Utiliser ma position";
  gpsBornageEndButton.hidden = !gpsTargetAvailable("line-end-reperage");
  gpsMapCreateButton.hidden = inspectingGpsPoint() || !gpsTargetAvailable("map-create-point");
  gpsMapEditButton.hidden = inspectingGpsPoint() || !gpsTargetAvailable("map-edit-point");
}

function presentGpsCandidate(state) {
  if (state.phase === "idle") {
    const hadFocus = gpsCandidatePanel.contains(document.activeElement);
    const origin = gpsCandidateOrigin;
    clearMapCandidate();
    gpsCandidatePanel.hidden = true;
    if (gpsCandidatePanel.parentElement !== gpsCandidateDefaultParent) {
      gpsCandidateDefaultParent.append(gpsCandidatePanel);
    }
    if (inspectingGpsPoint()) finishEditorMapInteraction({ restoreFocus: false });
    if (hadFocus) {
      const target = editorMapInteraction?.phase === "draft"
        && !finishEditorMapInteractionButton.hidden ? finishEditorMapInteractionButton
        : origin && !origin.hidden && !origin.closest("[inert], [hidden]")
          ? origin : !editorPanel.hidden ? editorCloseTarget() : createMenuButton;
      target.focus();
    }
    gpsCandidateOrigin = null;
    return;
  }
  gpsCandidatePanel.hidden = false;
  gpsCandidateStatus.textContent = state.phase === "acquiring"
    ? "Acquisition de la position…"
    : state.phase === "pending" ? "Position GPS acceptée. Calcul en cours…"
    : state.error
      ? gpsCandidateMessages[state.error.code]
        || `${state.error.message || "Calcul indisponible."} Accepter pour relancer le calcul.`
      : isMapPointGpsTarget(state.target)
        ? "Position proposée sur la carte. Acceptez-la pour placer le Point."
        : "Position détectée. Acceptez-la pour l’utiliser dans le formulaire.";
  if (state.error && isMapPointGpsTarget(state.target) && state.candidate) {
    gpsCandidateStatus.textContent += " Position précédente conservée sur la carte.";
  }
  gpsCandidateDetails.hidden = !state.candidate;
  if (state.candidate) {
    gpsCandidateLongitude.textContent = String(state.candidate.longitude);
    gpsCandidateLatitude.textContent = String(state.candidate.latitude);
    gpsCandidateAccuracy.textContent = String(Math.round(state.candidate.accuracy));
  }
  gpsCandidateAcceptButton.hidden = !state.candidate;
  gpsCandidateAcceptButton.disabled = ["acquiring", "pending"].includes(state.phase);
  gpsCandidateRetryButton.disabled = ["acquiring", "pending"].includes(state.phase);
  if (isMapPointGpsTarget(state.target)) {
    if (state.phase !== "ready") clearMapCandidate();
    if (state.phase === "error" && state.candidate && inspectingGpsPoint()) {
      showPointMapCandidate(state.candidate, { center: false });
    }
    if (state.phase === "ready" && gpsTargetAvailable(state.target)) {
      if (gpsCandidatePanel.parentElement !== primaryArea) {
        primaryArea.append(gpsCandidatePanel);
      }
      if (!inspectingGpsPoint()) {
        beginEditorMapInteraction({
          kind: "inspect-desordre-Point",
          phase: "inspection-point",
          instruction: "Position GPS proposée sur la carte. Inspectez-la puis choisissez dans le panneau GPS.",
          cancel: cancelGpsCandidate,
          returnFocusTarget: gpsCandidateOrigin,
        });
      }
      showPointMapCandidate(state.candidate);
      gpsCandidateAcceptButton.focus();
    }
  }
  if (state.phase === "acquiring") gpsCandidatePanel.focus();
}

function updateGpsCoordinatePair(longitudeInput, latitudeInput, candidate, precision = null) {
  const longitude = precision === null
    ? String(candidate.longitude) : coordinate(candidate.longitude, precision);
  const latitude = precision === null
    ? String(candidate.latitude) : coordinate(candidate.latitude, precision);
  longitudeInput.value = longitude;
  latitudeInput.value = latitude;
  for (const input of [longitudeInput, latitudeInput]) {
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

async function applyGpsCalculatedCandidate(target, candidate, isCurrent) {
  if (!navigator.onLine) throw new Error("Calcul indisponible hors connexion. Réessayez avec le réseau.");
  const geometryType = editorState.geometryType || desordreCreateGeometryType.value;
  const objectId = editorState.objectId;
  const xy = target === "point-xy" || target.endsWith("-xy");
  const line = geometryType === "LineString";
  const end = target.startsWith("line-end");
  const pair = xy ? target === "point-xy"
    ? [disorderFields.x, disorderFields.y]
    : end ? [lineEnd1, lineEnd2] : [lineStart1, lineStart2]
    : end ? [lineBorneEnd, lineDistanceEnd, lineSenseEnd]
      : [lineBorneStart, lineDistanceStart, lineSenseStart];
  const previous = pair.map(input => input.value);
  let url;
  let contextId = null;
  if (xy) {
    url = `/api/coordinates/transform?longitude=${encodeURIComponent(candidate.longitude)}`
      + `&latitude=${encodeURIComponent(candidate.latitude)}`;
  } else {
    const ids = selectedDesordreTronconIds();
    if (ids.length !== 1) throw new Error("Le bornage GPS exige exactement un tronçon associé.");
    contextId = ids[0];
    const reperage = editorState.mode === "create"
      ? await loadTronconReperageOptions(contextId)
      : line ? lastServerFeature?.properties?.reperage : currentReperage;
    if (!isCurrent()) return;
    if (!reperage?.systeme_reperage_id || !reperage?.bornes?.length
        || editorState.mode === "edit" && !reperage.disponible) {
      throw new Error("Références de bornage indisponibles pour le tronçon associé.");
    }
    url = `/api/troncons/${encodeURIComponent(contextId)}/position-reperage`
      + `?systeme_reperage_id=${encodeURIComponent(reperage.systeme_reperage_id)}`
      + `&longitude=${encodeURIComponent(candidate.longitude)}`
      + `&latitude=${encodeURIComponent(candidate.latitude)}`;
  }
  let result;
  try {
    result = await fetchJson(url);
  } catch (error) {
    throw new Error(navigator.onLine ? `Calcul GPS impossible : ${error.message}`
      : "Calcul indisponible hors connexion. Réessayez avec le réseau.");
  }
  if (!isCurrent() || !gpsTargetAvailable(target) || editorState.objectId !== objectId
      || contextId && selectedDesordreTronconIds().join() !== contextId) return;
  if (previous.some((value, index) => pair[index].value !== value)) {
    throw new Error("La saisie a changé pendant le calcul. Les valeurs GPS ne sont pas appliquées.");
  }
  if (xy) {
    if (!Number.isFinite(result?.x) || !Number.isFinite(result?.y)) {
      throw new Error("Transformation vers le CRS métier invalide.");
    }
    // Les deux champs reçoivent la même réponse avant tout événement de formulaire.
    updateGpsCoordinatePair(pair[0], pair[1],
      { longitude: result.x, latitude: result.y }, target === "point-xy" ? 2 : null);
  } else {
    const borne = result?.borne_id;
    const distance = result?.distance_borne_m;
    const sense = result?.position_relative;
    if (!borne || !Array.from(pair[0].options).some(option => option.value === borne)
        || !Number.isFinite(distance) || distance < 0
        || !Number.isFinite(result?.distance_axe_m) || result.distance_axe_m < 0
        || !["AVANT_BORNE", "SUR_BORNE", "APRES_BORNE"].includes(sense)
        || sense === "SUR_BORNE" && distance !== 0
        || result.point_projete?.type !== "Point"
        || result.point_projete?.coordinates?.length !== 2
        || !result.point_projete.coordinates.every(Number.isFinite)) {
      throw new Error("Résultat de projection invalide ou borne indisponible dans le formulaire.");
    }
    pair[0].value = borne;
    const displayedDistance = coordinate(distance, 2);
    EntityFormView.setMetreDistance(pair[1], displayedDistance);
    pair[2].value = Number(displayedDistance) === 0 ? "SUR_BORNE" : sense;
    const notice = end ? gpsBornageEndDistance : gpsBornageStartDistance;
    notice.textContent = `Position projetée sur le tronçon. Écart avec la position GPS : ${coordinate(result.distance_axe_m, 1)} m. Vérifiez cet écart avant d’enregistrer.`;
    notice.hidden = false;
  }
}

function applyGpsCandidate(target, candidate, isCurrent) {
  if (!gpsTargetAvailable(target)) return;
  if (["point-xy", "line-start-xy", "line-end-xy", "point-reperage",
    "line-start-reperage", "line-end-reperage"].includes(target)) {
    return applyGpsCalculatedCandidate(target, candidate, isCurrent);
  }
  if (target === "point-lonlat") {
    updateGpsCoordinatePair(desordreCreateLongitude, desordreCreateLatitude, candidate, 6);
  } else if (target === "line-start") {
    updateGpsCoordinatePair(desordreCreateLineStart1, desordreCreateLineStart2, candidate);
  } else if (target === "line-end") {
    updateGpsCoordinatePair(desordreCreateLineEnd1, desordreCreateLineEnd2, candidate);
  } else if (target === "map-create-point") {
    if (!inspectingGpsPoint() || provisionalDesordreLayer) {
      throw new Error("Annulez d’abord le dessin cartographique en cours.");
    }
    provisionalDesordreLayer = layerFromDesordreGeometry({
      type: "Point", coordinates: [candidate.longitude, candidate.latitude],
    }).addTo(map);
    provisionalDesordreLayer.enableEdit(map);
    startDesordreDrawButton.hidden = true;
    updateDesordreDraftStatus("Point GPS provisoire — déplacez-le si nécessaire.");
    beginEditorMapInteraction({
      kind: "create-desordre-Point",
      phase: "draft",
      replaceGpsInspection: true,
      instruction: "Point GPS placé. Déplacez-le si nécessaire, puis cliquez sur Terminer.",
      cancel: cancelDesordreCreationDrawing,
      returnFocusTarget: submitDesordreCreateButton,
      cancelFocusTarget: startDesordreDrawButton,
      finishAvailable: true,
    });
  } else if (target === "map-edit-point") {
    if (!inspectingGpsPoint() || graphicEditActive || !activePointLayer?.dragging) {
      throw new Error("Annulez d’abord l’édition cartographique en cours.");
    }
    beginPointPositionEdit({ fromGpsInspection: true });
    setProvisionalPointPosition(L.latLng(candidate.latitude, candidate.longitude));
  }
  syncGpsControls();
}

const gpsCandidateController = PwaPositionCandidate.createController({
  geolocation: PwaGeolocation,
  settings: PwaMapSettings,
  onChange: presentGpsCandidate,
  onAccept: applyGpsCandidate,
});

function acceptGpsTronconGeometry(geometry) {
  if (editorState.mode !== "create" || editorState.objectType !== "troncon" || geometry.type !== "LineString") return false;
  const candidate = L.polyline(geometry.coordinates.map(([lng, lat]) => [lat, lng]),
    { color: "#1769aa", dashArray: "7 5", weight: 6 }).addTo(map);
  clearTronconDraft();
  provisionalTronconLayer = candidate;
  updateTronconDraftStatus();
  heritageCreateMessage.classList.remove("error");
  return true;
}
function acceptGpsDesordreGeometry(geometry) {
  if (editorState.objectType !== "desordre" || selectedDesordreMode() !== "map") return false;
  if (editorState.mode === "create") {
    const candidate = layerFromDesordreGeometry(geometry).addTo(map);
    clearDesordreDraft();
    provisionalDesordreLayer = candidate;
    updateDesordreDraftStatus();
  } else {
    if (geometry.type !== editorState.geometryType) return false;
    stageDisorderGeometry(geometry);
  }
  desordreCreateMessage.classList.remove("error");
  return true;
}

gpsTronconTracking = PwaGeometryTracking.createWidget({
  root: window, map, L, container: MapFormViews.heritage.geometry.methods,
  ownerId: tronconCreateGeometry.id,
  geometryType: () => "LineString", getOriginal: () => null,
  getDraft: () => provisionalTronconLayer?.toGeoJSON(false).geometry || null,
  restoreDraft: acceptGpsTronconGeometry,
  draftContext: () => editorState.mode === "create" && editorState.objectType === "troncon"
    ? { resource: "troncons", mode: "create", id: null, version: null } : null,
  canStart: () => editorState.mode === "create" && editorState.objectType === "troncon"
    && !creationRequestInFlight && (!editorMapInteraction || editorMapInteraction.kind === "gps-troncon")
    && (!window.BusinessRuntime || BusinessRuntime.can("business.create")),
  onState: (state) => {
    const active = state.phase !== "idle";
    if (active && !editorMapInteraction) {
      beginEditorMapInteraction({ kind: "gps-troncon", instruction: "Relevé GPS en cours.",
        cancel: () => gpsTronconTracking?.cancel(), returnFocusTarget: editorCloseTarget });
    } else if (!active && editorMapInteraction?.kind === "gps-troncon") {
      finishEditorMapInteraction();
    }
    startTronconDrawButton.disabled = active || creationRequestInFlight;
    submitCreateButton.disabled = active || creationRequestInFlight;
  },
  onConfirm: acceptGpsTronconGeometry,
});

gpsDesordreTracking = PwaGeometryTracking.createWidget({
  root: window, map, L, container: MapFormViews.disorder.geometry.methods,
  ownerId: desordreCreateGeometry.id,
  geometryType: () => editorState.mode === "edit"
    ? editorState.geometryType : desordreCreateGeometryType.value,
  getOriginal: () => editorState.mode === "edit" ? lastServerFeature?.geometry : null,
  getDraft: () => editorState.mode === "create" ? provisionalDesordreLayer?.toGeoJSON(false).geometry || null : currentDisorderGeometry(),
  restoreDraft: acceptGpsDesordreGeometry,
  draftContext: () => editorState.objectType === "desordre" && selectedDesordreMode() === "map"
    ? { resource: "desordres", mode: editorState.mode, id: editorState.objectId,
      version: editorState.mode === "edit" ? lastServerFeature?.properties?.version : null }
    : null,
  canStart: () => editorState.objectType === "desordre" && !desordreEditorForm.hidden
    && selectedDesordreMode() === "map" && (!editorMapInteraction || editorMapInteraction.kind === "gps-desordre")
    && !editorState.conflict && !editorState.forbidden
    && (!window.BusinessRuntime || BusinessRuntime.can(editorState.mode === "edit" ? "business.update" : "business.create"))
    && !creationRequestInFlight && !lineRequestInFlight && !graphicRequestInFlight,
  onState: (state) => {
    if (state.phase !== "idle" && !gpsDesordreContext) {
      gpsDesordreContext = { mode: editorState.mode, id: editorState.objectId,
        type: editorState.mode === "edit" ? editorState.geometryType : desordreCreateGeometryType.value,
        version: lastServerFeature?.properties?.version };
    }
    if (state.phase === "idle") gpsDesordreContext = null;
    const active = state.phase !== "idle";
    if (active && !editorMapInteraction) {
      beginEditorMapInteraction({ kind: "gps-desordre", instruction: "Relevé GPS en cours.",
        cancel: () => gpsDesordreTracking?.cancel(), returnFocusTarget: editorCloseTarget });
    } else if (!active && editorMapInteraction?.kind === "gps-desordre") {
      finishEditorMapInteraction();
    }
    if (active && !gpsModeControls) {
      gpsModeControls = Array.from(desordreEditorForm.querySelectorAll('[name="desordre-mode"]'))
        .map((input) => ({ input, disabled: input.disabled }));
    }
    if (active) gpsModeControls?.forEach(({ input }) => { input.disabled = true; });
    else if (gpsModeControls) {
      gpsModeControls.forEach(({ input, disabled }) => { input.disabled = disabled; });
      gpsModeControls = null;
    }
    startDesordreDrawButton.disabled = active || creationRequestInFlight;
    startLineEditButton.disabled = active || lineRequestInFlight;
    submitDesordreCreateButton.disabled = active || creationRequestInFlight;
    desordreCreateGeometryType.disabled = active || editorState.mode === "edit";
  },
  onConfirm: (geometry) => {
    const context = gpsDesordreContext;
    if (!context || editorState.objectType !== "desordre" || editorState.mode !== context.mode
        || selectedDesordreMode() !== "map"
        || editorState.objectId !== context.id || geometry.type !== context.type
        || context.mode === "edit" && lastServerFeature?.properties?.version !== context.version) {
      throw new Error("L'objet ou sa version a changé. Annulez ce relevé.");
    }
    return acceptGpsDesordreGeometry(geometry);
  },
});

function cancelGpsCandidate() {
  if (gpsCandidateController.getState().phase !== "idle") gpsCandidateController.cancel();
}

function startGpsCandidate(target, origin) {
  if (isMapPointGpsTarget(target) && (provisionalDesordreLayer || graphicEditActive
      || editorMapInteraction && !inspectingGpsPoint())) {
    desordreCreateMessage.textContent =
      "Annulez explicitement le dessin ou l’édition cartographique avant d’utiliser le GPS.";
    desordreCreateMessage.classList.add("error");
    return;
  }
  if (!gpsTargetAvailable(target)) return;
  gpsCandidateOrigin = origin;
  gpsCandidateController.start(target);
}

gpsPointLonlatButton.addEventListener("click", () => startGpsCandidate("point-lonlat", gpsPointLonlatButton));
gpsPointXyButton.addEventListener("click", () => startGpsCandidate("point-xy", gpsPointXyButton));
gpsLineStartButton.addEventListener("click", () => startGpsCandidate(
  selectedDesordreMode() === "xy" ? "line-start-xy" : "line-start", gpsLineStartButton));
gpsLineEndButton.addEventListener("click", () => startGpsCandidate(
  selectedDesordreMode() === "xy" ? "line-end-xy" : "line-end", gpsLineEndButton));
gpsBornageStartButton.addEventListener("click", () => startGpsCandidate(
  (editorState.geometryType || desordreCreateGeometryType.value) === "Point"
    ? "point-reperage" : "line-start-reperage", gpsBornageStartButton));
gpsBornageEndButton.addEventListener("click", () => startGpsCandidate("line-end-reperage", gpsBornageEndButton));
gpsMapCreateButton.addEventListener("click", () => startGpsCandidate("map-create-point", gpsMapCreateButton));
gpsMapEditButton.addEventListener("click", () => startGpsCandidate("map-edit-point", gpsMapEditButton));
gpsCandidateAcceptButton.addEventListener("click", () => gpsCandidateController.accept());
gpsCandidateRetryButton.addEventListener("click", () => gpsCandidateController.retry());
gpsCandidateCancelButton.addEventListener("click", cancelGpsCandidate);
window.addEventListener("pagehide", cancelGpsCandidate);
document.addEventListener("digues-app:header-capabilities", () => {
  if (!PwaGeolocation.isStandalone()) cancelGpsCandidate();
  syncGpsControls();
});


function setCreateMenuOpen(open) {
  createMenuList.hidden = !open;
  createMenuButton.setAttribute("aria-expanded", String(open));
  positionCompactLandscapeMenu(createMenuButton, createMenuList);
}

function positionCompactLandscapeMenu(button, menu) {
  const compactPortrait = window.matchMedia(
    "(orientation: portrait) and (max-width: 1024px)",
  ).matches;
  if (menu.hidden) {
    menu.style.removeProperty("--compact-menu-left");
    menu.style.removeProperty("--compact-menu-top");
    return;
  }
  const headerBottom = Math.max(0, appHeader.getBoundingClientRect().bottom);
  menu.style.setProperty("--compact-menu-top", `${headerBottom}px`);
  if (compactPortrait) {
    menu.style.removeProperty("--compact-menu-left");
    return;
  }
  const anchorRect = button.getBoundingClientRect();
  const menuRect = menu.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const position = TopNavigation.fixedMenuPosition(
    anchorRect,
    menuRect.width,
    viewportWidth,
  );
  menu.style.setProperty("--compact-menu-left", `${position.left}px`);
}

function syncCompactLandscapeMenus() {
  positionCompactLandscapeMenu(createMenuButton, createMenuList);
  positionCompactLandscapeMenu(toolsMenuButton, toolsMenuList);
}

function clearTronconDraft() {
  legacyDrawingAcquisition?.stop(); legacyDrawingAcquisition=null;
  creationGeometryBeforeDrawing=null;
  if(provisionalTronconLayer) {
    provisionalTronconLayer.disableEdit?.();map.removeLayer(provisionalTronconLayer);provisionalTronconLayer=null;
  }
  map.editTools?.stopDrawing?.();cancelledTronconGeometry=null;
  tronconDrawStatus.textContent="";tronconDrawStatus.hidden=true;
  tronconDrawActions.hidden=true;startTronconDrawButton.hidden=false;
}

function updateTronconDraftStatus() {
  const drawing=Boolean(legacyDrawingAcquisition && provisionalTronconLayer?.editor?.drawing());
  const count=provisionalTronconLayer?.getLatLngs().length || 0;
  const controls=MapFormViews.heritage.geometry;
  EntityGeometryDraft.syncDrawingControls(controls,{drawing,type:"LineString",count,geometry:provisionalTronconLayer});
  tronconDrawActions.hidden=!drawing;
  tronconDrawStatus.textContent=drawing ? `${count} sommet(s) — 2 minimum.` : "";
  tronconDrawStatus.hidden=!drawing;
  if(drawing) setEditorMapInteractionFinishAvailable(count>=2);
}

function setTronconDraft(geometry) {
  const latLngs = geometry.coordinates.map(
    ([longitude, latitude]) => [latitude, longitude],
  );
  provisionalTronconLayer = L.polyline(latLngs, {
    color: "#1769aa",
    dashArray: "7 5",
    opacity: 1,
    weight: 6,
  }).addTo(map);
  provisionalTronconLayer.enableEdit(map);
  cancelledTronconGeometry = null;
  startTronconDrawButton.hidden = true;
}

function restoreTronconDraft() {
  if (cancelledTronconGeometry?.type !== "LineString") {
    return;
  }
  setTronconDraft(cancelledTronconGeometry);
  updateTronconDraftStatus("Dessin restauré localement — aucun enregistrement en base.");
  tronconDrawStatus.hidden=false;
  beginEditorMapInteraction({
    kind: "create-troncon",
    instruction: "Ajustez les sommets si nécessaire, puis cliquez sur Terminer.",
    cancel: cancelTronconCreationDrawing,
    returnFocusTarget: submitCreateButton,
    cancelFocusTarget: startTronconDrawButton,
    finishAvailable: true,
  });
}

function fillHeritageParentOptions(objectType, selectedId = null) {
  heritageParent.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = objectType === "digue"
    ? "Choisir un système d’endiguement"
    : "Choisir une digue";
  heritageParent.append(empty);

  const parents = objectType === "digue"
    ? heritageData.systemes
    : heritageData.systemes.flatMap((systeme) => systeme.digues);
  parents.filter((parent) => parent.valid).forEach((parent) => {
    const option = document.createElement("option");
    option.value = parent.id;
    option.textContent = businessLabel(parent);
    heritageParent.append(option);
  });
  heritageParent.value = selectedId || "";
}

function creationContextParent(objectType) {
  if (
    objectType === "digue"
    && selectedHeritageObject?.kind === "Système d'endiguement"
  ) {
    return selectedHeritageObject.item.id;
  }
  if (objectType === "troncon" && selectedHeritageObject?.kind === "Digue") {
    return selectedHeritageObject.item.id;
  }
  return null;
}

async function loadDesordreTypes() {
  if (desordreTypes.length > 0) {
    return desordreTypes;
  }
  if (!desordreTypesLoadingPromise) {
    desordreTypesLoadingPromise = ReferenceData.load("typesDesordre", fetchJson)
      .then((data) => {
        desordreTypes = data;
        return desordreTypes;
      })
      .finally(() => {
        desordreTypesLoadingPromise = null;
      });
  }
  return desordreTypesLoadingPromise;
}

async function loadDesordreCategories() {
  if (desordreCategories.length > 0) {
    return desordreCategories;
  }
  if (!desordreCategoriesLoadingPromise) {
    desordreCategoriesLoadingPromise = ReferenceData.load(
      "categoriesDesordre", fetchJson,
    ).then((data) => {
      desordreCategories = data;
      return desordreCategories;
    }).finally(() => {
      desordreCategoriesLoadingPromise = null;
    });
  }
  return desordreCategoriesLoadingPromise;
}

async function loadBusinessCRS() {
  if (businessCRS) return businessCRS;
  if (!businessCRSLoading) businessCRSLoading = (async () => {
    const summary = await fetchJson("/api/base-summary");
    const crs = LocalProjection.validateBusinessCRS(summary?.crs);
    businessCRS = crs;
    desordreCreateLineCrs.replaceChildren(
      new Option(`X/Y ${crs.code}`, crs.code, true, true),
      new Option("Longitude/latitude EPSG:4326", "EPSG:4326"),
    );
    for (const [input, axis] of [[desordreCreateX, "X"], [desordreCreateY, "Y"],
      [polygonRepresentativeX, "X"], [polygonRepresentativeY, "Y"]]) {
      input.closest("label").firstChild.textContent = `${axis} (${crs.code}) `;
    }
    return crs;
  })().finally(() => { businessCRSLoading = null; });
  return businessCRSLoading;
}

async function loadDesordreAuxiliaryReferences(selected = {}) {
  await loadBusinessCRS();
  [desordreCotes, desordrePositions, desordreSources] = await Promise.all([
    ReferenceData.load("cotes", fetchJson),
    ReferenceData.load("positions", fetchJson),
    ReferenceData.load("sources", fetchJson),
  ]);
  fillDesordreAuxiliaryReferences(selected);
}

function fillDesordreAuxiliaryReferences(selected = {}) {
  ReferenceData.fillSelect(
    desordreCreateCote, desordreCotes, selected.cote_id, "Sans côté",
    selected.cote_libelle,
  );
  ReferenceData.fillSelect(
    desordreCreatePosition, desordrePositions, selected.position_id, "Sans position",
    selected.position_libelle,
  );
  ReferenceData.fillSelect(
    desordreCreateSource, desordreSources, selected.source_id, "Sans source",
    selected.source_libelle,
  );
}

async function loadTronconCreationReferences() {
  const [rives, types, typologies] = await Promise.all([
    ReferenceData.load("rives", fetchJson),
    ReferenceData.load("typesTroncon", fetchJson),
    ReferenceData.load("typologiesTroncon", fetchJson),
  ]);
  ReferenceData.fillSelect(heritageObjectTypeRive, rives, null, "Sans rive");
  ReferenceData.fillSelect(heritageObjectTypeTroncon, types, null, "Sans type");
  ReferenceData.fillSelect(
    heritageObjectTypologieTroncon, typologies, null, "Sans typologie",
  );
}

async function loadDesordreTronconOptions() {
  if (desordreTronconOptionsLoaded) {
    return desordreTronconOptions;
  }
  if (!desordreTronconsLoadingPromise) {
    const version = desordreTronconOptionsVersion;
    const request = fetchJson("/api/troncons/options")
      .then((data) => {
        const options = Array.isArray(data.troncons)
          ? data.troncons.filter((item) => item.valid)
          : [];
        if (version === desordreTronconOptionsVersion) {
          desordreTronconOptions = options;
          desordreTronconOptionsLoaded = true;
          return desordreTronconOptions;
        }
        return null;
      })
      .finally(() => {
        if (desordreTronconsLoadingPromise === request) {
          desordreTronconsLoadingPromise = null;
        }
      });
    desordreTronconsLoadingPromise = request;
  }
  const options = await desordreTronconsLoadingPromise;
  return options === null ? loadDesordreTronconOptions() : options;
}

function invalidateDesordreTronconOptions() {
  desordreTronconOptionsVersion += 1;
  desordreTronconOptions = [];
  desordreTronconOptionsLoaded = false;
}

function invalidateReperageOptions(mutation) {
  if (mutation.resource !== "troncons" || mutation.ids === null) {
    reperageOptionsGlobalVersion += 1;
    reperageOptionsByTroncon.clear();
    return;
  }
  mutation.ids.forEach((value) => {
    const id = String(value);
    reperageOptionsVersionByTroncon.set(
      id,
      (reperageOptionsVersionByTroncon.get(id) || 0) + 1,
    );
    reperageOptionsByTroncon.delete(id);
  });
}

function invalidateMapRelationOptions(mutation) {
  invalidateDesordreTronconOptions();
  invalidateReperageOptions(mutation);
}

async function loadTronconReperageOptions(tronconId) {
  const id = String(tronconId);
  if (reperageOptionsByTroncon.has(id)) {
    return reperageOptionsByTroncon.get(id);
  }
  if (!reperageLoadingByTroncon.has(id)) {
    const globalVersion = reperageOptionsGlobalVersion;
    const entityVersion = reperageOptionsVersionByTroncon.get(id) || 0;
    const request = fetchJson(
      `/api/troncons/${encodeURIComponent(id)}/reperage-options`,
    ).then((options) => {
      if (globalVersion !== reperageOptionsGlobalVersion
          || entityVersion !== (reperageOptionsVersionByTroncon.get(id) || 0)) {
        return null;
      }
      reperageOptionsByTroncon.set(id, options);
      return options;
    }).finally(() => {
      if (reperageLoadingByTroncon.get(id) === request) {
        reperageLoadingByTroncon.delete(id);
      }
    });
    reperageLoadingByTroncon.set(id, request);
  }
  const options = await reperageLoadingByTroncon.get(id);
  return options === null ? loadTronconReperageOptions(id) : options;
}

function fillBorneSelect(select, bornes, selectedId = null) {
  select.replaceChildren();
  bornes.forEach((borne) => {
    const option = document.createElement("option");
    option.value = borne.id;
    option.textContent = borne.libelle_affichage || businessLabel(borne, "Borne");
    select.append(option);
  });
  select.value = selectedId || bornes[0]?.id || "";
}

function fillTypeSelect(
  select,
  selectedId = null,
  categoryId = null,
  { preserveOutsideCategory = false, currentLabel = null } = {},
) {
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Sans type";
  select.append(empty);
  const filtered = desordreTypes.filter(
    (item) => !categoryId || String(item.categorie_id) === String(categoryId),
  );
  filtered.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = ReferenceData.optionLabel(item);
    select.append(option);
  });
  const selectedType = desordreTypes.find(
    (item) => String(item.id) === String(selectedId),
  );
  if (
    selectedId
    && preserveOutsideCategory
    && !filtered.some((item) => String(item.id) === String(selectedId))
  ) {
    const current = document.createElement("option");
    current.value = selectedId;
    current.textContent = `${ReferenceData.optionLabel(selectedType) || currentLabel || "Type actuel"} `
      + "(actuel — hors catalogue/catégorie)";
    select.append(current);
  }
  select.value = selectedId || "";
}

function fillCategorieSelect(select, selectedId = null, currentLabel = null) {
  ReferenceData.fillSelect(
    select, desordreCategories, selectedId, "Sans catégorie", currentLabel,
  );
}

function fillDesordreReferenceOptions(
  typeId = null,
  categoryId = null,
  currentTypeLabel = null,
  currentCategoryLabel = null,
) {
  fillCategorieSelect(
    desordreCreateCategorieReference, categoryId, currentCategoryLabel,
  );
  fillTypeSelect(
    desordreCreateTypeReference,
    typeId,
    categoryId,
    { preserveOutsideCategory: true, currentLabel: currentTypeLabel },
  );
}

desordreCreateCategorieReference.addEventListener("change", () => {
  const selectedType = desordreCreateTypeReference.value;
  fillTypeSelect(
    desordreCreateTypeReference,
    selectedType,
    desordreCreateCategorieReference.value,
  );
});

desordreCreateTypeReference.addEventListener("change", () => {
  const selectedType = desordreTypes.find(
    (item) => String(item.id) === desordreCreateTypeReference.value,
  );
  if (selectedType?.categorie_id) {
    desordreCreateCategorieReference.value = selectedType.categorie_id;
  }
});

function filteredDesordreTronconOptions(
  selectedIds = [],
  { includeSelectedOutsideFilter = false } = {},
) {
  return RelationSelector.choices(desordreTronconOptions, selectedIds, {
    include: (troncon) => !hiddenTronconIds.has(String(troncon.id)),
    preserveSelectedOutsideFilter: includeSelectedOutsideFilter,
  }).map((choice) => choice.row);
}

function fillTronconSelect(
  select,
  selectedIds = [],
  { multiple = true, includeSelectedOutsideFilter = false } = {},
) {
  RelationSelector.fill(select, desordreTronconOptions, selectedIds, {
    multiple,
    emptyLabel: "Aucun tronçon",
    include: (troncon) => !hiddenTronconIds.has(String(troncon.id)),
    preserveSelectedOutsideFilter: includeSelectedOutsideFilter,
    label: (troncon) => `${businessLabel(troncon, "Tronçon sans libellé")} — ${troncon.digue_libelle || "Digue sans libellé"}`,
    outsideLabel: (label) => `${label} (rattachement actuel hors sélection)`,
  });
}

function fillDesordreTronconOptions() {
  const selectedId = selectedHeritageObject?.kind === "Tronçon"
    ? String(selectedHeritageObject.item.id)
    : null;
  const point = desordreCreateGeometryType.value === "Point";
  desordreCreateTroncons.multiple = !point;
  desordreCreateTroncons.size = point ? 1 : 5;
  fillTronconSelect(
    desordreCreateTroncons,
    selectedId ? [selectedId] : [],
    { multiple: !point },
  );
}

function selectedDesordreMode() {
  return desordreEditorForm.elements["desordre-mode"].value || "map";
}

function creationBornageChoiceState(geometryType, tronconCount, reperageAvailable) {
  const visible = geometryType !== "Polygon" && tronconCount === 1;
  return { visible, enabled: visible && reperageAvailable };
}

function availableDisorderModes(geometryType, tronconCount, reperageAvailable) {
  if (geometryType === "Polygon") return ["map"];
  const modes = ["map", "xy", "lonlat"];
  if (tronconCount === 1 && reperageAvailable) modes.push("bornage");
  return modes;
}

function canonicalDisorderBornageAvailable() {
  const persisted = lastServerFeature?.properties?.troncon_ids || [];
  const selected = selectedDesordreTronconIds();
  return persisted.length === 1 && selected.length === 1 && selected[0] === String(persisted[0])
    && Boolean(lastServerFeature.properties.reperage?.disponible);
}

function renderDisorderModeChoices(reperageAvailable = false) {
  const geometryType = editorState.geometryType || desordreCreateGeometryType.value;
  const compatible = editorState.mode === "edit" ? canonicalDisorderBornageAvailable() : reperageAvailable;
  const modes = availableDisorderModes(geometryType, selectedDesordreTronconIds().length, compatible);
  const changed = MapFormViews.disorder.geometry.setModeAvailability({
    xy: modes.includes("xy"), lonlat: modes.includes("lonlat"),
    bornage: creationBornageChoiceState(geometryType, selectedDesordreTronconIds().length, compatible),
  });
  if (changed) {
    if (geometryType === "Point" && editorState.mode === "edit") updateCoordinateInputs();
    else updateDisorderEditorControls();
  }
  return modes;
}

function updateLineCoordinateLabels(container, crs) {
  const labels = EntityGeometryDraft.coordinateLabels(crs);
  container.querySelectorAll(".line-axis-1").forEach((item) => {
    item.textContent = labels[0];
  });
  container.querySelectorAll(".line-axis-2").forEach((item) => {
    item.textContent = labels[1];
  });
}

function creationBornageDraftModified() {
  return [desordreCreateBorneStart, desordreCreateBorneEnd,
    desordreCreateDistanceStart, desordreCreateDistanceEnd].some(control => control.value !== "");
}

function desordreDraftVertexCount() {
  if (!provisionalDesordreLayer) {
    return 0;
  }
  const latLngs = provisionalDesordreLayer.getLatLngs?.();
  if (!Array.isArray(latLngs)) {
    return 1;
  }
  if (latLngs.length && Array.isArray(latLngs[0])) {
    return latLngs[0].length;
  }
  return latLngs.length;
}

function clearDesordreDraft() {
  legacyDrawingAcquisition?.stop(); legacyDrawingAcquisition=null;
  creationGeometryBeforeDrawing=null;
  if(provisionalDesordreLayer) {
    provisionalDesordreLayer.disableEdit?.();map.removeLayer(provisionalDesordreLayer);provisionalDesordreLayer=null;
  }
  map.editTools?.stopDrawing?.();cancelledDesordreGeometry=null;
  desordreDrawStatus.textContent="";desordreDrawStatus.hidden=true;
  desordreDrawActions.hidden=true;startDesordreDrawButton.hidden=false;
  clearResolvedGeometryTypeWarning();
}

function updateDesordreDraftStatus(message = null) {
  const drawing=Boolean(legacyDrawingAcquisition && provisionalDesordreLayer?.editor?.drawing());
  const type=desordreCreateGeometryType.value, count=desordreDraftVertexCount();
  const minimum=type === "Point" ? 1 : type === "LineString" ? 2 : 3;
  EntityGeometryDraft.syncDrawingControls(MapFormViews.disorder.geometry,{drawing,type,count,geometry:provisionalDesordreLayer});
  desordreDrawActions.hidden=!drawing;
  // GPS keeps its existing inspection feedback; manual drawings have no ready echo.
  desordreDrawStatus.textContent=drawing ? `${count} sommet(s) — ${minimum} minimum.` : message?.includes("GPS") ? message : "";
  desordreDrawStatus.hidden=!desordreDrawStatus.textContent;
  if(drawing) setEditorMapInteractionFinishAvailable(type !== "Point" && count>=minimum);
}

function layerFromDesordreGeometry(geometry) {
  if (geometry.type === "Point") {
    return createDesordrePointMarker([
      geometry.coordinates[1], geometry.coordinates[0],
    ]);
  }
  if (geometry.type === "LineString") {
    return L.polyline(
      geometry.coordinates.map(([longitude, latitude]) => [latitude, longitude]),
      { color: "#a44f18", dashArray: "7 5", opacity: 1, weight: 6 },
    );
  }
  const outerRing = geometry.coordinates[0];
  const withoutClosingDuplicate = outerRing.slice(0, -1);
  return L.polygon(
    withoutClosingDuplicate.map(([longitude, latitude]) => [latitude, longitude]),
    { color: "#a44f18", dashArray: "7 5", fillOpacity: 0.18, weight: 4 },
  );
}

function restoreDesordreDraft() {
  if (!cancelledDesordreGeometry) {
    return;
  }
  provisionalDesordreLayer = layerFromDesordreGeometry(
    cancelledDesordreGeometry,
  ).addTo(map);
  provisionalDesordreLayer.enableEdit(map);
  cancelledDesordreGeometry = null;
  startDesordreDrawButton.hidden = true;
  updateDesordreDraftStatus("Dessin restauré localement — aucune écriture en base.");
  desordreDrawStatus.hidden=false;
  beginEditorMapInteraction({
    kind: `create-desordre-${desordreCreateGeometryType.value}`,
    instruction: desordreCreateGeometryType.value === "Point"
      ? "Déplacez le point si nécessaire, puis cliquez sur Terminer."
      : "Ajustez les sommets si nécessaire, puis cliquez sur Terminer.",
    cancel: cancelDesordreCreationDrawing,
    returnFocusTarget: submitDesordreCreateButton,
    cancelFocusTarget: startDesordreDrawButton,
    finishAvailable: true,
  });
}

function updateDisorderEditorControls() {
  const geometryType = editorState.geometryType || desordreCreateGeometryType.value;
  const point = geometryType === "Point";
  const line = geometryType === "LineString";
  const editing = editorState.mode === "edit";
  const method = geometryType === "Polygon" ? "map" : selectedDesordreMode();
  desordreModeSelector.hidden = geometryType === "Polygon";
  if (geometryType === "Polygon") desordreEditorForm.elements["desordre-mode"].value = "map";
  gpsBornageStartDistance.hidden = true;
  gpsBornageEndDistance.hidden = true;
  disorderFields.x.readOnly = method !== "xy";
  disorderFields.y.readOnly = method !== "xy";
  disorderFields.longitude.readOnly = method !== "lonlat";
  disorderFields.latitude.readOnly = method !== "lonlat";
  desordreCreateXy.hidden = !point || method !== "xy";
  desordreCreateLonlat.hidden = !point || method !== "lonlat";
  const lineCoordinateMode = line && ["xy", "lonlat"].includes(method);
  desordreCreateLineCoordinates.hidden = !lineCoordinateMode;
  if (lineCoordinateMode) {
    desordreCreateLineCrs.value = method === "lonlat" ? "EPSG:4326" : businessCRS?.code || "";
    updateLineCoordinateLabels(
      desordreCreateLineCoordinates,
      desordreCreateLineCrs.value,
    );
  }
  desordreCreateBornage.hidden = method !== "bornage";
  desordreCreateBornageEnd.hidden = point;
  desordreCreateGeometry.hidden = method !== "map";
  startDesordreDrawButton.hidden = editing && geometryType !== "Polygon";
  if (editing && geometryType !== "Polygon") {
    desordreDrawStatus.hidden = true;
    desordreDrawActions.hidden = true;
  }
  pointMapEditor.hidden = !editing || !point || method !== "map";
  lineMapEditor.hidden = !editing || !line || method !== "map";
  pointBornageWarning.hidden = !editing || !point || method !== "bornage";
  lineBornageWarning.hidden = !editing || !line || method !== "bornage";
  polygonRepresentativePoint.hidden = !editing || geometryType !== "Polygon";
  desordreCreateGeometry.dataset.geometryType = geometryType;
  syncGpsControls();
  gpsDesordreTracking?.refresh();
  syncLegacyEditorPresentation();
}

async function refreshCreationReperageAvailability() {
  const requestVersion = ++creationReperageRequestVersion;
  const editorGeneration = legacyEditorGeneration;
  const current = () => requestVersion === creationReperageRequestVersion
    && editorGeneration === legacyEditorGeneration && editorState.mode === "create";
  creationReperageAvailable = false;
  const ids = selectedDesordreTronconIds();
  const geometryType = desordreCreateGeometryType.value;
  const eligible = availableDisorderModes(
    geometryType, ids.length, true,
  ).includes("bornage");
  renderDisorderModeChoices(false);
  if (!eligible) {
    return;
  }
  try {
    const options = await loadTronconReperageOptions(ids[0]);
    if (!current()
        || desordreCreateGeometryType.value !== geometryType
        || selectedDesordreTronconIds().length !== 1
        || selectedDesordreTronconIds()[0] !== ids[0]) {
      return;
    }
    if (!options.systeme_reperage_id || options.bornes.length === 0) {
      return;
    }
    creationReperageAvailable = true;
    renderDisorderModeChoices(true);
    fillBorneSelect(desordreCreateBorneStart, options.bornes);
    fillBorneSelect(desordreCreateBorneEnd, options.bornes);
  } catch (error) {
    if (current()) {
      creationReperageAvailable = false;
      renderDisorderModeChoices(false);
      MapFormViews.disorder.geometry.diagnostics.textContent =
        "Le repérage n’est pas disponible pour le tronçon sélectionné.";
    }
  }
}

async function openDesordreCreation() {
  if (!leaveBusinessEditor()) return;
  const generation = ++legacyEditorGeneration;
  legacyEditorLoading = false;
  if (gpsDesordreTracking?.isActive()) return;
  cancelGpsCandidate();
  await Promise.all([
    loadHeritageTree(), loadDesordreTypes(), loadDesordreCategories(),
    loadDesordreTronconOptions(), loadDesordreAuxiliaryReferences(),
  ]);
  if (generation !== legacyEditorGeneration) return;
  clearTronconDraft();
  clearDesordreDraft();
  disorderRelatedObjectsController.clear();
  setDisorderEditorState("create", "Point");
  lastServerFeature = null;
  requestedDesordreId = null;


  editorTabs.hidden = false;
  showEditorTab("general");
  generalTab.hidden = false;
  observationsTab.hidden = true;
  heritageObjectForm.hidden = true;
  desordreEditorForm.reset();
  creationReperageAvailable = false;
  MapFormViews.disorder.geometry.diagnostics.textContent = "";
  updateLineCoordinateLabels(desordreCreateLineCoordinates, desordreCreateLineCrs.value);
  desordreEditorForm.hidden = false;
  desordreCreateIdField.hidden = true;
  desordreCreateValid.checked = true;
  desordreCreateGeometryType.disabled = false;
  desordreCreateId.disabled = false;
  submitDesordreCreateButton.textContent = "Créer";
  cancelDesordreCreateButton.textContent = "Annuler";
  validateDesordreDrawButton.hidden = true;
  polygonRepresentativePoint.hidden = true;
  desordreCreateActions.hidden = false;
  Array.from(desordreEditorForm.elements).forEach((element) => {
    element.disabled = false;
  });
  fillDesordreReferenceOptions();
  fillDesordreTronconOptions();
  lastAcceptedCreationTronconIds = selectedDesordreTronconIds();
  previousDesordreGeometryType = "Point";
  desordreCreateMessage.textContent = "";
  desordreCreateMessage.classList.remove("error");
  updateDisorderEditorControls();
  renderDisorderModeChoices(false);
  await refreshCreationReperageAvailability();
  setEditorPanelVisible(true);
  heritageCreationBaseline = captureLegacyCreationForm();
  desordreCreateDesignation.focus();
}

function closeDesordreDraft() {
  gpsDesordreTracking?.cancel();
  clearDesordreDraft();
  disorderRelatedObjectsController.clear();
  desordreEditorForm.reset();
  desordreEditorForm.hidden = true;
  setEditorPanelVisible(false);
  entityEditor.setContext({ mode: "edit", objectType: null });
}

async function openHeritageCreation(objectType) {
  if (!leaveBusinessEditor()) return;
  const generation = ++legacyEditorGeneration;
  legacyEditorLoading = false;
  if (gpsTronconTracking?.isActive() || gpsDesordreTracking?.isActive()) return;
  clearAppStatus("general");
  if (objectType === "desordre") {
    await openDesordreCreation();
    return;
  }
  const configuration = heritageCreationTypes[objectType];
  if (!configuration || creationRequestInFlight) {
    return;
  }
  if (lineRequestInFlight || graphicRequestInFlight) {
    beginAppStatus(
      "general",
      "error",
      "Attendez la réponse PostgreSQL en cours.",
    );
    return;
  }
  if (lineEditActive) {
    stopLineEdit({ restore: true });
  }
  if (graphicEditActive) {
    stopGraphicEdit({ restore: true });
  }
  await loadHeritageTree();
  if (objectType === "troncon") {
    await loadTronconCreationReferences();
  }
  if (generation !== legacyEditorGeneration) return;
  clearTronconDraft();
  entityEditor.setContext({ mode: "create", objectType });
  lastServerFeature = null;
  requestedDesordreId = null;


  editorTabs.hidden = false;
  showEditorTab("general");
  generalTab.hidden = false;
  observationsTab.hidden = true;
  heritageObjectForm.hidden = false;
  desordreEditorForm.hidden = true;
  heritageObjectIdField.hidden = true;
  heritageObjectId.value = "";
  heritageObjectLabel.value = "";
  heritageObjectDesignation.value = "";
  heritageObjectCommentaire.value = "";
  heritageObjectPopulation.value = "";
  heritageObjectClassement.value = "";
  heritageObjectNiveau.value = "";
  heritageObjectDateDebut.value = "";
  heritageObjectDateFin.value = "";
  heritageObjectForm.querySelectorAll("input, textarea").forEach((element) => {
    element.disabled = false;
  });
  heritageObjectValid.checked = true;
  heritageObjectValid.disabled = false;
  heritageCreateActions.hidden = false;
  heritageCreateMessage.textContent = "";
  heritageCreateMessage.classList.remove("error");
  heritageObjectForm.querySelectorAll("[data-create-types]").forEach(
    (element) => {
      element.hidden = !element.dataset.createTypes.split(" ").includes(objectType);
    },
  );
  tronconCreateGeometry.hidden = objectType !== "troncon";
  heritageParentField.hidden = objectType === "systeme";
  if (objectType !== "systeme") {
    heritageParentLabel.textContent = objectType === "digue"
      ? "Système d’endiguement parent"
      : "Digue parente";
    heritageParent.disabled = false;
    fillHeritageParentOptions(objectType, creationContextParent(objectType));
  } else {
    heritageParent.disabled = true;
  }
  setEditorPanelVisible(true);
  gpsTronconTracking?.refresh();
  heritageCreationBaseline = captureLegacyCreationForm();
  heritageObjectLabel.focus();
}

function closeHeritageDraft() {
  gpsTronconTracking?.cancel();
  clearTronconDraft();
  heritageObjectForm.reset();
  heritageObjectForm.hidden = true;
  setEditorPanelVisible(false);
  entityEditor.setContext({ mode: "edit", objectType: null });
}

function selectCreatedHeritageObject(kind, identifier) {
  const button = Array.from(heritageTree.querySelectorAll(".tree-name")).find(
    (candidate) => candidate.dataset.objectKind === kind
      && candidate.dataset.objectId === String(identifier),
  );
  button?.click();
}

function addCreatedObjectToHeritage(objectType, created) {
  if (objectType === "systeme") {
    heritageData.systemes.push({ ...created, digues: created.digues || [] });
  } else if (objectType === "digue") {
    const systeme = heritageData.systemes.find(
      (item) => String(item.id) === String(created.systeme_endiguement_id),
    );
    systeme?.digues.push({ ...created, troncons: created.troncons || [] });
  } else {
    const properties = created.properties || {};
    const digue = heritageData.systemes.flatMap(
      (systeme) => systeme.digues,
    ).find((item) => String(item.id) === String(properties.digue_id));
    digue?.troncons.push(properties);
  }
  renderHeritageTree(heritageData);
  topNavigation.open("heritage");
  const kind = heritageCreationTypes[objectType].title;
  const identifier = objectType === "troncon" ? created.properties.id : created.id;
  selectCreatedHeritageObject(kind, identifier);
}

function showCreatedObject(objectType, created) {
  const values = objectType === "troncon" ? created.properties : created;
  entityEditor.setContext({
    mode: "edit", objectType, objectId: values.id, data: created,
  });
  mapEditorStale = false;


  heritageObjectIdField.hidden = false;
  heritageObjectId.value = values.id;
  heritageObjectLabel.value = values.libelle || "";
  heritageObjectDesignation.value = inputText(values.designation);
  heritageObjectCommentaire.value = inputText(values.commentaire);
  heritageObjectPopulation.value = inputText(values.population_protegee);
  heritageObjectClassement.value = inputText(values.classement);
  heritageObjectNiveau.value = inputText(values.niveau_protection);
  heritageObjectDateDebut.value = inputText(values.date_debut);
  heritageObjectDateFin.value = inputText(values.date_fin);
  heritageObjectForm.querySelectorAll("input, textarea").forEach((element) => {
    element.disabled = true;
  });
  heritageObjectValid.checked = Boolean(values.valid);
  heritageObjectValid.disabled = true;
  heritageParent.disabled = true;
  heritageCreateActions.hidden = true;
  if (objectType === "troncon") {
    tronconDrawStatus.textContent = "";
    tronconDrawStatus.hidden = true;
    tronconDrawActions.hidden = true;
    startTronconDrawButton.hidden = true;
  }
  heritageCreateMessage.textContent = "Objet créé et relu avec succès.";
  heritageCreateMessage.classList.remove("error");
}

function configureTronconLayer(feature, layer) {
  tronconLayersById.set(String(feature.properties.id), layer);
  layer.unbindPopup?.();
  layer.on("click", () => void openHeritageEdit("troncons", feature.properties.id));
}

function addTronconFeatureToMap(feature) {
  tronconPrintInventory.set(String(feature.properties.id), feature);
  if (!tronconsGeoJsonLayer) return null;
  let createdLayer = null;
  const layer = L.geoJSON(feature, {
    style: TRONCON_STYLE,
    onEachFeature: configureTronconLayer,
  });
  layer.eachLayer((item) => {
    tronconsGeoJsonLayer.addLayer(item);
    createdLayer = item;
  });
  return createdLayer;
}

function removeTronconFromMap(identifier) {
  const id = String(identifier);
  const hadInventory = tronconPrintInventory.delete(id);
  const layer = tronconLayersById.get(id);
  if (!layer) {
    hiddenTronconIds.delete(id);
    return hadInventory;
  }
  if (highlightedTronconLayer === layer) highlightedTronconLayer = null;
  tronconsGeoJsonLayer?.removeLayer(layer);
  tronconLayersById.delete(id);
  hiddenTronconIds.delete(id);
  updateHeritageVisibilityControls();
  updateAppBaseStatusFromHeritageSelection();
  return true;
}

function upsertTronconOnMap(feature) {
  const identifier = String(feature?.properties?.id || "");
  if (!identifier || feature?.type !== "Feature") {
    throw new Error("Tronçon cartographique invalide.");
  }
  const previousLayer = tronconLayersById.get(identifier);
  const restoreHighlight = highlightedTronconLayer === previousLayer
    || (selectedHeritageObject?.kind === "Tronçon"
      && String(selectedHeritageObject.item.id) === identifier);
  const reopenPopup = Boolean(previousLayer?.isPopupOpen?.());
  if (previousLayer) {
    if (highlightedTronconLayer === previousLayer) highlightedTronconLayer = null;
    tronconsGeoJsonLayer?.removeLayer(previousLayer);
    tronconLayersById.delete(identifier);
  }
  const layer = addTronconFeatureToMap(feature);
  if (restoreHighlight) highlightTroncon(identifier);
  if (reopenPopup) layer?.openPopup?.();
  applyIndividualTronconVisibility();
  applyIndividualDesordreVisibility();
  return layer;
}

function addCreatedTronconToMap(feature) {
  return upsertTronconOnMap(feature);
}

heritagePanelController = HeritagePanel.createController({
  fetchJson,
  getShowUuid: () => showUuid,
  hiddenTronconIds,
  canZoom(tronconIds) {
    return tronconIds.some((id) => tronconBounds(tronconLayersById.get(id))?.isValid?.());
  },
  onZoom: zoomToHeritageObject,
  async onSelection(selection, { origin = "user" } = {}) {
    if (window.BusinessRuntime && (!BusinessRuntime.isActive() || !BusinessRuntime.can("business.read"))) return false;
    const resource = selection.resource;
    if (origin !== "restore") {
      if (creationRequestInFlight || editorMapInteractionBlocksObjectOpen()) return false;
      const runtime = window.BusinessRuntime;
      let accepted;
      if (["systemes", "digues", "troncons"].includes(resource)) accepted = Boolean(await openHeritageEdit(resource, selection.item.id));
      else accepted = await MapEntityNavigation.open(resource, selection.item.id);
      if (!accepted || runtime !== window.BusinessRuntime || runtime?.isActive?.() === false) return false;
      topNavigation.close("heritage");
    }
    selectedHeritageObject = selection;
    document.querySelector("#create-troncon-observation").hidden = resource !== "troncons";
    clearHighlightedTroncon();
    if (resource === "troncons") highlightTroncon(selection.item.id);
    return true;
  },
  onVisibilityChange(_hiddenTronconIds, selection, context) {
    applyIndividualTronconVisibility();
    applyIndividualDesordreVisibility();
    heritageSelectionSyncController?.selectionChanged(selection, context);
  },
  onDataChange(data) {
    heritageData = data;
    window.LidarMapController?.invalidatePatrimoine();
  },
  isRequestBlocked: () => creationRequestInFlight,
  onRequestState(pending) {
    creationRequestInFlight = pending;
  },
});

window.OfflineRuntime?.attachHeritage(heritagePanelController);
heritageSelectionSyncController = HeritageSelectionSync.createController({
  bus: InterPageBus,
  checkbox: heritageSelectionSyncCheckbox,
  heritageController: heritagePanelController,
});

aiPanelController = AiPanel.createController({
  fetchJson,
  onVisibilityChange: refreshMapSize,
});

const topNavigation = TopNavigation.createController({
  onChange(state, mode) {
    appRoot.dataset.layout = mode;
    for (const name of ["ai", "heritage", "editor", "settings"]) {
      appRoot.classList.toggle(`${name}-open`, Boolean(state[name]));
    }
    primaryArea.inert = mode === "single-tool" && (state.ai || state.settings);
    const mapCovered = syncEditorMapInteractionPresentation(state, mode);
    if (!state.editor) cancelGpsCandidate();
    syncGpsControls();
    if (primaryArea.inert && primaryArea.contains(document.activeElement)) {
      (state.settings ? pwaSettingsToggleButton : aiToggleButton).focus();
    } else if (mapCovered && mapElement.contains(document.activeElement)) {
      (state.editor ? editorCloseTarget() : heritageToggleButton).focus();
    }
    refreshMapSize();
  },
  entries: {
    heritage: {
      panel: heritagePanel,
      toggleButton: heritageToggleButton,
      closeButton: heritageCloseButton,
      initialOpen: !heritagePanel.hidden,
      setOpen: setHeritagePanelOpen,
    },
    create: {
      toggleButton: createMenuButton,
      initialOpen: !createMenuList.hidden,
      setOpen: setCreateMenuOpen,
    },
    tools: {
      toggleButton: toolsMenuButton,
      initialOpen: !toolsMenuList.hidden,
      setOpen: setToolsMenuOpen,
    },
    ai: {
      panel: aiPanel,
      toggleButton: aiToggleButton,
      closeButton: aiCloseButton,
      initialOpen: !aiPanel.hidden,
      setOpen: setAiPanelOpen,
    },
    settings: {
      panel: pwaSettingsPanel,
      toggleButton: pwaSettingsToggleButton,
      closeButton: pwaSettingsCloseButton,
      focusTarget: pwaDistanceMeters,
      canOpen: () => PwaGeolocation.isStandalone() && !editorMapInteraction,
      initialOpen: false,
      setOpen: setPwaSettingsPanelOpen,
    },
    editor: {
      panel: editorPanel,
      get focusTarget() { return editorCloseTarget(); },
      returnFocusTarget: createMenuButton,
      initialOpen: !editorPanel.hidden,
      setOpen(open) { editorPanel.hidden = !open; },
    },
  },
});

window.DisorderReports?.createCatalogueController({
  button: document.querySelector('[data-tool-action="disorder-catalogue"]'),
  status: document.querySelector("#status"),
  getScope: () => selectedHeritageObject,
  onInvoke: () => topNavigation.close("tools"),
});

window.DisorderReports?.createDisorderPdfController({
  button: desordrePdfButton,
  status: desordrePdfStatus,
  getDisorderId: () => (editorState.objectType === "desordre" && editorState.mode === "edit"
    ? editorState.objectId
    : null),
});

function syncPwaSettingsAvailability() {
  const standalone = PwaGeolocation.isStandalone();
  const basemapContainer = document.querySelector("#basemap-selector");
  const destination = standalone
    ? document.querySelector("#pwa-settings-panel .settings-basemaps")
    : legacySettingsMenu.querySelector(".settings-menu-list");
  if (basemapContainer.parentElement !== destination) destination.append(basemapContainer);
  document.body.classList.toggle("pwa-standalone", standalone);
  if (!desordreEditorForm.hidden) updateDisorderEditorControls();
  const settingsHadFocus = pwaSettingsPanel.contains(document.activeElement)
    || document.activeElement === pwaSettingsToggleButton;
  if (!standalone) topNavigation.close("settings");
  pwaSettingsToggleButton.hidden = !standalone;
  legacySettingsMenu.hidden = standalone;
  if (standalone) legacySettingsMenu.open = false;
  else if (settingsHadFocus) legacySettingsMenu.querySelector("summary").focus();
  applyUserPositionPreference();
}
document.addEventListener("digues-app:header-capabilities", syncPwaSettingsAvailability);
syncPwaSettingsAvailability();

pwaSettingsForm.addEventListener("invalid", () => {
  pwaSettingsMessage.textContent = "Corrigez les valeurs hors limites avant d’enregistrer.";
}, true);
pwaSettingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!PwaGeolocation.isStandalone()) return;
  if (!pwaSettingsForm.reportValidity()) return;
  try {
    const saved = PwaMapSettings.saveSettings({
      showUserPosition: pwaShowUserPosition.checked,
      showWazeButton: pwaShowWazeButton.checked,
      tracking: {
        distanceMeters: pwaDistanceMeters.valueAsNumber,
        intervalSeconds: pwaIntervalSeconds.valueAsNumber,
        maxAccuracyMeters: pwaMaxAccuracyMeters.valueAsNumber,
        smartPolygonCorrection: pwaSmartPolygonCorrection.checked,
      },
      proximity: {
        enabled: pwaProximityEnabled.checked,
        radiusKm: pwaRadiusKm.valueAsNumber,
      },
    });
    fillPwaSettingsForm(saved);
    syncWazeButtonVisibility();
    pwaSettingsMessage.textContent = "Paramètres enregistrés sur cet appareil.";
    applyUserPositionPreference();
  } catch (_error) {
    pwaSettingsMessage.textContent = "Enregistrement local impossible.";
  }
});
pwaSettingsResetButton.addEventListener("click", () => {
  if (!PwaGeolocation.isStandalone()) return;
  try {
    fillPwaSettingsForm(PwaMapSettings.resetSettings());
    syncWazeButtonVisibility();
    applyUserPositionPreference();
    pwaSettingsMessage.textContent = "Valeurs par défaut rétablies.";
  } catch (_error) {
    pwaSettingsMessage.textContent = "Réinitialisation locale impossible.";
  }
});

// GEO-1 garde son watch pendant la visibilité normale ; pagehide le libère,
// pageshow rétablit uniquement cette préférence si la PWA est encore installée.
window.addEventListener("pagehide", () => userPositionController.stop());
window.addEventListener("pageshow", applyUserPositionPreference);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") applyUserPositionPreference();
});

function syncWorkspaceLayout() {
  topNavigation.setLayout(appWorkspace.clientWidth, window.innerHeight);
}
syncWorkspaceLayout();
new ResizeObserver(syncWorkspaceLayout).observe(appWorkspace);
new ResizeObserver(syncCompactLandscapeMenus).observe(appHeader);
window.addEventListener("resize", syncWorkspaceLayout);

appNavigationViewport.addEventListener("scroll", syncCompactLandscapeMenus, {
  passive: true,
});
window.addEventListener("resize", syncCompactLandscapeMenus);
window.addEventListener("orientationchange", syncCompactLandscapeMenus);

createMenuList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-create-type]");
  if (!button) {
    return;
  }
  topNavigation.close("create");
  if (!mapCreateActions.run(button.dataset.createType)) {
    openHeritageCreation(button.dataset.createType);
  }
});

toolsMenuList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tool-action]");
  if (!button) {
    return;
  }
  topNavigation.close("tools");
  if (button.dataset.toolAction === "territoire-administratif") {
    openTerritoireModal();
  } else if (button.dataset.toolAction === "print-map") {
    openMapPrintModal();
  } else if (button.dataset.toolAction === "offline-basemaps") {
    if (offlineBasemapUi) void offlineBasemapUi.open();
    else window.alert("Le stockage cartographique local est indisponible dans ce navigateur.");
  }
});

territoireForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (territoireImportPending) {
    return;
  }
  submitTerritoireImport().catch((error) => {
    console.error("Import du territoire administratif impossible", error);
    territoireMessage.textContent = error.message;
    territoireMessage.classList.add("error");
    setTerritoireImportPending(false);
  });
});

closeTerritoireModalButton.addEventListener("click", closeTerritoireModal);
cancelTerritoireModalButton.addEventListener("click", closeTerritoireModal);
territoireModal.addEventListener("click", (event) => {
  if (event.target === territoireModal) {
    closeTerritoireModal();
  }
});

mapPrintForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (mapPrintPending) return;
  submitMapPrint().catch((error) => {
    console.error("Export cartographique impossible", error);
    mapPrintMessage.textContent = error.message;
    mapPrintMessage.classList.add("error");
    setMapPrintPending(false);
  });
});

closeMapPrintModalButton.addEventListener("click", closeMapPrintModal);
cancelMapPrintModalButton.addEventListener("click", closeMapPrintModal);
mapPrintModal.addEventListener("click", (event) => {
  if (event.target === mapPrintModal) {
    closeMapPrintModal();
  }
});

function beginLegacyDrawing(kind, layer, previous) {
  const controls = kind === "troncon" ? MapFormViews.heritage.geometry : MapFormViews.disorder.geometry;
  const type = kind === "troncon" ? "LineString" : desordreCreateGeometryType.value;
  creationGeometryBeforeDrawing = previous;
  const vertices = () => type === "Point" ? (layer.getLatLng?.() ? [layer.getLatLng()] : [])
    : type === "Polygon" ? layer.getLatLngs()[0] || [] : layer.getLatLngs();
  legacyDrawingAcquisition = EntityGeometryDraft.beginAcquisition({ map, external: true,
    getType: () => type, getVertices: vertices,
    finish() { if (layer.editor?.drawing()) layer.editor.commitDrawing(); },
  });
  beginEditorMapInteraction({ kind: `create-${kind}${kind === "desordre" ? "-"+type : ""}`,
    instruction: type === "Point" ? "Cliquez pour placer le Point." : "Dessinez sur la carte.",
    cancel: kind === "troncon" ? cancelTronconCreationDrawing : cancelDesordreCreationDrawing,
    finishAction: () => legacyDrawingAcquisition?.finish(),
    returnFocusTarget: controls.draw, finishLabel: "Terminer" });
  controls.confirm.onclick = () => legacyDrawingAcquisition?.finish();
  if(kind === "troncon") updateTronconDraftStatus(); else updateDesordreDraftStatus();
}
function finishLegacyDrawing(layer) {
  if (!legacyDrawingAcquisition) return;
  legacyDrawingAcquisition.stop(); legacyDrawingAcquisition = null;
  creationGeometryBeforeDrawing=null;
  layer.disableEdit?.();
  finishEditorMapInteraction();
  if(layer === provisionalTronconLayer) updateTronconDraftStatus(); else updateDesordreDraftStatus();
}

startTronconDrawButton.addEventListener("click", () => {
  if (editorState.mode !== "create" || editorState.objectType !== "troncon") {
    return;
  }
  const previous=provisionalTronconLayer?.toGeoJSON(false).geometry || null;
  clearTronconDraft();
  provisionalTronconLayer = map.editTools.startPolyline(undefined, {
    color: "#1769aa",
    dashArray: "7 5",
    opacity: 1,
    weight: 6,
  });
  beginLegacyDrawing("troncon",provisionalTronconLayer,previous);
});

function cancelTronconCreationDrawing() {
  const previous=creationGeometryBeforeDrawing;
  legacyDrawingAcquisition?.stop(); legacyDrawingAcquisition=null;
  clearTronconDraft();
  if(previous) { setTronconDraft(previous); provisionalTronconLayer.disableEdit?.(); }
  updateTronconDraftStatus();
}

cancelTronconDrawButton.addEventListener("click", () => {
  if (editorMapInteraction?.kind === "create-troncon") {
    cancelEditorMapInteraction();
    return;
  }
  cancelTronconCreationDrawing();
});



map.on("editable:drawing:end", event => {
  if (event.layer === provisionalTronconLayer || event.layer === provisionalDesordreLayer) finishLegacyDrawing(event.layer);
});

cancelCreateButton.addEventListener("click", () => closeEditorButton.click());

heritageObjectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (gpsTronconTracking?.isActive()) return;
  if (editorState.mode !== "create" || creationRequestInFlight) {
    return;
  }
  const objectType = editorState.objectType;
  const configuration = heritageCreationTypes[objectType];
  const payload = {
    libelle: heritageObjectLabel.value,
    designation: optionalPayloadValue(heritageObjectDesignation.value),
    commentaire: optionalPayloadValue(heritageObjectCommentaire.value),
    valid: heritageObjectValid.checked,
  };
  if (objectType === "systeme") {
    try {
      payload.population_protegee = nullableNonNegativeInteger(
        heritageObjectPopulation.value,
      );
    } catch (error) {
      heritageCreateMessage.textContent = error.message;
      heritageCreateMessage.classList.add("error");
      return;
    }
    payload.classement = optionalPayloadValue(heritageObjectClassement.value);
    payload.niveau_protection = optionalPayloadValue(heritageObjectNiveau.value);
  } else if (objectType === "digue") {
    payload.systeme_endiguement_id = heritageParent.value;
  } else if (objectType === "troncon") {
    if (!provisionalTronconLayer) {
      heritageCreateMessage.textContent = "Dessinez une LineString avant de créer le tronçon.";
      heritageCreateMessage.classList.add("error");
      return;
    }
    if (provisionalTronconLayer.editor?.drawing?.()) {
      heritageCreateMessage.textContent = "Terminez le dessin par un double-clic avant validation.";
      heritageCreateMessage.classList.add("error");
      return;
    }
    payload.digue_id = heritageParent.value;
    payload.date_debut = heritageObjectDateDebut.value || null;
    payload.date_fin = heritageObjectDateFin.value || null;
    payload.type_rive_id = optionalPayloadValue(heritageObjectTypeRive.value);
    payload.type_troncon_id = optionalPayloadValue(heritageObjectTypeTroncon.value);
    payload.typologie_troncon_id = optionalPayloadValue(
      heritageObjectTypologieTroncon.value,
    );
    payload.geometry = provisionalTronconLayer.toGeoJSON(false).geometry;
  }
  finishEditorMapInteraction({ restoreFocus: false });
  creationRequestInFlight = true;
  syncLegacyEditorPresentation();
  submitCreateButton.disabled = true;
  cancelCreateButton.disabled = true;
  closeEditorButton.disabled = true;
  heritageCreateMessage.textContent = "Création et relecture PostgreSQL…";
  heritageCreateMessage.classList.remove("error");
  let confirmed = null;
  const runtime = window.BusinessRuntime;
  const generation = legacyEditorGeneration;
  try {
    let created;
    try {
      created = await fetchJson(configuration.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      console.error(`Création ${objectType} impossible`, error);
      heritageCreateMessage.textContent = error.message;
      heritageCreateMessage.classList.add("error");
      return;
    }
    if (generation !== legacyEditorGeneration || runtime !== window.BusinessRuntime || runtime?.isActive?.() === false) return;
    const createdId = featureIdentifier(created?.type === "Feature" ? created : {
      properties: created,
    });
    publishConfirmedMutation(configuration.resource, "create", createdId ? [createdId] : null, undefined, created);
    try {
      if (objectType === "troncon") {
        clearTronconDraft();
        addCreatedTronconToMap(created);
      }
      addCreatedObjectToHeritage(objectType, created);
      showCreatedObject(objectType, created);
      confirmed = created;
    } catch (refreshError) {
      console.error(`Actualisation locale ${objectType} impossible`, refreshError);
      heritageCreateMessage.textContent =
        "Objet créé. L’actualisation locale est incomplète.";
      heritageCreateMessage.classList.add("error");
    }
  } finally {
    creationRequestInFlight = false;
    syncLegacyEditorPresentation();
    submitCreateButton.disabled = false;
    cancelCreateButton.disabled = false;
    closeEditorButton.disabled = false;
  }
  if (confirmed && generation === legacyEditorGeneration && runtime === window.BusinessRuntime && runtime?.isActive?.() !== false) {
    const id = confirmed.properties?.id || confirmed.id;
    await openHeritageEdit(configuration.resource, id);
  }
});

function desordreDraftHasNumericCoordinates() {
  return [
    desordreCreateX.value,
    desordreCreateY.value,
    desordreCreateLongitude.value,
    desordreCreateLatitude.value,
  ].some((value) => value !== "");
}

desordreCreateGeometryType.addEventListener("change", () => {
  cancelGpsCandidate();
  if (provisionalDesordreLayer || desordreDraftHasNumericCoordinates()) {
    desordreCreateGeometryType.value = previousDesordreGeometryType;
    desordreCreateMessage.textContent =
      geometryTypeBlockedMessage;
    desordreCreateMessage.classList.add("error");
    return;
  }
  clearResolvedGeometryTypeWarning();
  previousDesordreGeometryType = desordreCreateGeometryType.value;
  editorState.geometryType = desordreCreateGeometryType.value;
  cancelledDesordreGeometry = null;
  const selectedIds = selectedDesordreTronconIds();
  const point = desordreCreateGeometryType.value === "Point";
  desordreCreateTroncons.multiple = !point;
  desordreCreateTroncons.size = point ? 1 : 5;
  fillTronconSelect(
    desordreCreateTroncons,
    point ? selectedIds.slice(0, 1) : selectedIds,
    { multiple: !point },
  );
  lastAcceptedCreationTronconIds = selectedDesordreTronconIds();
  updateDisorderEditorControls();
  creationReperageAvailable = false;
  renderDisorderModeChoices(false);
  refreshCreationReperageAvailability();
});

desordreCreateLineCrs.addEventListener("change", () => {
  updateLineCoordinateLabels(desordreCreateLineCoordinates, desordreCreateLineCrs.value);
});

function clearDisorderBornage() {
  for (const borne of [desordreCreateBorneStart, desordreCreateBorneEnd]) fillBorneSelect(borne, []);
  for (const input of [desordreCreateDistanceStart, desordreCreateDistanceEnd]) EntityFormView.setMetreDistance(input, "");
  desordreCreateSenseStart.value = desordreCreateSenseEnd.value = "AVANT_BORNE";
}

desordreCreateTroncons.addEventListener("change", () => {
  if (editorState.mode !== "create") return;
  const ids = selectedDesordreTronconIds();
  if (RelationSelector.sameValues(ids, lastAcceptedCreationTronconIds)) return;
  const lostBornage = selectedDesordreMode() === "bornage" || creationBornageDraftModified();
  if (selectedDesordreMode() === "bornage") cancelGpsCandidate();
  clearDisorderBornage();
  MapFormViews.disorder.geometry.diagnostics.textContent = lostBornage
    ? "Le bornage saisi concernait le tronçon précédent. Choisissez les nouvelles références." : "";
  lastAcceptedCreationTronconIds = ids;
  void refreshCreationReperageAvailability();
});

function setPolygonEditControls(active) {
  polygonEditActive = active;
  MapFormViews.disorder.geometry.setDrawingActive(active);
  startDesordreDrawButton.hidden = active;
  desordreDrawActions.hidden = !active;
  cancelDesordreDrawButton.hidden = !active;

  validateDesordreDrawButton.hidden = !active;
  observationsTabButton.disabled = active;
}

function stopPolygonEdit({ restore, finishInteraction = true }) {
  if (!polygonEditActive) {
    return;
  }
  refreshEditorMapInteractionFormSnapshot();
  activePolygonLayer?.disableEdit?.();
  setPolygonEditControls(false);
  if (restore && lastServerFeature?.geometry?.type === "Polygon") {
    updatePolygonLayer(lastServerFeature);
    renderPolygonServerFeature(lastServerFeature, activePolygonLayer);
    restoreEditorMapInteractionFormSnapshot();
  }
  setPolygonStyle("selected");
  if (
    finishInteraction
    && editorMapInteraction?.kind === "edit-desordre-Polygon"
  ) {
    finishEditorMapInteraction();
  }
}

function cancelExistingPolygonEdit() { cancelDisorderGeometryEdit(); }

startDesordreDrawButton.addEventListener("click", () => {
  cancelGpsCandidate();
  if (editorState.mode === "edit" && editorState.geometryType === "Polygon") {
    if (!activePolygonLayer?.enableEdit) return;
    desordreAcquisitionGeometry = structuredClone(currentDisorderGeometry());
    activePolygonLayer.enableEdit(map);
    setPolygonEditControls(true);
    setPolygonStyle("editing");
    desordreDrawStatus.textContent = "";
    beginEditorMapInteraction({
      kind: "edit-desordre-Polygon",
      instruction: "Ajustez les sommets, puis terminez.",
      cancel: cancelExistingPolygonEdit,
      finishAction: validateExistingPolygonEdit,
      finishLabel: "Terminer",
      formSnapshot: captureExistingGeometryEditFormSnapshot(),
      returnFocusTarget: startDesordreDrawButton,
      cancelFocusTarget: startDesordreDrawButton,
      finishAvailable: true,
    });
    beginDisorderGeometryAcquisition(activePolygonLayer,"Polygon",validateExistingPolygonEdit);
    return;
  }
  if (editorState.mode !== "create" || editorState.objectType !== "desordre") {
    return;
  }
  const previous=provisionalDesordreLayer?.toGeoJSON(false).geometry || null;
  clearDesordreDraft();
  const geometryType = desordreCreateGeometryType.value;
  if (geometryType === "Point") {
    provisionalDesordreLayer = map.editTools.startMarker(undefined, {
      icon: pointIcon,
    });
  } else if (geometryType === "LineString") {
    provisionalDesordreLayer = map.editTools.startPolyline(undefined, {
      color: "#a44f18", dashArray: "7 5", opacity: 1, weight: 6,
    });
  } else {
    provisionalDesordreLayer = map.editTools.startPolygon(undefined, {
      color: "#a44f18", dashArray: "7 5", fillOpacity: 0.18, weight: 4,
    });
  }
  beginLegacyDrawing("desordre",provisionalDesordreLayer,previous);
});

function cancelDesordreCreationDrawing() {
  const previous=creationGeometryBeforeDrawing;
  clearDesordreDraft();
  if(previous) provisionalDesordreLayer=layerFromDesordreGeometry(previous).addTo(map);
  updateDesordreDraftStatus();
  clearResolvedGeometryTypeWarning();
}

cancelDesordreDrawButton.addEventListener("click", () => {
  cancelGpsCandidate();
  if (polygonEditActive) {
    if (editorMapInteraction?.kind === "edit-desordre-Polygon") {
      cancelEditorMapInteraction();
      return;
    }
    cancelExistingPolygonEdit();
    return;
  }
  if (editorMapInteraction?.kind?.startsWith("create-desordre-")) {
    cancelEditorMapInteraction();
    return;
  }
  cancelDesordreCreationDrawing();
});


cancelDesordreCreateButton.addEventListener("click", () => closeEditorButton.click());

function validateExistingPolygonEdit() {
  if (!polygonEditActive || !activePolygonLayer) return;
  stageDisorderGeometry(activePolygonLayer.toGeoJSON(false).geometry);
  finishDisorderGeometryEdit();
}

validateDesordreDrawButton.addEventListener("click", validateExistingPolygonEdit);

function currentDisorderGeometry() {
  if (desordreSpatialDraft?.geometry) return desordreSpatialDraft.geometry;
  if (desordreSpatialDraft?.longitude !== undefined) return { type: "Point",
    coordinates: [desordreSpatialDraft.longitude, desordreSpatialDraft.latitude] };
  return lastServerFeature?.geometry;
}
function setDisorderLayerGeometry(geometry) {
  if (!geometry) return;
  if (geometry.type === "Point") activePointLayer?.setLatLng([geometry.coordinates[1], geometry.coordinates[0]]);
  else {
    const coordinates = geometry.type === "Polygon" ? geometry.coordinates[0].slice(0,-1) : geometry.coordinates;
    (geometry.type === "Polygon" ? activePolygonLayer : activeLineLayer)?.setLatLngs(
      coordinates.map(([lng,lat]) => [lat,lng]));
  }
}
function stageDisorderGeometry(geometry) {
  desordreSpatialDraft = geometry.type === "Point"
    ? { longitude: geometry.coordinates[0], latitude: geometry.coordinates[1] } : { geometry };
  setDisorderLayerGeometry(geometry);
  syncLegacyEditorPresentation();
}
function beginDisorderGeometryAcquisition(layer, type, finish) {
  disorderEditAcquisition?.stop();
  disorderEditAcquisition=EntityGeometryDraft.beginAcquisition({map,external:true,blockLayerClicks:true,
    getType:()=>type,getVertices:()=>type === "Point" ? [layer.getLatLng()]
      : type === "Polygon" ? layer.getLatLngs()[0] : layer.getLatLngs(),finish});
}
function finishDisorderGeometryEdit() {
  disorderEditAcquisition?.stop(); disorderEditAcquisition=null;
  if (polygonEditActive) { activePolygonLayer?.disableEdit?.(); setPolygonEditControls(false); }
  if (lineEditActive) { activeLineLayer?.disableEdit?.(); setLineEditControls(false); }
  if (graphicEditActive) { activePointLayer?.dragging?.disable(); setGraphicControls(false); }
  provisionalLatLng = null; desordreAcquisitionGeometry=null; pointPlacementFromGps=false;
  mapPositionStatus.textContent = lineGeometryStatus.textContent = desordreDrawStatus.textContent = "";
  finishEditorMapInteraction();
  syncLegacyEditorPresentation();
}
function cancelDisorderGeometryEdit() {
  disorderEditAcquisition?.stop(); disorderEditAcquisition=null;
  setDisorderLayerGeometry(desordreAcquisitionGeometry || currentDisorderGeometry());
  const point = desordreAcquisitionGeometry?.type === "Point" && desordreAcquisitionGeometry.coordinates;
  if (point) { disorderFields.longitude.value=coordinate(point[0],6); disorderFields.latitude.value=coordinate(point[1],6); }
  finishDisorderGeometryEdit();
}
function disorderCoordinateInputs(mode = selectedDesordreMode()) {
  if(editorState.geometryType === "LineString") return [lineStart1,lineStart2,lineEnd1,lineEnd2];
  return (mode === "xy" ? ["x","y"] : ["longitude","latitude"]).map(key=>disorderFields[key]);
}
function disorderCoordinatePairs(mode = selectedDesordreMode()) {
  const geometry=currentDisorderGeometry();
  const pairs=geometry.type === "Point" ? [geometry.coordinates] : [geometry.coordinates[0],geometry.coordinates.at(-1)];
  return mode === "xy" ? pairs.map(pair=>window.proj4("EPSG:4326",EntityGeometryDraft.projectedCRS[businessCRS.srid],pair)) : pairs;
}
function fillDisorderCoordinates() {
  const mode=selectedDesordreMode();
  if(editorState.mode !== "edit" || !["xy","lonlat"].includes(mode) || !currentDisorderGeometry()) return;
  if(mode === "xy" && !businessCRS) return;
  const fields=disorderCoordinateInputs(mode), canonical=disorderCoordinatePairs(mode).flat();
  fields.forEach((input,index)=>input.value=coordinate(canonical[index],mode === "xy" ? 2 : 6));
  disorderNumericBasis={mode,values:fields.map(input=>input.value),canonical};
}
function readDisorderCoordinates() {
  const mode=selectedDesordreMode(), fields=disorderCoordinateInputs(mode);
  const values=fields.map(input=>input.value);
  const basis=disorderNumericBasis?.mode === mode ? disorderNumericBasis : null;
  if(basis && values.every((value,index)=>value === basis.values[index])) return null;
  const numbers=[];
  for(let index=0;index<values.length;index+=2) numbers.push(...EntityGeometryDraft.readCoordinatePair(values.slice(index,index+2)));
  return numbers.map((number,index)=>basis && values[index] === basis.values[index] ? basis.canonical[index] : number);
}
async function saveDisorderDraft() {
  if (gpsDesordreTracking?.isActive()) return;
  if (!lastServerFeature || creationRequestInFlight || editorState.pending || editorState.conflict || editorState.forbidden) return;
  if (polygonEditActive || lineEditActive || graphicEditActive) {
    desordreCreateMessage.textContent="Terminez ou annulez le dessin avant d’enregistrer.";
    desordreCreateMessage.classList.add("error"); return;
  }
  let payload;
  try {
    payload = { designation: optionalPayloadValue(desordreCreateDesignation.value),
      type_desordre_id: optionalPayloadValue(desordreCreateTypeReference.value),
      categorie_desordre_id: optionalPayloadValue(desordreCreateCategorieReference.value),
      cote_id: optionalPayloadValue(desordreCreateCote.value), position_id: optionalPayloadValue(desordreCreatePosition.value),
      source_id: optionalPayloadValue(desordreCreateSource.value), commentaire: optionalPayloadValue(desordreCreateCommentaire.value),
      lieu_dit: optionalPayloadValue(desordreCreateLieuDit.value), date_debut: optionalPayloadValue(desordreCreateDateDebut.value),
      date_fin: optionalPayloadValue(desordreCreateDateFin.value), valid: desordreCreateValid.checked,
      troncon_ids: selectedDesordreTronconIds() };
    const type = editorState.geometryType, mode = selectedDesordreMode();
    if (mode === "bornage") payload.reperage = type === "Point" ? buildPointReperagePayload() : buildLineReperagePayload();
    else if (["Point","LineString"].includes(type) && ["xy","lonlat"].includes(mode)) {
      const values=readDisorderCoordinates();
      if(!values) Object.assign(payload,desordreSpatialDraft || {});
      else if(type === "Point") {
        const keys=mode === "xy" ? ["x","y"] : ["longitude","latitude"];
        keys.forEach((key,index)=>payload[key]=values[index]);
      } else {
        const pairs=[values.slice(0,2),values.slice(2)];
        if(desordreSpatialDraft?.geometry) {
          const positions=mode === "xy" ? pairs.map(pair=>window.proj4(EntityGeometryDraft.projectedCRS[businessCRS.srid],"EPSG:4326",pair)) : pairs;
          payload.geometry=EntityGeometryDraft.geometryFromCoordinates("LineString",positions,desordreSpatialDraft.geometry);
        } else payload.line_endpoints={crs:lineEndpointsCrs.value,debut:pairs[0],fin:pairs[1]};
      }
    } else Object.assign(payload, desordreSpatialDraft || {});
  } catch(error) {
    desordreCreateMessage.textContent=error.message; desordreCreateMessage.classList.add("error"); return;
  }
  const signature=JSON.stringify([lastServerFeature.properties.id,lastServerFeature.properties.version,payload]);
  if (disorderSaveIntent?.uncertain && disorderSaveIntent.signature !== signature) {
    desordreCreateMessage.textContent="Résultat non confirmé. Réessayez la même intention avant de modifier le brouillon.";
    desordreCreateMessage.classList.add("error"); return;
  }
  if(!disorderSaveIntent || disorderSaveIntent.signature !== signature)
    disorderSaveIntent={signature,operationId:crypto.randomUUID(),uncertain:false};
  const intent=disorderSaveIntent, generation=legacyEditorGeneration;
  creationRequestInFlight=true; syncLegacyEditorPresentation();
  let confirmed=false;
  try {
    const feature=await writeDesordre(`/api/desordres/${encodeURIComponent(lastServerFeature.properties.id)}`,
      {method:"PUT",headers:{"Content-Type":"application/json","Operation-Id":intent.operationId},body:JSON.stringify(payload)});
    if(generation !== legacyEditorGeneration) return;
    EntityFormSession.validateReceipt(feature,{id:lastServerFeature.properties.id,version:lastServerFeature.properties.version});
    confirmed=true; desordreSpatialDraft=null; disorderSaveIntent=null;
    publishConfirmedDesordreUpdate(feature);
    if(feature.geometry.type === "Point") { renderPointServerFeature(feature); updatePointLayer(feature); }
    else if(feature.geometry.type === "LineString") { renderLineServerFeature(feature); updateLineLayer(feature); }
    else { updatePolygonLayer(feature); renderPolygonServerFeature(feature,activePolygonLayer); }
    desordreCreateMessage.textContent="Enregistré — état relu depuis PostgreSQL.";
    desordreCreateMessage.classList.remove("error");
  } catch(error) {
    if(generation !== legacyEditorGeneration) return;
    if(!confirmed) {
      const failure=EntityFormSession.failureState(error);
      Object.assign(editorState,failure); intent.uncertain=failure.uncertain;
      if(!failure.uncertain) disorderSaveIntent=null;
    }
    desordreCreateMessage.textContent=confirmed ? "Enregistré. L’actualisation locale est incomplète."
      : intent.uncertain ? "Résultat non confirmé. Réessayez Enregistrer avec les mêmes valeurs." : error.message;
    desordreCreateMessage.classList.add("error");
  } finally { if(generation === legacyEditorGeneration) { creationRequestInFlight=false; syncLegacyEditorPresentation(); queuePendingMapEntityRefresh(); } }
}

function selectedDesordreTronconIds() {
  return Array.from(desordreCreateTroncons.selectedOptions).map(
    (option) => option.value,
  ).filter(Boolean);
}

function buildDesordreCreationPayload() {
  const payload = {
    designation: optionalPayloadValue(desordreCreateDesignation.value),
    type_desordre_id: optionalPayloadValue(desordreCreateTypeReference.value),
    categorie_desordre_id: optionalPayloadValue(
      desordreCreateCategorieReference.value,
    ),
    cote_id: optionalPayloadValue(desordreCreateCote.value),
    position_id: optionalPayloadValue(desordreCreatePosition.value),
    source_id: optionalPayloadValue(desordreCreateSource.value),
    commentaire: optionalPayloadValue(desordreCreateCommentaire.value),
    lieu_dit: optionalPayloadValue(desordreCreateLieuDit.value),
    date_debut: optionalPayloadValue(desordreCreateDateDebut.value),
    date_fin: optionalPayloadValue(desordreCreateDateFin.value),
    valid: desordreCreateValid.checked,
    troncon_ids: selectedDesordreTronconIds(),
  };
  const geometryType = desordreCreateGeometryType.value;
  payload.geometry_type = geometryType;
  const method = geometryType === "Point"
    ? selectedDesordreMode()
    : geometryType === "LineString" ? selectedDesordreMode() : "map";
  if (geometryType === "Point" && method === "xy") {
    [payload.x, payload.y] = EntityGeometryDraft.readCoordinatePair(
      [desordreCreateX.value, desordreCreateY.value], "X et Y doivent être renseignés ensemble.",
    );
    return payload;
  }
  if (geometryType === "LineString" && ["xy", "lonlat"].includes(method)) {
    const values = [
      desordreCreateLineStart1.value, desordreCreateLineStart2.value,
      desordreCreateLineEnd1.value, desordreCreateLineEnd2.value,
    ];
    const message = "Les quatre coordonnées de début/fin sont obligatoires.";
    payload.line_endpoints = {
      crs: desordreCreateLineCrs.value,
      debut: EntityGeometryDraft.readCoordinatePair(values.slice(0, 2), message),
      fin: EntityGeometryDraft.readCoordinatePair(values.slice(2), message),
    };
    return payload;
  }
  if (method === "bornage") {
    const modes = availableDisorderModes(
      geometryType,
      selectedDesordreTronconIds().length,
      creationReperageAvailable,
    );
    if (!modes.includes("bornage")) {
      throw new Error("Le bornage exige exactement un tronçon exploitable.");
    }
    const startDistance = Number(desordreCreateDistanceStart.value);
    const endDistance = Number(desordreCreateDistanceEnd.value);
    if (!desordreCreateBorneStart.value || !Number.isFinite(startDistance)) {
      throw new Error("Le bornage de début est incomplet.");
    }
    payload.reperage = {
      borne_debut_id: desordreCreateBorneStart.value,
      distance_debut_m: startDistance,
      position_debut_relative: desordreCreateSenseStart.value,
    };
    if (geometryType === "LineString") {
      if (!desordreCreateBorneEnd.value || !Number.isFinite(endDistance)) {
        throw new Error("Le bornage de fin est incomplet.");
      }
      Object.assign(payload.reperage, {
        borne_fin_id: desordreCreateBorneEnd.value,
        distance_fin_m: endDistance,
        position_fin_relative: desordreCreateSenseEnd.value,
      });
    }
    return payload;
  }
  if (geometryType === "Point" && method === "lonlat") {
    [payload.longitude, payload.latitude] = EntityGeometryDraft.readCoordinatePair(
      [desordreCreateLongitude.value, desordreCreateLatitude.value],
      "Longitude et latitude doivent être renseignées ensemble.",
    );
    return payload;
  }
  if (!provisionalDesordreLayer) {
    throw new Error("Dessinez la géométrie avant de créer le désordre.");
  }
  if (provisionalDesordreLayer.editor?.drawing?.()) {
    throw new Error("Terminez le dessin avant validation.");
  }
  payload.geometry = provisionalDesordreLayer.toGeoJSON(false).geometry;
  return payload;
}

function configureDesordreLayer(feature, layer) {
  const identifier = String(feature.properties.id);
  desordreLayersById.set(identifier, layer);
  layer.unbindPopup?.();
  layer.bindPopup(popupContent(feature.properties || {}, [
    ["Désordre", "designation"],
    ["Type", "type_desordre_libelle"],
    ["Géométrie", "type_geometrie"],
    ["Identifiant", "id"],
  ]));
  if (feature.geometry?.type === "Point") {
    layer.on("click", () => openPointEditor(feature.properties.id, layer));
    layer.on("dragstart", () => {
      if (graphicEditActive && layer === activePointLayer) {
        mapPositionStatus.textContent = "Déplacement en cours — position non enregistrée.";
      }
    });
    layer.on("drag", () => {
      if (!graphicEditActive || layer !== activePointLayer) {
        return;
      }
      const position = layer.getLatLng();
      disorderFields.longitude.value = coordinate(position.lng, 6);
      disorderFields.latitude.value = coordinate(position.lat, 6);
    });
    layer.on("dragend", () => {
      if (!graphicEditActive || layer !== activePointLayer) {
        return;
      }
      provisionalLatLng = layer.getLatLng();
      disorderFields.longitude.value = coordinate(provisionalLatLng.lng, 6);
      disorderFields.latitude.value = coordinate(provisionalLatLng.lat, 6);
      validateMapPositionButton.disabled = false;
      if (editorMapInteraction?.kind === "edit-desordre-Point") {
        setEditorMapInteractionFinishAvailable(true);
        updateEditorMapInteractionInstruction(
          "Position choisie. Validez le déplacement ou annulez.",
        );
      }
      if(!pointPlacementFromGps) validatePointPositionEdit();
    });
  } else if (feature.geometry?.type === "LineString") {
    layer.on("click", () => openLineEditor(feature.properties.id, layer));
  } else if (feature.geometry?.type === "Polygon") {
    layer.on("click", () => openPolygonEditor(feature.properties.id, layer));
  }
}

function addDesordreFeatureToMap(feature) {
  if (!desordrePointLayer || !desordreLineLayer || !desordrePolygonLayer) return null;
  const collection = L.geoJSON(feature, {
    style: DisorderUrgencyStyle.pathStyle,
    pointToLayer(pointFeature, latlng) {
      return createDesordrePointMarker(latlng, pointFeature);
    },
    onEachFeature: configureDesordreLayer,
  });
  let createdLayer = null;
  collection.eachLayer((layer) => {
    const target = feature.geometry.type === "Point"
      ? desordrePointLayer
      : feature.geometry.type === "LineString" ? desordreLineLayer : desordrePolygonLayer;
    target.addLayer(layer);
    createdLayer = layer;
  });
  applyIndividualDesordreVisibility();
  return createdLayer;
}

function removeDesordreFromMap(identifier) {
  const id = String(identifier);
  const layer = desordreLayersById.get(id);
  if (!layer) return false;
  desordreLayerGroup(layer)?.removeLayer(layer);
  desordreLayersById.delete(id);
  if (activePointLayer === layer) activePointLayer = null;
  if (activeLineLayer === layer) activeLineLayer = null;
  if (activePolygonLayer === layer) activePolygonLayer = null;
  if (selectedLineLayer === layer) selectedLineLayer = null;
  if (String(editorState.objectId || "") === id) markMapEditorStale();
  updateAppBaseStatusFromHeritageSelection();
  return true;
}

function refreshOpenDesordreEditor(feature, layer) {
  const identifier = String(feature.properties?.id || "");
  if (activeBusinessEditor || editorPanel.hidden
      || editorState.objectType !== "desordre"
      || String(editorState.objectId || "") !== identifier) return;
  activePointLayer = null;
  activeLineLayer = null;
  activePolygonLayer = null;
  selectedLineLayer = null;
  prepareDisorderEditorForEdit(feature.geometry.type, identifier);
  if (feature.geometry.type === "Point") {
    activePointLayer = layer;
    renderPointServerFeature(feature);
    setPointInteractionStyle(layer, graphicEditActive ? "editing" : "selected");
  } else if (feature.geometry.type === "LineString") {
    activeLineLayer = layer;
    selectedLineLayer = layer;
    renderLineServerFeature(feature);
    setLineStyle("selected");
  } else if (feature.geometry.type === "Polygon") {
    activePolygonLayer = layer;
    renderPolygonServerFeature(feature, layer);
  }
  mapEditorStale = false;
}

function upsertDesordreOnMap(feature) {
  const identifier = String(feature?.properties?.id || "");
  if (!identifier || feature?.type !== "Feature"
      || !["Point", "LineString", "Polygon"].includes(feature.geometry?.type)) {
    throw new Error("Désordre cartographique invalide.");
  }
  const previousLayer = desordreLayersById.get(identifier);
  const reopenPopup = Boolean(previousLayer?.isPopupOpen?.());
  if (previousLayer) {
    desordreLayerGroup(previousLayer)?.removeLayer(previousLayer);
    desordreLayersById.delete(identifier);
  }
  const layer = addDesordreFeatureToMap(feature);
  if (reopenPopup) layer?.openPopup?.();
  refreshOpenDesordreEditor(feature, layer);
  applyIndividualDesordreVisibility();
  return layer;
}

function addCreatedDesordreToMap(feature) {
  return upsertDesordreOnMap(feature);
}

function renderPolygonServerFeature(feature, layer = activePolygonLayer) {
  const properties = feature.properties;
  activePolygonLayer = layer;
  if (activePolygonLayer) activePolygonLayer.feature = feature;
  lastServerFeature = feature;
  prepareDisorderEditorForEdit("Polygon", properties.id);
  entityEditor.setSnapshot(feature);


  editorTabs.hidden = false;
  showEditorTab("general");
  heritageObjectForm.hidden = true;
  desordreEditorForm.hidden = false;
  desordreCreateIdField.hidden = false;
  desordreCreateId.value = properties.id;
  desordreCreateDesignation.value = properties.designation || "";
  fillDesordreReferenceOptions(
    properties.type_desordre_id,
    properties.categorie_desordre_id,
    properties.type_desordre_libelle,
    properties.categorie_desordre_libelle,
  );
  fillDesordreAuxiliaryReferences(properties);
  desordreCreateCommentaire.value = properties.commentaire || "";
  desordreCreateLieuDit.value = properties.lieu_dit || "";
  desordreCreateDateDebut.value = properties.date_debut || "";
  desordreCreateDateFin.value = properties.date_fin || "";
  desordreCreateValid.checked = Boolean(properties.valid);
  desordreCreateGeometryType.value = "Polygon";
  fillTronconSelect(
    desordreCreateTroncons,
    properties.troncon_ids || [],
    { includeSelectedOutsideFilter: true },
  );
  desordreEditorForm.elements["desordre-mode"].value = "map";
  renderDisorderModeChoices(false);
  updateDisorderEditorControls();
  startDesordreDrawButton.hidden = false;
  desordreDrawStatus.textContent = "Géométrie relue depuis PostgreSQL.";
  desordreDrawStatus.hidden = false;
  desordreDrawActions.hidden = true;
  validateDesordreDrawButton.hidden = true;
  desordreCreateActions.hidden = false;
  desordreCreateGeometryType.disabled = true;
  desordreCreateId.disabled = true;
  submitDesordreCreateButton.textContent = "Enregistrer";
  cancelDesordreCreateButton.textContent = "Annuler les modifications";
  polygonRepresentativePoint.hidden = false;
  polygonRepresentativeX.value = coordinate(properties.x, 2);
  polygonRepresentativeY.value = coordinate(properties.y, 2);
  polygonRepresentativeLongitude.value = coordinate(properties.longitude, 6);
  polygonRepresentativeLatitude.value = coordinate(properties.latitude, 6);
  desordreCreateMessage.textContent =
    "Le point représentatif est dérivé et non modifiable.";
  desordreCreateMessage.classList.remove("error");
  setEditorPanelVisible(true);
  setPolygonStyle(polygonEditActive ? "editing" : "selected");
  loadDisorderRelatedObjects(feature);
  syncLegacyEditorPresentation();
}

desordreEditorForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (gpsDesordreTracking?.isActive()) return;
  if (legacyEditorLoading || creationRequestInFlight || lineRequestInFlight || graphicRequestInFlight || editorState.pending) {
    return;
  }
  if (editorState.mode === "edit") { await saveDisorderDraft(); return; }
  if (editorState.mode !== "create") return;
  let payload;
  try {
    payload = buildDesordreCreationPayload();
  } catch (error) {
    desordreCreateMessage.textContent = error.message;
    desordreCreateMessage.classList.add("error");
    return;
  }
  finishEditorMapInteraction({ restoreFocus: false });
  creationRequestInFlight = true;
  syncLegacyEditorPresentation();
  submitDesordreCreateButton.disabled = true;
  cancelDesordreCreateButton.disabled = true;
  closeEditorButton.disabled = true;
  desordreCreateMessage.textContent = "Création et relecture PostgreSQL…";
  desordreCreateMessage.classList.remove("error");
  const generation = legacyEditorGeneration;
  const runtime = window.BusinessRuntime;
  try {
    let feature;
    try {
      feature = await fetchJson("/api/desordres", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      console.error("Création du désordre impossible", error);
      desordreCreateMessage.textContent = error.message;
      desordreCreateMessage.classList.add("error");
      return;
    }
    if (generation !== legacyEditorGeneration || runtime !== window.BusinessRuntime || runtime?.isActive?.() === false) return;
    const createdId = featureIdentifier(feature);
    publishConfirmedMutation("desordres", "create", createdId ? [createdId] : null, undefined, feature);
    try {
      clearDesordreDraft();
      const layer = addCreatedDesordreToMap(feature);
      requestedDesordreId = feature.properties.id;
      prepareDisorderEditorForEdit(feature.geometry.type, feature.properties.id);
      editorTabs.hidden = false;
      observationsLoadedFor = null;
      observationsListDirty = true;
      observationsList.replaceChildren();
      if (feature.geometry.type === "Point") {
        activePointLayer = layer;
        renderPointServerFeature(feature);
      } else if (feature.geometry.type === "LineString") {
        activeLineLayer = layer;
        renderLineServerFeature(feature);
      } else renderPolygonServerFeature(feature, layer);


      syncLegacyEditorPresentation();
      showEditorTab("general");

    } catch (refreshError) {
      console.error("Actualisation locale du désordre impossible", refreshError);
      desordreCreateMessage.textContent =
        "Désordre créé. L’actualisation locale est incomplète.";
      desordreCreateMessage.classList.add("error");
    }
  } finally {
    creationRequestInFlight = false;
    syncLegacyEditorPresentation();
    submitDesordreCreateButton.disabled = false;
    cancelDesordreCreateButton.disabled = false;
    closeEditorButton.disabled = false;
  }
});

function generalEditInProgress() {
  return lineEditActive || graphicEditActive || polygonEditActive
    || selectedDesordreMode() !== "map"
    || (editorState.geometryType === "Point" && textFieldsChanged());
}

function sameIdentifierList(left, right) {
  const normalizedLeft = (left || []).map(String).sort();
  const normalizedRight = (right || []).map(String).sort();
  return normalizedLeft.length === normalizedRight.length
    && normalizedLeft.every((value, index) => value === normalizedRight[index]);
}

function desordreAttributeDraftModified() {
  const properties = lastServerFeature?.properties;
  if (!properties || editorState.objectType !== "desordre") return false;
  const polygon = editorState.geometryType === "Polygon";
  const currentValues = polygon ? {
    designation: desordreCreateDesignation.value,
    commentaire: desordreCreateCommentaire.value,
    lieu_dit: desordreCreateLieuDit.value,
    type_desordre_id: desordreCreateTypeReference.value,
    categorie_desordre_id: desordreCreateCategorieReference.value,
    cote_id: desordreCreateCote.value,
    position_id: desordreCreatePosition.value,
    source_id: desordreCreateSource.value,
    date_debut: desordreCreateDateDebut.value,
    date_fin: desordreCreateDateFin.value,
    valid: desordreCreateValid.checked,
    troncon_ids: selectedValues(desordreCreateTroncons),
  } : {
    designation: disorderFields.designation.value,
    commentaire: disorderFields.commentaire.value,
    lieu_dit: disorderFields.lieuDit.value,
    type_desordre_id: disorderFields.type.value,
    categorie_desordre_id: disorderFields.categorie.value,
    cote_id: disorderFields.cote.value,
    position_id: disorderFields.position.value,
    source_id: disorderFields.source.value,
    date_debut: disorderFields.dateDebut.value,
    date_fin: disorderFields.dateFin.value,
    valid: disorderFields.valid.checked,
    troncon_ids: editorState.geometryType === "Point"
      ? [pointEditTroncon.value].filter(Boolean) : selectedValues(lineEditTroncons),
  };
  return Object.entries(currentValues).some(([key, value]) => {
    if (key === "troncon_ids") return !sameIdentifierList(value, properties[key]);
    if (key === "valid") return Boolean(value) !== Boolean(properties[key]);
    return inputText(value) !== inputText(properties[key]);
  });
}

function desordreEditorHasDraft() {
  if (editorState.objectType !== "desordre") return false;
  return entityEditor.hasDraft({
    active: gpsDesordreTracking?.isActive() || graphicEditActive || lineEditActive || polygonEditActive
      || Boolean(editorMapInteraction) || selectedDesordreMode() !== "map",
    dirty: Boolean(desordreSpatialDraft) || desordreAttributeDraftModified(),
    pending: editorState.pending
      || graphicRequestInFlight || lineRequestInFlight || creationRequestInFlight,
  });
}

function getMapEditorState() {
  if (heritageAttributeEditor.getState().open) return heritageAttributeEditor.getState();
  if (activeBusinessEditor) return {
    open: false, resource: null, id: null, editing: false,
    stale: false, conflict: false, baseVersion: null,
  };
  const resource = {
    systeme: "systemes", digue: "digues", troncon: "troncons", desordre: "desordres",
  }[editorState.objectType] || null;
  return {
    open: !editorPanel.hidden && Boolean(resource),
    resource,
    id: editorState.objectId == null ? null : String(editorState.objectId),
    editing: editorState.mode === "create" || desordreEditorHasDraft(),
    stale: mapEditorStale,
    conflict: editorState.conflict,
    baseVersion: lastServerFeature?.properties?.version ?? null,
  };
}

function replayPendingMapEntityRefresh() {
  const resource = {
    systeme: "systemes", digue: "digues", troncon: "troncons", desordre: "desordres",
  }[editorState.objectType];
  const identifier = String(editorState.objectId || "");
  const state = getMapEditorState();
  if (!resource || !identifier || (state.open && state.editing)) return false;
  return mapFeatureStore.flushDeferred(resource, identifier).catch((error) => {
    console.error("Relecture cartographique différée impossible", error);
    return false;
  });
}

function queuePendingMapEntityRefresh() {
  Promise.resolve().then(replayPendingMapEntityRefresh);
}

function markMapEditorStale() {
  if (heritageAttributeEditor.getState().open) return heritageAttributeEditor.markStale();
  mapEditorStale = true;

  const message = ["systeme", "digue", "troncon"].includes(editorState.objectType)
    ? heritageCreateMessage
    : editorState.geometryType === "LineString"
      ? lineEditorMessage
      : editorState.geometryType === "Polygon" ? desordreCreateMessage : editorMessage;
  message.textContent =
    "Cet objet a été modifié ailleurs. Votre saisie locale est conservée.";
  message.classList.remove("error");
}

function observationDraftModified() {
  return Boolean(currentObservation) && (
    observationConflict
    || Object.keys(ObservationForm.changes(observationControls, currentObservation)).length > 0
  );
}

async function invalidateMapOpenDetail(mutation) {
  if (heritageAttributeEditor.getState().open) return heritageAttributeEditor.invalidate(mutation);
  if (["troncons-lit", "elements-morphologiques-lit", "digues"].includes(mutation.resource)) {
    // Events intentionally carry no inferred parent. Reload the active Lit detail
    // (and its choices), retaining every draft through the editor session guard.
    await Promise.all([tronconLitEditor.refreshCurrent(), elementMorphologiqueLitEditor.refreshCurrent()]);
  }
  const editor = getMapEditorState();
  if (mutation.resource === "troncons-lit" && editor.open && editor.resource === "digues") {
    markMapEditorStale();
    return true;
  }
  if (editor.open && editor.resource === "desordres"
      && DisorderRelatedObjects.MUTATION_RESOURCES.has(mutation?.resource)) {
    return disorderRelatedObjectsController.invalidate(mutation);
  }
  if (["systemes", "digues", "troncons"].includes(mutation.resource)
      && editor.open && editor.resource === mutation.resource && editor.id
      && (mutation.ids === null || mutation.ids.includes(editor.id))) {
    // Ces fiches Carte ne disposent pas encore d'une route de relecture dédiée.
    // L'arbre et les couches sont à jour ; la vue de détail reste explicitement
    // signalée comme obsolète au lieu de masquer une modification distante.
    markMapEditorStale();
    return true;
  }
  if (!["observations", "photos"].includes(mutation.resource)
      || !lastServerFeature?.properties?.id) return false;
  observationsLoadedFor = null;
  observationsListDirty = true;
  observationsLoadSequence += 1;
  if (observationsTab.hidden) return true;
  if (mutation.resource === "observations" && currentObservation
      && mutation.ids !== null && !mutation.ids.includes(String(currentObservation.id))) {
    return true;
  }
  if (!observationDetailView.hidden && currentObservation) {
    if (observationDraftModified()) {
      observationStale = true;
      observationSuiteMessage.textContent =
        "Cette observation a été modifiée ailleurs. Votre saisie est conservée.";
      return true;
    }
    closePhotoLightbox();
    await openObservation(currentObservation.id, { propagateError: true });
    return true;
  }
  await loadObservations({ propagateError: true });
  return true;
}

function canSelectEditorTab(name) {
  if(name === "observations" && generalEditInProgress()) {
    const messageElement = lineEditActive ? lineEditorMessage : editorMessage;
    messageElement.textContent="Enregistrez ou annulez l’édition en cours avant de consulter les observations.";
    messageElement.classList.add("error");
    return false;
  }
  return true;
}

backToObservationsButton.addEventListener("click", () => {
  observationReadGeneration += 1;
  closePhotoLightbox();
  observationDetailView.hidden = true;
  observationsListView.hidden = false;
  if (observationsListDirty
      || observationsLoadedFor !== String(lastServerFeature?.properties.id || "")) {
    loadObservations();
  }
});

document.querySelector("#add-observation-photo").addEventListener("click", () => {
  if (currentObservation) PhotoMedia.openCreate(currentObservation, applyPhotoResponse);
});
document.querySelector("#edit-photo-metadata").addEventListener("click", () => {
  const photo = currentObservationPhotos[currentPhotoIndex];
  if (photo && currentObservation) { closePhotoLightbox(); PhotoMedia.openEdit(photo, currentObservation, applyPhotoResponse); }
});
document.querySelector("#edit-photo-localisation").addEventListener("click", () => {
  const photo = currentObservationPhotos[currentPhotoIndex];
  if (photo && currentObservation) { closePhotoLightbox(); PhotoLocalisation.open(photo, currentObservation, applyPhotoResponse); }
});
function applyPhotoResponse(photo) {
  if (currentObservation?.id !== photo.observation_id) return;
  currentObservation.photos = [...currentObservationPhotos.filter(item => item.id !== photo.id), photo];
  renderPhotoMetadata(currentObservation.photos);
}
closeLightboxButton.addEventListener("click", closePhotoLightbox);
previousPhotoButton.addEventListener("click", () => navigatePhoto(-1));
nextPhotoButton.addEventListener("click", () => navigatePhoto(1));
photoLightbox.addEventListener("click", (event) => {
  if (event.target === photoLightbox) {
    closePhotoLightbox();
  }
});
document.addEventListener("keydown", (event) => {
  if (photoLightbox.hidden) {
    return;
  }
  if (event.key === "Escape") {
    closePhotoLightbox();
  } else if (event.key === "ArrowLeft") {
    navigatePhoto(-1);
  } else if (event.key === "ArrowRight") {
    navigatePhoto(1);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !territoireModal.hidden) {
    closeTerritoireModal();
  } else if (event.key === "Escape" && !mapPrintModal.hidden) {
    closeMapPrintModal();
  }
});

function updateCoordinateInputs() {
  const family = selectedDesordreMode();
  disorderFields.x.readOnly = family !== "xy";
  disorderFields.y.readOnly = family !== "xy";
  disorderFields.longitude.readOnly = family !== "lonlat";
  disorderFields.latitude.readOnly = family !== "lonlat";
  bornageFields.hidden = family !== "bornage";
  disorderFields.designation.disabled = graphicEditActive;
  disorderFields.commentaire.disabled = graphicEditActive;
  disorderFields.lieuDit.disabled = graphicEditActive;
  disorderFields.type.disabled = graphicEditActive;
  disorderFields.categorie.disabled = graphicEditActive;
  disorderFields.dateDebut.disabled = graphicEditActive;
  disorderFields.dateFin.disabled = graphicEditActive;
  disorderFields.valid.disabled = graphicEditActive;
  pointEditTroncon.disabled = graphicEditActive;
  startMapPositionButton.disabled = family !== "map";
  updateDisorderEditorControls();
}

function clearCoordinateAuthority() {
  Array.from(desordreEditorForm.elements["desordre-mode"]).forEach((radio) => {
    radio.checked = radio.value === "map";
  });
  updateCoordinateInputs();
}

function textFieldsChanged() {
  return initialFormValues && (
    disorderFields.designation.value !== initialFormValues.designation
    || disorderFields.commentaire.value !== initialFormValues.commentaire
    || disorderFields.lieuDit.value !== initialFormValues.lieu_dit
    || disorderFields.type.value !== initialFormValues.type_desordre_id
    || disorderFields.categorie.value !== initialFormValues.categorie_desordre_id
    || disorderFields.dateDebut.value !== initialFormValues.date_debut
    || disorderFields.dateFin.value !== initialFormValues.date_fin
    || disorderFields.valid.checked !== initialFormValues.valid
    || pointEditTroncon.value !== initialFormValues.troncon_id
  );
}

function setGraphicControls(active) {
  graphicEditActive = active;
  MapFormViews.disorder.geometry.setDrawingActive(active);
  startMapPositionButton.hidden = active;
  mapPositionActions.hidden = !active;
  saveButton.disabled = active;
  disorderFields.designation.disabled = active;
  disorderFields.commentaire.disabled = active;
  disorderFields.lieuDit.disabled = active;
  disorderFields.type.disabled = active;
  disorderFields.categorie.disabled = active;
  disorderFields.dateDebut.disabled = active;
  disorderFields.dateFin.disabled = active;
  disorderFields.valid.disabled = active;
  pointEditTroncon.disabled = active;
  Array.from(desordreEditorForm.elements["desordre-mode"]).forEach((radio) => {
    radio.disabled = active || (
      radio.value === "bornage" && !currentReperage?.disponible
    );
  });
  setPointInteractionStyle(activePointLayer, active ? "editing" : "selected");
}

function renderReperage(reperage) {
  currentReperage = reperage || {
    nombre_troncons: 0,
    disponible: false,
    motif_indisponibilite: "Aucun état de repérage disponible.",
    bornes: [],
  };
  reperageFields.borne.replaceChildren();
  const bornes = Array.isArray(currentReperage.bornes)
    ? currentReperage.bornes
    : [];
  bornes.forEach((borne) => {
    const option = document.createElement("option");
    option.value = borne.id;
    option.textContent = borne.libelle_affichage || borne.libelle
      || (showUuid ? borne.id : "Borne");
    reperageFields.borne.append(option);
  });
  if (
    currentReperage.borne_debut_id
    && !bornes.some((borne) => borne.id === currentReperage.borne_debut_id)
  ) {
    const option = document.createElement("option");
    option.value = currentReperage.borne_debut_id;
    option.textContent = text(
      currentReperage.borne_debut_libelle,
      showUuid ? currentReperage.borne_debut_id : "Borne",
    );
    reperageFields.borne.append(option);
  }
  reperageFields.borne.value = inputText(currentReperage.borne_debut_id);
  EntityFormView.setMetreDistance(reperageFields.distance, coordinate(currentReperage.distance_debut_m, 2));
  reperageFields.sens.value = currentReperage.position_debut_relative
    || "SUR_BORNE";
}

function stopGraphicEdit({ restore }) {
  if (!graphicEditActive) {
    return;
  }
  activePointLayer?.dragging?.disable();
  setGraphicControls(false);
  provisionalLatLng = null;
  validateMapPositionButton.disabled = true;
  mapPositionStatus.textContent = "";
  if (restore) {
    refreshEditorMapInteractionFormSnapshot();
    restoreLastServerState();
    restoreEditorMapInteractionFormSnapshot();
  }
}

function renderPointServerFeature(feature) {
  const properties = feature.properties || {};
  lastServerFeature = feature;
  editorState.objectId = properties.id;
  entityEditor.setSnapshot(feature);
  desordreCreateGeometryType.value = "Point";
  disorderFields.id.value = inputText(properties.id);
  disorderFields.designation.value = inputText(properties.designation);
  fillDesordreReferenceOptions(
    properties.type_desordre_id,
    properties.categorie_desordre_id,
    properties.type_desordre_libelle,
    properties.categorie_desordre_libelle,
  );
  fillDesordreAuxiliaryReferences(properties);
  fillTronconSelect(
    pointEditTroncon,
    properties.troncon_ids || [],
    { multiple: false, includeSelectedOutsideFilter: true },
  );
  disorderFields.commentaire.value = inputText(properties.commentaire);
  disorderFields.lieuDit.value = inputText(properties.lieu_dit);
  disorderFields.dateDebut.value = inputText(properties.date_debut);
  disorderFields.dateFin.value = inputText(properties.date_fin);
  disorderFields.valid.checked = Boolean(properties.valid);
  disorderFields.x.value = coordinate(properties.x, 2);
  disorderFields.y.value = coordinate(properties.y, 2);
  disorderFields.longitude.value = coordinate(properties.longitude, 6);
  disorderFields.latitude.value = coordinate(properties.latitude, 6);
  renderReperage(properties.reperage);

  initialFormValues = {
    designation: disorderFields.designation.value,
    commentaire: disorderFields.commentaire.value,
    lieu_dit: disorderFields.lieuDit.value,
    type_desordre_id: disorderFields.type.value,
    categorie_desordre_id: disorderFields.categorie.value,
    cote_id: disorderFields.cote.value,
    position_id: disorderFields.position.value,
    source_id: disorderFields.source.value,
    date_debut: disorderFields.dateDebut.value,
    date_fin: disorderFields.dateFin.value,
    valid: disorderFields.valid.checked,
    troncon_id: pointEditTroncon.value,
    x: disorderFields.x.value,
    y: disorderFields.y.value,
    longitude: disorderFields.longitude.value,
    latitude: disorderFields.latitude.value,
  };
  clearCoordinateAuthority();
  desordreCreateIdField.hidden = false;
  desordreCreateGeometryType.disabled = true;
  submitDesordreCreateButton.textContent = "Enregistrer";
  cancelDesordreCreateButton.textContent = "Annuler les modifications";
  renderDisorderModeChoices(Boolean(properties.reperage?.disponible));
  updateDisorderEditorControls();
  editorMessage.textContent = "";
  editorMessage.classList.remove("error");
  loadDisorderRelatedObjects(feature);
  syncLegacyEditorPresentation();
}

function fillDisorderLineCoordinates() { fillDisorderCoordinates(); }
function renderLineServerFeature(feature) {
  const properties = feature.properties || {};
  lastServerFeature = feature;
  editorState.objectId = properties.id;
  entityEditor.setSnapshot(feature);
  disorderFields.id.value = inputText(properties.id);
  disorderFields.designation.value = inputText(properties.designation);
  fillDesordreReferenceOptions(
    properties.type_desordre_id,
    properties.categorie_desordre_id,
    properties.type_desordre_libelle,
    properties.categorie_desordre_libelle,
  );
  fillDesordreAuxiliaryReferences(properties);
  disorderFields.commentaire.value = inputText(properties.commentaire);
  disorderFields.lieuDit.value = inputText(properties.lieu_dit);
  disorderFields.dateDebut.value = inputText(properties.date_debut);
  disorderFields.dateFin.value = inputText(properties.date_fin);
  disorderFields.valid.checked = Boolean(properties.valid);
  fillTronconSelect(
    lineEditTroncons,
    properties.troncon_ids || [],
    { includeSelectedOutsideFilter: true },
  );
  disorderFields.geometryType.value = feature.geometry.type;

  const crs = lineEndpointsCrs.value || businessCRS?.code || "";
  if (crs === "EPSG:4326") {
    lineStart1.value = coordinate(properties.debut_longitude, 6);
    lineStart2.value = coordinate(properties.debut_latitude, 6);
    lineEnd1.value = coordinate(properties.fin_longitude, 6);
    lineEnd2.value = coordinate(properties.fin_latitude, 6);
  } else {
    lineStart1.value = coordinate(properties.debut_x, 2);
    lineStart2.value = coordinate(properties.debut_y, 2);
    lineEnd1.value = coordinate(properties.fin_x, 2);
    lineEnd2.value = coordinate(properties.fin_y, 2);
  }
  fillLineDisorderBornage(properties.reperage || {});
  initialLineReperageValues = {
    borneStart: lineBorneStart.value,
    distanceStart: lineDistanceStart.value,
    senseStart: lineSenseStart.value,
    borneEnd: lineBorneEnd.value,
    distanceEnd: lineDistanceEnd.value,
    senseEnd: lineSenseEnd.value,
  };
  desordreCreateIdField.hidden = false;
  desordreCreateGeometryType.disabled = true;
  submitDesordreCreateButton.textContent = "Enregistrer";
  cancelDesordreCreateButton.textContent = "Annuler les modifications";
  renderDisorderModeChoices(Boolean(properties.reperage?.disponible));
  updateDisorderEditorControls();
  lineEditorMessage.textContent = "";
  lineEditorMessage.classList.remove("error");
  loadDisorderRelatedObjects(feature);
  syncLegacyEditorPresentation();
}

function lineBornageDraftModified() {
  return initialLineReperageValues && (
    lineBorneStart.value !== initialLineReperageValues.borneStart
    || lineDistanceStart.value !== initialLineReperageValues.distanceStart
    || lineSenseStart.value !== initialLineReperageValues.senseStart
    || lineBorneEnd.value !== initialLineReperageValues.borneEnd
    || lineDistanceEnd.value !== initialLineReperageValues.distanceEnd
    || lineSenseEnd.value !== initialLineReperageValues.senseEnd
  );
}

function fillLineDisorderBornage(reperage = {}) {
  fillBorneSelect(lineBorneStart, reperage.bornes || [], reperage.borne_debut_id);
  fillBorneSelect(lineBorneEnd, reperage.bornes || [], reperage.borne_fin_id);
  EntityFormView.setMetreDistance(lineDistanceStart, coordinate(reperage.distance_debut_m, 2));
  EntityFormView.setMetreDistance(lineDistanceEnd, coordinate(reperage.distance_fin_m, 2));
  lineSenseStart.value = reperage.position_debut_relative || "SUR_BORNE";
  lineSenseEnd.value = reperage.position_fin_relative || "SUR_BORNE";
}
lineEditTroncons.addEventListener("change", () => {
  if (editorState.mode !== "edit" || !["Point", "LineString"].includes(editorState.geometryType)) return;
  const reperage = lastServerFeature?.properties?.reperage;
  if (canonicalDisorderBornageAvailable()) {
    if (editorState.geometryType === "Point") renderReperage(reperage);
    else fillLineDisorderBornage(reperage);
  } else {
    const lostBornage = selectedDesordreMode() === "bornage"
      || editorState.geometryType === "LineString" && lineBornageDraftModified();
    if (selectedDesordreMode() === "bornage") cancelGpsCandidate();
    clearDisorderBornage();
    if (lostBornage) MapFormViews.disorder.geometry.diagnostics.textContent =
      "Le bornage saisi concernait le tronçon enregistré. Enregistrez le nouveau rattachement avant de renseigner ses bornes.";
  }
  renderDisorderModeChoices(Boolean(reperage?.disponible));
  updateDisorderEditorControls();
});

function selectedValues(select) {
  return RelationSelector.values(select);
}

function updatePointLayer(feature) {
  if (!activePointLayer || feature.geometry?.type !== "Point") {
    return;
  }
  const [longitude, latitude] = feature.geometry.coordinates;
  activePointLayer.setLatLng([latitude, longitude]);
  activePointLayer.feature = feature;
  activePointLayer.setIcon(createDesordrePointIcon(feature));
  setPointInteractionStyle(activePointLayer, graphicEditActive ? "editing" : "selected");
  applyIndividualDesordreVisibility();
}

function setPointInteractionStyle(layer, mode = "default") {
  if (!layer?._icon) return;
  layer._icon.classList.toggle("is-selected", mode === "selected");
  layer._icon.classList.toggle("position-editing", mode === "editing");
}

function clearSelectedPoint() {
  if (activePointLayer) setPointInteractionStyle(activePointLayer);
  activePointLayer?.dragging?.disable();
  activePointLayer = null;
}

function setLineStyle(mode) {
  if (!activeLineLayer) {
    return;
  }
  activeLineLayer.setStyle(DisorderUrgencyStyle.pathStyle(
    activeLineLayer.feature,
    mode === "editing" ? "editing" : "selected",
  ));
  activeLineLayer.bringToFront();
}

function clearSelectedLine() {
  if (selectedLineLayer && desordreLineLayer && !lineEditActive) {
    desordreLineLayer.resetStyle(selectedLineLayer);
  }
  selectedLineLayer = null;
  activeLineLayer = null;
}

function updateLineLayer(feature) {
  if (!activeLineLayer || feature.geometry?.type !== "LineString") {
    return;
  }
  const latLngs = feature.geometry.coordinates.map(
    ([longitude, latitude]) => [latitude, longitude],
  );
  activeLineLayer.setLatLngs(latLngs);
  activeLineLayer.feature = feature;
  applyIndividualDesordreVisibility();
  setLineStyle("selected");
}

function updatePolygonLayer(feature) {
  if (!activePolygonLayer || feature.geometry?.type !== "Polygon") return;
  const rings = feature.geometry.coordinates.map((ring) => ring
    .slice(0, -1)
    .map(([longitude, latitude]) => [latitude, longitude]));
  activePolygonLayer.setLatLngs(rings);
  activePolygonLayer.feature = feature;
  setPolygonStyle(polygonEditActive ? "editing" : "selected");
  applyIndividualDesordreVisibility();
}

function setPolygonStyle(mode = "selected") {
  if (!activePolygonLayer) return;
  activePolygonLayer.setStyle(DisorderUrgencyStyle.pathStyle(
    activePolygonLayer.feature,
    mode === "editing" ? "editing" : "selected",
  ));
  activePolygonLayer.bringToFront();
}

function clearSelectedPolygon() {
  if (activePolygonLayer && desordrePolygonLayer && !polygonEditActive) {
    desordrePolygonLayer.resetStyle(activePolygonLayer);
  }
  activePolygonLayer = null;
}

function prepareDisorderEditorForEdit(geometryType, objectId) {
  desordreSpatialDraft = null; desordreAcquisitionGeometry = null; disorderSaveIntent = null; disorderNumericBasis = null;
  if (String(editorState.objectId || "") !== String(objectId || "")) {
    disorderRelatedObjectsController.clear();
    observationReadGeneration += 1;
    observationsLoadedFor = null;
    observationsListDirty = true;
    observationsLoadSequence += 1;
  }
  setDisorderEditorState("edit", geometryType, objectId);
  MapFormViews.disorder.geometry.diagnostics.textContent = "";
  desordreEditorForm.reset();
  Array.from(desordreEditorForm.elements).forEach((element) => {
    element.disabled = false;
  });
  desordreEditorForm.elements["desordre-mode"].value = "map";
  desordreCreateGeometryType.value = geometryType;
  desordreCreateGeometryType.disabled = true;
  desordreCreateTroncons.multiple = geometryType !== "Point";
  desordreCreateTroncons.size = geometryType === "Point" ? 1 : 5;
  desordreCreateIdField.hidden = false;
  desordreEditorForm.hidden = false;
  submitDesordreCreateButton.textContent = "Enregistrer";
  cancelDesordreCreateButton.textContent = "Annuler les modifications";
  desordreCreateActions.hidden = false;
  validateDesordreDrawButton.hidden = true;
  updateDisorderEditorControls();
}

function setLineEditControls(active) {
  lineEditActive = active;
  MapFormViews.disorder.geometry.setDrawingActive(active);
  startLineEditButton.hidden = active;
  lineGeometryActions.hidden = !active;
  observationsTabButton.disabled = active;
  setLineStyle(active ? "editing" : "selected");
}

function stopLineEdit({ restore }) {
  if (!lineEditActive) {
    return;
  }
  activeLineLayer?.disableEdit?.();
  setLineEditControls(false);
  validateLineEditButton.disabled = true;
  lineGeometryStatus.textContent = "";
  if (restore && lastServerFeature?.geometry?.type === "LineString") {
    refreshEditorMapInteractionFormSnapshot();
    renderLineServerFeature(lastServerFeature);
    updateLineLayer(lastServerFeature);
    restoreEditorMapInteractionFormSnapshot();
  }
}

async function openPointEditor(id, layer) {
  if (!leaveBusinessEditor()) return;
  const generation = ++legacyEditorGeneration;
  legacyEditorLoading = false;
  if (gpsDesordreTracking?.isActive()) return;
  cancelGpsCandidate();
  if (await window.OfflineRuntime?.consultIfOffline("desordres", id)) return;
  if (editorMapInteractionBlocksObjectOpen()) {
    return;
  }
  if (lineEditActive || lineRequestInFlight) {
    lineEditorMessage.textContent =
      "Terminez ou annulez le dessin en cours avant de changer de désordre.";
    lineEditorMessage.classList.add("error");
    return;
  }
  if (graphicRequestInFlight) {
    editorMessage.textContent = "Attendez la réponse PostgreSQL en cours.";
    editorMessage.classList.add("error");
    return;
  }
  if (graphicEditActive && activePointLayer === layer) {
    return;
  }
  if (graphicEditActive) {
    stopGraphicEdit({ restore: true });
  }
  await Promise.all([
    loadDesordreTypes(), loadDesordreCategories(), loadDesordreTronconOptions(),
    loadDesordreAuxiliaryReferences(),
  ]);
  if (generation !== legacyEditorGeneration) return;
  clearSelectedPoint();
  clearSelectedLine();
  clearSelectedPolygon();
  requestedDesordreId = id;
  prepareDisorderEditorForEdit("Point", id);
  activePointLayer = layer;
  setPointInteractionStyle(layer, "selected");


  editorTabs.hidden = false;
  heritageObjectForm.hidden = true;
  desordreEditorForm.hidden = false;
  observationsLoadedFor = null;
  currentObservationPhotos = [];
  observationsList.replaceChildren();
  observationsMessage.textContent = "";
  closePhotoLightbox();
  showEditorTab("general");
  setEditorPanelVisible(true);
  legacyEditorLoading = true;
  syncLegacyEditorPresentation();
  observationsTabButton.disabled = true;

  editorMessage.textContent = "Chargement…";
  editorMessage.classList.remove("error");
  try {
    const feature = await fetchJson(`/api/desordres/${encodeURIComponent(id)}`);
    if (generation !== legacyEditorGeneration || requestedDesordreId !== id) {
      return;
    }
    if (feature.type !== "Feature" || feature.geometry?.type !== "Point") {
      throw new Error("Réponse ponctuelle invalide.");
    }
    renderPointServerFeature(feature);
    updatePointLayer(feature);

  } catch (error) {
    if (generation !== legacyEditorGeneration) return;
    console.error("Lecture du désordre impossible", error);
    editorMessage.textContent = error.message;
    editorMessage.classList.add("error");
  } finally {
    if (generation === legacyEditorGeneration) {
      legacyEditorLoading = false;
      syncLegacyEditorPresentation();
    }
  }
}

async function openLineEditor(id, layer) {
  if (!leaveBusinessEditor()) return;
  const generation = ++legacyEditorGeneration;
  legacyEditorLoading = false;
  if (gpsDesordreTracking?.isActive()) return;
  cancelGpsCandidate();
  if (await window.OfflineRuntime?.consultIfOffline("desordres", id)) return;
  if (editorMapInteractionBlocksObjectOpen()) {
    return;
  }
  if (lineEditActive || lineRequestInFlight) {
    lineEditorMessage.textContent =
      "Terminez ou annulez le dessin en cours avant de changer de désordre.";
    lineEditorMessage.classList.add("error");
    return;
  }
  if (graphicRequestInFlight) {
    editorMessage.textContent = "Attendez la réponse PostgreSQL en cours.";
    editorMessage.classList.add("error");
    return;
  }
  if (graphicEditActive) {
    stopGraphicEdit({ restore: true });
  }
  await Promise.all([
    loadDesordreTypes(), loadDesordreCategories(), loadDesordreTronconOptions(),
    loadDesordreAuxiliaryReferences(),
  ]);
  if (generation !== legacyEditorGeneration) return;
  clearSelectedPoint();
  clearSelectedLine();
  clearSelectedPolygon();
  requestedDesordreId = id;
  prepareDisorderEditorForEdit("LineString", id);
  activeLineLayer = layer;
  selectedLineLayer = layer;
  setLineStyle("selected");
  observationsLoadedFor = null;
  currentObservationPhotos = [];
  observationsList.replaceChildren();
  observationsMessage.textContent = "";
  closePhotoLightbox();
  showEditorTab("general");


  editorTabs.hidden = false;
  heritageObjectForm.hidden = true;
  desordreEditorForm.hidden = false;
  setEditorPanelVisible(true);
  legacyEditorLoading = true;
  syncLegacyEditorPresentation();
  observationsTabButton.disabled = true;

  lineEditorMessage.textContent = "Chargement…";
  lineEditorMessage.classList.remove("error");
  try {
    const feature = await fetchJson(`/api/desordres/${encodeURIComponent(id)}`);
    if (generation !== legacyEditorGeneration || requestedDesordreId !== id) {
      return;
    }
    if (feature.type !== "Feature" || feature.geometry?.type !== "LineString") {
      throw new Error("Réponse LineString invalide.");
    }
    renderLineServerFeature(feature);
    updateLineLayer(feature);

  } catch (error) {
    if (generation !== legacyEditorGeneration) return;
    console.error("Lecture du désordre LineString impossible", error);
    lineEditorMessage.textContent = error.message;
    lineEditorMessage.classList.add("error");
  } finally {
    if (generation === legacyEditorGeneration) {
      legacyEditorLoading = false;
      syncLegacyEditorPresentation();
    }
  }
}

async function openPolygonEditor(id, _layer) {
  if (!leaveBusinessEditor()) return;
  const generation = ++legacyEditorGeneration;
  legacyEditorLoading = false;
  if (gpsDesordreTracking?.isActive()) return;
  cancelGpsCandidate();
  if (await window.OfflineRuntime?.consultIfOffline("desordres", id)) return;
  if (editorMapInteractionBlocksObjectOpen()) {
    return;
  }
  if (lineEditActive || lineRequestInFlight || graphicRequestInFlight) {
    return;
  }
  if (graphicEditActive) {
    stopGraphicEdit({ restore: true });
  }
  clearSelectedPoint();
  clearSelectedLine();
  clearSelectedPolygon();
  await Promise.all([
    loadHeritageTree(), loadDesordreTypes(), loadDesordreCategories(),
    loadDesordreTronconOptions(), loadDesordreAuxiliaryReferences(),
  ]);
  if (generation !== legacyEditorGeneration) return;
  prepareDisorderEditorForEdit("Polygon", id);
  legacyEditorLoading = true;
  fillDesordreReferenceOptions();
  fillDesordreTronconOptions();
  setEditorPanelVisible(true);
  desordreCreateMessage.textContent = "Chargement…";
  try {
    const feature = await fetchJson(`/api/desordres/${encodeURIComponent(id)}`);
    if (generation !== legacyEditorGeneration) return;
    if (feature.geometry?.type !== "Polygon") {
      throw new Error("Réponse Polygon invalide.");
    }
    renderPolygonServerFeature(feature, _layer);
  } catch (error) {
    if (generation !== legacyEditorGeneration) return;
    desordreCreateMessage.textContent = error.message;
    desordreCreateMessage.classList.add("error");
  } finally {
    if (generation === legacyEditorGeneration) {
      legacyEditorLoading = false;
      syncLegacyEditorPresentation();
    }
  }
}

function restoreLastServerState() {
  desordreSpatialDraft = null;
  entityEditor.setConflict(false);
  if (lastServerFeature?.geometry?.type === "Point") {
    renderPointServerFeature(lastServerFeature);
    updatePointLayer(lastServerFeature);
  } else if (lastServerFeature?.geometry?.type === "LineString") {
    renderLineServerFeature(lastServerFeature);
    updateLineLayer(lastServerFeature);
  }
}

function stopActiveExistingGeometryEdit({ restore }) {
  disorderEditAcquisition?.stop(); disorderEditAcquisition=null;
  let stopped = false;
  if (polygonEditActive) {
    stopPolygonEdit({ restore, finishInteraction: false });
    stopped = true;
  }
  if (lineEditActive) {
    stopLineEdit({ restore });
    stopped = true;
  }
  if (graphicEditActive) {
    stopGraphicEdit({ restore });
    stopped = true;
  }
  return stopped;
}

function changedNullableText(current, initial) {
  return current === initial ? undefined : current || null;
}



function buildPointReperagePayload() {
  if (!currentReperage?.disponible || !canonicalDisorderBornageAvailable()) {
    throw new Error("Le repérage n’est pas disponible pour ce désordre.");
  }
  const distance = Number(reperageFields.distance.value);
  if (!reperageFields.borne.value) {
    throw new Error("Une borne doit être sélectionnée.");
  }
  if (!Number.isFinite(distance) || distance < 0) {
    throw new Error("La distance doit être positive ou nulle.");
  }
  if (
    reperageFields.sens.value === "SUR_BORNE"
    && distance !== 0
  ) {
    throw new Error("La distance doit être nulle pour une position sur borne.");
  }
  return {
    borne_debut_id: reperageFields.borne.value,
    distance_debut_m: distance,
    position_debut_relative: reperageFields.sens.value,
  };
}

Array.from(desordreEditorForm.elements["desordre-mode"]).forEach((radio) => {
  radio.addEventListener("change", (event) => {
    cancelGpsCandidate();
    const geometryType = editorState.geometryType || desordreCreateGeometryType.value;
    if (editorState.mode === "create") {
      if (provisionalDesordreLayer && event.target.value !== "map") {
        desordreEditorForm.elements["desordre-mode"].value = "map";
        desordreCreateMessage.textContent =
          "Annulez explicitement la géométrie cartographique avant de changer de mode.";
        desordreCreateMessage.classList.add("error");
        return;
      }
      updateDisorderEditorControls();
      return;
    }
    updateDisorderEditorControls();
    fillDisorderCoordinates();
  });
});

reperageFields.sens.addEventListener("change", () => {
  if (reperageFields.sens.value === "SUR_BORNE") {
    EntityFormView.setMetreDistance(reperageFields.distance, "0.00");
  }
});

lineEndpointsCrs.addEventListener("change", () => {
  updateLineCoordinateLabels(lineCoordinateEditor, lineEndpointsCrs.value);
  if (lastServerFeature?.geometry?.type === "LineString") fillDisorderLineCoordinates();
});

function buildLineReperagePayload() {
  if (!canonicalDisorderBornageAvailable()) {
    throw new Error("Le bornage nécessite le tronçon compatible enregistré pour ce désordre.");
  }
  const startDistance = Number(lineDistanceStart.value);
  const endDistance = Number(lineDistanceEnd.value);
  if (!lineBorneStart.value || !lineBorneEnd.value
      || !Number.isFinite(startDistance) || !Number.isFinite(endDistance)) {
    throw new Error("Le bornage de début et de fin est obligatoire.");
  }
  return {
    borne_debut_id: lineBorneStart.value,
    distance_debut_m: startDistance,
    position_debut_relative: lineSenseStart.value,
    borne_fin_id: lineBorneEnd.value,
    distance_fin_m: endDistance,
    position_fin_relative: lineSenseEnd.value,
  };
}

[lineSenseStart, lineSenseEnd].forEach((select) => {
  select.addEventListener("change", () => {
    if (select.value === "SUR_BORNE") {
      EntityFormView.setMetreDistance(select === lineSenseStart ? lineDistanceStart : lineDistanceEnd, "0.00");
    }
  });
});

function beginPointPositionEdit({ fromGpsInspection = false } = {}) {
  pointPlacementFromGps=fromGpsInspection;
  if (!fromGpsInspection) cancelGpsCandidate();
  if (selectedDesordreMode() !== "map") {
    editorMessage.textContent =
      "Annulez d’abord le mode d’édition numérique des coordonnées.";
    editorMessage.classList.add("error");
    return false;
  }
  if (!activePointLayer?.dragging) {
    editorMessage.textContent = "Ce marqueur ne peut pas être déplacé.";
    editorMessage.classList.add("error");
    return false;
  }
  desordreAcquisitionGeometry = structuredClone(currentDisorderGeometry());
  provisionalLatLng = null;
  validateMapPositionButton.disabled = true;
  validateMapPositionButton.hidden = !fromGpsInspection;
  validateMapPositionButton.textContent = fromGpsInspection ? "Valider" : "Terminer";
  setGraphicControls(true);
  activePointLayer.dragging.enable();
  mapPositionStatus.textContent =
    "Édition graphique en cours — déplacez le marqueur sélectionné.";
  editorMessage.textContent = "";
  editorMessage.classList.remove("error");
  beginEditorMapInteraction({
    kind: "edit-desordre-Point",
    phase: fromGpsInspection ? "draft" : null,
    replaceGpsInspection: fromGpsInspection,
    instruction: fromGpsInspection ? "Déplacez le point ou cliquez sur la carte, puis validez."
      : "Cliquez pour placer le Point ou déplacez le marqueur.",
    cancel: cancelPointPositionEdit,
    finishAction: validatePointPositionEdit,
    finishLabel: fromGpsInspection ? "Valider" : "Terminer",
    formSnapshot: captureExistingGeometryEditFormSnapshot(),
    returnFocusTarget: startMapPositionButton,
    cancelFocusTarget: startMapPositionButton,
  });
  if(!fromGpsInspection) beginDisorderGeometryAcquisition(activePointLayer,"Point",validatePointPositionEdit);
  return true;
}

startMapPositionButton.addEventListener("click", () => beginPointPositionEdit());

function setProvisionalPointPosition(latlng) {
  provisionalLatLng = latlng;
  activePointLayer.setLatLng(provisionalLatLng);
  disorderFields.longitude.value = coordinate(provisionalLatLng.lng, 6);
  disorderFields.latitude.value = coordinate(provisionalLatLng.lat, 6);
  validateMapPositionButton.disabled = false;
  if (!pointPlacementFromGps) { validatePointPositionEdit(); return; }
  if (editorMapInteraction?.kind === "edit-desordre-Point") {
    setEditorMapInteractionFinishAvailable(true);
    updateEditorMapInteractionInstruction(
      "Position choisie. Validez le déplacement ou annulez.",
    );
  }
  mapPositionStatus.textContent =
    "Position provisoire choisie sur la carte — validez ou annulez.";
}

map.on("click", (event) => {
  if (!graphicEditActive || !activePointLayer || lineEditActive) return;
  // Leaflet n'émet pas ce click après un pan : un tap/clic volontaire reste
  // donc distinct de la navigation tactile normale.
  setProvisionalPointPosition(event.latlng);
});

function cancelPointPositionEdit() { cancelDisorderGeometryEdit(); }

cancelMapPositionButton.addEventListener("click", () => {
  if (editorMapInteraction?.kind === "edit-desordre-Point") {
    cancelEditorMapInteraction();
    return;
  }
  cancelPointPositionEdit();
});

function validatePointPositionEdit() {
  if (!graphicEditActive || !provisionalLatLng) return;
  stageDisorderGeometry({type:"Point",coordinates:[provisionalLatLng.lng,provisionalLatLng.lat]});
  finishDisorderGeometryEdit();
}

validateMapPositionButton.addEventListener("click", validatePointPositionEdit);

map.on("editable:editing", (event) => {
  if (event.layer === provisionalTronconLayer) {
    updateTronconDraftStatus();
    return;
  }
  if (event.layer === provisionalDesordreLayer) {
    updateDesordreDraftStatus();
    return;
  }
  if (polygonEditActive && event.layer === activePolygonLayer) {
    desordreDrawStatus.textContent = "";
    if (editorMapInteraction?.kind === "edit-desordre-Polygon") {
      updateEditorMapInteractionInstruction(
        "Ajustez les sommets, puis terminez.",
      );
    }
    return;
  }
  if (!lineEditActive || event.layer !== activeLineLayer) {
    return;
  }
  validateLineEditButton.disabled = false;
  if (editorMapInteraction?.kind === "edit-desordre-LineString") {
    setEditorMapInteractionFinishAvailable(true);
    updateEditorMapInteractionInstruction(
      "Ajustez les sommets, puis terminez.",
    );
  }
  lineGeometryStatus.textContent = "";
});

startLineEditButton.addEventListener("click", () => {
  if (!activeLineLayer || lastServerFeature?.geometry?.type !== "LineString") {
    return;
  }
  if (typeof activeLineLayer.enableEdit !== "function") {
    lineEditorMessage.textContent =
      "Le module léger d’édition Leaflet n’a pas pu être chargé.";
    lineEditorMessage.classList.add("error");
    return;
  }
  desordreAcquisitionGeometry = structuredClone(currentDisorderGeometry());
  activeLineLayer.enableEdit(map);
  setLineEditControls(true);
  validateLineEditButton.disabled = true;
  lineGeometryStatus.textContent =
    "Édition en cours — déplacez un sommet ou utilisez une poignée intermédiaire.";
  lineEditorMessage.textContent =
    "";
  lineEditorMessage.classList.remove("error");
  beginEditorMapInteraction({
    kind: "edit-desordre-LineString",
    instruction: "Ajustez les sommets, puis terminez.",
    cancel: cancelExistingLineEdit,
    finishAction: validateExistingLineEdit,
    finishLabel: "Terminer",
    formSnapshot: captureExistingGeometryEditFormSnapshot(),
    returnFocusTarget: startLineEditButton,
    cancelFocusTarget: startLineEditButton,
  });
  beginDisorderGeometryAcquisition(activeLineLayer,"LineString",validateExistingLineEdit);

});

function cancelExistingLineEdit() { cancelDisorderGeometryEdit(); }

cancelLineEditButton.addEventListener("click", () => {
  if (editorMapInteraction?.kind === "edit-desordre-LineString") {
    cancelEditorMapInteraction();
    return;
  }
  cancelExistingLineEdit();
});

function validateExistingLineEdit() {
  if (!lineEditActive || !activeLineLayer) return;
  stageDisorderGeometry(activeLineLayer.toGeoJSON(false).geometry);
  finishDisorderGeometryEdit();
}

validateLineEditButton.addEventListener("click", validateExistingLineEdit);



closeEditorButton.addEventListener("click", () => {
  if (activeBusinessEditor) {
    activeBusinessEditor.close();
    return;
  }
  if (!canLeaveLegacyEditor()) return;
  if (editorState.mode === "create") {
    if (!creationRequestInFlight) {
      if (editorState.objectType === "desordre") {
        closeDesordreDraft();
      } else {
        closeHeritageDraft();
      }
    }
    return;
  }
  gpsDesordreTracking?.cancel();
  if (["systeme", "digue", "troncon"].includes(editorState.objectType)) {
    heritageObjectForm.hidden = true;
    setEditorPanelVisible(false);
    entityEditor.setContext({ mode: "edit", objectType: null });
    return;
  }
  if (lineRequestInFlight || graphicRequestInFlight || creationRequestInFlight) {
    desordreCreateMessage.textContent =
      "Attendez la fin de la validation en cours avant de fermer.";
    desordreCreateMessage.classList.add("error");
    return;
  }
  stopActiveExistingGeometryEdit({ restore: true });
  desordreSpatialDraft = null;
  if (lastServerFeature?.geometry?.type === "Polygon") updatePolygonLayer(lastServerFeature);
  else restoreLastServerState();
  clearSelectedPoint();
  disorderRelatedObjectsController.clear();
  setEditorPanelVisible(false);
  requestedDesordreId = null;
  closePhotoLightbox();
  showEditorTab("general");
  clearSelectedLine();
  clearSelectedPolygon();
  queuePendingMapEntityRefresh();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !activeBusinessEditor || editorPanel.hidden
      || document.querySelector("dialog[open]")
      || document.activeElement?.closest('[role="dialog"]')) return;
  event.preventDefault();
  activeBusinessEditor.close();
});
window.addEventListener("digues-app:context-closed", () => {
  legacyEditorGeneration += 1;
  legacyEditorLoading = false;
  disorderRelatedObjectsController.clear();
  activeBusinessEditor?.discard();
});

async function loadMapData() {
  try {
    await loadFrontendConfig();
    const [troncons, desordres, territoire] = await Promise.all([
      fetchGeoJSON("/api/troncons"),
      fetchGeoJSON("/api/desordres"),
      fetchGeoJSON("/api/territoire-administratif").catch(error => {
        if (!window.OfflineRuntime?.networkError(error)) throw error;
        // Technical context is not in the business projection. Offline layers
        // can be consulted without a territorial mask or network basemap.
        return { type: "FeatureCollection", features: [], gpsTerritoryUnknown: true };
      }),
    ]);
    setTerritoireAdministratifState(territoire);

    tronconPrintInventory.clear();
    for (const feature of troncons.features) tronconPrintInventory.set(String(feature.properties.id), feature);
    tronconsGeoJsonLayer = L.geoJSON(troncons, {
      style: TRONCON_STYLE,
      onEachFeature: configureTronconLayer,
    }).addTo(map);

    applyIndividualTronconVisibility();
    updateHeritageVisibilityControls();
    if (selectedHeritageObject?.kind === "Tronçon") {
      highlightTroncon(selectedHeritageObject.item.id);
    }

    const commonOptions = {
      style: DisorderUrgencyStyle.pathStyle,
      pointToLayer(pointFeature, latlng) {
        return createDesordrePointMarker(latlng, pointFeature);
      },
      onEachFeature: configureDesordreLayer,
    };
    const features = desordres.features || [];
    const collection = (type) => ({
      type: "FeatureCollection",
      features: features.filter((feature) => feature.geometry?.type === type),
    });
    desordrePointLayer = L.geoJSON(collection("Point"), commonOptions).addTo(map);
    desordreLineLayer = L.geoJSON(collection("LineString"), commonOptions).addTo(map);
    desordrePolygonLayer = L.geoJSON(collection("Polygon"), commonOptions).addTo(map);
    desordresGeoJsonLayer = L.featureGroup([
      desordrePointLayer, desordreLineLayer, desordrePolygonLayer,
    ]);
    applyIndividualDesordreVisibility();

    const allData = L.featureGroup([
      tronconsGeoJsonLayer,
      desordresGeoJsonLayer,
    ]);
    historicalViewportBounds = allData.getBounds();
    applyTerritoireCartography(historicalViewportBounds);

    clearAppStatus("startup", startupStatusToken);
  } catch (error) {
    console.error("Chargement cartographique impossible", error);
    updateAppStatus(
      "startup",
      startupStatusToken,
      "error",
      error.message,
    );
    // Local cartographic packages can be available without a prepared business
    // projection. Preserve the incoming viewport even when business loading fails.
    restoreInitialMapViewport();
    revealMapAfterInitialViewport();
  }
}

function mapFeatureIdentifier(feature) {
  return feature?.properties?.id;
}

mapFeatureStore.registerResource("troncons", {
  fetchOne: (id) => fetchJson(`/api/troncons/${encodeURIComponent(id)}`),
  fetchCollection: () => fetchGeoJSON("/api/troncons"),
  getId: mapFeatureIdentifier,
  listIds: () => [...tronconLayersById.keys()],
  upsert: upsertTronconOnMap,
  remove: removeTronconFromMap,
});

mapFeatureStore.registerResource("desordres", {
  fetchOne: (id) => fetchJson(`/api/desordres/${encodeURIComponent(id)}`),
  fetchCollection: () => fetchGeoJSON("/api/desordres"),
  getId: mapFeatureIdentifier,
  listIds: () => [...desordreLayersById.keys()],
  upsert: upsertDesordreOnMap,
  remove: removeDesordreFromMap,
  shouldDefer({ id }) {
    const state = getMapEditorState();
    return state.open && state.resource === "desordres"
      && state.id === String(id) && state.editing;
  },
  markStale: markMapEditorStale,
  clearStale() {
    mapEditorStale = false;
    entityEditor.setConflict(false);
  },
});

const observationParentMapInvalidation = ObservationParentMapInvalidation.create({
  fetchObservation: (id) => fetchJson(
    `/api/observations/${encodeURIComponent(id)}`,
  ),
  reloadDisorder: (id, mutation) => mapFeatureStore.reloadMapEntity(
    "desordres", id, mutation,
  ),
});

businessEditorPanel.bindCoordinates(MapFormViews.heritage, {
  canApply: () => editorState.mode === "create" && editorState.objectType === "troncon"
    && !creationRequestInFlight && !editorMapInteraction,
  getGeometry: () => provisionalTronconLayer?.toGeoJSON(false).geometry,
  applyGeometry(geometry) {
    clearTronconDraft();
    setTronconDraft(geometry);
    updateTronconDraftStatus("Extrémités confirmées dans le brouillon.");
    syncLegacyEditorPresentation();
  },
});

const mobilierEditor = MobilierEditor.create({
  map,
  L,
  fetchJson,
  referenceData: ReferenceData,
  entityEditor: EntityEditorShell,
  relationSelector: RelationSelector,
  mutationController: ResourceMutationController,
  concurrency: ResourceConcurrency,
  observationCreate: ObservationCreate,
  openObservation,
  featureStore: mapFeatureStore,
  panel: businessEditorPanel,
});

const equipementMesureEditor = MobilierEditor.create({
  map,
  L,
  fetchJson,
  referenceData: ReferenceData,
  entityEditor: EntityEditorShell,
  relationSelector: RelationSelector,
  mutationController: ResourceMutationController,
  concurrency: ResourceConcurrency,
  observationCreate: ObservationCreate,
  openObservation,
  featureStore: mapFeatureStore,
  panel: businessEditorPanel,
  profile: MobilierForm.profile("equipements_mesure"),
});

const reseauTechniqueEditor = ReseauTechniqueEditor.create({
  map,
  L,
  fetchJson,
  referenceData: ReferenceData,
  entityEditor: EntityEditorShell,
  relationSelector: RelationSelector,
  mutationController: ResourceMutationController,
  concurrency: ResourceConcurrency,
  observationCreate: ObservationCreate,
  openObservation,
  featureStore: mapFeatureStore,
  panel: businessEditorPanel,
});

const ouvrageParticulierEditor = OuvrageParticulierEditor.create({
  map,
  L,
  fetchJson,
  entityEditor: EntityEditorShell,
  relationSelector: RelationSelector,
  mutationController: ResourceMutationController,
  concurrency: ResourceConcurrency,
  observationCreate: ObservationCreate,
  openObservation,
  featureStore: mapFeatureStore,
  panel: businessEditorPanel,
});

const cheminementEditor = CheminementEditor.create({
  map,
  L,
  fetchJson,
  referenceData: ReferenceData,
  entityEditor: EntityEditorShell,
  relationSelector: RelationSelector,
  mutationController: ResourceMutationController,
  concurrency: ResourceConcurrency,
  observationCreate: ObservationCreate,
  openObservation,
  featureStore: mapFeatureStore,
  panel: businessEditorPanel,
});

const tronconLitEditor = LitEditor.create({ classId: "troncons_lit", map, L, fetchJson,
  featureStore: mapFeatureStore, panel: businessEditorPanel });
const elementMorphologiqueLitEditor = LitEditor.create({ classId: "elements_morphologiques_lit", map, L, fetchJson,
  featureStore: mapFeatureStore, panel: businessEditorPanel });
mapCreateActions.register("troncons-lit", () => tronconLitEditor.beginCreate());
mapCreateActions.register("elements-morphologiques-lit", () => elementMorphologiqueLitEditor.beginCreate());

mapCreateActions.register("mobilier", () => mobilierEditor.beginCreate());
mapCreateActions.register("equipement-mesure", () => equipementMesureEditor.beginCreate());
mapCreateActions.register("reseau-technique", () => reseauTechniqueEditor.beginCreate());
mapCreateActions.register("ouvrage-particulier", () => ouvrageParticulierEditor.beginCreate());
mapCreateActions.register("cheminement", () => cheminementEditor.beginCreate());

const mapDataReadyPromise = loadMapData();
if (document.body.dataset.lidarUiEnabled === "true" &&
    window.BusinessRuntime?.context && window.LidarMap) {
  try {
    window.LidarMapController = window.LidarMap.create({ map, L,
      proj4: window.proj4, geotiff: window.GeoTIFF,
      core: window.LidarPrototypeCore, canvas: window.LidarCanvas,
      runtime: window.BusinessRuntime,
      async getPatrimoine({ signal }) {
        await loadHeritageTree();
        const response = await window.BusinessRuntime.fetch("/api/patrimoine/geometries/v1?crs=EPSG:2154",
          { cache: "no-store", signal });
        if (!response.ok) throw new Error(`Emprises patrimoniales : HTTP ${response.status}`);
        return { hierarchy: heritageData, geometries: await response.json() };
      } });
  } catch (error) {
    console.warn("Relief LiDAR indisponible", error);
  }
}
mapDataReadyPromise.then(() => Promise.all([
  mobilierEditor.load(), equipementMesureEditor.load(), reseauTechniqueEditor.load(),
  ouvrageParticulierEditor.load(), cheminementEditor.load(),
  tronconLitEditor.load(), elementMorphologiqueLitEditor.load(),
])).catch((error) => {
  console.error("Chargement des équipements niveau A impossible", error);
});
MapEntityNavigation.register("mobilier", mobilierEditor);
MapEntityNavigation.register("equipements_mesure", equipementMesureEditor);
MapEntityNavigation.register("reseaux_techniques", reseauTechniqueEditor);
MapEntityNavigation.register("ouvrages_particuliers", ouvrageParticulierEditor);
MapEntityNavigation.register("cheminements", cheminementEditor);
MapEntityNavigation.register("troncons_lit", tronconLitEditor);
MapEntityNavigation.register("elements_morphologiques_lit", elementMorphologiqueLitEditor);
for (const resource of ["systemes", "digues", "troncons"]) {
  MapEntityNavigation.register(resource, { openEdit: id => openHeritageEdit(resource, id) });
}

async function afterMapDataReady(callback) {
  await mapDataReadyPromise;
  return callback();
}

MapResourceInvalidators.register({
  mutationController: ResourceMutationController,
  consumers: {
    "heritage-tree": (mutation) => afterMapDataReady(async () => {
      await heritagePanelController.reload({ propagateError: true });
      if (["systemes", "digues"].includes(mutation.resource)) {
        await mapFeatureStore.reloadMapCollection("troncons", mutation);
      }
      applyIndividualTronconVisibility();
      applyIndividualDesordreVisibility();
      updateHeritageVisibilityControls();
    }),
    "relation-options": (mutation) => afterMapDataReady(
      () => invalidateMapRelationOptions(mutation),
    ),
    "map-feature-store": (mutation) => afterMapDataReady(async () => {
      if (mutation.resource === "observations") {
        return observationParentMapInvalidation.invalidate(mutation);
      }
      await mapFeatureStore.invalidate(mutation);
      if (["troncons", "troncons-lit", "zones-stockage", "amenagements-hydrauliques"]
        .includes(mutation.resource)) window.LidarMapController?.invalidatePatrimoine();
      if (mutation.resource === "zones-stockage") {
        await mapFeatureStore.reloadMapCollection("amenagements-hydrauliques", mutation);
      } else if (mutation.resource === "amenagements-hydrauliques") {
        await mapFeatureStore.reloadMapCollection("zones-stockage", mutation);
      }
    }),
    "open-detail": (mutation) => afterMapDataReady(
      () => invalidateMapOpenDetail(mutation),
    ),
    "map-context": () => afterMapDataReady(reloadTerritoireContext),
  },
  onError(error, unit) {
    console.error(`Invalidation Carte impossible (${unit})`, error);
  },
});

window.MapResourceConsumers = Object.freeze({
  currentTerritory: currentTerritoireFeature,
  getEditorState: getMapEditorState,
  invalidateRelationOptions: invalidateMapRelationOptions,
  loadObservations,
  reloadMapCollection: mapFeatureStore.reloadMapCollection,
  reloadMapEntity: mapFeatureStore.reloadMapEntity,
  reloadTerritoryContext: reloadTerritoireContext,
  removeMapEntity: mapFeatureStore.removeMapEntity,
});

window.ResourceEventTransport?.start(mapDataReadyPromise);

weatherLegendToggle?.addEventListener("click", toggleWeatherLegend);

weatherTimelineRange?.addEventListener("input", async () => {
  const input = document.querySelector('[data-layer-toggle="precipitations"]');
  if (!input?.checked) return;
  const requestedIndex = Number(weatherTimelineRange.value);
  const context = prepareWeatherFrontendContext();
  if (!weatherForecastSession) {
    await togglePrecipitationLayer(input);
    return;
  }
  const statusToken = beginAppStatus(
    "weather", "loading", "Chargement des précipitations prévues…",
  );
  try {
    await selectWeatherForecastIndex(requestedIndex, input, context, statusToken);
  } catch (error) {
    if (
      error?.name === "AbortError"
      || !appStatusIsCurrent("weather", statusToken)
    ) return;
    updateAppStatus(
      "weather",
      statusToken,
      "error",
      error.message,
    );
  }
});

layerToggleInputs.forEach((input) => {
  input.addEventListener("change", () => {
    if (input.dataset.layerToggle === "vigicrues") {
      toggleVigicruesLayer(input);
      return;
    }
    if (input.dataset.layerToggle === "precipitations") {
      togglePrecipitationLayer(input);
      return;
    }
    const layers = {
      troncons: tronconsGeoJsonLayer,
      Point: desordrePointLayer,
      LineString: desordreLineLayer,
      Polygon: desordrePolygonLayer,
    };
    const layer = layers[input.dataset.layerToggle];
    if (!layer) return;
    if (input.checked) layer.addTo(map);
    else map.removeLayer(layer);
  });
});

[desordreCreateX,desordreCreateY,desordreCreateLongitude,desordreCreateLatitude].forEach(input =>
  input.addEventListener("input", clearResolvedGeometryTypeWarning));
