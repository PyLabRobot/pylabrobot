// Working the viewport: wheel and trackpad panning, picking, the hover readout, the coordinate
// tool with its delta lines and bullseyes, and the toolbar buttons and their panels.

import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";

import {
  BULLSEYE_HOVER,
  BULLSEYE_PX,
  BULLSEYE_WRT,
  MOVING_PARTS,
  PICKABLE_PARTS,
} from "./constants.js";
import { initCoords } from "./coords.js";
import { input } from "./dom.js";
import { isVisible, meshes, OVERLAY_ORDER, stateOf } from "./drawn.js";
import { hexOf, liquid } from "./format.js";
import { invalidate, lastFrameMs } from "./frame.js";
import { setGlideSeconds } from "./live.js";
import {
  AXIS_COLORS,
  armWindow,
  halos,
  setHalos,
  setOriginDots,
  showHalos,
  showOriginDots,
} from "./marks.js";
import { clearSelection, hoverBox, infoPanel, select, selected, showHoverBox } from "./panel.js";
import {
  camera,
  controls,
  dolly,
  mmPerPixel,
  renderer,
  resize,
  view,
  viewHelper,
  viewportEl,
} from "./renderer.js";
import { markTreeRow } from "./tree.js";
import { modelOf, treeDepth, world } from "./world.js";

let activeTool = "cursor";

// A two-finger swipe is not a touch as far as the page is concerned: it arrives as the same `wheel`
// event a mouse sends, and nothing says which device sent it. OrbitControls does not try to tell
// them apart, so every swipe read as a zoom and a trackpad could not pan at all.
const WHEEL_NOTCH = 120; // one notch, in the units of the pre-standard `wheelDelta`

const WHEEL_NOTCH_PX = 50; // fallback threshold, for browsers reporting no `wheelDelta`

const GESTURE_GAP_MS = 120;

let gestureEndsAt = 0;

let gesturePans = false;

function looksLikeTrackpad(event) {
  if (event.deltaMode !== 0) return false; // lines and pages are only ever reported by a wheel
  if (event.deltaX !== 0) return true; // no wheel has a horizontal axis to report
  // `deltaY` cannot separate them on macOS, where it is the wheel that gets accelerated: one notch
  // ramps 4, 10, 42, 208 and arrives fractional, while a swipe stays in whole single digits.
  // `wheelDelta` survives that - a notch is a whole multiple of 120 in it however `deltaY` was
  // scaled, and a swipe reports three times its own delta.
  const legacy = Math.abs(event.wheelDeltaY ?? event.wheelDelta ?? 0);
  if (legacy > 0) return legacy % WHEEL_NOTCH !== 0;
  return Math.abs(event.deltaY) < WHEEL_NOTCH_PX;
}

// Once per gesture, not once per event: a swipe's momentum tail decays to deltas no wheel would
// send, and re-reading each event would flip from panning to zooming mid-stroke.
function wheelPans(event) {
  const now = performance.now();
  const fresh = now > gestureEndsAt;
  gestureEndsAt = now + GESTURE_GAP_MS;
  if (event.ctrlKey) {
    gesturePans = false; // a pinch is a zoom, whatever came before it
    return false;
  }
  if (fresh) gesturePans = looksLikeTrackpad(event);
  return gesturePans;
}

const panRight = new THREE.Vector3();

const panUp = new THREE.Vector3();

// Slide the view without turning it: camera and target move by the same vector, so the angle
// between them is untouched.
function panByPixels(dx, dy) {
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;
  // Columns 0 and 1 of the camera's matrix are screen right and screen up, in world space. Moving
  // against the scroll is what makes the scene follow the fingers.
  panRight.setFromMatrixColumn(camera.matrix, 0).multiplyScalar(dx * perPixel);
  panUp.setFromMatrixColumn(camera.matrix, 1).multiplyScalar(-dy * perPixel);
  panRight.add(panUp);
  camera.position.add(panRight);
  controls.target.add(panRight);
  controls.update();
}

// On the viewport rather than the canvas inside it, so a swipe can be stopped before OrbitControls
// sees it and zooms.
viewportEl.addEventListener(
  "wheel",
  (event) => {
    if (!wheelPans(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panByPixels(event.deltaX, event.deltaY);
  },
  { capture: true, passive: false },
);

// The get-location tool's two markers, as the existing visualizer draws them: blue on the resource
// under the pointer, at the reference asked of it, and pink on the resource everything is measured
// against. Sprites held at BULLSEYE_PX, placed by transform, so a frame costs no buffer.
function bullseyeTexture(colour) {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const context = canvas.getContext("2d");
  context.strokeStyle = "rgba(255,255,255,0.85)";
  context.lineWidth = 9;
  context.beginPath();
  context.arc(32, 32, 16, 0, Math.PI * 2);
  context.stroke();
  context.strokeStyle = colour;
  context.fillStyle = colour;
  context.lineWidth = 4;
  context.beginPath();
  context.arc(32, 32, 16, 0, Math.PI * 2);
  context.stroke();
  context.beginPath();
  context.arc(32, 32, 3.5, 0, Math.PI * 2);
  context.fill();
  for (const [dx, dy] of [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
  ]) {
    context.beginPath();
    context.moveTo(32 + dx * 20, 32 + dy * 20);
    context.lineTo(32 + dx * 31, 32 + dy * 31);
    context.stroke();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function bullseye(colour) {
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: bullseyeTexture(hexOf(colour)),
      transparent: true,
      depthTest: false,
      depthWrite: false,
    }),
  );
  sprite.renderOrder = OVERLAY_ORDER;
  sprite.frustumCulled = false;
  sprite.visible = false;
  view.add(sprite);
  return sprite;
}

const hoverBullseye = bullseye(BULLSEYE_HOVER);

function clearHover() {
  hoverBox.visible = false;
  hoverBullseye.visible = false;
  markTreeRow(null);
}

const raycaster = new THREE.Raycaster();

const pointer = new THREE.Vector2();

const readout = document.getElementById("hover-readout");

const _hitLocal = new THREE.Vector3();

const _hitInverse = new THREE.Matrix4();

/**
 * Whether a hit on a moving part lands in its window. The part's box is not drawn - its frame is,
 * with an opening - so through the opening the pointer is on whatever stands below, not on the
 * arm that happens to be parked over it.
 */
function throughWindow(index, point) {
  const model = modelOf(index);
  if (!MOVING_PARTS.has(model.category)) return false;
  const [left, right, front, back] = armWindow(model);
  _hitLocal.copy(point).applyMatrix4(_hitInverse.copy(world.matrices[index]).invert());
  return _hitLocal.x > left && _hitLocal.x < right && _hitLocal.y > front && _hitLocal.y < back;
}

export function pick(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  // A mesh that is culled or hidden is not drawn, so it is not under the pointer either.
  const candidates = meshes.filter((m) => m.mesh.visible).map((m) => m.mesh);
  const hits = raycaster.intersectObjects(candidates, false).filter((hit) => {
    const instances = hit.object.userData.instances;
    if (!instances || hit.instanceId === undefined) return false;
    return !throughWindow(instances[hit.instanceId], hit.point);
  });
  for (const hit of hits) {
    const instances = hit.object.userData.instances;
    if (instances && hit.instanceId !== undefined) {
      const index = instances[hit.instanceId];
      if (!isVisible(index)) continue;
      // Enclosures are translucent, so clicking through one to its contents is the useful
      // behaviour; take an enclosure only when nothing solid lies behind it. A part that always
      // carries something - a pipetting channel and its shaft - is taken where it is clicked, and
      // so is a moving part's frame: its window is already looked through, so this is its rim.
      const category = modelOf(index).category;
      const takesClick =
        world.childrenOf[index].length === 0 ||
        PICKABLE_PARTS.has(category) ||
        MOVING_PARTS.has(category);
      if (takesClick || hits.length === 1) return { index };
    }
  }
  // Nothing along the ray is a leaf: the pointer is on a plate between its wells, or on a deck
  // between its carriers. The deepest thing it passes through is the one it is over - never the
  // bench or the arm that happens to enclose it, which the nearest hit would name.
  let deepest = -1;
  for (const hit of hits) {
    const index = hit.object.userData.instances[hit.instanceId];
    if (!isVisible(index)) continue;
    if (deepest < 0 || treeDepth(index) > treeDepth(deepest)) deepest = index;
  }
  return deepest < 0 ? null : { index: deepest };
}

const coords = initCoords();

const { coordinateLabel, recordMeasurement, endpoints: deltaEndpoints, wrtPoint } = coords;

export const { populateWrtDropdown } = coords;

const wrtBullseye = bullseye(BULLSEYE_WRT);

// Every frame: the tool decides whether either shows, the reference dropdown decides where the
// pink one stands, and the zoom decides how big both are drawn.
export function updateBullseyes() {
  if (activeTool !== "coords" || !world) {
    hoverBullseye.visible = false;
    wrtBullseye.visible = false;
    return;
  }
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;
  const at = wrtPoint();
  wrtBullseye.visible = at !== null;
  if (at !== null) wrtBullseye.position.copy(at);
  hoverBullseye.scale.setScalar(perPixel * BULLSEYE_PX);
  wrtBullseye.scale.setScalar(perPixel * BULLSEYE_PX);
}

// The existing visualizer draws an L between the two points a measurement runs between, one leg per
// axis in the axis' own colour, labelled with the distance. In three dimensions the L becomes a
// staircase: x, then y, then z. A coordinate tells you how far apart two things are; this tells you
// which way, which is the part a number alone never shows.
//
// Drawn over everything, because it annotates the scene rather than standing in it: a measurement
// half-buried in a carrier would be worse than useless.
const DELTA_WIDTH = 2.4;

const DELTA_HALO_WIDTH = 6.0;

const DELTA_HALO_OPACITY = 0.35;

const DELTA_LABEL_MM = 26;

const DELTA_MIN = 0.05; // mm; a leg shorter than this is a rounding artefact, not a distance

const DELTA_AXES = ["x", "y", "z"];

const deltaToggle = input("delta-lines-toggle");

// Built once and then moved, never rebuilt. Hovering fires on every frame the pointer moves, so
// allocating a dozen objects each time would be waste - but the reason it has to be this way is
// harder to see: a material has no pipeline ready on the frame it is created, so an annotation
// rebuilt per hover is permanently on its first frame and never draws at all.
export let deltaAnnotation = null;

function buildDeltaAnnotation() {
  const group = new THREE.Group();
  group.visible = false;
  const legs = DELTA_AXES.map((axis) => {
    const color = AXIS_COLORS[axis];
    // Pale and wide behind, saturated and thin in front: the halo is what keeps a thin line
    // readable against a surface of any colour.
    const lines = [
      [DELTA_HALO_WIDTH, DELTA_HALO_OPACITY, OVERLAY_ORDER + 40],
      [DELTA_WIDTH, 1, OVERLAY_ORDER + 41],
    ].map(([linewidth, opacity, order]) => {
      const geometry = new LineSegmentsGeometry();
      geometry.setPositions([0, 0, 0, 0, 0, 0]);
      const material = new THREE.Line2NodeMaterial({
        color,
        linewidth,
        worldUnits: false,
        transparent: true,
        opacity,
        depthTest: false,
      });
      const line = new LineSegments2(geometry, material);
      line.frustumCulled = false;
      line.renderOrder = order;
      group.add(line);
      return line;
    });

    const canvas = document.createElement("canvas");
    canvas.width = 256;
    canvas.height = 64;
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    // A quad rather than a sprite, as the rail numbers are: this build draws one and not the other.
    // It is turned to face the camera each frame instead, since a distance should read the same
    // from wherever it is looked at.
    const label = new THREE.Mesh(
      new THREE.PlaneGeometry(DELTA_LABEL_MM * 4, DELTA_LABEL_MM),
      new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthTest: false }),
    );
    label.frustumCulled = false;
    label.renderOrder = OVERLAY_ORDER + 42;
    group.add(label);
    return {
      axis,
      color: hexOf(color),
      lines,
      canvas,
      texture,
      label,
      text: null,
    };
  });
  view.add(group);
  return { group, legs };
}

function writeDeltaLabel(leg, text) {
  if (leg.text === text) return; // the same number, redrawn, costs a texture upload for nothing
  leg.text = text;
  const context = leg.canvas.getContext("2d");
  context.clearRect(0, 0, leg.canvas.width, leg.canvas.height);
  context.font = "bold 34px ui-monospace, Menlo, monospace";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.lineWidth = 6;
  context.strokeStyle = "rgba(255, 255, 255, 0.9)";
  context.strokeText(text, 128, 34);
  context.fillStyle = leg.color;
  context.fillText(text, 128, 34);
  leg.texture.needsUpdate = true;
}

function clearDeltaLines() {
  if (deltaAnnotation) deltaAnnotation.group.visible = false;
}

function drawDeltaLines(index) {
  if (activeTool !== "coords" || !deltaToggle.checked) return clearDeltaLines();
  const { from, to } = deltaEndpoints(index);
  if (!from) return clearDeltaLines(); // an absolute measurement has no second point to run to

  deltaAnnotation = deltaAnnotation ?? buildDeltaAnnotation();
  // One corner per axis taken in turn, so each leg is parallel to the axis it is coloured for.
  const corners = [
    from,
    new THREE.Vector3(to.x, from.y, from.z),
    new THREE.Vector3(to.x, to.y, from.z),
    to,
  ];
  let any = false;
  deltaAnnotation.legs.forEach((leg, i) => {
    const start = corners[i];
    const end = corners[i + 1];
    const distance = to[leg.axis] - from[leg.axis];
    const shown = Math.abs(distance) >= DELTA_MIN;
    for (const line of leg.lines) {
      line.visible = shown;
      if (shown) line.geometry.setPositions([start.x, start.y, start.z, end.x, end.y, end.z]);
    }
    leg.label.visible = shown;
    if (!shown) return;
    writeDeltaLabel(leg, `\u0394${leg.axis} ${distance.toFixed(1)}`);
    leg.label.position.copy(start).add(end).multiplyScalar(0.5);
    any = true;
  });
  deltaAnnotation.group.visible = any;
}

// The labels are geometry, so left alone they turn with the scene and shrink with distance. Both
// are wrong for a number: it is read, not looked at. Turned to face the camera and sized in pixels
// rather than millimetres, they stay the same on screen at any zoom, as the existing visualizer's
// do - it scales its by the stage's own zoom for the same reason.
const DELTA_LABEL_PX = 34;

export function updateDeltaLabels() {
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;
  const scale = (DELTA_LABEL_PX * perPixel) / DELTA_LABEL_MM;
  for (const leg of deltaAnnotation.legs) {
    leg.label.quaternion.copy(camera.quaternion);
    leg.label.scale.setScalar(scale);
  }
}

// Turning them off takes the drawn one with it, rather than leaving it until the next hover.
deltaToggle.addEventListener("change", () => {
  if (!deltaToggle.checked) clearDeltaLines();
});

// Hover is answered once a frame at most, and not at all while a button is down.
//
// A pointer crossing the canvas fires far faster than frames are drawn, and every one of those
// events used to raycast the whole scene to produce a readout that had not changed. Measured, it
// cost more than panning the camera did - 12.2% of a profile against 4.5% - and none of it was our
// own code: it was three walking every instance of every model. Coalescing to a frame throws away
// the events nobody could have seen the result of. A pointer with a button down is driving the
// camera rather than pointing at anything, so there is nothing to pick.
export let hoverAt = null;

const READOUT_GAP_PX = 14; // between the pointer and the readout

/** Beside the pointer, and inside the viewport: near an edge it stops short of running off. */
function placeReadout(event) {
  const rect = viewportEl.getBoundingClientRect();
  const x = event.clientX - rect.left + READOUT_GAP_PX;
  const y = event.clientY - rect.top + READOUT_GAP_PX;
  readout.style.left = `${Math.max(0, Math.min(x, rect.width - readout.offsetWidth))}px`;
  readout.style.top = `${Math.max(0, Math.min(y, rect.height - readout.offsetHeight))}px`;
}

function showHoverFor(event) {
  const hit = pick(event);
  if (!hit) {
    readout.style.display = "none";
    clearHover();
    clearDeltaLines();
    return;
  }
  const model = modelOf(hit.index);
  readout.textContent =
    activeTool === "coords"
      ? coordinateLabel(hit.index)
      : [world.names[hit.index], model.type, model.model, liquid(model, stateOf.get(hit.index))]
          .filter(Boolean)
          .join("\n");
  readout.style.display = "block";
  placeReadout(event); // once it has its text, so it is measured at the size it will show at
  showHoverBox(hit.index);
  markTreeRow(hit.index);
  drawDeltaLines(hit.index);
  if (activeTool === "coords") {
    hoverBullseye.position.copy(deltaEndpoints(hit.index).to);
    hoverBullseye.visible = true;
  }
}

// Answered by the loop, at the start of the frame the pointer's own input asked for. Answering it
// in a callback of its own instead put it a frame behind: the loop registers its callback first,
// because the input that starts it is handled in the capture phase, so the hover box and the delta
// lines were drawn one frame late and the last hover before the pointer stopped never drew at all.
export function answerHover() {
  const at = hoverAt;
  hoverAt = null;
  if (at !== null && world) showHoverFor(at);
}

renderer.domElement.addEventListener("pointermove", (event) => {
  if (!world) return;
  if (event.buttons !== 0) {
    // Dragging: whatever the pointer passes over on the way is not being pointed at.
    readout.style.display = "none";
    clearHover();
    return;
  }
  hoverAt = { clientX: event.clientX, clientY: event.clientY };
});

renderer.domElement.addEventListener("pointerleave", () => {
  readout.style.display = "none";
  clearHover();
});

// Click behaviour follows the existing visualizer exactly. With the cursor tool a single click on
// a resource does nothing; what a canvas click does is close the info panel, guarded by 400 ms so
// the second click of a double click cannot close what the first one opened. A double click
// toggles: the same resource again closes it. The coordinate tool takes clicks instead, recording
// a measurement.
const PANEL_GUARD_MS = 400;

let panelOpenedAt = 0;

renderer.domElement.addEventListener("click", (event) => {
  // The helper owns its corner of the canvas; only if it declines does the click reach the scene.
  if (viewHelper) {
    viewHelper.center.copy(controls.target);
    if (viewHelper.handleClick(event)) return;
  }
  if (!world) return;

  if (activeTool === "coords") {
    const hit = pick(event);
    if (hit && hit.index !== undefined) recordMeasurement(hit.index);
    return;
  }
  if (performance.now() - panelOpenedAt > PANEL_GUARD_MS) clearSelection();
});

renderer.domElement.addEventListener("dblclick", (event) => {
  if (!world || activeTool === "coords") return;
  const hit = pick(event);
  if (!hit || hit.index === undefined) return;
  if (selected === hit.index && infoPanel?.isConnected) {
    clearSelection();
    return;
  }
  select(hit.index, true);
  panelOpenedAt = performance.now();
});

// tools
const toolButtons = {
  cursor: document.getElementById("toolbar-cursor-btn"),
  coords: document.getElementById("toolbar-coords-btn"),
  gif: document.getElementById("toolbar-gif-btn"),
};

const panels = {
  coords: document.getElementById("coords-panel"),
  gif: document.getElementById("gif-panel"),
};

// The active tool decides what a click on the canvas does; the open panel is separate, because
// the GIF panel does not change what clicking a resource means.
let openPanel = null;

export function refreshToolUI() {
  toolButtons.cursor.classList.toggle("active", activeTool === "cursor");
  toolButtons.coords.classList.toggle("active", activeTool === "coords");
  toolButtons.gif.classList.toggle("active", openPanel === "gif");
  panels.coords.style.display = openPanel === "coords" ? "flex" : "none";
  panels.gif.style.display = openPanel === "gif" ? "flex" : "none";
}

function setTool(tool) {
  activeTool = tool;
  if (tool !== "coords") clearDeltaLines();
  openPanel = tool === "coords" ? "coords" : openPanel === "coords" ? null : openPanel;
  refreshToolUI();
}

// The origins button is not a tool: it changes what is drawn, not what a click means.
const originsButton = document.getElementById("toolbar-origins-btn");

/** What the origins button does: the dots drawn or not, and the button showing which. */
export function switchOriginDots(on) {
  originsButton.classList.toggle("active", on);
  const t = performance.now();
  setOriginDots(on);
  return {
    on,
    dots: on ? (world?.names.length ?? 0) : 0,
    buildMs: +(performance.now() - t).toFixed(1),
  };
}

// The click is the edge, as it is for every other button: a frame is asked for where the press
// comes into the page, not where the scene changes. `plrViewer.origins` asks through `atBoundary`.
originsButton.addEventListener("click", () => {
  switchOriginDots(!showOriginDots);
  invalidate();
});

// The halos button is the same kind of button: what is drawn changes, what a click means does not.
const halosButton = document.getElementById("toolbar-halos-btn");

halosButton.classList.toggle("active", showHalos);

/** What the halos button does: the halos drawn or not, and the button showing which. */
export function switchHalos(on) {
  halosButton.classList.toggle("active", on);
  setHalos(on);
  return { on, halos: halos?.children.length ?? 0 };
}

halosButton.addEventListener("click", () => {
  switchHalos(!showHalos);
  invalidate();
});

// How fast a move is drawn, kept across reloads: a viewer left watching a run stays as it was set.
// Storage may refuse - a private window, or site data cleared - and the default stands.
const glideSlider = input("glide-rate");

try {
  const kept = window.localStorage?.getItem("plr.glideSeconds");
  if (kept !== null && kept !== undefined) glideSlider.value = kept;
} catch {
  /* left at the default */
}

setGlideSeconds(Number(glideSlider.value));

const glideLabel = document.getElementById("glide-label");

const sayGlide = () => {
  glideLabel.textContent = Number(glideSlider.value) ? `${glideSlider.value}s` : "off";
};

sayGlide();

glideSlider.addEventListener("input", () => {
  setGlideSeconds(Number(glideSlider.value));
  sayGlide();
  try {
    window.localStorage?.setItem("plr.glideSeconds", glideSlider.value);
  } catch {
    /* not remembered, which changes nothing about this session */
  }
  invalidate();
});

toolButtons.cursor.addEventListener("click", () => setTool("cursor"));

toolButtons.coords.addEventListener("click", () => setTool("coords"));

toolButtons.gif.addEventListener("click", () => {
  openPanel = openPanel === "gif" ? (activeTool === "coords" ? "coords" : null) : "gif";
  refreshToolUI();
});

document.getElementById("zoom-in-btn").addEventListener("click", () => dolly(0.8));

document.getElementById("zoom-out-btn").addEventListener("click", () => dolly(1.25));

// panel toggles
const leftRail = document.getElementById("toolbar-left");

document.getElementById("toolbar-left-toggle").addEventListener("click", () => {
  leftRail.classList.toggle("collapsed");
  resize();
});

// The tree, the panels and the toolbars all change the scene through their own handlers. Rather
// than raise the flag in each one and miss the next one added, any input earns a frame: the cost is
// one redraw per interaction, and it stops the moment the pointer does.
for (const kind of ["pointerdown", "pointerup", "keydown", "click"]) {
  document.addEventListener(kind, invalidate, { passive: true, capture: true });
}

// A pointer moving is the one input that arrives faster than frames, and it only reaches the
// picture through the viewport: elsewhere a move changes nothing until it crosses into a row that
// raises a hover box, so the crossing asks, not the move. Over the viewport a move is answered
// with a raycast and a frame; on a machine whose frames are slow that is asked for at most every
// HOVER_SLOW_INTERVAL_MS, with the last move always answered, so the readout never lags behind
// a pointer that has stopped.
const HOVER_SLOW_FRAME_MS = 24;

const HOVER_SLOW_INTERVAL_MS = 150;

let hoverAskedAt = 0;

let hoverTrailing = null;

viewportEl.addEventListener(
  "pointermove",
  () => {
    const now = performance.now();
    if (lastFrameMs < HOVER_SLOW_FRAME_MS || now - hoverAskedAt >= HOVER_SLOW_INTERVAL_MS) {
      hoverAskedAt = now;
      invalidate();
      return;
    }
    if (hoverTrailing === null) {
      hoverTrailing = setTimeout(
        () => {
          hoverTrailing = null;
          hoverAskedAt = performance.now();
          invalidate();
        },
        HOVER_SLOW_INTERVAL_MS - (now - hoverAskedAt),
      );
    }
  },
  { passive: true, capture: true },
);

viewportEl.addEventListener("wheel", invalidate, { passive: true, capture: true });

for (const kind of ["mouseover", "mouseout"]) {
  document.addEventListener(
    kind,
    (event) => {
      const target = /** @type {Element | null} */ (event.target);
      if (target?.closest?.(".tree-node-row, .search-result, #viewport")) invalidate();
    },
    { passive: true, capture: true },
  );
}
