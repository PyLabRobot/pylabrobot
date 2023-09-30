mode = MODE_SIMULATOR;

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

function adjustLiquids(pattern) {
  for (let i = 0; i < pattern.length; i++) {
    const { well_name, liquids } = pattern[i];
    const wellInstance = resources[well_name];
    wellInstance.setLiquids(liquids);
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
    well.aspirate(volume);

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
    well.dispense(volume);

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
      well.aspirate(aspiration.volume);
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
      well.dispense(dispense.volume);
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
      resource = loadResource(data.resource);
      resource.draw(resourceLayer);

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

    case "update_state":
      state = data.state
      state_keys = Object.keys(state)
      for (let i = 0; i < state_keys.length; i++) {
        resource = resources[state_keys[i]]
        
        if (resource instanceof Container){
          startingState = resource.liquids
          resource.liquids = state[state_keys[i]].pending_tip != null
          if (resource.liquids!=startingState){
            resource.draw(resourceLayer)
          }
        }
        else if (resource instanceof TipSpot){
          startingState = resource.has_tip
          resource.has_tip = state[state_keys[i]].pending_tip != null
          console.log(resource.has_tip)
          if (resource.has_tip!=startingState){
            resource.draw(resourceLayer)
          }
        }
      }
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

    case "adjust_well_liquids":
      ret.error = adjustLiquids(data.pattern);
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

    case "pong":
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

window.addEventListener("load", function () {
  updateStatusLabel("disconnected");

  openSocket();

  loadSettings();
});
