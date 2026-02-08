// ===========================================================================
// Global Color Map (edit this to try new combinations)
// ===========================================================================
const RESOURCE_COLORS = {
  Resource: "#BDB163",
  HamiltonSTARDeck: "#F5FAFC",
  Carrier: "#5C6C8F",
  MFXCarrier: "#536181",
  PlateCarrier: "#5C6C8F",
  TipCarrier: "#64405d",
  TroughCarrier: "#756793",
  TubeCarrier: "#756793",
  Plate: "#3A3A3A",
  Well: "#F5FAFC",
  TipRack: "#8f5c85",
  TubeRack: "#122D42",
  ResourceHolder: "#5B6277",
  PlateHolder: "#8D99AE",
  ContainerBackground: "#E0EAEE"
};

// ===========================================================================
// Mode and Layers
// ===========================================================================

var mode;
const MODE_VISUALIZER = "visualizer";
const MODE_GUI = "gui";

var layer = new Konva.Layer();
var resourceLayer = new Konva.Layer();
var tooltip;
var stage;
var selectedResource;

var canvasWidth, canvasHeight;
var activeTool = "cursor"; // "cursor" or "coords"
var wrtHighlightCircle;
var resHighlightBullseye;
var deltaLinesGroup = null; // Konva.Group for Δ lines between resource and wrt bullseyes

function buildWrtDropdown() {
  var wrtSelect = document.getElementById("coords-wrt-ref");
  if (!wrtSelect) return;
  var prevValue = wrtSelect.value;
  wrtSelect.innerHTML = '';

  // Collect all visible (not inside a collapsed container) resources from the sidebar tree.
  var tree = document.getElementById("resource-tree");
  if (!tree) return;

  function collectVisible(container) {
    var items = [];
    var nodes = container.querySelectorAll(":scope > .tree-node");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var name = node.dataset.resourceName;
      if (name) items.push(name);
      // Recurse into children if not collapsed
      var childrenDiv = node.querySelector(":scope > .tree-node-children");
      if (childrenDiv && !childrenDiv.classList.contains("collapsed")) {
        items = items.concat(collectVisible(childrenDiv));
      }
    }
    return items;
  }

  var visibleNames = collectVisible(tree);

  for (var i = 0; i < visibleNames.length; i++) {
    var opt = document.createElement("option");
    opt.value = visibleNames[i];
    opt.textContent = visibleNames[i];
    wrtSelect.appendChild(opt);
  }

  // Restore previous selection if still valid
  var allValues = Array.from(wrtSelect.options).map(function (o) { return o.value; });
  if (allValues.indexOf(prevValue) >= 0) {
    wrtSelect.value = prevValue;
  } else if (visibleNames.length > 0) {
    wrtSelect.value = visibleNames[0];
  }
}



function updateWrtBullseyeScale() {
  if (!wrtHighlightCircle) return;
  var s = Math.pow(1 / Math.abs(stage.scaleX()), 0.95);
  wrtHighlightCircle.scaleX(s);
  wrtHighlightCircle.scaleY(s);
  var haloGroup = wrtHighlightCircle.getChildren()[0];
  if (haloGroup && haloGroup.clearCache) {
    var r = 9.2, barH = r * 1.0125;
    var pad = 20 / s;
    haloGroup.cache({ x: -r - barH - pad, y: -r - barH - pad, width: (r + barH) * 2 + pad * 2, height: (r + barH) * 2 + pad * 2 });
  }
  resourceLayer.draw();
}

function updateTooltipScale() {
  if (!tooltip) return;
  var ts = Math.pow(1 / Math.abs(stage.scaleX()), 0.97);
  tooltip.scaleX(ts);
  tooltip.scaleY(-ts);
}

function clearDeltaLines() {
  if (deltaLinesGroup) { deltaLinesGroup.destroy(); deltaLinesGroup = null; }
  resourceLayer.draw();
}

function updateDeltaLinesScale() {
  if (!deltaLinesGroup) return;
  var s = Math.pow(1 / Math.abs(stage.scaleX()), 0.95);
  deltaLinesGroup.getChildren().forEach(function (child) {
    if (child.getClassName() === "Label") {
      child.scaleY(-s);
      child.scaleX(s);
    } else if (child.getClassName() === "Line") {
      child.strokeWidth(child._baseStrokeWidth * s);
      child.dash([14 * s, 6 * s]);
    }
  });
  resourceLayer.draw();
}

function drawDeltaLines(resource) {
  if (deltaLinesGroup) { deltaLinesGroup.destroy(); deltaLinesGroup = null; }
  if (!resource || activeTool !== "coords") return;
  var toggle = document.getElementById("delta-lines-toggle");
  if (toggle && !toggle.checked) return;

  // Get WRT bullseye position
  var wrtRef = document.getElementById("coords-wrt-ref");
  var wrtName = wrtRef ? wrtRef.value : null;
  var wrtRes = wrtName ? resources[wrtName] : null;
  if (!wrtRes) return;
  var wrtAbs = wrtRes.getAbsoluteLocation();
  var wrtOff = getWrtAnchorOffset(wrtRes);
  var wx = wrtAbs.x + wrtOff.x;
  var wy = wrtAbs.y + wrtOff.y;

  // Get resource bullseye position
  var abs = resource.getAbsoluteLocation();
  var xRef = document.getElementById("coords-x-ref");
  var yRef = document.getElementById("coords-y-ref");
  var xOff = !xRef || xRef.value === "left" ? 0 : xRef.value === "center" ? resource.size_x / 2 : resource.size_x;
  var yOff = !yRef || yRef.value === "front" ? 0 : yRef.value === "center" ? resource.size_y / 2 : resource.size_y;
  var rx = abs.x + xOff;
  var ry = abs.y + yOff;

  var dx = rx - wx;
  var dy = ry - wy;
  if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) return;

  deltaLinesGroup = new Konva.Group({ listening: false });
  var s = Math.pow(1 / Math.abs(stage.scaleX()), 0.95);
  var xColor = "#dc3545"; // matches x-axis legend
  var yColor = "#198754"; // matches y-axis legend
  var xHalo = "#EE8866";
  var yHalo = "#BBCC33";
  var corner = { x: rx, y: wy }; // L-shape corner

  // Halo lines (thicker, behind main lines)
  var xHaloLine = new Konva.Line({
    points: [wx, wy, corner.x, corner.y],
    stroke: xHalo, strokeWidth: 8 * s, dash: [14 * s, 6 * s], opacity: 0.4,
  });
  xHaloLine._baseStrokeWidth = 8;
  deltaLinesGroup.add(xHaloLine);

  var yHaloLine = new Konva.Line({
    points: [corner.x, corner.y, rx, ry],
    stroke: yHalo, strokeWidth: 8 * s, dash: [14 * s, 6 * s], opacity: 0.4,
  });
  yHaloLine._baseStrokeWidth = 8;
  deltaLinesGroup.add(yHaloLine);

  // Main horizontal line (Δx): wrt → corner
  var xLine = new Konva.Line({
    points: [wx, wy, corner.x, corner.y],
    stroke: xColor, strokeWidth: 3 * s, dash: [14 * s, 6 * s],
  });
  xLine._baseStrokeWidth = 3;
  deltaLinesGroup.add(xLine);

  // Main vertical line (Δy): corner → resource
  var yLine = new Konva.Line({
    points: [corner.x, corner.y, rx, ry],
    stroke: yColor, strokeWidth: 3 * s, dash: [14 * s, 6 * s],
  });
  yLine._baseStrokeWidth = 3;
  deltaLinesGroup.add(yLine);

  // Δx label at midpoint of horizontal line
  var dxLabel = new Konva.Label({
    x: (wx + corner.x) / 2,
    y: wy,
  });
  dxLabel.add(new Konva.Tag({
    fill: "white",
    opacity: 0.85,
    cornerRadius: 3,
  }));
  dxLabel.add(new Konva.Text({
    text: "\u0394x=" + dx.toFixed(1),
    fontSize: 18,
    fill: xColor,
    fontStyle: "bold",
    fontFamily: "Arial",
    padding: 3,
  }));
  dxLabel.offsetX(dxLabel.width() / 2);
  dxLabel.offsetY(-4);
  dxLabel.scaleY(-s);
  dxLabel.scaleX(s);
  deltaLinesGroup.add(dxLabel);

  // Δy label at midpoint of vertical line
  var dyLabel = new Konva.Label({
    x: rx,
    y: (wy + ry) / 2,
  });
  dyLabel.add(new Konva.Tag({
    fill: "white",
    opacity: 0.85,
    cornerRadius: 3,
  }));
  dyLabel.add(new Konva.Text({
    text: "\u0394y=" + dy.toFixed(1),
    fontSize: 18,
    fill: yColor,
    fontStyle: "bold",
    fontFamily: "Arial",
    padding: 3,
  }));
  dyLabel.offsetY(dyLabel.height() / 2);
  dyLabel.offsetX(-6);
  dyLabel.scaleY(-s);
  dyLabel.scaleX(s);
  deltaLinesGroup.add(dyLabel);

  resourceLayer.add(deltaLinesGroup);
  deltaLinesGroup.moveToTop();
  resourceLayer.draw();
}

function updateWrtHighlight() {
  if (wrtHighlightCircle) { wrtHighlightCircle.destroy(); wrtHighlightCircle = undefined; }
  if (activeTool !== "coords") return;
  var wrtRef = document.getElementById("coords-wrt-ref");
  var wrtName = wrtRef ? wrtRef.value : null;
  var wrtRes = wrtName ? resources[wrtName] : null;
  if (!wrtRes) return;
  var wrtAbs = wrtRes.getAbsoluteLocation();
  var wrtOff = getWrtAnchorOffset(wrtRes);
  var cx = wrtAbs.x + wrtOff.x;
  var cy = wrtAbs.y + wrtOff.y;
  var r = 9.2;
  var barH = r * 1.0125;
  var wrtHaloColor = "#DDDDDD";
  var wrtHaloExtra = 4;
  wrtHighlightCircle = new Konva.Group({ x: cx, y: cy, listening: false });
  // Halo: blurred duplicate behind
  var wrtHaloGroup = new Konva.Group({ opacity: 0.5 });
  wrtHaloGroup.filters([Konva.Filters.Blur]);
  wrtHaloGroup.blurRadius(6);
  wrtHaloGroup.add(new Konva.Circle({
    x: 0, y: 0, radius: r,
    fill: "transparent", stroke: wrtHaloColor, strokeWidth: 4.32 + wrtHaloExtra * 2,
  }));
  wrtHaloGroup.add(new Konva.Circle({
    x: 0, y: 0, radius: 2.4 + wrtHaloExtra / 2,
    fill: wrtHaloColor,
  }));
  wrtHaloGroup.add(new Konva.Line({
    points: [-r - barH, 0, -r, 0],
    stroke: wrtHaloColor, strokeWidth: 3.6 + wrtHaloExtra * 2,
  }));
  wrtHaloGroup.add(new Konva.Line({
    points: [r, 0, r + barH, 0],
    stroke: wrtHaloColor, strokeWidth: 3.6 + wrtHaloExtra * 2,
  }));
  wrtHaloGroup.add(new Konva.Line({
    points: [0, -r - barH, 0, -r],
    stroke: wrtHaloColor, strokeWidth: 3.6 + wrtHaloExtra * 2,
  }));
  wrtHaloGroup.add(new Konva.Line({
    points: [0, r, 0, r + barH],
    stroke: wrtHaloColor, strokeWidth: 3.6 + wrtHaloExtra * 2,
  }));
  wrtHaloGroup.cache({ x: -r - barH - 20, y: -r - barH - 20, width: (r + barH) * 2 + 40, height: (r + barH) * 2 + 40 });
  wrtHighlightCircle.add(wrtHaloGroup);
  // Main bullseye on top
  wrtHighlightCircle.add(new Konva.Circle({
    x: 0, y: 0, radius: r,
    fill: "transparent", stroke: "#FFAABB", strokeWidth: 4.32,
  }));
  wrtHighlightCircle.add(new Konva.Circle({
    x: 0, y: 0, radius: 2.4,
    fill: "#FFAABB", opacity: 0.85,
  }));
  wrtHighlightCircle.add(new Konva.Line({
    points: [-r - barH, 0, -r, 0],
    stroke: "#FFAABB", strokeWidth: 3.6,
  }));
  wrtHighlightCircle.add(new Konva.Line({
    points: [r, 0, r + barH, 0],
    stroke: "#FFAABB", strokeWidth: 3.6,
  }));
  wrtHighlightCircle.add(new Konva.Line({
    points: [0, -r - barH, 0, -r],
    stroke: "#FFAABB", strokeWidth: 3.6,
  }));
  wrtHighlightCircle.add(new Konva.Line({
    points: [0, r, 0, r + barH],
    stroke: "#FFAABB", strokeWidth: 3.6,
  }));
  resourceLayer.add(wrtHighlightCircle);
  wrtHighlightCircle.moveToTop();
  updateWrtBullseyeScale();
  updateTooltipScale();
  updateDeltaLinesScale();
}

function updateBullseyeScale() {
  if (!resHighlightBullseye) return;
  var s = Math.pow(1 / Math.abs(stage.scaleX()), 0.95);
  resHighlightBullseye.scaleX(s);
  resHighlightBullseye.scaleY(s);
  // Re-cache halo for blur filter at new scale
  var haloGroup = resHighlightBullseye.getChildren()[0];
  if (haloGroup && haloGroup.clearCache) {
    var r = 9.2, barH = r * 1.0125;
    var pad = 20 / s;
    haloGroup.cache({ x: -r - barH - pad, y: -r - barH - pad, width: (r + barH) * 2 + pad * 2, height: (r + barH) * 2 + pad * 2 });
  }
  resourceLayer.draw();
}

function showResHighlightBullseye(resource) {
  if (resHighlightBullseye) { resHighlightBullseye.destroy(); resHighlightBullseye = undefined; }
  if (!resource) return;
  var abs = resource.getAbsoluteLocation();
  var xRef = document.getElementById("coords-x-ref");
  var yRef = document.getElementById("coords-y-ref");
  var xOff = !xRef || xRef.value === "left" ? 0 : xRef.value === "center" ? resource.size_x / 2 : resource.size_x;
  var yOff = !yRef || yRef.value === "front" ? 0 : yRef.value === "center" ? resource.size_y / 2 : resource.size_y;
  var cx = abs.x + xOff;
  var cy = abs.y + yOff;
  var r = 9.2;
  var barH = r * 1.0125;
  var color = "#99DDFF";
  var haloColor = "#BBCC33";
  // Position group at bullseye center; draw elements relative to (0,0)
  resHighlightBullseye = new Konva.Group({ x: cx, y: cy, listening: false });
  // Halo: blurred, thicker duplicate of every element behind the bullseye
  var haloExtra = 4;
  var haloOpacity = 0.5;
  var haloGroup = new Konva.Group({
    opacity: haloOpacity,
  });
  haloGroup.filters([Konva.Filters.Blur]);
  haloGroup.blurRadius(6);
  haloGroup.add(new Konva.Circle({
    x: 0, y: 0, radius: r,
    fill: "transparent", stroke: haloColor, strokeWidth: 4.32 + haloExtra * 2,
  }));
  haloGroup.add(new Konva.Circle({
    x: 0, y: 0, radius: 2.4 + haloExtra / 2,
    fill: haloColor,
  }));
  haloGroup.add(new Konva.Line({
    points: [-r - barH, 0, -r, 0],
    stroke: haloColor, strokeWidth: 3.6 + haloExtra * 2,
  }));
  haloGroup.add(new Konva.Line({
    points: [r, 0, r + barH, 0],
    stroke: haloColor, strokeWidth: 3.6 + haloExtra * 2,
  }));
  haloGroup.add(new Konva.Line({
    points: [0, -r - barH, 0, -r],
    stroke: haloColor, strokeWidth: 3.6 + haloExtra * 2,
  }));
  haloGroup.add(new Konva.Line({
    points: [0, r, 0, r + barH],
    stroke: haloColor, strokeWidth: 3.6 + haloExtra * 2,
  }));
  // Must cache after adding children for blur filter to work
  haloGroup.cache({ x: -r - barH - 20, y: -r - barH - 20, width: (r + barH) * 2 + 40, height: (r + barH) * 2 + 40 });
  resHighlightBullseye.add(haloGroup);
  // Main bullseye on top
  resHighlightBullseye.add(new Konva.Circle({
    x: 0, y: 0, radius: r,
    fill: "transparent", stroke: color, strokeWidth: 4.32,
  }));
  resHighlightBullseye.add(new Konva.Circle({
    x: 0, y: 0, radius: 2.4,
    fill: color, opacity: 0.85,
  }));
  resHighlightBullseye.add(new Konva.Line({
    points: [-r - barH, 0, -r, 0],
    stroke: color, strokeWidth: 3.6,
  }));
  resHighlightBullseye.add(new Konva.Line({
    points: [r, 0, r + barH, 0],
    stroke: color, strokeWidth: 3.6,
  }));
  resHighlightBullseye.add(new Konva.Line({
    points: [0, -r - barH, 0, -r],
    stroke: color, strokeWidth: 3.6,
  }));
  resHighlightBullseye.add(new Konva.Line({
    points: [0, r, 0, r + barH],
    stroke: color, strokeWidth: 3.6,
  }));
  resourceLayer.add(resHighlightBullseye);
  resHighlightBullseye.moveToTop();
  // Apply inverse scale so bullseye appears constant size
  updateBullseyeScale();
}

function getAncestorAtDepth(resource, depth) {
  // Walk up from the resource to the sidebar root, skipping deck-like
  // intermediaries (same flattening as the sidebar).
  var rawChain = [];
  var cur = resource;
  while (cur) {
    rawChain.unshift(cur);
    if (sidebarRootResource && cur === sidebarRootResource) break;
    cur = cur.parent;
  }
  var displayChain = [];
  for (var i = 0; i < rawChain.length; i++) {
    // Keep the root (index 0) and skip deck-like intermediaries
    if (i > 0 && isDeckLike(rawChain[i])) continue;
    // Also skip ResourceHolder intermediaries
    if (i > 0 && isResourceHolder(rawChain[i])) continue;
    displayChain.push(rawChain[i]);
  }
  if (depth < displayChain.length) return displayChain[depth];
  return null;
}

function getWrtAnchorOffset(wrtResource) {
  var xRef = document.getElementById("coords-wrt-x-ref");
  var yRef = document.getElementById("coords-wrt-y-ref");
  var zRef = document.getElementById("coords-wrt-z-ref");

  var xOff = 0;
  if (xRef && xRef.value === "center") xOff = wrtResource.size_x / 2;
  else if (xRef && xRef.value === "right") xOff = wrtResource.size_x;

  var yOff = 0;
  if (yRef && yRef.value === "center") yOff = wrtResource.size_y / 2;
  else if (yRef && yRef.value === "back") yOff = wrtResource.size_y;

  var zOff = 0;
  if (zRef) {
    if (zRef.value === "center") zOff = wrtResource.size_z / 2;
    else if (zRef.value === "top") zOff = wrtResource.size_z;
    else if (zRef.value === "cavity_bottom") {
      if (wrtResource instanceof Container && wrtResource.material_z_thickness != null) {
        zOff = wrtResource.material_z_thickness;
      }
    }
  }

  return { x: xOff, y: yOff, z: zOff };
}

function getLocationWrt(resource, wrtName) {
  var wrtResource = resources[wrtName];
  if (!wrtResource) return resource.getAbsoluteLocation();
  var abs = resource.getAbsoluteLocation();
  var wrtAbs = wrtResource.getAbsoluteLocation();
  var wrtOff = getWrtAnchorOffset(wrtResource);
  return {
    x: abs.x - (wrtAbs.x + wrtOff.x),
    y: abs.y - (wrtAbs.y + wrtOff.y),
    z: (abs.z || 0) - ((wrtAbs.z || 0) + wrtOff.z),
  };
}

var scaleX, scaleY;

var resources = {}; // name -> Resource object
// Serialized resource data saved before resources are destroyed (e.g. picked up by arm).
// Used by the arm panel to re-instantiate the resource and draw it on a live Konva stage
// using the exact same draw() code as the main canvas — guaranteeing visual consistency.
// Each entry is a plain JS object from resource.serialize() (~1-5 KB for a 96-well plate).
var resourceSnapshots = {}; // name -> serialized resource data

var rootResource = null; // the root resource for fit-to-viewport

function fitToViewport() {
  if (!rootResource || !stage) return;
  const padding = 40;
  const stageW = stage.width();
  const stageH = stage.height();
  const viewW = stageW - padding * 2;
  const viewH = stageH - padding * 2;
  const fitScale = Math.min(viewW / rootResource.size_x, viewH / rootResource.size_y, 1);

  stage.scaleX(fitScale);
  stage.scaleY(-fitScale);

  const centerX = (stageW - rootResource.size_x * fitScale) / 2;
  const centerY = (stageH + rootResource.size_y * fitScale) / 2 - stageH * fitScale;
  stage.x(centerX);
  stage.y(centerY);

  if (typeof updateScaleBar === "function") updateScaleBar();
  updateBullseyeScale();
  updateWrtBullseyeScale();
  updateTooltipScale();
  updateDeltaLinesScale();
}

let trash;

let gif;

let resourceImage;

// Used in gif generation
let isRecording = false;
let recordingCounter = 0; // Counter to track the number of recorded frames
var frameImages = [];
let frameInterval = 8;
var _recordingTimer = null;

function getSnappingResourceAndLocationAndSnappingBox(resourceToSnap, x, y) {
  // Return the snapping resource that the given point is within, or undefined if there is no such resource.
  // A snapping resource is a spot within a plate/tip carrier or the OT deck.
  // This can probably be simplified a lot.
  // Returns {resource, location wrt resource}

  if (!snappingEnabled) {
    return undefined;
  }

  // Check if the resource is in the trash.
  if (
    x > trash.x() &&
    x < trash.x() + trash.width() &&
    y > trash.y() &&
    y < trash.y() + trash.height()
  ) {
    return {
      resource: trash,
      location: { x: 0, y: 0 },
      snappingBox: {
        x: trash.x(),
        y: trash.y(),
        width: trash.width(),
        height: trash.height(),
      },
    };
  }

  // Check if the resource is in a ResourceHolder.
  let deck = resources["deck"];
  for (let resource_name in deck.children) {
    const resource = deck.children[resource_name];

    // Check if we have a resource to snap
    let canSnapPlate =
      resourceToSnap.constructor.name === "Plate" &&
      resource.constructor.name === "PlateCarrier";
    let canSnapTipRack =
      resourceToSnap.constructor.name === "TipRack" &&
      resource.constructor.name === "TipCarrier";
    if (!(canSnapPlate || canSnapTipRack)) {
      continue;
    }

    for (let carrier_site_name in resource.children) {
      let carrier_site = resource.children[carrier_site_name];
      const { x: resourceX, y: resourceY } = carrier_site.getAbsoluteLocation();
      if (
        x > resourceX &&
        x < resourceX + carrier_site.size_x &&
        y > resourceY &&
        y < resourceY + carrier_site.size_y
      ) {
        return {
          resource: carrier_site,
          location: { x: 0, y: 0 },
          snappingBox: {
            x: resourceX,
            y: resourceY,
            width: carrier_site.size_x,
            height: carrier_site.size_y,
          },
        };
      }
    }
  }

  // Check if the resource is in the OT Deck.
  if (deck.constructor.name === "OTDeck") {
    const siteWidth = 128.0;
    const siteHeight = 86.0;

    for (let i = 0; i < otDeckSiteLocations.length; i++) {
      let siteLocation = otDeckSiteLocations[i];
      if (
        x > deck.location.x + siteLocation.x &&
        x < deck.location.x + siteLocation.x + siteWidth &&
        y > deck.location.y + siteLocation.y &&
        y < deck.location.y + siteLocation.y + siteHeight
      ) {
        return {
          resource: deck,
          location: { x: siteLocation.x, y: siteLocation.y },
          snappingBox: {
            x: deck.location.x + siteLocation.x,
            y: deck.location.y + siteLocation.y,
            width: siteWidth,
            height: siteHeight,
          },
        };
      }
    }
  }

  // Check if the resource is in an OTDeck.
  return undefined;
}

function getSnappingGrid(x, y, width, height) {
  // Get the snapping lines for the given resource (defined by x, y, width, height).
  // Returns {resourceX, resourceY, snapX, snapY} where resourceX and resourceY are the
  // location where the resource should be snapped to, and snapX and snapY are the
  // snapping lines that should be drawn.

  if (!snappingEnabled) {
    return {};
  }

  const SNAP_MARGIN = 5;

  let snappingLines = {};

  const deck = resources["deck"];
  if (
    deck.constructor.name === "HamiltonSTARDeck" ||
    deck.constructor.name === "VantageDeck"
  ) {
    const railOffset = deck.constructor.name === "VantageDeck" ? 32.5 : 100;

    if (Math.abs(y - deck.location.y - 63) < SNAP_MARGIN) {
      snappingLines.resourceY = deck.location.y + 63;
    }

    if (
      Math.abs(y - deck.location.y - 63 - deck.railHeight + height) <
      SNAP_MARGIN
    ) {
      snappingLines.resourceY = deck.location.y + 63 + deck.railHeight - height;
      snappingLines.snappingY = deck.location.y + 63 + deck.railHeight;
    }

    if (Math.abs(x - deck.location.x) < SNAP_MARGIN) {
      snappingLines.resourceX = deck.location.x;
    }

    for (let rail = 0; rail < deck.num_rails; rail++) {
      const railX = railOffset + 22.5 * rail;
      if (Math.abs(x - railX) < SNAP_MARGIN) {
        snappingLines.resourceX = railX;
      }
    }
  }

  // if resource snapping position defined, but not the snapping line, set the snapping line to the
  // resource snapping position.
  if (
    snappingLines.resourceX !== undefined &&
    snappingLines.snappingX === undefined
  ) {
    snappingLines.snappingX = snappingLines.resourceX;
  }
  if (
    snappingLines.resourceY !== undefined &&
    snappingLines.snappingY === undefined
  ) {
    snappingLines.snappingY = snappingLines.resourceY;
  }

  return snappingLines;
}

class Resource {
  constructor(resourceData, parent = undefined) {
    const { name, location, size_x, size_y, size_z, children } = resourceData;
    this.name = name;
    this.size_x = size_x;
    this.size_y = size_y;
    this.size_z = size_z;
    this.location = location;
    this.parent = parent;
    this.resourceType = resourceData.type || this.constructor.name;
    this.category = resourceData.category || "";
    this.methods = resourceData.methods || [];

    this.color = "#5B6D8F";

    this.children = [];
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      const childClass = classForResourceType(child.type, child.category);
      const childInstance = new childClass(child, this);
      this.assignChild(childInstance);

      // Save in global lookup
      resources[child.name] = childInstance;
    }
  }

  // Dynamically compute the color based on RESOURCE_COLORS
  getColor() {
    if (RESOURCE_COLORS.hasOwnProperty(this.constructor.name)) {
      return RESOURCE_COLORS[this.constructor.name];
    }
    return RESOURCE_COLORS["Resource"];
  }

  // Properties influenced by mode
  get draggable() { return mode === MODE_GUI; }
  get canDelete() { return mode === MODE_GUI; }

  draw(layer) {
    // On draw, destroy the old shape.
    if (this.group !== undefined) {
      this.group.destroy();
    }

    // Add all children to this shape's group.
    this.group = new Konva.Group({
      x: this.location.x,
      y: this.location.y,
      draggable: this.draggable,
    });
    this.mainShape = this.drawMainShape();
    if (this.mainShape !== undefined) {
      this.group.add(this.mainShape);
    }
    for (let i = 0; i < this.children.length; i++) {
      const child = this.children[i];
      child.draw(layer);
    }
    layer.add(this.group);
    // Add a reference to this to the shape (so that it may be accessed in event handlers)
    this.group.resource = this;

    // Add this group to parent group.
    if (this.parent !== undefined) {
      this.parent.group.add(this.group);
    }

    // If a shape is drawn, add event handlers and other things.
    if (this.mainShape !== undefined) {
      this.mainShape.resource = this;
      this.mainShape.on("mouseover", () => {
        const { x, y } = this.getAbsoluteLocation();
        if (tooltip !== undefined) {
          tooltip.destroy();
        }
        var labelText;
        if (activeTool === "coords") {
          const xRef = document.getElementById("coords-x-ref");
          const yRef = document.getElementById("coords-y-ref");
          const zRef = document.getElementById("coords-z-ref");
          const wrtRef = document.getElementById("coords-wrt-ref");
          const wrtName = wrtRef ? wrtRef.value : "root";
          const base = getLocationWrt(this, wrtName);
          const xOff = !xRef || xRef.value === "left" ? 0 : xRef.value === "center" ? this.size_x / 2 : this.size_x;
          const yOff = !yRef || yRef.value === "front" ? 0 : yRef.value === "center" ? this.size_y / 2 : this.size_y;
          var zOff = 0;
          var zNA = false;
          if (zRef) {
            if (zRef.value === "center") zOff = this.size_z / 2;
            else if (zRef.value === "top") zOff = this.size_z;
            else if (zRef.value === "cavity_bottom") {
              if (this instanceof Container && this.material_z_thickness != null) {
                zOff = this.material_z_thickness;
              } else {
                zNA = true;
              }
            }
          }
          const cx = base.x + xOff;
          const cy = base.y + yOff;
          const cz = (base.z || 0) + zOff;
          const czStr = zNA ? "na" : cz.toFixed(1);
          const wrtLabel = "wrt " + wrtName;
          labelText = `${this.name}\n${wrtLabel}: (${cx.toFixed(1)}, ${cy.toFixed(1)}, ${czStr}) mm`;
        } else {
          labelText = this.tooltipLabel();
        }
        tooltip = new Konva.Label({
          x: x + this.size_x / 2,
          y: y + this.size_y / 2 + (activeTool === "coords" ? this.size_y * 0.25 : 0),
          opacity: 0.75,
          listening: false,
        });
        tooltip.add(
          new Konva.Tag({
            fill: "black",
            pointerDirection: "down",
            pointerWidth: 10,
            pointerHeight: 10,
            lineJoin: "round",
            shadowColor: "black",
            shadowBlur: 10,
            shadowOffset: 10,
            shadowOpacity: 0.5,
          })
        );
        tooltip.add(
          new Konva.Text({
            text: labelText,
            fontFamily: "Arial",
            fontSize: activeTool === "coords" ? 17.5 : 21.4,
            lineHeight: activeTool === "coords" ? 1.6 : 1.2,
            padding: 5,
            fill: "white",
          })
        );
        var ts = Math.pow(1 / Math.abs(stage.scaleX()), 0.97);
        tooltip.scaleX(ts);
        tooltip.scaleY(-ts);
        layer.add(tooltip);
        if (typeof highlightSidebarRow === "function") {
          highlightSidebarRow(this.name);
        }
        if (activeTool === "coords") {
          showResHighlightBullseye(this);
          drawDeltaLines(this);
        }
      });
      this.mainShape.on("click", () => {
        if (activeTool === "coords") {
          const xRef = document.getElementById("coords-x-ref");
          const yRef = document.getElementById("coords-y-ref");
          const zRef = document.getElementById("coords-z-ref");
          const wrtRef = document.getElementById("coords-wrt-ref");
          const wrtName = wrtRef ? wrtRef.value : "root";
          const base = getLocationWrt(this, wrtName);
          const xOff = !xRef || xRef.value === "left" ? 0 : xRef.value === "center" ? this.size_x / 2 : this.size_x;
          const yOff = !yRef || yRef.value === "front" ? 0 : yRef.value === "center" ? this.size_y / 2 : this.size_y;
          var zOff = 0;
          var zNA = false;
          if (zRef) {
            if (zRef.value === "center") zOff = this.size_z / 2;
            else if (zRef.value === "top") zOff = this.size_z;
            else if (zRef.value === "cavity_bottom") {
              if (this instanceof Container && this.material_z_thickness != null) {
                zOff = this.material_z_thickness;
              } else {
                zNA = true;
              }
            }
          }
          const cx = base.x + xOff;
          const cy = base.y + yOff;
          const cz = (base.z || 0) + zOff;
          const czStr = zNA ? "na" : cz.toFixed(1);
          const container = document.getElementById("coords-measurements");
          if (container) {
            const xLabel = xRef ? xRef.value : "left";
            const yLabel = yRef ? yRef.value : "front";
            const zLabel = zRef ? zRef.value : "bottom";
            const row = document.createElement("div");
            row.style.padding = "5px 0";
            row.style.borderBottom = "2px solid #ced4da";
            row.style.fontSize = "14px";
            row.style.lineHeight = "1.4";
            row.style.display = "flex";
            row.style.alignItems = "flex-start";

            const content = document.createElement("div");
            content.style.flex = "1";

            const xl = xLabel[0];
            const yl = yLabel[0];
            const zl = zLabel[0];
            const wrtXRef = document.getElementById("coords-wrt-x-ref");
            const wrtYRef = document.getElementById("coords-wrt-y-ref");
            const wrtZRef = document.getElementById("coords-wrt-z-ref");
            const wxl = wrtXRef ? wrtXRef.value[0] : "l";
            const wyl = wrtYRef ? wrtYRef.value[0] : "f";
            const wzl = wrtZRef ? wrtZRef.value[0] : "b";

            const nameLine = document.createElement("div");
            nameLine.style.fontWeight = "600";
            nameLine.style.color = "#333";
            nameLine.textContent = `${this.name} (${xl}, ${yl}, ${zl})`;

            const wrtLine = document.createElement("div");
            wrtLine.style.color = "#888";
            wrtLine.style.fontSize = "12px";
            wrtLine.textContent = `wrt ${wrtName} (${wxl}, ${wyl}, ${wzl})`;

            const coordLine = document.createElement("div");
            coordLine.style.fontFamily = "monospace";
            coordLine.style.color = "#1a4b8c";
            coordLine.style.fontWeight = "600";
            coordLine.textContent = `(${cx.toFixed(1)}, ${cy.toFixed(1)}, ${czStr})`;

            content.appendChild(nameLine);
            content.appendChild(wrtLine);
            content.appendChild(coordLine);

            const deleteBtn = document.createElement("button");
            deleteBtn.textContent = "×";
            deleteBtn.style.background = "none";
            deleteBtn.style.border = "none";
            deleteBtn.style.color = "#aaa";
            deleteBtn.style.fontSize = "18px";
            deleteBtn.style.cursor = "pointer";
            deleteBtn.style.padding = "0 2px";
            deleteBtn.style.lineHeight = "1";
            deleteBtn.style.flexShrink = "0";
            deleteBtn.onmouseover = () => { deleteBtn.style.color = "#d33"; };
            deleteBtn.onmouseout = () => { deleteBtn.style.color = "#aaa"; };
            deleteBtn.onclick = () => { row.remove(); };

            row.appendChild(content);
            row.appendChild(deleteBtn);
            var hint = document.getElementById("coords-measurements-hint");
            if (hint) hint.remove();
            container.appendChild(row);
            container.scrollTop = container.scrollHeight;
          }
        }
      });
      this.mainShape.on("mouseout", () => {
        tooltip.destroy();
        showResHighlightBullseye(null);
        clearDeltaLines();
        if (typeof clearSidebarHighlight === "function") {
          clearSidebarHighlight();
        }
      });
      this.mainShape.on("dblclick dbltap", () => {
        showUmlPanel(this.name);
      });
    }
  }

  drawMainShape() {
    return new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: this.getColor(),
      stroke: "black",
      strokeWidth: 1,
    });
  }

  tooltipLabel() {
    return `${this.name} (${this.constructor.name})`;
  }

  getAbsoluteLocation() {
    if (this.parent !== undefined) {
      const parentLocation = this.parent.getAbsoluteLocation();
      return {
        x: parentLocation.x + this.location.x,
        y: parentLocation.y + this.location.y,
        z: parentLocation.z + this.location.z,
      };
    }
    return this.location;
  }

  serialize() {
    const serializedChildren = [];
    for (let i = 0; i < this.children.length; i++) {
      const child = this.children[i];
      serializedChildren.push(child.serialize());
    }

    return {
      name: this.name,
      type: this.constructor.name,
      location: {
        ...this.location,
        ...{
          type: "Coordinate",
        },
      },
      size_x: this.size_x,
      size_y: this.size_y,
      size_z: this.size_z,
      children: serializedChildren,
      parent_name: this.parent === undefined ? null : this.parent.name,
    };
  }

  assignChild(child) {
    if (child === this) {
      console.error("Cannot assign a resource to itself", this);
      return;
    }

    // Update layout tree.
    child.parent = this;
    this.children.push(child);

    // Add child group to UI.
    if (this.group !== undefined && child.group !== undefined) {
      this.group.add(child.group);
    }
  }

  unassignChild(child) {
    child.parent = undefined;
    const index = this.children.indexOf(child);
    if (index > -1) {
      this.children.splice(index, 1);
    }
  }

  destroy() {
    // Destroy children
    for (let i = this.children.length - 1; i >= 0; i--) {
      const child = this.children[i];
      child.destroy();
    }

    // Remove from global lookup
    delete resources[this.name];

    // Remove from UI
    if (this.group !== undefined) {
      this.group.destroy();
    }

    // Remove from parent
    if (this.parent !== undefined) {
      this.parent.unassignChild(this);
    }
  }

  update() {
    this.draw(resourceLayer);

    // GIF frame capture is now driven by _recordingTimer (setInterval)
  }

  setState() {}
}

class Deck extends Resource {
  draggable = false;
  canDelete = false;
}

class HamiltonSTARDeck extends Deck {
  constructor(resourceData) {
    super(resourceData, undefined);
    const { num_rails } = resourceData;
    this.num_rails = num_rails;
    this.railHeight = 497;
  }

  drawMainShape() {
    // Draw a transparent rectangle with an outline
    let mainShape = new Konva.Group();
    mainShape.add(
      new Konva.Rect({
        y: 63,
        width: this.size_x,
        height: this.railHeight,
        fill: "white",
        stroke: "black",
        strokeWidth: 1,
      })
    );

    // draw border around the deck
    mainShape.add(
      new Konva.Rect({
        width: this.size_x,
        height: this.size_y,
        stroke: "black",
        strokeWidth: 1,
      })
    );

    // Draw vertical rails as lines
    for (let i = 0; i < this.num_rails; i++) {
      const railBottomTickHeight = 10;
      const rail = new Konva.Line({
        points: [
          100 + i * 22.5, // 22.5 mm per rail
          63 - railBottomTickHeight,
          100 + i * 22.5, // 22.5 mm per rail
          this.railHeight + 63,
        ],
        stroke: "black",
        strokeWidth: 1,
      });
      mainShape.add(rail);

      // Add a text label every 5 rails. Rails are 1-indexed.
      // Keep in mind that the stage is flipped vertically.
      if ((i + 1) % 5 === 0 || i === 0) {
        const railLabel = new Konva.Text({
          x: 100 + i * 22.5 + 11.25, // center of rail (between lines)
          y: 50,
          text: i + 1,
          fontSize: 15,
          fill: "black",
        });
        railLabel.offsetX(railLabel.width() / 2);
        railLabel.scaleY(-1); // Flip the text vertically
        mainShape.add(railLabel);
      }
    }
    return mainShape;
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        num_rails: this.num_rails,
        with_trash: false,
        with_trash96: false,
      },
    };
  }
}

class VantageDeck extends Deck {
  constructor(resourceData) {
    super(resourceData, undefined);
    const { size } = resourceData;
    this.size = size;
    if (size === 1.3) {
      this.num_rails = 54;
    } else {
      alert(`Unsupported Vantage Deck size: ${size}. Only 1.3 is supported.`);
      this.num_rails = 0;
    }
    this.railHeight = 497;
  }

  drawMainShape() {
    let mainShape = new Konva.Group();
    mainShape.add(
      new Konva.Rect({
        y: 63,
        width: this.size_x,
        height: this.railHeight,
        fill: "white",
        stroke: "black",
        strokeWidth: 1,
      })
    );

    mainShape.add(
      new Konva.Rect({
        width: this.size_x,
        height: this.size_y,
        stroke: "black",
        strokeWidth: 1,
      })
    );

    for (let i = 0; i < this.num_rails; i++) {
      const railX = 32.5 + i * 22.5;
      const railBottomTickHeight = 10;
      const rail = new Konva.Line({
        points: [railX, 63 - railBottomTickHeight, railX, this.railHeight + 63],
        stroke: "black",
        strokeWidth: 1,
      });
      mainShape.add(rail);

      if ((i + 1) % 5 === 0 || i === 0) {
        const railLabel = new Konva.Text({
          x: railX + 11.25, // center of rail (between lines)
          y: 50,
          text: i + 1,
          fontSize: 15,
          fill: "black",
        });
        railLabel.offsetX(railLabel.width() / 2);
        railLabel.scaleY(-1);
        mainShape.add(railLabel);
      }
    }
    return mainShape;
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        size: this.size,
      },
    };
  }
}

const otDeckSiteLocations = [
  { x: 0.0, y: 0.0 },
  { x: 132.5, y: 0.0 },
  { x: 265.0, y: 0.0 },
  { x: 0.0, y: 90.5 },
  { x: 132.5, y: 90.5 },
  { x: 265.0, y: 90.5 },
  { x: 0.0, y: 181.0 },
  { x: 132.5, y: 181.0 },
  { x: 265.0, y: 181.0 },
  { x: 0.0, y: 271.5 },
  { x: 132.5, y: 271.5 },
  { x: 265.0, y: 271.5 },
];

class OTDeck extends Deck {
  constructor(resourceData) {
    resourceData.location = { x: 115.65, y: 68.03 };
    super(resourceData, undefined);
  }

  drawMainShape() {
    let group = new Konva.Group({});
    const width = 128.0;
    const height = 86.0;
    // Draw the sites
    for (let i = 0; i < otDeckSiteLocations.length; i++) {
      const siteLocation = otDeckSiteLocations[i];
      const site = new Konva.Rect({
        x: siteLocation.x,
        y: siteLocation.y,
        width: width,
        height: height,
        fill: "white",
        stroke: "black",
        strokeWidth: 1,
      });
      group.add(site);

      // Add a text label in the site
      const siteLabel = new Konva.Text({
        x: siteLocation.x,
        y: siteLocation.y + height,
        text: i + 1,
        width: width,
        height: height,
        fontSize: 16,
        fill: "black",
        align: "center",
        verticalAlign: "middle",
        scaleY: -1, // Flip the text vertically
      });
      group.add(siteLabel);
    }

    // draw border around the deck
    group.add(
      new Konva.Rect({
        width: this.size_x,
        height: this.size_y,
        stroke: "black",
        strokeWidth: 1,
      })
    );

    return group;
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        with_trash: false,
      },
    };
  }
}

let snapLines = [];
let snappingBox = undefined;

class Plate extends Resource {
  constructor(resourceData, parent = undefined) {
    super(resourceData, parent);
    const { num_items_x, num_items_y } = resourceData;
    this.num_items_x = num_items_x;
    this.num_items_y = num_items_y;
  }

  drawMainShape() {
    return new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: this.getColor(),
      stroke: "black",
      strokeWidth: 1,
    });
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        num_items_x: this.num_items_x,
        num_items_y: this.num_items_y,
      },
    };
  }

  update() {
    super.update();

    // Rename the children
    for (let i = 0; i < this.num_items_x; i++) {
      for (let j = 0; j < this.num_items_y; j++) {
        const child = this.children[i * this.num_items_y + j];
        child.name = `${this.name}_well_${i}_${j}`;
      }
    }
  }
}

class Container extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    const { max_volume, material_z_thickness } = resourceData;
    this.maxVolume = max_volume;
    this.volume = resourceData.volume || 0;
    this.material_z_thickness = material_z_thickness;
  }

  static colorForVolume(volume, maxVolume) {
    return `rgba(239, 35, 60, ${volume / maxVolume})`;
  }

  getVolume() {
    return this.volume;
  }

  setVolume(volume) {
    this.volume = volume;
    this.update();
  }

  setState(state) {
    this.setVolume(state.volume);
  }

  serializeState() {
    return {
      volume: this.volume,
      pending_volume: this.volume,
    };
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        max_volume: this.maxVolume,
      },
    };
  }
}

class Well extends Container {
  get draggable() { return false; }
  get canDelete() { return false; }

  constructor(resourceData, parent) {
    super(resourceData, parent);
    const { cross_section_type } = resourceData;
    this.cross_section_type = cross_section_type;
  }

  serialize() {
    return {
      ...super.serialize(),
      cross_section_type: this.cross_section_type,
    };
  }

  drawMainShape() {
    const mainShape = new Konva.Group({});
    if (this.cross_section_type === "circle") {
      mainShape.add(new Konva.Circle({  // background
        radius: this.size_x / 2,
        fill: RESOURCE_COLORS["ContainerBackground"],
        offsetX: -this.size_x / 2,
        offsetY: -this.size_y / 2,
      }));
      mainShape.add(new Konva.Circle({ // liquid
        radius: this.size_x / 2,
        fill: Well.colorForVolume(this.getVolume(), this.maxVolume),
        stroke: "black",
        strokeWidth: 1,
        offsetX: -this.size_x / 2,
        offsetY: -this.size_y / 2,
      }));
    } else {
      mainShape.add(new Konva.Rect({  // background
        width: this.size_x,
        height: this.size_y,
        fill: RESOURCE_COLORS["ContainerBackground"],
      }));
      mainShape.add(new Konva.Rect({ // liquid
        width: this.size_x,
        height: this.size_y,
        fill: Well.colorForVolume(this.getVolume(), this.maxVolume),
        stroke: "black",
        strokeWidth: 1,
      }));
    }
    return mainShape;
  }
}

class Trough extends Container {
  drawMainShape() {
    const group = new Konva.Group();
    group.add(new Konva.Rect({  // background
      width: this.size_x,
      height: this.size_y,
      fill: RESOURCE_COLORS["ContainerBackground"],
      stroke: "black",
      strokeWidth: 1,
    }));
    group.add(new Konva.Rect({  // liquid layer
      width: this.size_x,
      height: this.size_y,
      fill: Trough.colorForVolume(this.getVolume(), this.maxVolume),
    }));
    return group;
  }
}

class TipRack extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    const { num_items_x, num_items_y } = resourceData;
    this.num_items_x = num_items_x;
    this.num_items_y = num_items_y;
  }

  drawMainShape() {
    return new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: this.getColor(),
      stroke: "black",
      strokeWidth: 1,
    });
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        num_items_x: this.num_items_x,
        num_items_y: this.num_items_y,
      },
    };
  }

  update() {
    super.update();

    // Rename the children
    for (let i = 0; i < this.num_items_x; i++) {
      for (let j = 0; j < this.num_items_y; j++) {
        const child = this.children[i * this.num_items_y + j];
        child.name = `${this.name}_tipspot_${i}_${j}`;
      }
    }
  }
}

class TipSpot extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.has_tip = false;
    this.tip = resourceData.prototype_tip; // not really a creator, but good enough for now.
  }

  get draggable() { return false; }
  get canDelete() { return false; }

  drawMainShape() {
    return new Konva.Circle({
      radius: this.size_x / 2,
      fill: this.has_tip ? "#40CDA1" : "white",
      stroke: "black",
      strokeWidth: 1,
      offsetX: -this.size_x / 2,
      offsetY: -this.size_y / 2,
    });
  }

  setState(state) {
    this.has_tip = state.tip !== null;
    this.update();
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        prototype_tip: this.tip,
      },
    };
  }

  serializeState() {
    if (this.has_tip) {
      return {
        tip: this.tip,
        pending_tip: this.tip,
      };
    }
    return {
      tip: null,
      pending_tip: null,
    };
  }
}

class Tube extends Container {
  get draggable() { return false; }
  get canDelete() { return false; }

  constructor(resourceData, parent) {
    super(resourceData, parent);
  }

  drawMainShape() {
    const mainShape = new Konva.Group();
    mainShape.add(new Konva.Circle({  // background
      radius: this.size_x / 2,
      fill: RESOURCE_COLORS["ContainerBackground"],
      offsetX: -this.size_x / 2,
      offsetY: -this.size_y / 2,
    }));
    mainShape.add(new Konva.Circle({  // liquid
      radius: this.size_x / 2,
      fill: Tube.colorForVolume(this.getVolume(), this.maxVolume),
      stroke: "black",
      strokeWidth: 1,
      offsetX: -this.size_x / 2,
      offsetY: -this.size_y / 2,
    }));
    return mainShape;
  }
}

// Nothing special.
class Trash extends Resource {
  drawMainShape() {
    if (resources["deck"].constructor.name) {
      return undefined;
    }
    return super.drawMainShape();
  }
}

class Carrier extends Resource {}
class MFXCarrier extends Carrier {}
class PlateCarrier extends Carrier {}
class TipCarrier extends Carrier {}
class TroughCarrier extends Carrier {}
class TubeCarrier extends Carrier {}

class ResourceHolder extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    const { spot } = resourceData;
    this.spot = spot;
  }

  draggable = false;
  canDelete = false;

  serialize() {
    return {
      ...super.serialize(),
      ...{
        spot: this.spot,
      },
    };
  }
}

class TubeRack extends Resource {
  constructor(resourceData, parent = undefined) {
    super(resourceData, parent);
    const { num_items_x, num_items_y } = resourceData;
    this.num_items_x = num_items_x;
    this.num_items_y = num_items_y;
  }

  drawMainShape() {
    return new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: this.getColor(),
      stroke: "black",
      strokeWidth: 1,
    });
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        num_items_x: this.num_items_x,
        num_items_y: this.num_items_y,
      },
    };
  }

  update() {
    super.update();

    // Rename the children
    for (let i = 0; i < this.num_items_x; i++) {
      for (let j = 0; j < this.num_items_y; j++) {
        const child = this.children[i * this.num_items_y + j];
        child.name = `${this.name}_tube_${i}_${j}`;
      }
    }
  }
}

class PlateHolder extends ResourceHolder {}

// Track the currently open pipette info panel so it can be refreshed on state updates.
var _pipetteInfoState = null; // { ch, kind ("channel"|"tip"), anchorDropdown }

function buildChannelAttrs(ch, headState) {
  var chState = headState[ch] || {};
  var tipData = chState.tip;
  var hasTip = tipData !== null && tipData !== undefined;
  var attrs = [{ key: "channel", value: ch }];
  if (hasTip) {
    attrs.push({ key: "has_tip", value: "true" });
    attrs.push({ key: "tip_type", value: tipData.type || "Unknown" });
    attrs.push({ key: "tip_size", value: (tipData.tip_size || "").replace(/_/g, " ") });
    attrs.push({ key: "max_volume", value: (tipData.maximal_volume || "?") + " \u00B5L" });
    attrs.push({ key: "has_filter", value: tipData.has_filter ? "Yes" : "No" });
    attrs.push({ key: "tip_length", value: (tipData.total_tip_length || "?") + " mm" });
  } else {
    attrs.push({ key: "has_tip", value: "false" });
  }
  return attrs;
}

function buildTipAttrs(ch, headState) {
  var chState = headState[ch] || {};
  var tipData = chState.tip;
  var tipStateData = chState.tip_state;
  if (!tipData) return null;
  var attrs = [
    { key: "name", value: tipData.name || "Unknown" },
    { key: "type", value: tipData.type || "Unknown" },
    { key: "tip_size", value: (tipData.tip_size || "").replace(/_/g, " ") },
    { key: "total_tip_length", value: (tipData.total_tip_length || "?") + " mm" },
    { key: "has_filter", value: tipData.has_filter ? "Yes" : "No" },
    { key: "maximal_volume", value: (tipData.maximal_volume || "?") + " \u00B5L" },
    { key: "pickup_method", value: (tipData.pickup_method || "").replace(/_/g, " ") },
  ];
  if (tipStateData) {
    attrs.push({ key: "volume", value: (tipStateData.volume || 0) + " / " + (tipStateData.max_volume || "?") + " \u00B5L" });
    attrs.push({ key: "origin", value: tipStateData.thing || "?" });
  }
  return attrs;
}

// Refresh the pipette info panel if one is open (called after state updates).
function refreshPipetteInfoPanel(headState) {
  if (!_pipetteInfoState) return;
  var existing = document.getElementById("pipette-info-panel");
  if (!existing) { _pipetteInfoState = null; return; }
  var s = _pipetteInfoState;
  var title, type, attrs;
  if (s.kind === "channel") {
    title = "Channel " + s.ch;
    type = "PipetteChannel";
    attrs = buildChannelAttrs(s.ch, headState);
  } else {
    title = "Tip @ Channel " + s.ch;
    type = "Tip";
    attrs = buildTipAttrs(s.ch, headState);
    if (!attrs) {
      // Tip was removed — close the panel
      existing.remove();
      _pipetteInfoState = null;
      return;
    }
  }
  // No toggle — force show with updated data
  existing.remove();
  _showPipetteInfoPanelInner(title, type, attrs, s.anchorDropdown);
}

// Build a UML-style info panel for a pipette channel or tip, shown on click.
// `title` is the header name, `type` is the guillemet type label,
// `attrs` is an array of {key, value} pairs.
function showPipetteInfoPanel(title, type, attrs, anchorDropdown, ch, kind) {
  var existing = document.getElementById("pipette-info-panel");
  if (existing) {
    // Toggle off if clicking same thing
    if (existing.dataset.key === title) {
      existing.remove();
      _pipetteInfoState = null;
      return;
    }
    existing.remove();
  }
  _pipetteInfoState = { ch: ch, kind: kind, anchorDropdown: anchorDropdown };
  _showPipetteInfoPanelInner(title, type, attrs, anchorDropdown);
}

function _showPipetteInfoPanelInner(title, type, attrs, anchorDropdown) {

  var panel = document.createElement("div");
  panel.className = "uml-panel";
  panel.id = "pipette-info-panel";
  panel.dataset.key = title;

  var closeBtn = document.createElement("button");
  closeBtn.className = "uml-close-btn";
  closeBtn.textContent = "\u00D7";
  closeBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    panel.remove();
  });
  panel.appendChild(closeBtn);

  var header = document.createElement("div");
  header.className = "uml-header";
  var nameDiv = document.createElement("div");
  nameDiv.className = "uml-header-name";
  nameDiv.textContent = title;
  var typeDiv = document.createElement("div");
  typeDiv.className = "uml-header-type";
  typeDiv.textContent = "\u00AB" + type + "\u00BB";
  header.appendChild(nameDiv);
  header.appendChild(typeDiv);
  panel.appendChild(header);

  var sep = document.createElement("div");
  sep.className = "uml-separator";
  panel.appendChild(sep);

  var section = document.createElement("div");
  section.className = "uml-section";
  var sTitle = document.createElement("div");
  sTitle.className = "uml-section-title";
  sTitle.textContent = "Attributes";
  section.appendChild(sTitle);
  for (var i = 0; i < attrs.length; i++) {
    var row = document.createElement("div");
    row.className = "uml-row";
    var keySpan = document.createElement("span");
    keySpan.className = "uml-key";
    keySpan.textContent = attrs[i].key + ":";
    var valSpan = document.createElement("span");
    valSpan.className = "uml-value";
    valSpan.textContent = " " + attrs[i].value;
    row.appendChild(keySpan);
    row.appendChild(valSpan);
    section.appendChild(row);
  }
  panel.appendChild(section);

  var mainEl = document.querySelector("main");
  if (!mainEl) return;
  mainEl.appendChild(panel);

  // Position to the left of the single-channel dropdown
  if (anchorDropdown) {
    var mainRect = mainEl.getBoundingClientRect();
    var ddRect = anchorDropdown.getBoundingClientRect();
    var panelW = panel.offsetWidth;
    panel.style.top = (ddRect.top - mainRect.top) + "px";
    panel.style.right = "auto";
    panel.style.left = (ddRect.right - mainRect.left + 8) + "px";
  }
}

function fillHeadIcons(panel, headState) {
  panel.innerHTML = "";
  // Fixed height: pipette (27) + max tip (80mm * 0.8 = 64px)
  var maxTipPx = 64; // 80mm max tip
  var fixedSvgH = 27 + maxTipPx;
  var channels = Object.keys(headState).sort(function (a, b) { return +a - +b; });
  for (var ci = 0; ci < channels.length; ci++) {
    var ch = channels[ci];
    var tipData = headState[ch] && headState[ch].tip;
    var hasTip = tipData !== null && tipData !== undefined;
    // Scale tip length: total_tip_length in mm, map to px (0.8 px/mm, clamp 10mm–80mm)
    var tipLenPx = 0;
    if (hasTip && tipData.total_tip_length) {
      var clampedMm = Math.max(10, Math.min(80, tipData.total_tip_length));
      tipLenPx = clampedMm * 0.8;
    }
    var col = document.createElement("div");
    col.style.display = "flex";
    col.style.flexDirection = "column";
    col.style.alignItems = "center";
    col.style.position = "relative";
    // Label + channel cylinder: clickable with hover glow
    var label = document.createElement("span");
    label.textContent = ch;
    label.style.fontSize = "15px";
    label.style.fontWeight = "700";
    label.style.color = "#888";
    label.style.marginBottom = "2px";
    label.style.cursor = "pointer";
    label.title = "Channel " + ch + " — click for details";
    (function (ch) {
      label.addEventListener("click", function (e) {
        e.stopPropagation();
        showPipetteInfoPanel("Channel " + ch, "PipetteChannel", buildChannelAttrs(ch, headState), panel, ch, "channel");
      });
    })(ch);
    col.appendChild(label);
    var icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("width", "14");
    icon.setAttribute("height", String(fixedSvgH));
    icon.setAttribute("viewBox", "0 0 14 " + fixedSvgH);
    icon.style.overflow = "visible";
    icon.style.display = "block";
    // Glow filters for hover
    var defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    defs.innerHTML =
      '<filter id="chGlow' + ci + '" x="-200%" y="-200%" width="500%" height="500%">' +
        '<feFlood flood-color="#99DDFF" flood-opacity="1" result="color"/>' +
        '<feComposite in="color" in2="SourceAlpha" operator="in" result="colored"/>' +
        '<feGaussianBlur in="colored" stdDeviation="4" result="glow1"/>' +
        '<feGaussianBlur in="colored" stdDeviation="8" result="glow2"/>' +
        '<feGaussianBlur in="colored" stdDeviation="14" result="glow3"/>' +
        '<feMerge><feMergeNode in="glow3"/><feMergeNode in="glow2"/><feMergeNode in="glow1"/><feMergeNode in="SourceGraphic"/></feMerge>' +
      '</filter>' +
      '<filter id="tipGlow' + ci + '" x="-200%" y="-200%" width="500%" height="500%">' +
        '<feFlood flood-color="#EEDD88" flood-opacity="1" result="color"/>' +
        '<feComposite in="color" in2="SourceAlpha" operator="in" result="colored"/>' +
        '<feGaussianBlur in="colored" stdDeviation="4" result="glow1"/>' +
        '<feGaussianBlur in="colored" stdDeviation="8" result="glow2"/>' +
        '<feGaussianBlur in="colored" stdDeviation="14" result="glow3"/>' +
        '<feMerge><feMergeNode in="glow3"/><feMergeNode in="glow2"/><feMergeNode in="glow1"/><feMergeNode in="SourceGraphic"/></feMerge>' +
      '</filter>';
    icon.appendChild(defs);
    // Channel shapes (black cylinder + silver cylinder) — hover glow + click
    var channelG = document.createElementNS("http://www.w3.org/2000/svg", "g");
    channelG.style.cursor = "pointer";
    (function (idx, ch) {
      channelG.addEventListener("mouseenter", function () { this.setAttribute("filter", "url(#chGlow" + idx + ")"); });
      channelG.addEventListener("mouseleave", function () { this.removeAttribute("filter"); });
      channelG.addEventListener("click", function (e) {
        e.stopPropagation();
        showPipetteInfoPanel("Channel " + ch, "PipetteChannel", buildChannelAttrs(ch, headState), panel, ch, "channel");
      });
    })(ci, ch);
    channelG.innerHTML =
      '<rect x="0" y="1" width="14" height="18" rx="3" ry="3" fill="#333"/>' +
      '<ellipse cx="7" cy="2" rx="7" ry="2" fill="#555"/>' +
      '<ellipse cx="7" cy="19" rx="7" ry="2" fill="#222"/>' +
      '<rect x="2" y="20" width="10" height="4" rx="2" ry="2" fill="#b0b0b0"/>' +
      '<ellipse cx="7" cy="20" rx="5" ry="1.5" fill="#ccc"/>' +
      '<ellipse cx="7" cy="24" rx="5" ry="1.5" fill="#999"/>';
    var channelTitle = document.createElementNS("http://www.w3.org/2000/svg", "title");
    channelTitle.textContent = "Channel " + ch + " — click for details";
    channelG.appendChild(channelTitle);
    icon.appendChild(channelG);
    if (hasTip) {
      var collarH = 6.5;
      var collarY = 19.5;
      var bodyStart = collarY + collarH;
      var straightH = Math.round(tipLenPx * 0.4);
      var taperH = tipLenPx - straightH;
      var straightEnd = bodyStart + straightH;
      var tipEnd = straightEnd + taperH;
      var tipG = document.createElementNS("http://www.w3.org/2000/svg", "g");
      tipG.style.cursor = "pointer";
      (function (idx) {
        tipG.addEventListener("mouseenter", function () { this.setAttribute("filter", "url(#tipGlow" + idx + ")"); });
        tipG.addEventListener("mouseleave", function () { this.removeAttribute("filter"); });
      })(ci);
      var botW = (tipData.total_tip_length > 50) ? 2 : 1;
      var botL = 7 - botW / 2;
      var botR = 7 + botW / 2;
      var tipShapes =
        '<rect x="1.5" y="' + collarY + '" width="11" height="' + collarH + '" rx="0.5" ry="0.5" fill="#c8c8c8" fill-opacity="0.5" stroke="#888" stroke-width="0.8"/>' +
        '<rect x="3" y="' + bodyStart + '" width="8" height="' + straightH + '" rx="1" ry="1" fill="#d0d0d0" stroke="#888" stroke-width="0.8"/>' +
        '<polygon points="3,' + straightEnd + ' 11,' + straightEnd + ' ' + botR + ',' + tipEnd + ' ' + botL + ',' + tipEnd + '" fill="#d0d0d0" stroke="#888" stroke-width="0.8"/>';
      // Volume fill overlay
      var fillSvg = "";
      var chTipState = (headState[ch] || {}).tip_state;
      var fillRatio = 0;
      if (chTipState && chTipState.max_volume > 0) {
        fillRatio = Math.min(1, (chTipState.volume || 0) / chTipState.max_volume);
      }
      if (fillRatio > 0) {
        var totalFillableH = straightH + taperH;
        var fillH = fillRatio * totalFillableH;
        var fillColor = "rgba(0,119,187,0.45)";
        if (fillH <= taperH) {
          var widthAtFill = botW + (8 - botW) * (fillH / taperH);
          var leftX = 7 - widthAtFill / 2;
          var rightX = 7 + widthAtFill / 2;
          var fillTop = tipEnd - fillH;
          fillSvg = '<polygon points="' + botL + ',' + tipEnd + ' ' + botR + ',' + tipEnd + ' ' + rightX + ',' + fillTop + ' ' + leftX + ',' + fillTop + '" fill="' + fillColor + '" stroke="none"/>';
        } else {
          var bodyFillH = fillH - taperH;
          var bodyFillY = straightEnd - bodyFillH;
          fillSvg =
            '<polygon points="3,' + straightEnd + ' 11,' + straightEnd + ' ' + botR + ',' + tipEnd + ' ' + botL + ',' + tipEnd + '" fill="' + fillColor + '" stroke="none"/>' +
            '<rect x="3" y="' + bodyFillY + '" width="8" height="' + bodyFillH + '" fill="' + fillColor + '" stroke="none"/>';
        }
      }
      tipG.innerHTML = tipShapes + fillSvg;
      var tipTitle = document.createElementNS("http://www.w3.org/2000/svg", "title");
      tipTitle.textContent = "Tip on channel " + ch + " — click for details";
      tipG.appendChild(tipTitle);
      (function (ch) {
        tipG.addEventListener("click", function (e) {
          e.stopPropagation();
          showPipetteInfoPanel("Tip @ Channel " + ch, "Tip", buildTipAttrs(ch, headState), panel, ch, "tip");
        });
      })(ch);
      icon.appendChild(tipG);
    }
    // Pending tip: pulsing blurred overlay
    var chStateObj = headState[ch] || {};
    var pendingTip = chStateObj.pending_tip;
    var currentTip = chStateObj.tip;
    var isPending = (pendingTip !== null && pendingTip !== undefined) !== (currentTip !== null && currentTip !== undefined);
    if (isPending) {
      var pendingFilter = document.createElementNS("http://www.w3.org/2000/svg", "filter");
      pendingFilter.setAttribute("id", "pendingGlow" + ci);
      pendingFilter.setAttribute("x", "-100%");
      pendingFilter.setAttribute("y", "-100%");
      pendingFilter.setAttribute("width", "300%");
      pendingFilter.setAttribute("height", "300%");
      pendingFilter.innerHTML = '<feGaussianBlur stdDeviation="3"/>';
      defs.appendChild(pendingFilter);
      var pendingRect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      pendingRect.setAttribute("x", "0");
      pendingRect.setAttribute("y", "1");
      pendingRect.setAttribute("width", "14");
      pendingRect.setAttribute("height", String(fixedSvgH - 1));
      pendingRect.setAttribute("rx", "3");
      pendingRect.setAttribute("fill", "#EE8866");
      pendingRect.setAttribute("filter", "url(#pendingGlow" + ci + ")");
      pendingRect.style.pointerEvents = "none";
      pendingRect.style.animation = "pendingPulse 1.5s ease-in-out infinite";
      icon.appendChild(pendingRect);
    }
    col.appendChild(icon);
    panel.appendChild(col);
  }
}

function head96PosId(ch, startCh) {
  var local = ch - startCh;
  var row = local % 8;
  var col = Math.floor(local / 8);
  return String.fromCharCode(65 + row) + (col + 1);
}

function fillHead96Grid(panel, head96State) {
  panel.innerHTML = "";
  if (!head96State || Object.keys(head96State).length === 0) {
    // Set panel dimensions to match a normal 96-head grid, then center message
    panel.style.minWidth = "180px";
    panel.style.minHeight = "130px";
    panel.style.alignItems = "center";
    panel.style.justifyContent = "center";
    var msg = document.createElement("span");
    msg.style.color = "#888";
    msg.style.fontSize = "13px";
    msg.style.fontWeight = "500";
    msg.style.textAlign = "center";
    msg.style.padding = "16px";
    msg.textContent = "No multi-channel pipette is installed on this liquid handler.";
    panel.appendChild(msg);
    return;
  }
  // Split channels into groups of 96 (one grid per multi-channel pipette)
  var allChannels = Object.keys(head96State).sort(function (a, b) { return +a - +b; });
  var numPipettes = Math.max(1, Math.ceil(allChannels.length / 96));
  // Compute dot size to fit within panel height
  var panelH = parseFloat(panel.style.height) || 0;
  var availH = panelH > 0 ? panelH - 32 - 12 - 3 : 100;
  // 8 rows with gaps: 8d + 7*0.25d = 9.75d = availH
  var dotSize = Math.max(6, Math.min(14, Math.floor(availH / 9.75)));
  var gapSize = Math.max(1, Math.round(dotSize * 0.25));
  for (var p = 0; p < numPipettes; p++) {
    var startCh = p * 96;
    var box = document.createElement("div");
    box.style.border = "1.5px solid #555";
    box.style.borderRadius = "6px";
    box.style.padding = "6px";
    box.style.background = "#444";
    box.style.cursor = "pointer";
    box.title = "96-head pipette — click for details";
    box.onmouseover = function () { box.style.boxShadow = "0 0 8px 3px rgba(68, 187, 153, 0.5), 0 0 20px 6px rgba(68, 187, 153, 0.25)"; };
    box.onmouseout = function () { box.style.boxShadow = "none"; };
    (function (startCh, head96State) {
      box.addEventListener("click", function (e) {
        var tipCount = 0;
        for (var i = startCh; i < startCh + 96; i++) {
          var s = head96State[String(i)] || head96State[i];
          if (s && s.tip !== null && s.tip !== undefined) tipCount++;
        }
        var attrs = [
          { key: "channels", value: "96" },
          { key: "tips_loaded", value: tipCount + " / 96" },
        ];
        showPipetteInfoPanel("96-Head Pipette", "CoRe96Head", attrs, panel, String(startCh), "channel");
      });
    })(startCh, head96State);
    box.style.display = "inline-flex";
    box.style.flexDirection = "column";
    box.style.alignItems = "center";
    var grid = document.createElement("div");
    grid.style.display = "grid";
    grid.style.gridTemplateColumns = "repeat(12, " + dotSize + "px)";
    grid.style.gridTemplateRows = "repeat(8, " + dotSize + "px)";
    grid.style.gap = gapSize + "px";
    // 8 rows x 12 cols, column-major within each 96-group
    for (var row = 0; row < 8; row++) {
      for (var col = 0; col < 12; col++) {
        var ch = startCh + col * 8 + row;
        var chState = head96State[String(ch)] || head96State[ch];
        var hasTip = chState && chState.tip !== null && chState.tip !== undefined;
        var dot = document.createElement("div");
        dot.style.width = dotSize + "px";
        dot.style.height = dotSize + "px";
        dot.style.borderRadius = "50%";
        dot.style.border = "1.5px solid " + (hasTip ? "#40CDA1" : "#555");
        dot.style.background = hasTip ? "#40CDA1" : "white";
        var posId = head96PosId(ch, startCh);
        dot.title = "Channel " + ch + " / " + posId + (hasTip ? " (tip)" : "");
        if (hasTip) {
          dot.style.cursor = "pointer";
          dot.onmouseover = function () { this.style.boxShadow = "0 0 6px 2px rgba(238, 221, 136, 0.6), 0 0 14px 4px rgba(238, 221, 136, 0.3)"; };
          dot.onmouseout = function () { this.style.boxShadow = "none"; };
          (function (ch, posId) {
            dot.addEventListener("click", function (e) {
              e.stopPropagation();
              var tipAttrs = buildTipAttrs(String(ch), head96State);
              if (tipAttrs) {
                showPipetteInfoPanel("Tip @ Channel " + ch + " / " + posId, "Tip", tipAttrs, panel, String(ch), "tip");
              }
            });
          })(ch, posId);
        } else {
          (function (ch, posId) {
            dot.style.cursor = "pointer";
            dot.addEventListener("click", function (e) {
              e.stopPropagation();
              showPipetteInfoPanel("Channel " + ch + " / " + posId, "PipetteChannel", buildChannelAttrs(String(ch), head96State), panel, String(ch), "channel");
            });
          })(ch, posId);
        }
        grid.appendChild(dot);
      }
    }
    // Bars outside the grid box, 60% of the corresponding dimension, centered
    var gridW = 12 * dotSize + 11 * gapSize;
    var gridH = 8 * dotSize + 7 * gapSize;
    var hBarW = Math.round(gridW * 0.6);
    var vBarH = Math.round(gridH * 0.6);
    function makeHBar() {
      var bar = document.createElement("div");
      bar.style.width = hBarW + "px";
      bar.style.height = "4px";
      bar.style.background = "#444";
      return bar;
    }
    function makeVBar() {
      var bar = document.createElement("div");
      bar.style.width = "4px";
      bar.style.height = vBarH + "px";
      bar.style.background = "#444";
      return bar;
    }

    // Row: left bar + box + right bar
    box.appendChild(grid);
    var midRow = document.createElement("div");
    midRow.style.display = "flex";
    midRow.style.flexDirection = "row";
    midRow.style.alignItems = "center";
    midRow.appendChild(makeVBar());
    midRow.appendChild(box);
    midRow.appendChild(makeVBar());

    // Column: top bar + midRow + bottom bar
    var boxWrap = document.createElement("div");
    boxWrap.style.display = "flex";
    boxWrap.style.flexDirection = "column";
    boxWrap.style.alignItems = "center";
    boxWrap.appendChild(makeHBar());
    boxWrap.appendChild(midRow);
    boxWrap.appendChild(makeHBar());

    // Pipette index label (only when multiple 96-head pipettes)
    if (numPipettes > 1) {
      var wrapper = document.createElement("div");
      wrapper.style.display = "flex";
      wrapper.style.flexDirection = "column";
      wrapper.style.alignItems = "center";
      var idLabel = document.createElement("span");
      idLabel.textContent = String(p);
      idLabel.style.fontSize = "15px";
      idLabel.style.fontWeight = "700";
      idLabel.style.color = "#888";
      idLabel.style.marginBottom = "2px";
      wrapper.appendChild(idLabel);
      wrapper.appendChild(boxWrap);
      panel.appendChild(wrapper);
    } else {
      panel.appendChild(boxWrap);
    }
  }
}

function buildSingleArm(armData, anchorDropdown, armId) {
  // Build one gripper visualization column
  var hasResource = armData !== null && armData !== undefined;
  var col = document.createElement("div");
  col.style.display = "flex";
  col.style.flexDirection = "column";
  col.style.alignItems = "center";
  col.style.justifyContent = "center";

  // Compute scaled plate dimensions for gripper sizing.
  // The serialized resource data (saved before destruction in resourceSnapshots) is used
  // to re-instantiate the resource and draw it on a live Konva stage inside the arm panel,
  // using the exact same draw() code as the main canvas.
  var plateW = 52, plateH = 22;
  var snapshot = null; // serialized resource data, or null
  if (hasResource) {
    snapshot = resourceSnapshots[armData.resource_name] || null;
    var sizeX = snapshot ? snapshot.size_x : (armData.size_x || 127);
    var sizeY = snapshot ? snapshot.size_y : (armData.size_y || 86);
    var scale = Math.min(80 / sizeX, 80 / sizeY);
    plateW = Math.round(sizeX * scale);
    plateH = Math.round(sizeY * scale);
  }

  // Carriage uses a fixed "closed" gap regardless of plate presence.
  // Fingers spread outward to accommodate a held plate.
  // SVG is always wide enough for a standard plate (127×86 mm) so the popup
  // does not resize when a plate is picked up or dropped.
  var stdPlateW = Math.round(127 * Math.min(80 / 127, 80 / 86)); // ≈80px
  var minFingerGap = Math.round((stdPlateW + 16) * 1.1);          // ≈106px
  var closedGap = Math.round((52 + 16) * 1.1); // default closed spacing
  var fingerGap = hasResource ? Math.round((plateW + 16) * 1.1) : closedGap;
  var svgW = Math.max(closedGap, fingerGap, minFingerGap) + 28; // 14px margin each side (room for outer guide bars)
  var svgH = 110;
  var cx = svgW / 2; // centre x

  // Finger (rail) positions spread based on plate size
  var lRailX = cx - fingerGap / 2 - 7; // left rail, offset outward from center
  var rRailX = cx + fingerGap / 2 - 1; // right rail, offset outward from center

  var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", String(svgW));
  svg.setAttribute("height", String(svgH));
  svg.setAttribute("viewBox", "0 0 " + svgW + " " + svgH);
  svg.style.overflow = "visible";
  var defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  defs.innerHTML =
    '<filter id="armGlow" x="-50%" y="-50%" width="200%" height="200%">' +
      '<feFlood flood-color="#44BB99" flood-opacity="0.5" result="color"/>' +
      '<feComposite in="color" in2="SourceAlpha" operator="in" result="colored"/>' +
      '<feGaussianBlur in="colored" stdDeviation="3" result="glow1"/>' +
      '<feGaussianBlur in="colored" stdDeviation="6" result="glow2"/>' +
      '<feMerge><feMergeNode in="glow2"/><feMergeNode in="glow1"/><feMergeNode in="SourceGraphic"/></feMerge>' +
    '</filter>';
  svg.appendChild(defs);
  var shapes = "";

  // Horizontal guide bars — drawn first (behind rails), extending inward from each finger
  var barH = 5.1, barW = 12, barY = 10; // aligned with top of rails
  shapes += '<rect x="' + (lRailX + 7) + '" y="' + barY + '" width="' + barW + '" height="' + barH + '" rx="1" ry="1" fill="#555" stroke="#444" stroke-width="0.6"/>';
  shapes += '<rect x="' + (rRailX + 1 - barW) + '" y="' + barY + '" width="' + barW + '" height="' + barH + '" rx="1" ry="1" fill="#555" stroke="#444" stroke-width="0.6"/>';

  // Draw rails after guide bars (painter's order)
  // Left rail (7px wide, 90% of original 8px)
  shapes += '<rect x="' + lRailX + '" y="10" width="7" height="75" fill="#333" stroke="#222" stroke-width="1"/>';
  // Right rail
  shapes += '<rect x="' + (rRailX + 1) + '" y="10" width="7" height="75" fill="#333" stroke="#222" stroke-width="1"/>';

  // Top carriage block (grey, wide) — fixed size, drawn after rails so it covers them
  var carriageW = closedGap + 8;
  var carriageX = cx - carriageW / 2;
  shapes += '<rect x="' + carriageX + '" y="0" width="' + carriageW + '" height="24" rx="2" ry="2" fill="#aaa" stroke="#666" stroke-width="1.2"/>';
  // Darker top strip on carriage
  shapes += '<rect x="' + carriageX + '" y="0" width="' + carriageW + '" height="7" rx="2" ry="2" fill="#888" stroke="#666" stroke-width="1.2"/>';
  // Centre mounting post
  shapes += '<rect x="' + (cx - 4) + '" y="3" width="8" height="18" fill="#666" stroke="#555" stroke-width="0.8"/>';

  // Cushion geometry: pad y=74, height=22 → center at y=85
  var cushY = 74, cushH = 22, pinH = 2.4;
  var cushCenterY = cushY + cushH / 2;       // 85
  var pinOffset = 5;                          // distance from center to pin center
  var pinTopY = cushCenterY - pinOffset - pinH / 2;    // 78.8
  var pinBotY = cushCenterY + pinOffset - pinH / 2;    // 88.8

  // Left finger cushion — pins drawn first (behind), then vertical pad on top
  var lCushX = lRailX + 7 + 2; // rail width + gap
  shapes += '<rect x="' + (lCushX + 2) + '" y="' + pinTopY + '" width="5" height="' + pinH + '" rx="0.5" ry="0.5" fill="#555" stroke="#333" stroke-width="0.4"/>';
  shapes += '<rect x="' + (lCushX + 2) + '" y="' + pinBotY + '" width="5" height="' + pinH + '" rx="0.5" ry="0.5" fill="#555" stroke="#333" stroke-width="0.4"/>';
  shapes += '<rect x="' + lCushX + '" y="' + cushY + '" width="4" height="' + cushH + '" rx="1" ry="1" fill="#444" stroke="#333" stroke-width="0.6"/>';

  // Right finger cushion — pins drawn first (behind), then vertical pad on top
  var rCushX = rRailX + 1 - 2 - 4; // rail left edge - gap - cushion width
  shapes += '<rect x="' + (rCushX - 3) + '" y="' + pinTopY + '" width="5" height="' + pinH + '" rx="0.5" ry="0.5" fill="#555" stroke="#333" stroke-width="0.4"/>';
  shapes += '<rect x="' + (rCushX - 3) + '" y="' + pinBotY + '" width="5" height="' + pinH + '" rx="0.5" ry="0.5" fill="#555" stroke="#333" stroke-width="0.4"/>';
  shapes += '<rect x="' + rCushX + '" y="' + cushY + '" width="4" height="' + cushH + '" rx="1" ry="1" fill="#444" stroke="#333" stroke-width="0.6"/>';

  var gripperG = document.createElementNS("http://www.w3.org/2000/svg", "g");
  gripperG.innerHTML = shapes;
  gripperG.style.cursor = "pointer";
  var gripperTitle = document.createElementNS("http://www.w3.org/2000/svg", "title");
  gripperTitle.textContent = "Arm " + armId + (hasResource ? " — holding " + armData.resource_name : " — empty") + " — click for details";
  gripperG.appendChild(gripperTitle);
  gripperG.addEventListener("mouseenter", function () { gripperG.setAttribute("filter", "url(#armGlow)"); });
  gripperG.addEventListener("mouseleave", function () { gripperG.removeAttribute("filter"); });
  gripperG.addEventListener("click", function (e) {
    e.stopPropagation();
    var attrs = [{ key: "arm", value: armId }];
    attrs.push({ key: "has_resource", value: hasResource ? "true" : "false" });
    if (hasResource) {
      attrs.push({ key: "resource_name", value: armData.resource_name });
      attrs.push({ key: "resource_type", value: armData.resource_type || "Unknown" });
      attrs.push({ key: "direction", value: armData.direction || "?" });
      attrs.push({ key: "pickup_distance_from_top", value: (armData.pickup_distance_from_top || 0) + " mm" });
      attrs.push({ key: "size", value: (armData.size_x || "?") + " × " + (armData.size_y || "?") + " × " + (armData.size_z || "?") + " mm" });
      if (armData.num_items_x) attrs.push({ key: "wells", value: (armData.num_items_x * (armData.num_items_y || 1)) });
    }
    showPipetteInfoPanel("Arm " + armId, "IntegratedArm", attrs, anchorDropdown, armId, "channel");
  });
  svg.appendChild(gripperG);
  // Wrap the SVG and plate in a positioned container.
  var svgContainer = document.createElement("div");
  svgContainer.style.position = "relative";
  svgContainer.style.width = svgW + "px";
  svgContainer.style.height = svgH + "px";
  svgContainer.appendChild(svg);

  if (hasResource && snapshot) {
    // Render the plate using the exact same Konva draw() code as the main canvas.
    // The serialized resource data (saved before destruction) is re-instantiated via
    // loadResource() and drawn on a temporary DOM-attached Konva stage. The result is
    // exported as a PNG data URL and displayed as an <img> overlay on the gripper SVG.
    // Konva requires its container to be in the DOM to render, so we use a hidden div
    // attached to document.body, then clean up after export.
    // Cost: one temporary Konva stage + ~97 nodes for a 96-well plate, created and
    // destroyed each time the arm panel updates.
    var plateX = cx - plateW / 2;
    var plateY = 85 - plateH / 2;
    try {
      var realW = Math.ceil(snapshot.size_x);
      var realH = Math.ceil(snapshot.size_y);
      // Create a hidden div in the DOM for Konva to render into
      var tmpDiv = document.createElement("div");
      tmpDiv.style.position = "fixed";
      tmpDiv.style.left = "-9999px";
      tmpDiv.style.top = "-9999px";
      document.body.appendChild(tmpDiv);
      var plateStage = new Konva.Stage({ container: tmpDiv, width: realW, height: realH });
      var plateLayer = new Konva.Layer();
      plateStage.add(plateLayer);
      // Re-instantiate the resource from saved serialized data and draw it.
      // Temporarily save/restore global resources to avoid conflicts.
      var savedRes = {};
      var snapshotData = JSON.parse(JSON.stringify(snapshot));
      snapshotData.parent_name = undefined;
      snapshotData.location = { x: 0, y: 0, z: 0, type: "Coordinate" };
      function _saveKey(n) { if (n in resources) savedRes[n] = resources[n]; }
      _saveKey(snapshotData.name);
      for (var si = 0; si < (snapshotData.children || []).length; si++) {
        _saveKey(snapshotData.children[si].name);
      }
      var plateCopy = loadResource(snapshotData);
      plateCopy.draw(plateLayer);
      plateLayer.draw();
      // Export to data URL
      var plateDataUrl = plateStage.toDataURL({ pixelRatio: 2 });
      // Clean up: restore resources, destroy offscreen stage
      delete resources[plateCopy.name];
      for (var ci2 = 0; ci2 < plateCopy.children.length; ci2++) {
        delete resources[plateCopy.children[ci2].name];
      }
      for (var rk in savedRes) { resources[rk] = savedRes[rk]; }
      plateStage.destroy();
      document.body.removeChild(tmpDiv);
      // Display as an <img> overlay
      var plateImg = document.createElement("img");
      plateImg.src = plateDataUrl;
      plateImg.style.position = "absolute";
      plateImg.style.left = plateX + "px";
      plateImg.style.top = plateY + "px";
      plateImg.style.width = plateW + "px";
      plateImg.style.height = plateH + "px";
      plateImg.style.pointerEvents = "none";
      svgContainer.appendChild(plateImg);
    } catch (e) {
      console.warn("[arm plate render] failed:", e);
    }
  } else if (hasResource) {
    // Fallback: simple colored rectangle when no serialized data is available
    var plateX2 = cx - plateW / 2;
    var plateY2 = 85 - plateH / 2;
    var fallbackColor = RESOURCE_COLORS[armData.resource_type] || RESOURCE_COLORS["Resource"];
    var fallbackSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    fallbackSvg.setAttribute("width", String(svgW));
    fallbackSvg.setAttribute("height", String(svgH));
    fallbackSvg.setAttribute("viewBox", "0 0 " + svgW + " " + svgH);
    fallbackSvg.style.position = "absolute";
    fallbackSvg.style.left = "0";
    fallbackSvg.style.top = "0";
    fallbackSvg.style.pointerEvents = "none";
    fallbackSvg.innerHTML = '<rect x="' + plateX2 + '" y="' + plateY2 + '" width="' + plateW +
      '" height="' + plateH + '" fill="' + fallbackColor + '" stroke="#222" stroke-width="1.2"/>';
    svgContainer.appendChild(fallbackSvg);
  }

  svgContainer.style.cursor = "pointer";
  col.appendChild(svgContainer);
  var label = document.createElement("div");
  label.style.fontSize = "11px";
  label.style.fontWeight = "600";
  label.style.color = "#666";
  label.style.marginTop = "4px";
  label.style.textAlign = "center";
  if (hasResource) {
    label.textContent = armData.resource_name + " (" + armData.resource_type + ")";
  } else {
    label.textContent = "No resource held";
  }
  col.appendChild(label);
  return col;
}

function fillArmPanel(panel, armState) {
  panel.innerHTML = "";
  if (!armState || Object.keys(armState).length === 0) {
    // Set panel dimensions to match a normal arm panel, then center message
    var stdW = Math.round((Math.round(127 * Math.min(80 / 127, 80 / 86)) + 16) * 1.1) + 28;
    panel.style.minWidth = stdW + "px";
    panel.style.minHeight = "130px";
    panel.style.alignItems = "center";
    panel.style.justifyContent = "center";
    var msg = document.createElement("span");
    msg.style.color = "#888";
    msg.style.fontSize = "13px";
    msg.style.fontWeight = "500";
    msg.style.textAlign = "center";
    msg.style.padding = "16px";
    msg.textContent = "No robotic arm is installed on this liquid handler.";
    panel.appendChild(msg);
    return;
  }
  var arms = Object.keys(armState).sort(function (a, b) { return +a - +b; });
  for (var i = 0; i < arms.length; i++) {
    var armId = arms[i];
    var wrapper = document.createElement("div");
    wrapper.style.display = "flex";
    wrapper.style.flexDirection = "column";
    wrapper.style.alignItems = "center";
    // Arm index label (only when multiple arms)
    if (arms.length > 1) {
      var idLabel = document.createElement("span");
      idLabel.textContent = armId;
      idLabel.style.fontSize = "15px";
      idLabel.style.fontWeight = "700";
      idLabel.style.color = "#888";
      idLabel.style.marginBottom = "2px";
      idLabel.title = "Arm " + armId + " — click gripper for details";
      wrapper.appendChild(idLabel);
    }
    wrapper.appendChild(buildSingleArm(armState[armId], panel, armId));
    panel.appendChild(wrapper);
  }
}

class LiquidHandler extends Resource {
  constructor(resource) {
    super(resource);
    this.numHeads = 0;
    this.headState = {};
    this.head96State = {};
    this.armState = {};
  }

  drawMainShape() {
    return undefined; // just draw the children (deck and so on)
  }

  setState(state) {
    if (state.head_state) {
      this.headState = state.head_state;
      this.numHeads = Object.keys(state.head_state).length;
      var panel = document.getElementById("single-channel-dropdown-" + this.name);
      if (panel) {
        fillHeadIcons(panel, this.headState);
        refreshPipetteInfoPanel(this.headState);
      }
    }
    if ("head96_state" in state) {
      this.head96State = state.head96_state;
      // Show/hide multi-channel button based on whether the machine tool exists
      var multiBtn = document.getElementById("multi-channel-btn-" + this.name);
      if (multiBtn) multiBtn.style.display = (this.head96State !== null && this.head96State !== undefined) ? "" : "none";
      var panel96 = document.getElementById("multi-channel-dropdown-" + this.name);
      if (panel96) {
        fillHead96Grid(panel96, this.head96State);
      }
    }
    if ("arm_state" in state) {
      this.armState = state.arm_state;
      // Show/hide arm button based on whether the machine tool exists
      var armBtnEl = document.getElementById("arm-btn-" + this.name);
      if (armBtnEl) armBtnEl.style.display = (this.armState !== null && this.armState !== undefined) ? "" : "none";
      // Snapshot each held resource NOW, while it is still in the resources dict.
      // pick_up_resource() sends set_state BEFORE resource_unassigned fires, so
      // the resource and all its children are still intact at this point.
      for (var armKey in (this.armState || {})) {
        var ad = this.armState[armKey];
        if (ad && ad.resource_name && !resourceSnapshots[ad.resource_name]) {
          var res = resources[ad.resource_name];
          if (res) {
            try {
              resourceSnapshots[ad.resource_name] = res.serialize();
            } catch (e) {
              console.warn("[arm snapshot] failed for " + ad.resource_name, e);
            }
          }
        }
      }
      var armPanel = document.getElementById("arm-dropdown-" + this.name);
      if (armPanel) {
        fillArmPanel(armPanel, this.armState);
      }
    }
  }
}

// ===========================================================================
// Utility for mapping resource type strings to classes
// ===========================================================================

function classForResourceType(type, category) {
  switch (type) {
    case "Deck":
      return Deck;
    case ("HamiltonDeck", "HamiltonSTARDeck"):
      return HamiltonSTARDeck;
    case "Trash":
      return Trash;
    case "OTDeck":
      return OTDeck;
    case "Plate":
      return Plate;
    case "Well":
      return Well;
    case "TipRack":
      return TipRack;
    case "TipSpot":
      return TipSpot;
    case "ResourceHolder":
      return ResourceHolder;
    case "PlateHolder":
      return PlateHolder;
    case "Carrier":
      return Carrier;
    case "PlateCarrier":
      return PlateCarrier;
    case "TipCarrier":
      return TipCarrier;
    case "TroughCarrier":
      return TroughCarrier;
    case "TubeCarrier":
      return TubeCarrier;
    case "MFXCarrier":
      return Carrier;
    case "Container":
      return Container;
    case "Trough":
      return Trough;
    case "VantageDeck":
      return VantageDeck;
    case "LiquidHandler":
      return LiquidHandler;
    case "TubeRack":
      return TubeRack;
    case "Tube":
      return Tube;
    default:
      break;
  }

  // Fall back to category for unrecognized type names (e.g. concrete carrier subclasses).
  switch (category) {
    case "tip_carrier":
      return TipCarrier;
    case "plate_carrier":
      return PlateCarrier;
    case "trough_carrier":
      return TroughCarrier;
    case "tube_carrier":
      return TubeCarrier;
    case "carrier":
    case "mfx_carrier":
      return Carrier;
    case "deck":
      return Deck;
    case "liquid_handler":
      return LiquidHandler;
    case "tip_rack":
      return TipRack;
    case "plate":
      return Plate;
    case "well":
      return Well;
    case "tip_spot":
      return TipSpot;
    case "resource_holder":
      return ResourceHolder;
    case "plate_holder":
      return PlateHolder;
    case "tube_rack":
      return TubeRack;
    case "tube":
      return Tube;
    case "container":
    case "trough":
      return Container;
    default:
      return Resource;
  }
}

function loadResource(resourceData) {
  const resourceClass = classForResourceType(resourceData.type, resourceData.category);

  const parentName = resourceData.parent_name;
  var parent = undefined;
  if (parentName !== undefined) {
    parent = resources[parentName];
  }

  const resource = new resourceClass(resourceData, parent);
  resources[resource.name] = resource;

  // If the resource has a parent, ensure it's registered in the parent's children list.
  // The constructor sets this.parent but doesn't add to parent.children.
  if (parent && !parent.children.includes(resource)) {
    parent.assignChild(resource);
  }

  return resource;
}

// ===========================================================================
// init
// ===========================================================================

window.addEventListener("load", function () {
  const canvas = document.getElementById("kanvas");
  canvasWidth = canvas.offsetWidth;
  canvasHeight = canvas.offsetHeight;

  stage = new Konva.Stage({
    container: "kanvas",
    width: canvasWidth,
    height: canvasHeight,
    draggable: true,
  });
  stage.scaleY(-1);
  stage.offsetY(canvasHeight);

  // add white background (large enough to cover any pan position)
  var background = new Konva.Rect({
    x: -5000,
    y: -5000,
    width: 10000,
    height: 10000,
    fill: "white",
    listening: false,
  });

  // add the layer to the stage
  stage.add(layer);
  stage.add(resourceLayer);

  layer.add(background);

  // Scale bar update: picks a round mm value that fits ~80-120px on screen.
  function updateScaleBar() {
    const scale = stage.scaleX(); // CSS pixels per mm
    // Choose a nice round distance whose bar width falls near 100px
    const niceSteps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
    let bestMM = niceSteps[0];
    for (let i = 0; i < niceSteps.length; i++) {
      if (niceSteps[i] * scale >= 60) {
        bestMM = niceSteps[i];
        break;
      }
      bestMM = niceSteps[i];
    }
    const barPx = bestMM * scale;
    const barLine = document.getElementById("scale-bar-line");
    const barLabel = document.getElementById("scale-bar-label");
    if (barLine) barLine.style.width = barPx + "px";
    if (barLabel) barLabel.textContent = bestMM + " mm";
  }

  // Mouse wheel zoom
  const scaleBy = 1.1;
  stage.on("wheel", function (e) {
    e.evt.preventDefault();
    const oldScale = stage.scaleX();
    const pointer = stage.getPointerPosition();

    // scaleY is negative (flipped), so use absolute value for uniform zoom
    const mousePointTo = {
      x: (pointer.x - stage.x()) / oldScale,
      y: (pointer.y - stage.y()) / stage.scaleY(),
    };

    const direction = e.evt.deltaY > 0 ? -1 : 1;
    const newScale = direction > 0 ? oldScale * scaleBy : oldScale / scaleBy;

    // Clamp zoom level
    const clampedScale = Math.max(0.1, Math.min(30, newScale));

    stage.scaleX(clampedScale);
    stage.scaleY(-clampedScale); // keep Y flipped

    const newPos = {
      x: pointer.x - mousePointTo.x * clampedScale,
      y: pointer.y - mousePointTo.y * (-clampedScale),
    };
    stage.position(newPos);
    updateScaleBar();
    updateBullseyeScale();
    updateWrtBullseyeScale();
    updateTooltipScale();
    updateDeltaLinesScale();
  });

  updateScaleBar();

  // Keep Konva stage sized to its container when the layout changes
  // (e.g. sidebar expand/collapse/resize, window resize).
  const resizeObserver = new ResizeObserver(function () {
    const newWidth = canvas.offsetWidth;
    const newHeight = canvas.offsetHeight;
    if (newWidth > 0 && newHeight > 0) {
      stage.width(newWidth);
      stage.height(newHeight);
      stage.offsetY(newHeight);
    }
  });
  resizeObserver.observe(canvas);

  // Home button: reset view to initial position and zoom
  const homeBtn = document.getElementById("home-button");
  if (homeBtn) {
    homeBtn.addEventListener("click", function () {
      fitToViewport();
      homeBtn.classList.add("clicked");
      setTimeout(function () { homeBtn.classList.remove("clicked"); }, 400);
    });
  }

  // Zoom buttons
  function zoomByFactor(factor) {
    var oldScale = stage.scaleX();
    var newScale = Math.max(0.1, Math.min(15, oldScale * factor));
    var center = { x: stage.width() / 2, y: stage.height() / 2 };
    var mousePointTo = {
      x: (center.x - stage.x()) / oldScale,
      y: (center.y - stage.y()) / stage.scaleY(),
    };
    stage.scaleX(newScale);
    stage.scaleY(-newScale);
    stage.position({
      x: center.x - mousePointTo.x * newScale,
      y: center.y - mousePointTo.y * (-newScale),
    });
    if (typeof updateScaleBar === "function") updateScaleBar();
    updateBullseyeScale();
    updateWrtBullseyeScale();
    updateTooltipScale();
    updateDeltaLinesScale();
  }

  var zoomInBtn = document.getElementById("zoom-in-btn");
  var zoomOutBtn = document.getElementById("zoom-out-btn");
  if (zoomInBtn) zoomInBtn.addEventListener("click", function () { zoomByFactor(1.2); });
  if (zoomOutBtn) zoomOutBtn.addEventListener("click", function () { zoomByFactor(1 / 1.2); });

  // Check if there is an after stage setup callback, and if so, call it.
  if (typeof afterStageSetup === "function") {
    afterStageSetup();
  }
});

function gifResetUI() {
  document.getElementById("gif-start").hidden = true;
  document.getElementById("gif-recording").hidden = true;
  document.getElementById("gif-processing").hidden = true;
  document.getElementById("gif-download").hidden = true;
}

function gifShowStartUI() {
  document.getElementById("gif-start").hidden = false;
}

function gifShowRecordingUI() {
  document.getElementById("gif-recording").hidden = false;
}

function gifShowProcessingUI() {
  document.getElementById("gif-processing").hidden = false;
}

function gifShowDownloadUI() {
  document.getElementById("gif-download").hidden = false;
}

async function startRecording() {
  // Turn recording on
  isRecording = true;

  // Reset saved frames buffer
  frameImages = [];

  // Reset the render progress
  var info = document.getElementById("progressBar");
  info.innerText = " GIF Rendering Progress: " + Math.round(0 * 100) + "%";

  // Start sequential capture loop: capture a frame, wait for the interval,
  // then capture the next. This guarantees every tick produces a frame
  // even when html2canvas is slower than the interval.
  _captureLoop();

  gifResetUI();
  gifShowRecordingUI();
}

function stopRecording() {
  gifResetUI();
  gifShowProcessingUI();

  // Turn recording off
  isRecording = false;
  if (_recordingTimer) { clearTimeout(_recordingTimer); _recordingTimer = null; }

  // Wait for any in-flight capture to finish before building GIF
  setTimeout(function () {
    // Use dimensions from the first captured frame (includes overflow)
    var gifW = frameImages.length > 0 ? frameImages[0].width : stage.width();
    var gifH = frameImages.length > 0 ? frameImages[0].height : stage.height();
    gif = new GIF({
      workers: 10,
      workerScript: "gif.worker.js",
      background: "#FFFFFF",
      width: gifW,
      height: gifH,
    });

  // Add each frame to the GIF
  for (var i = 0; i < frameImages.length; i++) {
    gif.addFrame(frameImages[i], { delay: Math.max(200, frameInterval * 50) });
  }

  // Add progress bar based on how much the gif is rendered
  gif.on("progress", function (p) {
    var info = document.getElementById("progressBar");
    info.innerText = " GIF Rendering Progress: " + Math.round(p * 100) + "%";
  });

  // Load gif into right portion of screen
  gif.on("finished", function (blob) {
    renderedGifBlob = blob;
    gifResetUI();
    gifShowDownloadUI();
    gifShowStartUI();
  });

    gif.render();
  }, 1500);
}

// Capture the entire <main> element (canvas + overlays) as a frame
// Sequential capture loop: capture one frame, wait for the remaining interval
// time, then schedule the next. Every tick produces exactly one frame.
function _captureLoop() {
  if (!isRecording) return;
  var intervalMs = Math.max(200, frameInterval * 50);
  var startTime = Date.now();
  var mainEl = document.querySelector("main");
  if (!mainEl) return;
  // Compute capture size including any rightward/downward overflow
  var captureW = mainEl.offsetWidth;
  var captureH = mainEl.offsetHeight;
  var mainRect = mainEl.getBoundingClientRect();
  var overflows = mainEl.querySelectorAll(".machine-tool-dropdown.open, .tool-panel, .uml-panel");
  overflows.forEach(function (el) {
    var r = el.getBoundingClientRect();
    var right = r.right - mainRect.left;
    var bottom = r.bottom - mainRect.top;
    if (right > captureW) captureW = Math.ceil(right);
    if (bottom > captureH) captureH = Math.ceil(bottom);
  });
  html2canvas(mainEl, {
    backgroundColor: "#FFFFFF",
    scale: 1,
    useCORS: true,
    logging: false,
    width: captureW,
    height: captureH,
  }).then(function (canvas) {
    canvas.toBlob(function (blob) {
      if (blob) {
        var url = URL.createObjectURL(blob);
        var myImg = new Image();
        myImg.src = url;
        myImg.width = canvas.width;
        myImg.height = canvas.height;
        frameImages.push(myImg);
        myImg.onload = function () { URL.revokeObjectURL(url); };
      }
      // Wait for the remaining interval time before next capture
      var elapsed = Date.now() - startTime;
      var waitMs = Math.max(0, intervalMs - elapsed);
      _recordingTimer = setTimeout(_captureLoop, waitMs);
    }, "image/jpeg", 0.3);
  }).catch(function () {
    var elapsed = Date.now() - startTime;
    var waitMs = Math.max(0, intervalMs - elapsed);
    _recordingTimer = setTimeout(_captureLoop, waitMs);
  });
}

// Set up event listeners for the buttons
document
  .getElementById("start-recording-button")
  .addEventListener("click", startRecording);

document
  .getElementById("stop-recording-button")
  .addEventListener("click", stopRecording);

document
  .getElementById("gif-download-button")
  .addEventListener("click", function () {
    if (!renderedGifBlob) {
      alert("No GIF rendered yet. Please stop the recording first.");
      return;
    }

    var fileName =
      document.getElementById("fileName").value || "plr-visualizer";
    var url = URL.createObjectURL(renderedGifBlob);
    var a = document.createElement("a");
    a.href = url;
    if (!fileName.endsWith(".gif")) {
      fileName += ".gif";
    }
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  });

document
  .getElementById("gif-frame-rate")
  .addEventListener("input", function () {
    let value = parseInt(this.value);
    // Adjust the value to the nearest multiple of 8
    value = Math.round(value / 8) * 8;
    // Ensure the value stays within the allowed range
    if (value < 1) value = 1;
    if (value > 96) value = 96;

    this.value = value; // Update the slider value
    document.getElementById("current-value").textContent =
      "Frame Interval: " + value;

    frameInterval = value;
    // New interval takes effect on the next iteration of _captureLoop automatically
  });

window.addEventListener("load", function () {
  gifResetUI();
  gifShowStartUI();
});

// ===========================================================================
// Sidepanel Resource Tree
// ===========================================================================

var sidepanelSelectedResource = null;
var sidebarRootResource = null;
var sidepanelHighlightRect = null;
var sidepanelHoverRect = null;
var sidepanelHoverGlow = null;

function getResourceTypeName(resource) {
  return resource.constructor.name;
}

function getResourceColor(resource) {
  const typeName = getResourceTypeName(resource);
  return RESOURCE_COLORS[typeName] || RESOURCE_COLORS["Resource"];
}

function isTipRackLike(resource) {
  return resource.category === "tip_rack" ||
    resource instanceof TipRack ||
    (resource.children.length > 0 && resource.children[0] instanceof TipSpot);
}

function isPlateLike(resource) {
  return resource.category === "plate" ||
    resource instanceof Plate ||
    (resource.children.length > 0 && resource.children[0] instanceof Well);
}

function isTubeRackLike(resource) {
  return resource.category === "tube_rack" ||
    resource instanceof TubeRack ||
    (resource.children.length > 0 && resource.children[0] instanceof Tube);
}

function isCarrierLike(resource) {
  return resource instanceof Carrier ||
    ["carrier", "tip_carrier", "plate_carrier", "trough_carrier", "tube_carrier"]
      .includes(resource.category);
}

function getResourceSummary(resource) {
  if (isTipRackLike(resource)) {
    const totalSpots = resource.children.length;
    let tipsPresent = 0;
    for (let i = 0; i < resource.children.length; i++) {
      if (resource.children[i].has_tip) {
        tipsPresent++;
      }
    }
    return `${tipsPresent}/${totalSpots} tips`;
  }

  if (isPlateLike(resource)) {
    const numChildren = resource.children.length;
    return `${numChildren} wells`;
  }

  if (isTubeRackLike(resource)) {
    const numChildren = resource.children.length;
    return `${numChildren} tubes`;
  }

  if (resource instanceof Container) {
    const vol = resource.getVolume();
    if (vol > 0) {
      return `${vol.toFixed(1)} \u00B5L`;
    }
    return "";
  }

  if (isCarrierLike(resource)) {
    // Count meaningful children (skip empty resource holders)
    let childCount = 0;
    let childType = "";
    for (let i = 0; i < resource.children.length; i++) {
      const holder = resource.children[i];
      if (holder.children && holder.children.length > 0) {
        childCount++;
        if (!childType && holder.children[0]) {
          childType = (holder.children[0].resourceType || "item").toLowerCase() + "s";
        }
      }
    }
    if (childCount > 0) {
      return `${childCount} ${childType}`;
    }
    return `${resource.children.length} sites`;
  }

  return "";
}

function isResourceHolder(resource) {
  return resource instanceof ResourceHolder || resource instanceof PlateHolder ||
    resource.category === "resource_holder" || resource.category === "plate_holder";
}

function isDeckLike(resource) {
  return resource instanceof Deck || resource instanceof LiquidHandler ||
    ["deck", "liquid_handler"].includes(resource.category);
}

// Get the "display children" of a resource, skipping invisible intermediaries
// like ResourceHolders, and flattening Deck/LiquidHandler wrappers.
function getDisplayChildren(resource) {
  if (isDeckLike(resource)) {
    // Flatten: collect all children, recursing through nested decks/LiquidHandlers
    let result = [];
    for (let i = 0; i < resource.children.length; i++) {
      const child = resource.children[i];
      if (isDeckLike(child)) {
        result = result.concat(getDisplayChildren(child));
      } else {
        result.push(child);
      }
    }
    return result;
  }

  if (isCarrierLike(resource)) {
    // Return one entry per slot. Each entry is {index, resource} or {index, empty: true}.
    let result = [];
    for (let i = 0; i < resource.children.length; i++) {
      const holder = resource.children[i];
      if (isResourceHolder(holder) && holder.children.length > 0) {
        for (let j = 0; j < holder.children.length; j++) {
          result.push({ index: i, resource: holder.children[j] });
        }
      } else if (isResourceHolder(holder)) {
        result.push({ index: i, empty: true, holderName: holder.name });
      } else {
        result.push({ index: i, resource: holder });
      }
    }
    // Sort by high y to low y; when y is equal, sort by ascending x.
    function getSlotLocation(entry) {
      if (entry.empty) return resource.children[entry.index].location;
      return entry.resource.parent ? entry.resource.parent.location : entry.resource.location;
    }
    result.sort((a, b) => {
      const al = getSlotLocation(a);
      const bl = getSlotLocation(b);
      if (bl.y !== al.y) return bl.y - al.y;
      return al.x - bl.x;
    });
    // Assign display indices: for vertical layouts (varying y), use n,...,1,0 top to bottom.
    // For horizontal layouts (same y, varying x), use 0,...,n-1 left to right.
    const allSameY = result.length > 1 && result.every(function (e) {
      return getSlotLocation(e).y === getSlotLocation(result[0]).y;
    });
    for (let i = 0; i < result.length; i++) {
      result[i].index = allSameY ? i : result.length - 1 - i;
    }
    return result;
  }

  // Leaf containers: don't show individual wells/tips/tubes
  if (isTipRackLike(resource) || isPlateLike(resource) || isTubeRackLike(resource)) {
    return [];
  }

  return resource.children || [];
}

function buildEmptySlotDOM(index, depth, holderName) {
  const node = document.createElement("div");
  node.className = "tree-node tree-node-empty";

  const row = document.createElement("div");
  row.className = "tree-node-row empty-slot";
  row.style.paddingLeft = (8 + depth * 16) + "px";

  const arrow = document.createElement("span");
  arrow.className = "tree-node-arrow";

  const indexSpan = document.createElement("span");
  indexSpan.className = "tree-node-index";
  indexSpan.textContent = index;

  const nameSpan = document.createElement("span");
  nameSpan.className = "tree-node-name empty";
  nameSpan.textContent = "<empty>";

  row.appendChild(arrow);
  row.appendChild(indexSpan);
  row.appendChild(nameSpan);
  node.appendChild(row);

  // Hover to highlight the resource holder on canvas
  if (holderName) {
    row.addEventListener("mouseenter", function () {
      showHoverHighlight(holderName);
    });
    row.addEventListener("mouseleave", function () {
      clearHoverHighlight();
    });
  }

  return node;
}

function buildTreeNodeDOM(resource, depth, slotIndex) {
  const node = document.createElement("div");
  node.className = "tree-node";
  node.dataset.resourceName = resource.name;

  const row = document.createElement("div");
  row.className = "tree-node-row";
  row.style.paddingLeft = (8 + depth * 16) + "px";

  const displayChildren = getDisplayChildren(resource);
  // Carrier children are {index, resource} objects; others are plain resources
  const isCarrier = isCarrierLike(resource);
  const hasVisibleChildren = displayChildren.length > 0;

  // Arrow
  const arrow = document.createElement("span");
  arrow.className = "tree-node-arrow";
  if (hasVisibleChildren) {
    arrow.classList.add("has-children");
    arrow.textContent = "\u25BC";
  }

  // Slot index (shown for resources inside a carrier)
  if (slotIndex !== undefined) {
    const indexSpan = document.createElement("span");
    indexSpan.className = "tree-node-index";
    indexSpan.textContent = slotIndex;
    row.appendChild(arrow);
    row.appendChild(indexSpan);
  } else {
    // Color dot (only for top-level items without a slot index)
    const dot = document.createElement("span");
    dot.className = "tree-node-dot";
    dot.style.backgroundColor = getResourceColor(resource);
    row.appendChild(arrow);
    row.appendChild(dot);
  }

  // Name
  const nameSpan = document.createElement("span");
  nameSpan.className = "tree-node-name";
  nameSpan.textContent = resource.name;
  nameSpan.title = `${resource.name} (${resource.resourceType})`;

  // Type label
  const typeSpan = document.createElement("span");
  typeSpan.className = "tree-node-type";
  typeSpan.textContent = resource.resourceType;

  // Info summary
  const info = document.createElement("span");
  info.className = "tree-node-info";
  info.textContent = getResourceSummary(resource);

  row.appendChild(nameSpan);
  row.appendChild(typeSpan);
  row.appendChild(info);
  node.appendChild(row);

  // Hover row to show yellow highlight on canvas
  row.addEventListener("mouseenter", function () {
    showHoverHighlight(resource.name);
  });
  row.addEventListener("mouseleave", function () {
    clearHoverHighlight();
  });

  // Click row to select + show UML panel on canvas (single click only)
  var clickTimer = null;
  row.addEventListener("click", function (e) {
    e.stopPropagation();
    if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; return; }
    clickTimer = setTimeout(function () {
      clickTimer = null;
      showUmlPanel(resource.name);
    }, 250);
  });

  // Double-click row to focus/zoom on resource
  row.addEventListener("dblclick", function (e) {
    e.stopPropagation();
    if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
    focusOnResource(resource.name);
  });

  // Click arrow to toggle children
  if (hasVisibleChildren) {
    arrow.addEventListener("click", function (e) {
      e.stopPropagation();
      const childrenContainer = node.querySelector(":scope > .tree-node-children");
      if (childrenContainer) {
        const isCollapsed = childrenContainer.classList.toggle("collapsed");
        arrow.textContent = isCollapsed ? "\u25B6" : "\u25BC";
        buildWrtDropdown();
      }
    });
  }

  // Build children
  if (hasVisibleChildren) {
    const childrenDiv = document.createElement("div");
    childrenDiv.className = "tree-node-children";
    for (let i = 0; i < displayChildren.length; i++) {
      const entry = displayChildren[i];
      if (isCarrier && entry.empty) {
        childrenDiv.appendChild(buildEmptySlotDOM(entry.index, depth + 1, entry.holderName));
      } else if (isCarrier && entry.resource) {
        childrenDiv.appendChild(buildTreeNodeDOM(entry.resource, depth + 1, entry.index));
      } else {
        childrenDiv.appendChild(buildTreeNodeDOM(entry, depth + 1));
      }
    }
    node.appendChild(childrenDiv);
  }

  return node;
}

function buildResourceTree(rootResource, { rebuildNavbar = true } = {}) {
  const treeContainer = document.getElementById("resource-tree");
  if (!treeContainer) return;
  treeContainer.innerHTML = "";
  if (!rootResource) return;
  sidebarRootResource = rootResource;

  // Build a root node for the master resource (e.g. LiquidHandler)
  const rootNode = document.createElement("div");
  rootNode.className = "tree-node";
  rootNode.dataset.resourceName = rootResource.name;

  const rootRow = document.createElement("div");
  rootRow.className = "tree-node-row";
  rootRow.style.paddingLeft = "8px";

  const rootArrow = document.createElement("span");
  rootArrow.className = "tree-node-arrow has-children";
  rootArrow.textContent = "\u25BC";

  const rootDot = document.createElement("span");
  rootDot.className = "tree-node-dot";
  rootDot.style.backgroundColor = getResourceColor(rootResource);

  const rootName = document.createElement("span");
  rootName.className = "tree-node-name";
  rootName.textContent = rootResource.name;
  rootName.title = `${rootResource.name} (${rootResource.resourceType})`;

  const rootType = document.createElement("span");
  rootType.className = "tree-node-type";
  rootType.textContent = rootResource.resourceType;

  rootRow.appendChild(rootArrow);
  rootRow.appendChild(rootDot);
  rootRow.appendChild(rootName);
  rootRow.appendChild(rootType);
  rootNode.appendChild(rootRow);

  rootRow.addEventListener("mouseenter", function () {
    showHoverHighlight(rootResource.name);
  });
  rootRow.addEventListener("mouseleave", function () {
    clearHoverHighlight();
  });
  var rootClickTimer = null;
  rootRow.addEventListener("click", function (e) {
    e.stopPropagation();
    if (rootClickTimer) { clearTimeout(rootClickTimer); rootClickTimer = null; return; }
    rootClickTimer = setTimeout(function () {
      rootClickTimer = null;
      showUmlPanel(rootResource.name);
    }, 250);
  });
  rootRow.addEventListener("dblclick", function (e) {
    e.stopPropagation();
    if (rootClickTimer) { clearTimeout(rootClickTimer); rootClickTimer = null; }
    focusOnResource(rootResource.name);
  });

  // Build deck-level children inside the root node
  const topChildren = getDisplayChildren(rootResource);
  topChildren.sort((a, b) => {
    const ax = a.location ? a.location.x : (a.getAbsoluteLocation ? a.getAbsoluteLocation().x : 0);
    const bx = b.location ? b.location.x : (b.getAbsoluteLocation ? b.getAbsoluteLocation().x : 0);
    return ax - bx;
  });

  const childrenDiv = document.createElement("div");
  childrenDiv.className = "tree-node-children";
  for (let i = 0; i < topChildren.length; i++) {
    childrenDiv.appendChild(buildTreeNodeDOM(topChildren[i], 1));
  }
  rootNode.appendChild(childrenDiv);

  rootArrow.addEventListener("click", function (e) {
    e.stopPropagation();
    const isCollapsed = childrenDiv.classList.toggle("collapsed");
    rootArrow.textContent = isCollapsed ? "\u25B6" : "\u25BC";
  });

  treeContainer.appendChild(rootNode);
  buildWrtDropdown();
  if (rebuildNavbar) buildNavbarLHMachineTools();
}

function addResourceToTree(resource) {
  if (!resource || !resource.parent) return;
  const treeContainer = document.getElementById("resource-tree");
  if (!treeContainer) return;

  // Find the parent node in the tree
  const parentNode = treeContainer.querySelector(
    `.tree-node[data-resource-name="${CSS.escape(resource.parent.name)}"]`
  );
  if (!parentNode) {
    // Parent not in tree; rebuild the whole tree
    const rootName = Object.keys(resources).find(
      (n) => resources[n] && !resources[n].parent
    );
    if (rootName) buildResourceTree(resources[rootName]);
    return;
  }

  // Rebuild the parent node's subtree to reflect the new child
  const parentResource = resources[resource.parent.name];
  if (!parentResource) return;
  const depth = getResourceDepth(parentResource);
  const newParentNode = buildTreeNodeDOM(parentResource, depth);

  parentNode.replaceWith(newParentNode);
}

function removeResourceFromTree(resourceName) {
  const treeContainer = document.getElementById("resource-tree");
  if (!treeContainer) return;

  const node = treeContainer.querySelector(
    `.tree-node[data-resource-name="${CSS.escape(resourceName)}"]`
  );
  if (node) {
    // Find the parent tree-node and rebuild it
    const parentTreeNode = node.parentElement && node.parentElement.closest(".tree-node");
    if (parentTreeNode) {
      const parentName = parentTreeNode.dataset.resourceName;
      const parentResource = resources[parentName];
      if (parentResource) {
        const depth = getResourceDepth(parentResource);
        const newNode = buildTreeNodeDOM(parentResource, depth);
        parentTreeNode.replaceWith(newNode);
        return;
      }
    }
    node.remove();
  }
}

function getResourceDepth(resource) {
  let depth = 0;
  let current = resource;
  while (current.parent) {
    depth++;
    current = current.parent;
  }
  return depth;
}

function updateSidepanelState(resourceName) {
  const treeContainer = document.getElementById("resource-tree");
  if (!treeContainer) return;

  const resource = resources[resourceName];
  if (!resource) return;

  // For tip spots and wells, update the parent summary instead
  if (resource instanceof TipSpot || resource instanceof Well || resource instanceof Tube) {
    if (resource.parent) {
      updateSidepanelNodeInfo(resource.parent.name);
    }
    return;
  }

  updateSidepanelNodeInfo(resourceName);
}

function updateSidepanelNodeInfo(resourceName) {
  const treeContainer = document.getElementById("resource-tree");
  if (!treeContainer) return;

  const resource = resources[resourceName];
  if (!resource) return;

  const node = treeContainer.querySelector(
    `.tree-node[data-resource-name="${CSS.escape(resourceName)}"]`
  );
  if (!node) return;

  const infoSpan = node.querySelector(":scope > .tree-node-row > .tree-node-info");
  if (infoSpan) {
    infoSpan.textContent = getResourceSummary(resource);
  }
}

function showHoverHighlight(resourceName) {
  clearHoverHighlight();
  const resource = resources[resourceName];
  if (!resource || !resource.group) return;
  const absPos = resource.getAbsoluteLocation();
  // Outer glow rect (turquoise shadow, no fill)
  sidepanelHoverGlow = new Konva.Rect({
    x: absPos.x,
    y: absPos.y,
    width: resource.size_x,
    height: resource.size_y,
    stroke: "rgba(0, 220, 220, 0.7)",
    strokeWidth: 2,
    shadowColor: "rgba(0, 220, 220, 0.8)",
    shadowBlur: 12,
    shadowOffsetX: 0,
    shadowOffsetY: 0,
    listening: false,
  });
  // Inner fill rect (yellow, no shadow)
  sidepanelHoverRect = new Konva.Rect({
    x: absPos.x,
    y: absPos.y,
    width: resource.size_x,
    height: resource.size_y,
    fill: "rgba(255, 230, 0, 0.25)",
    listening: false,
  });
  resourceLayer.add(sidepanelHoverGlow);
  resourceLayer.add(sidepanelHoverRect);
  resourceLayer.draw();
}

function clearHoverHighlight() {
  if (sidepanelHoverGlow) {
    sidepanelHoverGlow.destroy();
    sidepanelHoverGlow = null;
  }
  if (sidepanelHoverRect) {
    sidepanelHoverRect.destroy();
    sidepanelHoverRect = null;
  }
  resourceLayer.draw();
}

function highlightSidebarRow(resourceName) {
  clearSidebarHighlight();
  const tree = document.getElementById("resource-tree");
  if (!tree) return;
  const nodes = tree.querySelectorAll(".tree-node");
  const nodeByName = {};
  for (const node of nodes) {
    if (node.dataset.resourceName) {
      nodeByName[node.dataset.resourceName] = node;
    }
  }
  // Walk up the parent chain until we find a resource with a sidebar entry.
  let name = resourceName;
  while (name) {
    if (nodeByName[name]) {
      const row = nodeByName[name].querySelector(":scope > .tree-node-row");
      if (row) {
        row.classList.add("canvas-hover");
        row.scrollIntoView({ block: "nearest" });
      }
      return;
    }
    const res = resources[name];
    if (res && res.parent) {
      name = res.parent.name;
    } else {
      break;
    }
  }
}

function clearSidebarHighlight() {
  const tree = document.getElementById("resource-tree");
  if (!tree) return;
  const highlighted = tree.querySelectorAll(".tree-node-row.canvas-hover");
  for (const row of highlighted) {
    row.classList.remove("canvas-hover");
  }
}

function highlightResourceOnCanvas(resourceName) {
  const resource = resources[resourceName];
  if (!resource) return;

  // Update selected state in tree
  const treeContainer = document.getElementById("resource-tree");
  if (treeContainer) {
    const prev = treeContainer.querySelector(".tree-node-row.selected");
    if (prev) prev.classList.remove("selected");

    const node = treeContainer.querySelector(
      `.tree-node[data-resource-name="${CSS.escape(resourceName)}"]`
    );
    if (node) {
      const row = node.querySelector(":scope > .tree-node-row");
      if (row) row.classList.add("selected");
    }
  }

  // Remove previous highlight
  if (sidepanelHighlightRect) {
    sidepanelHighlightRect.destroy();
    sidepanelHighlightRect = null;
  }

  if (!resource.group) return;

  // Get absolute position on the canvas
  const absPos = resource.getAbsoluteLocation();

  // Draw a highlight rectangle on the resource layer
  sidepanelHighlightRect = new Konva.Rect({
    x: absPos.x - 2,
    y: absPos.y - 2,
    width: resource.size_x + 4,
    height: resource.size_y + 4,
    stroke: "#0d6efd",
    strokeWidth: 2,
    dash: [6, 3],
    listening: false,
  });
  resourceLayer.add(sidepanelHighlightRect);
  resourceLayer.draw();

  // Auto-remove highlight after 2 seconds
  setTimeout(function () {
    if (sidepanelHighlightRect) {
      sidepanelHighlightRect.destroy();
      sidepanelHighlightRect = null;
      resourceLayer.draw();
    }
  }, 2000);
}

function focusOnResource(resourceName) {
  var resource = resources[resourceName];
  if (!resource || !stage) return;

  var absPos = resource.getAbsoluteLocation();
  var padding = 60;
  var stageW = stage.width();
  var stageH = stage.height();
  var viewW = stageW - padding * 2;
  var viewH = stageH - padding * 2;
  var fitScale = Math.min(viewW / resource.size_x, viewH / resource.size_y);

  // Adaptive max zoom based on resource size (smaller resources get more zoom)
  var resourceArea = resource.size_x * resource.size_y;
  var maxScale;
  if (resourceArea > 50000) maxScale = 2;       // large (e.g. deck, carrier)
  else if (resourceArea > 5000) maxScale = 5;   // medium (e.g. plate, tiprack)
  else if (resourceArea > 500) maxScale = 10;   // small (e.g. well, tipspot)
  else maxScale = 15;                            // tiny
  fitScale = Math.min(fitScale, maxScale);

  stage.scaleX(fitScale);
  stage.scaleY(-fitScale);

  // Center the resource in the viewport
  var centerX = (stageW - resource.size_x * fitScale) / 2 - absPos.x * fitScale;
  var centerY = (stageH + resource.size_y * fitScale) / 2 + absPos.y * fitScale - stageH * fitScale;
  stage.x(centerX);
  stage.y(centerY);

  if (typeof updateScaleBar === "function") updateScaleBar();
  updateBullseyeScale();
  updateWrtBullseyeScale();
  updateTooltipScale();
  updateDeltaLinesScale();
  stage.batchDraw();

  // Also highlight the resource
  highlightResourceOnCanvas(resourceName);
}

// Expand/collapse tree nodes to a given depth
function getTreeDepthLimit() {
  var input = document.getElementById("tree-depth-input");
  if (!input) return 1;
  var val = parseInt(input.value, 10);
  return isNaN(val) || val < 0 ? 1 : val;
}

function setTreeNodeExpansion(node, depth, maxDepth, expand) {
  var children = node.querySelectorAll(":scope > .tree-node-children > .tree-node");
  var childrenContainer = node.querySelector(":scope > .tree-node-children");
  var arrow = node.querySelector(":scope > .tree-node-row > .tree-node-arrow.has-children");
  if (!childrenContainer) return;

  if (expand && depth < maxDepth) {
    childrenContainer.classList.remove("collapsed");
    if (arrow) arrow.textContent = "\u25BC";
  } else if (!expand && depth >= maxDepth) {
    childrenContainer.classList.add("collapsed");
    if (arrow) arrow.textContent = "\u25B6";
  }

  children.forEach(function (child) {
    setTreeNodeExpansion(child, depth + 1, maxDepth, expand);
  });
}

function getMaxTreeDepth(container, depth) {
  var maxD = depth;
  var nodes = container.querySelectorAll(":scope > .tree-node");
  for (var i = 0; i < nodes.length; i++) {
    var childrenDiv = nodes[i].querySelector(":scope > .tree-node-children");
    if (childrenDiv) {
      var childMax = getMaxTreeDepth(childrenDiv, depth + 1);
      if (childMax > maxD) maxD = childMax;
    }
  }
  return maxD;
}

function expandAllTreeNodes() {
  var tree = document.getElementById("resource-tree");
  if (!tree) return;
  tree.querySelectorAll(".tree-node-children.collapsed").forEach(function (el) {
    el.classList.remove("collapsed");
  });
  tree.querySelectorAll(".tree-node-arrow.has-children").forEach(function (el) {
    el.textContent = "\u25BC";
  });
  var depthInput = document.getElementById("tree-depth-input");
  if (depthInput) {
    depthInput.value = getMaxTreeDepth(tree, 0);
  }
  buildWrtDropdown();
}

function showToDepth() {
  var tree = document.getElementById("resource-tree");
  if (!tree) return;
  var maxDepth = getTreeDepthLimit();
  var roots = tree.querySelectorAll(":scope > .tree-node");
  roots.forEach(function (root) {
    setTreeNodeExpansion(root, 0, maxDepth, true);
    setTreeNodeExpansion(root, 0, maxDepth, false);
  });
  buildWrtDropdown();
}

function collapseAllTreeNodes() {
  var tree = document.getElementById("resource-tree");
  if (!tree) return;
  var maxDepth = getTreeDepthLimit();
  var roots = tree.querySelectorAll(":scope > .tree-node");
  roots.forEach(function (root) {
    setTreeNodeExpansion(root, 0, maxDepth, false);
  });
  buildWrtDropdown();
}

// Sidepanel collapse toggle, resize, expand/collapse all
window.addEventListener("load", function () {
  var expandBtn = document.getElementById("toggle-expand-btn");
  var collapseBtn = document.getElementById("collapse-all-btn");
  var treeExpanded = true;
  if (expandBtn) expandBtn.addEventListener("click", function () {
    if (treeExpanded) {
      // Collapse all: force depth 0
      var tree = document.getElementById("resource-tree");
      if (tree) {
        tree.querySelectorAll(".tree-node-children").forEach(function (el) {
          el.classList.add("collapsed");
        });
        tree.querySelectorAll(".tree-node-arrow.has-children").forEach(function (el) {
          el.textContent = "\u25B6";
        });
        buildWrtDropdown();
      }
      expandBtn.title = "Expand All";
      expandBtn.innerHTML = '<svg width="14" height="18" viewBox="0 0 14 18" fill="none" stroke="#555" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
        '<polyline points="3,7 7,3 11,7"/>' +
        '<polyline points="3,11 7,15 11,11"/>' +
        '</svg>';
    } else {
      // Expand all without updating depth input
      var tree = document.getElementById("resource-tree");
      if (tree) {
        tree.querySelectorAll(".tree-node-children.collapsed").forEach(function (el) {
          el.classList.remove("collapsed");
        });
        tree.querySelectorAll(".tree-node-arrow.has-children").forEach(function (el) {
          el.textContent = "\u25BC";
        });
        buildWrtDropdown();
      }
      expandBtn.title = "Collapse All";
      expandBtn.innerHTML = '<svg width="14" height="18" viewBox="0 0 14 18" fill="none" stroke="#555" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
        '<polyline points="3,3 7,7 11,3"/>' +
        '<polyline points="3,15 7,11 11,15"/>' +
        '</svg>';
    }
    treeExpanded = !treeExpanded;
  });
  if (collapseBtn) collapseBtn.addEventListener("click", showToDepth);

  var depthInput = document.getElementById("tree-depth-input");
  if (depthInput) {
    // depth input no longer auto-applies; user must click the button
  }

  // Left toolbar tool switching
  var cursorBtn = document.getElementById("toolbar-cursor-btn");
  var coordsBtn = document.getElementById("toolbar-coords-btn");
  var coordsPanel = document.getElementById("coords-panel");
  var gifBtn = document.getElementById("toolbar-gif-btn");
  var gifPanel = document.getElementById("gif-panel");
  function setActiveTool(tool) {
    activeTool = tool === "gif" ? "cursor" : tool;
    if (cursorBtn) cursorBtn.classList.toggle("active", tool === "cursor");
    if (coordsBtn) coordsBtn.classList.toggle("active", tool === "coords");
    if (gifBtn) gifBtn.classList.toggle("active", tool === "gif");
    if (coordsPanel) coordsPanel.style.display = tool === "coords" ? "" : "none";
    if (gifPanel) gifPanel.style.display = tool === "gif" ? "" : "none";
    clearDeltaLines();
    updateWrtHighlight();
  }
  if (cursorBtn) cursorBtn.addEventListener("click", function () { setActiveTool("cursor"); });
  if (coordsBtn) coordsBtn.addEventListener("click", function () { setActiveTool("coords"); });
  if (gifBtn) gifBtn.addEventListener("click", function () { setActiveTool("gif"); });

  // Update wrt highlight when dropdowns change
  ["coords-wrt-ref", "coords-wrt-x-ref", "coords-wrt-y-ref", "coords-wrt-z-ref"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("change", updateWrtHighlight);
  });

  // Left toolbar collapse toggle
  var leftToggle = document.getElementById("toolbar-left-toggle");
  var leftToolbar = document.getElementById("toolbar-left");
  if (leftToggle && leftToolbar) {
    leftToggle.addEventListener("click", function () {
      leftToolbar.classList.toggle("collapsed");
    });
  }

  const toggle = document.getElementById("toolbar-tree-btn");
  const searchBtn = document.getElementById("toolbar-search-btn");
  const panel = document.getElementById("sidepanel");
  const treeHeader = document.querySelector(".sidepanel-header");
  const treeView = document.getElementById("resource-tree");
  const searchView = document.getElementById("search-view");
  const searchInput = document.getElementById("search-input");

  var sidepanelWidthBeforeCollapse = null;

  function setSidepanelView(view) {
    // Ensure sidepanel is visible
    if (panel.classList.contains("collapsed")) {
      panel.classList.remove("collapsed");
      if (sidepanelWidthBeforeCollapse) {
        panel.style.width = sidepanelWidthBeforeCollapse;
      } else {
        panel.style.width = "";
      }
    }

    if (view === "tree") {
      treeHeader.style.display = "";
      treeView.style.display = "";
      searchView.style.display = "none";
      toggle.classList.add("active");
      searchBtn.classList.remove("active");
    } else if (view === "search") {
      treeHeader.style.display = "none";
      treeView.style.display = "none";
      searchView.style.display = "";
      toggle.classList.remove("active");
      searchBtn.classList.add("active");
      searchInput.focus();
    }
  }

  if (toggle && panel) {
    toggle.addEventListener("click", function () {
      if (!panel.classList.contains("collapsed") && toggle.classList.contains("active")) {
        // Already showing tree — collapse
        sidepanelWidthBeforeCollapse = panel.style.width || panel.offsetWidth + "px";
        panel.style.width = "";
        panel.classList.add("collapsed");
        toggle.classList.remove("active");
      } else {
        setSidepanelView("tree");
      }
    });
  }

  if (searchBtn && panel) {
    searchBtn.addEventListener("click", function () {
      if (!panel.classList.contains("collapsed") && searchBtn.classList.contains("active")) {
        // Already showing search — collapse
        sidepanelWidthBeforeCollapse = panel.style.width || panel.offsetWidth + "px";
        panel.style.width = "";
        panel.classList.add("collapsed");
        searchBtn.classList.remove("active");
      } else {
        setSidepanelView("search");
      }
    });
  }

  // Fuzzy search on resources
  function fuzzyMatch(query, text) {
    query = query.toLowerCase();
    text = text.toLowerCase();
    var qi = 0;
    for (var ti = 0; ti < text.length && qi < query.length; ti++) {
      if (text[ti] === query[qi]) qi++;
    }
    return qi === query.length;
  }

  function fuzzyScore(query, text) {
    query = query.toLowerCase();
    text = text.toLowerCase();
    // Prioritize: exact match > starts with > contains > fuzzy
    if (text === query) return 4;
    if (text.startsWith(query)) return 3;
    if (text.indexOf(query) !== -1) return 2;
    return 1;
  }

  // Collect all resources with a sort key matching the Workcell Tree display order.
  // Each resource gets a hierarchical key [topX, depth, slotIndex, ...] for sorting.
  function getResourcesInTreeOrder() {
    if (!rootResource) return [];
    var result = [];

    function addResource(res, sortKey) {
      result.push({ resource: res, sortKey: sortKey });
    }

    // Get top-level children (deck items) sorted by x
    var topChildren = getDisplayChildren(rootResource);
    topChildren.sort(function (a, b) {
      var ax = a.location ? a.location.x : 0;
      var bx = b.location ? b.location.x : 0;
      return ax - bx;
    });

    addResource(rootResource, [-1]); // root first

    for (var ti = 0; ti < topChildren.length; ti++) {
      var topChild = topChildren[ti];
      var topKey = [ti];
      addResource(topChild, topKey);

      // Carrier children — include sites (resource holders) for search
      var carrierChildren = getDisplayChildren(topChild);
      for (var ci = 0; ci < carrierChildren.length; ci++) {
        var entry = carrierChildren[ci];
        // Insert the site holder before its child at this slot
        if (isCarrierLike(topChild) && entry.index !== undefined) {
          var holder = topChild.children[entry.index];
          if (holder && isResourceHolder(holder)) {
            addResource(holder, topKey.concat([ci, 0]));
          }
        }
        var child = entry.resource || entry;
        if (!child || entry.empty) continue;
        addResource(child, topKey.concat([ci, 1]));

        // Leaf children (wells/tips) not shown in tree
        if (isTipRackLike(child) || isPlateLike(child) || isTubeRackLike(child)) {
          for (var li = 0; li < (child.children || []).length; li++) {
            addResource(child.children[li], topKey.concat([ci, 1, li]));
          }
        }

        // Deeper children
        var subChildren = getDisplayChildren(child);
        for (var si = 0; si < subChildren.length; si++) {
          var subEntry = subChildren[si];
          var subChild = subEntry.resource || subEntry;
          if (!subChild || subEntry.empty) continue;
          addResource(subChild, topKey.concat([ci, 1, si]));
        }
      }

      // If top-level item is itself a leaf rack (not on a carrier)
      if (isTipRackLike(topChild) || isPlateLike(topChild) || isTubeRackLike(topChild)) {
        for (var li = 0; li < (topChild.children || []).length; li++) {
          addResource(topChild.children[li], topKey.concat([li]));
        }
      }
    }

    // Sort by hierarchical key
    result.sort(function (a, b) {
      var ka = a.sortKey, kb = b.sortKey;
      for (var i = 0; i < Math.min(ka.length, kb.length); i++) {
        if (ka[i] !== kb[i]) return ka[i] - kb[i];
      }
      return ka.length - kb.length;
    });

    // Deduplicate (sites may appear for each child in the same slot)
    var seen = {};
    var deduped = [];
    for (var i = 0; i < result.length; i++) {
      var name = result[i].resource.name;
      if (!seen[name]) {
        seen[name] = true;
        deduped.push(result[i].resource);
      }
    }
    return deduped;
  }

  function performSearch(query) {
    var resultsDiv = document.getElementById("search-results");
    if (!resultsDiv) return;
    resultsDiv.innerHTML = "";

    if (!query || query.trim() === "") return;

    var includeWells = document.getElementById("search-include-wells");
    var showWells = includeWells && includeWells.checked;
    var includeTips = document.getElementById("search-include-tips");
    var showTips = includeTips && includeTips.checked;
    var includeSites = document.getElementById("search-include-sites");
    var showSites = includeSites && includeSites.checked;
    var terms = query.trim().toLowerCase().split(/\s+/);
    var allResources = getResourcesInTreeOrder();
    var matches = [];

    for (var ri = 0; ri < allResources.length; ri++) {
      var resource = allResources[ri];
      var name = resource.name;
      if (!resource) continue;
      if (!showWells && resource instanceof Container) continue;
      if (!showTips && resource instanceof TipSpot) continue;
      if (!showSites && isResourceHolder(resource)) continue;
      var allMatch = true;
      var totalScore = 0;
      for (var i = 0; i < terms.length; i++) {
        if (fuzzyMatch(terms[i], name)) {
          totalScore += fuzzyScore(terms[i], name);
        } else if (fuzzyMatch(terms[i], resource.resourceType || "")) {
          totalScore += fuzzyScore(terms[i], resource.resourceType || "");
        } else {
          allMatch = false;
          break;
        }
      }
      if (allMatch) matches.push({ resource: resource, score: totalScore, order: ri });
    }

    matches.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      return a.order - b.order;
    });

    for (var i = 0; i < matches.length; i++) {
      var res = matches[i].resource;
      var row = document.createElement("div");
      row.className = "tree-node-row";
      row.style.paddingLeft = "12px";

      var dot = document.createElement("span");
      dot.className = "tree-node-dot";
      dot.style.backgroundColor = getResourceColor(res);

      var nameSpan = document.createElement("span");
      nameSpan.className = "tree-node-name";
      nameSpan.textContent = res.name;
      nameSpan.title = res.name + " (" + (res.resourceType || "") + ")";

      var typeSpan = document.createElement("span");
      typeSpan.className = "tree-node-type";
      typeSpan.textContent = res.resourceType || "";

      row.appendChild(dot);
      row.appendChild(nameSpan);
      row.appendChild(typeSpan);

      row.addEventListener("mouseenter", (function (rName) {
        return function () { showHoverHighlight(rName); };
      })(res.name));
      row.addEventListener("mouseleave", function () {
        clearHoverHighlight();
      });
      (function (rName) {
        var searchClickTimer = null;
        row.addEventListener("click", function () {
          if (searchClickTimer) { clearTimeout(searchClickTimer); searchClickTimer = null; return; }
          searchClickTimer = setTimeout(function () {
            searchClickTimer = null;
            showUmlPanel(rName);
          }, 250);
        });
        row.addEventListener("dblclick", function () {
          if (searchClickTimer) { clearTimeout(searchClickTimer); searchClickTimer = null; }
          focusOnResource(rName);
        });
      })(res.name);

      resultsDiv.appendChild(row);
    }
  }

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      performSearch(this.value);
    });
  }

  var includeWellsCheckbox = document.getElementById("search-include-wells");
  if (includeWellsCheckbox && searchInput) {
    includeWellsCheckbox.addEventListener("change", function () {
      performSearch(searchInput.value);
    });
  }

  var includeTipsCheckbox = document.getElementById("search-include-tips");
  if (includeTipsCheckbox && searchInput) {
    includeTipsCheckbox.addEventListener("change", function () {
      performSearch(searchInput.value);
    });
  }

  var includeSitesCheckbox = document.getElementById("search-include-sites");
  if (includeSitesCheckbox && searchInput) {
    includeSitesCheckbox.addEventListener("change", function () {
      performSearch(searchInput.value);
    });
  }

  // Navbar right toggle (mirrors toolbar-tree-btn behavior)
  var rightToggle = document.getElementById("toolbar-right-toggle");
  var rightToolbar = document.getElementById("toolbar");
  var sidebarWasCollapsedBeforeInspectorClose = false;

  if (rightToggle && panel && rightToolbar) {
    rightToggle.addEventListener("click", function () {
      var toolbarHidden = rightToolbar.style.display === "none";

      if (toolbarHidden) {
        // Toolbar hidden — reopen toolbar, restore sidebar to previous state
        rightToolbar.style.display = "";
        if (!sidebarWasCollapsedBeforeInspectorClose) {
          panel.classList.remove("collapsed");
          if (sidepanelWidthBeforeCollapse) {
            panel.style.width = sidepanelWidthBeforeCollapse;
          } else {
            panel.style.width = "";
          }
          toggle.classList.add("active");
        }
      } else {
        // Toolbar visible — remember sidebar state, then collapse all
        sidebarWasCollapsedBeforeInspectorClose = panel.classList.contains("collapsed");
        if (!sidebarWasCollapsedBeforeInspectorClose) {
          sidepanelWidthBeforeCollapse = panel.style.width || panel.offsetWidth + "px";
          panel.style.width = "";
          panel.classList.add("collapsed");
        }
        rightToolbar.style.display = "none";
        toggle.classList.remove("active");
        searchBtn.classList.remove("active");
      }
    });
  }

  // Drag-to-resize handle
  const handle = document.getElementById("sidepanel-resize-handle");
  if (handle && panel) {
    let dragging = false;

    handle.addEventListener("mousedown", function (e) {
      e.preventDefault();
      dragging = true;
      handle.classList.add("dragging");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    });

    document.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      const newWidth = window.innerWidth - e.clientX;
      const clamped = Math.max(150, Math.min(newWidth, window.innerWidth * 0.6));
      panel.style.width = clamped + "px";
    });

    document.addEventListener("mouseup", function () {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    });
  }
});

// ===========================================================================
// UML-Style Resource Info Panel
// ===========================================================================

var umlPanelResourceName = null;
var _umlPanelOpenedAt = 0;

function getUmlAttributes(resource) {
  var attrs = [];
  attrs.push({ key: "name", value: JSON.stringify(resource.name) });
  attrs.push({ key: "type", value: resource.resourceType || resource.constructor.name });
  attrs.push({ key: "size_x", value: resource.size_x });
  attrs.push({ key: "size_y", value: resource.size_y });
  attrs.push({ key: "size_z", value: resource.size_z });
  if (resource.location) {
    attrs.push({ key: "location", value: "(" + resource.location.x + ", " + resource.location.y + ", " + (resource.location.z || 0) + ")" });
  }
  var abs = resource.getAbsoluteLocation();
  attrs.push({ key: "abs_location", value: "(" + abs.x.toFixed(1) + ", " + abs.y.toFixed(1) + ", " + (abs.z || 0).toFixed(1) + ")" });
  attrs.push({ key: "parent", value: resource.parent ? JSON.stringify(resource.parent.name) : "none" });
  attrs.push({ key: "children", value: resource.children ? resource.children.length : 0 });
  if (resource.category) {
    attrs.push({ key: "category", value: resource.category });
  }

  // Container (Well/Trough/Tube)
  if (resource instanceof Container) {
    attrs.push({ key: "max_volume", value: resource.maxVolume });
    attrs.push({ key: "volume", value: resource.volume });
    if (resource.material_z_thickness != null) {
      attrs.push({ key: "material_z_thickness", value: resource.material_z_thickness });
    }
  }
  // Well-specific
  if (resource instanceof Well) {
    if (resource.cross_section_type) {
      attrs.push({ key: "cross_section_type", value: resource.cross_section_type });
    }
  }
  // TipSpot
  if (resource instanceof TipSpot) {
    attrs.push({ key: "has_tip", value: resource.has_tip });
    if (resource.tip) {
      attrs.push({ key: "tip", value: JSON.stringify(resource.tip) });
    }
  }
  // Plate/TipRack/TubeRack
  if (resource instanceof Plate || resource instanceof TipRack || resource instanceof TubeRack) {
    if (resource.num_items_x != null) attrs.push({ key: "num_items_x", value: resource.num_items_x });
    if (resource.num_items_y != null) attrs.push({ key: "num_items_y", value: resource.num_items_y });
  }
  // ResourceHolder
  if (resource instanceof ResourceHolder) {
    if (resource.spot != null) attrs.push({ key: "spot", value: resource.spot });
  }
  // HamiltonSTARDeck
  if (resource instanceof HamiltonSTARDeck) {
    attrs.push({ key: "num_rails", value: resource.num_rails });
  }

  return attrs;
}

function getUmlMethods(resource) {
  if (resource.methods && resource.methods.length > 0) {
    return resource.methods;
  }
  return [];
}

function buildUmlPanelDOM(resource) {
  var panel = document.createElement("div");
  panel.className = "uml-panel";
  panel.id = "uml-panel";

  // Close button
  var closeBtn = document.createElement("button");
  closeBtn.className = "uml-close-btn";
  closeBtn.textContent = "\u00D7";
  closeBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    hideUmlPanel();
  });
  panel.appendChild(closeBtn);

  // Header
  var header = document.createElement("div");
  header.className = "uml-header";
  var nameDiv = document.createElement("div");
  nameDiv.className = "uml-header-name";
  nameDiv.textContent = resource.name;
  var typeDiv = document.createElement("div");
  typeDiv.className = "uml-header-type";
  typeDiv.textContent = "\u00AB" + (resource.resourceType || resource.constructor.name) + "\u00BB";
  header.appendChild(nameDiv);
  header.appendChild(typeDiv);
  panel.appendChild(header);

  // Separator
  var sep1 = document.createElement("div");
  sep1.className = "uml-separator";
  panel.appendChild(sep1);

  // Attributes section
  var attrsSection = document.createElement("div");
  attrsSection.className = "uml-section";
  var attrsTitle = document.createElement("div");
  attrsTitle.className = "uml-section-title";
  attrsTitle.textContent = "Attributes";
  attrsSection.appendChild(attrsTitle);

  var attrs = getUmlAttributes(resource);
  for (var i = 0; i < attrs.length; i++) {
    var row = document.createElement("div");
    row.className = "uml-row";
    var keySpan = document.createElement("span");
    keySpan.className = "uml-key";
    keySpan.textContent = attrs[i].key + ":";
    var valSpan = document.createElement("span");
    valSpan.className = "uml-value";
    valSpan.textContent = " " + attrs[i].value;
    row.appendChild(keySpan);
    row.appendChild(valSpan);
    attrsSection.appendChild(row);
  }
  panel.appendChild(attrsSection);

  // Separator
  var sep2 = document.createElement("div");
  sep2.className = "uml-separator";
  panel.appendChild(sep2);

  // Methods section
  var methodsSection = document.createElement("div");
  methodsSection.className = "uml-section";

  var methodsHeader = document.createElement("div");
  methodsHeader.style.display = "flex";
  methodsHeader.style.alignItems = "center";
  methodsHeader.style.justifyContent = "space-between";
  methodsHeader.style.cursor = "pointer";

  var methodsTitle = document.createElement("div");
  methodsTitle.className = "uml-section-title";
  methodsTitle.textContent = "Methods";
  methodsTitle.style.marginBottom = "0";

  var toggleBtn = document.createElement("button");
  toggleBtn.style.background = "none";
  toggleBtn.style.border = "none";
  toggleBtn.style.cursor = "pointer";
  toggleBtn.style.padding = "0 2px";
  toggleBtn.style.fontSize = "12px";
  toggleBtn.style.color = "#999";
  toggleBtn.style.lineHeight = "1";
  toggleBtn.innerHTML = "&#9660;";

  methodsHeader.appendChild(methodsTitle);
  methodsHeader.appendChild(toggleBtn);
  methodsSection.appendChild(methodsHeader);

  var methodsList = document.createElement("div");
  methodsList.style.display = "none";

  var methods = getUmlMethods(resource);
  for (var i = 0; i < methods.length; i++) {
    var methodDiv = document.createElement("div");
    methodDiv.className = "uml-method";
    methodDiv.textContent = methods[i];
    methodsList.appendChild(methodDiv);
  }
  methodsSection.appendChild(methodsList);

  methodsHeader.addEventListener("click", function () {
    var collapsed = methodsList.style.display === "none";
    methodsList.style.display = collapsed ? "block" : "none";
    toggleBtn.innerHTML = collapsed ? "&#9650;" : "&#9660;";
  });

  panel.appendChild(methodsSection);

  return panel;
}

function showUmlPanel(resourceName) {
  var resource = resources[resourceName];
  if (!resource) return;

  // Toggle off if clicking the same resource
  if (umlPanelResourceName === resourceName) {
    hideUmlPanel();
    return;
  }

  umlPanelResourceName = resourceName;
  _umlPanelOpenedAt = Date.now();

  // Remove existing panel
  var existing = document.getElementById("uml-panel");
  if (existing) existing.remove();

  // Build and insert the panel into <main>
  var mainEl = document.querySelector("main");
  if (!mainEl) return;
  var panelDOM = buildUmlPanelDOM(resource);
  mainEl.appendChild(panelDOM);

  // Also highlight the resource on canvas
  highlightResourceOnCanvas(resourceName);
}

function hideUmlPanel() {
  umlPanelResourceName = null;
  var existing = document.getElementById("uml-panel");
  if (existing) existing.remove();
}

// Close UML panel when clicking on empty canvas area
window.addEventListener("load", function () {
  var kanvas = document.getElementById("kanvas");
  if (kanvas) {
    kanvas.addEventListener("click", function (e) {
      // Only close if the click is directly on the kanvas container (not on a child)
      // Skip if a panel was just opened (e.g. via dblclick) to avoid race conditions
      if (e.target === kanvas || e.target.tagName === "CANVAS") {
        if (Date.now() - _umlPanelOpenedAt > 400) {
          hideUmlPanel();
        }
      }
    });
  }
});

// ===========================================================================
// Navbar Liquid Handler Machine Tool Buttons
// ===========================================================================

function makeSVG(viewBox, innerHTML) {
  var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "40");
  svg.setAttribute("height", "40");
  svg.setAttribute("viewBox", viewBox);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.innerHTML = innerHTML;
  return svg;
}

var multiChannelSVG = (function () {
  var body =
    '<g stroke-linejoin="round">' +
    // === Main body — large isometric block ===
    '<polygon points="20,0 38,7 20,14 2,7" fill="#e0e0e0" stroke="#888" stroke-width="0.7"/>' +
    '<polygon points="2,7 20,14 20,26 2,19" fill="#c8c8c8" stroke="#888" stroke-width="0.7"/>' +
    '<polygon points="38,7 20,14 20,26 38,19" fill="#b0b0b0" stroke="#888" stroke-width="0.7"/>' +
    '<line x1="20" y1="14" x2="20" y2="26" stroke="#999" stroke-width="0.6"/>' +
    // === Dark adapter plate ===
    '<polygon points="20,24 38,17 38,19 20,26 2,19 2,17" fill="#333" stroke="#222" stroke-width="0.7"/>' +
    '<polygon points="2,19 20,26 20,28 2,21" fill="#222" stroke="#111" stroke-width="0.5"/>' +
    '<polygon points="38,19 20,26 20,28 38,21" fill="#2a2a2a" stroke="#111" stroke-width="0.5"/>';

  // === Isometric tip grid ===
  // The adapter plate bottom face is a diamond:
  //   back=(20,21), left=(2,21), front=(20,28), right=(38,21)
  // Isometric axes on this face:
  //   "column" axis: left→right = from (2,21) toward (38,21) through back → direction (+2.25, -0.44)
  //   "row" axis: back→front = from back toward (20,28) → direction (-2.25, +0.44) per step
  // But we want to shift each row forward (toward viewer).
  //
  // Grid: 8 columns x 6 rows. Origin at center of adapter bottom face.
  // Column direction (along right edge): dx_c=+2.25, dy_c=-0.44
  // Row direction (toward front): dx_r=-2.25, dy_r=+0.44
  // Center of bottom face: (20, 24.5)

  var cx = 20, cy = 24.5;
  var cols = 8, rows = 6;
  var dx_c = 2.1, dy_c = -0.75;   // step per column (going right-back)
  var dx_r = -2.1, dy_r = 0.75;   // step per row (going left-front)
  var tipLen = 11;                  // tip length (straight down)

  // Collect all tips with their positions, sorted back-to-front for correct overlap
  var tips = [];
  for (var r = 0; r < rows; r++) {
    for (var c = 0; c < cols; c++) {
      var cc = c - (cols - 1) / 2;  // center columns
      var rr = r - (rows - 1) / 2;  // center rows
      var x = cx + cc * dx_c + rr * dx_r;
      var y = cy + cc * dy_c + rr * dy_r;
      // depth: back rows (low r) are far, front rows (high r) are near
      tips.push({ x: x, y: y, row: r });
    }
  }
  // Sort back-to-front (low y first = back = draw first)
  tips.sort(function (a, b) { return a.y - b.y; });

  var tipsSvg = '';
  for (var i = 0; i < tips.length; i++) {
    var t = tips[i];
    // Depth shading: back rows lighter, front rows darker
    var frac = t.row / (rows - 1); // 0=back, 1=front
    var gray = Math.round(180 - frac * 150); // 180 (light) → 30 (dark)
    var sw = 0.5 + frac * 0.5;               // 0.5 → 1.0 stroke width
    var color = 'rgb(' + gray + ',' + gray + ',' + gray + ')';
    var x1 = t.x.toFixed(1);
    var y1 = t.y.toFixed(1);
    var y2 = (t.y + tipLen).toFixed(1);
    tipsSvg += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x1 + '" y2="' + y2 +
               '" stroke="' + color + '" stroke-width="' + sw.toFixed(2) + '"/>';
  }

  return body + tipsSvg + '</g>';
})();

var singleChannelSVG =
  '<g fill="#222" stroke="none">' +
  '<rect x="4" y="0" width="12" height="3.5" rx="0.5"/>' +
  '<rect x="8" y="3.5" width="3" height="4.5"/>' +
  '<rect x="2" y="8" width="16" height="2.5" rx="0.5"/>' +
  '<rect x="5" y="10.5" width="10" height="21.5" rx="1"/>' +
  '<rect x="6.5" y="14.5" width="5" height="1.8" rx="0.3" fill="#fff"/>' +
  '<rect x="6.5" y="18" width="5" height="1.8" rx="0.3" fill="#fff"/>' +
  '<rect x="6.5" y="21.5" width="5" height="1.8" rx="0.3" fill="#fff"/>' +
  '<rect x="6.5" y="25" width="5" height="1.8" rx="0.3" fill="#fff"/>' +
  '<path d="M7 32 L13 32 L11 36 L9 36 Z"/>' +
  '<rect x="9" y="36" width="2" height="10" rx="0.5"/>' +
  '</g>';

var integratedArmSVG =
  '<g stroke-linejoin="round">' +
  // --- Dark vertical column (isometric cylinder) ---
  // Column left face
  '<polygon points="11,2 15,0 15,14 11,16" fill="#2a2a2a" stroke="#111" stroke-width="0.6"/>' +
  // Column right face
  '<polygon points="15,0 21,3 21,17 15,14" fill="#444" stroke="#111" stroke-width="0.6"/>' +
  // Column top ellipse
  '<polygon points="11,2 15,0 21,3 17,5" fill="#555" stroke="#222" stroke-width="0.6"/>' +
  // Highlight strip
  '<polygon points="14,1 16,0 16,14 14,15" fill="#666" stroke="none" opacity="0.5"/>' +
  // --- White carriage plate (isometric box) ---
  // Plate top face
  '<polygon points="4,16 16,11 28,16 16,21" fill="#ddd" stroke="#888" stroke-width="0.6"/>' +
  // Plate front-left face
  '<polygon points="4,16 16,21 16,24 4,19" fill="#c0c0c0" stroke="#888" stroke-width="0.6"/>' +
  // Plate front-right face
  '<polygon points="16,21 28,16 28,19 16,24" fill="#aaa" stroke="#888" stroke-width="0.6"/>' +
  // --- Back arm (upper rail, extends front-right) ---
  // Back arm top
  '<polygon points="16,17 32,24 34,23 18,16" fill="#aaa" stroke="#777" stroke-width="0.5"/>' +
  // Back arm front face
  '<polygon points="16,17 32,24 32,26.5 16,19.5" fill="#909090" stroke="#777" stroke-width="0.5"/>' +
  // Back arm right end
  '<polygon points="32,24 34,23 34,25.5 32,26.5" fill="#808080" stroke="#666" stroke-width="0.5"/>' +
  // --- Front arm (lower rail, extends front-right) ---
  // Front arm top
  '<polygon points="10,20 26,27 28,26 12,19" fill="#aaa" stroke="#777" stroke-width="0.5"/>' +
  // Front arm front face
  '<polygon points="10,20 26,27 26,29.5 10,22.5" fill="#909090" stroke="#777" stroke-width="0.5"/>' +
  // Front arm right end
  '<polygon points="26,27 28,26 28,28.5 26,29.5" fill="#808080" stroke="#666" stroke-width="0.5"/>' +
  // --- Back gripper (at end of back arm) ---
  // Back crossbar top
  '<polygon points="32,24 34,23 38,25 36,26" fill="#2a2a2a" stroke="#111" stroke-width="0.5"/>' +
  // Back crossbar front
  '<polygon points="32,24 36,26 36,28 32,26" fill="#1a1a1a" stroke="#111" stroke-width="0.5"/>' +
  // Back left finger
  '<polygon points="32,26 33.5,25.3 33.5,33 32,33.7" fill="#222" stroke="#111" stroke-width="0.4"/>' +
  // Back right finger
  '<polygon points="35,27.3 36.5,26.5 36.5,34.5 35,35.3" fill="#222" stroke="#111" stroke-width="0.4"/>' +
  // --- Front gripper (at end of front arm) ---
  // Front crossbar top
  '<polygon points="26,27 28,26 32,28 30,29" fill="#1a1a1a" stroke="#000" stroke-width="0.5"/>' +
  // Front crossbar front
  '<polygon points="26,27 30,29 30,31 26,29" fill="#111" stroke="#000" stroke-width="0.5"/>' +
  // Front left finger
  '<polygon points="26,29 27.5,28.3 27.5,37 26,37.7" fill="#1a1a1a" stroke="#000" stroke-width="0.4"/>' +
  // Front right finger
  '<polygon points="29,30.3 30.5,29.5 30.5,38.5 29,39.3" fill="#1a1a1a" stroke="#000" stroke-width="0.4"/>' +
  '</g>';

function buildNavbarLHMachineTools() {
  var container = document.getElementById("navbar-lh-machine-tools");
  if (!container) return;
  container.innerHTML = "";

  // Find all LiquidHandler resources
  var lhNames = [];
  for (var name in resources) {
    if (resources[name] instanceof LiquidHandler) {
      lhNames.push(name);
    }
  }

  for (var i = 0; i < lhNames.length; i++) {
    var lhName = lhNames[i];
    var group = document.createElement("div");
    group.className = "navbar-pipette-group";

    // Label (styled as button without changing appearance)
    var label = document.createElement("button");
    label.className = "navbar-pipette-label";
    label.title = "Show/hide liquid handler machine tools";
    label.textContent = "";
    label.appendChild(document.createTextNode(lhName));
    label.appendChild(document.createElement("br"));
    label.appendChild(document.createTextNode("Machine Tools"));
    group.appendChild(label);

    // Collapsible container for machine tool buttons
    var machineToolBtns = document.createElement("div");
    machineToolBtns.className = "navbar-machine-tool-btns";
    group.appendChild(machineToolBtns);

    // Toggle machine tool buttons on label click
    label.addEventListener("click", function () {
      var collapsed = machineToolBtns.classList.toggle("collapsed");
      label.classList.toggle("collapsed", collapsed);
      // Close any open dropdowns when collapsing
      if (collapsed) {
        var dropdowns = document.querySelectorAll(".machine-tool-dropdown.open");
        dropdowns.forEach(function (d) { d.classList.remove("open"); });
        group.querySelectorAll(".navbar-pipette-btn.active").forEach(function (b) { b.classList.remove("active"); });
      }
    });

    // Multi-channel button (hidden unless setState has already confirmed machine tool exists)
    var lhRes = resources[lhName];
    var multiBtn = document.createElement("button");
    multiBtn.className = "navbar-pipette-btn";
    multiBtn.id = "multi-channel-btn-" + lhName;
    multiBtn.style.display = (lhRes && lhRes.head96State !== null && lhRes.head96State !== undefined) ? "" : "none";
    multiBtn.title = "Multi-Channel Pipettes";
    var multiImg = document.createElement("img");
    multiImg.src = "img/multi_channel_pipette.png";
    multiImg.alt = "Multi-Channel Pipettes";
    multiImg.style.width = "44px";
    multiImg.style.height = "44px";
    multiImg.style.objectFit = "contain";
    multiBtn.appendChild(multiImg);
    machineToolBtns.appendChild(multiBtn);

    // Single-channel button
    var singleBtn = document.createElement("button");
    singleBtn.className = "navbar-pipette-btn";
    singleBtn.id = "single-channel-btn-" + lhName;
    singleBtn.title = "Single-Channel Pipettes";
    var singleImg = document.createElement("img");
    singleImg.src = "img/single_channel_pipette.png";
    singleImg.alt = "Single-Channel Pipettes";
    singleImg.style.width = "44px";
    singleImg.style.height = "44px";
    singleImg.style.objectFit = "contain";
    singleBtn.appendChild(singleImg);
    machineToolBtns.appendChild(singleBtn);

    // Helper: position both panels based on the single-channel button position
    function positionPanels(handlerName, singleBtnRef) {
      var mainEl = document.querySelector("main");
      if (!mainEl) return;
      var mainRect = mainEl.getBoundingClientRect();
      var btnRect = singleBtnRef.getBoundingClientRect();
      var topPx = (btnRect.bottom - mainRect.top + 20);
      var singleCenterPx = (btnRect.left - mainRect.left + btnRect.width / 2);

      var singlePanel = document.getElementById("single-channel-dropdown-" + handlerName);

      // Measure single panel (temporarily show if hidden)
      var singleW = 0, singleH = 0, singleLeft = singleCenterPx;
      if (singlePanel) {
        // Temporarily set left + transform so we can measure offsetWidth accurately
        singlePanel.style.top = topPx + "px";
        singlePanel.style.left = singleCenterPx + "px";
        var wasHidden = !singlePanel.classList.contains("open");
        if (wasHidden) { singlePanel.style.visibility = "hidden"; singlePanel.classList.add("open"); }
        singleW = singlePanel.offsetWidth;
        singleH = singlePanel.offsetHeight;
        singleLeft = singleCenterPx - singleW / 2;
        if (wasHidden) { singlePanel.classList.remove("open"); singlePanel.style.visibility = ""; }
      }

      // Determine multi-channel and arm panel widths for clamping
      var multiPanel = document.getElementById("multi-channel-dropdown-" + handlerName);
      var armPanel = document.getElementById("arm-dropdown-" + handlerName);
      var multiW = 0;
      if (multiPanel && multiPanel.classList.contains("open")) {
        multiPanel.style.transform = "none";
        multiW = multiPanel.offsetWidth;
      }
      var armW = 0;
      if (armPanel && armPanel.classList.contains("open")) {
        armPanel.style.transform = "none";
        armW = armPanel.offsetWidth;
      }

      // Clamp: ensure the leftmost panel doesn't go below 0
      var totalLeftEdge = singleLeft - (multiW > 0 ? multiW + 8 : 0);
      if (totalLeftEdge < 0) {
        singleLeft = singleLeft + (-totalLeftEdge);
      }

      // Always position single panel with direct left (no CSS transform),
      // because html2canvas misrenders translateX(-50%).
      if (singlePanel) {
        singlePanel.style.transform = "none";
        singlePanel.style.left = Math.max(0, singleLeft) + "px";
      }

      if (multiPanel && multiPanel.classList.contains("open")) {
        multiPanel.style.top = topPx + "px";
        multiPanel.style.height = singleH > 0 ? singleH + "px" : "auto";
        multiPanel.style.left = Math.max(0, singleLeft - multiW - 8) + "px";
      }

      if (armPanel && armPanel.classList.contains("open")) {
        armPanel.style.top = topPx + "px";
        armPanel.style.height = singleH > 0 ? singleH + "px" : "auto";
        var singleRight = singleLeft + singleW;
        armPanel.style.left = (singleRight + 8) + "px";
      }
    }

    // Single-channel dropdown panel
    (function (btn, handlerName) {
      var panelId = "single-channel-dropdown-" + handlerName;
      btn.addEventListener("click", function () {
        var existing = document.getElementById(panelId);
        if (existing) {
          var isOpen = existing.classList.toggle("open");
          btn.classList.toggle("active", isOpen);
          positionPanels(handlerName, btn);
          return;
        }
        var mainEl = document.querySelector("main");
        if (!mainEl) return;
        var panel = document.createElement("div");
        panel.className = "machine-tool-dropdown open";
        panel.id = panelId;
        var lhResource = resources[handlerName];
        var headState = (lhResource && lhResource.headState) ? lhResource.headState : {};
        fillHeadIcons(panel, headState);
        mainEl.appendChild(panel);
        btn.classList.add("active");
        positionPanels(handlerName, btn);
      });
    })(singleBtn, lhName);

    // Multi-channel dropdown panel
    (function (btn, handlerName, singleBtnRef) {
      var panelId = "multi-channel-dropdown-" + handlerName;
      btn.addEventListener("click", function () {
        var existing = document.getElementById(panelId);
        if (existing) {
          var isOpen = existing.classList.toggle("open");
          btn.classList.toggle("active", isOpen);
          if (isOpen) positionPanels(handlerName, singleBtnRef);
          return;
        }
        var mainEl = document.querySelector("main");
        if (!mainEl) return;
        var panel = document.createElement("div");
        panel.className = "machine-tool-dropdown multi-channel open";
        panel.id = panelId;
        var lhResource = resources[handlerName];
        var head96State = (lhResource && lhResource.head96State) ? lhResource.head96State : {};
        fillHead96Grid(panel, head96State);
        mainEl.appendChild(panel);
        positionPanels(handlerName, singleBtnRef);
        btn.classList.add("active");
      });
    })(multiBtn, lhName, singleBtn);

    // Integrated arm button (hidden unless setState has already confirmed machine tool exists)
    var armBtn = document.createElement("button");
    armBtn.className = "navbar-pipette-btn";
    armBtn.id = "arm-btn-" + lhName;
    armBtn.style.display = (lhRes && lhRes.armState !== null && lhRes.armState !== undefined) ? "" : "none";
    armBtn.title = "Integrated Arms";
    var armImg = document.createElement("img");
    armImg.src = "img/integrated_arm.png";
    armImg.alt = "Integrated Arms";
    armImg.style.width = "44px";
    armImg.style.height = "44px";
    armImg.style.objectFit = "contain";
    armBtn.appendChild(armImg);
    machineToolBtns.appendChild(armBtn);

    // Arm dropdown panel
    (function (btn, handlerName, singleBtnRef) {
      var panelId = "arm-dropdown-" + handlerName;
      btn.addEventListener("click", function () {
        var existing = document.getElementById(panelId);
        if (existing) {
          var isOpen = existing.classList.toggle("open");
          btn.classList.toggle("active", isOpen);
          if (isOpen) positionPanels(handlerName, singleBtnRef);
          return;
        }
        var mainEl = document.querySelector("main");
        if (!mainEl) return;
        var panel = document.createElement("div");
        panel.className = "machine-tool-dropdown arm open";
        panel.id = panelId;
        var lhResource = resources[handlerName];
        var armState = (lhResource && lhResource.armState) ? lhResource.armState : {};
        fillArmPanel(panel, armState);
        mainEl.appendChild(panel);
        positionPanels(handlerName, singleBtnRef);
        btn.classList.add("active");
      });
    })(armBtn, lhName, singleBtn);

    container.appendChild(group);
  }
}

/**
 * Programmatically open all visible machine tool panels (single-channel, multi-channel, arm)
 * for every LiquidHandler. Buttons that are hidden (machine tool absent) are skipped.
 */
function openAllMachineToolPanels() {
  for (var name in resources) {
    if (!(resources[name] instanceof LiquidHandler)) continue;
    var btns = [
      document.getElementById("single-channel-btn-" + name),
      document.getElementById("multi-channel-btn-" + name),
      document.getElementById("arm-btn-" + name),
    ];
    for (var i = 0; i < btns.length; i++) {
      var btn = btns[i];
      if (btn && btn.style.display !== "none") {
        btn.click();
      }
    }
  }
}
