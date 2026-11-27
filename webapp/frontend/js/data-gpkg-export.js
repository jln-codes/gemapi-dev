(function (root, factory) {
  const dataGeoPackage = factory();
  if (typeof module === "object" && module.exports) module.exports = dataGeoPackage;
  root.DataGeoPackage = dataGeoPackage;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function isPosition(value) {
    return Array.isArray(value) && value.length >= 2
      && Number.isFinite(value[0]) && Number.isFinite(value[1]);
  }

  function isLine(value, minimum = 2) {
    return Array.isArray(value) && value.length >= minimum && value.every(isPosition);
  }

  function isUsableGeometry(geometry) {
    const coordinates = geometry?.coordinates;
    if (geometry?.type === "Point") return isPosition(coordinates);
    if (geometry?.type === "MultiPoint") return isLine(coordinates, 1);
    if (geometry?.type === "LineString") return isLine(coordinates);
    if (geometry?.type === "MultiLineString") {
      return Array.isArray(coordinates) && coordinates.length > 0
        && coordinates.every((line) => isLine(line));
    }
    if (geometry?.type === "Polygon") {
      return Array.isArray(coordinates) && coordinates.length > 0
        && coordinates.every((ring) => isLine(ring, 4));
    }
    if (geometry?.type === "MultiPolygon") {
      return Array.isArray(coordinates) && coordinates.length > 0
        && coordinates.every((polygon) => (
          Array.isArray(polygon) && polygon.length > 0
          && polygon.every((ring) => isLine(ring, 4))
        ));
    }
    return false;
  }

  function hasUsableGeometry(rows, entityClasses = []) {
    const included = new Set(entityClasses || []);
    return (rows || []).some((row) => (row.entities || []).some((entity) => (
      included.has(entity?.class) && isUsableGeometry(entity?.geometry)
    )));
  }

  const GEOGRAPHIC_CLASSES = new Set(["systemes", "troncons", "desordres", "photos"]);

  function hasPotentialGeometry(entityClasses = []) {
    return (entityClasses || []).some((entityClass) => GEOGRAPHIC_CLASSES.has(entityClass));
  }

  function buildExportPayload(dataset) {
    return {
      root: dataset?.root,
      fields: (dataset?.fields || []).map((field) => ({
        table: field.table, field: field.field,
      })),
      hidden_troncon_ids: [...new Set(
        (dataset?.hiddenTronconIds || []).map(String),
      )],
      entity_classes: [...new Set(dataset?.entityClasses || [])],
    };
  }

  function buildCsvPayload(dataset) {
    const payload = buildExportPayload(dataset);
    delete payload.entity_classes;
    return payload;
  }

  function downloadName(response) {
    const disposition = response.headers?.get?.("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    return match?.[1] || "digues-donnees.gpkg";
  }

  function createController({
    button,
    status,
    hideWhenUnavailable = false,
    fetchImpl = (...args) => (globalThis.BusinessRuntime?.fetch || fetch)(...args),
    documentTarget = document,
    urlApi = URL,
  }) {
    let dataset = { columns: [], rows: [] };

    function setDataset(nextDataset) {
      dataset = nextDataset || { columns: [], rows: [] };
      const available = dataset.fields?.length > 0
        && hasPotentialGeometry(dataset.entityClasses);
      button.disabled = !available;
      if (hideWhenUnavailable) button.hidden = !available;
      if (!available) status.textContent = "";
      return available;
    }

    async function exportDataset() {
      if (button.disabled) return;
      button.disabled = true;
      status.textContent = "Création du GeoPackage…";
      try {
        const response = await fetchImpl("/api/data/export-gpkg", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildExportPayload(dataset)),
        });
        if (!response.ok) {
          let payload = null;
          try { payload = await response.json(); } catch (_error) { /* HTTP fallback. */ }
          throw new Error(payload?.detail || "Export GeoPackage impossible.");
        }
        const blob = await response.blob();
        const objectUrl = urlApi.createObjectURL(blob);
        const link = documentTarget.createElement("a");
        link.href = objectUrl;
        link.download = downloadName(response);
        link.hidden = true;
        documentTarget.body.append(link);
        link.click();
        link.remove();
        urlApi.revokeObjectURL(objectUrl);
        status.textContent = "GeoPackage téléchargé.";
      } catch (error) {
        status.textContent = error.message;
      } finally {
        button.disabled = !(dataset.fields?.length > 0
          && hasPotentialGeometry(dataset.entityClasses));
      }
    }

    button.addEventListener("click", exportDataset);
    setDataset(dataset);
    return { exportDataset, setDataset };
  }

  function createCsvController({
    button,
    status,
    fetchImpl = (...args) => (globalThis.BusinessRuntime?.fetch || fetch)(...args),
    documentTarget = document,
    urlApi = URL,
  }) {
    let dataset = { fields: [] };

    function setDataset(nextDataset) {
      dataset = nextDataset || { fields: [] };
      button.disabled = !dataset.fields?.length;
      return !button.disabled;
    }

    async function exportDataset() {
      if (button.disabled) return;
      button.disabled = true;
      status.textContent = "Création du CSV…";
      try {
        const response = await fetchImpl("/api/data/export-csv", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildCsvPayload(dataset)),
        });
        if (!response.ok) {
          let payload = null;
          try { payload = await response.json(); } catch (_error) { /* HTTP fallback. */ }
          throw new Error(payload?.detail || "Export CSV impossible.");
        }
        const blob = await response.blob();
        const objectUrl = urlApi.createObjectURL(blob);
        const link = documentTarget.createElement("a");
        link.href = objectUrl;
        link.download = downloadName(response).replace(/\.gpkg$/i, ".csv");
        link.hidden = true;
        documentTarget.body.append(link);
        link.click();
        link.remove();
        urlApi.revokeObjectURL(objectUrl);
        status.textContent = "CSV téléchargé.";
      } catch (error) {
        status.textContent = error.message;
      } finally {
        button.disabled = !dataset.fields?.length;
      }
    }

    button.addEventListener("click", exportDataset);
    setDataset(dataset);
    return { exportDataset, setDataset };
  }

  return {
    buildCsvPayload, buildExportPayload, createController, createCsvController,
    downloadName, hasPotentialGeometry, hasUsableGeometry, isUsableGeometry,
  };
});
