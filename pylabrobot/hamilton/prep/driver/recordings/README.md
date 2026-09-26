# Prep recordings

What a real Prep reported about itself, kept as data so a simulated one can stand in for it.

`PrepSimulationDriver` answers from two kinds of recording: a saved configuration, which says what
device this is and what it carries, and a firmware tree, which says which objects and methods its
firmware has.

## Configurations

A configuration is written by the driver, not by hand:

```python
prep = Prep(host="192.168.100.102")
await prep.setup()
prep.driver.save_configuration("prep_PRPAA1087_v1_2_2.json")
```

and read back through `declared_configuration_json`, which is the one way a configuration is read
from a file:

```python
prep = Prep(simulation=True, declared_configuration_json="prep_PRPAA1087_v1_2_2.json")
await prep.setup()
```

It means two different things depending on what is on the other end. A **simulated** device answers
as the declaration says. A **physical** device answers for itself, and the declaration is
cross-checked against it: setup refuses if the two disagree on how many channels there are, whether
an 8-channel head is installed, or whether there is an enclosure.

| file | device | notes |
|---|---|---|
| `prep_PRPAA1087_v1_2_2.json` | PRPAA1087, MLPrep Runtime V1.2.2.444 | The default for a simulated device. Identical to what `save_configuration` wrote off PRPAA1087 on 2026-09-14. |
| `prep_PRPAA1087_v1_2_2_head8.json` | derived from the above | The same, with `head8_installed` set. No device with an 8-channel head has been recorded. Its channel windows are PRPAA1087's, which has no head: on a real device the head rides the same Y rail and leaves both channels less Y reach, by an amount not yet recorded. |

### What is in one

```json
{
  "device":   { ... },
  "pipettes": { ... }
}
```

`device` is what MLPrep, its deck configuration and its CPU answered: identity, fitted channels and
head, enclosure, safe speeds, traverse height, deck bounds, deck and waste sites. `pipettes` is what
each channel answered: its firmware, and the X, Y and Z window it reaches.

A simulated device needs both. A declaration without `pipettes` leaves the channels' firmware
unanswered, and setup fails. Configurations saved before `pipettes` was added, or with the older
`has_mph` key in place of `head8_installed`, cannot be declared.

### What is not

- **Where the device is.** Channel and arm positions are state, not configuration. A simulated
  device answers them from the resource model.
- **Device memory with no reading here.** The X axis velocity and acceleration, the speed scales and
  the deck light answer PRPAA1087's values, kept in `simulator.py`.

## Firmware trees

Read off a device by introspection, then trimmed to what a simulated device answers from: each
object's name, path, address, version and interfaces, and each method with its recorded `GetMethod`
answer.

| file | device |
|---|---|
| `prep_PRPAA1087_v1_2_2_firmware_tree.json` | PRPAA1087, MLPrep Runtime V1.2.2.444. The default. |
| `prep_PRPBD1394_v3_0_20_firmware_tree.json` | PRPBD1394, MLPrep Runtime V3.0.20.675. |

### Enums and structs

A tree records what each object declares as types, beside its methods: `enums` maps each enum's
names to its values, `structs` each struct's fields to their wire types. Without them a field typed
as an enum on the wire is a bare number, and what else it could be is a question only the device can
answer.

They are read off a device into an existing tree by

```
python tools/record_prep_firmware_types.py 192.168.100.102 \
  pylabrobot/hamilton/prep/driver/recordings/prep_PRPAA1087_v1_2_2_firmware_tree.json
```

which reads only, and leaves every other key as it was. An object that declares none has neither
key. Trees recorded before this have neither, and load as they always did.

Selected with `firmware_tree_json`:

```python
from pylabrobot.hamilton.prep.driver.simulator import FIRMWARE_TREE_V3_0_20

prep = Prep(simulation=True, firmware_tree_json=FIRMWARE_TREE_V3_0_20)
```

A method missing from the selected tree is refused as that firmware refuses it: on V1.2.2,
`is_parked` raises `PrepMethodNotFoundError`; on V3.0.20 it answers. The configuration and the tree
are chosen separately, so a V1.2.2 configuration on the V3.0.20 tree reports V1.2.2 as its firmware
while having V3.0.20's methods.

Neither tree holds an 8-channel head. When one is declared, the simulator adds `MphRoot.MPH`, with
the root's recorded introspection methods and the commands the driver sends the head, at addresses
no device reported.

## Naming

A convention to follow, not something the driver does: `save_configuration` writes exactly the path
it is given.

`prep_<serial>_v<firmware major>_<minor>_<patch>`, then `_head8` for a configuration derived with an
8-channel head, or `_firmware_tree` for a tree. The serial and firmware version are
`device.serial_number` and `device.firmware_version` in the configuration, and `serial_number` and
`firmware_version` in the tree.
