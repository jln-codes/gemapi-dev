(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DisorderRelatedObjects = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const RESOURCE_ORDER = Object.freeze([
    "troncons",
    "mobilier",
    "equipements_mesure",
    "reseaux_techniques",
    "ouvrages_particuliers",
    "cheminements",
    "vegetation",
    "amenagements_hydrauliques",
  ]);
  const resources = new Set(RESOURCE_ORDER);
  const MUTATION_RESOURCES = new Set([
    "troncons",
    "mobilier",
    "equipements-mesure",
    "reseaux-techniques",
    "ouvrages-particuliers",
    "cheminements",
    "vegetation",
    "amenagements-hydrauliques",
  ]);
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function validItem(item) {
    return item && resources.has(item.resource) && UUID.test(String(item.id || ""))
      && typeof item.category === "string" && item.category.trim()
      && typeof item.label === "string" && item.label.trim();
  }

  function createController({
    container,
    fetchJson,
    onNavigate,
    isCurrent = () => true,
    isRepresented = () => false,
    documentTarget = globalThis.document,
    runtime = () => globalThis.BusinessRuntime,
  } = {}) {
    if (!container || typeof fetchJson !== "function" || typeof onNavigate !== "function") {
      throw new TypeError("Configuration de consultation inverse invalide.");
    }
    let disorderId = null;
    let requestSequence = 0;
    let nextCursor = null;
    let hasMore = false;
    let loading = false;
    let reloadPending = false;
    let items = [];
    let activeRuntime = null;

    const heading = documentTarget.createElement("h2");
    heading.textContent = "Objets associés";
    const status = documentTarget.createElement("p");
    status.className = "field-help disorder-related-objects-status";
    status.setAttribute?.("role", "status");
    status.setAttribute?.("aria-live", "polite");
    const groups = documentTarget.createElement("div");
    groups.className = "disorder-related-object-groups";
    const more = documentTarget.createElement("button");
    more.type = "button";
    more.className = "secondary";
    more.textContent = "Afficher la suite";
    more.hidden = true;
    container.classList?.add("disorder-related-objects");
    container.append(heading, status, groups, more);

    function current(sequence, identifier, selectedRuntime) {
      return sequence === requestSequence && disorderId === identifier
        && runtime() === selectedRuntime && selectedRuntime?.isActive?.() !== false
        && container.isConnected !== false && isCurrent(identifier);
    }

    function render() {
      groups.replaceChildren();
      const visibleItems = items.filter(item => !isRepresented(item));
      const byResource = new Map();
      visibleItems.forEach((item) => {
        if (!byResource.has(item.resource)) byResource.set(item.resource, []);
        byResource.get(item.resource).push(item);
      });
      RESOURCE_ORDER.forEach((resource) => {
        const rows = byResource.get(resource);
        if (!rows?.length) return;
        const section = documentTarget.createElement("section");
        section.className = "disorder-related-object-group";
        const title = documentTarget.createElement("h3");
        title.textContent = rows[0].category;
        const list = documentTarget.createElement("ul");
        rows.forEach((item) => {
          const entry = documentTarget.createElement("li");
          const button = documentTarget.createElement("button");
          button.type = "button";
          button.className = "disorder-related-object-link";
          button.textContent = item.label;
          button.dataset.resource = item.resource;
          button.dataset.id = item.id;
          button.setAttribute?.("aria-label", `Ouvrir ${item.label} — ${item.category}`);
          button.addEventListener("click", async () => {
            if (button.disabled || !isCurrent(disorderId)) return;
            button.disabled = true;
            try {
              await onNavigate(item);
            } catch (error) {
              if (isCurrent(disorderId)) status.textContent = error.message;
            } finally {
              if (button.isConnected) button.disabled = false;
            }
          });
          const identifier = documentTarget.createElement("span");
          identifier.className = "disorder-related-object-id";
          identifier.textContent = item.id;
          entry.append(button, identifier);
          list.append(entry);
        });
        section.append(title, list);
        groups.append(section);
      });
      status.textContent = visibleItems.length
        ? `${visibleItems.length} objet${visibleItems.length > 1 ? "s" : ""} affiché${visibleItems.length > 1 ? "s" : ""}.`
        : hasMore ? "Consultez la suite des objets associés." : "Aucun objet métier associé.";
      container.hidden = !visibleItems.length && !hasMore;
      more.hidden = !hasMore;
      more.disabled = loading;
    }

    async function load({ append = false } = {}) {
      if (!disorderId || loading || (append && !hasMore)) return false;
      const identifier = disorderId;
      const selectedRuntime = activeRuntime;
      const sequence = ++requestSequence;
      loading = true;
      container.setAttribute?.("aria-busy", "true");
      more.disabled = true;
      status.textContent = append ? "Chargement de la suite…" : "Chargement des objets associés…";
      const parameters = new URLSearchParams({ limit: "50" });
      if (append && nextCursor) parameters.set("cursor", nextCursor);
      try {
        const response = await fetchJson(
          `/api/desordres/${encodeURIComponent(identifier)}/objets-associes?${parameters}`,
        );
        if (!current(sequence, identifier, selectedRuntime)) return false;
        if (!response || !Array.isArray(response.items)
            || response.items.some((item) => !validItem(item))
            || typeof response.has_more !== "boolean") {
          throw new Error("Réponse de consultation inverse invalide.");
        }
        const known = new Set((append ? items : []).map((item) => `${item.resource}:${item.id}`));
        items = append ? [...items] : [];
        response.items.forEach((item) => {
          const key = `${item.resource}:${item.id}`;
          if (!known.has(key)) {
            known.add(key);
            items.push(Object.freeze({ ...item, id: String(item.id) }));
          }
        });
        nextCursor = response.next_cursor || null;
        hasMore = response.has_more;
        render();
        return true;
      } catch (error) {
        if (!current(sequence, identifier, selectedRuntime)) return false;
        status.textContent = `Objets associés indisponibles : ${error.message}`;
        more.hidden = !hasMore;
        return false;
      } finally {
        if (current(sequence, identifier, selectedRuntime)) {
          loading = false;
          container.setAttribute?.("aria-busy", "false");
          more.disabled = false;
          if (reloadPending) {
            reloadPending = false;
            void load({ append: false });
          }
        }
      }
    }

    function open(identifier) {
      const normalized = String(identifier || "").trim().toLowerCase();
      if (!UUID.test(normalized)) {
        clear();
        container.hidden = false;
        status.textContent = "Objets associés indisponibles : identifiant de Désordre invalide.";
        return Promise.resolve(false);
      }
      requestSequence += 1;
      disorderId = normalized;
      activeRuntime = runtime();
      nextCursor = null;
      hasMore = false;
      loading = false;
      reloadPending = false;
      items = [];
      container.hidden = false;
      groups.replaceChildren();
      more.hidden = true;
      return load();
    }

    function clear() {
      requestSequence += 1;
      disorderId = null;
      activeRuntime = null;
      nextCursor = null;
      hasMore = false;
      loading = false;
      reloadPending = false;
      items = [];
      groups.replaceChildren();
      status.textContent = "";
      more.hidden = true;
      container.hidden = true;
    }

    function invalidate(mutation) {
      if (!disorderId || !MUTATION_RESOURCES.has(mutation?.resource)) return false;
      if (loading) {
        reloadPending = true;
        return true;
      }
      return load({ append: false });
    }

    more.addEventListener("click", () => load({ append: true }));
    container.hidden = true;
    return Object.freeze({ clear, invalidate, loadMore: () => load({ append: true }), open, refresh: render });
  }

  return { MUTATION_RESOURCES, RESOURCE_ORDER, createController, validItem };
});
