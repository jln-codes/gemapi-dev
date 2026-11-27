import json
from pathlib import Path
import shutil
import subprocess
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"


NODE_CONTROLLER_HARNESS = r'''
const Detail = require(__MODULE__);
globalThis.EntityFormView=require(__MODULE__.replace("data-entity-detail.js","entity-form-view.js"));
// Explicit acceptance for scenarios which intentionally cancel a dirty draft.
globalThis.confirm = () => true;
class Element {
  constructor(ownerDocument=null,tag="div") {
    this.ownerDocument=ownerDocument;this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.dataset={};
    this.hidden=false;this.disabled=false;this.textContent="";this.value="";this.checked=false;this.type="";this.name="";
    this.classList={add(){},remove(){},toggle(){}};
  }
  append(...items) { for(const item of items){item.remove?.();item.parentElement=this;this.children.push(item);} }
  replaceChildren(...items) {this.children=[];this.append(...items);}
  insertBefore(item,other){item.remove?.();item.parentElement=this;this.children.splice(this.children.indexOf(other),0,item);}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);this.parentElement=null;}
  // The compositor registers a save handler and a capture guard. Preserve
  // both listeners; replacing the first silently bypassed every save test.
  addEventListener(name,callback){
    const previous=this.listeners[name];
    this.listeners[name]=previous?event=>Promise.all([previous(event),callback(event)]):callback;
  }
  setAttribute(name,value){this[name]=String(value);}
  querySelector(){return null;}
  querySelectorAll(){return [];}
  reportValidity(){return true;}
  get elements(){const values={};const visit=n=>{if(n.name)values[n.name]=n;n.children.forEach(visit);};this.children.forEach(visit);return values;}
  focus(){this.focused=true;}

}
function createEnvironment(fetchJson, onSaved=()=>{}, onMutationConfirmed=()=>{},onClose=()=>{}) {
  const documentTarget={createElement:tag=>new Element(documentTarget,tag)};
  const ids=["data-entity-detail", "data-entity-detail-back", "data-entity-detail-title",
    "data-entity-detail-status", "data-entity-detail-read", "data-entity-detail-edit",
    "data-entity-detail-edit-actions", "data-entity-detail-form", "data-entity-detail-save",
    "data-entity-detail-edit-fields", "data-entity-detail-error"];
  const elements=Object.fromEntries(ids.map((id)=>[id,new Element(documentTarget)]));
  elements["data-entity-detail"].hidden=true;
  elements["data-entity-detail-form"].hidden=true;
  elements["data-entity-detail-edit-actions"].hidden=true;
  const eventTarget=new Element(documentTarget);
  const root={ownerDocument:documentTarget,
    querySelector:(selector)=>elements[selector.slice(1)]||null};
  const controller=Detail.createController({rootElement:root,eventTarget,fetchJson,onSaved,onMutationConfirmed,onClose});
  const ports={...elements};elements.controller=controller;
  for(const [name,key] of [["form","form"],["status","status"],["error","error"],["save","save"],["edit-fields","fieldset"]])
    Object.defineProperty(elements,`data-entity-detail-${name}`,{get:()=>controller.getFormView()?.[key]||ports[`data-entity-detail-${name}`]});
  return {controller,elements};
}
function editControl(elements, name) {
  return elements.controller.getFormView().controls.get(name);
}
function displayedFields(elements) {
  const result={};
  elements["data-entity-detail-read"].children.forEach((section)=>{
    if (!section.children[1]) return;
    section.children[1].children.forEach((row)=>{
      result[row.children[0].textContent]=row.children[1].textContent;
    });
  });
  return result;
}
__SCENARIO__
'''


def run_controller_scenario(module: Path, scenario: str) -> dict:
    program = NODE_CONTROLLER_HARNESS.replace(
        "__MODULE__", json.dumps(str(module))
    ).replace("__SCENARIO__", scenario)
    return json.loads(subprocess.check_output(["node", "-e", program], text=True))


class DataEntityDetailTest(unittest.TestCase):
    def test_unlocated_disorder_keeps_metadata_and_explicit_zone_labels(self):
        # Read projection only: NULL geometry must not acquire an implicit Point.
        module = FRONTEND_DIRECTORY / 'js' / 'data-entity-detail.js'
        program = f'''const Detail=require({json.dumps(str(module))});
const payload={{type:'Feature',geometry:null,properties:{{id:'d1',designation:'Sans géométrie',valid:false,
  commentaire:'',zones_stockage:[{{id:'z1',designation:'Zone conservée'}}]}}}};
const detail=Detail.normalizeDetail('desordres',payload);
const autonomous=Detail.normalizeDetail('desordres',{{...payload,properties:{{...payload.properties,zones_stockage:[]}}}});
const ordinary=Detail.normalizeDetail('desordres',{{type:'Feature',geometry:{{type:'Point',coordinates:[1,2]}},properties:{{id:'d2'}}}});
console.log(JSON.stringify({{detail,autonomous,ordinary,geometry:payload.geometry}}));'''
        result = json.loads(subprocess.check_output(['node', '-e', program], text=True))
        self.assertIsNone(result['geometry'])
        self.assertFalse(result['detail']['values']['valid'])
        self.assertEqual(result['detail']['values']['commentaire'], '')
        self.assertEqual(result['detail']['values']['localisation'], 'Géométrie non renseignée')
        self.assertEqual(result['detail']['values']['zones_stockage_labels'], 'Zone conservée')
        self.assertEqual(result['autonomous']['values']['zones_stockage_labels'], 'Aucune')
        self.assertEqual(result['ordinary']['values']['zones_stockage_labels'], '—')

    @classmethod
    def setUpClass(cls):
        cls.page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        cls.script = (FRONTEND_DIRECTORY / "js" / "data-entity-detail.js").read_text(
            encoding="utf-8"
        )

    def test_shared_detail_replaces_disorder_specific_component(self):
        self.assertIn('id="data-entity-detail"',self.page)
        self.assertIn('function createHost(',self.script)
        self.assertIn('factory.create(',self.script)
        self.assertNotIn('id="data-entity-detail-cancel"', self.page)
        self.assertIn('edit.textContent = "Modifier"', self.script)
        self.assertIn('edit.textContent = "Annuler"', self.script)
        self.assertIn(
            'edit.addEventListener("click", () => {',
            self.script,
        )
        self.assertIn('src="/static/js/data-entity-detail.js"', self.page)
        self.assertNotIn("data-disorder-detail", self.page)
        self.assertNotIn("heritage-properties", self.page)

    def test_disorder_detail_exposes_the_contextual_pdf_action(self):
        self.assertIn('pdf.textContent = "Fiche PDF"', self.script)
        self.assertIn("createDisorderPdfController", self.script)
        self.assertIn(
            'current.entityClass === "desordres" && globalThis.DisorderReports',
            self.script,
        )
        self.assertNotIn('data-tool-action="disorder-pdf"', self.page)

    def test_all_requested_classes_share_the_same_cycle_and_endpoints(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        program = f'''const Detail = require({json.dumps(str(module))});
const configs = Object.fromEntries(Object.entries(Detail.ENTITY_CONFIGS).map(([key, value]) => [key, {{
  endpoint: value.endpoint,
  editable: value.fields.filter((field) => field[3]).map((field) => field[0]),
}}]));
const disorder = Detail.normalizeDetail("desordres", {{type: "Feature", id: "d1", properties: {{version:1,id: "d1", designation: "Désordre"}}}}, [
  {{class: "troncons", id: "t1", attributes: {{designation: "T1"}}}},
  {{class: "digues", id: "di1", attributes: {{libelle: "Digue A"}}}},
  {{class: "systemes", id: "s1", attributes: {{libelle: "SE A"}}}},
]);
process.stdout.write(JSON.stringify({{configs, disorder}}));'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(set(result["configs"]), {
            "systemes", "digues", "troncons", "desordres", "observations",
            "mobilier", "equipements_mesure", "reseaux_techniques",
            "ouvrages_particuliers", "cheminements", "vegetation",
            "amenagements_hydrauliques", "prestations", "zones_stockage",
            "troncons_lit", "elements_morphologiques_lit",
        })
        self.assertEqual(result["configs"]["systemes"]["endpoint"], "/api/systemes-endiguement")
        self.assertEqual(result["configs"]["digues"]["endpoint"], "/api/digues")
        self.assertEqual(result["configs"]["troncons"]["endpoint"], "/api/troncons")
        self.assertEqual(result["configs"]["desordres"]["endpoint"], "/api/desordres")
        self.assertTrue(
            {"date_debut", "date_fin", "valid"}
            <= set(result["configs"]["desordres"]["editable"])
        )
        self.assertEqual(result["disorder"]["values"]["troncons"], "T1")
        self.assertEqual(result["disorder"]["values"]["digues"], "Digue A")
        self.assertEqual(result["disorder"]["values"]["systemes"], "SE A")
        for operation in ("cancelEditing", "close", "open", "startEditing"):
            self.assertIn(operation, self.script)
        self.assertIn('method: "PUT"', self.script)
        self.assertIn('eventTarget.addEventListener("digues-app:open-entity"', self.script)

    def test_only_identity_metadata_drives_table_navigation(self):
        builder = (FRONTEND_DIRECTORY / "js" / "data-view-builder.js").read_text(
            encoding="utf-8"
        )
        data = (FRONTEND_DIRECTORY / "js" / "data-view.js").read_text(encoding="utf-8")
        self.assertIn("column?.navigable", builder)
        self.assertIn("column.source_class", builder)
        self.assertIn("column.source_field", builder)
        self.assertNotIn('key === "desordres.designation"', builder)
        self.assertIn('new CustomEvent("digues-app:open-entity"', data)

    def test_system_detail_open_edit_cancel_save_and_return_cycle(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        result = run_controller_scenario(module, r'''let stored={version:1,id:"s1",libelle:"SE A",designation:"Système A",commentaire:"Initial",
 population_protegee:10,classement:"A",niveau_protection:"N1",valid:true,digues:[]};
const calls=[]; const closed=[];
const {controller,elements}=createEnvironment(async(url,options={})=>{calls.push([url,options.method||"GET",options.body||null]);
  if(options.method==="PUT")stored={...stored,...JSON.parse(options.body),version:stored.version+1};return stored;},()=>{},()=>{},value=>closed.push(value.source));
(async()=>{
 await controller.open("systemes","s1","views",[]);
 const readTitle=elements["data-entity-detail-title"].textContent;
 await controller.startEditing();
 const libelle=editControl(elements,"libelle");
 libelle.value="SE modifié"; controller.cancelEditing();
 const cancelledTitle=elements["data-entity-detail-title"].textContent;
 await controller.startEditing();
 editControl(elements,"libelle").value="SE enregistré";
 await elements["data-entity-detail-form"].listeners.submit({preventDefault(){}});
 const savedTitle=elements["data-entity-detail-title"].textContent;
 elements["data-entity-detail-back"].listeners.click();
 process.stdout.write(JSON.stringify({readTitle,cancelledTitle,savedTitle,calls,closed,
   panelHidden:elements["data-entity-detail"].hidden}));
})();''')
        self.assertEqual(result["readTitle"], "Système d’endiguement : Système A")
        self.assertEqual(result["cancelledTitle"], result["readTitle"])
        self.assertEqual(result["savedTitle"], "Système d’endiguement : Système A")
        self.assertEqual(result["calls"][0][:2], ["/api/systemes-endiguement/s1", "GET"])
        self.assertEqual(result["calls"][1][:2], ["/api/systemes-endiguement/s1", "PUT"])
        self.assertEqual(result["closed"], ["views"])
        self.assertTrue(result["panelHidden"])

    def test_point_dates_and_valid_survive_detail_edit_and_reread(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
let stored={type:"Feature",id:"p1",geometry:{type:"Point",coordinates:[2.1,50.5]},
  properties:{version:1,id:"p1",designation:"Point daté",date_debut:"2025-01-02",
    date_fin:"2025-03-04",valid:true}};
const fetchJson=async(url,options={})=>{
  if(url.includes("types-desordre")) return {types:[]};
  if(url.includes("categories-desordre")) return {categories:[]};
  if(url.includes("/api/referentiels/")) return {references:[]};
  if(options.method==="PUT") {
    stored={...stored,properties:{...stored.properties,...JSON.parse(options.body)}};
  }
  return stored;
};
const {controller,elements}=createEnvironment(fetchJson);
(async()=>{
  await controller.open("desordres","p1","views",[]);
  const before=displayedFields(elements);
  await controller.startEditing();
  editControl(elements,"commentaire").value="Commentaire ajouté";
  await elements["data-entity-detail-form"].listeners.submit({preventDefault(){}});
  const after=displayedFields(elements);
  process.stdout.write(JSON.stringify({before,after,stored}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertEqual(result["before"]["Date de début"], "2025-01-02")
        self.assertEqual(result["before"]["Date de fin"], "2025-03-04")
        self.assertEqual(result["before"]["Objet valide"], "Oui")
        self.assertEqual(result["after"]["Date de début"], "2025-01-02")
        self.assertEqual(result["after"]["Date de fin"], "2025-03-04")
        self.assertTrue(result["stored"]["properties"]["valid"])

    def test_confirmed_save_publishes_before_a_secondary_refresh_failure(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
const publications=[];
const fetchJson=async(url,options={})=>{
  if(options.method === "PUT") return {version:1,id:"authoritative-id",libelle:"Sauvé",valid:true};
  return {version:1,id:"s1",libelle:"Avant",valid:true};
};
const {controller,elements}=createEnvironment(
  fetchJson,
  async()=>{throw new Error("liste indisponible");},
  (mutation)=>publications.push(mutation),
);
(async()=>{
  await controller.open("systemes","s1","views",[]);
  await controller.startEditing();
  editControl(elements,"libelle").value="Sauvé";
  await elements["data-entity-detail-form"].listeners.submit({preventDefault(){}});
  process.stdout.write(JSON.stringify({publications,
    status:elements["data-entity-detail-status"].textContent,
    error:elements["data-entity-detail-error"].textContent,
    title:elements["data-entity-detail-title"].textContent}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertEqual(result["publications"], [{
            "resource": "systemes", "action": "update", "ids": ["authoritative-id"],
        }])
        self.assertIn("Enregistré", result["status"])
        self.assertIn("liste indisponible", result["error"])
        self.assertEqual(result["title"], "Système d’endiguement : Sauvé")

    def test_rejected_save_never_publishes_a_mutation(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
const publications=[];
const fetchJson=async(url,options={})=>{
  if(options.method === "PUT") throw new Error("écriture refusée");
  return {version:1,id:"s1",libelle:"Avant",valid:true};
};
const {controller,elements}=createEnvironment(fetchJson,()=>{},(mutation)=>publications.push(mutation));
(async()=>{
  await controller.open("systemes","s1","views",[]);
  await controller.startEditing();
  editControl(elements,"libelle").value="Refusé";
  await elements["data-entity-detail-form"].listeners.submit({preventDefault(){}});
  process.stdout.write(JSON.stringify({publications,error:elements["data-entity-detail-error"].textContent}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertEqual(result["publications"], [])
        self.assertEqual(result["error"], "écriture refusée")

    def test_navigation_is_refused_until_the_pending_save_is_confirmed(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
const records={a:{version:1,id:"a",libelle:"A",designation:"Fiche A",valid:true},
  b:{version:1,id:"b",libelle:"B",designation:"Fiche B",valid:true}};
let finishSave; let savedCallbacks=0;
const fetchJson=async(url,options={})=>{
  if(options.method==="PUT") return new Promise((resolve)=>{finishSave=()=>resolve(records.a);});
  return url.endsWith("/b") ? records.b : records.a;
};
const {controller,elements}=createEnvironment(fetchJson,()=>{savedCallbacks+=1;});
(async()=>{
  await controller.open("systemes","a","views",[]);
  await controller.startEditing();
  editControl(elements,"libelle").value="A modifié";
  const pending=elements["data-entity-detail-form"].listeners.submit({preventDefault(){}});
  const openedWhilePending=await controller.open("systemes","b","views",[]);
  const titleBeforeFinish=elements["data-entity-detail-title"].textContent;
  finishSave(); await pending;
  process.stdout.write(JSON.stringify({titleBeforeFinish,openedWhilePending,
    titleAfterFinish:elements["data-entity-detail-title"].textContent,
    formHidden:elements["data-entity-detail-form"].hidden,
    saveDisabled:elements["data-entity-detail-save"].disabled,
    phase:controller.getFormView().element.dataset.state,busy:controller.getFormView().element["aria-busy"],
    error:elements["data-entity-detail-error"].textContent,savedCallbacks}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertFalse(result["openedWhilePending"])
        self.assertEqual(result["titleBeforeFinish"], "Système d’endiguement : Fiche A")
        self.assertEqual(result["titleAfterFinish"], result["titleBeforeFinish"])
        self.assertTrue(result["formHidden"])
        self.assertTrue(result["saveDisabled"])
        self.assertEqual(result["phase"],"readonly")
        self.assertEqual(result["busy"],"false")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["savedCallbacks"], 1)

    def test_double_submission_starts_only_one_put(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
const record={version:1,id:"a",libelle:"A",designation:"Fiche A",valid:true};
let finishSave; let putCount=0;
const fetchJson=async(_url,options={})=>{
  if(options.method==="PUT") {
    putCount+=1;
    return new Promise((resolve)=>{finishSave=()=>resolve(record);});
  }
  return record;
};
const {controller,elements}=createEnvironment(fetchJson);
(async()=>{
  await controller.open("systemes","a","views",[]);
  await controller.startEditing();
  editControl(elements,"libelle").value="A modifié";
  const submit=elements["data-entity-detail-form"].listeners.submit;
  const first=submit({preventDefault(){}}); const second=submit({preventDefault(){}});
  const disabledDuring=elements["data-entity-detail-save"].disabled;
  finishSave(); await Promise.all([first,second]);
  process.stdout.write(JSON.stringify({putCount,disabledDuring,
    disabledAfter:elements["data-entity-detail-save"].disabled,
    phase:controller.getFormView().element.dataset.state,busy:controller.getFormView().element["aria-busy"],
    error:elements["data-entity-detail-error"].textContent}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertEqual(result["putCount"], 1)
        self.assertTrue(result["disabledDuring"])
        self.assertTrue(result["disabledAfter"])
        self.assertEqual(result["phase"],"readonly")
        self.assertEqual(result["busy"],"false")
        self.assertEqual(result["error"], "")

    def test_conflict_retains_draft_and_base_version_until_explicit_cancel(self):
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        result = run_controller_scenario(module, r'''
let version=17;const publications=[];let supplied;
const fetchJson=async(url,options={})=>{
  if(options.method==="PUT") {
    supplied=options.headers["If-Match"];
    throw Object.assign(new Error("Conflit : brouillon conservé"),{status:409});
  }
  return {id:"s1",version,libelle:"Serveur",valid:true};
};
const {controller,elements}=createEnvironment(fetchJson,()=>{},e=>publications.push(e));
(async()=>{
  await controller.open("systemes","s1");await controller.startEditing();
  editControl(elements,"libelle").value="Mon brouillon";version=18;
  await controller.invalidate({resource:"systemes",ids:["s1"]});
  const stale=controller.getState();
  await elements["data-entity-detail-form"].listeners.submit({preventDefault(){}});
  const conflict=controller.getState();const draft=editControl(elements,"libelle").value;
  controller.cancelEditing();await new Promise(r=>setTimeout(r,0));
  process.stdout.write(JSON.stringify({stale,conflict,draft,supplied,publications,after:controller.getState()}));
})();
''')
        self.assertTrue(result["stale"]["stale"])
        self.assertFalse(result["stale"]["conflict"])
        self.assertTrue(result["conflict"]["conflict"])
        self.assertEqual(result["conflict"]["baseVersion"],17)
        self.assertEqual(result["draft"],"Mon brouillon")
        self.assertEqual(result["supplied"],'"17"')
        self.assertEqual(result["publications"],[])
        self.assertFalse(result["after"]["conflict"])
        self.assertEqual(result["after"]["baseVersion"],18)

    def test_matching_remote_mutation_reloads_current_detail(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
let reads=0;
const fetchJson=async()=>({version:1,id:"s1",libelle:`SE ${++reads}`,designation:`Fiche ${reads}`,valid:true});
const {controller,elements}=createEnvironment(fetchJson);
(async()=>{
  await controller.open("systemes","s1","views",[]);
  const initial=controller.getState();
  const other=await controller.invalidate({resource:"systemes",ids:["s2"]});
  const matching=await controller.invalidate({resource:"systemes",ids:["s1"]});
  const collection=await controller.invalidate({resource:"systemes",ids:null});
  process.stdout.write(JSON.stringify({initial,other,matching,collection,reads,
    current:controller.getState(),title:elements["data-entity-detail-title"].textContent}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertEqual(result["initial"], {
            "open": True, "resource": "systemes", "id": "s1",
            "editing": False, "pending": False, "forbidden": False, "stale": False,
            "conflict": False, "baseVersion": 1,
        })
        self.assertFalse(result["other"])
        self.assertTrue(result["matching"])
        self.assertTrue(result["collection"])
        self.assertEqual(result["reads"], 3)
        self.assertEqual(result["title"], "Système d’endiguement : Fiche 3")
        self.assertFalse(result["current"]["stale"])

    def test_remote_mutation_marks_an_active_draft_stale_without_overwriting_it(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
let reads=0;
const fetchJson=async()=>({version:1,id:"s1",libelle:`SE ${++reads}`,designation:"Fiche",valid:true});
const {controller,elements}=createEnvironment(fetchJson);
(async()=>{
  await controller.open("systemes","s1","views",[]);
  await controller.startEditing();
  const control=editControl(elements,"libelle"); control.value="Mon brouillon";
  const reloaded=await controller.invalidate({resource:"systemes",ids:null});
  process.stdout.write(JSON.stringify({reloaded,reads,state:controller.getState(),
    draft:control.value,status:elements["data-entity-detail-status"].textContent}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertFalse(result["reloaded"])
        self.assertEqual(result["reads"], 1)
        self.assertEqual(result["draft"], "Mon brouillon")
        self.assertTrue(result["state"]["open"])
        self.assertTrue(result["state"]["editing"])
        self.assertTrue(result["state"]["stale"])
        self.assertIn("modifiée ailleurs", result["status"])

    def test_disorder_relation_label_mutation_is_marked_stale(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
let reads=0;
const fetchJson=async()=>{reads+=1;return {type:"Feature",id:"p1",
  properties:{version:1,id:"p1",designation:"Désordre",valid:true}};};
const {controller,elements}=createEnvironment(fetchJson);
(async()=>{
  await controller.open("desordres","p1","views",[
    {class:"troncons",id:"t1",attributes:{libelle:"Ancien tronçon"}},
    {class:"digues",id:"d1",attributes:{libelle:"Ancienne digue"}},
    {class:"systemes",id:"s1",attributes:{libelle:"Ancien SE"}},
  ]);
  const unrelated=await controller.invalidate({resource:"troncons",ids:["t2"]});
  const related=await controller.invalidate({resource:"troncons",ids:["t1"]});
  process.stdout.write(JSON.stringify({unrelated,related,reads,state:controller.getState(),
    fields:displayedFields(elements),status:elements["data-entity-detail-status"].textContent}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertFalse(result["unrelated"])
        self.assertFalse(result["related"])
        self.assertEqual(result["reads"], 1)
        self.assertTrue(result["state"]["stale"])
        self.assertEqual(result["fields"]["Tronçon(s)"], "Ancien tronçon")
        self.assertIn("modifiée ailleurs", result["status"])

    def test_disorder_reload_rebuilds_authoritative_relation_labels(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js indisponible")
        module = FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        scenario = r'''
let disorderReads=0;
const fetchJson=async(url)=>{
  if(url==="/api/troncons/t2") return {type:"Feature",properties:{version:1,id:"t2",
    libelle:"Nouveau tronçon",digue_id:"d2",digue_libelle:"Nouvelle digue",
    systeme_endiguement_id:"s2",systeme_endiguement_libelle:"Nouveau SE"}};
  disorderReads+=1;
  return {type:"Feature",id:"p1",properties:{version:1,id:"p1",designation:"Désordre",
    troncon_ids:disorderReads===1?["t1"]:["t2"],valid:true}};
};
const {controller,elements}=createEnvironment(fetchJson);
(async()=>{
  await controller.open("desordres","p1","views",[
    {class:"troncons",id:"t1",attributes:{libelle:"Ancien tronçon"}},
    {class:"digues",id:"d1",attributes:{libelle:"Ancienne digue"}},
    {class:"systemes",id:"s1",attributes:{libelle:"Ancien SE"}},
  ]);
  const reloaded=await controller.invalidate({resource:"desordres",ids:["p1"]});
  process.stdout.write(JSON.stringify({reloaded,state:controller.getState(),
    fields:displayedFields(elements)}));
})();
'''
        result = run_controller_scenario(module, scenario)
        self.assertTrue(result["reloaded"])
        self.assertFalse(result["state"]["stale"])
        self.assertEqual(result["fields"]["Tronçon(s)"], "Nouveau tronçon")
        self.assertEqual(result["fields"]["Digue(s)"], "Nouvelle digue")
        self.assertEqual(result["fields"]["Système(s)"], "Nouveau SE")


if __name__ == "__main__":
    unittest.main()
