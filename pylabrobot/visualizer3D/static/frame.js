// The render loop and what it costs. A frame is drawn only when something asked for one, the loop
// stops when the scene settles, and the page steps its own quality down while frames are slow.

import * as THREE from "three";
import {
  QUALITY_FAST_MS,
  QUALITY_HOLD_MS,
  QUALITY_LEVELS,
  QUALITY_RECOVER_MS,
  QUALITY_SETTLE_MS,
  QUALITY_SLOW_MS,
  QUALITY_WARMUP_MS,
  SKY_LIGHT,
  SKY_LIGHT_WITHOUT_ENVIRONMENT,
} from "./constants.js";

// What the loop draws with and where. Given once by the page, before anything asks for a frame.

let renderer = null;
let view = null;
let viewportEl = null;
let cameraNow = () => null;
let skyLight = null;

// How long the last frame took: this thread's work or the gap since the frame before, whichever is
// longer. What the pointer gate reads: a slow frame is a machine that cannot answer every sample.
export let lastFrameMs = 0;

// The page steps its own cost down while frames are slow and back up once they are fast, so a
// machine that cannot draw the scene at full quality still draws it at a usable rate without
// anyone naming its renderer. `?quality=low` pins the lowest level; any other value pins the top.
export const qualityPinned = new URLSearchParams(location.search).get("quality");
let quality = qualityPinned === "low" ? QUALITY_LEVELS - 1 : 0;
let frameCostAverage = 0;
let slowSince = null;
let fastSince = null;
let sceneCameAt = performance.now();
const demotedAt = new Map(); // level -> when it was last found too slow

// What a frame runs, in the order the page registered it. A mover is given the seconds since the
// last frame and says whether it is still moving; a preparer works out what this frame draws;
// a finisher runs after the draw, over it.
const movers = [];
const preparers = [];
const finishers = [];

export function whileMoving(mover) {
  movers.push(mover);
}

export function beforeDraw(preparer) {
  preparers.push(preparer);
}

export function afterDraw(finisher) {
  finishers.push(finisher);
}

export function applyQuality(level) {
  quality = Math.max(0, Math.min(QUALITY_LEVELS - 1, level));
  renderer.setPixelRatio(quality >= 1 ? 1 : Math.min(window.devicePixelRatio, 2));
  // The drawing buffer follows the pixel ratio only through setSize.
  renderer.setSize(viewportEl.clientWidth || 1, viewportEl.clientHeight || 1);
  view.environment = quality >= 2 ? null : (view.userData.roomEnvironment ?? null);
  skyLight.intensity = view.environment ? SKY_LIGHT : SKY_LIGHT_WITHOUT_ENVIRONMENT;
}

export function initFrame(deps) {
  ({ renderer, view, viewportEl, skyLight } = deps);
  cameraNow = deps.camera;
  if (quality > 0) applyQuality(quality);
}

let renderPending = true;
let lastRenderAt = 0;
let looping = false;
const clock = new THREE.Clock();

const statsEl = document.getElementById("stats-panel");
let frames = 0;
let lastSample = performance.now();
let stats = {};

let lastDrawCalls = 0;

function drawStats(rate) {
  // three zeroes its counters between frames, so an idle viewer would otherwise report no draws at
  // all. What the scene costs when it is drawn does not change just because it is not being drawn.
  const info = renderer.info.render;
  const calls = info.drawCalls ?? info.calls ?? 0;
  if (calls > 0) lastDrawCalls = calls;
  const text =
    `instances  <b>${(stats.instances ?? 0).toLocaleString()}</b>   ` +
    `models <b>${stats.models ?? 0}</b>   ` +
    `draws <b>${lastDrawCalls}</b>   ` +
    `${renderer.backend?.isWebGPUBackend ? "WebGPU" : "WebGL2"}   ` +
    `<b>${rate}</b>` +
    `${quality > 0 ? `   quality <b>${quality === 1 ? "low" : "lowest"}</b>` : ""}\n` +
    `tree JSON ${((stats.legacy_bytes ?? 0) / 1024).toFixed(1)} kB  ` +
    `→ this scene <b>${((stats.scene_bytes ?? 0) / 1024).toFixed(1)} kB</b> (${stats.ratio ?? 0}×)`;
  // Writing the same markup back forces layout and paint for nothing, twice a second, forever.
  if (text !== statsEl.innerHTML) statsEl.innerHTML = text;
}

function updateStats() {
  frames++;
  const now = performance.now();
  if (now - lastSample < 500) return;
  const fps = Math.round((frames * 1000) / (now - lastSample));
  frames = 0;
  lastSample = now;
  drawStats(`${String(fps)} fps`);
}

// Read after each drawn frame. Down after slow frames have settled, up after fast ones have, and
// never back into a level found slow within QUALITY_HOLD_MS.
function adaptQuality(frameMs) {
  if (qualityPinned !== null) return;
  if (performance.now() - sceneCameAt < QUALITY_WARMUP_MS) return;
  frameCostAverage = frameCostAverage === 0 ? frameMs : frameCostAverage * 0.9 + frameMs * 0.1;
  const now = performance.now();
  if (frameCostAverage > QUALITY_SLOW_MS) {
    fastSince = null;
    slowSince ??= now;
    if (now - slowSince >= QUALITY_SETTLE_MS && quality < QUALITY_LEVELS - 1) {
      demotedAt.set(quality, now);
      applyQuality(quality + 1);
      slowSince = null;
      frameCostAverage = 0;
    }
  } else if (frameCostAverage < QUALITY_FAST_MS) {
    slowSince = null;
    fastSince ??= now;
    const above = quality - 1;
    const heldBack = above >= 0 && now - (demotedAt.get(above) ?? -Infinity) < QUALITY_HOLD_MS;
    if (now - fastSince >= QUALITY_RECOVER_MS && above >= 0 && !heldBack) {
      applyQuality(above);
      fastSince = null;
      frameCostAverage = 0;
    }
  } else {
    slowSince = null;
    fastSince = null;
  }
}

function drawFrame() {
  const frameStarted = performance.now();
  const delta = clock.getDelta();

  let moving = false;
  for (const mover of movers) if (mover(delta)) moving = true;

  if (!renderPending && !moving) {
    looping = false;
    renderer.setAnimationLoop(null);
    return;
  }
  renderPending = false;
  lastRenderAt = performance.now();

  for (const prepare of preparers) prepare();

  renderer.render(view, cameraNow());
  // What a frame costs is the longer of this thread's work and the gap since the last frame: the
  // GPU, or a rasteriser in another process, shows up only in the gap. The gap of an idle spell is
  // discarded where the loop is woken, so the first frame back is costed by its work alone.
  lastFrameMs = Math.max(performance.now() - frameStarted, delta * 1000);
  adaptQuality(lastFrameMs);
  for (const finish of finishers) finish();
  // Read after the draw, because it is the draw it reports.
  updateStats();
}

// Skipping the draw is not enough on its own: the per-frame callback alone costs a third of a core,
// because it is still called sixty times a second to decide there is nothing to do. So the loop is
// stopped outright when the scene settles, and started again by whatever changes it.
//
// Raised at the edges of the viewer rather than wherever something happens to change: a message
// arriving, an input, a resize, a call from outside. That way adding a function that changes the
// scene cannot forget to ask for a frame, which is a silent freeze - the failure this had five
// times over while it was the caller's job to remember.
export function invalidate() {
  renderPending = true;
  if (!looping) {
    looping = true;
    clock.getDelta(); // discard the idle gap, or the first frame back sees a huge delta
    // The frame-rate window restarts with the loop. Left running across the idle gap, the first
    // frame back averages out to nothing and reads as "0 fps".
    frames = 0;
    lastSample = performance.now();
    renderer.setAnimationLoop(drawFrame);
  }
}

/** The scene's own measurements, as the server sent them. */
export function setStats(measurements) {
  stats = measurements;
}

export function statsNow() {
  return stats;
}

// Quoting a frame rate while nothing is being drawn would be a lie, so an idle viewer says so. This
// runs on a timer rather than in the loop, because the loop is exactly what has stopped.
function reportIdle() {
  if (performance.now() - lastRenderAt > 400) drawStats("idle");
}

setInterval(reportIdle, 500);

export function qualityNow() {
  return quality;
}

/** A scene has arrived: new pipelines to compile, so the frame cost is not judged until they have. */
export function sceneArrived() {
  sceneCameAt = performance.now();
  frameCostAverage = 0;
  slowSince = null;
  fastSince = null;
}
