var config = {
  pip_aspiration_duration: 2,
  pip_dispense_duration: 2,
  pip_tip_pickup_duration: 2,
  pip_tip_discard_duration: 2,

  core_aspiration_duration: 2,
  core_dispense_duration: 2,
  core_tip_pickup_duration: 2,
  core_tip_discard_duration: 2,

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
const robotHeightMM = 497; // mm
var scaleX, scaleY;

const numRails = 30;

var resources = {}; // name -> {info, resource, group, shape}

const plateColor = "#2B2D42";
const hasTipsColor = "#40CDA1";
const noTipsColor = plateColor;

// Initialize pipetting heads.
var pipHead = []; // [{has_tip: bool, volume: float, tipMaxVolume: float}]
for (var i = 0; i < 8; i++) {
  pipHead.push({ has_tip: false, volume: 0 });
}
var CoRe96Head = []; // [[{has_tip: bool, volume: float, tipMaxVolume: float}]]
for (var i = 0; i < 8; i++) {
  CoRe96Head[i] = [];
  for (var j = 0; j < 12; j++) {
    CoRe96Head[i].push({ has_tip: false, volume: 0, tipMaxVolume: 0 });
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

const COLORS = {
  tip_carrier: "#D80032",
  carrier_site: "#00D2FF",
  plate_carrier: "#D80032",
  tips: plateColor,
  tip: noTipsColor,
  plate: plateColor,
  well: "#AAFF32",
};

function min(a, b) {
  return a < b ? a : b;
}

function sleep(s) {
  let ms = s * 1000;
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createShape(resource) {
  if (resource.category === "tip" || resource.category === "well") {
    return new Konva.Circle({
      x: 0,
      y: 0,
      width: min(resource.size_x, resource.size_y),
      height: min(resource.size_x, resource.size_y),
      fill: noTipsColor,
      stroke: hasTipsColor,
      strokeWidth: 1,
    });
  }
  return new Konva.Rect({
    x: 0,
    y: 0,
    width: resource.size_x,
    height: resource.size_y,
    fill: COLORS[resource.category],
    stroke: hasTipsColor,
    strokeWidth: 1,
  });
}

function drawResource(resource, parentGroup) {
  var group = new Konva.Group({
    x: resource.location.x,
    y: resource.location.y,
  });

  var shape = createShape(resource);
  group.add(shape);

  for (var i = 0; i < resource.children.length; i++) {
    var child = resource.children[i];
    drawResource(child, group);
  }

  // TODO: move to create resource.
  // TODO: get maxVolume from server.
  var info = {};
  if (resource.category === "well") {
    info = {
      maxVolume: 1000,
      volume: 0,
    };
  }
  resources[resource.name] = {
    resource: resource,
    info: info,
    group: group,
    shape: shape,
  };

  parentGroup.add(group);
}

function removeResource(resource_name) {
  if (resource_name in resources) {
    resources[resource_name].shape.destroy();
    resources[resource_name].group.destroy();
    delete resources[resource_name];
  }
}

function colorForVolume(volume, maxVolume) {
  return `rgba(239, 35, 60, ${volume / maxVolume})`;
}

function adjustVolumeSingleWell(well_name, volume) {
  resources[well_name].info.volume = volume;
  const newColor = colorForVolume(volume, resources[well_name].info.maxVolume);
  resources[well_name].shape.fill(newColor);
}

function adjustVolume(pattern) {
  for (let i = 0; i < pattern.length; i++) {
    const { well, volume } = pattern[i];
    adjustVolumeSingleWell(well.name, volume);
  }
  return null;
}

function getAbsoluteLocation(resource) {
  var parentLocation;
  if (
    resource.hasOwnProperty("parent_name") &&
    resource.parent_name in resources
  ) {
    const parent = resources[resource.parent_name].resource;
    parentLocation = getAbsoluteLocation(parent);
  } else {
    // If resource has no parent, assume it's at the origin.
    // TODO: get the origin from the server / load deck as parent.
    parentLocation = { x: 0, y: 63 };
  }
  return {
    x: resource.location.x + parentLocation.x,
    y: resource.location.y + parentLocation.y,
  };
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

// Returns error message if there is a problem, otherwise returns null.
function pickUpTips(channels) {
  for (var i = 0; i < channels.length; i++) {
    var tip = channels[i];
    if (tip === null || tip === undefined) {
      continue;
    }

    const resource = resources[tip.name];

    if (!resource.info.has_tip) {
      return `${tip.name} is not a tip`;
    }
    if (pipHead[i].has_tip) {
      return `${tip.name} is already picked up`;
    }

    if (
      checkPipHeadReach(getAbsoluteLocation(resource.resource).x) !== undefined
    ) {
      return checkPipHeadReach(getAbsoluteLocation(resource.resource).x);
    }

    resource.shape.fill(noTipsColor);
    resource.info.has_tip = false;
    pipHead[i].has_tip = true;
    pipHead[i].tipMaxVolume = resource.info.maxVolume;
  }
  return null;
}

// Returns error message if there is a problem, otherwise returns null.
function discardTips(channels) {
  for (let i = 0; i < channels.length; i++) {
    var tip = channels[i];
    if (tip === null || tip === undefined) {
      continue;
    }

    const resource = resources[tip.name];

    if (resource.info.has_tip) {
      return `There already is tip at location ${tip.resource}.`;
    }
    if (!pipHead[i].has_tip) {
      return `Pip head channel ${i + 1} already does not have a tip.`;
    }
    if (pipHead[i].volume > 0) {
      return `Pip head channel ${i + 1} has a volume of ${
        pipHead[i].volume
      }uL > 0`;
    }

    if (
      checkPipHeadReach(getAbsoluteLocation(resource.resource).x) !== undefined
    ) {
      return checkPipHeadReach(getAbsoluteLocation(resource.resource).x);
    }

    resource.shape.fill(hasTipsColor);
    resource.info.has_tip = true;
    pipHead[i].has_tip = false;
    pipHead[i].tipMaxVolume = undefined;
  }
  return null;
}

function editTips(pattern) {
  for (let i = 0; i < pattern.length; i++) {
    const { tip, has_one } = pattern[i];
    resources[tip.name].shape.fill(has_one ? hasTipsColor : noTipsColor);
    resources[tip.name].info.has_tip = has_one;
  }
  return null;
}

function aspirate(channels) {
  for (let i = 0; i < channels.length; i++) {
    if (channels[i] === null || channels[i] === undefined) {
      continue;
    }

    let { resource, volume } = channels[i];

    const well = resources[resource.name];

    if (well.info.volume < volume) {
      return `Not enough volume in well: ${well.info.volume}uL.`;
    }
    if (!pipHead[i].has_tip) {
      return `Pip head channel ${i + 1} does not have a tip.`;
    }
    if (volume + pipHead[i].volume > pipHead[i].tipMaxVolume) {
      return `Aspirated volume (${volume}uL) + volume of tip (${pipHead[i].volume}uL) > maximal volume of tip (${pipHead[i].tipMaxVolume}uL).`;
    }

    if (checkPipHeadReach(getAbsoluteLocation(well.resource).x) !== undefined) {
      return checkPipHeadReach(getAbsoluteLocation(well.resource).x);
    }

    pipHead[i].volume += volume;
    adjustVolumeSingleWell(resource.name, well.info.volume - volume);
  }
  return null;
}

function dispense(channels) {
  for (let i = 0; i < channels.length; i++) {
    if (channels[i] === null || channels[i] === undefined) {
      continue;
    }

    let { resource, volume } = channels[i];

    const well = resources[resource.name];

    if (pipHead[i].volume < volume) {
      return `Not enough volume in tip: ${pipHead[i].volume}.`;
    }
    if (!pipHead[i].has_tip) {
      return `Pip head channel ${i + 1} does not have a tip.`;
    }
    if (volume + well.volume > well.maxVolume) {
      return `Dispensed volume (${volume}uL) + volume of well (${well.volume}uL) > maximal volume of well (${well.maxVolume}uL).`;
    }

    if (checkPipHeadReach(getAbsoluteLocation(well.resource).x) !== undefined) {
      return checkPipHeadReach(getAbsoluteLocation(well.resource).x);
    }

    pipHead[i].volume -= volume;
    adjustVolumeSingleWell(resource.name, well.info.volume + volume);
  }
  return null;
}

function pickupTips96(resource) {
  // Validate there are enough tips first, and that there are no tips in the head.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip_name = resource.children[i * resource.num_items_x + j].name;
      const tip = resources[tip_name];
      if (!tip.info.has_tip) {
        return `There is no tip at (${i},${j}) in ${resource.name}.`;
      }
      if (CoRe96Head[i][j].has_tip) {
        return `There already is a tip in the CoRe 96 head at (${i},${j}) in ${resource.name}.`;
      }
    }
  }

  // Check reachable for A1.
  let a1_name = resource.children[0].name;
  let a1_resource = resources[a1_name];
  if (checkCoreHeadReachable(a1_resource.x) !== undefined) {
    return checkCoreHeadReachable(a1_resource.x);
  }

  // Then pick up the tips.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip_name = resource.children[i * resource.num_items_x + j].name;
      const tip = resources[tip_name];
      tip.info.has_tip = false;
      tip.shape.fill(noTipsColor);
      CoRe96Head[i][j].has_tip = true;
      CoRe96Head[i][j].tipMaxVolume = tip.info.maxVolume;
    }
  }
}

function discardTips96(resource) {
  // Validate there are enough tips first, and that there are no tips in the head.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip_name = resource.children[i * resource.num_items_x + j].name;
      const tip = resources[tip_name];
      if (tip.info.has_tip) {
        return `There already is a tip at (${i},${j}) in ${resource.name}.`;
      }
      if (!CoRe96Head[i][j].has_tip) {
        return `There is no tip in the CoRe 96 head at (${i},${j}) in ${resource.name}.`;
      }
    }
  }

  // Check reachable for A1.
  let a1_name = resource.children[0].name;
  let a1_resource = resources[a1_name];
  if (checkCoreHeadReachable(a1_resource.x) !== undefined) {
    return checkCoreHeadReachable(a1_resource.x);
  }

  // Then pick up the tips.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip_name = resource.children[i * resource.num_items_x + j].name;
      const tip = resources[tip_name];
      tip.info.has_tip = true;
      tip.shape.fill(hasTipsColor);
      CoRe96Head[i][j].has_tip = false;
      CoRe96Head[i][j].tipMaxVolume = undefined;
    }
  }
}

function aspirate96(resource, pattern) {
  // Check reachable for A1.
  let a1_name = resource.children[0].name;
  let a1_resource = resources[a1_name];
  if (checkCoreHeadReachable(a1_resource.x) !== undefined) {
    return checkCoreHeadReachable(a1_resource.x);
  }

  // Validate there is enough liquid available, that it fits in the tips, and that each channel
  // has a tip before aspiration.
  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      const well_name = resource.children[i * resource.num_items_x + j].name;
      const well = resources[well_name];
      if (well.info.volume < pattern[i][j]) {
        return `Not enough volume in well: ${well.volume}uL.`;
      }
      if (
        CoRe96Head[i][j].volume + pattern[i][j] >
        CoRe96Head[i][j].maxVolume
      ) {
        return `Aspirated volume (${pattern[i][j]}uL) + volume of tip (${CoRe96Head[i][j].volume}uL) > maximal volume of tip (${CoRe96Head[i][j].maxVolume}uL).`;
      }
      if (!CoRe96Head[i][j].has_tip) {
        return `CoRe 96 head channel (${i},${j}) does not have a tip.`;
      }
    }
  }

  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      const well_name = resource.children[i * resource.num_items_x + j].name;
      const well = resources[well_name];
      CoRe96Head[i][j].volume += pattern[i][j];
      adjustVolumeSingleWell(well_name, well.info.volume - pattern[i][j]);
    }
  }

  return null;
}

function dispense96(resource, pattern) {
  // Check reachable for A1.
  let a1_name = resource.children[0].name;
  let a1_resource = resources[a1_name];
  if (checkCoreHeadReachable(a1_resource.x) !== undefined) {
    return checkCoreHeadReachable(a1_resource.x);
  }

  // Validate there is enough liquid available, that it fits in the well, and that each channel
  // has a tip before dispense.
  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      const well_name = resource.children[i * resource.num_items_x + j].name;
      const well = resources[well_name];
      if (CoRe96Head[i][j].volume < pattern[i][j]) {
        return `Not enough volume in head: ${CoRe96Head[i][j].volume}uL.`;
      }
      if (well.info.volume + pattern[i][j] > well.info.maxVolume) {
        return `Dispensed volume (${pattern[i][j]}uL) + volume of well (${well.info.volume}uL) > maximal volume of well (${well.info.maxVolume}uL).`;
      }
      if (!CoRe96Head[i][j].has_tip) {
        return `CoRe 96 head channel (${i},${j}) does not have a tip.`;
      }
    }
  }

  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      const well_name = resource.children[i * resource.num_items_x + j].name;
      const well = resources[well_name];
      CoRe96Head[i][j].volume -= pattern[i][j];
      adjustVolumeSingleWell(well_name, well.info.volume + pattern[i][j]);
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
      var group;
      const has_parent_name = data.hasOwnProperty("parent_name");
      if (
        has_parent_name === undefined ||
        has_parent_name === null ||
        data["parent_name"] === "deck"
      ) {
        group = resourceLayer;
      } else {
        group = resources[data["parent_name"]].group;
      }
      drawResource(resource, group);
      break;

    case "resource_unassigned":
      removeResource(data.resource_name);
      break;

    case "pickup_tips":
      await sleep(config.pip_tip_pickup_duration);
      ret.error = pickUpTips(data.channels);
      break;

    case "discard_tips":
      await sleep(config.pip_tip_discard_duration);
      ret.error = discardTips(data.channels);
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

    case "pickup_tips96":
      await sleep(config.core_tip_pickup_duration);
      ret.error = pickupTips96(resource);
      break;

    case "discard_tips96":
      await sleep(config.core_tip_discard_duration);
      ret.error = discardTips96(resource);
      break;

    case "aspirate96":
      await sleep(config.core_aspiration_duration);
      ret.error = aspirate96(resource, data.pattern);
      break;

    case "dispense96":
      await sleep(config.core_dispense_duration);
      ret.error = dispense96(resource, data.pattern);
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

const railOffsetX = 100;
function drawRails() {
  for (var i = 0; i < numRails; i++) {
    const x = 100 + ((canvasWidth / scaleX - 100) / numRails) * i;
    const line = new Konva.Line({
      points: [x, 0, x, canvasHeight],
      stroke: "gray",
      strokeWidth: 1,
    });
    layer.add(line);
  }
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

window.addEventListener("load", function () {
  const canvas = document.getElementById("kanvas");
  canvasWidth = canvas.offsetWidth;
  canvasHeight = canvas.offsetHeight;

  scaleX = canvasWidth / robotWidthMM;
  scaleY = canvasHeight / robotHeightMM;

  // TODO: use min(scaleX, scaleY) to preserve aspect ratio.

  stage = new Konva.Stage({
    container: "kanvas",
    width: canvasWidth,
    height: canvasHeight,

    scaleX: scaleX,
    // VENUS & robot coordinates have the origin in the bottom left corner, so we need to flip the y axis.
    scaleY: -1 * scaleY,
    offsetY: canvasHeight / scaleY,
  });

  // add the layer to the stage
  stage.add(layer);
  stage.add(resourceLayer);
  stage.add(tooltipLayer);
  tooltipLayer.scaleY(-1);
  tooltipLayer.offsetY(canvasHeight);
  updateStatusLabel("disconnected");

  drawRails();

  openSocket();

  loadSettings();
});
