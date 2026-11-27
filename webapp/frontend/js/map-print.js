(function exposeMapPrint(globalScope) {
  "use strict";

  const PAPER_SIZES_MM = Object.freeze({
    A4: Object.freeze([210, 297]),
    A3: Object.freeze([297, 420]),
  });
  const PNG_DPI = 150;
  const TILE_LOAD_TIMEOUT_MS = 60000;
  const PRINT_TILE_CONCURRENCY = 6;
  const PRINT_TILE_RETRY_DELAYS_MS = [250, 500, 1000, 2000, 4000];
  const TRANSIENT_TILE_STATUSES = new Set([429, 500, 502, 503, 504]);
  const PRINT_TILE_MAX_BYTES = 8 * 1024 * 1024;
  const TILE_MIME = Object.freeze({ webp: "image/webp", png: "image/png", jpeg: "image/jpeg" });

  function printTileFormat(tileUrl, declaredFormat) {
    if (declaredFormat === "jpg") declaredFormat = "jpeg";
    const format = declaredFormat || (/\.(webp|png|jpe?g)(?:\?|$)/i.exec(tileUrl)?.[1] || "").toLowerCase();
    const normalized = format === "jpg" ? "jpeg" : format;
    if (!TILE_MIME[normalized]) throw new Error("Format du fond non pris en charge pour l’impression.");
    return normalized;
  }

  async function checkedPrintTile(response, format) {
    const mime = response.headers?.get("content-type")?.split(";")[0].trim().toLowerCase();
    if (mime !== TILE_MIME[format]) throw new Error("Type de tuile du fond invalide.");
    const bytes = await response.arrayBuffer();
    const data = new Uint8Array(bytes);
    const png = [137, 80, 78, 71, 13, 10, 26, 10];
    const valid = format === "png" ? data.length >= 24 && png.every((byte, index) => data[index] === byte)
      : format === "jpeg" ? data.length >= 16 && data[0] === 255 && data[1] === 216 &&
        data[2] === 255 && data.at(-2) === 255 && data.at(-1) === 217
        : data.length >= 12 && String.fromCharCode(...data.slice(0, 4)) === "RIFF" &&
          String.fromCharCode(...data.slice(8, 12)) === "WEBP";
    if (!valid || data.length > PRINT_TILE_MAX_BYTES) throw new Error("Image du fond invalide.");
    return new Blob([bytes], { type: TILE_MIME[format] });
  }

  function paperDimensions(format, orientation) {
    const size = PAPER_SIZES_MM[String(format || "").toUpperCase()];
    if (!size) {
      throw new Error("Format de papier non pris en charge.");
    }
    const portrait = orientation === "portrait";
    if (!portrait && orientation !== "landscape") {
      throw new Error("Orientation non prise en charge.");
    }
    const [shortSide, longSide] = size;
    const widthMm = portrait ? shortSide : longSide;
    const heightMm = portrait ? longSide : shortSide;
    return {
      widthMm,
      heightMm,
      widthPx: Math.round((widthMm / 25.4) * PNG_DPI),
      heightPx: Math.round((heightMm / 25.4) * PNG_DPI),
      aspectRatio: widthMm / heightMm,
    };
  }

  function expandProjectedBounds(bounds, targetAspectRatio) {
    const width = bounds.maxX - bounds.minX;
    const height = bounds.maxY - bounds.minY;
    if (!(width > 0) || !(height > 0) || !(targetAspectRatio > 0)) {
      throw new Error("Emprise cartographique invalide.");
    }
    const centerX = (bounds.minX + bounds.maxX) / 2;
    const centerY = (bounds.minY + bounds.maxY) / 2;
    let expandedWidth = width;
    let expandedHeight = height;
    if (width / height < targetAspectRatio) {
      expandedWidth = height * targetAspectRatio;
    } else {
      expandedHeight = width / targetAspectRatio;
    }
    return {
      minX: centerX - expandedWidth / 2,
      maxX: centerX + expandedWidth / 2,
      minY: centerY - expandedHeight / 2,
      maxY: centerY + expandedHeight / 2,
    };
  }

  function adaptLeafletBounds(L, bounds, targetAspectRatio) {
    if (!bounds?.isValid?.()) {
      throw new Error("La carte ne possède pas d'emprise exportable.");
    }
    const crs = L.CRS.EPSG3857;
    const northWest = crs.latLngToPoint(bounds.getNorthWest(), 0);
    const southEast = crs.latLngToPoint(bounds.getSouthEast(), 0);
    const expanded = expandProjectedBounds({
      minX: northWest.x,
      maxX: southEast.x,
      minY: northWest.y,
      maxY: southEast.y,
    }, targetAspectRatio);
    return L.latLngBounds(
      crs.pointToLatLng(L.point(expanded.minX, expanded.maxY), 0),
      crs.pointToLatLng(L.point(expanded.maxX, expanded.minY), 0),
    );
  }

  function legendItemsForState(state) {
    const items = [];
    if (state.territoryOutline?.length) {
      items.push({ id: "territory", label: "Territoire administratif", symbol: "territory" });
    }
    if (state.troncons?.length) {
      items.push({ id: "troncons", label: "Tronçons", symbol: "troncon", category: "troncons" });
    }
    const undisplayed = new Set((state.tronconsNonRepresentables || []).map(item => item.id));
    if (undisplayed.size) {
      items.push({ id: "troncons-non-representables",
        label: `${undisplayed.size} tronçon(s) avec géométrie non représentable sur cette carte`, symbol: "troncon" });
    }
    if (state.disorderPoints?.length) {
      items.push({ id: "disorder-points", label: "Désordres ponctuels", symbol: "point" });
    }
    if (state.disorderLines?.length) {
      items.push({ id: "disorder-lines", label: "Désordres linéaires", symbol: "desordre-line" });
    }
    if (state.disorderPolygons?.length) {
      items.push({ id: "disorder-polygons", label: "Désordres polygonaux", symbol: "desordre-polygon" });
    }
    if (state.weather?.length) {
      items.push({ id: "weather", label: "Précipitations prévues", symbol: "weather" });
    }
    return items;
  }

  function serializeVisibleLayerGroup(map, group) {
    if (!group || !map.hasLayer(group)) return [];
    const serialized = [];
    const styleProperties = [
      "color", "dashArray", "dashOffset", "fill", "fillColor", "fillOpacity",
      "lineCap", "lineJoin", "opacity", "stroke", "weight",
    ];
    const visit = (layer) => {
      if (typeof layer.eachLayer === "function") {
        layer.eachLayer(visit);
        return;
      }
      if (typeof layer.toGeoJSON !== "function") return;
      const style = {};
      styleProperties.forEach((property) => {
        if (layer.options?.[property] !== undefined) {
          style[property] = layer.options[property];
        }
      });
      serialized.push({ geojson: layer.toGeoJSON(false), style });
    };
    visit(group);
    return serialized;
  }

  function appendLegend(documentObject, container, state) {
    const title = documentObject.createElement("strong");
    title.textContent = "Légende";
    container.append(title);
    legendItemsForState(state).forEach((item) => {
      const row = documentObject.createElement("div");
      row.className = "map-print-legend-row";
      const symbol = documentObject.createElement("i");
      symbol.className = `map-print-symbol ${item.symbol}`;
      if (item.category) {
        globalScope.MapLayerControls?.applyCategoryLegendColor(symbol, item.category);
      }
      const label = documentObject.createElement("span");
      label.textContent = item.label;
      row.append(symbol, label);
      container.append(row);
    });
    if (!state.weather?.length) return;
    const weatherTitle = documentObject.createElement("strong");
    weatherTitle.textContent = "Cumul sur 24 h";
    container.append(weatherTitle);
    if (state.weatherValidTimeLabel) {
      const validTime = documentObject.createElement("span");
      validTime.className = "map-print-weather-date";
      validTime.textContent = state.weatherValidTimeLabel;
      container.append(validTime);
    }
    (state.weatherLegend || []).forEach((item) => {
      const row = documentObject.createElement("div");
      row.className = "map-print-legend-row";
      const symbol = documentObject.createElement("i");
      symbol.className = "map-print-weather-swatch";
      symbol.style.background = item.color;
      const label = documentObject.createElement("span");
      label.textContent = item.label;
      row.append(symbol, label);
      container.append(row);
    });
  }

  function createNorthArrow(documentObject) {
    const north = documentObject.createElement("div");
    north.className = "map-print-north";
    north.setAttribute("aria-label", "Nord");
    const label = documentObject.createElement("strong");
    label.textContent = "N";
    const arrow = documentObject.createElement("span");
    arrow.textContent = "↑";
    north.append(label, arrow);
    return north;
  }

  function niceScaleDistance(maxDistance) {
    const magnitude = 10 ** Math.floor(Math.log10(maxDistance));
    return [5, 2, 1]
      .map((factor) => factor * magnitude)
      .find((distance) => distance <= maxDistance) || magnitude / 10;
  }

  function createScaleBar(documentObject, printMap, widthPx) {
    const maximumWidth = Math.min(260, Math.round(widthPx * 0.22));
    const y = printMap.getSize().y - 30;
    const left = printMap.containerPointToLatLng([30, y]);
    const right = printMap.containerPointToLatLng([30 + maximumWidth, y]);
    const maximumDistance = printMap.distance(left, right);
    const distance = niceScaleDistance(maximumDistance);
    const scale = documentObject.createElement("div");
    scale.className = "map-print-scale";
    scale.style.width = `${Math.max(1, Math.round(maximumWidth * distance / maximumDistance))}px`;
    scale.textContent = distance >= 1000
      ? `${Number((distance / 1000).toPrecision(3))} km`
      : `${Math.round(distance)} m`;
    return scale;
  }

  function addSerializedLayers(L, printMap, pane, layers, pointMarkerFactory = null) {
    (layers || []).forEach(({ geojson, style }) => {
      L.geoJSON(geojson, {
        pane,
        interactive: false,
        style: () => ({ ...style, pane }),
        pointToLayer(_feature, latlng) {
          if (pointMarkerFactory) {
            return pointMarkerFactory(latlng, pane);
          }
          return L.circleMarker(latlng, { pane, interactive: false, ...style });
        },
      }).addTo(printMap);
    });
  }

  function createPrintDesordrePointMarker(L, latlng, pane) {
    return L.circleMarker(latlng, {
      className: "map-print-desordre-point",
      color: "#ffffff",
      fillColor: "#c92f3c",
      fillOpacity: 1,
      interactive: false,
      opacity: 1,
      pane,
      radius: 8,
      stroke: true,
      weight: 2,
    });
  }

  function waitForTiles(tileLayer, signal = null, timeoutMs = TILE_LOAD_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        globalScope.clearTimeout(timeout);
        tileLayer.off?.("load", onLoad);
        tileLayer.off?.("tileerror", onError);
        signal?.removeEventListener("abort", onAbort);
        if (error) reject(error); else resolve();
      };
      const onLoad = () => finish();
      const onError = () => finish(new Error("Une tuile du fond de carte n'a pas pu être chargée pour l'export."));
      const onAbort = () => finish(new Error("Le fond a changé pendant l’impression ; relancez l’export."));
      const timeout = globalScope.setTimeout(() => finish(new Error("Le fond de carte n'a pas pu être chargé à temps.")), timeoutMs);
      tileLayer.on("load", onLoad);
      tileLayer.on("tileerror", onError);
      signal?.addEventListener("abort", onAbort, { once: true });
      if (signal?.aborted) onAbort();
    });
  }

  // Only the export map uses this loader. Fetch exposes the HTTP status that an
  // <img> error event hides, so permanent 400/404 errors are never retried.
  function createPrintTileLayer(L, tileUrl, tileOptions, attribution, runtime = globalScope, tileFormat = null) {
    const format = printTileFormat(tileUrl, tileFormat);
    const layer = L.tileLayer(tileUrl, {
      ...tileOptions,
      attribution,
      crossOrigin: "anonymous",
    });
    const waiting = [];
    const controllers = new Set();
    const objectUrls = new Set();
    let active = 0;
    let stopped = false;

    function pause(ms, signal) {
      return new Promise((resolve, reject) => {
        const onAbort = () => {
          runtime.clearTimeout(timer);
          reject(new Error("Export cartographique interrompu."));
        };
        const timer = runtime.setTimeout(() => {
          signal.removeEventListener("abort", onAbort);
          resolve();
        }, ms);
        signal.addEventListener("abort", onAbort, { once: true });
        if (signal.aborted) onAbort();
      });
    }

    async function load(task) {
      const controller = new AbortController();
      controllers.add(controller);
      try {
        const url = layer.getTileUrl(task.coords);
        for (let attempt = 0; ; attempt++) {
          const response = await (runtime.BusinessRuntime?.fetch || runtime.fetch)(url, {
            mode: "cors", credentials: "same-origin", signal: controller.signal,
          });
          if (!response.ok) {
            if (TRANSIENT_TILE_STATUSES.has(response.status) &&
                attempt < PRINT_TILE_RETRY_DELAYS_MS.length) {
              await pause(PRINT_TILE_RETRY_DELAYS_MS[attempt], controller.signal);
              continue;
            }
            throw new Error(`Tuile du fond indisponible (HTTP ${response.status}).`);
          }
          const blob = await checkedPrintTile(response, format);
          if (stopped) throw new Error("Export cartographique interrompu.");
          const objectUrl = runtime.URL.createObjectURL(blob);
          objectUrls.add(objectUrl);
          await new Promise((resolve, reject) => {
            task.image.onload = resolve;
            task.image.onerror = () => reject(new Error("Image du fond invalide."));
            task.image.src = objectUrl;
          });
          if (!stopped) task.done(null, task.image);
          return;
        }
      } catch (error) {
        task.done(error, task.image);
      } finally {
        controllers.delete(controller);
        active--;
        pump();
      }
    }

    function pump() {
      while (!stopped && active < PRINT_TILE_CONCURRENCY && waiting.length) {
        active++;
        void load(waiting.shift());
      }
    }

    layer.createTile = (coords, done) => {
      const image = runtime.document.createElement("img");
      image.alt = "";
      image.crossOrigin = "anonymous";
      waiting.push({ coords, done, image });
      pump();
      return image;
    };
    layer.stopPrintTiles = () => {
      stopped = true;
      for (const controller of controllers) controller.abort();
      for (const task of waiting.splice(0)) {
        task.done(new Error("Export cartographique interrompu."), task.image);
      }
      for (const url of objectUrls) runtime.URL.revokeObjectURL(url);
      objectUrls.clear();
    };
    return layer;
  }

  function downloadBlob(documentObject, blob, filename) {
    const url = globalScope.URL.createObjectURL(blob);
    const link = documentObject.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    globalScope.URL.revokeObjectURL(url);
  }

  function timestamp() {
    return new Date().toISOString().replace(/[-:]/g, "").replace(/T/, "-").slice(0, 15);
  }

  async function exportMap({
    L,
    sourceBounds,
    state,
    options,
    tileUrl,
    tileOptions,
    attribution,
    tileFormat,
    signal,
  }) {
    if (typeof globalScope.html2canvas !== "function") {
      throw new Error("Le moteur d'export PNG est indisponible.");
    }
    const dimensions = paperDimensions(options.format, options.orientation);
    const exportBounds = adaptLeafletBounds(L, sourceBounds, dimensions.aspectRatio);
    const documentObject = globalScope.document;
    const composition = documentObject.createElement("div");
    composition.className = "map-print-composition";
    composition.style.width = `${dimensions.widthPx}px`;
    composition.style.height = `${dimensions.heightPx}px`;
    const mapContainer = documentObject.createElement("div");
    mapContainer.className = "map-print-map";
    composition.append(mapContainer);
    documentObject.body.append(composition);

    let printMap = null;
    let tileLayer = null;
    try {
      printMap = L.map(mapContainer, {
        attributionControl: true,
        fadeAnimation: false,
        markerZoomAnimation: false,
        preferCanvas: false,
        zoomAnimation: false,
        zoomControl: false,
      });
      printMap.attributionControl.setPrefix(false);
      [
        ["print-weather", 340],
        ["print-territory-mask", 350],
        ["print-territory-outline", 360],
        ["print-business", 400],
      ].forEach(([name, zIndex]) => {
        printMap.createPane(name);
        printMap.getPane(name).style.zIndex = String(zIndex);
        printMap.getPane(name).style.pointerEvents = "none";
      });
      if (Number.isInteger(tileOptions.minZoom) &&
          printMap.getBoundsZoom(exportBounds, false, [0, 0]) < tileOptions.minZoom) {
        throw new Error("Le fond actif ne couvre pas cette échelle d’impression.");
      }
      tileLayer = createPrintTileLayer(L, tileUrl, tileOptions, attribution, globalScope, tileFormat);
      const tilesReady = waitForTiles(tileLayer, signal);
      // Layer setup can fail before the await below; keep a rejected tile wait handled.
      tilesReady.catch(() => {});
      tileLayer.addTo(printMap);
      addSerializedLayers(L, printMap, "print-weather", state.weather);
      addSerializedLayers(L, printMap, "print-territory-mask", state.territoryMask);
      addSerializedLayers(L, printMap, "print-territory-outline", state.territoryOutline);
      addSerializedLayers(L, printMap, "print-business", state.troncons);
      addSerializedLayers(L, printMap, "print-business", state.disorderLines);
      addSerializedLayers(L, printMap, "print-business", state.disorderPolygons);
      addSerializedLayers(
        L,
        printMap,
        "print-business",
        state.disorderPoints,
        (latlng, pane) => createPrintDesordrePointMarker(L, latlng, pane),
      );
      if (Number.isInteger(tileOptions.maxZoom)) printMap.setMaxZoom(tileOptions.maxZoom);
      printMap.fitBounds(exportBounds, { animate: false, padding: [0, 0] });
      printMap.invalidateSize({ animate: false, pan: false });

      if (options.legend) {
        const legend = documentObject.createElement("div");
        legend.className = "map-print-legend";
        appendLegend(documentObject, legend, state);
        composition.append(legend);
      }
      if (options.north) {
        composition.append(createNorthArrow(documentObject));
      }
      if (options.scale) {
        composition.append(createScaleBar(documentObject, printMap, dimensions.widthPx));
      }

      await tilesReady;
      if (signal?.aborted) throw new Error("Le fond a changé pendant l’impression ; relancez l’export.");
      if (documentObject.fonts?.ready) await documentObject.fonts.ready;
      const canvas = await globalScope.html2canvas(composition, {
        allowTaint: false,
        backgroundColor: "#ffffff",
        logging: false,
        scale: 1,
        useCORS: true,
      });
      if (signal?.aborted) throw new Error("Le fond a changé pendant l’impression ; relancez l’export.");
      const filename = `carte-${timestamp()}`;
      if (options.output === "png") {
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
        if (!blob) throw new Error("La génération du PNG a échoué.");
        globalScope.BusinessRuntime?.assertActive();
        downloadBlob(documentObject, blob, `${filename}.png`);
      } else if (options.output === "pdf") {
        const JsPdf = globalScope.jspdf?.jsPDF;
        if (!JsPdf) throw new Error("Le moteur d'export PDF est indisponible.");
        const pdf = new JsPdf({
          compress: true,
          format: options.format.toLowerCase(),
          orientation: options.orientation,
          unit: "mm",
        });
        pdf.addImage(
          canvas.toDataURL("image/png"),
          "PNG",
          0,
          0,
          dimensions.widthMm,
          dimensions.heightMm,
        );
        globalScope.BusinessRuntime?.assertActive();
        pdf.save(`${filename}.pdf`);
      } else {
        throw new Error("Type de sortie non pris en charge.");
      }
      return { dimensions, exportBounds };
    } finally {
      tileLayer?.stopPrintTiles();
      printMap?.remove();
      composition.remove();
    }
  }

  const api = {
    adaptLeafletBounds,
    addSerializedLayers,
    createPrintDesordrePointMarker,
    createPrintTileLayer,
    checkedPrintTile,
    expandProjectedBounds,
    exportMap,
    legendItemsForState,
    paperDimensions,
    printTileFormat,
    serializeVisibleLayerGroup,
    waitForTiles,
  };
  globalScope.MapPrint = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof window !== "undefined" ? window : globalThis));
