// The motion player, outside a page: `node --test pylabrobot/visualizer3D/motion_player_tests.mjs`.
// Run from Python by `motion_tests.py`, which skips it where there is no Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { heldAt, siteUnder } from "./static/carry.js";
import { createPlayer, turnedLocation } from "./static/motion_player.js";
import { motionProfile } from "./static/motion_profile.js";

const DRIVES = {
  x: { speed: 400, acceleration: 500 },
  y: { speed: 250, acceleration: null },
  z: { speed: 125, acceleration: 800 },
};

// A world of named resources with positions, recording every change in order.
function fakeWorld(positions) {
  const names = Object.keys(positions);
  const at = names.map((n) => [...positions[n]]);
  const log = [];
  const parents = {};
  return {
    log,
    parents,
    at: (name) => at[names.indexOf(name)],
    deps: (skipping = () => false) => ({
      readAxis: (i, axis) => at[i][axis],
      setAxis: (i, axis, value) => {
        at[i][axis] = value;
        log.push({ name: names[i], axis, value });
      },
      indexOf: (name) => (names.includes(name) ? names.indexOf(name) : undefined),
      attach: (name, parent) => {
        parents[name] = parent;
        log.push({ attach: name, parent, z: at[names.indexOf("ch0")][2] });
      },
      skipping,
    }),
  };
}

// Play to the end in fixed frames, as the frame loop would, and say how long it took.
async function playOut(player, request, frame = 1 / 60) {
  let done = false;
  const playing = player.play(request).then(() => {
    done = true;
  });
  let seconds = 0;
  for (let i = 0; i < 100000 && !done; i++) {
    await new Promise((resolve) => setImmediate(resolve)); // let finished moves start the next
    if (done) break;
    player.step(frame);
    seconds += frame;
  }
  await playing;
  return seconds;
}

const pickUp = {
  kind: "tip_pickup",
  arm: { name: "arm", x: 300 },
  traverse: [{ name: "ch0", z: 0 }],
  channels: [{ name: "ch0", channel: 0, y: 200, down: -100, end: 10 }],
  attach: [{ name: "tip", parent: "shaft0" }],
  dwell: 0,
  drives: DRIVES,
};

const start = () => ({ arm: [100, 0, 0], ch0: [0, 150, 10], tip: [0, 0, 0] });

test("a pick-up goes across, then down, takes the tip at the bottom, then comes up", async () => {
  const world = fakeWorld(start());
  await playOut(createPlayer(world.deps()), pickUp);

  const firstZ = world.log.findIndex((e) => e.axis === 2);
  const across = world.log.slice(0, firstZ);
  assert.ok(across.some((e) => e.name === "arm") && across.some((e) => e.axis === 1));
  // The arm and the channel travel together: their changes interleave rather than follow.
  const lastArm = across.findLastIndex((e) => e.name === "arm");
  const firstY = across.findIndex((e) => e.axis === 1);
  assert.ok(firstY < lastArm, "the channel waited for the arm");

  const handover = world.log.findIndex((e) => e.attach === "tip");
  assert.ok(handover > firstZ, "the tip was taken before the channel went down");
  assert.equal(world.log[handover].z, -100, "the tip was taken away from the bottom");
  assert.equal(world.parents.tip, "shaft0");
  const after = world.log.slice(handover + 1).filter((e) => e.axis === 2);
  assert.ok(after.length > 1 && after.at(-1).value === 10, "the channel did not come back up");
  assert.deepEqual(world.at("arm"), [300, 0, 0]);
  assert.deepEqual(world.at("ch0"), [0, 200, 10]);
});

test("a move passes through the positions between, forward only", async () => {
  const world = fakeWorld(start());
  await playOut(createPlayer(world.deps()), pickUp);
  const xs = world.log.filter((e) => e.name === "arm").map((e) => e.value);
  assert.ok(xs.filter((x) => x > 101 && x < 299).length >= 10, "the arm jumped");
  for (let i = 1; i < xs.length; i++) assert.ok(xs[i] >= xs[i - 1], "the arm went backwards");
});

test("a motion takes as long as its drives say", async () => {
  const world = fakeWorld(start());
  const seconds = await playOut(createPlayer(world.deps()), pickUp);
  // Across is the slower of X and Y; then down and back up in Z.
  const across = Math.max(
    motionProfile(200, 400, 500).duration,
    motionProfile(50, 250, null).duration,
  );
  const expected =
    across + motionProfile(110, 125, 800).duration + motionProfile(110, 125, 800).duration;
  assert.ok(Math.abs(seconds - expected) < 0.2, `took ${seconds}, expected ${expected}`);
});

test("an aspiration dwells at the bottom and leaves the liquid at its own speed", async () => {
  const world = fakeWorld(start());
  const aspirate = {
    ...pickUp,
    kind: "aspirate",
    attach: [],
    dwell: 1.5,
    channels: [{ name: "ch0", channel: 0, y: 150, down: -100, end: 10, leave: -90, leave_speed: 5 }],
  };
  const quick = await playOut(createPlayer(fakeWorld(start()).deps()), { ...aspirate, dwell: 0 });
  const withDwell = await playOut(createPlayer(world.deps()), aspirate);
  assert.ok(Math.abs(withDwell - quick - 1.5) < 0.05, "the dwell was not waited out");
  // 10 mm at 5 mm/s is at least 2 s, far slower than the drive's own 125 mm/s.
  const leaving = world.log.filter((e) => e.axis === 2 && e.value > -100 && e.value <= -90);
  assert.ok(leaving.length > 100, "it left the liquid at the drive's speed, not its own");
});

test("a command's fixed time is spent before anything moves", async () => {
  const world = fakeWorld(start());
  const quick = await playOut(createPlayer(fakeWorld(start()).deps()), pickUp);
  const player = createPlayer(world.deps());
  let seconds = 0;
  const playing = player.play({ ...pickUp, fixed: 1.2 });
  for (; seconds < 1.1; seconds += 1 / 60) {
    await new Promise((resolve) => setImmediate(resolve));
    player.step(1 / 60);
  }
  assert.equal(world.log.length, 0, "something moved during the fixed time");
  let rest = 0;
  let done = false;
  playing.then(() => (done = true));
  for (let i = 0; i < 100000 && !done; i++) {
    await new Promise((resolve) => setImmediate(resolve));
    if (done) break;
    player.step(1 / 60);
    rest += 1 / 60;
  }
  assert.ok(Math.abs(seconds + rest - quick - 1.2) < 0.05, `took ${seconds + rest}, expected ${quick + 1.2}`);
});

test("the channels set off in Y one after another, each a stagger after the last", async () => {
  const positions = { arm: [100, 0, 0], ch0: [0, 150, 10], ch1: [0, 140, 10], ch2: [0, 130, 10] };
  const world = fakeWorld(positions);
  const request = {
    ...pickUp,
    arm: null,
    attach: [],
    traverse: [],
    channels: [
      { name: "ch2", channel: 2, y: 30 },
      { name: "ch0", channel: 0, y: 50 },
      { name: "ch1", channel: 1, y: 40 },
    ],
    drives: { ...DRIVES, y: { speed: 250, acceleration: 800, stagger: 0.5 } },
  };
  const seconds = await playOut(createPlayer(world.deps()), request);
  // In channel order: ch0 first, ch2 last, two staggers after it.
  const first = (name) => world.log.findIndex((e) => e.name === name);
  assert.ok(first("ch0") < first("ch1") && first("ch1") < first("ch2"), "not in channel order");
  const expected = 2 * 0.5 + motionProfile(100, 250, 800).duration;
  assert.ok(Math.abs(seconds - expected) < 0.05, `took ${seconds}, expected ${expected}`);
  assert.deepEqual([world.at("ch0")[1], world.at("ch1")[1], world.at("ch2")[1]], [50, 40, 30]);
});

test("a tip pick-up presses the last stretch at its own slow speed", async () => {
  const world = fakeWorld(start());
  const pressing = {
    ...pickUp,
    channels: [{ name: "ch0", channel: 0, y: 150, down: -90, press: -100, press_speed: 10, end: 10 }],
  };
  const seconds = await playOut(createPlayer(world.deps()), pressing);
  const handover = world.log.findIndex((e) => e.attach === "tip");
  assert.equal(world.log[handover].z, -100, "the tip was taken before the press ended");
  const slow = world.log.filter((e) => e.axis === 2 && e.value < -90 && e.value >= -100);
  assert.ok(slow.length > 50, "the press ran at the drive's speed");
  assert.ok(seconds > 1.0, `10 mm at 10 mm/s took only ${seconds}`);
});

test("an aspiration follows the surface down while it draws, then pulls out", async () => {
  const world = fakeWorld(start());
  const aspirate = {
    ...pickUp,
    kind: "aspirate",
    arm: null,
    attach: [],
    dwell: 2,
    channels: [
      {
        name: "ch0",
        channel: 0,
        y: 150,
        down: -100,
        follow: -104,
        follow_speed: 2,
        leave: -100,
        leave_speed: 50,
        pull_out: -90,
        end: 10,
      },
    ],
  };
  const seconds = await playOut(createPlayer(world.deps()), aspirate);
  const zs = world.log.filter((e) => e.name === "ch0" && e.axis === 2).map((e) => e.value);
  const lowest = Math.min(...zs);
  assert.ok(Math.abs(lowest + 104) < 0.01, `it went down to ${lowest}, not the followed -104`);
  // 4 mm at 2 mm/s fills the 2 s dwell: steadily, not in a jump.
  const following = world.log.filter((e) => e.axis === 2 && e.value < -100 && e.value > -104);
  assert.ok(following.length > 60, "it jumped rather than followed");
  // After the lowest point: out to the surface, then on up through the pull-out stretch.
  const after = zs.slice(zs.indexOf(lowest));
  assert.ok(after.filter((z) => z > -95 && z < -90).length > 3, "it did not pull out steadily");
  assert.equal(world.at("ch0")[2], 10);
  assert.ok(seconds > 2, `took only ${seconds}`);
});

test("a pick-up starts down before its crossing has finished", async () => {
  const request = { ...pickUp, attach: [], down_from: 0.5 };
  const world = fakeWorld(start());
  const overlapped = await playOut(createPlayer(world.deps()), request);
  const sequence = await playOut(createPlayer(fakeWorld(start()).deps()), { ...request, down_from: 0 });
  assert.ok(overlapped < sequence - 0.1, `overlapped ${overlapped}, in sequence ${sequence}`);
  // The channel was going down while the arm still moved.
  const firstDown = world.log.findIndex((e) => e.name === "ch0" && e.axis === 2);
  const lastArm = world.log.findLastIndex((e) => e.name === "arm");
  assert.ok(firstDown < lastArm, "the descent waited for the arm");
  assert.deepEqual(world.at("arm"), [300, 0, 0]);
});

test("a page in the background jumps to the end and still hands the tips over", async () => {
  const world = fakeWorld(start());
  const player = createPlayer(world.deps(() => true));
  await player.play(pickUp); // resolves with no frames at all
  assert.deepEqual(world.at("ch0"), [0, 200, 10]);
  assert.equal(world.parents.tip, "shaft0");
  assert.equal(player.step(0.016), false);
});

test("speed 0 jumps to the end", async () => {
  const world = fakeWorld(start());
  const player = createPlayer(world.deps());
  player.setSpeed(0);
  await player.play(pickUp);
  assert.deepEqual(world.at("arm"), [300, 0, 0]);
});

test("a resource the page does not have is skipped, not waited for", async () => {
  const world = fakeWorld(start());
  const request = { ...pickUp, arm: { name: "gone", x: 5 } };
  await playOut(createPlayer(world.deps()), request);
  assert.deepEqual(world.at("ch0"), [0, 200, 10]);
});

test("the frame loop keeps going between one move ending and the next starting", async () => {
  const world = fakeWorld(start());
  const player = createPlayer(world.deps());
  const playing = player.play(pickUp);
  for (let i = 0; i < 2000; i++) {
    await new Promise((resolve) => setImmediate(resolve));
    if (!player.step(1 / 60)) break;
  }
  await playing;
  assert.deepEqual(world.at("ch0"), [0, 200, 10], "the loop stopped with the motion unfinished");
});

test("the profile reaches the end exactly and never overshoots", () => {
  for (const [d, v, a] of [
    [200, 400, 500],
    [5, 400, 500],
    [50, 250, null],
  ]) {
    const p = motionProfile(d, v, a);
    assert.equal(p.progress(p.duration), 1);
    for (let t = 0; t <= p.duration; t += p.duration / 50) {
      assert.ok(p.progress(t) >= 0 && p.progress(t) <= 1);
    }
  }
});

// -- iSWAP -----------------------------------------------------------------------------------------

test("a turn keeps its pivot where PyLabRobot's rotate_to keeps it", () => {
  // iSWAP link 1 in the demo, turned by `rotate_to(z=200, pivot_coordinate=proximal_joint)`.
  const pivot = { x: 12.7, y: 12.75, z: -20.3 };
  const after = turnedLocation({ x: 2.8527, y: 2.2055, z: -15.3 }, 1.34477, 200, pivot);
  assert.ok(Math.abs(after.x - 22.8233) < 1e-3 && Math.abs(after.y - 31.5748) < 1e-3);
  assert.equal(after.z, -15.3);
});

// A joint as the page has it: its rotation and location, turned through `turnTo`.
function jointWorld(rotation, location) {
  const state = { rotation, location: { ...location } };
  const turns = [];
  const deps = {
    readAxis: (_i, axis) => (axis === 5 ? state.rotation : 0),
    setAxis: () => {},
    indexOf: (name) => (name === "link" ? 0 : undefined),
    attach: () => {},
    skipping: () => false,
    turnTo: (_i, degrees, pivot) => {
      state.location = turnedLocation(state.location, state.rotation, degrees, pivot);
      state.rotation = degrees;
      turns.push(degrees);
    },
  };
  return { state, turns, deps };
}

test("a joint turns the way its drive runs, from its angle to the one sent", async () => {
  // Link 1 pointing right (drive 90, rotation 0) sent to the left (drive -90): through the front
  // (rotation 270), not the short way through the back.
  const pivot = { x: 12.7, y: 12.75, z: -20.3 };
  const { state, turns, deps } = jointWorld(0, { x: 0, y: 0, z: 0 });
  const start = turnedLocation(state.location, 0, 0, pivot);
  const turn = { name: "link", drive: -90, base: 90, pivot, speed: 60, acceleration: 200 };
  await playOut(createPlayer(deps), { turns: [turn] });
  assert.equal(((state.rotation % 360) + 360) % 360, 180);
  const unwrapped = turns.map((z) => z + 90);
  assert.ok(unwrapped.some((d) => Math.abs(d) < 5), "it did not pass through the front");
  for (let i = 1; i < unwrapped.length; i++) assert.ok(unwrapped[i] <= unwrapped[i - 1] + 1e-9);
  // The pivot has not moved.
  const a = (state.rotation * Math.PI) / 180;
  const px = state.location.x + Math.cos(a) * pivot.x - Math.sin(a) * pivot.y;
  const py = state.location.y + Math.sin(a) * pivot.x + Math.cos(a) * pivot.y;
  const p0x = start.x + pivot.x;
  const p0y = start.y + pivot.y;
  assert.ok(Math.abs(px - p0x) < 1e-6 && Math.abs(py - p0y) < 1e-6, "the pivot moved");
});

test("a turn takes as long as its drive's speed and acceleration say", async () => {
  const { deps } = jointWorld(0, { x: 0, y: 0, z: 0 });
  const turn = { name: "link", drive: -90, base: 90, pivot: { x: 0, y: 0, z: 0 }, speed: 60, acceleration: 200 };
  const seconds = await playOut(createPlayer(deps), { turns: [turn] });
  const expected = motionProfile(180, 60, 200).duration;
  assert.ok(Math.abs(seconds - expected) < 0.1, `took ${seconds}, expected ${expected}`);
});

function jawsWorld(y0, y1) {
  const at = { f0: y0, f1: y1 };
  const log = [];
  const deps = {
    readAxis: (i) => (i === 0 ? at.f0 : at.f1),
    setAxis: (i, _axis, value) => {
      at[i === 0 ? "f0" : "f1"] = value;
    },
    indexOf: (name) => ({ f0: 0, f1: 1 })[name],
    attach: () => {},
    skipping: () => false,
    grip: (gripper) => log.push({ grip: gripper, f0: at.f0 }),
    release: (gripper) => log.push({ release: gripper, f0: at.f0 }),
  };
  return { at, log, deps };
}

const jaws = (f0, f1) => ({
  jaws: {
    gripper: "g",
    fingers: [
      { name: "f0", y: f0 },
      { name: "f1", y: f1 },
    ],
    speed: 20,
    acceleration: 100,
    grip_point: { x: 0, y: 0, z: 0 },
  },
});

test("jaws that close take hold once they have closed", async () => {
  const { log, deps } = jawsWorld(110, -20);
  await playOut(createPlayer(deps), jaws(90, 0));
  assert.deepEqual(log, [{ grip: "g", f0: 90 }]);
});

test("jaws that open let go before they move", async () => {
  const { log, deps } = jawsWorld(90, 0);
  await playOut(createPlayer(deps), jaws(110, -20));
  assert.deepEqual(log, [{ release: "g", f0: 90 }]);
});

const box = (x0, y0, z0, x1, y1, z1) => ({
  min: { x: x0, y: y0, z: z0 },
  max: { x: x1, y: y1, z: z1 },
});

test("the jaws take the plate, not a well in it or the site under it", () => {
  const candidates = [
    { index: 1, category: "plate_holder", box: box(0, 0, 100, 127, 86, 100) },
    { index: 2, category: "plate", box: box(0, 0, 97, 128, 85, 111) },
    { index: 3, category: "well", box: box(60, 40, 98, 67, 47, 111) },
    { index: 4, category: "plate_carrier", box: box(-5, -5, 0, 140, 500, 120) },
  ];
  assert.equal(heldAt({ x: 64, y: 43, z: 104 }, candidates), 2);
  assert.equal(heldAt({ x: 300, y: 43, z: 104 }, candidates), undefined);
});

test("a plate let go of lands on the site under it", () => {
  const plate = box(200, 0, 97, 328, 85, 111);
  const sites = [
    { index: 1, category: "plate_holder", box: box(0, 0, 100, 127, 86, 100) },
    { index: 5, category: "plate_holder", box: box(200, 0, 100, 327, 86, 100) },
    { index: 6, category: "plate_holder", box: box(200, 0, 40, 327, 86, 40) },
  ];
  // The skirt sits 3 mm into the site: its surface stands above the plate's bottom.
  assert.equal(siteUnder(plate, sites, { index: 9, category: "plate" }), 5);
  // Carried away from any site, it has none.
  assert.equal(siteUnder(box(600, 0, 97, 728, 85, 111), sites, { index: 9 }), undefined);
});

// A plate 14.2 mm tall on a site at z 100, seated 3 mm into it, and its lid, 8.9 mm tall, nesting
// 7.6 mm over it - the demo's Corning plate.
const site = { index: 1, category: "plate_holder", box: box(0, 0, 100, 127, 86, 100) };
const plateOn = (index, covered = false) => ({
  index,
  category: "plate",
  covered,
  box: box(0, 0, 97, 128, 85, 111.2),
});
const lidAt = (z) => box(0, 0, z, 128, 85, z + 8.9);
const LID = { index: 7, category: "lid", nesting: 7.6 };

test("the jaws take the top lid of a nested stack, not the one under it", () => {
  // Hamilton's ComfortLid: 8.5 tall, 6.7 apart in a stack. Gripped 5 below its top, the point is
  // in the top lid and 1.7 above the one under it: within reach of both.
  const stack = [0, 1, 2, 3, 4].map((i) => ({
    index: 10 + i,
    category: "lid",
    box: box(0, 0, 200 + 6.7 * i, 127.5, 85.3, 208.5 + 6.7 * i),
  }));
  const top = 200 + 6.7 * 4 + 8.5;
  assert.equal(heldAt({ x: 64, y: 43, z: top - 5 }, stack), 14);
  assert.equal(heldAt({ x: 64, y: 43, z: top - 5 }, [...stack].reverse()), 14);
});

test("the jaws take the lid at its height, and the plate below it", () => {
  const candidates = [plateOn(2), { index: 7, category: "lid", box: lidAt(103.6) }];
  assert.equal(heldAt({ x: 64, y: 43, z: 109 }, candidates), 7);
  assert.equal(heldAt({ x: 64, y: 43, z: 101 }, candidates), 2);
});

test("a lid let go of over a plate with none becomes that plate's lid", () => {
  // Where it sits on the plate: the plate's top less the nesting.
  assert.equal(siteUnder(lidAt(103.6), [site, plateOn(2)], LID), 2);
});

test("a lid is not put on a plate that has one, nor is a plate put on a plate", () => {
  assert.equal(siteUnder(lidAt(103.6), [site, plateOn(2, true)], LID), 1);
  assert.equal(siteUnder(box(0, 0, 111, 128, 85, 125), [plateOn(2)], { category: "plate" }), undefined);
});

test("a lid let go of on an empty site lands on the site", () => {
  assert.equal(siteUnder(lidAt(100), [site], LID), 1);
});

test("a tip is handed over where it stands, then seated where the model gives it", async () => {
  const world = fakeWorld(start());
  const deps = world.deps();
  const placed = [];
  deps.attach = (name, parent, where) => placed.push({ name, parent, where });
  const location = { x: -0.6, y: -0.6, z: -2.05 };
  const request = {
    ...pickUp,
    attach: [{ name: "tip", parent: "shaft0", location, rotation: { x: 0, y: 0, z: 0 } }],
  };
  await playOut(createPlayer(deps), request);
  // No place given with the handover: it keeps where it is, and moves only as a seating.
  assert.deepEqual(placed, [{ name: "tip", parent: "shaft0", where: undefined }]);
  assert.deepEqual(world.at("tip"), [-0.6, -0.6, -2.05]);
  const seating = world.log.filter((e) => e.name === "tip" && e.axis === 2).map((e) => e.value);
  assert.ok(seating.length > 2, "the tip jumped to its seat instead of moving there");
});

// -- the jerk-limited profile -----------------------------------------------------------------------

test("an S-curve takes as long as the fitted X-arm model says, short moves and long", () => {
  // Reference durations from `tools/hxusbcomm_timing.py`'s `move_time` at the fitted X-arm limits.
  const expected = { 1: 0.21522, 10: 0.463676, 100: 0.99896, 500: 1.709912 };
  for (const [d, t] of Object.entries(expected)) {
    const { duration } = motionProfile(Number(d), 600, 1297, 3210);
    assert.ok(Math.abs(duration - t) < 1e-4, `${d} mm: ${duration} s, expected ${t} s`);
  }
});

test("an S-curve starts and ends at rest, climbs monotonically and is symmetric", () => {
  for (const d of [1, 10, 100, 500]) {
    const { duration, progress } = motionProfile(d, 600, 1297, 3210);
    assert.equal(progress(0), 0);
    assert.equal(progress(duration), 1);
    let last = 0;
    const n = 200;
    for (let k = 1; k <= n; k++) {
      const p = progress((duration * k) / n);
      assert.ok(p >= last - 1e-12, `${d} mm goes backwards at step ${k}`);
      last = p;
    }
    // Rest to rest, mirrored: as far in at a quarter as short of the end at three quarters.
    assert.ok(Math.abs(progress(duration / 4) - (1 - progress((3 * duration) / 4))) < 1e-9);
    // And it starts gently: jerk-limited, so the first hundredth covers far less than a hundredth.
    assert.ok(progress(duration / 100) < 0.001);
  }
});

test("without a jerk the profile is the simulator's trapezoid", () => {
  const withNone = motionProfile(100, 250, 800);
  const withInfinite = motionProfile(100, 250, 800, Number.POSITIVE_INFINITY);
  assert.ok(Math.abs(withNone.duration - (100 / 250 + 250 / 800)) < 1e-9);
  assert.equal(withInfinite.duration, withNone.duration);
});
