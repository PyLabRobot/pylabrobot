import * as THREE from "three";
import { forgetDetail, updateDetail, updateEdgeMode } from "./appearance.js";
import { buildMeshes } from "./boxes.js";
import { PROTOCOL } from "./constants.js";
import { initDeviceTools } from "./device_tools.js";
import { hiddenNames, meshes, meshRoots, modelMeshes, stateOf, worldBox } from "./drawn.js";
import { niceNumber } from "./format.js";
import {
  afterDraw,
  applyQuality,
  beforeDraw,
  invalidate,
  qualityNow,
  qualityPinned,
  sceneArrived,
  setStats,
  statsNow,
  whileMoving,
} from "./frame.js";
import { initGif } from "./gif.js";
import {
  applyMoves,
  applyState,
  glides,
  onChange,
  setHidden,
  updateArms,
  updateGlides,
} from "./live.js";
import {
  arms,
  buildArms,
  buildGridMarks,
  buildHalos,
  buildOrigin,
  buildOriginDots,
  buildReferenceMarks,
  floorState,
  gridLabels,
  gridMarks,
  resetFloor,
  updateFloor,
  updateHalos,
  updateOrigin,
} from "./marks.js";
import { buildDeclaredMeshes, dracoLoader, gltfLoader } from "./models.js";
import {
  clearSelection,
  hoverBox,
  infoPanel,
  refreshPlacement,
  renderInfoPanel,
  select,
  selected,
  selectionBox,
} from "./panel.js";
import {
  buildViewHelper,
  camera,
  controls,
  frameBox,
  mmPerPixel,
  projection,
  projectionButton,
  renderer,
  rendererInitMs,
  resize,
  setProjection,
  startView,
  startViewName,
  VIEWS,
  view,
  viewHelper,
} from "./renderer.js";
import {
  answerHover,
  deltaAnnotation,
  hoverAt,
  pick,
  populateWrtDropdown,
  refreshToolUI,
  switchHalos,
  switchOriginDots,
  updateBullseyes,
  updateDeltaLabels,
} from "./tools.js";
import { connect, initTransport } from "./transport.js";
import {
  buildTree,
  refreshTreeInfo,
  refreshTreeVisibility,
  rememberView,
  reopenRowsUnder,
  restoreView,
  revealAndHighlight,
  showPane,
} from "./tree.js";
import { buildWorld, setWorld, world } from "./world.js";

// A facility viewer that knows nothing about liquid handlers.
//
// The server sends models once and instances as a packed transform array. One InstancedMesh is
// built per model, so a full deck draws in a couple of dozen calls no matter how many wells are
// on it. Nothing here is keyed on a resource type: geometry comes from the model's own fields,
// colour from its category, and the tree from the parent array.
//
// The interface follows the existing visualizer. Only what three dimensions genuinely adds is
// new: camera presets, an axis gizmo that turns with the view, and a Z reference in the
// coordinate tool.

// Milliseconds, `readyMs` from the page's time origin; the rest are the spans they name.
const timings = { rendererMs: rendererInitMs };

let framed = false; // whether this connection has framed the camera on its first scene

dracoLoader.setDecoderPath("./vendor/draco/");

dracoLoader.setDecoderConfig({ type: "wasm" });

gltfLoader.setDRACOLoader(dracoLoader);

// What each device carries, offered from the navbar. It reads the tree rather than being told, so
// there is nothing to keep in step: a tip picked up is a resource assigned, and the panel that
// draws tips is looking at the same tree the viewport is.
const deviceTools = initDeviceTools({
  onSelect: (index) => {
    revealAndHighlight(index);
    select(index, true);
  },
});

function sceneBounds() {
  const box = new THREE.Box3();
  for (let i = 0; i < world.names.length; i++) {
    if (world.parentOf[i] >= 0) continue;
    box.union(worldBox(i));
  }
  if (box.isEmpty()) box.set(new THREE.Vector3(0, 0, 0), new THREE.Vector3(1000, 500, 300));
  return box;
}

// Fit what the box actually covers from this direction, not its bounding sphere. A sphere fitted
// to a 2600 x 1400 x 1000 facility is 3.1 m across, so framing one leaves most of the viewport
// empty in a plan view where the height contributes nothing.
const frame = (direction) => frameBox(sceneBounds(), direction ?? VIEWS.iso);

function goToStartView() {
  setProjection(startViewName === "iso" ? "perspective" : "orthographic");
  frame(startView);
}

// A small handle on the viewer, so a notebook cell or a link can drive it: a call from outside the
// page, which tests and benchmarks use to drive the same paths a message takes. Everything that
// changes something is wrapped as a group, so a method added to that group is covered without
// anyone remembering to cover it.
//
// Reads are deliberately not wrapped. Asking the viewer a question must not be a reason to redraw,
// or watching for it to settle is what stops it settling.
function atBoundary(surface) {
  return Object.fromEntries(
    Object.entries(surface).map(([name, fn]) => [
      name,
      (...args) => {
        const result = fn(...args);
        invalidate();
        return result;
      },
    ]),
  );
}

/** @type {any} */ (window).plrViewer = {
  // Changes something, so asking for a frame afterwards is not the caller's job.
  ...atBoundary({
    focus(name, viewName) {
      const index = world?.indexOfName.get(name);
      if (index === undefined) return false;
      select(index);
      if (viewName) setProjection(viewName === "iso" ? "perspective" : "orthographic");
      frameBox(worldBox(index), VIEWS[viewName] ?? VIEWS.iso);
      return true;
    },
    view: (name) => {
      setProjection(name === "iso" ? "perspective" : "orthographic");
      frame(VIEWS[name] ?? VIEWS.iso);
    },
    projection: (kind) => setProjection(kind),
    hide: (name) => setHidden(name, true),
    show: (name) => setHidden(name, false),
    // Exposed so a benchmark can drive the same path a websocket message takes.
    applyState: (payload) => applyState(payload),
  }),

  // Only answers questions. Asking must not be a reason to redraw, or watching the viewer settle
  // is what stops it settling.
  stats: () => statsNow(),
  resources: () => world?.names ?? [],
  // What a resource last published, as the page holds it: what its colour and panel are drawn from.
  stateOf: (name) => {
    const index = world?.indexOfName.get(name);
    return index === undefined ? null : (stateOf.get(index) ?? null);
  },
  // What the pointer at a point of the page would be over, by name.
  pickAt: (clientX, clientY) => {
    const hit = world ? pick({ clientX, clientY }) : null;
    return hit ? world.names[hit.index] : null;
  },
  // Where a resource is drawn, in facility coordinates. The one thing a test outside the page
  // cannot work out for itself, because it is the product of the whole parent chain.
  worldOf: (name) => {
    const index = world?.indexOfName.get(name);
    if (index === undefined) return null;
    const m = world.matrices[index].elements;
    return [m[12], m[13], m[14]];
  },
  // Where the view is looking from and at. Panning moves both by the same amount and zooming moves
  // only the first, which is how the two are told apart from outside the page.
  camera: () => ({
    from: camera.position.toArray(),
    at: controls.target.toArray(),
    distance: camera.position.distanceTo(controls.target),
    zoom: camera.zoom,
  }),
  timings: () => timings,
  // The models that arrived as files, and what is being done with each. A box that is not drawn
  // and a model that is not either leaves nothing on screen, and from outside the page the two
  // are indistinguishable - so the model has to be able to say so itself.
  models: () => {
    const drawnBy = (o) => ({
      visible: o.visible && o.material.visible,
      order: o.renderOrder,
      depthTest: o.material.depthTest,
      transparent: o.material.transparent,
      opacity: o.material.opacity,
    });
    // One entry per resource drawn from a file, whichever way it is drawn.
    const listed = meshRoots.map((root) => {
      const parts = [];
      root.traverse((o) => o.isMesh && parts.push(drawnBy(o)));
      return { name: world?.names[root.userData.index], parts };
    });
    for (const built of modelMeshes) {
      const parts = built.meshes.map(drawnBy);
      for (const index of built.instances) listed.push({ name: world?.names[index], parts });
    }
    return listed;
  },
  detail: () =>
    meshes.map((e) => ({
      type: e.model.type,
      // The mesh itself, by id: a rebuild that finds the same resources on a model keeps it.
      id: e.mesh.id,
      mm: Math.max(e.model.size_x ?? 0, e.model.size_y ?? 0),
      drawn: e.mesh.visible,
      filled: e.mesh.material.visible,
      outlineRule: !!e.holdsEnclosure,
      // What decides which of two overlapping models is seen. Order alone does not: three draws
      // every transparent material after every opaque one, so a translucent shell wins over a solid
      // thing above it whatever its order says.
      order: e.mesh.renderOrder,
      transparent: e.mesh.material.transparent,
      opacity: e.mesh.material.opacity,
    })),
  // What the coordinate tool is drawing, so a test can check the annotation rather than the number
  // the panel prints beside it.
  deltas: () =>
    !deltaAnnotation?.group.visible
      ? []
      : deltaAnnotation.legs
          .filter((leg) => leg.label.visible)
          .map((leg) => ({
            axis: leg.axis,
            text: leg.text,
            at: leg.label.position.toArray().map((v) => +v.toFixed(1)),
          })),
  grid: () => {
    // Rail numbers are flat quads lying in the deck, not sprites - they were sprites once, and
    // this counted them by that type long after they stopped being it.
    const out = {
      groups: gridMarks.length,
      lines: 0,
      labels: gridLabels.length,
      labelsDrawn: gridLabels.filter((l) => l.visible).length,
      at: null,
    };
    for (const g of gridMarks) {
      g.traverse((o) => {
        if (o.type === "LineSegments") out.lines++;
      });
    }
    if (gridLabels.length) {
      out.at = gridLabels[0]
        .getWorldPosition(new THREE.Vector3())
        .toArray()
        .map((v) => +v.toFixed(1));
    }
    return out;
  },
  // What a travelling part is doing: where it is drawn, where it has been told to go, and where
  // along it the drive's position refers to. A part that will not move is one of those three.
  arms: () =>
    arms.map((arm) => ({
      name: world?.names[arm.index],
      currentX: +arm.currentX.toFixed(2),
      targetX: +arm.targetX.toFixed(2),
      referenceOffset: arm.referenceOffset,
    })),
  hover: () => ({
    visible: hoverBox.visible,
    empty: hoverBox.box.isEmpty(),
    min: hoverBox.box.min.toArray().map((v) => +v.toFixed(1)),
    max: hoverBox.box.max.toArray().map((v) => +v.toFixed(1)),
  }),
  // What the toolbar's origins button does, for a test that has no pointer.
  origins: (on = true) => switchOriginDots(on),
  // What the toolbar's halos button does, for the same reason.
  halos: (on = true) => switchHalos(on),
  // The quality level, set when given, for a test that has no slow machine to hand.
  quality: (level) => {
    if (level !== undefined) applyQuality(level);
    return {
      level: qualityNow(),
      pixelRatio: renderer.getPixelRatio(),
      environment: view.environment !== null,
      pinned: qualityPinned,
    };
  },
  // Renders `frames` frames back to back and reports what each one cost. The viewer only draws
  // when something changes, so the cost of a frame is otherwise not observable from outside.
  benchmark: (frames = 120) => {
    // The counters run on until they are reset, so one frame is measured on its own.
    renderer.render(view, camera); // warm up
    renderer.info.reset?.();
    renderer.render(view, camera);
    const info = renderer.info.render;
    const calls = info.drawCalls ?? info.calls ?? 0;
    const triangles = info.triangles;
    const t = performance.now();
    for (let i = 0; i < frames; i++) renderer.render(view, camera);
    const ms = (performance.now() - t) / frames;
    return { frames, msPerFrame: +ms.toFixed(3), fps: Math.round(1000 / ms), calls, triangles };
  },
  // What the renderer is actually asked to draw, by kind: one line per group, a draw call each
  // unless it is instanced. Answering "where do the draw calls come from" without guessing.
  audit: () => {
    const kinds = {};
    view.traverse((o) => {
      if (!o.visible || !(o.isMesh || o.isLine || o.isPoints || o.isSprite)) return;
      for (let p = o.parent; p; p = p.parent) if (!p.visible) return;
      const drawn = o.material?.visible !== false;
      const kind = o.isInstancedMesh
        ? "instanced mesh"
        : o.isLine
          ? "line"
          : o.isSprite
            ? "sprite"
            : o.isPoints
              ? "points"
              : "mesh";
      const index = o.geometry?.index?.count ?? o.geometry?.attributes?.position?.count ?? 0;
      kinds[kind] ??= { objects: 0, drawn: 0, triangles: 0 };
      const row = kinds[kind];
      row.objects += 1;
      if (drawn) {
        row.drawn += 1;
        row.triangles += Math.round((index / 3) * (o.isInstancedMesh ? o.count : 1));
      }
    });
    return kinds;
  },
  sceneObjects: () => {
    let n = 0;
    view.traverse(() => n++);
    return n;
  },
};

const scaleLine = document.getElementById("scale-bar-line");

const scaleLabel = document.getElementById("scale-bar-label");

// Under perspective there is no single scale, so the bar is quoted at the orbit target's depth.
// Under orthographic there is one scale for the whole viewport, and the bar is exact.
// About how long the bar should be, in pixels. Long enough to read a number against, short enough
// to leave the corner it sits in.
const SCALE_BAR_PX = 120;

// What the bar last showed: written on change only, since a write forces layout every frame.
let scaleShown = { nice: 0, px: 0 };

function updateScaleBar() {
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;
  // Counted in floor cells rather than rounded on its own, so the bar always spans a whole number
  // of the squares it is drawn over. Both used to pick a nice number from the same 1-2-5 ladder
  // but for different pixel targets, which agree only sometimes - and a scale that disagrees with
  // the floor beneath it is worse than having no floor to check it against.
  const cell = floorState?.cell;
  const nice = cell
    ? cell * ([1, 2, 5].find((n) => (n * cell) / perPixel >= SCALE_BAR_PX) ?? 10)
    : niceNumber(perPixel * SCALE_BAR_PX);
  const px = Math.round(nice / perPixel);
  if (nice === scaleShown.nice && px === scaleShown.px) return;
  scaleShown = { nice, px };
  scaleLine.style.width = `${px}px`;
  scaleLabel.textContent = nice >= 1000 ? `${nice / 1000} m` : `${nice} mm`;
}

projectionButton.addEventListener("click", () =>
  setProjection(projection === "orthographic" ? "perspective" : "orthographic"),
);

const homeButton = document.getElementById("home-button");

homeButton.addEventListener("click", () => {
  goToStartView();
  // A flash while the camera moves, so the button that did it is the thing you were last looking
  // at. Long enough to register, short enough not to linger over a view that has already settled.
  homeButton.classList.add("clicked");
  setTimeout(() => homeButton.classList.remove("clicked"), 400);
});

// A scene arriving whole: everything drawn is built again from it, in this order, with what the
// reader had open put back afterwards.
function rebuildScene(data) {
  // A page is fetched fresh on every load; the Python serving it is as old as its process. A
  // scene from another protocol is not drawn, since what it says would be misread.
  if (data.protocol !== PROTOCOL) {
    window.dispatchEvent(new CustomEvent("plr:mismatch"));
    return;
  }
  const _tScene = performance.now();
  sceneArrived();
  setStats(data.stats ?? {});
  const kept = rememberView();
  setWorld(buildWorld(data));
  glides.clear();
  timings.decodeMs = performance.now() - _tScene;
  const _tBuild = performance.now();
  forgetDetail();
  buildMeshes();
  resetFloor(sceneBounds().min.z);
  buildGridMarks();
  buildArms();
  buildReferenceMarks();
  buildDeclaredMeshes();
  buildOrigin();
  buildOriginDots();
  buildHalos();
  timings.meshesMs = performance.now() - _tBuild;
  const _tTree = performance.now();
  buildTree();
  // A rebuild draws everything at its true position, but what was switched off stays switched
  // off: the tree is rebuilt on every assignment while a deck is being laid out, and without
  // this, hiding a plate and then assigning anything at all put the plate and its wells back
  // on screen with the eye still showing them as hidden.
  for (const name of [...hiddenNames]) setHidden(name, true);
  deviceTools.rebuild();
  timings.treeMs = performance.now() - _tTree;
  timings.readyMs = performance.now();
  populateWrtDropdown();
  clearSelection();
  stateOf.clear();
  restoreView(kept);
  resize();
  // Only the first scene of a connection frames the camera. A tree that is assembled while the
  // viewer watches rebuilds on every assignment, and framing each one would throw the view away
  // as you build. The camera holds no scene indices, so it survives a rebuild unchanged.
  if (!framed) {
    goToStartView();
    framed = true;
  }
}

// The camera is read at capture time: a view change swaps it, and the recording follows the view.
const gif = initGif({
  renderer,
  view,
  get camera() {
    return camera;
  },
});

// What a frame runs, in order. Three things keep drawing on their own account: the helper's snap
// animation, an arm gliding to a new position, and a recording that needs a frame to capture;
// `controls.update` reports whether damping is still carrying the camera.
whileMoving(() => {
  // At most one hover answered per frame, however many times the pointer moved in between: a
  // pointer crosses the canvas far faster than frames are drawn, and each answer raycasts the
  // whole scene to produce a readout nobody could have seen the previous version of.
  if (hoverAt !== null) answerHover();
  return false;
});

whileMoving((delta) => {
  if (!viewHelper?.animating) return false;
  viewHelper.update(delta);
  // Every direction the helper can snap to is axis-aligned, so the view it lands on is a plan or
  // an elevation. Switch once the animation is done, not during it, since changing projection
  // rebuilds the helper.
  if (!viewHelper.animating) setProjection("orthographic");
  return true;
});

whileMoving(() => {
  if (deltaAnnotation?.group.visible) updateDeltaLabels();
  return false;
});

whileMoving(updateArms);

whileMoving(updateGlides);

whileMoving(() => controls.update());

whileMoving(() => gif.isRecording());

// What the camera can see decides what is worth drawing, so it is decided before the frame is
// drawn rather than after it. Asked afterwards, every frame drew what the frame before it had
// worked out, and the last frame of a move - the one left on screen - never drew its own answer
// at all: a zoom settled with the detail of where it started, a window resize changed nothing
// until the camera next moved, and a view turned to a plan kept the colours of the angle it came
// from. Nothing here asks for another frame; they are worked out for this one.
for (const prepare of [
  updateFloor,
  updateDetail,
  updateEdgeMode,
  updateOrigin,
  updateHalos,
  updateBullseyes,
  updateScaleBar,
]) {
  beforeDraw(prepare);
}

afterDraw(() => {
  if (!viewHelper) return;
  // The helper renders a second pass into a corner of the same canvas. Without turning auto-clear
  // off it clears the colour buffer for that corner first, leaving a blank patch over the scene.
  renderer.autoClear = false;
  viewHelper.render(renderer);
  renderer.autoClear = true;
});

afterDraw(() => gif.tick());

// The panels and the tree follow the live state: told what changed, each redraws its own part.
onChange((change) => {
  if (change.kind === "glide") {
    if (change.index !== selected) return;
    selectionBox.box.copy(worldBox(change.index));
    refreshPlacement(change.index);
  } else if (change.kind === "state") {
    // Only a panel showing something this message touched, or holding a tip it touched, is drawn
    // again: drawing the rest afresh reset what the reader had opened, on every well of a protocol.
    const shown = (i) =>
      i === selected || (world.parentOf[i] === selected && modelOf(i).category === "tip");
    if (infoPanel?.isConnected && [...change.changed].some(shown)) renderInfoPanel();
    refreshTreeInfo();
    deviceTools.refresh(change.changed);
  } else if (change.kind === "moves") {
    for (const at of change.rowsUnder) reopenRowsUnder(at);
    refreshTreeInfo();
    deviceTools.refresh();
    if (selected >= 0 && infoPanel?.isConnected) renderInfoPanel();
  } else if (change.kind === "visibility") {
    refreshTreeVisibility();
  }
});

initTransport({
  renderer,
  handlers: {
    // A new socket frames the camera on its first scene, and on that one only.
    connecting: () => {
      framed = false;
    },
    scene: rebuildScene,
    state: (data) => {
      if (world) applyState(data);
    },
    moves: (moves) => {
      if (world && Array.isArray(moves)) applyMoves(moves);
    },
  },
});

buildViewHelper();

refreshToolUI();

showPane("tree");

resize();

connect();
