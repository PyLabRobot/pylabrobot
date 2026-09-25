import * as THREE from "three";

import { DEG } from "./constants.js";

/**
 * The scene as the client holds it: what exists, where it is, and what shape each thing is.
 *
 * One object, read from about a hundred and twenty places and replaced from exactly one. It lives
 * here rather than in `app.js` so that the parts of the viewer that only need to know what is in
 * the scene do not have to be part of the file that draws it.
 *
 * Read it directly. ES modules export a live binding, so an importer always sees the current scene
 * without asking for it. Replacing it goes through `setWorld`, which is the only way it changes and
 * therefore the only place to look when it does.
 */

/**
 * @typedef {object} World
 * @property {string[]} names            every instance, in the order the scene emitted them
 * @property {number[]} modelOf          instance index -> index into `models`
 * @property {number[]} parentOf         instance index -> parent index, or -1 at the root
 * @property {any[]} matrices            world transform per instance, absolute
 * @property {Float32Array} local        six floats per instance: position, then rotation
 * @property {number[][]} childrenOf     instance index -> its children
 * @property {any[]} models              each distinct shape, sent once
 * @property {Map<string, number>} indexOfName
 */

/** @type {World | null} */
export let world = null;

function decodeTransforms(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

export function buildWorld(data) {
  const { names, model, parent, transforms } = data.instances;
  const xf = decodeTransforms(transforms);
  const n = names.length;

  const matrices = new Array(n);
  const local = new THREE.Matrix4();
  const euler = new THREE.Euler();

  // Parents are emitted before children, so one forward pass resolves every world transform.
  for (let i = 0; i < n; i++) {
    const o = i * 6;
    euler.set(xf[o + 3] * DEG, xf[o + 4] * DEG, xf[o + 5] * DEG, "XYZ");
    local.makeRotationFromEuler(euler);
    local.setPosition(xf[o], xf[o + 1], xf[o + 2]);
    const p = parent[i];
    matrices[i] = p < 0 ? local.clone() : matrices[p].clone().multiply(local);
  }

  const childrenOf = Array.from({ length: n }, () => []);
  for (let i = 0; i < n; i++) if (parent[i] >= 0) childrenOf[parent[i]].push(i);

  const indexOfName = new Map();
  for (let i = 0; i < n; i++) indexOfName.set(names[i], i);

  return {
    names,
    modelOf: model,
    parentOf: parent,
    matrices,
    local: xf,
    childrenOf,
    models: data.models,
    indexOfName,
  };
}

/**
 * Take a new scene. Everything derived from the old one is invalid from here on: a rebuild
 * renumbers every instance, so an index held across this call means a different resource after it.
 *
 * @param {World | null} next
 */
export function setWorld(next) {
  world = next;
}

/** The model a given instance is an instance of. */
export const modelOf = (index) => world.models[world.modelOf[index]];

/** A model's extent in mm, never zero: a resource with no size still has to be pickable.
 *
 * A round resource may give a diameter and no footprint - a tip is the one that matters, since a
 * rack holds ninety-six of them. Read as 0.1 mm it sits under every rule that asks how big a thing
 * is on screen, so its model was culled at any zoom and the rack showed empty holes. */
export const sizeOf = (model) => [
  model.size_x || model.diameter || 0.1,
  model.size_y || model.diameter || 0.1,
  model.size_z || 0.1,
];

/** How many resources stand between this one and the root. */
export function treeDepth(index) {
  let depth = 0;
  for (let i = world.parentOf[index]; i >= 0; i = world.parentOf[i]) depth++;
  return depth;
}

// Working out a world transform means walking down from whatever moved, because a resource's own
// position is relative to its parent while the matrices the scene is drawn from are absolute. A
// parent moving therefore changes its children's place in the world without changing anything they
// hold about themselves - which is the whole reason this exists rather than being a one-line update.
const _localMatrix = new THREE.Matrix4();
const _localEuler = new THREE.Euler();

/**
 * Recompute the world transform of everything at or beneath `index`.
 *
 * Returns the instances it touched, so a caller that draws them can move exactly those and no more.
 * Deliberately knows nothing about what is drawn: the arithmetic is the same whether anything is on
 * screen or not.
 *
 * @param {number} index
 * @param {boolean} [skipSelf] when the caller has already set this one's matrix itself
 * @returns {number[]}
 */
export function refreshTransforms(index, skipSelf) {
  const touched = [];
  const stack = [index];
  while (stack.length) {
    const at = stack.pop();
    if (at !== index || !skipSelf) {
      const parent = world.parentOf[at];
      const q = at * 6;
      _localEuler.set(
        world.local[q + 3] * DEG,
        world.local[q + 4] * DEG,
        world.local[q + 5] * DEG,
        "XYZ",
      );
      _localMatrix.makeRotationFromEuler(_localEuler);
      _localMatrix.setPosition(world.local[q], world.local[q + 1], world.local[q + 2]);
      if (parent < 0) world.matrices[at].copy(_localMatrix);
      else world.matrices[at].multiplyMatrices(world.matrices[parent], _localMatrix);
      touched.push(at);
    }
    for (const child of world.childrenOf[at]) stack.push(child);
  }
  return touched;
}

/**
 * Where a resource sits, in mm, relative to its parent. Returns whether it actually moved, so a
 * caller can do nothing when told something it already knew.
 *
 * @param {number} index
 * @param {{x: number, y: number, z: number}} location
 * @returns {boolean}
 */
export function setLocal(index, location) {
  const o = index * 6;
  if (
    world.local[o] === location.x &&
    world.local[o + 1] === location.y &&
    world.local[o + 2] === location.z
  ) {
    return false;
  }
  world.local[o] = location.x;
  world.local[o + 1] = location.y;
  world.local[o + 2] = location.z;
  return true;
}

/**
 * Which way a resource is turned, in degrees, relative to its parent. Returns whether it actually
 * turned, so a caller can do nothing when told something it already knew.
 *
 * A joint reports its angle and nothing else - an arm's links have no position of their own, they
 * sit at the joint they turn on - so without this a link that swings is a link that never moves.
 *
 * @param {number} index
 * @param {{x: number, y: number, z: number}} rotation
 * @returns {boolean}
 */
export function setLocalRotation(index, rotation) {
  const o = index * 6 + 3;
  const x = rotation.x ?? 0;
  const y = rotation.y ?? 0;
  const z = rotation.z ?? 0;
  if (world.local[o] === x && world.local[o + 1] === y && world.local[o + 2] === z) {
    return false;
  }
  world.local[o] = x;
  world.local[o + 1] = y;
  world.local[o + 2] = z;
  return true;
}

/**
 * Mirror where a travelling part has been put.
 *
 * Most resources are placed by working their transform out from their parent. A part that moves is
 * the other way round: it is drawn by a group of its own that is moved directly, and the world has
 * to be told what that group did, or everything reading position from here - the info panel, the
 * selection box, the coordinate tool - quotes where it used to be.
 *
 * @param {number} index
 * @param {number} localX  where it now sits along x, relative to its parent
 * @param {any} matrix     the world transform the group was given
 */
export function mirrorPlacement(index, localX, matrix) {
  world.local[index * 6] = localX;
  world.matrices[index].copy(matrix);
}
