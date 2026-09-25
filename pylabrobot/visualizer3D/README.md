# visualizer3D

A parallel PyLabRobot visualizer whose world is a resource, not a liquid handler. It exists to
check that the three claims in the architecture note hold against real v1 code. Its tests sit
beside it (`scene_tests.py`, `server_tests.py`, `client_tests.py`, `browser_tests.py`), its static
files ship with the package, and `pylabrobot.visualizer3D` exports `Viewer3D`.

## Running it

Needs `websockets` 14 or newer.

```
python -m pylabrobot.visualizer3D.demo
```

It opens a browser on `http://127.0.0.1:1338/#token=<token>`, the link it prints, in a top view;
`?view=iso` or `?view=front` (before the `#`) picks another, and `?quality=low` pins the low-cost
level. From the console, `plrViewer.focus("destination_0")` frames and selects a named resource.

A simulated run is over in milliseconds, and a script that ends stops its viewer with it. Call
`await viewer.wait_for_browser()` before the run, as `demo.py` does: it returns once a page says it
is drawing, so the page shows the run from the start.

`demo.py` sets the STAR deck's height to the top of the X-arm riding above it (334.7 mm of
channel travel plus the arm's own 140 mm). The deck's own `size_z` of 900 mm is the working
envelope from the instrument's configuration file, not the deck's extent: drawn as is, it
protrudes 75.5 mm through the roof of the device carrying it around 665 mm of empty space. The
viewer applies this itself rather than editing the resource library.

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
| instances | 3,276 |
| distinct models | 53 |
| tree JSON, as the current visualizer sends it | 1,758.4 kB |
| models plus a packed instance array | 338.3 kB |
| ratio | 5.2x |
| draw calls, WebGPU, by view | 888 to 945 |

A facility holding a single 96-well plate goes from 59,178 to 14,238 bytes, 4.16x. Transforms
ride as base64 little-endian float32, six per instance.

Getting there needed one correction worth keeping. Splitting on `Resource.serialize()` alone, with
only the top-level instance fields taken off, gives 1,127 models for 3,276 instances, because
identity leaks below the top level: a tip spot carries the
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

- No WebGPU verification in CI. The headless browser there draws in software, and software WebGPU
  loses its device within a second of drawing, so a browser that renders in software (`boot.js`'s
  probe) draws with WebGL2; WebGPU is exercised only on a machine with a GPU. Frame rates seen in
  software mean nothing; draw calls are the figure that transfers.
- Liquid is driven from tracker state only: the demo moves the volume trackers directly, and a
  real command moves the same trackers. Tips are not drawn from tracker state at all: a tip is
  drawn when it stands in the tree as a resource.
- Picking is `InstancedMesh` raycasting, which is fine at this size. GPU picking is the production
  answer.
- GIF recording captures the viewport only, not the floating panels over it. It records on both
  backends: the frame is rendered into a render target and read back, which three's WebGL2 backend
  does too.
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
headless (always software rendering), and they skip where `_find_chrome` finds no Chrome named by
`PLR_CHROME`, on the path or at the macOS install location.

**On the Jetson AGX Orin workcell host** (JetPack 5, Xorg, no `/dev/dri`):

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

1. **Done** for finding Chrome: `shutil.which` first, the macOS install path second. Still to add:
   runs with no GPU, and with the page served but the websocket port closed.
2. **Done where it breaks:** a browser that renders in software gets `forceWebGL`, since software
   WebGPU loses its device while drawing. Still open: WebGPU opt-in everywhere
   (`?backend=webgpu`), as at 24–34 draw calls it gains nothing and doubles the paths to test.
3. **Done.** Add a small `boot.js` that checks capability and websocket reachability, then `import("./app.js")` inside
   try/catch. A failure becomes an on-page diagnosis with a per-browser fix instead of "Loading...". The client
   sends its backend and renderer string over the websocket, and Python prints it, e.g.
   `viewer: a browser connected, drawing with WebGL2 on llvmpipe - software rendering, expect it to be slow`.
4. **Done.** Check the websocket `Origin` (and the HTTP `Host`) and require a per-run token,
   carried in the link's `#token=` fragment. Browsers don't apply same-origin
   rules to websockets, so without this any web page open in the operator's browser could read the deck state.
5. Serve HTTP and the websocket on one port (websockets `process_request`), and connect with
   `new URL("ws", location)`. That leaves one tunnel, and it works behind a proxy or HTTPS. If the port is taken,
   fail loudly instead of moving up silently.
6. **Done.** GIF frames are read back from a render target on both backends. `preserveDrawingBuffer`
   was never read by this three and is gone.
7. **Done**, by measurement rather than detection: `frame.js` steps the pixel ratio to 1 and then
   drops the environment while frames stay slow, and back up once they are fast; `?quality=low`
   pins the lowest level. Antialiasing is fixed when the renderer is made, so it is not a level.
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
static/boot.js        checks the browser can draw and reach the server before loading the page
static/app.js         the page: builds the scene from a message and wires the modules together
static/world.js       the tree as the page holds it: names, parents, models, placements
static/transport.js   the websocket: scene, state and move messages, reconnecting
static/frame.js       the frame loop: draws on request, adapts quality, reports its cost
static/renderer.js    the renderer, cameras, lights, controls and the view helper
static/drawn.js       what is drawn per resource, and the ownership registry that disposes it
static/boxes.js       every resource as a box, with outlines, cavities and discs
static/models.js      resources drawn from model files, with joints the state drives
static/appearance.js  detail level, view mode, opacity and paint order
static/marks.js       arms, grids, reference marks, origin, floor and channel halos
static/live.js        state, moves, glides and visibility applied to what is drawn
static/panel.js       the selection, its boxes and the info panel
static/tree.js        the resource tree, its search and its side panel
static/tools.js       panning, picking, the hover readout, the coordinate tool, the toolbar
static/coords.js      the get-location tool's measurements and reference dropdown
static/device_tools.js  what a device pipettes and grips with, listed from the tree
static/gif.js         recording the viewport
static/constants.js   the palette and the thresholds, as data
static/format.js      turning values into the text the info panel shows
static/dom.js         element lookups that say which kind of element is being asked for
```

Modules import downwards only: `app.js` imports the modules that draw and connect, and reaches
`coords.js`, `dom.js` and `format.js` through them; `tools.js` imports `tree.js` and `panel.js`.
Only `boot.js` imports `app.js`, dynamically, once it can say why the import failed. Within a
module a helper is declared above its callers.

## Protocol and model contract

Everything crosses one websocket as JSON, `{"event": kind, "data": ...}`. Non-finite floats are
written as the strings `"Infinity"`, `"-Infinity"` and `"NaN"` (`_finite` in `server.py`), since
a trough's capacity is genuinely infinite and bare `Infinity` is not JSON.

**Access.** Every run makes a token (`secrets.token_urlsafe(32)`) and hands it out only in the
link it prints and opens, `Viewer3D.url`, as the fragment `#token=<token>`. A browser never sends
a fragment, so the token is in no request, no log and no `Referer`, and anyone who can reach the
file server but was not given the link gets a page that cannot connect. The page keeps the token
in the tab's `sessionStorage`, so a reload reconnects, and takes the fragment off the address with
`history.replaceState`, so it is not bookmarked or shown on a shared screen. The page itself carries
only the websocket port and the source name, in place of `{{ ws_port }}` and
`{{ source_filename }}`; it connects to `ws://<hostname>:<ws_port>/?token=<token>`, or `wss:` when
it was served over https, at the hostname it was reached by. A
handshake without this run's token, or with an `Origin` whose hostname is not an IP literal,
`localhost`, this machine's name, `<name>.local`, the bound host or one of `allowed_hosts`, is
answered 403. The file server applies the same hostname rule to the HTTP `Host` header: DNS
rebinding, a hostile name resolved to 127.0.0.1, would make that name's page same-origin with the
viewer. That is why an unrecognised hostname is refused, and why any IP address is accepted: it
cannot be rebound, which is what keeps an SSH tunnel or a LAN address working without configuration.

**Cache headers.** `/`, `/index.html` and `/mesh/<id>` are `Cache-Control: no-store`, since the
page carries this run's websocket port and a mesh id belongs to this run. Everything else is
`no-cache`: revalidated and answered 304 when unchanged.

**Ports.** The websocket binds first, so the page can be told the port it actually got, then the
file server; the other way round, a viewer whose preferred port is taken would serve a page
pointing at the viewer that took it, and quietly show someone else's facility. Each walks up from its default (2122 and 1338) through at most `PORT_TRIES` (20)
ports while the bind fails with `EADDRINUSE`; any other error is raised.

### Messages

| kind | when | `data` |
|---|---|---|
| `scene` | the first message to a new client (the kept scene, not a rebuild); broadcast, with a full `state` after it, when a name appears in or disappears from the tree | `protocol`, `epoch`, `stats`, `models`, `instances` |
| `state` | a snapshot after every `scene`; a delta whenever a batch of tracker updates holds something that looks different from what was last sent | `epoch`, `states`, `of`, `locations` |
| `moves` | the tree changed shape but holds the same names: a reparent or a relocation, applied to the scene the page has | `epoch`, `moves` |
| `hello` | page to server, once per socket: what it draws with, or from `boot.js` why it could not; the first without an `error` ends `wait_for_browser` | `backend`, `renderer`, `software`, `quality`, `userAgent`, `error` |

**`scene`.** `protocol` is `PROTOCOL` in `server.py`, currently 1, and the page holds its own
copy in `constants.js`: on a mismatch `rebuildScene` draws nothing and raises `plr:mismatch`, and
`boot.js` shows "The viewer is older than this page". The two can drift apart because the page is
fetched fresh on every load while the Python side lives as long as its process. `epoch` counts rebuilds and rides on every
message; the page does not read it yet. `stats` is `{instances, models, legacy_bytes,
scene_bytes, ratio}`, where `legacy_bytes` is the old per-resource tree serialized once, on the
first build only, and the stats panel prints all five. `models` is the list of distinct models
(below). `instances` is `{names, model, parent, transforms}`: parallel arrays by instance index,
`model` an index into `models`, `parent` an instance index or -1 at the root, `transforms` base64
little-endian float32, six per instance (x, y, z in mm, then rotation x, y, z in degrees, relative
to the parent). Parents are emitted before their children, so `world.js` resolves world matrices
in one forward pass.

**`state`.** `collect_state` walks the tree and keeps every resource whose `serialize_state()`
still says something once cleaned: `name`, `thing` and `parent_name` dropped at any depth, an
identity `rotation` dropped, floats rounded to one decimal (`STATE_DECIMALS`: firmware reports
position to 0.1 mm, and a tenth of a microlitre is below anything a well can show). Rounding
rather than comparing against a threshold means two values that look the same produce one
signature, so one rule both suppresses a resend and shares one table entry. `pack_state` then
sends `states`, the table of distinct cleaned states, `of`, name to index into that table, and
`locations`, name to the `location` it published, kept out of the shared table because a position
is one resource's own. A snapshot carries everything but `location` (the scene just placed it),
except for a resource that moved after the scene was built (the kept scene is handed out
unchanged, so this snapshot is the only message that will ever correct its placement), and leaves
out a resource with nothing to say. A delta carries only names whose signature changed since they
were last sent, and keeps a name whose state cleaned down to nothing, because there it means "no
longer what I last said". Updates coalesce per event-loop turn into one message; while a rebuild
is pending, locations are held back so nothing is drawn relative to a parent the page does not
have yet. Addressed by name throughout: instance order is not stable across rebuilds, because
resources are created lazily during setup and reassigned by code that reorders a parent's
children, so an index that means one resource in one scene means another in the next.

**`moves`.** Each entry is `{name, parent, location: {x, y, z}, rotation: {x, y, z}}`, `parent` a
name or null, for every resource whose parent or local transform differs from the kept scene. A
burst of structural changes is debounced by `SCENE_DEBOUNCE_S` (50 ms) into one `moves` or one
rebuild: picking up ninety-six tips is one operation to a user and a hundred and ninety-two
callbacks to the server, and a scene per callback would be quadratic in the burst. A client
arriving is handed the kept scene rather than a rebuild, which would renumber everything under the
clients already watching. The page re-parents it in `world.childrenOf`, sets its local transform
and recomputes the subtree.

**Meshes.** `/mesh/<id>` serves a file the scene named, as `model/gltf-binary`, without the token;
`<id>` is sixteen random hex digits plus `.glb`, drawn once per file per run, so only a client
that was sent the scene knows it, and only registered ids are served. Only a `.glb` is registered,
and a `reference_glb` that resolves outside `models_root` (an absolute path, `..`, a symlink) is
drawn as a box.

### The model

`scene.py` derives a model from `Resource.serialize()` by removing `INSTANCE_FIELDS` (`name`,
`location`, `rotation`, `parent_name`, `children`); inside nested values every `name` and
`parent_name` key is dropped and a string equal to any resource name in the tree becomes
`"<resource>"`. `methods`, `grid`, `bands` and the `DECLARED_FIELDS` read straight off the resource
(`reference_point`, `window`, `mesh`, `reference_glb`, `appearance`) are added, and dicts that
compare equal within one `type` are interned to one model. The declared fields belong in the
resource's own serialization upstream; passing them through keeps the viewer free of any device's
constants meanwhile. `_register_meshes` then works on a
copy: `reference_glb` is popped and never reaches the page; a resource with neither `mesh` nor
`reference_glb` gets a `mesh` when a `<model>.glb` exists anywhere under the package (a `tip`
whose `model` ends in `_filter` falls back to the unfiltered file), so a resource ships its
geometry beside the code that describes it and nobody names a path, which model names namespaced
by manufacturer and device keep unambiguous in one flat index; and a `mesh`'s `path` is
replaced by `url`. A package `.glb` or a `reference_glb` is taken as metres, Z up
(`REFERENCE_GLB_UNITS`, `REFERENCE_GLB_UP`); a declared `mesh` states its own, and the page reads a
missing `units` as mm and a missing `up` as Y.

| field | produced from | read by | effect |
|---|---|---|---|
| `type` | `Resource.serialize` | `scene.py`, `panel.js`, `tree.js`, `tools.js` | the interning bucket; panel header, tree row, hover readout, search |
| `category` | `Resource.serialize` | most modules; `server.py` | colour (`RESOURCE_COLORS`) and everything in the tables below |
| `size_x`, `size_y`, `size_z` | `Resource.serialize` | `world.js` `sizeOf`, `device_tools.js` | the box, its outline, pick box and detail culling; a tip's drawn length and a held plate's proportions in the device tools |
| `diameter` | `tip.py`, `petri_dish.py` | `world.js` `sizeOf` | stands in for a zero `size_x`/`size_y` |
| `model` | `Resource.serialize` | `server.py`, `panel.js`, `tools.js` | names the `.glb` looked up under the package; panel and hover readout |
| `cross_section_type` | `well.py`, `petri_dish.py`, `n_channel_pipettes.py` | `boxes.js` | `"circle"` draws a cylinder, anything else a box |
| `max_volume` | `container.py`, `tube.py` | `boxes.js`, `drawn.js`, `live.js`, `panel.js` | finite and above 0: drawn as a vessel (rim, wall, cavity); present at all: an enclosure; the fill colour's denominator; "volume / max" in the panel |
| `material_z_thickness` | `container.py` | `drawn.js` | the `cavity_bottom` height in the coordinate tool |
| `ordering` | `itemized_resource.py` | `panel.js`, `device_tools.js` | item count; a held plate's rows and columns |
| `has_filter`, `collar_height` | `tip.py` | `boxes.js` | a filter disc `FILTER_BELOW_COLLAR` under a `tip`'s collar |
| `methods` | `scene.py` `_public_methods` | `panel.js` | the collapsed "Methods" list |
| `grid` | `grids.describe_grid`: a declared `position_grid` (a loading tray's markings line up with the deck it feeds, so nothing about itself could derive them), else `track_to_location` or `rail_to_location` with `num_tracks` or `num_rails`; `extent` is the deepest child seated on the line, else the resource's own depth, so a mark never overshoots into a reach that is not there | `marks.js` | rail marks from `origin` every `spacing`, `count` of them, `extent` deep, numbered every `label_every`; the deck surface; a `deck`'s working Z for reference marks. `axis` and `label` are not read |
| `bands` | `grids.describe_bands` from `access_bands` | `marks.js` | two lines per band at `from` and `to`, bounded by `x_from` and `x_to`; `label` is not drawn |
| `reference_point` | attribute, a `Coordinate` or a dict (`hamilton_decks.py`, `n_channel_pipettes.py`, `iswap.py`) | `marks.js` | `x`: where the reference line or mark sits, else half the width; `y_range`: an arm's line reach; `z`: the mark's height on a carried part |
| `window` | attribute (`demo.py`) | `marks.js`, `tools.js` | the opening in a moving part's frame: `width`, `right_margin`, `inset_y`, defaulting to `ARM_INSET_X`/`ARM_INSET_Y`; a click through it lands on what is below |
| `appearance` | attribute (`prep_decks.py`, prep `x_arm.py`) | `boxes.js`, `marks.js` | `color` overrides the category colour; `metalness` and `roughness` on a moving part's frame |
| `tool_center_point`, `proximal_joint` | `end_effector.py`, `manipulator.py` | `marks.js` | the grip crosshair at `proximal_joint + tool_center_point` on a `mechanical_gripper` |
| `mesh` | attribute, or `server.py` from `reference_glb` or a package `.glb` | `models.js`, `boxes.js` | `url` fetched once per model; `units` (`mm`, `cm`, `m`) scales; `up` (`Y` default, or `Z`) rotates; `joints` `{key: {node, axis, type}}` maps state `joints[key]` onto a node, `prismatic` in mm along `axis`, else degrees about it; a `tip`'s bore sizes its filter disc |
| anything else | subclass `serialize` | `panel.js` | listed under "Specifics", or "Construction" for `with_*` and `core_grippers`, with units from `UNITS` in `format.js` |

State keys the page reads: `rotation` (absent means zero), `location`, `volume`, `tracker.x`
(an arm's tracked X), `joints`, and a tip's `volume` and `max_volume` in the device tools and the
info panel, `max_volume` falling back to the model's `maximal_volume`. `volume` is the committed
volume, never `pending_volume`; a tracker publishes on a rollback too, so a failed operation
leaves the committed state as the last one sent. A tracker's `thing` is its resource's name, so
it is dropped before signing: a rack of empty tips is one state, not ninety-six. The panel prints
the rest under "Tracker state".

### Categories

The sets in `constants.js`, and `GROUND` in `drawn.js`:

| set | categories | where it matters |
|---|---|---|
| `MOVING_PARTS` | `x_arm`, `arm`, `gripper`, `head`, `channel` | drawn as an open frame with a reference line in a group of its own (`marks.js`); no box fill or generic outline (`boxes.js`); `MOVING_OPACITY`, and the carried layer in a plan (`appearance.js`); `tracker.x` and `locations` glide it (`live.js`); takes a click on its rim (`tools.js`); everything under it travels (`drawn.js`) |
| `PICKABLE_PARTS` | `pipette_channel` | a click selects it although it has children |
| `NO_REFERENCE_MARK` | `pipette_channel` | its `reference_point` gets no mark |
| `TREE_HIDDEN` | `well`, `tip_spot`, `tube` | no tree row; the parent's row counts them |
| `CONTENTS` | `well`, `tip_spot`, `tube`, `tip_mounting_shaft` | show-to-depth does not open a level made only of these |
| `HOLDERS` | `resource_holder`, `plate_holder`, `embedded_tip_rack_holder` | a numbered site: the tree shows what stands in it, or `<empty>`; counted through for "n plates"; the search "sites" filter |
| `SEARCH_CONTAINERS` | `well`, `tube`, `trough`, `container`, `petri_dish` | the search "Wells" filter |
| `CONTAINERS` | `plate`, `tip_rack`, `tube_rack`, `plate_adapter`, `plate_holder`, `resource_holder`, `trough`, `trash` | keep their walls at `SHELL_OPACITY` while their contents are drawn |
| `CATEGORY_OPACITY` | `tip_rack` 0.7, `plate` 0.25 | the box's own opacity |
| `RESOURCE_COLORS` | 31 categories and `default` | the box colour and the tree's dot |
| `GROUND` | `facility`, `deck` | `SPACE_OPACITY`, out of the depth buffer |

Categories named directly in the code:

| category | where it matters |
|---|---|
| any containing `carrier` (`isCarrier`, `drawn.js`) | a filled floor; walls kept while its contents are drawn |
| `tip_spot` | round, and a vessel without a `max_volume`; its cavity is hidden while it holds a tip; "n/m tips" in the tree; the search "tips" filter |
| `tip` | the filter disc and the green plan disc (`boxes.js`); the `_filter` file fallback (`server.py`) |
| `tip_mounting_shaft` | an open tube; a tip under it is what its channel holds (halos, device tools) |
| `pipette_channel` | a halo with its number; a column in the device tools |
| `head96`, `head384` | a head grid in the device tools |
| `mechanical_gripper` | the grip crosshair; the gripper figure in the device tools |
| `body`, `finger`, `pad` | a gripper's own parts, not its cargo; a `pad` sizes the crosshair |
| `device` | gets a device-tools button |
| `deck` | its `grid` sets the working Z for reference marks; the tree lists its children in its parent's row, sorted by X; a panel note on construction flags |
| `plate_adapter` | its tree row names what it carries |
| `well`, `tube` | "n wells" and "n tubes" in the parent's row |

## Type checking

The JavaScript is checked without being converted. `static/tsconfig.json` turns on `checkJs`, and
`static/types/vendor.d.ts` marks where our code stops and three's begins.

```
npx tsc --noEmit -p pylabrobot/visualizer3D/static
```

Editors that read `tsconfig.json` report the same findings inline, with no install. `strict` is
deliberately off: against untyped JavaScript it reports far more than anyone acts on, and buries
what matters.
