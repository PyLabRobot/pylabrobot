var layer = new Konva.Layer();
var resourceLayer = new Konva.Layer();
var tooltipLayer = new Konva.Layer();
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
  for (let resource_name in resources) {
    const resource = resources[resource_name];
    if (
      resource.constructor.name === "CarrierSite" &&
      resourceToSnap.constructor.name !== "Carrier"
    ) {
      const { x: resourceX, y: resourceY } = resource.getAbsoluteLocation();
      if (
        x > resourceX &&
        x < resourceX + resource.size_x &&
        y > resourceY &&
        y < resourceY + resource.size_y
      ) {
        return {
          resource: resource,
          location: { x: 0, y: 0 },
          snappingBox: {
            x: resourceX,
            y: resourceY,
            width: resource.size_x,
            height: resource.size_y,
          },
        };
      }
    }
  }

  // Check if the resource is in the OT Deck.
  const deck = resources["deck"];
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
  if (deck.constructor.name === "HamiltonDeck") {
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

  draw(layer) {
    // On draw, destroy the old shape.
    if (this.mainShape !== undefined) {
      this.mainShape.destroy();
    }

    this.drawMainShape(layer);

    this.drawChildren(layer);

    // If a shape is drawn, add event handlers and other things.
    if (this.mainShape !== undefined) {
      // Add a reference to this to the shape (so that it may be accessed in event handlers)
      this.mainShape.resource = this;

      // Add a tooltip
      this.mainShape.on("mouseover", () => {
        const { x, y } = this.mainShape.position();
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
        tooltipLayer.add(tooltip);
        tooltipLayer.draw();
        tooltip.scaleY(-1);
      });

      this.mainShape.on("mouseout", () => {
        tooltip.destroy();
      });
    }
  }

  drawMainShape(layer) {
    // Draw the main shape of the resource.
    const { x, y } = this.getAbsoluteLocation();
    this.mainShape = new Konva.Rect({
      x: x,
      y: y,
      width: this.size_x,
      height: this.size_y,
      fill: this.color,
      stroke: "black",
      strokeWidth: 1,
      draggable: true,
    });
    layer.add(this.mainShape);
  }

  updateChildrenLocation(x, y) {
    // Update the UI location of children.
    for (let i = 0; i < this.children.length; i++) {
      const child = this.children[i];

      // why was child.size_x / 2 needed?
      child.mainShape.x(child.location.x + x); // + child.size_x / 2);
      child.mainShape.y(child.location.y + y); // + child.size_y / 2);

      child.updateChildrenLocation(child.location.x + x, child.location.y + y);
    }
  }

  tooltipLabel() {
    return `${this.name} (${this.constructor.name})`;
  }

  drawChildren(layer) {
    for (let i = 0; i < this.children.length; i++) {
      const child = this.children[i];
      child.draw(layer);
    }
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
      location: this.location,
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

    child.parent = this;
    this.children.push(child);
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
    if (this.mainShape !== undefined) {
      this.mainShape.destroy();
    }

    // Remove from parent
    if (this.parent !== undefined) {
      this.parent.unassignChild(this);
    }
  }

  update() {
    this.draw(resourceLayer);
  }
}

class Deck extends Resource {
  mainResource(layer) {
    // Draw a transparent rectangle with an outline
    this.mainResource = new Konva.Rect({
      x: 0,
      y: 0,
      width: this.size_x,
      height: this.size_y,
      fill: "white",
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(this.mainResource);
  }
}

class HamiltonDeck extends Deck {
  constructor(resourceData) {
    super(resourceData, undefined);
    const { num_rails } = resourceData;
    this.num_rails = num_rails;
  }

  drawMainShape(layer) {
    // Draw a transparent rectangle with an outline
    const { x, y } = this.getAbsoluteLocation();

    this.railHeight = 497;

    this.mainShape = new Konva.Rect({
      x: x,
      y: y + 63,
      width: this.size_x,
      height: this.railHeight,
      fill: "white",
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(this.mainShape);

    this.drawRails(layer);
  }

  drawRails(layer) {
    // Draw vertical rails as lines
    for (let i = 0; i < numRails; i++) {
      const rail = new Konva.Line({
        points: [
          100 + i * 22.5, // 22.5 mm per rail
          this.location.y + 63,
          100 + i * 22.5, // 22.5 mm per rail
          this.location.y + this.railHeight + 63,
        ],
        stroke: "black",
        strokeWidth: 1,
      });
      layer.add(rail);

      // Add a text label every 5 rails. Rails are 1-indexed.
      // Keep in mind that the stage is flipped vertically.
      if ((i + 1) % 5 === 0) {
        const railLabel = new Konva.Text({
          x: 100 + i * 22.5, // 22.5 mm per rail
          y: this.location.y - 10,
          text: i + 1,
          fontSize: 12,
          fill: "black",
        });
        railLabel.scaleY(-1); // Flip the text vertically
        layer.add(railLabel);
      }
    }
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

  drawMainShape(layer) {
    // Draw the sites
    for (let i = 0; i < otDeckSiteLocations.length; i++) {
      const siteLocation = otDeckSiteLocations[i];
      const width = 128.0;
      const height = 86.0;
      const site = new Konva.Rect({
        x: this.location.x + siteLocation.x,
        y: this.location.y + siteLocation.y,
        width: width,
        height: height,
        fill: "white",
        stroke: "black",
        strokeWidth: 1,
      });
      layer.add(site);

      // Add a text label in the site
      const siteLabel = new Konva.Text({
        x: this.location.x + siteLocation.x,
        y: this.location.y + siteLocation.y + height,
        text: i + 1,
        width: width,
        height: height,
        fontSize: 16,
        fill: "black",
        align: "center",
        verticalAlign: "middle",
      });
      siteLabel.scaleY(-1); // Flip the text vertically
      layer.add(siteLabel);
    }
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

    this.color = "#2B2D42";
  }

  drawMainShape(layer) {
    const { x, y } = this.getAbsoluteLocation();

    const rect = new Konva.Rect({
      x: x,
      y: y,
      width: this.size_x,
      height: this.size_y,
      fill: this.color,
      stroke: "black",
      strokeWidth: 1,
      draggable: true,
    });
    layer.add(rect);
    this.mainShape = rect;
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

class Well extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.volume = 0;
    this.maxVolume = resourceData.max_volume;
  }

  static colorForVolume(volume, maxVolume) {
    return `rgba(239, 35, 60, ${volume / maxVolume})`;
  }

  drawMainShape(layer) {
    const { x, y } = this.getAbsoluteLocation();
    this.mainShape = new Konva.Circle({
      x: x,
      y: y,
      radius: this.size_x / 2,
      fill: Well.colorForVolume(this.volume, this.maxVolume),
      stroke: "black",
      strokeWidth: 1,
    });
    this.mainShape.offsetX(-this.size_x / 2);
    this.mainShape.offsetY(-this.size_y / 2);
    layer.add(this.mainShape);
  }

  setVolume(volume, layer) {
    this.volume = volume;
    this.draw(layer);
  }

  aspirate(volume, layer) {
    if (volume > this.volume) {
      throw new Error(
        `Aspirating ${volume}uL from well ${this.name} with ${this.volume}uL`
      );
    }

    this.setVolume(this.volume - volume, layer);
  }

  dispense(volume, layer) {
    if (volume + this.volume > this.maxVolume) {
      throw new Error(
        `Adding ${volume}uL to well ${this.name} with ${this.volume}uL would exceed max volume of ${this.maxVolume}uL`
      );
    }

    this.setVolume(this.volume + volume, layer);
  }
}

class TipRack extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    const { num_items_x, num_items_y } = resourceData;
    this.num_items_x = num_items_x;
    this.num_items_y = num_items_y;

    this.color = "#2B2D42";
  }

  drawMainShape(layer) {
    const { x, y } = this.getAbsoluteLocation();

    this.mainShape = new Konva.Rect({
      x: x,
      y: y,
      width: this.size_x,
      height: this.size_y,
      fill: this.color,
      stroke: "black",
      strokeWidth: 1,
      draggable: true,
    });
    layer.add(this.mainShape);
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
}

class TipSpot extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.color = "#40CDA1";
    this.has_tip = false;
    this.tip = resourceData.prototype_tip; // not really a creator, but good enough for now.

    this._circles = [];
  }

  drawMainShape(layer) {
    for (let i = 0; i < this._circles.length; i++) {
      this._circles[i].destroy();
    }

    const { x, y } = this.getAbsoluteLocation();
    this.mainShape = new Konva.Circle({
      x: x,
      y: y,
      radius: this.size_x / 2,
      fill: this.has_tip ? this.color : "white",
      stroke: "black",
      strokeWidth: 1,
    });
    this.mainShape.offsetX(-this.size_x / 2);
    this.mainShape.offsetY(-this.size_y / 2);
    layer.add(this.mainShape);
    this._circles.push(this.mainShape);
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
        has_tip: this.has_tip,
      },
    };
  }
}

class Trash extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    this.color = "red";
  }

  drawMainShape(layer) {
    // Don't draw trash
  }
}

class Carrier extends Resource {
  // Nothing special.
}

class CarrierSite extends Resource {
  constructor(resourceData, parent) {
    super(resourceData, parent);
    const { spot } = resourceData;
    this.spot = spot;
  }

  serialize() {
    return {
      ...super.serialize(),
      ...{
        spot: this.spot,
      },
    };
  }
}

function moveToTop(resource) {
  // Recursively move the resource and its children to the top of the layer.
  resource.mainShape.moveToTop();
  for (let i = 0; i < resource.children.length; i++) {
    moveToTop(resource.children[i]);
  }
}

resourceLayer.on("dragstart", (e) => {
  resourceLayer.add(trash);

  let resource = e.target.resource;

  // Move dragged resource to top of layer
  moveToTop(resource);

  // I think we can set resourceBeingDragged somewhere, and use that in the handler. This will allow
  // us to drag a plate when in reality a well is being dragged.
});

resourceLayer.on("dragmove", (e) => {
  if (tooltip !== undefined) {
    tooltip.destroy();
  }

  let { x, y } = e.target.position();
  let resource = e.target.resource;

  // Snap children to position in UI.
  resource.updateChildrenLocation(x, y);

  // Remove any existing snap lines and boxes.
  if (snapLines.length > 0) {
    for (let i = snapLines.length - 1; i >= 0; i--) {
      snapLines[i].destroy();
    }
  }

  if (snappingBox !== undefined) {
    snappingBox.destroy();
  }

  // If we have a snapping box match, draw a snapping box indicator around the area.
  const snapResult = getSnappingResourceAndLocationAndSnappingBox(
    resource,
    x + resource.size_x / 2,
    y + resource.size_y / 2
  );

  if (snapResult !== undefined) {
    const {
      snappingBox: { x, y, width, height },
    } = snapResult;

    snappingBox = new Konva.Rect({
      x: x,
      y: y,
      width: width,
      height: height,
      fill: "rgba(0, 0, 0, 0.1)",
      stroke: "red",
      strokeWidth: 1,
      dash: [10, 5],
    });
    resourceLayer.add(snappingBox);
  } else {
    // If there is no box snapping match, check if there is a grid snapping match.
    // Find the snapping lines for the resource.
    let { snappingX, snappingY, resourceX, resourceY } = getSnappingGrid(
      x,
      y,
      resource.size_x,
      resource.size_y
    );

    // If we have a snapping match, show an indicator and snap to the grid.
    if (snappingX !== undefined) {
      x = resourceX;

      // Draw a vertical line
      let snapLine = new Konva.Line({
        points: [snappingX, 0, snappingX, canvasHeight],
        stroke: "red",
        strokeWidth: 2,
        dash: [10, 5],
      });
      resourceLayer.add(snapLine);
      snapLines.push(snapLine);
    }
    if (snappingY !== undefined) {
      y = resourceY;

      // Draw a vertical line
      let snapLine = new Konva.Line({
        points: [0, snappingY, canvasWidth, snappingY],
        stroke: "red",
        strokeWidth: 2,
        dash: [10, 5],
      });
      resourceLayer.add(snapLine);
      snapLines.push(snapLine);
    }

    // Snap the box to the grid.
    e.target.position({ x: x, y: y });
    resource.updateChildrenLocation(x, y);
  }
});

resourceLayer.on("dragend", (e) => {
  let { x: rectX, y: rectY } = e.target.position();
  let resource = e.target.resource;

  const snapResult = getSnappingResourceAndLocationAndSnappingBox(
    resource,
    rectX + resource.size_x / 2,
    rectY + resource.size_y / 2
  );

  if (snapResult !== undefined) {
    const { resource: parent, location } = snapResult;

    if (parent === trash) {
      // special case for trash
      // Delete the plate.
      resource.destroy();
    } else {
      const { x, y } = location;
      const { x: parentX, y: parentY } = parent.getAbsoluteLocation();
      rectX = parentX + x;
      rectY = parentY + y;

      // Snap to position in UI.
      e.target.position({ x: rectX, y: rectY });
      resource.updateChildrenLocation(rectX, rectY);

      // Update the deck layout with the new parent.
      if (resource.parent !== undefined) {
        resource.parent.unassignChild(resource);
      }
      parent.assignChild(resource);
    }
  }

  if (snappingBox !== undefined) {
    snappingBox.destroy();
  }

  if (snapLines.length > 0) {
    for (let i = snapLines.length - 1; i >= 0; i--) {
      snapLines[i].destroy();
    }
  }

  // Update the deck layout with the new location.
  if (resource.parent === undefined) {
    // not in the tree, so no need to update
    resource.location = undefined;
  } else {
    resource.location.x = rectX - resource.parent.getAbsoluteLocation().x;
    resource.location.y = rectY - resource.parent.getAbsoluteLocation().y;
  }

  // hide the trash icon
  trash.remove();

  autoSave();
});

function handleClick(e) {
  if (tooltip !== undefined) {
    tooltip.destroy();
  }

  selectedResource = e.target.resource;

  // Open the editor for the resource.
  if (selectedResource !== undefined) {
    if (
      ["HamiltonDeck", "OTDeck", "Deck"].includes(
        selectedResource.constructor.name
      )
    ) {
      closeRightSidebar();
      closeContextMenu();
    } else {
      loadEditor(selectedResource);
    }
  } else {
    closeRightSidebar();
    closeContextMenu();
  }
}

// on right click, show options
resourceLayer.on("contextmenu", (e) => {
  e.evt.preventDefault();
  selectedResource = e.target.resource;

  // If the resource is not the deck or the trash, show the context menu.
  let deck = resources["deck"];
  if (
    selectedResource !== undefined &&
    ![trash, deck].includes(selectedResource)
  ) {
    openContextMenu();
  }
});

function classForResourceType(type) {
  switch (type) {
    case "Deck":
      return Deck;
    case "HamiltonDeck":
      return HamiltonDeck;
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
    case "PlateCarrier":
    case "TipCarrier":
      return Carrier;
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

function scaleStage(stage) {
  const canvas = document.getElementById("kanvas");
  canvasWidth = canvas.offsetWidth;
  canvasHeight = canvas.offsetHeight;

  scaleX = canvasWidth / robotWidthMM;
  scaleY = canvasHeight / robotHeightMM;

  const effectiveScale = Math.min(scaleX, scaleY);

  stage.scaleX(effectiveScale);
  stage.scaleY(-1 * effectiveScale);
  stage.offsetY(canvasHeight / effectiveScale);
}

window.addEventListener("load", function () {
  const canvas = document.getElementById("kanvas");
  canvasWidth = canvas.offsetWidth;
  canvasHeight = canvas.offsetHeight;

  stage = new Konva.Stage({
    container: "kanvas",
    width: canvasWidth,
    height: canvasHeight,
  });

  scaleStage(stage);

  // Add click handler to stage
  stage.on("click", handleClick);

  // add the layer to the stage
  stage.add(layer);
  stage.add(resourceLayer);
  stage.add(tooltipLayer);

  // add a trash icon for deleting resources
  var imageObj = new Image();
  trash = new Konva.Image({
    x: 700,
    y: 100,
    image: imageObj,
    width: 50,
    height: 50,
  });
  imageObj.src = "/trash3.svg";
  // Flip the image vertically in place
  trash.scaleY(-1);
  trash.offsetY(50);
});

window.addEventListener("resize", function () {
  scaleStage(stage);
});
