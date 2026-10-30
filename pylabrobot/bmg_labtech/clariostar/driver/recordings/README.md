# Recordings

What a real CLARIOstar reported about itself, kept as data so a simulated one can stand in for it.

A recording is written by the driver, not by hand:

```python
reader = CLARIOstarPlus()
await reader.setup()
reader.driver.save_configuration(compute_recording_name(reader.driver.configuration))
```

and read back through `declared_configuration_json`, the one way a configuration is read from a
file. A **simulated** reader takes its configuration from it. A **physical** reader is
cross-checked against it at setup, on `FITTED_FIELDS`: setup refuses if the two disagree on what
is fitted. Firmware is not compared, so a recording taken off one reader still describes another
of the same build.

| file | reader |
|---|---|
| `clariostar_plus_1_35_temperature_controller.json` | CLARIOstar Plus, firmware 1.35. Decoded from the INIT and stored-parameters replies of one reader; the default for a simulated `CLARIOstarPlus`. |

## What is not in one

- **The serial number.** Never read from the reader; every file holds `"000-0000"`.
- **The firmware build date.** The same on every reader of a firmware; not saved.
- **State.** Where the drawer is, temperatures, anything that changes during a run.

## Naming

`compute_recording_name` builds the name from fields read off the reader, in this order:

`<model>_<firmware>[_temperature_controller | _extended_temperature_range][_pumps_N][_stacker][_atmospheric_control][_1536].json`

A part is left out when the reader has none of it. Detectors and measurements are not in the
name; readers that differ only there share a name and are told apart by the file's contents.
