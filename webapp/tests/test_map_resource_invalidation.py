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


class MapResourceInvalidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.map_page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        cls.map_script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(
            encoding="utf-8"
        )
        cls.service_worker = (
            FRONTEND_DIRECTORY / "service-worker.js"
        ).read_text(encoding="utf-8")

    def test_feature_store_supports_lifecycle_visibility_popup_and_geometry(self):
        module = FRONTEND_DIRECTORY / "js" / "map-feature-store.js"
        program = f'''
const Store=require({json.dumps(str(module))});
const store=Store.createRegistry(); const hidden=new Set(["t-hidden"]);
function adapter(initial, authoritative) {{
  const layers=new Map(Object.entries(initial)); const calls=[]; let stale=0;
  return {{layers,calls,get stale(){{return stale;}},
    fetchOne:async(id)=>{{calls.push(["one",id]);return structuredClone(authoritative[id]);}},
    fetchCollection:async()=>{{calls.push(["collection"]);return {{features:Object.values(authoritative).map((item)=>structuredClone(item))}};}},
    getId:(feature)=>feature.properties.id,listIds:()=>[...layers.keys()],
    shouldDefer:({{id}})=>id==="draft",markStale:()=>{{stale+=1;}},
    upsert:async(feature)=>layers.set(feature.properties.id,{{feature,
      popup:`popup:${{feature.properties.label}}`,visible:!(feature.properties.troncon_ids||[])
        .some((id)=>hidden.has(id))}}),
    remove:async(id)=>layers.delete(id),
  }};
}}
const tronconData={{t1:{{type:"Feature",properties:{{id:"t1",label:"T1 nouveau"}},
  geometry:{{type:"LineString",coordinates:[[0,0],[2,2]]}}}},
  t2:{{type:"Feature",properties:{{id:"t2",label:"T2"}},geometry:{{type:"LineString",coordinates:[[1,1],[3,3]]}}}}}};
const disorderData={{point:{{type:"Feature",properties:{{id:"point",label:"Point nouveau",troncon_ids:["t-hidden"]}},geometry:{{type:"Point",coordinates:[3,4]}}}},
  line:{{type:"Feature",properties:{{id:"line",label:"Ligne nouvelle",troncon_ids:[]}},geometry:{{type:"LineString",coordinates:[[0,0],[4,4]]}}}},
  polygon:{{type:"Feature",properties:{{id:"polygon",label:"Polygone nouveau",troncon_ids:["t-hidden"]}},geometry:{{type:"Polygon",coordinates:[[[0,0],[2,0],[2,2],[0,0]]]}}}},
  created:{{type:"Feature",properties:{{id:"created",label:"Créé",troncon_ids:[]}},geometry:{{type:"Point",coordinates:[8,9]}}}},
  draft:{{type:"Feature",properties:{{id:"draft",label:"Distant",troncon_ids:[]}},geometry:{{type:"Point",coordinates:[1,1]}}}}}};
const troncons=adapter({{t1:{{feature:{{properties:{{id:"t1"}}}}}}}},tronconData);
const disorders=adapter(Object.fromEntries(["point","line","polygon","draft"].map((id)=>[id,{{feature:{{properties:{{id}}}}}}])),disorderData);
store.registerResource("troncons",troncons);store.registerResource("desordres",disorders);
(async()=>{{
 await store.invalidate({{resource:"troncons",action:"update",ids:["t1"]}});
 const updatedTroncon=structuredClone(troncons.layers.get("t1"));
 await store.invalidate({{resource:"troncons",action:"create",ids:["t2"]}});
 await store.invalidate({{resource:"troncons",action:"delete",ids:["t1"]}});
 for(const id of ["point","line","polygon","created"]) await store.invalidate({{resource:"desordres",action:id==="created"?"create":"update",ids:[id]}});
 const updatedLine=structuredClone(disorders.layers.get("line"));
 await store.invalidate({{resource:"desordres",action:"update",ids:["draft"]}});
 delete disorderData.line;
 await store.invalidate({{resource:"desordres",action:"delete",ids:null}});
 process.stdout.write(JSON.stringify({{
  tronconIds:[...troncons.layers.keys()],tronconCalls:troncons.calls,updatedTroncon,
  disorderIds:[...disorders.layers.keys()].sort(),stale:disorders.stale,
  point:disorders.layers.get("point"),polygon:disorders.layers.get("polygon"),
  created:disorders.layers.get("created"),draft:disorders.layers.get("draft"),updatedLine,
 }}));
}})();
'''
        result = run_node(program)
        self.assertEqual(result["tronconIds"], ["t2"])
        self.assertEqual(result["tronconCalls"], [["one", "t1"], ["one", "t2"]])
        self.assertEqual(result["updatedTroncon"]["popup"], "popup:T1 nouveau")
        self.assertEqual(
            result["updatedTroncon"]["feature"]["geometry"]["coordinates"],
            [[0, 0], [2, 2]],
        )
        self.assertEqual(
            result["disorderIds"], ["created", "draft", "point", "polygon"]
        )
        self.assertEqual(result["stale"], 2)
        self.assertEqual(result["point"]["feature"]["geometry"]["coordinates"], [3, 4])
        self.assertFalse(result["point"]["visible"])
        self.assertEqual(result["point"]["popup"], "popup:Point nouveau")
        self.assertEqual(result["polygon"]["feature"]["geometry"]["type"], "Polygon")
        self.assertFalse(result["polygon"]["visible"])
        self.assertEqual(result["updatedLine"]["popup"], "popup:Ligne nouvelle")
        self.assertEqual(
            result["updatedLine"]["feature"]["geometry"]["coordinates"],
            [[0, 0], [4, 4]],
        )
        self.assertTrue(result["created"]["visible"])
        self.assertEqual(result["draft"]["feature"]["properties"]["id"], "draft")

    def test_disorder_urgency_palette_styles_all_geometries_and_interactions(self):
        module = FRONTEND_DIRECTORY / "js" / "disorder-urgency-style.js"
        program = f'''
const Style=require({json.dumps(str(module))});
const expected={{
  "RefUrgence:4":["#c62828","#b91c1c"],
  "RefUrgence:3":["#ef6c00","#c45100"],
  "RefUrgence:2":["#f6c90e","#8a6d00"],
  "RefUrgence:1":["#2e7d32","#23712a"],
  "RefUrgence:99":["#7a7f87","#666b73"],
  null:["#f8fafc","#475569"],
}};
const geometries=["Point","LineString","Polygon"];
const results={{}};
for(const [id,colors] of Object.entries(expected)) {{
  const urgency=id==="null"?null:id;
  results[id]=geometries.map(type=>{{
    const feature={{type:"Feature",properties:{{dernier_niveau_urgence_id:urgency}},geometry:{{type}}}};
    const base=Style.pathStyle(feature),selected=Style.pathStyle(feature,"selected"),editing=Style.pathStyle(feature,"editing");
    const point=Style.pointVariables(feature);
    return {{type,base,selected,editing,point,colors}};
  }});
}}
process.stdout.write(JSON.stringify(results));
'''
        result = run_node(program)
        for cases in result.values():
            for case in cases:
                fill, line = case["colors"]
                self.assertEqual(case["base"]["fillColor"], fill)
                self.assertEqual(case["base"]["color"], line)
                self.assertEqual(
                    case["point"]["--disorder-urgency-color"], fill
                )
                self.assertEqual(case["selected"]["color"], line)
                self.assertEqual(case["selected"]["weight"], 7)
                self.assertEqual(case["editing"]["color"], line)
                self.assertEqual(case["editing"]["dashArray"], "7 5")
                self.assertEqual(case["editing"]["weight"], 8)

    def test_observation_mutation_reloads_only_its_parent_disorder(self):
        modules = FRONTEND_DIRECTORY / "js"
        program = f'''
const Parent=require({json.dumps(str(modules / "observation-parent-map-invalidation.js"))});
const Store=require({json.dumps(str(modules / "map-feature-store.js"))});
const Style=require({json.dumps(str(modules / "disorder-urgency-style.js"))});
const store=Store.createRegistry();const calls=[];const rendered=[];
let urgency="RefUrgence:4";
store.registerResource("desordres",{{
 fetchOne:async(id)=>{{calls.push(["desordre",id]);return {{type:"Feature",properties:{{id,dernier_niveau_urgence_id:urgency}},geometry:{{type:"Point",coordinates:[0,0]}}}};}},
 fetchCollection:async()=>{{throw new Error("collection interdite");}},getId:item=>item.properties.id,listIds:()=>["d-1"],
 upsert:async(item)=>rendered.push(Style.resolveFeature(item).id),remove:async()=>{{}},
}});
const invalidator=Parent.create({{
 fetchObservation:async(id)=>{{calls.push(["observation",id]);return {{id,parent:{{resource:"desordres",id:"d-1"}}}};}},
 reloadDisorder:(id,mutation)=>store.reloadMapEntity("desordres",id,mutation),
}});
(async()=>{{
 await invalidator.invalidate({{resource:"observations",action:"create",ids:["o-1"]}});
 urgency="RefUrgence:1";
 await invalidator.invalidate({{resource:"observations",action:"update",ids:["o-1"]}});
 urgency=null;
 // Same authoritative event shape is emitted by a successful offline replay.
 await invalidator.invalidate({{resource:"observations",action:"update",ids:["o-1"]}});
 process.stdout.write(JSON.stringify({{calls,rendered,pending:invalidator.pendingIds()}}));
}})();
'''
        result = run_node(program)
        self.assertEqual(
            result["calls"],
            [
                ["observation", "o-1"], ["desordre", "d-1"],
                ["observation", "o-1"], ["desordre", "d-1"],
                ["observation", "o-1"], ["desordre", "d-1"],
            ],
        )
        self.assertEqual(result["rendered"], ["RefUrgence:4", "RefUrgence:1", None])
        self.assertEqual(result["pending"], [])

    def test_parent_resolution_is_isolated_per_business_database_context(self):
        module = FRONTEND_DIRECTORY / "js" / "observation-parent-map-invalidation.js"
        program = f'''
const Parent=require({json.dumps(str(module))});const calls=[];
function context(base,parent) {{return Parent.create({{
 fetchObservation:async(id)=>{{calls.push([base,"observation",id]);return {{id,parent:{{resource:"desordres",id:parent}}}};}},
 reloadDisorder:async(id)=>calls.push([base,"desordre",id]),
}});}}
const a=context("base-a","d-a"),b=context("base-b","d-b");
(async()=>{{const mutation={{resource:"observations",action:"update",ids:["same-observation"]}};
 await a.invalidate(mutation);await b.invalidate(mutation);process.stdout.write(JSON.stringify(calls));}})();
'''
        result = run_node(program)
        self.assertEqual(result, [
            ["base-a", "observation", "same-observation"],
            ["base-a", "desordre", "d-a"],
            ["base-b", "observation", "same-observation"],
            ["base-b", "desordre", "d-b"],
        ])

    def test_same_entity_requests_are_coalesced_with_one_trailing_refresh(self):
        module = FRONTEND_DIRECTORY / "js" / "map-feature-store.js"
        program = f'''
const Store=require({json.dumps(str(module))});const store=Store.createRegistry();
let fetches=0;const resolvers=[];let value=0;
store.registerResource("desordres",{{fetchOne:async()=>{{fetches+=1;return new Promise((resolve)=>resolvers.push(resolve));}},
 fetchCollection:async()=>({{features:[]}}),getId:(item)=>item.properties.id,listIds:()=>[],
 upsert:async(item)=>{{value=item.properties.value;}},remove:async()=>{{}}}});
const mutation={{resource:"desordres",action:"update",ids:["d1"]}};
const first=store.invalidate(mutation);const second=store.invalidate(mutation);const third=store.invalidate(mutation);
setTimeout(()=>{{resolvers.shift()({{properties:{{id:"d1",value:1}}}});setTimeout(()=>resolvers.shift()({{properties:{{id:"d1",value:2}}}}),0);}},0);
Promise.all([first,second,third]).then(()=>process.stdout.write(JSON.stringify({{fetches,value}})));
'''
        result = run_node(program)
        self.assertEqual(result, {"fetches": 2, "value": 2})

    def test_newer_entity_refresh_wins_over_an_older_collection_response(self):
        module = FRONTEND_DIRECTORY / "js" / "map-feature-store.js"
        program = f'''
const Store=require({json.dumps(str(module))});
async function scenario(resource) {{
  const store=Store.createRegistry();const layers=new Map();let resolveCollection;let resolveEntity;
  store.registerResource(resource,{{
    fetchOne:async()=>new Promise((resolve)=>{{resolveEntity=resolve;}}),
    fetchCollection:async()=>new Promise((resolve)=>{{resolveCollection=resolve;}}),
    getId:(item)=>item.properties.id,listIds:()=>[...layers.keys()],
    upsert:async(item)=>layers.set(item.properties.id,item.properties.value),remove:async(id)=>layers.delete(id),
  }});
  const collection=store.reloadMapCollection(resource);
  const entity=store.reloadMapEntity(resource,"42");
  resolveEntity({{properties:{{id:"42",value:"recent"}}}});await entity;
  resolveCollection({{features:[{{properties:{{id:"42",value:"ancien"}}}}]}});await collection;
  return layers.get("42");
}}
(async()=>process.stdout.write(JSON.stringify({{
  desordres:await scenario("desordres"),troncons:await scenario("troncons"),
}})))();
'''
        result = run_node(program)
        self.assertEqual(result, {"desordres": "recent", "troncons": "recent"})

    def test_deferred_entity_mutations_are_replayed_once_after_editing(self):
        module = FRONTEND_DIRECTORY / "js" / "map-feature-store.js"
        program = f'''
const Store=require({json.dumps(str(module))});const store=Store.createRegistry();
let editing=true;let fetches=0;let stale=0;let cleared=0;let value="initial";
store.registerResource("desordres",{{
  fetchOne:async()=>{{fetches+=1;return {{properties:{{id:"d1",value:"distant"}}}};}},
  fetchCollection:async()=>({{features:[]}}),getId:(item)=>item.properties.id,listIds:()=>["d1"],
  shouldDefer:()=>editing,markStale:()=>{{stale+=1;}},clearStale:()=>{{cleared+=1;}},
  upsert:async(item)=>{{value=item.properties.value;}},remove:async()=>{{}},
}});
(async()=>{{
  await store.invalidate({{resource:"desordres",action:"update",ids:["d1"]}});
  await store.invalidate({{resource:"desordres",action:"update",ids:["d1"]}});
  await store.invalidate({{resource:"desordres",action:"update",ids:["d1"]}});
  const whileEditing={{fetches,value,stale,waiting:store.hasDeferred("desordres","d1")}};
  editing=false;const replayed=await store.flushDeferred("desordres","d1");
  const secondReplay=await store.flushDeferred("desordres","d1");
  process.stdout.write(JSON.stringify({{whileEditing,replayed,secondReplay,fetches,value,cleared}}));
}})();
'''
        result = run_node(program)
        self.assertEqual(result["whileEditing"]["fetches"], 0)
        self.assertEqual(result["whileEditing"]["value"], "initial")
        self.assertTrue(result["whileEditing"]["waiting"])
        self.assertTrue(result["replayed"])
        self.assertFalse(result["secondReplay"])
        self.assertEqual(result["fetches"], 1)
        self.assertEqual(result["value"], "distant")
        self.assertEqual(result["cleared"], 1)

    def test_map_invalidation_order_is_local_and_error_isolated(self):
        module = FRONTEND_DIRECTORY / "js" / "map-resource-invalidators.js"
        program = f'''
const Wiring=require({json.dumps(str(module))});const handlers=new Map();const calls=[];
const mutationController={{registerInvalidator(unit,handler){{handlers.set(unit,handler);return()=>{{}};}}}};
Wiring.register({{mutationController,consumers:{{
 "heritage-tree":async()=>calls.push("heritage"),
 "relation-options":async()=>{{calls.push("relations");throw new Error("cache");}},
 "map-feature-store":async()=>calls.push("features"),
 "open-detail":async()=>calls.push("detail"),
 "map-context":async()=>calls.push("context"),
}},onError:(error,unit)=>calls.push(`error:${{unit}}:${{error.message}}`)}});
const mutation={{resource:"troncons"}};
["open-detail","map-feature-store","relation-options","heritage-tree"]
 .forEach((unit)=>handlers.get(unit)(mutation).catch(()=>{{}}));
setTimeout(()=>process.stdout.write(JSON.stringify(calls)),0);
'''
        result = run_node(program)
        self.assertEqual(result, [
            "heritage", "relations", "error:relation-options:cache",
            "features", "detail",
        ])

    def test_map_entry_registers_targeted_consumers_without_a_producer(self):
        for module in (
            "map-feature-store.js",
            "disorder-urgency-style.js",
            "observation-parent-map-invalidation.js",
            "map-resource-invalidators.js",
        ):
            self.assertIn(f'src="/static/js/{module}"', self.map_page)
            self.assertEqual(
                self.service_worker.count(f'"/static/js/{module}"'), 1
            )
        self.assertIn('mapFeatureStore.registerResource("troncons"', self.map_script)
        self.assertIn('mapFeatureStore.registerResource("desordres"', self.map_script)
        self.assertIn("MapResourceInvalidators.register", self.map_script)
        self.assertIn("upsertTronconOnMap", self.map_script)
        self.assertIn("upsertDesordreOnMap", self.map_script)
        self.assertIn("layer.unbindPopup?.()", self.map_script)
        self.assertIn("applyIndividualDesordreVisibility();", self.map_script)
        self.assertIn("invalidateDesordreTronconOptions", self.map_script)
        self.assertIn("invalidateReperageOptions", self.map_script)
        self.assertIn("desordreTronconOptionsLoaded", self.map_script)
        self.assertIn("reperageLoadingByTroncon", self.map_script)
        self.assertIn("reperageOptionsGlobalVersion", self.map_script)
        self.assertIn("reloadTerritoireContext", self.map_script)
        self.assertIn("markMapEditorStale", self.map_script)
        self.assertIn("function publishConfirmedMutation", self.map_script)
        self.assertIn("ResourceMutationController.publishMutation(descriptor)", self.map_script)
        self.assertIn(
            "ResourceMutationController.publishMutation(descriptor, { applyLocally: true })",
            self.map_script,
        )

        territory_reload = self.map_script.split(
            "async function reloadTerritoireContext()", 1
        )[1].split("function precipitationColor", 1)[0]
        self.assertIn("renderTerritoireLayers()", territory_reload)
        self.assertIn("updateTerritoireSpatialConstraint", territory_reload)
        self.assertIn("invalidatePrecipitationCache()", territory_reload)
        self.assertIn("clearVigicruesLayer()", territory_reload)
        self.assertIn("invalidateVigicruesRequest", territory_reload)
        self.assertNotIn("fitBounds", territory_reload)
        self.assertNotIn("applyViewportBounds", territory_reload)

    def test_multicast_updates_other_map_and_base_without_reemission(self):
        modules = FRONTEND_DIRECTORY / "js"
        program = f'''
const Mutation=require({json.dumps(str(modules / "resource-mutation.js"))});
const Store=require({json.dumps(str(modules / "map-feature-store.js"))});
const MapWiring=require({json.dumps(str(modules / "map-resource-invalidators.js"))});
const DataWiring=require({json.dumps(str(modules / "data-resource-invalidators.js"))});
const channels=[];const publications=[];
function channel(name){{const listeners=new Map();const api={{name,listeners,
 subscribe(type,callback){{const all=listeners.get(type)||[];all.push(callback);listeners.set(type,all);return()=>{{}};}},
 publish(type,payload){{publications.push([name,type]);channels.filter((item)=>item!==api)
  .forEach((item)=>(item.listeners.get(type)||[]).forEach((callback)=>callback(payload)));}}}};channels.push(api);return api;}}
const originBus=channel("map-a");const mapBus=channel("map-b");const baseBus=channel("base-c");
const origin=Mutation.createController({{bus:originBus,localInstanceId:"map-a"}});
const remoteMap=Mutation.createController({{bus:mapBus,localInstanceId:"map-b"}});
const remoteBase=Mutation.createController({{bus:baseBus,localInstanceId:"base-c"}});
const store=Store.createRegistry();const mapState={{features:[],heritage:0,relations:0}};
store.registerResource("desordres",{{fetchOne:async(id)=>({{properties:{{id}}}}),fetchCollection:async()=>({{features:[]}}),
 getId:(item)=>item.properties.id,listIds:()=>[],upsert:async(item)=>mapState.features.push(item.properties.id),remove:async()=>{{}}}});
MapWiring.register({{mutationController:remoteMap,consumers:{{
 "map-feature-store":(mutation)=>store.invalidate(mutation),"open-detail":async()=>{{}},
}}}});
const baseState={{view:0,detail:0}};
DataWiring.register({{mutationController:remoteBase,heritageController:{{reload:async()=>{{}}}},
 viewController:{{invalidate:async()=>{{baseState.view+=1;}}}},
 detailController:{{invalidate:async()=>{{baseState.detail+=1;}}}}}});
origin.publishMutation({{resource:"desordres",action:"update",ids:["D42"]}});
setTimeout(()=>process.stdout.write(JSON.stringify({{mapState,baseState,publications}})),0);
'''
        result = run_node(program)
        self.assertEqual(result["mapState"]["features"], ["D42"])
        self.assertEqual(result["baseState"], {"view": 1, "detail": 1})
        self.assertEqual(result["publications"], [["map-a", "resource-mutated"]])


if __name__ == "__main__":
    unittest.main()
