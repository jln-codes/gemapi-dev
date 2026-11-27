import subprocess
import unittest


class AppHeaderTest(unittest.TestCase):
    def test_capabilities_keep_presentation_separate_from_runtime_context(self):
        subprocess.run(["node", "-e", r'''
const assert = require("node:assert/strict");
const { DATA_MIN_WIDTH, capabilities, isStandalone, normalizeNetworkState, networkStateLabel, networkStateIcon } = require("./webapp/frontend/js/app-header.js");
assert.equal(DATA_MIN_WIDTH, 700);
assert.deepEqual(capabilities({ interfaceType: "map", width: 699, standalone: false }), {
  canAccessData: false,
  canShowInterfaceSwitch: false,
  canUseInterInterfacePreferences: false,
  canUseDesktopMapTools: true,
  canShowExternalMapLayers: true,
  canUseFieldMapTools: true,
});
assert.deepEqual(capabilities({ interfaceType: "map", width: 700, standalone: false }), {
  canAccessData: true,
  canShowInterfaceSwitch: true,
  canUseInterInterfacePreferences: true,
  canUseDesktopMapTools: true,
  canShowExternalMapLayers: true,
  canUseFieldMapTools: true,
});
assert.deepEqual(capabilities({ interfaceType: "map", width: 1200, standalone: true }), {
  canAccessData: false,
  canShowInterfaceSwitch: false,
  canUseInterInterfacePreferences: false,
  canUseDesktopMapTools: false,
  canShowExternalMapLayers: false,
  canUseFieldMapTools: true,
});
assert.deepEqual(capabilities({ interfaceType: "data", width: 768, standalone: false }), {
  canAccessData: true,
  canShowInterfaceSwitch: true,
  canUseInterInterfacePreferences: true,
  canUseDesktopMapTools: false,
  canShowExternalMapLayers: false,
  canUseFieldMapTools: false,
});
assert.equal(isStandalone({ navigator: { standalone: true }, matchMedia() { return { matches: false }; } }), true);
assert.equal(isStandalone({ navigator: {}, matchMedia() { return { matches: true }; } }), true);
assert.equal(isStandalone({ navigator: {}, matchMedia() { return { matches: false }; } }), false);
assert.equal(normalizeNetworkState(), "unknown");
assert.equal(normalizeNetworkState({ status: "checking", offline: null }), "checking");
assert.equal(normalizeNetworkState({ offline: false }), "online");
assert.equal(normalizeNetworkState({ offline: true }), "offline");
assert.equal(networkStateLabel("unknown"), "État du serveur non déterminé");
assert.equal(networkStateLabel("checking"), "Vérification de l’accès au serveur");
assert.equal(networkStateLabel("online"), "Serveur accessible");
assert.equal(networkStateLabel("offline"), "Serveur inaccessible — mode local");
assert.equal(networkStateIcon("unknown", false), "network");
assert.equal(networkStateIcon("checking", true), "wifi");
assert.equal(networkStateIcon("online", false), "network");
assert.equal(networkStateIcon("offline", false), "network-off");
assert.equal(networkStateIcon("online", true), "wifi");
assert.equal(networkStateIcon("offline", true), "wifi-off");
assert.throws(() => capabilities({ interfaceType: "other", width: 1200, standalone: false }));
assert.ok(Object.values(capabilities({ interfaceType: "admin", width: 1200, standalone: false })).every(value => value === false));
'''], check=True)

    def test_both_interfaces_expose_the_disorder_catalogue_tool(self):
        subprocess.run(["node", "-e", r'''
const assert = require("node:assert/strict");
const Header = require("./webapp/frontend/js/app-header.js");
const header = { innerHTML: "" };
Header.render(header, "data");
assert.match(header.innerHTML, /data-tool-action="disorder-catalogue"/);
assert.match(header.innerHTML, />Outils</);
assert.match(header.innerHTML, /aria-controls="tools-menu-list"/);
assert.match(header.innerHTML, /id="toggle-heritage"/);
const commands = [...header.innerHTML.matchAll(/<button id="([^"]+)"/g)].map(match => match[1]);
const statisticsIndex = commands.indexOf("show-statistics");
assert.deepEqual(commands.slice(statisticsIndex, statisticsIndex + 2),
  ["show-statistics", "toggle-views"]);
assert.doesNotMatch(header.innerHTML.split('<nav class="main-navigation"')[1].split('</nav>')[0], /toggle-heritage/);
assert.doesNotMatch(header.innerHTML.split('<nav class="main-navigation"')[1].split('</nav>')[0], /view-toggle/);
assert.equal((header.innerHTML.match(/id="toggle-heritage"/g) || []).length, 1);
assert.match(header.innerHTML, /class="heritage-tab"[^>]*aria-controls="heritage-panel"/);
assert.match(header.innerHTML, /id="show-statistics"[^>]*aria-controls="data-statistics-view"[^>]*>Statistiques</);
assert.match(header.innerHTML, /class="header-actions"/);
const actions = header.innerHTML.split('<div class="header-actions"')[1].split('<p id="status"')[0];
for (const token of ['class="view-toggle"', 'id="open-business-session"', 'id="network-status"']) {
  assert.ok(actions.includes(token));
}
assert.ok(actions.indexOf('class="view-toggle"') < actions.indexOf('id="open-business-session"'));
assert.ok(actions.indexOf('id="open-business-session"') < actions.indexOf('id="network-status"'));
assert.match(header.innerHTML, /id="open-business-session"/);
assert.match(header.innerHTML, /id="network-status"/);
const statusTag = header.innerHTML.match(/<span id="network-status"[^>]*>/)?.[0];
assert.ok(statusTag);
assert.match(statusTag, /class="header-action header-network-status"/);
assert.match(statusTag, /role="img"/);
assert.match(statusTag, /aria-label="État du serveur en cours de vérification"/);
assert.match(statusTag, /title="État du serveur en cours de vérification"/);
assert.doesNotMatch(statusTag, /tabindex|aria-disabled|onclick/);
assert.doesNotMatch(header.innerHTML, /<button id="network-status"/);
assert.match(header.innerHTML, /<button id="open-business-session" class="header-action"/);
for (const icon of ["network", "network-off", "wifi", "wifi-off"]) {
  assert.match(header.innerHTML, new RegExp(`data-network-icon="${icon}"`));
}
function glyphPaths(name) {
  const svg = header.innerHTML.match(new RegExp(`<svg data-network-icon="${name}"[^>]*>(.*?)</svg>`))?.[1];
  assert.ok(svg, `SVG ${name} manquant`);
  return [...svg.matchAll(/<path\b[^>]*\bd="([^"]+)"/g)].map(match => match[1]);
}
assert.deepEqual(glyphPaths("network"), [
  "M0 0h24v24H0z",
  "M6 9a6 6 0 1 0 12 0a6 6 0 0 0 -12 0",
  "M12 3c1.333 .333 2 2.333 2 6s-.667 5.667 -2 6",
  "M12 3c-1.333 .333 -2 2.333 -2 6s.667 5.667 2 6",
  "M6 9h12", "M3 20h7", "M14 20h7",
  "M10 20a2 2 0 1 0 4 0a2 2 0 0 0 -4 0", "M12 15v3",
]);
assert.deepEqual(glyphPaths("network-off"), [
  "M0 0h24v24H0z",
  "M6.528 6.536a6 6 0 0 0 7.942 7.933m2.247 -1.76a6 6 0 0 0 -8.427 -8.425",
  "M12 3c1.333 .333 2 2.333 2 6c0 .337 -.006 .66 -.017 .968m-.55 3.473c-.333 .884 -.81 1.403 -1.433 1.559",
  "M12 3c-.936 .234 -1.544 1.29 -1.822 3.167m-.16 3.838c.116 3.029 .776 4.695 1.982 4.995",
  "M6 9h3m4 0h5", "M3 20h7", "M14 20h7",
  "M10 20a2 2 0 1 0 4 0a2 2 0 0 0 -4 0", "M12 15v3", "M3 3l18 18",
]);
for (const name of ["network", "network-off"]) {
  const svg = header.innerHTML.match(new RegExp(`<svg data-network-icon="${name}"[^>]*>(.*?)</svg>`))?.[0];
  assert.match(svg, /xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  assert.match(svg, /<path stroke="none" d="M0 0h24v24H0z" fill="none"\/>/);
  assert.doesNotMatch(svg, /<circle\b/);
}
assert.equal((header.innerHTML.match(/class="header-action"/g) || []).length, 1);
assert.equal((header.innerHTML.match(/aria-disabled="true"/g) || []).length, 1);
assert.equal((header.innerHTML.match(/stroke="currentColor"/g) || []).length, 7);
assert.equal((header.innerHTML.match(/aria-hidden="true"/g) || []).length, 9);
assert.match(header.innerHTML, /aria-label="Base"[^>]*aria-current="page"/);
assert.match(header.innerHTML, /href="\/carte"[^>]*aria-label="Carte"[^>]*title="Ouvrir Cartographie"/);
Header.render(header, "map");
assert.match(header.innerHTML, /aria-label="Carte"[^>]*aria-current="page"/);
assert.match(header.innerHTML, /href="\/base"[^>]*aria-label="Base"[^>]*title="Ouvrir Base de données"/);
assert.doesNotMatch(header.innerHTML, /show-statistics/);
assert.match(header.innerHTML, /data-tool-action="disorder-catalogue"/);
assert.match(header.innerHTML, /data-tool-action="print-map"/);
assert.match(header.innerHTML, /class="header-actions"/);
Header.render(header, "admin");
assert.match(header.innerHTML, /href="\/diagnostics"/);
assert.doesNotMatch(header.innerHTML, /toggle-ai|toggle-pwa|view-switcher|Patrimoine|header-actions/);
assert.doesNotThrow(() => Header.createController({ header, interfaceType: "admin" }));
'''], check=True)


if __name__ == "__main__":
    unittest.main()
