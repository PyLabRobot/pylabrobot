var mode;
const MODE_VISUALIZER = "visualizer";
const MODE_GUI = "gui";

var layer = new Konva.Layer();
var resourceLayer = new Konva.Layer();
var tooltip;
var stage;
var selectedResource;

var canvasWidth, canvasHeight;

const robotWidthMM = 100 + 30 * 22.5; // mm, just the deck
const robotHeightMM = 653.5; // mm
var scaleX, scaleY;

const numRails = 30;

var resources = {}; // name -> Resource object

let trash;

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

  // Check if the resource is in a CarrierSite.
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
  if (deck.constructor.name === "HamiltonSTARDeck") {
    // TODO: vantage
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

    // Check if the resource is on a Hamilton deck rail. (100 + 22.5 * i)
    for (let rail = 0; rail < deck.num_rails; rail++) {
      const railX = 100 + 22.5 * rail;
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

    this.color = "#5B6D8F";

    this.children = [];
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      const childClass = classForResourceType(child.type);
      const childInstance = new childClass(child, this);
      this.assignChild(childInstance);

      // Save in global lookup
      resources[child.name] = childInstance;
    }
  }

  draggable = mode === MODE_GUI;
  canDelete = mode === MODE_GUI;

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
            text: this.tooltipLabel(),
            fontFamily: "Arial",
            fontSize: 18,
            padding: 5,
            fill: "white",
          })
        );
        tooltip.scaleY(-1);
        layer.add(tooltip);
      });
      this.mainShape.on("mouseout", () => {
        tooltip.destroy();
      });
    }
  }

  drawMainShape() {
    return new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: this.color,
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
    for (let i = 0; i < numRails; i++) {
      const rail = new Konva.Line({
        points: [
          100 + i * 22.5, // 22.5 mm per rail
          63,
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
        no_trash: true,
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
        no_trash: true,
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
      fill: "#2B2D42",
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
    const { max_volume } = resourceData;
    this.maxVolume = max_volume;
    this.liquids = resourceData.liquids || [];
  }

  static colorForVolume(volume, maxVolume) {
    return `rgba(239, 35, 60, ${volume / maxVolume})`;
  }

  getVolume() {
    return this.liquids.reduce((acc, liquid) => acc + liquid.volume, 0);
  }

  aspirate(volume) {
    if (volume > this.getVolume()) {
      throw new Error(
        `Aspirating ${volume}uL from well ${
          this.name
        } with ${this.getVolume()}uL`
      );
    }

    // Remove liquids top down until we have removed the desired volume.
    let volumeToRemove = volume;
    for (let i = this.liquids.length - 1; i >= 0; i--) {
      const liquid = this.liquids[i];
      if (volumeToRemove >= liquid.volume) {
        volumeToRemove -= liquid.volume;
        this.liquids.splice(i, 1);
      } else {
        liquid.volume -= volumeToRemove;
        volumeToRemove = 0;
      }
    }

    this.update();
  }

  addLiquid(liquid) {
    this.liquids.push(liquid);
    this.update();
  }

  setLiquids(liquids) {
    this.liquids = liquids;
    this.update();
  }

  setState(state) {
    let liquids = [];
    for (let i = 0; i < state.liquids.length; i++) {
      const liquid = state.liquids[i];
      liquids.push({
        name: liquid[0],
        volume: liquid[1],
      });
    }
    this.setLiquids(liquids);
  }

  dispense(volume) {
    if (volume + this.volume > this.maxVolume) {
      throw new Error(
        `Adding ${volume}uL to well ${this.name} with ${this.volume}uL would exceed max volume of ${this.maxVolume}uL`
      );
    }

    this.addLiquid({
      volume: volume,
      name: "Unknown liquid", // TODO: get liquid name from parameter?
    });
  }

  serializeState() {
    return {
      liquids: this.liquids,
      pending_liquids: this.liquids,
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

class Trough extends Container {
  drawMainShape() {
    let mainShape = new Konva.Group();

    let background = new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: "white",
      stroke: "black",
      strokeWidth: 1,
    });

    let liquidLayer = new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: Trough.colorForVolume(this.getVolume(), this.maxVolume),
      stroke: "black",
      strokeWidth: 1,
    });

    mainShape.add(background);
    mainShape.add(liquidLayer);
    return mainShape;
  }
}

class Well extends Container {
  draggable = false;
  canDelete = false;

  constructor(resourceData, parent) {
    super(resourceData, parent);
    const { cross_section_type } = resourceData;
    this.cross_section_type = cross_section_type;
  }

  drawMainShape() {
    if (this.cross_section_type === "circle") {
      return new Konva.Circle({
        radius: this.size_x / 2,
        fill: Well.colorForVolume(this.getVolume(), this.maxVolume),
        stroke: "black",
        strokeWidth: 1,
        offsetX: -this.size_x / 2,
        offsetY: -this.size_y / 2,
      });
    } else {
      return new Konva.Rect({
        width: this.size_x,
        height: this.size_y,
        fill: Well.colorForVolume(this.getVolume(), this.maxVolume),
        stroke: "black",
        strokeWidth: 1,
      });
    }
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
      fill: "#2B2D42",
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

  draggable = false;
  canDelete = false;

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

  setTip(has_tip, layer) {
    this.has_tip = has_tip;
    this.draw(layer);
  }

  pickUpTip(layer) {
    if (!this.has_tip) {
      throw new Error("No tip to pick up");
    }
    this.setTip(false, layer);
  }

  dropTip(layer) {
    if (this.has_tip) {
      throw new Error("Already has tip");
    }
    this.setTip(true, layer);
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

// Nothing special.
class Trash extends Resource {
  dropTip(layer) {} // just ignore

  drawMainShape() {
    if (resources["deck"].constructor.name) {
      return undefined;
    }
    return super.drawMainShape();
  }
}

// Nothing special.
class Carrier extends Resource {}
class PlateCarrier extends Carrier {}
class TipCarrier extends Carrier {}

class CarrierSite extends Resource {
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

class LiquidHandler extends Resource {
  drawMainShape() {
    return undefined; // just draw the children (deck and so on)
  }
}

function classForResourceType(type) {
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
    case "CarrierSite":
      return CarrierSite;
    case "Carrier":
      return Carrier;
    case "PlateCarrier":
      return PlateCarrier;
    case "TipCarrier":
      return TipCarrier;
    case "Container":
      return Container;
    case "Trough":
      return Trough;
    case "VantageDeck":
      alert(
        "VantageDeck is not completely implemented yet: the trash and plate loader are not drawn"
      );
      return HamiltonSTARDeck;
    case "LiquidHandler":
      return LiquidHandler;
    default:
      return Resource;
  }
}

function loadResource(resourceData) {
  const resourceClass = classForResourceType(resourceData.type);

  const parentName = resourceData.parent_name;
  var parent = undefined;
  if (parentName !== undefined) {
    parent = resources[parentName];
  }

  const resource = new resourceClass(resourceData, parent);
  resources[resource.name] = resource;

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

  // limit draggable area to size of canvas
  stage.dragBoundFunc(function (pos) {
    // Set the bounds of the draggable area to 1/2 off the canvas.
    let minX = -(1 / 2) * canvasWidth;
    let minY = -(1 / 2) * canvasHeight;
    let maxX = (1 / 2) * canvasWidth;
    let maxY = (1 / 2) * canvasHeight;

    let newX = Math.max(minX, Math.min(maxX, pos.x));
    let newY = Math.max(minY, Math.min(maxY, pos.y));

    return {
      x: newX,
      y: newY,
    };
  });

  // add the layer to the stage
  stage.add(layer);
  stage.add(resourceLayer);

  // Check if there is an after stage setup callback, and if so, call it.
  if (typeof afterStageSetup === "function") {
    afterStageSetup();
  }
});
