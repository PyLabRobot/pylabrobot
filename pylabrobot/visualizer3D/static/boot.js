// Starts the viewer, and says why when it cannot.
//
// app.js needs a GPU context before it does anything else: it creates its renderer at module level,
// so a browser without WebGPU or WebGL2 stops it before it connects, builds the tree or changes the
// status label. That used to leave "Loading..." on screen forever, with the reason only in the
// devtools console, and a websocket that cannot be reached (one tunnelled port of two, a port that
// moved) looked exactly the same. This file imports nothing that can fail to resolve, loads app.js
// behind a catch, and turns each of those failures into a diagnosis on the page - and, where the
// server can be reached, one in the Python output too.

const statusLabel = document.getElementById("status-label");
// Long enough for a software renderer on a slow machine to build a large scene; any later and a
// person has already decided the page is broken.
const CONNECT_GRACE_MS = 8000;
const SOFTWARE_RENDERERS = /swiftshader|llvmpipe|softpipe|software|microsoft basic render/i;

window.__plrBooted = true; // the inline guard in index.html checks this

/** What the browser offers, measured once and shared with app.js for the hello it sends. */
function probeWebGL2() {
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl2");
  if (!gl) return { webgl2: false, renderer: null, software: false };
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = String(
    debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
  );
  gl.getExtension("WEBGL_lose_context")?.loseContext();
  return { webgl2: true, renderer, software: SOFTWARE_RENDERERS.test(renderer) };
}

async function probeWebGPU() {
  if (!navigator.gpu) return false;
  const timeout = new Promise((resolve) => setTimeout(() => resolve(null), 3000));
  try {
    return !!(await Promise.race([navigator.gpu.requestAdapter(), timeout]));
  } catch {
    return false;
  }
}

/** Whether the server answers, told apart from whether the page could draw. */
function probeWebsocket(hello) {
  return new Promise((resolve) => {
    let socket;
    try {
      socket = new WebSocket(window.WS_URL);
    } catch {
      resolve(false);
      return;
    }
    const timer = setTimeout(() => {
      socket.close();
      resolve(false);
    }, 4000);
    socket.onopen = () => {
      clearTimeout(timer);
      socket.send(JSON.stringify({ event: "hello", data: hello }));
      socket.close();
      resolve(true);
    };
    socket.onerror = () => {
      clearTimeout(timer);
      resolve(false);
    };
  });
}

function browserHint() {
  const ua = navigator.userAgent;
  if (/firefox/i.test(ua)) {
    return (
      "Firefox: open about:support and look at Graphics > WebGL 2 Driver Renderer. If WebGL is " +
      "blocklisted, set webgl.force-enabled = true in about:config. On NVIDIA Jetson also set " +
      "gfx.x11-egl.force-enabled = true and start Firefox with " +
      "__EGL_VENDOR_LIBRARY_FILENAMES=/usr/lib/aarch64-linux-gnu/tegra-egl/nvidia.json."
    );
  }
  if (/chrome|chromium|edg/i.test(ua)) {
    return (
      "Chrome/Chromium: open chrome://gpu and check WebGL2. For software rendering (slow) start " +
      "the browser with --enable-unsafe-swiftshader. A snap-packaged Chromium may not be able to " +
      "reach the GPU driver at all."
    );
  }
  if (/safari/i.test(ua)) return "Safari: enable WebGL 2.0 under Develop > Feature Flags.";
  return "Use a current Firefox or Chrome with hardware acceleration enabled.";
}

function setStatus(text) {
  statusLabel.textContent = text;
  statusLabel.classList.remove("connected");
  statusLabel.classList.add("disconnected");
}

/** One panel for every reason the viewer is not showing anything. Rebuilt, never appended to. */
function showDiagnosis({ title, checks, hint, detail, dismissible }) {
  document.getElementById("boot-diagnosis")?.remove();
  const panel = document.createElement("section");
  panel.id = "boot-diagnosis";
  panel.setAttribute("role", "alert");
  const heading = document.createElement("h2");
  heading.textContent = title;
  panel.append(heading);
  const list = document.createElement("ul");
  for (const [ok, text] of checks) {
    const item = document.createElement("li");
    item.className = ok ? "ok" : "fail";
    item.textContent = text;
    list.append(item);
  }
  panel.append(list);
  for (const text of [hint, detail]) {
    if (!text) continue;
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    panel.append(paragraph);
  }
  if (dismissible) {
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Dismiss";
    close.addEventListener("click", () => panel.remove());
    panel.append(close);
  }
  document.body.append(panel);
}

function websocketHint() {
  return (
    "The page was served, but the viewer's websocket at " +
    `ws://${location.hostname}:${window.WS_PORT} does not answer. Over SSH, tunnel both ports - ` +
    "this page's and the websocket's. If the viewer was restarted, its ports may have moved: use " +
    "the URL it printed. Reload once the server is up."
  );
}

// ---------------------------------------------------------------- boot

const webgl = probeWebGL2();
window.plrCapability = webgl;
setStatus("Starting...");

// Listening before app.js loads, so no status change can come and go unheard.
let connected = false;
window.addEventListener("plr:status", (event) => {
  connected = event.detail.connected;
  if (connected) document.getElementById("boot-diagnosis")?.remove();
});

let started = false;
try {
  await import("./app.js");
  started = true;
} catch (error) {
  const webgpu = await probeWebGPU();
  const noGPU = !webgpu && !webgl.webgl2;
  const reason = noGPU
    ? "this browser offers neither WebGPU nor WebGL2"
    : `${error?.name ?? "Error"}: ${error?.message ?? error}`;
  const reachable = await probeWebsocket({
    backend: null,
    renderer: webgl.renderer,
    software: webgl.software,
    error: reason,
    userAgent: navigator.userAgent,
  });
  setStatus("Viewer failed");
  showDiagnosis({
    title: noGPU ? "No 3D graphics in this browser" : "The viewer could not start",
    checks: [
      [webgpu, webgpu ? "WebGPU available" : "WebGPU unavailable"],
      [
        webgl.webgl2,
        webgl.webgl2
          ? `WebGL2 available: ${webgl.renderer}${webgl.software ? " (software, slow)" : ""}`
          : "WebGL2 unavailable",
      ],
      [reachable, reachable ? "server reachable" : "server not reachable"],
    ],
    hint: noGPU ? browserHint() : reachable ? null : websocketHint(),
    detail: noGPU ? null : reason,
  });
  console.error(error);
}

if (started) {
  setTimeout(() => {
    if (connected) return;
    showDiagnosis({
      title: "Waiting for the server",
      checks: [
        [true, `renderer started${webgl.software ? " (software, slow)" : ""}`],
        [false, "server not reachable"],
      ],
      hint: websocketHint(),
      dismissible: true,
    });
  }, CONNECT_GRACE_MS);
}
