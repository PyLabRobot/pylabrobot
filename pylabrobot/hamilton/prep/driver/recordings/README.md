# Prep recordings

What `PrepSimulationDriver` answers from.

## Configurations

Saved configurations, in the shape `PrepDriver.save_configuration` writes.

| file | device | notes |
|---|---|---|
| `prep_PRPAA1087_v1_2_2.json` | PRPAA1087, MLPrep Runtime V1.2.2.444 | The default. `device` is the configuration PRPAA1087 saved on 2026-09-14, with `has_mph` renamed to `head8_installed`. `pipettes` was assembled from the channel readings logged in the same session (firmware, channel bounds, v2 support), because that save predates the `pipettes` section; it was not saved by the driver. |
| `prep_PRPAA1087_v1_2_2_head8.json` | as above | The same, declared with an 8-channel head. No device with a head has been recorded. |

## Firmware trees

Firmware trees read off a device by introspection, trimmed to what a simulated device answers from:
each object's name, path, address, version, interfaces, and methods with their recorded `GetMethod`
answer.

| file | device |
|---|---|
| `prep_PRPAA1087_v1_2_2_firmware_tree.json` | PRPAA1087, MLPrep Runtime V1.2.2.444. The default. |
| `prep_PRPBD1394_v3_0_20_firmware_tree.json` | PRPBD1394, MLPrep Runtime V3.0.20.675. |

A method missing from the selected tree is refused as that firmware refuses it, so a simulated device
has the firmware version the tree was read from. Neither tree holds an 8-channel head; when one is
declared, the simulator adds `MphRoot.MPH` with the root's recorded introspection methods and the
commands the driver sends the head, at addresses no device reported.
