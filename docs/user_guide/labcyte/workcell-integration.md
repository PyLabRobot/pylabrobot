# Echo Workcell Integration

The `Echo` frontend models the physical source and destination positions as PLR resource holders.
That lets a workcell keep the resource tree aligned with real plate movement while the Echo driver
continues to handle Medman commands.

## Moving Plates Into The Echo Model

```python
from pylabrobot.labcyte import Echo
from pylabrobot.resources.resource_holder import ResourceHolder


async def move_plate_with_arm(arm, plate, source: ResourceHolder, destination: ResourceHolder):
  """Move a plate physically, then update the PLR resource tree.

  Replace `arm.move_resource(...)` with the matching arm API in your workcell.
  """

  await arm.move_resource(plate, destination)
  source.resource = None
  destination.resource = plate


async def run_echo_transfer(arm, source_hotel_site, assay_hotel_site, source_plate, assay_plate):
  echo = Echo(host="192.168.0.25", app_name="PLR Echo workcell")

  source_hotel_site.resource = source_plate
  assay_hotel_site.resource = assay_plate

  async with echo:
    await move_plate_with_arm(
      arm,
      source_plate,
      source=source_hotel_site,
      destination=echo.source_position,
    )
    await move_plate_with_arm(
      arm,
      assay_plate,
      source=assay_hotel_site,
      destination=echo.destination_position,
    )

    await echo.lock()
    try:
      await echo.load_source_plate(source_plate.model or "384PP_DMSO2")
      await echo.load_destination_plate(assay_plate.model or "1536LDV_Dest")
      result = await echo.transfer(
        [(source_plate.get_well("A1"), assay_plate.get_well("B1"), 2.5)],
        do_survey=True,
      )
    finally:
      await echo.unlock()

    await echo.eject_destination_plate()
    await move_plate_with_arm(
      arm,
      assay_plate,
      source=echo.destination_position,
      destination=assay_hotel_site,
    )

    await echo.eject_source_plate()
    await move_plate_with_arm(
      arm,
      source_plate,
      source=echo.source_position,
      destination=source_hotel_site,
    )

  return result
```

`echo.source_position` and `echo.destination_position` are PLR holders and accept `Plate` resources
only. The convenience properties `echo.source_plate` and `echo.destination_plate` are shortcuts for
assigning or reading the held plates:

```python
echo.source_plate = source_plate
echo.destination_plate = assay_plate
```

This model is intentionally separate from Echo access state. Assigning a plate to the holder updates
the PLR workcell model; calling `load_source_plate`, `load_destination_plate`, `eject_source_plate`,
or `eject_destination_plate` controls the real Echo.
