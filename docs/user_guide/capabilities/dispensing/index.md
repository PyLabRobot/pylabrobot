# Bulk dispensing

Bulk dispensers deliver reagent into microplates at high throughput. They are used for filling plates with media, adding assay reagents, wash steps, and any operation where many wells need the same (or similar) volumes.

Bulk-dispensing capabilities vary along two independent axes:

* **Mechanism** — how the fluid is moved. PLR currently models three:
  * **Peristaltic** — a rotating pump head pushes fluid through flexible tubing. Fast, large volumes; needs priming and purging.
  * **Syringe** — a fixed volume is aspirated into a syringe barrel and dispensed under positive pressure. High accuracy at low volumes.
  * **Diaphragm** — a disposable silicon chip with microvalves driven by pressurized air delivers nanoliter-to-microliter doses without contacting the liquid.
* **Head format** — how targets are addressed:
  * **Variable** (no suffix) — one container at a time. Callers pass parallel `containers` and `volumes` lists.
  * **8-channel** (`8` suffix) — one column at a time. Callers pass a dict mapping 1-indexed column numbers to volumes in uL.
  * **96-channel** (`96` suffix) — the full plate at once.

Each mechanism × head format combination is a separate capability class. For example, the 8-channel peristaltic capability is {class}`~pylabrobot.capabilities.bulk_dispensers.peristaltic.peristaltic8.PeristalticDispensing8`. Not every combination exists yet — what's currently implemented:

| Mechanism | Variable | 8-channel | 96-channel |
|---|:---:|:---:|:---:|
| [Peristaltic](peristaltic) | -- | {class}`~pylabrobot.capabilities.bulk_dispensers.peristaltic.peristaltic8.PeristalticDispensing8` | -- |
| [Syringe](syringe) | -- | {class}`~pylabrobot.capabilities.bulk_dispensers.syringe.syringe8.SyringeDispensing8` | -- |
| [Diaphragm](diaphragm) | {class}`~pylabrobot.capabilities.bulk_dispensers.diaphragm.diaphragm.DiaphragmDispenser` | -- | -- |

The mechanism name in the first column links to its walkthrough page; the cells link to the API reference for each implemented variant. More variants will land as devices need them — the class name suffix tells you which head format you're getting, and the unsuffixed name is always the variable (per-container) variant.

Device-specific settings (pump speed, cassette type, flow rate, chip number, dispense Z, etc.) are passed as `backend_params` for all variants. Some devices (like the BioTek EL406) host more than one mechanism on a single instrument — pick the capability that matches your volume and accuracy requirements.

## Choosing a mechanism

| | Peristaltic | Syringe | Diaphragm |
|---|---|---|---|
| **Volume range** | Medium--high | Low--medium | Nanoliter--microliter |
| **Accuracy** | Good | High | Very high (contactless) |
| **Throughput** | High | Lower | Lower (per-container) |
| **Prime needed** | Yes | Optional | Yes (per chip) |
| **Purge needed** | Yes | No | No |

Peristaltic dispensers push fluid through flexible tubing using a rotating pump head — fast, large volumes, but require priming and purging. Syringe dispensers aspirate a fixed volume into a barrel and dispense under positive pressure — slower but more accurate at low volumes. Diaphragm dispensers use a disposable chip with microvalves driven by pressurized air — contactless, very precise at small volumes, and address one container at a time.

## Tips and gotchas

- **Always prime before dispensing** (peristaltic, diaphragm). Air in the lines causes inaccurate volumes.
- **Purge after dispensing** (peristaltic) to prevent reagent from drying in the lines.
- **8-channel head formats are 1-indexed by column.** `{1: 50.0}` sets column 1, not column 0. Columns not in the dict retain their previous setting on the instrument — when in doubt, set them all.
- **Variable head formats take parallel lists.** `containers[i]` gets `volumes[i]` uL, in order. The capability validates that the lengths match and the volumes are positive.

## Supported hardware

| Device | Manufacturer | Peristaltic | Syringe | Diaphragm |
|--------|-------------|:-----------:|:-------:|:---------:|
| [Multidrop Combi](../../thermo_fisher/multidrop_combi/hello-world) | Thermo Fisher | 8-channel | -- | -- |
| [EL406](../../agilent/biotek/el406/hello-world) | BioTek (Agilent) | 8-channel | 8-channel | -- |
| [Mantis](../../formulatrix/mantis/hello-world) | Formulatrix | -- | -- | variable |

```{toctree}
:maxdepth: 1
:hidden:

peristaltic
syringe
diaphragm
```

## API reference

- {class}`~pylabrobot.capabilities.bulk_dispensers.peristaltic.peristaltic8.PeristalticDispensing8` / {class}`~pylabrobot.capabilities.bulk_dispensers.peristaltic.backend8.PeristalticDispensingBackend8` (peristaltic, 8-channel)
- {class}`~pylabrobot.capabilities.bulk_dispensers.syringe.syringe8.SyringeDispensing8` / {class}`~pylabrobot.capabilities.bulk_dispensers.syringe.backend8.SyringeDispensingBackend8` (syringe, 8-channel)
- {class}`~pylabrobot.capabilities.bulk_dispensers.diaphragm.diaphragm.DiaphragmDispenser` / {class}`~pylabrobot.capabilities.bulk_dispensers.diaphragm.backend.DiaphragmDispenserBackend` (diaphragm, variable)
