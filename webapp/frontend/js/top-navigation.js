(function (root, factory) {
  const topNavigation = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = topNavigation;
  }
  root.TopNavigation = topNavigation;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ENTRY_NAMES = ["heritage", "create", "tools", "queries", "ai", "settings"];
  const COMPATIBLE_PAIR = new Set(["queries", "ai"]);

  // Deux outils demandent au moins ~350 px chacun. Le portrait intermédiaire
  // partage l'espace ; le paysage réserve environ un tiers à l'assistant.
  function layoutMode(width, height) {
    if (width < 700) return "single-tool";
    if (width < 840 || (width < 960 && height > width)) return "split-balanced";
    return "split-docked";
  }

  function fixedMenuPosition(anchorRect, menuWidth, viewportWidth, margin = 8) {
    const safeViewportWidth = Math.max(0, Number(viewportWidth) || 0);
    const safeMenuWidth = Math.max(0, Number(menuWidth) || 0);
    const safeMargin = Math.max(0, Number(margin) || 0);
    const maximumLeft = Math.max(
      safeMargin,
      safeViewportWidth - safeMenuWidth - safeMargin,
    );
    return {
      left: Math.min(Math.max(safeMargin, anchorRect.left), maximumLeft),
      top: Math.max(0, anchorRect.bottom),
    };
  }

  function createController({
    entries,
    documentTarget = document,
    onChange = () => {},
    additionalCompatiblePairs = [],
  }) {
    const names = [...ENTRY_NAMES, "editor"].filter((name) => entries[name]);

    const settingsMenu = documentTarget.querySelector?.(".settings-menu");
    function closeSettingsMenu() {
      if (settingsMenu) settingsMenu.open = false;
    }

    const compatiblePairs = [
      COMPATIBLE_PAIR,
      ...additionalCompatiblePairs.map((pair) => new Set(pair)),
    ];
    let mode = "split-docked";
    let sequence = 0;
    const openedAt = {};
    function compatible(a, b) {
      if (a === b) return true;
      if (mode !== "single-tool" && (
        compatiblePairs.some((pair) => pair.has(a) && pair.has(b)) ||
        ([a, b].includes("heritage") && [a, b].includes("editor"))
      )) return true;
      // Les menus sont transitoires : accéder à Créer ne doit pas fermer
      // le patrimoine avant d'ouvrir son éditeur sur un espace partagé.
      return mode !== "single-tool" && [a, b].includes("create") &&
        [a, b].includes("heritage");
    }
    for (const name of names) {
      if (!entries[name] || typeof entries[name].setOpen !== "function") {
        throw new Error(`Configuration de navigation manquante : ${name}`);
      }
    }

    let state = Object.fromEntries(
      names.map((name) => [name, Boolean(entries[name].initialOpen)]),
    );
    const initiallyOpen = names.filter((name) => state[name]);
    if (
      initiallyOpen.length > 1 &&
      !initiallyOpen.every((a) => initiallyOpen.every((b) => compatible(a, b)))
    ) {
      throw new Error("L’état initial de la navigation est incompatible.");
    }

    function applyState(nextState) {
      const changes = names.filter(
        (name) => state[name] !== nextState[name],
      );
      const focusedClosed = changes.some((name) => !nextState[name] &&
        entries[name].panel?.contains(documentTarget.activeElement));
      state = nextState;

      // Fermer avant d'ouvrir évite des états DOM incompatibles transitoires.
      for (const open of [false, true]) {
        for (const name of changes) {
          if (state[name] === open) {
            entries[name].setOpen(open);
          }
        }
      }
      onChange({ ...state }, mode);
      if (focusedClosed) {
        const remaining = names.filter((name) => state[name])
          .sort((a, b) => (openedAt[b] || 0) - (openedAt[a] || 0))[0];
        const entry = entries[remaining];
        const closed = entries[changes.find((name) => !state[name])];
        (entry?.focusTarget || entry?.toggleButton ||
          closed?.toggleButton || closed?.returnFocusTarget)?.focus();
      }
    }

    function close(name) {
      if (!names.includes(name) || !state[name]) {
        return;
      }
      applyState({ ...state, [name]: false });
    }

    function open(name) {
      if (!names.includes(name)) {
        throw new Error(`Entrée de navigation inconnue : ${name}`);
      }
      if (entries[name].canOpen && !entries[name].canOpen()) return;
      openedAt[name] = ++sequence;
      if (state[name]) {
        return;
      }

      const nextState = Object.fromEntries(
        names.map((entryName) => {
          return [entryName, compatible(name, entryName) && state[entryName]];
        }),
      );
      nextState[name] = true;
      applyState(nextState);
    }

    function toggle(name) {
      if (state[name]) {
        close(name);
      } else {
        open(name);
      }
    }

    function setLayout(width, height) {
      const nextMode = layoutMode(width, height);
      if (nextMode === mode) {
        onChange({ ...state }, mode);
        return;
      }
      mode = nextMode;
      const keep = [];
      const next = { ...state };
      names.filter((name) => state[name])
        .sort((a, b) => (openedAt[b] || 0) - (openedAt[a] || 0))
        .forEach((name) => {
          if (keep.every((other) => compatible(name, other))) keep.push(name);
          else next[name] = false;
        });
      applyState(next);
    }

    for (const name of names.filter((entryName) => entryName !== "editor")) {
      const entry = entries[name];
      entry.toggleButton.addEventListener("click", () => {
        closeSettingsMenu();
        if (name !== "create") {
          close("create");
        }
        if (name !== "tools") {
          close("tools");
        }
        toggle(name);
      });
      entry.closeButton?.addEventListener("click", () => close(name));
    }

    documentTarget.addEventListener("click", (event) => {
      if (!event.target.closest(".create-menu")) {
        close("create");
      }
      if (!event.target.closest(".tools-menu")) {
        close("tools");
      }
      if (!event.target.closest(".settings-menu")) {
        closeSettingsMenu();
      }
    });

    documentTarget.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !state.heritage) return;
      const heritage = entries.heritage;
      if (heritage.panel?.contains(documentTarget.activeElement)
        || heritage.toggleButton === documentTarget.activeElement) {
        close("heritage");
        heritage.toggleButton.focus();
        event.preventDefault();
      }
    });

    return {
      close,
      open,
      toggle,
      setLayout,
      getState() {
        return { ...state };
      },
    };
  }

  return { createController, fixedMenuPosition, layoutMode };
});
