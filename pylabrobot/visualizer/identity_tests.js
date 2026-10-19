// Run with: node pylabrobot/visualizer/identity_tests.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const crypto = require("node:crypto");

// Load the real browser code with inert DOM/event bindings. These tests exercise the
// resource registry and wire format without opening a canvas or a websocket.
const element = { addEventListener() {} };
const context = vm.createContext({
  console,
  crypto,
  Konva: { Layer: class {} },
  document: { getElementById: () => element },
  window: { addEventListener() {} },
});
vm.runInContext(fs.readFileSync(`${__dirname}/lib.js`, "utf8"), context);
context.assert = assert;
vm.runInContext(`
  const data = (identifier, name, children = [], parent_identifier = null) => ({
    identifier, metadata: { name }, type: "Resource", size_x: 1, size_y: 2,
    size_z: 3, location: { x: 0, y: 0, z: 0 }, children, parent_identifier,
  });
  const root = loadResource(data("root-id", "same", [
    data("first-id", "same"), data("__proto__", "same"),
  ]));
  const first = resources["first-id"];
  const second = resources["__proto__"];
  assert.notEqual(first, second);
  assert.equal(second.parent, root);
  first.name = "renamed";
  assert.equal(first.metadata.name, "renamed");
  assert.equal(resources["first-id"], first);
  assert.equal(first.serialize().identifier, "first-id");
  assert.equal(first.serialize().parent_identifier, "root-id");
  assert.equal(first.serialize().metadata.name, "renamed");
  const added = loadResource(data("added-id", "same", [], "root-id"));
  assert.equal(added.parent, root);
  assert.equal(root.children.length, 3);
  hiddenResourceNames.add(root.identifier);
  assert.equal(isHiddenByAncestor(first), true);
  root.name = "another label";
  assert.equal(isHiddenByAncestor(second), true);
  hiddenResourceNames.clear();
  first.destroy();
  assert.equal(resources["first-id"], undefined);
  assert.equal(resources["__proto__"], second);
  root.destroy();
  assert.equal(Object.keys(resources).length, 0);
`, context);
console.log("Visualizer identity tests passed.");
