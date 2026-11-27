/* No business script, IDB handle or replay starts before this gate succeeds. */
(async function (root) {
  "use strict";
  const LEASE = "digues-app:browser-lease-v1", MODE = "digues-app:browser-mode-v1";
  const SIGNAL = "digues-app:identity-signal-v1", LOGOUT_PENDING = "digues-app:logout-pending";
  const nativeFetch = root.fetch.bind(root);
  const shell = document.querySelector("main");
  // The shell must be rendered for Leaflet/layout initialisation, but its
  // controls cannot accept input before the ordered business scripts bind them.
  shell.inert = true;
  const gate = document.createElement("section");
  gate.id = "identity-gate"; gate.className = "app-modal-backdrop";
  document.body.append(gate);
  let catalog, config, revoked = false, configFromServer = false, configFetchedAt = null;
  const identityRevision = localStorage.getItem(SIGNAL);
  const channel = typeof BroadcastChannel === "function" ? new BroadcastChannel(SIGNAL) : null;
  function emit() {
    const message = { nonce: crypto.randomUUID() };
    localStorage.setItem(SIGNAL, JSON.stringify(message)); channel?.postMessage(message);
  }
  function stop() {
    root.BusinessRuntime?.stop(); root.ResourceEventTransport?.stop();
    root.InterfaceCoordination?.destroy(); root.InterPageBus?.close();
    root.OfflineRuntime?.store.close().catch(() => {});
    root.dispatchEvent(new Event("digues-app:context-closed"));
    shell.hidden = true;
    // Remove previews and business content immediately, before any asynchronous logout.
    shell.replaceChildren();
    document.querySelectorAll('dialog, #offline-notice, #identity-business-notice').forEach(node => node.remove());
  }
  function lock(message = "Session fermée. Reconnectez-vous.") {
    if (revoked) return;
    revoked = true; stop(); gate.hidden = false;
    const body = screen("Session fermée");
    const text = document.createElement("p"); text.textContent = message;
    const actions = document.createElement("div"); actions.className = "app-modal-actions";
    const button = document.createElement("button"); button.className = "app-modal-primary"; button.textContent = "Connexion";
    button.onclick = () => {
      const destination = new URL(location.href); destination.searchParams.set("login", "1");
      location.replace(destination.pathname + destination.search + destination.hash);
    };
    actions.append(button); body.append(text, actions);
  }
  channel?.addEventListener("message", () => lock());
  root.addEventListener("pagehide", stop);
  root.addEventListener("storage", event => { if (event.key === SIGNAL) lock(); });
  root.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
  async function json(path, options = {}) {
    const response = await nativeFetch(path, { cache: "no-store", credentials: "same-origin",
      ...options, signal: AbortSignal.timeout(12000) });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw Object.assign(new Error(response.status === 401 ? "Authentification requise." : `Service indisponible (${response.status}).`), { status: response.status, code: detail.code });
    }
    return response.status === 204 ? null : response.json();
  }
  function screen(title) {
    gate.replaceChildren();
    const dialog = document.createElement("section"); dialog.className = "app-modal-dialog identity-dialog";
    dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
    const header = document.createElement("header"); header.className = "app-modal-header";
    const heading = document.createElement("h1"); heading.textContent = title; header.append(heading);
    const body = document.createElement("div"); body.className = "app-modal-body";
    dialog.append(header, body); gate.append(dialog);
    return body;
  }
  async function logout({ keepDestination = false } = {}) {
    localStorage.removeItem(LEASE); localStorage.setItem(LOGOUT_PENDING, "1");
    emit(); lock("Déconnexion en cours…");
    try {
      await json("/api/auth/logout", { method: "POST", headers: { "X-Digues-CSRF": "1" } });
      localStorage.removeItem(LOGOUT_PENDING);
      const destination = new URL(location.href);
      if (keepDestination) {
        destination.searchParams.set("login", "1");
        destination.searchParams.delete("demo");
        destination.searchParams.delete("choose");
      }
      location.replace(keepDestination ? destination.pathname + destination.search + destination.hash : location.pathname);
    } catch (_) {
      gate.querySelector("p").textContent = "Accès local fermé. Révocation serveur à reprendre au retour réseau, avant toute connexion.";
    }
  }
  function login(message = "") {
    const body = screen("Connexion à digues.app");
    body.parentElement.classList.add("identity-login-dialog");
    const form = document.createElement("form");
    form.className = "app-modal-body";
    form.innerHTML = '<label>Identifiant <input name="login" autocomplete="username" required></label><label>Mot de passe <input name="password" type="password" autocomplete="current-password" required></label><div class="app-modal-actions"><button class="app-modal-primary" type="submit">Se connecter</button></div><p role="status"></p>';
    body.append(form);
    if (config.public_demo) {
      const secondary = document.createElement("nav"); secondary.className = "identity-demo-access";
      secondary.setAttribute("aria-label", "Découverte de l’application");
      const link = document.createElement("a"); link.textContent = "Découvrir la démonstration";
      const destination = new URL(location.href); destination.pathname = "/";
      destination.searchParams.set("base", config.public_demo.slug);
      for (const key of ["login", "demo", "choose"]) destination.searchParams.delete(key);
      link.href = destination.pathname + destination.search + destination.hash;
      secondary.append(link); body.append(secondary);
    }
    form.querySelector('[role="status"]').textContent = message;
    form.onsubmit = async event => {
      event.preventDefault(); const button = form.querySelector("button"); button.disabled = true;
      try {
        await json("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json", "X-Digues-CSRF": "1" },
          body: JSON.stringify({ login: form.elements.login.value, password: form.elements.password.value }) });
        form.elements.password.value = ""; localStorage.removeItem(LEASE); emit();
        // Only a requested Carte/Base route survives login; authorization is rechecked after reload.
        const destination = new URL(location.href);
        destination.searchParams.delete("login");
        destination.searchParams.delete("demo");
        if (!destination.searchParams.has("base")) destination.searchParams.set("choose", "1");
        location.replace(destination.pathname + destination.search + destination.hash);
      } catch (error) { form.querySelector('[role="status"]').textContent = error.status === 401 ? "Identifiant ou mot de passe incorrect." : error.message; }
      finally { button.disabled = false; }
    };
    form.elements.login.focus();
  }
  async function loginPublicDemo() {
    try {
      // Recheck immediately before POST; the normal endpoint also refuses a
      // valid existing session when this optional anonymous-only guard is set.
      try { await json("/api/auth/me"); location.reload(); return; }
      catch (error) { if (error.status !== 401) throw error; }
      if (revoked) return;
      await json("/api/auth/login", { method: "POST", headers: {
        "Content-Type": "application/json", "X-Digues-CSRF": "1", "X-Digues-Anonymous-Only": "1" },
        body: JSON.stringify({ login: config.public_demo.login, password: config.public_demo.password }) });
      localStorage.removeItem(LEASE); emit(); location.reload();
    } catch (error) {
      if (error.status === 409) { location.reload(); return; }
      login("L’accès public à la démonstration est indisponible. Vous pouvez vous connecter avec votre compte.");
    }
  }
  function chooseBase(message = "Choisir une base métier") {
    const body = screen(message);
    const list = document.createElement("div"); list.className = "identity-base-list";
    for (const base of catalog.databases) {
      const link = document.createElement("a");
      link.className = "identity-base-choice";
      const destination = new URL(location.href); destination.searchParams.set("base", base.slug);
      for (const key of ["choose", "login", "demo"]) destination.searchParams.delete(key);
      link.href = destination.pathname + destination.search + destination.hash;
      link.textContent = `${base.structure_name} — ${base.display_name}`; list.append(link);
    }
    if (!catalog.databases.length) list.append(document.createTextNode("Aucune affectation métier active."));
    const actions = document.createElement("div"); actions.className = "app-modal-actions";
    const exit = document.createElement("button"); exit.className = "app-modal-primary";
    exit.textContent = "Se déconnecter"; exit.onclick = () => logout(); actions.append(exit);
    const change = document.createElement("button"); change.textContent = "Se connecter avec un autre compte";
    change.onclick = () => logout({ keepDestination: true }); actions.append(change);
    body.append(list, actions);
  }
  let sessionUser = null;
  function displayIdentity(user) {
    return {
      display_name: typeof user?.display_name === "string" ? user.display_name.trim() : "",
      login: typeof user?.login === "string" ? user.login.trim() : "",
    };
  }
  function mountBusinessSession(context, user) {
    const toggle = document.querySelector("#open-business-session");
    if (!toggle) throw new Error("Contrôle de session introuvable.");
    toggle.removeAttribute("aria-disabled");
    toggle.setAttribute("aria-label", "Compte et base");
    toggle.title = "Compte et base";
    toggle.setAttribute("aria-haspopup", "dialog");
    toggle.setAttribute("aria-controls", "business-session-modal");
    toggle.setAttribute("aria-expanded", "false");

    const modal = document.createElement("div");
    modal.id = "business-session-modal";
    modal.className = "app-modal-backdrop territoire-modal business-session-modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "business-session-title");
    modal.hidden = true;
    modal.innerHTML = `
      <section class="app-modal-dialog territoire-dialog business-session-dialog">
        <div class="app-modal-header editor-title">
          <strong id="business-session-title">Compte et base</strong>
          <button id="close-business-session" class="app-modal-close icon-button" type="button" aria-label="Fermer le compte et la base">×</button>
        </div>
        <dl class="business-session-details">
          <div><dt>Utilisateur</dt><dd><strong id="business-session-user"></strong><span id="business-session-login"></span></dd></div>
          <div><dt>Structure</dt><dd id="business-session-structure"></dd></div>
          <div><dt>Base active</dt><dd id="business-session-base"></dd></div>
        </dl>
        <div class="app-modal-actions editor-actions business-session-actions">
          <a id="change-business-base" href="">Changer de base</a>
          <button id="logout-business-session" type="button">Se déconnecter</button>
        </div>
      </section>`;
    shell.append(modal);
    const userName = user.display_name || user.login || "Identité indisponible hors ligne";
    const login = user.display_name && user.login && user.display_name !== user.login ? user.login : "";
    modal.querySelector("#business-session-user").textContent = userName;
    const loginElement = modal.querySelector("#business-session-login");
    loginElement.textContent = login;
    loginElement.hidden = !login;
    modal.querySelector("#business-session-structure").textContent = context.structure_name;
    modal.querySelector("#business-session-base").textContent = context.display_name;
    modal.querySelector("#change-business-base").href = location.pathname;
    if (context.role_code === 'database_admin' && context.permissions.includes('members.manage')) {
      root.DatabaseAccess.mount(modal.querySelector('.business-session-dialog'), context, json);
    }
    const closeButton = modal.querySelector("#close-business-session");
    let previousInert = [];
    function close({ restoreFocus = true } = {}) {
      if (modal.hidden) return;
      modal.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      document.removeEventListener("keydown", onKeydown);
      for (const [element, wasInert] of previousInert) element.inert = wasInert;
      previousInert = [];
      if (restoreFocus) toggle.focus();
    }
    function onKeydown(event) {
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.key !== "Tab") return;
      const focusable = [...modal.querySelectorAll('button:not([disabled]), a[href], input, select, summary')]
        .filter(element => element.getClientRects().length && !element.closest('[inert]'));
      const first = focusable[0], last = focusable.at(-1);
      if (!first) return;
      if (event.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }
    function open() {
      if (revoked || !root.BusinessRuntime.isActive() || !modal.hidden) return;
      previousInert = [...shell.children].filter(element => element !== modal)
        .map(element => [element, element.inert]);
      for (const [element] of previousInert) element.inert = true;
      modal.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      document.addEventListener("keydown", onKeydown);
      closeButton.focus();
    }
    toggle.addEventListener("click", open);
    closeButton.addEventListener("click", () => close());
    modal.addEventListener("click", event => { if (event.target === modal) close(); });
    modal.querySelector("#logout-business-session").addEventListener("click", () => logout());
    root.addEventListener("digues-app:context-closed", () => close({ restoreFocus: false }), { once: true });
  }
  try {
    try {
      config = await json("/api/config");
      configFromServer = true; configFetchedAt = Date.now();
      localStorage.setItem(MODE, JSON.stringify({ legacy_single_database_enabled: config.legacy_single_database_enabled === true,
        lidar_ui_enabled: config.lidar_ui_enabled === true }));
    } catch (error) {
      if (error.status) throw error;
      config = JSON.parse(localStorage.getItem(MODE) || "null");
      if (!config) throw new Error("Une première ouverture connectée est nécessaire.");
    }
    const legacy = config.legacy_single_database_enabled === true;
    const requested = new URL(location.href);
    let context = null;
    if (!legacy) {
      if (localStorage.getItem(LOGOUT_PENDING)) {
        await json("/api/auth/logout", { method: "POST", headers: { "X-Digues-CSRF": "1" } });
        localStorage.removeItem(LOGOUT_PENDING); localStorage.removeItem(LEASE);
      }
      if (requested.searchParams.get("login") === "1") { login(); return; }
      if (requested.searchParams.get("demo") === "1" && !requested.searchParams.has("base")) {
        if (!config.public_demo) { login("Accès public à la démonstration désactivé."); return; }
        requested.searchParams.delete("demo");
        requested.searchParams.set("base", config.public_demo.slug);
        location.replace(requested.pathname + requested.search + requested.hash); return;
      }
      let verifiedUser = null;
      try {
        const user = verifiedUser = await json("/api/auth/me");
        catalog = await json("/api/auth/databases");
        if (catalog.user_id !== user.id) throw new Error("Identité modifiée pendant le chargement.");
        if (revoked || localStorage.getItem(SIGNAL) !== identityRevision) { lock(); return; }
        // A delayed catalogue must never resurrect a lease after another tab's logout.
        sessionUser = displayIdentity(user);
        localStorage.setItem(LEASE, JSON.stringify({ ...catalog, user_display: sessionUser,
          browser_identity_revision: identityRevision }));
      } catch (error) {
        if (error.status === 401) {
          const previous = localStorage.getItem(LEASE); localStorage.removeItem(LEASE);
          if (previous) emit();
          const selections = requested.searchParams.getAll("base");
          const demo = config.public_demo;
          if (!verifiedUser && demo && selections.length === 1
              && [demo.slug, demo.database_id].includes(selections[0])) {
            await loginPublicDemo(); return;
          }
          login(); return;
        }
        if (error.status || !(error instanceof TypeError || error.name === 'TimeoutError')) throw error;
        catalog = JSON.parse(localStorage.getItem(LEASE) || "null");
        if (!catalog || catalog.browser_identity_revision !== localStorage.getItem(SIGNAL)
            || (verifiedUser && verifiedUser.id !== catalog.user_id)
            || !Number.isFinite(Date.parse(catalog.expires_at)) || Date.parse(catalog.expires_at) <= Date.now()) throw new Error("Session locale absente ou expirée : connexion réseau requise.");
        sessionUser = displayIdentity(verifiedUser || catalog.user_display);
      }
      const selections = new URL(location.href).searchParams.getAll("base");
      if (!selections.length) { chooseBase(); return; }
      if (selections.length !== 1) { chooseBase("Base non autorisée — choisir une affectation active"); return; }
      try {
        const databaseId = root.BusinessContext.resolveDatabaseId(catalog, selections[0]);
        context = root.BusinessContext.createContext(catalog, databaseId);
      }
      catch (_) { chooseBase("Base non autorisée — choisir une affectation active"); return; }
      // Transitional UUID input is resolved above under the current identity;
      // publish only its authorized slug, preserving the incoming object/viewport.
      if (selections[0] !== context.slug) {
        const canonicalUrl = new URL(location.href);
        canonicalUrl.searchParams.set("base", context.slug);
        history.replaceState(history.state, "", canonicalUrl.pathname + canonicalUrl.search + canonicalUrl.hash);
      }
      setTimeout(() => lock("Session expirée : reconnectez-vous. Intentions conservées."), Math.max(0, Date.parse(catalog.expires_at) - Date.now()));
    } else if (new URL(location.href).searchParams.has("base")) {
      throw new Error("Sélection multi-base interdite en mode démo legacy.");
    }
    if (revoked) return;
    root.PhotoQualityPolicy = null;
    root.BusinessRuntime = root.BusinessContext.createRuntime({ context, legacy, fetchImpl: nativeFetch,
      origin: location.origin, isCurrent: () => legacy || (localStorage.getItem(SIGNAL) === identityRevision && Date.now() < Date.parse(catalog.expires_at)),
      onUnauthorized: () => { localStorage.removeItem(LEASE); emit(); lock(); } });
    document.body.dataset.businessMode = legacy ? 'legacy' : 'multi';
    document.body.dataset.lidarUiEnabled = config.lidar_ui_enabled === true ? 'true' : 'false';
    for (const permission of ['business.create', 'business.update', 'business.delete', 'sql.read', 'ai.read', 'exports.read', 'offline.prepare', 'offline.write']) {
      document.body.setAttribute('data-' + permission.replace('.', '-'), String(root.BusinessRuntime.can(permission)));
    }
    if (!root.BusinessRuntime.can('business.update')) {
      const readonlyFields = () => document.querySelectorAll(
        '#heritage-object-editor input, #heritage-object-editor select, #heritage-object-editor textarea, '
        + '#desordre-editor input, #desordre-editor select, #desordre-editor textarea, '
        + '#observation-suite-form input, #observation-suite-form select, #observation-suite-form textarea'
      ).forEach(input => { if (!input.disabled) input.disabled = true; });
      readonlyFields();
      const observer = new MutationObserver(readonlyFields);
      observer.observe(shell, { childList: true, subtree: true });
      root.addEventListener('digues-app:context-closed', () => observer.disconnect(), { once: true });
    }
    // Every context owns its module instances. Switching base is a full navigation.
    gate.hidden = true; shell.hidden = false;
    await Promise.all([...document.querySelectorAll("script[data-business-src]")].map(placeholder =>
      new Promise((resolve, reject) => {
        const script = document.createElement("script");
        // Fetch in parallel; classic scripts with async=false execute in insertion order.
        script.async = false;
        for (const [key, value] of Object.entries(placeholder.dataset)) if (key !== "businessSrc") script.dataset[key] = value;
        script.src = placeholder.dataset.businessSrc; script.onload = resolve; script.onerror = reject;
        placeholder.replaceWith(script);
      })));
    if (revoked) return;
    // Autonomous Carte editors register after map.js. Resolve incoming targets
    // only once the complete ordered business-script set has loaded.
    void root.MapEntityNavigation?.start();
    let photoPolicy = null;
    if (configFromServer) {
      try {
        const serverPolicy = root.PhotoFile.normalizePolicy(config.photo_quality_policy);
        photoPolicy = serverPolicy;
        try {
          const cached = await root.OfflineRuntime.store.writePhotoQualityPolicy(serverPolicy, configFetchedAt);
          photoPolicy = root.PhotoFile.normalizePolicy(cached.policy);
        } catch (error) {
          console.warn("Politique photo disponible mais cache local indisponible.", error);
        }
      } catch (error) {
        console.warn("Politique photo serveur ignorée car incompatible.", error);
      }
    }
    if (!photoPolicy) {
      try {
        const cached = await root.OfflineRuntime.store.readPhotoQualityPolicy();
        if (cached) photoPolicy = root.PhotoFile.normalizePolicy(cached.policy);
      } catch (error) {
        // Preserve the corrupt record for diagnosis; never erase other offline data.
        console.warn("Cache local de politique photo ignoré car incompatible.", error);
      }
    }
    root.PhotoQualityPolicy = photoPolicy;
    if (revoked) return;
    shell.inert = false;
    gate.hidden = true; shell.hidden = false;
    if (!legacy) {
      mountBusinessSession(context, sessionUser);
      const check = async () => {
        if (revoked) return;
        if (!root.BusinessRuntime.isActive()) { lock(); return; }
        try {
          const fresh = await json("/api/auth/databases");
          // A verified removal/downgrade must replace the offline lease too.
          // Never publish a delayed response across logout/account changes.
          if (revoked || localStorage.getItem(SIGNAL) !== identityRevision) { lock(); return; }
          if (fresh.user_id !== context.user_id || fresh.instance_id !== context.instance_id) {
            localStorage.removeItem(LEASE); emit(); lock(); return;
          }
          localStorage.setItem(LEASE, JSON.stringify({ ...fresh, user_display: sessionUser,
            browser_identity_revision: identityRevision }));
          const base = fresh.databases.find(item => item.business_database_id === context.business_database_id);
          if (fresh.user_id !== context.user_id || fresh.instance_id !== context.instance_id || !base || base.data_epoch !== context.data_epoch
              || base.role_code !== context.role_code
              || JSON.stringify([...base.permissions].sort()) !== JSON.stringify([...context.permissions].sort())) lock("Identité ou droits modifiés. Rechargez le contexte.");
        } catch (error) {
          if (revoked || localStorage.getItem(SIGNAL) !== identityRevision) { lock(); return; }
          if (error.status === 401) { localStorage.removeItem(LEASE); emit(); }
          if (error.status) lock(); /* Network outage keeps the bounded local lease only. */
        }
      };
      root.addEventListener("focus", check); root.addEventListener("online", check); setInterval(check, 60000);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) void check(); });
    }
  } catch (error) { stop(); gate.hidden = false; const body = screen("Application indisponible"); const message = document.createElement("p"); message.textContent = error.message || "Chargement impossible."; body.append(message); }
})(globalThis);
