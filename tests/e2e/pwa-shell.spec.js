const { test, expect } = require("@playwright/test");
const http = require("node:http");
const fs = require("node:fs");

const workerSource = fs.readFileSync("webapp/frontend/service-worker.js", "utf8");
const registrationSource = fs.readFileSync("webapp/frontend/js/pwa.js", "utf8");

test("a controlled PWA recovers its missing shell cache and still updates atomically", async ({ browser }) => {
  let generation = "N";
  let assetFailure = null;
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    const pathname = url.pathname;
    const headers = { "Cache-Control": "no-store" };
    // Transport regression with a real SW. The live campaign separately
    // exercises two archived releases, PostgreSQL and business intentions.
    if (pathname === "/static/css/modal.css" && assetFailure) {
      if (assetFailure === "missing") {
        response.writeHead(503, headers); response.end("Asset unavailable");
      } else {
        response.writeHead(200, { ...headers, "Content-Type": "text/css", "Content-Length": 1024 });
        response.write(":root {"); response.destroy();
      }
      return;
    }
    if (pathname === "/api/config") {
      response.writeHead(200, { ...headers, "Content-Type": "application/json" });
      response.end(JSON.stringify({ build_id: generation }));
    } else if (pathname === "/service-worker.js") {
      // Same contract as the backend route, including automatic revalidation
      // of the active worker's URL after the server has changed releases.
      if (url.searchParams.has("v") && url.searchParams.get("v") !== generation) {
        response.writeHead(409, headers); response.end("Worker version unavailable"); return;
      }
      response.writeHead(200, { ...headers, "Content-Type": "application/javascript" });
      response.end(workerSource + `\n// Actual worker source generation: ${generation}\n`);
    } else if (pathname === "/static/js/pwa.js") {
      response.writeHead(200, { ...headers, "Content-Type": "application/javascript" });
      response.end(registrationSource);
    } else if (pathname === "/static/js/map.js") {
      response.writeHead(200, { ...headers, "Content-Type": "application/javascript" });
      response.end("document.querySelector('#ready').textContent = 'ready';");
    } else if (pathname === "/static/css/modal.css") {
      response.writeHead(200, { ...headers, "Content-Type": "text/css" });
      response.end(`:root { --modal-cache-generation: "${generation}"; }`);
    } else if (pathname === "/carte" || pathname === "/base" || pathname === "/") {
      response.writeHead(200, { ...headers, "Content-Type": "text/html" });
      response.end(`<!doctype html><title>audit</title><link rel="stylesheet" href="/static/css/modal.css">
        <div id="generation">${generation}</div>
        <div id="ready"></div><div id="pwa-update-notice" hidden>
        <button id="pwa-update-button">Mettre à jour</button></div>
        <script src="/static/js/map.js"></script><script src="/static/js/pwa.js"></script>`);
    } else if (pathname === "/manifest.webmanifest") {
      response.writeHead(200, { ...headers, "Content-Type": "application/manifest+json" });
      response.end(JSON.stringify({ name: "PWA audit", start_url: "/carte", display: "standalone" }));
    } else {
      const contentType = pathname.endsWith(".js") ? "application/javascript"
        : pathname.endsWith(".css") ? "text/css" : "application/octet-stream";
      response.writeHead(200, { ...headers, "Content-Type": contentType });
      response.end(contentType === "application/javascript" ? "" : generation);
    }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.goto(`${origin}/carte`);
    await page.evaluate(() => navigator.serviceWorker.ready);
    await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
    await page.reload();
    await expect(page.locator("#generation")).toHaveText("N");
    await expect(page.locator("#ready")).toHaveText("ready");
    await expect.poll(() => page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--modal-cache-generation").trim(),
    )).toBe('"N"');

    // Native IDB and Blob: failed installs and successful activation must leave
    // this data untouched. No purge or replacement database is permitted.
    const localName = await page.evaluate(async () => {
      const name = "shell-upgrade-" + crypto.randomUUID();
      const db = await new Promise((resolve, reject) => {
        const q = indexedDB.open(name, 1);
        q.onupgradeneeded = () => q.result.createObjectStore("pending", { keyPath: "id" });
        q.onsuccess = () => resolve(q.result); q.onerror = () => reject(q.error);
      });
      const tx = db.transaction("pending", "readwrite");
      tx.objectStore("pending").put({ id: "intent", baseVersion: 3,
        changes: { designation: "Unsynced" }, blob: new Blob(["original-photo"]) });
      await new Promise((resolve, reject) => {
        tx.oncomplete = resolve; tx.onabort = () => reject(tx.error);
      });
      db.close(); return name;
    });
    async function pendingData() {
      return page.evaluate(async name => {
        const db = await new Promise((resolve, reject) => {
          const q = indexedDB.open(name, 1);
          q.onsuccess = () => resolve(q.result); q.onerror = () => reject(q.error);
        });
        try {
          const row = await new Promise((resolve, reject) => {
            const q = db.transaction("pending").objectStore("pending").get("intent");
            q.onsuccess = () => resolve(q.result); q.onerror = () => reject(q.error);
          });
          return { id: row.id, baseVersion: row.baseVersion, changes: row.changes, blob: await row.blob.text() };
        } finally { db.close(); }
      }, localName);
    }
    const originalPending = await pendingData();
    for (const failure of ["missing", "interrupted"]) {
      generation = "failed-" + failure; assetFailure = failure;
      await page.evaluate(async () => {
        const registration = await navigator.serviceWorker.getRegistration();
        try { await registration.update(); throw new Error("Old worker URL unexpectedly updated"); }
        catch (error) { if (!(error instanceof TypeError)) throw error; }
      });
      const failed = await page.evaluate(async version => {
        const r = await navigator.serviceWorker.register("/service-worker.js?v=" + version, { scope: "/" });
        const worker = r.installing;
        if (worker && worker.state !== "redundant") await new Promise(resolve => {
          worker.addEventListener("statechange", () => { if (worker.state === "redundant") resolve(); });
        });
        return { active: r.active?.scriptURL, waiting: r.waiting?.scriptURL || null };
      }, generation);
      expect(failed.active).toContain("v=N"); expect(failed.waiting).toBeNull();
      await expect.poll(() => page.evaluate(() => caches.keys())).toEqual(["digues-shell-N"]);
      expect(await pendingData()).toEqual(originalPending);
      await context.setOffline(true);
      expect(await page.evaluate(async () => (await fetch("/static/css/modal.css")).text()))
        .toContain('--modal-cache-generation: "N"');
      await context.setOffline(false);
    }
    generation = "N"; assetFailure = null;

    await page.evaluate(async () => {
      const cache = await caches.open("digues-shell-N");
      await cache.delete("/static/css/modal.css?v=N");
    });

    await page.evaluate(async () => {
      const active = (await navigator.serviceWorker.getRegistration()).active;
      const id = new URL(active.scriptURL).searchParams.get("v");
      await caches.delete(`digues-shell-${id}`);
    });
    const recovered = await page.goto(`${origin}/carte`);
    expect(recovered.status()).toBe(200);
    await expect(page.locator("#generation")).toHaveText("N");
    await expect(page.locator("#ready")).toHaveText("ready");
    expect(await page.evaluate(() => caches.keys())).toEqual(["digues-shell-N"]);
    expect(await page.evaluate(async () => (await (await caches.open("digues-shell-N")).keys()).length)).toBe(0);

    const cleanContext = await browser.newContext();
    try {
      const cleanPage = await cleanContext.newPage();
      expect((await cleanPage.goto(`${origin}/carte`)).status()).toBe(200);
      await expect(cleanPage.locator("#ready")).toHaveText("ready");
    } finally {
      await cleanContext.close();
    }

    generation = "N2";
    await page.reload();
    await expect.poll(() => page.evaluate(async () =>
      (await navigator.serviceWorker.getRegistration())?.waiting?.scriptURL.includes("v=N2") || false,
    )).toBe(true);
    expect(await page.evaluate(() => caches.keys())).toEqual(["digues-shell-N", "digues-shell-N2"]);
    // The server already renders N2 before the waiting worker takes control.
    // Observe the update-triggered reload before inspecting its cache and CSS.
    const updatedPage = page.waitForEvent("load");
    await page.locator("#pwa-update-button").click();
    await updatedPage;
    await expect(page.locator("#generation")).toHaveText("N2");
    await expect(page.locator("#ready")).toHaveText("ready");
    await expect.poll(() => page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--modal-cache-generation").trim(),
    )).toBe('"N2"');
    await expect.poll(() => page.evaluate(() => caches.keys())).toEqual(["digues-shell-N2"]);
    expect(await page.evaluate(async () => {
      const cache = await caches.open("digues-shell-N2");
      return Boolean(await cache.match("/static/css/modal.css?v=N2"));
    })).toBe(true);

    expect(await pendingData()).toEqual(originalPending);
    await context.setOffline(true);
    expect(await page.evaluate(async () => (await fetch("/static/css/modal.css")).text()))
      .toContain('--modal-cache-generation: "N2"');
    await context.setOffline(false);
  } finally {
    await context.close();
    await new Promise(resolve => server.close(resolve));
  }
});
