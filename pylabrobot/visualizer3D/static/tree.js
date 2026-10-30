// The resource tree beside the viewport: rows built from the scene, expanded and revealed on
// demand, searched, sized, and kept in step with the selection and the hover.

import { colorFor } from "./boxes.js";
import { CONTENTS, HOLDERS, SEARCH_CONTAINERS, TREE_HIDDEN } from "./constants.js";
import { input, query } from "./dom.js";
import { hiddenNames, isVisible, stateOf, worldBox } from "./drawn.js";
import { escapeHtml, fmt, hexOf } from "./format.js";
import { setHidden } from "./live.js";
import {
  hoverBox,
  infoPanel,
  onSelection,
  restoreSelection,
  select,
  selected,
  showHoverBox,
} from "./panel.js";
import { camera, controls, frameBox, resize, VIEWS } from "./renderer.js";
import { modelOf, world } from "./world.js";

// "TipRack" -> "tipracks", as the existing visualizer writes them. Deliberately naive: a count is
// always in front of it, so "1 plates" reads as a count rather than as a mistake.

const plural = (type) => `${String(type).toLowerCase()}s`;

// The plural naming these resources, from the first of them, as the existing visualizer counts a
// carrier: "3 plates" says what a carrier is for even when one site holds something else.
function countable(indices) {
  return plural(modelOf(indices[0]).type);
}

const treeEl = document.getElementById("resource-tree");

const rowOf = new Map();

const expanded = new Set();

function eyeSvg(hidden) {
  return hidden
    ? '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5c2.6 3.3 5.9 5 9 5s6.4-1.7 9-5" stroke-width="2.2"/></svg>'
    : '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3.07 12C5.23 8.2 8.43 6 12 6s6.77 2.2 8.93 6c-2.16 3.8-5.36 6-8.93 6s-6.77-2.2-8.93-6Z" stroke-width="2.2"/><circle cx="12" cy="12" r="3.8" fill="currentColor" stroke="none"/></svg>';
}

function shortName(index) {
  const parent = world.parentOf[index];
  const name = world.names[index];
  if (parent < 0) return name;
  const prefix = `${world.names[parent]}_`;
  return name.startsWith(prefix) ? name.slice(prefix.length) : name;
}

// What the tree says about a resource beyond its name and type: a carrier counts what it holds by
// kind, a rack how many of its spots are taken, a plate its well count, a container its volume, and
// a site with nothing in it says so. The existing visualizer's tree answers the same questions.
function summaryOf(index) {
  const children = world.childrenOf[index];

  // An adapter carries one thing, and its row says which, as the existing visualizer's does.
  if (modelOf(index).category === "plate_adapter") {
    return children.length ? shortName(children[0]) : "empty";
  }

  if (!children.length) {
    const state = stateOf.get(index);
    if (state?.volume !== undefined) return `${fmt(state.volume)} uL`;
    // A vacant site is labelled `<empty>` in place of its name, so a summary would repeat it.
    return "";
  }

  // An occupied holder needs no summary: the row directly beneath it says what is standing there.
  if (HOLDERS.has(modelOf(index).category)) return "";

  const kind = modelOf(children[0]).category;

  if (kind === "tip_spot") {
    // A tip is a resource standing in its spot, so a spot holds one when the tree says so.
    const filled = children.filter((c) => world.childrenOf[c].length > 0).length;
    return `${filled}/${children.length} tips`;
  }
  if (kind === "well") return `${children.length} wells`;
  if (kind === "tube") return `${children.length} tubes`;

  // Look through holders to what stands in them, so the count names the contents. With every site
  // empty there is nothing to name, and the useful fact is how many positions there are.
  if (HOLDERS.has(kind)) {
    const held = children.map((c) => world.childrenOf[c][0]).filter((c) => c !== undefined);
    if (!held.length) return `${children.length} sites`;
    return `${held.length} ${countable(held)}`;
  }

  // Nothing else is counted. A deck holding carriers, a waste block and an arm has no single
  // number worth quoting, and a device holding one deck has none either. The existing visualizer
  // is summarised the same way: carriers, racks and plates, and nothing above them.
  return "";
}

// How far apart two sites may stand in y and still count as the same row, in mm.
const SAME_ROW = 0.5;

// The sites on a carrier, in the order a reader takes them and numbered the way PyLabRobot numbers
// them. Holders arrive in whatever order they were assigned; what a person reads is the deck, so
// they are listed back to front, and left to right within a row. The numbers then run the other
// way down a column, because site 0 is the front one - and straight along a single row across.
//
// Null for anything that is not a carrier: only a resource whose children are all holders has
// sites at all.
function siteOrder(index) {
  const children = index >= 0 ? world.childrenOf[index] : [];
  if (children.length < 2 || !children.every((c) => HOLDERS.has(modelOf(c).category))) return null;
  const at = (i) => world.matrices[i].elements;
  const sorted = [...children].sort((a, b) => {
    const dy = at(b)[13] - at(a)[13];
    return Math.abs(dy) > SAME_ROW ? dy : at(a)[12] - at(b)[12];
  });
  const oneRow = sorted.every((c) => Math.abs(at(c)[13] - at(sorted[0])[13]) <= SAME_ROW);
  const number = new Map();
  sorted.forEach((c, i) => {
    number.set(c, oneRow ? i : sorted.length - 1 - i);
  });
  return { sorted, number };
}

// What the reader has open, by name, so a scene arriving does not fold the tree they opened, drop
// what they selected or close the panel they were reading. Taken before the new world replaces
// the old, since the indices held here mean nothing once it has.
export function rememberView() {
  if (!world) return null;
  return {
    open: [...expanded].map((i) => world.names[i]),
    selected: selected >= 0 ? world.names[selected] : null,
    panel: infoPanel?.isConnected === true,
  };
}

// What the tree lists below a row, in the order a reader takes them. Organised as the existing
// visualizer's tree is. The positions inside a container are left out: a plate already says how
// many wells it has. A deck is looked through, and what stood on it is listed left to right: the
// carriers are what a person came to find, not the surface under them. A holder is a numbered
// position, not a thing: the row is what stands in it, or the holder itself while it stands empty,
// so the vacancy still shows. Nothing about the viewport changes - this decides the panel only.
function treeChildren(index) {
  const listed = [];
  let sawDeck = false;
  for (const child of world.childrenOf[index]) {
    const category = modelOf(child).category;
    if (TREE_HIDDEN.has(category)) continue;
    if (category === "deck") {
      sawDeck = true;
      listed.push(...treeChildren(child));
      continue;
    }
    listed.push(child);
  }
  if (sawDeck) {
    listed.sort((a, b) => world.matrices[a].elements[12] - world.matrices[b].elements[12]);
  }
  const rows = [];
  for (const child of siteOrder(index)?.sorted ?? listed) {
    const held = HOLDERS.has(modelOf(child).category)
      ? world.childrenOf[child].filter((c) => !TREE_HIDDEN.has(modelOf(c).category))
      : [];
    rows.push(...(held.length ? held : [child]));
  }
  return rows;
}

function applyRowVisibility(index) {
  const entry = rowOf.get(index);
  if (!entry) return;
  const own = hiddenNames.has(world.names[index]);
  const inherited = !own && !isVisible(index);
  entry.row.classList.toggle("resource-hidden", !isVisible(index));
  // Parsing the eye's markup again on every message is what a refresh of the whole tree costs.
  if (entry.eyeHidden !== own) {
    entry.eye.innerHTML = eyeSvg(own);
    entry.eyeHidden = own;
  }
  entry.eye.classList.toggle("is-hidden", own);
  entry.eye.classList.toggle("inherited", inherited);
  entry.eye.title = inherited
    ? "Hidden by a parent - alt-click to reveal it"
    : own
      ? "Show"
      : "Hide";
}

function addRow(index, depth, before) {
  const model = modelOf(index);
  const children = treeChildren(index);

  const row = document.createElement("div");
  row.className = "tree-node-row";
  row.style.paddingLeft = `${8 + depth * 16}px`;
  row.dataset.index = index;
  row.tabIndex = 0;

  const arrow = document.createElement("span");
  arrow.className = `tree-node-arrow${children.length ? " has-children" : ""}`;
  arrow.textContent = children.length ? "▶" : "";
  row.appendChild(arrow);

  // A holder is a numbered position on its carrier, so what stands in it is labelled by that
  // number rather than by a name nobody chose - and the number takes the colour dot's place, as it
  // does in the existing visualizer. The holder itself only gets a row while it stands empty.
  const holder = HOLDERS.has(model.category);
  const parent = world.parentOf[index];
  const seat = holder ? index : parent >= 0 && HOLDERS.has(modelOf(parent).category) ? parent : -1;
  const number = seat < 0 ? undefined : siteOrder(world.parentOf[seat])?.number.get(seat);
  if (number !== undefined) {
    const site = document.createElement("span");
    site.className = "tree-node-site";
    site.textContent = String(number);
    row.appendChild(site);
  } else {
    const dot = document.createElement("span");
    dot.className = "tree-node-dot";
    dot.style.backgroundColor = hexOf(colorFor(model));
    row.appendChild(dot);
  }

  const name = document.createElement("span");
  name.className = "tree-node-name";
  // An empty site has nothing worth naming, and saying so is the point of showing it at all.
  const vacant = holder && !children.length;
  if (vacant) name.classList.add("tree-node-vacant");
  name.textContent = vacant ? "<empty>" : shortName(index);
  name.title = `${world.names[index]} (${model.type})`;
  row.appendChild(name);

  const type = document.createElement("span");
  type.className = "tree-node-type";
  type.textContent = vacant ? "" : model.type;
  row.appendChild(type);

  const info = document.createElement("span");
  info.className = "tree-node-info";
  info.textContent = summaryOf(index);
  row.appendChild(info);

  const eye = document.createElement("button");
  eye.className = "tree-eye-btn";
  eye.title = "Show or hide";
  eye.addEventListener("click", (e) => {
    e.stopPropagation();
    // Hidden by a parent, a plain click can do nothing and says so; alt-click clears the whole
    // chain above, as the existing visualizer's does.
    if (!hiddenNames.has(world.names[index]) && !isVisible(index)) {
      if (!e.altKey) return;
      for (let i = index; i >= 0; i = world.parentOf[i]) {
        if (hiddenNames.has(world.names[i])) setHidden(world.names[i], false);
      }
      return;
    }
    setHidden(world.names[index], !hiddenNames.has(world.names[index]));
  });
  row.appendChild(eye);

  row.addEventListener("mouseenter", () => showHoverBox(index));
  row.addEventListener("mouseleave", () => (hoverBox.visible = false));
  // As the existing visualizer's rows: a click opens the panel on the resource, a double click
  // frames it in the viewport, from wherever the camera is looking.
  // The arrow and the indent before it fold the row; the rest of it, from the name on, selects.
  const onArrow = (e) =>
    children.length > 0 && e.clientX <= arrow.getBoundingClientRect().right + 4;
  row.addEventListener("click", (e) => {
    if (onArrow(e)) toggle(index, !expanded.has(index));
    else select(index, true);
  });
  row.addEventListener("dblclick", (e) => {
    if (onArrow(e)) return;
    frameBox(worldBox(index), camera.position.clone().sub(controls.target));
  });

  treeEl.insertBefore(row, before ?? null);
  rowOf.set(index, { row, depth, arrow, info, eye, eyeHidden: null });
  applyRowVisibility(index);
  return row;
}

// Children only enter the DOM when a node is opened, so a deck of thousands of wells does not
// build thousands of rows to show four carriers.
function toggle(index, open) {
  const entry = rowOf.get(index);
  if (!entry || !treeChildren(index).length || open === expanded.has(index)) return;

  if (open) {
    expanded.add(index);
    entry.arrow.textContent = "▼";
    const before = entry.row.nextSibling;
    for (const child of treeChildren(index)) addRow(child, entry.depth + 1, before);
  } else {
    expanded.delete(index);
    entry.arrow.textContent = "▶";
    const drop = (i) => {
      for (const child of world.childrenOf[i]) {
        drop(child);
        const childEntry = rowOf.get(child);
        if (childEntry) {
          childEntry.row.remove();
          rowOf.delete(child);
          expanded.delete(child);
        }
      }
    };
    drop(index);
  }
}

function openPathTo(index) {
  const chain = [];
  for (let i = index; i >= 0; i = world.parentOf[i]) chain.unshift(i);
  for (const i of chain) toggle(i, true);
}

export function restoreView(kept) {
  if (!kept) return;
  for (const name of kept.open) {
    const index = world.indexOfName.get(name);
    if (index !== undefined) openPathTo(index);
  }
  const index = world.indexOfName.get(kept.selected);
  if (index !== undefined) restoreSelection(index, kept.panel);
}

// Whether opening this row would open a grid of positions rather than a level of the deck. Those
// rows exist - a mounting shaft is a real part - but a depth should not spend itself on ninety-six
// of them, so they open when they are asked for by name.
function holdsContentsOnly(index) {
  const children = treeChildren(index);
  return children.length > 0 && children.every((c) => CONTENTS.has(modelOf(c).category));
}

function showToDepth(maxDepth) {
  const walk = (index, depth) => {
    if (depth < maxDepth && !holdsContentsOnly(index)) {
      toggle(index, true);
      for (const child of treeChildren(index)) walk(child, depth + 1);
    } else {
      toggle(index, false);
    }
  };
  for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) walk(i, 0);
}

// tree actions
const depthInput = input("tree-depth-input");

export function buildTree() {
  treeEl.textContent = "";
  rowOf.clear();
  expanded.clear();
  for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) addRow(i, 0, null);
  showToDepth(Number(depthInput.value) || 1);
}

function expandAll(open) {
  if (!open) {
    for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) toggle(i, false);
    return;
  }
  const walk = (index) => {
    toggle(index, true);
    for (const child of treeChildren(index)) walk(child);
  };
  for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) walk(i);
}

export function refreshTreeVisibility() {
  for (const [index] of rowOf) applyRowVisibility(index);
}

export function refreshTreeInfo() {
  for (const [index, entry] of rowOf) {
    const text = summaryOf(index);
    if (entry.info.textContent !== text) entry.info.textContent = text;
  }
}

export function revealAndHighlight(index) {
  // A position inside a container has no row of its own, so the row to land on is the container
  // that names it - which is where a reader would look for it anyway.
  let at = index;
  while (at >= 0 && TREE_HIDDEN.has(modelOf(at).category)) at = world.parentOf[at];
  if (at < 0) at = index;
  const chain = [];
  for (let i = world.parentOf[at]; i >= 0; i = world.parentOf[i]) chain.unshift(i);
  for (const ancestor of chain) toggle(ancestor, true);
  for (const [, entry] of rowOf) entry.row.classList.remove("selected");
  const entry = rowOf.get(at);
  if (entry) {
    entry.row.classList.add("selected");
    entry.row.scrollIntoView({ block: "nearest" });
  }
}

let hoveredRow = null;

// Hovering a resource in the viewport marks its row in the tree, the mirror of hovering a row
// marking the resource. The existing visualizer does both, and only having one of them is what
// makes a tree feel disconnected from the scene.
export function markTreeRow(index) {
  const entry = index === null ? null : rowOf.get(index);
  if (entry === hoveredRow) return;
  hoveredRow?.row.classList.remove("canvas-hover");
  hoveredRow = entry ?? null;
  hoveredRow?.row.classList.add("canvas-hover");
}

// From the keyboard a row does what a click on it does: Enter selects, the arrows fold and
// unfold. Only the row itself - its eye button answers its own keys.
treeEl.addEventListener("keydown", (e) => {
  const row = /** @type {HTMLElement} */ (e.target);
  if (!row.classList.contains("tree-node-row")) return;
  const index = Number(row.dataset.index);
  if (e.key === "Enter") select(index, true);
  else if (e.key === "ArrowRight") toggle(index, true);
  else if (e.key === "ArrowLeft") toggle(index, false);
  else return;
  e.preventDefault();
});

const sidepanel = document.getElementById("sidepanel");

document.getElementById("toolbar-right-toggle").addEventListener("click", () => {
  sidepanel.classList.toggle("collapsed");
  resize();
});

let allExpanded = false;

document.getElementById("toggle-expand-btn").addEventListener("click", () => {
  allExpanded = !allExpanded;
  expandAll(allExpanded);
});

document
  .getElementById("collapse-all-btn")
  .addEventListener("click", () => showToDepth(Number(depthInput.value) || 0));

depthInput.addEventListener("change", () => showToDepth(Number(depthInput.value) || 0));

// search
const searchView = document.getElementById("search-view");

const searchInput = input("search-input");

const searchResults = document.getElementById("search-results");

const treeButton = document.getElementById("toolbar-tree-btn");

const searchButton = document.getElementById("toolbar-search-btn");

export function showPane(which) {
  const searching = which === "search";
  treeEl.style.display = searching ? "none" : "block";
  query(".sidepanel-header").style.display = searching ? "none" : "flex";
  searchView.style.display = searching ? "flex" : "none";
  treeButton.classList.toggle("active", !searching);
  searchButton.classList.toggle("active", searching);
  if (searching) searchInput.focus();
}

// Each rail button is a toggle, as the existing visualizer's are: it opens the panel on its own
// pane, switches panes when the other one is showing, and closes the panel when its own pane
// already is. Closing is a class, and the stylesheet's width beats the resize handle's inline one,
// so a panel dragged wider comes back the width it was left at.
function pickPane(which, button) {
  const showing = !sidepanel.classList.contains("collapsed") && button.classList.contains("active");
  sidepanel.classList.toggle("collapsed", showing);
  if (showing) button.classList.remove("active");
  else showPane(which);
  resize();
}

treeButton.addEventListener("click", () => pickPane("tree", treeButton));

searchButton.addEventListener("click", () => pickPane("search", searchButton));

// Matching as the existing visualizer matches: every term of the query must be found, as a
// subsequence, in the name or the type; an exact name outranks a prefix, a prefix a substring, a
// substring a mere subsequence; ties keep the scene's order. No cap on hits.
function fuzzyMatch(term, text) {
  let at = 0;
  for (let i = 0; i < text.length && at < term.length; i++) if (text[i] === term[at]) at++;
  return at === term.length;
}

function fuzzyScore(term, text) {
  if (text === term) return 4;
  if (text.startsWith(term)) return 3;
  if (text.includes(term)) return 2;
  return 1;
}

function runSearch() {
  if (!world) return;
  const query = searchInput.value.trim().toLowerCase();
  const includeWells = input("search-include-wells").checked;
  const includeTips = input("search-include-tips").checked;
  const includeSites = input("search-include-sites").checked;
  searchResults.textContent = "";
  if (!query) {
    searchResults.innerHTML = '<div class="search-empty">Type to search.</div>';
    return;
  }
  const terms = query.split(/\s+/);
  const hits = [];
  for (let i = 0; i < world.names.length; i++) {
    const category = modelOf(i).category;
    if (SEARCH_CONTAINERS.has(category) && !includeWells) continue;
    if (category === "tip_spot" && !includeTips) continue;
    if (HOLDERS.has(category) && !includeSites) continue;
    const name = world.names[i].toLowerCase();
    const type = String(modelOf(i).type ?? "").toLowerCase();
    let score = 0;
    for (const term of terms) {
      if (fuzzyMatch(term, name)) score += fuzzyScore(term, name);
      else if (fuzzyMatch(term, type)) score += fuzzyScore(term, type);
      else {
        score = -1;
        break;
      }
    }
    if (score >= 0) hits.push({ index: i, score });
  }
  hits.sort((a, b) => b.score - a.score || a.index - b.index);
  if (!hits.length) {
    searchResults.innerHTML = '<div class="search-empty">No resource matches.</div>';
    return;
  }
  for (const { index } of hits) {
    const row = document.createElement("div");
    row.className = "search-result";
    row.tabIndex = 0;
    row.innerHTML =
      `<span class="sr-name">${escapeHtml(world.names[index])}</span>` +
      `<span class="sr-type">${escapeHtml(modelOf(index).type)}</span>`;
    // A property, not markup: a declared colour is resource data, and could close the attribute.
    const dot = document.createElement("span");
    dot.className = "tree-node-dot";
    dot.style.backgroundColor = hexOf(colorFor(modelOf(index)));
    row.prepend(dot);
    row.addEventListener("mouseenter", () => showHoverBox(index));
    row.addEventListener("mouseleave", () => (hoverBox.visible = false));
    row.addEventListener("click", () => {
      showPane("tree");
      select(index);
      frameBox(worldBox(index), VIEWS.iso);
    });
    searchResults.appendChild(row);
  }
}

searchInput.addEventListener("input", runSearch);

searchResults.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  /** @type {HTMLElement} */ (e.target).closest(".search-result")?.click();
  e.preventDefault();
});

for (const id of ["search-include-wells", "search-include-tips", "search-include-sites"]) {
  document.getElementById(id).addEventListener("change", runSearch);
}

// sidepanel resize
const resizeHandle = document.getElementById("sidepanel-resize-handle");

let resizingFrom = null;

resizeHandle.addEventListener("pointerdown", (e) => {
  resizingFrom = { x: e.clientX, width: sidepanel.offsetWidth };
  resizeHandle.setPointerCapture(e.pointerId);
});

resizeHandle.addEventListener("pointermove", (e) => {
  if (!resizingFrom) return;
  const width = Math.max(
    150,
    Math.min(window.innerWidth * 0.6, resizingFrom.width - (e.clientX - resizingFrom.x)),
  );
  sidepanel.style.width = `${width}px`;
  resize();
});

resizeHandle.addEventListener("pointerup", () => (resizingFrom = null));

// The rows under the nearest ancestor that has one, listed again if it is open, so a moved
// resource is shown where it now stands. A holder has no row of its own; its carrier does.
export function reopenRowsUnder(index) {
  let at = index;
  while (at >= 0 && !rowOf.has(at)) at = world.parentOf[at];
  if (at < 0 || !expanded.has(at)) return;
  toggle(at, false);
  toggle(at, true);
}

// The tree follows the selection: it opens to the selected row and marks it, or unmarks all.
onSelection((index) => {
  if (index >= 0) revealAndHighlight(index);
  else for (const [, entry] of rowOf) entry.row.classList.remove("selected");
});
