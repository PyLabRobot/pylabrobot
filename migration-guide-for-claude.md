# Docs migration guide: legacy category-based to manufacturer-based layout

## Background

The PLR codebase organizes device code by manufacturer (e.g. `pylabrobot/agilent/biotek/el406/`), but the docs historically used category-based directories:

- `docs/user_guide/00_liquid-handling/`
- `docs/user_guide/01_material-handling/`
- `docs/user_guide/02_analytical/`

We are migrating docs to mirror the codebase: `docs/user_guide/<manufacturer>/...`.

This file lives at the repo root (not inside `docs/`) so Sphinx doesn't complain about it not being in a toctree.

## How to migrate a single device

### 1. Create the new doc directory

Mirror the code path. For example:
- Code at `pylabrobot/qinstruments/bioshake.py` -> docs at `docs/user_guide/qinstruments/bioshake/`
- Code at `pylabrobot/agilent/biotek/el406/` -> docs at `docs/user_guide/agilent/biotek/el406/`

### 2. Write the notebook

Create a `hello-world.ipynb` at the new location. The notebook should:

- **Import from the new code path** (e.g. `from pylabrobot.qinstruments import BioShakeQ1`, not `from pylabrobot.heating_shaking import BioShake`).
- **Show device setup and teardown.**
- **Give brief demos of each capability** the device supports (shaking, temperature control, etc.) — just enough to show the device-specific API surface (factory functions, backend params, model-specific notes).
- **Link to the capability docs for full API details** rather than duplicating them. Use relative links like `[Shaking](../../capabilities/shaking)` and `[Temperature Control](../../capabilities/temperature-control)`.
- **Include a model table** if the device has multiple models/variants, with a "PLR Name" column showing the factory function or class name.
- **Add Sphinx cross-references for BackendParams classes** used in the notebook. In markdown cells, use `{class}\`~pylabrobot.<module>.<Backend>.<Params>\`` syntax so they link to the API docs. Every BackendParams class that appears in a code cell should be mentioned with a cross-reference in a nearby markdown cell.

See `docs/user_guide/qinstruments/bioshake/hello-world.ipynb` as the reference example for structure, and `docs/user_guide/agilent/biotek/el406/hello-world.ipynb` for BackendParams cross-referencing.

### 3. Wire up the toctree

The Manufacturers section in `docs/user_guide/index.md` lists manufacturer-level indexes. Each manufacturer has an `index.md` that lists its devices.

**When there's only one device under a level, skip the intermediate index and point directly to the notebook.** Only create an `index.md` when a level has multiple children.

Current structure:

```
docs/user_guide/index.md  (Manufacturers toctree)
├── agilent/index.md           -> lists biotek/index
│   └── biotek/index.md        -> lists el406/hello-world  (no el406/index.md — only one item)
├── azenta/index.md            -> lists a4s/hello-world, xpeel/hello-world
├── inheco/index.md            -> lists cpac, incubator_shaker, odtc, scila, thermoshake
├── liconic/index.md           -> lists stx/hello-world
├── mettler_toledo/index.md    -> lists wxs205sdu/hello-world
└── qinstruments/index.md      -> lists bioshake/hello-world
```

**Adding a device to an existing manufacturer:** add the notebook path to the manufacturer's `index.md` toctree. If the manufacturer previously pointed directly to a single notebook, you'll need to create an intermediate `index.md` now that there are multiple items.

**Adding a new manufacturer:** create `<manufacturer>/index.md` and add it to the Manufacturers toctree in `docs/user_guide/index.md`.

### 4. Add to the API reference

Each manufacturer needs an RST file in `docs/api/` (e.g. `pylabrobot.azenta.rst`) that documents the device classes, drivers, and backends via `autosummary`. If the manufacturer already has an RST file, just add the new device's classes.

**For nested BackendParams classes** (e.g. `XPeelPeelerBackend.PeelParams`), autosummary can't handle them directly. Use `autoclass` directives instead:

```rst
.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    XPeelPeelerBackend

.. autoclass:: pylabrobot.azenta.xpeel.XPeelPeelerBackend.PeelParams
   :members:
```

The RST file must be listed in the `Manufacturers` toctree in `docs/api/pylabrobot.rst`.

See `docs/api/pylabrobot.azenta.rst` as the reference example.

### 5. Remove the old notebook from the legacy location

Delete the old `.ipynb` file from `00_liquid-handling/`, `01_material-handling/`, or `02_analytical/`.

### 6. Remove the entry from the legacy category toctree

Remove the device's toctree entry from the parent page (e.g. `heating_shaking.md`, `plate-washing.md`). If a sub-section becomes empty after removal, delete the entire sub-section directory too. The goal is to eventually delete `00_liquid-handling/`, `01_material-handling/`, and `02_analytical/` entirely.

Do NOT update other text/links in the legacy pages — just remove the toctree entry and the file.

### 7. Do NOT touch `machines.md`

`machines.md` is legacy and will be kept as-is. Don't update links there.

### 8. Build and verify

Run `make clean-docs && make docs` for a full build including API docs. Fix any warnings — the build uses `-W` so warnings are errors. (The only acceptable warning is nbformat's `MissingIDFieldWarning` about cell IDs, which is pre-existing.)

## Rules

- Use **relative links** between doc pages, not absolute `https://docs.pylabrobot.org/...` URLs.
- The directory structure under `docs/user_guide/` should mirror the package structure under `pylabrobot/`.
- Migrate one device at a time. Don't batch.
- When a device has capabilities (shaking, temperature control, etc.), link to the capability docs — don't duplicate the API walkthrough.
- Include a "PLR Name" column in model tables showing the factory function or class name users should import.
- Skip intermediate `index.md` files when a level has only one child — point directly to the notebook instead.
- Always add the device's classes/backends to the API reference RST files.
- Use `autoclass` (not `autosummary`) for nested `BackendParams` classes — autosummary can't resolve inner classes.

## Completed migrations

| Device | Old location | New location |
|--------|-------------|--------------|
| BioTek EL406 | `00_liquid-handling/plate-washing/biotek-el406.ipynb` | `agilent/biotek/el406/hello-world.ipynb` |
| QInstruments BioShake | `01_material-handling/heating_shaking/qinstruments.ipynb` | `qinstruments/bioshake/hello-world.ipynb` |
| Mettler Toledo WXS205SDU | `02_analytical/scales/mettler-toledo-WXS205SDU.ipynb` | `mettler_toledo/wxs205sdu/hello-world.ipynb` |
| Azenta a4S | `01_material-handling/sealers/a4s.ipynb` | `azenta/a4s/hello-world.ipynb` |
| Azenta XPeel | _(no old doc)_ | `azenta/xpeel/hello-world.ipynb` |
| Liconic STX | `01_material-handling/storage/liconic.ipynb` | `liconic/stx/hello-world.ipynb` |
| Inheco ThermoShake | `01_material-handling/heating_shaking/inheco.ipynb` | `inheco/thermoshake/hello-world.ipynb` |
| Inheco CPAC | `01_material-handling/temperature-controllers/inheco.ipynb` | `inheco/cpac/hello-world.ipynb` |
| Inheco SCILA | `01_material-handling/storage/inheco/scila.ipynb` | `inheco/scila/hello-world.ipynb` |
| Inheco Incubator Shaker | `01_material-handling/storage/inheco/incubator_shaker.ipynb` | `inheco/incubator_shaker/hello-world.ipynb` |
| Inheco ODTC | `01_material-handling/thermocycling/inheco-odtc.ipynb` | `inheco/odtc/hello-world.ipynb` |
| Thermo Fisher Multidrop Combi | _(new with codebase)_ | `thermo_fisher/multidrop_combi/hello-world.ipynb` |
| BioTek Cytation | `02_analytical/plate-reading/cytation.ipynb` | `agilent/biotek/cytation/hello-world.ipynb` |
