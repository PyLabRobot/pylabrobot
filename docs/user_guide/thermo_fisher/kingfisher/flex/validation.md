# KingFisher Flex support and operation

The driver executes local BindIt BDZ exports for the **96DW head, tip comb and
plates**. It preserves exported motion coordinates, command parameters and order.
It does not calculate positions from liquid volumes or generate protocols.

Hardware validation covers firmware 1.01.0 on macOS: identification, initialization,
loading/unloading through Python callbacks, mixing, bead collection/release,
precollection, postmix, drying outside and above wells, automatic timed pauses,
expanded mixing loops, manual pauses with reagent additions, and 37 °C heating
with and without preheat. Other firmware versions and operating systems require
validation. Standard KF96, PCR and 24-head configurations are not supported.

The driver remains experimental. Partial tip-comb attachment can occur without a
firmware error; command completion does not verify attachment. Inspect pickup and
clearance. The driver does not retry motion or recover an interrupted method.

## Installation

Install PyLabRobot with the HID extra and a system HIDAPI library:

```console
pip install 'pylabrobot[hid]'
```

On macOS, install HIDAPI with `brew install hidapi`. If Python cannot find
`libhidapi.dylib`, set `DYLD_LIBRARY_PATH` to the Homebrew library directory when
launching Python (typically `/opt/homebrew/lib` on Apple Silicon).
On Linux, install the distribution's HIDAPI library and configure USB permissions
for vendor `0x0AB6`, product `0x02C7`. Close BindIt before opening the device in PLR.
All communication uses `pylabrobot.io.HID`.

## Inspect before executing

`read_flex_bdz(path)` accepts a path or bytes. It validates the binary container,
compressed XML, Flex identity and references, retaining the original bytes and
script. `compatibility_issues()` reports unsupported features with source locations;
`preflight()` raises if any blocker remains. Inspection does not open USB.

```python
from pylabrobot.thermo_fisher.kingfisher.flex import read_flex_bdz

protocol = read_flex_bdz("protocol.bdz")
issues = protocol.compatibility_issues()
commands = protocol.preflight()
prompts = protocol.prompts()
```

An accepted export is supported by the parser; this does not establish validation
of that particular method. Unsupported commands are never silently dropped.
Streaming excludes only `DTL` and `TIM` metadata. BindIt expands loops into commands;
the driver sends that sequence once.

The CLI provides offline checks and an explicit identification-only probe:

```console
plr dev kingfisher-flex inspect protocol.bdz
plr dev kingfisher-flex audit /path/to/protocols
plr dev kingfisher-flex probe
```

`audit` scans directories nonrecursively, reports each invalid or unsupported file,
and exits with status 1 if any input fails. `probe` opens real hardware, sends `VER`
and disconnects. It does not initialize or start a method.

## Execution and confirmations

Follow the [hello-world notebook](hello-world.ipynb) for connection and execution.
`setup()` identifies the device. `initialize()` explicitly moves the axes, disables
heating and presents tray 4 at loading, with tray 1 at processing.

`run_protocol(path_or_protocol)` checks the entire export before transmitting its
execution preamble. It serializes commands, waiting for acknowledgment and terminal
completion separately. Timed mixing and timer waits receive duration-aware deadlines.
Temperature telemetry continues during motion and operator waits.

By default, `PAU` commands wait for the physical Start button. Pass an asynchronous
`confirm_prompt` callback to handle loading/unloading in Python, and a separate
`confirm_pause` callback for protocol pauses. Each callback must return exactly
`True` after its action is complete. The corresponding `PAU` is then omitted from
the transmitted sequence. This does not remotely press Start on an existing pause.

`FlexPrompt` contains the action, label, processing tray and loading tray.
`additions` describes any required reagent additions with plate, reagent and
per-well volume in microliters. The label also includes these instructions.
Confirmation acknowledges completion; the driver does not dispense liquid.
Pause text mentioning a duration is descriptive: only exported timer commands
create automatic timed waits. Each addition pause presents one plate.

`operator_timeout=None` allows an indefinite operator wait. A rejected confirmation,
callback error, timeout, cancellation, instrument error or disconnect prevents
subsequent commands and invalidates the session. No failed motion is resent.

## Heating and events

`TEM target interval` uses integer degrees Celsius and a reporting interval in
centiseconds. Supported targets are `0` (RT/off) or `10..96`; the interval is `0`
(disabled) or at least `100`. The instrument can reject a target based on calibration.
Hardware validation covers 37 °C. `MOI 310` raises the heater block and `MOI 0` lowers
it; the driver preserves those commands independently of the temperature setting.

`TEM END 0` acknowledges setting completion, not target attainment. The driver adds
no temperature wait beyond the exported sequence. Temperature reports describe the
heater and ambient sensors, not independently measured liquid temperature.

`events()` supplies temperature, step, prompt, run-completion and failure events.
A single consumer should read the event stream. `FlexRunResult` reports the source
BDZ digest, completed script-command count and host-confirmation count after
finalization. `transcript` exposes timestamped command/response entries for diagnostics.

## Interruption and disconnect

`abort()` sends the instrument abort sequence and waits for `ABO END`. It can run
from a supervising task while execution or a confirmation is pending. Abort can
move axes; completion does not establish a safe physical position.

`stop()` only disconnects. Canceling a Python task does not stop the instrument.
After interruption, inspect physical state, close the connection and establish a
fresh session before deliberately restarting. There is no automatic resume.

## Onboard protocols

`list_protocols()` reads onboard names, including maintenance routines.
`start_stored_protocol(name)` requires an exact unique inventory match and starts
that sequence using `BGN`. Hardware validation covers launch to the first loading
prompt only. Internal loading pauses are not individually exposed to PLR callbacks;
physical Start remains necessary. `resume()` raises `NotImplementedError`.
Protocol upload and virtual advancement of onboard protocols are unsupported.
