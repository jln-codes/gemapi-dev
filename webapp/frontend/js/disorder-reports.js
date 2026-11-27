(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }
  root.DisorderReports = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Les rapports PDF sont produits par le serveur : ces endpoints ne passent
  // jamais par OfflineRuntime.fetch, dont le repli local renverrait du JSON pour
  // une URL métier au lieu du document attendu.
  const CATALOGUE_RESOURCES = Object.freeze({
    "Aménagement hydraulique": "amenagements-hydrauliques",
    "Système d'endiguement": "systemes-endiguement",
    "Digue": "digues",
    "Tronçon": "troncons",
  });

  const OFFLINE_MESSAGE =
    "La génération des PDF nécessite le serveur ; réessayez en ligne.";
  const NETWORK_MESSAGE =
    "Le serveur est injoignable ; vérifiez votre connexion puis réessayez.";
  const NO_SELECTION_MESSAGE =
    "Sélectionnez un système d'endiguement, une digue, un aménagement hydraulique ou un tronçon dans Patrimoine.";

  function isNetworkError(error) {
    return error instanceof TypeError || error?.name === "TimeoutError";
  }

  function defaultIsOffline() {
    try {
      return Boolean(globalThis.OfflineRuntime?.isOffline?.());
    } catch (_error) {
      return false;
    }
  }

  function catalogueEndpoint(kind, identifier) {
    const resource = CATALOGUE_RESOURCES[kind];
    const id = String(identifier ?? "").trim();
    if (!resource || !id) return null;
    const base = `/api/${resource}/${encodeURIComponent(id)}/desordres/catalogue`;
    return { json: base, pdf: `${base}/pdf` };
  }

  function disorderPdfUrl(identifier) {
    const id = String(identifier ?? "").trim();
    return id ? `/api/desordres/${encodeURIComponent(id)}/pdf` : null;
  }

  function plural(count, singular, pluralForm) {
    return `${count} ${count > 1 ? pluralForm : singular}`;
  }

  function catalogueSummary(payload) {
    const count = Number(payload?.entry_count) || 0;
    const digues = Number(payload?.digue_count) || 0;
    const troncons = Number(payload?.troncon_count) || 0;
    return [
      plural(count, "désordre", "désordres"),
      plural(digues, "digue", "digues"),
      plural(troncons, "tronçon", "tronçons"),
    ].join(" · ");
  }

  function downloadName(response, fallback = "document.pdf") {
    const disposition = response?.headers?.get?.("Content-Disposition") || "";
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    if (encoded) {
      try {
        const decoded = decodeURIComponent(encoded[1].trim());
        if (decoded) return decoded;
      } catch (_error) {
        /* Repli sur filename=. */
      }
    }
    const plain = /filename="?([^";]+)"?/i.exec(disposition);
    return plain?.[1]?.trim() || fallback;
  }

  async function responseError(response, fallback) {
    let message = fallback;
    try {
      const payload = await response.json();
      const detail = payload?.detail;
      if (typeof detail === "string" && detail.trim()) message = detail.trim();
      else if (typeof detail?.message === "string" && detail.message.trim()) {
        message = detail.message.trim();
      }
    } catch (_error) {
      /* Réponse non JSON : message générique. */
    }
    const error = new Error(message);
    error.status = response?.status;
    return error;
  }

  function setStatus(element, message, isError = false) {
    if (!element) return;
    element.textContent = message || "";
    element.classList?.toggle?.("error", Boolean(isError && message));
  }

  function setBusy(element, busy, busyLabel) {
    if (!element) return;
    if (busy) {
      if (element.dataset && element.dataset.idleLabel === undefined) {
        element.dataset.idleLabel = element.textContent || "";
      }
      element.disabled = true;
      element.setAttribute?.("aria-busy", "true");
      if (busyLabel) element.textContent = busyLabel;
      return;
    }
    element.disabled = false;
    element.removeAttribute?.("aria-busy");
    if (element.dataset?.idleLabel !== undefined) {
      if (element.dataset.idleLabel) element.textContent = element.dataset.idleLabel;
      delete element.dataset.idleLabel;
    }
  }

  async function downloadResponse(response, {
    documentTarget = globalThis.document,
    urlApi = globalThis.URL,
    fallback = "document.pdf",
  } = {}) {
    const blob = await response.blob();
    const filename = downloadName(response, fallback);
    const objectUrl = urlApi.createObjectURL(blob);
    const link = documentTarget.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    link.hidden = true;
    documentTarget.body.append(link);
    link.click();
    link.remove();
    urlApi.revokeObjectURL(objectUrl);
    return filename;
  }

  function reportErrorMessage(error) {
    if (error?.code === "offline") return error.message;
    if (isNetworkError(error)) return NETWORK_MESSAGE;
    return error?.message || "Génération impossible.";
  }

  function requireOnline(isOffline) {
    if (!isOffline()) return;
    const error = new Error(OFFLINE_MESSAGE);
    error.code = "offline";
    throw error;
  }

  function createDisorderPdfController({
    button,
    status,
    getDisorderId,
    fetchImpl = (...args) => (globalThis.BusinessRuntime?.fetch || fetch)(...args),
    isOffline = defaultIsOffline,
    documentTarget = globalThis.document,
    urlApi = globalThis.URL,
  }) {
    let pending = false;

    async function run() {
      if (pending) return false;
      const identifier = String(getDisorderId?.() ?? "").trim();
      if (!identifier) {
        setStatus(status, "Aucun désordre sélectionné.", true);
        return false;
      }
      const url = disorderPdfUrl(identifier);
      pending = true;
      setBusy(button, true, "Génération…");
      setStatus(status, "Génération de la fiche…");
      try {
        requireOnline(isOffline);
        const response = await fetchImpl(url, { headers: { Accept: "application/pdf" } });
        if (!response.ok) {
          throw await responseError(response, "Génération de la fiche impossible.");
        }
        const filename = await downloadResponse(response, {
          documentTarget,
          urlApi,
          fallback: "fiche-desordre.pdf",
        });
        setStatus(status, `Fiche téléchargée : ${filename}`);
        return true;
      } catch (error) {
        setStatus(status, reportErrorMessage(error), true);
        return false;
      } finally {
        pending = false;
        setBusy(button, false);
      }
    }

    button?.addEventListener("click", () => { void run(); });
    return { run, get pending() { return pending; } };
  }

  function createCatalogueController({
    button,
    status,
    getScope,
    fetchImpl = (...args) => (globalThis.BusinessRuntime?.fetch || fetch)(...args),
    isOffline = defaultIsOffline,
    documentTarget = globalThis.document,
    urlApi = globalThis.URL,
    onInvoke = () => {},
  }) {
    let pending = false;

    async function run() {
      if (pending) return false;
      onInvoke();
      const scope = getScope?.();
      const endpoint = catalogueEndpoint(scope?.kind, scope?.item?.id);
      if (!endpoint) {
        setStatus(status, NO_SELECTION_MESSAGE, true);
        return false;
      }
      pending = true;
      setBusy(button, true, "Génération…");
      setStatus(status, "Préparation du catalogue…");
      try {
        requireOnline(isOffline);
        const metadata = await fetchImpl(endpoint.json, {
          headers: { Accept: "application/json" },
        });
        if (!metadata.ok) {
          throw await responseError(metadata, "Catalogue indisponible.");
        }
        const payload = await metadata.json();
        if (!Number(payload?.entry_count)) {
          setStatus(status, "Aucun désordre dans ce périmètre.");
          return false;
        }
        setStatus(status, `Génération du catalogue… ${catalogueSummary(payload)}`);
        const response = await fetchImpl(endpoint.pdf, {
          headers: { Accept: "application/pdf" },
        });
        if (!response.ok) {
          throw await responseError(response, "Génération du catalogue impossible.");
        }
        const filename = await downloadResponse(response, {
          documentTarget,
          urlApi,
          fallback: "catalogue-desordres.pdf",
        });
        setStatus(status, `Catalogue téléchargé : ${filename}`);
        return true;
      } catch (error) {
        setStatus(status, reportErrorMessage(error), true);
        return false;
      } finally {
        pending = false;
        setBusy(button, false);
      }
    }

    button?.addEventListener("click", () => { void run(); });
    return { run, get pending() { return pending; } };
  }

  return {
    CATALOGUE_RESOURCES,
    NO_SELECTION_MESSAGE,
    OFFLINE_MESSAGE,
    catalogueEndpoint,
    catalogueSummary,
    createCatalogueController,
    createDisorderPdfController,
    disorderPdfUrl,
    downloadName,
    downloadResponse,
    isNetworkError,
    reportErrorMessage,
    setBusy,
    setStatus,
  };
});
