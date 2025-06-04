// ===========================================================================
// Global Color Map (edit this to try new combinations)
// ===========================================================================
const RESOURCE_COLORS = {
  Resource: "#BDB163",
  HamiltonSTARDeck: "#F5FAFC",
  Carrier: "#5C6C8F",
  TipCarrier: "#756793",
  Plate: "#3A3A3A",
  Well: "#F5FAFC",
  TipRack: "#2B2D42",
  TubeRack: "#122D42",
  ResourceHolder: "#8D99AE",
  PlateHolder: "#5B6277",
};

// ===========================================================================
// Mode and Layers (unchanged)
// ===========================================================================

var mode;
const MODE_VISUALIZER = "visualizer";
const MODE_GUI = "gui";

let layer = new Konva.Layer();
let resourceLayer = new Konva.Layer();
let tooltip;
let stage;
let selectedResource;

let canvasWidth, canvasHeight;

const robotWidthMM = 100 + 30 * 22.5; // mm, just the deck
const robotHeightMM = 653.5; // mm
let scaleX, scaleY;

let resources = {}; // name -> Resource instance

let trash;
let gif;

// Used in GIF generation
let isRecording = false;
let recordingCounter = 0;
let frameImages = [];
let frameInterval = 8;

// ===========================================================================
// Snapping Helpers (unchanged)
// ===========================================================================

function getSnappingResourceAndLocationAndSnappingBox(resourceToSnap, x, y) {
  if (!snappingEnabled) return undefined;

  // Check Trash
  if (
    trash &&
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

  const deck = resources["deck"];
  if (!deck) return undefined;

  // Check ResourceHolder children (PlateCarrier / TipCarrier)
  for (let child of deck.children) {
    const isPlateCarrier =
      resourceToSnap.constructor.name === "Plate" &&
      child.constructor.name === "PlateCarrier";
    const isTipCarrier =
      resourceToSnap.constructor.name === "TipRack" &&
      child.constructor.name === "TipCarrier";
    if (!isPlateCarrier && !isTipCarrier) continue;

    for (let site of child.children) {
      const { x: resX, y: resY } = site.getAbsoluteLocation();
      if (
        x > resX &&
        x < resX + site.size_x &&
        y > resY &&
        y < resY + site.size_y
      ) {
        return {
          resource: site,
          location: { x: 0, y: 0 },
          snappingBox: {
            x: resX,
            y: resY,
            width: site.size_x,
            height: site.size_y,
          },
        };
      }
    }
  }

  // Check OTDeck sites
  if (deck.constructor.name === "OTDeck") {
    const SITE_WIDTH = 128.0;
    const SITE_HEIGHT = 86.0;
    for (let siteLocation of otDeckSiteLocations) {
      const absX = deck.location.x + siteLocation.x;
      const absY = deck.location.y + siteLocation.y;
      if (
        x > absX &&
        x < absX + SITE_WIDTH &&
        y > absY &&
        y < absY + SITE_HEIGHT
      ) {
        return {
          resource: deck,
          location: { x: siteLocation.x, y: siteLocation.y },
          snappingBox: {
            x: absX,
            y: absY,
            width: SITE_WIDTH,
            height: SITE_HEIGHT,
          },
        };
      }
    }
  }

  return undefined;
}

function getSnappingGrid(x, y, width, height) {
  if (!snappingEnabled) return {};

  const SNAP_MARGIN = 5;
  let snappingLines = {};
  const deck = resources["deck"];
  if (!deck) return {};

  if (deck.constructor.name === "HamiltonSTARDeck") {
    // Snap Y to top rail boundary
    const topRailY = deck.location.y + 63;
    if (Math.abs(y - topRailY) < SNAP_MARGIN) {
      snappingLines.resourceY = topRailY;
    }
    // Snap bottom of resource to bottom of rail region
    const bottomRailY = topRailY + deck.railHeight;
    if (Math.abs(y - (bottomRailY - height)) < SNAP_MARGIN) {
      snappingLines.resourceY = bottomRailY - height;
      snappingLines.snappingY = bottomRailY;
    }
    // Snap X to deck origin
    if (Math.abs(x - deck.location.x) < SNAP_MARGIN) {
      snappingLines.resourceX = deck.location.x;
    }
    // Snap X to any rail center
    for (let i = 0; i < deck.num_rails; i++) {
      const railX = 100 + i * 22.5;
      if (Math.abs(x - railX) < SNAP_MARGIN) {
        snappingLines.resourceX = railX;
      }
    }
  }

  // If we have a resourceX but no snappingX, align them
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

// ===========================================================================
// Base Resource Class (color now read dynamically from RESOURCE_COLORS)
// ===========================================================================

class Resource {
  constructor(resourceData, parent = undefined) {
    const { name, location, size_x, size_y, size_z, children } = resourceData;
    this.name = name;
    this.location = location;
    this.size_x = size_x;
    this.size_y = size_y;
    this.size_z = size_z;
    this.parent = parent;
    this.children = [];

    // Instantiate and assign child resources
    for (let childData of children) {
      const ChildClass = classForResourceType(childData.type);
      const childInstance = new ChildClass(childData, this);
      this.assignChild(childInstance);
      resources[childData.name] = childInstance;
    }
  }

  // Dynamically compute the color based on RESOURCE_COLORS
  getColor() {
    if (RESOURCE_COLORS.hasOwnProperty(this.constructor.name)) {
      return RESOURCE_COLORS[this.constructor.name];
    } else if (
      this.constructor.name === "Resource" &&
      this.name.toLowerCase().includes("workcell")
    ) {
      return "lightgrey";
    } else if (RESOURCE_COLORS["Resource"]) {
      return RESOURCE_COLORS["Resource"];
    } else {
      return "#eab676";
    }
  }

  // Properties influenced by mode
  get draggable() {
    return mode === MODE_GUI;
  }
  get canDelete() {
    return mode === MODE_GUI;
  }

  // Top-level draw: destroys previous group, creates a new group, calls drawMainShape & children
  draw(layer) {
    if (this.group) {
      this.group.destroy();
    }

    this.group = new Konva.Group({
      x: this.location.x,
      y: this.location.y,
      draggable: this.draggable,
    });

    this.mainShape = this.drawMainShape();
    if (this.mainShape) {
      this.group.add(this.mainShape);
      this._attachTooltipHandlers(layer);
    }

    // Draw children recursively
    for (let child of this.children) {
      child.draw(layer);
    }

    // Add to layers and parent group
    layer.add(this.group);
    if (this.parent && this.parent.group) {
      this.parent.group.add(this.group);
    }
    this.group.resource = this;
  }

  // Default rectangular shape—uses getColor() now
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

  _attachTooltipHandlers(layer) {
    this.mainShape.resource = this;
    this.mainShape.on("mouseover", () => {
      const { x, y } = this.getAbsoluteLocation();
      if (tooltip) tooltip.destroy();

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
      if (tooltip) tooltip.destroy();
    });
  }

  getAbsoluteLocation() {
    if (this.parent) {
      const parentLoc = this.parent.getAbsoluteLocation();
      return {
        x: parentLoc.x + this.location.x,
        y: parentLoc.y + this.location.y,
        z: (parentLoc.z || 0) + this.location.z,
      };
    }
    return this.location;
  }

  serialize() {
    let serializedChildren = this.children.map((c) => c.serialize());
    return {
      name: this.name,
      type: this.constructor.name,
      location: { ...this.location, type: "Coordinate" },
      size_x: this.size_x,
      size_y: this.size_y,
      size_z: this.size_z,
      children: serializedChildren,
      parent_name: this.parent ? this.parent.name : null,
    };
  }

  assignChild(child) {
    if (child === this) {
      console.error("Cannot assign a resource to itself", this);
      return;
    }
    child.parent = this;
    this.children.push(child);
    if (this.group && child.group) {
      this.group.add(child.group);
    }
  }

  unassignChild(child) {
    child.parent = undefined;
    const idx = this.children.indexOf(child);
    if (idx > -1) this.children.splice(idx, 1);
  }

  destroy() {
    // Destroy children first
    for (let i = this.children.length - 1; i >= 0; i--) {
      this.children[i].destroy();
    }
    delete resources[this.name];
    if (this.group) this.group.destroy();
    if (this.parent) this.parent.unassignChild(this);
  }

  update() {
    this.draw(resourceLayer);
    if (isRecording) {
      if (recordingCounter % frameInterval === 0) {
        stageToBlob(stage, handleBlob);
      }
      recordingCounter++;
    }
  }

  setState() {
    // Default no-op
  }
}

// ===========================================================================
// Deck Classes
// ===========================================================================

class Deck extends Resource {
  get draggable() {
    return false;
  }
  get canDelete() {
    return false;
  }
}

class HamiltonSTARDeck extends Deck {
  constructor(resourceData) {
    super(resourceData);
    this.num_rails = resourceData.num_rails;
    this.railHeight = 497;
  }

  drawMainShape() {
    const group = new Konva.Group();

    // Add a tinted background using getColor()
    const background = new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: this.getColor(),
      stroke: "black",
      strokeWidth: 1,
    });
    group.add(background);

    // Rail area (white on top of tinted background)
    const railArea = new Konva.Rect({
      y: 63,
      width: this.size_x,
      height: this.railHeight,
      fill: "white",
      stroke: "black",
      strokeWidth: 1,
    });
    group.add(railArea);

    // Draw vertical rails and labels
    for (let i = 0; i < this.num_rails; i++) {
      const xPos = 100 + i * 22.5;
      const railLine = new Konva.Line({
        points: [xPos, 63, xPos, 63 + this.railHeight],
        stroke: "black",
        strokeWidth: 1,
      });
      group.add(railLine);

      if ((i + 1) % 5 === 0) {
        const label = new Konva.Text({
          x: xPos,
          y: 50,
          text: i + 1,
          fontSize: 12,
          fill: "black",
        });
        label.scaleY(-1);
        group.add(label);
      }
    }

    return group;
  }

  serialize() {
    return {
      ...super.serialize(),
      num_rails: this.num_rails,
      with_trash: false,
      with_trash96: false,
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
    super(resourceData);
  }

  drawMainShape() {
    const group = new Konva.Group();

    // Tinted background
    const background = new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: this.getColor(),
      stroke: "black",
      strokeWidth: 1,
    });
    group.add(background);

    const SITE_WIDTH = 128.0;
    const SITE_HEIGHT = 86.0;

    // Draw each deck site
    for (let i = 0; i < otDeckSiteLocations.length; i++) {
      const loc = otDeckSiteLocations[i];
      const siteRect = new Konva.Rect({
        x: loc.x,
        y: loc.y,
        width: SITE_WIDTH,
        height: SITE_HEIGHT,
        fill: "white",
        stroke: "black",
        strokeWidth: 1,
      });
      group.add(siteRect);

      // Label the site
      const siteLabel = new Konva.Text({
        x: loc.x,
        y: loc.y + SITE_HEIGHT,
        text: i + 1,
        width: SITE_WIDTH,
        height: SITE_HEIGHT,
        fontSize: 16,
        fill: "black",
        align: "center",
        verticalAlign: "middle",
      });
      siteLabel.scaleY(-1);
      group.add(siteLabel);
    }

    return group;
  }

  serialize() {
    return {
      ...super.serialize(),
      with_trash: false,
    };
  }
}

// ===========================================================================
// Plate and Container Classes
// ===========================================================================

class Plate extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.num_items_x = resourceData.num_items_x;
    this.num_items_y = resourceData.num_items_y;
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

  update() {
    super.update();
    // Rename wells based on grid position
    for (let i = 0; i < this.num_items_x; i++) {
      for (let j = 0; j < this.num_items_y; j++) {
        let idx = i * this.num_items_y + j;
        if (this.children[idx]) {
          this.children[idx].name = `${this.name}_well_${i}_${j}`;
        }
      }
    }
  }

  serialize() {
    return {
      ...super.serialize(),
      num_items_x: this.num_items_x,
      num_items_y: this.num_items_y,
    };
  }
}

class Container extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.maxVolume = resourceData.max_volume;
    this.liquids = resourceData.liquids || [];
  }

  static colorForVolume(volume, maxVolume) {
    const alpha = maxVolume > 0 ? volume / maxVolume : 0;
    return `rgba(239, 35, 60, ${alpha})`;
  }

  getVolume() {
    return this.liquids.reduce((sum, l) => sum + l.volume, 0);
  }

<<<<<<< Updated upstream
=======
  aspirate(volume) {
    let currentVol = this.getVolume();
    if (volume > currentVol) {
      throw new Error(
        `Cannot aspirate ${volume}uL from ${this.name} (only ${currentVol}uL available)`
      );
    }
    let toRemove = volume;
    for (let i = this.liquids.length - 1; i >= 0 && toRemove > 0; i--) {
      if (this.liquids[i].volume <= toRemove) {
        toRemove -= this.liquids[i].volume;
        this.liquids.splice(i, 1);
      } else {
        this.liquids[i].volume -= toRemove;
        toRemove = 0;
      }
    }
    this.update();
  }

  addLiquid(liquid) {
    this.liquids.push(liquid);
    this.update();
  }

>>>>>>> Stashed changes
  setLiquids(liquids) {
    this.liquids = liquids;
    this.update();
  }

  setState(state) {
    const newLiquids = state.liquids.map(([name, vol]) => ({ name, volume: vol }));
    this.setLiquids(newLiquids);
  }

<<<<<<< Updated upstream
=======
  dispense(volume) {
    const totalVol = this.getVolume();
    if (volume + totalVol > this.maxVolume) {
      throw new Error(
        `Cannot dispense ${volume}uL into ${this.name} (exceeds max volume ${this.maxVolume}uL)`
      );
    }
    this.addLiquid({ name: "Unknown liquid", volume });
  }

>>>>>>> Stashed changes
  serializeState() {
    return {
      liquids: this.liquids,
      pending_liquids: [...this.liquids],
    };
  }

  serialize() {
    return {
      ...super.serialize(),
      max_volume: this.maxVolume,
    };
  }
}

class Trough extends Container {
  drawMainShape() {
    const group = new Konva.Group();

    const background = new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: "white",
      stroke: "black",
      strokeWidth: 1,
    });
    const liquidLayer = new Konva.Rect({
      width: this.size_x,
      height: this.size_y,
      fill: Trough.colorForVolume(this.getVolume(), this.maxVolume),
      stroke: "black",
      strokeWidth: 1,
    });
    group.add(background, liquidLayer);
    return group;
  }
}

class Well extends Container {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.cross_section_type = resourceData.cross_section_type;
  }

  get draggable() {
    return false;
  }
  get canDelete() {
    return false;
  }

  drawMainShape() {
    const volume = this.getVolume();
    const alpha = this.maxVolume > 0 ? volume / this.maxVolume : 0;
    const liquidColor = `rgba(239, 35, 60, ${alpha})`;

    // Create a group so we can draw a white background and then the liquid overlay
    const group = new Konva.Group();

    if (this.cross_section_type === "circle") {
      // Draw a white circular background
      const background = new Konva.Circle({
        radius: this.size_x / 2,
        fill: "#E0EAEE",
        stroke: "black",
        strokeWidth: 1,
        offsetX: -this.size_x / 2,
        offsetY: -this.size_y / 2,
      });
      group.add(background);

      // Draw the liquid layer on top (may be fully transparent if empty)
      const liquidLayer = new Konva.Circle({
        radius: this.size_x / 2,
        fill: liquidColor,
        offsetX: -this.size_x / 2,
        offsetY: -this.size_y / 2,
      });
      group.add(liquidLayer);
    } else {
      // Draw a white rectangular background
      const background = new Konva.Rect({
        width: this.size_x,
        height: this.size_y,
        fill: "#E0EAEE",
        stroke: "black",
        strokeWidth: 1,
      });
      group.add(background);

      // Draw the liquid layer on top (transparent if empty)
      const liquidLayer = new Konva.Rect({
        width: this.size_x,
        height: this.size_y,
        fill: liquidColor,
      });
      group.add(liquidLayer);
    }

    return group;
  }
}


// ===========================================================================
// TipRack and TipSpot Classes
// ===========================================================================

class TipRack extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.num_items_x = resourceData.num_items_x;
    this.num_items_y = resourceData.num_items_y;
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

  update() {
    super.update();
    // Rename tip spots based on grid position
    for (let i = 0; i < this.num_items_x; i++) {
      for (let j = 0; j < this.num_items_y; j++) {
        let idx = i * this.num_items_y + j;
        if (this.children[idx]) {
          this.children[idx].name = `${this.name}_tipspot_${i}_${j}`;
        }
      }
    }
  }

  serialize() {
    return {
      ...super.serialize(),
      num_items_x: this.num_items_x,
      num_items_y: this.num_items_y,
    };
  }
}

class TipSpot extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.has_tip = false;
    this.tip = resourceData.prototype_tip;
  }

  get draggable() {
    return false;
  }
  get canDelete() {
    return false;
  }

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

  setTip(hasTip, layer) {
    this.has_tip = hasTip;
    this.draw(layer);
  }

  pickUpTip(layer) {
    if (!this.has_tip) throw new Error("No tip to pick up");
    this.setTip(false, layer);
  }

  dropTip(layer) {
    if (this.has_tip) throw new Error("Tip spot already occupied");
    this.setTip(true, layer);
  }

  serialize() {
    return {
      ...super.serialize(),
      prototype_tip: this.tip,
    };
  }

  serializeState() {
    if (this.has_tip) {
      return { tip: this.tip, pending_tip: this.tip };
    }
    return { tip: null, pending_tip: null };
  }
}

// ===========================================================================
// Trash, Carrier, ResourceHolder, TubeRack, Tube Classes
// ===========================================================================

class Trash extends Resource {
  drawMainShape() {
    // Do not draw if deck exists
    if (resources["deck"]) return undefined;
    return super.drawMainShape();
  }

  dropTip(layer) {
    // No-op
  }
}

class Carrier extends Resource {
  getColor() {
    return RESOURCE_COLORS["Carrier"];
  }
}

class PlateCarrier extends Carrier {}
class TipCarrier extends Carrier {
  getColor() {
    return RESOURCE_COLORS["TipCarrier"];
  }
}
class MFXCarrier extends Carrier {}

class ResourceHolder extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.spot = resourceData.spot;
  }

  get draggable() {
    return false;
  }
  get canDelete() {
    return false;
  }

  serialize() {
    return {
      ...super.serialize(),
      spot: this.spot,
    };
  }
}

class TubeRack extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.num_items_x = resourceData.num_items_x;
    this.num_items_y = resourceData.num_items_y;
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

  update() {
    super.update();
    // Rename tubes based on grid position
    for (let i = 0; i < this.num_items_x; i++) {
      for (let j = 0; j < this.num_items_y; j++) {
        let idx = i * this.num_items_y + j;
        if (this.children[idx]) {
          this.children[idx].name = `${this.name}_tube_${i}_${j}`;
        }
      }
    }
  }

  serialize() {
    return {
      ...super.serialize(),
      num_items_x: this.num_items_x,
      num_items_y: this.num_items_y,
    };
  }
}

class PlateHolder extends ResourceHolder {}

class Tube extends Container {
  get draggable() {
    return false;
  }
  get canDelete() {
    return false;
  }

  drawMainShape() {
    return new Konva.Circle({
      radius: (1.25 * this.size_x) / 2,
      fill: Tube.colorForVolume(this.getVolume(), this.maxVolume),
      stroke: "black",
      strokeWidth: 1,
      offsetX: -this.size_x / 2,
      offsetY: -this.size_y / 2,
    });
  }
}

class LiquidHandler extends Resource {
  drawMainShape() {
    return undefined; // Only children (deck, etc.) are drawn
  }
}

// ===========================================================================
// Utility for mapping resource type strings to classes
// ===========================================================================

function classForResourceType(type) {
  switch (type) {
    case "Deck":
      return Deck;
    case "HamiltonSTARDeck":
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
    case "MFXCarrier":
      return Carrier;
    case "Container":
      return Container;
    case "Trough":
      return Trough;
    case "LiquidHandler":
      return LiquidHandler;
    case "TubeRack":
      return TubeRack;
    case "Tube":
      return Tube;
    default:
      return Resource;
  }
}

function loadResource(resourceData) {
  const ResourceClass = classForResourceType(resourceData.type);
  const parentName = resourceData.parent_name;
  let parent = parentName ? resources[parentName] : undefined;
  let resource = new ResourceClass(resourceData, parent);
  resources[resource.name] = resource;
  return resource;
}

// ===========================================================================
// Initialization and GIF Utilities (unchanged)
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

  let halfW = canvasWidth / 2;
  let halfH = canvasHeight / 2;
  const dragBound = (pos) => ({
    x: Math.max(-halfW, Math.min(halfW, pos.x)),
    y: Math.max(-halfH, Math.min(halfH, pos.y)),
  });
  stage.dragBoundFunc(dragBound);

  // White background
  const background = new Konva.Rect({
    x: -halfW,
    y: -halfH,
    width: canvasWidth,
    height: canvasHeight,
    fill: "white",
    listening: false,
  });
  layer.add(background);

  stage.add(layer);
  stage.add(resourceLayer);

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
  isRecording = true;
  frameImages = [];
  recordingCounter = 0;
  document.getElementById("progressBar").innerText = " GIF Rendering Progress: 0%";
  stageToBlob(stage, handleBlob);
  gifResetUI();
  gifShowRecordingUI();
}

function stopRecording() {
  gifResetUI();
  gifShowProcessingUI();
  isRecording = false;
  // Capture final frames
  stageToBlob(stage, handleBlob);
  stageToBlob(stage, handleBlob);

  gif = new GIF({
    workers: 10,
    workerScript: "gif.worker.js",
    background: "#FFFFFF",
    width: stage.width(),
    height: stage.height(),
  });

  for (let img of frameImages) {
    gif.addFrame(img, { delay: 1 });
  }

  gif.on("progress", function (p) {
    document.getElementById(
      "progressBar"
    ).innerText = " GIF Rendering Progress: " + Math.round(p * 100) + "%";
  });

  gif.on("finished", function (blob) {
    renderedGifBlob = blob;
    gifResetUI();
    gifShowDownloadUI();
    gifShowStartUI();
  });

  gif.render();
}

function stageToBlob(stageObj, callback) {
  stageObj.toBlob({
    callback: callback,
    mimeType: "image/jpg",
    quality: 0.3,
  });
}

function handleBlob(blob) {
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.src = url;
  img.width = stage.width();
  img.height = stage.height();
  frameImages.push(img);
  img.onload = () => URL.revokeObjectURL(url);
}

// Button event listeners
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
    let fileName = document.getElementById("fileName").value || "plr-visualizer";
    if (!fileName.endsWith(".gif")) fileName += ".gif";
    const url = URL.createObjectURL(renderedGifBlob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  });
document
  .getElementById("gif-frame-rate")
  .addEventListener("input", function () {
    let val = Math.round(parseInt(this.value) / 8) * 8;
    val = Math.max(1, Math.min(96, val));
    this.value = val;
    document.getElementById("current-value").textContent =
      "Frame Save Interval: " + val;
    frameInterval = val;
  });

window.addEventListener("load", function () {
  gifResetUI();
  gifShowStartUI();
});
