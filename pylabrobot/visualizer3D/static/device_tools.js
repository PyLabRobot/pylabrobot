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

import { escapeHtml } from "./format.js";

// What the tree calls the parts a device works with.
const CHANNEL = "pipette_channel";
const HEADS = new Set(["head96", "head384"]);
const GRIPPER = "mechanical_gripper";
const SHAFT = "tip_mounting_shaft";
// A gripper's own parts, as opposed to whatever it has picked up: anything else below it is cargo.
const GRIPPER_PARTS = new Set(["body", "finger", "pad"]);

// Somewhere a device stands rather than a device. A facility holds instruments and a bench holds
// them too, and a deck is the surface one works over - none of the three is the thing that
// pipettes, so the search goes through them and stops at what they contain.
const PLACES = new Set(["facility", "bench", "deck"]);

// A tip is drawn at its own length, so a 1000 uL tip reads as one. Millimetres at this many pixels
// each, clamped so that a panel of eight channels still fits on a laptop.
const TIP_PX_PER_MM = 0.8;
const TIP_SHORTEST_MM = 10;
const TIP_LONGEST_MM = 80;
// The channel body above it, in pixels. Fixed, because it is a stalk to hang a tip from rather
// than a measurement of anything.
const CHANNEL_PX = 30;

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
 * @param {{getWorld: () => any, modelOf: (i: number) => any, onSelect: (i: number) => void}} deps
 * @returns {{rebuild: () => void, refresh: () => void}}
 */
export function initDeviceTools({ getWorld, modelOf, onSelect }) {
  const containerEl = document.getElementById("navbar-device-tools");
  const mainEl = document.querySelector("main");
  /** Open panels, by their own id. Each is { element, device, kind, button, offset }. */
  const open = new Map();
  /** Where the user dragged a panel, by id, until it is reset. */
  const moved = new Map();

  // ------------------------------------------------------------------ reading the tree

  /** Every index below `index`, itself included, that `keep` accepts. */
  function descendants(index, keep) {
    const world = getWorld();
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
   * The devices in the scene: the outermost resource that is not simply a place, and that
   * pipettes or grips. A facility holding two instruments answers with both; an instrument holding
   * an arm holding channels answers once, as the instrument.
   */
  function devices() {
    const world = getWorld();
    const found = [];
    const walk = (index) => {
      if (!PLACES.has(categoryOf(index)) && carries(partsOf(index))) {
        found.push(index);
        return; // nothing below a device is another device
      }
      for (const child of world.childrenOf[index]) walk(child);
    };
    for (let i = 0; i < world.names.length; i++) if (world.parentOf[i] < 0) walk(i);
    return found;
  }

  /** The tip a channel is holding, as its model, or null. It hangs off the mounting shaft. */
  function tipOf(index) {
    const world = getWorld();
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
    const world = getWorld();
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
    const world = getWorld();
    return world.childrenOf[index].find((c) => !GRIPPER_PARTS.has(categoryOf(c))) ?? -1;
  }

  // ------------------------------------------------------------------ drawing a panel

  /** A row of one label and one value, the shape the info panel already uses. */
  function row(key, value) {
    return (
      `<div class="dt-row"><span class="dt-key">${escapeHtml(key)}</span>` +
      `<span class="dt-val">${escapeHtml(String(value))}</span></div>`
    );
  }

  function fillSingle(panel, device) {
    const { channels } = partsOf(device);
    if (!channels.length) {
      panel.innerHTML = `<div class="dt-empty">This device has no pipetting channels.</div>`;
      return;
    }
    const world = getWorld();
    const columns = channels.map((index) => {
      const tip = tipOf(index);
      const mm = Number(tip?.model?.total_tip_length) || 0;
      const length = mm
        ? Math.min(TIP_LONGEST_MM, Math.max(TIP_SHORTEST_MM, mm)) * TIP_PX_PER_MM
        : 0;
      // The channel's own number, as the tree names it, so the panel and the tree agree.
      const numbered = /(\d+)$/.exec(world.names[index]);
      const label = numbered ? numbered[1] : String(channels.indexOf(index));
      const title = tip
        ? `${world.names[index]} - ${world.names[tip.index]}, ${mm} mm`
        : `${world.names[index]} - no tip`;
      return (
        `<div class="dt-channel" data-index="${index}" title="${escapeHtml(title)}">` +
        `<span class="dt-channel-label">${escapeHtml(label)}</span>` +
        `<span class="dt-stalk" style="height:${CHANNEL_PX}px"></span>` +
        (length ? `<span class="dt-tip" style="height:${length.toFixed(1)}px"></span>` : "") +
        `</div>`
      );
    });
    const fitted = channels.filter((index) => tipOf(index)).length;
    panel.innerHTML =
      `<div class="dt-title">Channels<span class="dt-count">${fitted} / ${channels.length} tipped</span></div>` +
      `<div class="dt-channels" style="min-height:${CHANNEL_PX + TIP_LONGEST_MM * TIP_PX_PER_MM}px">` +
      columns.join("") +
      `</div>`;
  }

  function fillMulti(panel, device) {
    const { heads } = partsOf(device);
    if (!heads.length) {
      panel.innerHTML = `<div class="dt-empty">This device has no multi-channel head.</div>`;
      return;
    }
    const world = getWorld();
    panel.innerHTML = heads
      .map((index) => {
        const grid = gridOf(index);
        const dots = grid.placed
          .map(
            (spot) =>
              `<i class="dt-dot${spot.filled ? " is-filled" : ""}"` +
              ` style="grid-row:${spot.row + 1};grid-column:${spot.column + 1}"` +
              ` title="${escapeHtml(world.names[spot.index])}"></i>`,
          )
          .join("");
        return (
          `<div class="dt-head" data-index="${index}">` +
          `<div class="dt-title">${escapeHtml(world.names[index])}` +
          `<span class="dt-count">${grid.filled} / ${grid.placed.length} tipped</span></div>` +
          `<div class="dt-grid" style="grid-template-columns:repeat(${grid.columns},1fr)">${dots}</div>` +
          `</div>`
        );
      })
      .join("");
  }

  function fillArm(panel, device) {
    const { grippers } = partsOf(device);
    if (!grippers.length) {
      panel.innerHTML = `<div class="dt-empty">This device has no gripper.</div>`;
      return;
    }
    const world = getWorld();
    panel.innerHTML = grippers
      .map((index) => {
        const model = modelOf(index);
        const held = heldBy(index);
        const jaws =
          model.jaw_width !== undefined && model.jaw_width !== null
            ? `${Number(model.jaw_width).toFixed(1)} mm`
            : "unreported";
        return (
          `<div class="dt-arm" data-index="${index}">` +
          `<div class="dt-title">${escapeHtml(world.names[index])}</div>` +
          row("jaws", jaws) +
          row("holding", held >= 0 ? world.names[held] : "nothing") +
          `</div>`
        );
      })
      .join("");
  }

  const FILL = { single: fillSingle, multi: fillMulti, arm: fillArm };

  // ------------------------------------------------------------------ panels

  /** Put every open panel where it belongs: multi, single and arm in a row under their buttons. */
  function layOut() {
    if (!mainEl) return;
    const bounds = mainEl.getBoundingClientRect();
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
      if (event.target.closest(".dt-reset, .dt-channel, .dt-head, .dt-arm")) return;
      const start = { x: event.clientX, y: event.clientY };
      const from = { x: panel.offsetLeft, y: panel.offsetTop };
      panel.classList.add("is-dragging");
      const move = (e) => {
        const at = { x: from.x + e.clientX - start.x, y: from.y + e.clientY - start.y };
        moved.set(panel.id, at);
        panel.style.left = `${at.x}px`;
        panel.style.top = `${at.y}px`;
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
    element.innerHTML = `<button class="dt-reset" title="Put this panel back">&#8635;</button><div class="dt-body"></div>`;
    element.querySelector(".dt-reset").addEventListener("click", (event) => {
      event.stopPropagation();
      moved.delete(id);
      layOut();
    });
    mainEl?.appendChild(element);
    draggable(element);
    button.classList.add("active");
    const panel = { element, device, kind, button };
    open.set(id, panel);
    render(panel);
    layOut();
  }

  // ------------------------------------------------------------------ the navbar cluster

  function rebuild() {
    for (const id of [...open.keys()]) close(id);
    if (!containerEl) return;
    containerEl.textContent = "";
    const world = getWorld();
    if (!world) return;

    for (const device of devices()) {
      const parts = partsOf(device);
      const group = document.createElement("div");
      group.className = "dt-group";

      const label = document.createElement("button");
      label.className = "dt-label";
      label.title = "Show or hide what this device carries";
      label.innerHTML = `${escapeHtml(world.names[device])}<br>Capabilities`;
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
      }
      containerEl.appendChild(group);
    }
  }

  /** Redraw what is open. The tree is the source, so this is all that a state change needs. */
  function refresh() {
    for (const panel of open.values()) render(panel);
    layOut();
  }

  window.addEventListener("resize", layOut);
  return { rebuild, refresh };
}
