(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DisorderUrgencyStyle = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function entry(id, label, color, borderColor, lineColor) {
    return Object.freeze({ id, label, color, borderColor, lineColor });
  }

  const PALETTE = Object.freeze({
    "RefUrgence:4": entry("RefUrgence:4", "Désordre devant être traité de façon urgente", "#c62828", "#7f1d1d", "#b91c1c"),
    "RefUrgence:3": entry("RefUrgence:3", "Désordre devant être traité à court ou moyen terme", "#ef6c00", "#8a3d00", "#c45100"),
    "RefUrgence:2": entry("RefUrgence:2", "Pas de grande urgence", "#f6c90e", "#5f4b00", "#8a6d00"),
    "RefUrgence:1": entry("RefUrgence:1", "Faible urgence", "#2e7d32", "#14532d", "#23712a"),
    "RefUrgence:99": entry("RefUrgence:99", "Indéfini", "#7a7f87", "#41464d", "#666b73"),
  });
  const NO_URGENCY = entry(null, "Sans urgence", "#f8fafc", "#475569", "#475569");

  function resolve(urgencyId) {
    return PALETTE[urgencyId] || NO_URGENCY;
  }

  function resolveFeature(feature) {
    return resolve(feature?.properties?.dernier_niveau_urgence_id ?? null);
  }

  function pathStyle(feature, interaction = "default") {
    const urgency = resolveFeature(feature);
    const selected = interaction === "selected";
    const editing = interaction === "editing";
    return {
      color: urgency.lineColor,
      dashArray: editing ? "7 5" : null,
      fillColor: urgency.color,
      fillOpacity: 0.22,
      opacity: 0.95,
      weight: editing ? 8 : selected ? 7 : 5,
    };
  }

  function pointVariables(feature) {
    const urgency = resolveFeature(feature);
    return Object.freeze({
      "--disorder-urgency-color": urgency.color,
      "--disorder-urgency-border": urgency.borderColor,
    });
  }

  return { NO_URGENCY, PALETTE, pathStyle, pointVariables, resolve, resolveFeature };
});
