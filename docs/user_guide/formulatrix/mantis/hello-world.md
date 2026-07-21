# Formulatrix Mantis

The Formulatrix Mantis is a chip-based contactless liquid dispenser. It uses disposable silicon chips with microvalves driven by pressurized air to deliver nanoliter-to-microliter volumes into individual wells without contacting the liquid.

PLR exposes it as a {class}`~pylabrobot.formulatrix.mantis.mantis.Mantis` device with a {class}`~pylabrobot.capabilities.bulk_dispensers.diaphragm.diaphragm.DiaphragmDispenser` capability. The driver communicates over an FTDI/USB serial link using the FMLX protocol.

## Architecture

The Mantis package is split into three layers:

| Layer | Class | Responsibility |
|---|---|---|
| Device | {class}`~pylabrobot.formulatrix.mantis.mantis.Mantis` | Wires the driver and capability together; owns the lifecycle. |
| Driver | {class}`~pylabrobot.formulatrix.mantis.driver.MantisDriver` | Hardware-level: FTDI connection, init sequence, motion in machine-frame coords, chip attach/prime/detach, raw PPI sequence playback, pressure init/shutdown. |
| Capability backend | {class}`~pylabrobot.formulatrix.mantis.diaphragm_dispenser_backend.MantisDiaphragmDispenserBackend` | Translates capability `dispense()` calls into `move_to` + N×`execute_ppi_sequence` driver calls, and converts PLR well coordinates into the Mantis machine frame. |

The driver knows which physical chips are loaded (`chip_type_map`) so that prime and PPI sequences can pick the right variant per chip type. Per-call calibration like the dispense Z-height belongs in `BackendParams`, not on the driver, because it depends on the plate and chip in use.

## Setup

```python
from pylabrobot.formulatrix.mantis import Mantis

mantis = Mantis(serial_number="M-000438")  # FTDI serial number
await mantis.setup()
```

`setup()` connects to the FTDI device, runs the full Mantis initialization sequence (homing, calibration, pressure controllers), and leaves the instrument ready to dispense. By default, chips 3, 4, and 5 are configured as `"high_volume"`. Override the mapping with `chip_type_map` if your machine has different chips loaded:

```python
mantis = Mantis(
    serial_number="M-000438",
    chip_type_map={3: "high_volume", 4: "low_volume"},
)
```

## Dispensing

The capability takes parallel `containers` and `volumes` lists:

```python
from pylabrobot.formulatrix.mantis.diaphragm_dispenser_backend import (
    MantisDiaphragmDispenserBackend,
)
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

plate = Cor_96_wellplate_360ul_Fb("plate1")

await mantis.diaphragm_dispenser.dispense(
    containers=plate["A1:C1"],
    volumes=[5.0, 2.5, 1.0],  # uL
    backend_params=MantisDiaphragmDispenserBackend.DispenseParams(
        chip=3,
        dispense_z=44.331,
    ),
)
```

The backend will (re-)attach and prime the chip if necessary, visit each container in order, decompose each volume into the largest available pulse counts (e.g. 5 uL = 1×`dispense_5uL`, 7 uL = 1×`dispense_5uL` + 2×`dispense_1uL`), then return to the home and ready positions and detach the chip.

### `DispenseParams`

| Field | Default | Meaning |
|---|---|---|
| `chip` | `None` (uses driver default) | Chip number 1–6. Must be present in the driver's `chip_type_map`. |
| `dispense_z` | `44.331` | Machine-frame Z height in mm at which to dispense. **Plate-dependent calibration** — set this per plate. |
| `prime_volume` | `20.0` | Prime volume in uL used when (re-)priming the chip before this dispense. |

### Coordinate conversion

Mantis uses its own plate-local frame in which "A1" sits at the front of the plate (low y), while PLR places A1 at the back (high y). The backend mirrors each well center across the plate's `size_y` before applying a calibrated stage homography to get the final machine-frame XY. This is done per-container from the well's location in its parent `Plate`, so any plate definition (factory or custom) works without further configuration.

## Priming

You can prime explicitly. Otherwise, the next `dispense()` will prime automatically if the requested chip is not yet primed.

```python
await mantis.diaphragm_dispenser.prime(
    backend_params=MantisDiaphragmDispenserBackend.PrimeParams(chip=3, volume=20.0),
)
```

## Shutdown

```python
await mantis.stop()
```

`stop()` detaches the current chip, returns to home and ready, shuts down pressure controllers, and disconnects the FTDI link.

## Tips and gotchas

- **`dispense_z` is plate- and chip-dependent.** The default value is a placeholder and is not appropriate for every plate. Calibrate per plate.
- **Chip numbers are physical slots.** The driver's `chip_type_map` tells it what kind of chip is in each slot; it does not auto-detect.
- **One chip per `dispense()` call.** If you need multiple chips, issue separate calls — the backend will detach and re-attach as needed.
- **Containers must have a `Plate` parent.** Orphan wells raise `ValueError` because the y-flip needs `plate.size_y`.

## API reference

- {class}`~pylabrobot.formulatrix.mantis.mantis.Mantis`
- {class}`~pylabrobot.formulatrix.mantis.driver.MantisDriver`
- {class}`~pylabrobot.formulatrix.mantis.diaphragm_dispenser_backend.MantisDiaphragmDispenserBackend`
