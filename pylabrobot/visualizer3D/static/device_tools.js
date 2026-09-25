/**
 * The device-tools cluster: what a device pipettes and grips with, drawn from the resource tree.
 *
 * The existing visualizer keeps a parallel picture of this - the backend pushes `headState`,
 * `head96State` and `armState` onto a liquid handler, and the navbar draws those. Here the same
 * facts are already in the tree: a channel is a resource, a collected tip is a child of that
 * channel's mounting shaft, and what a gripper is holding is a child of the gripper. So nothing is
 * pushed and nothing can drift - the panels read the scene the viewport is drawing.
 *
 * It knows nothing about any one device. A resource that has pipetting channels, a head or a
 * gripper anywhere below it is a device, and everything else follows from the categories the tree
 * already uses.
 */

import { stateOf } from "./drawn.js";
import { escapeHtml } from "./format.js";
import { modelOf, world } from "./world.js";

// What the tree calls the parts a device works with.
const CHANNEL = "pipette_channel";
const HEADS = new Set(["head96", "head384"]);
const GRIPPER = "mechanical_gripper";
const SHAFT = "tip_mounting_shaft";
// A gripper's own parts, as opposed to whatever it has picked up: anything else below it is cargo.
const GRIPPER_PARTS = new Set(["body", "finger", "pad"]);

// What a device says it is. A STAR and a Prep both declare it, and it is the only thing that
// gets a button: a bench or a facility holds devices and is no feature of anything.
const DEVICE = "device";

// The panels draw what the existing visualizer's draw, at its sizes. A tip is drawn at its own
// length, millimetres at this many pixels each, clamped so eight channels still fit on a laptop.
const TIP_PX_PER_MM = 0.8;
const TIP_SHORTEST_MM = 10;
const TIP_LONGEST_MM = 80;
// The channel glyph above the tip, and the whole column, in pixels.
const CHANNEL_PX = 27;
const COLUMN_PX = CHANNEL_PX + TIP_LONGEST_MM * TIP_PX_PER_MM;
// Liquid in a tip, as the existing visualizer tints it.
const LIQUID_FILL = "rgba(0,119,187,0.45)";
// A head's grid: dot size and gap. The green a spot with a tip is drawn in is the stylesheet's.
const DOT_PX = 10;
const DOT_GAP_PX = 3;
// A gripper's fingers spread to what they hold, the plate scaled to this many pixels at most.
const PLATE_PX = 80;

// How far a panel sits below the button that opens it, and how far apart two panels sit.
const PANEL_DROP = 12;
const PANEL_GAP = 8;
const PANEL_EDGE = 8; // closest a panel comes to the edge of the viewport

const KINDS = [
  { kind: "multi", icon: "multi_channel_pipette.png", title: "Multi-channel pipettes" },
  { kind: "single", icon: "single_channel_pipette.png", title: "Single-channel pipettes" },
  { kind: "arm", icon: "integrated_arm.png", title: "Integrated arms" },
];

/**
 * @param {{onSelect: (i: number) => void}} deps
 * @returns {{rebuild: () => void, refresh: () => void}}
 */
export function initDeviceTools({ onSelect }) {
  const containerEl = document.getElementById("navbar-device-tools");
  const mainEl = document.querySelector("main");
  /** Open panels, by their own id. Each is { element, device, kind, button, offset }. */
  const open = new Map();
  /** Where the user dragged a panel, by id, until it is reset. */
  const moved = new Map();
  /** Devices whose panels have been shown: a device opens its panels once, when it arrives. */
  const shown = new Set();

  // ------------------------------------------------------------------ reading the tree

  /** Every index below `index`, itself included, that `keep` accepts. */
  function descendants(index, keep) {
    const found = [];
    const stack = [index];
    while (stack.length) {
      const at = stack.pop();
      if (at !== index && keep(at)) found.push(at);
      for (const child of world.childrenOf[at]) stack.push(child);
    }
    return found.sort((a, b) => a - b);
  }

  const categoryOf = (index) => modelOf(index).category ?? "";

  /** What a device carries: its channels, its heads and its grippers, in tree order. */
  function partsOf(index) {
    return {
      channels: descendants(index, (i) => categoryOf(i) === CHANNEL),
      heads: descendants(index, (i) => HEADS.has(categoryOf(i))),
      grippers: descendants(index, (i) => categoryOf(i) === GRIPPER),
    };
  }

  const carries = (parts) => parts.channels.length || parts.heads.length || parts.grippers.length;

  /**
   * The devices in the scene: every resource that declares itself one and pipettes or grips, in
   * tree order. A facility holding two instruments answers with both; whatever a device stands on
   * is not asked.
   */
  function devices() {
    const found = [];
    for (let i = 0; i < world.names.length; i++) {
      if (categoryOf(i) === DEVICE && carries(partsOf(i))) found.push(i);
    }
    return found;
  }

  /** The tip a channel is holding, as its model, or null. It hangs off the mounting shaft. */
  function tipOf(index) {
    for (const child of world.childrenOf[index]) {
      if (categoryOf(child) !== SHAFT) continue;
      const tip = world.childrenOf[child][0];
      if (tip !== undefined) return { index: tip, model: modelOf(tip) };
    }
    return null;
  }

  /**
   * A head's mounting shafts, placed on the grid their names describe - `A1` through `H12` on a
   * 96-head. A head that names them some other way falls back to one long row, which is honest
   * about not knowing rather than inventing a shape.
   */
  function gridOf(index) {
    const shafts = world.childrenOf[index].filter((c) => categoryOf(c) === SHAFT);
    const placed = shafts.map((shaft) => {
      const spot = /_([A-Z]+)(\d+)$/.exec(world.names[shaft]);
      return {
        index: shaft,
        row: spot ? spot[1].charCodeAt(spot[1].length - 1) - 65 : 0,
        column: spot ? Number(spot[2]) - 1 : shafts.indexOf(shaft),
        filled: world.childrenOf[shaft].length > 0,
      };
    });
    const rows = Math.max(1, ...placed.map((p) => p.row + 1));
    const columns = Math.max(1, ...placed.map((p) => p.column + 1));
    return { placed, rows, columns, filled: placed.filter((p) => p.filled).length };
  }

  /** What a gripper is holding, or -1. Its own body, fingers and pads are not cargo. */
  function heldBy(index) {
    return world.childrenOf[index].find((c) => !GRIPPER_PARTS.has(categoryOf(c))) ?? -1;
  }

  // ------------------------------------------------------------------ drawing a panel

  /** The channel's own number, as the tree names it, so the panel and the tree agree. */
  function channelLabel(index, fallback) {
    const numbered = /(\d+)$/.exec(world.names[index]);
    return numbered ? numbered[1] : String(fallback);
  }

  const glowFilter = (id, colour) =>
    `<filter id="${id}" x="-200%" y="-200%" width="500%" height="500%">` +
    `<feFlood flood-color="${colour}" flood-opacity="1" result="color"/>` +
    `<feComposite in="color" in2="SourceAlpha" operator="in" result="colored"/>` +
    `<feGaussianBlur in="colored" stdDeviation="4" result="glow1"/>` +
    `<feGaussianBlur in="colored" stdDeviation="8" result="glow2"/>` +
    `<feGaussianBlur in="colored" stdDeviation="14" result="glow3"/>` +
    `<feMerge><feMergeNode in="glow3"/><feMergeNode in="glow2"/><feMergeNode in="glow1"/>` +
    `<feMergeNode in="SourceGraphic"/></feMerge></filter>`;

  /** A hover glow on an SVG group, switched by the filter the group names in data-glow. */
  function glowOnHover(root) {
    for (const g of root.querySelectorAll("[data-glow]")) {
      g.addEventListener("mouseenter", () => g.setAttribute("filter", `url(#${g.dataset.glow})`));
      g.addEventListener("mouseleave", () => g.removeAttribute("filter"));
    }
  }

  /**
   * One channel as the existing visualizer draws it: its number, the channel as a black cylinder
   * on a silver collar, and the tip hanging from it at its own length, with the liquid it holds.
   */
  function channelColumn(index, ordinal, id) {
    const tip = tipOf(index);
    const label = channelLabel(index, ordinal);
    const name = world.names[index];
    let shapes =
      `<g data-index="${index}" data-glow="ch${id}" style="cursor:pointer">` +
      `<title>${escapeHtml(name)} - click to select</title>` +
      `<rect x="0" y="1" width="14" height="18" rx="3" ry="3" fill="#333"/>` +
      `<ellipse cx="7" cy="2" rx="7" ry="2" fill="#555"/>` +
      `<ellipse cx="7" cy="19" rx="7" ry="2" fill="#222"/>` +
      `<rect x="2" y="20" width="10" height="4" rx="2" ry="2" fill="#b0b0b0"/>` +
      `<ellipse cx="7" cy="20" rx="5" ry="1.5" fill="#ccc"/>` +
      `<ellipse cx="7" cy="24" rx="5" ry="1.5" fill="#999"/></g>`;
    if (tip) {
      const mm = Number(tip.model.size_z) || 0;
      const tipPx = Math.min(TIP_LONGEST_MM, Math.max(TIP_SHORTEST_MM, mm)) * TIP_PX_PER_MM;
      const collarY = 19.5;
      const collarH = 6.5;
      const bodyStart = collarY + collarH;
      const straightH = Math.round(tipPx * 0.4);
      const taperH = tipPx - straightH;
      const straightEnd = bodyStart + straightH;
      const tipEnd = straightEnd + taperH;
      const botW = mm > 50 ? 2 : 1;
      const botL = 7 - botW / 2;
      const botR = 7 + botW / 2;
      // What the tip holds, when its state says: a tip publishes its volume once it tracks one.
      const state = stateOf.get(tip.index) ?? {};
      const max = Number(state.max_volume) || 0;
      const ratio = max > 0 ? Math.min(1, (Number(state.volume) || 0) / max) : 0;
      let fill = "";
      if (ratio > 0) {
        const fillH = ratio * (straightH + taperH);
        if (fillH <= taperH) {
          const width = botW + (8 - botW) * (fillH / taperH);
          const top = tipEnd - fillH;
          fill = `<polygon points="${botL},${tipEnd} ${botR},${tipEnd} ${7 + width / 2},${top} ${7 - width / 2},${top}" fill="${LIQUID_FILL}"/>`;
        } else {
          const bodyH = fillH - taperH;
          fill =
            `<polygon points="3,${straightEnd} 11,${straightEnd} ${botR},${tipEnd} ${botL},${tipEnd}" fill="${LIQUID_FILL}"/>` +
            `<rect x="3" y="${straightEnd - bodyH}" width="8" height="${bodyH}" fill="${LIQUID_FILL}"/>`;
        }
      }
      shapes +=
        `<g data-index="${tip.index}" data-glow="tip${id}" style="cursor:pointer">` +
        `<title>${escapeHtml(world.names[tip.index])}, ${mm} mm - click to select</title>` +
        `<rect x="1.5" y="${collarY}" width="11" height="${collarH}" rx="0.5" ry="0.5" fill="#c8c8c8" fill-opacity="0.5" stroke="#888" stroke-width="0.8"/>` +
        `<rect x="3" y="${bodyStart}" width="8" height="${straightH}" rx="1" ry="1" fill="#d0d0d0" stroke="#888" stroke-width="0.8"/>` +
        `<polygon points="3,${straightEnd} 11,${straightEnd} ${botR},${tipEnd} ${botL},${tipEnd}" fill="#d0d0d0" stroke="#888" stroke-width="0.8"/>` +
        `${fill}</g>`;
    }
    return (
      `<div class="dt-column">` +
      `<span class="dt-column-label" data-index="${index}" title="${escapeHtml(name)}">${escapeHtml(label)}</span>` +
      `<svg width="14" height="${COLUMN_PX}" viewBox="0 0 14 ${COLUMN_PX}" style="overflow:visible;display:block">` +
      `<defs>${glowFilter(`ch${id}`, "#99DDFF")}${glowFilter(`tip${id}`, "#EEDD88")}</defs>` +
      `${shapes}</svg></div>`
    );
  }

  function fillSingle(panel, device) {
    const { channels } = partsOf(device);
    if (!channels.length) {
      panel.innerHTML = `<span class="dt-empty">No single-channel pipette is installed on this device.</span>`;
      return;
    }
    panel.innerHTML = channels
      .map((index, i) => channelColumn(index, i, `${device}_${i}`))
      .join("");
    glowOnHover(panel);
  }

  /**
   * A head as the existing visualizer draws it: a dark block with one dot a position, green where
   * a tip is on, and the four bars that carry it. A dot selects the tip, or the shaft when empty.
   */
  function headBlock(index, label) {
    const grid = gridOf(index);
    const dots = grid.placed
      .map((spot) => {
        const tip = world.childrenOf[spot.index][0];
        const target = tip === undefined ? spot.index : tip;
        const title = tip === undefined ? world.names[spot.index] : world.names[tip];
        return (
          `<i class="dt-dot${spot.filled ? " is-filled" : ""}" data-index="${target}"` +
          ` style="grid-row:${spot.row + 1};grid-column:${spot.column + 1}"` +
          ` title="${escapeHtml(title)} - click to select"></i>`
        );
      })
      .join("");
    const gridW = grid.columns * DOT_PX + (grid.columns - 1) * DOT_GAP_PX;
    const gridH = grid.rows * DOT_PX + (grid.rows - 1) * DOT_GAP_PX;
    const hBar = `<span class="dt-bar" style="width:${Math.round(gridW * 0.6)}px;height:4px"></span>`;
    const vBar = `<span class="dt-bar" style="width:4px;height:${Math.round(gridH * 0.6)}px"></span>`;
    return (
      `<div class="dt-column">` +
      (label === null ? "" : `<span class="dt-column-label">${escapeHtml(label)}</span>`) +
      `${hBar}<div class="dt-head-row">${vBar}` +
      `<div class="dt-head" data-index="${index}" title="${escapeHtml(world.names[index])} - click to select">` +
      `<div class="dt-grid" style="grid-template-columns:repeat(${grid.columns},${DOT_PX}px);` +
      `grid-template-rows:repeat(${grid.rows},${DOT_PX}px);gap:${DOT_GAP_PX}px">${dots}</div>` +
      `</div>${vBar}</div>${hBar}</div>`
    );
  }

  function fillMulti(panel, device) {
    const { heads } = partsOf(device);
    if (!heads.length) {
      panel.innerHTML = `<span class="dt-empty">No multi-channel pipette is installed on this device.</span>`;
      return;
    }
    panel.innerHTML = heads
      .map((index, i) => headBlock(index, heads.length > 1 ? String(i) : null))
      .join("");
  }

  /** What a held plate's wells lie on, read off its ordering: rows by letter, columns by number. */
  function wellGridOf(model) {
    const ids = Object.keys(model.ordering ?? {});
    let rows = 0;
    let columns = 0;
    for (const id of ids) {
      const m = /^([A-Z]+)(\d+)$/.exec(id);
      if (!m) return null;
      rows = Math.max(rows, m[1].charCodeAt(m[1].length - 1) - 64);
      columns = Math.max(columns, Number(m[2]));
    }
    return rows && columns ? { rows, columns } : null;
  }

  /**
   * A gripper as the existing visualizer draws it: a carriage on two rails whose fingers spread
   * to the plate they hold, the plate drawn between them to scale with its wells.
   */
  function gripperFigure(index, label) {
    const held = heldBy(index);
    const holding = held >= 0 ? modelOf(held) : null;
    let plateW = 52;
    let plateH = 22;
    if (holding) {
      const scale = Math.min(PLATE_PX / (holding.size_x || 127), PLATE_PX / (holding.size_y || 86));
      plateW = Math.round((holding.size_x || 127) * scale);
      plateH = Math.round((holding.size_y || 86) * scale);
    }
    const stdPlateW = Math.round(127 * Math.min(PLATE_PX / 127, PLATE_PX / 86));
    const minGap = Math.round((stdPlateW + 16) * 1.1);
    const closedGap = Math.round((52 + 16) * 1.1);
    const gap = holding ? Math.round((plateW + 16) * 1.1) : closedGap;
    const svgW = Math.max(closedGap, gap, minGap) + 28;
    const svgH = 110;
    const cx = svgW / 2;
    const lRail = cx - gap / 2 - 7;
    const rRail = cx + gap / 2 - 1;
    const carriageW = closedGap + 8;
    const carriageX = cx - carriageW / 2;
    const pinTop = 85 - 5 - 1.2;
    const pinBot = 85 + 5 - 1.2;
    const lCush = lRail + 9;
    const rCush = rRail + 1 - 6;
    let shapes =
      `<rect x="${lRail + 7}" y="10" width="12" height="5.1" rx="1" fill="#555" stroke="#444" stroke-width="0.6"/>` +
      `<rect x="${rRail + 1 - 12}" y="10" width="12" height="5.1" rx="1" fill="#555" stroke="#444" stroke-width="0.6"/>` +
      `<rect x="${lRail}" y="10" width="7" height="75" fill="#333" stroke="#222" stroke-width="1"/>` +
      `<rect x="${rRail + 1}" y="10" width="7" height="75" fill="#333" stroke="#222" stroke-width="1"/>` +
      `<rect x="${carriageX}" y="0" width="${carriageW}" height="24" rx="2" fill="#aaa" stroke="#666" stroke-width="1.2"/>` +
      `<rect x="${carriageX}" y="0" width="${carriageW}" height="7" rx="2" fill="#888" stroke="#666" stroke-width="1.2"/>` +
      `<rect x="${cx - 4}" y="3" width="8" height="18" fill="#666" stroke="#555" stroke-width="0.8"/>`;
    for (const [x, pinX] of [
      [lCush, lCush + 2],
      [rCush, rCush - 3],
    ]) {
      shapes +=
        `<rect x="${pinX}" y="${pinTop}" width="5" height="2.4" rx="0.5" fill="#555" stroke="#333" stroke-width="0.4"/>` +
        `<rect x="${pinX}" y="${pinBot}" width="5" height="2.4" rx="0.5" fill="#555" stroke="#333" stroke-width="0.4"/>` +
        `<rect x="${x}" y="74" width="4" height="22" rx="1" fill="#444" stroke="#333" stroke-width="0.6"/>`;
    }
    let plate = "";
    if (holding) {
      const px = cx - plateW / 2;
      const py = 85 - plateH / 2;
      plate = `<rect x="${px}" y="${py}" width="${plateW}" height="${plateH}" rx="2" fill="#e9ecef" stroke="#3a3a3a" stroke-width="1"/>`;
      const wells = wellGridOf(holding);
      if (wells) {
        const dx = plateW / (wells.columns + 1);
        const dy = plateH / (wells.rows + 1);
        const r = Math.max(0.6, Math.min(dx, dy) * 0.32);
        for (let row = 1; row <= wells.rows; row++) {
          for (let col = 1; col <= wells.columns; col++) {
            plate += `<circle cx="${(px + col * dx).toFixed(1)}" cy="${(py + row * dy).toFixed(1)}" r="${r.toFixed(1)}" fill="#fff" stroke="#8a949a" stroke-width="0.4"/>`;
          }
        }
      }
      plate = `<g data-index="${held}" style="cursor:pointer"><title>${escapeHtml(world.names[held])} - click to select</title>${plate}</g>`;
    }
    const title = `${world.names[index]}${holding ? ` - holding ${world.names[held]}` : " - empty"} - click to select`;
    return (
      `<div class="dt-column">` +
      (label === null ? "" : `<span class="dt-column-label">${escapeHtml(label)}</span>`) +
      `<svg width="${svgW}" height="${svgH}" viewBox="0 0 ${svgW} ${svgH}" style="overflow:visible;display:block">` +
      `<defs>${glowFilter(`arm${index}`, "#44BB99")}</defs>` +
      `<g data-index="${index}" data-glow="arm${index}" style="cursor:pointer"><title>${escapeHtml(title)}</title>${shapes}</g>` +
      `${plate}</svg></div>`
    );
  }

  function fillArm(panel, device) {
    const { grippers } = partsOf(device);
    if (!grippers.length) {
      panel.innerHTML = `<span class="dt-empty">No robotic arm is installed on this device.</span>`;
      return;
    }
    panel.innerHTML = grippers
      .map((index, i) => gripperFigure(index, grippers.length > 1 ? String(i) : null))
      .join("");
    glowOnHover(panel);
  }

  const FILL = { single: fillSingle, multi: fillMulti, arm: fillArm };

  // ------------------------------------------------------------------ panels

  /** Where main was last laid out: a dragged panel keeps its place on screen, not in main. */
  let mainWas = null;

  /** Put every open panel where it belongs: multi, single and arm in a row under their buttons. */
  function layOut() {
    if (!mainEl) return;
    const bounds = mainEl.getBoundingClientRect();
    if (mainWas) {
      for (const at of moved.values()) {
        at.x += mainWas.left - bounds.left;
        at.y += mainWas.top - bounds.top;
      }
    }
    mainWas = bounds;
    for (const device of new Set([...open.values()].map((p) => p.device))) {
      const mine = KINDS.map(({ kind }) => open.get(idOf(device, kind))).filter(Boolean);
      if (!mine.length) continue;
      // Anchored on the single-channel button, as the existing visualizer anchors them, so the
      // group keeps the same place whichever of the three happen to be open.
      const anchor = (open.get(idOf(device, "single")) ?? mine[0]).button.getBoundingClientRect();
      const top = anchor.bottom - bounds.top + PANEL_DROP;
      // Widths are only known once the content is in, so this measures rather than assumes.
      const widths = mine.map((p) => p.element.offsetWidth);
      const total = widths.reduce((a, b) => a + b, 0) + PANEL_GAP * (mine.length - 1);
      // The run is brought inside the viewport as a whole. Clamping each panel on its own pushes
      // them into one another, which is worse than a group that runs off a narrow window.
      let left = Math.min(
        Math.max(anchor.left - bounds.left + anchor.width / 2 - total / 2, PANEL_EDGE),
        Math.max(PANEL_EDGE, bounds.width - total - PANEL_EDGE),
      );
      mine.forEach((panel, i) => {
        const dragged = moved.get(panel.element.id);
        panel.element.style.left = `${dragged ? dragged.x : left}px`;
        panel.element.style.top = `${dragged ? dragged.y : top}px`;
        left += widths[i] + PANEL_GAP;
      });
    }
  }

  const idOf = (device, kind) => `dt-panel-${kind}-${device}`;

  /** Drag by the panel itself, the way the existing visualizer's dropdowns move. */
  function draggable(panel) {
    panel.addEventListener("mousedown", (event) => {
      if (event.target.closest(".dt-reset, [data-index]")) return;
      const start = { x: event.clientX, y: event.clientY };
      const from = { x: panel.offsetLeft, y: panel.offsetTop };
      panel.classList.add("is-dragging");
      const move = (e) => {
        const at = { x: from.x + e.clientX - start.x, y: from.y + e.clientY - start.y };
        moved.set(panel.id, at);
        panel.style.left = `${at.x}px`;
        panel.style.top = `${at.y}px`;
        // The way back appears once there is somewhere to come back from.
        panel.querySelector(".dt-reset").hidden = false;
      };
      const up = () => {
        panel.classList.remove("is-dragging");
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        window.removeEventListener("blur", up);
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
      // Releasing outside the page, or alt-tabbing away, ends the drag as well.
      window.addEventListener("blur", up);
      event.preventDefault();
    });
  }

  function render(panel) {
    const body = panel.element.querySelector(".dt-body");
    FILL[panel.kind](body, panel.device);
    for (const target of body.querySelectorAll("[data-index]")) {
      target.addEventListener("click", (event) => {
        event.stopPropagation();
        onSelect(Number(target.dataset.index));
      });
    }
  }

  function close(id) {
    const panel = open.get(id);
    if (!panel) return;
    panel.element.remove();
    panel.button.classList.remove("active");
    open.delete(id);
    layOut();
  }

  function toggle(device, kind, button) {
    const id = idOf(device, kind);
    if (open.has(id)) {
      close(id);
      return;
    }
    const element = document.createElement("div");
    element.className = `dt-panel mt-panel-${kind}`;
    element.id = id;
    // The same house as the deck's "Reset view", as the existing visualizer's panels carry, and
    // hidden until the panel has been dragged: a way back is only offered once there is one.
    element.innerHTML =
      `<button class="dt-reset" title="Return this panel to its default position" hidden>` +
      `<svg width="15" height="15" viewBox="0 0 20 20" aria-hidden="true">` +
      `<path d="M10 1L1 9h3v8h5v-5h2v5h5V9h3L10 1z" fill="currentColor"/></svg>` +
      `</button><div class="dt-body"></div>`;
    const reset = element.querySelector(".dt-reset");
    reset.addEventListener("click", (event) => {
      event.stopPropagation();
      moved.delete(id);
      reset.hidden = true;
      layOut();
    });
    mainEl?.appendChild(element);
    draggable(element);
    button.classList.add("active");
    const panel = { element, device, kind, button, name: world.names[device] };
    open.set(id, panel);
    render(panel);
    layOut();
  }

  // ------------------------------------------------------------------ the navbar cluster

  function rebuild() {
    // What was open comes back, by device name: a scene arriving is no reason to close a panel.
    const wasOpen = [...open.values()].map((panel) => [panel.name, panel.kind]);
    const buttonOf = new Map();
    for (const id of [...open.keys()]) close(id);
    if (!containerEl) return;
    containerEl.textContent = "";
    if (!world) return;
    // One button a device, carrying the device's name and nothing else, as the existing
    // visualizer's navbar has one per liquid handler.
    for (const device of devices()) {
      const parts = partsOf(device);
      const group = document.createElement("div");
      group.className = "dt-group";

      const label = document.createElement("button");
      label.className = "dt-label";
      label.title = "Show or hide this device's features";
      label.textContent = world.names[device];
      group.appendChild(label);

      const buttons = document.createElement("div");
      buttons.className = "dt-buttons";
      group.appendChild(buttons);

      label.addEventListener("click", () => {
        const collapsed = buttons.classList.toggle("collapsed");
        label.classList.toggle("collapsed", collapsed);
        // A collapsed group leaves nothing to close its panels with, so they go with it.
        if (collapsed) for (const id of KINDS.map((k) => idOf(device, k.kind))) close(id);
      });

      const has = {
        single: parts.channels.length,
        multi: parts.heads.length,
        arm: parts.grippers.length,
      };
      for (const { kind, icon, title } of KINDS) {
        if (!has[kind]) continue;
        const button = document.createElement("button");
        button.className = "dt-btn";
        button.title = title;
        button.innerHTML = `<img src="./img/${icon}" alt="${escapeHtml(title)}">`;
        button.addEventListener("click", () => toggle(device, kind, button));
        buttons.appendChild(button);
        buttonOf.set(idOf(device, kind), button);
      }
      containerEl.appendChild(group);
    }
    for (const [name, kind] of wasOpen) {
      const device = world.indexOfName.get(name);
      const button = device === undefined ? undefined : buttonOf.get(idOf(device, kind));
      if (button) toggle(device, kind, button);
    }
    // A device that has just arrived shows its panels; closed once, they stay closed.
    for (const device of devices()) {
      if (shown.has(world.names[device])) continue;
      shown.add(world.names[device]);
      for (const { kind } of KINDS) {
        const button = buttonOf.get(idOf(device, kind));
        if (button && !open.has(idOf(device, kind))) toggle(device, kind, button);
      }
    }
  }

  /** Whether any of these resources stands under this device. */
  function under(device, indices) {
    for (let index of indices) {
      for (; index >= 0; index = world.parentOf[index]) if (index === device) return true;
    }
    return false;
  }

  /** Redraw what is open, or only the panels a change to `changed` reaches. */
  function refresh(changed) {
    let drawn = false;
    for (const panel of open.values()) {
      if (changed !== undefined && !under(panel.device, changed)) continue;
      render(panel);
      drawn = true;
    }
    if (drawn) layOut();
  }

  // Whatever resizes main - the window, either rail, the side panel - moves it out from under the
  // buttons, and a panel left where it was in main is no longer under its own.
  if (mainEl) new ResizeObserver(layOut).observe(mainEl);
  return { rebuild, refresh };
}
