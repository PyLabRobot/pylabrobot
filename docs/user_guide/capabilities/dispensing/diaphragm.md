# Diaphragm dispensing

{class}`~pylabrobot.capabilities.bulk_dispensers.diaphragm.diaphragm.DiaphragmDispenser` controls chip-based contactless dispensing into individual containers.

A diaphragm dispenser uses a disposable silicon chip with microvalves driven by pressurized air to deliver nanoliter-to-microliter volumes into a single well at a time.

`DiaphragmDispenser` is the **variable** head-format variant of the diaphragm capability — it addresses one container per dispense op, so callers pass two parallel lists, one of containers and one of volumes, in the order to be dispensed. Future 8-channel (`DiaphragmDispensing8`) and 96-channel (`DiaphragmDispensing96`) variants will follow the same naming convention as the peristaltic and syringe capabilities. See [the bulk dispensing index](index) for the full mechanism × head-format matrix.

## Walkthrough

```python
from pylabrobot.capabilities.bulk_dispensers.diaphragm import (
    DiaphragmDispenser,
    DiaphragmDispenserChatterboxBackend,
)
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

dispenser = DiaphragmDispenser(backend=DiaphragmDispenserChatterboxBackend())
await dispenser._on_setup()

plate = Cor_96_wellplate_360ul_Fb("demo_plate")
```

### Dispensing

Pass parallel `containers` and `volumes` lists. Each `volumes[i]` (in uL) is dispensed into `containers[i]`, in order.

```python
# Same volume to a few wells
await dispenser.dispense(
    containers=plate["A1:C1"],
    volumes=[5.0, 5.0, 5.0],
)

# Different volumes per container
await dispenser.dispense(
    containers=plate["A1:A3"],
    volumes=[1.0, 2.5, 10.0],
)
```

The capability validates that both lists have the same length and that all volumes are positive.

### Priming

Most diaphragm dispensers need to be primed before they can deliver accurate volumes. Backends typically also re-prime automatically before dispensing if no chip has been primed yet — see your device's docs for the exact behavior.

```python
await dispenser.prime()
```

### Backend parameters

Device-specific settings (chip number, dispense Z-height, prime volume, etc.) are passed as a `BackendParams` instance defined by the concrete backend. For example, on the Mantis:

```python
from pylabrobot.formulatrix.mantis.diaphragm_dispenser_backend import (
    MantisDiaphragmDispenserBackend,
)

await dispenser.dispense(
    containers=plate["A1"],
    volumes=[5.0],
    backend_params=MantisDiaphragmDispenserBackend.DispenseParams(
        chip=3,
        dispense_z=44.331,
        prime_volume=20.0,
    ),
)
```

See the device's user guide page for the full list of parameters.

## Tips and gotchas

- **`containers` and `volumes` must be the same length** and ordered to match: index `i` in one corresponds to index `i` in the other.
- **All volumes must be positive.** Zero or negative volumes raise `ValueError`.
- **Containers are visited in list order.** If your hardware optimizes path planning, sort the list yourself before calling `dispense`.
- **Chip and Z-height belong in `backend_params`.** Different chips may be loaded for different volume ranges, and the dispense Z-height depends on the plate. Set these per call.

## Supported hardware

| Device | Manufacturer |
|--------|-------------|
| [Mantis](../../formulatrix/mantis/hello-world) | Formulatrix |

## API reference

See {class}`~pylabrobot.capabilities.bulk_dispensers.diaphragm.diaphragm.DiaphragmDispenser` and {class}`~pylabrobot.capabilities.bulk_dispensers.diaphragm.backend.DiaphragmDispenserBackend`.
