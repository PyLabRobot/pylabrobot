# The device registry

Every machine listed on the {doc}`/user_guide/machines` page comes from a single JSON file,
`docs/_static/devices.json`. Two directives read it: `device-table` renders a searchable table of
devices, `device-card` renders a single device as a card. Adding a machine to the docs means adding
one object to that file.

## Adding a device

Append an object to `docs/_static/devices.json`:

```json
{
  "id": "cole-parmer-masterflex",
  "vendor": "Cole Parmer",
  "name": "Masterflex L/S",
  "models": [
    {"name": "07522-20", "status": "full"},
    {"name": "07522-30", "status": "full"},
    {"name": "07551-20", "status": "full"},
    {"name": "07551-30", "status": "full"},
    {"name": "07575-30", "status": "full"},
    {"name": "07575-40", "status": "full"}
  ],
  "kind": "pump",
  "capabilities": ["pumping"],
  "status": "full",
  "api": "pylabrobot.cole_parmer.Masterflex",
  "api_version": "v1",
  "code_slug": "cole_parmer",
  "manager": "https://discuss.pylabrobot.org/u/rickwierenga",
  "oem": "https://corporate.avantorsciences.com/us/en/bioprocess-solutions/fluid-management/masterflex-peristaltic-pumps/ls-series"
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique kebab-case identifier. Used by `device-card` and as the HTML anchor (`#device-<id>`). |
| `vendor` | yes | Manufacturer, as users would search for it. |
| `name` | yes | Display name for the device or device family, without the vendor. |
| `models` | no | Model objects when one entry covers several models. `name` is required; `status` may be `wip`, `basic`, `mostly`, or `full` and defaults to the device status when omitted. Models render as searchable sub-rows with their support status beneath the device. |
| `kind` | yes | Device type, e.g. `plate reader`, `sealer`, `arm`. Must be one of `KINDS` in `docs/_exts/plr_devices/data.py`. |
| `status` | yes | One of `wip`, `basic`, `mostly`, `full`. See {doc}`/user_guide/machines` for what each level means. |
| `capabilities` | no | Core functions, e.g. `["heating", "shaking"]`. Must come from `CAPABILITIES` in `docs/_exts/plr_devices/data.py`. These drive the badges and the capability filter. |
| `api` | no | Import path of the driver class, e.g. `pylabrobot.curiox.CurioxHT2000`. |
| `api_version` | no | `v1`, or `v0` for drivers still under `pylabrobot.legacy`. |
| `doc_slug` | no | The machine's own page, relative to `docs/user_guide/` and without the extension. Links the device name and builds the **docs** link; verified at build time. |
| `code_slug` | no | The driver's module or package, relative to `pylabrobot/`. Builds the **code** link to the source on GitHub; verified at build time. |
| `manager` | no | Forum profile of whoever looks after this driver, e.g. `https://discuss.pylabrobot.org/u/rickwierenga`. Shown as their handle, and who to ask about the device. |
| `oem` | no | Manufacturer product page. |
| `notes` | no | One line of additional context about the entry, such as functionality that is missing. Use `models` for the hardware models an entry covers. |

The registry is validated when the docs build starts: unknown fields, duplicate ids and unknown
statuses fail the build, as do `doc_slug` and `code_slug` values that do not point at a real page
or a real module.

`kind` and `capabilities` are controlled vocabularies, listed as `KINDS` and `CAPABILITIES` in
`docs/_exts/plr_devices/data.py`. A machine that needs a genuinely new term gets it added there in
the same change — the point is that "sealer" and "heat sealer" cannot quietly become two different
filter chips. Terms that nothing uses are removed, and a test enforces both directions.

The two slugs exist so entries stay short and the prefixes stay in one place. `doc_slug` is
resolved against `plr_devices_doc_prefix`, and `code_slug` against `plr_devices_code_root` and the
repository and branch in `html_context` — the same ones the theme's "edit this page" links use.

### Adding a guide to an existing device

If a device already has a registry object, update that object rather than appending another one:

1. Set `doc_slug` to the guide's path relative to `docs/user_guide/`, without `.md` or `.ipynb`.
2. Add the guide to its manufacturer's `{toctree}`.
3. Put a `{device-card}` directive in the guide using the registry object's `id`.

For example, a notebook at `docs/user_guide/agilent/vspin/hello-world.ipynb` uses:

```json
"doc_slug": "agilent/vspin/hello-world"
```

and its card is:

````md
```{device-card} agilent-vspin
```
````

Validate registry changes with:

```bash
python -m pytest docs/_exts/plr_devices/registry_tests.py
```

## Rendering a table

A bare `device-table` renders every device, with a search box, a **Show models** toggle and filter
chips. Models render as sub-rows beneath their device. A device row can reveal its own models, and
the toggle reveals them all. Each model's support badge is aligned beneath the device support
column. Models are initially hidden, but a text search
always matches them and automatically reveals matching model rows when 10 or fewer devices remain:

````md
```{device-table}
```
````

Options narrow it down:

````md
```{device-table}
:vendor: QInstruments
:search: false
:filters: false
```
````

`capabilities`, `vendor`, `kind` and `status` each take one value and filter the rows. `search` and
`filters` take `false` to hide the search box or the chips, which is useful for a short,
pre-filtered list on a vendor page.

## Rendering a card

`device-card` renders one device. It works anywhere MyST is parsed, including markdown cells in the
notebooks under `docs/user_guide` — put one at the top of a machine's hello-world notebook so the
page carries the same vendor, models, support level, capabilities and links as the table.

````md
```{device-card} curiox-ht2000
```
````

A directive is rendered when Sphinx builds the docs. In a notebook opened in an editor it shows as
a literal code block instead, so a card on a notebook page only appears on the published version.

## Styling

The markup is generated by `docs/_exts/plr_devices`, and styled by `docs/_static/plr_devices.css`.
Search and filtering are handled by `docs/_static/plr_devices.js`. Capability badge colors are
derived from a hash of the capability name, so a new capability gets a stable color without a CSS
change.
