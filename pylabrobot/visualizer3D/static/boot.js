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
// A renderer that never answers is the failure with no error: on some GPUs the adapter or the
// first context never resolves, and the page said "Starting..." for good.
const STALL_MS = 20000;
const SOFTWARE_RENDERERS = /swiftshader|llvmpipe|softpipe|software|microsoft basic render/i;
// The newest thing the page relies on is the import map that names three: the browsers that
// first read one. The inline guard in index.html names the same versions.
const NEEDS = "Chrome 89, Firefox 108 or Safari 16.4 or newer";

window.__plrBooted = true; // the inline guard in index.html checks this

/** Whether the browser reads import maps; null where it cannot say, and the import itself will. */
const importMaps = HTMLScriptElement.supports?.("importmap") ?? null;

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

/** Whether the viewer that served this page still serves it: a script that ends stops it. */
async function pageStillServed() {
  try {
    await fetch("./index.html", { method: "HEAD", cache: "no-store" });
    return true;
  } catch {
    return false;
  }
}

// A run that ends stops its viewer, and a page still loading then fails to import its own files.
const STOPPED_WHILE_LOADING = {
  title: "The viewer stopped while this page loaded",
  checks: [[false, "the viewer that served this page no longer answers"]],
  hint:
    "Its run has ended: a script stops its viewer when it finishes. Keep the script running, or " +
    "call viewer.wait_for_browser() before its run, then open the link it prints.",
};

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

/** The rows every graphics diagnosis opens with: what the browser offers, as probed. */
function gpuChecks(webgpu, webgl, note = "") {
  return [
    [webgpu, webgpu ? "WebGPU available" : "WebGPU unavailable"],
    [
      webgl.webgl2 && !note,
      webgl.webgl2
        ? `WebGL2 available: ${webgl.renderer}${webgl.software ? " (software, slow)" : ""}${note}`
        : "WebGL2 unavailable",
    ],
  ];
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
  if (!window.plrToken) {
    return (
      "This page was opened without its key: use the link the viewer printed, which ends in " +
      "#token=..."
    );
  }
  const { protocol, host } = new URL(window.WS_URL);
  return (
    "The page was served, but the viewer's websocket at " +
    `${protocol}//${host} does not answer. Over SSH, tunnel both ports - ` +
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

// app.js has stopped trying: nothing has answered for a minute, and it says so once rather than
// leaving a status dot to pulse and a hint about tunnelling ports that were never the problem.
window.addEventListener("plr:gone", () => {
  showDiagnosis({
    title: "This viewer has stopped",
    checks: [
      [true, "renderer started"],
      [false, "the viewer that served this page is no longer answering"],
    ],
    hint:
      "Its Python process has ended or its kernel was restarted. A new run serves a new page with " +
      "a key of its own, so this one cannot reconnect to it: use the URL the new run printed. " +
      "Reload only if the same viewer is being started again.",
    dismissible: true,
  });
});

// The server's scene is from another protocol than this page: its Python is older than the files
// it serves, which happens when the package is updated under a running kernel.
window.addEventListener("plr:mismatch", () => {
  showDiagnosis({
    title: "The viewer is older than this page",
    checks: [
      [true, "renderer started"],
      [false, "the viewer's Python speaks another protocol than this page"],
    ],
    hint:
      "The Python process serving this page was started before the viewer's files changed on " +
      "disk. Restart the kernel or the script, then reload this page.",
  });
});

let started = false;
// Said once, and taken down by the first status change if the renderer comes round after all.
const stall = setTimeout(async () => {
  const webgpu = await probeWebGPU();
  setStatus("Renderer stuck");
  showDiagnosis({
    title: "The renderer has not started",
    checks: [
      ...gpuChecks(webgpu, webgl),
      [false, `no renderer after ${STALL_MS / 1000} s: the browser is waiting on its GPU driver`],
    ],
    hint: browserHint(),
    dismissible: true,
  });
}, STALL_MS);
try {
  await import("./app.js");
  started = true;
} catch (error) {
  if (!(await pageStillServed())) {
    setStatus("Viewer stopped");
    showDiagnosis(STOPPED_WHILE_LOADING);
  } else {
    const webgpu = await probeWebGPU();
    const tooOld = importMaps === false;
    const noGPU = !webgpu && !webgl.webgl2;
    // renderer.js hangs its canvas in the viewport once the renderer has started. WebGL2 offered
    // to the probe and then not drawn with is a driver the browser should not be trusting.
    const drew = !!document.querySelector("#viewport canvas");
    const reason = tooOld
      ? `this browser has no import maps: the viewer needs ${NEEDS}`
      : noGPU
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
    const checks = [
      ...gpuChecks(webgpu, webgl, drew || tooOld ? "" : ", but the renderer could not use it"),
      [reachable, reachable ? "server reachable" : "server not reachable"],
    ];
    if (tooOld) checks.unshift([false, `no import maps: the viewer needs ${NEEDS}`]);
    showDiagnosis({
      title: tooOld
        ? "This browser is too old for the viewer"
        : noGPU
          ? "No 3D graphics in this browser"
          : "The viewer could not start",
      checks,
      hint: tooOld
        ? "Update the browser, or open this page in a current one."
        : noGPU || (reachable && !drew)
          ? browserHint()
          : reachable
            ? null
            : websocketHint(),
      detail: noGPU || tooOld ? null : reason,
    });
  }
  console.error(error);
}
clearTimeout(stall);

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
