(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DataViewDefault = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Bounded offline schema, checked against the server catalogue in tests.
  // Online initialization always uses the server's default_view metadata.
  const root = "desordres";
  const identities = [
    ["systemes", "libelle", "Systèmes d’endiguement", "Libellé"],
    ["digues", "libelle", "Digues", "Libellé"],
    ["troncons", "libelle", "Tronçons", "Libellé"],
    ["desordres", "designation", "Désordres", "Désignation"],
  ];
  function definition() {
    return { root, fields: identities.map(([table, field]) => ({ table, field })) };
  }
  function matches(query) {
    return query?.root === root && Array.isArray(query.fields)
      && query.fields.length === identities.length
      && identities.every(([table, field], index) => query.fields[index]?.table === table
        && query.fields[index]?.field === field);
  }
  function catalog() {
    const ids = identities.map(([id]) => id);
    return {
      default_root: root, default_view: definition(), local_only: true,
      roots: identities.map(([id, _field, label]) => ({ id, label })),
      relations: ids.slice(0, -1).map((parent, index) => ({ parent, child: ids[index + 1] })),
      tables: identities.map(([id, field, label, fieldLabel]) => ({
        id, label, compatible_roots: ids,
        fields: [{ id: field, label: fieldLabel, type: "text",
          source_class: id, source_field: field, navigable: true }],
      })),
    };
  }
  function columns() {
    return identities.map(([table, field, label, fieldLabel]) => ({
      key: `${table}.${field}`, label: `${label} — ${fieldLabel}`, type: "text",
      source_class: table, source_field: field, navigable: true,
    }));
  }
  return { definition, matches, catalog, columns };
});
