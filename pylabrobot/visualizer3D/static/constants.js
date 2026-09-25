import * as THREE from "three";

// Colours and thresholds for the viewer. Pure data: nothing here reads or writes scene state, so
// it can be changed without knowing anything about how the scene is built.

export const DEG = Math.PI / 180;
// What this page and the server agree on; the scene carries the server's number. See server.py.
export const PROTOCOL = 1;

// The existing visualizer's resource colours, so the same deck reads the same in both.
export const RESOURCE_COLORS = {
  facility: 0xe8ebed,
  device: 0xdfe4e7,
  bench: 0xe4e8ea,
  deck: 0xf5fafc,
  carrier: 0x5c6c8f,
  mfx_carrier: 0x536181,
  plate_carrier: 0x5c6c8f,
  tip_carrier: 0x4e3149, // darkest of the three: the carrier
  trough_carrier: 0x756793,
  tube_carrier: 0x756793,
  plate: 0x5c6c8f, // the carrier's blue: a plate is read by its wells, not by its own box
  well: 0xbcc7cf,
  tip_rack: 0x9b6690, // mid: the rack sitting in it
  tip_spot: 0x6b4f66, // the spot's rim
  // Not of the rack's family: a tip is the charcoal its own file is modelled in, so a spot with one
  // in it is dark against the spot's light rim whether the model or the box is being drawn. Made
  // any lighter it reads the same as an empty spot, and a full rack and a spent one look alike.
  tip: 0x333333,
  tube_rack: 0x122d42,
  tube: 0xbcc7cf,
  trough: 0x756793,
  container: 0x756793,
  resource_holder: 0x5b6277,
  plate_holder: 0x8d99ae,
  plate_adapter: 0x7a8088,
  lid: 0x9aa7ad,
  trash: 0x8a949a,
  x_arm: 0x6b7a85,
  // The iSWAP, in warm colours the deck does not use, so a thin arm over a full deck is findable
  // and its parts are told apart: the links carry the arm, the fingers are what points somewhere.
  iswap_head: 0x8c5a2b,
  iswap_link: 0xd4761f,
  mechanical_gripper: 0xb8471f,
  body: 0xb8471f,
  finger: 0xff2d55,
  pad: 0xff7a90,
  default: 0xbdb163,
};

// Parts that travel over the deck rather than standing on it. Drawn see-through, because an arm
// spanning the full deck depth otherwise hides everything under it in a plan view. This is the
// case a per-package renderer registry should own: the arm's own package knows it is a moving
// part, and this list is a stand-in until that exists.
export const MOVING_PARTS = new Set(["x_arm", "arm", "gripper", "head", "channel"]);

// Parts whose reference point says nothing the picture does not already. A single pipetting channel
// is measured from its own axis, which is where it visibly is, and eight of them side by side turn
// one useful line into a thicket over the deck. A head is a different case and keeps its mark: it
// is measured from channel A1, in a corner of a block a hundred millimetres across, which is not
// somewhere anyone would read off the shape.
export const NO_REFERENCE_MARK = new Set(["pipette_channel"]);

// Parts a click selects even though they carry something. A click otherwise passes through anything with
// children to what lies behind it, which is right for a hood or a carrier; a pipetting channel always carries its
// tip mounting shaft, so without this a click on the channel selects whatever is on the deck under it. The arm
// stays out: it spans the deck, and would take every click meant for the labware beneath it.
export const PICKABLE_PARTS = new Set(["pipette_channel"]);

// Structure, reference and content, in that order of prominence. With the fills gone, outlines
// carry the information about what is on the deck, so they are the darkest thing; rails, bands and
// grid are references and recede; only live contents are saturated.
const STRUCTURE_LIGHT = 0xaeb7bd; // an outer shell: a facility, a device
const STRUCTURE_DARK = 0x49555e; // an inner one: a plate, a holder
const STRUCTURE_MAX_DEPTH = 4; // depth at which the ramp reaches STRUCTURE_DARK
// Outlines are drawn as fat lines, because `LineBasicMaterial.linewidth` is ignored on every
// backend that matters: a hairline is one device pixel whatever you ask for. This is in CSS
// pixels, so it means the same thing on a retina display as anywhere else.
// An axis view is a drawing, not a lit scene: a fill is exactly its colour, and every resource is
// stroked near-black. That flat, outlined look is most of why the 2D visualizer reads.
export const FLAT_EDGE = 0x1f2529;
// A translucent box on a white ground is its outline, so the outline has to carry it. Half again
// what a hairline would be: enough to read a carrier apart from what stands in it, still well under
// the carriage, which is heavier again so that the part that travels stays the boldest thing drawn.
export const EDGE_WIDTH_FLAT = 1.5;
export const EDGE_WIDTH_3D = 1.8; // 20 percent thicker where the scene has depth to read

export function structureEdgeStyle(depth) {
  const t = Math.min(depth, STRUCTURE_MAX_DEPTH) / STRUCTURE_MAX_DEPTH;
  return {
    color: new THREE.Color(STRUCTURE_LIGHT).lerp(new THREE.Color(STRUCTURE_DARK), t),
    opacity: 0.5 + 0.45 * t,
  };
}

export const LIQUID = 0xf39c12;
export const VESSEL_EMPTY = 0xffffff; // nothing in it reads as white, as it does on a plan
export const VESSEL_RIM = 0x5c666e;
// Looking straight down, a tip is a circle and the only thing worth reading off it is whether the
// spot it stands in is still filled. The existing visualizer answers that with a green disc, and
// this is that visualizer's own green, so a deck of racks reads the same in both.
export const TIP_PLAN_FILL = 0x40cda1;
// How thick a container's wall is drawn, in mm, measured OUTWARDS from the cavity. A resource's box
// is what it holds, so its material stands outside that box - nothing is drawn within the extent
// the resource declares. Nobody reports a wall thickness, so this is a drawing convention rather
// than a measurement: thin enough to fit between two wells on a 9 mm pitch, thick enough to find.
export const VESSEL_WALL = 0.5;
// And how solid it is drawn. Slightly see-through, so what is standing in the cavity reads through
// the wall around it rather than being hidden by it from every angle but straight down.
export const VESSEL_WALL_OPACITY = 0.85;
// A tip's filter: a white disc across the bore, this far below the collar, in mm.
export const FILTER = 0xffffff;
export const FILTER_BELOW_COLLAR = 2;
// How wide the disc is drawn, as a share of the tip's outer width, until the tip's own file has
// been read for the bore at that height.
export const FILTER_WIDTH_UNMEASURED = 0.6;
export const SELECT = 0x1a4b8c;
// How long the selection box stays after a pick, as the existing visualizer's dashed rectangle
// does: long enough to see what was chosen, gone before it gets in the way of looking at it.
export const SELECTION_SHOWN_MS = 2000;
// The get-location tool's bullseyes, held at this many pixels: blue on the resource under the
// pointer, pink on the resource everything is measured against, as the existing visualizer has.
export const BULLSEYE_PX = 22;
export const BULLSEYE_HOVER = 0x0d6efd;
export const BULLSEYE_WRT = 0xff5fa2;
// What the search pane's "Wells" filter admits: anything that holds liquid, as the existing
// visualizer admits any container, not wells alone.
export const SEARCH_CONTAINERS = new Set(["well", "tube", "trough", "container", "petri_dish"]);
export const HOVER = 0xbbcc33;
// Ported from the X-arm tracker branch's `XArm` renderer: a translucent frame with a window
// punched through it, and a cyan line at the tracked X. The window is what lets you read the deck
// underneath, and the line sits inside it rather than crossing the whole deck.
export const ARM_COLOR = 0x404040;
export const ARM_OPACITY = 0.575;
// The carriage is see-through so you can read the deck under it, which leaves its own extent hard
// to place. A stroke noticeably heavier than a resource outline is what puts the boundary back,
// and it follows the window as well as the footprint, so the opening reads as part of the part.
// A carrier or a rack is a container you look into, so its walls are glass and its bottom is not:
// the floor is drawn opaque as its own surface, and these are the five faces around it.
export const SHELL_OPACITY = 0.5;
// A resource with no model of its own is drawn as its bounding box. Slightly see-through, because
// a box is a statement about extent rather than a picture of the thing: what it contains, and
// what stands behind it, should still read through it. A resource whose model is being drawn has
// its box hidden entirely; one whose model is too small to be worth drawing has its box back, and
// that box is the picture, so it is drawn solid rather than at this.
export const BOX_OPACITY = 0.4;
// A part that travels over the deck, drawn see-through wherever it is. Solid it would hide
// whatever it happens to be above, which is the one thing you need to see under an arm.
export const MOVING_OPACITY = 0.35;
// What is left of a box once the model it stood in for is being drawn: its border, and only just.
// The extent is still worth being able to find - it is what the collision model uses - but it is
// no longer what says where the thing is, so it must not compete with the geometry inside it.
export const MODEL_EDGE_OPACITY = 0.22;
// The facility is the space everything stands in, not a thing to look at.
export const SPACE_OPACITY = 0.06;

// A holder is one position on a carrier: it holds at most one thing, and what the tree needs to
// say is what is standing in it, not that a holder exists. Counting through them is what makes a
// tip carrier read as "5 tipracks" rather than "5 resource holders".
// Things you look into and read positions off. A container keeps its walls - glass, so what is in
// it still reads - because the walls are what makes it a container rather than a floor with things
// floating over it. Everything else that holds an enclosure gives its fill up entirely: a facility,
// a device or a deck drawn as a sheet between the eye and the deck buys nothing at all.
export const CONTAINERS = new Set([
  "plate",
  "tip_rack",
  "tube_rack",
  "plate_adapter",
  "plate_holder",
  "resource_holder",
  "trough",
  "trash",
]);

// The positions inside a container: a plate's wells, a rack's tip spots, a tube rack's tubes. The
// tree does not list them - a plate says "96 wells" on its own row, which is the whole of what a
// reader wants, where ninety-six rows are a wall to scroll past. They are drawn in the viewport
// exactly as before; this is about the panel only.
export const TREE_HIDDEN = new Set(["well", "tip_spot", "tube"]);

export const HOLDERS = new Set([
  "resource_holder",
  "plate_holder",
  // A tip carrier's or an MFX tip module's position: a holder by class, under its own category.
  "embedded_tip_rack_holder",
]);

// Glazing: a model's own see-through parts, at or below this opacity. Looking down an axis you are
// looking THROUGH the hood at the deck, and each pane you look through lightens everything under it
// - two read as an enclosure, five read as a wash. In an axis view the panes come out; in a free
// view they stay, because there they are what makes the device read as enclosed.
export const GLAZED_MAX_OPACITY = 0.5;

// What a resource holds rather than what it is: the positions inside a plate, a rack or a head. A
// depth control counts levels, and these are a level - so opening to the depth that shows a plate
// on a carrier would open ninety-six wells with it. They open when they are asked for by name.
export const CONTENTS = new Set(["well", "tip_spot", "tube", "tip_mounting_shaft"]);
// Drawn see-through at an opacity of their own rather than the box's or the shell's: a tip rack is
// read by which of its positions still hold a tip, a plate by the wells standing in it.
export const CATEGORY_OPACITY = { tip_rack: 0.7, plate: 0.25 };

export const ARM_EDGE = 0x1a1f24;
export const ARM_EDGE_WIDTH_FLAT = 2.2;
export const ARM_EDGE_WIDTH_3D = 2.6;
// Fallback opening, used when a part does not declare its own: symmetric, which is the least
// wrong guess. A part that knows its geometry says so in `window`.
export const ARM_INSET_X = 95;
export const ARM_INSET_Y = 20;
export const REFERENCE_LINE = 0x00e5ff;
// A gripper's own reference is the point it grips at. Drawn as a line lying flat, as long as a pad
// is - which pad it is, is read off the tree - and thicker than a resource's reference mark, since
// it marks a point the arm is programmed against rather than an edge of something standing still.
export const GRIP_MARK_OPACITY = 0.7;
export const GRIP_MARK_WIDTH = 5.4; // mm across each arm of the cross
// The opening at the middle, as a radius in mm. A crosshair with a solid centre covers the very
// point it is marking; left open, the thing being gripped shows through where the grip happens.
// Wider than an arm, so the four arms stand clear of it rather than meeting.
export const GRIP_MARK_OPENING = 3.75;
// A line material's width is ignored on most backends, so the reference mark is geometry: a thin
// quad lying just under the arm, where it reads against the deck rather than floating inside the
// carriage.
export const REFERENCE_WIDTH = 4.2; // mm
export const REFERENCE_DROP = 2; // mm below the arm's underside
export const ARM_REFERENCE_OPACITY = 0.7;

// A halo on each pipetting channel, on unless the rail button says not: a disc facing the camera,
// held at this many pixels across whatever the zoom, so a nine-millimetre channel is findable at
// deck scale. Its colour is the channel's place in the ramp below and its number is drawn in it; it
// is filled while the channel's mounting shaft holds a tip and hollow while it does not.
export const HALO_PX = 30;
// Where the disc sits relative to its channel, in pixels on screen: to one side and a little up,
// right for the first channel and alternating down the row, so the channel stays uncovered and
// neighbours' discs do not stack, with a line in the disc's colour joining the two. That is for a
// channel smaller on screen than the disc. Zoomed in, the offset shrinks as the channel grows,
// and once the channel is this many discs wide the disc sits on its centre with no line.
export const HALO_OFFSET_PX = { right: 34, up: 18 };
export const HALO_CENTRED_AT = 3;
// And a small glow on the channel itself, in the same colour, this many pixels across: the point
// the line runs from is marked, not bare geometry.
export const HALO_MARK_PX = 14;
// Sixteen steps from amber to magenta, one per channel in row order, OKLCH lightness 0.86 to
// 0.50. Adjacent steps are about 3 OKLab units apart, a gradation rather than a distinction, so
// the number in the halo is what names a channel and the ramp says where in the row it stands;
// every fourth channel is 12 apart and the ends 44. Twenty-one from the tip green, so a full
// channel never reads as a full spot. Dark ink reads on the first nine steps, white on the rest.
export const CHANNEL_RAMP = [
  0xfec766, 0xfebb5e, 0xfeae57, 0xfca254, 0xfa9553, 0xf78955, 0xf37d58, 0xed715d, 0xe76662,
  0xe05b68, 0xd7506e, 0xce4775, 0xc33e7b, 0xb83581, 0xac2e87, 0x9f268d,
];
export const CHANNEL_RAMP_DARK_INK_STEPS = 9;
export const HALO_INK = "#1f2529";
// The disc's background: white while the mounting shaft is empty, and this light green once it
// holds a tip, a tint of the green a full spot is drawn in. The change is meant to be seen from
// across the deck, so it is the whole disc and not only the coin at its centre.
export const HALO_TIPPED_BACKGROUND = "#b9edd9";

// Adaptive quality: the page steps its own cost down while frames are slow and back up once they
// are fast again, averaged over drawn frames. A step down after QUALITY_SETTLE_MS of slow frames,
// a step up after QUALITY_RECOVER_MS of fast ones, and a level found slow is not returned to for
// QUALITY_HOLD_MS, so a machine slow at full and fast at low does not swing between the two.
// A frame is costed by the gap since the last one as well as by its own work, and the gap is
// never under the display's refresh, so "fast" sits above a sixty-hertz frame.
export const QUALITY_SLOW_MS = 33;
export const QUALITY_FAST_MS = 20;
export const QUALITY_SETTLE_MS = 1000;
export const QUALITY_RECOVER_MS = 4000;
export const QUALITY_HOLD_MS = 60000;
// The first frames after a scene arrives are spent compiling pipelines and read as slow on any
// machine, so nothing is judged until this long after the scene came.
export const QUALITY_WARMUP_MS = 3000;
// The levels: 0 draws everything as set up, 1 drops the pixel ratio to one, 2 also drops the
// environment lighting. Antialiasing is fixed when the renderer is made, so it is not a level.
export const QUALITY_LEVELS = 3;
// The sky light, with the environment and without it. The environment is most of the light on
// every surface, so the lowest level, which drops it, stands the sky light in for it instead.
export const SKY_LIGHT = 1.0;
export const SKY_LIGHT_WITHOUT_ENVIRONMENT = 3.6;
