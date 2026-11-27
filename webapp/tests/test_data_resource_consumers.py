import json
from pathlib import Path
import shutil
import subprocess
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"


def run_node(program: str) -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js indisponible")
    return json.loads(subprocess.check_output([node, "-e", program], text=True))


class DataResourceConsumersTest(unittest.TestCase):
    def test_heritage_invalidation_is_reentrant_preserves_each_local_selection(self):
        modules = FRONTEND_DIRECTORY / "js"
        program = f'''
const HeritagePanel = require({json.dumps(str(modules / "heritage-panel.js"))});
const HeritageSync = require({json.dumps(str(modules / "heritage-selection-sync.js"))});
const ResourceMutation = require({json.dumps(str(modules / "resource-mutation.js"))});
const DataInvalidators = require({json.dumps(str(modules / "data-resource-invalidators.js"))});
class Element {{
  constructor() {{
    this.children=[]; this.listeners={{}}; this.attributes={{}}; this.dataset={{}};
    this.className=""; this.checked=false; this.indeterminate=false;
    this.disabled=false; this.hidden=false; this.textContent="";
    this.classList={{
      add:(...names)=>{{this.className += ` ${{names.join(" ")}}`; }},
      remove:(...names)=>{{this.className=this.className.split(/\\s+/)
        .filter((name)=>name&&!names.includes(name)).join(" ");}},
    }};
  }}
  append(...children) {{ this.children.push(...children); }}
  replaceChildren(...children) {{ this.children=children; }}
  addEventListener(name, listener) {{ this.listeners[name]=listener; }}
  setAttribute(name, value) {{ this.attributes[name]=String(value); }}
  getAttribute(name) {{ return this.attributes[name] ?? null; }}
  querySelectorAll(selector) {{
    const matches=[]; const visit=(item)=>{{
      if(selector===".tree-name"&&String(item.className).includes("tree-name")) matches.push(item);
      (item.children||[]).forEach(visit);
    }}; this.children.forEach(visit); return matches;
  }}
  click() {{ this.listeners.click?.({{stopPropagation(){{}}}}); }}
}}
global.document={{createElement:()=>new Element(),createElementNS:()=>new Element()}};
function hierarchy(troncons) {{ return {{systemes:[{{id:"s1",libelle:"SE",digues:[{{
  id:"d1",libelle:"Digue",troncons:troncons.map((id)=>({{id,libelle:id}})),
}}]}}]}}; }}
function environment(preference, fetchOverride=null) {{
  const elements={{}};
  ["heritage-panel","toggle-heritage","close-heritage","heritage-tree","heritage-loading"]
    .forEach((id)=>{{elements[id]=new Element();}});
  const rootElement={{querySelector:(selector)=>elements[selector.slice(1)]}};
  const responses=[hierarchy(["t1","t2"]),hierarchy(["t2","t3"]),hierarchy(["t2","t3"])];
  let fetchCount=0; const hidden=new Set(); const published=[]; const listeners=new Map();
  const bus={{
    publish:(type,payload)=>published.push([type,payload]),
    subscribe:(type,callback)=>{{listeners.set(type,callback);return()=>listeners.delete(type);}},
  }};
  const checkbox=new Element();
  const storage={{getItem:()=>preference,setItem(){{}}}};
  const heritage=HeritagePanel.createController({{
    rootElement,hiddenTronconIds:hidden,
    fetchJson:fetchOverride||(async()=>responses[Math.min(fetchCount++,responses.length-1)]),
    onVisibilityChange:(_hidden,selection,context)=>sync.selectionChanged(selection,context),
  }});
  const sync=HeritageSync.createController({{bus,checkbox,heritageController:heritage,storage,windowTarget:null}});
  const snapshots=[];
  const view={{invalidate:async(mutation)=>{{snapshots.push(["view",mutation.resource,[...hidden],heritage.getSelection().tronconIds]);}}}};
  const detail={{invalidate:async()=>{{snapshots.push(["detail"]);}}}};
  const mutation=ResourceMutation.createController({{bus,localInstanceId:`local-${{preference}}`}});
  DataInvalidators.register({{mutationController:mutation,heritageController:heritage,
    viewController:view,detailController:detail}});
  return {{heritage,hidden,published,snapshots,mutation,getFetchCount:()=>fetchCount}};
}}
const tick=()=>new Promise((resolve)=>setTimeout(resolve,0));
(async()=>{{
  const outcomes=[];
  for(const preference of ["false","true"]) {{
    const env=environment(preference); await env.heritage.load();
    await env.heritage.applySelection({{systemeIds:[],digueIds:[],tronconIds:["t2"]}},{{origin:"remote"}});
    env.published.length=0;
    env.mutation.handleRemoteMutation({{schemaVersion:1,eventId:"reload-event",originClientId:"remote-client",resource:"systemes",action:"update",
      ids:["s1"],originInstanceId:"remote",occurredAt:10}});
    await tick(); await tick();
    await env.heritage.reload();
    outcomes.push({{preference,selection:env.heritage.getSelection(),hidden:[...env.hidden].sort(),
      published:env.published,snapshots:env.snapshots,fetchCount:env.getFetchCount()}});
  }}
  let finishReload;let requestCount=0;
  const interactive=environment("false",async()=>{{
    requestCount+=1;
    if(requestCount===1)return hierarchy(["t1","t2"]);
    return new Promise((resolve)=>{{finishReload=resolve;}});
  }});
  await interactive.heritage.load();
  const pendingReload=interactive.heritage.reload();
  await Promise.resolve();
  await interactive.heritage.applySelection(
    {{systemeIds:[],digueIds:[],tronconIds:["t1"]}},{{origin:"user"}},
  );
  const publicationsBeforeResponse=interactive.published.length;
  finishReload(hierarchy(["t1","t2","t3"]));
  await pendingReload;
  outcomes.push({{interactive:true,selection:interactive.heritage.getSelection(),
    hidden:[...interactive.hidden].sort(),published:interactive.published,
    publicationsBeforeResponse}});
  process.stdout.write(JSON.stringify(outcomes));
}})();
'''
        outcomes = run_node(program)
        for outcome in outcomes[:2]:
            with self.subTest(preference=outcome["preference"]):
                self.assertEqual(outcome["selection"]["tronconIds"], ["t2"])
                self.assertEqual(outcome["hidden"], ["t3"])
                self.assertEqual(outcome["published"], [])
                self.assertEqual(outcome["fetchCount"], 3)
                query_snapshots = [
                    snapshot for snapshot in outcome["snapshots"]
                    if snapshot[0] in {"home", "view"}
                ]
                self.assertEqual(
                    query_snapshots,
                    [
                        ["view", "systemes", ["t3"], ["t2"]],
                    ],
                )
        interactive = outcomes[2]
        self.assertEqual(interactive["selection"]["tronconIds"], ["t1"])
        self.assertEqual(interactive["hidden"], ["t2", "t3"])
        self.assertEqual(
            len(interactive["published"]), interactive["publicationsBeforeResponse"]
        )

    def test_view_dependencies_cover_fields_joins_and_heritage_scope(self):
        module = FRONTEND_DIRECTORY / "js" / "data-view-builder.js"
        program = f'''
const Builder=require({json.dumps(str(module))});
const relations=[
  {{parent:"systemes",child:"digues"}},{{parent:"digues",child:"troncons"}},
  {{parent:"troncons",child:"desordres"}},{{parent:"desordres",child:"observations"}},
  {{parent:"observations",child:"photos"}},
];
const direct=Builder.dependenciesForDefinition({{root:"observations",fields:[{{table:"observations",field:"date"}}]}},relations);
const joined=Builder.dependenciesForDefinition({{root:"systemes",fields:[{{table:"photos",field:"date"}}]}},relations);
const simple=Builder.dependenciesForDefinition({{root:"desordres",fields:[{{table:"desordres",field:"designation"}}]}},relations);
process.stdout.write(JSON.stringify({{direct,joined,simple,none:Builder.dependenciesForDefinition(null,relations)}}));
'''
        result = run_node(program)
        self.assertEqual(set(result["direct"]), {"observations", "desordres", "troncons"})
        self.assertEqual(
            set(result["joined"]),
            {"systemes", "digues", "troncons", "desordres", "observations", "photos"},
        )
        self.assertEqual(set(result["simple"]), {"desordres", "troncons"})
        self.assertNotIn("photos", result["simple"])
        self.assertEqual(result["none"], [])

    def test_active_view_replays_only_for_a_declared_dependency(self):
        module = FRONTEND_DIRECTORY / "js" / "data-view-builder.js"
        program = f'''
const Builder=require({json.dumps(str(module))});
class Element {{ constructor(tag="div") {{this.tag=tag;this.children=[];this.dataset={{}};
  this.hidden=false;this.listeners={{}};this.textContent="";this.value="";this.scrollTop=0;
  this.scrollHeight=1;this.clientHeight=1;this.style={{setProperty(){{}}}};}}
  append(...items){{this.children.push(...items);if(this.tag==="select"&&!this.value&&items[0])this.value=items[0].value;}}
  replaceChildren(...items){{this.children=[];this.value="";this.append(...items);}}
  addEventListener(name,callback){{this.listeners[name]=callback;}}
  setAttribute(name,value){{this[name]=String(value);}}
  getBoundingClientRect(){{return {{height:144}};}}
}}
const elements={{"#data-view-builder-workspace":new Element(),".data-view-builder":new Element(),
  "#data-view-root":new Element("select"),"#data-view-table":new Element("select"),
  "#data-view-field":new Element("select"),"#data-view-add-field":new Element(),
  "#data-view-field-list":new Element(),"#data-view-resizer":new Element(),
  "#data-view-result-table":new Element(),"#data-view-result-head":new Element(),
  "#data-view-result-body":new Element(),"#data-view-result-empty":new Element(),
  "#data-view-result-status":new Element(),"#data-view-export-bar":new Element(),
  ".data-view-result-scroll":new Element()}};
elements["#data-view-builder-workspace"].clientHeight=500;
const root={{ownerDocument:{{createElement:(tag)=>new Element(tag)}},querySelector:(selector)=>elements[selector]||null}};
const chain=["systemes","digues","troncons","desordres","observations","photos"];
const requests=[];
const fetchJson=async(url,options={{}})=>{{
  if(url.endsWith("view-catalog")) return {{default_root:"desordres",
    roots:chain.map((id)=>({{id,label:id}})),
    relations:chain.slice(0,-1).map((id,index)=>({{parent:id,child:chain[index+1]}})),
    tables:chain.map((id)=>({{id,label:id,compatible_roots:chain,
      fields:[{{id:"name",label:"Nom",source_class:id,source_field:"name"}}]}}))}};
  const request=JSON.parse(options.body);requests.push(request);
  return {{root:request.root,granularity:request.root,columns:[],rows:[],total:0,has_next:false}};
}};
const controller=Builder.createController({{rootElement:root,fetchJson,hiddenTronconIds:new Set(["t9"])}});
(async()=>{{
  await controller.setOpen(true); const before=controller.dependsOn("observations");
  elements["#data-view-table"].value="observations";
  elements["#data-view-table"].listeners.change();
  elements["#data-view-add-field"].listeners.click();
  await new Promise((resolve)=>setTimeout(resolve,0));
  const dependencies=Object.fromEntries(["observations","desordres","troncons","photos"]
    .map((resource)=>[resource,controller.dependsOn(resource)]));
  const ignored=await controller.invalidate({{resource:"photos"}});
  const joined=await controller.invalidate({{resource:"desordres",ids:null}});
  const direct=await controller.invalidate({{resource:"observations",ids:["o1"]}});
  process.stdout.write(JSON.stringify({{before,dependencies,ignored,joined,direct,requests}}));
}})();
'''
        result = run_node(program)
        self.assertFalse(result["before"])
        self.assertEqual(result["dependencies"], {
            "observations": True, "desordres": True,
            "troncons": True, "photos": False,
        })
        self.assertFalse(result["ignored"])
        self.assertTrue(result["joined"])
        self.assertTrue(result["direct"])
        self.assertEqual(len(result["requests"]), 3)
        self.assertTrue(all(request["root"] == "observations" for request in result["requests"]))
        self.assertTrue(all(request["hidden_troncon_ids"] == ["t9"] for request in result["requests"]))

    def test_data_invalidator_isolates_consumers_and_unregisters(self):
        module = FRONTEND_DIRECTORY / "js" / "data-resource-invalidators.js"
        program = f'''
const Wiring=require({json.dumps(str(module))});
const handlers=new Map(); const removed=[]; const calls=[];
const mutationController={{registerInvalidator(unit,handler){{handlers.set(unit,handler);return()=>removed.push(unit);}}}};
const mutation={{resource:"troncons"}};
const close=Wiring.register({{mutationController,
  heritageController:{{reload:async()=>calls.push("heritage")}},
  viewController:{{invalidate:async()=>{{calls.push("view");throw new Error("view");}}}},
  detailController:{{invalidate:async()=>calls.push("detail")}},
  onError:(error)=>calls.push(`error:${{error.message}}`),
}});
(async()=>{{
  const heritage=handlers.get("heritage-tree")(mutation);
  const data=handlers.get("data-query-results")(mutation);
  const detail=handlers.get("open-detail")(mutation);
  const results=await Promise.allSettled([heritage,data,detail]);
  calls.push(`result:${{results[1].status}}`); close();
  process.stdout.write(JSON.stringify({{calls,units:[...handlers.keys()],removed}}));
}})();
'''
        result = run_node(program)
        self.assertEqual(
            set(result["units"]), {"heritage-tree", "data-query-results", "open-detail"}
        )
        self.assertLess(result["calls"].index("heritage"), result["calls"].index("view"))
        self.assertIn("view", result["calls"])
        self.assertIn("detail", result["calls"])
        self.assertIn("result:rejected", result["calls"])
        self.assertEqual(
            set(result["removed"]), {"heritage-tree", "data-query-results", "open-detail"}
        )

    def test_two_base_instances_refresh_independently_without_selection_or_mutation_loop(self):
        modules = FRONTEND_DIRECTORY / "js"
        program = f'''
const Mutation=require({json.dumps(str(modules / "resource-mutation.js"))});
const Sync=require({json.dumps(str(modules / "heritage-selection-sync.js"))});
const Wiring=require({json.dumps(str(modules / "data-resource-invalidators.js"))});
const channels=[]; const publications=[];
function channel(name) {{
  const listeners=new Map(); const api={{name,listeners,
    subscribe(type,callback){{const values=listeners.get(type)||[];values.push(callback);listeners.set(type,values);
      return()=>listeners.set(type,(listeners.get(type)||[]).filter((value)=>value!==callback));}},
    publish(type,payload){{publications.push([name,type]);channels.filter((item)=>item!==api)
      .forEach((item)=>(item.listeners.get(type)||[]).forEach((callback)=>callback(payload)));}},
  }}; channels.push(api); return api;
}}
function base(name,selection) {{
  const bus=channel(name); const mutation=Mutation.createController({{bus,localInstanceId:name}});
  const state={{selection:[...selection],reloads:0,view:[],detail:[]}};
  const heritage={{getSelection:()=>({{systemeIds:[],digueIds:[],tronconIds:[...state.selection]}}),
    applySelection:async(next)=>{{state.selection=[...next.tronconIds];}},
    reload:async()=>{{state.reloads+=1;}}}};
  const checkbox={{checked:true,listeners:{{}},addEventListener(type,callback){{this.listeners[type]=callback;}}}};
  Sync.createController({{bus,checkbox,heritageController:heritage,
    storage:{{getItem:()=>"false",setItem(){{}}}},windowTarget:null}});
  Wiring.register({{mutationController:mutation,heritageController:heritage,
    viewController:{{invalidate:async(item)=>state.view.push([item.resource,[...state.selection]])}},
    detailController:{{invalidate:async(item)=>state.detail.push(item.resource)}}}});
  return state;
}}
const a=base("base-a",["t1"]); const b=base("base-b",["t9"]);
const emitterBus=channel("map-a");
const emitter=Mutation.createController({{bus:emitterBus,localInstanceId:"map-a"}});
emitter.publishMutation({{resource:"troncons",action:"update",ids:["t42"]}});
setTimeout(()=>process.stdout.write(JSON.stringify({{a,b,publications}})),0);
'''
        result = run_node(program)
        for name, selection in (("a", ["t1"]), ("b", ["t9"])):
            state = result[name]
            self.assertEqual(state["selection"], selection)
            self.assertEqual(state["reloads"], 1)
            self.assertEqual(state["view"], [["troncons", selection]])
            self.assertEqual(state["detail"], ["troncons"])
        self.assertEqual(result["publications"], [["map-a", "resource-mutated"]])


if __name__ == "__main__":
    unittest.main()
