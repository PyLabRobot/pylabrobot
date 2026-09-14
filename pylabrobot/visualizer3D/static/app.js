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

const _t0 = performance.now();

import * as THREE from "three";
import { OrbitControls } from "three/addons/OrbitControls.js";
import { ViewHelper } from "three/addons/ViewHelper.js";
import { RoomEnvironment } from "three/addons/RoomEnvironment.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { GLTFLoader } from "three/addons/GLTFLoader.js";
import { DRACOLoader } from "three/addons/DRACOLoader.js";

import {
  DEG,
  RESOURCE_COLORS,
  CONTAINERS,
  CONTENTS,
  PICKABLE_PARTS,
  TREE_HIDDEN,
  GLAZED_MAX_OPACITY,
  MOVING_PARTS,
  NO_REFERENCE_MARK,
  FLAT_EDGE,
  EDGE_WIDTH_FLAT,
  EDGE_WIDTH_3D,
  structureEdgeStyle,
  LIQUID,
  VESSEL_EMPTY,
  VESSEL_RIM,
  VESSEL_WALL,
  VESSEL_WALL_OPACITY,
  TIP,
  SELECT,
  HOVER,
  ARM_COLOR,
  ARM_OPACITY,
  BOX_OPACITY,
  MODEL_EDGE_OPACITY,
  MOVING_OPACITY,
  SHELL_OPACITY,
  SPACE_OPACITY,
  HOLDERS,
  TIP_RACK_OPACITY,
  ARM_EDGE,
  ARM_EDGE_WIDTH_FLAT,
  ARM_EDGE_WIDTH_3D,
  ARM_INSET_X,
  ARM_INSET_Y,
  GRIP_MARK_OPACITY,
  GRIP_MARK_OPENING,
  GRIP_MARK_WIDTH,
  REFERENCE_LINE,
  REFERENCE_WIDTH,
  REFERENCE_DROP,
  ARM_REFERENCE_OPACITY,
} from "./constants.js";
import { initGif } from "./gif.js";
import { initCoords } from "./coords.js";
import { initDeviceTools } from "./device_tools.js";
import { input, query } from "./dom.js";
import {
  buildWorld,
  mirrorPlacement,
  modelOf,
  refreshTransforms,
  setLocal,
  setLocalRotation,
  setWorld,
  sizeOf,
  treeDepth,
  world,
} from "./world.js";
import { escapeHtml, fmt, NBSP, tuple, withUnit, section } from "./format.js";


const timings = { moduleMs: performance.now() - _t0 };

// Every fat-line material, so a resize can refresh the resolution each one is sized against.
const edgeMaterials = new Set();

// ---------------------------------------------------------------- state

let meshes = [];
let placementOf = []; // instance index -> { mesh, slot }
let vesselOf = new Map(); // index -> the inner body whose colour tracks what is in it
// index -> the instanced parts drawn for it outside the box pipeline, and where each one stands.
// Switching a resource off empties its box; these have to be emptied with it, or hiding a plate
// leaves ninety-six cavities and their walls floating where the plate was.
let overlayOf = new Map();
let tipOf = new Map();
let edgeOf = new Map();
// The instances whose model arrived as a file. Their box is not drawn at all and its border is
// only just there, and both have to be decided from here rather than at the moment the file
// landed: everything that says what is drawn runs again on every view change, so a one-off switch
// would be undone by the next orbit past an axis.
let drawnFromFile = new Set();
let stateOf = new Map();
let hiddenNames = new Set();
let selected = -1;
let stats = {};
let activeTool = "cursor";
let framed = false; // whether this connection has framed the camera on its first scene

// ---------------------------------------------------------------- renderer

const viewportEl = document.getElementById("viewport");
// `preserveDrawingBuffer` keeps the rendered frame readable after it is composited, which is
// what lets a GIF frame be copied off the canvas. Without it the copy comes back blank on the
// WebGL2 fallback path.
const renderer = new THREE.WebGPURenderer({ antialias: true, preserveDrawingBuffer: true });
const _tInit = performance.now();
await renderer.init();
timings.rendererMs = performance.now() - _tInit;
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
// No tone mapping: it compresses the top of the range, which turns a pure white background grey.
// Over-exposure is handled by budgeting the lights instead.
renderer.toneMapping = THREE.NoToneMapping;
viewportEl.appendChild(renderer.domElement);

const view = new THREE.Scene();
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

let camera = perspectiveCamera;
let projection = "perspective";

// Drawing only when something has changed. A scene with nothing moving in it costs a full core at
// sixty frames a second otherwise, which is the wrong price for a viewer meant to sit open beside a
// running protocol all day. Anything that changes what is on screen raises this flag; the loop
// draws once and lowers it again.
let renderPending = true;
let lastRenderAt = 0;
let looping = false;

// Skipping the draw is not enough on its own: the per-frame callback alone costs a third of a core,
// because it is still called sixty times a second to decide there is nothing to do. So the loop is
// stopped outright when the scene settles, and started again by whatever changes it.
//
// Raised at the edges of the viewer rather than wherever something happens to change: a message
// arriving, an input, a resize, a call from outside. That way adding a function that changes the
// scene cannot forget to ask for a frame, which is a silent freeze - the failure this had five
// times over while it was the caller's job to remember.
function invalidate() {
  renderPending = true;
  if (!looping) {
    looping = true;
    clock.getDelta(); // discard the idle gap, or the first frame back sees a huge delta
    // The frame-rate window restarts with the loop. Left running across the idle gap, the first
    // frame back averages out to nothing and reads as "0 fps".
    frames = 0;
    lastSample = performance.now();
    renderer.setAnimationLoop(drawFrame);
  }
}

const controls = new OrbitControls(camera, renderer.domElement);
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

// ---------------------------------------------------------------- two-finger pan

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
  if (event.ctrlKey) return (gesturePans = false); // a pinch is a zoom, whatever came before it
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

// Lighting that rides with the camera. Lights fixed in the world make the same surface a different
// colour from every angle - a top face catches the key from above and washes out, a side face goes
// dark - and looking down an axis is exactly where a device modelled in white stops reading.
// Carried on the camera, the shading a surface gets follows its own shape and not where you stand,
// so a view can change without anything changing colour, and the form is still there to see.
//
// A key off to one side rather than straight down the lens: dead-on light flattens as surely as no
// light at all, because every face pointing at you gets the same amount of it.
const lights = new THREE.Group();
lights.add(new THREE.HemisphereLight(0xffffff, 0xeceff1, 1.0));
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
  pmrem.dispose();
} catch (error) {
  console.warn("no environment map; metal surfaces will look flat", error);
}

// A resource that lays things out on a repeated grid says so in its model, and the viewer draws
// whatever it is told: how many, how far apart, where the first one sits, how to label them.
// Nothing here knows what a rail is, so a deck with slots or a nest with positions draws the same
// way the day it declares one.
let gridMarks = [];
// Every rail number in the scene. They are one draw call each and the largest per-device cost the
// viewer has, so they are the first thing to stop drawing once they are too small to read.
let gridLabels = [];
let surfaces = [];
let arms = []; // { group, index, referenceOffset, targetX, currentX }

const GRID_LINE = 0x4a545c; // dark, so it reads on a light deck as the numbers beside it do
const GRID_LABEL = "#3d4a52";
const GRID_LIFT = 0.4; // mm above the surface, so the marks do not fight the deck for depth
// The work surface a grid is laid out on. A resource that declares a grid is declaring that things
// stand on it at that height, so that is where the surface goes; nothing here knows it is a deck.
const SURFACE_COLOR = 0xfbfcfd; // the deck is the brightest thing; what stands on it is darker
// The work surface is a lid over everything the instrument keeps below it, and drawing it hid all
// of that. Fully transparent, what stands on the deck reads against the space around it rather than
// against a sheet the same colour. The number to turn: above zero the surface is drawn again, and in
// an axis view it will paint over what stands on it, because a transparent material draws after
// every opaque one and depth testing is off there.
const SURFACE_OPACITY = 0;

// What counts as ground rather than as an object standing on it.
const GROUND = new Set(["facility", "deck"]);
// Bands a capability can reach across a surface, drawn as a pair of lines with the reach labelled
// at the near edge. Blue keeps them apart from the grey position grid they cross.
const BAND_COLOR = 0x8ba7c6;
const GRID_LABEL_MM = 30; // label height in deck millimetres
const GRID_TICK = 30; // mm the mark runs forward of the grid, into the margin where labels sit
// How far a number keeps from the front edge of what it is drawn on, in mm. Only reached where the
// resource has no margin to give: a deck runs on well ahead of its first carrier, and a loading
// tray's grid starts at the tray's own front edge.
const GRID_MARGIN = 4;
// How strongly a track mark shows through what is standing on it. A carrier with a solid base hides
// the very mark that says which track it is on, and which track a carrier is on is most of what a
// person reads a deck for - but a line at full strength over an opaque part reads as an error
// rather than as a reference. Faint, it reads the way a hidden line does on a drawing: behind the
// thing, and still there.
//
// Set against the ink, not on its own: 0.3 of a near-white line was barely there, and the same 0.3
// of the dark line that replaced it painted stripes across every carrier floor, which reads as a
// floor you can see through rather than as a mark behind one.
const GRID_GHOST_OPACITY = 0.14;
// A number is drawn inside its quad with room above and below it, so the quad's edge is not where
// the ink stops. This is how much of the quad's height the digits actually take - a bold face's
// cap height against the canvas the label is drawn on - and it is what a margin has to be measured
// against, or the gap comes out 8.5 mm wider than it says.
const GRID_LABEL_INK = 0.433;

function labelSprite(text, color = GRID_LABEL, sizeMm = GRID_LABEL_MM) {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 64;
  const context = canvas.getContext("2d");
  context.font = "bold 38px ui-monospace, Menlo, monospace";
  context.fillStyle = color;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text, 64, 34);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  // A flat quad in the surface's own plane, not a sprite. A sprite turns to face the camera, which
  // is right for a marker and wrong for a number painted on a deck: these are part of the drawing,
  // so they lie in it. PlaneGeometry is already in XY, which is the surface's plane.
  const label = new THREE.Mesh(
    new THREE.PlaneGeometry(sizeMm * 2, sizeMm),
    new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthTest: false })
  );
  label.frustumCulled = false;
  return label;
}

// Where the device's x refers to, from the resource's own origin. Declared by the resource, so the
// viewer needs no knowledge of rail types: a dual-rail arm reports its centre, a single-rail one its
// right edge, and the line lands in the right place either way. Half the width when undeclared.
function referenceOffset(model) {
  const [sx] = sizeOf(model);
  return model.reference_point?.x ?? sx / 2;
}

// Geometry a resource declared for itself, drawn in place of its box.
//
// The file is loaded once per model and shared by every instance of it, the same way one geometry
// serves every well of a plate. Loading is asynchronous and the scene is already on screen by the
// time it lands, so each mesh is added when it arrives rather than being waited for: the box shows
// until then, and nothing blocks.
const gltfLoader = new GLTFLoader();
// Draco-compressed meshes are common in files exported for the web, and cannot be read without a
// decoder. It is fetched only when a compressed mesh actually turns up, so a viewer that never
// loads one pays nothing for it. Only the WebAssembly decoder is vendored; the much larger
// JavaScript fallback is for browsers that predate WebAssembly, which cannot run this viewer anyway.
const dracoLoader = new DRACOLoader();
dracoLoader.setDecoderPath("./vendor/draco/");
dracoLoader.setDecoderConfig({ type: "wasm" });
gltfLoader.setDRACOLoader(dracoLoader);
let meshRoots = [];

function clearDeclaredMeshes() {
  for (const root of meshRoots) view.remove(root);
  meshRoots = [];
}

// glTF says metres and Y-up; a resource that means something else says so in its declaration.
const MESH_UNITS = { mm: 1, cm: 10, m: 1000 };

function buildDeclaredMeshes() {
  clearDeclaredMeshes();

  // One load per distinct model, however many instances stand on it. A file of several hundred
  // thousand triangles is expensive to fetch and parse, and cloning shares both geometry and
  // materials, so the cost is paid once no matter how many arms are in the facility.
  const byModel = new Map();
  for (let index = 0; index < world.names.length; index++) {
    const declared = modelOf(index).mesh;
    if (!declared || !declared.url) continue;
    if (!byModel.has(world.modelOf[index])) byModel.set(world.modelOf[index], []);
    byModel.get(world.modelOf[index]).push(index);
  }

  for (const [modelIndex, instances] of byModel) {
    const declared = world.models[modelIndex].mesh;
    const scale = MESH_UNITS[declared.units] ?? 1;
    const names = instances.map((i) => world.names[i]);

    gltfLoader.load(
      declared.url,
      (gltf) => {
        // The scene may have been rebuilt while this was in flight. Placing it then would leave
        // objects nothing owns, positioned by transforms that no longer apply.
        if (!world || names.some((n, k) => world.indexOfName.get(n) !== instances[k])) return;

        instances.forEach((index, k) => {
          const scene = k === 0 ? gltf.scene : gltf.scene.clone(true);
          scene.scale.setScalar(scale);
          // Y-up is glTF's default; a Z-up file is already in our own convention.
          if ((declared.up ?? "Y") === "Y") scene.rotation.x = Math.PI / 2;

          const root = new THREE.Group();
          root.add(scene);
          root.matrixAutoUpdate = false;
          root.matrix.copy(world.matrices[index]);
          root.matrixWorldNeedsUpdate = true;
          root.traverse((o) => {
            o.frustumCulled = false;
            if (o.isMesh) {
              o.userData.declaredBy = index;
              // The same unlit twin the boxes keep. A model has more shape to lose than a box
              // does, but in a plan view what is wanted from it is its outline and its colour,
              // and shading it from above gives neither.
              o.userData.lit = o.material;
              o.userData.flat = flatVariant(o.material);
              // What the file said, kept before a plan view changes it. A travelling part is put
              // into the same pass as the content below it, which means writing over its material's
              // own flags - and a material asked afterwards what it was modelled as would answer
              // with whatever the plan view just gave it.
              o.userData.asModelled = {
                transparent: o.material.transparent,
                opacity: o.material.opacity,
                depthWrite: o.material.depthWrite,
                glazed: o.material.transparent && o.material.opacity <= GLAZED_MAX_OPACITY,
              };
            }
          });

          // A rigged file names the parts that move. The declaration says which node answers to
          // which joint, so the viewer drives what it is told and holds no knowledge of any arm's
          // geometry. Each node's rest transform is kept, because a joint value is a displacement
          // from where the file was authored, not an absolute pose.
          const joints = new Map();
          for (const [key, spec] of Object.entries(declared.joints ?? {})) {
            const node = scene.getObjectByName(spec.node);
            if (!node) {
              console.warn(`${world.names[index]} declares joint ${key} on node ${spec.node}, which the file does not have`);
              continue;
            }
            joints.set(key, {
              node,
              spec,
              restPosition: node.position.clone(),
              restQuaternion: node.quaternion.clone(),
            });
          }
          root.userData.joints = joints;
          root.userData.scale = scale;
          root.userData.index = index;

          view.add(root);
          meshRoots.push(root);
          applyJoints(index);
        });

        // The box that stood in for it is not needed once the real geometry is here. Recorded on
        // the entry rather than only switched off, because everything that decides what is drawn
        // runs again on every view change, and each of those would otherwise put the box back over
        // the model it was standing in for.
        const entry = meshes.find((m) => m.modelIndex === modelIndex);
        if (entry) {
          entry.modelDrawn = true;
          entry.mesh.material.visible = false;
          for (const i of entry.instances) drawnFromFile.add(i);
          // A part that travels is drawn twice over: once as the open frame `buildArms` extrudes
          // for it, and now as itself. The frame and the stroke around it were standing in for
          // geometry nobody had, so they go the way the box does. The reference line stays: it
          // marks where the drive reports this part to be, which is not a fact about the shape
          // and is the one thing the geometry cannot say for itself.
          for (const arm of arms) {
            if (!entry.instances.includes(arm.index)) continue;
            arm.frame.visible = false;
            arm.outline.visible = false;
          }
          // The view is not going to change just because a file finished loading, so the border
          // has to be faded here as well as in the rule that keeps it faded.
          setRenderMode(planView ?? false);
        }
      },
      undefined,
      (error) => console.warn(`could not load the mesh declared by ${names[0]}`, error)
    );
  }
}

// Move a resource's mesh to the joint values it publishes.
//
// A revolute joint turns about its declared axis, a prismatic one slides along it. Both are applied
// as a displacement from the rest transform the file was authored in, so a value of zero puts the
// arm back exactly where the file drew it.
function applyJoints(index) {
  const root = meshRoots.find((r) => r.userData.index === index);
  if (!root) return;
  const published = stateOf.get(index)?.joints;
  if (!published) return;

  for (const [key, joint] of root.userData.joints) {
    const value = published[key];
    if (value === undefined || value === null) continue;
    const axis = AXIS_VECTOR[joint.spec.axis ?? "z"];
    if (!axis) continue;

    if (joint.spec.type === "prismatic") {
      // Published in millimetres; the node lives in the file's own units.
      const travel = value / (root.userData.scale || 1);
      joint.node.position.copy(joint.restPosition).addScaledVector(axis, travel);
    } else {
      const turn = new THREE.Quaternion().setFromAxisAngle(axis, value * DEG);
      joint.node.quaternion.copy(joint.restQuaternion).multiply(turn);
    }
  }

}

const AXIS_VECTOR = {
  x: new THREE.Vector3(1, 0, 0),
  y: new THREE.Vector3(0, 1, 0),
  z: new THREE.Vector3(0, 0, 1),
};

// A resource that says where the device's x refers to gets that point marked, whether or not it is
// an arm. An arm draws its own inside the group that carries it; everything else is marked here -
// the autoload's sled being the case that prompted it, since the drive reports its carrier-handling
// wheel rather than the sled's own corner.
let referenceMarks = [];

// A moving part is drawn as three objects rather than one instanced box - a reference line, the
// carriage, and the stroke that bounds it - so they need ordering against each other as well as
// against the scene. These are the three offsets, applied to whatever order the part is at: the
// line reads under the carriage, and the stroke over it. They match the offsets a static resource
// uses for its own surface and edge, so the two schemes interleave.
const ARM_LINE_OFFSET = -0.5;
const ARM_FRAME_OFFSET = 0;
const ARM_OUTLINE_OFFSET = 1;

// The reference mark on a resource that does not travel, drawn over its own resource but under the
// edge line that bounds it.
const REFERENCE_MARK_OFFSET = 0.75;

// What the tree calls a gripper, and what it calls the faces it grips with.
const GRIPPER = "mechanical_gripper";
const PAD = "pad";

/** The pad a gripper meets a resource with, as its model, or null where it grips with its fingers. */
function padOf(index) {
  const stack = [...world.childrenOf[index]];
  while (stack.length) {
    const at = stack.pop();
    if (modelOf(at).category === PAD) return modelOf(at);
    stack.push(...world.childrenOf[at]);
  }
  return null;
}

/**
 * A crosshair lying flat at the grip centre: two arms as long as a pad's face, crossing, with a
 * circular opening in the middle so the mark does not cover the very point it is marking.
 */
// How finely the opening's edge is stepped. Ten is smooth at any size this is ever drawn at.
const GRIP_ARC_STEPS = 10;

function gripCross(reach, width, opening) {
  const arm = reach / 2;
  const half = width / 2;
  // Each arm is its own shape, starting ON the circle rather than at the centre. Punching a hole
  // through one solid cross only works while the cross is wider than the hole, and it is not: the
  // opening is deliberately the wider of the two, so the arms stand clear of it.
  const theta = Math.asin(Math.min(1, half / opening));
  const shapes = [];
  for (let quarter = 0; quarter < 4; quarter++) {
    const turn = (quarter * Math.PI) / 2;
    const points = [];
    for (let step = 0; step <= GRIP_ARC_STEPS; step++) {
      const angle = turn - theta + (2 * theta * step) / GRIP_ARC_STEPS;
      points.push(new THREE.Vector2(opening * Math.cos(angle), opening * Math.sin(angle)));
    }
    for (const [x, y] of [[arm, half], [arm, -half]]) {
      points.push(
        new THREE.Vector2(
          x * Math.cos(turn) - y * Math.sin(turn),
          x * Math.sin(turn) + y * Math.cos(turn)
        )
      );
    }
    shapes.push(new THREE.Shape(points));
  }
  return new THREE.ShapeGeometry(shapes);
}

/** A crosshair lying flat at the grip centre, where the tool says its grip centre is. */
function buildGripMark(index, model, pad) {
  const tcp = model.tool_center_point;
  const [along] = sizeOf(pad);
  const plane = new THREE.Mesh(
    gripCross(along, GRIP_MARK_WIDTH, GRIP_MARK_OPENING),
    new THREE.MeshBasicMaterial({
      color: REFERENCE_LINE, transparent: true, opacity: GRIP_MARK_OPACITY,
      depthTest: false, side: THREE.DoubleSide,
    })
  );
  plane.frustumCulled = false;
  plane.renderOrder = OVERLAY_ORDER + 5;
  plane.matrixAutoUpdate = false;
  // Lying flat, centred where the tool says it is programmed against. Read from the tool rather
  // than taken as the far end of its own box: the two agree on this gripper, and only because its
  // box is its link length - a tool that grips somewhere other than its tip would have the mark
  // drawn at the tip, which is the one place it is not.
  plane.userData.local = new THREE.Matrix4().makeTranslation(tcp.x, tcp.y, tcp.z);
  plane.matrix.multiplyMatrices(world.matrices[index], plane.userData.local);
  plane.matrixWorldNeedsUpdate = true;
  view.add(plane);
  referenceMarks.push({ plane, index });
}

/** Whether this resource travels over the deck, itself or by riding something that does. */
function travels(index) {
  return MOVING_PARTS.has(modelOf(index).category) || carried(index);
}

/** Whether this resource rides something that travels, rather than standing on the deck. */
function carried(index) {
  for (let i = world.parentOf[index]; i >= 0; i = world.parentOf[i]) {
    if (MOVING_PARTS.has(modelOf(i).category)) return true;
  }
  return false;
}

function buildReferenceMarks() {
  for (const mark of referenceMarks) view.remove(mark.plane);
  referenceMarks = [];

  // Draw these at the deck's working surface. A mark at the top of its own resource floats above
  // whatever it is pointing at, which on a part as tall as the autoload sled is 215 mm of daylight.
  // The surface is not the deck resource's origin - on a Hamilton deck that sits 100 mm below it -
  // but the z its rail grid is measured on, which is where anything seated on the deck stands.
  let deckZ = null;
  for (let index = 0; index < world.names.length; index++) {
    const deck = modelOf(index);
    if (deck.category !== "deck" || !deck.grid) continue;
    deckZ = world.matrices[index].elements[14] + deck.grid.origin[2];
    break;
  }

  for (let index = 0; index < world.names.length; index++) {
    const model = modelOf(index);

    // A gripper states the point it is programmed against, and that is what gets the mark. What is
    // worth seeing there is not a line on the deck but the face the pads close on, so the mark is
    // drawn as long as a pad - a crosshair in the middle, where the resource goes.
    if (model.category === GRIPPER) {
      const pad = padOf(index);
      // A tool that does not say where it grips gets no mark: the point is the tool's to state,
      // and guessing it from the box is what this stopped doing.
      if (pad && model.tool_center_point) buildGripMark(index, model, pad);
      continue;
    }

    if (!model.reference_point || MOVING_PARTS.has(model.category)) continue;
    if (NO_REFERENCE_MARK.has(model.category)) continue;
    const [, sy] = sizeOf(model);
    // Held in the resource's own frame, so a part that only travels in x keeps it as it moves.
    //
    // Dropped to the deck's surface for something standing on the deck, where a mark at the top of
    // a tall part would float above whatever it points at. A part CARRIED by an arm is not standing
    // on anything, so its mark rides with it - at the reference point's own height, which is the
    // height the drive reports and the only one worth marking. On a head that is the bottom of
    // shaft A1, eight millimetres below the plane the body is measured from; the mark used to sit
    // on that plane, which is the resource's origin and nothing the device ever refers to.
    const sz = carried(index)
      ? model.reference_point.z ?? 0
      : deckZ === null
        ? 0
        : deckZ - world.matrices[index].elements[14];
    const plane = new THREE.Mesh(
      new THREE.PlaneGeometry(REFERENCE_WIDTH, sy),
      new THREE.MeshBasicMaterial({
        color: REFERENCE_LINE, transparent: true, opacity: ARM_REFERENCE_OPACITY,
        depthTest: false, side: THREE.DoubleSide,
      })
    );
    plane.frustumCulled = false;
    plane.renderOrder = paintOrderOf(index) + REFERENCE_MARK_OFFSET;
    plane.matrixAutoUpdate = false;
    plane.userData.local = new THREE.Matrix4().makeTranslation(
      referenceOffset(model), sy / 2, sz);
    plane.matrix.multiplyMatrices(world.matrices[index], plane.userData.local);
    plane.matrixWorldNeedsUpdate = true;
    view.add(plane);
    referenceMarks.push({ plane, index });
  }
}

function buildArms() {
  for (const arm of arms) view.remove(arm.group);
  arms = [];

  for (let index = 0; index < world.names.length; index++) {
    const model = modelOf(index);
    if (!MOVING_PARTS.has(model.category)) continue;
    const [sx, sy, sz] = sizeOf(model);

    // Outer footprint with a rectangular hole, extruded to the part's height: a carriage you can
    // see the deck through, rather than a wall across it.
    const shape = new THREE.Shape();
    shape.moveTo(0, 0);
    shape.lineTo(sx, 0);
    shape.lineTo(sx, sy);
    shape.lineTo(0, sy);
    shape.closePath();
    // The opening is declared by the part when it knows its own geometry. A declared width is
    // centred on the part unless the part also says how far its right edge sits from the end,
    // which is the case for an opening that is deliberately off-centre.
    const window = model.window ?? {};
    const insetY = window.inset_y ?? ARM_INSET_Y;
    let holeLeft;
    let holeRight;
    if (window.width === undefined) {
      holeLeft = ARM_INSET_X;
      holeRight = sx - ARM_INSET_X;
    } else if (window.right_margin === undefined) {
      holeLeft = (sx - window.width) / 2;
      holeRight = holeLeft + window.width;
    } else {
      holeRight = sx - window.right_margin;
      holeLeft = holeRight - window.width;
    }
    const innerH = sy - 2 * insetY;
    if (holeRight > holeLeft && innerH > 0) {
      const hole = new THREE.Path();
      hole.moveTo(holeLeft, insetY);
      hole.lineTo(holeLeft, sy - insetY);
      hole.lineTo(holeRight, sy - insetY);
      hole.lineTo(holeRight, insetY);
      hole.closePath();
      shape.holes.push(hole);
    }

    const solid = new THREE.ExtrudeGeometry(shape, { depth: sz, bevelEnabled: false });
    // How the part looks, when it says: its colour and how metallic it reads. Otherwise every arm
    // is the same translucent grey.
    const appearance = model.appearance ?? {};
    const frame = new THREE.Mesh(
      solid,
      new THREE.MeshStandardMaterial({
        color: appearance.color ?? ARM_COLOR,
        metalness: appearance.metalness ?? 0,
        roughness: appearance.roughness ?? 0.6,
        transparent: true,
        opacity: ARM_OPACITY,
        depthWrite: false,
      })
    );
    frame.renderOrder = paintOrderOf(index) + ARM_FRAME_OFFSET;

    // Struck from the same extrusion, so the stroke follows the window and the footprint both.
    // It lives in the arm's own group, which is what moves, so there is nothing left behind to
    // keep in step - the failure the generic box outline had.
    const outlineEdges = new THREE.EdgesGeometry(solid);
    const outlineGeometry = new LineSegmentsGeometry();
    outlineGeometry.setPositions(outlineEdges.getAttribute("position").array);
    outlineEdges.dispose();
    const outlineMaterial = new THREE.Line2NodeMaterial({
      color: ARM_EDGE,
      linewidth: ARM_EDGE_WIDTH_3D,
      worldUnits: false,
    });
    outlineMaterial.resolution?.set(viewportEl.clientWidth || 1, viewportEl.clientHeight || 1);
    edgeMaterials.add(outlineMaterial);
    const outline = new LineSegments2(outlineGeometry, outlineMaterial);
    outline.frustumCulled = false;
    outline.renderOrder = paintOrderOf(index) + ARM_OUTLINE_OFFSET;

    const offset = referenceOffset(model);
    const line = new THREE.Mesh(
      new THREE.PlaneGeometry(REFERENCE_WIDTH, sy),
      new THREE.MeshBasicMaterial({
        color: REFERENCE_LINE, transparent: true, opacity: ARM_REFERENCE_OPACITY, depthTest: false,
      })
    );
    line.position.set(offset, sy / 2, -REFERENCE_DROP);
    // Under the frame in paint order as well as in z, so it reads through the window and is tinted
    // by the carriage everywhere else. Ordering, not position, is what decides this: depth testing
    // is off in an axis view, so a lower render order is the only thing that puts it underneath.
    line.renderOrder = paintOrderOf(index) + ARM_LINE_OFFSET;

    const group = new THREE.Group();
    group.add(line, frame, outline);
    group.matrixAutoUpdate = false;
    group.matrix.copy(world.matrices[index]);
    group.matrixWorldNeedsUpdate = true;
    view.add(group);

    const parent = world.parentOf[index];
    arms.push({
      group,
      frame,
      outline,
      line,
      index,
      parentMatrix: parent >= 0 ? world.matrices[parent].clone() : new THREE.Matrix4(),
      local: world.local.slice(index * 6, index * 6 + 6),
      referenceOffset: offset,
      currentX: world.local[index * 6],
      targetX: world.local[index * 6],
    });
  }
}

// Glide rather than teleport, so a move reads as motion. The tracker carries commanded targets, so
// this interpolation is cosmetic and says nothing about where the arm physically is mid-move.
const ARM_GLIDE_PER_SECOND = 6;

const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");

function updateArms(delta) {
  const reduce = reducedMotion?.matches;
  let moved = false;
  for (const arm of arms) {
    if (Math.abs(arm.targetX - arm.currentX) < 0.01) continue;
    moved = true;
    arm.currentX = reduce
      ? arm.targetX
      : arm.currentX + (arm.targetX - arm.currentX) * Math.min(1, delta * ARM_GLIDE_PER_SECOND);
    const local = new THREE.Matrix4().makeTranslation(
      arm.currentX, arm.local[1], arm.local[2]
    );
    arm.group.matrix.multiplyMatrices(arm.parentMatrix, local);
    arm.group.matrixWorldNeedsUpdate = true;

    // Keep the scene model in step with what is drawn. Everything else reads position from here -
    // the info panel, the selection box, the coordinate tool - so moving only the group would
    // leave all of them quoting where the arm used to be.
    mirrorPlacement(arm.index, arm.currentX, arm.group.matrix);
    // What the arm itself is drawn from. Its frame and outline ride the group and have moved
    // already, but everything else the arm owns is a separate object with a baked matrix - its
    // declared model above all, which hangs off the view rather than off the group - and the line
    // below deliberately skips the arm while it works out the subtree beneath it. Without this a
    // part drawn from a file stays where it was loaded while the arm travels out from under it,
    // which is what a model-drawn X-arm did: the reference line moved and the geometry did not.
    redraw([arm.index]);
    // Whatever rides the arm moves with it. Its own matrix is already set from the group, so only
    // what is beneath it needs working out.
    refreshSubtree(arm.index, true);
    if (selected === arm.index) {
      selectionBox.box.copy(worldBox(arm.index));
      refreshPlacement(arm.index);
    }
  }
  return moved;
}

// The tracked X, from the arm's own tracker when it has one. The frame's left edge sits at that X
// minus the reference offset, so the resource's box stays where the resource says it is.
// A resource has moved. Position is published as state now, the same way rotation always has been,
// so this is the one path by which anything that travels reaches the picture: an arm over a deck, a
// plate put down somewhere new, a robot between workcells.
//
// Its own transform changes, and so does the world transform of everything standing on it, so the
// subtree is recomputed and every instance in it repositioned.
// Recompute the world transform of everything at or beneath `index`, and move the drawn instances
// to match. A resource's own transform is relative to its parent, so a parent moving carries its
// children with it in the model for free - but the matrices the scene draws from are absolute, and
// those have to be worked out again.
// Move the drawn instances to wherever the world now says they are. The transforms are worked out
// in `world.js`, which has no idea any of this is on screen; this is only the part that is.
function redraw(indices) {
  const touched = new Set();
  for (const at of indices) {
    const [sx, sy, sz] = sizeOf(modelOf(at));
    // A resource that is switched off is drawn nowhere, and moving it does not turn it back on.
    // Putting the instance back at its new transform regardless is what brought a hidden plate's
    // wells back the moment anything under it moved.
    const visible = isVisible(at);
    const placement = placementOf[at];
    if (placement) {
      if (visible) placeInstance(placement.mesh, placement.slot, world.matrices[at], sx, sy, sz);
      else placement.mesh.setMatrixAt(placement.slot, ZERO);
      placement.mesh.instanceMatrix.needsUpdate = true;
    }
    // An outline is its own object with its own baked matrix, so a move that touched only the
    // instance left it standing at the old position - a wireframe ghost of whatever rode the arm.
    const line = edgeOf.get(at);
    if (line) line.matrix.copy(boxMatrix(world.matrices[at], sx, sy, sz));
    // A reference mark is its own object too, and marks a point ON the resource - so when the
    // resource travels, the point travels with it.
    for (const mark of referenceMarks) {
      if (mark.index !== at) continue;
      mark.plane.matrix.multiplyMatrices(world.matrices[at], mark.plane.userData.local);
      mark.plane.matrixWorldNeedsUpdate = true;
    }
    // A declared mesh is its own object with a baked matrix for the same reason, and needs the same
    // treatment: without it the model stays where it was loaded while the box it stood in for
    // travels on without it. An autoload sled driven along its rail showed exactly that - box in
    // one place, geometry in another.
    const root = meshRoots.find((r) => r.userData.index === at);
    if (root) {
      root.matrix.copy(world.matrices[at]);
      root.matrixWorldNeedsUpdate = true;
    }
    // A well's rim and cavity are their own instances, so a move that touched only the box left
    // them where the resource was - and left them standing when the resource was switched off.
    placeParts(at, touched);
  }
  for (const mesh of touched) mesh.instanceMatrix.needsUpdate = true;
}

function refreshSubtree(index, skipSelf) {
  redraw(refreshTransforms(index, skipSelf));
}

function applyLocation(index, location) {
  if (!setLocal(index, location)) return;

  // A travelling part is drawn by its own group and glides there, so it is told the target rather
  // than being moved under it. What stands on it is not part of that group, though - the 96-head
  // rides the arm in the model but is drawn with everything else - so the glide carries it.
  if (MOVING_PARTS.has(modelOf(index).category)) {
    const arm = arms.find((a) => a.index === index);
    if (arm) {
      arm.targetX = location.x;
      return;
    }
  }

  refreshSubtree(index);
}

// A resource has turned. Only its own transform changes, but everything standing on it is drawn
// from an absolute matrix worked out through that transform - so an arm's links carry the gripper,
// its fingers and their pads round with them, and all of it has to be recomputed.
function applyRotation(index, rotation) {
  if (!setLocalRotation(index, rotation)) return;
  refreshSubtree(index);
}

function setArmX(index, referenceX) {
  const arm = arms.find((a) => a.index === index);
  if (arm) arm.targetX = referenceX - arm.referenceOffset;
}

function buildGridMarks() {
  for (const mark of gridMarks) view.remove(mark);
  gridMarks = [];
  gridLabels = [];
  surfaces = [];

  for (let index = 0; index < world.names.length; index++) {
    const grid = modelOf(index).grid;
    if (!grid) continue;

    const group = new THREE.Group();
    group.matrixAutoUpdate = false;
    group.matrix.copy(world.matrices[index]);
    // With matrixAutoUpdate off, three only recomputes matrixWorld when told to; without this the
    // whole group silently renders at the identity transform.
    group.matrixWorldNeedsUpdate = true;

    const [ox, oy, oz] = grid.origin;
    const z = oz + GRID_LIFT;

    const [footprintX, footprintY] = sizeOf(modelOf(index));
    // Nothing to draw at all when it is fully transparent, rather than a draw call per deck that
    // contributes no pixels. The grid, the rail numbers and the access bands are their own objects
    // and stay either way, so the deck still reads as a deck.
    if (SURFACE_OPACITY > 0) {
      const surfaceMaterial = new THREE.MeshStandardMaterial({
        color: SURFACE_COLOR,
        metalness: 0.3,
        roughness: 0.42,
        transparent: true,
        opacity: SURFACE_OPACITY,
        // What stands on the deck is drawn opaque and so lands in the depth buffer first; the surface
        // is below it and fails against it, which is what keeps a plate from being tinted by the deck
        // it sits on. Writing depth as well would have the surface occlude whatever is under it.
        depthWrite: false,
      });
      const surface = new THREE.Mesh(
        new THREE.PlaneGeometry(footprintX, footprintY),
        surfaceMaterial
      );
      // The deck's own top face, so it paints just after the deck's box and just before whatever
      // stands on it. On the same height scale as everything else - left on the old tree-depth scale
      // it sorted below the box it belongs to, and the box covered it.
      surface.renderOrder = paintOrderOf(index) + 0.25;
      surface.userData.lit = surfaceMaterial;
      surface.userData.flat = flatVariant(surfaceMaterial);
      surfaces.push(surface);
      surface.position.set(footprintX / 2, footprintY / 2, oz);
      group.add(surface);
    }
    // A mark reaches forward of the grid into a margin the numbers sit in, and neither leaves the
    // resource it is drawn on: a line hanging off the front of a part reads as geometry that is not
    // there, and a number floating past the edge belongs to nothing. Where there is no margin to
    // reach into - a loading tray's grid starts at the tray's own front edge - both come inside.
    const half = GRID_LABEL_MM / 2;
    const ink = (GRID_LABEL_MM * GRID_LABEL_INK) / 2;
    const front = Math.max(oy - GRID_TICK, 0);
    const labelY = Math.max(oy - GRID_TICK - half, ink + GRID_MARGIN);
    const points = [];
    for (let i = 0; i < grid.count; i++) {
      const x = ox + i * grid.spacing;
      points.push(x, front, z, x, oy + grid.extent, z);

      const position = i + 1;
      if (position === 1 || position % grid.label_every === 0) {
        const sprite = labelSprite(String(position));
        gridLabels.push(sprite);
        // Between this mark and the next, so a number never sits on a line.
        sprite.position.set(x + grid.spacing / 2, labelY, z);
        group.add(sprite);
      }
    }

    // Access bands: two lines each, spanning the surface, labelled at the near edge.
    const bands = modelOf(index).bands ?? [];
    if (bands.length) {
      const bandPoints = [];
      // Lines only: the reach each pair belongs to is in the band's label if anything ever needs
      // it, but drawn on the deck the numbers competed with the rail numbering.
      for (const band of bands) {
        // A band runs only as far as whatever reaches across it says it does.
        const x0 = band.x_from ?? 0;
        const x1 = band.x_to ?? footprintX;
        for (const y of [band.from, band.to]) bandPoints.push(x0, y, z, x1, y, z);
      }
      const bandGeometry = new THREE.BufferGeometry();
      bandGeometry.setAttribute("position", new THREE.Float32BufferAttribute(bandPoints, 3));
      const bandLines = new THREE.LineSegments(
        bandGeometry,
        new THREE.LineBasicMaterial({ color: BAND_COLOR, depthTest: false })
      );
      bandLines.renderOrder = paintOrderOf(index) + 0.5;
      group.add(bandLines);
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
    const marks = new THREE.LineSegments(
        geometry,
        // Opaque, so it sorts with everything else: a transparent line renders after all opaque
        // geometry no matter its render order, which is what kept these on top of the carriers.
        new THREE.LineBasicMaterial({ color: GRID_LINE, depthTest: false })
    );
    // Just above the surface it is drawn on, and below anything standing on that surface.
    marks.renderOrder = paintOrderOf(index) + 0.5;
    marks.userData.mark = "solid";
    group.add(marks);

    // The same marks again, faint and over the top of whatever is standing on them, so the track a
    // carrier occupies can still be read off. Shares the geometry - this is a second material, not
    // a second set of lines.
    const showThrough = new THREE.LineSegments(
      geometry,
      new THREE.LineBasicMaterial({
        color: GRID_LINE, transparent: true, opacity: GRID_GHOST_OPACITY, depthTest: false,
      })
    );
    showThrough.userData.mark = "through";
    showThrough.renderOrder = OVERLAY_ORDER - 1;
    group.add(showThrough);

    group.userData.owner = index;
    view.add(group);
    gridMarks.push(group);
  }
}

// The facility's origin, drawn as a triad. Everything in the scene is measured from here, so it
// should be findable without hunting: the coordinate tool, the deck maths and the readouts all
// resolve against this point.
let originMarker = null;

const AXIS_COLORS = { x: 0xdc3545, y: 0x198754, z: 0x1a4b8c };

function buildOrigin() {
  if (originMarker) view.remove(originMarker);
  const length = 1; // unit sized; scaled to a constant screen size in updateOrigin()
  const shaft = length * 0.022;

  originMarker = new THREE.Group();
  // The facility is the root instance, so its own frame is the origin everything resolves against.
  originMarker.position.setFromMatrixPosition(world.matrices[0]);

  // Solid shafts rather than lines: a one-pixel line disappears the moment the scene is a room
  // wide, and the origin is the one thing that has to stay findable at any zoom.
  const axes = [
    [new THREE.Vector3(1, 0, 0), AXIS_COLORS.x],
    [new THREE.Vector3(0, 1, 0), AXIS_COLORS.y],
    [new THREE.Vector3(0, 0, 1), AXIS_COLORS.z],
  ];
  for (const [direction, color] of axes) {
    const material = new THREE.MeshStandardMaterial({ color, roughness: 0.45 });
    const body = length * 0.78;
    const bar = new THREE.Mesh(new THREE.CylinderGeometry(shaft, shaft, body, 12), material);
    const head = new THREE.Mesh(
      new THREE.ConeGeometry(shaft * 2.6, length - body, 14), material
    );
    // Cylinders and cones point along +Y; turn each onto its own axis.
    const quaternion = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
    bar.quaternion.copy(quaternion);
    head.quaternion.copy(quaternion);
    bar.position.copy(direction).multiplyScalar(body / 2);
    head.position.copy(direction).multiplyScalar(body + (length - body) / 2);
    originMarker.add(bar, head);
  }

  originMarker.add(
    new THREE.Mesh(
      new THREE.SphereGeometry(shaft * 2.2, 18, 14),
      new THREE.MeshStandardMaterial({ color: 0x1a1f22, roughness: 0.4 })
    )
  );

  // A ring flat on the floor, so the origin is still findable from directly above.
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(length * 0.15, length * 0.2, 48),
    new THREE.MeshBasicMaterial({ color: 0x1a4b8c, transparent: true, opacity: 0.6, side: THREE.DoubleSide })
  );
  originMarker.add(ring);

  // The origin is a marker on the viewport, not something standing in the scene, so it reads over
  // whatever is drawn there.
  originMarker.traverse((o) => {
    if (o.material) {
      o.material.depthTest = false;
      o.material.depthWrite = false;
      o.renderOrder = OVERLAY_ORDER;
    }
  });
  view.add(originMarker);
  updateOrigin();
}

// Below this many pixels across, a resource contributes noise rather than information. Set so a
// well still draws at a 500 mm scale bar, where a pixel is worth about 2.5 mm and a 6.9 mm well
// projects to roughly 2.7 px; it drops out at facility zoom, where it is closer to 1.4 px and a
// thousand of them read as grey haze. Its parent is still drawn, so nothing vanishes without
// something in its place.
const DETAIL_MIN_PX = 2;
// Below this a rail number is a smudge rather than a number. Nothing is lost by not drawing it, and
// at facility scale it is most of what the renderer is being asked to do.
const LABEL_MIN_PX = 7;
let detailScale = null;

function updateDetail() {
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;
  // Only rework when the scale has moved enough to change an answer.
  if (detailScale !== null && Math.abs(perPixel / detailScale - 1) < 0.02) return;
  detailScale = perPixel;

  // A rail number is drawn in deck millimetres, so how big it lands on screen is a division away.
  const labelsLegible = GRID_LABEL_MM / perPixel >= LABEL_MIN_PX;
  for (const label of gridLabels) {
    if (label.visible !== labelsLegible) label.visible = labelsLegible;
  }

  const drawn = new Set();
  for (const entry of meshes) {
    const [sx, sy] = sizeOf(entry.model);
    const visible = Math.max(sx, sy) / perPixel >= DETAIL_MIN_PX;
    if (entry.mesh.visible !== visible) entry.mesh.visible = visible;
    for (const overlay of entry.overlays ?? []) {
      if (overlay.visible !== visible) overlay.visible = visible;
    }
    if (visible) drawn.add(entry.modelIndex);
  }

  // In a free view an enclosure gets its fill back once nothing inside it is being drawn, so a
  // plate whose wells have been culled reads as a plate rather than an empty frame. In an axis
  // view everything is opaque and painted in order, so an enclosure keeps its fill whatever stands
  // in it - and the fill is put back here rather than left to the mode change that turns painting
  // on. A fill is only ever taken away in a free view, so one missed transition used to leave a
  // plan view drawn as bare outlines with nothing behind them, which is what it looked like.
  if (planView) {
    for (const entry of meshes) {
      const wanted = fillsBox(entry);
      if (entry.mesh.material.visible !== wanted) entry.mesh.material.visible = wanted;
    }
    return;
  }
  for (const entry of meshes) {
    // A container is exempt: its walls are what you read the layout off, so they stay drawn at
    // SHELL_OPACITY whether or not what it holds is on screen. Everything above it in the stack -
    // a bench, a device, the facility - still drops to its outline, which is what keeps the layers
    // from compounding.
    if (!entry.holdsEnclosure || keepsWalls(entry) || entry.modelDrawn) continue;
    const showsContents = entry.enclosedModels.some((m) => drawn.has(m));
    if (entry.mesh.material.visible === showsContents) entry.mesh.material.visible = !showsContents;
  }
}

// Whether the camera is looking straight down. The plan view is the one view drawn differently -
// flatter, and with a container's walls kept - and it is the only one, because "higher" and
// "nearer" are the same thing only from directly above.
//
// What it is NOT is a drawing painted by rule. Both views let the depth buffer decide what covers
// what, which is the one arbiter that is per-pixel and therefore right about every instance of a
// model at once. The plan view used to sort by a number worked out from each resource's height,
// with depth testing off; a number cannot be per instance - one mesh carries them all - so every
// well on a deck was ordered as the tallest well in the room, and everything that had to be seen
// over them needed its own exception. The exceptions are gone. What is left ordered by hand is
// what has no depth of its own to test: grids, numbers, marks and outlines, up in OVERLAY_ORDER.
let planView = null;

// What a resource stands on, in facility mm, and then how deep it sits in the tree. Depth testing is
// off in an axis view, so what paints last is what shows, and nesting alone decided that - which put
// a well 100 mm up over a 96-head 400 mm up, because the well sat one level deeper. Height leads
// now; nesting only separates things standing at the same level, where a parent still paints first.
//
// Where a resource stands rather than how high it reaches: a shell is as tall as everything it
// holds, so reaching highest would have the facility paint over its own contents.
const PAINT_LEVEL = 100;

function paintOrderOf(index) {
  return world.matrices[index].elements[14] * PAINT_LEVEL + treeDepth(index) * 2;
}

// Anything drawn over the scene rather than in it - the origin marker, the highlight boxes, the
// measurement legs - orders above every paint order a resource can reach. A fixed number cannot do
// that on its own: paint order is a height in millimetres times PAINT_LEVEL, so the band has to
// start past the tallest facility anyone will draw. Ten metres of stacked equipment is that.
const OVERLAY_ORDER = 10_000 * PAINT_LEVEL;

// The layer a part held over the deck is drawn in, above what stands on the deck. Not a height and
// not a band of them: depth says what is over what, and this says only that a travelling part is
// blended over the deck rather than into it. Whole numbers, with room for a vessel's own contents
// between them.
const CARRIED_LAYER = 2;


// Whether a model's box is drawn as a filled solid at all. A part that travels over the deck is
// drawn see-through wherever it is, and a model whose own geometry has arrived has no use for the
// box that stood in for it. Every mode change asks this rather than each one deciding again.
function fillsBox(entry) {
  // A vessel's box is drawn by its own cavity mesh, which fills it exactly. Drawing the box as well
  // puts two surfaces on the same plane, and the depth buffer cannot choose between them.
  return !MOVING_PARTS.has(entry.model.category) && !entry.modelDrawn && !entry.isVessel;
}

// Whether this is a thing you look into. Its walls stay drawn, at the shell's opacity, however much
// is standing in it: a plate with no walls is a floor with wells floating over it, and a carrier
// with none is a line around some plates.
function keepsWalls(entry) {
  return isCarrier(entry.model) || CONTAINERS.has(entry.model.category);
}

// How see-through a resource is drawn, whatever the angle it is seen from. A part that travels is
// the see-through one, because drawn solid it hides whatever it happens to be above.
function OPACITY_OF(isSpace, moves, isTipRack, isShell) {
  if (isSpace) return SPACE_OPACITY;
  if (moves) return MOVING_OPACITY;
  if (isTipRack) return TIP_RACK_OPACITY;
  if (isShell) return SHELL_OPACITY;
  return BOX_OPACITY;
}

function setRenderMode(plan) {
  for (const entry of meshes) {
    // Lit, at every angle. The lights ride with the camera, so a surface is shaded by its own shape
    // rather than by where it is being looked at - which is what makes a box read as a box without
    // the view being able to change what colour it is.
    const material = entry.mesh.material.userData.lit ?? entry.mesh.material;
    if (entry.mesh.material !== material) entry.mesh.material = material;
    const isShell = entry.holdsEnclosure;
    // A tip rack is read by which of its positions still hold a tip, so it is drawn see-through at
    // its own opacity rather than at the shell's - both in a plan view and in a free one.
    const isTipRack = entry.model.category === "tip_rack";
    const moves = MOVING_PARTS.has(entry.model.category);
    // Ground: the space things stand in, and the surface they stand on. Neither is a thing to look
    // at, and drawing either solid hides what it carries - a deck drawn opaque is a sheet the same
    // colour as the plates on it. Both stay barely there in every mode: enough to see where the
    // floor ends and where the deck reaches, never enough to tint what is on them. What makes a
    // deck legible is its rail grid, its numbers and its access bands, and those are their own
    // objects.
    const isSpace = GROUND.has(entry.model.category);
    const rides = entry.instances.some((i) => travels(i));

    material.transparent = true;
    material.opacity = OPACITY_OF(isSpace, moves, isTipRack, isShell);
    material.side = isShell || isSpace ? THREE.BackSide : THREE.FrontSide;
    // Ground stays out of the depth buffer in either view - a wash over the picture, not a surface
    // anything is behind.
    material.depthTest = !isSpace;
    // Everything writes depth, in either view, so the nearest surface wins per pixel - which is
    // right about every instance of a model at once, where an order could only ever be right about
    // the model.
    //
    // A container used to be held out of the depth buffer in a free view, so that you could look
    // into it. It never needed to be: a container is drawn back-faces-only, so the only surface of
    // a plate ever seen is its far wall, which stands behind its own contents and cannot hide
    // them. What holding it out did instead was stop it hiding anything at all - a plate behind
    // another still drew, a well printed through its own plate from underneath, and an empty
    // well's white cavity read as floating with nothing around it.
    //
    // A part held over the deck writes too, and it costs nothing: it is drawn after everything it
    // is above, so the deck is already in the picture and a depth written now cannot rub it out.
    // What it does stop is the part shading itself - an arm is several surfaces and it carries
    // channels and an iSWAP, and with nothing to separate them each overlap blended again, so the
    // arm came out at a third of its own opacity here and two thirds there as it travelled.
    material.depthWrite = !isSpace;
    // A shell that is not a container shows its outline and nothing else: a device drawn as a
    // sheet the size of the device sits under everything standing on it. A container keeps its
    // walls, which is what makes it read as one.
    material.visible = fillsBox(entry) && (!isShell || keepsWalls(entry));
    // Two layers, and only in a plan: what stands on the deck, and what is held over it. Depth
    // decides everything else; this decides only that a travelling part is blended over the deck
    // rather than into it.
    const layer = plan && rides ? CARRIED_LAYER : 0;
    entry.mesh.renderOrder = layer;
    for (const overlay of entry.overlays ?? []) {
      if (overlay.userData.lit) overlay.material = overlay.userData.lit;
      // From above a vessel has to show its own contents, and depth would stop it: a tip hangs
      // below the spot that holds it, and liquid sits below the cavity it fills. So in a plan they
      // are painted in their own resource's layer, which is as far as they can reach. From any
      // other angle depth is right - what stands in front of a well does cover it - and there they
      // test like everything else.
      overlay.material.depthTest = !plan;
      overlay.material.depthWrite = !plan;
      overlay.renderOrder = plan ? layer + (overlay.userData.behind ? 0.5 : 1) : 0;
      overlay.material.needsUpdate = true;
    }
    material.needsUpdate = true;
  }

  for (const surface of surfaces) {
    surface.material = surface.userData.lit;
  }

  for (const root of meshRoots) {
    root.traverse((o) => {
      if (!o.isMesh) return;
      if (o.userData.lit) o.material = o.userData.lit;
      const modelled = o.userData.asModelled;
      const rides = travels(o.userData.declaredBy);
      // Glazing comes out of an axis view. A part that travels keeps whatever it was modelled with,
      // see-through included: it is drawn that way so the deck under it can be read.
      o.material.visible = !(plan && modelled?.glazed && !rides);
      if (!modelled) return;
      // A part held over the deck is drawn see-through, as its box is, so that what it is above
      // still reads through it. It does not write depth for the same reason; it still tests, so
      // the machine's own structure above it covers it as it should.
      const lifted = plan && rides;
      o.material.transparent = lifted ? true : modelled.transparent;
      o.material.opacity = lifted ? Math.min(modelled.opacity, MOVING_OPACITY) : modelled.opacity;
      o.material.depthWrite = lifted ? true : modelled.depthWrite;
      o.material.depthTest = true;
      o.renderOrder = lifted ? CARRIED_LAYER : 0;
      o.material.needsUpdate = true;
    });
  }

  for (const mark of gridMarks) {
    mark.traverse((o) => {
      if (!o.material) return;
      // The faint copy belongs to a plan alone, and which view that is follows the camera rather
      // than the mode: an elevation is axis-aligned too, and from the front a line over a carrier
      // is a line through it.
      if (o.userData.mark === "through") {
        o.visible = planView === true;
        return;
      }
      // Against depth, in either view: a mark lies on the deck surface, and whatever stands on the
      // deck is above it. It used to be drawn without depth and kept under a carrier by its order
      // instead, which stopped working the moment content stopped carrying orders - the solid mark
      // was then painted over every carrier at full strength, which is the error the faint copy
      // handled above exists to avoid.
      if (o.material.depthTest !== undefined) o.material.depthTest = true;
    });
  }

  for (const arm of arms) {
    // Where the part stands now. An arm is the one thing in the scene that travels, so the order it
    // was built with is the order it had when it was somewhere else - and in an axis view, where
    // depth testing is off, that order is the whole of what puts it over the deck it passes above.
    const order = paintOrderOf(arm.index);
    arm.frame.renderOrder = order + ARM_FRAME_OFFSET;
    arm.outline.renderOrder = order + ARM_OUTLINE_OFFSET;
    arm.outline.material.depthTest = !plan;
    arm.outline.material.linewidth = plan ? ARM_EDGE_WIDTH_FLAT : ARM_EDGE_WIDTH_3D;
    arm.outline.material.needsUpdate = true;
    // The reference line lies under the carriage, so in a 3D view the channels hanging off it stand
    // in front and have to hide it - but only those, not the deck it is well above.
    //
    // A transparent material always draws after every opaque one, whatever its render order, so as
    // long as the line was transparent it could only be wholly in front or wholly behind. Opaque and
    // drawn first, it lays down its own depth: what is nearer covers it, what is further fails
    // against it and stays behind. In an axis view nothing is in front of anything, so there it goes
    // back to painting through, under the carriage it marks.
    arm.line.material.transparent = plan;
    arm.line.material.opacity = plan ? ARM_REFERENCE_OPACITY : 1;
    arm.line.material.depthTest = !plan;
    arm.line.renderOrder = plan ? order + ARM_LINE_OFFSET : -1;
    arm.line.material.needsUpdate = true;
  }

  for (const [index, line] of edgeOf) {
    // All that is left of a box whose model is being drawn: its border, and only just.
    const stoodIn = drawnFromFile.has(index);
    line.material.depthTest = !plan;
    line.material.opacity = stoodIn
      ? MODEL_EDGE_OPACITY
      : plan
        ? 1
        : line.userData.baseOpacity;
    line.material.linewidth = plan ? EDGE_WIDTH_FLAT : EDGE_WIDTH_3D;
    line.material.color.set(plan ? FLAT_EDGE : line.userData.baseColor);
    line.renderOrder = plan ? paintOrderOf(index) + 1 : 0;
    const wanted = plan ? line.userData.footprintGeometry : line.userData.boxGeometry;
    if (wanted && line.geometry !== wanted) line.geometry = wanted;
    line.material.needsUpdate = true;
  }
}

function updateEdgeMode() {
  const direction = camera.position.clone().sub(controls.target).normalize();
  // Looking straight down, and nothing else. An elevation is axis-aligned too and used to get the
  // same treatment, which is wrong twice over: from the front, higher is not nearer, and a drawing
  // painted by height puts the deck's back row in front of its front row. Looking straight up is
  // not a plan either - what is highest is then furthest away.
  const plan = direction.z > 0.999;
  // No frame is asked for: this runs inside one, and the camera only reaches an axis through an
  // input, which has asked for frames already and is still damping to a stop.
  if (plan === planView) return;
  planView = plan;
  for (const mark of gridMarks) showThroughMarks(mark);
  setRenderMode(plan);
}

/** Show or hide the faint copies of a grid's marks, which belong to a plan view alone. */
function showThroughMarks(group) {
  group.traverse((o) => {
    if (o.userData.mark === "through") o.visible = planView === true;
  });
}

// Held at a constant size on screen, so it marks the origin without swamping a close view or
// vanishing from a wide one.
const ORIGIN_PX = 90;

// How close to the edge of the viewport an origin that has left it is brought, in normalised
// device coordinates. Inside 1.0 so the whole marker shows rather than half of it.
const ORIGIN_EDGE = 0.92;

const _originAt = new THREE.Vector3();
const _originNdc = new THREE.Vector3();
const _originDepth = new THREE.Vector3();

function updateOrigin() {
  if (!originMarker) return;
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;
  originMarker.scale.setScalar(perPixel * ORIGIN_PX);

  // The frame every coordinate in the panel is measured against, so it is the one marker that must
  // not be able to go missing: panned or zoomed far enough, the facility's own corner leaves the
  // viewport entirely. It is held at the edge instead, in the direction the origin actually lies,
  // which keeps both the frame and the way back to it on screen.
  _originAt.setFromMatrixPosition(world.matrices[0]);
  _originNdc.copy(_originAt).project(camera);
  // A point behind the camera projects mirrored through the centre, so it would be pinned to the
  // opposite edge from the one it lies towards.
  if (_originAt.clone().applyMatrix4(camera.matrixWorldInverse).z > 0) {
    _originNdc.x *= -1;
    _originNdc.y *= -1;
  }
  if (Math.abs(_originNdc.x) <= ORIGIN_EDGE && Math.abs(_originNdc.y) <= ORIGIN_EDGE) {
    originMarker.position.copy(_originAt);
    return;
  }
  // Held at the depth the camera is looking at, so it is drawn at the size the scale above gives it.
  const depth = _originDepth.copy(controls.target).project(camera).z;
  originMarker.position
    .set(
      Math.max(-ORIGIN_EDGE, Math.min(ORIGIN_EDGE, _originNdc.x)),
      Math.max(-ORIGIN_EDGE, Math.min(ORIGIN_EDGE, _originNdc.y)),
      depth
    )
    .unproject(camera);
}

// The floor grid follows the view, the way the existing visualizer's does: its spacing is chosen
// from how many millimetres a pixel is worth, and it re-centres on what you are looking at. A grid
// fixed at build time is a grid that means nothing once you zoom.
let grid = null;
let gridState = null;
let floorZ = 0;

// 1, 2 or 5 times a power of ten: the spacings a person can count in.
function niceNumber(value) {
  const magnitude = Math.pow(10, Math.floor(Math.log10(Math.max(value, 1e-6))));
  return ([1, 2, 5, 10].map((m) => m * magnitude).find((v) => v >= value) ?? magnitude * 10);
}

function mmPerPixel() {
  const height = viewportEl.clientHeight || 1;
  if (projection === "orthographic") {
    return (camera.top - camera.bottom) / camera.zoom / height;
  }
  const distance = camera.position.distanceTo(controls.target);
  return (2 * distance * Math.tan((camera.fov * DEG) / 2)) / height;
}

// Aim for a cell around this many pixels: dense enough to measure against, open enough to see past.
const GRID_TARGET_PX = 64;
const GRID_MAX_DIVISIONS = 320;

// The floor is drawn with the same fat lines everything else uses, rather than with `GridHelper`:
// a hairline is one device pixel whatever width is asked for, which on a retina display is half of
// what it looks like anywhere else and too faint to measure against either way. In CSS pixels, so
// it means the same thing on every screen.
const FLOOR_LINE = 0xc0c7cc;
const FLOOR_AXIS = 0x9aa4ab; // the two lines through the centre, which say where the origin is
const FLOOR_LINE_WIDTH = 1.4;
const FLOOR_AXIS_WIDTH = 2.0;

const floorMaterials = ["line", "axis"].map((kind) => {
  const material = new THREE.Line2NodeMaterial({
    color: kind === "axis" ? FLOOR_AXIS : FLOOR_LINE,
    linewidth: kind === "axis" ? FLOOR_AXIS_WIDTH : FLOOR_LINE_WIDTH,
    worldUnits: false,
  });
  material.resolution?.set(viewportEl.clientWidth || 1, viewportEl.clientHeight || 1);
  return material;
});

function updateGrid() {
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;

  const span = perPixel * Math.hypot(viewportEl.clientWidth, viewportEl.clientHeight) * 1.3;
  // Coarsen the spacing rather than shrink the coverage: a grid that stops inside the viewport
  // reads as a hole in the floor, whereas a larger cell just reads as a larger cell.
  const cell = Math.max(niceNumber(perPixel * GRID_TARGET_PX), niceNumber(span / GRID_MAX_DIVISIONS));
  const divisions = Math.max(4, Math.ceil(span / cell));
  // Snap the centre to the spacing, or the lines crawl as you pan.
  const cx = Math.round(controls.target.x / cell) * cell;
  const cy = Math.round(controls.target.y / cell) * cell;

  if (
    gridState &&
    gridState.cell === cell &&
    gridState.divisions === divisions &&
    gridState.cx === cx &&
    gridState.cy === cy
  ) {
    return;
  }
  gridState = { cell, divisions, cx, cy };

  for (const line of grid ?? []) {
    view.remove(line);
    line.geometry.dispose();
  }

  // Already in PLR's XY: the lines are built in the plane rather than laid down from another one.
  const half = (divisions * cell) / 2;
  const runs = [[], []]; // ordinary lines, then the two the world's own axes fall on
  // A line is an axis when it lands on zero IN THE WORLD, which is not the middle of the patch: the
  // patch follows the camera, so its middle is wherever you happen to be looking. The geometry is
  // built around the patch's centre and drawn at (cx, cy), so a local t sits at t + cx or t + cy.
  // Where the origin is off the patch entirely, neither axis is drawn, which is the truth.
  const axis = (at) => (Math.abs(at) < cell * 1e-6 ? 1 : 0);
  for (let i = 0; i <= divisions; i++) {
    const t = -half + i * cell;
    runs[axis(t + cy)].push(-half, t, 0, half, t, 0);
    runs[axis(t + cx)].push(t, -half, 0, t, half, 0);
  }

  grid = runs
    .map((points, kind) => {
      if (!points.length) return null;
      const geometry = new LineSegmentsGeometry();
      geometry.setPositions(new Float32Array(points));
      // A fat line is sized in pixels, so it needs the viewport it is being sized against. Set here
      // as well as on a resize, because the first grid is built before the first resize lands.
      floorMaterials[kind].resolution?.set(
        viewportEl.clientWidth || 1,
        Math.max(viewportEl.clientHeight, 1)
      );
      const line = new LineSegments2(geometry, floorMaterials[kind]);
      line.position.set(cx, cy, floorZ);
      line.renderOrder = -1;
      line.frustumCulled = false;
      view.add(line);
      return line;
    })
    .filter(Boolean);
}

const selectionBox = new THREE.Box3Helper(new THREE.Box3(), new THREE.Color(SELECT));
selectionBox.visible = false;
view.add(selectionBox);

const hoverBox = new THREE.Box3Helper(new THREE.Box3(), new THREE.Color(HOVER));
hoverBox.visible = false;
view.add(hoverBox);

// Both highlights draw over everything. A hairline box behind translucent walls, landing on the
// resource's own outline, is a highlight nobody can see.
for (const helper of [selectionBox, hoverBox]) {
  helper.material.depthTest = false;
  helper.material.transparent = true;
  helper.renderOrder = OVERLAY_ORDER + 30;
}


// three's own view helper, in place of the hand-drawn legend: same three axes, but clickable,
// and it animates the camera onto the axis you pick.
const clock = new THREE.Clock();
let viewHelper = null;

function buildViewHelper() {
  viewHelper?.dispose();
  viewHelper = new ViewHelper(camera, renderer.domElement);
  viewHelper.corner = "left";
  viewHelper.setLabels("X", "Y", "Z");
}

// Unit primitives, shared by every model of the same shape and scaled per instance.
const BOX = new THREE.BoxGeometry(1, 1, 1);
const CYL = new THREE.CylinderGeometry(0.5, 0.5, 1, 20).rotateX(Math.PI / 2);
// Open at both ends. A shaft is a length of tube: the bottom is where a tip goes on and the top is
// where the channel carries on, so capping either reads as a solid slug hanging off the head.
const TUBE = new THREE.CylinderGeometry(0.5, 0.5, 1, 20, 1, true).rotateX(Math.PI / 2);
const CONE = new THREE.ConeGeometry(0.5, 1, 14).rotateX(-Math.PI / 2);

// Above this many instances of one model, outlining each stops being cheap.
const EDGE_LIMIT = 160;

// ---------------------------------------------------------------- scene build



// A resource says what shape it is through `cross_section_type`. A tip spot does not serialize
// one, though it is plainly round, so it is special-cased here; upstream it should declare the
// field the way a well does, and this line can go.
// The same material, unlit. Colours land exactly as specified rather than being darkened by the
// lighting, and per-instance colour still works because basic materials multiply it into the fill.
function flatVariant(material) {
  const flat = new THREE.MeshBasicMaterial({ color: material.color.clone() });
  flat.transparent = material.transparent;
  flat.opacity = material.opacity;
  flat.side = material.side;
  flat.visible = material.visible;
  // What is printed on a part is part of it. A model's maps carry the branding, the door labels
  // and the biohazard mark, and a twin built from the colour alone drops all of them - the part
  // arrives blank, which reads as the print having been removed rather than the light changed.
  flat.map = material.map ?? null;
  flat.alphaMap = material.alphaMap ?? null;
  flat.alphaTest = material.alphaTest;
  flat.aoMap = material.aoMap ?? null;
  if (material.emissiveMap) flat.map = flat.map ?? material.emissiveMap;
  return flat;
}

function geometryFor(model) {
  // A shaft is open at both ends; anything else round is a vessel or a spot, which is not.
  if (model.category === "tip_mounting_shaft") return TUBE;
  return model.cross_section_type === "circle" || model.category === "tip_spot" ? CYL : BOX;
}

// The outline a resource drops to in an axis view. Unit-sized and scaled per instance, so there is
// one of each shape rather than one per model.
function ringFootprint(corners) {
  const points = [];
  for (let corner = 0; corner < corners; corner++) {
    // Square from the diagonals, circle from a fine enough ring: the same walk either way.
    const from = ((corner + 0.5) / corners) * Math.PI * 2;
    const to = ((corner + 1.5) / corners) * Math.PI * 2;
    const reach = corners === 4 ? Math.SQRT1_2 : 0.5;
    points.push(
      Math.cos(from) * reach, Math.sin(from) * reach, -0.5,
      Math.cos(to) * reach, Math.sin(to) * reach, -0.5
    );
  }
  const geometry = new LineSegmentsGeometry();
  geometry.setPositions(new Float32Array(points));
  return geometry;
}

const SQUARE_FOOTPRINT = ringFootprint(4);
const ROUND_FOOTPRINT = ringFootprint(20);

const footprintFor = (model) => (geometryFor(model) === BOX ? SQUARE_FOOTPRINT : ROUND_FOOTPRINT);
const colorFor = (model) =>
  model.appearance?.color ?? RESOURCE_COLORS[model.category] ?? RESOURCE_COLORS.default;
// "TipRack" -> "tipracks", as the existing visualizer writes them. Deliberately naive: a count is
// always in front of it, so "1 plates" reads as a count rather than as a mistake.
const plural = (type) => String(type).toLowerCase() + "s";

// The plural naming these resources, or "" if they are not all of one kind and so cannot be
// counted as one thing.
function countable(indices) {
  const types = new Set(indices.map((i) => modelOf(i).type));
  return types.size === 1 ? plural(modelOf(indices[0]).type) : "";
}
const hexOf = (n) => "#" + n.toString(16).padStart(6, "0");

const IDENTITY_Q = new THREE.Quaternion();
const tmpMatrix = new THREE.Matrix4();
const tmpVec = new THREE.Vector3();
const tmpScale = new THREE.Vector3();
const ZERO = new THREE.Matrix4().makeScale(0, 0, 0);

// A resource's origin is the minimum corner of its box, so the drawn centre sits half a size in.
function boxMatrix(matrix, sx, sy, sz, ox, oy, oz) {
  tmpVec.set(ox ?? sx / 2, oy ?? sy / 2, oz ?? sz / 2);
  tmpScale.set(sx, sy, sz);
  return tmpMatrix.compose(tmpVec, IDENTITY_Q, tmpScale).premultiply(matrix);
}

function placeInstance(mesh, slot, matrix, sx, sy, sz, ox, oy, oz) {
  mesh.setMatrixAt(slot, boxMatrix(matrix, sx, sy, sz, ox, oy, oz));
}

function isEnclosure(index) {
  const model = modelOf(index);
  return (
    world.childrenOf[index].length > 0 ||
    model.max_volume !== undefined ||
    MOVING_PARTS.has(model.category)
  );
}

function collectEnclosedModels(index, into) {
  for (const child of world.childrenOf[index]) {
    if (isEnclosure(child)) into.add(world.modelOf[child]);
    collectEnclosedModels(child, into);
  }
}


// A carrier is the level you look at rather than through: its floor is filled in, and its walls
// keep their fill instead of being culled when the things it holds are drawn.
function isCarrier(model) {
  return String(model.category).includes("carrier");
}

function enclosureDepth(index) {
  let depth = 0;
  for (let i = world.parentOf[index]; i >= 0; i = world.parentOf[i]) {
    if (isEnclosure(i)) depth++;
  }
  return depth;
}

function hasEnclosedDescendant(index) {
  for (const child of world.childrenOf[index]) {
    if (isEnclosure(child) || hasEnclosedDescendant(child)) return true;
  }
  return false;
}

function buildMeshes() {
  for (const entry of meshes) {
    view.remove(entry.mesh);
    entry.mesh.dispose();
  }
  for (const line of edgeOf.values()) view.remove(line);
  edgeMaterials.clear();
  planView = null;
  detailScale = null;
  meshes = [];
  placementOf = new Array(world.names.length);
  vesselOf = new Map();
  overlayOf = new Map();
  tipOf = new Map();
  edgeOf = new Map();
  drawnFromFile = new Set();

  const byModel = new Map();
  for (let i = 0; i < world.names.length; i++) {
    const m = world.modelOf[i];
    if (!byModel.has(m)) byModel.set(m, []);
    byModel.get(m).push(i);
  }

  for (const [modelIndex, instances] of byModel) {
    const model = world.models[modelIndex];
    const [sx, sy, sz] = sizeOf(model);

    // A well or a tip spot is not drawn as a shell to see through, but as a rim with an inside:
    // the rim gives it an edge thick enough to find, and the inside carries what is in it. That is
    // how the existing visualizer draws them, and it is what survives being looked at from above.
    const isVessel =
      (Number.isFinite(model.max_volume) && model.max_volume > 0) || model.category === "tip_spot";

    // A resource that holds something is an enclosure: other resources, read off the tree, or
    // liquid, read off its own capacity. Neither test names a resource type.
    const encloses =
      !isVessel &&
      (instances.some((i) => world.childrenOf[i].length > 0) ||
        model.max_volume !== undefined ||
        MOVING_PARTS.has(model.category));

    // But only the innermost enclosures are filled. A well in a plate on a holder on a carrier on
    // a deck in a device in a facility sits under six translucent shells, and six layers at 0.3
    // opacity leave about a tenth of the contrast underneath. So anything that holds another
    // enclosure is drawn as its outline alone, and only the level you are actually looking into
    // keeps a fill.
    const holdsEnclosure = encloses && instances.some((i) => hasEnclosedDescendant(i));

    const material = new THREE.MeshStandardMaterial({
      color: colorFor(model),
      roughness: 0.68,
      metalness: 0.0,
      transparent: encloses,
      opacity: encloses ? 0.26 : 1.0,
      depthWrite: !encloses,
      side: encloses ? THREE.BackSide : THREE.FrontSide,
    });

    if (isVessel) material.color.setHex(VESSEL_RIM);
    // Looking down an axis, a lit material reports the light rather than the resource: horizontal
    // top faces take the environment's ceiling head-on and wash out, which is what took the colour
    // out of a plan view. The overlays and the deck surfaces already switch to an unlit twin
    // there; the box that carries most of the picture was the one thing that did not.
    material.userData.flat = flatVariant(material);
    material.userData.lit = material;

    if (MOVING_PARTS.has(model.category)) material.visible = false;

    const mesh = new THREE.InstancedMesh(geometryFor(model), material, instances.length);
    mesh.frustumCulled = false;
    instances.forEach((globalIndex, slot) => {
      placeInstance(mesh, slot, world.matrices[globalIndex], sx, sy, sz);
      placementOf[globalIndex] = { mesh, slot };
    });
    mesh.instanceMatrix.needsUpdate = true;
    mesh.userData.instances = instances;
    view.add(mesh);
    meshes.push({
      mesh, model, modelIndex, instances,
      depth: treeDepth(instances[0]),
      lit: material,
      flat: flatVariant(material),
      // Filled in below, once the overlays this model needs are known.
      overlays: /** @type {any[]} */ ([]),
      // Set once this model's declared .glb has arrived and been placed.
      modelDrawn: false,
      holdsEnclosure: false,
      enclosedModels: /** @type {any[]} */ ([]),
    });

    // The existing visualizer strokes every resource, and a translucent box on a white ground
    // needs that stroke to read at all. So does a solid one that holds nothing: a 96-head, a
    // channel, a loading tray. What decides is how many there are, not whether anything is inside -
    // an outline is one line object per instance, and there are a thousand wells. The count is the
    // whole of the cost control, and it already excludes exactly the things too small to read.
    //
    // A travelling part draws its own frame and moves, so it gets no generic box outline: the box
    // would describe the slab rather than the frame, and it would be a second thing to keep in
    // step with every move - which is exactly what left a ghost behind at the old position.
    if (!MOVING_PARTS.has(model.category) && instances.length <= EDGE_LIMIT) {
      const boxEdges = new THREE.EdgesGeometry(geometryFor(model));
      const edgeGeometry = new LineSegmentsGeometry();
      edgeGeometry.setPositions(boxEdges.getAttribute("position").array);
      boxEdges.dispose();
      // Looking down an axis, every edge projects onto the footprint anyway, and the verticals
      // collapse to points. Keeping a footprint-only geometry to swap in removes that redundancy
      // and, more usefully, stops stacked shapes reading as a thicket in a plan view.
      const footprintGeometry = footprintFor(model);
      const style = structureEdgeStyle(enclosureDepth(instances[0]));
      const edgeMaterial = new THREE.Line2NodeMaterial({
        color: style.color,
        transparent: true,
        opacity: style.opacity,
        linewidth: EDGE_WIDTH_3D,
        worldUnits: false,
      });
      edgeMaterial.resolution?.set(viewportEl.clientWidth || 1, viewportEl.clientHeight || 1);
      edgeMaterials.add(edgeMaterial);
      for (const globalIndex of instances) {
        const line = new LineSegments2(edgeGeometry, edgeMaterial);
        line.userData.boxGeometry = edgeGeometry;
        line.userData.baseOpacity = style.opacity;
        line.userData.baseColor = style.color.clone();
        line.userData.footprintGeometry = footprintGeometry;
        line.matrixAutoUpdate = false;
        line.matrix.copy(boxMatrix(world.matrices[globalIndex], sx, sy, sz));
        line.frustumCulled = false;
        view.add(line);
        edgeOf.set(globalIndex, line);
      }
    }

    // A trough reports an infinite capacity, which arrives as the string "Infinity". There is no
    // fill fraction to draw against that, so it gets no liquid body.
    // Which enclosure models sit inside this one. An outline is only the right answer while its
    // contents are actually being drawn; once they are culled the outline has nothing to frame.
    const enclosedModels = new Set();
    for (const i of instances) collectEnclosedModels(i, enclosedModels);

    const overlays = [];

    // A carrier's base is solid, so looking into one should stop at its floor rather than carrying
    // on through to the deck. The shell stays see-through; only the bottom face is filled in.
    if (encloses && isCarrier(model)) {
      const floor = new THREE.InstancedMesh(
        // Unit geometry: `placeInstance` supplies the real size through the instance matrix, so a
        // pre-sized plane would be scaled by its own dimensions a second time.
        new THREE.PlaneGeometry(1, 1),
        new THREE.MeshStandardMaterial({ color: colorFor(model), roughness: 0.7 }),
        instances.length
      );
      floor.frustumCulled = false;
      instances.forEach((globalIndex, slot) => {
        // A hair above its own base, or it fights the deck surface it stands on for depth.
        const at = [sx, sy, 1, sx / 2, sy / 2, 0.3];
        placeInstance(floor, slot, world.matrices[globalIndex], ...at);
        remember(globalIndex, floor, slot, at);
      });
      floor.instanceMatrix.needsUpdate = true;
      floor.userData.lit = floor.material;
      floor.userData.flat = flatVariant(floor.material);
      view.add(floor);
      overlays.push(floor);
    }

    if (isVessel) {
      // The wall, standing outside the cavity. Grown rather than inset, because the box IS the
      // cavity - and grown rather than left as the box itself, because a box and a cavity on the
      // same plane are two surfaces at the same depth, which is what made the side of every well
      // shimmer. Nothing is coplanar with anything now.
      const wall = new THREE.InstancedMesh(
        geometryFor(model),
        new THREE.MeshStandardMaterial({
          color: VESSEL_RIM,
          roughness: 0.6,
          transparent: true,
          opacity: VESSEL_WALL_OPACITY,
        }),
        instances.length
      );
      wall.frustumCulled = false;
      instances.forEach((globalIndex, slot) => {
        const at = [sx + 2 * VESSEL_WALL, sy + 2 * VESSEL_WALL, sz, sx / 2, sy / 2, sz / 2];
        placeInstance(wall, slot, world.matrices[globalIndex], ...at);
        remember(globalIndex, wall, slot, at);
      });
      wall.instanceMatrix.needsUpdate = true;
      wall.userData.lit = wall.material;
      wall.userData.flat = flatVariant(wall.material);
      wall.userData.behind = true; // painted before the cavity it surrounds
      view.add(wall);
      overlays.push(wall);

      const inner = new THREE.InstancedMesh(
        geometryFor(model),
        // Flagged transparent although it is fully opaque, so that it sits in the same pass as the
        // wall around it. Three draws every transparent object after every opaque one whatever the
        // render order says, so an opaque cavity inside a see-through wall is painted first and
        // then covered by the wall's own top face - which is what hid the well from above.
        new THREE.MeshStandardMaterial({
          color: 0xffffff, roughness: 0.55, transparent: true, opacity: 1,
        }),
        instances.length
      );
      inner.frustumCulled = false;
      const white = new THREE.Color(VESSEL_EMPTY);
      instances.forEach((globalIndex, slot) => {
        // The cavity IS the box. A container's size is what it holds, and the material around it
        // stands outside that - so the inside fills the resource's own extent exactly, and a wall
        // is never drawn within it. Drawn inset, as this was, the walls were inside the box, which
        // is the opposite of what the box means. A hair taller only, so that from directly above it
        // does not fight the box's own top face for depth.
        const at = [sx, sy, sz * 1.02, sx / 2, sy / 2, (sz * 1.02) / 2];
        placeInstance(inner, slot, world.matrices[globalIndex], ...at);
        remember(globalIndex, inner, slot, at);
        inner.setColorAt(slot, white);
        vesselOf.set(globalIndex, { mesh: inner, slot, model });
      });
      inner.instanceMatrix.needsUpdate = true;
      inner.instanceColor.needsUpdate = true;
      inner.userData.flat = flatVariant(inner.material);
      inner.userData.lit = inner.material;
      view.add(inner);
      overlays.push(inner);
    }
    // Only tip spots get an overlay. What a container holds is shown by colouring its inner body
    // through `vesselOf`, not by a mesh of its own.
    if (model.category === "tip_spot") overlays.push(buildOverlay(instances, model));
    const entry = meshes[meshes.length - 1];
    entry.overlays = overlays;
    entry.isVessel = isVessel;
    entry.holdsEnclosure = holdsEnclosure;
    entry.enclosedModels = [...enclosedModels];
  }
}

// Where one instanced part stands, so it can be put back after being emptied.
function remember(index, mesh, slot, at) {
  if (!overlayOf.has(index)) overlayOf.set(index, []);
  overlayOf.get(index).push({ mesh, slot, at });
}

function buildOverlay(instances, model) {
  const mesh = new THREE.InstancedMesh(
    CONE,
    new THREE.MeshStandardMaterial({ color: TIP, roughness: 0.55 }),
    instances.length
  );
  mesh.frustumCulled = false;
  for (let slot = 0; slot < instances.length; slot++) mesh.setMatrixAt(slot, ZERO);
  mesh.instanceMatrix.needsUpdate = true;
  mesh.userData.flat = flatVariant(mesh.material);
  mesh.userData.lit = mesh.material;
  view.add(mesh);
  instances.forEach((globalIndex, slot) => tipOf.set(globalIndex, { mesh, slot, model }));
  return mesh;
}

// ---------------------------------------------------------------- live state

// State arrives as a table of the distinct states in the scene, plus which of them each resource
// holds. Empty wells and unused tip spots share a single entry, and anything that has not changed
// since the client was last told is absent.
//
// Addressed by name rather than by scene index: the order instances are emitted in is not stable
// across rebuilds, so an index can mean a different resource in the next scene. A name cannot.
function applyState(payload) {
  const touched = new Set();
  const { states, of } = payload;
  for (const [name, slot] of Object.entries(of ?? {})) {
    const index = world.indexOfName.get(name);
    if (index === undefined) continue;
    // Shared between every resource in the same state, and only ever read.
    stateOf.set(index, states[slot]);
    if (states[slot]?.location) applyLocation(index, states[slot].location);
    // A state carries the whole of what a resource publishes, so a rotation that is not in it is
    // one that has come back to zero - the sender drops the identity to keep the message small.
    // Taking absence as "unchanged" leaves a joint drawn at the last angle it was turned to.
    applyRotation(index, states[slot]?.rotation ?? { x: 0, y: 0, z: 0 });
    refreshOverlays(index, touched);
    applyJoints(index);
  }
  for (const mesh of touched) mesh.instanceMatrix.needsUpdate = true;
  if (selected >= 0 && infoPanel?.isConnected) renderInfoPanel();
  refreshTreeInfo();
  deviceTools.refresh();
}

function refreshOverlays(index, touched) {
  const state = stateOf.get(index);

  if (MOVING_PARTS.has(modelOf(index).category)) {
    const tracked = state?.tracker?.x;
    if (tracked !== undefined && tracked !== null) setArmX(index, tracked);
  }

  const vessel = vesselOf.get(index);
  if (vessel && vessel.model.category === "tip_spot") {
    // Green when a tip is fitted, white when not, as the existing visualizer does. `pending_tip`
    // is the live intent, so a pickup shows the moment it is requested.
    const fitted = state ? !!state.pending_tip : false;
    vessel.mesh.setColorAt(vessel.slot, new THREE.Color(fitted ? TIP : VESSEL_EMPTY));
    if (vessel.mesh.instanceColor) vessel.mesh.instanceColor.needsUpdate = true;
  } else if (vessel && Number.isFinite(vessel.model.max_volume)) {
    const volume = state ? state.pending_volume ?? state.volume ?? 0 : 0;
    const fraction = Math.max(0, Math.min(1, volume / (vessel.model.max_volume || 1)));
    // Empty is white; any liquid at all steps clear of white so a nearly empty well still reads.
    const t = fraction > 0 ? 0.35 + 0.65 * fraction : 0;
    vessel.mesh.setColorAt(
      vessel.slot,
      new THREE.Color(VESSEL_EMPTY).lerp(new THREE.Color(LIQUID), t)
    );
    if (vessel.mesh.instanceColor) vessel.mesh.instanceColor.needsUpdate = true;
  }

  placeParts(index, touched);
}

// A well's rim and its cavity, a carrier's floor, a fitted tip: everything drawn outside the box
// pipeline. Each one follows the resource it belongs to - emptied when that resource is switched
// off, put back where it stands when it is switched on, and carried along when it moves.
function placeParts(index, touched) {
  const visible = isVisible(index);

  for (const part of overlayOf.get(index) ?? []) {
    if (visible) placeInstance(part.mesh, part.slot, world.matrices[index], ...part.at);
    else part.mesh.setMatrixAt(part.slot, ZERO);
    touched.add(part.mesh);
  }

  const tip = tipOf.get(index);
  if (tip) {
    const [sx, sy, sz] = sizeOf(tip.model);
    // `pending_tip` is the live intent; `tip` is what has been committed. The viewer follows
    // intent, so a pickup shows the moment it is requested.
    const mounted = stateOf.get(index)?.pending_tip ?? null;
    if (!mounted || !visible) tip.mesh.setMatrixAt(tip.slot, ZERO);
    else {
      const length = mounted.total_tip_length || sz;
      placeInstance(tip.mesh, tip.slot, world.matrices[index],
        sx * 0.62, sy * 0.62, length, sx / 2, sy / 2, length / 2);
    }
    touched.add(tip.mesh);
  }
}

// ---------------------------------------------------------------- visibility

// Only explicitly hidden resources go in the set. A resource is drawn when neither it nor any
// ancestor is hidden, so "hidden because I was toggled off" stays distinct from "hidden because
// a parent is off".
function isVisible(index) {
  for (let i = index; i >= 0; i = world.parentOf[i]) {
    if (hiddenNames.has(world.names[i])) return false;
  }
  return true;
}

function setHidden(name, hidden) {
  if (hidden) hiddenNames.add(name);
  else hiddenNames.delete(name);
  const root = world.indexOfName.get(name);
  if (root === undefined) return;

  const touched = new Set();
  const walk = (index) => {
    const placement = placementOf[index];
    if (placement) {
      if (isVisible(index)) {
        const [sx, sy, sz] = sizeOf(modelOf(index));
        placeInstance(placement.mesh, placement.slot, world.matrices[index], sx, sy, sz);
      } else {
        placement.mesh.setMatrixAt(placement.slot, ZERO);
      }
      touched.add(placement.mesh);
    }
    const line = edgeOf.get(index);
    if (line) line.visible = isVisible(index);
    // A resource drawn from a file is drawn outside the instanced pipeline, so switching off the
    // box it stood in for leaves the geometry on screen unless it is told too.
    const model = meshRoots.find((r) => r.userData.index === index);
    if (model) model.visible = isVisible(index);
    for (const mark of gridMarks) {
      if (mark.userData.owner === index) mark.visible = isVisible(index);
    }
    // A travelling part is drawn by its own group rather than the instanced pipeline, so it has to
    // be told separately. Anything else drawn outside that pipeline needs the same line.
    const arm = arms.find((a) => a.index === index);
    if (arm) arm.group.visible = isVisible(index);
    refreshOverlays(index, touched);
    for (const child of world.childrenOf[index]) walk(child);
  };
  walk(root);
  for (const mesh of touched) mesh.instanceMatrix.needsUpdate = true;
  refreshTreeVisibility();
}

// ---------------------------------------------------------------- reference points

// PLR's own reference semantics: a resource's origin is its left, front, bottom corner.
//
// `cavity_bottom` is the one reference a resource may be unable to answer: it is the floor of what
// a container holds, standing its base's thickness above the outside of that base, and only a
// container states a thickness. The point still comes back, so x and y read as they always do,
// with `zKnown` false so a caller can say the height is unavailable rather than print the bottom
// of the box as though it were the cavity's.
function referencePoint(index, xRef, yRef, zRef) {
  const model = modelOf(index);
  const [sx, sy, sz] = sizeOf(model);
  const thickness = model.material_z_thickness;
  const x = xRef === "center" ? sx / 2 : xRef === "right" ? sx : 0;
  const y = yRef === "center" ? sy / 2 : yRef === "back" ? sy : 0;
  const z =
    zRef === "center"
      ? sz / 2
      : zRef === "top"
        ? sz
        : zRef === "cavity_bottom"
          ? thickness ?? 0
          : 0;
  const point = new THREE.Vector3(x, y, z).applyMatrix4(world.matrices[index]);
  point.zKnown = zRef !== "cavity_bottom" || typeof thickness === "number";
  return point;
}

function worldBox(index) {
  const [sx, sy, sz] = sizeOf(modelOf(index));
  const box = new THREE.Box3();
  const corner = new THREE.Vector3();
  for (const c of [
    [0, 0, 0], [sx, 0, 0], [0, sy, 0], [0, 0, sz],
    [sx, sy, 0], [sx, 0, sz], [0, sy, sz], [sx, sy, sz],
  ]) {
    corner.set(c[0], c[1], c[2]).applyMatrix4(world.matrices[index]);
    box.expandByPoint(corner);
  }
  return box;
}

// ---------------------------------------------------------------- facility tree

const treeEl = document.getElementById("resource-tree");
const rowOf = new Map();
const expanded = new Set();

function eyeSvg(hidden) {
  return hidden
    ? '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5c2.6 3.3 5.9 5 9 5s6.4-1.7 9-5" stroke-width="2.2"/></svg>'
    : '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3.07 12C5.23 8.2 8.43 6 12 6s6.77 2.2 8.93 6c-2.16 3.8-5.36 6-8.93 6s-6.77-2.2-8.93-6Z" stroke-width="2.2"/><circle cx="12" cy="12" r="3.8" fill="currentColor" stroke="none"/></svg>';
}

function shortName(index) {
  const parent = world.parentOf[index];
  const name = world.names[index];
  if (parent < 0) return name;
  const prefix = world.names[parent] + "_";
  return name.startsWith(prefix) ? name.slice(prefix.length) : name;
}

// What the tree says about a resource beyond its name and type: a carrier counts what it holds by
// kind, a rack how many of its spots are taken, a plate its well count, a container its volume, and
// a site with nothing in it says so. The existing visualizer's tree answers the same questions.
function summaryOf(index) {
  const children = world.childrenOf[index];

  if (!children.length) {
    const state = stateOf.get(index);
    if (state && state.pending_volume !== undefined) return `${fmt(state.pending_volume)} uL`;
    if (state && "pending_tip" in state) return state.pending_tip ? "tip" : "";
    // A vacant site is labelled `<empty>` in place of its name, so a summary would repeat it.
    return "";
  }

  // An occupied holder needs no summary: the row directly beneath it says what is standing there.
  if (HOLDERS.has(modelOf(index).category)) return "";

  const kind = modelOf(children[0]).category;

  if (kind === "tip_spot") {
    const filled = children.filter((c) => !!stateOf.get(c)?.pending_tip).length;
    return `${filled}/${children.length} tips`;
  }
  if (kind === "well") return `${children.length} wells`;

  // Look through holders to what stands in them, so the count names the contents. With every site
  // empty there is nothing to name, and the useful fact is how many positions there are.
  if (HOLDERS.has(kind)) {
    const held = children.map((c) => world.childrenOf[c][0]).filter((c) => c !== undefined);
    if (!held.length) return `${children.length} sites`;
    return `${held.length} ${countable(held)}`;
  }

  // Nothing else is counted. A deck holding carriers, a waste block and an arm has no single
  // number worth quoting, and a device holding one deck has none either. The existing visualizer
  // is summarised the same way: carriers, racks and plates, and nothing above them.
  return "";
}

// How far apart two sites may stand in y and still count as the same row, in mm.
const SAME_ROW = 0.5;

// The sites on a carrier, in the order a reader takes them and numbered the way PyLabRobot numbers
// them. Holders arrive in whatever order they were assigned; what a person reads is the deck, so
// they are listed back to front, and left to right within a row. The numbers then run the other
// way down a column, because site 0 is the front one - and straight along a single row across.
//
// Null for anything that is not a carrier: only a resource whose children are all holders has
// sites at all.
function siteOrder(index) {
  const children = index >= 0 ? world.childrenOf[index] : [];
  if (children.length < 2 || !children.every((c) => HOLDERS.has(modelOf(c).category))) return null;
  const at = (i) => world.matrices[i].elements;
  const sorted = [...children].sort((a, b) => {
    const dy = at(b)[13] - at(a)[13];
    return Math.abs(dy) > SAME_ROW ? dy : at(a)[12] - at(b)[12];
  });
  const oneRow = sorted.every((c) => Math.abs(at(c)[13] - at(sorted[0])[13]) <= SAME_ROW);
  const number = new Map();
  sorted.forEach((c, i) => number.set(c, oneRow ? i : sorted.length - 1 - i));
  return { sorted, number };
}

function buildTree() {
  treeEl.textContent = "";
  rowOf.clear();
  expanded.clear();
  for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) addRow(i, 0, null);
  showToDepth(Number(depthInput.value) || 1);
}

function addRow(index, depth, before) {
  const model = modelOf(index);
  const children = treeChildren(index);

  const row = document.createElement("div");
  row.className = "tree-node-row";
  row.style.paddingLeft = `${8 + depth * 16}px`;
  row.dataset.index = index;

  const arrow = document.createElement("span");
  arrow.className = "tree-node-arrow" + (children.length ? " has-children" : "");
  arrow.textContent = children.length ? "▶" : "";
  row.appendChild(arrow);

  const dot = document.createElement("span");
  dot.className = "tree-node-dot";
  dot.style.backgroundColor = hexOf(colorFor(model));
  row.appendChild(dot);

  // A holder is a numbered position on its carrier, so it is labelled by that number rather than by
  // a name nobody chose - and so is whatever stands in it, which is the row a reader is actually
  // looking for when they want to know which position a plate is at.
  const holder = HOLDERS.has(model.category);
  const parent = world.parentOf[index];
  const seat = holder ? index : parent >= 0 && HOLDERS.has(modelOf(parent).category) ? parent : -1;
  const number = seat < 0 ? undefined : siteOrder(world.parentOf[seat])?.number.get(seat);
  if (number !== undefined) {
    const site = document.createElement("span");
    site.className = "tree-node-site";
    site.textContent = String(number);
    row.appendChild(site);
  }

  const name = document.createElement("span");
  name.className = "tree-node-name";
  // An empty site has nothing worth naming, and saying so is the point of showing it at all.
  const vacant = holder && !children.length;
  if (vacant) name.classList.add("tree-node-vacant");
  name.textContent = vacant ? "<empty>" : shortName(index);
  name.title = `${world.names[index]} (${model.type})`;
  row.appendChild(name);

  const type = document.createElement("span");
  type.className = "tree-node-type";
  type.textContent = vacant ? "" : model.type;
  row.appendChild(type);

  const info = document.createElement("span");
  info.className = "tree-node-info";
  info.textContent = summaryOf(index);
  row.appendChild(info);

  const eye = document.createElement("button");
  eye.className = "tree-eye-btn";
  eye.title = "Show or hide";
  eye.innerHTML = eyeSvg(hiddenNames.has(world.names[index]));
  eye.addEventListener("click", (e) => {
    e.stopPropagation();
    setHidden(world.names[index], !hiddenNames.has(world.names[index]));
  });
  row.appendChild(eye);

  row.addEventListener("mouseenter", () => showHoverBox(index));
  row.addEventListener("mouseleave", () => (hoverBox.visible = false));
  // Same gesture as the viewport: click selects, double click inspects. The existing visualizer
  // opens the panel on a single click here, which made the tree and the scene behave differently
  // for the same intent.
  row.addEventListener("click", (e) => {
    if (e.offsetX < 20 && children.length) toggle(index, !expanded.has(index));
    else select(index, false);
  });
  row.addEventListener("dblclick", (e) => {
    if (e.offsetX < 20 && children.length) return;
    select(index, true);
  });

  treeEl.insertBefore(row, before ?? null);
  rowOf.set(index, { row, depth, arrow, info, eye });
  applyRowVisibility(index);
  return row;
}

// Children only enter the DOM when a node is opened, so a deck of thousands of wells does not
// build thousands of rows to show four carriers.
function toggle(index, open) {
  const entry = rowOf.get(index);
  if (!entry || !treeChildren(index).length || open === expanded.has(index)) return;

  if (open) {
    expanded.add(index);
    entry.arrow.textContent = "▼";
    const before = entry.row.nextSibling;
    const order = siteOrder(index)?.sorted ?? treeChildren(index);
    for (const child of order) addRow(child, entry.depth + 1, before);
  } else {
    expanded.delete(index);
    entry.arrow.textContent = "▶";
    const drop = (i) => {
      for (const child of world.childrenOf[i]) {
        drop(child);
        const childEntry = rowOf.get(child);
        if (childEntry) {
          childEntry.row.remove();
          rowOf.delete(child);
          expanded.delete(child);
        }
      }
    };
    drop(index);
  }
}

// What the tree lists below a row. The positions inside a container are left out: a plate already
// says how many wells it has, and the rows would be a wall to scroll past. Nothing about the
// viewport changes - this decides the panel and nothing else.
function treeChildren(index) {
  return world.childrenOf[index].filter((c) => !TREE_HIDDEN.has(modelOf(c).category));
}

// Whether opening this row would open a grid of positions rather than a level of the deck. Those
// rows exist - a mounting shaft is a real part - but a depth should not spend itself on ninety-six
// of them, so they open when they are asked for by name.
function holdsContentsOnly(index) {
  const children = treeChildren(index);
  return children.length > 0 && children.every((c) => CONTENTS.has(modelOf(c).category));
}

function showToDepth(maxDepth) {
  const walk = (index, depth) => {
    if (depth < maxDepth && !holdsContentsOnly(index)) {
      toggle(index, true);
      for (const child of treeChildren(index)) walk(child, depth + 1);
    } else {
      toggle(index, false);
    }
  };
  for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) walk(i, 0);
}

function expandAll(open) {
  if (!open) {
    for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) toggle(i, false);
    return;
  }
  const walk = (index) => {
    toggle(index, true);
    for (const child of treeChildren(index)) walk(child);
  };
  for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) walk(i);
}

function applyRowVisibility(index) {
  const entry = rowOf.get(index);
  if (!entry) return;
  const own = hiddenNames.has(world.names[index]);
  entry.row.classList.toggle("resource-hidden", !isVisible(index));
  entry.eye.innerHTML = eyeSvg(own);
  entry.eye.classList.toggle("is-hidden", own);
}

function refreshTreeVisibility() {
  for (const [index] of rowOf) applyRowVisibility(index);
}

function refreshTreeInfo() {
  for (const [index, entry] of rowOf) entry.info.textContent = summaryOf(index);
}

function revealAndHighlight(index) {
  // A position inside a container has no row of its own, so the row to land on is the container
  // that names it - which is where a reader would look for it anyway.
  let at = index;
  while (at >= 0 && TREE_HIDDEN.has(modelOf(at).category)) at = world.parentOf[at];
  if (at < 0) at = index;
  const chain = [];
  for (let i = world.parentOf[at]; i >= 0; i = world.parentOf[i]) chain.unshift(i);
  for (const ancestor of chain) toggle(ancestor, true);
  for (const [, entry] of rowOf) entry.row.classList.remove("selected");
  const entry = rowOf.get(at);
  if (entry) {
    entry.row.classList.add("selected");
    entry.row.scrollIntoView({ block: "nearest" });
  }
}

// ---------------------------------------------------------------- info panel

let infoPanel = null;

// Values go through innerHTML, and a resource name is user data. Escape it, or a model field
// holding `<resource>` disappears into the markup.
function ensureInfoPanel() {
  if (infoPanel?.isConnected) return infoPanel;
  infoPanel = document.createElement("div");
  infoPanel.className = "uml-panel";
  document.querySelector("main").appendChild(infoPanel);
  return infoPanel;
}

function refreshPlacement(index) {
  if (selected !== index || !infoPanel?.isConnected) return;
  const o = index * 6;
  const xf = world.local;
  const m = world.matrices[index].elements;
  const local = infoPanel.querySelector('[data-live="location"]');
  const global = infoPanel.querySelector('[data-live="world"]');
  if (local) local.textContent = tuple(xf[o], xf[o + 1], xf[o + 2], "mm");
  if (global) global.textContent = tuple(m[12], m[13], m[14], "mm");
}

function closeInfoPanel() {
  selected = -1;
  selectionBox.visible = false;
  for (const [, entry] of rowOf) entry.row.classList.remove("selected");
  hideInfoPanel();
}

function hideInfoPanel() {
  if (infoPanel) infoPanel.remove();
  infoPanel = null;
}

// Units for the fields that have them. A number without its unit is not an answer.
// Shown by the panel's own sections, so they must not appear again under Specifics.
const HANDLED = new Set([
  "type", "category", "methods", "model",
  "size_x", "size_y", "size_z",
  "max_volume", "volume", "pending_volume",
  "height_volume_data", "ordering",
]);

// A field that records how a resource was constructed rather than what it is now. These are kept,
// not hidden, but put under a heading that says what they are: a deck reports `with_trash: false`
// while holding a trash, and a reader has to be able to see that without being misled by it.
const isConstruction = (key) => key.startsWith("with_") || key === "core_grippers";

// Per-category panel contributions. This is the seam a package that defines a resource would
// write into; everything works without an entry, which is what makes it a default rather than a
// registry every new type must join.
const PANELS = {
  deck: { note: "Construction flags describe how the deck was built, not what it now holds." },
};

function renderInfoPanel() {
  if (selected < 0) return hideInfoPanel();
  const panel = ensureInfoPanel();
  const index = selected;
  const model = modelOf(index);
  const contributed = PANELS[model.category] ?? {};
  const m = world.matrices[index].elements;
  const o = index * 6;
  const xf = world.local;
  const state = stateOf.get(index);

  const identity = [["name", escapeHtml(world.names[index])], ["type", escapeHtml(model.type)]];
  if (model.model) identity.push(["model", escapeHtml(String(model.model))]);
  identity.push(["category", escapeHtml(model.category ?? "uncategorised")]);

  const placement = [
    ["location", `<span data-live="location">${tuple(xf[o], xf[o + 1], xf[o + 2], "mm")}</span>`],
    ["world", `<span data-live="world">${tuple(m[12], m[13], m[14], "mm")}</span>`],
  ];
  if (xf[o + 3] || xf[o + 4] || xf[o + 5]) {
    placement.push(["rotation", tuple(xf[o + 3], xf[o + 4], xf[o + 5], "deg")]);
  }
  placement.push(["parent", world.parentOf[index] >= 0 ? escapeHtml(world.names[world.parentOf[index]]) : "none"]);
  placement.push(["children", String(world.childrenOf[index].length)]);

  const [sx, sy, sz] = sizeOf(model);
  const geometry = [["size", `${fmt(sx)}${NBSP}&#215;${NBSP}${fmt(sy)}${NBSP}&#215;${NBSP}${fmt(sz)}${NBSP}mm`]];
  if (model.ordering) geometry.push(["items", String(Object.keys(model.ordering).length)]);

  const contents = [];
  if (model.max_volume !== undefined) {
    const volume = state ? state.pending_volume ?? state.volume ?? 0 : 0;
    contents.push(["volume", `${fmt(volume)}${NBSP}/${NBSP}${fmt(model.max_volume)}${NBSP}uL`]);
  }
  if (state && "pending_tip" in state) {
    contents.push(["tip", state.pending_tip ? "fitted" : "none"]);
    if (state.pending_tip) {
      for (const key of ["total_tip_length", "nominal_volume", "has_filter"]) {
        if (state.pending_tip[key] !== undefined) contents.push([key, withUnit(key, state.pending_tip[key])]);
      }
    }
  }

  const specifics = [];
  const construction = [];
  for (const [key, value] of Object.entries(model)) {
    if (HANDLED.has(key)) continue;
    (isConstruction(key) ? construction : specifics).push([key, withUnit(key, value)]);
  }

  const tracker = state
    ? Object.entries(state)
        .filter(([k]) => !["rotation", "pending_volume", "volume", "tip", "pending_tip", "tip_state"].includes(k))
        .map(([k, v]) => [k, withUnit(k, v)])
    : [];

  const methods = (model.methods ?? [])
    .map((signature) => `<div class="uml-method">${escapeHtml(signature)}</div>`)
    .join("");

  panel.innerHTML =
    `<button class="uml-close-btn" title="Close">&times;</button>` +
    `<div class="uml-header">` +
    `<div class="uml-header-name">${escapeHtml(world.names[index])}</div>` +
    `<div class="uml-header-type">${escapeHtml(model.type)} &middot; ${escapeHtml(model.category ?? "uncategorised")}</div>` +
    `</div>` +
    section("Identity", identity) +
    section("Placement", placement) +
    section("Geometry", geometry) +
    section("Contents", contents) +
    section("Specifics", specifics) +
    section("Tracker state", tracker) +
    section("Construction", construction, contributed.note) +
    // The one section that is a list rather than a fact about the part. Sixty signatures push
    // everything a reader came for off the top of the panel, so it opens shut and says how many are
    // behind it.
    (methods
      ? `<div class="uml-separator"></div><div class="uml-section">` +
        `<details class="uml-methods-block"><summary class="uml-section-title">` +
        `Methods <span class="uml-count">${model.methods.length}</span></summary>` +
        `<div class="uml-methods">${methods}</div></details></div>`
      : "");

  panel.querySelector(".uml-close-btn").addEventListener("click", closeInfoPanel);
}

// Selecting and inspecting are separate, as they are in the existing visualizer: a click in the
// viewport selects, a double click opens the panel, and a click in the tree does both. An already
// open panel follows the selection rather than being left showing something else.
function select(index, openPanel = true) {
  selected = index;
  selectionBox.box.copy(worldBox(index));
  selectionBox.visible = true;
  revealAndHighlight(index);
  if (openPanel || infoPanel?.isConnected) renderInfoPanel();
}

let hoveredRow = null;

function showHoverBox(index) {
  hoverBox.box.copy(worldBox(index));
  hoverBox.visible = true;
}

// Hovering a resource in the viewport marks its row in the tree, the mirror of hovering a row
// marking the resource. The existing visualizer does both, and only having one of them is what
// makes a tree feel disconnected from the scene.
function markTreeRow(index) {
  const entry = index === null ? null : rowOf.get(index);
  if (entry === hoveredRow) return;
  hoveredRow?.row.classList.remove("canvas-hover");
  hoveredRow = entry ?? null;
  hoveredRow?.row.classList.add("canvas-hover");
}

function clearHover() {
  hoverBox.visible = false;
  markTreeRow(null);
}


// A line at the X the device positions the arm by. Where that sits on the arm is the whole
// difference between a dual-rail arm, positioned by its centre, and a single-rail one, positioned
// by its right edge - so drawing the reported X against the arm shows which it is without the
// viewer needing to know anything about rail types.


// ---------------------------------------------------------------- picking

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const readout = document.getElementById("hover-readout");

function pick(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const candidates = meshes.map((m) => m.mesh);
  const hits = raycaster.intersectObjects(candidates, false);
  for (const hit of hits) {
    const instances = hit.object.userData.instances;
    if (instances && hit.instanceId !== undefined) {
      const index = instances[hit.instanceId];
      if (!isVisible(index)) continue;
      // Enclosures are translucent, so clicking through one to its contents is the useful
      // behaviour; take an enclosure only when nothing solid lies behind it. A part that always
      // carries something - a pipetting channel and its shaft - is taken where it is clicked.
      const takesClick = world.childrenOf[index].length === 0 || PICKABLE_PARTS.has(modelOf(index).category);
      if (takesClick || hits.length === 1) return { index };
    }
  }
  const first = hits.find((h) => h.object.userData.instances && h.instanceId !== undefined);
  return first ? { index: first.object.userData.instances[first.instanceId] } : null;
}

const coords = initCoords({ getWorld: () => world, referencePoint, escapeHtml });
// What each device carries, offered from the navbar. It reads the tree rather than being told, so
// there is nothing to keep in step: a tip picked up is a resource assigned, and the panel that
// draws tips is looking at the same tree the viewport is.
const deviceTools = initDeviceTools({
  getWorld: () => world,
  modelOf,
  onSelect: (index) => {
    revealAndHighlight(index);
    select(index, true);
  },
});
const { coordinateLabel, recordMeasurement, populateWrtDropdown, endpoints: deltaEndpoints } = coords;

// ---------------------------------------------------------------- delta lines

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
let deltaAnnotation = null;

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
        color, linewidth, worldUnits: false, transparent: true, opacity, depthTest: false,
      });
      edgeMaterials.add(material);
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
      new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthTest: false })
    );
    label.frustumCulled = false;
    label.renderOrder = OVERLAY_ORDER + 42;
    group.add(label);
    return { axis, color: `#${color.toString(16).padStart(6, "0")}`, lines, canvas, texture, label, text: null };
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

function updateDeltaLabels() {
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

// ---------------------------------------------------------------- camera

const VIEWS = {
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

function setProjection(kind) {
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
    kind === "orthographic" ? "Orthographic: switch to perspective" : "Perspective: switch to orthographic";
}

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
  for (const c of [box.min, box.max]) void c;
  for (let i = 0; i < 8; i++) {
    corner.set(
      i & 1 ? box.max.x : box.min.x,
      i & 2 ? box.max.y : box.min.y,
      i & 4 ? box.max.z : box.min.z
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

function frameBox(box, direction) {
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

const frame = (direction) => frameBox(sceneBounds(), direction ?? VIEWS.iso);

function dolly(factor) {
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
const startViewName = new URLSearchParams(location.search).get("view") ?? "top";
const startView = VIEWS[startViewName] ?? VIEWS.top;

function goToStartView() {
  setProjection(startViewName === "iso" ? "perspective" : "orthographic");
  frame(startView);
}

// A small handle on the viewer, so a notebook cell or a link can drive it.
// The fourth and last way in: a call from outside the page, which tests and benchmarks use to drive
// the same paths a message takes. Everything that changes something is wrapped as a group, so a
// method added to that group is covered without anyone remembering to cover it.
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
    ])
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
  stats: () => stats,
  resources: () => world?.names ?? [],
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
  models: () =>
    meshRoots.map((root) => {
      const drawn = [];
      root.traverse((o) => {
        if (o.isMesh) {
          drawn.push({
            visible: o.visible && o.material.visible,
            order: o.renderOrder,
            depthTest: o.material.depthTest,
            transparent: o.material.transparent,
            opacity: o.material.opacity,
          });
        }
      });
      return { name: world?.names[root.userData.index], parts: drawn };
    }),
  detail: () =>
    meshes.map((e) => ({
      type: e.model.type,
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
      out.at = gridLabels[0].getWorldPosition(new THREE.Vector3()).toArray().map((v) => +v.toFixed(1));
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
  sceneObjects: () => {
    let n = 0;
    view.traverse(() => n++);
    return n;
  },
};

// ---------------------------------------------------------------- overlays

const scaleLine = document.getElementById("scale-bar-line");
const scaleLabel = document.getElementById("scale-bar-label");

// Under perspective there is no single scale, so the bar is quoted at the orbit target's depth.
// Under orthographic there is one scale for the whole viewport, and the bar is exact.
// About how long the bar should be, in pixels. Long enough to read a number against, short enough
// to leave the corner it sits in.
const SCALE_BAR_PX = 120;

function updateScaleBar() {
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;
  // Counted in floor cells rather than rounded on its own, so the bar always spans a whole number
  // of the squares it is drawn over. Both used to pick a nice number from the same 1-2-5 ladder
  // but for different pixel targets, which agree only sometimes - and a scale that disagrees with
  // the grid beneath it is worse than having no grid to check it against.
  const cell = gridState?.cell;
  const nice = cell
    ? cell * ([1, 2, 5].find((n) => (n * cell) / perPixel >= SCALE_BAR_PX) ?? 10)
    : niceNumber(perPixel * SCALE_BAR_PX);
  scaleLine.style.width = `${Math.round(nice / perPixel)}px`;
  scaleLabel.textContent = nice >= 1000 ? `${nice / 1000} m` : `${nice} mm`;
}

const statsEl = document.getElementById("stats-panel");
let frames = 0;
let lastSample = performance.now();

function updateStats() {
  frames++;
  const now = performance.now();
  if (now - lastSample < 500) return;
  const fps = Math.round((frames * 1000) / (now - lastSample));
  frames = 0;
  lastSample = now;
  drawStats(String(fps) + " fps");
}

// Quoting a frame rate while nothing is being drawn would be a lie, so an idle viewer says so. This
// runs on a timer rather than in the loop, because the loop is exactly what has stopped.
function reportIdle() {
  if (performance.now() - lastRenderAt > 400) drawStats("idle");
}

let lastDrawCalls = 0;

function drawStats(rate) {
  // three zeroes its counters between frames, so an idle viewer would otherwise report no draws at
  // all. What the scene costs when it is drawn does not change just because it is not being drawn.
  const info = renderer.info.render;
  const calls = info.drawCalls ?? info.calls ?? 0;
  if (calls > 0) lastDrawCalls = calls;
  const text =
    `instances  <b>${(stats.instances ?? 0).toLocaleString()}</b>   ` +
    `models <b>${stats.models ?? 0}</b>   ` +
    `draws <b>${lastDrawCalls}</b>   ` +
    `${renderer.backend?.isWebGPUBackend ? "WebGPU" : "WebGL2"}   ` +
    `<b>${rate}</b>\n` +
    `tree JSON ${((stats.legacy_bytes ?? 0) / 1024).toFixed(1)} kB  ` +
    `→ this scene <b>${((stats.scene_bytes ?? 0) / 1024).toFixed(1)} kB</b> (${stats.ratio ?? 0}×)`;
  // Writing the same markup back forces layout and paint for nothing, twice a second, forever.
  if (text !== statsEl.innerHTML) statsEl.innerHTML = text;
}

// ---------------------------------------------------------------- interaction

// Hover is answered once a frame at most, and not at all while a button is down.
//
// A pointer crossing the canvas fires far faster than frames are drawn, and every one of those
// events used to raycast the whole scene to produce a readout that had not changed. Measured, it
// cost more than panning the camera did - 12.2% of a profile against 4.5% - and none of it was our
// own code: it was three walking every instance of every model. Coalescing to a frame throws away
// the events nobody could have seen the result of. A pointer with a button down is driving the
// camera rather than pointing at anything, so there is nothing to pick.
let hoverAt = null;

// Answered by the loop, at the start of the frame the pointer's own input asked for. Answering it
// in a callback of its own instead put it a frame behind: the loop registers its callback first,
// because the input that starts it is handled in the capture phase, so the hover box and the delta
// lines were drawn one frame late and the last hover before the pointer stopped never drew at all.
function answerHover() {
  const at = hoverAt;
  hoverAt = null;
  if (at !== null && world) showHoverFor(at);
}

function showHoverFor(event) {
  const hit = pick(event);
  if (!hit) {
    readout.style.display = "none";
    clearHover();
    clearDeltaLines();
    return;
  }
  readout.style.display = "block";
  const rect = viewportEl.getBoundingClientRect();
  readout.style.left = `${event.clientX - rect.left + 14}px`;
  readout.style.top = `${event.clientY - rect.top + 14}px`;
  showHoverBox(hit.index);
  markTreeRow(hit.index);
  drawDeltaLines(hit.index);
  readout.textContent =
    activeTool === "coords" ? coordinateLabel(hit.index) : `${world.names[hit.index]}\n${modelOf(hit.index).type}`;
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
  if (performance.now() - panelOpenedAt > PANEL_GUARD_MS) closeInfoPanel();
});

renderer.domElement.addEventListener("dblclick", (event) => {
  if (!world || activeTool === "coords") return;
  const hit = pick(event);
  if (!hit || hit.index === undefined) return;
  if (selected === hit.index && infoPanel?.isConnected) {
    closeInfoPanel();
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

function refreshToolUI() {
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

toolButtons.cursor.addEventListener("click", () => setTool("cursor"));
toolButtons.coords.addEventListener("click", () => setTool("coords"));
toolButtons.gif.addEventListener("click", () => {
  openPanel = openPanel === "gif" ? (activeTool === "coords" ? "coords" : null) : "gif";
  refreshToolUI();
});

// view presets and viewport furniture
// The axis presets live on the view helper now: click an axis there and the camera animates onto
// it. What the helper cannot do is choose a projection, so that button stays.
const projectionButton = document.getElementById("view-projection");
projectionButton.addEventListener("click", () =>
  setProjection(projection === "orthographic" ? "perspective" : "orthographic")
);
const homeButton = document.getElementById("home-button");
homeButton.addEventListener("click", () => {
  goToStartView();
  // A flash while the camera moves, so the button that did it is the thing you were last looking
  // at. Long enough to register, short enough not to linger over a view that has already settled.
  homeButton.classList.add("clicked");
  setTimeout(() => homeButton.classList.remove("clicked"), 400);
});
document.getElementById("zoom-in-btn").addEventListener("click", () => dolly(0.8));
document.getElementById("zoom-out-btn").addEventListener("click", () => dolly(1.25));

// panel toggles
const leftRail = document.getElementById("toolbar-left");
const sidepanel = document.getElementById("sidepanel");
document.getElementById("toolbar-left-toggle").addEventListener("click", () => {
  leftRail.classList.toggle("collapsed");
  resize();
});
document.getElementById("toolbar-right-toggle").addEventListener("click", () => {
  sidepanel.classList.toggle("collapsed");
  resize();
});

// tree actions
const depthInput = input("tree-depth-input");
let allExpanded = false;
document.getElementById("toggle-expand-btn").addEventListener("click", () => {
  allExpanded = !allExpanded;
  expandAll(allExpanded);
});
document.getElementById("collapse-all-btn").addEventListener("click", () =>
  showToDepth(Number(depthInput.value) || 0)
);
depthInput.addEventListener("change", () => showToDepth(Number(depthInput.value) || 0));

// search
const searchView = document.getElementById("search-view");
const searchInput = input("search-input");
const searchResults = document.getElementById("search-results");
const treeButton = document.getElementById("toolbar-tree-btn");
const searchButton = document.getElementById("toolbar-search-btn");

function showPane(which) {
  const searching = which === "search";
  treeEl.style.display = searching ? "none" : "block";
  query(".sidepanel-header").style.display = searching ? "none" : "flex";
  searchView.style.display = searching ? "flex" : "none";
  treeButton.classList.toggle("active", !searching);
  searchButton.classList.toggle("active", searching);
  if (searching) searchInput.focus();
}

// Each rail button is a toggle, as the existing visualizer's are: it opens the panel on its own
// pane, switches panes when the other one is showing, and closes the panel when its own pane
// already is. Closing is a class, and the stylesheet's width beats the resize handle's inline one,
// so a panel dragged wider comes back the width it was left at.
function pickPane(which, button) {
  const showing = !sidepanel.classList.contains("collapsed") && button.classList.contains("active");
  sidepanel.classList.toggle("collapsed", showing);
  if (showing) button.classList.remove("active");
  else showPane(which);
  resize();
}

treeButton.addEventListener("click", () => pickPane("tree", treeButton));
searchButton.addEventListener("click", () => pickPane("search", searchButton));

function runSearch() {
  if (!world) return;
  const query = searchInput.value.trim().toLowerCase();
  const includeWells = input("search-include-wells").checked;
  const includeTips = input("search-include-tips").checked;
  const includeSites = input("search-include-sites").checked;
  searchResults.textContent = "";
  if (!query) {
    searchResults.innerHTML = '<div class="search-empty">Type to search.</div>';
    return;
  }
  const hits = [];
  for (let i = 0; i < world.names.length && hits.length < 300; i++) {
    const category = modelOf(i).category;
    if (category === "well" && !includeWells) continue;
    if (category === "tip_spot" && !includeTips) continue;
    if ((category === "resource_holder" || category === "plate_holder") && !includeSites) continue;
    if (world.names[i].toLowerCase().includes(query)) hits.push(i);
  }
  if (!hits.length) {
    searchResults.innerHTML = '<div class="search-empty">No resource matches.</div>';
    return;
  }
  for (const index of hits) {
    const row = document.createElement("div");
    row.className = "search-result";
    row.innerHTML =
      `<span class="tree-node-dot" style="background:${hexOf(colorFor(modelOf(index)))}"></span>` +
      `<span class="sr-name">${escapeHtml(world.names[index])}</span>` +
      `<span class="sr-type">${escapeHtml(modelOf(index).type)}</span>`;
    row.addEventListener("mouseenter", () => showHoverBox(index));
    row.addEventListener("mouseleave", () => (hoverBox.visible = false));
    row.addEventListener("click", () => {
      showPane("tree");
      select(index);
      frameBox(worldBox(index), VIEWS.iso);
    });
    searchResults.appendChild(row);
  }
}

searchInput.addEventListener("input", runSearch);
for (const id of ["search-include-wells", "search-include-tips", "search-include-sites"]) {
  document.getElementById(id).addEventListener("change", runSearch);
}

// sidepanel resize
const resizeHandle = document.getElementById("sidepanel-resize-handle");
let resizingFrom = null;
resizeHandle.addEventListener("pointerdown", (e) => {
  resizingFrom = { x: e.clientX, width: sidepanel.offsetWidth };
  resizeHandle.setPointerCapture(e.pointerId);
});
resizeHandle.addEventListener("pointermove", (e) => {
  if (!resizingFrom) return;
  const width = Math.max(150, Math.min(window.innerWidth * 0.6, resizingFrom.width - (e.clientX - resizingFrom.x)));
  sidepanel.style.width = `${width}px`;
  resize();
});
resizeHandle.addEventListener("pointerup", () => (resizingFrom = null));

// ---------------------------------------------------------------- transport

const statusDot = document.getElementById("status-indicator");
const statusLabel = document.getElementById("status-label");

// The status is only ever as fresh as the last time this tab ran. A backgrounded tab gets frozen,
// so neither the close handler nor the reconnect timer fires, and it goes on painting whatever it
// last said. Keep a handle on the socket and re-read its real state whenever the tab comes back.
let socket = null;

function showStatus(connected) {
  for (const el of [statusDot, statusLabel]) {
    el.classList.toggle("connected", connected);
    el.classList.toggle("disconnected", !connected);
  }
  statusLabel.textContent = connected ? "Connected" : "Disconnected";
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  const live = socket && socket.readyState === WebSocket.OPEN;
  showStatus(!!live);
  if (!live) connect();
});

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  socket = new WebSocket(`ws://${location.hostname}:${window.WS_PORT}`);
  framed = false;
  socket.onopen = () => showStatus(true);
  socket.onclose = () => {
    showStatus(false);
    setTimeout(connect, 1500);
  };
  socket.onmessage = (event) => {
    const { event: kind, data } = JSON.parse(event.data);
    // Everything the server says changes what is on screen: the scene it draws, or the state it
    // draws it in. There is no message that only touches the panels around the viewport.
    invalidate();
    if (kind === "scene") {
      const _tScene = performance.now();
      stats = data.stats ?? {};
      setWorld(buildWorld(data));
      timings.decodeMs = performance.now() - _tScene;
      const _tBuild = performance.now();
      buildMeshes();
      floorZ = sceneBounds().min.z;
      gridState = null;
      planView = null;
      buildGridMarks();
      buildArms();
      buildReferenceMarks();
      buildDeclaredMeshes();
      buildOrigin();
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
      timings.readyMs = performance.now() - _t0;
      populateWrtDropdown();
      selected = -1;
      selectionBox.visible = false;
      hideInfoPanel();
      stateOf.clear();
      resize();
      // Only the first scene of a connection frames the camera. A tree that is assembled while the
      // viewer watches rebuilds on every assignment, and framing each one would throw the view away
      // as you build. The camera holds no scene indices, so it survives a rebuild unchanged.
      if (!framed) {
        goToStartView();
        framed = true;
      }
    } else if (kind === "state" && world) {
      applyState(data);
    }
  };
}

statusDot.addEventListener("click", connect);

const gif = initGif({ renderer, view, camera });

// ---------------------------------------------------------------- loop

let sizedTo = { w: 0, h: 0 };

function resize() {
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
  for (const material of edgeMaterials) material.resolution?.set(w, Math.max(h, 1));
  // Kept apart from the edge materials: that set is emptied and refilled with every scene, and a
  // fat line sized against a stale resolution is drawn at the wrong width or not at all.
  for (const material of floorMaterials) material.resolution?.set(w, Math.max(h, 1));
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

// The tree, the panels and the toolbars all change the scene through their own handlers. Rather
// than raise the flag in each one and miss the next one added, any input earns a frame: the cost is
// one redraw per interaction, and it stops the moment the pointer does.
for (const kind of ["pointerdown", "pointermove", "pointerup", "wheel", "keydown", "click"]) {
  document.addEventListener(kind, invalidate, { passive: true, capture: true });
}
setInterval(reportIdle, 500);

function drawFrame() {
  const delta = clock.getDelta();

  // At most one hover answered per frame, however many times the pointer moved in between: a
  // pointer crosses the canvas far faster than frames are drawn, and each answer raycasts the whole
  // scene to produce a readout nobody could have seen the previous version of.
  if (hoverAt !== null) answerHover();

  // Three things keep drawing on their own account: the helper's snap animation, an arm gliding to
  // a new position, and a recording that needs a frame to capture. `controls.update` reports
  // whether damping is still carrying the camera.
  let moving = false;
  if (viewHelper?.animating) {
    viewHelper.update(delta);
    // Every direction the helper can snap to is axis-aligned, so the view it lands on is a plan or
    // an elevation. Switch once the animation is done, not during it, since changing projection
    // rebuilds the helper.
    if (!viewHelper.animating) setProjection("orthographic");
    moving = true;
  }
  if (deltaAnnotation?.group.visible) updateDeltaLabels();
  if (updateArms(delta)) moving = true;
  if (controls.update()) moving = true;
  if (gif.isRecording()) moving = true;

  if (!renderPending && !moving) {
    looping = false;
    renderer.setAnimationLoop(null);
    return;
  }
  renderPending = false;
  lastRenderAt = performance.now();

  renderer.render(view, camera);
  if (viewHelper) {
    // The helper renders a second pass into a corner of the same canvas. Without turning auto-clear
    // off it clears the colour buffer for that corner first, leaving a blank patch over the scene.
    renderer.autoClear = false;
    viewHelper.render(renderer);
    renderer.autoClear = true;
  }
  gif.tick();
  updateGrid();
  updateDetail();
  updateEdgeMode();
  updateOrigin();
  updateScaleBar();
  updateStats();
}

buildViewHelper();
refreshToolUI();
showPane("tree");
resize();
connect();
