"""Hardware test: TecanEVO v1b1 RoMa plate handling.

Tests RoMa pick up and place of a plate between two carriers.

Deck layout:
  Rail 16: MP_3Pos carrier ("carrier_src")
    Position 1: source plate (Eppendorf 96-well)
  Rail 22: MP_3Pos carrier ("carrier_dst")
    Position 1: destination (empty, plate will be placed here)

Usage:
  python keyser-testing/test_v1b1_roma.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import Eppendorf_96_wellplate_250ul_Vb_skirted, MP_3Pos_Corrected
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO
from pylabrobot.tecan.evo.params import TecanRoMaParams

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

# Slow speeds for first test — defaults are x=10000, y=5000, z=1300, r=5000
SLOW_PARAMS = TecanRoMaParams(
  speed_x=3000,
  speed_y=2000,
  speed_z=800,
  speed_r=2000,
  accel_y=800,
  accel_r=800,
)


async def main():
  print("=" * 60)
  print("  TecanEVO v1b1 RoMa Plate Handling Test")
  print("=" * 60)

  # --- Deck setup ---
  deck = EVO150Deck()
  evo = TecanEVO(
    name="evo",
    deck=deck,
    diti_count=8,
    air_liha=True,
    has_roma=True,
    packet_read_timeout=30,
    read_timeout=120,
    write_timeout=120,
  )

  carrier_src = MP_3Pos_Corrected("carrier_src")
  carrier_dst = MP_3Pos_Corrected("carrier_dst")
  deck.assign_child_resource(carrier_src, rails=16)
  deck.assign_child_resource(carrier_dst, rails=22)

  source_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("plate")
  carrier_src[0] = source_plate

  print("\nDeck layout:")
  print(f"  Rail 16: {carrier_src.name}")
  print(f"    Position 1: {source_plate.name}")
  print(f"  Rail 22: {carrier_dst.name}")
  print(f"    Position 1: (destination)")

  print("\nInitializing...")
  try:
    await evo.setup()
    print(f"  Channels: {evo.pip.num_channels}")
    print(f"  RoMa: {'available' if evo.arm else 'not available'}")
    print("Ready!")
  except Exception as e:
    print(f"Init FAILED: {e}")
    import traceback
    traceback.print_exc()
    return

  assert evo.arm is not None, "RoMa arm not initialized"
  roma_backend = evo.arm.backend

  try:
    # --- Report computed positions ---
    z_range = await roma_backend.roma.report_z_param(5)
    print(f"\n  RoMa Z range: {z_range}")

    src_offset = source_plate.get_location_wrt(deck)
    src_x, src_y, src_z = roma_backend._roma_positions(source_plate, src_offset, z_range)
    print(f"\n  Source (carrier_src pos 1):")
    print(f"    X={src_x}  Y={src_y}")
    print(f"    Z safe={src_z['safe']}  travel={src_z['travel']}  end={src_z['end']}")

    dst_site = carrier_dst[0]
    dst_offset = dst_site.get_location_wrt(deck)
    dst_x, dst_y, dst_z = roma_backend._roma_positions(source_plate, dst_offset, z_range)
    print(f"\n  Destination (carrier_dst pos 1):")
    print(f"    X={dst_x}  Y={dst_y}")
    print(f"    Z safe={dst_z['safe']}  travel={dst_z['travel']}  end={dst_z['end']}")

    # --- Test 1: Pick up plate ---
    print("\n--- Test 1: Pick Up Plate ---")
    print("  (slow speeds: x=3000, y=2000, z=800, r=2000)")
    input("Press Enter to pick up plate from carrier_src position 1...")
    await roma_backend.pick_up_from_carrier(source_plate, backend_params=SLOW_PARAMS)
    print("Plate picked up!")

    # --- Test 2: Place plate ---
    print("\n--- Test 2: Place Plate ---")
    input("Press Enter to place plate at carrier_dst position 1...")
    await roma_backend.drop_at_carrier(source_plate, dst_offset, backend_params=SLOW_PARAMS)
    print("Plate placed!")

    # --- Test 3: Park RoMa ---
    print("\n--- Test 3: Park RoMa ---")
    await roma_backend.park()
    print("RoMa parked!")

    print("\n*** ROMA PLATE HANDLING TEST PASSED ***")

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
