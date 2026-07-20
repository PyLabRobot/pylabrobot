# Writing a Device Driver

This page collects the practical conventions for adding a driver for a specific
device. It complements {doc}`new-machine-type` (adding a whole new *type* of
machine) and focuses on the day-to-day shape of a single device driver.

```{attention}
If you cannot test on hardware, say so explicitly in the code (see
[Unverified drivers](#unverified-drivers)). Do not present an untested driver
as verified.
```

## Module layout

- One device, one file. Group by vendor: the folder is the manufacturer, the
  file is the device — e.g. `pylabrobot/sartorius/entris.py`,
  `pylabrobot/qinstruments/bioshake.py`.
- Keep it a single class where the protocol is simple. Do not split into
  driver/backend/frontend layers unless the device genuinely needs the shared
  machinery (a capability backend, a resource-tree frontend). Reach for more
  structure only when a second device or a frontend feature demands it.
- `__init__.py` re-exports the public class and its error type.
- Use the shared IO layer (`pylabrobot.io.serial.Serial`,
  `pylabrobot.io.usb.USB`, …) for transport rather than talking to `pyserial` /
  `pyusb` directly. It handles port discovery, capture/replay, and logging.
- Expose `async setup()` / `async stop()` plus the device's public operations.
  `setup()` opens the IO and brings the device online; `stop()` closes it.

## OS- and vendor-agnostic

PyLabRobot runs on Windows, macOS, and Linux. A driver must not depend on
OS-specific libraries or on vendor DLLs/executables. Talk to the device over
its own protocol (serial, USB, TCP, …). If the only public interface is a
vendor SDK, reverse-engineer the wire protocol instead of shelling out to it.

## Reverse-engineering the protocol

Sources, in rough order of usefulness: manufacturer firmware/interface manuals,
packet captures of the vendor software driving the device, and — as a last
resort — decompiling the vendor software.

```{tip}
When you extract an error table or command set from vendor software, pull the
**real values**, not placeholders. Protocol data (e.g. error-message strings)
often lives in a resource bundle referenced by name from the code, not inline —
extract the bundle so the messages are accurate. Capture the *complete* set of
codes the device can return, not just the common ones.
```

## Code style

- Comments say what the code does, not where it came from or how it changed.
  No provenance ("ported from vendor X"), no change-history ("now uses…"), no
  narrative.
- No dead code — drop constants and branches nothing uses.
- Map device errors to a single lookup table with accurate descriptions, and
  raise a typed exception (`<Vendor>Error`).

## Unverified drivers

If the driver has not been run against real hardware in PyLabRobot, emit a
warning in `setup()` so users are not misled:

```python
logger.warning(
  "<ClassName> has NOT been tested against hardware in PyLabRobot. "
  "Please make a PR to remove this message if you have verified it on your hardware."
)
```

Remove the warning in the PR that confirms hardware operation.

## Documentation

Add an API page and link it:

1. Create `docs/api/pylabrobot.<vendor>.rst` (copy an existing one).
2. Add `pylabrobot.<vendor>` to the toctree in `docs/api/pylabrobot.rst`.

A short usage notebook or Markdown page under `docs/` is optional but
encouraged.

## Before you open the PR

- Format and lint with the project's tools: `ruff format`, `ruff check`, and
  `mypy` (see `CONTRIBUTING.md`). Match the repo's 2-space indentation.
- Confirm the module imports and the public class constructs.
- Keep the PR to the new module plus its docs entry.
```

Get in touch on [the PyLabRobot Development forum](https://discuss.pylabrobot.org)
before starting, so you don't duplicate work.
