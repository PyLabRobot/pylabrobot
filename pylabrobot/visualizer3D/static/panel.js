// The selected resource: its box in the scene, the hover box, the info panel that shows what it
// is and holds, and the listeners told when the selection changes.

import * as THREE from "three";

import { HOVER, SELECT, SELECTION_SHOWN_MS } from "./constants.js";
import { OVERLAY_ORDER, stateOf, worldBox } from "./drawn.js";
import { escapeHtml, fmt, liquid, NBSP, section, tuple, withUnit } from "./format.js";
import { invalidate } from "./frame.js";
import { view } from "./renderer.js";
import { modelOf, sizeOf, world } from "./world.js";

export let selected = -1;

export const selectionBox = new THREE.Box3Helper(new THREE.Box3(), new THREE.Color(SELECT));

selectionBox.visible = false;

view.add(selectionBox);

export const hoverBox = new THREE.Box3Helper(new THREE.Box3(), new THREE.Color(HOVER));

hoverBox.visible = false;

view.add(hoverBox);

// Both highlights draw over everything. A hairline box behind translucent walls, landing on the
// resource's own outline, is a highlight nobody can see.
for (const helper of [selectionBox, hoverBox]) {
  helper.material.depthTest = false;
  helper.material.transparent = true;
  helper.renderOrder = OVERLAY_ORDER + 30;
}

export let infoPanel = null;

function ensureInfoPanel() {
  if (infoPanel?.isConnected) return infoPanel;
  infoPanel = document.createElement("div");
  infoPanel.className = "uml-panel";
  document.querySelector("main").appendChild(infoPanel);
  return infoPanel;
}

export function refreshPlacement(index) {
  if (selected !== index || !infoPanel?.isConnected) return;
  const o = index * 6;
  const xf = world.local;
  const m = world.matrices[index].elements;
  const local = infoPanel.querySelector('[data-live="location"]');
  const global = infoPanel.querySelector('[data-live="world"]');
  if (local) local.textContent = tuple(xf[o], xf[o + 1], xf[o + 2], "mm");
  if (global) global.textContent = tuple(m[12], m[13], m[14], "mm");
}

// Who follows the selection: the tree, which opens to the selected row and marks it. Told the
// index, or -1 when the selection is cleared.
const selectionListeners = [];

export function onSelection(listener) {
  selectionListeners.push(listener);
}

function announceSelection(index) {
  for (const listener of selectionListeners) listener(index);
}

function hideInfoPanel() {
  if (infoPanel) infoPanel.remove();
  infoPanel = null;
}

/** No selection: the box goes, the panel goes, and the tree is told. */
export function clearSelection() {
  selected = -1;
  selectionBox.visible = false;
  hideInfoPanel();
  announceSelection(-1);
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && infoPanel?.isConnected) clearSelection();
});

// Shown by the panel's own sections, so they must not appear again under Specifics.
const HANDLED = new Set([
  "type",
  "category",
  "methods",
  "model",
  "size_x",
  "size_y",
  "size_z",
  "max_volume",
  "volume",
  "pending_volume",
  "height_volume_data",
  "ordering",
]);

// State shown under Placement or Contents, or that names the resource itself (`thing`).
const STATE_HANDLED = new Set([
  "rotation",
  "volume",
  "pending_volume",
  "max_volume",
  "thing",
  "tip",
  "pending_tip",
  "tip_state",
]);

// A field that records how a resource was constructed rather than what it is now. These are kept,
// not hidden, but put under a heading that says what they are: a deck reports `with_trash: false`
// while holding a trash, and a reader has to be able to see that without being misled by it.
const isConstruction = (key) => key.startsWith("with_") || key === "core_grippers";

// Per-category panel contributions. This is the seam a package that defines a resource would
// write into; everything works without an entry, which is what makes it a default rather than a
// registry every new type must join.
const PANELS = {
  deck: { note: "Construction flags describe how the deck was built, not what it now holds." },
};

// Values go through innerHTML, and a resource name is user data. Escape it, or a model field
// holding `<resource>` disappears into the markup.
export function renderInfoPanel() {
  if (selected < 0) return hideInfoPanel();
  const panel = ensureInfoPanel();
  const index = selected;
  const model = modelOf(index);
  const contributed = PANELS[model.category] ?? {};
  const m = world.matrices[index].elements;
  const o = index * 6;
  const xf = world.local;
  const state = stateOf.get(index);

  const identity = [
    ["name", escapeHtml(world.names[index])],
    ["type", escapeHtml(model.type)],
    // Always shown: a resource without a model says so rather than leaving the row out.
    ["model", model.model ? escapeHtml(String(model.model)) : "none"],
    ["category", escapeHtml(model.category ?? "uncategorised")],
  ];

  const placement = [
    ["location", `<span data-live="location">${tuple(xf[o], xf[o + 1], xf[o + 2], "mm")}</span>`],
    ["world", `<span data-live="world">${tuple(m[12], m[13], m[14], "mm")}</span>`],
  ];
  if (xf[o + 3] || xf[o + 4] || xf[o + 5]) {
    placement.push(["rotation", tuple(xf[o + 3], xf[o + 4], xf[o + 5], "deg")]);
  }
  placement.push([
    "parent",
    world.parentOf[index] >= 0 ? escapeHtml(world.names[world.parentOf[index]]) : "none",
  ]);
  placement.push(["children", String(world.childrenOf[index].length)]);

  const [sx, sy, sz] = sizeOf(model);
  const geometry = [
    ["size", `${fmt(sx)}${NBSP}&#215;${NBSP}${fmt(sy)}${NBSP}&#215;${NBSP}${fmt(sz)}${NBSP}mm`],
  ];
  if (model.ordering) geometry.push(["items", String(Object.keys(model.ordering).length)]);

  const contents = [];
  const volume = liquid(model, state);
  if (volume) contents.push(["volume", `<span data-live="volume">${volume}</span>`]);
  // A spot or a shaft holds its tip as a child, and the tip's own state says what is in it.
  const tip = world.childrenOf[index].find((i) => modelOf(i).category === "tip");
  if (tip !== undefined) {
    const held = `${escapeHtml(world.names[tip])}, ${liquid(modelOf(tip), stateOf.get(tip))}`;
    contents.push(["tip", `<span data-live="tip">${held}</span>`]);
  }

  const specifics = [];
  const construction = [];
  for (const [key, value] of Object.entries(model)) {
    if (HANDLED.has(key)) continue;
    (isConstruction(key) ? construction : specifics).push([key, withUnit(key, value)]);
  }

  const tracker = state
    ? Object.entries(state)
        .filter(([k]) => !STATE_HANDLED.has(k))
        .map(([k, v]) => [k, withUnit(k, v)])
    : [];

  const methods = (model.methods ?? [])
    .map((signature) => `<div class="uml-method">${escapeHtml(signature)}</div>`)
    .join("");

  panel.innerHTML =
    `<button class="uml-close-btn" title="Close">&times;</button>` +
    `<div class="uml-header">` +
    `<div class="uml-header-name">${escapeHtml(world.names[index])}</div>` +
    `<div class="uml-header-type">${escapeHtml(model.type)} &middot; ${escapeHtml(model.category ?? "uncategorised")}</div>` +
    `</div>` +
    section("Identity", identity) +
    section("Placement", placement) +
    section("Geometry", geometry) +
    section("Contents", contents) +
    section("Specifics", specifics) +
    section("Tracker state", tracker) +
    section("Construction", construction, contributed.note) +
    // The one section that is a list rather than a fact about the part. Sixty signatures push
    // everything a reader came for off the top of the panel, so it opens shut and says how many are
    // behind it.
    (methods
      ? `<div class="uml-separator"></div><div class="uml-section">` +
        `<details class="uml-methods-block"><summary class="uml-section-title">` +
        `Methods <span class="uml-count">${model.methods.length}</span></summary>` +
        `<div class="uml-methods">${methods}</div></details></div>`
      : "");

  panel.querySelector(".uml-close-btn").addEventListener("click", clearSelection);
}

/** The selection put back after a rebuild: no flash of the box, the panel only if it was up. */
export function restoreSelection(index, withPanel) {
  selected = index;
  announceSelection(index);
  if (withPanel) renderInfoPanel();
}

// Selecting and inspecting are separate, as they are in the existing visualizer: a click in the
// viewport selects, a double click opens the panel, and a click in the tree does both. An already
// open panel follows the selection rather than being left showing something else.
let selectionTimer = null;

// The selection box stays a moment and goes, as the existing visualizer's dashed rectangle does.
// A timer the page set for itself asks for the one frame that removes it; keeping the loop
// running for the whole moment cost two seconds of frames on every click.
function hideSelectionLater() {
  clearTimeout(selectionTimer);
  selectionTimer = setTimeout(() => {
    selectionTimer = null;
    selectionBox.visible = false;
    invalidate();
  }, SELECTION_SHOWN_MS);
}

export function select(index, openPanel = true) {
  selected = index;
  selectionBox.box.copy(worldBox(index));
  selectionBox.visible = true;
  hideSelectionLater();
  announceSelection(index);
  if (openPanel || infoPanel?.isConnected) renderInfoPanel();
}

export function showHoverBox(index) {
  hoverBox.box.copy(worldBox(index));
  hoverBox.visible = true;
}
