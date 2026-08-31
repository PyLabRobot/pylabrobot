# Plate-reader and imager events

The instrumented `legacy.plate_reading.PlateReader` and `legacy.plate_reading.Imager` frontends emit
one semantic lifecycle for each public operation below.

## PlateReader

| Operation | Primary fields |
| --- | --- |
| `plate_reader.open` | `device`, loaded `resources` |
| `plate_reader.close` | `device`, loaded `resources` |
| `plate_reader.read_luminescence` | `device`, selected-well `resources`, `well_count`, `return_format`, `focal_height`; completed event adds `record_count` |
| `plate_reader.read_absorbance` | `device`, selected-well `resources`, `well_count`, `return_format`, `wavelength_nm`; completed event adds `record_count` |
| `plate_reader.read_fluorescence` | `device`, selected-well `resources`, `well_count`, `return_format`, `excitation_wavelength_nm`, `emission_wavelength_nm`, `focal_height`; completed event adds `record_count` |

For `open` and `close`, `resources` contains the directly loaded plate when present. For a
measurement, it contains each directly selected `Well`; the well references' `ancestors` provide
the owning-plate context. `well_count` is the selection size, and the completion-only
`record_count` is the number of records returned by the backend. `return_format` is `"records"` or
`"legacy_matrix"`.

Focal heights use PLR's default length unit (millimeters). Wavelengths are nanometers, hence the
explicit `_nm` suffix. Events do not contain the returned measurement values or backend-only
keyword arguments.

## Imager

| Operation | Primary fields |
| --- | --- |
| `imager.capture` | `device`, `resources`, optional `plate`, `target`, `mode`, `objective`, `exposure`, `focus`, `gain`; completed event adds `image_count`, `reported_exposure_time_ms`, `reported_focal_height` |

`target` contains the resolved integer `row` and `column`. `mode` and `objective` are the selected
enum member names. When the caller passes a `Well`, `resources` contains that direct well; when the
caller passes a row/column tuple, `resources` is empty. The optional `plate` reference identifies
the loaded plate used for capture.

The requested capture settings are JSON objects:

- `exposure` has `mode` equal to `"fixed"`, `"machine_auto"`, or `"software_auto"`. Fixed exposure
  adds `time_ms`; software auto adds `minimum_time_ms`, `maximum_time_ms`, and optional
  `max_rounds`.
- `focus` has `mode` equal to `"fixed"`, `"machine_auto"`, or `"software_auto"`. Fixed focus adds
  `height`; software auto adds `minimum_height`, `maximum_height`, `tolerance`, and `timeout`.
- `gain` has `mode` equal to `"fixed"` or `"machine_auto"`; fixed gain adds `value`.

Exposure times are milliseconds. Focus heights and tolerance use PLR's default length unit, and
autofocus timeout uses the default time unit.

A software auto-exposure or autofocus request can make several backend captures, but EventBus
still emits exactly one lifecycle for the user's `capture` call. The completed event exposes only
the number of images and the backend-reported exposure and focal height. It never contains pixels,
image arrays, or backend-only keyword arguments.

`legacy.plate_reading.ImageReader` inherits both operation families. Calls on an `ImageReader`
emit those inherited canonical events once, without an additional wrapper lifecycle.
