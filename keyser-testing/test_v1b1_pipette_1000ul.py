"""Hardware test: TecanEVO v1b1 pipetting with 1000uL tips.

Tests pick up tips, aspirate 100uL, dispense, and drop tips.

Deck layout:
  Rail 16: MP_3Pos carrier
    Position 1: Eppendorf plate (source, water in column 3)
    Position 2: Eppendorf plate (destination, empty)
    Position 3: DiTi_1000ul_SBS_LiHa tip rack

Usage:
  python keyser-testing/test_v1b1_pipette_1000ul.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import DiTi_1000ul_SBS_LiHa_Air, Eppendorf_96_wellplate_250ul_Vb_skirted, MP_3Pos_Corrected
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

COLUMN_3 = ["A3", "B3", "C3", "D3", "E3", "F3", "G3", "H3"]


async def main():
  print("=" * 60)
  print("  TecanEVO v1b1 Pipetting Test — 1000uL tips, 100uL volume")
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

  carrier = MP_3Pos_Corrected("carrier")
  deck.assign_child_resource(carrier, rails=16)

  source_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("source")
  dest_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("dest")
  tip_rack = DiTi_1000ul_SBS_LiHa_Air("tips")
  carrier[0] = source_plate
  carrier[1] = dest_plate
  carrier[2] = tip_rack

  print("\nDeck layout:")
  print("  MP_3Pos carrier: rail 16")
  print(f"    Position 1: {source_plate.name} (water in column 3)")
  print(f"    Position 2: {dest_plate.name} (empty)")
  print(f"    Position 3: {tip_rack.name} (DiTi 1000uL)")

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
    print("\n--- Test 1: Pick Up 1000uL Tips ---")
    print(f"  AGT: z_start={tip_rack.z_start} z_max={tip_rack.z_max}")
    input("Press Enter to pick up 8 tips from column 3...")
    await evo.pip.pick_up_tips(tip_rack.get_items(COLUMN_3))
    print("Tips picked up!")

    # --- Test 2: Aspirate ---
    print("\n--- Test 2: Aspirate 100uL ---")
    input("Press Enter to aspirate 100uL from source column 3...")
    await evo.pip.aspirate(source_plate.get_items(COLUMN_3), vols=[100] * 8)
    print("Aspirated!")

    # --- Test 3: Dispense ---
    print("\n--- Test 3: Dispense 100uL ---")
    input("Press Enter to dispense 100uL to dest column 3...")
    await evo.pip.dispense(dest_plate.get_items(COLUMN_3), vols=[100] * 8)
    print("Dispensed!")

    # --- Test 4: Drop tips ---
    print("\n--- Test 4: Drop Tips ---")
    input("Press Enter to drop tips...")
    await evo.pip.drop_tips(tip_rack.get_items(COLUMN_3))
    print("Tips dropped!")

    print("\n*** 1000uL PIPETTING TEST PASSED ***")

  except Exception as e:
    print(f"\nTest FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

  finally:
    pip_be = evo.pip.backend
    z_range = pip_be._z_range
    num_ch = pip_be.num_channels
    z_params = ",".join([str(z_range)] * num_ch)
    await evo._driver.send_command("C5", command=f"PAZ{z_params}")

    print("\nStopping...")
    await evo.stop()
    print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
