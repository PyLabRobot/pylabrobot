# Head96 (96-Channel Head)

{class}`~pylabrobot.capabilities.liquid_handling.head96.Head96` controls a 96-channel pipetting head that operates on full tip racks and plates at once. All 96 channels move together as a single unit.

## When to use

Use this for plate-to-plate transfers, plate replication, or any operation where all 96 wells are processed identically. Much faster than using independent channels for full-plate operations.

## Setup

```python
from pylabrobot.hamilton.star import STAR

lh = STAR(name="star", ...)
await lh.setup()

# 96-head is at lh.head96
await lh.head96.pick_up_tips(tip_rack)
```

## Walkthrough

### Plate-to-plate transfer

```python
await lh.head96.pick_up_tips(tip_rack)
await lh.head96.aspirate(source_plate, volume=50)
await lh.head96.dispense(target_plate, volume=50)
await lh.head96.return_tips()
```

### Stamp (one-liner plate copy)

```python
await lh.head96.pick_up_tips(tip_rack)
await lh.head96.stamp(source_plate, target_plate, volume=50)
await lh.head96.discard_tips(trash)
```

### Aspirating from a trough

```python
# All 96 tips dip into the same container
await lh.head96.pick_up_tips(tip_rack)
await lh.head96.aspirate(trough, volume=200)
await lh.head96.dispense(plate, volume=200)
await lh.head96.discard_tips(trash)
```

## Tips and gotchas

- **Volumes are in uL, flow rates in uL/s, heights in mm.**
- **Sparse pickup is supported.** If a tip rack is partially empty, only the positions that have tips are picked up.
- **Trough minimum size.** When aspirating from a single container, it must be at least ~101 mm x ~65 mm to accommodate the 96-head geometry (9 mm tip spacing).
- **`stamp` requires same-shape plates.** Both plates must have the same `num_items_x` and `num_items_y`.
- **`return_tips` requires all tips from the same rack.** Raises `RuntimeError` if mounted tips originated from different racks.
- **A `default_offset`** (set at construction) is added to all operation offsets.

## Supported hardware

```{supported-devices} liquid handling
```

## API reference

See {class}`~pylabrobot.capabilities.liquid_handling.head96.Head96` and {class}`~pylabrobot.capabilities.liquid_handling.head96.Head96Backend`.
