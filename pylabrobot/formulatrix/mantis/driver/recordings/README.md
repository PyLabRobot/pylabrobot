# Recordings

What a real Mantis reported about itself, and its unit's own values, kept as data so a simulated
one can stand in for it, and so a unit without its own values still runs.

A configuration has two sources. What the device reports is read at setup. What it cannot report,
its unit's calibration, sits in the `Data/Device/Configs` folder of its vendor install, which setup
reads when given it:

```python
mantis = Mantis(oem_config_directory="path/to/Data/Device/Configs")
await mantis.setup()
mantis.driver.save_configuration(path="my_mantis.json")
```

`save_configuration` writes both: the `device` block and the unit's blocks. From then on the file
alone carries them, read back through `declared_configuration_json`; the folder is no longer
needed.

| block | from | a **simulated** Mantis | a **physical** Mantis |
|---|---|---|---|
| `device` | the device, at setup | takes its configuration from it; its pump answers the generation's recording's sensor calibration (`feedback_sensor_parameters`) | is checked against it at setup, on `FITTED_FIELDS`; setup refuses if they disagree. The sensor calibration is read off the device at every setup and never saved; one in a declared file is ignored |
| `plate_clamp` | `Device.config`: `PlateClampInisiatePWM`, `PlateClampTimer`; only for a clamp driven as a DC motor | takes its lock levels from it | takes its lock levels from it |
| `rfid` | `RFID.config`: every `RFIDAntennaId.ACC<n>`; `Device.config`: `RfidMultiplexerDelay`, as written (`multiplexer_delay_raw`) | takes its slots from it; every slot reads empty | takes each slot's antenna; a delay other than 0 refuses the reads, as its unit is unconfirmed |
| `led_bar` | `Device.config`: `NumberOfLed`; set on a v4 only | takes its LED count from it | takes its LED count from it: each colour write sets that many LEDs |
| `arm` | `RobotArm.config`: `Motor.1/2.Offset` and `HomingPositionA`; the `Data` folder's `System/Encoder Correction/Sampling File/OneRevolutionSamplingData_Motor0/1.csv`; only where `HomingPositionA` is set (a v4) | takes its offsets from it, else 0 | needs it to bring the arm up; never taken from a recording |
| `stage` | `Device.config`: `MicroplateTransformationMatrix`, `MicroplateOffsetX/Y`, `ArmType`, `ArmTypeTranslation`, `ArmTypeRotationDegree`; the `Data/Device/Adapters/SBS Adapter.ad.txt` origin; only where the matrix is set | takes it from it, else none: a move to a wellplate refuses | needs it to move to a wellplate; never taken from a recording |

For each unit block, setup takes:

1. the folder's and the declared file's, when both have it and they agree; setup refuses, listing
   the differences, when they do not;
2. the one source's, when only one has it;
3. the generation's recording's, when neither has it, with a warning: another unit's values,
   less accurate on this one. `save_configuration` does not save a block taken from a recording.
   A block that calibrates motion (`MOTION_CALIBRATION_BLOCKS`: `arm`, `zones`, `approach_paths`,
   `stage`) is never taken from a recording, and no recording holds one: without it, the motion it
   calibrates refuses.

A declared file saved before a block or a non-fitted `device` field existed still loads: each
value it lacks comes from the folder, else the recording, on a device and in simulation, with one
warning naming each and where it came from.

| file | device |
|---|---|
| `mantis_v4.json` | Mantis v4, motion firmware 4.20.6.2, with that unit's lock levels, RFID antennas, LED count (20) and pressure sensor calibration. The default for a simulated `Mantis`. |
| `mantis_v3.json` | Mantis v3 (vendor software 4.8.2.5): the generation only. No v3 has run setup, so its firmware is not recorded; its install has no DC-motor clamp and no `RFID.config`, so it has no unit blocks yet. |

## What is not in one

- **The serial number.** Never saved.
- **The device name.** Read with `request_name`; a simulated Mantis does not answer it.
- **State.** Where the arm is, the clamp's state, what the LED bar shows, which chips are loaded,
  anything that changes during a run.
