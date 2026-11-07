# Writing PyLabRobot Device Drivers & Hello-World Guides

How to add a device driver and its hello-world notebook, for humans and agents. This guide covers device-specific requirements; follow the [general contribution guide](contributing.md) for shared code style, testing, and contribution conventions. Post on the [PyLabRobot forum](https://discuss.pylabrobot.org) before starting to avoid duplicated effort and get support.

## 1. Understand the device

Recover the protocol before writing code.

- **Extract, don't guess.** Work from an authoritative source — firmware/protocol docs, manufacturer log files, or a reference binary. Capture the *complete* set of command frames, error codes, status values, and exact message text, filled with real values, not placeholders.
- **Get the wire format byte-exact:** transport (serial params / USB endpoint / socket), framing (delimiters, length fields, checksums), handshake (echo? ack? busy→ok?).
- **Note blocking vs non-blocking commands** and how faults (e-stop, jam) surface.

## 2. Structure the driver

Keep it small and idiomatic to PyLabRobot.

- **One file, one plain class.** The old Driver/Backend split and capability machinery are deprecated — don't use them. Instead write a single plain class whose public methods are the device's operations, talking to hardware through a `pylabrobot.io` transport. Path `pylabrobot/<vendor>/<device>.py`, re-exported from `__init__.py`. Promote to a `<device>/` package only when it genuinely helps — a distinct subsystem, the protocol/framing layer, or a large command table — not by reflex.
- **Model the device's real objects.** When the hardware has distinct addressable parts — a shaker's daisy-chained nests, a gripper's arm, a multi-channel head — give them their own classes or objects so `nest[2].shake(...)` reads like the machine works. Let the physical layout guide the object model where it makes sense; don't invent a class hierarchy the device doesn't have. A one-part device stays one class.
- **Keep the logic client-side, in PLR.** Do as much control as possible from PLR rather than delegating to the device's firmware. When the hardware exposes both a canned high-level feature and the lower-level primitives it's built from, prefer driving the primitives so the sequencing, state, and decisions live in readable Python you can inspect and adapt — not in an opaque on-device routine. Reach for a firmware macro only when the primitives genuinely aren't exposed or the timing must be enforced on-device.
- **Stay OS-agnostic:** no OS-specific libraries or DLLs. Running on Windows, Mac, and Linux is what keeps experiments portable and reproducible.
- **Use `pylabrobot.io` for all device communication.** Use the appropriate PyLabRobot transport (`USB`, `Serial`, `FTDI`, `HID`, `Socket`, etc.) instead of calling PyUSB, pyserial, hidapi, raw sockets, vendor DLLs, or OS APIs directly from a device driver. If a required transport capability is missing, add it to `pylabrobot.io` rather than bypassing the abstraction.
- **Async `setup()` / `stop()`** plus public operation methods over those `pylabrobot.io` transport primitives.
- **Log important device events.** Define a module logger with `logger = logging.getLogger(__name__)` and use it for meaningful lifecycle events, operations, state changes, recoveries, and faults. Use the appropriate log level and structured `%`-style arguments; don't use `print()` for runtime status.
- **Prefer string `Literal[...]` over enums**, especially anything user-facing. A `Literal["standard", "head", "pump"]` argument plus an internal dict mapping to wire codes reads better at the call site than an enum import. `IntEnum` is fine in narrow internal cases (e.g. a fixed set of wire/register codes never exposed to callers).
- **API docs:** add `docs/api/pylabrobot.<vendor>.rst` plus a line in `docs/api/pylabrobot.rst`.

### Name methods by prefix

A method's prefix says what it touches and what it does, following the [PLR command prefix proposal](https://discuss.pylabrobot.org/t/standardised-plr-command-prefix-proposal/403).

| prefix | meaning | examples |
|---|---|---|
| `move_`, `aspirate_`, `dispense_`, `pickup_`, `shake_` | the device does work | `move_to_y_position`, `move_to_safe_z` |
| `start_`, `stop_` | start or stop a process left running on the device | `start_temperature_control`, `start_shaking` |
| `measure_`, `sense_`, `read_`, `capture_` | a sensor reading taken at the moment of asking | `measure_weight`, `sense_tip_presence`, `sense_drawer_open`, `read_absorbance` |
| `probe_` | a measurement taken by moving | `probe_z_max`, `gripper_probe_for_object` |
| `request_` | read device memory: a value the device answers from its registers | `request_y_positions`, `request_parameters` |
| `set_` | write device memory, nothing else | `set_drive_parameter`, `set_loading_indicators` |
| `get_` | read the resource model, computed with no I/O | `elbow_drive_get_angle` |
| `update_`, `assign_`, `unassign_` | write the resource model | `elbow_drive_update_angle`, `assign_child_resource` |

- A process left running is a `start_` / `stop_` pair, never `set_`: `start_temperature_control` / `stop_temperature_control`.
- On a feature with several drives, the drive's name comes first: `gripper_request_width`, `elbow_drive_get_angle`.
- Lifecycle methods take no prefix: `discover`, `initialize`, `park`.
- Don't use synonyms of these prefixes: `query_`, `fetch_`, `retrieve_`, `obtain_`, `poll_` (use `request_` or `get_`), `write_`, `put_` (use `set_`). Propose a missing prefix on the forum before adding one.

### Share nothing between device drivers

Each driver stands alone: one device's package never imports another's. What more than one device needs goes in
`pylabrobot.lib`, which imports no device and nothing from `pylabrobot.legacy`:

- **`pylabrobot.lib.liquid_handling`** plans pipetting for any multi-channel pipette device: where channels go
  inside a container (`channel_positioning.compute_channel_offsets`) and which channels can reach their targets in
  one X/Y move (`pipette_batch_scheduling.plan_batches`). It is pure and synchronous. The device supplies its
  per-channel minimum spacing and executes the plan with its own moves; `hamilton/prep/driver/features/pipettes_tests.py` and
  `hamilton/star/driver/features/pipettes_tests.py` show the pattern.

### Idempotent public API

The public surface must expose **no non-idempotent commands.** If the hardware only offers a raw toggle/flip, keep it private (`_toggle_x`) and expose move-to-state methods (`move_x_out` / `move_x_in`) that read current state, act only if needed, then confirm. This keeps the API safe to call repeatedly — the caller states intent ("be open"), not a blind toggle.

Keep connection, calibration, and state on the device; pass operation-specific settings (plate geometry, grip, offsets, speed, duration) as method arguments without carrying them between calls.

### Unverified drivers

If the driver hasn't been checked against real hardware, say so loudly: `setup()` should `logger.warning(...)` that it's untested and invite a change once someone verifies it. Don't quietly present untested code as ready.

## 3. Hello-world notebook

Every device ships a runnable notebook that takes a user from cabling to first command. Path `docs/user_guide/<vendor>/<device>/hello-world.ipynb`; wire it in via the `<vendor>/index.md` `{toctree}` and add `<vendor>/index` to the Manufacturers `{toctree}` in `docs/user_guide/index.md` (alphabetical).

Sections (markdown cell then code cell): (1) title + property table + untested-warning; (2) how it talks — brief, since users care about the machine, not the wire; (3) physical setup; (4) `setup()`; (5) one section per operation.

Cell rules:
- **One concept per code cell.** If a physical action happens between steps, that's two cells: `move_tray_out()` → place plate → `move_tray_in()`. Every code cell gets a preceding markdown cell.
- **Notebook JSON:** edit with a notebook-aware tool (plain-text replace is blocked on `.ipynb`); code cells `execution_count: null`, empty `outputs`; `nbformat: 4`, `nbformat_minor: 5`; every cell has an `id`; validate it parses.

## 4. Register the device and its guide

Add or update the device's object in `docs/_static/devices.json` in the same change. Follow the [device registry guide](device-registry.md) for the schema. A driver and hello-world change is not complete until its registry entry has:

- `api`, `api_version`, and `code_slug` pointing to the driver;
- `doc_slug` pointing to the hello-world notebook, relative to `docs/user_guide/` and without `.ipynb`; and
- an `id` matching the notebook's `{device-card}` directive.

When the device is already registered, edit its existing object instead of adding a duplicate. In particular, add `doc_slug` when a guide is added to a device that previously had no documentation page.

## 5. Verify the integration

- Test framing, parsing, command sequencing, status handling, and error handling through a mocked or captured transport without connecting to hardware.
- Validate the hello-world notebook structure and imports without executing its hardware cells.
- Run the device-registry tests and build the documentation so its API, code, and documentation links are checked.
- Keep the unverified-driver warning until the implementation has been checked on the real device.
