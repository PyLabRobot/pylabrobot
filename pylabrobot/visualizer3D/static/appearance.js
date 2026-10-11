// How the drawn scene looks from where the camera is: the detail level, the view mode, and the
// opacity and paint order every box is given.

import * as THREE from "three";

import { showEdges } from "./boxes.js";
import {
  ARM_EDGE_WIDTH_3D,
  ARM_EDGE_WIDTH_FLAT,
  ARM_REFERENCE_OPACITY,
  BOX_OPACITY,
  CATEGORY_OPACITY,
  CONTAINERS,
  EDGE_WIDTH_3D,
  EDGE_WIDTH_FLAT,
  FLAT_EDGE,
  MODEL_EDGE_OPACITY,
  MOVING_OPACITY,
  MOVING_PARTS,
  SHELL_OPACITY,
  SPACE_OPACITY,
} from "./constants.js";
import {
  CARRIED_LAYER,
  drawnFromFile,
  GROUND,
  isCarrier,
  meshes,
  meshRoots,
  modelMeshes,
  paintOrderOf,
  travels,
} from "./drawn.js";
import {
  ARM_FRAME_OFFSET,
  ARM_LINE_OFFSET,
  ARM_OUTLINE_OFFSET,
  arms,
  GRID_LABEL_MM,
  gridLabels,
  gridMarks,
  surfaces,
} from "./marks.js";
import { camera, controls, mmPerPixel } from "./renderer.js";
import { modelOf, sizeOf, world } from "./world.js";

// A model that is on screen puts its box away. Recorded on the entry rather than only switched
// off, because everything that decides what is drawn runs again on every view change, and each of
// those would otherwise put the box back over the model it was standing in for.

let detailScale = null;

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

// Set when a file's arrival changed what a box is, for the next frame's mode pass to pick up: one
// pass then, however many files landed, rather than a pass per file.
let renderModeDirty = false;

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
function opacityOf(isSpace, moves, own, isShell, standsIn) {
  if (isSpace) return SPACE_OPACITY;
  if (moves) return MOVING_OPACITY;
  if (own !== undefined) return own;
  // A box that is holding the place of a model is no longer a statement about extent: it is the
  // picture of the thing, and it is drawn as solidly as the model would have been. A tip at
  // BOX_OPACITY over a white rack composites to about #a3a3a3, which is the colour of an empty
  // spot - so a full rack and a spent one looked alike everywhere the model was too small to draw.
  if (standsIn) return 1;
  if (isShell) return SHELL_OPACITY;
  return BOX_OPACITY;
}

/** How see-through this model's box is drawn, given what it is and whether it stands in for a
 * model. Read by the mode change and by the rule that hands the box back and forth with the
 * model, so the two cannot disagree about it. */
function boxOpacity(entry) {
  return opacityOf(
    GROUND.has(entry.model.category),
    MOVING_PARTS.has(entry.model.category),
    CATEGORY_OPACITY[entry.model.category],
    entry.holdsEnclosure,
    entry.standsIn === true,
  );
}

// The flags a pipeline is built from, with the renderer told only when one of them changed: a bump
// that changes nothing still re-keys the material, and a line material never keys the same twice.
function setPipelineFlags(material, flags) {
  let changed = false;
  for (const [flag, value] of Object.entries(flags)) {
    if (material[flag] === value) continue;
    material[flag] = value;
    changed = true;
  }
  if (changed) material.needsUpdate = true;
}

function setRenderMode(plan) {
  for (const entry of meshes) {
    // Lit, at every angle. The lights ride with the camera, so a surface is shaded by its own shape
    // rather than by where it is being looked at - which is what makes a box read as a box without
    // the view being able to change what colour it is.
    const material = entry.mesh.material.userData.lit ?? entry.mesh.material;
    if (entry.mesh.material !== material) entry.mesh.material = material;
    const isShell = entry.holdsEnclosure;
    // Ground: the space things stand in, and the surface they stand on. Neither is a thing to look
    // at, and drawing either solid hides what it carries - a deck drawn opaque is a sheet the same
    // colour as the plates on it. Both stay barely there in every mode: enough to see where the
    // floor ends and where the deck reaches, never enough to tint what is on them. What makes a
    // deck legible is its rail grid, its numbers and its access bands, and those are their own
    // objects.
    const isSpace = GROUND.has(entry.model.category);
    const rides = entry.instances.some((i) => travels(i));

    material.opacity = boxOpacity(entry);
    // Ground stays out of the depth buffer in either view - a wash over the picture, not a surface
    // anything is behind.
    //
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
    setPipelineFlags(material, {
      transparent: true,
      side: isShell || isSpace ? THREE.BackSide : THREE.FrontSide,
      depthTest: !isSpace,
      depthWrite: !isSpace,
    });
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
      // A mode change is not a zoom, so the rule that culls small things may not run again before
      // the next frame: a plan-only overlay is switched here too, and by the same two tests.
      if (overlay.userData.planOnly) overlay.visible = plan && entry.detailVisible !== false;
      // From above a vessel has to show its own contents, and depth would stop it: a tip hangs
      // below the spot that holds it, and liquid sits below the cavity it fills. So in a plan they
      // are painted in their own resource's layer, which is as far as they can reach. From any
      // other angle depth is right - what stands in front of a well does cover it - and there they
      // test like everything else.
      setPipelineFlags(overlay.material, { depthTest: !plan, depthWrite: !plan });
      overlay.renderOrder = plan ? layer + (overlay.userData.behind ? 0.5 : 1) : 0;
    }
  }

  for (const surface of surfaces) {
    surface.material = surface.userData.lit;
  }

  // A mesh out of a model file, whether it was cloned for one resource or instanced for many.
  const fromFile = [];
  for (const root of meshRoots) root.traverse((o) => o.isMesh && fromFile.push(o));
  for (const built of modelMeshes) for (const mesh of built.meshes) fromFile.push(mesh);
  for (const o of fromFile) {
    if (o.userData.lit) o.material = o.userData.lit;
    const modelled = o.userData.asModelled;
    // An instanced mesh holds only what stands still; a clone is drawn for one resource.
    const rides = o.userData.declaredBy !== undefined && travels(o.userData.declaredBy);
    // Glazing comes out of an axis view. A part that travels keeps whatever it was modelled with,
    // see-through included: it is drawn that way so the deck under it can be read.
    o.material.visible = !(plan && modelled?.glazed && !rides);
    if (!modelled) return;
    // A part held over the deck is drawn see-through, as its box is, so that what it is above
    // still reads through it. It does not write depth for the same reason; it still tests, so
    // the machine's own structure above it covers it as it should.
    const lifted = plan && rides;
    o.material.opacity = lifted ? Math.min(modelled.opacity, MOVING_OPACITY) : modelled.opacity;
    setPipelineFlags(o.material, {
      transparent: lifted ? true : modelled.transparent,
      depthWrite: lifted ? true : modelled.depthWrite,
      depthTest: true,
    });
    o.renderOrder = lifted ? CARRIED_LAYER : 0;
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

  for (const entry of meshes) {
    const edges = entry.edges;
    if (!edges) continue;
    // All that is left of a box whose model is being drawn: its border, and only just.
    const stoodIn = entry.modelDrawn;
    edges.material.depthTest = !plan;
    edges.material.opacity = stoodIn ? MODEL_EDGE_OPACITY : plan ? 1 : edges.userData.baseOpacity;
    edges.material.linewidth = plan ? EDGE_WIDTH_FLAT : EDGE_WIDTH_3D;
    edges.material.color.set(plan ? FLAT_EDGE : edges.userData.baseColor);
    // One order for every instance of a model, the highest of theirs: outlines are one colour in
    // a plan, so the order only decides what they are painted over.
    edges.renderOrder = plan ? Math.max(...entry.instances.map(paintOrderOf)) + 1 : 0;
    const wanted = edges.userData.sets[plan ? 1 : 0].geometry;
    if (edges.geometry !== wanted) edges.geometry = wanted;
    // Told every pass, not only when a flag changed: a line material told only then draws a plan
    // view's outlines faintly, so three reads more off it than the values above and its flags.
    edges.material.needsUpdate = true;
  }
}

export function modelIsDrawn(modelIndex) {
  // Geometry that has just arrived has not been asked whether it is big enough to be worth
  // drawing: that is decided per view, and the view has not changed just because a file loaded.
  detailScale = null;
  const entry = meshes.find((m) => m.modelIndex === modelIndex);
  if (!entry) return;
  entry.modelDrawn = true;
  entry.mesh.material.visible = false;
  for (const i of entry.instances) drawnFromFile.add(i);
  // A part that travels is drawn twice over: once as the open frame `buildArms` extrudes for it,
  // and now as itself. The frame and the stroke around it were standing in for geometry nobody
  // had, so they go the way the box does. The reference line stays: it marks where the drive
  // reports this part to be, which is not a fact about the shape and is the one thing the geometry
  // cannot say for itself.
  for (const arm of arms) {
    if (!entry.instances.includes(arm.index)) continue;
    arm.frame.visible = false;
    arm.outline.visible = false;
  }
  // The view is not going to change just because a file finished loading, so the border is faded
  // by the next frame's mode pass, which the rule that keeps it faded runs anyway.
  renderModeDirty = true;
}

// Below this many pixels across, a resource contributes noise rather than information. Set so a
// well still draws at a 500 mm scale bar, where a pixel is worth about 2.5 mm and a 6.9 mm well
// projects to roughly 2.7 px; it drops out at facility zoom, where it is closer to 1.4 px and a
// thousand of them read as grey haze. Its parent is still drawn, so nothing vanishes without
// something in its place.
const DETAIL_MIN_PX = 2;

// And below this a model is not worth its geometry: the box it stood in for says the same thing
// at a fraction of the cost, and one box is drawn with all the others in a single call. Between
// the two a resource is still there, drawn as a box; below the smaller one it is not drawn at all.
// Eight pixels is where an 8.2 mm tip lands with the camera about a metre off an 840 px tall
// canvas: far enough out that a deck being worked on is still made of things, not boxes.
const MODEL_MIN_PX = 8;

// Below this a rail number is a smudge rather than a number. Nothing is lost by not drawing it, and
// at facility scale it is most of what the renderer is being asked to do.
const LABEL_MIN_PX = 7;

/** A new scene: the detail level and the view mode are worked out afresh for it. */
export function forgetDetail() {
  detailScale = null;
  planView = null;
}

/** The mode pass a landed file asked for, run ahead of the detail rules that build on it. */
function settleRenderMode() {
  if (!renderModeDirty || planView === null) return;
  renderModeDirty = false;
  setRenderMode(planView);
}

export function updateDetail() {
  settleRenderMode();
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

  // A model is geometry, and geometry is what the renderer spends its frame on: a tip rack two
  // pixels across was still drawing its ninety-six tips, one draw call each. The rule that decides
  // whether a box is worth drawing decides this too - and a part that travels is exempt, because
  // what it is doing is the thing being watched.
  const geometryOf = new Set();
  for (const root of meshRoots) {
    const index = root.userData.index;
    const [sx, sy] = sizeOf(modelOf(index));
    const visible = travels(index) || Math.max(sx, sy) / perPixel >= MODEL_MIN_PX;
    if (root.visible !== visible) root.visible = visible;
    if (visible) geometryOf.add(world.modelOf[index]);
  }
  // Every resource drawn from one instanced mesh is the same model at the same size, and none of
  // them travels - what travels keeps a clone of its own - so one answer covers the lot.
  for (const built of modelMeshes) {
    const [sx, sy] = sizeOf(world.models[built.modelIndex]);
    const visible = Math.max(sx, sy) / perPixel >= MODEL_MIN_PX;
    for (const mesh of built.meshes) if (mesh.visible !== visible) mesh.visible = visible;
    if (visible) geometryOf.add(built.modelIndex);
  }

  const drawn = new Set();
  for (const entry of meshes) {
    const [sx, sy] = sizeOf(entry.model);
    const visible = Math.max(sx, sy) / perPixel >= DETAIL_MIN_PX;
    if (entry.mesh.visible !== visible) {
      entry.mesh.visible = visible;
      for (const index of entry.instances) showEdges(index);
    }
    // The box stands in for the model again as soon as the model is too small to be worth
    // drawing, and steps back out of the way when it is not. Standing in, it is drawn as solidly
    // as the model was, so what a resource looks like does not change as it crosses the threshold.
    if (entry.modelDrawn) {
      const fills = !geometryOf.has(entry.modelIndex);
      if (entry.mesh.material.visible !== fills) entry.mesh.material.visible = fills;
      // Opacity is a uniform, read every frame: no version bump, which would re-key the pipeline.
      if (entry.standsIn !== fills) {
        entry.standsIn = fills;
        entry.mesh.material.opacity = boxOpacity(entry);
      }
    }
    entry.detailVisible = visible;
    for (const overlay of entry.overlays ?? []) {
      const wanted = visible && (!overlay.userData.planOnly || planView === true);
      if (overlay.visible !== wanted) overlay.visible = wanted;
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

/** Show or hide the faint copies of a grid's marks, which belong to a plan view alone. */
function showThroughMarks(group) {
  group.traverse((o) => {
    if (o.userData.mark === "through") o.visible = planView === true;
  });
}

export function updateEdgeMode() {
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
  renderModeDirty = false;
  for (const mark of gridMarks) showThroughMarks(mark);
  setRenderMode(plan);
}
