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

// Initialize pipetting heads.
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
}

function adjustResourceLiquids(liquids, resource_name) {
  const resource = resources[resource_name];
  resource.setLiquids(liquids);
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
}

function hideSetupInstruction() {
  let noRootResourceInfo = document.getElementById("setup-instruction");
  noRootResourceInfo.style.display = "none";
}

async function handleEvent(id, event, data) {
  if (event === "ready") {
    return; // don't parse response.
  }

  if (event === "pong") {
    return; // don't respond to pongs.
  }

  const ret = {
    event: event,
    id: id,
  };

  console.log("[event] " + event, data);

  // send event to appropriate device
  let deviceName = data.device_name;
  if (deviceName === null) {
    // ...
  } else {
    // ...
    let device = devices[deviceName];
    // ...
  }

  switch (event) {
    case "set_root_resource":
      resource = loadResource(data.resource);

      // the code for setting up a deck thingy should be move into a new LiquidHandler resource.
      if (data.resource.type === "LiquidHandler") {
        // infer the system from the deck.
        let deck = data.resource.children[0];
        mainHead = []; // reset the mainHead

        if (deck.type === "OTDeck") {
          system = SYSTEM_OPENTRONS;
          // Just one channel for Opentrons right now. Should create a UI to select the config.
          mainHead.push(new PipettingChannel("Channel: 1"));
        } else if (["HamiltonSTARDeck", "HamiltonDeck"].includes(deck.type)) {
          system = SYSTEM_HAMILTON;
          for (let i = 0; i < 8; i++) {
            mainHead.push(new PipettingChannel(`Channel: ${i + 1}`));
          }
        } else {
          let errorString = `Unknown deck type: ${deck.type}. Supported deck types: OTDeck, HamiltonSTARDeck, HamiltonDeck.`;
          alert(errorString);
          throw new Error(errorString);
        }
      }

      // TODO: instantiate a new LiquidHandler
      let lh = LiquidHandler(data.resource);

      hideSetupInstruction();

      resource.location = { x: 0, y: 0, z: 0 };
      resource.draw(resourceLayer);

      // center the root resource on the stage.
      let centerXOffset = (stage.width() - resource.size_x) / 2;
      let centerYOffset = (stage.height() - resource.size_y) / 2;
      stage.x(centerXOffset);
      stage.y(-centerYOffset);

      break;

    case "resource_assigned":
      resource = loadResource(data.resource);
      resource.draw(resourceLayer);
      break;

    case "resource_unassigned":
      removeResource(data.resource_name);
      break;

    case "pick_up_tips":
      await sleep(config.pip_tip_pickup_duration);
      try {
        pickUpTips(data.channels);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "drop_tips":
      await sleep(config.pip_tip_drop_duration);
      try {
        dropTips(data.channels);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "edit_tips":
      try {
        editTips(data.pattern);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "adjust_well_liquids":
      try {
        adjustLiquids(data.pattern);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "adjust_container_liquids":
      try {
        adjustResourceLiquids(data.liquids, data.resource_name);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "aspirate":
      await sleep(config.pip_aspiration_duration);
      try {
        aspirate(data.channels);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "dispense":
      await sleep(config.pip_dispense_duration);
      try {
        dispense(data.channels);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "pick_up_tips96":
      await sleep(config.core_tip_pickup_duration);
      try {
        pickupTips96(data.resource_name);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "drop_tips96":
      await sleep(config.core_tip_drop_duration);
      ret.error = dropTips96(data.resource_name);
      break;

    case "aspirate96":
      await sleep(config.core_aspiration_duration);
      try {
        aspirate96(data.aspiration);
      } catch (e) {
        ret.error = e.message;
      }
      break;

    case "dispense96":
      await sleep(config.core_dispense_duration);
      try {
        dispense96(data.dispense);
      } catch (e) {
        ret.error = e.message;
      }
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
  let wsHostInput = document.querySelector(`input[id="ws_host"]`);
  let wsPortInput = document.querySelector(`input[id="ws_port"]`);
  let wsHost = wsHostInput.value;
  let wsPort = wsPortInput.value;
  webSocket = new WebSocket(`ws://${wsHost}:${wsPort}/`);

  webSocket.onopen = function (event) {
    console.log("Connected to " + event.target.url);
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
    handleEvent(data.id, data.event, data.data);
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
