"""Hardware test: TecanEVO v1b1 pipetting operations.

Tests pick up tips, aspirate, dispense, and drop tips using the v1b1
TecanEVO device with PIP capability.

Deck layout:
  Rail 16: MP_3Pos carrier
    Position 1: Eppendorf plate (source, water in A1-H1)
    Position 2: Eppendorf plate (destination, empty)
    Position 3: DiTi_50ul_SBS_LiHa tip rack

Usage:
  python keyser-testing/test_v1b1_pipette.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import DiTi_50ul_SBS_LiHa_Air, Eppendorf_96_wellplate_250ul_Vb_skirted
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

COLUMN_1 = ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"]


async def main():
  print("=" * 60)
  print("  TecanEVO v1b1 Pipetting Test")
  print("=" * 60)

  # --- Deck setup ---
  deck = EVO150Deck()
  evo = TecanEVO(
    name="evo",
    deck=deck,
    diti_count=8,
    air_liha=True,
    has_roma=False,
    packet_read_timeout=30,
    read_timeout=120,
    write_timeout=120,
  )

  carrier = MP_3Pos("carrier")
  deck.assign_child_resource(carrier, rails=16)

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

  print("\nInitializing...")
  try:
    await evo.setup()
    print(f"  Channels: {evo.pip.num_channels}")
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
    await evo.pip.pick_up_tips(tip_rack.get_items(COLUMN_1))
    print("Tips picked up!")

    # --- Test 2: Aspirate ---
    print("\n--- Test 2: Aspirate 25uL ---")
    input("Press Enter to aspirate 25uL from source column 1...")
    await evo.pip.aspirate(source_plate.get_items(COLUMN_1), vols=[25] * 8)
    print("Aspirated!")

    # --- Test 3: Dispense ---
    print("\n--- Test 3: Dispense 25uL ---")
    input("Press Enter to dispense 25uL to dest column 1...")
    await evo.pip.dispense(dest_plate.get_items(COLUMN_1), vols=[25] * 8)
    print("Dispensed!")

    # --- Test 4: Drop tips ---
    print("\n--- Test 4: Drop Tips ---")
    input("Press Enter to drop tips...")
    await evo.pip.drop_tips(tip_rack.get_items(COLUMN_1))
    print("Tips dropped!")

    print("\n*** PIPETTING TEST PASSED ***")

  except Exception as e:
    print(f"\nTest FAILED: {type(e).__name__}: {e}")
    import traceback

    traceback.print_exc()

  finally:
    print("\nStopping...")
    await evo.stop()
    print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
