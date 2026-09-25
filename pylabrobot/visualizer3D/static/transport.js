// The websocket to the viewer: its status, reconnection, and where each message goes.

import { invalidate, qualityNow } from "./frame.js";

// What the hello says the page draws with, and who takes each kind of message. Given once by the
// page: `connecting` as every new socket is made, then `scene`, `state` and `moves` as they arrive.

let renderer = null;
let handlers = {};

export function initTransport(deps) {
  ({ renderer, handlers } = deps);
}

const statusDot = document.getElementById("status-indicator");
const statusLabel = document.getElementById("status-label");

// The status is only ever as fresh as the last time this tab ran. A backgrounded tab gets frozen,
// so neither the close handler nor the reconnect timer fires, and it goes on painting whatever it
// last said. Keep a handle on the socket and re-read its real state whenever the tab comes back.
let socket = null;

const RECONNECT_MS = 1500;
// A minute of refused attempts says the viewer is gone, not busy: its kernel was restarted, or the
// run that served this page has ended. A new run serves a new page with a key of its own.
const GIVE_UP_MS = 60000;
let lostAt = null;

function showStatus(connected) {
  for (const el of [statusDot, statusLabel]) {
    el.classList.toggle("connected", connected);
    el.classList.toggle("disconnected", !connected);
  }
  statusLabel.textContent = connected ? "Connected" : "Disconnected";
  window.dispatchEvent(new CustomEvent("plr:status", { detail: { connected } }));
}

/** Tell the server what this page draws with, so a slow or odd viewer is visible from Python. */
function sayHello() {
  const probe = window.plrCapability ?? {};
  const webgpu = !!renderer.backend?.isWebGPUBackend;
  socket.send(
    JSON.stringify({
      event: "hello",
      data: {
        backend: webgpu ? "WebGPU" : "WebGL2",
        renderer: webgpu ? null : probe.renderer,
        software: webgpu ? false : !!probe.software,
        quality: qualityNow(),
        userAgent: navigator.userAgent,
      },
    }),
  );
}

export function connect() {
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  socket = new WebSocket(window.WS_URL);
  handlers.connecting();
  socket.onopen = () => {
    lostAt = null;
    showStatus(true);
    sayHello();
  };
  socket.onclose = () => {
    showStatus(false);
    lostAt ??= performance.now();
    if (performance.now() - lostAt < GIVE_UP_MS) setTimeout(connect, RECONNECT_MS);
    else window.dispatchEvent(new CustomEvent("plr:gone"));
  };
  socket.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      console.warn("a message from the viewer was not JSON, and was ignored");
      return;
    }
    const { event: kind, data } = message;
    if (!data) return;
    // Everything the server says changes what is on screen: the scene it draws, or the state it
    // draws it in. There is no message that only touches the panels around the viewport.
    invalidate();
    if (kind === "scene") handlers.scene(data);
    else if (kind === "state") handlers.state(data);
    else if (kind === "moves") handlers.moves(data.moves);
  };
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  const live = socket && socket.readyState === WebSocket.OPEN;
  showStatus(!!live);
  if (live) return;
  lostAt = null; // a tab coming back gets its minute again
  connect();
});

statusDot.addEventListener("click", connect);
