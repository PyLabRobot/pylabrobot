import asyncio
import argparse
from pylabrobot.liquid_handling import LiquidHandler, STARBackend
from pylabrobot.liquid_handling.standard import GripDirection
from pylabrobot.resources import CellVis_96_wellplate_350uL_Fb, PLT_CAR_L5AC_A00, STARLetDeck, STARDeck, Plate, PlateCarrier


async def qc_iswap(lh: LiquidHandler, backend: STARBackend, plate: Plate, plate_carrier: PlateCarrier):
  async def move_plate(pickup_direction: GripDirection, drop_direction: GripDirection, destination):
    await lh.pick_up_resource(
      plate,
      direction=pickup_direction,
      pickup_distance_from_top=6,
      iswap_fold_up_sequence_at_the_end_of_process=False,
    )
    await lh.drop_resource(
      destination,
      direction=drop_direction,
    )

  async def in_place_tests(spot):
    print(f"performing in-place tests on spot {spot}")

    # move in place left and right
    print("  testing iswap left and right movement...")
    await move_plate(GripDirection.LEFT, GripDirection.LEFT, plate_carrier[spot])

    await move_plate(GripDirection.RIGHT, GripDirection.RIGHT, plate_carrier[spot])
    print("  [pass] iswap left and right movement successful")

    # move and rotate 180
    print("  testing iswap left and right movement with 180 degree rotation...")
    await move_plate(GripDirection.RIGHT, GripDirection.LEFT, plate_carrier[spot])
    await move_plate(GripDirection.LEFT, GripDirection.RIGHT, plate_carrier[spot])
    print("  [pass] iswap left and right movement with 180 degree rotation successful")

    print(f"[pass] in-place tests on spot {spot} successful")

  await in_place_tests(0)  # test on the first spot
  print("moving plate to fifth spot...")

  await move_plate(GripDirection.LEFT, GripDirection.LEFT, plate_carrier[4])  # move to the fifth spot
  await in_place_tests(4)  # test on the fifth spot
  await move_plate(GripDirection.LEFT, GripDirection.LEFT, plate_carrier[0])  # move back to the first spot

  print("parking iswap...")
  await backend.park_iswap()


async def main(lh: LiquidHandler, backend: STARBackend, plate: Plate, plate_carrier: PlateCarrier):
  print("Starting QC for Hamilton STAR(let)")

  await lh.setup()
  print("[pass] setup successful")

  validate_iswap = True
  if validate_iswap:
    try:
      if not backend.iswap_installed:
        raise RuntimeError("iswap is not installed on the backend, cannot run iswap QC test.")
      await qc_iswap(lh, backend, plate, plate_carrier)
      print("[pass] iswap QC test completed successfully")
    except Exception as e:
      print(f"[fail] iswap QC test failed: {e}")
      # move robot to safe position
      await backend.park_iswap()
      raise
  
  await lh.stop()
  print("All QC tests completed successfully")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="QC Hamilton STAR(let)")
  parser.add_argument("--model", choices=["STAR", "STARlet"], default="STAR", help="Model to use")
  parser.add_argument("--plate-carrier-rails", type=int, default=6, help="Rails where the plate carrier is placed")
  args = parser.parse_args()

  assert args.plate_carrier_rails >= 6, "will crash into robot frame"

  backend = STARBackend()
  lh = LiquidHandler(backend=backend, deck=STARDeck() if args.model == "STAR" else STARLetDeck())
  plate_carrier = PLT_CAR_L5AC_A00(name="plate_carrier")
  plate_carrier[0] = plate = CellVis_96_wellplate_350uL_Fb(name="plate")
  lh.deck.assign_child_resource(plate_carrier, rails=args.plate_carrier_rails)

  asyncio.run(main(lh, backend, plate, plate_carrier))
