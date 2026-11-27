import json
from pathlib import Path
import shutil
import subprocess
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"


class TopNavigationTest(unittest.TestCase):
    def test_optional_pwa_settings_are_guarded_and_incompatible(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const { createController } = require("./webapp/frontend/js/top-navigation.js");
let standalone = false;
const doc = { activeElement: null, addEventListener() {} };
function entry(name) {
  return {
    toggleButton: { addEventListener() {}, focus() { doc.activeElement = name; } },
    closeButton: { addEventListener() {} },
    panel: { contains() { return false; } },
    setOpen(open) { this.open = open; },
  };
}
const entries = Object.fromEntries(
  ["heritage", "editor", "ai", "settings"].map((name) => [name, entry(name)]),
);
entries.settings.canOpen = () => standalone;
const controller = createController({ entries, documentTarget: doc });
controller.setLayout(390, 844);
controller.open("heritage");
controller.open("settings");
assert.equal(controller.getState().settings, false);
assert.equal(controller.getState().heritage, true);
standalone = true;
controller.open("settings");
assert.deepEqual(controller.getState(), {
  heritage: false, ai: false, settings: true, editor: false,
});
controller.open("editor");
assert.equal(controller.getState().settings, false);
controller.open("settings");
controller.open("ai");
assert.equal(controller.getState().settings, false);
controller.open("settings");
controller.setLayout(1200, 800);
controller.open("heritage");
assert.equal(controller.getState().settings, false);
controller.open("settings");
assert.equal(controller.getState().heritage, false);
controller.close("settings");
assert.equal(controller.getState().settings, false);
const base = createController({ entries: { heritage: entry("heritage") }, documentTarget: doc });
assert.deepEqual(base.getState(), { heritage: false });
'''
        subprocess.run([node, "-e", program], cwd=FRONTEND_DIRECTORY.parents[1], check=True)

    def test_compact_dropdown_repositions_and_resets_after_rotation(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const TopNavigation = require("./webapp/frontend/js/top-navigation.js");
const source = fs.readFileSync("./webapp/frontend/js/map.js", "utf8");
let compactMode = "landscape";
const window = { matchMedia(query) { return { matches:
  compactMode === "landscape" ? query.includes("orientation: landscape")
    : compactMode === "portrait" ? query.includes("orientation: portrait") : false,
}; } };
const document = { documentElement: { clientWidth: 844 } };
const appHeader = { bottom: 72,
  getBoundingClientRect() { return { bottom: this.bottom }; } };
function button(left) { return { left, setAttribute(k,v) { this[k] = v; },
  getBoundingClientRect() { return { left: this.left, bottom: 56 }; } }; }
function menu() { return { hidden: true, style: {
  setProperty(k,v) { this[k] = v; }, removeProperty(k) { delete this[k]; },
}, getBoundingClientRect() { return { width: 288 }; } }; }
const createMenuButton = button(300), toolsMenuButton = button(700);
const createMenuList = menu(), toolsMenuList = menu();
eval("function setToolsMenuOpen" + source.split("function setToolsMenuOpen")[1]
  .split("function renderTerritoireModal")[0]);
eval("function setCreateMenuOpen" + source.split("function setCreateMenuOpen")[1]
  .split("function clearTronconDraft")[0]);
setCreateMenuOpen(true);
assert.equal(createMenuList.style["--compact-menu-left"], "300px");
assert.equal(createMenuList.style["--compact-menu-top"], "72px");
createMenuButton.left = 90; // scroll horizontal de la navigation
syncCompactLandscapeMenus();
assert.equal(createMenuList.style["--compact-menu-left"], "90px");
setCreateMenuOpen(false);
assert.equal(createMenuList.hidden, true);
assert.equal(createMenuButton["aria-expanded"], "false");
assert.equal(createMenuList.style["--compact-menu-left"], undefined);
setToolsMenuOpen(true);
assert.equal(toolsMenuList.style["--compact-menu-left"], "548px");
assert.equal(toolsMenuButton["aria-expanded"], "true");
compactMode = "portrait";
appHeader.bottom = 88;
syncCompactLandscapeMenus();
assert.equal(toolsMenuList.style["--compact-menu-left"], undefined);
assert.equal(toolsMenuList.style["--compact-menu-top"], "88px");
assert.equal(toolsMenuList.hidden, false);
compactMode = "wide";
syncCompactLandscapeMenus();
// The message row belongs to the header on desktop too. Menus remain below
// its real bottom after rotation instead of reverting to the button's edge.
assert.equal(toolsMenuList.style["--compact-menu-top"], "88px");
assert.equal(toolsMenuList.style["--compact-menu-left"], "548px");
compactMode = "landscape";
toolsMenuButton.left = 130;
syncCompactLandscapeMenus();
assert.equal(toolsMenuList.style["--compact-menu-left"], "130px");
assert.equal(toolsMenuList.style["--compact-menu-top"], "88px");
'''
        subprocess.run([node, "-e", program], cwd=FRONTEND_DIRECTORY.parents[1], check=True)

    def test_responsive_modes_transitions_and_focus(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const { createController, layoutMode } = require("./webapp/frontend/js/top-navigation.js");
const dimensions = [[390,844], [844,390], [768,1024], [820,1180],
  [1024,768], [1180,820], [1366,768], [1920,1080]];
assert.deepEqual(dimensions.map(([w,h]) => layoutMode(w,h)), [
  "single-tool", "split-docked", "split-balanced", "split-balanced",
  "split-docked", "split-docked", "split-docked", "split-docked"]);
assert.equal(layoutMode(699, 900), "single-tool");
assert.equal(layoutMode(700, 900), "split-balanced");
assert.equal(layoutMode(960, 1200), "split-docked");
const doc = { activeElement: null, addEventListener() {} };
let lastMode;
const entries = Object.fromEntries(["heritage", "create", "tools", "queries", "ai", "editor"]
  .map(name => [name, {
    hidden: true, ariaExpanded: "false", draft: "unchanged",
    panel: { contains(element) { return element?.owner === name; } },
    toggleButton: { addEventListener() {}, focus() { doc.activeElement = { toggle: name }; } },
    setOpen(open) { this.hidden = !open; this.ariaExpanded = String(open); },
  }]));
const controller = createController({ entries, documentTarget: doc,
  additionalCompatiblePairs: [["heritage", "ai"]],
  onChange(state, mode) {
    lastMode = mode;
    for (const [name, open] of Object.entries(state)) {
      assert.equal(entries[name].hidden, !open);
      assert.equal(entries[name].ariaExpanded, String(open));
    }
  },
});
const open = () => Object.entries(controller.getState()).filter(([, v]) => v).map(([k]) => k);
controller.setLayout(390, 844);
controller.open("queries");
controller.open("ai");
assert.deepEqual(open(), ["ai"]);
controller.open("queries");
assert.deepEqual(open(), ["queries"]);
controller.setLayout(768, 1024);
controller.open("ai");
assert.deepEqual(open(), ["queries", "ai"]);
assert.equal(lastMode, "split-balanced");
doc.activeElement = { owner: "queries" };
controller.setLayout(390, 844);
assert.deepEqual(open(), ["ai"]);
assert.equal(doc.activeElement.toggle, "ai");
controller.setLayout(844, 390);
assert.deepEqual(open(), ["ai"]); // aucun panneau rouvert implicitement
controller.open("queries");
assert.deepEqual(open(), ["queries", "ai"]);
assert.equal(lastMode, "split-docked");
controller.setLayout(390, 844);
assert.deepEqual(open(), ["queries"]); // dernier ouvert conservé dans l'autre sens
controller.setLayout(768, 1024);
controller.open("heritage");
controller.open("create");
assert.deepEqual(open(), ["heritage", "create"]);
controller.close("create");
controller.open("editor");
assert.deepEqual(open(), ["heritage", "editor"]);
doc.activeElement = { owner: "heritage" };
controller.setLayout(390, 844);
assert.deepEqual(open(), ["editor"]);
assert.equal(doc.activeElement.toggle, "editor");
assert.equal(entries.editor.draft, "unchanged");
controller.open("heritage");
assert.deepEqual(open(), ["heritage"]);
controller.open("editor");
assert.deepEqual(open(), ["editor"]);
controller.setLayout(1024, 768);
controller.open("heritage");
assert.deepEqual(open(), ["heritage", "editor"]);
controller.close("editor");
controller.open("ai");
assert.deepEqual(open(), ["heritage", "ai"]);
controller.setLayout(390, 844);
assert.deepEqual(open(), ["ai"]);
controller.open("queries");
assert.deepEqual(open(), ["queries"]);
doc.activeElement = { owner: "queries" };
controller.close("queries");
assert.deepEqual(open(), []);
assert.equal(doc.activeElement.toggle, "queries");
'''
        subprocess.run([node, "-e", program], cwd=FRONTEND_DIRECTORY.parents[1], check=True)

    def test_editor_map_interaction_creation_contract(self):
        page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")

        primary_start = page.index('id="primary-area"')
        banner_start = page.index('id="editor-map-interaction"')
        editor_start = page.index('id="editor-panel"')
        self.assertLess(primary_start, banner_start)
        self.assertLess(banner_start, editor_start)
        for expected in (
            'aria-describedby="editor-map-interaction-status"',
            'id="editor-map-interaction-status"',
            'id="finish-editor-map-interaction"',
            'id="cancel-editor-map-interaction"',
        ):
            self.assertEqual(page.count(expected), 1)

        suspension_rule = css.split(
            'main[data-layout="single-tool"].editor-map-mode .editor-panel {', 1
        )[1].split("}", 1)[0]
        self.assertIn("display: none", suspension_rule)
        self.assertNotIn("editor-map-mode", (FRONTEND_DIRECTORY / "data.html").read_text(
            encoding="utf-8"
        ))

        begin = script.split("function beginEditorMapInteraction", 1)[1].split(
            "function finishEditorMapInteraction", 1
        )[0]
        central_interaction = script.split(
            "function shouldSuspendEditorMapInteraction", 1
        )[1].split("function setCreateMenuOpen", 1)[0]
        presentation = script.split(
            "function syncEditorMapInteractionPresentation", 1
        )[1].split("function updateEditorMapInteractionInstruction", 1)[0]
        self.assertIn('topNavigation.open("editor")', begin)
        self.assertIn('mode === "single-tool"', script.split(
            "function shouldSuspendEditorMapInteraction", 1
        )[1].split("function setEditorMapNavigationLocked", 1)[0])
        self.assertIn('appRoot.classList.add("editor-map-mode")', presentation)
        self.assertIn("editorMapInteractionBanner.focus()", presentation)
        self.assertLess(
            presentation.index("editorMapInteractionBanner.focus()"),
            presentation.index('editorPanel.setAttribute("aria-hidden", "true")'),
        )
        self.assertIn("editorPanel.inert = true", presentation)
        self.assertIn("mapElement.inert = Boolean(mapCovered)", presentation)
        self.assertIn("headerNavigation.inert = locked", script)
        for forbidden in (
            'topNavigation.close("editor")',
            "closeDesordreDraft()",
            "closeHeritageDraft()",
            ".reset()",
        ):
            self.assertNotIn(forbidden, central_interaction)
        self.assertNotIn("display-mode", script)
        self.assertNotIn("isPWA", script)

        troncon_start = script.split(
            'startTronconDrawButton.addEventListener("click"', 1
        )[1].split("function cancelTronconCreationDrawing", 1)[0]
        desordre_start = script.split(
            'startDesordreDrawButton.addEventListener("click"', 1
        )[1].split("function cancelDesordreCreationDrawing", 1)[0]
        desordre_edit_prefix, desordre_create = desordre_start.split(
            'if (editorState.mode !== "create"', 1
        )
        self.assertIn("beginEditorMapInteraction", troncon_start)
        self.assertNotIn("fetchJson", troncon_start)
        self.assertIn('kind: "edit-desordre-Polygon"', desordre_edit_prefix)
        self.assertIn("startMarker", desordre_create)
        self.assertIn("startPolyline", desordre_create)
        self.assertIn("startPolygon", desordre_create)
        self.assertIn("beginEditorMapInteraction", desordre_create)
        self.assertNotIn("fetchJson", desordre_create)

        restore_troncon = script.split("function restoreTronconDraft", 1)[1].split(
            "function fillHeritageParentOptions", 1
        )[0]
        restore_desordre = script.split("function restoreDesordreDraft", 1)[1].split(
            "function updateDisorderEditorControls", 1
        )[0]
        self.assertIn("beginEditorMapInteraction", restore_troncon)
        self.assertIn("finishAvailable: true", restore_troncon)
        self.assertIn("beginEditorMapInteraction", restore_desordre)
        self.assertIn("finishAvailable: true", restore_desordre)

        drawing_end = script.split('map.on("editable:drawing:end"', 1)[1].split(
            'heritageObjectForm.addEventListener("submit"', 1
        )[0]
        self.assertIn("provisionalTronconLayer.enableEdit(map)", drawing_end)
        self.assertIn("provisionalDesordreLayer.enableEdit(map)", drawing_end)
        self.assertIn("setEditorMapInteractionFinishAvailable(true)", drawing_end)
        self.assertNotIn("finishEditorMapInteraction(", drawing_end)

        cancel_troncon = script.split(
            "function cancelTronconCreationDrawing", 1
        )[1].split('cancelTronconDrawButton.addEventListener', 1)[0]
        cancel_desordre = script.split(
            "function cancelDesordreCreationDrawing", 1
        )[1].split('cancelDesordreDrawButton.addEventListener', 1)[0]
        self.assertIn("clearTronconDraft({ keepRestorable: true })", cancel_troncon)
        self.assertIn("clearDesordreDraft({ keepRestorable: true })", cancel_desordre)

        existing_geometry_handlers = (
            script.split('function beginPointPositionEdit', 1)[1].split(
                'startMapPositionButton.addEventListener', 1
            )[0],
            script.split('startLineEditButton.addEventListener("click"', 1)[1].split(
                'cancelLineEditButton.addEventListener', 1
            )[0],
        )
        for handler, kind in zip(existing_geometry_handlers, ("Point", "LineString")):
            self.assertIn("beginEditorMapInteraction", handler)
            self.assertIn(f'kind: "edit-desordre-{kind}"', handler)

        heritage_submit = script.split(
            'heritageObjectForm.addEventListener("submit"', 1
        )[1].split("function selectedCoordinateFamily", 1)[0]
        desordre_submit = script.split(
            'desordreEditorForm.addEventListener("submit"', 1
        )[1].split("function generalEditInProgress", 1)[0]
        desordre_creation_submit = desordre_submit.split(
            'if (editorState.mode !== "create") return;', 1
        )[1]
        self.assertLess(
            heritage_submit.index("finishEditorMapInteraction"),
            heritage_submit.index("fetchJson"),
        )
        self.assertLess(
            desordre_creation_submit.index("finishEditorMapInteraction"),
            desordre_creation_submit.index("fetchJson"),
        )

    def test_existing_geometry_editor_map_interaction_contract(self):
        script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")

        snapshot_controls = script.split(
            "const existingGeometryEditFormControls", 1
        )[1].split("]);", 1)[0]
        for control in (
            "disorderFields.designation",
            "disorderFields.type",
            "disorderFields.categorie",
            "disorderFields.cote",
            "disorderFields.position",
            "disorderFields.source",
            "disorderFields.commentaire",
            "disorderFields.lieuDit",
            "disorderFields.dateDebut",
            "disorderFields.dateFin",
            "disorderFields.valid",
            "lineEditTroncons",
        ):
            self.assertIn(control, snapshot_controls)
        for geometric_control in (
            "disorderFields.x",
            "disorderFields.y",
            "disorderFields.longitude",
            "disorderFields.latitude",
            "disorderFields.reperage",
            "lineStart1",
            "lineStart2",
            "lineEnd1",
            "lineEnd2",
            "reperageFields",
            "polygonRepresentative",
        ):
            self.assertNotIn(geometric_control, snapshot_controls)

        for function_name in (
            "validatePointPositionEdit",
            "validateExistingLineEdit",
            "validateExistingPolygonEdit",
        ):
            self.assertIn(f"finishAction: {function_name}", script)
            self.assertIn(
                f'addEventListener("click", {function_name})',
                script,
            )

        for renderer, validator, end_marker in (
            (
                "renderPointServerFeature",
                "validatePointPositionEdit",
                'validateMapPositionButton.addEventListener',
            ),
            (
                "renderLineServerFeature",
                "validateExistingLineEdit",
                'validateLineEditButton.addEventListener',
            ),
            (
                "renderPolygonServerFeature",
                "validateExistingPolygonEdit",
                'validateDesordreDrawButton.addEventListener',
            ),
        ):
            validation = script.split(f"async function {validator}", 1)[1].split(
                end_marker, 1
            )[0]
            self.assertLess(
                validation.index(renderer),
                validation.index("restoreEditorMapInteractionFormSnapshot"),
            )
            self.assertLess(
                validation.index("restoreEditorMapInteractionFormSnapshot"),
                validation.index("finishEditorMapInteraction"),
            )

        polygon_stop = script.split("function stopPolygonEdit", 1)[1].split(
            "function cancelExistingPolygonEdit", 1
        )[0]
        self.assertIn("activePolygonLayer?.disableEdit?.()", polygon_stop)
        self.assertIn("setPolygonEditControls(false)", polygon_stop)
        self.assertIn("updatePolygonLayer(lastServerFeature)", polygon_stop)
        self.assertIn("renderPolygonServerFeature(lastServerFeature", polygon_stop)
        self.assertIn("finishEditorMapInteraction()", polygon_stop)

        for opener in ("openPointEditor", "openLineEditor", "openPolygonEditor"):
            body = script.split(f"async function {opener}", 1)[1].split(
                "await Promise.all", 1
            )[0]
            self.assertIn("editorMapInteractionBlocksObjectOpen()", body)

        close_handler = script.split(
            'closeEditorButton.addEventListener("click"', 1
        )[1].split("async function loadMapData", 1)[0]
        self.assertIn(
            "stopActiveExistingGeometryEdit({ restore: true })",
            close_handler,
        )

    def test_existing_geometry_snapshot_restores_supported_controls(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const source = fs.readFileSync("./webapp/frontend/js/map.js", "utf8");
function input(value) { return { tagName: "INPUT", type: "text", value }; }
const text = input("commentaire local");
const checkbox = { tagName: "INPUT", type: "checkbox", checked: true };
const ownerDocument = { createElement() { return {
  selected: false, textContent: "", value: "",
}; } };
const multiple = {
  tagName: "SELECT", type: "select-multiple", multiple: true, ownerDocument,
  options: [
    { selected: true, textContent: "Tronçon A", value: "a" },
    { selected: false, textContent: "Tronçon B", value: "b" },
  ],
  get selectedOptions() { return this.options.filter(option => option.selected); },
  append(option) { this.options.push(option); },
  value: "a",
};
const existingGeometryEditFormControls = [text, checkbox, multiple];
let editorMapInteraction = null;
eval("function captureExistingGeometryEditFormSnapshot" + source
  .split("function captureExistingGeometryEditFormSnapshot")[1]
  .split("function beginEditorMapInteraction")[0]);
const snapshot = captureExistingGeometryEditFormSnapshot();
text.value = "valeur serveur";
checkbox.checked = false;
multiple.options = [{ selected: true, textContent: "Tronçon B", value: "b" }];
restoreExistingGeometryEditFormSnapshot(snapshot);
assert.equal(text.value, "commentaire local");
assert.equal(checkbox.checked, true);
assert.deepEqual(
  multiple.options.filter(option => option.selected).map(option => option.value),
  ["a"],
);
'''
        subprocess.run([node, "-e", program], cwd=FRONTEND_DIRECTORY.parents[1], check=True)

    def test_editor_map_interaction_presentation_at_layout_boundary(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { layoutMode } = require("./webapp/frontend/js/top-navigation.js");
const source = fs.readFileSync("./webapp/frontend/js/map.js", "utf8");

class ClassList {
  constructor() { this.values = new Set(); }
  add(name) { this.values.add(name); }
  remove(name) { this.values.delete(name); }
  toggle(name, force) {
    if (force) this.values.add(name); else this.values.delete(name);
  }
  contains(name) { return this.values.has(name); }
}
const document = { activeElement: null };
function element({ hidden = false, left = 0, right = 0 } = {}) {
  return {
    attributes: {}, classList: new ClassList(), disabled: false, hidden, inert: false,
    listeners: {}, value: "",
    addEventListener(name, callback) { this.listeners[name] = callback; },
    focus() { document.activeElement = this; },
    getBoundingClientRect() { return { left, right }; },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
  };
}

const appRoot = element();
const editorPanel = element({ left: 600 });
const editorMapInteractionBanner = element({ hidden: true });
const editorMapInteractionStatus = element();
const finishEditorMapInteractionButton = element({ hidden: true });
const cancelEditorMapInteractionButton = element();
const mapElement = element();
const heritagePanel = element({ right: 300 });
const heritageToggleButton = element();
const createMenuButton = element();
const toolsMenuButton = element();
const aiToggleButton = element();
const headerNavigation = element();
const closeEditorButton = element();
const submitButton = element();
const restoreButton = element();
let editorMapInteraction = null;
let editorMapPresentationSuspended = false;
let activeBusinessEditor = null;
// Host rendering is outside this isolated layout contract. Its actual DOM is
// covered by editor-map-interaction.spec.js on disposable PostgreSQL.
let legacyPresentations = 0;
function syncLegacyEditorPresentation() { legacyPresentations++; }
eval(source.slice(source.indexOf('function editorCloseTarget()'),
  source.indexOf('const businessEditorPanel')));
function queuePendingMapEntityRefresh() {}
function syncGpsControls() {}
function cancelGpsCandidate() {}
const gpsTronconTracking = { refresh() {} };
const gpsDesordreTracking = { refresh() {} };
const gpsCandidatePanel = { contains() { return false; } };
let currentTopNavigationState = {
  ai: false, create: false, editor: true, heritage: false, tools: false,
};
let currentTopNavigationLayout = layoutMode(390, 844);
const topNavigation = {
  editor: true,
  opens: [],
  open(name) { this.opens.push(name); assert.equal(name, "editor"); },
};
let editorConflictCleared = false;
const entityEditor = {
  setConflict(value) { editorConflictCleared = value === false; },
};

eval("function shouldSuspendEditorMapInteraction" + source
  .split("function shouldSuspendEditorMapInteraction")[1]
  .split('cancelEditorMapInteractionButton.addEventListener("click"')[0]);

const formField = { value: "valeur conservée" };
let cancelled = false;
beginEditorMapInteraction({
  kind: "create-troncon",
  instruction: "Tracez",
  cancel() { cancelled = true; },
  returnFocusTarget: submitButton,
  cancelFocusTarget: restoreButton,
});
assert.deepEqual(topNavigation.opens, ["editor"]);
assert.equal(editorMapInteraction.kind, "create-troncon");
assert.equal(editorMapInteractionBanner.hidden, false);
assert.equal(appRoot.classList.contains("editor-map-mode"), true);
assert.equal(editorPanel.inert, true);
assert.equal(editorPanel.attributes["aria-hidden"], "true");
assert.equal(mapElement.inert, false);
assert.equal(document.activeElement, editorMapInteractionBanner);
assert.equal(finishEditorMapInteractionButton.hidden, true);
assert.equal(headerNavigation.inert, true);
assert.equal(formField.value, "valeur conservée");

setEditorMapInteractionFinishAvailable(true);
assert.equal(finishEditorMapInteractionButton.hidden, false);
syncEditorMapInteractionPresentation(currentTopNavigationState, layoutMode(699, 900));
assert.equal(editorMapInteractionBanner.hidden, false);
assert.equal(editorPanel.inert, true);
assert.equal(mapElement.inert, false);
syncEditorMapInteractionPresentation(currentTopNavigationState, layoutMode(700, 900));
assert.equal(editorMapInteractionBanner.hidden, true);
assert.equal(appRoot.classList.contains("editor-map-mode"), false);
assert.equal(editorPanel.inert, false);
assert.equal(editorPanel.attributes["aria-hidden"], undefined);
assert.equal(mapElement.inert, false);
assert.equal(editorMapInteraction.kind, "create-troncon");
assert.equal(headerNavigation.inert, true);

syncEditorMapInteractionPresentation(currentTopNavigationState, layoutMode(1280, 800));
assert.equal(editorMapInteractionBanner.hidden, true);
assert.equal(mapElement.inert, false);
syncEditorMapInteractionPresentation(currentTopNavigationState, layoutMode(699, 900));
assert.equal(editorPanel.inert, true);
assert.equal(mapElement.inert, false);
assert.equal(headerNavigation.inert, true);

finishEditorMapInteraction();
assert.equal(editorMapInteraction, null);
assert.equal(appRoot.classList.contains("editor-map-mode"), false);
assert.equal(editorPanel.inert, false);
assert.equal(mapElement.inert, true);
assert.equal(document.activeElement, submitButton);
assert.equal(headerNavigation.inert, false);
assert.equal(topNavigation.editor, true);
assert.equal(formField.value, "valeur conservée");

beginEditorMapInteraction({
  kind: "create-desordre-Point",
  instruction: "Placez le point",
  cancel() { cancelled = true; },
  returnFocusTarget: submitButton,
  cancelFocusTarget: restoreButton,
});
cancelEditorMapInteraction();
assert.equal(cancelled, true);
assert.equal(editorConflictCleared, true);
assert.equal(editorMapInteraction, null);
assert.equal(document.activeElement, restoreButton);
assert.equal(formField.value, "valeur conservée");
assert.ok(legacyPresentations >= 2);
const composedCloseButton = element();
activeBusinessEditor = { formView: { close: composedCloseButton } };
beginEditorMapInteraction({ kind: "business-point", instruction: "Point", cancel() {} });
finishEditorMapInteraction();
assert.equal(document.activeElement, composedCloseButton);
assert.equal(formField.value, "valeur conservée");
'''
        subprocess.run([node, "-e", program], cwd=FRONTEND_DIRECTORY.parents[1], check=True)

    def test_fixed_menu_position_tracks_anchor_and_stays_inside_viewport(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test de navigation frontend.")

        program = r'''
const { fixedMenuPosition } = require("./webapp/frontend/js/top-navigation.js");
process.stdout.write(JSON.stringify({
  regular: fixedMenuPosition({ left: 120, bottom: 64 }, 288, 844),
  clippedRight: fixedMenuPosition({ left: 700, bottom: 64 }, 288, 844),
  scrolledLeft: fixedMenuPosition({ left: -40, bottom: 64 }, 288, 844),
}));
'''
        result = json.loads(
            subprocess.check_output(
                [node, "-e", program],
                cwd=FRONTEND_DIRECTORY.parents[1],
                text=True,
                encoding="utf-8",
            )
        )
        self.assertEqual(result["regular"], {"left": 120, "top": 64})
        self.assertEqual(result["clippedRight"], {"left": 548, "top": 64})
        self.assertEqual(result["scrolledLeft"], {"left": 8, "top": 64})

    def test_compatibility_matrix_close_buttons_and_outside_clicks(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible pour le test de navigation frontend.")

        program = r'''
const { createController } = require("./webapp/frontend/js/top-navigation.js");

class EventTarget {
  constructor() {
    this.listeners = {};
  }
  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }
  click(target = this) {
    this.listeners.click({ target });
  }
}

function fixture() {
  const names = ["heritage", "create", "tools", "queries", "ai"];
  const toggles = Object.fromEntries(names.map((name) => [name, new EventTarget()]));
  const closes = Object.fromEntries(
    ["heritage", "queries", "ai"].map((name) => [name, new EventTarget()]),
  );
  const rendered = Object.fromEntries(
    names.map((name) => [name, { hidden: true, ariaExpanded: "false" }]),
  );
  const documentTarget = new EventTarget();
  const controller = createController({
    entries: Object.fromEntries(names.map((name) => [name, {
      toggleButton: toggles[name],
      closeButton: closes[name],
      setOpen(open) {
        rendered[name].hidden = !open;
        rendered[name].ariaExpanded = String(open);
      },
    }])),
    documentTarget,
  });
  return { controller, toggles, closes, rendered, documentTarget };
}

function openNames(controller) {
  return Object.entries(controller.getState())
    .filter(([, open]) => open)
    .map(([name]) => name);
}

function transition(sequence) {
  const context = fixture();
  for (const name of sequence) {
    context.toggles[name].click();
  }
  return openNames(context.controller);
}

const transitions = {
  heritageTools: transition(["heritage", "tools"]),
  toolsCreate: transition(["tools", "create"]),
  createQueries: transition(["create", "queries"]),
  queriesAi: transition(["queries", "ai"]),
  aiQueries: transition(["ai", "queries"]),
  queriesAiHeritage: transition(["queries", "ai", "heritage"]),
  queriesAiTools: transition(["queries", "ai", "tools"]),
  toggleClosed: transition(["heritage", "heritage"]),
};

const closeContext = fixture();
closeContext.toggles.heritage.click();
closeContext.closes.heritage.click();
const heritageClosed = openNames(closeContext.controller);
closeContext.toggles.queries.click();
closeContext.toggles.ai.click();
closeContext.closes.queries.click();
const queriesClosed = openNames(closeContext.controller);
closeContext.closes.ai.click();
const aiClosed = openNames(closeContext.controller);

const outsideContext = fixture();
const insideCreate = { closest(selector) { return selector === ".create-menu" ? this : null; } };
const insideTools = { closest(selector) { return selector === ".tools-menu" ? this : null; } };
const outside = { closest() { return null; } };
outsideContext.toggles.create.click();
outsideContext.documentTarget.click(insideCreate);
const createInside = openNames(outsideContext.controller);
outsideContext.documentTarget.click(outside);
const createOutside = openNames(outsideContext.controller);
outsideContext.toggles.tools.click();
outsideContext.documentTarget.click(insideTools);
const toolsInside = openNames(outsideContext.controller);
outsideContext.documentTarget.click(outside);
const toolsOutside = openNames(outsideContext.controller);

const synchronized = Object.entries(outsideContext.controller.getState()).every(
  ([name, open]) =>
    outsideContext.rendered[name].hidden === !open &&
    outsideContext.rendered[name].ariaExpanded === String(open),
);

process.stdout.write(JSON.stringify({
  transitions,
  heritageClosed,
  queriesClosed,
  aiClosed,
  createInside,
  createOutside,
  toolsInside,
  toolsOutside,
  synchronized,
}));
'''
        result = json.loads(
            subprocess.check_output(
                [node, "-e", program],
                cwd=FRONTEND_DIRECTORY.parents[1],
                text=True,
                encoding="utf-8",
            )
        )

        self.assertEqual(result["transitions"], {
            "heritageTools": ["tools"],
            "toolsCreate": ["create"],
            "createQueries": ["queries"],
            "queriesAi": ["queries", "ai"],
            "aiQueries": ["queries", "ai"],
            "queriesAiHeritage": ["heritage"],
            "queriesAiTools": ["tools"],
            "toggleClosed": [],
        })
        self.assertEqual(result["heritageClosed"], [])
        self.assertEqual(result["queriesClosed"], ["ai"])
        self.assertEqual(result["aiClosed"], [])
        self.assertEqual(result["createInside"], ["create"])
        self.assertEqual(result["createOutside"], [])
        self.assertEqual(result["toolsInside"], ["tools"])
        self.assertEqual(result["toolsOutside"], [])
        self.assertTrue(result["synchronized"])


    def test_controller_accepts_page_specific_entry_sets(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const assert = require("node:assert/strict");
const { createController } = require("./webapp/frontend/js/top-navigation.js");
const documentTarget = { activeElement: null, addEventListener() {} };
const makeEntry = () => ({
  toggleButton: { addEventListener() {} },
  setOpen(open) { this.open = open; },
});
const entries = { heritage: makeEntry(), queries: makeEntry(), ai: makeEntry() };
const controller = createController({ entries, documentTarget });
controller.setLayout(1000, 700);
controller.open("queries");
controller.open("ai");
assert.deepEqual(controller.getState(), { heritage: false, queries: true, ai: true });
controller.open("heritage");
assert.deepEqual(controller.getState(), { heritage: true, queries: false, ai: false });
'''
        subprocess.run(
            [node, "-e", program],
            cwd=FRONTEND_DIRECTORY.parents[1],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
