// What a refused command would hit: a red, transparent box over each resource the command ran
// into, drawn over everything. Where the check walked its way finely, what would have hit is
// brought to the meeting first, and the page holds it there. Held until the next word about
// collisions or the scene is rebuilt.

import * as THREE from "three";

import { COLLISION, COLLISION_OPACITY } from "./constants.js";
import { OVERLAY_ORDER, worldBox } from "./drawn.js";
import { holdAtMeeting } from "./live.js";
import { view } from "./renderer.js";
import { world } from "./world.js";

const boxes = new THREE.Group();

boxes.visible = false;

view.add(boxes);

export function clearCollisions() {
  for (const child of [...boxes.children]) {
    child.geometry.dispose();
    child.material.dispose();
    boxes.remove(child);
  }
  boxes.visible = false;
}

/** One box over one resource's world extent, filled where it is and outlined where it ends. */
function collisionBox(index) {
  const box = worldBox(index);
  const size = new THREE.Vector3();
  box.getSize(size);
  const centre = new THREE.Vector3();
  box.getCenter(centre);
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(size.x, size.y, size.z),
    new THREE.MeshBasicMaterial({
      color: COLLISION,
      transparent: true,
      opacity: COLLISION_OPACITY,
      depthTest: false,
    }),
  );
  mesh.position.copy(centre);
  mesh.renderOrder = OVERLAY_ORDER + 30;
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(mesh.geometry),
    new THREE.LineBasicMaterial({ color: COLLISION, transparent: true, depthTest: false }),
  );
  mesh.add(edges);
  return mesh;
}

/** The server's word about what a refused command would hit: what would have met it is eased to
 * the instant of the first meeting, and a box goes over each resource met there. */
export function showCollisions(items, moves = []) {
  clearCollisions();
  // Where each moved part stands at the instant, worked out against where things stood before, so
  // nothing is dragged twice: a part's parent moves by its own delta, and the part by its own.
  const instant = new Map();
  for (const move of moves) {
    if (typeof move.index !== "number" || !Array.isArray(move.at)) continue;
    if (instant.has(move.index)) continue;
    instant.set(move.index, metMatrix(move.at, world.matrices[move.index]));
  }
  const parts = [];
  for (const [index, met] of instant) {
    const parent = world.parentOf[index];
    const stood = parent >= 0 ? (instant.get(parent) ?? world.matrices[parent]) : null;
    const to = stood ? new THREE.Matrix4().copy(stood).invert().multiply(met) : met;
    const from =
      parent >= 0
        ? new THREE.Matrix4().copy(world.matrices[parent]).invert().multiply(world.matrices[index])
        : new THREE.Matrix4().copy(world.matrices[index]);
    parts.push({ index, from, to });
  }
  holdAtMeeting(parts);
  for (const item of items) {
    if (typeof item.index !== "number") continue;
    boxes.add(collisionBox(item.index));
  }
  boxes.visible = boxes.children.length > 0;
}

/** A part's world matrix at the meeting: its pose there, composed with where it stands. */
function metMatrix(at, stood) {
  const [turn, px, py, pz, sx, sy, sz] = at;
  const rad = (turn * Math.PI) / 180;
  const met = new THREE.Matrix4()
    .makeTranslation(px, py, pz)
    .multiply(new THREE.Matrix4().makeRotationZ(rad))
    .multiply(new THREE.Matrix4().makeTranslation(-px, -py, -pz));
  met.premultiply(new THREE.Matrix4().makeTranslation(sx, sy, sz));
  return new THREE.Matrix4().multiplyMatrices(met, stood);
}
