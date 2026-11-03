/**
 * Element lookups that say what kind of element the caller is asking for.
 *
 * `document.getElementById` promises only an `HTMLElement`, so reading `.value` or `.checked` off
 * one is unchecked. These wrappers carry the narrowing, which means a typo in an id still fails at
 * the same place it always did, but a wrong assumption about the kind of element shows up as a
 * type error rather than `undefined` at runtime.
 */

/** @param {string} id @returns {HTMLInputElement} */
export const input = (id) => /** @type {any} */ (document.getElementById(id));

/** @param {string} id @returns {HTMLSelectElement} */
export const select = (id) => /** @type {any} */ (document.getElementById(id));

/** @param {string} id @returns {HTMLButtonElement} */
export const button = (id) => /** @type {any} */ (document.getElementById(id));

/** @param {string} selector @returns {HTMLElement} */
export const query = (selector) => /** @type {any} */ (document.querySelector(selector));
