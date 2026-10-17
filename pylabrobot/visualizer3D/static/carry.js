// What a gripper takes hold of when its jaws close, and what a plate it lets go of comes to rest on.
//
// PyLabRobot does not move a plate when the iSWAP grips it, so the page does: the plate is handed to
// the gripper when the jaws close on it, rides the arm, and is handed to the site under it when they
// open. A lid is labware like any other, except that what takes it can be a plate: set down on one
// with no lid, it becomes that plate's lid, as `Plate.assign_child_resource` makes it. Pure - it is
// given boxes, not the scene - so it can be checked outside a page.

// What an arm picks up: labware, not what labware holds or what holds it.
export const MOVABLE = new Set([
  "plate",
  "lid",
  "tip_rack",
  "tube_rack",
  "plate_adapter",
  "trough",
]);

// What a plate is set down on.
export const SITES = new Set(["resource_holder", "plate_holder", "plate_adapter"]);

// What a lid is set down on besides a site: a plate that has none, its bottom `nesting_z_height`
// below the plate's top.
export const LIDDABLE = new Set(["plate"]);

// How far a point may lie outside a box and still be taken as in it, in mm.
const REACH = 2;
// How far above a site a plate may be let go of and still land on it, in mm.
const DROP = 30;
// How far a site's surface may stand above the plate's bottom: a plate's skirt sits down into its
// site, 3 mm on the demo's carriers.
const SUNK = 10;

/**
 * @typedef {object} Candidate
 * @property {number} index
 * @property {string} category
 * @property {{min: {x: number, y: number, z: number}, max: {x: number, y: number, z: number}}} box
 * @property {boolean} [covered] a plate that already has a lid
 */

const inside = (p, box, pad) =>
  p.x >= box.min.x - pad &&
  p.x <= box.max.x + pad &&
  p.y >= box.min.y - pad &&
  p.y <= box.max.y + pad &&
  p.z >= box.min.z - pad &&
  p.z <= box.max.z + pad;

const volume = (box) => (box.max.x - box.min.x) * (box.max.y - box.min.y) * (box.max.z - box.min.z);

// How far a point lies outside a box, in mm: 0 inside it.
const outside = (p, box) =>
  Math.hypot(
    Math.max(box.min.x - p.x, 0, p.x - box.max.x),
    Math.max(box.min.y - p.y, 0, p.y - box.max.y),
    Math.max(box.min.z - p.z, 0, p.z - box.max.z),
  );

/**
 * The labware at the point the jaws close on: of the movable things whose box holds it (within
 * `REACH`), the one it is least outside of, then the smallest. A lid nested on another lid is
 * gripped in its own box and within reach of the one below; the one it is in is the one taken.
 *
 * @param {{x: number, y: number, z: number}} point in world mm
 * @param {Candidate[]} candidates
 * @returns {number | undefined}
 */
export function heldAt(point, candidates) {
  let best;
  let rank = [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY];
  for (const c of candidates) {
    if (!MOVABLE.has(c.category) || !inside(point, c.box, REACH)) continue;
    const r = [outside(point, c.box), volume(c.box)];
    if (r[0] < rank[0] - 1e-9 || (Math.abs(r[0] - rank[0]) <= 1e-9 && r[1] < rank[1])) {
      rank = r;
      best = c.index;
    }
  }
  return best;
}

/**
 * What something let go of lands on: the highest seat under its middle that is near its bottom - a
 * little above it, as a skirt sits down into a site, or a drop's height below it. A site's seat is
 * its surface; for a lid, a plate with no lid is a seat too, `nesting` below the plate's top.
 *
 * @param {{min: {x: number, y: number, z: number}, max: {x: number, y: number, z: number}}} box
 *   what was let go of, in world mm
 * @param {Candidate[]} candidates
 * @param {{index?: number, category?: string, nesting?: number}} [released] what was let go of:
 *   not a seat for itself, and a lid when its category says so
 * @returns {number | undefined}
 */
export function siteUnder(box, candidates, released = {}) {
  const lid = released.category === "lid";
  const middle = { x: (box.min.x + box.max.x) / 2, y: (box.min.y + box.max.y) / 2 };
  let best;
  let highest = Number.NEGATIVE_INFINITY;
  for (const c of candidates) {
    if (c.index === released.index) continue;
    const b = c.box;
    let seat;
    if (SITES.has(c.category)) seat = b.max.z;
    else if (lid && LIDDABLE.has(c.category) && !c.covered)
      seat = b.max.z - (released.nesting ?? 0);
    else continue;
    const under =
      middle.x >= b.min.x - REACH &&
      middle.x <= b.max.x + REACH &&
      middle.y >= b.min.y - REACH &&
      middle.y <= b.max.y + REACH;
    if (!under || seat > box.min.z + SUNK || seat < box.min.z - DROP) continue;
    if (seat > highest) {
      highest = seat;
      best = c.index;
    }
  }
  return best;
}
