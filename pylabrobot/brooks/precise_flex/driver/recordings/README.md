# Device recordings

What a real PreciseFlex reported about itself, kept as data.

A recording is written by the driver, not by hand:

```python
await driver.setup()
driver.save_configuration("pf400_extended_400mm.json")
```

| arm | recording |
|---|---|
| PreciseFlex 400, extended reach, 400 mm of Z travel, vision gripper (2 cameras), no rail | `pf400_extended_400mm.json` |

It is every answer one arm gave to the driver's discovery, saved by the driver at setup on the arm.
The one field changed afterwards is `controller_serial`, which is blanked: a recording says what
kind of arm it is, not which one. It was saved with each feature nested in the device's section and
has since been written out again by the driver, each feature beside the device's; no value changed.
