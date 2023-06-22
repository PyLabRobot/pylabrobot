var layer = new Konva.Layer();
var resourceLayer = new Konva.Layer();
var tooltipLayer = new Konva.Layer();
var tooltip;
var stage;

var canvasWidth, canvasHeight;

const robotWidthMM = 100 + 30 * 22.5; // mm, just the deck
const robotHeightMM = 653.5; // mm
var scaleX, scaleY;

const numRails = 30;

var resources = {}; // name -> Resource object

let trash;

function getSnappingResourceAndLocationAndSnappingBox(x, y) {
  // Return the snapping resource that the given point is within, or undefined if there is no such resource.
  // A snapping resource is a spot within a plate/tip carrier or the OT deck.
  // This can probably be simplified a lot.
  // Returns {resource, location wrt resource}

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
    if (resource.type === "CarrierSite") {
      const { x: resourceX, y: resourceY } = resource.getAbsoluteLocation();
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

class Resource {
  constructor(resource_data, parent = undefined) {
    const { name, location, size_x, size_y, size_z, children } = resource_data;
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
      this.children.push(childInstance);

      // Save in global lookup
      resources[child.name] = childInstance;
    }
  }

  draw(layer) {
    const { x, y } = this.getAbsoluteLocation();

    const rect = new Konva.Rect({
      x: x,
      y: y,
      width: this.size_x,
      height: this.size_y,
      fill: this.color,
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(rect);

    this.drawChildren(layer);
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
      };
    } else {
      return this.location;
    }
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
}

class Deck extends Resource {
  draw(layer) {
    // Draw a transparent rectangle with an outline
    const rect = new Konva.Rect({
      x: 0,
      y: 0,
      width: this.size_x,
      height: this.size_y,
      fill: "white",
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(rect);
  }
}

class HamiltonDeck extends Deck {
  constructor(resource_data) {
    super(resource_data, undefined);
    const { num_rails } = resource_data;
    this.num_rails = num_rails;
  }

  draw(layer) {
    // Draw a transparent rectangle with an outline
    const { x, y } = this.getAbsoluteLocation();

    this.railHeight = 497;

    const rect = new Konva.Rect({
      x: x,
      y: y + 63,
      width: this.size_x,
      height: this.railHeight,
      fill: "white",
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(rect);

    this.drawRails(layer);
    // this.drawChildren(layer);
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
  constructor(resource_data) {
    resource_data.location = { x: 115.65, y: 68.03 };
    super(resource_data, undefined);
  }

  draw(layer) {
    super.draw(layer);

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

class Plate extends Resource {
  constructor(resource_data, parent = undefined) {
    super(resource_data, parent);
    const { num_items_x, num_items_y } = resource_data;
    this.num_items_x = num_items_x;
    this.num_items_y = num_items_y;

    this.color = "#2B2D42";
  }

  draw(layer) {
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

    this.drawChildren(layer);

    rect.on("dragstart", () => {
      resourceLayer.add(trash);
      // resourceLayer.draw();
    });

    // Update the location of the children when the plate is dragged
    rect.on("dragmove", () => {
      const { x, y } = rect.position();

      // Update the UI location of children.
      this.updateChildrenLocation(x, y);

      // If we have a snapping match, show an indicator.
      const { x: rectX, y: rectY } = rect.position();
      const snapResult = getSnappingResourceAndLocationAndSnappingBox(
        rectX + this.size_x / 2,
        rectY + this.size_y / 2
      );

      if (this._snappingBox !== undefined) {
        this._snappingBox.destroy();
      }

      if (snapResult !== undefined) {
        const {
          snappingBox: { x, y, width, height },
        } = snapResult;

        this._snappingBox = new Konva.Rect({
          x: x,
          y: y,
          width: width,
          height: height,
          fill: "rgba(0, 0, 0, 0.1)",
          stroke: "red",
          strokeWidth: 1,
          dash: [10, 5],
        });
        layer.add(this._snappingBox);
      }
    });

    rect.on("dragend", () => {
      let { x: rectX, y: rectY } = rect.position();

      const snapResult = getSnappingResourceAndLocationAndSnappingBox(
        rectX + this.size_x / 2,
        rectY + this.size_y / 2
      );

      if (snapResult !== undefined) {
        const { resource: parent, location } = snapResult;

        if (parent === trash) {
          // special case for trash
          // Delete the plate.
          this.destroy();
        } else {
          const { x, y } = location;
          const { x: parentX, y: parentY } = parent.getAbsoluteLocation();
          rectX = parentX + x;
          rectY = parentY + y;

          // Snap to position in UI.
          rect.position({ x: rectX, y: rectY });
          this.updateChildrenLocation(rectX, rectY);

          // Update the deck layout with the new parent.
          if (this.parent !== undefined) {
            this.parent.unassignChild(this);
          }
          parent.assignChild(this);
        }
      }

      if (this._snappingBox !== undefined) {
        this._snappingBox.destroy();
      }

      // Update the deck layout with the new location.
      if (this.parent === undefined) {
        // not in the tree, so no need to update
        this.location = undefined;
      } else {
        this.location.x = rectX - this.parent.getAbsoluteLocation().x;
        this.location.y = rectY - this.parent.getAbsoluteLocation().y;
      }

      // hide the trash icon
      trash.remove();

      // TODO: I think we can auto save here.
      // we should have a saving indicator, show a warning if the user tries to leave the page
    });
  }

  updateChildrenLocation(x, y) {
    // Update the UI location of children.
    for (let i = 0; i < this.children.length; i++) {
      const child = this.children[i];

      child.mainShape.x(child.location.x + x + child.size_x / 2);
      child.mainShape.y(child.location.y + y + child.size_y / 2);
    }
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

class Well extends Resource {
  constructor(resource_data, parent) {
    super(resource_data, parent);
    this.volume = 0;
    this.maxVolume = resource_data.max_volume;
  }

  static colorForVolume(volume, maxVolume) {
    return `rgba(239, 35, 60, ${volume / maxVolume})`;
  }

  draw(layer) {
    if (this.mainShape !== undefined) {
      this.mainShape.destroy();
    }

    const { x, y } = this.getAbsoluteLocation();
    this.mainShape = new Konva.Circle({
      x: x + this.size_x / 2,
      y: y + this.size_y / 2,
      radius: this.size_x / 2,
      fill: Well.colorForVolume(this.volume, this.maxVolume),
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(this.mainShape);

    super.drawChildren(layer);
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
  constructor(resource_data, parent) {
    super(resource_data, parent);
    const { num_items_x, num_items_y } = resource_data;
    this.num_items_x = num_items_x;
    this.num_items_y = num_items_y;

    this.color = "#2B2D42";
  }

  draw(layer) {
    const { x, y } = this.getAbsoluteLocation();

    const rect = new Konva.Rect({
      x: x,
      y: y,
      width: this.size_x,
      height: this.size_y,
      fill: this.color,
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(rect);

    this.drawChildren(layer);
  }
}

class TipSpot extends Resource {
  constructor(resource_data, parent) {
    super(resource_data, parent);
    this.color = "#40CDA1";
    this.has_tip = false;
    this.tip = resource_data.prototype_tip; // not really a creator, but good enough for now.

    this._circles = [];
  }

  draw(layer) {
    for (let i = 0; i < this._circles.length; i++) {
      this._circles[i].destroy();
    }

    const { x, y } = this.getAbsoluteLocation();
    const magicTipOffset = system === SYSTEM_OPENTRONS ? 1 : 5; // what is this?
    const circ = new Konva.Circle({
      x: x + this.size_x / 2,
      y: y + this.size_y / 2,
      radius: this.size_x,
      fill: this.has_tip ? this.color : "white",
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(circ);
    this._circles.push(circ);

    super.drawChildren(layer);
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
}

class Trash extends Resource {
  constructor(resource_data, parent) {
    super(resource_data, parent);
    this.color = "red";
  }

  draw(layer) {
    // Don't draw trash
  }
}

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
    default:
      return Resource;
  }
}

function drawResource(resource) {
  const resourceClass = classForResourceType(resource.type);

  const parentName = resource.parent_name;
  var parent = undefined;
  if (parentName !== undefined) {
    parent = resources[parentName];
  }

  const resourceInstance = new resourceClass(resource, parent);
  resourceInstance.draw(resourceLayer);

  resources[resource.name] = resourceInstance;
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

  // add the layer to the stage
  stage.add(layer);
  stage.add(resourceLayer);
  stage.add(tooltipLayer);
  tooltipLayer.scaleY(-1);
  tooltipLayer.offsetY(canvasHeight);

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
