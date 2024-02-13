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

let devices = {};

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

function setRootResource(data) {
  resource = loadResource(data.resource);

  hideSetupInstruction();

  resource.location = { x: 0, y: 0, z: 0 };
  resource.draw(resourceLayer);

  // center the root resource on the stage.
  let centerXOffset = (stage.width() - resource.size_x) / 2;
  let centerYOffset = (stage.height() - resource.size_y) / 2;
  stage.x(centerXOffset);
  stage.y(-centerYOffset);
}

function removeResource(resourceName) {
  let resource = resources[resourceName];
  resource.destroy();
}

function addDevice(deviceName) {
  let device = resources[deviceName];
  devices[deviceName] = device;
}

function removeDevice(deviceName) {
  delete devices[deviceName];
}

async function processCentralEvent(event, data) {
  switch (event) {
    case "set_root_resource":
      setRootResource(data);
      break;

    case "resource_assigned":
      resource = loadResource(data.resource);
      resource.draw(resourceLayer);
      break;

    case "resource_unassigned":
      removeResource(data.resource.name);
      break;

    case "edit_tips":
      editTips(data.pattern);
      break;

    case "adjust_well_liquids":
      adjustLiquids(data.pattern);
      break;

    case "adjust_container_liquids":
      adjustResourceLiquids(data.liquids, data.resource_name);
      break;

    case "add_device":
      addDevice(data.device_name);
      break;

    case "remove_device":
      removeDevice(data.device_name);
      break;

    default:
      throw new Error(`Unknown event: ${event}`);
  }
}

async function handleEvent(id, event, data, deviceName) {
  // If data.device_name corresponds to a device, then send the event to that device.
  // If it doesn't correspond to a device, then raise an error.
  // If data.device_name is null, then the event is processed centrally (in this function).
  // In all cases, this function is responsibly for sending a response back to the server.

  if (event === "ready") {
    return; // don't parse response.
  }

  if (event === "pong") {
    return; // don't parse pongs.
  }

  console.log("[event] " + event, data);

  const ret = {
    event: event,
    id: id,
  };

  // Actually process the event.
  try {
    if (deviceName === null) {
      await processCentralEvent(event, data);
    } else {
      let device = devices[deviceName];
      await device.processEvent(event, data);
    }
  } catch (e) {
    console.error(e);
    ret.error = e.message;
  }

  // Set the `success` field based on whether there was an error.
  if (ret.error === undefined || ret.error === null) {
    ret.success = true;
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
    handleEvent(data.id, data.event, data.data, data.device_name);
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
