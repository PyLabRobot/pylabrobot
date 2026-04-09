"""Pick up tips from a selected column (1-12).

Initializes the EVO, then picks up 8 tips from the chosen column.
Useful for calibrating Z positions with tips mounted.

Deck layout:
  Rail 16: MP_3Pos carrier
    Position 2: tip rack (swappable: 50uL, 200uL, or 1000uL)

Usage:
  python keyser-testing/load_tips.py [tip_type] [column]

  tip_type: 50, 200, or 1000 (default: 50)
  column:   1-12 (default: 1)
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import (
  DiTi_50ul_SBS_LiHa_Air,
  DiTi_200ul_SBS_LiHa_Air,
  DiTi_1000ul_SBS_LiHa_Air,
  MP_3Pos_Corrected,
)
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

ROWS = ["A", "B", "C", "D", "E", "F", "G", "H"]

TIP_RACKS = {
  "50": ("DiTi 50uL", DiTi_50ul_SBS_LiHa_Air),
  "200": ("DiTi 200uL", DiTi_200ul_SBS_LiHa_Air),
  "1000": ("DiTi 1000uL", DiTi_1000ul_SBS_LiHa_Air),
}


async def main():
  # Parse args
  args = sys.argv[1:]
  tip_type = "50"
  col = 1

  if args:
    if args[0] in TIP_RACKS:
      tip_type = args[0]
      args = args[1:]
    if args:
      col = int(args[0])

  if tip_type not in TIP_RACKS:
    print(f"Unknown tip type: {tip_type}. Choose from: {', '.join(TIP_RACKS.keys())}")
    return

  if not 1 <= col <= 12:
    print(f"Invalid column: {col}. Must be 1-12.")
    return

  label, rack_fn = TIP_RACKS[tip_type]
  wells = [f"{row}{col}" for row in ROWS]
  print(f"Tip type: {label}")
  print(f"Column {col}: {wells[0]}-{wells[-1]}")

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

  tip_rack = rack_fn("tips")
  carrier[2] = tip_rack

  print(f"\nTip rack: {tip_rack.model}")
  print(f"  z_start={tip_rack.z_start}  z_max={tip_rack.z_max}")

  print("\nInitializing...")
  await evo.setup()
  print(f"Ready! ({evo.pip.num_channels} channels)")

  try:
    input(f"\nPress Enter to pick up {label} tips from column {col}...")
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

    print(f"\n{label} tips loaded. Use jog UI to calibrate Z positions.")
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
