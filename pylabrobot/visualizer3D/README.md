# plr_viz3d

A parallel PyLabRobot visualizer whose world is a resource, not a liquid handler. Prototype only:
no tests, no packaging, no upstream wiring. It exists to check that the three claims in the
architecture note hold against real v1 code.

## Running it

Needs the v1 STAR branch on the path and `websockets`.

```
python -m plr_viz3d.demo
```

It opens a browser on `http://127.0.0.1:1338`. Append `?view=top` for a top view. From the console,
`plrViewer.focus("destination_0")` frames and selects a named resource.

## What it demonstrates

**Any resource is the world.** `Viewer3D(root)` takes a `Resource`. The demo's world is a plain
`Resource` holding a simulated `STARDevice` at one coordinate and a bench at another. The bench has
no deck, no driver and no device of any kind; it holds a plate and a tip rack, and renders exactly
like everything else. Nothing in the client switches on a resource type: geometry comes from the
model's own `cross_section_type` and sizes, colour from its `category`, structure from the parent
array, and the inspector prints whatever fields arrived.

**Prototype and instance, measured.** On the demo facility:

| | value |
|---|---|
| instances | 1,490 |
| distinct models | 18 |
| tree JSON, as the current visualizer sends it | 847.3 kB |
| models plus a packed instance array | 100.1 kB |
| ratio | 8.46x |
| draw calls | 24 to 34 |

A single 96-well plate goes from 59,870 to 8,261 bytes, 7.25x. Transforms ride as base64
little-endian float32, six per instance.

Getting there needed one correction worth keeping. Splitting on `Resource.serialize()` alone gives
421 models for 1,490 instances, because identity leaks below the top level: a tip spot carries the
name of its prototype tip, and a plate carries a map of identifier to the name of the well it
holds. Two general rules fix it, both in `scene.py`: a key called `name` at any depth names one
particular thing, and inside a nested structure a string matching a resource in this tree is a link
to it. The rule deliberately stops at the top level, or a deck called "deck" loses its category.

**One channel, not two.** Everything the viewer draws arrives as tracking state: a well's volume and
the position of anything that travels. A moving part reaches the picture
because `Resource.location` publishes when it is set, so an X-arm read off the device is on the
same path as a well being filled.

A polling reader sat beside that for a while, asking a device where its arm was five times a second
and labelling each number `measured`, `derived` or `unavailable`. It was a second communication
channel for a position state already carried, so it is gone. What is lost with it is the labelling
of what v1 does not publish - per-channel X, Y and Z have no live readout, and saying so in the
interface is worth doing again once state itself can carry that distinction.

## What it is not

- No WebGPU verification. The headless browser used to check it has no adapter, so it exercised the
  WebGL2 fallback path. Frame rates seen there are software rendering and mean nothing; draw calls
  are the figure that transfers.
- Liquid is driven from tracker state only. The v1 STAR has no aspirate or dispense yet, so the
  demo moves trackers directly. A real command would move the same trackers. Tips are not drawn
  from tracker state at all: a tip is drawn when it stands in the tree as a resource.
- Picking is `InstancedMesh` raycasting, which is fine at this size. GPU picking is the production
  answer.
- GIF recording only works on the WebGPU backend. three's WebGL2 backend cannot read a render
  target back (r180), so on the fallback path the control disables itself and says why rather than
  producing an empty file. Untested on WebGPU here, since the browser used to check it has no
  adapter. It captures the viewport only, not the floating panels over it.
- No GUI editing mode.

## Design fault: the renderer gates everything

**Symptom.** The page stays on "Loading..." forever. No error shows on the page, and nothing connects to the
websocket port.

**Why.** `app.js` runs `await renderer.init()` as the module loads. If the browser has neither WebGPU nor WebGL2,
that throws, the module stops, and `connect()` never runs. The tree, search and inspector never appear either,
even though none of them need a GPU. The same symptom also comes from a websocket that can't be reached
(a tunnel with only one port, or a port that moved up on collision), so the two causes look identical.

**Where it breaks.** WebGL2 is often missing even on capable hardware: the browser blocklists the GPU, a
sandbox can't reach the driver, or the session has no GPU (VMs, some remote desktops, CI). Chrome also no longer
falls back to software rendering on its own. The browser tests don't catch any of this. They are
headless (always software rendering), and they skip entirely off macOS
(`CHROME` is a macOS path).

**On the Jetson AGX Orin workcell host** (JetPack 5, Xorg, no `/dev/dri`), verified 2026-09-16:

| browser | default | fix |
|---|---|---|
| Chromium (snap) | no WebGL: EGL can't start inside the snap | `--enable-unsafe-swiftshader`: software only, the snap can't reach the GPU |
| Firefox (apt) | no WebGL: blocklisted, because `glxtest: drmGetDevices2 failed` | hardware WebGL2 ("NVIDIA Tegra Orin"), but only with all three settings below |

```
__EGL_VENDOR_LIBRARY_FILENAMES=/usr/lib/aarch64-linux-gnu/tegra-egl/nvidia.json firefox
# about:config  gfx.x11-egl.force-enabled = true   webgl.force-enabled = true
```

Leave out the environment variable and glvnd picks Mesa's EGL, so Firefox renders on the CPU (llvmpipe). WebGPU is never available.

Measured with 1,165 instances (six plates, six tip racks) at 1920x1080, dragging the camera:

| browser | scene ready | frame rate while orbiting |
|---|---|---|
| Firefox, recipe above (Orin GPU) | 3.4 s | 40-60 fps |
| Firefox, no env var (llvmpipe) | 11.3 s | 1-4 fps |
| Chromium snap, `--enable-unsafe-swiftshader` | 4 s | about one frame per 3 s |

To make the recipe permanent, put the two prefs in a dedicated profile's `user.js` and set the variable in a small
launcher that backgrounds Firefox (`webbrowser.open` waits for the command to exit):

```sh
#!/bin/sh
export __EGL_VENDOR_LIBRARY_FILENAMES=/usr/lib/aarch64-linux-gnu/tegra-egl/nvidia.json
setsid firefox --profile "$HOME/.mozilla/firefox-gpu" "$@" </dev/null >/dev/null 2>&1 &
```

Then `export BROWSER=<that launcher>` in `~/.profile`, so `Viewer3D` opens it. A Firefox that is already running
without the variable renders on the CPU, and the URL opens in it, so start it through the launcher.

**Better design, ship in this order.** This came out of an adversarial review. The server-side items were checked against the code.

1. Make the browser tests find Chrome on Linux (env var or `shutil.which`). Add runs with no GPU, and with the
   page served but the websocket port closed.
2. Construct `WebGPURenderer` with `forceWebGL: true`, and make WebGPU opt-in (`?backend=webgpu`). At 24–34 draw
   calls WebGPU gains nothing and doubles the paths to test.
3. **Done.** Add a small `boot.js` that checks capability and websocket reachability, then `import("./app.js")` inside
   try/catch. A failure becomes an on-page diagnosis with a per-browser fix instead of "Loading...". The client
   sends its backend and renderer string over the websocket, and Python prints it, e.g.
   `viewer: a browser connected, drawing with WebGL2 on llvmpipe - software rendering, expect it to be slow`.
4. **Done.** Check the websocket `Origin` (and the HTTP `Host`) and require a per-run token baked into `index.html`. Browsers don't apply same-origin
   rules to websockets, so without this any web page open in the operator's browser could read the deck state.
5. Serve HTTP and the websocket on one port (websockets `process_request`), and connect with
   `new URL("ws", location)`. That leaves one tunnel, and it works behind a proxy or HTTPS. If the port is taken,
   fail loudly instead of moving up silently.
6. Capture GIF frames with `drawImage(renderer.domElement)` straight after `render`. `preserveDrawingBuffer` is
   already on, so this should work on both backends (untested).
7. Detect software renderers and switch to a low-cost mode: pixel ratio 1, no antialias, no PMREM environment.
8. Send broadcasts to all clients at once and drop slow ones. Today one slow remote viewer stalls updates for every client.

Rejected: a Canvas2D fallback renderer (a second renderer that would drift from the first) and a full
scene/renderer split (`world.js` already holds the scene state). Revisit classic `WebGLRenderer` only if the WebGL
backend of `WebGPURenderer` misbehaves.

## Interface

The shell follows the existing visualizer: its palette and metrics, the navbar with the source
name, the left tool rail (select, get-location, GIF), the facility tree
with per-item eye toggles, expand-all and show-to-depth, the search pane with its include filters,
the floating info panel, the scale bar and the resizable side panel.

Three things are new, because three dimensions asks for them: camera presets in the left rail
(ISO, TOP, FRT), an axis legend that turns with the view instead of a fixed x/y pair, and a Z
reference in the coordinate tool alongside X and Y.

## Layout

```
scene.py      flatten any resource tree into models and instances; measure the split
server.py     static files and a websocket, same two servers as today
demo.py       a facility with a STAR and a bench in it
static/       index.html, main.css, and the client modules below; vendored three.js and gif.js
              (no CDN, so it runs air-gapped)
```

The client is plain ES modules, loaded straight by the browser. There is no build step, and adding
one is not on the table: the page has to keep working from a checkout, offline.

```
static/app.js         the scene: geometry, camera, tree, info panel, interaction, transport
static/constants.js   the palette and the thresholds, as data
static/format.js      turning values into the text the info panel shows
static/dom.js         element lookups that say which kind of element is being asked for
static/coords.js      the get-location tool
static/gif.js         recording the viewport
```

## Type checking

The JavaScript is checked without being converted. `static/tsconfig.json` turns on `checkJs`, and
`static/types/vendor.d.ts` marks where our code stops and three's begins.

```
npx tsc --noEmit -p pylabrobot/visualizer3D/static
```

Editors that read `tsconfig.json` report the same findings inline, with no install. `strict` is
deliberately off: against untyped JavaScript it reports far more than anyone acts on, and buries
what matters.
