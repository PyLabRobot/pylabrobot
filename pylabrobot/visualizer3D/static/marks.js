// What is drawn beside the resources: the arms' frames, the grids and their numbers, the
// reference and grip marks, the origin, the floor, and the channel halos.

import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";

import {
  ARM_COLOR,
  ARM_EDGE,
  ARM_EDGE_WIDTH_3D,
  ARM_INSET_X,
  ARM_INSET_Y,
  ARM_OPACITY,
  ARM_REFERENCE_OPACITY,
  CHANNEL_RAMP,
  CHANNEL_RAMP_DARK_INK_STEPS,
  GRIP_MARK_OPACITY,
  GRIP_MARK_OPENING,
  GRIP_MARK_WIDTH,
  HALO_CENTRED_AT,
  HALO_INK,
  HALO_MARK_PX,
  HALO_OFFSET_PX,
  HALO_PX,
  HALO_TIPPED_BACKGROUND,
  MOVING_PARTS,
  NO_REFERENCE_MARK,
  REFERENCE_DROP,
  REFERENCE_LINE,
  REFERENCE_WIDTH,
} from "./constants.js";
import { carried, disposeOwned, OVERLAY_ORDER, own, paintOrderOf } from "./drawn.js";
import { hexOf, niceNumber } from "./format.js";
import { camera, controls, mmPerPixel, view, viewportEl } from "./renderer.js";
import { modelOf, sizeOf, world } from "./world.js";

// A resource that lays things out on a repeated grid says so in its model, and the viewer draws
// whatever it is told: how many, how far apart, where the first one sits, how to label them.
// Nothing here knows what a rail is, so a deck with slots or a nest with positions draws the same
// way the day it declares one.

export let gridMarks = [];

// Every rail number in the scene. They are one draw call each and the largest per-device cost the
// viewer has, so they are the first thing to stop drawing once they are too small to read.
export let gridLabels = [];

export let surfaces = [];

export let arms = []; // { group, index, referenceOffset, targetX, currentX }

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

// Bands a capability can reach across a surface, drawn as a pair of lines with the reach labelled
// at the near edge. Blue keeps them apart from the grey position grid they cross.
const BAND_COLOR = 0x8ba7c6;

export const GRID_LABEL_MM = 30; // label height in deck millimetres

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
    new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthTest: false }),
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

// A resource that says where the device's x refers to gets that point marked, whether or not it is
// an arm. An arm draws its own inside the group that carries it; everything else is marked here -
// the autoload's sled being the case that prompted it, since the drive reports its carrier-handling
// wheel rather than the sled's own corner.
export let referenceMarks = [];

// A moving part is drawn as three objects rather than one instanced box - a reference line, the
// carriage, and the stroke that bounds it - so they need ordering against each other as well as
// against the scene. These are the three offsets, applied to whatever order the part is at: the
// line reads under the carriage, and the stroke over it. They match the offsets a static resource
// uses for its own surface and edge, so the two schemes interleave.
export const ARM_LINE_OFFSET = -0.5;

export const ARM_FRAME_OFFSET = 0;

export const ARM_OUTLINE_OFFSET = 1;

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
    for (const [x, y] of [
      [arm, half],
      [arm, -half],
    ]) {
      points.push(
        new THREE.Vector2(
          x * Math.cos(turn) - y * Math.sin(turn),
          x * Math.sin(turn) + y * Math.cos(turn),
        ),
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
      color: REFERENCE_LINE,
      transparent: true,
      opacity: GRIP_MARK_OPACITY,
      depthTest: false,
      side: THREE.DoubleSide,
    }),
  );
  own(buildReferenceMarks, plane.geometry, plane.material);
  plane.frustumCulled = false;
  plane.renderOrder = OVERLAY_ORDER + 5;
  plane.matrixAutoUpdate = false;
  // Lying flat, centred where the tool says it is programmed against. Read from the tool rather
  // than taken as the far end of its own box: the two agree on this gripper, and only because its
  // box is its link length - a tool that grips somewhere other than its tip would have the mark
  // drawn at the tip, which is the one place it is not.
  //
  // The tool states that point from its joint, not from its own origin, so the joint is added back:
  // left out, the mark stands the joint's offset away from where the jaws close.
  const joint = model.proximal_joint;
  plane.userData.local = new THREE.Matrix4().makeTranslation(
    joint.x + tcp.x,
    joint.y + tcp.y,
    joint.z + tcp.z,
  );
  plane.matrix.multiplyMatrices(world.matrices[index], plane.userData.local);
  plane.matrixWorldNeedsUpdate = true;
  view.add(plane);
  referenceMarks.push({ plane, index });
}

export function buildReferenceMarks() {
  for (const mark of referenceMarks) view.remove(mark.plane);
  disposeOwned(buildReferenceMarks);
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
      ? (model.reference_point.z ?? 0)
      : deckZ === null
        ? 0
        : deckZ - world.matrices[index].elements[14];
    const plane = new THREE.Mesh(
      new THREE.PlaneGeometry(REFERENCE_WIDTH, sy),
      new THREE.MeshBasicMaterial({
        color: REFERENCE_LINE,
        transparent: true,
        opacity: ARM_REFERENCE_OPACITY,
        depthTest: false,
        side: THREE.DoubleSide,
      }),
    );
    own(buildReferenceMarks, plane.geometry, plane.material);
    plane.frustumCulled = false;
    plane.renderOrder = paintOrderOf(index) + REFERENCE_MARK_OFFSET;
    plane.matrixAutoUpdate = false;
    plane.userData.local = new THREE.Matrix4().makeTranslation(referenceOffset(model), sy / 2, sz);
    plane.matrix.multiplyMatrices(world.matrices[index], plane.userData.local);
    plane.matrixWorldNeedsUpdate = true;
    view.add(plane);
    referenceMarks.push({ plane, index });
  }
}

/**
 * The opening in a moving part's frame, in the part's own frame: left, right, front, back in mm.
 *
 * Declared by the part when it knows its own geometry. A declared width is centred on the part
 * unless the part also says how far its right edge sits from the end, which is the case for an
 * opening that is deliberately off-centre.
 */
export function armWindow(model) {
  const [sx, sy] = sizeOf(model);
  const window = model.window ?? {};
  const insetY = window.inset_y ?? ARM_INSET_Y;
  let left;
  let right;
  if (window.width === undefined) {
    left = ARM_INSET_X;
    right = sx - ARM_INSET_X;
  } else if (window.right_margin === undefined) {
    left = (sx - window.width) / 2;
    right = left + window.width;
  } else {
    right = sx - window.right_margin;
    left = right - window.width;
  }
  return [left, right, insetY, sy - insetY];
}

/** Where an arm stands in the model: what its group is drawn from, and where its glide ends. */
export function armPose(index) {
  const parent = world.parentOf[index];
  const local = world.local.slice(index * 6, index * 6 + 6);
  return {
    parentMatrix: parent >= 0 ? world.matrices[parent].clone() : new THREE.Matrix4(),
    local,
    currentX: local[0],
    targetX: local[0],
  };
}

export function buildArms() {
  for (const arm of arms) view.remove(arm.group);
  disposeOwned(buildArms);
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
    const [holeLeft, holeRight, holeFront, holeBack] = armWindow(model);
    if (holeRight > holeLeft && holeBack > holeFront) {
      const hole = new THREE.Path();
      hole.moveTo(holeLeft, holeFront);
      hole.lineTo(holeLeft, holeBack);
      hole.lineTo(holeRight, holeBack);
      hole.lineTo(holeRight, holeFront);
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
      }),
    );
    frame.renderOrder = paintOrderOf(index) + ARM_FRAME_OFFSET;
    own(buildArms, solid, frame.material);

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
    own(buildArms, outlineGeometry, outlineMaterial);
    const outline = new LineSegments2(outlineGeometry, outlineMaterial);
    outline.frustumCulled = false;
    outline.renderOrder = paintOrderOf(index) + ARM_OUTLINE_OFFSET;

    const offset = referenceOffset(model);
    // Along the Y its reference point reaches, when the arm declares it, else the arm's whole depth.
    const [reachFront, reachBack] = model.reference_point?.y_range ?? [0, sy];
    const line = new THREE.Mesh(
      new THREE.PlaneGeometry(REFERENCE_WIDTH, reachBack - reachFront),
      new THREE.MeshBasicMaterial({
        color: REFERENCE_LINE,
        transparent: true,
        opacity: ARM_REFERENCE_OPACITY,
        depthTest: false,
      }),
    );
    line.position.set(offset, (reachFront + reachBack) / 2, -REFERENCE_DROP);
    own(buildArms, line.geometry, line.material);
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

    arms.push({ group, frame, outline, line, index, referenceOffset: offset, ...armPose(index) });
  }
}

export function buildGridMarks() {
  for (const mark of gridMarks) view.remove(mark);
  disposeOwned(buildGridMarks);
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
        surfaceMaterial,
      );
      // The deck's own top face, so it paints just after the deck's box and just before whatever
      // stands on it. On the same height scale as everything else - left on the old tree-depth scale
      // it sorted below the box it belongs to, and the box covered it.
      surface.renderOrder = paintOrderOf(index) + 0.25;
      surface.userData.lit = surfaceMaterial;
      own(buildGridMarks, surface.geometry, surfaceMaterial);
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
        own(buildGridMarks, sprite.geometry, sprite.material, sprite.material.map);
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
        new THREE.LineBasicMaterial({ color: BAND_COLOR, depthTest: false }),
      );
      bandLines.renderOrder = paintOrderOf(index) + 0.5;
      own(buildGridMarks, bandGeometry, bandLines.material);
      group.add(bandLines);
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
    const marks = new THREE.LineSegments(
      geometry,
      // Opaque, so it sorts with everything else: a transparent line renders after all opaque
      // geometry no matter its render order, which is what kept these on top of the carriers.
      new THREE.LineBasicMaterial({ color: GRID_LINE, depthTest: false }),
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
        color: GRID_LINE,
        transparent: true,
        opacity: GRID_GHOST_OPACITY,
        depthTest: false,
      }),
    );
    showThrough.userData.mark = "through";
    showThrough.renderOrder = OVERLAY_ORDER - 1;
    group.add(showThrough);
    own(buildGridMarks, geometry, marks.material, showThrough.material);

    group.userData.owner = index;
    view.add(group);
    gridMarks.push(group);
  }
}

// The facility's origin, drawn as a triad. Everything in the scene is measured from here, so it
// should be findable without hunting: the coordinate tool, the deck maths and the readouts all
// resolve against this point.
let originMarker = null;

export const AXIS_COLORS = { x: 0xdc3545, y: 0x198754, z: 0x1a4b8c };

// A small magenta sphere on every resource's origin, for checking where a resource is measured
// from. Off until the toolbar button turns it on.
//
// One InstancedMesh, not a mesh each: measured on a 316-resource Prep, instancing costs one draw
// call and 0.9% of a frame, where a mesh per resource cost 215 draw calls and 37%.
const ORIGIN_DOT_RADIUS = 1.0; // mm

const ORIGIN_DOT_COLOR = 0xff00ff;

let originDots = null;

export let showOriginDots = false;

export function buildOriginDots() {
  if (originDots) {
    view.remove(originDots);
    originDots.geometry.dispose();
    originDots.material.dispose();
    originDots = null;
  }
  if (!showOriginDots || !world) return;
  const count = world.names.length;
  const mesh = new THREE.InstancedMesh(
    new THREE.SphereGeometry(ORIGIN_DOT_RADIUS, 8, 6),
    new THREE.MeshBasicMaterial({ color: ORIGIN_DOT_COLOR }),
    count,
  );
  mesh.frustumCulled = false;
  const at = new THREE.Vector3();
  const matrix = new THREE.Matrix4();
  for (let i = 0; i < count; i++) {
    at.setFromMatrixPosition(world.matrices[i]);
    mesh.setMatrixAt(i, matrix.makeTranslation(at.x, at.y, at.z));
  }
  mesh.instanceMatrix.needsUpdate = true;
  originDots = mesh;
  view.add(originDots);
}

export function setOriginDots(on) {
  showOriginDots = on;
  buildOriginDots();
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

export function updateOrigin() {
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
      depth,
    )
    .unproject(camera);
}

export function buildOrigin() {
  if (originMarker) view.remove(originMarker);
  disposeOwned(buildOrigin);
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
    const head = new THREE.Mesh(new THREE.ConeGeometry(shaft * 2.6, length - body, 14), material);
    // Cylinders and cones point along +Y; turn each onto its own axis.
    const quaternion = new THREE.Quaternion().setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      direction,
    );
    bar.quaternion.copy(quaternion);
    head.quaternion.copy(quaternion);
    bar.position.copy(direction).multiplyScalar(body / 2);
    head.position.copy(direction).multiplyScalar(body + (length - body) / 2);
    originMarker.add(bar, head);
    own(buildOrigin, material, bar.geometry, head.geometry);
  }

  const centre = new THREE.Mesh(
    new THREE.SphereGeometry(shaft * 2.2, 18, 14),
    new THREE.MeshStandardMaterial({ color: 0x1a1f22, roughness: 0.4 }),
  );
  originMarker.add(centre);
  own(buildOrigin, centre.geometry, centre.material);

  // A ring flat on the floor, so the origin is still findable from directly above.
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(length * 0.15, length * 0.2, 48),
    new THREE.MeshBasicMaterial({
      color: 0x1a4b8c,
      transparent: true,
      opacity: 0.6,
      side: THREE.DoubleSide,
    }),
  );
  originMarker.add(ring);
  own(buildOrigin, ring.geometry, ring.material);

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

// A halo on each pipetting channel: a disc facing the camera, held at HALO_PX across whatever the
// zoom. Sprites, one a channel, rather than one instanced quad:
// a sprite faces the camera on its own and is placed through its own transform, where an
// instanced quad needs its matrix buffer rewritten every frame, and on the WebGPU backend a buffer
// rewritten every frame drew on some frames and not others. Drawn in the overlay band, so a halo
// sits over the arm's frame and whatever the channel is above without any of them being reordered.
//
// What a halo says: its colour is the channel's place in CHANNEL_RAMP, its number is drawn in it,
// and it is filled while the channel's mounting shaft holds a tip and hollow while it does not.
// On from the start; the rail button turns them off and on.
export let halos = null;

export let showHalos = true;

const HALO_TEXTURE_PX = 96;

/** One texture per look, kept: a look is a number, a ramp step and whether it is filled. */
const haloTextures = new Map();

function haloTextureFor(label, step, filled) {
  const key = `${label}|${step}|${filled}`;
  const kept = haloTextures.get(key);
  if (kept) return kept;
  const canvas = document.createElement("canvas");
  canvas.width = HALO_TEXTURE_PX;
  canvas.height = HALO_TEXTURE_PX;
  const context = canvas.getContext("2d");
  const colour = hexOf(CHANNEL_RAMP[step]);
  const mid = HALO_TEXTURE_PX / 2;
  const radius = mid - 6;
  // Two zones. The background is white with an empty shaft and light green with a tip in it, so
  // the change reads from across the deck. The coin at the centre carries the channel's colour:
  // a solid coin with a tip, a ring without.
  context.beginPath();
  context.arc(mid, mid, radius, 0, Math.PI * 2);
  // A surface ring around every disc, so two halos that overlap still read as two.
  context.lineWidth = 4;
  context.strokeStyle = "rgba(255,255,255,0.9)";
  context.fillStyle = filled ? HALO_TIPPED_BACKGROUND : "rgba(255,255,255,0.88)";
  context.fill();
  context.stroke();
  const coin = radius - 12;
  context.beginPath();
  context.arc(mid, mid, coin, 0, Math.PI * 2);
  if (filled) {
    context.fillStyle = colour;
    context.fill();
  } else {
    context.lineWidth = 6;
    context.strokeStyle = colour;
    context.stroke();
  }
  // Ink, never the series colour, on a solid coin; the ramp colour itself inside a ring.
  context.fillStyle = filled ? (step < CHANNEL_RAMP_DARK_INK_STEPS ? HALO_INK : "#ffffff") : colour;
  context.font = `bold ${label.length > 1 ? 34 : 42}px ui-monospace, Menlo, monospace`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(label, mid, mid + 2);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  haloTextures.set(key, texture);
  return texture;
}

/** A sprite material over a kept texture: made per build, so the build can let it go. */
function haloMaterial(map, color = 0xffffff) {
  return new THREE.SpriteMaterial({
    map,
    color,
    transparent: true,
    depthTest: false,
    depthWrite: false,
  });
}

// The line from a channel to its disc: a unit line along x, placed by its transform alone, so the
// frame-by-frame update touches no buffer. One material per ramp step, kept like the discs' are.
const LEADER_GEOMETRY = new THREE.BufferGeometry().setFromPoints([
  new THREE.Vector3(0, 0, 0),
  new THREE.Vector3(1, 0, 0),
]);

function leaderMaterial(step) {
  return new THREE.LineBasicMaterial({
    color: CHANNEL_RAMP[step],
    transparent: true,
    opacity: 0.9,
    depthTest: false,
    depthWrite: false,
  });
}

// The glow on the channel itself: one soft white disc, tinted per ramp step by its material.
function markTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const context = canvas.getContext("2d");
  const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
  gradient.addColorStop(0, "rgba(255,255,255,1)");
  gradient.addColorStop(0.5, "rgba(255,255,255,0.7)");
  gradient.addColorStop(1, "rgba(255,255,255,0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 64, 64);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

const MARK_TEXTURE = markTexture();

/** Whether a channel holds a tip: a tip is a resource under the channel's mounting shaft. */
function channelTipped(index) {
  return world.childrenOf[index].some(
    (c) => modelOf(c).category === "tip_mounting_shaft" && world.childrenOf[c].length > 0,
  );
}

/** The channel's own number, as the tree names it, so the halo, the panel and the tree agree. */
function channelLabel(index, ordinal) {
  const numbered = /(\d+)$/.exec(world.names[index]);
  return numbered ? numbered[1] : String(ordinal);
}

export function buildHalos() {
  if (halos) {
    view.remove(halos);
    halos = null;
  }
  disposeOwned(buildHalos);
  if (!showHalos || !world) return;
  const channels = [];
  for (let index = 0; index < world.names.length; index++) {
    if (modelOf(index).category === "pipette_channel") channels.push(index);
  }
  if (!channels.length) return;
  const group = new THREE.Group();
  for (const index of channels) {
    // Its place in the row it belongs to: the channels on the same arm, in tree order. Sixteen
    // channels take the whole ramp; eight take every other step, so the ends stay the ends.
    const row = channels.filter((c) => world.parentOf[c] === world.parentOf[index]);
    const ordinal = row.indexOf(index);
    const step = Math.round((ordinal * (CHANNEL_RAMP.length - 1)) / Math.max(1, row.length - 1));
    const label = channelLabel(index, ordinal);
    const tipped = channelTipped(index);
    const sprite = new THREE.Sprite(haloMaterial(haloTextureFor(label, step, tipped)));
    sprite.renderOrder = OVERLAY_ORDER;
    sprite.frustumCulled = false;
    sprite.userData.index = index;
    // What its disc was drawn for, so a move can swap the disc of the one channel it changed.
    Object.assign(sprite.userData, { label, step, tipped });
    // Discs alternate sides down the row, the first to the right, so neighbours nine millimetres
    // apart do not stack their discs on one side.
    sprite.userData.side = ordinal % 2 === 0 ? 1 : -1;
    const leader = new THREE.Line(LEADER_GEOMETRY, leaderMaterial(step));
    leader.renderOrder = OVERLAY_ORDER - 2;
    leader.frustumCulled = false;
    const mark = new THREE.Sprite(haloMaterial(MARK_TEXTURE, CHANNEL_RAMP[step]));
    mark.renderOrder = OVERLAY_ORDER - 1;
    mark.frustumCulled = false;
    mark.userData.mark = true;
    sprite.userData.leader = leader;
    sprite.userData.mark = mark;
    own(buildHalos, sprite.material, leader.material, mark.material);
    group.add(leader, mark, sprite);
  }
  halos = group;
  view.add(halos);
}

/** Bring each halo's disc up to date with whether its channel holds a tip, keeping the sprites. */
export function refreshHalos() {
  for (const sprite of halos?.children ?? []) {
    if (!sprite.isSprite || sprite.userData.mark === true) continue;
    const tipped = channelTipped(sprite.userData.index);
    if (tipped === sprite.userData.tipped) continue;
    sprite.userData.tipped = tipped;
    sprite.material.map = haloTextureFor(sprite.userData.label, sprite.userData.step, tipped);
  }
}

export function setHalos(on) {
  showHalos = on;
  buildHalos();
}

const _haloRight = new THREE.Vector3();

const _haloUp = new THREE.Vector3();

const _haloAnchor = new THREE.Vector3();

const _haloReach = new THREE.Vector3();

const X_AXIS = new THREE.Vector3(1, 0, 0);

// Each disc sits off the middle of its channel, right and up as the camera sees it, scaled to
// HALO_PX, with its line running from the channel to the disc's edge - while the channel is small
// on screen. As it grows the offset shrinks, and at HALO_CENTRED_AT discs wide the disc sits on the
// channel and the line is gone. Done every frame, since the arm and the camera both move.
export function updateHalos() {
  if (!halos) return;
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;
  _haloRight.set(1, 0, 0).applyQuaternion(camera.quaternion);
  _haloUp.set(0, 1, 0).applyQuaternion(camera.quaternion);
  const discMm = perPixel * HALO_PX;
  for (const sprite of halos.children) {
    if (!sprite.isSprite || sprite.userData.mark === true) continue;
    const index = sprite.userData.index;
    const [sx, sy, sz] = sizeOf(modelOf(index));
    // How many discs wide the channel is on screen: none of the offset once it reaches
    // HALO_CENTRED_AT, all of it while it is under one.
    const widths = Math.max(sx, sy) / discMm;
    const standOff = Math.max(0, Math.min(1, (HALO_CENTRED_AT - widths) / (HALO_CENTRED_AT - 1)));
    _haloReach
      .copy(_haloRight)
      .multiplyScalar(HALO_OFFSET_PX.right * sprite.userData.side)
      .addScaledVector(_haloUp, HALO_OFFSET_PX.up)
      .multiplyScalar(perPixel * standOff);
    _haloAnchor.set(sx / 2, sy / 2, sz / 2).applyMatrix4(world.matrices[index]);
    sprite.position.copy(_haloAnchor).add(_haloReach);
    sprite.scale.setScalar(discMm);
    sprite.userData.mark.position.copy(_haloAnchor);
    sprite.userData.mark.scale.setScalar(perPixel * HALO_MARK_PX);
    const leader = sprite.userData.leader;
    const toEdge = _haloReach.length() - discMm / 2;
    leader.visible = toEdge > 0;
    if (!leader.visible) continue;
    leader.position.copy(_haloAnchor);
    leader.quaternion.setFromUnitVectors(X_AXIS, _haloReach.clone().normalize());
    leader.scale.set(toEdge, 1, 1);
  }
}

// The floor lines follow the view, the way the existing visualizer's do: their spacing is chosen
// from how many millimetres a pixel is worth, and they re-centre on what you are looking at. Lines
// fixed at build time mean nothing once you zoom.
let floorLines = null;

export let floorState = null;

let floorZ = 0;

/** A new scene stands on a floor at `z`; the floor lines are laid out again for it. */
export function resetFloor(z) {
  floorZ = z;
  floorState = null;
}

// Aim for a cell around this many pixels: dense enough to measure against, open enough to see past.
const FLOOR_CELL_PX = 64;

const FLOOR_MAX_DIVISIONS = 320;

// The floor is drawn with the same fat lines everything else uses, rather than with `GridHelper`:
// a hairline is one device pixel whatever width is asked for, which on a retina display is half of
// what it looks like anywhere else and too faint to measure against either way. In CSS pixels, so
// it means the same thing on every screen.
const FLOOR_LINE = 0xc0c7cc;

const FLOOR_AXIS = 0x9aa4ab; // the two lines through the centre, which say where the origin is

const FLOOR_LINE_WIDTH = 1.4;

const FLOOR_AXIS_WIDTH = 2.0;

// A material per line, gone with the line: the renderer keeps a line's draw state until its
// material is disposed, so lines sharing one material piled that up with every change of zoom.
function floorMaterial(axis) {
  const material = new THREE.Line2NodeMaterial({
    color: axis ? FLOOR_AXIS : FLOOR_LINE,
    linewidth: axis ? FLOOR_AXIS_WIDTH : FLOOR_LINE_WIDTH,
    worldUnits: false,
  });
  return material;
}

// The world's own axes: a unit segment each, scaled to the patch and moved with it, so a pan
// touches no buffer. Made once, and kept: neither their geometry nor their material changes.
function axisLine(along) {
  const geometry = new LineSegmentsGeometry();
  geometry.setPositions(along === "x" ? [-1, 0, 0, 1, 0, 0] : [0, -1, 0, 0, 1, 0]);
  const line = new LineSegments2(geometry, floorMaterial(true));
  line.renderOrder = -1;
  line.frustumCulled = false;
  view.add(line);
  return line;
}

let axes = null;

// Whether a line of the patch lands on the world's zero along one axis. The patch is centred at
// `centre`, so its lines sit at centre - half + i * cell.
function lineOnZero(centre, half, cell, divisions) {
  const i = (half - centre) / cell;
  return i > -1e-6 && i < divisions + 1e-6 && Math.abs(i - Math.round(i)) < 1e-6;
}

export function updateFloor() {
  const perPixel = mmPerPixel();
  if (!Number.isFinite(perPixel) || perPixel <= 0) return;

  const span = perPixel * Math.hypot(viewportEl.clientWidth, viewportEl.clientHeight) * 1.3;
  // Coarsen the spacing rather than shrink the coverage: lines that stop inside the viewport read
  // as a hole in the floor, whereas a larger cell just reads as a larger cell.
  const cell = Math.max(
    niceNumber(perPixel * FLOOR_CELL_PX),
    niceNumber(span / FLOOR_MAX_DIVISIONS),
  );
  const divisions = Math.max(4, Math.ceil(span / cell));
  // Snap the centre to the spacing, or the lines crawl as you pan.
  const cx = Math.round(controls.target.x / cell) * cell;
  const cy = Math.round(controls.target.y / cell) * cell;

  if (
    floorState &&
    floorState.cell === cell &&
    floorState.divisions === divisions &&
    floorState.cx === cx &&
    floorState.cy === cy
  ) {
    return;
  }
  const half = (divisions * cell) / 2;

  // Only a change of spacing or extent needs new lines; a pan moves the patch it has.
  if (floorState?.cell !== cell || floorState?.divisions !== divisions) {
    if (floorLines) {
      view.remove(floorLines);
      floorLines.geometry.dispose();
      floorLines.material.dispose();
    }
    // Already in PLR's XY: the lines are built in the plane rather than laid down from another one.
    const points = [];
    for (let i = 0; i <= divisions; i++) {
      const t = -half + i * cell;
      points.push(-half, t, 0, half, t, 0, t, -half, 0, t, half, 0);
    }
    const geometry = new LineSegmentsGeometry();
    geometry.setPositions(new Float32Array(points));
    floorLines = new LineSegments2(geometry, floorMaterial(false));
    floorLines.renderOrder = -2; // under the axes, which lie on two of its lines
    floorLines.frustumCulled = false;
    view.add(floorLines);
  }
  floorState = { cell, divisions, cx, cy };
  floorLines.position.set(cx, cy, floorZ);

  // An axis is drawn where it lands on a line of the patch, which is zero IN THE WORLD and not
  // the middle of the patch: the patch follows the camera. Off the patch, neither is - the truth.
  axes ??= { x: axisLine("x"), y: axisLine("y") };
  axes.x.visible = lineOnZero(cy, half, cell, divisions);
  axes.x.position.set(cx, 0, floorZ);
  axes.x.scale.set(half, 1, 1);
  axes.y.visible = lineOnZero(cx, half, cell, divisions);
  axes.y.position.set(0, cy, floorZ);
  axes.y.scale.set(1, half, 1);
}
