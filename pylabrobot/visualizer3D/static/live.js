// The scene kept in step with the tree: state messages, moves, glides and visibility, applied to
// what is drawn, with the page told what changed.

import * as THREE from "three";

import { placeEdges, showEdges } from "./boxes.js";
import { LIQUID, MOVING_PARTS, VESSEL_EMPTY } from "./constants.js";
import {
  hiddenNames,
  isVisible,
  meshRoots,
  placeInstance,
  placementOf,
  placeParts,
  stateOf,
  vesselOf,
  ZERO,
} from "./drawn.js";
import { armPose, arms, gridMarks, referenceMarks, refreshHalos } from "./marks.js";
import { applyJoints } from "./models.js";
import {
  mirrorPlacement,
  modelOf,
  refreshTransforms,
  setLocal,
  setLocalRotation,
  sizeOf,
  world,
} from "./world.js";

// Glide rather than teleport, so a move reads as motion. The tracker carries commanded targets, so
// this interpolation is cosmetic and says nothing about where the arm physically is mid-move.

// How long a move takes to draw, in seconds. Off by default: a viewer watching a device should show
// where it is, and a glide is the drawing running behind. Turned up to follow a simulated run.
const DEFAULT_GLIDE_SECONDS = 0;

let glideSeconds = DEFAULT_GLIDE_SECONDS;

export function setGlideSeconds(seconds) {
  glideSeconds = Math.max(0, seconds);
}

const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");

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
      touched.add(placement.mesh);
    }
    // An outline is segments in its model's line buffer, so a move that touched only the instance
    // left them at the old position - a wireframe ghost of whatever rode the arm.
    placeEdges(at);
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
  for (const mesh of touched) {
    mesh.instanceMatrix.needsUpdate = true;
    mesh.boundingSphere = null;
  }
}

function refreshSubtree(index, skipSelf) {
  redraw(refreshTransforms(index, skipSelf));
}

// Who is told when the live state changed what is drawn: the tree, the panels, the device tools.
// Told what kind of change it was, and for a state message which resources it touched.
const changeListeners = [];

function announce(change) {
  for (const listener of changeListeners) listener(change);
}

export function updateArms(delta) {
  const reduce = reducedMotion?.matches;
  let moved = false;
  for (const arm of arms) {
    if (Math.abs(arm.targetX - arm.currentX) < 0.01) continue;
    moved = true;
    arm.currentX =
      reduce || !glideSeconds
        ? arm.targetX
        : arm.currentX + (arm.targetX - arm.currentX) * Math.min(1, delta / glideSeconds);
    const local = new THREE.Matrix4().makeTranslation(arm.currentX, arm.local[1], arm.local[2]);
    arm.group.matrix.multiplyMatrices(arm.parentMatrix, local);
    arm.group.matrixWorldNeedsUpdate = true;

    // Keep the scene model in step with what is drawn. Everything else reads position from here -
    // the info panel, the selection box, the coordinate tool - so moving only the group would
    // leave all of them quoting where the arm used to be.
    mirrorPlacement(arm.index, arm.currentX, arm.group.matrix);
    announce({ kind: "glide", index: arm.index });
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
  }
  return moved;
}

// What each gliding resource is easing toward, by index. A second move replaces the first: the
// model is already at the new target and only the drawing is behind.
export const glides = new Map();

function applyLocation(index, location) {
  // A travelling part is drawn by its own group and glides there, so it is told the target rather
  // than being moved under it. What stands on it is not part of that group, though - the 96-head
  // rides the arm in the model but is drawn with everything else - so the glide carries it.
  if (MOVING_PARTS.has(modelOf(index).category)) {
    const arm = arms.find((a) => a.index === index);
    if (arm) {
      if (!setLocal(index, location)) return;
      arm.targetX = location.x;
      return;
    }
  }

  // Everything else eases its own transform, so what stands on it comes along and every panel
  // reads the position where it always has.
  if (!glideSeconds || reducedMotion?.matches) {
    glides.delete(index);
    if (setLocal(index, location)) refreshSubtree(index);
    return;
  }
  const o = index * 6;
  if (
    world.local[o] === location.x &&
    world.local[o + 1] === location.y &&
    world.local[o + 2] === location.z
  ) {
    glides.delete(index);
    return;
  }
  // No frame asked for here: the message that carried this position asked for one at its own
  // edge, and `updateGlides` keeps the loop running while anything is still easing.
  glides.set(index, {
    from: { x: world.local[o], y: world.local[o + 1], z: world.local[o + 2] },
    to: { x: location.x, y: location.y, z: location.z },
    left: glideSeconds,
  });
}

export function onChange(listener) {
  changeListeners.push(listener);
}

export function updateGlides(delta) {
  if (!glides.size) return false;
  for (const [index, glide] of glides) {
    glide.left -= delta;
    const done = glide.left <= 0;
    // How much of the move is still to come, so the whole of it takes `glideSeconds` whatever its
    // length and however the frames fall.
    const left = done ? 0 : glide.left / glideSeconds;
    const next = done
      ? glide.to
      : {
          x: glide.to.x - (glide.to.x - glide.from.x) * left,
          y: glide.to.y - (glide.to.y - glide.from.y) * left,
          z: glide.to.z - (glide.to.z - glide.from.z) * left,
        };
    if (done) glides.delete(index);
    if (setLocal(index, next)) refreshSubtree(index);
  }
  return true;
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

function refreshOverlays(index, touched) {
  const state = stateOf.get(index);

  if (MOVING_PARTS.has(modelOf(index).category)) {
    const tracked = state?.tracker?.x;
    if (tracked !== undefined && tracked !== null) setArmX(index, tracked);
  }

  const vessel = vesselOf.get(index);
  if (vessel && Number.isFinite(vessel.model.max_volume)) {
    // The committed volume, as the existing visualizer draws it: what is in the well, not what an
    // operation under way would leave there if it succeeds.
    const volume = state?.volume ?? 0;
    const fraction = Math.max(0, Math.min(1, volume / (vessel.model.max_volume || 1)));
    // Empty is white; any liquid at all steps clear of white so a nearly empty well still reads.
    const t = fraction > 0 ? 0.35 + 0.65 * fraction : 0;
    vessel.mesh.setColorAt(
      vessel.slot,
      new THREE.Color(VESSEL_EMPTY).lerp(new THREE.Color(LIQUID), t),
    );
    if (vessel.mesh.instanceColor) vessel.mesh.instanceColor.needsUpdate = true;
  }

  placeParts(index, touched);
}

// State arrives as a table of the distinct states in the scene, plus which of them each resource
// holds. Empty wells and unused tip spots share a single entry, and anything that has not changed
// since the client was last told is absent.
//
// Addressed by name rather than by scene index: the order instances are emitted in is not stable
// across rebuilds, so an index can mean a different resource in the next scene. A name cannot.
export function applyState(payload) {
  const touched = new Set();
  const { states, of } = payload;
  if (!states || !of) return;
  for (const [name, slot] of Object.entries(of)) {
    const index = world.indexOfName.get(name);
    if (index === undefined) continue;
    // Shared between every resource in the same state, and only ever read.
    stateOf.set(index, states[slot]);
    // A state carries the whole of what a resource publishes, so a rotation that is not in it is
    // one that has come back to zero - the sender drops the identity to keep the message small.
    // Taking absence as "unchanged" leaves a joint drawn at the last angle it was turned to.
    applyRotation(index, states[slot]?.rotation ?? { x: 0, y: 0, z: 0 });
    refreshOverlays(index, touched);
    applyJoints(index);
  }
  for (const [name, location] of Object.entries(payload.locations ?? {})) {
    const index = world.indexOfName.get(name);
    if (index !== undefined) applyLocation(index, location);
  }
  for (const mesh of touched) {
    mesh.instanceMatrix.needsUpdate = true;
    mesh.boundingSphere = null;
  }
  const changed = new Set(Object.keys(of).map((name) => world.indexOfName.get(name)));
  announce({ kind: "state", changed });
}

export function setHidden(name, hidden) {
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
    showEdges(index);
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
  for (const mesh of touched) {
    mesh.instanceMatrix.needsUpdate = true;
    mesh.boundingSphere = null;
  }
  announce({ kind: "visibility" });
}

// A resource put somewhere else, applied to the scene the page has rather than the scene being
// built again: a tip picked up is the same tip, now under a channel's shaft. The tree keeps its
// shape, the panel stays open, nothing is torn down to be fetched and drawn again.
export function applyMoves(moves) {
  const touched = new Set();
  const rowsUnder = new Set();
  for (const move of moves) {
    const index = world.indexOfName.get(move.name);
    if (index === undefined) continue;
    const parent = move.parent === null ? -1 : (world.indexOfName.get(move.parent) ?? -1);
    const was = world.parentOf[index];
    if (was !== parent) {
      if (was >= 0) {
        const siblings = world.childrenOf[was];
        const at = siblings.indexOf(index);
        if (at >= 0) siblings.splice(at, 1);
        rowsUnder.add(was);
      }
      if (parent >= 0) {
        world.childrenOf[parent].push(index);
        rowsUnder.add(parent);
      }
      world.parentOf[index] = parent;
    }
    setLocal(index, move.location);
    setLocalRotation(index, move.rotation);
    for (const i of refreshTransforms(index)) touched.add(i);
    // The spot it left and the one it reached draw their cavities from whether they hold a tip.
    for (const i of [was, parent]) if (i >= 0) touched.add(i);
    // An arm is drawn by its own group, which is put where the model now says, with nothing to glide.
    const arm = arms.find((a) => a.index === index);
    if (arm) {
      Object.assign(arm, armPose(index));
      arm.group.matrix.copy(world.matrices[index]);
      arm.group.matrixWorldNeedsUpdate = true;
    }
  }
  redraw([...touched]);
  refreshHalos();
  announce({ kind: "moves", rowsUnder });
}
