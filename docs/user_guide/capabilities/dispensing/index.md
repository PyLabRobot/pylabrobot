# Bulk dispensing

Bulk dispensers deliver reagent into microplates at high throughput. They are used for filling plates with media, adding assay reagents, wash steps, and any operation where many wells need the same (or similar) volumes.

PLR supports two dispensing mechanisms, each with its own capability:

| Capability | Mechanism | Typical use |
|---|---|---|
| **[Peristaltic dispensing](peristaltic)** | Peristaltic pump | Media, wash buffer, large-volume reagents |
| **[Syringe dispensing](syringe)** | Syringe pump | Detection reagents, substrates, low-volume precision |

Both capabilities share the same `volumes` interface: a dict mapping **1-indexed column numbers** to volumes in uL. Device-specific settings (pump speed, cassette type, flow rate, etc.) are passed as `backend_params`.

Some devices (like the BioTek EL406) have both systems on a single instrument. Use the one that matches your volume and accuracy requirements.

## Peristaltic vs syringe

| | Peristaltic | Syringe |
|---|---|---|
| **Volume range** | Medium--high | Low--medium |
| **Accuracy** | Good | High |
| **Throughput** | High | Lower |
| **Purge needed** | Yes | No |

Peristaltic dispensers push fluid through flexible tubing using a rotating pump head. They are fast and handle large volumes well, but require priming before use and purging after to clear the lines. Syringe dispensers aspirate a fixed volume into a barrel and dispense it with high precision. They are slower but more accurate at low volumes.

## Tips and gotchas

- **Always prime before dispensing** (peristaltic). Air in the tubing causes inaccurate volumes.
- **Purge after dispensing** to prevent reagent from drying in the lines.
- **Columns are 1-indexed.** `{1: 50.0}` sets column 1, not column 0.
- **Only columns in the dict are set.** Columns not in `volumes` retain their previous setting on the instrument. If in doubt, explicitly set all columns.

## Supported hardware

| Device | Manufacturer | Peristaltic | Syringe |
|--------|-------------|:-----------:|:-------:|
| [Multidrop Combi](../thermo_fisher/multidrop_combi/hello-world) | Thermo Fisher | yes | -- |
| [EL406](../agilent/biotek/el406/hello-world) | BioTek (Agilent) | yes | yes |

```{toctree}
:maxdepth: 1
:hidden:

peristaltic
syringe
```

## API reference

- {class}`~pylabrobot.capabilities.bulk_dispensers.peristaltic.peristaltic.PeristalticDispensing` / {class}`~pylabrobot.capabilities.bulk_dispensers.peristaltic.backend.PeristalticDispensingBackend`
- {class}`~pylabrobot.capabilities.bulk_dispensers.syringe.syringe.SyringeDispensing` / {class}`~pylabrobot.capabilities.bulk_dispensers.syringe.backend.SyringeDispensingBackend`
