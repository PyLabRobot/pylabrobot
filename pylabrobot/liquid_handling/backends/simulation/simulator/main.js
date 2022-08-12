// Simulator.js

// ===========================================================================
// UI tools
// ===========================================================================

const robotWidthMM = 30 * 22.5; // mm, just the deck
const robotHeightMM = 497; // mm

var layer = new Konva.Layer();
var resourceLayer = new Konva.Layer();
var tooltipLayer = new Konva.Layer();
var tooltip;
var stage;

var canvasWidth, canvasHeight;

const numRails = 30;

var resources = {};

const plateColor = "#2B2D42";

function locationToPixel(location) {
  return {
    x: (location.x * canvasWidth) / robotWidthMM,
    y: (location.y * canvasHeight) / robotHeightMM,
  };
}

function robotWidthToScreenWidth(robotWidth) {
  return (robotWidth * canvasWidth) / robotWidthMM;
}

function robotHeightToScreenHeight(robotHeight) {
  return (robotHeight * canvasHeight) / robotHeightMM;
}

var tips = {};
var wells = {};

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

function drawCarrier(resource) {}

function addTooltipOnMouseMove(text) {
  var mousePos = stage.getPointerPosition();
  tooltip.position({
    x: mousePos.x + 5,
    // y: canvasHeight - (mousePos.y + 5),
    y: mousePos.y + 5,
  });
  tooltip.getText().text(text);
  tooltip.show();
}

function addTooltipWell(well, row, column, resource) {
  const letter = String.fromCharCode(65 + row);

  well.on("mousemove", () => {
    addTooltipOnMouseMove(
      `${resource.name} (${letter}${column + 1}) (${
        wells[resource.name][row][column].volume
      }uL)`
    );
  });

  well.on("mouseout", function () {
    tooltip.hide();
  });
}

function addTooltipTip(tip, row, column, resource) {
  const letter = String.fromCharCode(65 + row);

  tip.on("mousemove", () => {
    addTooltipOnMouseMove(
      `${resource.name} (${letter}${column + 1}) (${
        tips[resource.name][row][column].has_tip ? "has" : "no"
      } tip)`
    );
  });

  tip.on("mouseout", function () {
    tooltip.hide();
  });
}

function drawPlate(resource, resourceLocation, group) {
  // Draw plate background.
  var plate = new Konva.Rect({
    x: resourceLocation.x,
    y: resourceLocation.y,
    width: robotWidthToScreenWidth(resource.size_x),
    height: robotHeightToScreenHeight(resource.size_y),
    fill: plateColor,
    stroke: "black",
    strokeWidth: 1,
  });
  group.add(plate);

  // Initialize empty well objects.
  wells[resource.name] = {};
  for (var i = 0; i < 8; i++) {
    wells[resource.name][i] = {};
    for (var j = 0; j < 12; j++) {
      wells[resource.name][i][j] = {};
    }
  }

  // Draw wells.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      // Fit n+1 wells in the row/column.
      var well = new Konva.Circle({
        x:
          resourceLocation.x +
          ((j + 1) * robotWidthToScreenWidth(resource.size_x) - 0) / (12 + 1),
        y:
          resourceLocation.y +
          ((8 - i) * robotHeightToScreenHeight(resource.size_y) - 20) / (8 + 1),
        width: 10,
        height: 10,
        stroke: "#D80032",
        strokeWidth: 1,
      });

      const volume = 0;
      const maxVolume = 1000; // TODO: get well volume from server.
      wells[resource.name][i][j] = {
        maxVolume: maxVolume,
        shape: well,
        volume: volume,
      };

      adjustVolumeSingleWell(resource, i, j, volume);

      addTooltipWell(well, i, j, resource);

      group.add(well);
    }
  }
}

function drawTips(resource, resourceLocation, group) {
  // Draw tips background.
  var tipsShape = new Konva.Rect({
    x: resourceLocation.x,
    y: resourceLocation.y,
    width: robotWidthToScreenWidth(resource.size_x),
    height: robotHeightToScreenHeight(resource.size_y),
    fill: plateColor,
    stroke: "black",
    strokeWidth: 1,
  });
  group.add(tipsShape);

  // Initialize empty tip objects.
  tips[resource.name] = {};
  for (var i = 0; i < 8; i++) {
    tips[resource.name][i] = {};
    for (var j = 0; j < 12; j++) {
      tips[resource.name][i][j] = {};
    }
  }

  // Draw tips.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      var tip = new Konva.Circle({
        x:
          resourceLocation.x +
          ((j + 1) * robotWidthToScreenWidth(resource.size_x) - 0) / (12 + 1),
        y:
          resourceLocation.y +
          ((8 - i) * robotHeightToScreenHeight(resource.size_y) - 20) / (8 + 1),
        width: 10,
        height: 10,
        fill: noTipsColor,
        stroke: "#1ee172",
        strokeWidth: 1,
      });

      tips[resource.name][i][j] = {
        tip: tip,
        has_tip: false,
      };
      addTooltipTip(tip, i, j, resource);

      group.add(tip);
    }
  }
}

// ===========================================================================
// event handling
// ===========================================================================

function drawResource(resource) {
  const location = locationToPixel({
    x: resource.location.x - 100,
    y: resource.location.y - 63,
  });

  var group = new Konva.Group({
    x: location.x,
    y: location.y,
  });

  var carrier = new Konva.Rect({
    x: 0,
    y: 0,
    width: robotWidthToScreenWidth(resource.size_x),
    height: robotHeightToScreenHeight(resource.size_y),
    fill: "#8D99AE",
    stroke: "black",
    strokeWidth: 1,
  });

  group.add(carrier);

  // If resource is a carrier, draw the sites too.
  for (var i = 0; i < resource.sites.length; i++) {
    const site = resource.sites[i].site;
    const location = locationToPixel(site.location);

    // If the site has a resource, draw that too.
    if (site.resource) {
      if (site.resource.category === "plate") {
        drawPlate(site.resource, location, group);
      } else if (site.resource.category === "tips") {
        drawTips(site.resource, location, group);
      }
    } else {
      // If the site is empty, draw a circle.
      var emptySite = new Konva.Rect({
        x: location.x,
        y: location.y,
        width: robotWidthToScreenWidth(site.width),
        height: robotHeightToScreenHeight(site.height),
        fill: plateColor,
        stroke: "black",
        opacity: 0.5,
        strokeWidth: 1,
      });
      group.add(emptySite);
    }
  }

  // TODO: should we add subresources, plates in particular, as separate resources?
  resources[resource.name] = group;
  layer.add(group);
}

function removeResource(resource_name) {
  if (resource_name in resources) {
    resources[resource_name].destroy();
    delete resources[resource_name];
  }
}

function colorForVolume(volume, maxVolume) {
  return `rgba(239, 35, 60, ${volume / maxVolume})`;
}

function adjustVolumeSingleWell(resource, row, column, volume) {
  const well = wells[resource.name][row][column];
  well.volume = volume;
  const newColor = colorForVolume(volume, well.maxVolume);
  well.shape.fill(newColor);
}

function adjustVolume(resource, pattern) {
  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      adjustVolumeSingleWell(resource, i, j, pattern[i][j]);
    }
  }
  return null;
}

const hasTipsColor = "#40CDA1";
const noTipsColor = plateColor;

// A1 -> 0, 0; B2 -> 1, 1; C3 -> 2, 2; etc.
function positionToCoordinate(position) {
  const row = position.charCodeAt(0) - 65;
  const column = position.slice(1) - 1;
  return { row, column };
}

// Returns error message if there is a problem, otherwise returns null.
function pickUpTips(resource, channels) {
  // channel locations
  channels = [
    channels["channel_1"],
    channels["channel_2"],
    channels["channel_3"],
    channels["channel_4"],
    channels["channel_5"],
    channels["channel_6"],
    channels["channel_7"],
    channels["channel_8"],
  ];
  for (let i = 0; i < channels.length; i++) {
    let location = channels[i];

    // Skip if the channel is empty: in that case, no tip should be picked up.
    if (location === null) {
      continue;
    }

    const { row, column } = positionToCoordinate(location);

    const tip = tips[resource.name][row][column];
    if (!tip.has_tip) {
      return `No tip at location ${location}.`;
    }
    if (pipHead[i].has_tip) {
      return `Pip head channel ${i + 1} already has a tip.`;
    }

    tip.has_tip = false;
    tip.tip.fill(noTipsColor);
    pipHead[i].has_tip = true;
    pipHead[i].tipMaxVolume = resource.tip_type.maximal_volume;
  }
  return null;
}

// Returns error message if there is a problem, otherwise returns null.
function discardTips(resource, channels) {
  // channel locations
  channels = [
    channels["channel_1"],
    channels["channel_2"],
    channels["channel_3"],
    channels["channel_4"],
    channels["channel_5"],
    channels["channel_6"],
    channels["channel_7"],
    channels["channel_8"],
  ];
  for (let i = 0; i < channels.length; i++) {
    let location = channels[i];

    // Skip if the channel is empty: in that case, no tip should be picked up.
    if (location === null) {
      continue;
    }

    const { row, column } = positionToCoordinate(location);

    const tip = tips[resource.name][row][column];
    if (tip.has_tip) {
      return `There already is tip at location ${location}.`;
    }
    if (!pipHead[i].has_tip) {
      return `Pip head channel ${i + 1} already does not have a tip.`;
    }
    if (pipHead[i].volume > 0) {
      return `Pip head channel ${i + 1} has a volume of ${
        pipHead[i].volume
      }uL > 0`;
    }

    tip.has_tip = true;
    tip.tip.fill(hasTipsColor);
    pipHead[i].has_tip = false;
    pipHead[i].tipMaxVolume = undefined;
  }
  return null;
}

function editTips(resource, pattern) {
  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      tips[resource.name][i][j].tip.fill(
        pattern[i][j] ? hasTipsColor : noTipsColor
      );
      tips[resource.name][i][j].has_tip = pattern[i][j];
    }
  }
  return null;
}

function aspirate(resource, channels) {
  var channels = [
    channels["channel_1"],
    channels["channel_2"],
    channels["channel_3"],
    channels["channel_4"],
    channels["channel_5"],
    channels["channel_6"],
    channels["channel_7"],
    channels["channel_8"],
  ];
  for (let i = 0; i < channels.length; i++) {
    let info = channels[i];

    // Skip if the channel is empty.
    if (info === null || info === undefined) {
      continue;
    }

    const { row, column } = positionToCoordinate(info.position);
    const well = wells[resource.name][row][column];

    if (well.volume < info.volume) {
      return `Not enough volume in well: ${well.volume}uL.`;
    }

    if (!pipHead[i].has_tip) {
      return `Pip head channel ${i + 1} does not have a tip.`;
    }

    if (info.volume + pipHead[i].volume > pipHead[i].tipMaxVolume) {
      return `Aspirated volume (${info.volume}uL) + volume of tip (${pipHead[i].volume}uL) > maximal volume of tip (${pipHead[i].tipMaxVolume}uL).`;
    }

    pipHead[i].volume += info.volume;
    adjustVolumeSingleWell(resource, row, column, well.volume - info.volume);
  }
  return null;
}

function dispense(resource, channels) {
  var channels = [
    channels["channel_1"],
    channels["channel_2"],
    channels["channel_3"],
    channels["channel_4"],
    channels["channel_5"],
    channels["channel_6"],
    channels["channel_7"],
    channels["channel_8"],
  ];
  for (let i = 0; i < channels.length; i++) {
    let info = channels[i];

    // Skip if the channel is empty.
    if (info === null || info === undefined) {
      continue;
    }

    const { row, column } = positionToCoordinate(info.position);
    const well = wells[resource.name][row][column];

    if (pipHead[i].volume < info.volume) {
      return `Not enough volume in tip: ${pipHead[i].volume}.`;
    }

    if (!pipHead[i].has_tip) {
      return `Pip head channel ${i + 1} does not have a tip.`;
    }

    if (info.volume + well.volume > well.maxVolume) {
      return `Dispensed volume (${info.volume}uL) + volume of well (${well.volume}uL) > maximal volume of well (${well.maxVolume}uL).`;
    }

    pipHead[i].volume -= info.volume;
    adjustVolumeSingleWell(resource, row, column, well.volume + info.volume);
  }
  return null;
}

function pickupTips96(resource) {
  // Validate there are enough tips first, and that there are no tips in the head.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      if (!tips[resource.name][i][j].has_tip) {
        return `There is no tip at (${i},${j}) in ${resource.name}.`;
      }
      if (CoRe96Head[i][j].has_tip) {
        return `There already is a tip in the CoRe 96 head at (${i},${j}) in ${resource.name}.`;
      }
    }
  }

  // Then pick up the tips.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip = tips[resource.name][i][j];
      tip.has_tip = false;
      tip.tip.fill(noTipsColor);
      CoRe96Head[i][j].has_tip = true;
      CoRe96Head[i][j].tipMaxVolume = resource.tip_type.maximal_volume;
    }
  }
}

function discardTips96(resource) {
  // Validate there are enough tips first, and that there are no tips in the head.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      if (tips[resource.name][i][j].has_tip) {
        return `There already is a tip at (${i},${j}) in ${resource.name}.`;
      }
      if (!CoRe96Head[i][j].has_tip) {
        return `There is no tip in the CoRe 96 head at (${i},${j}) in ${resource.name}.`;
      }
    }
  }

  // Then pick up the tips.
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 12; j++) {
      const tip = tips[resource.name][i][j];
      tip.has_tip = true;
      tip.tip.fill(hasTipsColor);
      CoRe96Head[i][j].has_tip = false;
      CoRe96Head[i][j].tipMaxVolume = undefined;
    }
  }
}

function aspirate96(resource, pattern) {
  // Validate there is enough liquid available, that it fits in the tips, and that each channel
  // has a tip before aspiration.
  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      if (pattern[i][j] > 0) {
        const well = wells[resource.name][i][j];
        if (well.volume < pattern[i][j]) {
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
  }

  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      const well = wells[resource.name][i][j];
      CoRe96Head[i][j].volume += pattern[i][j];
      adjustVolumeSingleWell(resource, i, j, well.volume - pattern[i][j]);
    }
  }

  return null;
}

function dispense96(resource, pattern) {
  // Validate there is enough liquid available, that it fits in the well, and that each channel
  // has a tip before dispense.
  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      if (pattern[i][j] > 0) {
        const well = wells[resource.name][i][j];
        if (CoRe96Head[i][j].volume < pattern[i][j]) {
          return `Not enough volume in head: ${CoRe96Head[i][j].volume}uL.`;
        }
        if (well.volume + pattern[i][j] > well.maxVolume) {
          return `Dispensed volume (${pattern[i][j]}uL) + volume of well (${well.volume}uL) > maximal volume of well (${well.maxVolume}uL).`;
        }
        if (!CoRe96Head[i][j].has_tip) {
          return `CoRe 96 head channel (${i},${j}) does not have a tip.`;
        }
      }
    }
  }

  for (let i = 0; i < pattern.length; i++) {
    for (let j = 0; j < pattern[i].length; j++) {
      const well = wells[resource.name][i][j];
      CoRe96Head[i][j].volume -= pattern[i][j];
      adjustVolumeSingleWell(resource, i, j, well.volume + pattern[i][j]);
    }
  }

  return null;
}

function handleEvent(event, data) {
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
      drawResource(resource);
      break;

    case "resource_unassigned":
      removeResource(data.resource_name);
      break;

    case "pickup_tips":
      ret.error = pickUpTips(resource, data.channels);
      break;

    case "discard_tips":
      ret.error = discardTips(resource, data.channels);
      break;

    case "edit_tips":
      ret.error = editTips(resource, data.pattern);
      break;

    case "adjust_well_volume":
      ret.error = adjustVolume(resource, data.pattern);
      break;

    case "aspirate":
      ret.error = aspirate(resource, data.channels);
      break;

    case "dispense":
      ret.error = dispense(resource, data.channels);
      break;

    case "pickup_tips96":
      ret.error = pickupTips96(resource);
      break;

    case "discard_tips96":
      ret.error = discardTips96(resource);
      break;

    case "aspirate96":
      ret.error = aspirate96(resource, data.pattern);
      break;

    case "dispense96":
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

function drawRails() {
  for (var i = 1; i < numRails; i++) {
    const x = (canvasWidth / numRails) * i;
    const line = new Konva.Line({
      points: [x, 0, x, canvasHeight],
      stroke: "black",
      strokeWidth: 1,
    });
    layer.add(line);
  }
}

function drawTooltip() {
  tooltip = new Konva.Label({
    x: 0,
    y: 0,
    opacity: 1,
    visible: false,
  });

  tooltip.add(
    new Konva.Tag({
      fill: "yellow",
    })
  );

  tooltip.add(
    new Konva.Text({
      text: "",
      fontFamily: "Arial",
      fontSize: 14,
      padding: 5,
      fill: "black",
    })
  );

  tooltipLayer.add(tooltip);
}

window.addEventListener("load", function () {
  const canvas = document.getElementById("kanvas");
  canvasWidth = canvas.offsetWidth;
  canvasHeight = canvas.offsetHeight;

  stage = new Konva.Stage({
    container: "kanvas",
    width: canvasWidth,
    height: canvasHeight,

    // VENUS & robot coordinates have the origin in the bottom left corner.
    scaleY: -1,
    offsetY: canvasHeight,
  });

  drawRails();
  drawTooltip();

  // add the layer to the stage
  stage.add(layer);
  stage.add(resourceLayer);
  stage.add(tooltipLayer);
  tooltipLayer.scaleY(-1);
  tooltipLayer.offsetY(canvasHeight);
  updateStatusLabel("disconnected");

  openSocket();
});
