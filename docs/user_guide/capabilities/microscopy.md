# Microscopy

{class}`~pylabrobot.capabilities.microscopy.microscopy.Microscopy` controls automated microscopes with support for PLR-driven auto-exposure and auto-focus.

## When to use

Use this for imaging cells, colonies, crystals, or any sample in microplates -- with automated focus and exposure optimization.

## Setup

```python
from pylabrobot.molecular_devices.imageXpress.pico import Pico

microscope = Pico(name="pico", ...)
await microscope.setup()
```

## Walkthrough

### Basic capture

```python
result = await microscope.microscopy.capture(
    well=plate.get_well("A1"),
    mode=ImagingMode.BRIGHTFIELD,
    objective=Objective.O_10X_PL_FL,
    plate=plate,
)
# result.images contains the captured data
# result.exposure_time and result.focal_height report what was used
```

### Auto-exposure

PLR can optimize exposure time via a binary search. You provide a callback that evaluates whether an image is under-, over-, or correctly exposed:

```python
from pylabrobot.capabilities.microscopy.microscopy import AutoExposure, max_pixel_at_fraction

result = await microscope.microscopy.capture(
    well=plate.get_well("A1"),
    mode=ImagingMode.BRIGHTFIELD,
    objective=Objective.O_10X_PL_FL,
    plate=plate,
    exposure_time=AutoExposure(
        evaluate_exposure=max_pixel_at_fraction(0.8, margin=0.05),
        low=1.0,       # min exposure (ms)
        high=500.0,    # max exposure (ms)
        max_rounds=10,
    ),
    focal_height=5.0,  # must be numeric when using AutoExposure
    gain=1.0,          # must be numeric when using AutoExposure
)
```

### Auto-focus

PLR can optimize focal height via a golden-ratio search:

```python
from pylabrobot.capabilities.microscopy.microscopy import AutoFocus, evaluate_focus_nvmg_sobel

result = await microscope.microscopy.capture(
    well=(0, 0),  # can also pass a (row, col) tuple
    mode=ImagingMode.BRIGHTFIELD,
    objective=Objective.O_10X_PL_FL,
    plate=plate,
    exposure_time=50.0,  # must be numeric when using AutoFocus
    focal_height=AutoFocus(
        evaluate_focus=evaluate_focus_nvmg_sobel,
        low=0.0,         # min focal height (mm)
        high=10.0,       # max focal height (mm)
        tolerance=0.01,  # convergence tolerance (mm)
        timeout=60,      # seconds
    ),
    gain=1.0,  # must be numeric when using AutoFocus
)
```

## Tips and gotchas

- **When using `AutoExposure`, `focal_height` and `gain` must be numeric** (not `"machine-auto"` or `AutoFocus`). Same constraint applies in reverse for `AutoFocus`.
- **`"machine-auto"` defers to the microscope's built-in defaults.** Use this when you don't need PLR-driven optimization.
- **`evaluate_focus_nvmg_sobel`** computes focus quality using a Sobel filter on the center 50% of the image. Higher scores mean sharper focus.

## Supported hardware

```{supported-devices} microscopy
```

## API reference

See {class}`~pylabrobot.capabilities.microscopy.microscopy.Microscopy` and {class}`~pylabrobot.capabilities.microscopy.microscopy.MicroscopyBackend`.
