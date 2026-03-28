"""Hardware test: AirEVOBackend pipetting operations.

Tests pick up tips, aspirate, dispense, and drop tips.

Deck layout:
  Rail 16: MP_3Pos carrier
    Position 1: Eppendorf plate (source, water in A1-H1)
    Position 2: Eppendorf plate (destination, empty)
    Position 3: DiTi_50ul_SBS_LiHa tip rack

Prerequisites:
  - EVO powered on with Air LiHa
  - Eppendorf plate with water in wells A1-H1 in position 1
  - Empty Eppendorf plate in position 2
  - DiTi 50uL tips loaded in position 3
  - pip install -e ".[usb]"

Usage:
  python keyser-testing/test_air_evo_pipette.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import DiTi_50ul_SBS_LiHa_Air, Eppendorf_96_wellplate_250ul_Vb_skirted
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan import AirEVOBackend
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


async def main():
  print("=" * 60)
  print("  AirEVOBackend Pipetting Test")
  print("=" * 60)

  # --- Deck setup ---
  backend = AirEVOBackend(diti_count=8)
  deck = EVO150Deck()
  lh = LiquidHandler(backend=backend, deck=deck)

  # MP_3Pos carrier at rail 16
  carrier = MP_3Pos("carrier")
  deck.assign_child_resource(carrier, rails=16)

  # Position 1: source plate, Position 2: dest plate, Position 3: tip rack
  source_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("source")
  dest_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("dest")
  tip_rack = DiTi_50ul_SBS_LiHa_Air("tips")
  carrier[0] = source_plate
  carrier[1] = dest_plate
  carrier[2] = tip_rack

  print("\nDeck layout:")
  print("  MP_3Pos carrier: rail 16")
  print(f"    Position 1: {source_plate.name} (water in A1-H1)")
  print(f"    Position 2: {dest_plate.name} (empty)")
  print(f"    Position 3: {tip_rack.name} (DiTi 50uL)")

  # Setup handles init-skip automatically (checks REE0, skips PIA if already initialized)
  print("\nInitializing...")
  try:
    await lh.setup()
    print(f"  Channels: {backend.num_channels}, Z-range: {backend._z_range}")
    print("Ready!")
  except Exception as e:
    print(f"Init FAILED: {e}")
    import traceback

    traceback.print_exc()
    return

  try:
    # --- Test 1: Pick up 8 tips ---
    print("\n--- Test 1: Pick Up Tips ---")
    input("Press Enter to pick up 8 tips from column 1...")
    await lh.pick_up_tips(tip_rack.get_items(["A1","B1","C1","D1","E1","F1","G1","H1"]))
    print("Tips picked up!")

    # --- Test 2: Aspirate ---
    print("\n--- Test 2: Aspirate 25uL ---")
    input("Press Enter to aspirate 25uL from source column 1...")
    await lh.aspirate(source_plate.get_items(["A1","B1","C1","D1","E1","F1","G1","H1"]), vols=[25] * 8)
    print("Aspirated!")

    # --- Test 3: Dispense ---
    print("\n--- Test 3: Dispense 25uL ---")
    input("Press Enter to dispense 25uL to dest column 1...")
    await lh.dispense(dest_plate.get_items(["A1","B1","C1","D1","E1","F1","G1","H1"]), vols=[25] * 8)
    print("Dispensed!")

    # --- Test 4: Drop tips ---
    print("\n--- Test 4: Drop Tips ---")
    input("Press Enter to drop tips back to tip rack...")
    await lh.drop_tips(tip_rack.get_items(["A1","B1","C1","D1","E1","F1","G1","H1"]))
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
