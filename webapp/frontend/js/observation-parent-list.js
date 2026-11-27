/* Liste des Observations des éditeurs métier : même rattachement pour lecture et retour. */
(function (root) {
  "use strict";
  const sequences = new WeakMap();

  async function load({ container, parent, path, fetchJson, openObservation, isCurrent }) {
    const id = String(parent.id || "");
    const runtime = root.BusinessRuntime;
    const sequence = (sequences.get(container) || 0) + 1;
    sequences.set(container, sequence);
    const current = () => sequences.get(container) === sequence && isCurrent()
      && runtime === root.BusinessRuntime && (!runtime || runtime.isActive());
    if (!current() || !id) return false;
    container.replaceChildren();
    const notice = document.createElement("p");
    notice.textContent = "Chargement des observations…";
    container.append(notice);
    try {
      const result = await fetchJson(path);
      if (!current()) return false;
      if (result.parent?.resource !== parent.resource || String(result.parent.id) !== id
          || !Array.isArray(result.observations)) {
        throw new Error("Réponse d’observations incohérente.");
      }
      const rows = result.observations.map(observation => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "secondary observation-list-item";
        item.textContent = [observation.date, observation.designation].filter(Boolean).join(" — ") || observation.id;
        const count = document.createElement("small");
        count.textContent = ` — ${observation.photo_count || 0} photo(s)`;
        item.append(count);
        item.addEventListener("click", () => {
          if (!current()) return;
          void openObservation(observation.id, {
            parent: { resource: parent.resource, id },
            isParentCurrent: isCurrent,
            onReturn: () => { if (isCurrent()) void load({ container, parent, path, fetchJson, openObservation, isCurrent }); },
          });
        });
        return item;
      });
      if (!rows.length) {
        notice.textContent = "Aucune observation.";
        return true;
      }
      container.replaceChildren(...rows);
      return true;
    } catch (error) {
      if (current()) notice.textContent = error.message;
      return false;
    }
  }

  root.ObservationParentList = { load };
})(globalThis);
