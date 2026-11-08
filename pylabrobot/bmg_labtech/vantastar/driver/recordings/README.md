# Recordings

What a real VANTAstar reported about itself, kept as data.

A recording is written by the driver, not by hand:

```python
reader = VANTAstar()
await reader.setup()
reader.driver.save_configuration(compute_recording_name(reader.driver.configuration))
```

and read back through `declared_configuration_json`, the one way a configuration is read from a
file. A **simulated** reader takes its configuration from it. A **physical** reader is
cross-checked against it at setup, on `FITTED_FIELDS`: setup refuses if the two disagree on what is
fitted. Firmware is not compared, so a recording taken off one reader still
describes another of the same build.

| file | reader |
|---|---|
| `vantastar_1_12_temperature_controller.json` | VANTAstar, firmware 1.12. Decoded from the INIT reply of the tested reader; the default for a simulated `VANTAstar` (`RECORDING_VANTASTAR`). |

## What is not in one

- **The serial number.** Every file holds `"0000-000000"`.
- **The raw modules.** They hold the serial numbers of the reader's boards.
- **Luminescence.** The INIT reply does not say whether the luminescence module is fitted; the
  tested reader has none and refuses a luminescence read, a simulated one simulates it.
- **State.** Where the drawer is, temperatures, anything that changes during a run.

## Naming

`compute_recording_name` builds the name from fields read off the reader, in this order:

`vantastar_<firmware>[_temperature_controller][_pumps_N][_stacker][_atmospheric_control][_stirrer].json`

A part is left out when the reader has none of it. Detectors and the monochromator are not in the
name; readers that differ only there share a name and are told apart by the file's contents.
