mode = MODE_VISUALIZER;

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

function setRootResource(data) {
  resource = loadResource(data.resource);

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

function setState(allStates) {
  for (let resourceName in allStates) {
    let state = allStates[resourceName];
    let resource = resources[resourceName];
    resource.setState(state);
  }
}

async function processCentralEvent(event, data) {
  switch (event) {
    case "set_root_resource":
      setRootResource(data);
      break;

    case "resource_assigned":
      resource = loadResource(data.resource);
      resource.draw(resourceLayer);
      setState(data.state);
      break;

    case "resource_unassigned":
      removeResource(data.resource_name);
      break;

    case "set_state":
      let allStates = data;
      setState(allStates);

      break;

    default:
      throw new Error(`Unknown event: ${event}`);
  }
}

async function handleEvent(id, event, data) {
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
    await processCentralEvent(event, data);
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
    handleEvent(data.id, data.event, data.data);
  });
}

function heartbeat() {
  if (!webSocket) return;
  if (webSocket.readyState !== WebSocket.OPEN) return;
  webSocket.send(JSON.stringify({ event: "ping" }));
  setTimeout(heartbeat, 5000);
}

window.addEventListener("load", function () {
  updateStatusLabel("disconnected");

  openSocket();
});
