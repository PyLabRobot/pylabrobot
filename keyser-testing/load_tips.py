"""Pick up tips from a selected column (1-12).

Initializes the EVO, then picks up 8 tips from the chosen column.
Useful for calibrating Z positions with tips mounted.

Deck layout must match test scripts:
  Rail 16: MP_3Pos carrier, Position 3: tip rack

Usage:
  python keyser-testing/load_tips.py [column]

  column: 1-12 (default: 1)
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import DiTi_50ul_SBS_LiHa_Air, Eppendorf_96_wellplate_250ul_Vb_skirted, MP_3Pos_Corrected
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

ROWS = ["A", "B", "C", "D", "E", "F", "G", "H"]


async def main():
  # Parse column from args or prompt
  if len(sys.argv) > 1:
    col = int(sys.argv[1])
  else:
    col_str = input("Column to pick up tips from (1-12) [1]: ").strip()
    col = int(col_str) if col_str else 1

  if not 1 <= col <= 12:
    print(f"Invalid column: {col}. Must be 1-12.")
    return

  wells = [f"{row}{col}" for row in ROWS]
  print(f"Will pick up 8 tips from column {col}: {wells[0]}-{wells[-1]}")

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
  tip_rack = DiTi_50ul_SBS_LiHa_Air("tips")
  carrier[0] = source_plate
  carrier[1] = dest_plate
  carrier[2] = tip_rack

  print("\nInitializing...")
  await evo.setup()
  print(f"Ready! ({evo.pip.num_channels} channels)")

  try:
    input(f"\nPress Enter to pick up tips from column {col}...")
    await evo.pip.pick_up_tips(tip_rack.get_items(wells))

    # Verify
    resp = await evo._driver.send_command("C5", command="RTS")
    tip_status = resp["data"][0] if resp and resp.get("data") else 0
    print(f"Tip status: {tip_status} (255=all mounted)")

    # Raise Z
    z_range = evo.pip.backend._z_range
    num_ch = evo.pip.backend.num_channels
    z_params = ",".join([str(z_range)] * num_ch)
    await evo._driver.send_command("C5", command=f"PAZ{z_params}")
    print("Channels raised to Z max.")

    print("\nTips loaded. You can now use the jog UI to calibrate Z positions.")
    print("Run tips_off_tipbox.py when done to return tips.")

  except Exception as e:
    print(f"\nFailed: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

  finally:
    await evo.stop()
    print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
