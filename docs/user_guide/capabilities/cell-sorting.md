# Cell sorting (FACS)

`CellSorter` controls fluorescence-activated cell sorters that deposit gated
events into the wells of a plate.

## Why this capability exists

Sorting is the one step in plate-based single-cell sequencing that usually has to
happen off-automation. A liquid handler can build libraries end to end, but the
sort itself, one gated cell per well or a targeted, enriched population, is
typically a manual hand-off. Modeling the sorter as a capability closes that gap: a
deck stages the plate, calls `sort_to_plate`, and finishes the prep, so walkaway
library prep becomes possible.

It also lets a workflow sort for a target population, enriching a cell type, a
marker-positive subset, or live singlets before the assay. That is what gives
cell-type resolution to downstream measurements such as cell-type-specific
epigenomics, where the goal is to read regulatory state in the exact cell type a
variant acts in rather than in a bulk average.

## Interface

The frontend is hardware-agnostic. The primitive backend operations are:

- `get_status()` -- a coarse instrument state (`idle`, `running`, `error`).
- `load_template(name)` -- select a pre-built sort template (gate hierarchy).
- `set_deposition(cells_per_well, plate_format)` -- configure the deposition target.
- `prime()` -- prime fluidics and stabilize the stream.
- `start_sort(wells)` -- begin depositing into the staged plate.
- `wait_for_completion(poll_interval, timeout)` -- block until the sort finishes.
- `abort()` -- stop the current sort immediately.
- `clean()` -- run the clean or flush cycle between samples.

The frontend adds validation and the `sort_to_plate` convenience method, which
sequences load, deposition, prime, sort, wait, and clean in one call.

The interface is intentionally small and event-oriented so it can sit next to a
future acquisition/cytometry capability that reuses the same fluidics and gate
template concepts, letting a single instrument expose both without duplicating an
interface.

## Device-free testing

`CellSorterChatterboxBackend` implements the interface with logging and no I/O, so
you can build and test a workflow without an instrument:

```python
from pylabrobot.capabilities.cell_sorting import CellSorter, CellSorterChatterboxBackend

sorter = CellSorter(backend=CellSorterChatterboxBackend())
await sorter._on_setup()
await sorter.sort_to_plate(cells_per_well=1, wells=96, template="singlet_deposit")
```

## Supported hardware

- BD FACSMelody (`pylabrobot.becton_dickinson.facsmelody`). This backend replays a
  decoded `ProtocolMap` and is not yet hardware-validated; it runs end to end dry
  and refuses to open a live link until a complete map is supplied. See
  [Reverse-engineering the BD FACSMelody](../../facsmelody-re.md) for how the map
  is produced and the safety model that governs a live sort.
