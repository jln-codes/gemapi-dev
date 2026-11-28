(() => {
  "use strict";
  const form = document.querySelector("form");
  const login = document.querySelector("#login");
  const password = document.querySelector("#password");
  const button = form.querySelector("button");
  const status = form.querySelector("[role=status]");
  const options = {method: "POST", credentials: "same-origin", cache: "no-store",
    headers: {"Content-Type": "application/json", "X-Gemapi-CSRF": "1"}};
  let edited = false;
  form.addEventListener("input", () => { edited = true; });
  addEventListener("pagehide", () => { form.reset(); edited = true; });
  fetch("/api/auth/demo", options).then(async response => {
    if (!response.ok) return;
    const demo = await response.json();
    if (!edited && demo.login && !login.value && !password.value) {
      login.value = demo.login;
      password.value = demo.password;
    }
  }).catch(() => {});
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (button.disabled) return;
    edited = true;
    button.disabled = true;
    status.textContent = "Connexion en cours…";
    try {
      const request = fetch("/api/auth/login", {...options,
        body: JSON.stringify({login: login.value, password: password.value})});
      password.value = "";
      const response = await request;
      if (!response.ok) {
        status.textContent = (await response.json()).detail || "Connexion impossible.";
        password.focus();
        return;
      }
      const channel = new BroadcastChannel("gemapi-session");
      channel.postMessage("changed");
      channel.close();
      location.replace("/carte");
    } catch {
      status.textContent = "Serveur inaccessible. Vérifiez la connexion.";
    } finally {
      button.disabled = false;
    }
  });
  button.disabled = false;
})();
