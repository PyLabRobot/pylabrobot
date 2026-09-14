/**
 * Turning values into the text the info panel shows.
 *
 * Everything here is a pure function of its arguments: no scene state is read or written, so these
 * can be reasoned about, and changed, on their own.
 */

export function escapeHtml(text) {
  return String(text).replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
}

export function fmt(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "object") {
    // A plate's item ordering runs to ninety-six entries. Its length is the useful fact here;
    // the entries themselves are the tree, which is already on screen.
    const count = Array.isArray(value) ? value.length : Object.keys(value).length;
    const text = JSON.stringify(value);
    return text.length > 60 ? `${count} ${count === 1 ? "entry" : "entries"}` : escapeHtml(text);
  }
  return escapeHtml(value);
}

export function umlRows(pairs) {
  return pairs
    .map(
      ([k, v]) =>
        `<div class="uml-row"><span class="uml-key">${escapeHtml(k)}</span><span class="uml-value">${v}</span></div>`,
    )
    .join("");
}

export const UNITS = {
  size_x: "mm",
  size_y: "mm",
  size_z: "mm",
  material_z_thickness: "mm",
  total_tip_length: "mm",
  plate_z_offset: "mm",
  max_volume: "uL",
  volume: "uL",
  pending_volume: "uL",
  nominal_volume: "uL",
  maximal_volume: "uL",
};

export const NBSP = "\u00a0";
export const tuple = (x, y, z, unit) =>
  `(${x.toFixed(1)},${NBSP}${y.toFixed(1)},${NBSP}${z.toFixed(1)})${unit ? NBSP + unit : ""}`;

export function withUnit(key, value) {
  const unit = UNITS[key];
  return unit ? `${fmt(value)}${NBSP}${unit}` : fmt(value);
}

export function section(title, rows, note) {
  if (!rows.length) return "";
  return (
    `<div class="uml-separator"></div><div class="uml-section">` +
    `<div class="uml-section-title">${title}</div>` +
    umlRows(rows) +
    (note ? `<p class="uml-note">${escapeHtml(note)}</p>` : "") +
    `</div>`
  );
}
