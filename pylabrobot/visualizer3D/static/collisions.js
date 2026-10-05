// What a refused command would hit: a red, transparent box over each resource the command ran
// into, drawn over everything. Where the check walked its way finely, what would have hit is
// brought to the meeting first, and the page holds it there. Held until the next word about
// collisions or the scene is rebuilt.

import * as THREE from "three";

import { COLLISION, COLLISION_OPACITY } from "./constants.js";
import { OVERLAY_ORDER, worldBox } from "./drawn.js";
import { applyContactPose } from "./live.js";
import { view } from "./renderer.js";

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

/** The server's word about what a refused command would hit: boxes over each resource named, and
 * (`moves`) what would have hit it brought to where the meeting happened. */
export function showCollisions(items, moves = []) {
  clearCollisions();
  const brought = new Set();
  for (const move of moves) {
    if (typeof move.index !== "number" || !Array.isArray(move.at)) continue;
    if (brought.has(move.index)) continue;
    brought.add(move.index);
    applyContactPose(move.index, move.at);
  }
  for (const item of items) {
    if (typeof item.index !== "number") continue;
    boxes.add(collisionBox(item.index));
  }
  boxes.visible = boxes.children.length > 0;
}
