# Supported Machines

Every machine PyLabRobot supports, and how complete each driver is. Some are still work in
progress (WIP) — if you have one of those, or a machine that is not listed at all, get in touch on
the [forum](https://discuss.pylabrobot.org).

```{device-table}
```

An asterisk after a device name marks a note about the models that entry covers; hover it to read
the note.

## Reading the table

Classifying lab automation equipment is hard. Many machines have overlapping capabilities (the
Thermo Fisher Cytomat 2 C470 is a fridge, heated chamber, oven, plate hotel *and* shaker in one),
different user groups refer to the same machine by whichever capability they happen to use, naming
sits somewhere between user intuition and historical legacy (a thermocycler is really just a fast
heater/cooler), and there is no widely accepted standard.

PyLabRobot does not solve that. **Type** is the one label that names what a machine is, and
**capabilities** are the core functions it provides — a machine typically has several. Both are
filterable, so a machine you think of as a shaker is still findable by someone who thinks of it as
a heater.

**Support** is how complete the PyLabRobot integration is:

- **WIP** — work in progress.
- **Basic** — core functionality is available, integrated into `pylabrobot:main`.
- **Mostly** — most capabilities are available, but some known commands are still missing.
- **Full** — comprehensive support (≥90% of capabilities), with documentation.

The `v0`/`v1` marker next to the support level is the API generation a machine's driver belongs to.
`v0` drivers live under `pylabrobot.legacy` and are being migrated.

PyLabRobot aims to expose every hardware/firmware capability of integrated equipment, including
capabilities the OEM software does not surface. That lets you *choose* which functions you need.

## Adding or updating a machine

The table is generated from `docs/_static/devices.json`. Adding a machine means adding one JSON
object — there is no table markup to keep in sync. See
{doc}`/contributor_guide/device-registry` for the schema, and for how to put a device card on a
machine's own documentation page.
