// Seul le shell technique est disponible hors ligne. Aucune file métier n'est ouverte.
(() => {
  "use strict";
  const button = document.querySelector("#session-logout");
  if (!button) return;
  const loginLink = document.querySelector("#session-login");
  const status = document.querySelector("#status");
  const channel = new BroadcastChannel("gemapi-session");
  let identity = null;
  let generation = 0;
  let lastActivity = 0;
  let activityInterval = 30000;
  const options = {credentials: "same-origin", cache: "no-store"};

  function clear() {
    generation++;
    identity = null;
    lastActivity = 0;
    button.hidden = true;
    loginLink.hidden = !navigator.onLine;
    window.BusinessRuntime?.stop();
  }
  async function refresh() {
    const captured = ++generation;
    try {
      const res = await fetch("/api/auth/me", options);
      const next = res.ok ? await res.json() : null;
      if (generation !== captured) return;
      if (!next || next.context !== identity?.context) clear();
      identity = next;
      activityInterval = Number(res.headers.get("X-Gemapi-Activity-Interval")) || 30000;
      button.hidden = !identity;
      loginLink.hidden = Boolean(identity) || !navigator.onLine;
    } catch {
      if (generation === captured) clear();
    }
  }
  function submit(path) {
    return fetch("/api/auth/" + path, {...options, method: "POST",
      headers: {"X-Gemapi-CSRF": "1", "X-Gemapi-Context": identity.context}});
  }
  async function activity(event) {
    if (!event.isTrusted || !identity || Date.now() - lastActivity < activityInterval) return;
    lastActivity = Date.now();
    const captured = generation;
    try {
      const res = await submit("activity");
      if (!res.ok && captured === generation) await refresh();
    } catch { if (generation === captured) clear(); }
  }
  button.addEventListener("click", async () => {
    if (!identity) return;
    button.disabled = true;
    try {
      const res = await submit("logout");
      if (res.ok) {
        clear();
        channel.postMessage("changed");
        location.replace("/");
      } else {
        await refresh();
        status.textContent = "La session a changé. Vérifiez la déconnexion.";
      }
    } catch { status.textContent = "Déconnexion non confirmée. Réessayez en ligne."; }
    finally { button.disabled = false; }
  });
  channel.onmessage = () => { clear(); void refresh(); };
  addEventListener("pagehide", clear);
  addEventListener("offline", clear);
  addEventListener("online", refresh);
  addEventListener("pageshow", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
  document.addEventListener("pointerdown", activity);
  document.addEventListener("keydown", activity);
})();
