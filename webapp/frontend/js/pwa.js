(function initializePwa() {
  "use strict";

  if (!("serviceWorker" in navigator)) {
    return;
  }

  const updateNotice = document.getElementById("pwa-update-notice");
  const updateButton = document.getElementById("pwa-update-button");
  let waitingWorker = null;
  let updateRequested = false;
  let pageReloaded = false;

  function offerUpdate(worker) {
    if (!navigator.serviceWorker.controller || !worker) {
      return;
    }
    waitingWorker = worker;
    if (updateNotice) {
      updateNotice.hidden = false;
    }
  }

  function watchInstallingWorker(registration) {
    const installingWorker = registration.installing;
    if (!installingWorker) {
      return;
    }
    installingWorker.addEventListener("statechange", () => {
      if (installingWorker.state === "installed") {
        offerUpdate(registration.waiting);
      }
    });
  }

  async function registerServiceWorker() {
    const response = await fetch("/api/config", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("La configuration PWA est indisponible.");
    }
    const config = await response.json();
    if (!config.build_id) {
      throw new Error("BUILD_ID absent de la configuration PWA.");
    }

    const registration = await navigator.serviceWorker.register(
      `/service-worker.js?v=${encodeURIComponent(config.build_id)}`,
      { scope: "/" },
    );

    offerUpdate(registration.waiting);
    watchInstallingWorker(registration);
    registration.addEventListener("updatefound", () => {
      watchInstallingWorker(registration);
    });
  }

  updateButton?.addEventListener("click", () => {
    if (!waitingWorker) {
      return;
    }
    updateRequested = true;
    updateButton.disabled = true;
    waitingWorker.postMessage({ type: "SKIP_WAITING" });
  });

  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!updateRequested || pageReloaded) {
      return;
    }
    pageReloaded = true;
    window.location.reload();
  });

  registerServiceWorker().catch((error) => {
    console.warn("Enregistrement du service worker impossible.", error);
  });
}());
