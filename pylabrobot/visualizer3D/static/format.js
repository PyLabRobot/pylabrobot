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

function umlRows(pairs) {
  return pairs
    .map(
      ([k, v]) =>
        `<div class="uml-row"><span class="uml-key">${escapeHtml(k)}</span><span class="uml-value">${v}</span></div>`,
    )
    .join("");
}

// Units for the fields that have them. A number without its unit is not an answer.
const UNITS = {
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

// 1, 2 or 5 times a power of ten: the spacings a person can count in.
export function niceNumber(value) {
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(value, 1e-6)));
  return [1, 2, 5, 10].map((m) => m * magnitude).find((v) => v >= value) ?? magnitude * 10;
}

/** A colour number as CSS writes it. */
export const hexOf = (n) => `#${n.toString(16).padStart(6, "0")}`;

export const NBSP = "\u00a0";
export const tuple = (x, y, z, unit) =>
  `(${x.toFixed(1)},${NBSP}${y.toFixed(1)},${NBSP}${z.toFixed(1)})${unit ? NBSP + unit : ""}`;

export function withUnit(key, value) {
  const unit = UNITS[key];
  return unit ? `${fmt(value)}${NBSP}${unit}` : fmt(value);
}

// The committed volume against the capacity, which a tip's state and model carry under other
// names than a well's model. Undefined for a resource that holds no liquid.
export function liquid(model, state) {
  const max = state?.max_volume ?? model.maximal_volume ?? model.max_volume;
  if (max === undefined) return undefined;
  return `${fmt(state?.volume ?? 0)}${NBSP}/${NBSP}${fmt(max)}${NBSP}uL`;
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
