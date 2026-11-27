(function (root, factory) {
  const controls = factory(root.DisorderUrgencyStyle);
  if (typeof module === "object" && module.exports) module.exports = controls;
  root.MapLayerControls = controls;
})(typeof globalThis !== "undefined" ? globalThis : this, function (defaultUrgencyStyle) {
  "use strict";

  const DISORDER_URGENCY_LEVELS = Object.freeze([
    Object.freeze({ id: "RefUrgence:4", label: "Urgence 4" }),
    Object.freeze({ id: "RefUrgence:3", label: "Urgence 3" }),
    Object.freeze({ id: "RefUrgence:2", label: "Urgence 2" }),
    Object.freeze({ id: "RefUrgence:1", label: "Urgence 1" }),
    Object.freeze({ id: "RefUrgence:99", label: "Indéfini" }),
    Object.freeze({ id: null, label: "Sans urgence" }),
  ]);

  const CATEGORY_STYLES = Object.freeze({
    troncons: Object.freeze({ color: "#5b6470", fillColor: "#89919a" }),
    "troncons-lit": Object.freeze({ color: "#087f8c", fillColor: "#53b7bc" }),
    "elements-morphologiques-lit": Object.freeze({ color: "#986c33", fillColor: "#c5a56a" }),
    desordres: Object.freeze({ color: "#c92f3c", fillColor: "#df6b75" }),
    mobilier: Object.freeze({ color: "#8a5a2b", fillColor: "#b67b43" }),
    "equipements-mesure": Object.freeze({ color: "#00838f", fillColor: "#36aab3" }),
    "reseaux-techniques": Object.freeze({ color: "#d18b00", fillColor: "#e3aa39" }),
    "ouvrages-particuliers": Object.freeze({ color: "#6f4a8e", fillColor: "#9670b3" }),
    cheminements: Object.freeze({ color: "#7a6f22", fillColor: "#a49a4c" }),
    vegetation: Object.freeze({ color: "#3f7d44", fillColor: "#67a66c" }),
    "amenagements-hydrauliques": Object.freeze({ color: "#1769aa", fillColor: "#4b91c8" }),
    "zones-stockage": Object.freeze({ color: "#1769aa", fillColor: "#4b91c8" }),
    prestations: Object.freeze({ color: "#b04a7f", fillColor: "#ce74a5" }),
  });

  function categoryStyle(category) {
    const colors = CATEGORY_STYLES[category];
    if (!colors) throw new Error(`Catégorie de légende inconnue : ${category}.`);
    const path = Object.freeze({ color: colors.color, weight: 4 });
    return Object.freeze({
      color: colors.color,
      fillColor: colors.fillColor,
      path,
      polygon: category === "zones-stockage"
        ? Object.freeze({ ...path, stroke: false, fillOpacity: 0.4 })
        : path,
      point: Object.freeze({
        color: colors.color,
        fillColor: colors.fillColor,
        fillOpacity: 0.9,
        radius: 7,
        weight: 2,
      }),
    });
  }

  function applyCategoryLegendColor(element, category) {
    const style = categoryStyle(category);
    element.style?.setProperty?.("--legend-category-color", style.color);
    return style;
  }

  function initializeCategoryLegend(rootElement = document) {
    rootElement.querySelectorAll("[data-legend-category]").forEach((element) => {
      applyCategoryLegendColor(element, element.dataset.legendCategory);
    });
  }

  function disorderUrgencyLegendItems(urgencyStyle = defaultUrgencyStyle) {
    if (typeof urgencyStyle?.resolve !== "function") {
      throw new Error("Palette canonique des urgences indisponible.");
    }
    return DISORDER_URGENCY_LEVELS.map(({ id, label }) => {
      const style = urgencyStyle.resolve(id);
      return Object.freeze({
        borderColor: style.borderColor,
        color: style.color,
        id,
        label,
      });
    });
  }

  function createDisclosure({ button, panel, collapsedLabel, expandedLabel }) {
    if (!button || !panel) throw new Error("Contrôle de développement incomplet.");
    let expanded = false;
    function setExpanded(value) {
      expanded = Boolean(value);
      button.setAttribute("aria-expanded", String(expanded));
      button.setAttribute("aria-label", expanded ? expandedLabel : collapsedLabel);
      panel.hidden = !expanded;
      return expanded;
    }
    button.addEventListener("click", () => setExpanded(!expanded));
    setExpanded(false);
    return { isExpanded: () => expanded, setExpanded };
  }

  function createDisorderUrgencyLegend({
    rootElement = document,
    urgencyStyle = defaultUrgencyStyle,
  } = {}) {
    const button = rootElement.querySelector(".legend-disorders-toggle");
    const panel = rootElement.querySelector("#legend-disorder-urgencies");
    if (!button || !panel) throw new Error("Légende d’urgence des désordres incomplète.");
    const items = disorderUrgencyLegendItems(urgencyStyle);
    panel.replaceChildren();
    items.forEach((item) => {
      const row = rootElement.createElement("div");
      row.className = "legend-disorder-urgency-row";
      const swatch = rootElement.createElement("i");
      swatch.className = "legend-disorder-urgency-swatch";
      swatch.setAttribute("aria-hidden", "true");
      swatch.style.setProperty("--legend-urgency-color", item.color);
      swatch.style.setProperty("--legend-urgency-border", item.borderColor);
      const label = rootElement.createElement("span");
      label.className = "legend-disorder-urgency-label";
      label.textContent = item.label;
      row.append(swatch, label);
      panel.append(row);
    });
    const disclosure = createDisclosure({
      button,
      panel,
      collapsedLabel: "Afficher les niveaux d’urgence des désordres",
      expandedLabel: "Masquer les niveaux d’urgence des désordres",
    });
    return { ...disclosure, button, items, panel };
  }

  function createDisorderToggle({ rootElement = document, EventClass = Event } = {}) {
    const master = rootElement.querySelector("[data-disorder-layer-master]");
    const geometryToggles = Array.from(
      rootElement.querySelectorAll("[data-disorder-layer-toggle]"),
    );
    if (!master || geometryToggles.length !== 3) {
      throw new Error("Structure de la légende des désordres incomplète.");
    }

    const apply = () => {
      geometryToggles.forEach((toggle) => {
        toggle.checked = master.checked;
        toggle.dispatchEvent(new EventClass("change", { bubbles: true }));
      });
    };
    master.addEventListener("change", apply);
    return { apply, geometryToggles, master };
  }

  function createBusinessLayerToggle({
    rootElement = document,
    map,
    layer,
    label,
    geometryTypes,
    symbolClass,
    category,
  }) {
    if (!map || !layer || !label || !category
        || !Array.isArray(geometryTypes) || !geometryTypes.length) {
      throw new Error("Configuration de légende métier incomplète.");
    }
    const legend = rootElement.querySelector("#legend-business-layers");
    if (!legend) throw new Error("Légende cartographique introuvable.");

    const toggleLabel = rootElement.createElement("label");
    toggleLabel.className = "legend-business-layer";
    const symbols = geometryTypes.map((geometryType) => {
      const geometryClass = {
        Point: "point",
        LineString: "line",
        Polygon: "polygon",
      }[geometryType];
      if (!geometryClass) throw new Error(`Géométrie de légende inconnue : ${geometryType}.`);
      return `<i class="${geometryClass}"></i>`;
    }).join("");
    toggleLabel.innerHTML = `<input type="checkbox" checked>`
      + `<span class="legend-layer-symbols ${symbolClass}" aria-hidden="true">${symbols}</span>`
      + `<span>${label}</span>`;
    if (toggleLabel.dataset) toggleLabel.dataset.legendCategory = category;
    applyCategoryLegendColor(toggleLabel, category);
    const toggle = toggleLabel.querySelector("input");
    toggle.addEventListener("change", () => {
      if (toggle.checked) layer.addTo(map);
      else map.removeLayer(layer);
    });
    legend.append(toggleLabel);
    return { label: toggleLabel, toggle };
  }

  if (typeof document !== "undefined") {
    initializeCategoryLegend();
    createDisorderToggle();
    createDisorderUrgencyLegend();
  }
  return {
    CATEGORY_STYLES,
    applyCategoryLegendColor,
    categoryStyle,
    createBusinessLayerToggle,
    createDisclosure,
    createDisorderToggle,
    createDisorderUrgencyLegend,
    disorderUrgencyLegendItems,
    initializeCategoryLegend,
  };
});
