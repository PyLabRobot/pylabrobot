// What is drawn for the scene, shared by every builder: the ownership registry, the instanced
// meshes and models by resource, the state each resource last published, and the rules about
// what a model is that more than one builder needs.

import * as THREE from "three";

import { MOVING_PARTS } from "./constants.js";
import { modelOf, sizeOf, treeDepth, world } from "./world.js";

// What each owner made - geometries, materials, textures, instance buffers - so it can let them go:
// a builder that starts afresh each scene, or one drawing that outlives its scene. Nothing shared.

const ownedBy = new Map();

export function own(owner, ...things) {
  if (!ownedBy.has(owner)) ownedBy.set(owner, []);
  ownedBy.get(owner).push(...things);
}

/** Release what an owner made. The renderer keeps a draw's state until its material is disposed. */
export function disposeOwned(owner) {
  for (const thing of ownedBy.get(owner) ?? []) thing.dispose();
  ownedBy.delete(owner);
}

export let meshes = [];

export let placementOf = []; // instance index -> { mesh, slot }

export let vesselOf = new Map(); // index -> the inner body whose colour tracks what is in it

// index -> the instanced parts drawn for it outside the box pipeline, and where each one stands.
// Switching a resource off empties its box; these have to be emptied with it, or hiding a plate
// leaves ninety-six cavities and their walls floating where the plate was.
let overlayOf = new Map();

export let edgeOf = new Map();

// The instances whose model arrived as a file. Their box is not drawn at all and its border is
// only just there, and both have to be decided from here rather than at the moment the file
// landed: everything that says what is drawn runs again on every view change, so a one-off switch
// would be undone by the next orbit past an axis.
export let drawnFromFile = new Set();

// The models drawn from files: the roots that ride a moving part, and the instanced meshes.
export let meshRoots = [];

export let modelMeshes = [];

/** Forget every box drawn for a scene, ahead of drawing the next one. */
export function clearDrawn() {
  meshes = [];
  placementOf = new Array(world.names.length);
  vesselOf = new Map();
  overlayOf = new Map();
  edgeOf = new Map();
  drawnFromFile = new Set();
}

/** Forget every model drawn from a file, ahead of drawing them for the next scene. */
export function clearModels() {
  meshRoots = [];
  modelMeshes = [];
}

export const stateOf = new Map();

export const hiddenNames = new Set();

// What counts as ground rather than as an object standing on it.
export const GROUND = new Set(["facility", "deck"]);

/** Whether this resource rides something that travels, rather than standing on the deck. */
export function carried(index) {
  for (let i = world.parentOf[index]; i >= 0; i = world.parentOf[i]) {
    if (MOVING_PARTS.has(modelOf(i).category)) return true;
  }
  return false;
}

/** Whether this resource travels over the deck, itself or by riding something that does. */
export function travels(index) {
  return MOVING_PARTS.has(modelOf(index).category) || carried(index);
}

// What a resource stands on, in facility mm, and then how deep it sits in the tree. Depth testing is
// off in an axis view, so what paints last is what shows, and nesting alone decided that - which put
// a well 100 mm up over a 96-head 400 mm up, because the well sat one level deeper. Height leads
// now; nesting only separates things standing at the same level, where a parent still paints first.
//
// Where a resource stands rather than how high it reaches: a shell is as tall as everything it
// holds, so reaching highest would have the facility paint over its own contents.
const PAINT_LEVEL = 100;

export function paintOrderOf(index) {
  return world.matrices[index].elements[14] * PAINT_LEVEL + treeDepth(index) * 2;
}

// Anything drawn over the scene rather than in it - the origin marker, the highlight boxes, the
// measurement legs - orders above every paint order a resource can reach. A fixed number cannot do
// that on its own: paint order is a height in millimetres times PAINT_LEVEL, so the band has to
// start past the tallest facility anyone will draw. Ten metres of stacked equipment is that.
export const OVERLAY_ORDER = 10_000 * PAINT_LEVEL;

// The layer a part held over the deck is drawn in, above what stands on the deck. Not a height and
// not a band of them: depth says what is over what, and this says only that a travelling part is
// blended over the deck rather than into it. Whole numbers, with room for a vessel's own contents
// between them.
export const CARRIED_LAYER = 2;

const IDENTITY_Q = new THREE.Quaternion();

const tmpMatrix = new THREE.Matrix4();

const tmpVec = new THREE.Vector3();

const tmpScale = new THREE.Vector3();

export const ZERO = new THREE.Matrix4().makeScale(0, 0, 0);

// A resource's origin is the minimum corner of its box, so the drawn centre sits half a size in.
export function boxMatrix(matrix, sx, sy, sz, ox, oy, oz) {
  tmpVec.set(ox ?? sx / 2, oy ?? sy / 2, oz ?? sz / 2);
  tmpScale.set(sx, sy, sz);
  return tmpMatrix.compose(tmpVec, IDENTITY_Q, tmpScale).premultiply(matrix);
}

export function placeInstance(mesh, slot, matrix, sx, sy, sz, ox, oy, oz) {
  // A mesh out of a model file sits at a transform of its own inside that file, which is not a
  // scale and an offset. Given one, it is applied to the resource's own matrix as it stands.
  if (sx?.isMatrix4) {
    mesh.setMatrixAt(slot, tmpMatrix.multiplyMatrices(matrix, sx));
    return;
  }
  mesh.setMatrixAt(slot, boxMatrix(matrix, sx, sy, sz, ox, oy, oz));
}

// A well or a tip spot is drawn as a rim with an inside that carries what is in it, rather than as
// a shell to see through: what holds liquid, or holds a tip.
export const isVessel = (model) =>
  (Number.isFinite(model.max_volume) && model.max_volume > 0) || model.category === "tip_spot";

function isEnclosure(index) {
  const model = modelOf(index);
  return (
    world.childrenOf[index].length > 0 ||
    model.max_volume !== undefined ||
    MOVING_PARTS.has(model.category)
  );
}

export function collectEnclosedModels(index, into) {
  for (const child of world.childrenOf[index]) {
    if (isEnclosure(child)) into.add(world.modelOf[child]);
    collectEnclosedModels(child, into);
  }
}

// A carrier is the level you look at rather than through: its floor is filled in, and its walls
// keep their fill instead of being culled when the things it holds are drawn.
export function isCarrier(model) {
  return String(model.category).includes("carrier");
}

export function enclosureDepth(index) {
  let depth = 0;
  for (let i = world.parentOf[index]; i >= 0; i = world.parentOf[i]) {
    if (isEnclosure(i)) depth++;
  }
  return depth;
}

export function hasEnclosedDescendant(index) {
  for (const child of world.childrenOf[index]) {
    if (isEnclosure(child) || hasEnclosedDescendant(child)) return true;
  }
  return false;
}

/**
 * Where one instanced part stands, so it can be put back after being emptied. `emptyOnly` marks
 * a part drawn only while the resource holds nothing, such as a spot's cavity.
 */
export function remember(index, mesh, slot, at, emptyOnly = false) {
  if (!overlayOf.has(index)) overlayOf.set(index, []);
  overlayOf.get(index).push({ mesh, slot, at, emptyOnly });
}

// Only explicitly hidden resources go in the set. A resource is drawn when neither it nor any
// ancestor is hidden, so "hidden because I was toggled off" stays distinct from "hidden because
// a parent is off".
export function isVisible(index) {
  for (let i = index; i >= 0; i = world.parentOf[i]) {
    if (hiddenNames.has(world.names[i])) return false;
  }
  return true;
}

// A well's rim and its cavity, a carrier's floor: everything drawn outside the box
// pipeline. Each one follows the resource it belongs to - emptied when that resource is switched
// off, put back where it stands when it is switched on, and carried along when it moves.
export function placeParts(index, touched) {
  const visible = isVisible(index);

  for (const part of overlayOf.get(index) ?? []) {
    const shown = visible && !(part.emptyOnly && world.childrenOf[index].length > 0);
    if (shown) placeInstance(part.mesh, part.slot, world.matrices[index], ...part.at);
    else part.mesh.setMatrixAt(part.slot, ZERO);
    touched.add(part.mesh);
  }
}

// PLR's own reference semantics: a resource's origin is its left, front, bottom corner.
//
// `cavity_bottom` is the one reference a resource may be unable to answer: it is the floor of what
// a container holds, standing its base's thickness above the outside of that base, and only a
// container states a thickness. The point still comes back, so x and y read as they always do,
// with `zKnown` false so a caller can say the height is unavailable rather than print the bottom
// of the box as though it were the cavity's.
export function referencePoint(index, xRef, yRef, zRef) {
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
          ? (thickness ?? 0)
          : 0;
  const point = new THREE.Vector3(x, y, z).applyMatrix4(world.matrices[index]);
  point.zKnown = zRef !== "cavity_bottom" || typeof thickness === "number";
  return point;
}

export function worldBox(index) {
  const [sx, sy, sz] = sizeOf(modelOf(index));
  const box = new THREE.Box3();
  const corner = new THREE.Vector3();
  for (const c of [
    [0, 0, 0],
    [sx, 0, 0],
    [0, sy, 0],
    [0, 0, sz],
    [sx, sy, 0],
    [sx, 0, sz],
    [0, sy, sz],
    [sx, sy, sz],
  ]) {
    corner.set(c[0], c[1], c[2]).applyMatrix4(world.matrices[index]);
    box.expandByPoint(corner);
  }
  return box;
}
