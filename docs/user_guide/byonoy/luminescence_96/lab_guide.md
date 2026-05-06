# Byonoy Luminescence 96 — lab guide

A walkthrough for running a luminescence assay on the Byonoy L96 from PyLabRobot. Assumes the device is plugged in via USB and you've installed `pylabrobot` (with `hid` and `hidapi`).

The L96 is a 96-well luminescence-only plate reader. It reads emitted light per well, no excitation source. Communicates over USB HID (vid `0x16D0`, pid `0x119B`).

---

## 1. Connect

```python
from pylabrobot.byonoy import byonoy_l96  # or byonoy_l96a for the automate variant

base, reader = byonoy_l96(name="l96")
await reader.setup()
```

`base` is a resource (a plate goes here); `reader` is both a resource and a device (the detector unit). After `setup()` the HID handle is open and a heartbeat thread is running.

When you're done:

```python
await reader.stop()
```

> **One process at a time.** macOS / Windows / Linux all give exclusive HID access. If a previous Python session crashed without `stop()`, the next `setup()` will fail with "device already open". Replug the USB cable to force-release.

---

## 2. Load a plate

```python
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb

base.reader_unit_holder.unassign_child_resource(reader)  # take detector off
plate = Cor_96_wellplate_360ul_Fb(name="plate")
base.plate_holder.assign_child_resource(plate)
# physically: place the plate in the reader, place the detector back on top
```

The reader-on-base interlock prevents you from assigning a plate while the detector is still on the holder — it forces a sane physical sequence.

---

## 3. Read — the basics

```python
results = await reader.luminescence.read(plate=plate, focal_height=13.0)
data = results[0].data            # 8 × 12 list[list[float]]
timestamp = results[0].timestamp  # epoch seconds
```

### Result shape

`data` is plate row-major:

```
data[0] = [A1, A2, A3, ..., A12]
data[1] = [B1, B2, ..., B12]
...
data[7] = [H1, H2, ..., H12]
```

So `data[2][5]` is well `C6`. Values are floats in **RLU (Relative Light Units) per integration period** — not per second. Doubling integration time roughly doubles signal *and* dark counts.

### Background

With nothing in the wells (and a dark environment), expect noise around ±50 RLU at SENSITIVE (2 s). Non-zero noise is dark-current spread; **negative values are normal** because the firmware applies a baseline subtraction.

> **Light leakage.** The L96 is designed to be light-tight from above (the detector unit covers the plate) but the bottom housing isn't perfectly sealed. Reading on a *white* surface vs a *black* surface can change empty-well readings from ~50 to ~50,000+ RLU because reflected ambient light leaks in. For real assays use a black mat or a dark cabinet.

---

## 4. Picking an integration mode

Four modes, mapping to the byonoy_device_library presets:

| Mode | Integration time | Use for |
|---|---|---|
| `RAPID` | 100 ms | Saturation checks, quick "is it bright?" |
| `SENSITIVE` | 2 s (default) | Most assays — luciferase, BRET, NanoBiT |
| `ULTRA_SENSITIVE` | 20 s | Very faint signals; low-copy reporters |
| `CUSTOM` | user-supplied | Your own duration |

```python
from pylabrobot.byonoy import ByonoyLuminescence96Backend, Lum96IntegrationMode

# Preset
results = await reader.luminescence.read(
    plate=plate,
    focal_height=13.0,
    backend_params=ByonoyLuminescence96Backend.LuminescenceParams(
        mode=Lum96IntegrationMode.ULTRA_SENSITIVE,
    ),
)

# Custom (any duration in seconds)
results = await reader.luminescence.read(
    plate=plate,
    focal_height=13.0,
    backend_params=ByonoyLuminescence96Backend.LuminescenceParams(
        integration_time=5.0,  # auto-switches to CUSTOM mode
    ),
)
```

---

## 5. Reading specific wells

Pass a 96-bool mask in plate row-major order (A1 = index 0, A12 = 11, B1 = 12, …, H12 = 95):

```python
# Only column 1 (A1, B1, ..., H1)
mask = [False] * 96
for row in range(8):
    mask[row * 12 + 0] = True

results = await reader.luminescence.read(
    plate=plate,
    focal_height=13.0,
    backend_params=ByonoyLuminescence96Backend.LuminescenceParams(
        selected_wells=mask,
    ),
)
```

Unselected wells come back as exactly `0.0`. The result shape is still 8×12 — it's an output filter, not a different report.

> **No speed-up.** The firmware always integrates the whole 96-sensor array. Reading one column with `SENSITIVE` takes the same wall-clock as reading the full plate (~28 s in our hardware test). Use `selected_wells` to keep your downstream tidy, not to save time. If you want fast, use `RAPID` mode.

---

## 6. Timed read (delay before reading)

For a substrate-injection assay where you want a fixed delay between adding reagent and reading:

```python
import asyncio

# ... pipette substrate into the plate ...
await asyncio.sleep(60)   # 60 s incubation
results = await reader.luminescence.read(plate=plate, focal_height=13.0)
```

Nothing special — `await asyncio.sleep` doesn't block the event loop, and the reader stays connected.

---

## 7. Kinetic read (time series)

Read the same plate every N seconds, collect a stack of matrices:

```python
import asyncio, time

frames = []
duration_s = 600      # 10 minutes total
interval_s = 30       # one read every 30 s

t_start = time.time()
while time.time() - t_start < duration_s:
    t_read = time.time()
    results = await reader.luminescence.read(plate=plate, focal_height=13.0)
    frames.append({
        "t": t_read - t_start,
        "data": results[0].data,
    })
    # Sleep the *remainder* of the interval (read takes ~3 s for SENSITIVE)
    elapsed = time.time() - t_read
    if elapsed < interval_s:
        await asyncio.sleep(interval_s - elapsed)

print(f"collected {len(frames)} frames over {duration_s} s")
```

Storing as a list of `{t, data}` dicts is simple. Convert to `numpy` for analysis:

```python
import numpy as np
matrix_stack = np.array([f["data"] for f in frames])  # shape (n_frames, 8, 12)
times = np.array([f["t"] for f in frames])
```

For an 8 × 12 well at column `c`, row `r`:
```python
trace = matrix_stack[:, r, c]   # (n_frames,) signal over time
```

> **Kinetic read budget**: with `SENSITIVE` (2 s) the wall-clock per read is around 3 s including overhead. So `interval_s` must be ≥ 3. With `ULTRA_SENSITIVE` (20 s) it's around 28 s — plan accordingly.

---

## 8. Stopping a long read

If you need to bail out of an `ULTRA_SENSITIVE` read mid-flight (or any read takes longer than expected):

```python
# Start the read in a task, cancel from elsewhere
task = asyncio.create_task(
    reader.luminescence.read(plate=plate, focal_height=13.0,
        backend_params=ByonoyLuminescence96Backend.LuminescenceParams(
            mode=Lum96IntegrationMode.ULTRA_SENSITIVE,
        ),
    )
)
# ... later:
await reader.driver.cancel(report_id=0x0340)
try:
    await task
except asyncio.CancelledError:
    print("aborted cleanly")
```

`cancel()` raises a flag the read loop checks; bail-out is within ~2 s. After cancel the device stays initialised and immediately accepts new reads — no need to `setup()` again.

---

## 9. Device health & identity

Useful at the start of a session, in error messages, or for run logging.

```python
status = await reader.driver.get_status()
# ByonoyStatus(is_initialized, slot_state, error_code, uptime_s, is_measuring, boot_completed)

env = await reader.driver.get_environment()
# ByonoyEnvironment(temperature_c, humidity, acceleration_g)

info = await reader.driver.get_device_info()
# device_id, device_name, manufacturer, serial_no, firmware_version, ref_number

versions = await reader.driver.get_versions()
# ByonoyVersions with system / STM / ESP / bootloader version numbers; .is_production

api = await reader.driver.get_api_version()      # protocol version
supported = await reader.driver.get_supported_reports()  # list of HID report IDs

print(f"{info.device_name} sn={info.serial_no} fw={info.firmware_version}")
print(f"  uptime {status.uptime_s} s, T={env.temperature_c:.1f}°C, RH={env.humidity*100:.0f}%")
print(f"  slot: {status.slot_state.name}, error: {reader.driver.describe_error_code(status.error_code)}")
```

> **`slot_state` interpretation**: `OCCUPIED` when a plate is loaded, `UNKNOWN` when nothing is in the reader (the firmware can't tell empty from missing). Don't treat `UNKNOWN` as an error — it's just "no plate".

> **`error_code` interpretation**: `0` is `NO_ERROR`. The Lum96 firmware doesn't publish a documented table for non-zero values, so non-zero codes surface as `errorCode=0xNN` (matching what Byonoy's own C library returns). For Abs96 / AbsOne backends, names like `ERROR_CALIB` / `AMBIENT_LIGHT` are decoded automatically.

---

## 10. Visual feedback (LED bar)

The L96 has a 20-pixel RGB front bar. Useful in a workcell to flag run state ("queued", "reading", "done", "errored").

```python
from pylabrobot.byonoy import LedEffect

# Solid colour — auto-enables manual mode
await reader.driver.set_led_colours([(255, 200, 0)] * 20)   # amber: queued
await reader.driver.set_led_colours([(0, 255, 0)] * 20)     # green: ready
await reader.driver.set_led_colours([(255, 0, 0)] * 20)     # red:   error

# Or per-pixel
gradient = [(int(255 * i / 20), 0, int(255 * (1 - i / 20))) for i in range(20)]
await reader.driver.set_led_colours(gradient)

# Built-in firmware effects
await reader.driver.set_led_effect(LedEffect.BREATHING, duration_ms=10000)
await reader.driver.set_led_effect(LedEffect.CYLON, duration_ms=5000)
await reader.driver.set_led_effect(LedEffect.SOLID, duration_ms=0)  # back to default
```

Available effects: `SOLID`, `BLINKING`, `BREATHING`, `CYLON`, `RAINBOW`, `PROGRESS`. The visual rendering of dynamic effects (`CYLON`, `RAINBOW`, ...) is firmware-defined; `set_led_colours` is the precise way to control exactly what you see.

---

## 11. Common workflow recipe — luciferase end-point

Putting it together for a typical end-point luciferase assay:

```python
import asyncio, time
import numpy as np
from pylabrobot.byonoy import (
    byonoy_l96, ByonoyLuminescence96Backend,
    Lum96IntegrationMode, LedEffect,
)
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb

# 1. Connect
base, reader = byonoy_l96(name="l96")
await reader.setup()

# Light up amber: device is being prepared
await reader.driver.set_led_colours([(255, 150, 0)] * 20)

# 2. Sanity check
status = await reader.driver.get_status()
info = await reader.driver.get_device_info()
print(f"{info.device_name} sn={info.serial_no} — {status.slot_state.name}")
assert status.error_code == 0

# 3. Load plate
base.reader_unit_holder.unassign_child_resource(reader)
plate = Cor_96_wellplate_360ul_Fb(name="assay_plate")
base.plate_holder.assign_child_resource(plate)
# (operator places plate, places detector back on top)

# 4. Read — show green while measuring
await reader.driver.set_led_colours([(0, 255, 0)] * 20)
results = await reader.luminescence.read(
    plate=plate,
    focal_height=13.0,
    backend_params=ByonoyLuminescence96Backend.LuminescenceParams(
        mode=Lum96IntegrationMode.SENSITIVE,
    ),
)
data = np.array(results[0].data)   # 8 × 12

# 5. Save + tidy up
np.save(f"luminescence_{int(time.time())}.npy", data)
await reader.driver.set_led_effect(LedEffect.SOLID, duration_ms=0)
await reader.stop()
```

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `setup()` raises "device already open" | Previous Python session left HID handle locked | Replug USB cable, or kill stale Python processes |
| All wells read very high (10⁴–10⁶) with no sample | Light leak through housing bottom | Use a dark mat or close the room |
| Strong gradient A→H with no sample | Directional light leak (one side leakier) | Same — dark mat |
| `slot_state=UNKNOWN` | No plate loaded | Expected — firmware cannot detect "nothing" definitively |
| `slot_state=OCCUPIED` but plate is the wrong one | Sensor only checks presence, not identity | Track plate identity in your code |
| Read takes 28 s for one well in SENSITIVE | Firmware always integrates 96 wells | Use `RAPID` for fast, accept the 28 s for SENSITIVE |
| `cancel()` doesn't abort | Wrong report id | Default `0x0340` is the lum trigger; usually correct |
| Negative readings on empty wells | Firmware baseline subtraction | Expected — they should sit around zero |

---

## 13. Reference

- **Hardware protocol**: HID 64-byte frames, vid `0x16D0`, pid `0x119B`. Report IDs decoded from Byonoy's `byonoyusbhid.h`. `routing_info=\x80\x40` requests a reply; `\x00\x00` is fire-and-forget.
- **Source**: `pylabrobot/byonoy/backend.py` (transport + queries), `pylabrobot/byonoy/luminescence_96.py` (lum read).
- **Vendor library**: `byonoy_device_library` (C with pybind11 wrapper). Not a runtime dependency for PLR — every byte is decoded from the headers and goes through PLR's own HID transport.
- **Companion notebook**: `docs/user_guide/byonoy/luminescence_96/hello-world.ipynb` for a minimal run-through.
