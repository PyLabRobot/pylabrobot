// Every resource as a box: one instanced mesh per model, with the outlines, the vessel cavities,
// the filter and plan discs that ride on it. A drawing is kept across rebuilds while unchanged.

import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";

import {
  EDGE_WIDTH_3D,
  FILTER,
  FILTER_BELOW_COLLAR,
  FILTER_WIDTH_UNMEASURED,
  MOVING_PARTS,
  RESOURCE_COLORS,
  structureEdgeStyle,
  TIP_PLAN_FILL,
  VESSEL_EMPTY,
  VESSEL_RIM,
  VESSEL_WALL,
  VESSEL_WALL_OPACITY,
} from "./constants.js";
import {
  boxMatrix,
  clearDrawn,
  collectEnclosedModels,
  disposeOwned,
  edgeOf,
  enclosureDepth,
  hasEnclosedDescendant,
  isCarrier,
  isVessel,
  isVisible,
  meshes,
  own,
  placeInstance,
  placementOf,
  placeParts,
  remember,
  vesselOf,
} from "./drawn.js";
import { view } from "./renderer.js";
import { sizeOf, world } from "./world.js";

// Unit primitives, shared by every model of the same shape and scaled per instance.

const BOX = new THREE.BoxGeometry(1, 1, 1);

const CYL = new THREE.CylinderGeometry(0.5, 0.5, 1, 20).rotateX(Math.PI / 2);

// Open at both ends. A shaft is a length of tube: the bottom is where a tip goes on and the top is
// where the channel carries on, so capping either reads as a solid slug hanging off the head.
const TUBE = new THREE.CylinderGeometry(0.5, 0.5, 1, 20, 1, true).rotateX(Math.PI / 2);

// Flat in XY, facing up: a filter lies across a tip standing on the deck.
const DISC = new THREE.CircleGeometry(0.5, 32);

// A carrier's floor. Unit-sized like the rest: `placeInstance` supplies the real size through the
// instance matrix, so a pre-sized plane would be scaled by its own dimensions a second time.
const PLANE = new THREE.PlaneGeometry(1, 1);

// Above this many instances of one model, outlining each stops being cheap.
const EDGE_LIMIT = 160;

// The unit edges of each shape, as segment endpoints: what every instance's outline is scaled from.
const unitEdges = new Map();

function edgesOf(geometry) {
  let edges = unitEdges.get(geometry);
  if (!edges) {
    const boxEdges = new THREE.EdgesGeometry(geometry);
    edges = boxEdges.getAttribute("position").array;
    boxEdges.dispose();
    unitEdges.set(geometry, edges);
  }
  return edges;
}

// A resource says what shape it is through `cross_section_type`. A tip spot does not serialize
// one, though it is plainly round, so it is special-cased here; upstream it should declare the
// field the way a well does, and this line can go.
function geometryFor(model) {
  // A shaft is open at both ends; anything else round is a vessel or a spot, which is not.
  if (model.category === "tip_mounting_shaft") return TUBE;
  return model.cross_section_type === "circle" || model.category === "tip_spot" ? CYL : BOX;
}

// The outline a resource drops to in an axis view, as unit segment endpoints scaled per instance.
function ringFootprint(corners) {
  const points = [];
  for (let corner = 0; corner < corners; corner++) {
    // Square from the diagonals, circle from a fine enough ring: the same walk either way.
    const from = ((corner + 0.5) / corners) * Math.PI * 2;
    const to = ((corner + 1.5) / corners) * Math.PI * 2;
    const reach = corners === 4 ? Math.SQRT1_2 : 0.5;
    points.push(
      Math.cos(from) * reach,
      Math.sin(from) * reach,
      -0.5,
      Math.cos(to) * reach,
      Math.sin(to) * reach,
      -0.5,
    );
  }
  return new Float32Array(points);
}

const SQUARE_FOOTPRINT = ringFootprint(4);

const ROUND_FOOTPRINT = ringFootprint(20);

const footprintFor = (model) => (geometryFor(model) === BOX ? SQUARE_FOOTPRINT : ROUND_FOOTPRINT);

export const colorFor = (model) =>
  model.appearance?.color ?? RESOURCE_COLORS[model.category] ?? RESOURCE_COLORS.default;

/**
 * One instanced part riding every instance of a drawing, standing at `at` in each of them.
 * `emptyOnly` marks a part drawn only while the resource holds nothing, such as a spot's cavity.
 */
function makeOverlay(entry, geometry, material, at, emptyOnly = false) {
  const overlay = new THREE.InstancedMesh(geometry, material, entry.mesh.count);
  overlay.userData.lit = material;
  overlay.userData.at = at;
  overlay.userData.emptyOnly = emptyOnly;
  own(entry, overlay, material);
  entry.overlays.push(overlay);
  return overlay;
}

/**
 * Everything drawn for one model: its box, an outline per instance, and the parts that ride on it.
 * Made for a count of instances and nothing about which: `placeDrawing` puts those in, per scene.
 */
function makeDrawing(model, count, encloses, edgeDepth) {
  const [sx, sy, sz] = sizeOf(model);
  const vessel = isVessel(model);

  const material = new THREE.MeshStandardMaterial({
    color: colorFor(model),
    roughness: 0.68,
    metalness: 0.0,
    transparent: encloses,
    opacity: encloses ? 0.26 : 1.0,
    depthWrite: !encloses,
    side: encloses ? THREE.BackSide : THREE.FrontSide,
  });

  if (vessel) material.color.setHex(VESSEL_RIM);
  material.userData.lit = material;

  if (MOVING_PARTS.has(model.category)) material.visible = false;

  // Culled by the sphere three works out over the instances, as every instanced mesh, overlay
  // and outline here is: what is off screen is not submitted. A move or a hide drops the sphere.
  const mesh = new THREE.InstancedMesh(geometryFor(model), material, count);
  const entry = {
    mesh,
    model,
    isVessel: vessel,
    overlays: /** @type {any[]} */ ([]),
    // One line object outlining every instance, or null: see below.
    edges: /** @type {any} */ (null),
    // The cavity whose colour tracks what the vessel holds; the filter discs a tip's file sizes.
    vessel: /** @type {any} */ (null),
    filterDiscs: /** @type {any} */ (null),
  };
  own(entry, mesh, material);

  // Stroked, as the existing visualizer strokes every resource, up to a count past which a
  // thousand wells read as haze. A travelling part draws its own frame and gets no box outline.
  if (!MOVING_PARTS.has(model.category) && count <= EDGE_LIMIT) {
    const style = structureEdgeStyle(edgeDepth);
    const edgeMaterial = new THREE.Line2NodeMaterial({
      color: style.color,
      transparent: true,
      opacity: style.opacity,
      linewidth: EDGE_WIDTH_3D,
      worldUnits: false,
    });
    // Every instance's edges in one geometry, and its footprint in a second one to swap in for a
    // plan view: one draw per model, an instance's own segments rewritten when it moves or hides.
    const sets = [edgesOf(geometryFor(model)), footprintFor(model)].map((unit) => {
      const geometry = new LineSegmentsGeometry();
      geometry.setPositions(new Float32Array(unit.length * count));
      own(entry, geometry);
      return { unit, geometry };
    });
    const edges = new LineSegments2(sets[0].geometry, edgeMaterial);
    // One object for every instance, parked ones included: three's sphere over them all says
    // nothing worth testing, so it is submitted whenever the model is.
    edges.frustumCulled = false;
    edges.userData.sets = sets;
    edges.userData.shown = new Uint8Array(count); // per slot, whether its segments are in view
    edges.userData.baseOpacity = style.opacity;
    edges.userData.baseColor = style.color.clone();
    own(entry, edgeMaterial);
    entry.edges = edges;
  }

  // A carrier's base is solid, so looking into one should stop at its floor rather than carrying
  // on through to the deck. The shell stays see-through; only the bottom face is filled in, a
  // hair above its own base, or it fights the deck surface it stands on for depth.
  if (encloses && isCarrier(model)) {
    const floorMaterial = new THREE.MeshStandardMaterial({
      color: colorFor(model),
      roughness: 0.7,
    });
    makeOverlay(entry, PLANE, floorMaterial, [sx, sy, 1, sx / 2, sy / 2, 0.3]);
  }

  if (vessel) {
    // The wall, standing outside the cavity. Grown rather than inset, because the box IS the
    // cavity - and grown rather than left as the box itself, because a box and a cavity on the
    // same plane are two surfaces at the same depth, which is what made the side of every well
    // shimmer. Nothing is coplanar with anything now.
    const wallMaterial = new THREE.MeshStandardMaterial({
      color: VESSEL_RIM,
      roughness: 0.6,
      transparent: true,
      opacity: VESSEL_WALL_OPACITY,
    });
    const wallAt = [sx + 2 * VESSEL_WALL, sy + 2 * VESSEL_WALL, sz, sx / 2, sy / 2, sz / 2];
    const wall = makeOverlay(entry, geometryFor(model), wallMaterial, wallAt);
    wall.userData.behind = true; // painted before the cavity it surrounds

    // The cavity IS the box. A container's size is what it holds, and the material around it
    // stands outside that - so the inside fills the resource's own extent exactly, and a wall
    // is never drawn within it. Drawn inset, as this was, the walls were inside the box, which
    // is the opposite of what the box means. A hair taller only, so that from directly above it
    // does not fight the box's own top face for depth.
    //
    // Flagged transparent although it is fully opaque, so that it sits in the same pass as the
    // wall around it. Three draws every transparent object after every opaque one whatever the
    // render order says, so an opaque cavity inside a see-through wall is painted first and
    // then covered by the wall's own top face - which is what hid the well from above.
    const innerMaterial = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      roughness: 0.55,
      transparent: true,
      opacity: 1,
    });
    const innerAt = [sx, sy, sz * 1.02, sx / 2, sy / 2, (sz * 1.02) / 2];
    // A spot holding a tip shows the tip, and the white of an empty hole would lie across its
    // bore - over the filter, which sits just below the spot.
    const spot = model.category === "tip_spot";
    const inner = makeOverlay(entry, geometryFor(model), innerMaterial, innerAt, spot);
    const white = new THREE.Color(VESSEL_EMPTY);
    for (let slot = 0; slot < count; slot++) inner.setColorAt(slot, white);
    inner.instanceColor.needsUpdate = true;
    entry.vessel = inner;
  }

  // A filter in every tip of one model: a white disc across the bore, `FILTER_BELOW_COLLAR` below
  // the collar. One instanced mesh however many tips there are, so a rack of filtered tips costs
  // one draw. Its width is a guess until the tip's own file says what the bore is at that height.
  if (model.category === "tip" && model.has_filter && Number.isFinite(model.collar_height)) {
    const filterMaterial = new THREE.MeshBasicMaterial({ color: FILTER, side: THREE.DoubleSide });
    // A tip stands on its bottom end, so its top is its length and the collar hangs from there.
    const z = sz - (model.collar_height + FILTER_BELOW_COLLAR);
    const width = sx * FILTER_WIDTH_UNMEASURED;
    const at = [width, width, 1, sx / 2, sy / 2, z];
    makeOverlay(entry, DISC, filterMaterial, at);
    entry.filterDiscs = { at, z, cx: sx / 2, cy: sy / 2 };
  }

  // The green disc that says a spot is filled, lying across the top of every tip of one model.
  //
  // From directly above a tip is a circle, and the only thing worth reading off it is that
  // something is standing there - which is what the existing visualizer says with a green circle.
  // Said with a disc rather than by colouring the tip, it is unlit, so the colour lands on the
  // value asked for rather than on whatever the lighting makes of it; and it is one instanced mesh
  // however many tips there are, so a rack of them costs one draw. A tip is only a resource while
  // it is in its spot, so there is one of these for every tip still standing and none for a spot
  // that has been used.
  //
  // Flagged transparent although it is fully opaque, for the reason the cavity above is: the
  // spot's own rim is in the transparent pass. Left opaque, this was painted first and the rim it
  // is meant to fill then covered it, which is a green disc nobody ever saw.
  if (model.category === "tip") {
    const planMaterial = new THREE.MeshBasicMaterial({
      color: TIP_PLAN_FILL,
      transparent: true,
      opacity: 1,
    });
    // Level with the top of the tip, which is the first thing the eye meets looking down at it.
    const disc = makeOverlay(entry, DISC, planMaterial, [sx, sy, 1, sx / 2, sy / 2, sz]);
    // A plan view alone. The mode change and the rule that culls small things share the switch.
    disc.userData.planOnly = true;
    disc.visible = false;
  }
  return entry;
}

// Where a hidden instance's segments go: past every far plane, so the GPU clips them unrun. A
// zero-length segment is no hiding place - the line shader normalises its direction.
const PARKED_Z = -1e7;

const _corner = new THREE.Vector3();

/** Write one instance's outline into its drawing's line buffers, at `matrix` or parked. */
function writeEdges(entry, slot, matrix) {
  for (const { unit, geometry } of entry.edges.userData.sets) {
    const buffer = geometry.attributes.instanceStart.data;
    const at = slot * unit.length;
    for (let i = 0; i < unit.length; i += 3) {
      if (matrix) _corner.set(unit[i], unit[i + 1], unit[i + 2]).applyMatrix4(matrix);
      else _corner.set(i % 6 ? 1 : 0, 0, PARKED_Z);
      buffer.array[at + i] = _corner.x;
      buffer.array[at + i + 1] = _corner.y;
      buffer.array[at + i + 2] = _corner.z;
    }
    buffer.needsUpdate = true;
  }
}

/** Whether an instance's outline is drawn: not while its box is culled or switched off. */
function edgeShown(index) {
  return placementOf[index].mesh.visible && isVisible(index);
}

/** Put an instance's outline where it now stands, or park it: what a move calls. */
export function placeEdges(index) {
  const entry = edgeOf.get(index);
  if (!entry) return;
  const slot = placementOf[index].slot;
  const shown = edgeShown(index);
  entry.edges.userData.shown[slot] = shown ? 1 : 0;
  const [sx, sy, sz] = sizeOf(entry.model);
  writeEdges(entry, slot, shown ? boxMatrix(world.matrices[index], sx, sy, sz) : null);
}

/** Park or restore an instance's outline as its box is culled, switched off or brought back. */
export function showEdges(index) {
  const entry = edgeOf.get(index);
  if (!entry) return;
  const slot = placementOf[index].slot;
  if (entry.edges.userData.shown[slot] === (edgeShown(index) ? 1 : 0)) return;
  placeEdges(index);
}

/** Put every instance of a drawing where the tree has it, and register each part by resource. */
function placeDrawing(entry, touched) {
  const { mesh, model, instances } = entry;
  const [sx, sy, sz] = sizeOf(model);
  mesh.userData.instances = instances;
  instances.forEach((index, slot) => {
    placeInstance(mesh, slot, world.matrices[index], sx, sy, sz);
    placementOf[index] = { mesh, slot };
    for (const overlay of entry.overlays) {
      remember(index, overlay, slot, overlay.userData.at, overlay.userData.emptyOnly);
    }
    if (entry.vessel) vesselOf.set(index, { mesh: entry.vessel, slot, model });
    placeParts(index, touched);
    if (entry.edges) {
      edgeOf.set(index, entry);
      placeEdges(index);
    }
  });
  touched.add(mesh);
}

const drawnObjects = (entry) => [
  entry.mesh,
  ...entry.overlays,
  ...(entry.edges ? [entry.edges] : []),
];

// What is baked into a drawing's objects: the model, the resources standing on it in order,
// whether it encloses anything and how deeply it is enclosed. The same key, and they still serve.
function drawingKey(model, instances, encloses) {
  const names = instances.map((index) => world.names[index]).join("\n");
  return `${JSON.stringify(model)}\n${encloses}\n${enclosureDepth(instances[0])}\n${names}`;
}

// A scene arrives whole whenever the tree changes shape, and most of it was on screen already.
// A drawing with the same key is kept, objects and all: a new mesh costs a shader state to draw.
export function buildMeshes() {
  const kept = new Map(meshes.map((entry) => [entry.key, entry]));
  clearDrawn();

  const byModel = new Map();
  for (let i = 0; i < world.names.length; i++) {
    const m = world.modelOf[i];
    if (!byModel.has(m)) byModel.set(m, []);
    byModel.get(m).push(i);
  }

  const touched = new Set();
  for (const [modelIndex, instances] of byModel) {
    const model = world.models[modelIndex];

    // A resource that holds something is an enclosure: other resources, read off the tree, or
    // liquid, read off its own capacity. Neither test names a resource type.
    const encloses =
      !isVessel(model) &&
      (instances.some((i) => world.childrenOf[i].length > 0) ||
        model.max_volume !== undefined ||
        MOVING_PARTS.has(model.category));

    const key = drawingKey(model, instances, encloses);
    let entry = kept.get(key);
    if (entry) {
      kept.delete(key);
    } else {
      entry = makeDrawing(model, instances.length, encloses, enclosureDepth(instances[0]));
      entry.key = key;
      for (const object of drawnObjects(entry)) view.add(object);
    }

    // What the tree makes of this model, worked out afresh: a new scene renumbers every model.
    entry.modelIndex = modelIndex;
    entry.instances = instances;
    // But only the innermost enclosures are filled. A well in a plate on a holder on a carrier on
    // a deck in a device in a facility sits under six translucent shells, and six layers at 0.3
    // opacity leave about a tenth of the contrast underneath. So anything that holds another
    // enclosure is drawn as its outline alone, and only the level you are actually looking into
    // keeps a fill.
    entry.holdsEnclosure = encloses && instances.some((i) => hasEnclosedDescendant(i));
    // Which enclosure models sit inside this one. An outline is only the right answer while its
    // contents are actually being drawn; once they are culled the outline has nothing to frame.
    const enclosedModels = new Set();
    for (const i of instances) collectEnclosedModels(i, enclosedModels);
    entry.enclosedModels = [...enclosedModels];
    // Set once this model's declared .glb has arrived and been placed, which every scene does
    // again; and whether the box is holding the place of a model too small to be worth drawing.
    entry.modelDrawn = false;
    entry.standsIn = false;
    placeDrawing(entry, touched);
    meshes.push(entry);
  }
  for (const mesh of touched) {
    mesh.instanceMatrix.needsUpdate = true;
    mesh.boundingSphere = null;
  }

  // What has no place in this scene goes, overlays and outlines with it: both rules that show and
  // hide an overlay walk `meshes`, and one left in the view keeps whatever it was last told.
  for (const entry of kept.values()) {
    for (const object of drawnObjects(entry)) view.remove(object);
    disposeOwned(entry);
  }
}

/** Twice the nearest a surface of `object` comes to the axis through (cx, cy), in the plane z. */
function boreWidthAt(object, z, cx, cy) {
  let nearest = Number.POSITIVE_INFINITY;
  const corners = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()];
  object.traverse((o) => {
    if (!o.isMesh) return;
    const position = o.geometry.attributes.position;
    const index = o.geometry.index;
    const count = index ? index.count : position.count;
    for (let i = 0; i + 2 < count; i += 3) {
      for (let k = 0; k < 3; k++) {
        const vertex = index ? index.getX(i + k) : i + k;
        corners[k].fromBufferAttribute(position, vertex).applyMatrix4(o.matrixWorld);
      }
      // Where each edge crosses the plane, if it does.
      for (let k = 0; k < 3; k++) {
        const p = corners[k];
        const q = corners[(k + 1) % 3];
        if (p.z === q.z || (p.z - z) * (q.z - z) > 0) continue;
        const t = (z - p.z) / (q.z - p.z);
        const r = Math.hypot(p.x + t * (q.x - p.x) - cx, p.y + t * (q.y - p.y) - cy);
        if (r < nearest) nearest = r;
      }
    }
  });
  return Number.isFinite(nearest) ? 2 * nearest : null;
}

// The bore a file has at a disc's height, by file, scale, axis and height: a file never changes,
// and every scene asks again for every tip model.
const boreWidths = new Map();

/**
 * Size a model's filter discs to the bore its own file has at their height, once the file is here.
 * The file is in the tip's frame, so the plane the disc lies in cuts the tip's inner wall at the
 * nearest distance from the axis.
 */
export function fitFilterDiscs(modelIndex, scene, scale, up) {
  const entry = meshes.find((e) => e.modelIndex === modelIndex);
  const discs = entry?.filterDiscs;
  if (!discs) return;
  const key = [entry.model.mesh.url, scale, up, discs.z, discs.cx, discs.cy].join("\n");
  let width = boreWidths.get(key);
  if (width === undefined) {
    scene.scale.setScalar(scale);
    if (up === "Y") scene.rotation.x = Math.PI / 2;
    scene.updateMatrixWorld(true);
    width = boreWidthAt(scene, discs.z, discs.cx, discs.cy);
    boreWidths.set(key, width);
  }
  if (width === null) return;
  const touched = new Set();
  discs.at[0] = width;
  discs.at[1] = width;
  for (const index of entry.instances) placeParts(index, touched);
  for (const mesh of touched) {
    mesh.instanceMatrix.needsUpdate = true;
    mesh.boundingSphere = null;
  }
}
