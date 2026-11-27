(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }
  root.HelpPanel = api;
  api.mount({ root, documentTarget: root.document, fetchImpl: root.fetch.bind(root) });
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ALLOWED_TAGS = new Set([
    "A", "BLOCKQUOTE", "BR", "CODE", "DEL", "EM", "H1", "H2", "H3", "H4", "H5", "H6",
    "HR", "LI", "OL", "P", "PRE", "STRONG", "TABLE", "TBODY", "TD", "TH", "THEAD", "TR", "UL",
  ]);
  const DROP_WITH_CONTENT = new Set(["IFRAME", "OBJECT", "SCRIPT", "STYLE", "SVG", "TEMPLATE"]);
  const PUBLIC_PATH = /^(?:README\.md|docs\/[^/]+\.md|docs\/deployment\/(?:local-deployment|server-deployment)\.md)$/;

  function decode(value) {
    try { return decodeURIComponent(value); } catch (_error) { return null; }
  }

  function normaliseDocumentationTarget(currentPath, href) {
    if (typeof href !== "string" || !href.trim()) return null;
    const base = new URL(currentPath, "https://help.invalid/");
    let target;
    try { target = new URL(href, base); } catch (_error) { return null; }
    if (target.origin !== base.origin) return null;
    const path = decode(target.pathname);
    const fragment = decode(target.hash.slice(1));
    if (path === null || fragment === null || path.includes("\\")) return null;
    return { path: path.replace(/^\/+/, ""), fragment };
  }

  function resolveHelpTarget(currentPath, href, documentsByPath) {
    const target = normaliseDocumentationTarget(currentPath, href);
    if (!target) return null;
    const document = documentsByPath.get(target.path);
    return document ? { document, fragment: target.fragment } : null;
  }

  function headingSlug(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-");
  }

  function isExternalHref(href) {
    try {
      const url = new URL(href, "https://help.invalid/");
      return ["http:", "https:", "mailto:"].includes(url.protocol)
        && url.origin !== "https://help.invalid";
    } catch (_error) {
      return false;
    }
  }

  function sanitizeMarkdownHtml({ html, documentTarget, currentPath, documentsByPath }) {
    const template = documentTarget.createElement("template");
    template.innerHTML = html;
    for (const element of [...template.content.querySelectorAll("*")]) {
      if (!ALLOWED_TAGS.has(element.tagName)) {
        if (DROP_WITH_CONTENT.has(element.tagName)) element.remove();
        else element.replaceWith(...element.childNodes);
        continue;
      }
      const originalHref = element.tagName === "A" ? element.getAttribute("href") : null;
      for (const attribute of [...element.attributes]) element.removeAttribute(attribute.name);
      if (originalHref !== null) element.dataset.helpOriginalHref = originalHref;
    }

    const usedHeadingIds = new Map();
    for (const heading of template.content.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
      const base = headingSlug(heading.textContent) || "section";
      const count = usedHeadingIds.get(base) || 0;
      usedHeadingIds.set(base, count + 1);
      heading.id = count ? `${base}-${count}` : base;
    }

    for (const anchor of template.content.querySelectorAll("a")) {
      const href = anchor.dataset.helpOriginalHref || "";
      delete anchor.dataset.helpOriginalHref;
      if (href.startsWith("#")) {
        anchor.dataset.helpAnchor = decode(href.slice(1)) || "";
        anchor.href = href;
        continue;
      }
      const target = resolveHelpTarget(currentPath, href, documentsByPath);
      if (target) {
        anchor.href = `#help-document-${target.document.id}`;
        anchor.dataset.helpDocument = target.document.id;
        anchor.dataset.helpAnchor = target.fragment;
        continue;
      }
      if (isExternalHref(href)) {
        anchor.href = href;
        if (!href.toLowerCase().startsWith("mailto:")) {
          anchor.target = "_blank";
          anchor.rel = "noopener noreferrer";
        }
        continue;
      }
      anchor.removeAttribute("href");
      anchor.classList.add("help-link-unavailable");
      anchor.setAttribute("aria-disabled", "true");
      anchor.title = "Document non disponible dans l’aide";
    }
    return template.content;
  }

  function validateInventory(payload) {
    if (!payload || !Array.isArray(payload.documents)) throw new Error("Inventaire d’aide invalide.");
    return payload.documents.map((document) => {
      if (!document || !/^[0-9a-f]{16}$/.test(document.id)
          || typeof document.label !== "string" || !document.label.trim()
          || typeof document.path !== "string" || !PUBLIC_PATH.test(document.path)) {
        throw new Error("Inventaire d’aide invalide.");
      }
      return Object.freeze({ id: document.id, label: document.label.trim(), path: document.path });
    });
  }

  function mount({ root, documentTarget, fetchImpl }) {
    const toggle = documentTarget.querySelector("#toggle-help");
    if (!toggle) return null;

    const modal = documentTarget.createElement("div");
    modal.id = "help-modal";
    modal.className = "help-modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "help-title");
    modal.hidden = true;
    modal.innerHTML = `
      <section class="help-dialog">
        <header class="help-header editor-title">
          <div><strong id="help-title">Aide</strong><span>Documentation publique de Digues App</span></div>
          <button id="close-help" class="icon-button" type="button" aria-label="Fermer l’aide">×</button>
        </header>
        <div class="help-list-view">
          <p class="help-introduction">Choisissez un document pour le consulter.</p>
          <p id="help-list-status" class="field-help" role="status"></p>
          <ul id="help-document-list" class="help-document-list"></ul>
        </div>
        <article class="help-document-view" hidden>
          <div class="help-document-toolbar">
            <button id="help-back" type="button" class="secondary">← Tous les documents</button>
            <strong id="help-document-title"></strong>
          </div>
          <p id="help-document-status" class="field-help" role="status"></p>
          <div id="help-markdown" class="help-markdown"></div>
        </article>
      </section>`;
    documentTarget.body.append(modal);

    const closeButton = modal.querySelector("#close-help");
    const backButton = modal.querySelector("#help-back");
    const listView = modal.querySelector(".help-list-view");
    const documentView = modal.querySelector(".help-document-view");
    const list = modal.querySelector("#help-document-list");
    const listStatus = modal.querySelector("#help-list-status");
    const documentStatus = modal.querySelector("#help-document-status");
    const documentTitle = modal.querySelector("#help-document-title");
    const markdown = modal.querySelector("#help-markdown");
    let documents = null;
    let documentsByPath = new Map();
    let documentsById = new Map();
    let previousFocus = null;
    let requestController = null;

    function showList({ focus = false } = {}) {
      documentView.hidden = true;
      listView.hidden = false;
      documentTitle.textContent = "";
      markdown.replaceChildren();
      if (focus) list.querySelector("button")?.focus();
    }

    function close() {
      if (modal.hidden) return;
      requestController?.abort();
      modal.hidden = true;
      documentTarget.body.classList.remove("help-open");
      toggle.setAttribute("aria-expanded", "false");
      previousFocus?.focus?.();
    }

    function renderList() {
      list.replaceChildren();
      for (const document of documents) {
        const item = documentTarget.createElement("li");
        const button = documentTarget.createElement("button");
        button.type = "button";
        button.textContent = document.label;
        button.addEventListener("click", () => void openDocument(document.id));
        item.append(button);
        list.append(item);
      }
    }

    async function loadInventory() {
      if (documents) return;
      listStatus.textContent = "Chargement de la documentation…";
      const response = await fetchImpl("/api/help/documents", {
        cache: "no-store", credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Documentation indisponible.");
      documents = validateInventory(await response.json());
      documentsByPath = new Map(documents.map((document) => [document.path, document]));
      documentsById = new Map(documents.map((document) => [document.id, document]));
      renderList();
      listStatus.textContent = documents.length ? "" : "Aucun document disponible.";
    }

    function scrollToAnchor(fragment) {
      if (!fragment) {
        markdown.scrollIntoView({ block: "start" });
        return;
      }
      const target = [...markdown.querySelectorAll("[id]")].find((element) => element.id === fragment);
      target?.scrollIntoView({ block: "start" });
    }

    async function openDocument(identifier, fragment = "") {
      const document = documentsById.get(identifier);
      if (!document) return;
      requestController?.abort();
      requestController = new AbortController();
      listView.hidden = true;
      documentView.hidden = false;
      documentTitle.textContent = document.label;
      documentStatus.textContent = "Chargement du document…";
      markdown.replaceChildren();
      try {
        const response = await fetchImpl(`/api/help/documents/${encodeURIComponent(identifier)}`, {
          cache: "no-store", credentials: "same-origin", signal: requestController.signal,
        });
        if (!response.ok) throw new Error("Document indisponible.");
        const payload = await response.json();
        if (payload.id !== identifier || payload.path !== document.path
            || typeof payload.content !== "string") throw new Error("Document invalide.");
        if (!root.marked?.parse) throw new Error("Rendu Markdown indisponible.");
        const html = root.marked.parse(payload.content, { gfm: true, breaks: false });
        markdown.append(sanitizeMarkdownHtml({
          html, documentTarget, currentPath: document.path, documentsByPath,
        }));
        documentStatus.textContent = "";
        scrollToAnchor(fragment);
        backButton.focus();
      } catch (error) {
        if (error.name === "AbortError") return;
        documentStatus.textContent = error.message || "Document indisponible.";
      }
    }

    async function open() {
      previousFocus = documentTarget.activeElement;
      modal.hidden = false;
      documentTarget.body.classList.add("help-open");
      toggle.setAttribute("aria-expanded", "true");
      showList();
      closeButton.focus();
      try { await loadInventory(); }
      catch (error) { listStatus.textContent = error.message || "Documentation indisponible."; }
    }

    toggle.addEventListener("click", () => modal.hidden ? void open() : close());
    closeButton.addEventListener("click", close);
    backButton.addEventListener("click", () => showList({ focus: true }));
    modal.addEventListener("click", (event) => {
      if (event.target === modal) close();
      const anchor = event.target.closest?.("a[data-help-document], a[data-help-anchor]");
      if (!anchor) return;
      event.preventDefault();
      if (anchor.dataset.helpDocument) {
        void openDocument(anchor.dataset.helpDocument, anchor.dataset.helpAnchor || "");
      } else scrollToAnchor(anchor.dataset.helpAnchor || "");
    });
    documentTarget.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hidden) close();
    });
    return { close, open, openDocument, showList };
  }

  return {
    headingSlug,
    mount,
    normaliseDocumentationTarget,
    resolveHelpTarget,
    validateInventory,
  };
});
