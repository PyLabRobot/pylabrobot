# PIP (Independent Channels)

{class}`~pylabrobot.capabilities.liquid_handling.pip.PIP` controls independent pipetting channels for tip handling, aspiration, and dispensing.

## When to use

Use this for any pipetting operation that uses individual channels: serial dilutions, cherry-picking, reformatting between plates, etc.

## Setup

PIP is accessed as an attribute on a liquid handler:

```python
from pylabrobot.hamilton.star import STAR

lh = STAR(name="star", ...)
await lh.setup()

# independent channels are at lh.pip
await lh.pip.pick_up_tips(tip_rack["A1:H1"])
```

## Walkthrough

### Basic pipetting

```python
# Pick up 8 tips
await lh.pip.pick_up_tips(tip_rack["A1:H1"])

# Aspirate 100 uL from column 1
await lh.pip.aspirate(plate["A1:H1"], vols=[100] * 8)

# Dispense 100 uL into column 2
await lh.pip.dispense(plate["A2:H2"], vols=[100] * 8)

# Return tips to where they came from
await lh.pip.return_tips()
```

### Using specific channels

```python
# Use only channels 0 and 1
with lh.pip.use_channels([0, 1]):
    await lh.pip.pick_up_tips([tip_rack["A1"], tip_rack["B1"]])
    await lh.pip.aspirate([plate["A1"], plate["B1"]], vols=[50, 50])
    await lh.pip.dispense([plate["A2"], plate["B2"]], vols=[50, 50])
    await lh.pip.drop_tips([tip_rack["A1"], tip_rack["B1"]])
```

### Automatic tip management

```python
# use_tips picks up on entry, discards on exit
async with lh.pip.use_tips(tip_rack["A1:H1"], trash=trash):
    await lh.pip.aspirate(plate["A1:H1"], vols=[100] * 8)
    await lh.pip.dispense(plate["A2:H2"], vols=[100] * 8)
# tips are discarded automatically
```

### One-to-many transfer

```python
# Aspirate from one well, distribute to multiple targets
await lh.pip.transfer(
    source=plate["A1"],
    targets=[plate["B1"], plate["C1"], plate["D1"]],
    source_vol=300,  # aspirate 300 uL total
    ratios=[1, 1, 1],  # equal distribution (100 uL each)
)
```

## Tips and gotchas

- **Volumes are in uL, flow rates in uL/s, heights in mm, offsets in mm.**
- **`spread` mode** controls how channels are positioned when aspirating/dispensing from a single container: `"wide"` maximizes spacing, `"tight"` minimizes it, `"custom"` uses your offsets.
- **Tip tracking is transactional.** If a multi-channel operation partially fails, only the channels that succeeded are committed. The rest are rolled back.
- **Volume tracking.** The capability tracks liquid volumes per tip and per well. `allow_nonzero_volume=False` (default on `drop_tips`) prevents you from dropping tips that still have liquid.
- **`discard_tips` defaults to `allow_nonzero_volume=True`**, since discarding tips with residual liquid is common.

## Supported hardware

```{supported-devices} liquid handling
```

## API reference

See {class}`~pylabrobot.capabilities.liquid_handling.pip.PIP` and {class}`~pylabrobot.capabilities.liquid_handling.pip.PIPBackend`.
