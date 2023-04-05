var config = {
  pip_aspiration_duration: 2,
  pip_dispense_duration: 2,
  pip_tip_pickup_duration: 2,
  pip_tip_drop_duration: 2,

  core_aspiration_duration: 2,
  core_dispense_duration: 2,
  core_tip_pickup_duration: 2,
  core_tip_drop_duration: 2,

  min_pip_head_location: -1,
  max_pip_head_location: -1,
  min_core_head_location: -1,
  max_core_head_location: -1,
};

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

class PipettingChannel {
  constructor(identifier) {
    this.identifier = identifier;
    this.volume = null;
    this.tip = null;
  }

  has_tip() {
    return this.tip !== null;
  }

  pickUpTip(tip) {
    if (this.tip !== null) {
      throw `Tip already on pipetting channel ${this.identifier}`;
    }

    this.tip = tip;
    this.volume = 0;
  }

  dropTip() {
    if (this.tip === null) {
      throw `No tip on pipetting channel ${this.identifier}`;
    }

    if (this.volume !== 0) {
      throw `Cannot drop tip from channel ${this.identifier} with volume ${this.volume}`;
    }

    this.tip = null;
  }

  aspirate(volume) {
    if (this.tip === null) {
      throw `No tip on pipetting channel ${this.identifier}`;
    }

    if (this.volume + volume > this.tip.maximal_volume) {
      throw `Not enough volume in tip on pipetting channel ${this.identifier}`;
    }

    this.volume += volume;
  }

  dispense(volume) {
    if (this.volume - volume < 0) {
      throw `Not enough volume in pipetting channel ${this.identifier}`;
    }

    this.volume -= volume;
  }
}

// Initialize pipetting heads.
var mainHead = [];
const SYSTEM_HAMILTON = "Hamilton";
const SYSTEM_OPENTRONS = "Opentrons";
var system = undefined; // "Hamilton" or "Opentrons"

var CoRe96Head = [];
for (var i = 0; i < 8; i++) {
  CoRe96Head[i] = [];
  for (var j = 0; j < 12; j++) {
    CoRe96Head[i].push(new PipettingChannel(`96 head: ${i * 12 + j}`));
  }
}

const statusLabel = document.getElementById("status-label");
const statusIndicator = document.getElementById("status-indicator");
function updateStatusLabel(status) {
  if (status === "loaded") {
    statusLabel.innerText = "Connected";
    statusLabel.classList.add("connected");
    statusLabel.classList.remove("disconnected");
    statusIndicator.classList.add("connected");
    statusIndicator.classList.remove("disconnected");
  } else if (status === "disconnected") {
    statusLabel.innerText = "Disconnected";
    statusLabel.classList.add("disconnected");
    statusLabel.classList.remove("connected");
    statusIndicator.classList.add("disconnected");
    statusIndicator.classList.remove("connected");
  } else {
    statusLabel.innerText = "Loading...";
    statusLabel.classList.remove("connected");
    statusLabel.classList.remove("disconnected");
    statusIndicator.classList.remove("connected");
    statusIndicator.classList.remove("disconnected");
  }
}

function sleep(s) {
  let ms = s * 1000;
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class Resource {
  constructor(resource_data, parent = undefined) {
    const { name, location, size_x, size_y, children } = resource_data;
    this.name = name;
    this.size_x = size_x;
    this.size_y = size_y;
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
}

class OTDeck extends Deck {
  constructor(resource_data) {
    resource_data.location = { x: 115.65, y: 68.03 };
    super(resource_data, undefined);
  }

  draw(layer) {
    super.draw(layer);

    // Draw the sites
    const siteLocations = [
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

    for (let i = 0; i < siteLocations.length; i++) {
      const siteLocation = siteLocations[i];
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
    });
    layer.add(rect);

    this.drawChildren(layer);
  }
}

class Well extends Resource {
  constructor(resource_data, parent) {
    super(resource_data, parent);
    this.volume = 0;
    this.maxVolume = resource_data.max_volume;

    this._circles = [];
  }

  static colorForVolume(volume, maxVolume) {
    return `rgba(239, 35, 60, ${volume / maxVolume})`;
  }

  draw(layer) {
    for (let i = 0; i < this._circles.length; i++) {
      this._circles[i].destroy();
    }

    const { x, y } = this.getAbsoluteLocation();
    const circ = new Konva.Circle({
      x: x + 5,
      y: y + 5,
      radius: 4,
      fill: Well.colorForVolume(this.volume, this.maxVolume),
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(circ);
    this._circles.push(circ);

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
      x: x + magicTipOffset,
      y: y + magicTipOffset,
      radius: 4,
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

function drawResource(data) {
  const resource = data.resource;
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

function adjustVolume(pattern) {
  for (let i = 0; i < pattern.length; i++) {
    const { well_name, volume } = pattern[i];
    const wellInstance = resources[well_name];
    wellInstance.setVolume(volume, resourceLayer);
  }

  return null;
}

function checkPipHeadReach(x) {
  // Check if the x coordinate is within the pip head range. Undefined indicates no limit.
  // Returns the error.
  if (config.min_pip_head_location !== -1 && x < config.min_pip_head_location) {
    return `x position ${x} not reachable, because it is lower than the left limit (${config.min_pip_head_location})`;
  }
  if (config.max_pip_head_location !== -1 && x > config.max_pip_head_location) {
    return `x position ${x} not reachable, because it is higher than the right limit (${config.max_pip_head_location})`;
  }
  return undefined;
}

function checkCoreHeadReachable(x) {
  // Check if the x coordinate is within the core head range. Undefined indicates no limit.
  // Returns the error.
  if (
    config.min_core_head_location !== -1 &&
    x < config.min_core_head_location
  ) {
    return `x position ${x} not reachable, because it is lower than the left limit (${config.min_core_head_location})`;
  }
  if (
    config.max_core_head_location !== -1 &&
    x > config.max_core_head_location
  ) {
    return `x position ${x} not reachable, because it is higher than the right limit (${config.max_core_head_location})`;
  }
  return undefined;
}

function editTips(pattern) {
  for (let i = 0; i < pattern.length; i++) {
    const { tip, has_one } = pattern[i];
    resources[tip.name].setTip(has_one, resourceLayer);
  }
  return null;
}

// Returns error message if there is a problem, otherwise returns null.
function pickUpTips(channels) {
  if (channels.length > mainHead.length) {
    throw new Error(`Too many channels (${channels.length})`);
  }

  for (var i = 0; i < channels.length; i++) {
    var tipSpot = resources[channels[i].resource_name];
    tipSpot.pickUpTip(resourceLayer);

    if (system === SYSTEM_HAMILTON) {
      const pipError = checkPipHeadReach(tipSpot.getAbsoluteLocation().x);
      if (pipError !== undefined) {
        return pipError;
      }
    }

    mainHead[i].pickUpTip(tipSpot.tip);
  }
  return null;
}

// Returns error message if there is a problem, otherwise returns null.
function dropTips(channels) {
  if (channels.length > mainHead.length) {
    throw new Error(`Too many channels (${channels.length})`);
  }

  for (let i = 0; i < channels.length; i++) {
    var tipSpot = resources[channels[i].resource_name];
    tipSpot.dropTip(resourceLayer);

    if (system === SYSTEM_HAMILTON) {
      const pipError = checkPipHeadReach(tipSpot.getAbsoluteLocation().x);
      if (pipError !== undefined) {
        return pipError;
      }
    }

    mainHead[i].dropTip();
  }
  return null;
}

function aspirate(channels) {
  if (channels.length > mainHead.length) {
    throw new Error(`Too many channels (${channels.length})`);
  }

  for (let i = 0; i < channels.length; i++) {
    let { resource_name, volume } = channels[i];

    const well = resources[resource_name];
    well.aspirate(volume, resourceLayer);

    if (system === SYSTEM_HAMILTON) {
      const pipError = checkPipHeadReach(well.getAbsoluteLocation().x);
      if (pipError !== undefined) {
        return pipError;
      }
    }

    mainHead[i].aspirate(volume);
  }
  return null;
}

function dispense(channels) {
  if (channels.length > mainHead.length) {
    throw new Error(`Too many channels (${channels.length})`);
  }

  for (let i = 0; i < channels.length; i++) {
    let { resource_name, volume } = channels[i];

    const well = resources[resource_name];
    well.dispense(volume, resourceLayer);

    if (system === SYSTEM_HAMILTON) {
      const pipError = checkPipHeadReach(well.getAbsoluteLocation().x);
      if (pipError !== undefined) {
        return pipError;
      }
    }

    mainHead[i].dispense(volume);
  }
  return null;
}

function pickupTips96(resource_name) {
  if (system !== SYSTEM_HAMILTON) {
    throw "The 96 head actions are currently only available on the Hamilton Simulator.";
  }

  const tipRack = resources[resource_name];

  // Validate there are enough tips first, and that there are no tips in the head.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip_name = tipRack.children[i + tipRack.num_items_y * j].name;
      const tip_spot = resources[tip_name];
      if (!tip_spot.has_tip) {
        return `There is no tip at (${i},${j}) in ${resource_name}.`;
      }
      if (CoRe96Head[i][j].has_tip()) {
        return `There already is a tip in the CoRe 96 head at (${i},${j}) in ${resource_name}.`;
      }
    }
  }

  // Check reachable for A1.
  let a1_name = tipRack.children[0].name;
  let a1_resource = resources[a1_name];
  if (checkCoreHeadReachable(a1_resource.x) !== undefined) {
    return checkCoreHeadReachable(a1_resource.x);
  }

  // Then pick up the tips.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip_name = tipRack.children[i + tipRack.num_items_y * j].name;
      const tip_spot = resources[tip_name];
      tip_spot.pickUpTip(resourceLayer);
      CoRe96Head[i][j].pickUpTip(tip_spot.tip);
    }
  }
}

function dropTips96(resource_name) {
  if (system !== SYSTEM_HAMILTON) {
    throw "The 96 head actions are currently only available on the Hamilton Simulator.";
  }

  const tipRack = resources[resource_name];

  // Validate there are enough tips first, and that there are no tips in the head.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip_name = tipRack.children[i * tipRack.num_items_x + j].name;
      const tip_spot = resources[tip_name];
      if (tip_spot.has_tip) {
        return `There already is a tip at (${i},${j}) in ${resource_name}.`;
      }
      if (!CoRe96Head[i][j].has_tip()) {
        return `There is no tip in the CoRe 96 head at (${i},${j}) in ${resource_name}.`;
      }
    }
  }

  // Check reachable for A1.
  let a1_name = tipRack.children[0].name;
  let a1_resource = resources[a1_name];
  if (checkCoreHeadReachable(a1_resource.x) !== undefined) {
    return checkCoreHeadReachable(a1_resource.x);
  }

  // Then pick up the tips.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip_spot = tipRack.children[i * tipRack.num_items_x + j];
      tip_spot.dropTip(resourceLayer);
      CoRe96Head[i][j].dropTip();
    }
  }
}

function aspirate96(aspiration) {
  if (system !== SYSTEM_HAMILTON) {
    throw "The 96 head actions are currently only available on the Hamilton Simulator.";
  }

  const resource_name = aspiration.resource_name;
  const plate = resources[resource_name];

  // Check reachable for A1.
  let a1_name = plate.children[0].name;
  let a1_resource = resources[a1_name];
  if (checkCoreHeadReachable(a1_resource.x) !== undefined) {
    return checkCoreHeadReachable(a1_resource.x);
  }

  // Validate there is enough liquid available, that it fits in the tips, and that each channel
  // has a tip before aspiration.
  for (let i = 0; i < plate.num_items_y; i++) {
    for (let j = 0; j < plate.num_items_x; j++) {
      const well = plate.children[i * plate.num_items_x + j];
      if (well.volume < aspiration.volume) {
        return `Not enough volume in well ${well.name}: ${well.volume}uL.`;
      }
      if (
        CoRe96Head[i][j].volume + aspiration.volume >
        CoRe96Head[i][j].tip.maximal_volume
      ) {
        return `Aspirated volume (${aspiration.volume}uL) + volume of tip (${CoRe96Head[i][j].volume}uL) > maximal volume of tip (${CoRe96Head[i][j].tip.maximal_volume}uL).`;
      }
      if (!CoRe96Head[i][j].has_tip()) {
        return `CoRe 96 head channel (${i},${j}) does not have a tip.`;
      }
    }
  }

  for (let i = 0; i < plate.num_items_y; i++) {
    for (let j = 0; j < plate.num_items_x; j++) {
      const well = plate.children[i * plate.num_items_x + j];
      CoRe96Head[i][j].aspirate(aspiration.volume);
      well.aspirate(aspiration.volume, resourceLayer);
    }
  }

  return null;
}

function dispense96(dispense) {
  if (system !== SYSTEM_HAMILTON) {
    throw "The 96 head actions are currently only available on the Hamilton Simulator.";
  }

  const resource_name = dispense.resource_name;
  const plate = resources[resource_name];

  // Check reachable for A1.
  let a1_name = plate.children[0].name;
  let a1_resource = resources[a1_name];
  if (checkCoreHeadReachable(a1_resource.x) !== undefined) {
    return checkCoreHeadReachable(a1_resource.x);
  }

  // Validate there is enough liquid available, that it fits in the well, and that each channel
  // has a tip before dispense.
  for (let i = 0; i < plate.num_items_y; i++) {
    for (let j = 0; j < plate.num_items_x; j++) {
      const well = plate.children[i * plate.num_items_x + j];
      if (CoRe96Head[i][j].volume < dispense.volume) {
        return `Not enough volume in head: ${CoRe96Head[i][j].volume}uL.`;
      }
      if (well.volume + dispense.volume > well.maxVolume) {
        return `Dispensed volume (${dispense.volume}uL) + volume of well (${well.volume}uL) > maximal volume of well (${well.maxVolume}uL).`;
      }
      if (!CoRe96Head[i][j].has_tip()) {
        return `CoRe 96 head channel (${i},${j}) does not have a tip.`;
      }
    }
  }

  for (let i = 0; i < plate.num_items_y; i++) {
    for (let j = 0; j < plate.num_items_x; j++) {
      const well = plate.children[i * plate.num_items_x + j];
      CoRe96Head[i][j].dispense(dispense.volume);
      well.dispense(dispense.volume, resourceLayer);
    }
  }

  return null;
}

async function handleEvent(event, data) {
  if (event === "ready") {
    return; // don't parse response.
  }

  var resource = undefined;
  if (data.resource) {
    resource = data.resource;
  }

  const ret = {
    event: event,
    id: data.id,
  };

  console.log("[event] " + event, data);

  switch (event) {
    case "resource_assigned":
      drawResource(data);
      if (data.resource.name === "deck") {
        // infer the system from the deck.
        if (data.resource.type === "OTDeck") {
          system = SYSTEM_OPENTRONS;
          // Just one channel for Opentrons right now. Should create a UI to select the config.
          mainHead.push(new PipettingChannel("Channel: 1"));
        } else if (data.resource.type === "HamiltonDeck") {
          system = SYSTEM_HAMILTON;
          for (let i = 0; i < 8; i++) {
            mainHead.push(new PipettingChannel(`Channel: ${i + 1}`));
          }
        }
      }
      break;

    case "resource_unassigned":
      removeResource(data.resource_name);
      break;

    case "pick_up_tips":
      await sleep(config.pip_tip_pickup_duration);
      ret.error = pickUpTips(data.channels);
      break;

    case "drop_tips":
      await sleep(config.pip_tip_drop_duration);
      ret.error = dropTips(data.channels);
      break;

    case "edit_tips":
      ret.error = editTips(data.pattern);
      break;

    case "adjust_well_volume":
      ret.error = adjustVolume(data.pattern);
      break;

    case "aspirate":
      await sleep(config.pip_aspiration_duration);
      ret.error = aspirate(data.channels);
      break;

    case "dispense":
      await sleep(config.pip_dispense_duration);
      ret.error = dispense(data.channels);
      break;

    case "pick_up_tips96":
      await sleep(config.core_tip_pickup_duration);
      ret.error = pickupTips96(data.resource_name);
      break;

    case "drop_tips96":
      await sleep(config.core_tip_drop_duration);
      ret.error = dropTips96(data.resource_name);
      break;

    case "aspirate96":
      await sleep(config.core_aspiration_duration);
      ret.error = aspirate96(data.aspiration);
      break;

    case "dispense96":
      await sleep(config.core_dispense_duration);
      ret.error = dispense96(data.dispense);
      break;

    default:
      ret.error = "Unknown event";
      break;
  }

  if (ret.error === undefined || ret.error === null) {
    ret.success = true;
    delete ret.error;
  } else {
    ret.success = false;
  }

  webSocket.send(JSON.stringify(ret));
}

// ===========================================================================
// init
// ===========================================================================

var socketLoading = false;
function openSocket() {
  if (socketLoading) {
    return;
  }

  socketLoading = true;
  updateStatusLabel("loading");
  webSocket = new WebSocket(`ws://localhost:2121/`);

  webSocket.onopen = function (event) {
    console.log("Connected to " + event.target.URL);
    webSocket.send(`{"event": "ready"}`);
    updateStatusLabel("loaded");
    socketLoading = false;

    heartbeat();
  };

  webSocket.onerror = function () {
    updateStatusLabel("disconnected");
    socketLoading = false;
  };

  webSocket.onclose = function () {
    updateStatusLabel("disconnected");
    socketLoading = false;
  };

  // webSocket.onmessage = function (event) {
  webSocket.addEventListener("message", function (event) {
    var data = event.data;
    data = JSON.parse(data);
    console.log(`[message] Data received from server:`, data);
    handleEvent(data.event, data);
  });
}

function heartbeat() {
  if (!webSocket) return;
  if (webSocket.readyState !== WebSocket.OPEN) return;
  webSocket.send(JSON.stringify({ event: "ping" }));
  setTimeout(heartbeat, 5000);
}

var settingsWindow = document.getElementById("settings-window");
settingsWindow.onclick = function (event) {
  if (event.target.id === "settings-window") {
    closeSettings();
  }
};

function loadSettings() {
  // Load settings from localStorage.
  if (localStorage.getItem("config") !== null) {
    let configString = localStorage.getItem("config");
    let configFromLS = JSON.parse(configString);
    // Override config with config from localStorage.
    // This makes any new keys will be added to the config.
    for (let key in configFromLS) {
      config[key] = parseInt(configFromLS[key]); // FIXME: this is not good style.
    }
  }

  // Set settings in UI.
  for (var c in config) {
    var input = document.querySelector(`input[name="${c}"]`);
    if (input) {
      input.value = config[c];
    }
  }
}

function saveSettings(e) {
  // Get settings from UI.
  for (var c in config) {
    var input = document.querySelector(`input[name="${c}"]`);
    if (input) {
      config[c] = parseInt(input.value); // FIXME: this is not good style, what if value is not int?
    }
  }

  // Save settings to localStorage.
  let configString = JSON.stringify(config);
  localStorage.setItem("config", configString);
}

function openSettings() {
  settingsWindow.style.display = "block";
}

function closeSettings() {
  saveSettings();
  settingsWindow.style.display = "none";
}

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
  updateStatusLabel("disconnected");

  openSocket();

  loadSettings();
});

window.addEventListener("resize", function () {
  scaleStage(stage);
});
