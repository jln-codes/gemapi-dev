/* La Carte consomme le contrôleur partagé des éditeurs patrimoniaux. */
(function (root) {
  "use strict";
  root.MapHeritageEditor = {
    create({ panel, map, L, onSaved, onMutationConfirmed }) {
      const element = root.document.createElement("div");
      element.className = "business-editor-view";
      element.id = "heritage-edit-view";
      const detailPanel=root.document.createElement("section");
      detailPanel.id="data-entity-detail";detailPanel.className="business-editor-view";detailPanel.hidden=true;
      root.DataEntityDetail.createHost(root.document,detailPanel);
      detailPanel.querySelector("#data-entity-detail-back").hidden=true;
      element.append(detailPanel);
      let feature = null;
      let gps = null, gpsForm = null, gpsHost = null, gpsInteraction = false;
      let geometryDraft=null,geometryForm=null;
      function disposeGps() {
        gps?.destroy(); gps = null; gpsForm = null;
        gpsHost?.remove(); gpsHost = null;
        if (gpsInteraction) { gpsInteraction = false; panel.finishGpsTracking?.(); }
      }
      function disposeGeometry(){geometryDraft?.dispose();geometryDraft=null;geometryForm=null;}
      function writeGeometry(geometry){
        const view=controller.getFormView();if(!view?.form.elements.geometry_mode)return false;
        const form=view.form;
        form.elements.geometry_mode.value=geometry===null?'effacer':'remplacer';
        if(geometry){
          form.elements.geometry_type_input.value=geometry.type;
          const points=geometry.type==='Point'?[geometry.coordinates]:geometry.type==='Polygon'?geometry.coordinates[0]:geometry.coordinates;
          form.elements.geometry_sommets.value=points.map(point=>point.join(' ')).join('\n');
        }
        root.TronconForm.updateConditional(form,feature);
        form.dispatchEvent(new root.Event('input',{bubbles:true}));return true;
      }
      function syncGeometry(state){
        const view=controller.getFormView();
        if(state.resource!=='troncons'||!state.editing||!view?.geometry){disposeGeometry();return;}
        if(geometryForm!==view.form){
          disposeGeometry();geometryForm=view.form;
          view.geometry.setRequirement(true,true);
          geometryDraft=root.EntityGeometryDraft.create({map,L,panel,controls:view.geometry,
            pathStyle:root.MapLayerControls.categoryStyle('troncons').path,
            // Keep the physical form's initial choice when a historical shape
            // has no drawable GeoJSON; opening it must not create a draft.
            allowedGeometryTypes:[view.geometry.type.value,...['Point','LineString','Polygon'].filter(type=>type!==view.geometry.type.value)],
            getOriginal:()=>feature?.geometry,
            canStart:()=>{const s=controller.getState();return s.editing&&!s.pending&&!s.conflict&&!s.stale&&!s.forbidden;},
            getGpsTracking:()=>null,getOwnerState:()=>controller.getState(),
            onDirty:()=>{
              if(geometryDraft?.isChanged())writeGeometry(geometryDraft.draftGeometry());
              else view.form.dispatchEvent(new root.Event('input',{bubbles:true}));
            },setStatus:(text,error)=>view.setStatus(text,error)});
          geometryDraft.renderOriginal();
          view.geometry.draw.addEventListener('click',()=>{if(!gps?.isActive())geometryDraft?.start();});
          view.geometry.confirm.addEventListener('click',()=>geometryDraft?.confirm());
          view.geometry.type.addEventListener('change',()=>geometryDraft?.changeType());
        }
        geometryDraft.updateStatus();
      }
      function syncGps(state) {
        const view = controller.getFormView();
        if (state.resource !== "troncons" || !view?.form.elements.geometry_mode || !map || !root.PwaGeometryTracking) {
          disposeGps(); return;
        }
        if (gpsForm !== view.form) {
          disposeGps();
          gpsForm = view.form;
          const form = gpsForm;
          gpsHost = root.document.createElement("div");
          gpsHost.id = "troncon-edit-geometry";
          (view.geometry?.methods||form.elements.geometry_mode.closest(".entity-form__field")).append(gpsHost);
          const accept = geometry => {
            const current = controller.getState();
            if (!current.editing || current.pending || current.conflict || current.stale || current.forbidden
                || current.resource !== "troncons" || geometry && !["LineString", "Polygon"].includes(geometry.type)) return false;
            if(geometry&&geometryDraft)return geometryDraft.acceptGeometry(geometry);
            geometryDraft?.reset();return writeGeometry(geometry);
          };
          gps = root.PwaGeometryTracking.createWidget({
            root, map, L, container: gpsHost,
            geometryType: () => form.elements.geometry_type_input.value,
            getOriginal: () => feature?.geometry,
            getDraft: () => {
              const geometry = root.TronconForm.geometry(form);
              return geometry === undefined ? feature?.geometry : geometry;
            },
            draftContext: () => ({ resource: "troncons", mode: "edit", id: controller.getState().id,
              version: controller.getState().baseVersion }),
            canStart: () => {
              const current = controller.getState();
              return current.editing && !geometryDraft?.isDrawing() && !current.pending && !current.conflict && !current.stale && !current.forbidden
                && (!root.BusinessRuntime || root.BusinessRuntime.can("business.update"));
            },
            onConfirm: accept,
            onState(state) {
              const active = state.phase !== "idle";
              if (active && !gpsInteraction) {
                gpsInteraction = true; panel.beginGpsTracking?.(() => gps?.cancel());
              } else if (!active && gpsInteraction) {
                gpsInteraction = false; panel.finishGpsTracking?.();
              }
              const current = controller.getState();
              for (const control of [view.save, form.elements.geometry_mode,
                form.elements.geometry_type_input, form.elements.geometry_sommets])
                control.disabled = active || current.pending || current.conflict || current.forbidden;
            },
          });
          form.addEventListener("change", () => gps?.refresh());
        }
        gpsHost.hidden = !state.editing;
        gps.setDisabled(state.pending || state.conflict || state.forbidden);
        gps.refresh();
      }
      const owner = {
        element,
        get formView() { return controller.getFormView(); },
        get selection() {
          const state = controller.getState();
          return state.open ? { entityClass: state.resource, id: state.id } : null;
        },
        close: () => controller.close(),
        discard: () => controller.close(),
      };
      const controller = root.DataEntityDetail.createController({
        rootElement: element, eventTarget: element,
        onBack: () => controller.close(),
        onShow() { panel.update(owner); },
        onStateChange(state) {
          if (!state.open) return;
          syncGeometry(state);
          syncGps(state);
          panel.update(owner);
        },
        hasExternalDraft: () => Boolean(gps?.isActive()||geometryDraft?.isDrawing()),
        geometryEditable:true,
        onReady({ payload }) { feature = payload;syncGeometry(controller.getState());geometryDraft?.renderOriginal();syncGps(controller.getState()); },
        onClose() { gps?.cancel(); disposeGps();disposeGeometry(); feature = null; panel.close(owner); },
        async onSaved({ entity, payload }) { feature = payload; await onSaved(entity, payload); },
        onMutationConfirmed,
      });
      return {
        async openEdit(resource, id) {
          if (!panel.activate(owner)) return null;
          const state = controller.getState();
          if (state.open && state.resource === resource && state.id === String(id)) {
            if (!state.editing) await controller.startEditing();
            return feature;
          }
          if (!await controller.open(resource, id) || controller.getState().id !== String(id)) return null;
          await controller.startEditing();
          return feature;
        },
        invalidate: controller.invalidate,
        getState: controller.getState,
        markStale: controller.markStale,
      };
    },
  };
})(globalThis);
