// The renderer, the scene it draws into, the two cameras, the lights that ride them and the
// controls: everything the page draws with, made once here, and the ways to move the view.

import * as THREE from "three";
import { OrbitControls } from "three/addons/OrbitControls.js";
import { RoomEnvironment } from "three/addons/RoomEnvironment.js";
import { ViewHelper } from "three/addons/ViewHelper.js";

import { DEG, SKY_LIGHT } from "./constants.js";
import { initFrame, invalidate } from "./frame.js";

export const viewportEl = document.getElementById("viewport");
// No `preserveDrawingBuffer`: this three never reads it, and a GIF frame is captured through a
// render target rather than off the canvas.
export const renderer = new THREE.WebGPURenderer({
  antialias: true,
  // Software WebGPU loses its device within a second of drawing; software WebGL2 keeps drawing.
  // boot.js has measured which this browser is before this runs.
  forceWebGL: window.plrCapability?.software === true,
});
const _tInit = performance.now();
await renderer.init();
export const rendererInitMs = performance.now() - _tInit;
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
// No tone mapping: it compresses the top of the range, which turns a pure white background grey.
// Over-exposure is handled by budgeting the lights instead.
renderer.toneMapping = THREE.NoToneMapping;
viewportEl.appendChild(renderer.domElement);

export const view = new THREE.Scene();
// A shade off white, so the deck has something to be brighter than. Pure white left the work
// surface - which is very nearly white itself - with no edge against the space around it, and the
// whole view read as washed out. The number to turn if it wants more or less separation.
const BACKGROUND = 0xe8ecef;
view.background = new THREE.Color(BACKGROUND);

// Two projections. Perspective reads a three-dimensional scene better; orthographic is what an
// axis view has to be, because under perspective only the point directly beneath the camera
// projects straight down and everything else is seen at an angle. A plan view with converging
// verticals is not a plan view.
const perspectiveCamera = new THREE.PerspectiveCamera(45, 1, 1, 20000);
const orthographicCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, -20000, 20000);
for (const c of [perspectiveCamera, orthographicCamera]) c.up.set(0, 0, 1); // PLR is Z-up

export let camera = perspectiveCamera;
export let projection = "perspective";

export const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.addEventListener("change", invalidate);
controls.dampingFactor = 0.12;

// A mouse has no second finger and a laptop has no middle button, so panning is bound to whichever
// each one has. Out of the box the middle button dollies, which the wheel already does.
controls.mouseButtons = {
  LEFT: THREE.MOUSE.ROTATE,
  MIDDLE: THREE.MOUSE.PAN,
  RIGHT: THREE.MOUSE.PAN,
};

// Lighting that rides with the camera. Lights fixed in the world make the same surface a different
// colour from every angle - a top face catches the key from above and washes out, a side face goes
// dark - and looking down an axis is exactly where a device modelled in white stops reading.
// Carried on the camera, the shading a surface gets follows its own shape and not where you stand,
// so a view can change without anything changing colour, and the form is still there to see.
//
// A key off to one side rather than straight down the lens: dead-on light flattens as surely as no
// light at all, because every face pointing at you gets the same amount of it.
const lights = new THREE.Group();
const skyLight = new THREE.HemisphereLight(0xffffff, 0xeceff1, SKY_LIGHT);
lights.add(skyLight);
const keyLight = new THREE.DirectionalLight(0xffffff, 0.9);
keyLight.position.set(-0.6, 0.5, 1);
lights.add(keyLight);
const fillLight = new THREE.DirectionalLight(0xffffff, 0.45);
fillLight.position.set(0.8, -0.4, 0.6);
lights.add(fillLight);
camera.add(lights);
view.add(camera);

// A metal surface has almost no diffuse colour of its own; it is what it reflects. Without an
// environment it renders nearly black under directional lights, so give the scene something to
// reflect. Kept dim, so the flat technical shading of everything else is barely touched.
try {
  const pmrem = new THREE.PMREMGenerator(renderer);
  view.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  view.environmentIntensity = 0.45;
  // Kept, so a quality level that takes the lighting away can give it back.
  view.userData.roomEnvironment = view.environment;
  pmrem.dispose();
} catch (error) {
  console.warn("no environment map; metal surfaces will look flat", error);
}

initFrame({ renderer, view, viewportEl, camera: () => camera, skyLight });

// three's own view helper, in place of the hand-drawn legend: same three axes, but clickable,
// and it animates the camera onto the axis you pick.
export let viewHelper = null;

export function buildViewHelper() {
  viewHelper?.dispose();
  viewHelper = new ViewHelper(camera, renderer.domElement);
  viewHelper.corner = "left";
  viewHelper.setLabels("X", "Y", "Z");
}

export function mmPerPixel() {
  const height = viewportEl.clientHeight || 1;
  if (projection === "orthographic") {
    return (camera.top - camera.bottom) / camera.zoom / height;
  }
  const distance = camera.position.distanceTo(controls.target);
  return (2 * distance * Math.tan((camera.fov * DEG) / 2)) / height;
}

export const VIEWS = {
  iso: new THREE.Vector3(-0.7, -1, 0.85),
  top: new THREE.Vector3(0, -0.001, 1),
  front: new THREE.Vector3(0, -1, 0.12),
};

// The orthographic frustum is sized to cover what the perspective camera covered at the target,
// so switching does not jump the framing.
function sizeOrthographic(distance) {
  const aspect = (viewportEl.clientWidth || 1) / (viewportEl.clientHeight || 1);
  const halfHeight = distance * Math.tan((perspectiveCamera.fov * DEG) / 2);
  orthographicCamera.top = halfHeight;
  orthographicCamera.bottom = -halfHeight;
  orthographicCamera.left = -halfHeight * aspect;
  orthographicCamera.right = halfHeight * aspect;
  orthographicCamera.updateProjectionMatrix();
}

// The axis presets live on the view helper: click an axis there and the camera animates onto it.
// What the helper cannot do is choose a projection, so that button stays.
export const projectionButton = document.getElementById("view-projection");

export function setProjection(kind) {
  if (kind === projection) return;
  const target = controls.target.clone();
  const position = camera.position.clone();
  const distance = position.distanceTo(target);

  projection = kind;
  camera = kind === "orthographic" ? orthographicCamera : perspectiveCamera;
  // The lights ride the camera, and there are two cameras with only one ever in use. Whichever
  // that is has to be carrying them: left on the other, they keep the pose it was last at, and the
  // scene goes on being lit from a direction the view no longer has - which is the one thing
  // putting the lights on the camera exists to prevent.
  camera.add(lights);
  view.add(camera);
  camera.position.copy(position);
  camera.up.set(0, 0, 1);
  if (kind === "orthographic") {
    camera.zoom = 1;
    sizeOrthographic(distance);
  } else {
    camera.updateProjectionMatrix();
  }
  controls.object = camera;
  controls.target.copy(target);
  controls.update();
  buildViewHelper();
  projectionButton.textContent = kind === "orthographic" ? "ORT" : "PSP";
  projectionButton.title =
    kind === "orthographic"
      ? "Orthographic: switch to perspective"
      : "Perspective: switch to orthographic";
}

const FRAME_MARGIN = 1.06;

function viewBasis(direction) {
  const forward = direction.clone().normalize();
  // The world up is Z, except when that is what we are looking along.
  const up = Math.abs(forward.z) > 0.99 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
  const right = new THREE.Vector3().crossVectors(forward, up).normalize();
  const camUp = new THREE.Vector3().crossVectors(right, forward).normalize();
  return { forward, right, camUp };
}

function projectedExtent(box, direction) {
  const { forward, right, camUp } = viewBasis(direction);
  const centre = box.getCenter(new THREE.Vector3());
  const corner = new THREE.Vector3();
  const offset = new THREE.Vector3();
  let halfWidth = 0;
  let halfHeight = 0;
  let halfDepth = 0;
  for (let i = 0; i < 8; i++) {
    corner.set(
      i & 1 ? box.max.x : box.min.x,
      i & 2 ? box.max.y : box.min.y,
      i & 4 ? box.max.z : box.min.z,
    );
    offset.subVectors(corner, centre);
    halfWidth = Math.max(halfWidth, Math.abs(offset.dot(right)));
    halfHeight = Math.max(halfHeight, Math.abs(offset.dot(camUp)));
    halfDepth = Math.max(halfDepth, Math.abs(offset.dot(forward)));
  }
  return { centre, halfWidth, halfHeight, halfDepth };
}

function fitOrthographic(halfWidth, halfHeight) {
  const aspect = (viewportEl.clientWidth || 1) / (viewportEl.clientHeight || 1);
  let w = halfWidth;
  let h = halfHeight;
  if (w / h > aspect) h = w / aspect;
  else w = h * aspect;
  orthographicCamera.zoom = 1;
  orthographicCamera.top = h;
  orthographicCamera.bottom = -h;
  orthographicCamera.left = -w;
  orthographicCamera.right = w;
  orthographicCamera.updateProjectionMatrix();
}

export function frameBox(box, direction) {
  const { centre, halfWidth, halfHeight, halfDepth } = projectedExtent(box, direction);
  const w = Math.max(halfWidth * FRAME_MARGIN, 1);
  const h = Math.max(halfHeight * FRAME_MARGIN, 1);
  const aspect = (viewportEl.clientWidth || 1) / (viewportEl.clientHeight || 1);

  const halfFov = (perspectiveCamera.fov * DEG) / 2;
  const distance = Math.max(h / Math.tan(halfFov), w / (Math.tan(halfFov) * aspect)) + halfDepth;

  controls.target.copy(centre);
  camera.position.copy(centre).add(direction.clone().normalize().multiplyScalar(distance));
  if (projection === "orthographic") {
    fitOrthographic(w, h);
  } else {
    camera.near = Math.max(distance / 1000, 0.1);
    camera.far = distance * 50;
    camera.updateProjectionMatrix();
  }
  // A drag leaves OrbitControls holding a rotation it has not finished applying: with damping on it
  // spends that residue over the following frames and only decays it, so it outlives the pointer
  // going up. Framing writes the camera straight to where it belongs, and the residue then turns it
  // back off the axis - by more than the 2.5 degrees an axis view is allowed, which is why a plan
  // view reached through the home button came back drawn as a free one, translucent and washed out,
  // while the same view on opening was solid. Damping off for the single update that lands the
  // frame is what clears it; `update` zeroes the residue itself in that branch.
  const damped = controls.enableDamping;
  controls.enableDamping = false;
  controls.update();
  controls.enableDamping = damped;
}

export function dolly(factor) {
  if (projection === "orthographic") {
    camera.zoom = Math.max(0.02, camera.zoom / factor);
    camera.updateProjectionMatrix();
  } else {
    const offset = camera.position.clone().sub(controls.target).multiplyScalar(factor);
    camera.position.copy(controls.target).add(offset);
  }
  controls.update();
}

// A plan view is what a deck is read in, so that is where the viewer opens. `?view=iso` or
// `?view=front` picks another, so a link can still point at a particular angle.
export const startViewName = new URLSearchParams(location.search).get("view") ?? "top";
export const startView = VIEWS[startViewName] ?? VIEWS.top;

let sizedTo = { w: 0, h: 0 };

export function resize() {
  const { clientWidth: w, clientHeight: h } = viewportEl;
  // `setSize` writes the canvas' CSS size, which changes layout, which wakes the ResizeObserver
  // that called this. Without this guard the two chase each other at sixty layouts a second for as
  // long as the page is open, drawing nothing and costing a third of a core.
  if (w === sizedTo.w && h === sizedTo.h) return;
  sizedTo = { w, h };
  invalidate();
  // three must set the canvas' CSS size as well as its drawing buffer. Told not to, it still sizes
  // the buffer by the pixel ratio, and with no CSS size the element lays out at that buffer size -
  // twice the viewport on a 2x display, overflowing down and right.
  renderer.setSize(w, h);
  perspectiveCamera.aspect = w / Math.max(h, 1);
  perspectiveCamera.updateProjectionMatrix();
  if (projection === "orthographic") {
    sizeOrthographic(camera.position.distanceTo(controls.target));
  }
}

// The viewport changes size without the window doing so: dragging the side panel, or toggling
// either panel, resizes it while `window.resize` stays silent. The renderer and the camera aspect
// then go stale, and anything that frames against them - the home button most visibly - works off
// the wrong shape. Observing the element covers both cases, as the existing visualizer does.
new ResizeObserver(resize).observe(viewportEl);
