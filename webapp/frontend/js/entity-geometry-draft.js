/* Brouillon de dessin cartographique ; les règles métier et les mutations restent dans les éditeurs. */
(function (root, factory) {
  const view = typeof module === "object" && module.exports ? require("./entity-form-view.js") : root.EntityFormView;
  const api = factory(view);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.EntityGeometryDraft = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (EntityFormView) {
  "use strict";

  const MIN_VERTICES = { Point: 1, LineString: 2, Polygon: 3 };
  // Exact EPSG Lambert 2SP parameters, GRS80 (RGF93 v1). EPSG 1671
  // uses a null translation to WGS84 at 1 m accuracy, without a grid.
  // Qualified against PostGIS AND independent EPSG 9802 control points.
  const PROJECTED = Object.freeze({
    2154: "+proj=lcc +lat_0=46.5 +lon_0=3 +lat_1=49 +lat_2=44 +x_0=700000 +y_0=6600000 +ellps=GRS80 +units=m",
    ...Object.fromEntries(Array.from({ length: 9 }, (_, index) => {
      const latitude = 42 + index;
      return [3942 + index, `+proj=lcc +lat_0=${latitude} +lon_0=3 +lat_1=${latitude - 0.75} +lat_2=${latitude + 0.75} +x_0=1700000 +y_0=${1200000 + index * 1000000} +ellps=GRS80 +units=m`];
    })),
  });

  function coordinateLabels(crs) {
    return crs === "EPSG:4326" ? ["Longitude", "Latitude"] : [`X (${crs})`, `Y (${crs})`];
  }

  function readCoordinatePair(values, message = "Renseignez les deux coordonnées.") {
    const coordinates = values.map(Number);
    if (values.some(value => value.trim() === "") || !coordinates.every(Number.isFinite)) {
      throw new Error(message);
    }
    return coordinates;
  }

  function geometryFromCoordinates(type, pairs, original) {
    if (pairs.length !== (type === "LineString" ? 2 : 1)
        || !["Point", "LineString"].includes(type)
        || pairs.some(([lng, lat]) => !Number.isFinite(lng) || !Number.isFinite(lat)
          || Math.abs(lng) > 180 || Math.abs(lat) > 90)) {
      throw new Error("Coordonnées hors du domaine longitude/latitude.");
    }
    if (type === "Point") return { type, coordinates: pairs[0] };
    const middle = original?.type === "LineString" ? original.coordinates.slice(1, -1) : [];
    if (!middle.length && pairs[0].every((value, index) => value === pairs[1][index])) {
      throw new Error("Le début et la fin de la ligne doivent être distincts.");
    }
    return { type, coordinates: [pairs[0], ...middle, pairs[1]] };
  }

  function renderRead({ container, geometry, values = {}, request, isCurrent = () => true }) {
    const documentTarget = container.ownerDocument;
    container.replaceChildren();
    const heading = documentTarget.createElement("h2");
    heading.textContent = "Coordonnées et repérage";
    container.append(heading);
    const fields = documentTarget.createElement("dl");
    fields.className = "data-entity-detail-fields";
    container.append(fields);
    const add = (label, value) => {
      const row = documentTarget.createElement("div");
      const term = documentTarget.createElement("dt"), output = documentTarget.createElement("dd");
      term.textContent = label; output.textContent = String(value ?? "").trim() || "Non renseigné";
      row.append(term, output); fields.append(row);
      return output;
    };
    const points = [];
    function vertices(coordinates, path = []) {
      if (!Array.isArray(coordinates)) return;
      if (coordinates.length >= 2 && coordinates.every(Number.isFinite)) {
        points.push({ coordinates, label: path.length ? `Sommet ${path.join(".")}` : "Position" });
      } else coordinates.forEach((part, index) => vertices(part, [...path, index + 1]));
    }
    vertices(geometry?.coordinates);
    if (!points.length) add("Coordonnées", geometry ? "Géométrie sans coordonnées affichables" : "Géométrie non renseignée");
    const projected = points.map(({ coordinates, label }) => {
      add(`${label} — longitude (EPSG:4326)`, coordinates[0]);
      add(`${label} — latitude (EPSG:4326)`, coordinates[1]);
      if (coordinates.length > 2) add(`${label} — Z`, coordinates[2]);
      return add(`${label} — X/Y (CRS métier)`, "Chargement…");
    });
    const rep = values.reperage;
    if (rep) {
      for (const [key, label] of [
        ["troncon_libelle", "Tronçon de repérage"], ["systeme_reperage_libelle", "Système de repérage"],
        ["borne_debut_libelle", "Borne de début"], ["distance_debut_m", "Distance de début (m)"],
        ["position_debut_relative", "Position relative de début"], ["pr_debut", "PR de début"],
        ["borne_fin_libelle", "Borne de fin"], ["distance_fin_m", "Distance de fin (m)"],
        ["position_fin_relative", "Position relative de fin"], ["pr_fin", "PR de fin"],
      ]) add(label, rep[key]);
      if (rep.motif_indisponibilite) add("Disponibilité du repérage", rep.motif_indisponibilite);
    }
    if (!points.length) return;
    // Per-detail metadata, guarded like the detail itself: no CRS/cache shared
    // between bases, and no late response written into a newly opened object.
    const live = isCurrent;
    void (async () => {
      try {
        const summary = await request("/api/base-summary");
        if (!live()) return;
        const crs = globalThis.LocalProjection.validateBusinessCRS(summary?.crs);
        const definition = PROJECTED[crs.srid];
        if (!definition || typeof globalThis.proj4 !== "function") throw new Error("Projection indisponible");
        points.forEach(({ coordinates }, index) => {
          const xy = globalThis.proj4("EPSG:4326", definition, coordinates.slice(0, 2));
          projected[index].textContent = `X : ${xy[0].toFixed(3)} · Y : ${xy[1].toFixed(3)} (${crs.code})`;
        });
      } catch (_failure) {
        if (live()) projected.forEach(output => { output.textContent = "Coordonnées projetées indisponibles"; });
      }
    })();
  }

  // Confirmed coordinates join the existing cartographic draft. Before
  // confirmation, the form protects the input as part of the same session.
  function bindCoordinates({ view, getCRS, loadCRS, project, request, getGeometry, canApply, applyGeometry }) {
    const { geometry, form } = view;
    const { coordinateEntry: entry, type, modeChoices, canvas } = geometry;
    const { endpoints, start, end, panel, accept, cancel, status, bornage } = entry;
    const reading = form.ownerDocument.createElement("section");
    reading.dataset.spatialRead = ""; geometry.block.append(reading);
    let readSignature = null, readRuntime = null, readSequence = 0;
    function syncReading() {
      const shape = getGeometry(), runtime = globalThis.BusinessRuntime;
      const signature = JSON.stringify(shape ?? null);
      if (signature === readSignature && runtime === readRuntime) return;
      readSignature = signature; readRuntime = runtime;
      const sequence = ++readSequence;
      renderRead({ container: reading, geometry: shape, request,
        isCurrent: () => sequence === readSequence && runtime === globalThis.BusinessRuntime
          && runtime?.isActive?.() !== false && signature === JSON.stringify(getGeometry() ?? null) });
    }
    const radios = [...modeChoices.values()].map(({ input }) => input);
    let activeMode = "map";
    let activeType = "";
    let writable = false;
    let busy = false;
    let pending = false;
    let basis = null;
    let crsRequested = false;
    let parentSignature = "";
    let referenceId = "";
    let referenceOptions = null;
    let referenceSequence = 0;
    let calculationSequence = 0;
    let calculating = false;
    const fields = endpoints.flat().map(({ input }) => input);
    const selected = () => radios.find(input => input.checked)?.value || "map";
    const currentCRS = () => getCRS();
    const definition = () => PROJECTED[currentCRS()?.srid];
    const supported = () => writable && ["Point", "LineString"].includes(type.value) && canApply();
    const draftBasis = () => JSON.stringify([getGeometry(), activeMode === "xy" ? currentCRS()?.code : null,
      activeMode === "bornage" ? [referenceId, referenceOptions?.systeme_reperage_id] : null]);

    function invalidateBornage() {
      referenceSequence++;
      calculationSequence++;
      calculating = false;
      referenceOptions = null;
      if (activeMode === "bornage") {
        pending = false;
        basis = null;
      }
      for (const controls of bornage.endpoints) {
        controls.borne.replaceChildren();
        EntityFormView.setMetreDistance(controls.distance, "");
        controls.sense.value = "SUR_BORNE";
      }
    }

    function refreshReferences() {
      if (!bornage) return;
      const selectedOptions = [...bornage.parent.selectedOptions].filter(option => option.value);
      const signature = JSON.stringify(selectedOptions.map(option => option.value).sort());
      if (signature !== parentSignature) {
        parentSignature = signature;
        const previous = bornage.referenceInput.value;
        bornage.referenceInput.replaceChildren(new Option("Choisir un tronçon", ""),
          ...selectedOptions.map(option => new Option(option.textContent, option.value)));
        if (selectedOptions.some(option => option.value === previous)) bornage.referenceInput.value = previous;
      }
      bornage.reference.hidden = selectedOptions.length < 2 || !supported();
      if (!supported()) return;
      const next = selectedOptions.length === 1 ? selectedOptions[0].value
        : bornage.referenceInput.value;
      if (next === referenceId) return;
      const hadReference = Boolean(referenceId);
      referenceId = next;
      invalidateBornage();
      if (hadReference) geometry.diagnostics.textContent =
        "Les références du tronçon précédent ont été retirées. Les attributs et la géométrie sont conservés.";
      if (!next) return;
      const sequence = referenceSequence;
      void request(`/api/troncons/${encodeURIComponent(next)}/reperage-options`).then(options => {
        if (sequence !== referenceSequence || next !== referenceId) return;
        if (options.troncon_id !== next || !options.systeme_reperage_id || !options.bornes?.length) return;
        referenceOptions = options;
        for (const controls of bornage.endpoints) controls.borne.replaceChildren(
          ...options.bornes.map(borne => new Option(borne.libelle_affichage || borne.libelle, borne.id)));
        render();
      }, error => {
        if (sequence === referenceSequence) status.textContent = error.message;
      });
    }

    function positions(geometry) {
      if (geometry?.type !== type.value) return [];
      return type.value === "LineString"
        ? [geometry.coordinates[0], geometry.coordinates.at(-1)] : [geometry.coordinates];
    }

    function fill() {
      const projected = activeMode === "xy";
      const labels = coordinateLabels(projected ? currentCRS()?.code : "EPSG:4326");
      const points = positions(getGeometry());
      endpoints.forEach((controls, index) => {
        const point = points[index];
        const coordinates = point && projected ? project("EPSG:4326", definition(), point.slice(0, 2)) : point;
        controls.forEach(({ input, label }, axis) => {
          label.textContent = labels[axis];
          input.value = Number.isFinite(coordinates?.[axis]) ? String(coordinates[axis]) : "";
        });
      });
    }

    function render({ refill = false } = {}) {
      syncReading();
      refreshReferences();
      const allowed = supported();
      geometry.setModeAvailability({
        xy: (allowed && Boolean(definition() && project)) || (pending && activeMode === "xy"),
        lonlat: allowed || (pending && activeMode === "lonlat"),
        ...(bornage ? { bornage: allowed && Boolean(referenceOptions) } : {}),
      }, { locked: busy || calculating });
      const mode = selected();
      const numeric = ["xy", "lonlat"].includes(mode);
      const relative = mode === "bornage";
      const line = type.value === "LineString";
      if (numeric && !pending && (refill || activeMode !== mode || activeType !== type.value)) {
        activeMode = mode;
        fill();
      }
      if (!pending) {
        activeMode = mode;
        activeType = type.value;
      }
      canvas.hidden = mode !== "map";
      panel.hidden = !numeric;
      start.classList.toggle("entity-form__coordinate-pair", !line);
      start.firstElementChild.hidden = !line;
      if (line) start.removeAttribute("aria-label");
      else start.setAttribute("aria-label", "Coordonnées du Point");
      end.hidden = !line;
      for (const { input } of endpoints[0]) input.disabled = !numeric || busy;
      for (const { input } of endpoints[1]) input.disabled = !numeric || !line || busy;
      accept.disabled = !pending || busy;
      cancel.hidden = !pending;
      if (bornage) {
        bornage.panel.hidden = !relative;
        bornage.end.hidden = !line;
        bornage.start.firstElementChild.hidden = !line;
        for (const [index, controls] of bornage.endpoints.entries()) {
          for (const control of Object.values(controls)) control.disabled = !relative || busy || calculating || (index === 1 && !line);
        }
        bornage.confirm.disabled = !pending || busy || calculating;
        bornage.cancel.hidden = !pending;
      }
    }

    function reset() {
      pending = false;
      basis = null;
      crsRequested = false;
      if (bornage) {
        invalidateBornage();
        referenceId = "";
        parentSignature = "";
        bornage.referenceInput.value = "";
      }
      for (const input of fields) input.value = "";
      modeChoices.get("map").input.checked = true;
      activeMode = "map";
      status.textContent = "";
    }

    entry.sync = state => {
      ({ writable, busy } = state);
      render();
      if (!busy && supported() && project && !currentCRS() && !crsRequested) {
        crsRequested = true;
        // This only refreshes availability. The current draft is never reloaded.
        void loadCRS().then(() => render(), () => {});
      }
    };
    entry.hasDraft = () => pending;
    entry.reset = reset;
    form.addEventListener("reset", reset);
    geometry.modeSelector.addEventListener("change", () => {
      if (pending) {
        modeChoices.get(activeMode).input.checked = true;
        status.textContent = "Confirmez ou annulez les coordonnées avant de changer de mode.";
      } else status.textContent = "";
      render();
    });
    type.addEventListener("change", event => {
      if (!pending) return;
      type.value = activeType;
      event.stopImmediatePropagation();
      status.textContent = "Confirmez ou annulez les coordonnées avant de changer le type.";
      render();
    }, true);
    panel.addEventListener("input", () => {
      if (!pending) basis = draftBasis();
      pending = true;
      render();
    });
    cancel.addEventListener("click", () => {
      pending = false;
      basis = null;
      render({ refill: true });
      status.textContent = "";
      form.dispatchEvent(new Event("input", { bubbles: true }));
    });
    accept.addEventListener("click", () => {
      if (!pending || busy || !supported()) return;
      try {
        const original = getGeometry();
        if (basis !== draftBasis()) {
          throw new Error("La géométrie a changé. Annulez la saisie pour relire ses coordonnées.");
        }
        const points = positions(original);
        const pairs = endpoints.slice(0, type.value === "LineString" ? 2 : 1).map((controls, index) => {
          const pair = readCoordinatePair(controls.map(({ input }) => input.value));
          if (activeMode !== "xy") return pair;
          const point = points[index];
          if (point && project("EPSG:4326", definition(), point.slice(0, 2))
            .every((value, axis) => value === pair[axis])) return point;
          return project(definition(), "EPSG:4326", pair);
        });
        if (applyGeometry(geometryFromCoordinates(type.value, pairs, original)) === false) return;
        pending = false;
        basis = null;
        render({ refill: true });
        status.textContent = "";
        form.dispatchEvent(new Event("input", { bubbles: true }));
      } catch (error) {
        status.textContent = error.message;
      }
    });
    if (bornage) {
      bornage.parent.addEventListener("change", () => render());
      bornage.referenceInput.addEventListener("change", () => render());
      bornage.panel.addEventListener("input", () => {
        if (!pending) basis = draftBasis();
        pending = true;
        render();
      });
      bornage.cancel.addEventListener("click", () => {
        calculationSequence++;
        calculating = false;
        pending = false;
        basis = null;
        for (const controls of bornage.endpoints) EntityFormView.setMetreDistance(controls.distance, "");
        render();
        status.textContent = "";
        form.dispatchEvent(new Event("input", { bubbles: true }));
      });
      bornage.confirm.addEventListener("click", async () => {
        if (!pending || busy || calculating || !supported() || !referenceOptions) return;
        const params = new URLSearchParams({ systeme_reperage_id: referenceOptions.systeme_reperage_id });
        const shape = type.value;
        for (const [index, controls] of bornage.endpoints.slice(0, shape === "LineString" ? 2 : 1).entries()) {
          if (!controls.distance.value || !controls.distance.checkValidity()) {
            status.textContent = "Renseignez une distance positive ou nulle.";
            return;
          }
          const end = index === 0 ? "debut" : "fin";
          params.set(`borne_${end}_id`, controls.borne.value);
          params.set(`distance_${end}_m`, controls.distance.value);
          params.set(`position_${end}_relative`, controls.sense.value);
        }
        const sequence = ++calculationSequence;
        const currentReference = referenceId;
        calculating = true;
        render();
        try {
          const result = await request(`/api/troncons/${encodeURIComponent(currentReference)}/geometrie-bornage?${params}`);
          if (sequence !== calculationSequence || !supported()) return;
          if (basis !== draftBasis() || result.troncon_id !== referenceId
              || result.systeme_reperage_id !== referenceOptions?.systeme_reperage_id || result.geometry?.type !== shape) {
            throw new Error("Le contexte du bornage a changé. La saisie est conservée.");
          }
          if (applyGeometry(result.geometry) === false) return;
          pending = false;
          basis = null;
          status.textContent = "";
          form.dispatchEvent(new Event("input", { bubbles: true }));
        } catch (error) {
          if (sequence === calculationSequence) status.textContent = error.message;
        } finally {
          if (sequence === calculationSequence) {
            calculating = false;
            render();
          }
        }
      });
    }
    form.addEventListener("submit", event => {
      if (!pending) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      status.textContent = activeMode === "bornage"
        ? "Confirmez ou annulez le bornage avant d’enregistrer."
        : "Confirmez ou annulez la saisie des coordonnées avant d’enregistrer.";
      (activeMode === "bornage" ? bornage.confirm : accept).focus();
    }, true);
  }

  function readContract(value, resource) {
    if (!value || value.schemaVersion !== 1 || value.resource !== resource
        || !["approved", "compatibility"].includes(value.status)
        || !/^[a-f0-9]{64}$/.test(value.contractVersion || "")
        || !/^[a-f0-9]{64}$/.test(value.modelDigest || "")
        || !Array.isArray(value.allowedTypes) || !value.allowedTypes.length
        || new Set(value.allowedTypes).size !== value.allowedTypes.length
        || value.allowedTypes.some(type => !Object.hasOwn(MIN_VERTICES, type))
        || ["required", "create", "edit", "replace", "convert", "remove"]
          .some(key => typeof value[key] !== "boolean")
        || (value.allowMissingWhenInvalid !== undefined && typeof value.allowMissingWhenInvalid !== "boolean")
        || (value.requiredForValid !== undefined && typeof value.requiredForValid !== "boolean")
        || (value.allowMissingWhenInvalid === true && (value.status !== "approved" || value.requiredForValid !== true))
        || value.validity?.preserveExisting !== true
        || value.validity?.requiresConformingForValid !== true) {
      throw new Error("Le contrat géométrique est indisponible ou incompatible. Rechargez la fiche.");
    }
    return Object.freeze({ ...value, allowedTypes: Object.freeze([...value.allowedTypes]),
      validity: Object.freeze({ ...value.validity }) });
  }

  function geometryFromVertices(type, vertices) {
    if (!Object.hasOwn(MIN_VERTICES, type) || vertices.length < MIN_VERTICES[type]) {
      throw new Error("Type de géométrie ou nombre de sommets invalide.");
    }
    const coordinates = vertices.map(({ lng, lat }) => [Number(lng), Number(lat)]);
    if (type === "Point") return { type, coordinates: coordinates[0] };
    if (type === "LineString") return { type, coordinates };
    return { type, coordinates: [[...coordinates, coordinates[0]]] };
  }

  // Acquisition only: neither this transition nor its callbacks persist a resource.
  // A second click of a dblclick must not reach either a local vertex handler
  // or Leaflet.Editable's mouseup handler.
  const terminalGuards = new WeakMap();
  function beginAcquisition({ map, getType, getVertices, onVertex, finish,
    readDraft, restoreDraft, external = false, blockLayerClicks = !external }) {
    const previous = readDraft?.();
    let active = true;
    const container = map.getContainer?.();
    terminalGuards.get(map)?.();
    const zoom = map.doubleClickZoom;
    const zoomEnabled = zoom?.enabled();
    zoom?.disable();
    if(blockLayerClicks) map.editTools?.blockEvents();
    const minimum = () => MIN_VERTICES[getType()];
    function end() {
      if (!active) return;
      active = false;
      map.off("click", click);
      map.off("dblclick", doubleClick);
      container?.removeEventListener("mousedown", secondClick, true);
      container?.removeEventListener("mouseup", secondClick, true);
      container?.removeEventListener("click", secondClick, true);
      if(blockLayerClicks) map.editTools?.unblockEvents();
      if (zoomEnabled) zoom.enable();
      // Native Editable can finish on the first mouseup over its end vertex.
      // Consume only the trailing dblclick; a fresh first mousedown removes it.
      if (container) {
        const clear = () => {
          container.removeEventListener("dblclick", trailingDoubleClick, true);
          container.removeEventListener("mousedown", freshClick, true);
          terminalGuards.delete(map);
        };
        const trailingDoubleClick = event => { event.preventDefault(); event.stopImmediatePropagation(); clear(); };
        const freshClick = event => { if (event.detail === 1) clear(); };
        container.addEventListener("dblclick", trailingDoubleClick, true);
        container.addEventListener("mousedown", freshClick, true);
        terminalGuards.set(map, clear);
      }
    }
    function complete() {
      if (!active || getVertices().length < minimum()) return false;
      finish();
      return true;
    }
    const touchGesture = event => event?.pointerType === "touch" || event?._simulated
      || event?.sourceCapabilities?.firesTouchEvents
      || container?.ownerDocument?.defaultView?.matchMedia("(pointer: coarse)").matches;
    function secondClick(event) {
      if (!touchGesture(event) && event.detail > 1) { event.preventDefault(); event.stopImmediatePropagation(); }
    }
    function doubleClick(event) {
      if(touchGesture(event.originalEvent)) {
        // Leaflet may emit only a simulated dblclick for the second tap.
        // It is a vertex, never a mandatory touch shortcut for finishing.
        if(!external) click({ ...event, originalEvent: null });
        return;
      }
      event.originalEvent?.preventDefault?.();
      complete();
    }
    function click(event) {
      if (!active || (!touchGesture(event.originalEvent) && event.originalEvent?.detail > 1)) return;
      const vertices = getVertices();
      const same = (a, b) => a && b && a.lat === b.lat && a.lng === b.lng;
      const nearStart = vertices.length >= 3 && map.latLngToContainerPoint
        && map.latLngToContainerPoint(vertices[0]).distanceTo(map.latLngToContainerPoint(event.latlng)) <= 10;
      if (getType() === "Polygon" && (same(vertices[0], event.latlng) || nearStart)) {
        complete(); return;
      }
      if (same(vertices.at(-1), event.latlng)) return;
      onVertex(event);
    }
    container?.addEventListener("mousedown", secondClick, true);
    container?.addEventListener("mouseup", secondClick, true);
    container?.addEventListener("click", secondClick, true);
    if (!external) map.on("click", click);
    map.on("dblclick", doubleClick);
    return { stop: end, finish: complete, cancel() { end(); restoreDraft?.(previous); } };
  }

  function syncDrawingControls(controls, { drawing, type, count = 0, geometry = null }) {
    controls.setDrawingActive?.(drawing);
    if(!drawing && geometry?.coordinates?.length) controls.resolveGeometryError?.(geometry, controls.ownerState);
    controls.draw.hidden = drawing;
    controls.draw.textContent = geometry ? "Modifier" : "Dessiner";
    controls.confirm.textContent = "Terminer";
    controls.confirm.hidden = !drawing || type === "Point";
    controls.confirm.disabled = count < MIN_VERTICES[type];
    if (controls.cancel) controls.cancel.hidden = !drawing;
  }

  function create({ map, L, panel, pathStyle, controls, allowedGeometryTypes,
    contractResource = null, getMode = () => "edit",
    getOriginal, canStart, onDirty, setStatus, getGpsTracking, getOwnerState }) {
    if (!map || !L || !panel || !controls || !getOriginal || !canStart || !onDirty
        || !setStatus || !Array.isArray(allowedGeometryTypes) || (!allowedGeometryTypes.length && !contractResource)
        || allowedGeometryTypes.some(type => !Object.hasOwn(MIN_VERTICES, type))) {
      throw new Error("Configuration du brouillon géométrique incomplète.");
    }
    const { type: typeControl, status, draw, confirm } = controls;
    let selectedType = allowedGeometryTypes[0];
    let vertices = [];
    let draftLayer = null;
    let drawing = false;
    let changed = false;
    let disposed = false;
    let acquisition = null;
    let contract = null;

    const permitted = type => allowedGeometryTypes.includes(type);
    const canAcquire = () => !disposed && (!contractResource || Boolean(contract
      && (getMode() === "create" ? contract.create : contract.edit)
      && (!getOriginal() || (contract.replace
        && (contract.convert || typeControl.value === getOriginal().type))))) && permitted(typeControl.value);
    const minimum = () => MIN_VERTICES[selectedType];
    const draftGeometry = () => vertices.length >= minimum()
      ? geometryFromVertices(selectedType, vertices) : null;

    function updateStatus() {
      if (draftLayer?.dragging) draftLayer.dragging[canStart() ? "enable" : "disable"]();
      const geometry = changed ? draftGeometry() : getOriginal();
      const message = EntityFormView.geometryStatus({ geometry, drawing, count: vertices.length, minimum: minimum() });
      status.textContent = message;
      if (!drawing && geometry && contract && !permitted(geometry.type)) {
        status.textContent = `${geometry.type} existante hors contrat — conservée sans transformation ; correction requise pour la rendre valide.`;
      }
      if (!drawing && geometry && contract && Array.isArray(geometry.coordinates)
          && geometry.coordinates.length === 0) {
        status.textContent = `${geometry.type} vide, non dessinable — conservée sans transformation ; correction requise pour la rendre valide.`;
      }
      if (contractResource) {
        draw.disabled = !canAcquire() || !canStart();
        typeControl.disabled = !contract || !canStart();
      }
      syncDrawingControls({ ...controls, ownerState: getOwnerState?.() }, { drawing, type: selectedType, count: vertices.length, geometry });
      if (drawing) panel.updateGeometryDrawing(message,
        selectedType !== "Point" && vertices.length >= minimum());
    }

    function stop() {
      if (drawing) {
        acquisition?.stop(); acquisition = null;
        panel.finishGeometryDrawing();
      }
      drawing = false;
      getGpsTracking()?.refresh();

      updateStatus();
    }

    function clearLayer() {
      if (draftLayer) map.removeLayer(draftLayer);
      draftLayer = null;
    }

    function redraw() {
      clearLayer();
      if (!vertices.length) return;
      if (selectedType === "Point") {
        const marker = L.marker(vertices[0], { draggable: true }).addTo(map);
        draftLayer = marker;
        marker.on("dragend", () => {
          if (disposed || draftLayer !== marker) return;
          vertices = [marker.getLatLng()];
          changed = true;
          onDirty();
          updateStatus();
        });
      } else if (selectedType === "Polygon" && vertices.length >= 3) {
        draftLayer = L.polygon(vertices, pathStyle).addTo(map);
      } else {
        draftLayer = L.polyline(vertices, pathStyle).addTo(map);
      }
    }

    function captureVertex(event) {
      if (selectedType === "Point") vertices = [event.latlng];
      else vertices.push(event.latlng);
      redraw();
      // A point is complete on its first vertex; consumers must observe the
      // confirmed draft when notified, just as they do for lines/surfaces.
      if (selectedType === "Point") changed = true;
      onDirty();
      if (selectedType === "Point") stop();
      updateStatus();
    }

    function start() {
      if (disposed || !canStart() || !canAcquire()) return;
      if (!permitted(typeControl.value)) {
        typeControl.value = selectedType;
        setStatus("Ce type de géométrie n'est pas autorisé.", true);
        return;
      }
      selectedType = typeControl.value;
      stop();
      const previous = { vertices: vertices.slice(), changed, selectedType };
      clearLayer();
      vertices = [];
      changed = false;
      drawing = true;
      getGpsTracking()?.refresh();
      acquisition = beginAcquisition({ map, getType: () => selectedType, getVertices: () => vertices,
        onVertex: captureVertex, finish: confirmDraft, readDraft: () => previous,
        restoreDraft(before) {
          vertices = before.vertices; changed = before.changed; selectedType = before.selectedType;
          drawing = false; redraw(); panel.finishGeometryDrawing(); onDirty(); updateStatus();
        } });
      panel.beginGeometryDrawing({
        instruction: "Dessinez sur la carte.",
        cancel: cancelDrawing, finish: confirmDraft, returnFocusTarget: draw,
      });

      updateStatus();
    }

    function confirmDraft() {
      if (!drawing) return;
      if (vertices.length < minimum()) {
        setStatus(`Ce dessin exige ${minimum()} sommet(s) minimum.`, true);
        return;
      }
      changed = true;
      onDirty();
      stop();

    }

    function reset() {
      getGpsTracking()?.cancel();
      stop();
      clearLayer();
      vertices = [];
      changed = false;
      updateStatus();
    }

    function chooseOriginalType() {
      selectedType = permitted(getOriginal()?.type)
        ? getOriginal().type : allowedGeometryTypes[0];
      typeControl.value = selectedType;
    }

    function changeType() {
      const requested = typeControl.value;
      if (contract && getOriginal() && !contract.convert && requested !== getOriginal().type) {
        typeControl.value = selectedType;
        setStatus("La conversion de la géométrie est interdite.", true);
        return false;
      }
      if (!permitted(requested)) {
        typeControl.value = selectedType;
        setStatus("Ce type de géométrie n'est pas autorisé.", true);
        return false;
      }
      if (drawing || changed) reset();
      selectedType = requested;
      typeControl.value = requested;
      updateStatus();
      getGpsTracking()?.refresh();
      return true;
    }

    function renderOriginal() {
      chooseOriginalType();
      updateStatus();
      getGpsTracking()?.refresh();
    }

    function acceptGeometry(geometry) {
      if (!canAcquire() || !canStart()) return false;
      if (contract && getOriginal() && !contract.convert && geometry?.type !== getOriginal().type) {
        setStatus("La conversion de la géométrie est interdite.", true);
        return false;
      }
      if (!permitted(geometry?.type)) {
        setStatus("Ce type de géométrie n'est pas autorisé.", true);
        return false;
      }
      const coordinates = geometry.type === "Point" ? [geometry.coordinates]
        : geometry.type === "Polygon" ? geometry.coordinates[0].slice(0, -1)
          : geometry.coordinates;
      if (!Array.isArray(coordinates) || coordinates.length < MIN_VERTICES[geometry.type]) {
        setStatus("Le tracé ne contient pas assez de sommets.", true);
        return false;
      }
      stop();
      selectedType = geometry.type;
      typeControl.value = selectedType;
      vertices = coordinates.map(([lng, lat]) => ({ lng, lat }));
      redraw();
      changed = true;
      onDirty();
      updateStatus();
      return true;
    }

    function dispose() {
      if (disposed) return;
      reset();
      disposed = true;
      controls.cancel?.removeEventListener("click", cancelDrawing);
    }

    function setContract(value) {
      const next = readContract(value, contractResource);
      if (drawing || changed) throw new Error("Terminez ou annulez le brouillon avant de changer le contrat géométrique.");
      contract = next;
      allowedGeometryTypes = next.allowedTypes;
      if (controls.setTypes) controls.setTypes(allowedGeometryTypes);
      else if (typeControl.ownerDocument) {
        typeControl.replaceChildren(...allowedGeometryTypes.map(type => {
          const option = typeControl.ownerDocument.createElement("option");
          option.value = type;
          option.textContent = type;
          return option;
        }));
      }
      controls.setRequirement?.(next.required, next.allowMissingWhenInvalid);
      if (controls.contractStatus) controls.contractStatus.textContent = next.status === "compatibility"
        ? "Configuration actuelle conservée ; contrat géométrique à qualifier."
        : "";
      renderOriginal();
      return next;
    }

    function clearContract() {
      if (!contractResource) return;
      contract = null;
      allowedGeometryTypes = [];
      if (controls.setTypes) controls.setTypes([]);
      else typeControl.replaceChildren?.();
      typeControl.value = "";
      if (controls.contractStatus) controls.contractStatus.textContent = "Chargement du contrat géométrique…";
      updateStatus();
    }

    function validate({ mode = getMode(), valid = true, label = "cet objet" } = {}) {
      if (!contract) throw new Error("Le contrat géométrique n'est pas chargé.");
      if (drawing) throw new Error("Confirmez ou annulez le dessin en cours.");
      const original = getOriginal();
      const geometry = changed ? draftGeometry() : original;
      if ((mode === "create" && !contract.create) || (mode !== "create" && changed && !contract.edit)) {
        throw new Error("La saisie géométrique n'est pas disponible.");
      }
      if (changed && original && !contract.replace) throw new Error("Le remplacement de la géométrie est interdit.");
      if (changed && original && geometry?.type !== original.type && !contract.convert) {
        throw new Error("La conversion de la géométrie est interdite.");
      }
      // Omettre geometry sur un PUT conserve exactement l'état serveur, même
      // historique. Une nouvelle forme interdite n'emprunte jamais cette voie.
      if (mode === "edit" && !changed && valid === false && contract.validity.preserveExisting) return geometry;
      if (!geometry && contract.required && !(valid === false && contract.allowMissingWhenInvalid === true)) throw Object.assign(new Error(`La géométrie de ${label} est obligatoire.`), { geometryCause: "required" });
      if (geometry && !permitted(geometry.type)) {
        throw new Error(`La géométrie de ${label} doit être de type ${allowedGeometryTypes.join(" ou ")}.`);
      }
      if (geometry && Array.isArray(geometry.coordinates) && geometry.coordinates.length === 0) {
        throw new Error(`La géométrie de ${label} est vide.`);
      }
      return geometry;
    }

    function cancelDrawing() {
      if (!canStart()) return;
      acquisition?.cancel(); acquisition = null;
      updateStatus();
    }
    controls.cancel?.addEventListener("click", cancelDrawing);
    typeControl.value = selectedType;
    updateStatus();
    return { start, stop, confirm: confirmDraft, reset, changeType,
      renderOriginal, acceptGeometry, dispose, updateStatus, draftGeometry,
      setContract, clearContract, validate, canAcquire, contractVersion: () => contract?.contractVersion,
      isDrawing: () => drawing, isChanged: () => changed };
  }

  return { projectedCRS: PROJECTED, renderRead, create, beginAcquisition, syncDrawingControls, geometryFromVertices, readContract, bindCoordinates, coordinateLabels,
    readCoordinatePair, geometryFromCoordinates,
    supportedTypes: Object.freeze(Object.keys(MIN_VERTICES)) };
});
