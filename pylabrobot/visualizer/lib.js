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

function updateCoordsPanel(resource) {
  // No-op; dropdown is built once via buildWrtDropdown.
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
  var zNA = false;
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
  // If wrt is the sidebar root, return absolute location.
  if (sidebarRootResource && wrtName === sidebarRootResource.name) {
    return resource.getAbsoluteLocation();
  }
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

var homeView = null; // saved initial view {x, y, scaleX, scaleY}
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
}

let trash;

let gif;

let resourceImage;

// Used in gif generation
let isRecording = false;
let recordingCounter = 0; // Counter to track the number of recorded frames
var frameImages = [];
let frameInterval = 8;

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
          y: y + this.size_y / 2,
          opacity: 0.75,
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
            fontSize: activeTool === "coords" ? 14 : 18,
            padding: 5,
            fill: "white",
          })
        );
        tooltip.scaleY(-1);
        layer.add(tooltip);
        if (typeof highlightSidebarRow === "function") {
          highlightSidebarRow(this.name);
        }
        if (activeTool === "coords") {
          updateCoordsPanel(this);
        }
      });
      this.mainShape.on("mouseout", () => {
        tooltip.destroy();
        if (typeof clearSidebarHighlight === "function") {
          clearSidebarHighlight();
        }
        if (activeTool === "coords") {
          updateCoordsPanel(null);
        }
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

    if (isRecording) {
      if (recordingCounter % frameInterval == 0) {
        stageToBlob(stage, handleBlob);
      }
      recordingCounter += 1;
    }
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
      if ((i + 1) % 5 === 0) {
        const railLabel = new Konva.Text({
          x: 100 + i * 22.5, // 22.5 mm per rail
          y: 50,
          text: i + 1,
          fontSize: 12,
          fill: "black",
        });
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

      if ((i + 1) % 5 === 0) {
        const railLabel = new Konva.Text({
          x: railX,
          y: 50,
          text: i + 1,
          fontSize: 12,
          fill: "black",
        });
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

function fillHeadIcons(panel, headState) {
  panel.innerHTML = "";
  panel.style.display = "flex";
  panel.style.flexWrap = "wrap";
  panel.style.gap = "6px";
  panel.style.alignItems = "flex-start";
  var channels = Object.keys(headState).sort(function (a, b) { return +a - +b; });
  for (var ci = 0; ci < channels.length; ci++) {
    var ch = channels[ci];
    var tipData = headState[ch] && headState[ch].tip;
    var hasTip = tipData !== null && tipData !== undefined;
    // Scale tip length: total_tip_length in mm, map to px (0.4 px/mm, min 10, max 40)
    var tipLenPx = 0;
    if (hasTip && tipData.total_tip_length) {
      tipLenPx = Math.max(10, Math.min(40, tipData.total_tip_length * 0.4));
    }
    var col = document.createElement("div");
    col.style.display = "flex";
    col.style.flexDirection = "column";
    col.style.alignItems = "center";
    var label = document.createElement("span");
    label.textContent = ch;
    label.style.fontSize = "15px";
    label.style.fontWeight = "700";
    label.style.color = "#888";
    label.style.marginBottom = "2px";
    col.appendChild(label);
    // Base pipette is 27px; tip adds a straight section + taper below
    var svgH = hasTip ? 27 + tipLenPx : 27;
    var icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("width", "14");
    icon.setAttribute("height", String(svgH));
    icon.setAttribute("viewBox", "0 0 14 " + svgH);
    // Black cylinder (top): 14px wide, 20px tall with rounded ends
    var shapes =
      '<rect x="0" y="1" width="14" height="18" rx="3" ry="3" fill="#333"/>' +
      '<ellipse cx="7" cy="2" rx="7" ry="2" fill="#555"/>' +
      '<ellipse cx="7" cy="19" rx="7" ry="2" fill="#222"/>' +
      // Silver cylinder (bottom): 10px wide, 5px tall, centered
      '<rect x="2" y="20" width="10" height="4" rx="2" ry="2" fill="#b0b0b0"/>' +
      '<ellipse cx="7" cy="20" rx="5" ry="1.5" fill="#ccc"/>' +
      '<ellipse cx="7" cy="24" rx="5" ry="1.5" fill="#999"/>';
    if (hasTip) {
      // Tip: straight section (40% of length) then taper to point (60%)
      var straightH = Math.round(tipLenPx * 0.4);
      var taperH = tipLenPx - straightH;
      var straightEnd = 25 + straightH;
      var tipEnd = straightEnd + taperH;
      shapes +=
        '<rect x="3" y="25" width="8" height="' + straightH + '" rx="1" ry="1" fill="#e8e8e8" stroke="#bbb" stroke-width="0.5"/>' +
        '<polygon points="3,' + straightEnd + ' 11,' + straightEnd + ' 7,' + tipEnd + '" fill="#e8e8e8" stroke="#bbb" stroke-width="0.5"/>';
    }
    icon.innerHTML = shapes;
    col.appendChild(icon);
    panel.appendChild(col);
  }
}

class LiquidHandler extends Resource {
  constructor(resource) {
    super(resource);
    this.numHeads = 0;
    this.headState = {};
  }

  drawMainShape() {
    return undefined; // just draw the children (deck and so on)
  }

  setState(state) {
    if (state.head_state) {
      this.headState = state.head_state;
      this.numHeads = Object.keys(state.head_state).length;
      // Update dropdown panel if it exists
      var panel = document.getElementById("single-channel-dropdown-" + this.name);
      if (panel) {
        fillHeadIcons(panel, this.headState);
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
    const clampedScale = Math.max(0.1, Math.min(10, newScale));

    stage.scaleX(clampedScale);
    stage.scaleY(-clampedScale); // keep Y flipped

    const newPos = {
      x: pointer.x - mousePointTo.x * clampedScale,
      y: pointer.y - mousePointTo.y * (-clampedScale),
    };
    stage.position(newPos);
    updateScaleBar();
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

  stageToBlob(stage, handleBlob);

  gifResetUI();
  gifShowRecordingUI();
}

function stopRecording() {
  gifResetUI();
  gifShowProcessingUI();

  // Turn recording off
  isRecording = false;

  // Render the final image
  // Do it twice bc it looks better

  stageToBlob(stage, handleBlob);
  stageToBlob(stage, handleBlob);

  gif = new GIF({
    workers: 10,
    workerScript: "gif.worker.js",
    background: "#FFFFFF",
    width: stage.width(),
    height: stage.height(),
  });

  // Add each frame to the GIF
  for (var i = 0; i < frameImages.length; i++) {
    gif.addFrame(frameImages[i], { delay: 1 });
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
}

// convert stage to a blob and handle the blob
function stageToBlob(stage, callback) {
  stage.toBlob({
    callback: callback,
    mimeType: "image/jpg",
    quality: 0.3,
  });
}

// handle the blob (e.g., create an Image element and add it to frameImages)
function handleBlob(blob) {
  const url = URL.createObjectURL(blob);
  const myImg = new Image();

  myImg.src = url;
  myImg.width = stage.width();
  myImg.height = stage.height();

  frameImages.push(myImg);

  myImg.onload = function () {
    URL.revokeObjectURL(url); // Free up memory
  };
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
      "Frame Save Interval: " + value;

    frameInterval = value;
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

function buildResourceTree(rootResource) {
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
  buildNavbarLHModules();
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
  var expandBtn = document.getElementById("expand-all-btn");
  var collapseBtn = document.getElementById("collapse-all-btn");
  if (expandBtn) expandBtn.addEventListener("click", expandAllTreeNodes);
  if (collapseBtn) collapseBtn.addEventListener("click", collapseAllTreeNodes);

  var depthInput = document.getElementById("tree-depth-input");
  if (depthInput) {
    depthInput.addEventListener("input", function () {
      collapseAllTreeNodes();
    });
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
    if (tool !== "coords") updateCoordsPanel(null);
  }
  if (cursorBtn) cursorBtn.addEventListener("click", function () { setActiveTool("cursor"); });
  if (coordsBtn) coordsBtn.addEventListener("click", function () { setActiveTool("coords"); });
  if (gifBtn) gifBtn.addEventListener("click", function () { setActiveTool("gif"); });

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
  var methods = ["getAbsoluteLocation()", "serialize()", "draw()", "destroy()"];
  if (resource instanceof Container) {
    methods.push("getVolume()");
    methods.push("setVolume()");
    methods.push("setState()");
  }
  if (resource instanceof TipSpot) {
    methods.push("setState()");
  }
  if (resource instanceof Plate || resource instanceof TipRack || resource instanceof TubeRack) {
    methods.push("update()");
  }
  return methods;
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
  var methodsTitle = document.createElement("div");
  methodsTitle.className = "uml-section-title";
  methodsTitle.textContent = "Methods";
  methodsSection.appendChild(methodsTitle);

  var methods = getUmlMethods(resource);
  for (var i = 0; i < methods.length; i++) {
    var methodDiv = document.createElement("div");
    methodDiv.className = "uml-method";
    methodDiv.textContent = methods[i];
    methodsSection.appendChild(methodDiv);
  }
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
      if (e.target === kanvas || e.target.tagName === "CANVAS") {
        hideUmlPanel();
      }
    });
  }
});

// ===========================================================================
// Navbar Liquid Handler Module Buttons
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

function buildNavbarLHModules() {
  var container = document.getElementById("navbar-lh-modules");
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

    // Label
    var label = document.createElement("span");
    label.className = "navbar-pipette-label";
    label.textContent = lhName;
    group.appendChild(label);

    // Multi-channel button
    var multiBtn = document.createElement("button");
    multiBtn.className = "navbar-pipette-btn";
    multiBtn.title = "Multi-Channel Pipettes";
    var multiImg = document.createElement("img");
    multiImg.src = "img/multi_channel_pipette.png";
    multiImg.alt = "Multi-Channel Pipettes";
    multiImg.style.width = "44px";
    multiImg.style.height = "44px";
    multiImg.style.objectFit = "contain";
    multiBtn.appendChild(multiImg);
    group.appendChild(multiBtn);

    // Single-channel button
    var singleBtn = document.createElement("button");
    singleBtn.className = "navbar-pipette-btn";
    singleBtn.title = "Single-Channel Pipettes";
    var singleImg = document.createElement("img");
    singleImg.src = "img/single_channel_pipette.png";
    singleImg.alt = "Single-Channel Pipettes";
    singleImg.style.width = "44px";
    singleImg.style.height = "44px";
    singleImg.style.objectFit = "contain";
    singleBtn.appendChild(singleImg);
    group.appendChild(singleBtn);

    // Single-channel dropdown panel
    (function (btn, handlerName) {
      var panelId = "single-channel-dropdown-" + handlerName;
      btn.addEventListener("click", function () {
        var existing = document.getElementById(panelId);
        if (existing) {
          // Toggle
          var isOpen = existing.classList.toggle("open");
          btn.classList.toggle("active", isOpen);
          return;
        }
        // Create the dropdown panel inside <main>
        var mainEl = document.querySelector("main");
        if (!mainEl) return;
        var panel = document.createElement("div");
        panel.className = "module-dropdown open";
        panel.id = panelId;
        // Position below the button
        var btnRect = btn.getBoundingClientRect();
        var mainRect = mainEl.getBoundingClientRect();
        panel.style.top = (btnRect.bottom - mainRect.top + 20) + "px";
        panel.style.left = (btnRect.left - mainRect.left + btnRect.width / 2) + "px";
        // Show head icons from LiquidHandler state
        var lhResource = resources[handlerName];
        var headState = (lhResource && lhResource.headState) ? lhResource.headState : {};
        fillHeadIcons(panel, headState);
        mainEl.appendChild(panel);
        btn.classList.add("active");
      });
    })(singleBtn, lhName);

    // Integrated arm button
    var armBtn = document.createElement("button");
    armBtn.className = "navbar-pipette-btn";
    armBtn.title = "Integrated Arm";
    var armImg = document.createElement("img");
    armImg.src = "img/integrated_arm.png";
    armImg.alt = "Integrated Arm";
    armImg.style.width = "44px";
    armImg.style.height = "44px";
    armImg.style.objectFit = "contain";
    armBtn.appendChild(armImg);
    group.appendChild(armBtn);

    container.appendChild(group);
  }
}
