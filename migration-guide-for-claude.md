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

See `docs/user_guide/qinstruments/bioshake/hello-world.ipynb` as the reference example.

### 3. Wire up the toctree

The Manufacturers section in `docs/user_guide/index.md` lists manufacturer-level indexes. Each manufacturer has an `index.md` that lists its devices.

**When there's only one device under a level, skip the intermediate index and point directly to the notebook.** Only create an `index.md` when a level has multiple children.

Current structure:

```
docs/user_guide/index.md  (Manufacturers toctree)
├── agilent/index.md           -> lists biotek/index
│   └── biotek/index.md        -> lists el406/hello-world  (no el406/index.md — only one item)
└── qinstruments/index.md      -> lists bioshake/hello-world  (no bioshake/index.md — only one item)
```

**Adding a device to an existing manufacturer:** add the notebook path to the manufacturer's `index.md` toctree. If the manufacturer previously pointed directly to a single notebook, you'll need to create an intermediate `index.md` now that there are multiple items.

**Adding a new manufacturer:** create `<manufacturer>/index.md` and add it to the Manufacturers toctree in `docs/user_guide/index.md`.

### 4. Remove the old notebook from the legacy location

Delete the old `.ipynb` file from `00_liquid-handling/`, `01_material-handling/`, or `02_analytical/`.

### 5. Remove the entry from the legacy category toctree

Remove the device's toctree entry from the parent page (e.g. `heating_shaking.md`, `plate-washing.md`). If a sub-section becomes empty after removal, delete the entire sub-section directory too. The goal is to eventually delete `00_liquid-handling/`, `01_material-handling/`, and `02_analytical/` entirely.

Do NOT update other text/links in the legacy pages — just remove the toctree entry and the file.

### 6. Do NOT touch `machines.md`

`machines.md` is legacy and will be kept as-is. Don't update links there.

### 7. Build and verify

Run `make clean-docs && make docs-fast` to build pages from scratch (no API docs). Fix any warnings — the build uses `-W` so warnings are errors.

## Rules

- Use **relative links** between doc pages, not absolute `https://docs.pylabrobot.org/...` URLs.
- The directory structure under `docs/user_guide/` should mirror the package structure under `pylabrobot/`.
- Migrate one device at a time. Don't batch.
- When a device has capabilities (shaking, temperature control, etc.), link to the capability docs — don't duplicate the API walkthrough.
- Include a "PLR Name" column in model tables showing the factory function or class name users should import.
- Skip intermediate `index.md` files when a level has only one child — point directly to the notebook instead.

## Completed migrations

| Device | Old location | New location |
|--------|-------------|--------------|
| BioTek EL406 | `00_liquid-handling/plate-washing/biotek-el406.ipynb` | `agilent/biotek/el406/hello-world.ipynb` |
| QInstruments BioShake | `01_material-handling/heating_shaking/qinstruments.ipynb` | `qinstruments/bioshake/hello-world.ipynb` |
