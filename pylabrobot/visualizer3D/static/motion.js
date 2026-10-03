// Acting out what a device's drives do between the positions PyLabRobot records.
//
// PyLabRobot sends a device a command and records where it ends up; the device's own motion
// controller decides how to get there. The server reads each command for its targets (see
// `motion.py`) and sends them here as a motion, which `motion_player.js` plays; the server is told
// when it is done. Python holds the command until then, so the model moves only once the picture
// has got there.
//
// One thing is the page's alone: a plate the iSWAP grips. PyLabRobot does not move it, so the page
// hands it to the gripper when the jaws close on it and to the site under it when they open
// (`carry.js`). A new scene puts it back where PyLabRobot has it.

import * as THREE from "three";

import { heldAt, LIDDABLE, MOVABLE, SITES, siteUnder } from "./carry.js";
import { worldBox } from "./drawn.js";
import { readAxis, reattach, setAxis, turnTo } from "./live.js";
import { createPlayer } from "./motion_player.js";
import { modelOf, world } from "./world.js";

// What each gripper holds, by name, as the page has it.
const held = new Map();

// Every resource of the given categories, with the box it takes up in the world.
function candidates(categories) {
  const found = [];
  for (let index = 0; index < world.names.length; index++) {
    const category = modelOf(index).category;
    if (categories.has(category)) found.push({ index, category, box: worldBox(index) });
  }
  return found;
}

function grip(gripper, point) {
  const index = world.indexOfName.get(gripper);
  if (index === undefined || held.has(gripper)) return;
  const at = new THREE.Vector3(point.x, point.y, point.z).applyMatrix4(world.matrices[index]);
  const taken = heldAt(at, candidates(MOVABLE));
  if (taken === undefined) return;
  reattach(world.names[taken], gripper);
  held.set(gripper, world.names[taken]);
}

function release(gripper) {
  const name = held.get(gripper);
  held.delete(gripper);
  const index = name === undefined ? undefined : world.indexOfName.get(name);
  if (index === undefined) return;
  const model = modelOf(index);
  const lid = model.category === "lid";
  const seats = candidates(lid ? new Set([...SITES, ...LIDDABLE]) : SITES).map((c) =>
    LIDDABLE.has(c.category)
      ? {
          ...c,
          covered: world.childrenOf[c.index].some(
            (child) => child !== index && modelOf(child).category === "lid",
          ),
        }
      : c,
  );
  const site = siteUnder(worldBox(index), seats, {
    index,
    category: model.category,
    nesting: model.nesting_z_height,
  });
  // Nothing under it: it stays where it was let go of.
  reattach(name, site === undefined ? null : world.names[site]);
}

const player = createPlayer({
  readAxis,
  setAxis,
  indexOf: (name) => world?.indexOfName.get(name),
  attach: reattach,
  turnTo,
  grip,
  release,
  // A tab in the background gets no frames, so nothing it played would ever finish and the
  // command would wait out its timeout. It jumps instead.
  skipping: () => document.hidden,
});

// How fast motions play against the drives' own speeds: `?motion=2` twice as fast, `?motion=0`
// not at all - everything jumps to where it ends and the command goes straight on.
const asked = Number(new URLSearchParams(location.search).get("motion") ?? 1);
player.setSpeed(Number.isFinite(asked) && asked >= 0 ? asked : 1);

export const stepMotion = (delta) => player.step(delta);

/** A new scene: nothing moving carries on, and nothing is held that PyLabRobot does not have. */
export function dropMotions() {
  player.dropAll();
  held.clear();
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) player.finishAll();
});

/**
 * Play a motion the server sent, then say so. The command waits for that, so it is said whatever
 * happens: a motion that cannot be played is reported played at once.
 *
 * @param {any} request what `motion.py` read from the command
 * @param {() => void} done tells the server
 */
export async function playMotion(request, done) {
  try {
    if (world) await player.play(request);
  } catch (error) {
    console.warn("a motion could not be played", error);
  } finally {
    done();
  }
}
