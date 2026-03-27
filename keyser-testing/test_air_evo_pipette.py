"""Hardware test: AirEVOBackend pipetting operations.

Tests pick up tips, aspirate, dispense, and drop tips using the
default deck layout (MP_3Pos carrier, DiTi_50ul_SBS_LiHa tips,
Eppendorf_96_wellplate_250ul_Vb plates).

Prerequisites:
  - EVO powered on with Air LiHa
  - DiTi 50uL tips loaded in tip rack on DiTi_3Pos carrier
  - Eppendorf plate with water in wells A1-H1 on MP_3Pos carrier
  - pip install -e ".[usb]"

Usage:
  python keyser-testing/test_air_evo_pipette.py
"""

import asyncio

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan import AirEVOBackend
from pylabrobot.resources.eppendorf.plates import Eppendorf_96_wellplate_250ul_Vb
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.resources.tecan.tip_carriers import DiTi_3Pos
from pylabrobot.resources.tecan.tip_racks import DiTi_50ul_SBS_LiHa


async def main():
  print("=" * 60)
  print("  AirEVOBackend Pipetting Test")
  print("=" * 60)

  # --- Deck setup ---
  backend = AirEVOBackend(diti_count=8)
  deck = EVO150Deck()
  lh = LiquidHandler(backend=backend, deck=deck)

  # Tip carrier at rail 15, plate carrier at rail 25
  tip_carrier = DiTi_3Pos("tip_carrier")
  plate_carrier = MP_3Pos("plate_carrier")
  deck.assign_child_resource(tip_carrier, rails=15)
  deck.assign_child_resource(plate_carrier, rails=25)

  # Tip rack in position 1 of tip carrier
  tip_rack = DiTi_50ul_SBS_LiHa("tips_1")
  tip_carrier[0] = tip_rack

  # Source plate in position 1, dest plate in position 2
  source_plate = Eppendorf_96_wellplate_250ul_Vb("source")
  dest_plate = Eppendorf_96_wellplate_250ul_Vb("dest")
  plate_carrier[0] = source_plate
  plate_carrier[1] = dest_plate

  print("\nDeck layout:")
  print(f"  Tip carrier: rail 15 ({tip_carrier.name})")
  print(f"    Position 1: {tip_rack.name}")
  print(f"  Plate carrier: rail 25 ({plate_carrier.name})")
  print(f"    Position 1: {source_plate.name} (water in A1-H1)")
  print(f"    Position 2: {dest_plate.name} (empty)")

  print("\nInitializing (ZaapMotion config + PIA)...")
  try:
    await lh.setup()
    print("Init OK!")
  except Exception as e:
    print(f"Init FAILED: {e}")
    return

  try:
    # --- Test 1: Pick up 8 tips ---
    print("\n--- Test 1: Pick Up Tips ---")
    input("Press Enter to pick up 8 tips from column 1...")
    await lh.pick_up_tips(tip_rack["A1":"H1"])
    print("Tips picked up!")

    # --- Test 2: Aspirate ---
    print("\n--- Test 2: Aspirate 25uL ---")
    input("Press Enter to aspirate 25uL from source column 1...")
    await lh.aspirate(source_plate["A1":"H1"], vols=[25] * 8)
    print("Aspirated!")

    # --- Test 3: Dispense ---
    print("\n--- Test 3: Dispense 25uL ---")
    input("Press Enter to dispense 25uL to dest column 1...")
    await lh.dispense(dest_plate["A1":"H1"], vols=[25] * 8)
    print("Dispensed!")

    # --- Test 4: Drop tips ---
    print("\n--- Test 4: Drop Tips ---")
    input("Press Enter to drop tips back to tip rack...")
    await lh.drop_tips(tip_rack["A1":"H1"])
    print("Tips dropped!")

    print("\n*** PIPETTING TEST PASSED ***")

  except Exception as e:
    print(f"\nTest FAILED: {type(e).__name__}: {e}")
    import traceback

    traceback.print_exc()

  finally:
    print("\nStopping...")
    await lh.stop()
    print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
