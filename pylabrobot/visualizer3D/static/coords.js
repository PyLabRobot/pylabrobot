// The coordinate tool: reads a point off a resource against a chosen reference and lists it.

import { select as selectEl } from "./dom.js";
import { referencePoint } from "./drawn.js";
import { escapeHtml } from "./format.js";
import { world } from "./world.js";

/**
 * The geometry is `referencePoint`'s, so where a reference stands is decided in one place.
 *
 * @returns {{coordinateLabel: (i: number) => string, recordMeasurement: (i: number) => void,
 *            populateWrtDropdown: () => void, endpoints: (i: number) => {from: any, to: any},
 *            wrtPoint: () => any}}
 */
export function initCoords() {
  const refValue = (id) => selectEl(id).value;
  const measurementsEl = document.getElementById("coords-measurements");
  const hintEl = document.getElementById("coords-measurements-hint");

  /**
   * The two points a measurement runs between, in world millimetres. `from` is null when the
   * measurement is absolute, which is the one case there is no second point to draw to.
   *
   * @param {number} index
   * @returns {{from: any, to: any}}
   */
  function endpoints(index) {
    const to = referencePoint(
      index,
      refValue("coords-x-ref"),
      refValue("coords-y-ref"),
      refValue("coords-z-ref"),
    );
    const wrtName = refValue("coords-wrt-ref");
    if (wrtName === "root") return { from: null, to };
    const wrtIndex = world.indexOfName.get(wrtName);
    if (wrtIndex === undefined) return { from: null, to };
    return {
      from: referencePoint(
        wrtIndex,
        refValue("coords-wrt-x-ref"),
        refValue("coords-wrt-y-ref"),
        refValue("coords-wrt-z-ref"),
      ),
      to,
    };
  }

  function coordinateFor(index) {
    const { from, to } = endpoints(index);
    // A difference is only as known as the two points it runs between: measuring against a
    // reference whose own height is unavailable leaves the height unavailable.
    const zKnown = to.zKnown && (!from || from.zKnown);
    const point = from ? to.sub(from) : to;
    point.zKnown = zKnown;
    return point;
  }

  /** Where the chosen reference stands, in world millimetres, or null when measuring absolutely. */
  function wrtPoint() {
    const wrtName = refValue("coords-wrt-ref");
    if (wrtName === "root") return null;
    const wrtIndex = world.indexOfName.get(wrtName);
    if (wrtIndex === undefined) return null;
    return referencePoint(
      wrtIndex,
      refValue("coords-wrt-x-ref"),
      refValue("coords-wrt-y-ref"),
      refValue("coords-wrt-z-ref"),
    );
  }

  /** A height, or "na" where the resource cannot answer for the reference asked of it. */
  const height = (point) => (point.zKnown ? point.z.toFixed(1) : "na");

  function coordinateLabel(index) {
    const p = coordinateFor(index);
    const wrtName = refValue("coords-wrt-ref");
    const wrt = wrtName === "root" ? "abs" : `wrt ${wrtName}`;
    return `${world.names[index]}\n${wrt}: (${p.x.toFixed(1)}, ${p.y.toFixed(1)}, ${height(p)}) mm`;
  }

  function recordMeasurement(index) {
    const p = coordinateFor(index);
    hintEl.remove();

    // First letters, except that "center" and "cavity_bottom" share one: the compact row has to
    // say which of the two was asked for.
    const short = (value) => (value === "cavity_bottom" ? "cb" : (value || "?")[0]);
    const initials = (...ids) => ids.map((id) => short(refValue(id))).join(", ");
    const wrtName = refValue("coords-wrt-ref");

    const row = document.createElement("div");
    row.className = "measurement-row";
    row.innerHTML =
      `<div class="m-content">` +
      `<div class="m-name">${escapeHtml(world.names[index])} ` +
      `(${initials("coords-x-ref", "coords-y-ref", "coords-z-ref")})</div>` +
      `<div class="m-wrt">wrt ${escapeHtml(wrtName)} ` +
      `(${initials("coords-wrt-x-ref", "coords-wrt-y-ref", "coords-wrt-z-ref")})</div>` +
      `<div class="m-val">(${p.x.toFixed(1)}, ${p.y.toFixed(1)}, ${height(p)})</div>` +
      `</div><button class="m-remove" title="Remove">&times;</button>`;
    row.querySelector(".m-remove").addEventListener("click", () => row.remove());
    measurementsEl.appendChild(row);
    measurementsEl.scrollTop = measurementsEl.scrollHeight;
  }

  function populateWrtDropdown() {
    const select = selectEl("coords-wrt-ref");
    const current = select.value;
    select.innerHTML = '<option value="root">(abs)</option>';
    // Everything above the leaves: a well is rarely the thing another thing is measured against,
    // and listing 1,400 of them makes the dropdown unusable.
    for (let i = 0; i < world.names.length; i++) {
      if (world.childrenOf[i].length === 0) continue;
      const option = document.createElement("option");
      option.value = world.names[i];
      option.textContent = world.names[i];
      select.appendChild(option);
    }
    select.value =
      current && [...select.options].some((o) => o.value === current) ? current : "root";
  }

  return { coordinateLabel, recordMeasurement, populateWrtDropdown, endpoints, wrtPoint };
}
