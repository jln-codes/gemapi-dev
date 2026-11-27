"use strict";

const sqlQueryForm = document.querySelector("#sql-query-form");
const sqlQueryEditor = document.querySelector("#sql-query-editor");
const sqlQuerySubmit = document.querySelector("#sql-query-submit");
const sqlQueryStatus = document.querySelector("#sql-query-status");
const sqlPresetSelect = document.querySelector("#sql-preset-select");
const sqlPresetDescription = document.querySelector("#sql-preset-description");
const sqlExportCsv = document.querySelector("#sql-export-csv");
const sqlResults = document.querySelector("#sql-results");
const sqlResultSummary = document.querySelector("#sql-result-summary");
const sqlResultError = document.querySelector("#sql-result-error");
const sqlResultEmpty = document.querySelector("#sql-result-empty");
const sqlResultTableWrapper = document.querySelector("#sql-result-table-wrapper");
const sqlResultTable = document.querySelector("#sql-result-table");
const sqlResultActions = document.querySelector("#sql-result-actions");

let sqlQueryPending = false;
let sqlPresets = new Map();
let sqlPresetsAvailable = false;
let currentSqlResult = null;

function setSqlQueryPending(pending) {
  sqlQueryPending = pending;
  sqlQueryEditor.disabled = pending;
  sqlQuerySubmit.disabled = pending;
  sqlPresetSelect.disabled = pending || !sqlPresetsAvailable;
  sqlQueryForm.setAttribute("aria-busy", String(pending));
  sqlQueryStatus.textContent = pending ? "Exécution…" : "";
}

function clearSqlResult() {
  currentSqlResult = null;
  sqlExportCsv.disabled = true;
  sqlResultActions.hidden = true;
  sqlResultSummary.hidden = true;
  sqlResultError.hidden = true;
  sqlResultEmpty.hidden = true;
  sqlResultTableWrapper.hidden = true;
  sqlResultTable.querySelector("thead").replaceChildren();
  sqlResultTable.querySelector("tbody").replaceChildren();
}

function sqlCellText(value) {
  if (value === null) return "NULL";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function renderSqlResult(result) {
  if (!Array.isArray(result?.columns) || !Array.isArray(result?.rows)) {
    throw new Error("Réponse invalide de l’outil SQL.");
  }
  clearSqlResult();
  currentSqlResult = result;
  sqlExportCsv.disabled = result.rows.length === 0;
  sqlResultActions.hidden = result.rows.length === 0;

  const rowCount = Number.isInteger(result.rowCount) ? result.rowCount : result.rows.length;
  const durationMs = Number.isFinite(result.durationMs) ? result.durationMs : 0;
  sqlResultSummary.textContent = `${rowCount} ligne${rowCount === 1 ? "" : "s"} · ${durationMs} ms${
    result.truncated ? " · résultat tronqué" : ""
  }`;
  sqlResultSummary.hidden = false;

  if (!result.rows.length) {
    sqlResultEmpty.hidden = false;
    return;
  }

  const headerRow = document.createElement("tr");
  result.columns.forEach((column) => {
    const header = document.createElement("th");
    header.scope = "col";
    header.textContent = String(column);
    headerRow.append(header);
  });
  sqlResultTable.querySelector("thead").append(headerRow);

  const body = sqlResultTable.querySelector("tbody");
  result.rows.forEach((row) => {
    const tableRow = document.createElement("tr");
    result.columns.forEach((_column, index) => {
      const cell = document.createElement("td");
      cell.textContent = sqlCellText(Array.isArray(row) ? row[index] : null);
      tableRow.append(cell);
    });
    body.append(tableRow);
  });
  sqlResultTableWrapper.hidden = false;
}

function presetOption(value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  return option;
}

async function loadSqlPresets() {
  sqlPresetsAvailable = false;
  sqlPresetSelect.disabled = true;
  sqlPresetDescription.hidden = true;
  try {
    const response = await (globalThis.BusinessRuntime?.fetch || fetch)("/api/sql/presets");
    if (!response.ok) throw new Error("Les requêtes prédéfinies sont indisponibles.");
    const payload = await response.json();
    if (!Array.isArray(payload)) {
      throw new Error("La réponse des requêtes prédéfinies est invalide.");
    }

    const presets = payload.filter((preset) => (
      typeof preset?.id === "string"
      && typeof preset.label === "string"
      && typeof preset.description === "string"
      && typeof preset.sql === "string"
      && preset.sql.trim()
    ));
    sqlPresets = new Map(presets.map((preset) => [preset.id, preset]));
    sqlPresetSelect.replaceChildren(
      presetOption("", "Choisir une requête…"),
      ...presets.map((preset) => presetOption(preset.id, preset.label)),
    );
    sqlPresetsAvailable = true;
    sqlPresetSelect.disabled = sqlQueryPending;
  } catch (_error) {
    sqlPresets = new Map();
    sqlPresetSelect.replaceChildren(
      presetOption("", "Requêtes prédéfinies indisponibles"),
    );
    sqlPresetDescription.textContent = "Vous pouvez toujours saisir une requête manuellement.";
    sqlPresetDescription.hidden = false;
  }
}

function selectSqlPreset() {
  const preset = sqlPresets.get(sqlPresetSelect.value);
  if (!preset) {
    sqlPresetDescription.hidden = true;
    return;
  }
  sqlQueryEditor.value = preset.sql;
  sqlPresetDescription.textContent = preset.description;
  sqlPresetDescription.hidden = false;
}

function csvCellText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  if (typeof value === "string" && /^\s*[=+\-@]/u.test(text)) {
    // L'apostrophe force Excel et LibreOffice à conserver la cellule comme texte.
    // Elle est placée avant les éventuels espaces initiaux, laissés intacts.
    return `'${text}`;
  }
  return text;
}

function csvEscape(value) {
  return `"${csvCellText(value).replaceAll('"', '""')}"`;
}

function csvContentForResult(result) {
  const lines = [
    result.columns.map(csvEscape).join(";"),
    ...result.rows.map((row) => (
      result.columns.map((_column, index) => (
        csvEscape(Array.isArray(row) ? row[index] : null)
      )).join(";")
    )),
  ];
  return `\uFEFF${lines.join("\r\n")}\r\n`;
}

function sqlExportFilename(now = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `resultats-sql-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${
    pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())
  }.csv`;
}

function exportCurrentSqlResult() {
  if (!currentSqlResult || !currentSqlResult.columns.length) return;
  const blob = new Blob([csvContentForResult(currentSqlResult)], {
    type: "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const download = document.createElement("a");
  download.href = url;
  download.download = sqlExportFilename();
  download.click();
  URL.revokeObjectURL(url);
}

function sqlErrorMessage(payload, fallback) {
  const error = payload?.error;
  if (!error || typeof error.message !== "string" || !error.message.trim()) {
    return fallback;
  }
  return error.message.trim();
}

async function executeSqlQuery() {
  if (sqlQueryPending) return;
  const sql = sqlQueryEditor.value;
  clearSqlResult();
  setSqlQueryPending(true);
  try {
    const response = await (globalThis.BusinessRuntime?.fetch || fetch)("/api/sql/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      // Le message générique reste exploitable si le corps est invalide.
    }
    if (!response.ok) {
      throw new Error(sqlErrorMessage(payload, "La requête SQL a échoué."));
    }
    renderSqlResult(payload);
  } catch (error) {
    sqlResultError.textContent = error.message || "Impossible d’exécuter la requête SQL.";
    sqlResultError.hidden = false;
  } finally {
    setSqlQueryPending(false);
  }
}

sqlQueryForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  executeSqlQuery();
});

sqlQueryEditor?.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key === "Enter") {
    event.preventDefault();
    sqlQueryForm.requestSubmit();
  }
});

sqlPresetSelect?.addEventListener("change", selectSqlPreset);
sqlExportCsv?.addEventListener("click", exportCurrentSqlResult);

if (!globalThis.BusinessRuntime || globalThis.BusinessRuntime.can('sql.read')) loadSqlPresets();
