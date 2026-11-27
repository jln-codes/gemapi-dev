(function (root, factory) {
  const aiPanel = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = aiPanel;
  }
  root.AiPanel = aiPanel;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const AI_MEMORY_STORAGE_KEY = globalThis.BusinessRuntime?.key("digues-ai-memory-v1") || "digues-ai-memory-v1";
  const AI_RECENT_MESSAGES_FALLBACK_MAX = 24;

  function normalizedMemory(value) {
    const summary = typeof value?.conversation_summary === "string"
      ? value.conversation_summary
      : "";
    const recentMessages = Array.isArray(value?.recent_messages)
      ? value.recent_messages.filter(
        (message) => ["user", "assistant"].includes(message?.role)
          && typeof message?.content === "string"
          && message.content.trim(),
      ).map((message) => ({
        role: message.role,
        content: message.content,
      }))
      : [];
    return { conversation_summary: summary, recent_messages: recentMessages };
  }

  function loadMemory(storage) {
    if (!storage?.getItem) {
      return normalizedMemory(null);
    }
    try {
      return normalizedMemory(JSON.parse(storage.getItem(AI_MEMORY_STORAGE_KEY)));
    } catch (_error) {
      return normalizedMemory(null);
    }
  }

  function saveMemory(storage, memory) {
    if (!storage?.setItem) {
      return;
    }
    try {
      storage.setItem(AI_MEMORY_STORAGE_KEY, JSON.stringify(memory));
    } catch (_error) {
      // Une politique navigateur restrictive ne doit pas bloquer le chat courant.
    }
  }

  function currentSessionStorage() {
    try {
      return globalThis.sessionStorage;
    } catch (_error) {
      return null;
    }
  }

  async function defaultFetchJson(url, options = {}) {
    const response = await (globalThis.BusinessRuntime?.fetch || fetch)(url, options);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      // Le message HTTP générique reste utilisable.
    }
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === "string"
        ? detail
        : detail?.message || "Le service IA est indisponible.";
      throw new Error(message);
    }
    return payload;
  }

  function createController({
    rootElement = document,
    fetchJson = defaultFetchJson,
    onVisibilityChange = () => {},
    storage = currentSessionStorage(),
  } = {}) {
    const aiPanel = rootElement.querySelector("#ai-panel");
    const aiToggleButton = rootElement.querySelector("#toggle-ai");
    const aiCloseButton = rootElement.querySelector("#close-ai");
    const aiConversation = rootElement.querySelector("#ai-conversation");
    const aiConversationEmpty = rootElement.querySelector("#ai-conversation-empty");
    const aiChatForm = rootElement.querySelector("#ai-chat-form");
    const aiMessageInput = rootElement.querySelector("#ai-message");
    const aiSendButton = rootElement.querySelector("#ai-send");
    const aiChatStatus = rootElement.querySelector("#ai-chat-status");
    const required = [
      aiPanel,
      aiToggleButton,
      aiCloseButton,
      aiConversation,
      aiChatForm,
      aiMessageInput,
      aiSendButton,
      aiChatStatus,
    ];
    if (required.some((element) => !element)) {
      throw new Error("Structure DOM de l’assistant IA incomplète.");
    }

    const aiConversationHistory = [];
    let aiMemory = loadMemory(storage);
    let aiRequestPending = false;

  async function copyTextToClipboard(text) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch (_error) {
        // Le fallback reste utile hors contexte sécurisé ou si l'accès est refusé.
      }
    }
    const temporaryInput = document.createElement("textarea");
    temporaryInput.value = text;
    temporaryInput.setAttribute("aria-hidden", "true");
    temporaryInput.className = "clipboard-fallback";
    document.body.append(temporaryInput);
    temporaryInput.select();
    const copied = document.execCommand("copy");
    temporaryInput.remove();
    if (!copied) {
      throw new Error("La copie est indisponible.");
    }
  }
  
  function safeAiLinkHref(href) {
    if (typeof href !== "string") {
      return null;
    }
    try {
      const url = new URL(href.trim());
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch (_error) {
      return null;
    }
  }
  
  function appendAiInlineTokens(parent, tokens) {
    (tokens || []).forEach((token) => {
      if (token.type === "text") {
        if (Array.isArray(token.tokens)) {
          appendAiInlineTokens(parent, token.tokens);
        } else {
          parent.append(document.createTextNode(token.text || ""));
        }
        return;
      }
  
      const inlineTags = {
        strong: "strong",
        em: "em",
        codespan: "code",
        del: "del",
      };
      if (inlineTags[token.type]) {
        const element = document.createElement(inlineTags[token.type]);
        if (token.type === "codespan") {
          element.textContent = token.text || "";
        } else {
          appendAiInlineTokens(element, token.tokens);
        }
        parent.append(element);
        return;
      }
  
      if (token.type === "link") {
        const href = safeAiLinkHref(token.href);
        const element = document.createElement(href ? "a" : "span");
        appendAiInlineTokens(element, token.tokens);
        if (href) {
          element.href = href;
          element.target = "_blank";
          element.rel = "noopener noreferrer";
        }
        parent.append(element);
        return;
      }
  
      if (token.type === "br") {
        parent.append(document.createElement("br"));
        return;
      }
  
      if (Array.isArray(token.tokens)) {
        appendAiInlineTokens(parent, token.tokens);
        return;
      }
      parent.append(document.createTextNode(token.text || token.raw || ""));
    });
  }
  
  function lineWithEscapedTablePipes(line) {
    return line.includes("\\|") ? line.replace(/\\\|/g, "|") : line;
  }
  
  function isMarkdownTableSeparator(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(
      lineWithEscapedTablePipes(line),
    );
  }
  
  function isMarkdownTableHeader(line) {
    const normalized = lineWithEscapedTablePipes(line);
    return (normalized.match(/\|/g) || []).length >= 2
      && !isMarkdownTableSeparator(normalized);
  }
  
  function normalizeEscapedMarkdownTables(content) {
    const lines = String(content).split(/\r?\n/);
    let inCodeFence = false;
    let fenceMarker = null;
    for (let index = 0; index < lines.length; index += 1) {
      const fence = lines[index].match(/^\s*(```|~~~)/);
      if (fence) {
        if (!inCodeFence) {
          inCodeFence = true;
          fenceMarker = fence[1];
        } else if (fence[1] === fenceMarker) {
          inCodeFence = false;
          fenceMarker = null;
        }
        continue;
      }
      if (
        inCodeFence
        || !lines[index].includes("\\|")
        || index + 1 >= lines.length
        || !isMarkdownTableHeader(lines[index])
        || !isMarkdownTableSeparator(lines[index + 1])
      ) {
        continue;
      }
      lines[index] = lineWithEscapedTablePipes(lines[index]);
      lines[index + 1] = lineWithEscapedTablePipes(lines[index + 1]);
      let rowIndex = index + 2;
      while (rowIndex < lines.length && lines[rowIndex].trim() !== "") {
        if (/^\s*(```|~~~)/.test(lines[rowIndex])) {
          break;
        }
        lines[rowIndex] = lineWithEscapedTablePipes(lines[rowIndex]);
        rowIndex += 1;
      }
      index = rowIndex - 1;
    }
    return lines.join("\n");
  }
  
  function createAiCopyButton(content, className) {
    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = `ai-copy-button ${className}`;
    copyButton.textContent = "Copier";
    copyButton.setAttribute("aria-live", "polite");
    copyButton.addEventListener("click", async () => {
      try {
        await copyTextToClipboard(content);
        copyButton.textContent = "Copié";
      } catch (_error) {
        copyButton.textContent = "Copie impossible.";
      }
      window.setTimeout(() => {
        copyButton.textContent = "Copier";
      }, 1400);
    });
    return copyButton;
  }
  
  function appendAiTableCell(row, cell, tagName) {
    const element = document.createElement(tagName);
    appendAiInlineTokens(element, cell.tokens);
    row.append(element);
  }
  
  function appendAiMarkdownTable(parent, token) {
    const wrapper = document.createElement("div");
    wrapper.className = "ai-table-wrapper";
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    (token.header || []).forEach((cell) => appendAiTableCell(headerRow, cell, "th"));
    thead.append(headerRow);
    table.append(thead);
  
    const tbody = document.createElement("tbody");
    (token.rows || []).forEach((cells) => {
      const row = document.createElement("tr");
      cells.forEach((cell) => appendAiTableCell(row, cell, "td"));
      tbody.append(row);
    });
    table.append(tbody);
    wrapper.append(table);
    parent.append(wrapper);
  }
  
  function appendAiMarkdownBlocks(parent, tokens) {
    (tokens || []).forEach((token) => {
      if (token.type === "space") {
        return;
      }
      if (token.type === "heading") {
        const heading = document.createElement(`h${Math.min(token.depth || 1, 4)}`);
        appendAiInlineTokens(heading, token.tokens);
        parent.append(heading);
        return;
      }
      if (["paragraph", "text"].includes(token.type)) {
        const paragraph = document.createElement("p");
        appendAiInlineTokens(paragraph, token.tokens || [token]);
        parent.append(paragraph);
        return;
      }
      if (token.type === "list") {
        const list = document.createElement(token.ordered ? "ol" : "ul");
        token.items.forEach((item) => {
          const listItem = document.createElement("li");
          appendAiMarkdownBlocks(listItem, item.tokens);
          list.append(listItem);
        });
        parent.append(list);
        return;
      }
      if (token.type === "blockquote") {
        const quote = document.createElement("blockquote");
        appendAiMarkdownBlocks(quote, token.tokens);
        parent.append(quote);
        return;
      }
      if (token.type === "table") {
        appendAiMarkdownTable(parent, token);
        return;
      }
      if (token.type === "code") {
        const wrapper = document.createElement("section");
        wrapper.className = "ai-code-block";
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = token.text || "";
        const language = (token.lang || "").trim().split(/\s+/, 1)[0];
        if (/^[a-z0-9_-]{1,32}$/i.test(language)) {
          code.dataset.language = language.toLowerCase();
        }
        pre.append(code);
        wrapper.append(pre, createAiCopyButton(code.textContent, "ai-code-copy"));
        parent.append(wrapper);
        return;
      }
      if (token.type === "hr") {
        parent.append(document.createElement("hr"));
        return;
      }
  
      const fallback = document.createElement("p");
      fallback.textContent = token.raw || token.text || "";
      parent.append(fallback);
    });
  }
  
  function renderAiMarkdown(content) {
    const body = document.createElement("div");
    body.className = "ai-message-body ai-markdown";
    if (!globalThis.marked?.lexer) {
      body.textContent = content;
      return body;
    }
    try {
      appendAiMarkdownBlocks(body, globalThis.marked.lexer(
        normalizeEscapedMarkdownTables(content),
        {
        gfm: true,
        breaks: false,
        },
      ));
    } catch (_error) {
      body.textContent = content;
    }
    return body;
  }
  
  function appendAiExecutedQueries(message, executedQueries) {
    const queries = Array.isArray(executedQueries)
      ? executedQueries.filter(
        (query) => typeof query?.sql === "string" && query.sql.trim(),
      )
      : [];
    if (!queries.length) {
      return;
    }
  
    const details = document.createElement("details");
    details.className = "ai-sql-details";
    const summary = document.createElement("summary");
    summary.textContent = `Requêtes SQL utilisées — ${queries.length} requête${queries.length > 1 ? "s" : ""}`;
    details.append(summary);
  
    queries.forEach((query, index) => {
      const item = document.createElement("section");
      item.className = "ai-sql-query";
      const heading = document.createElement("strong");
      heading.textContent = `Requête ${index + 1}`;
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = query.sql;
      pre.append(code);
      const copyButton = createAiCopyButton(query.sql, "ai-sql-copy");
      item.append(heading, pre, copyButton);
      details.append(item);
    });
    message.append(details);
  }
  
  function appendAiConsultedSources(message, consultedSources) {
    const sources = Array.isArray(consultedSources)
      ? consultedSources.filter(
        (source) => typeof source?.title === "string"
          && source.title.trim()
          && typeof source?.path === "string"
          && source.path.trim(),
      )
      : [];
    if (!sources.length) {
      return;
    }
  
    const details = document.createElement("details");
    details.className = "ai-source-details";
    const summary = document.createElement("summary");
    summary.textContent = `Sources consultées — ${sources.length}`;
    details.append(summary);
  
    sources.forEach((source) => {
      const item = document.createElement("section");
      item.className = "ai-source";
      const title = document.createElement("strong");
      title.textContent = source.title;
      const path = document.createElement("code");
      path.textContent = source.path;
      item.append(title, path);
      if (typeof source.heading === "string" && source.heading.trim()) {
        const heading = document.createElement("span");
        heading.textContent = source.heading;
        item.append(heading);
      }
      details.append(item);
    });
    message.append(details);
  }
  
  function safeExternalSourceUrl(value) {
    if (typeof value !== "string") {
      return null;
    }
    try {
      const url = new URL(value);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
        return null;
      }
      return url.href;
    } catch (_error) {
      return null;
    }
  }
  
  function appendAiExternalSources(message, externalSources) {
    const sources = Array.isArray(externalSources)
      ? externalSources.filter(
        (source) => typeof source?.title === "string"
          && source.title.trim()
          && typeof source?.organization === "string"
          && source.organization.trim()
          && safeExternalSourceUrl(source.url),
      )
      : [];
    if (!sources.length) {
      return;
    }
  
    const details = document.createElement("details");
    details.className = "ai-external-source-details";
    const summary = document.createElement("summary");
    summary.textContent = `Sources externes consultées — ${sources.length}`;
    details.append(summary);
  
    sources.forEach((source) => {
      const item = document.createElement("section");
      item.className = "ai-external-source";
      const organization = document.createElement("span");
      organization.textContent = source.organization;
      const link = document.createElement("a");
      link.textContent = source.title;
      link.href = safeExternalSourceUrl(source.url);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      item.append(link, organization);
      details.append(item);
    });
    message.append(details);
  }
  
  function appendAiMessage(
    role,
    content,
    {
      error = false,
      remember = true,
      executedQueries = [],
      consultedSources = [],
      externalSources = [],
    } = {},
  ) {
    aiConversationEmpty?.remove();
    const message = document.createElement("article");
    message.className = `ai-message ai-message-${role}`;
    if (error) {
      message.classList.add("ai-message-error");
    }
    const author = document.createElement("strong");
    author.textContent = role === "user" ? "Vous" : "Assistant IA";
    const body = role === "assistant"
      ? renderAiMarkdown(content)
      : document.createElement("span");
    if (role === "user") {
      body.className = "ai-message-body";
      body.textContent = content;
    }
    message.append(author, body);
    if (role === "assistant") {
      appendAiExecutedQueries(message, executedQueries);
      appendAiConsultedSources(message, consultedSources);
      appendAiExternalSources(message, externalSources);
    }
    aiConversation.append(message);
    aiConversation.scrollTop = aiConversation.scrollHeight;
    if (remember) {
      aiConversationHistory.push({ role, content });
    }
  }
  
  function setAiRequestPending(pending) {
    aiRequestPending = pending;
    aiMessageInput.disabled = pending;
    aiSendButton.disabled = pending;
    aiChatStatus.textContent = pending ? "Envoi en cours…" : "";
  }

  aiMemory.recent_messages.forEach((message) => {
    appendAiMessage(message.role, message.content);
  });
  
  aiChatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = aiMessageInput.value.trim();
    if (!message || aiRequestPending) {
      return;
    }
  
    appendAiMessage("user", message);
    aiMessageInput.value = "";
    setAiRequestPending(true);
    try {
      const response = await fetchJson("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_summary: aiMemory.conversation_summary,
          recent_messages: aiMemory.recent_messages,
          message,
        }),
      });
      if (typeof response.answer !== "string" || !response.answer.trim()) {
        throw new Error("Réponse invalide du service IA.");
      }
      appendAiMessage("assistant", response.answer.trim(), {
        executedQueries: response.executed_queries,
        consultedSources: response.consulted_sources,
        externalSources: response.external_sources,
      });
      const returnedMemory = normalizedMemory(response);
      aiMemory = (
        typeof response.conversation_summary === "string"
        && Array.isArray(response.recent_messages)
      ) ? returnedMemory : normalizedMemory({
        conversation_summary: aiMemory.conversation_summary,
        recent_messages: [
          ...aiMemory.recent_messages,
          { role: "user", content: message },
          { role: "assistant", content: response.answer.trim() },
        ].slice(-AI_RECENT_MESSAGES_FALLBACK_MAX),
      });
      saveMemory(storage, aiMemory);
    } catch (error) {
      console.error("Réponse de l’assistant impossible", error);
      appendAiMessage(
        "assistant",
        error.message || "Impossible d’obtenir une réponse de l’assistant.",
        { error: true, remember: false },
      );
    } finally {
      setAiRequestPending(false);
      aiMessageInput.focus();
    }
  });
  
  aiMessageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!aiRequestPending) {
        aiChatForm.requestSubmit();
      }
    }
  });

    function setOpen(open) {
      aiPanel.hidden = !open;
      aiToggleButton.setAttribute("aria-expanded", String(open));
      onVisibilityChange(Boolean(open));
    }

    return {
      closeButton: aiCloseButton,
      panel: aiPanel,
      setOpen,
      toggleButton: aiToggleButton,
    };
  }

  return { createController };
});
